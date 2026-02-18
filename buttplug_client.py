#!/usr/bin/env python3
"""
rpi_client_buttplug.py
======================
RPi haptic bridge client — Buttplug v3 edition.

Architecture
------------
  FastAPI server  ──WS──►  this script  ──WS──►  Intiface Engine (localhost:12345)
                                         │
                                         └──SDK──►  Skinetic (unchanged path)

The upstream FastAPI protocol is identical to the original script.
The downstream serial path is replaced by Buttplug v3 JSON-over-WebSocket
messages sent to a local Intiface Engine instance.

Buttplug message flow
---------------------
  1. RequestServerInfo   →  ServerInfo
  2. RequestDeviceList   →  DeviceList
  3. StartScanning       →  ScanningFinished / DeviceAdded events
  4. Per payload:  VibrateCmd  (one ScalarCmd entry per node_index)
  5. On disconnect / shutdown:  StopAllDevices

Device mapping
--------------
  ERM vest motors   →  VibrateCmd  (ScalarCmd, ActuatorType "Vibrate")
  Skinetic voice coils  →  handled separately via Skinetic SDK (unchanged)

Intensity scaling
-----------------
  Incoming node intensity is treated as 0–255.
  Buttplug expects 0.0–1.0, so we divide by 255 and clamp.

Config additions (config.yaml)
-------------------------------
  buttplug:
    server_url: "ws://localhost:12345"   # Intiface Engine WebSocket
    client_name: "haptic-bridge"
    scan_timeout: 5                      # seconds to wait for devices after scan
    device_name_filter: []               # optional list of substrings to match device names
                                         # empty = use all devices
"""

import os
import sys
import json
import time
import logging
import asyncio
import threading
from logging import Logger
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# ---- Optional: remote troubleshooting via Glitchtip/Sentry
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

# ---- Upstream WS (FastAPI server)
from websockets.sync.client import connect as ws_connect_sync
from websockets.exceptions import ConnectionClosed, WebSocketException

# ---- Buttplug client uses asyncio websockets
import websockets  # pip install websockets

# ---- Skinetic + SPN pipeline
try:
    from skinetic.skineticSDK import Skinetic
    from inputs.haptidesigner import FrameConverter as HDFrameConverter
    from inputs.image_processor import HapticProcessorInput
    from outputs.schemas import Output
except Exception:
    Skinetic = None
    HDFrameConverter = None
    HapticProcessorInput = None
    Output = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pp_json(obj: Any, limit: int = 2000) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        s = str(obj)
    return s[:limit] + " ... (truncated)" if len(s) > limit else s


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


# ---------------------------------------------------------------------------
# Config / paths  (unchanged from original)
# ---------------------------------------------------------------------------

APP_FAMILY = "haptic-bridge"
DEFAULT_CONFIG_NAME = "config.yaml"
DEFAULT_LOG_NAME = "rpi_client.log"


def is_rpi_linux() -> bool:
    return sys.platform.startswith("linux")


def bundled_base_dir() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def user_config_dir() -> Path:
    return Path.home() / ".config" / APP_FAMILY


def find_config_path() -> Path:
    p1 = user_config_dir() / DEFAULT_CONFIG_NAME
    if p1.exists():
        return p1
    return bundled_base_dir() / DEFAULT_CONFIG_NAME


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        if path.suffix.lower() == ".json":
            return json.loads(path.read_text(encoding="utf-8"))
        raise


def dump_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    except Exception:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def env_override(cfg: dict) -> dict:
    """
    Environment overrides — original keys preserved, buttplug keys added.
      BHX_WS_URL, BHX_CLIENT_ID, BHX_DEBUG,
      BHX_SENTRY_DSN / BHX_GLITCHTIP_DSN,
      BHX_DEVICE (auto|buttplug|skinetic|both),
      BHX_SKINETIC_OUTPUT,
      BHX_BP_URL  (Intiface Engine WebSocket URL)
    """
    cfg["ws_url"] = os.getenv("BHX_WS_URL", cfg.get("ws_url", ""))
    cfg["client_id"] = os.getenv("BHX_CLIENT_ID", cfg.get("client_id", ""))

    if "BHX_DEBUG" in os.environ:
        cfg["debug"] = _as_bool(os.getenv("BHX_DEBUG"))

    # Device mode: "buttplug" replaces the old "serial" mode
    cfg["device"] = (os.getenv("BHX_DEVICE", cfg.get("device", "buttplug")) or "buttplug").lower()
    if cfg["device"] not in ("auto", "buttplug", "skinetic", "both"):
        cfg["device"] = "buttplug"

    cfg["glitchtip_dsn"] = os.getenv(
        "BHX_SENTRY_DSN",
        os.getenv("BHX_GLITCHTIP_DSN", cfg.get("glitchtip_dsn", "")),
    )

    # Buttplug / Intiface Engine
    cfg.setdefault("buttplug", {})
    cfg["buttplug"]["server_url"] = os.getenv(
        "BHX_BP_URL", cfg["buttplug"].get("server_url", "ws://localhost:12345")
    )
    cfg["buttplug"].setdefault("client_name", "haptic-bridge")
    cfg["buttplug"].setdefault("scan_timeout", 5)
    cfg["buttplug"].setdefault("device_name_filter", [])

    # Skinetic
    cfg.setdefault("skinetic", {})
    cfg["skinetic"]["output_type"] = os.getenv(
        "BHX_SKINETIC_OUTPUT", cfg["skinetic"].get("output_type", "USB")
    )

    return cfg


def sanitize_websocket_url(url: str, client_id: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not (url.startswith("ws://") or url.startswith("wss://")):
        url = url.replace("http://", "ws://").replace("https://", "wss://")
        if not (url.startswith("ws://") or url.startswith("wss://")):
            url = f"wss://{url}"
    suffix = f"/ws/listen/{client_id}" if client_id else "/ws/listen/"
    if not url.endswith(suffix):
        url += suffix
    return url


def load_config(logger: Logger) -> dict:
    """
    Load config (yaml or json), apply env overrides, persist to ~/.config.

    Example config.yaml additions for Buttplug:

    device: "buttplug"   # "auto" | "buttplug" | "skinetic" | "both"

    buttplug:
      server_url: "ws://localhost:12345"
      client_name: "haptic-bridge"
      scan_timeout: 5
      device_name_filter: []   # e.g. ["Vest", "LoveBug"] — empty = all devices

    skinetic:
      output_type: "USB"
    """
    cfg_path = find_config_path()
    cfg = load_yaml(cfg_path) if cfg_path.exists() else {}
    cfg = env_override(cfg)

    if not cfg.get("client_id"):
        cfg["client_id"] = input("Enter client ID (e.g., moorchyk1): ").strip()

    if not cfg.get("ws_url"):
        ws = input("Enter WebSocket base (e.g., wss://host:8000): ").strip()
        cfg["ws_url"] = sanitize_websocket_url(ws, cfg["client_id"])
    else:
        cfg["ws_url"] = sanitize_websocket_url(cfg["ws_url"], cfg["client_id"])

    cfg["debug"] = _as_bool(cfg.get("debug", False))

    save_to = user_config_dir() / DEFAULT_CONFIG_NAME
    dump_yaml(save_to, cfg)
    logger.info(f"Config loaded. Saved to: {save_to}")
    return cfg


# ---------------------------------------------------------------------------
# Logging & Sentry  (unchanged)
# ---------------------------------------------------------------------------

def setup_logging() -> Logger:
    log_file = bundled_base_dir() / DEFAULT_LOG_NAME
    logger = logging.getLogger("bhx-rpi")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.info(f"Logging to: {log_file}")
    return logger


def setup_sentry(dsn: str, logger: Logger, environment: str = "raspbian"):
    if not dsn:
        return
    sentry_logging = LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)
    try:
        sentry_sdk.init(
            dsn=dsn,
            integrations=[sentry_logging],
            environment=environment,
            traces_sample_rate=0.0,
            send_default_pii=False,
        )
        logger.info("Glitchtip (Sentry) reporting enabled.")
    except Exception:
        logger.exception("Failed to initialize Glitchtip (Sentry). Continuing without error reporting.")


# ---------------------------------------------------------------------------
# Payload parser  (shared by both output paths)
# ---------------------------------------------------------------------------

class FrameNodeParser:
    """
    Parses the upstream FastAPI payload into a flat list of (node_index, intensity_0_1) tuples
    per frame, paired with a duration_ms.

    Yields: List of (duration_ms, [(node_index, intensity_0_1), ...]) tuples.

    Intensity input is assumed 0–255; output is clamped to 0.0–1.0.
    """

    @staticmethod
    def _as_list(x):
        if x is None:
            return []
        return list(x) if isinstance(x, (list, tuple)) else [x]

    @staticmethod
    def _to_int(x, default=0):
        try:
            return int(x)
        except Exception:
            return default

    @classmethod
    def parse(
        cls,
        sentence: Union[Dict[str, Any], List[Dict[str, Any]]],
        logger: Logger,
    ) -> List[tuple]:
        """
        Returns list of (duration_ms: int, nodes: List[(node_index: int, intensity: float)])
        """
        frames_raw = cls._collect_frames(sentence, logger)
        result = []
        for frame in frames_raw:
            if isinstance(frame, int):
                # bare pause
                result.append((frame, []))
                continue
            if not isinstance(frame, dict):
                continue
            dur = cls._to_int(frame.get("duration", 0))
            nodes = []
            fns = frame.get("frame_nodes", [])
            if isinstance(fns, dict):
                fns = [fns]
            elif not isinstance(fns, list):
                fns = []
            for fn in fns:
                if not isinstance(fn, dict):
                    continue
                idxs = cls._as_list(fn.get("node_index", []))
                vals = cls._as_list(fn.get("intensity", []))
                if len(vals) == 1 and len(idxs) > 1:
                    vals = vals * len(idxs)
                for i, idx in enumerate(idxs):
                    try:
                        node_i = int(idx)
                    except Exception:
                        continue
                    raw_val = cls._to_int(vals[i] if i < len(vals) else 0)
                    intensity = _clamp01(raw_val / 255.0)
                    nodes.append((node_i, intensity))
            result.append((dur, nodes))
        return result

    @classmethod
    def _collect_frames(cls, sentence, logger):
        if isinstance(sentence, list):
            return sentence
        if isinstance(sentence, dict):
            frames = []
            for key, val in sentence.items():
                if isinstance(val, list):
                    frames.extend(val)
                elif isinstance(val, dict):
                    frames.append(val)
                elif isinstance(val, int):
                    frames.append(val)
                else:
                    logger.warning(f"FrameNodeParser: ignoring key '{key}' with type {type(val)}")
            return frames
        raise TypeError(f"Unsupported sentence type: {type(sentence)}")


# ---------------------------------------------------------------------------
# Buttplug v3 client  (async, runs in a dedicated thread/event-loop)
# ---------------------------------------------------------------------------

class ButtplugClient:
    """
    Minimal async Buttplug v3 client that connects to Intiface Engine.

    Buttplug v3 message format:
      [ { "MessageName": { "Id": <int>, ...fields... } } ]

    Key messages used:
      → RequestServerInfo, RequestDeviceList, StartScanning, StopScanning
      → ScalarCmd  (replaces VibrateCmd in v3; ActuatorType "Vibrate")
      → StopAllDevices
      ← ServerInfo, DeviceList, DeviceAdded, DeviceRemoved, Ok, Error
    """

    MSG_VERSION = 3

    def __init__(self, server_url: str, client_name: str, scan_timeout: int,
                 device_name_filter: List[str], logger: Logger):
        self.server_url = server_url
        self.client_name = client_name
        self.scan_timeout = scan_timeout
        self.device_name_filter = [f.lower() for f in device_name_filter]
        self.logger = logger

        self._ws = None
        self._msg_id = 1
        self._devices: Dict[int, dict] = {}   # device_index → device info dict
        self._connected = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Low-level messaging
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        mid = self._msg_id
        self._msg_id += 1
        return mid

    async def _send(self, msg_name: str, fields: dict) -> int:
        mid = self._next_id()
        envelope = [{msg_name: {"Id": mid, **fields}}]
        raw = json.dumps(envelope)
        self.logger.debug(f"[BP→] {raw}")
        await self._ws.send(raw)
        return mid

    async def _recv_until(self, expected_id: Optional[int] = None, timeout: float = 5.0) -> List[dict]:
        """
        Receive messages, processing DeviceAdded/DeviceRemoved as side-effects,
        until we see a message with the expected Id (or timeout).
        Returns the list of message dicts from the matching envelope.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return []
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                return []
            self.logger.debug(f"[BP←] {raw}")
            try:
                msgs = json.loads(raw)
            except json.JSONDecodeError:
                continue
            self._handle_events(msgs)
            if expected_id is not None:
                for m in msgs:
                    for k, v in m.items():
                        if v.get("Id") == expected_id:
                            return msgs
            else:
                return msgs
        
    def _handle_events(self, msgs: List[dict]):
        for m in msgs:
            for k, v in m.items():
                if k == "DeviceAdded":
                    idx = v.get("DeviceIndex")
                    name = v.get("DeviceName", "?")
                    self._devices[idx] = v
                    self.logger.info(f"[BP] DeviceAdded: [{idx}] {name}")
                elif k == "DeviceRemoved":
                    idx = v.get("DeviceIndex")
                    name = self._devices.get(idx, {}).get("DeviceName", "?")
                    self._devices.pop(idx, None)
                    self.logger.info(f"[BP] DeviceRemoved: [{idx}] {name}")
                elif k == "Error":
                    self.logger.warning(f"[BP] Error: {v}")

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self):
        self.logger.info(f"[BP] Connecting to Intiface Engine at {self.server_url}")
        self._ws = await websockets.connect(
            self.server_url,
            ping_interval=25,
            ping_timeout=10,
            close_timeout=5,
        )

        # Handshake
        mid = await self._send("RequestServerInfo", {
            "ClientName": self.client_name,
            "MessageVersion": self.MSG_VERSION,
        })
        msgs = await self._recv_until(expected_id=mid, timeout=10.0)
        server_info = next(
            (m["ServerInfo"] for m in msgs if "ServerInfo" in m), None
        )
        if server_info:
            self.logger.info(
                f"[BP] ServerInfo: {server_info.get('ServerName')} "
                f"v{server_info.get('MessageVersion')}"
            )
        else:
            self.logger.warning("[BP] No ServerInfo received during handshake.")

        # Get already-known devices
        mid = await self._send("RequestDeviceList", {})
        msgs = await self._recv_until(expected_id=mid, timeout=5.0)
        device_list = next(
            (m["DeviceList"] for m in msgs if "DeviceList" in m), None
        )
        if device_list:
            for d in device_list.get("Devices", []):
                idx = d["DeviceIndex"]
                self._devices[idx] = d
                self.logger.info(f"[BP] Known device: [{idx}] {d.get('DeviceName')}")

        # Scan for new devices
        self.logger.info(f"[BP] Scanning for devices ({self.scan_timeout}s) ...")
        mid = await self._send("StartScanning", {})
        await self._recv_until(expected_id=mid, timeout=5.0)
        # Collect DeviceAdded events during the scan window
        scan_deadline = asyncio.get_event_loop().time() + self.scan_timeout
        while asyncio.get_event_loop().time() < scan_deadline:
            remaining = scan_deadline - asyncio.get_event_loop().time()
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
                msgs = json.loads(raw)
                self._handle_events(msgs)
            except asyncio.TimeoutError:
                break
            except Exception:
                break

        mid = await self._send("StopScanning", {})
        await self._recv_until(expected_id=mid, timeout=3.0)
        self.logger.info(f"[BP] Scan complete. {len(self._devices)} device(s) known.")
        self._connected = True

    async def disconnect(self):
        if self._ws:
            try:
                await self._send("StopAllDevices", {})
            except Exception:
                pass
            await self._ws.close()
            self._ws = None
        self._connected = False
        self.logger.info("[BP] Disconnected from Intiface Engine.")

    # ------------------------------------------------------------------
    # Device filtering
    # ------------------------------------------------------------------

    def _filtered_devices(self) -> List[dict]:
        """Return devices that pass the name filter (empty filter = all)."""
        if not self.device_name_filter:
            return list(self._devices.values())
        return [
            d for d in self._devices.values()
            if any(f in d.get("DeviceName", "").lower() for f in self.device_name_filter)
        ]

    # ------------------------------------------------------------------
    # Haptic commands
    # ------------------------------------------------------------------

    async def send_vibrate_frame(
        self,
        nodes: List[tuple],   # [(node_index, intensity_0_1), ...]
    ):
        """
        Send a ScalarCmd (v3 equivalent of VibrateCmd) to each matching device.

        Each (node_index, intensity) pair maps to one entry in the Scalars list.
        If a device has fewer actuators than the max node_index, we cap to what
        it reports in DeviceMessages.ScalarCmd.FeatureCount.
        """
        if not nodes:
            return

        async with self._lock:
            for device in self._filtered_devices():
                dev_idx = device["DeviceIndex"]
                dev_name = device.get("DeviceName", "?")

                # Determine actuator count from device capabilities
                scalar_info = (
                    device.get("DeviceMessages", {})
                          .get("ScalarCmd", [])
                )
                # ScalarCmd is a list of actuator descriptors in v3
                feature_count = len(scalar_info) if scalar_info else 1

                # Build Scalars list — one entry per (node_index, intensity)
                # capped to reported feature count
                scalars = [
                    {
                        "Index": node_i,
                        "Scalar": intensity,
                        "ActuatorType": "Vibrate",
                    }
                    for node_i, intensity in nodes
                    if node_i < feature_count
                ]

                if not scalars:
                    self.logger.debug(
                        f"[BP] No valid scalars for device [{dev_idx}] {dev_name} "
                        f"(feature_count={feature_count}); skipping."
                    )
                    continue

                try:
                    await self._send("ScalarCmd", {
                        "DeviceIndex": dev_idx,
                        "Scalars": scalars,
                    })
                    self.logger.debug(
                        f"[BP] ScalarCmd → [{dev_idx}] {dev_name}: {scalars}"
                    )
                except Exception:
                    self.logger.exception(f"[BP] ScalarCmd failed for device [{dev_idx}]")

    async def stop_all(self):
        async with self._lock:
            try:
                await self._send("StopAllDevices", {})
            except Exception:
                self.logger.exception("[BP] StopAllDevices failed")


# ---------------------------------------------------------------------------
# Async Buttplug runner  (bridges sync world → async Buttplug client)
# ---------------------------------------------------------------------------

class ButtplugRunner:
    """
    Runs an asyncio event loop in a background thread.
    Exposes a thread-safe `send_payload` method for the sync WebSocket consumer.
    """

    def __init__(self, bp_cfg: dict, logger: Logger):
        self.logger = logger
        self._client = ButtplugClient(
            server_url=bp_cfg.get("server_url", "ws://localhost:12345"),
            client_name=bp_cfg.get("client_name", "haptic-bridge"),
            scan_timeout=int(bp_cfg.get("scan_timeout", 5)),
            device_name_filter=bp_cfg.get("device_name_filter", []),
            logger=logger,
        )
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._stopped = False

    def start(self):
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="buttplug-loop")
        self._thread.start()
        self._ready.wait(timeout=30)
        if not self._client._connected:
            self.logger.warning("[BP] Runner started but Buttplug not connected.")

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main())
        except Exception:
            self.logger.exception("[BP] Event loop crashed")
        finally:
            self._loop.close()

    async def _async_main(self):
        try:
            await self._client.connect()
        except Exception:
            self.logger.exception("[BP] Failed to connect to Intiface Engine")
        finally:
            self._ready.set()

        # Keep the loop alive
        while not self._stopped:
            await asyncio.sleep(0.1)

        await self._client.disconnect()

    def send_payload(
        self,
        parsed_frames: List[tuple],  # output of FrameNodeParser.parse()
    ):
        """
        Thread-safe. Submits each frame's vibrate command and respects frame duration.
        Runs in the calling (sync) thread by scheduling coroutines on the async loop.
        """
        if not self._loop or not self._client._connected:
            self.logger.warning("[BP] send_payload called but Buttplug not ready; dropping.")
            return

        for duration_ms, nodes in parsed_frames:
            if nodes:
                fut = asyncio.run_coroutine_threadsafe(
                    self._client.send_vibrate_frame(nodes),
                    self._loop,
                )
                try:
                    fut.result(timeout=2.0)
                except Exception:
                    self.logger.exception("[BP] send_vibrate_frame error")

            # Respect frame duration (pacing), then stop all actuators
            if duration_ms > 0:
                time.sleep(duration_ms / 1000.0)
                fut = asyncio.run_coroutine_threadsafe(
                    self._client.stop_all(), self._loop
                )
                try:
                    fut.result(timeout=2.0)
                except Exception:
                    self.logger.exception("[BP] stop_all error")

    def stop(self):
        self._stopped = True
        if self._thread:
            self._thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Skinetic output
# ---------------------------------------------------------------------------

class SkineticSenderSPN:
    def __init__(self, logger: Logger, output_type: str = "USB"):
        self.logger = logger
        self.dev: Optional[Any] = None
        self.output_type = output_type
        self.available = all([Skinetic, HDFrameConverter, HapticProcessorInput, Output])

    def connect(self):
        if not self.available:
            self.logger.warning("Skinetic pipeline not available (missing SDK or inputs/outputs modules).")
            return
        if self.dev:
            return
        try:
            self.dev = Skinetic()
            ot = getattr(Skinetic.OutputType, self.output_type, Skinetic.OutputType.USB)
            self.dev.connect(output_type=ot)
            self.logger.info(f"Skinetic connected (output_type={self.output_type}).")
        except Exception:
            self.logger.exception("Failed to connect Skinetic device")
            self.dev = None

    def send_payload(self, payload: Union[Dict[str, Any], List[Dict[str, Any]]]):
        if not self.available:
            return
        if not self.dev:
            self.connect()
        if not self.dev:
            return
        try:
            frames = HDFrameConverter(payload)._skinetic
            msg = HapticProcessorInput(frame_list=frames)
            output: Output = msg.format()
            js = output.model_dump_json()
            if self.dev.get_connection_state() == self.dev.ConnectionState.Connected:
                pattern_id = self.dev.load_pattern_json(js)
                self.dev.play_effect(pattern_id)
                self.dev.unload_pattern(pattern_id)
            else:
                self.logger.warning("Skinetic not connected; dropping message.")
        except Exception:
            self.logger.exception("Skinetic send failed")

    def dump_preview(self, payload, logger: Logger):
        if not self.available:
            logger.debug("Skinetic dump skipped (SDK or models not available).")
            return
        try:
            frames = HDFrameConverter(payload)._skinetic
            logger.debug("=== SKINETIC _skinetic frames (n=%d) ===", len(frames))
            for i, f in enumerate(frames[:50]):
                logger.debug("  %03d %s", i, f)
            if len(frames) > 50:
                logger.debug("  ... (truncated)")
            msg = HapticProcessorInput(frame_list=frames)
            output: Output = msg.format()
            logger.debug("=== SKINETIC Output JSON ===\n%s", _pp_json(output.model_dump(), limit=5000))
        except Exception:
            logger.exception("Failed to dump Skinetic preview")


# ---------------------------------------------------------------------------
# WebSocket runtime (upstream FastAPI consumer)
# ---------------------------------------------------------------------------

def websocket_client(cfg: dict, logger: Logger):
    websocket_url = cfg["ws_url"]
    debug = _as_bool(cfg.get("debug", False))
    device_mode = (cfg.get("device") or "buttplug").lower()
    if device_mode not in ("auto", "buttplug", "skinetic", "both"):
        device_mode = "buttplug"
    logger.info(f"Output device mode: {device_mode}")

    bp_cfg = cfg.get("buttplug", {})
    skinetic_cfg = cfg.get("skinetic", {})
    skinetic_output_type = skinetic_cfg.get("output_type", "USB")

    # Initialise Buttplug runner
    bp_runner: Optional[ButtplugRunner] = None
    if device_mode in ("buttplug", "both", "auto"):
        bp_runner = ButtplugRunner(bp_cfg, logger)
        bp_runner.start()

    # Initialise Skinetic sender
    skinetic_sender: Optional[SkineticSenderSPN] = None
    if device_mode in ("skinetic", "both", "auto"):
        skinetic_sender = SkineticSenderSPN(logger, output_type=skinetic_output_type)

    def resolve_auto_choice() -> str:
        if bp_runner and bp_runner._client._connected and bp_runner._client._devices:
            logger.info("Auto device select: Buttplug")
            return "buttplug"
        if skinetic_sender and skinetic_sender.available:
            try:
                skinetic_sender.connect()
                if skinetic_sender.dev and \
                   skinetic_sender.dev.get_connection_state() == skinetic_sender.dev.ConnectionState.Connected:
                    logger.info("Auto device select: Skinetic")
                    return "skinetic"
            except Exception:
                logger.exception("Skinetic auto-connect failed")
        logger.warning("Auto device select: No suitable device; logging only")
        return "none"

    backoff_s = 2
    max_backoff_s = 30

    try:
        while True:
            try:
                logger.info(f"Connecting to FastAPI server at {websocket_url} ...")
                with ws_connect_sync(websocket_url, ping_interval=25, ping_timeout=10, close_timeout=5) as ws:
                    logger.info("Connected to FastAPI server.")
                    backoff_s = 2

                    for raw in ws:
                        if raw is None:
                            continue

                        if isinstance(raw, (bytes, bytearray)):
                            try:
                                raw = raw.decode("utf-8")
                            except UnicodeDecodeError:
                                logger.warning("Received non-UTF8 binary; ignoring.")
                                continue

                        # Keepalive
                        if raw == "__ping__":
                            try:
                                ws.send("__pong__")
                                logger.debug("Replied to __ping__ with __pong__")
                            except Exception as e:
                                logger.warning(f"Failed to send __pong__: {e}")
                            continue

                        # Parse upstream payload
                        try:
                            payload = json.loads(raw)
                            logger.debug("WS PAYLOAD:\n%s", _pp_json(payload, limit=4000))
                        except json.JSONDecodeError:
                            logger.warning("Received non-JSON message; ignoring.")
                            continue

                        # Server event envelope passthrough
                        if isinstance(payload, dict) and "type" in payload and "message" in payload:
                            logger.info(f"Server event: type={payload.get('type')} message={payload.get('message')}")
                            continue

                        try:
                            choice = device_mode
                            if device_mode == "auto":
                                choice = resolve_auto_choice()

                            # Parse into (duration_ms, [(node_index, intensity), ...]) frames
                            parsed_frames = FrameNodeParser.parse(payload, logger)

                            if debug:
                                logger.debug("Parsed frames:")
                                for dur, nodes in parsed_frames:
                                    logger.debug(f"  dur={dur}ms  nodes={nodes}")

                            # --- Buttplug output ---
                            if choice in ("buttplug", "both"):
                                if bp_runner and bp_runner._client._connected:
                                    try:
                                        bp_runner.send_payload(parsed_frames)
                                        if debug:
                                            logger.debug("[buttplug] send complete")
                                    except Exception:
                                        logger.exception("Buttplug send failed")
                                else:
                                    logger.warning("[buttplug] runner not connected; dropping payload.")

                            # --- Skinetic output (original SDK path, unchanged) ---
                            if choice in ("skinetic", "both"):
                                if skinetic_sender and skinetic_sender.available:
                                    try:
                                        if debug:
                                            skinetic_sender.dump_preview(payload, logger)
                                        skinetic_sender.send_payload(payload)
                                        if debug:
                                            logger.debug("[skinetic] send complete")
                                    except Exception:
                                        logger.exception("Skinetic send failed")
                                else:
                                    if debug:
                                        logger.debug("Skinetic not available; skipping.")

                            if choice not in ("buttplug", "skinetic", "both"):
                                if debug:
                                    logger.info("No output device selected; payload processed but not sent.")

                        except Exception:
                            logger.exception("Error during message handling")

            except (ConnectionClosed, WebSocketException) as e:
                logger.warning(f"WebSocket closed: {e}. Reconnecting in {backoff_s}s ...")
            except (ConnectionRefusedError, OSError) as e:
                logger.warning(f"Cannot connect: {e}. Retrying in {backoff_s}s ...")
            except KeyboardInterrupt:
                raise
            except Exception:
                logger.exception(f"Unexpected error. Reconnecting in {backoff_s}s ...")

            time.sleep(backoff_s)
            backoff_s = min(max_backoff_s, backoff_s * 2)

    except KeyboardInterrupt:
        logger.info("Interrupted by user. Shutting down.")
    finally:
        if bp_runner:
            bp_runner.stop()


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main():
    logger = setup_logging()
    try:
        cfg = load_config(logger)
    except Exception:
        logger.exception("Failed to load config; continuing with partial defaults.")
        cfg = {
            "ws_url": "", "client_id": "", "debug": False,
            "device": "buttplug",
            "buttplug": {"server_url": "ws://localhost:12345"},
            "skinetic": {},
        }

    if _as_bool(cfg.get("debug", False)):
        logger.setLevel(logging.DEBUG)
        for h in logger.handlers:
            h.setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled.")

    setup_sentry(cfg.get("glitchtip_dsn", ""), logger)

    if not cfg.get("ws_url"):
        logger.error("No WebSocket URL configured. Exiting.")
        sys.exit(2)

    websocket_client(cfg, logger)


if __name__ == "__main__":
    main()
