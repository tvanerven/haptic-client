#!/usr/bin/env python3
# spn_color_haptic_client.py
import os
import sys
import asyncio
import logging
from logging import Logger
from pathlib import Path
from typing import Any, Optional

# Third-party & your libs
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from starlette.websockets import WebSocketDisconnect

from clients.spnclient_haptidesigner import SPNClient
from skinetic.skineticSDK import Skinetic

# ----------------------------
# Paths / config / logging
# ----------------------------
APP_FAMILY = "bhx-bridge"
DEFAULT_CONFIG_NAME = "config.yaml"
DEFAULT_LOG_NAME = "spn_client.log"


def script_dir() -> Path:
    # next to the EXE when frozen, otherwise next to this .py
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def user_config_dir() -> Path:
    # Linux style; fine on RPi too
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
            import json
            return json.loads(path.read_text(encoding="utf-8"))
        raise


def dump_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    except Exception:
        import json
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "on")


def env_override(cfg: dict) -> dict:
    # Endpoint
    cfg["ws_url"] = os.getenv("BHX_WS_URL", cfg.get("ws_url", ""))  # e.g., wss://host:8000
    cfg["client_id"] = os.getenv("BHX_CLIENT_ID", cfg.get("client_id", "hotrod"))

    # Behavior
    if "BHX_DEBUG" in os.environ:
        cfg["debug"] = _as_bool(os.getenv("BHX_DEBUG"))
    cfg["ping_interval"] = int(os.getenv("BHX_PING_INTERVAL", cfg.get("ping_interval", 25)))
    cfg["ping_timeout"] = int(os.getenv("BHX_PING_TIMEOUT", cfg.get("ping_timeout", 10)))

    # Backoff tuning
    cfg["reconnect_initial"] = int(os.getenv("BHX_RECONNECT_INITIAL", cfg.get("reconnect_initial", 2)))
    cfg["reconnect_max"] = int(os.getenv("BHX_RECONNECT_MAX", cfg.get("reconnect_max", 30)))

    # Sentry / Glitchtip
    cfg["glitchtip_dsn"] = os.getenv(
        "BHX_SENTRY_DSN",
        os.getenv("BHX_GLITCHTIP_DSN", cfg.get("glitchtip_dsn", "")),
    )

    return cfg


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


def setup_logging() -> Logger:
    log_file = script_dir() / DEFAULT_LOG_NAME
    logger = logging.getLogger("spn-client")
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
        logger.info("No Glitchtip DSN provided; remote error reporting disabled.")
        return
    try:
        sentry_sdk.init(
            dsn=dsn,
            integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
            environment=environment,
            traces_sample_rate=0.0,
            send_default_pii=False,
            release="spn_client@1.0.0",
        )
        logger.info("Glitchtip (Sentry) reporting enabled.")
    except Exception:
        logger.exception("Failed to initialize Glitchtip (Sentry). Continuing without remote reporting.")


def load_config(logger: Logger) -> dict:
    """
    Example config.yaml:

    ws_url: "wss://host:8000"
    client_id: "hotrod"
    debug: false
    glitchtip_dsn: ""
    ping_interval: 25
    ping_timeout: 10
    reconnect_initial: 2
    reconnect_max: 30
    """
    cfg_path = find_config_path()
    cfg = load_yaml(cfg_path) if cfg_path.exists() else {}
    cfg = env_override(cfg)

    if not cfg.get("client_id"):
        cfg["client_id"] = input("Client ID (e.g., hotrod): ").strip()

    if not cfg.get("ws_url"):
        base = input("WebSocket base (e.g., wss://host:8000): ").strip()
        cfg["ws_url"] = base
    cfg["ws_url"] = sanitize_ws_url(cfg["ws_url"], cfg["client_id"])

    cfg["debug"] = _as_bool(cfg.get("debug", False))

    save_to = user_config_dir() / DEFAULT_CONFIG_NAME
    dump_yaml(save_to, cfg)
    logger.info(f"Config saved to: {save_to}")
    return cfg


# ----------------------------
# Runtime with reconnect/ping
# ----------------------------
async def run_client(cfg: dict, logger: Logger):
    ws_url = cfg["ws_url"]
    debug = _as_bool(cfg.get("debug", False))
    ping_interval = int(cfg.get("ping_interval", 25))
    ping_timeout = int(cfg.get("ping_timeout", 10))

    backoff = int(cfg.get("reconnect_initial", 2))
    backoff_max = int(cfg.get("reconnect_max", 30))

    # Initialize Skinetic once; reconnects reuse it
    skinetic = Skinetic()
    skinetic.connect(output_type=Skinetic.OutputType.USB)
    logger.info("Skinetic connected over USB.")

    while True:
        try:
            # SPNClient is your class; assume it accepts (skinetic, url, **ws_opts)
            # If it supports kwargs like ping_interval/ping_timeout, pass them in:
            client = SPNClient(skinetic, ws_url, ping_interval=ping_interval, ping_timeout=ping_timeout)

            async with client:
                logger.info(f"Connected to {ws_url}")
                backoff = int(cfg.get("reconnect_initial", 2))  # reset on success

                async for message in client.listen():
                    # App-level heartbeat (if your server sends "__ping__")
                    if isinstance(message, str) and message == "__ping__":
                        if hasattr(client, "send") and callable(getattr(client, "send")):
                            try:
                                await client.send("__pong__")
                                logger.debug("Replied to __ping__ with __pong__")
                            except Exception as e:
                                logger.warning(f"Failed to send __pong__: {e}")
                        continue

                    try:
                        await client.process_messages(message)
                    except Exception:
                        logger.exception("Error during message processing")

        except WebSocketDisconnect:
            logger.warning("WebSocket disconnected.")
        except (ConnectionRefusedError, OSError) as e:
            logger.warning(f"Cannot connect: {e}")
        except Exception:
            logger.exception("Unexpected error in client loop")
        finally:
            logger.info(f"Reconnecting in {backoff}s ...")
            await asyncio.sleep(backoff)
            backoff = min(backoff_max, backoff * 2)


async def main():
    logger = setup_logging()
    try:
        cfg = load_config(logger)
    except Exception:
        logger.exception("Failed to load config; using minimal defaults")
        cfg = {"ws_url": "", "client_id": "hotrod", "debug": False}
    setup_sentry(cfg.get("glitchtip_dsn", ""), logger)

    if not cfg.get("ws_url"):
        logger.error("No WebSocket URL configured. Exiting.")
        sys.exit(2)

    await run_client(cfg, logger)


if __name__ == "__main__":
    asyncio.run(main())
