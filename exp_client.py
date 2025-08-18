#!/usr/bin/env python3
import os
import sys
import json
import time
import logging
from logging import Logger
from pathlib import Path
from typing import Any, Dict, List, Union

# ---- Optional: remote troubleshooting via Glitchtip/Sentry
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

# ---- IO
import serial  # pip install pyserial
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException


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
    Follow Linux conventions: ~/.config/bhx-bridge
    """
    return Path.home() / ".config" / APP_FAMILY


def find_config_path() -> Path:
    """
    Priority:
    1) ~/.config/bhx-bridge/config.yaml  (user-editable, persisted)
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
    Environment overrides (keep parity with Windows client):
      BHX_WS_URL, BHX_CLIENT_ID, BHX_DEBUG,
      BHX_SENTRY_DSN or BHX_GLITCHTIP_DSN,
      BHX_SERIAL_PORT, BHX_SERIAL_BAUD
    """
    cfg["ws_url"] = os.getenv("BHX_WS_URL", cfg.get("ws_url", ""))
    cfg["client_id"] = os.getenv("BHX_CLIENT_ID", cfg.get("client_id", ""))
    if "BHX_DEBUG" in os.environ:
        cfg["debug"] = _as_bool(os.getenv("BHX_DEBUG"))

    # Sentry/Glitchtip
    cfg["glitchtip_dsn"] = os.getenv(
        "BHX_SENTRY_DSN",
        os.getenv("BHX_GLITCHTIP_DSN", cfg.get("glitchtip_dsn", "")),
    )

    # Serial
    cfg["serial"] = cfg.get("serial", {})
    cfg["serial"]["port"] = os.getenv("BHX_SERIAL_PORT", cfg["serial"].get("port", ""))  # e.g., /dev/ttyACM0
    cfg["serial"]["baudrate"] = int(os.getenv("BHX_SERIAL_BAUD", cfg["serial"].get("baudrate", 9600)))

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
    glitchtip_dsn: ""   # your Glitchtip project DSN (optional)
    serial:
      port: "/dev/ttyACM0"
      baudrate: 9600
    """
    cfg_path = find_config_path()
    cfg = load_yaml(cfg_path) if cfg_path.exists() else {}

    cfg = env_override(cfg)

    # Fill/sanitize minimal required fields
    if not cfg.get("client_id"):
        cfg["client_id"] = input("Enter client ID (e.g., moorchyk1): ").strip()

    if not cfg.get("ws_url"):
        ws = input("Enter WebSocket URL (e.g., wss://host:8000): ").strip()
        cfg["ws_url"] = sanitize_websocket_url(ws, cfg["client_id"])
    else:
        cfg["ws_url"] = sanitize_websocket_url(cfg["ws_url"], cfg["client_id"])

    cfg["debug"] = _as_bool(cfg.get("debug", False))

    # Persist to ~/.config/bhx-bridge
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
class FrameConverter:
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

    def get_serial_device(self, port: str, baudrate: int = 9600):
        ser = serial.Serial(port, baudrate, timeout=1)
        if not ser.is_open:
            ser.open()
        return ser

    def send_serial_data(self, serial_device, data: List[Union[str, int]], logger: Logger) -> None:
        """
        Stream prepared data to the serial device with pacing.
        """
        for item in data:
            if isinstance(item, str):
                try:
                    encoded = item.encode("utf-8")
                    max_packet_size = 64  # typical USB CDC packet size
                    for i in range(0, len(encoded), max_packet_size):
                        chunk = encoded[i:i + max_packet_size]
                        logger.info(f"Sending chunk: {chunk!r}")
                        serial_device.write(chunk)
                        serial_device.flush()
                        time.sleep(0.05)  # small inter-chunk delay
                except serial.SerialTimeoutException as e:
                    logger.error(f"Serial timeout while sending data: {e}")
            elif isinstance(item, int):
                # Duration is in ms
                time.sleep(max(0, item) / 1000.0)


# =========================
# WebSocket runtime
# =========================
def websocket_client(cfg: dict, logger: Logger):
    websocket_url = cfg["ws_url"]
    debug = _as_bool(cfg.get("debug", False))

    serial_cfg = cfg.get("serial", {})
    serial_port = serial_cfg.get("port") or ""
    serial_baud = int(serial_cfg.get("baudrate", 9600))

    backoff_s = 2
    max_backoff_s = 30

    while True:
        try:
            logger.info(f"Connecting to {websocket_url} ...")
            # Consider adding ping_interval / timeouts if your server expects protocol pings
            with connect(websocket_url, ping_interval=25, ping_timeout=10, close_timeout=5) as ws:
                logger.info("Connected.")
                backoff_s = 2  # reset backoff on success

                # Iterate incoming messages until the server closes the socket.
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
                    except json.JSONDecodeError:
                        logger.warning("Received non-JSON message; ignoring.")
                        continue

                    # Ignore obvious server event envelopes
                    if isinstance(payload, dict) and "type" in payload and "message" in payload:
                        logger.info(f"Server event: type={payload.get('type')} message={payload.get('message')}")
                        continue

                    try:
                        fc = FrameConverter(sentence=payload, logger=logger, debug=debug)

                        # SERIAL OUTPUT (enable if configured)
                        if serial_port:
                            try:
                                dev = fc.get_serial_device(port=serial_port, baudrate=serial_baud)
                                fc.send_serial_data(dev, fc._data, logger)
                            except Exception:
                                logger.exception("Serial send failed")
                        else:
                            # If no serial configured, just log the conversion when debug is on.
                            if debug:
                                logger.info("No serial port configured; frames logged only.")
                    except Exception:
                        logger.exception("Error during frame handling")

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

    # Load config and set up Glitchtip/Sentry
    try:
        cfg = load_config(logger)
    except Exception:
        # log to file/console and still try to continue with defaults
        logger.exception("Failed to load config; continuing with partial defaults.")
        cfg = {"ws_url": "", "client_id": "", "debug": False, "serial": {}}

    setup_sentry(cfg.get("glitchtip_dsn", ""), logger)

    # Require a URL after loading config (allows interactive prompt)
    if not cfg.get("ws_url"):
        logger.error("No WebSocket URL configured. Exiting.")
        sys.exit(2)

    websocket_client(cfg, logger)


if __name__ == "__main__":
    main()
