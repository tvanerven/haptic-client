#!/usr/bin/env python3
# unified_client.py
import os
import sys
import json
import time
import logging
from logging import Logger
from pathlib import Path
from typing import Any, Dict, List, Union, Optional

# Remote troubleshooting (Glitchtip/Sentry)
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

# Serial (optional on dev machines)
try:
    import serial  # pip install pyserial
except Exception:
    serial = None

# WebSocket (sync client fits nicely with simple loops)
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

# =========================
# Paths / logging / config
# =========================
APP_FAMILY = "bhx-bridge"
APP_NAME   = "unified-client"
DEFAULT_CONFIG_NAME = "config.yaml"
DEFAULT_LOG_NAME    = "unified_client.log"

def script_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def user_config_dir() -> Path:
    return Path.home() / ".config" / APP_FAMILY

def find_config_path() -> Path:
    p1 = user_config_dir() / DEFAULT_CONFIG_NAME
    if p1.exists():
        return p1
    return script_dir() / DEFAULT_CONFIG_NAME

def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml  # pip install pyyaml
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

def _as_bool(v: Any) -> bool:
    if isinstance(v, bool): return v
    if v is None: return False
    return str(v).strip().lower() in ("1", "true", "yes", "on")

def setup_logging() -> Logger:
    log_file = script_dir() / DEFAULT_LOG_NAME
    logger = logging.getLogger(APP_NAME)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    fh.setLevel(logging.INFO); fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO); ch.setFormatter(fmt)

    logger.addHandler(fh); logger.addHandler(ch)
    logger.info(f"Logging to: {log_file}")
    return logger

def setup_sentry_from_cfg(cfg: dict, logger: Logger) -> None:
    dsn = cfg.get("glitchtip_dsn") or os.getenv("BHX_SENTRY_DSN") or os.getenv("BHX_GLITCHTIP_DSN") or ""
    if not dsn:
        logger.info("No Glitchtip DSN provided; remote error reporting disabled.")
        return
    try:
        sentry_sdk.init(
            dsn=dsn,
            integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
            environment=cfg.get("environment", "raspbian"),
            traces_sample_rate=0.0,
            send_default_pii=False,
            release=f"{APP_NAME}@1.0.0",
        )
        logger.info("Glitchtip (Sentry) reporting enabled.")
    except Exception:
        logger.exception("Failed to initialize Glitchtip (Sentry). Continuing without remote reporting.")

def sanitize_ws_url(base: str, client_id: str) -> str:
    base = (base or "").rstrip("/")
    if not (base.startswith("ws://") or base.startswith("wss://")):
        base = base.replace("http://", "ws://").replace("https://", "wss://")
        if not (base.startswith("ws://") or base.startswith("wss://")):
            base = f"wss://{base}"
    suffix = f"/ws/listen/{client_id}" if client_id else "/ws/listen/"
    if not base.endswith(suffix):
        base = base + suffix
    return base

def env_override(cfg: dict) -> dict:
    cfg["ws_url"]   = os.getenv("BHX_WS_URL", cfg.get("ws_url", "ws://localhost:8000"))
    cfg["client_id"]= os.getenv("BHX_CLIENT_ID", cfg.get("client_id", "test"))
    cfg["debug"]    = _as_bool(os.getenv("BHX_DEBUG", cfg.get("debug", False)))
    cfg["mode"]     = os.getenv("BHX_MODE", cfg.get("mode", "auto")).lower() # "auto"|"color"|"contour"

    cfg["ping_interval"]   = int(os.getenv("BHX_PING_INTERVAL", cfg.get("ping_interval", 25)))
    cfg["ping_timeout"]    = int(os.getenv("BHX_PING_TIMEOUT",  cfg.get("ping_timeout", 10)))
    cfg["reconnect_initial"]=int(os.getenv("BHX_RECONNECT_INITIAL", cfg.get("reconnect_initial", 2)))
    cfg["reconnect_max"]   = int(os.getenv("BHX_RECONNECT_MAX", cfg.get("reconnect_max", 30)))

    cfg["serial"] = cfg.get("serial", {})
    cfg["serial"]["port"]     = os.getenv("BHX_SERIAL_PORT", cfg["serial"].get("port", ""))  # e.g. /dev/ttyACM0
    cfg["serial"]["baudrate"] = int(os.getenv("BHX_SERIAL_BAUD", cfg["serial"].get("baudrate", 9600)))

    # Glitchtip also read in setup_sentry_from_cfg
    cfg["glitchtip_dsn"] = cfg.get("glitchtip_dsn", os.getenv("BHX_SENTRY_DSN") or os.getenv("BHX_GLITCHTIP_DSN") or "")

    return cfg

def load_config(logger: Logger) -> dict:
    """
    Example config.yaml:

    ws_url: "wss://host:8000"
    client_id: "test"
    debug: false
    glitchtip_dsn: ""
    environment: "raspbian"
    mode: "auto"    # "auto" | "color" | "contour"
    ping_interval: 25
    ping_timeout: 10
    reconnect_initial: 2
    reconnect_max: 30
    serial:
      port: "/dev/ttyACM0"
      baudrate: 9600
    """
    cfg_path = find_config_path()
    cfg = load_yaml(cfg_path) if cfg_path.exists() else {}
    cfg = env_override(cfg)

    # minimal interactive fill (only once if you want)
    if not cfg.get("client_id"):
        cfg["client_id"] = input("Client ID: ").strip() or "test"
    if not cfg.get("ws_url"):
        base = input("WebSocket base (e.g., wss://host:8000): ").strip() or "ws://localhost:8000"
        cfg["ws_url"] = base
    cfg["ws_url"] = sanitize_ws_url(cfg["ws_url"], cfg["client_id"])

    # persist merged config to ~/.config
    save_to = user_config_dir() / DEFAULT_CONFIG_NAME
    dump_yaml(save_to, cfg)
    logger.info(f"Config saved to: {save_to}")
    return cfg

# =========================
# Serial sender
# =========================
class SerialSender:
    def __init__(self, logger: Logger, port: Optional[str], baudrate: int = 9600):
        self.logger = logger
        self.port = port
        self.baudrate = baudrate
        self.dev = None

    def _ensure_open(self):
        if not self.port:
            return
        if serial is None:
            raise RuntimeError("pyserial is not installed")
        if self.dev and getattr(self.dev, "is_open", False):
            return
        self.dev = serial.Serial(self.port, self.baudrate, timeout=1)
        if not self.dev.is_open:
            self.dev.open()
        self.logger.info(f"Serial opened: {self.port} @ {self.baudrate}")

    def send(self, items: List[Union[str, int]]):
        if not self.port:
            return
        self._ensure_open()
        for item in items:
            if isinstance(item, str):
                encoded = item.encode("utf-8")
                max_packet = 64
                for i in range(0, len(encoded), max_packet):
                    chunk = encoded[i:i + max_packet]
                    self.logger.info(f"Serial chunk: {chunk!r}")
                    self.dev.write(chunk)
                    self.dev.flush()
                    time.sleep(0.05)
            elif isinstance(item, int):
                time.sleep(max(0, item) / 1000.0)
                stop = "[L,all:0]".encode("utf-8")
                self.dev.write(stop); self.dev.flush()
                time.sleep(0.05)

# =========================
# Converters
# =========================
class ColorConverter:
    """Maps color payload to three motors + duration."""
    def __init__(self, payload: Dict[str, Any], logger: Logger):
        self.logger = logger
        self._data: List[Union[str, int]] = []
        self._parse(payload)

    @staticmethod
    def _to_int(x: Any, default: int = 0) -> int:
        try: return int(x)
        except Exception: return default

    def _parse(self, p: Dict[str, Any]) -> None:
        color = p.get("color", {}) or {}
        r = self._to_int(color.get("r", 0)); g = self._to_int(color.get("g", 0)); b = self._to_int(color.get("b", 0))
        intensity = self._to_int(p.get("intensity", 255), 255)

        def scale(chan: int) -> int:
            v = max(0, min(255, chan))
            return int(round((v/255.0) * max(0, min(255, intensity))))

        rS, gS, bS = scale(r), scale(g), scale(b)
        self._data += [f"[L,9:{rS}]", f"[L,6:{gS}]", f"[L,4:{bS}]"]
        dur = self._to_int(p.get("duration", 160), 160)
        self._data.append(max(0, dur))
        self.logger.info(f"COLOR r={r} g={g} b={b} intensity={intensity} -> R{rS} G{gS} B{bS} dur={dur}ms")

    @property
    def data(self): return self._data

class ContourConverter:
    """Flexible parser for your 'frame_nodes' frames."""
    def __init__(self, sentence: Union[Dict[str, Any], List[Dict[str, Any]]], logger: Logger):
        self.logger = logger
        self._data: List[Union[str, int]] = []
        self._parse_sentence(sentence)

    @staticmethod
    def _as_list(x):
        if x is None: return []
        if isinstance(x, (list, tuple)): return list(x)
        return [x]

    @staticmethod
    def _to_int(x, default=0):
        try: return int(x)
        except Exception: return default

    def _parse_sentence(self, sentence) -> None:
        if isinstance(sentence, dict) and "type" in sentence and "message" in sentence:
            self.logger.info(f"Ignoring server event: {sentence.get('type')} {sentence.get('message')}")
            return

        if isinstance(sentence, dict):
            for key, val in sentence.items():
                frames = [val] if isinstance(val, dict) else (val if isinstance(val, list) else None)
                if frames is None:
                    if isinstance(val, int): self._data.append(val)
                    else: self.logger.warning(f"Ignoring {key}: unexpected type {type(val)}")
                    continue
                self._parse_frames(frames, key)
        elif isinstance(sentence, list):
            self._parse_frames(sentence, "list")
        elif isinstance(sentence, int):
            self._data.append(sentence)
        else:
            self.logger.warning(f"Unexpected payload type {type(sentence)}")

    def _parse_frames(self, frames, label):
        for idx_frame, frame in enumerate(frames):
            if not isinstance(frame, dict):
                if isinstance(frame, int): self._data.append(frame)
                else: self.logger.warning(f"Ignoring non-dict frame {label}[{idx_frame}]: {type(frame)}")
                continue
            dur = self._to_int(frame.get("duration", 0), 0)
            fns = frame.get("frame_nodes", [])
            if isinstance(fns, dict): fns = [fns]
            elif not isinstance(fns, list): fns = []
            for fn_idx, fn in enumerate(fns):
                if not isinstance(fn, dict):
                    self.logger.warning(f"{label}[{idx_frame}].frame_nodes[{fn_idx}] not a dict; skipping.")
                    continue
                idxs = self._as_list(fn.get("node_index", []))
                vals = self._as_list(fn.get("intensity", []))
                if len(vals) == 1 and len(idxs) > 1:
                    vals = vals * len(idxs)
                for i, idx in enumerate(idxs):
                    try: idx_i = int(idx)
                    except Exception:
                        self.logger.warning(f"{label}[{idx_frame}].frame_nodes[{fn_idx}]: bad node_index={idx}")
                        continue
                    val = self._to_int(vals[i] if i < len(vals) else 0, 0)
                    self._data.append(f"[L,{idx_i}:{val}]")
            self._data.append(dur)

    @property
    def data(self): return self._data

# =========================
# Mode management
# =========================
class Mode:
    AUTO = "auto"
    COLOR = "color"
    CONTOUR = "contour"

def detect_mode(payload: Any) -> Optional[str]:
    """
    Heuristics when cmd not given:
    - dict with 'color' -> COLOR
    - dict with any value that is dict/list with 'frame_nodes' -> CONTOUR
    - list of frames with dicts having 'frame_nodes' -> CONTOUR
    """
    if isinstance(payload, dict):
        if "color" in payload:
            return Mode.COLOR
        for v in payload.values():
            if isinstance(v, dict) and "frame_nodes" in v:
                return Mode.CONTOUR
            if isinstance(v, list) and any(isinstance(x, dict) and "frame_nodes" in x for x in v):
                return Mode.CONTOUR
    if isinstance(payload, list) and any(isinstance(x, dict) and "frame_nodes" in x for x in payload):
        return Mode.CONTOUR
    return None

# =========================
# WebSocket runtime
# =========================
def websocket_loop(cfg: dict, logger: Logger):
    # Compose url with suffix
    ws_url = sanitize_ws_url(cfg["ws_url"], cfg["client_id"])
    debug  = _as_bool(cfg.get("debug", False))
    mode   = cfg.get("mode", Mode.AUTO).lower()

    serial_port = cfg.get("serial", {}).get("port") or ""
    serial_baud = int(cfg.get("serial", {}).get("baudrate", 9600))
    sender = SerialSender(logger, port=serial_port if serial_port else None, baudrate=serial_baud)

    ping_interval = int(cfg.get("ping_interval", 25))
    ping_timeout  = int(cfg.get("ping_timeout", 10))
    backoff_s     = int(cfg.get("reconnect_initial", 2))
    max_backoff_s = int(cfg.get("reconnect_max", 30))

    logger.info(f"Startup mode: {mode}")

    while True:
        try:
            logger.info(f"Connecting to {ws_url} ...")
            with connect(ws_url, ping_interval=ping_interval, ping_timeout=ping_timeout, close_timeout=5) as ws:
                logger.info("Connected.")
                backoff_s = int(cfg.get("reconnect_initial", 2))  # reset on success

                for raw in ws:
                    if raw is None:
                        continue

                    if isinstance(raw, (bytes, bytearray)):
                        try:
                            raw = raw.decode("utf-8")
                        except UnicodeDecodeError:
                            logger.warning("Received non-UTF8 binary; ignoring.")
                            continue

                    # App-level heartbeat
                    if raw == "__ping__":
                        try:
                            ws.send("__pong__")
                            logger.debug("Replied to __ping__ with __pong__")
                        except Exception as e:
                            logger.warning(f"Failed to send __pong__: {e}")
                        continue

                    # Parse JSON
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.warning("Received non-JSON message; ignoring.")
                        continue

                    # Server envelopes
                    if isinstance(payload, dict) and "type" in payload and "message" in payload:
                        logger.info(f"Server event: type={payload.get('type')} message={payload.get('message')}")
                        continue

                    # Commands: {"cmd":"set_mode","value":"color"} | {"cmd":"get_mode"}
                    if isinstance(payload, dict) and "cmd" in payload:
                        cmd = str(payload.get("cmd", "")).lower()
                        if cmd == "set_mode":
                            value = str(payload.get("value", "")).lower()
                            if value in (Mode.COLOR, Mode.CONTOUR, Mode.AUTO):
                                mode = value
                                logger.info(f"Mode switched via command to: {mode}")
                                # optional: acknowledge
                                try: ws.send(json.dumps({"ok": True, "mode": mode}))
                                except Exception: pass
                            else:
                                logger.warning(f"Ignoring unknown mode value: {value}")
                        elif cmd == "get_mode":
                            try: ws.send(json.dumps({"mode": mode}))
                            except Exception: pass
                        else:
                            logger.info(f"Ignoring unknown command: {cmd}")
                        continue

                    # No command → choose handling path
                    effective_mode = mode
                    if mode == Mode.AUTO:
                        inferred = detect_mode(payload)
                        if inferred:
                            effective_mode = inferred
                        else:
                            logger.warning("AUTO could not infer mode; ignoring payload.")
                            continue

                    try:
                        if effective_mode == Mode.COLOR:
                            conv = ColorConverter(payload, logger)
                            sender.send(conv.data)
                            if debug: logger.info(f"Sent COLOR: {conv.data}")
                        elif effective_mode == Mode.CONTOUR:
                            conv = ContourConverter(payload, logger)
                            sender.send(conv.data)
                            if debug: logger.info(f"Sent CONTOUR: {conv.data}")
                        else:
                            logger.warning(f"Unknown mode {effective_mode}; skipping payload.")
                    except Exception:
                        logger.exception("Error during conversion / serial send")

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
        logger.exception("Failed to load config; falling back to defaults")
        cfg = {"ws_url":"ws://localhost:8000", "client_id":"test", "mode":"auto", "serial":{}}
    setup_sentry_from_cfg(cfg, logger)

    if not cfg.get("ws_url"):
        logger.error("No WebSocket URL configured. Exiting.")
        sys.exit(2)

    websocket_loop(cfg, logger)

if __name__ == "__main__":
    main()
