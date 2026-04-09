"""Scan lifecycle controller — asyncio-based pause / resume / stop signals."""

import asyncio
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class ScanStoppedError(Exception):
    """Raised inside an agent node when a stop has been requested."""


class ScanController:
    """
    Holds one pair of asyncio Events per active scan run:
      - _stop   : set when user requests stop
      - _resume : cleared when paused, set when running (so waiting tasks block on clear)

    All three operations are safe to call from any coroutine on the same event loop
    (FastAPI / uvicorn use a single loop).
    """

    def __init__(self):
        self._stop:   Dict[str, asyncio.Event] = {}
        self._resume: Dict[str, asyncio.Event] = {}
        self._paused: Dict[str, bool]          = {}

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def register(self, scan_run_id: str) -> None:
        self._stop[scan_run_id]   = asyncio.Event()
        self._resume[scan_run_id] = asyncio.Event()
        self._resume[scan_run_id].set()       # not paused initially
        self._paused[scan_run_id] = False
        logger.info(f"[ScanController] registered {scan_run_id}")

    def is_registered(self, scan_run_id: str) -> bool:
        return scan_run_id in self._stop

    def cleanup(self, scan_run_id: str) -> None:
        self._stop.pop(scan_run_id, None)
        self._resume.pop(scan_run_id, None)
        self._paused.pop(scan_run_id, None)
        logger.info(f"[ScanController] cleaned up {scan_run_id}")

    # ── Commands ───────────────────────────────────────────────────────────

    def request_pause(self, scan_run_id: str) -> None:
        if scan_run_id in self._resume:
            self._resume[scan_run_id].clear()
            self._paused[scan_run_id] = True
            logger.info(f"[ScanController] pause requested for {scan_run_id}")

    def request_resume(self, scan_run_id: str) -> None:
        if scan_run_id in self._resume:
            self._paused[scan_run_id] = False
            self._resume[scan_run_id].set()
            logger.info(f"[ScanController] resume requested for {scan_run_id}")

    def request_stop(self, scan_run_id: str) -> None:
        if scan_run_id in self._stop:
            self._stop[scan_run_id].set()
            # Unblock any pause-wait so the stop propagates immediately
            if scan_run_id in self._resume:
                self._resume[scan_run_id].set()
            logger.info(f"[ScanController] stop requested for {scan_run_id}")

    # ── Queries ────────────────────────────────────────────────────────────

    def should_stop(self, scan_run_id: str) -> bool:
        ev = self._stop.get(scan_run_id)
        return ev.is_set() if ev else False

    def is_paused(self, scan_run_id: str) -> bool:
        return self._paused.get(scan_run_id, False)

    # ── Checkpoint (called at the top of every agent node) ─────────────────

    async def checkpoint(self, scan_run_id: str) -> None:
        """
        Call at the start of each agent node.
        • If stop was requested  → raises ScanStoppedError immediately.
        • If pause was requested → waits (non-blocking for the event loop) until resumed or stopped.
        • Otherwise             → returns immediately.
        """
        if self.should_stop(scan_run_id):
            raise ScanStoppedError(f"Scan {scan_run_id} stopped by user.")

        resume_ev = self._resume.get(scan_run_id)
        if resume_ev and not resume_ev.is_set():
            logger.info(f"[ScanController] {scan_run_id} paused — waiting for resume…")
            await resume_ev.wait()

        # Re-check stop in case stop was requested while paused
        if self.should_stop(scan_run_id):
            raise ScanStoppedError(f"Scan {scan_run_id} stopped by user.")


scan_controller = ScanController()
