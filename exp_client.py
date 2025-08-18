#!/usr/bin/env python3
import os
import sys
import json
import time
import logging
from logging import Logger
from pathlib import Path
from typing import Any, Dict, List, Union, Optional

# ---- Optional: remote troubleshooting via Glitchtip/Sentry
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

# ---- IO
try:
    import serial  # pip install pyserial
except Exception:
    serial = None

from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

# ---- Skinetic + your SPN pipeline bits
try:
    from skinetic.skineticSDK import Skinetic
    from inputs.haptidesigner import FrameConverter as HDFrameConverter
    from inputs.image_processor import HapticProcessorInput
    from outputs.schemas import Output
except Exception:
    Skinetic = None  # allow running on boxes without the SDK
    HDFrameConverter = None
    HapticProcessorInput = None
    Output = None

def _pp_json(obj: Any, limit: int = 2000) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        s = str(obj)
    if len(s) > limit:
        s = s[:limit] + " ... (truncated)"
    return s

# =========================
# Config / paths
# =========================
APP_FAMILY = "haptic-bridge"
DEFAULT_CONFIG_NAME = "config.yaml"
DEFAULT_LOG_NAME = "rpi_client.log"


def is_rpi_linux() -> bool:
    return sys.platform.startswith("linux")


def bundled_base_dir() -> Path:
    """
    Directory that contains this script/binary.
    - When frozen with PyInstaller (_MEIPASS is temp), logs/config go next to the EXE.
    - When run as .py, logs/config go next to the .py file.
    """
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def user_config_dir() -> Path:
    """
    Follow Linux conventions: ~/.config/haptic-bridge
    """
    return Path.home() / ".config" / APP_FAMILY


def find_config_path() -> Path:
    """
    Priority:
    1) ~/.config/haptic-bridge/config.yaml  (user-editable, persisted)
    2) same folder as script/binary
    """
    p1 = user_config_dir() / DEFAULT_CONFIG_NAME
    if p1.exists():
        return p1
    return bundled_base_dir() / DEFAULT_CONFIG_NAME


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml  # pip install pyyaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        # allow .json file as fallback if user renames the extension
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


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "on")


def env_override(cfg: dict) -> dict:
    """
    Environment overrides:
      BHX_WS_URL, BHX_CLIENT_ID, BHX_DEBUG,
      BHX_SENTRY_DSN or BHX_GLITCHTIP_DSN,
      BHX_SERIAL_PORT, BHX_SERIAL_BAUD,
      BHX_DEVICE (auto|serial|skinetic|both),
      BHX_SKINETIC_OUTPUT (USB|...)
    """
    cfg["ws_url"] = os.getenv("BHX_WS_URL", cfg.get("ws_url", ""))
    cfg["client_id"] = os.getenv("BHX_CLIENT_ID", cfg.get("client_id", ""))

    if "BHX_DEBUG" in os.environ:
        cfg["debug"] = _as_bool(os.getenv("BHX_DEBUG"))

    # Device selector (which output backend to use)
    cfg["device"] = (os.getenv("BHX_DEVICE", cfg.get("device", "serial")) or "serial").lower()
    # allow "both" if you want to fan out to both devices
    if cfg["device"] not in ("auto", "serial", "skinetic", "both"):
        cfg["device"] = "serial"

    # Sentry/Glitchtip
    cfg["glitchtip_dsn"] = os.getenv(
        "BHX_SENTRY_DSN",
        os.getenv("BHX_GLITCHTIP_DSN", cfg.get("glitchtip_dsn", "")),
    )

    # Serial
    cfg["serial"] = cfg.get("serial", {})
    cfg["serial"]["port"] = os.getenv("BHX_SERIAL_PORT", cfg["serial"].get("port", ""))  # e.g., /dev/ttyACM0
    cfg["serial"]["baudrate"] = int(os.getenv("BHX_SERIAL_BAUD", cfg["serial"].get("baudrate", 9600)))

    # Skinetic
    cfg["skinetic"] = cfg.get("skinetic", {})
    cfg["skinetic"]["output_type"] = os.getenv("BHX_SKINETIC_OUTPUT", cfg["skinetic"].get("output_type", "USB"))

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
    Load config (yaml or json), apply env overrides, persist to ~/.config for next run.
    Example config.yaml:

    ws_url: "wss://host:8000"
    client_id: "moorchyk1"
    debug: false
    glitchtip_dsn: ""   # optional
    device: "auto"      # "auto" | "serial" | "skinetic" | "both"

    serial:
      port: "/dev/ttyACM0"
      baudrate: 9600

    skinetic:
      output_type: "USB"
    """
    cfg_path = find_config_path()
    cfg = load_yaml(cfg_path) if cfg_path.exists() else {}

    cfg = env_override(cfg)

    # Fill/sanitize minimal required fields
    if not cfg.get("client_id"):
        cfg["client_id"] = input("Enter client ID (e.g., moorchyk1): ").strip()

    if not cfg.get("ws_url"):
        ws = input("Enter WebSocket base (e.g., wss://host:8000): ").strip()
        cfg["ws_url"] = sanitize_websocket_url(ws, cfg["client_id"])
    else:
        cfg["ws_url"] = sanitize_websocket_url(cfg["ws_url"], cfg["client_id"])

    cfg["debug"] = _as_bool(cfg.get("debug", False))

    # Persist to ~/.config/haptic-bridge
    save_to = user_config_dir() / DEFAULT_CONFIG_NAME
    dump_yaml(save_to, cfg)
    logger.info(f"Config loaded. Saved to: {save_to}")
    return cfg


# =========================
# Logging & Sentry
# =========================
def setup_logging() -> Logger:
    log_file = bundled_base_dir() / DEFAULT_LOG_NAME  # same dir as script/exe
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
    sentry_logging = LoggingIntegration(
        level=logging.INFO,        # breadcrumbs at ≥ INFO
        event_level=logging.ERROR  # send events at ≥ ERROR
    )
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


# =========================
# Frame conversion → serial
# =========================
class SerialFrameConverter:
    """
    Converts a payload into a flat stream of serial commands:
      - For each frame_node: "[L,{node_index}:{intensity}]"
      - After each frame: frame['duration'] (ms) as an int, used for pacing.

    Accepts flexible shapes:
      dict: {"word": [frame, ...], ...}  or {"word": frame}
      list: [frame, frame, ...]
    Where frame may contain:
      duration: int (ms)
      frame_nodes: list|dict of {"node_index": int|list, "intensity": int|list}
    """

    def __init__(self, sentence: Union[Dict[str, Any], List[Dict[str, Any]]], logger: Logger, debug: bool = False):
        self.sentence = sentence
        self._data: List[Union[str, int]] = []
        self._logger = logger
        if debug:
            self._logger.info(f"Sentence received: {self.sentence}")
        self._parse_sentence()
        if debug:
            self._logger.info(f"Converted to: {self._data}")

    @staticmethod
    def _as_list(x):
        if x is None:
            return []
        if isinstance(x, (list, tuple)):
            return list(x)
        return [x]

    @staticmethod
    def _to_int(x, default=0):
        try:
            return int(x)
        except Exception:
            return default

    def _parse_sentence(self) -> None:
        if isinstance(self.sentence, dict):
            # values may be a single frame or a list
            for key, val in self.sentence.items():
                if isinstance(val, list):
                    self._parse_frames(val, label=key)
                elif isinstance(val, dict):
                    self._parse_frames([val], label=key)
                else:
                    # allow int as "pause"
                    if isinstance(val, int):
                        self._data.append(val)
                    else:
                        self._logger.warning(f"Ignoring {key}: unexpected type {type(val)}")
        elif isinstance(self.sentence, list):
            self._parse_frames(self.sentence, label="list")
        else:
            raise TypeError(f"Unsupported sentence type: {type(self.sentence)}")
        
    def dump_preview(self, logger: Logger, max_lines: int = 200):
        """Pretty-print the converted serial script."""
        logger.debug("=== SERIAL CONVERSION (items=%d) ===", len(self._data))
        shown = 0
        for idx, item in enumerate(self._data):
            if isinstance(item, str):
                logger.debug("  %03d CMD %s", idx, item)
            else:
                logger.debug("  %03d SLP %d ms", idx, item)
            shown += 1
            if shown >= max_lines:
                logger.debug("  ... (truncated)")
                break

    def _parse_frames(self, frames: List[Dict[str, Any]], label: str = "") -> None:
        for idx_frame, frame in enumerate(frames):
            if not isinstance(frame, dict):
                if isinstance(frame, int):
                    self._data.append(frame)
                else:
                    self._logger.warning(f"Ignoring non-dict frame {label}[{idx_frame}]: {type(frame)}")
                continue

            # duration
            dur = self._to_int(frame.get("duration", 0), 0)

            # frame_nodes can be dict or list
            fns = frame.get("frame_nodes", [])
            if isinstance(fns, dict):
                fns = [fns]
            elif not isinstance(fns, list):
                self._logger.warning(f"{label}[{idx_frame}]: frame_nodes has unexpected type {type(fns)}; treating as empty.")
                fns = []

            for fn_idx, fn in enumerate(fns):
                if not isinstance(fn, dict):
                    self._logger.warning(f"{label}[{idx_frame}].frame_nodes[{fn_idx}] not a dict; skipping.")
                    continue

                idxs = self._as_list(fn.get("node_index", []))
                vals = self._as_list(fn.get("intensity", []))

                if len(vals) == 1 and len(idxs) > 1:
                    vals = vals * len(idxs)

                for i, idx in enumerate(idxs):
                    try:
                        idx_i = int(idx)
                    except Exception:
                        self._logger.warning(f"{label}[{idx_frame}].frame_nodes[{fn_idx}]: bad node_index={idx}; skipping")
                        continue
                    val = self._to_int(vals[i] if i < len(vals) else 0, 0)
                    # Emit serial command
                    self._data.append(f"[L,{idx_i}:{val}]")

            # append duration at the end of each frame
            self._data.append(dur)

    def get_serial_device(self, port: str, baudrate: int = 9600, terminator: str = "\n"):
        if serial is None:
            raise RuntimeError("pyserial is not installed")
        ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            timeout=1,
            write_timeout=1,   # avoid blocking forever on write
            dsrdtr=False,
            rtscts=False,
        )
        # Some boards need a brief settle and DTR pulse
        try:
            ser.dtr = True
            ser.rts = False
        except Exception:
            pass
        time.sleep(0.05)
        if not ser.is_open:
            ser.open()
        setattr(ser, "_bhx_term", terminator)
        self._logger.info(f"Serial opened: {port} @ {baudrate}")
        return ser

    def send_serial_data(self, serial_device, data: List[Union[str, int]], logger: Logger) -> None:
        """
        Stream prepared data to the serial device with pacing.
        """
        # get terminator from config if present; default newline
        term = "\n"
        try:
            # serial_device is created by get_serial_device; we can stash the chosen terminator on it
            term = getattr(serial_device, "_bhx_term", "\n")
        except Exception:
            pass

        current_delay_ms = 0
        for item in data:
            if isinstance(item, str):
                try:
                    payload = (item + term).encode("utf-8")
                    max_packet_size = 64
                    for i in range(0, len(payload), max_packet_size):
                        chunk = payload[i:i + max_packet_size]
                        logger.info(f"Sending chunk: {chunk!r}")
                        serial_device.write(chunk)
                        serial_device.flush()
                        time.sleep(0.02)
                except Exception as e:
                    logger.error(f"Serial error while sending data: {e}")

            elif isinstance(item, int):
                # End of frame: wait duration, then send a stop/clear packet
                current_delay_ms = max(0, item)
                time.sleep(current_delay_ms / 1000.0)
                try:
                    stop = ("[L,all:0]" + term).encode("utf-8")
                    serial_device.write(stop)
                    serial_device.flush()
                    time.sleep(0.02)
                    logger.info(f"Frame stop sent after {current_delay_ms}ms")
                except Exception as e:
                    logger.error(f"Serial error while sending stop: {e}")


# =========================
# Skinetic output (SPN path)
# =========================
class SkineticSenderSPN:
    """
    Mirrors your SPNClient behavior using your models/utilities.
    """
    def __init__(self, logger: Logger, output_type: str = "USB"):
        self.logger = logger
        self.dev: Optional[Skinetic] = None
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
            frames = HDFrameConverter(payload)._skinetic  # [{order,node_index,intensity,duration}, ...]
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

    def dump_preview(self, payload: Union[Dict[str, Any], List[Dict[str, Any]]], logger: Logger):
        """Pretty-print _skinetic frames and the final Output JSON (without sending)."""
        if not self.available:
            logger.debug("Skinetic dump skipped (SDK or models not available).")
            return
        try:
            frames = HDFrameConverter(payload)._skinetic
            logger.debug("=== SKINETIC _skinetic frames (n=%d) ===", len(frames))
            for i, f in enumerate(frames[:50]):  # cap spam
                logger.debug("  %03d %s", i, f)
            if len(frames) > 50:
                logger.debug("  ... (truncated)")
            msg = HapticProcessorInput(frame_list=frames)
            output: Output = msg.format()
            logger.debug("=== SKINETIC Output JSON ===\n%s", _pp_json(output.model_dump(), limit=5000))
        except Exception:
            logger.exception("Failed to dump Skinetic preview")


# =========================
# WebSocket runtime
# =========================
def websocket_client(cfg: dict, logger: Logger):
    websocket_url = cfg["ws_url"]
    debug = _as_bool(cfg.get("debug", False))
    device_mode = (cfg.get("device") or cfg.get("output") or "serial").lower()  # auto | serial | skinetic | both
    if device_mode not in ("auto", "serial", "skinetic", "both"):
        device_mode = "serial"
    logger.info(f"Output device mode: {device_mode}")

    serial_cfg = cfg.get("serial", {})
    term = cfg.get("serial", {}).get("terminator", "\n")
    serial_port = serial_cfg.get("port") or ""
    serial_baud = int(serial_cfg.get("baudrate", 9600))

    skinetic_cfg = cfg.get("skinetic", {})
    skinetic_output_type = skinetic_cfg.get("output_type", "USB")
    skinetic_sender = SkineticSenderSPN(logger, output_type=skinetic_output_type) if device_mode in ("skinetic", "both", "auto") else None

    # In "auto" mode, prefer Skinetic if it's available and can connect; else fall back to serial if configured.
    def resolve_auto_choice() -> str:
        if skinetic_sender and skinetic_sender.available:
            try:
                skinetic_sender.connect()
                if skinetic_sender.dev and skinetic_sender.dev.get_connection_state() == skinetic_sender.dev.ConnectionState.Connected:
                    logger.info("Auto device select: Skinetic")
                    return "skinetic"
            except Exception:
                logger.exception("Skinetic auto-connect failed")
        if serial_port:
            logger.info("Auto device select: Serial")
            return "serial"
        logger.warning("Auto device select: No suitable device configured; logging only")
        return "none"

    backoff_s = 2
    max_backoff_s = 30

    while True:
        try:
            logger.info(f"Connecting to {websocket_url} ...")
            with connect(websocket_url, ping_interval=25, ping_timeout=10, close_timeout=5) as ws:
                logger.info("Connected.")
                backoff_s = 2  # reset backoff on success

                for raw in ws:
                    if raw is None:
                        continue

                    # Handle keepalive
                    if isinstance(raw, (bytes, bytearray)):
                        try:
                            raw = raw.decode("utf-8")
                        except UnicodeDecodeError:
                            logger.warning("Received non-UTF8 binary; ignoring.")
                            continue

                    if raw == "__ping__":
                        try:
                            ws.send("__pong__")
                            logger.debug("Replied to __ping__ with __pong__")
                        except Exception as e:
                            logger.warning(f"Failed to send __pong__: {e}")
                        continue

                    # Normal payload: expect JSON
                    try:
                        payload = json.loads(raw)
                        logger.debug("WS PAYLOAD:\n%s", _pp_json(payload, limit=4000))
                    except json.JSONDecodeError:
                        logger.warning("Received non-JSON message; ignoring.")
                        continue

                    # Ignore obvious server event envelopes
                    if isinstance(payload, dict) and "type" in payload and "message" in payload:
                        logger.info(f"Server event: type={payload.get('type')} message={payload.get('message')}")
                        continue

                    try:
                        # Decide device(s)
                        choice = device_mode
                        if device_mode == "auto":
                            choice = resolve_auto_choice()

                        # --- Always build and dump the serial conversion in debug ---
                        fc = SerialFrameConverter(sentence=payload, logger=logger, debug=False)
                        if debug:
                            fc.dump_preview(logger)

                        # --- Always build and dump the Skinetic conversion in debug (if libs present) ---
                        if skinetic_sender and skinetic_sender.available and debug:
                            skinetic_sender.dump_preview(payload, logger)

                        # SERIAL OUTPUT (only if chosen and configured)
                        if choice in ("serial", "both"):
                            if serial_port and serial is not None:
                                try:
                                    dev = fc.get_serial_device(port=serial_port, baudrate=serial_baud, terminator=term)
                                    fc.send_serial_data(dev, fc._data, logger)
                                    if debug:
                                        logger.debug("[serial] send complete")
                                except Exception:
                                    logger.exception("Serial send failed")
                            else:
                                if debug:
                                    logger.debug("Serial not configured; skipping actual serial send.")

                        # SKINETIC OUTPUT (only if chosen)
                        if choice in ("skinetic", "both"):
                            if skinetic_sender and skinetic_sender.available:
                                try:
                                    skinetic_sender.send_payload(payload)
                                    if debug:
                                        logger.debug("[skinetic] send complete")
                                except Exception:
                                    logger.exception("Skinetic send failed")
                            else:
                                if debug:
                                    logger.debug("Skinetic not available; skipping actual skinetic send.")

                        # NONE (auto could not resolve)
                        if choice not in ("serial", "skinetic", "both"):
                            if debug:
                                logger.info("No output device selected; payload processed but not sent.")

                    except Exception:
                        logger.exception("Error during message handling")

        except (ConnectionClosed, WebSocketException) as e:
            logger.warning(f"WebSocket closed: {e}. Reconnecting in {backoff_s}s ...")
        except (ConnectionRefusedError, OSError) as e:
            logger.warning(f"Cannot connect: {e}. Retrying in {backoff_s}s ...")
        except KeyboardInterrupt:
            logger.info("Interrupted by user. Exiting.")
            break
        except Exception:
            logger.exception(f"Unexpected error. Reconnecting in {backoff_s}s ...")

        time.sleep(backoff_s)
        backoff_s = min(max_backoff_s, backoff_s * 2)


# =========================
# Entry
# =========================
def main():
    logger = setup_logging()
    try:
        cfg = load_config(logger)
    except Exception:
        logger.exception("Failed to load config; continuing with partial defaults.")
        cfg = {"ws_url": "", "client_id": "", "debug": False, "device": "serial", "serial": {}, "skinetic": {}}

    # NEW: honor debug by raising log level to DEBUG
    if _as_bool(cfg.get("debug", False)):
        logger.setLevel(logging.DEBUG)
        for h in logger.handlers:
            h.setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled.")

    setup_sentry(cfg.get("glitchtip_dsn", ""), logger)

    setup_sentry(cfg.get("glitchtip_dsn", ""), logger)

    # Require a URL after loading config (allows interactive prompt)
    if not cfg.get("ws_url"):
        logger.error("No WebSocket URL configured. Exiting.")
        sys.exit(2)

    websocket_client(cfg, logger)


if __name__ == "__main__":
    main()
