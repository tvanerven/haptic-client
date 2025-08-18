#!/usr/bin/env python3
# rpi_color_client.py
import os
import sys
import json
import time
import logging
from logging import Logger
from pathlib import Path
from typing import Any, Dict, List, Union, Optional

# --- Optional: remote troubleshooting via Glitchtip/Sentry
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

# --- IO
try:
    import serial  # pip install pyserial
except Exception:  # keep import optional for non-serial testing
    serial = None

from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

# =========================
# Paths / logging / sentry
# =========================
APP_NAME = "rpi-color-client"
DEFAULT_LOG_NAME = "rpi_color_client.log"


def script_dir() -> Path:
    # When frozen by PyInstaller, __file__ points inside the temp bundle; we still want the EXE dir.
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def setup_logging() -> Logger:
    log_file = script_dir() / DEFAULT_LOG_NAME
    logger = logging.getLogger(APP_NAME)
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


def setup_sentry_from_env(logger: Logger) -> None:
    dsn = os.getenv("BHX_SENTRY_DSN") or os.getenv("BHX_GLITCHTIP_DSN") or ""
    if not dsn:
        logger.info("No Glitchtip DSN provided; remote error reporting disabled.")
        return
    try:
        sentry_sdk.init(
            dsn=dsn,
            integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
            environment="raspbian",
            traces_sample_rate=0.0,
            send_default_pii=False,
            release=f"{APP_NAME}@1.0.0",
        )
        logger.info("Glitchtip (Sentry) reporting enabled.")
    except Exception:
        logger.exception("Failed to initialize Glitchtip (Sentry). Continuing without remote reporting.")


# =========================
# Color conversion
# =========================
class ColorConverter:
    """
    Accepts payloads shaped like:
      {
        "position": {"x": int, "y": int},        # optional for logging/trace
        "color": {"r": int, "g": int, "b": int}, # 0..255
        "intensity": int                         # optional, 0..255 (default 255)
      }

    Produces a flat stream:
      ["[L,9:R]", "[L,6:G]", "[L,4:B]", 160]  # 160 ms duration example
    """

    def __init__(self, colordata: Dict[str, Any], logger: Logger):
        self.logger = logger
        self._data: List[Union[str, int]] = []
        self._parse_colors(colordata)

    @staticmethod
    def _to_int(x: Any, default: int = 0) -> int:
        try:
            return int(x)
        except Exception:
            return default

    def _parse_colors(self, payload: Dict[str, Any]) -> None:
        color = payload.get("color", {}) or {}
        r = self._to_int(color.get("r", 0))
        g = self._to_int(color.get("g", 0))
        b = self._to_int(color.get("b", 0))
        base_intensity = self._to_int(payload.get("intensity", 255), 255)  # default full scale

        # Scale each channel by intensity (0..255)
        def scale(chan: int) -> int:
            v = max(0, min(255, chan))
            return int(round((v / 255.0) * max(0, min(255, base_intensity))))

        r_scaled = scale(r)
        g_scaled = scale(g)
        b_scaled = scale(b)

        # Map channels to node indices (adjust mapping to your hardware)
        self._data.append(f"[L,9:{r_scaled}]")  # Red motor
        self._data.append(f"[L,6:{g_scaled}]")  # Green motor
        self._data.append(f"[L,4:{b_scaled}]")  # Blue motor

        # Frame duration; allow override via payload["duration"], default 160ms
        duration_ms = self._to_int(payload.get("duration", 160), 160)
        self._data.append(max(0, duration_ms))

        self.logger.info(f"Color payload r={r} g={g} b={b} intensity={base_intensity} -> R{r_scaled} G{g_scaled} B{b_scaled} dur={duration_ms}ms")

    @property
    def data(self) -> List[Union[str, int]]:
        return self._data


# =========================
# Serial output
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
            # No serial configured; nothing to do.
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
                self.dev.write(stop)
                self.dev.flush()
                time.sleep(0.05)


# =========================
# WebSocket runtime
# =========================
def websocket_loop(logger: Logger):
    # Config via environment (easy to use in systemd or CI)
    # Example:
    #   BHX_WS_URL=wss://host:8000 BHX_CLIENT_ID=test  (client id appended if not present)
    ws_url = os.getenv("BHX_WS_URL", "ws://localhost:8000")
    client_id = os.getenv("BHX_CLIENT_ID", "test")

    # append suffix if missing
    suffix = f"/ws/listen/{client_id}"
    if not ws_url.endswith(suffix):
        ws_url = ws_url.rstrip("/") + suffix

    serial_port = os.getenv("BHX_SERIAL_PORT", "/dev/ttyACM0")
    serial_baud = int(os.getenv("BHX_SERIAL_BAUD", "9600"))
    debug = (os.getenv("BHX_DEBUG", "0").lower() in ("1", "true", "yes", "on"))

    sender = SerialSender(logger, port=serial_port if serial_port else None, baudrate=serial_baud)

    backoff_s = 2
    max_backoff_s = 30

    while True:
        try:
            logger.info(f"Connecting to {ws_url} ...")
            # Protocol-level pings (keeps NATs and proxies happy)
            with connect(ws_url, ping_interval=25, ping_timeout=10, close_timeout=5) as ws:
                logger.info("Connected.")
                backoff_s = 2  # reset backoff on success

                for raw in ws:
                    if raw is None:
                        continue

                    # Convert binary → text if needed
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

                    # JSON payload
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.warning("Received non-JSON message; ignoring.")
                        continue

                    # Server event envelopes (ignore)
                    if isinstance(payload, dict) and "type" in payload and "message" in payload:
                        logger.info(f"Server event: type={payload.get('type')} message={payload.get('message')}")
                        continue

                    try:
                        conv = ColorConverter(colordata=payload, logger=logger)
                        sender.send(conv.data)
                        if debug:
                            logger.info(f"Sent: {conv.data}")
                    except Exception:
                        logger.exception("Error during color conversion / serial send")

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


def main():
    logger = setup_logging()
    setup_sentry_from_env(logger)
    websocket_loop(logger)


if __name__ == "__main__":
    main()
