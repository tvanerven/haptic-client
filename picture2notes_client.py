#!/usr/bin/env python3
# unified_client_multiout_spn.py
import os
import sys
import json
import time
import logging
from logging import Logger
from pathlib import Path
from typing import Any, Dict, List, Union, Optional

# Glitchtip/Sentry
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

# Serial (optional)
try:
    import serial  # pip install pyserial
except Exception:
    serial = None

# Skinetic SDK + your schema pipeline
from skinetic.skineticSDK import Skinetic
from inputs.haptidesigner import FrameConverter as HDFrameConverter
from inputs.image_processor import HapticProcessorInput
from outputs.schemas import Output

# WebSockets
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

# =========================
# Paths / logging / config
# =========================
APP_FAMILY = "bhx-bridge"
APP_NAME   = "unified-client-multiout-spn"
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
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        if path.suffix.lower() == ".json":
            import json as _json
            return _json.loads(path.read_text(encoding="utf-8"))
        raise

def dump_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    except Exception:
        import json as _json
        path.write_text(_json.dumps(data, indent=2), encoding="utf-8")

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

    cfg["ping_interval"]     = int(os.getenv("BHX_PING_INTERVAL",  cfg.get("ping_interval", 25)))
    cfg["ping_timeout"]      = int(os.getenv("BHX_PING_TIMEOUT",   cfg.get("ping_timeout", 10)))
    cfg["reconnect_initial"] = int(os.getenv("BHX_RECONNECT_INITIAL", cfg.get("reconnect_initial", 2)))
    cfg["reconnect_max"]     = int(os.getenv("BHX_RECONNECT_MAX",  cfg.get("reconnect_max", 30)))

    # Output backend(s): "serial" | "skinetic" | "both"
    cfg["output"] = (os.getenv("BHX_OUTPUT", cfg.get("output", "serial")) or "serial").lower()

    # Serial config
    cfg["serial"] = cfg.get("serial", {})
    cfg["serial"]["port"]     = os.getenv("BHX_SERIAL_PORT", cfg["serial"].get("port", ""))  # e.g. /dev/ttyACM0
    cfg["serial"]["baudrate"] = int(os.getenv("BHX_SERIAL_BAUD", cfg["serial"].get("baudrate", 9600)))

    # Skinetic config
    cfg["skinetic"] = cfg.get("skinetic", {})
    cfg["skinetic"]["output_type"] = os.getenv("BHX_SKINETIC_OUTPUT", cfg["skinetic"].get("output_type", "USB"))

    # Glitchtip
    cfg["glitchtip_dsn"] = cfg.get("glitchtip_dsn", os.getenv("BHX_SENTRY_DSN") or os.getenv("BHX_GLITCHTIP_DSN") or "")

    return cfg

def load_config(logger: Logger) -> dict:
    """
    Example config.yaml:

    ws_url: "wss://host:8000"
    client_id: "test"
    debug: false
    environment: "raspbian"
    glitchtip_dsn: ""
    ping_interval: 25
    ping_timeout: 10
    reconnect_initial: 2
    reconnect_max: 30

    # Choose output backend(s): "serial" | "skinetic" | "both"
    output: "both"

    serial:
      port: "/dev/ttyACM0"
      baudrate: 9600

    skinetic:
      output_type: "USB"
    """
    cfg_path = find_config_path()
    cfg = load_yaml(cfg_path) if cfg_path.exists() else {}
    cfg = env_override(cfg)

    if not cfg.get("client_id"):
        cfg["client_id"] = input("Client ID: ").strip() or "test"
    if not cfg.get("ws_url"):
        base = input("WebSocket base (e.g., wss://host:8000): ").strip() or "ws://localhost:8000"
        cfg["ws_url"] = base
    cfg["ws_url"] = sanitize_ws_url(cfg["ws_url"], cfg["client_id"])

    save_to = user_config_dir() / DEFAULT_CONFIG_NAME
    dump_yaml(save_to, cfg)
    logger.info(f"Config saved to: {save_to}")
    return cfg

# =========================
# Converters for Serial
# =========================
class ColorToSerial:
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
        self.logger.info(f"COLOR → serial R{rS} G{gS} B{bS} dur={dur}ms")

    @property
    def data(self) -> List[Union[str, int]]:
        return self._data

class ContourToSerial:
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

    def _parse_sentence(self, s) -> None:
        if isinstance(s, dict) and "type" in s and "message" in s:
            self.logger.info(f"Ignoring server event: {s.get('type')} {s.get('message')}")
            return
        if isinstance(s, dict):
            for key, val in s.items():
                frames = [val] if isinstance(val, dict) else (val if isinstance(val, list) else None)
                if frames is None:
                    if isinstance(val, int): self._data.append(val)
                    else: self.logger.warning(f"Ignoring {key}: unexpected type {type(val)}")
                    continue
                self._parse_frames(frames, key)
        elif isinstance(s, list):
            self._parse_frames(s, "list")
        elif isinstance(s, int):
            self._data.append(s)
        else:
            self.logger.warning(f"Unexpected payload type {type(s)}")

    def _parse_frames(self, frames, label):
        for i, frame in enumerate(frames):
            if not isinstance(frame, dict):
                if isinstance(frame, int): self._data.append(frame)
                else: self.logger.warning(f"Ignoring non-dict frame {label}[{i}]: {type(frame)}")
                continue
            dur = self._to_int(frame.get("duration", 0), 0)
            fns = frame.get("frame_nodes", [])
            if isinstance(fns, dict): fns = [fns]
            elif not isinstance(fns, list): fns = []
            for j, fn in enumerate(fns):
                if not isinstance(fn, dict):
                    self.logger.warning(f"{label}[{i}].frame_nodes[{j}] not a dict; skipping.")
                    continue
                idxs = self._as_list(fn.get("node_index", []))
                vals = self._as_list(fn.get("intensity", []))
                if len(vals) == 1 and len(idxs) > 1:
                    vals = vals * len(idxs)
                for k, idx in enumerate(idxs):
                    try: idx_i = int(idx)
                    except Exception:
                        self.logger.warning(f"{label}[{i}].frame_nodes[{j}]: bad node_index={idx}")
                        continue
                    val = self._to_int(vals[k] if k < len(vals) else 0, 0)
                    self._data.append(f"[L,{idx_i}:{val}]")
            self._data.append(dur)

    @property
    def data(self) -> List[Union[str, int]]:
        return self._data

# =========================
# Output backends
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
                    self.dev.write(chunk); self.dev.flush()
                    time.sleep(0.05)
            elif isinstance(item, int):
                time.sleep(max(0, item) / 1000.0)
                stop = "[L,all:0]".encode("utf-8")
                self.dev.write(stop); self.dev.flush()
                time.sleep(0.05)

class SkineticSenderSPN:
    """
    Skinetic output that mirrors your SPNClient:
      - For CONTOUR: reuse inputs.haptidesigner.FrameConverter to build frame_list
      - For COLOR: synthesize a tiny frame_list with three nodes (9,6,4) and the given duration
      - Build HapticProcessorInput -> Output, then call load_pattern_json / play_effect / unload_pattern
    """
    def __init__(self, logger: Logger, output_type: str = "USB"):
        self.logger = logger
        self.dev: Optional[Skinetic] = None
        self.output_type = output_type

    def connect(self):
        if self.dev:
            return
        self.dev = Skinetic()
        ot = getattr(Skinetic.OutputType, self.output_type, Skinetic.OutputType.USB)
        self.dev.connect(output_type=ot)
        self.logger.info(f"Skinetic connected (output_type={self.output_type}).")

    def _ensure_connected(self):
        if not self.dev:
            self.connect()
        if not self.dev:
            raise RuntimeError("Skinetic device not available")

    def send_contour(self, payload: Union[Dict[str, Any], List[Dict[str, Any]]]):
        self._ensure_connected()
        # Build frames via your verified converter:
        frames = HDFrameConverter(payload)._skinetic  # list of dicts with order/node_index/intensity/duration
        msg = HapticProcessorInput(frame_list=frames)
        output: Output = msg.format()
        js = output.model_dump_json()
        if self.dev.get_connection_state() == self.dev.ConnectionState.Connected:
            pattern_id = self.dev.load_pattern_json(js)
            self.dev.play_effect(pattern_id)
            self.dev.unload_pattern(pattern_id)
        else:
            self.logger.warning("Skinetic not connected; dropping contour message.")

    def send_color(self, payload: Dict[str, Any]):
        self._ensure_connected()
        # Scale color → intensities like serial path, then build a minimal frame list
        color = (payload or {}).get("color", {}) or {}
        intensity = int((payload or {}).get("intensity", 255))
        duration = int((payload or {}).get("duration", 160))

        def scale(chan: Any) -> int:
            try:
                c = int(chan)
            except Exception:
                c = 0
            c = max(0, min(255, c))
            return int(round((c/255.0) * max(0, min(255, intensity))))

        rS = scale(color.get("r", 0))
        gS = scale(color.get("g", 0))
        bS = scale(color.get("b", 0))

        # three nodes: 9 (R), 6 (G), 4 (B)
        frames = [
            {"order": 0, "node_index": 9, "intensity": rS, "duration": duration},
            {"order": 1, "node_index": 6, "intensity": gS, "duration": duration},
            {"order": 2, "node_index": 4, "intensity": bS, "duration": duration},
        ]
        msg = HapticProcessorInput(frame_list=frames)
        output: Output = msg.format()
        js = output.model_dump_json()
        if self.dev.get_connection_state() == self.dev.ConnectionState.Connected:
            pattern_id = self.dev.load_pattern_json(js)
            self.dev.play_effect(pattern_id)
            self.dev.unload_pattern(pattern_id)
        else:
            self.logger.warning("Skinetic not connected; dropping color message.")

# =========================
# Mode detection
# =========================
class Mode:
    AUTO = "auto"
    COLOR = "color"
    CONTOUR = "contour"

def detect_mode(payload: Any) -> Optional[str]:
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
    ws_url = sanitize_ws_url(cfg["ws_url"], cfg["client_id"])
    debug  = _as_bool(cfg.get("debug", False))
    mode   = cfg.get("mode", Mode.AUTO).lower()  # you can add this to yaml if you want; defaults to AUTO

    output_choice = (cfg.get("output") or "serial").lower()  # "serial"|"skinetic"|"both"

    # Serial
    serial_port = cfg.get("serial", {}).get("port") or ""
    serial_baud = int(cfg.get("serial", {}).get("baudrate", 9600))
    serial_sender = SerialSender(logger, port=serial_port if "serial" in output_choice else None,
                                 baudrate=serial_baud)

    # Skinetic (verified SPN flow)
    skinetic_output_type = cfg.get("skinetic", {}).get("output_type", "USB")
    skinetic_sender = SkineticSenderSPN(logger, output_type=skinetic_output_type) if "skinetic" in output_choice else None

    ping_interval = int(cfg.get("ping_interval", 25))
    ping_timeout  = int(cfg.get("ping_timeout", 10))
    backoff_s     = int(cfg.get("reconnect_initial", 2))
    max_backoff_s = int(cfg.get("reconnect_max", 30))

    logger.info(f"Outputs: {output_choice}")

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

                    if raw == "__ping__":
                        try:
                            ws.send("__pong__"); logger.debug("Replied to __ping__ with __pong__")
                        except Exception as e:
                            logger.warning(f"Failed to send __pong__: {e}")
                        continue

                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.warning("Received non-JSON message; ignoring.")
                        continue

                    if isinstance(payload, dict) and "type" in payload and "message" in payload:
                        logger.info(f"Server event: type={payload.get('type')} message={payload.get('message')}")
                        continue

                    # Commands (optional): set/get output or mode
                    if isinstance(payload, dict) and "cmd" in payload:
                        cmd = str(payload.get("cmd", "")).lower()
                        if cmd == "set_output":
                            value = str(payload.get("value", "")).lower()
                            if value in ("serial", "skinetic", "both"):
                                output_choice = value
                                logger.info(f"Output switched to: {output_choice}")
                                try: ws.send(json.dumps({"ok": True, "output": output_choice}))
                                except Exception: pass
                        elif cmd == "get_output":
                            try: ws.send(json.dumps({"output": output_choice}))
                            except Exception: pass
                        elif cmd == "set_mode":
                            value = str(payload.get("value", "")).lower()
                            if value in (Mode.COLOR, Mode.CONTOUR, Mode.AUTO):
                                mode = value
                                logger.info(f"Mode switched to: {mode}")
                                try: ws.send(json.dumps({"ok": True, "mode": mode}))
                                except Exception: pass
                        elif cmd == "get_mode":
                            try: ws.send(json.dumps({"mode": mode}))
                            except Exception: pass
                        continue

                    effective_mode = mode
                    if mode == Mode.AUTO:
                        inferred = detect_mode(payload)
                        if inferred:
                            effective_mode = inferred
                        else:
                            logger.warning("AUTO could not infer mode; ignoring payload.")
                            continue

                    # SERIAL path
                    if "serial" in output_choice and serial_port:
                        try:
                            if effective_mode == Mode.COLOR:
                                sconv = ColorToSerial(payload, logger)
                                serial_sender.send(sconv.data)
                            else:
                                sconv = ContourToSerial(payload, logger)
                                serial_sender.send(sconv.data)
                            if debug: logger.info("[serial] sent")
                        except Exception:
                            logger.exception("Serial send failed")

                    # SKINETIC path (SPN flow)
                    if "skinetic" in output_choice and skinetic_sender is not None:
                        try:
                            if effective_mode == Mode.COLOR:
                                skinetic_sender.send_color(payload)
                            else:
                                skinetic_sender.send_contour(payload)
                            if debug: logger.info("[skinetic] sent")
                        except Exception:
                            logger.exception("Skinetic send failed")

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
        cfg = {"ws_url":"ws://localhost:8000", "client_id":"test", "output":"serial", "serial":{}, "skinetic":{}}
    setup_sentry_from_cfg(cfg, logger)

    if not cfg.get("ws_url"):
        logger.error("No WebSocket URL configured. Exiting.")
        sys.exit(2)

    websocket_loop(cfg, logger)

if __name__ == "__main__":
    main()
