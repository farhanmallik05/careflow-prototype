"""Background worker daemon for asynchronous outbox message dispatch."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging

from careflow.persistence import SQLiteStore

logger = logging.getLogger("careflow.worker")


class OutboxWorker:
    """Asynchronous background worker that dispatches pending outbox notifications."""

    def __init__(
        self,
        store: SQLiteStore,
        *,
        poll_interval: float = 1.0,
        batch_size: int = 10,
    ) -> None:
        self.store = store
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self.dispatched_count = 0

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Start the background dispatcher task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("OutboxWorker started with poll_interval=%.1fs", self.poll_interval)

    async def stop(self) -> None:
        """Gracefully stop the background dispatcher task."""
        if not self._running:
            return
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("OutboxWorker stopped. Total dispatched: %d", self.dispatched_count)

    async def dispatch_once(self, *, now: datetime | None = None) -> int:
        """Process one batch of pending notifications and return count processed."""
        dispatch_time = now or datetime.now(timezone.utc)
        pending = self.store.pending_notifications(now=dispatch_time)
        processed = 0
        for notification in pending[: self.batch_size]:
            try:
                self.store.mark_notification_sent(notification["id"], sent_at=dispatch_time)
                processed += 1
                self.dispatched_count += 1
                logger.debug(
                    "Dispatched outbox notification %s to %s",
                    notification["id"],
                    notification.get("recipient"),
                )
            except Exception as exc:
                logger.error(
                    "Failed to dispatch notification %s: %s",
                    notification.get("id"),
                    exc,
                )
        return processed

    async def _run_loop(self) -> None:
        """Continuous background polling loop."""
        while self._running:
            try:
                await self.dispatch_once()
            except Exception as exc:
                logger.error("OutboxWorker iteration error: %s", exc)
            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break


__all__ = ["OutboxWorker"]
