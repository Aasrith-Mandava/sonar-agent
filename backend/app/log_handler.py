"""
In-memory ring-buffer log handler + async queue for SSE streaming, plus a
rotating file handler so application logs survive restarts and are tailable
from disk.
"""

import asyncio
import logging
from collections import deque
from datetime import datetime, UTC
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Deque, Dict, Any

# Subscribers: each is an asyncio.Queue that receives log dicts
_subscribers: list[asyncio.Queue] = []

# Ring buffer of the last 1000 log records (for "catch-up" on new SSE connections)
_ring: Deque[Dict[str, Any]] = deque(maxlen=1000)

# Where the file logs go
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOG_DIR / "app.log"


class BroadcastLogHandler(logging.Handler):
    """Captures Python log records, stores in ring buffer, and fans out to SSE queues."""

    LEVEL_MAP = {
        logging.DEBUG:    "debug",
        logging.INFO:     "info",
        logging.WARNING:  "warning",
        logging.ERROR:    "error",
        logging.CRITICAL: "critical",
    }

    def emit(self, record: logging.LogRecord) -> None:
        # Skip very noisy sqlalchemy echoes in the broadcast
        if record.name.startswith("sqlalchemy.engine"):
            return

        entry: Dict[str, Any] = {
            "ts":      datetime.now(UTC).isoformat(),
            "level":   self.LEVEL_MAP.get(record.levelno, "info"),
            "logger":  record.name,
            "message": self.format(record),
        }
        _ring.append(entry)

        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        for q in list(_subscribers):
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                pass


def install_handler() -> None:
    """
    Attach the BroadcastLogHandler (in-memory + SSE) AND a RotatingFileHandler
    (writes to backend/logs/app.log, 5MB × 5 backups) to the root logger.
    Idempotent — safe to call multiple times.
    """
    root = logging.getLogger()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if not any(isinstance(h, BroadcastLogHandler) for h in root.handlers):
        broadcast = BroadcastLogHandler()
        broadcast.setFormatter(fmt)
        broadcast.setLevel(logging.DEBUG)
        root.addHandler(broadcast)

    if not any(
        isinstance(h, RotatingFileHandler) and getattr(h, "_sonaragent_file", False)
        for h in root.handlers
    ):
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                LOG_FILE,
                maxBytes=5 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler._sonaragent_file = True  # marker
            file_handler.setFormatter(fmt)
            file_handler.setLevel(logging.INFO)
            root.addHandler(file_handler)
            logging.getLogger(__name__).info(f"Application log file: {LOG_FILE}")
        except Exception as exc:
            logging.getLogger(__name__).warning(
                f"Could not attach RotatingFileHandler: {exc}"
            )


def get_ring_snapshot() -> list[Dict[str, Any]]:
    return list(_ring)


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    try:
        _subscribers.remove(q)
    except ValueError:
        pass
