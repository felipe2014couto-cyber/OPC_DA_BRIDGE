"""Destination receiver ingesting streaming telemetry into the bounded latest-value cache.

Implements bounded incoming queueing, stale monitoring loop, and
automatic disconnect marking upon transport closure.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from opc_da_bridge.core.cache import BoundedLatestValueCache, UpdateResult
from opc_da_bridge.protocol.messages import DataStreamPayload

logger = logging.getLogger(__name__)


class DestinationReceiver:
    """Receiver ingesting incoming stream batches into BoundedLatestValueCache."""

    def __init__(
        self,
        cache: BoundedLatestValueCache,
        queue_maxsize: int = 1000,
        stale_check_interval_s: float = 1.0,
    ) -> None:
        self.cache = cache
        self.queue_maxsize = queue_maxsize
        self.stale_check_interval_s = stale_check_interval_s

        self._queue: asyncio.Queue[DataStreamPayload] = asyncio.Queue(maxsize=queue_maxsize)
        self._running = False
        self._worker_task: asyncio.Task[None] | None = None
        self._stale_task: asyncio.Task[None] | None = None

        self.metrics = {
            "batches_enqueued": 0,
            "batches_dropped_queue_full": 0,
            "items_processed": 0,
            "items_accepted": 0,
            "items_duplicates": 0,
            "items_out_of_order": 0,
        }

    async def start(self) -> None:
        """Start background processing worker and stale monitor."""
        self._running = True
        self._worker_task = asyncio.create_task(self._process_queue())
        self._stale_task = asyncio.create_task(self._monitor_stale())

    async def stop(self) -> None:
        """Stop receiver and cancel background tasks."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        if self._stale_task:
            self._stale_task.cancel()
            try:
                await self._stale_task
            except asyncio.CancelledError:
                pass

    async def on_data_received(self, data_stream: DataStreamPayload) -> None:
        """Callback invoked by TlsTransportServer when a DataStreamPayload arrives."""
        try:
            self._queue.put_nowait(data_stream)
            self.metrics["batches_enqueued"] += 1
        except asyncio.QueueFull:
            # Bounded queue policy: drop oldest batch to prevent memory exhaustion
            # Balance asyncio.Queue task accounting using task_done() so queue.join() does not hang.
            try:
                _ = self._queue.get_nowait()
                self._queue.task_done()
                self._queue.put_nowait(data_stream)
                self.metrics["batches_dropped_queue_full"] += 1
                self.metrics["batches_enqueued"] += 1
            except Exception:
                pass

    async def on_client_connected(self, source_id: str, session_id: str) -> None:
        """Callback invoked by TlsTransportServer when a client successfully authenticates."""
        logger.info(f"Binding active session {session_id} to source {source_id}")
        self.cache.bind_active_session(source_id, session_id)

    async def on_client_disconnected(self, session_id: str) -> None:
        """Callback invoked by TlsTransportServer when a client connection drops."""
        logger.info(f"Marking cache disconnected for session {session_id}")
        count = self.cache.mark_disconnected(session_id)
        logger.info(f"Transitioned {count} items to Bad [Not Connected]")

    async def _process_queue(self) -> None:
        """Process stream payloads from the bounded queue into the cache."""
        while self._running:
            try:
                payload = await self._queue.get()
                session_id = payload.session_id
                seq = payload.sequence

                for item_update in payload.items:
                    self.metrics["items_processed"] += 1
                    sample = item_update.to_item_sample(session_id=session_id, sequence=seq, source_id=payload.source_id)
                    res = self.cache.put_sample(sample)

                    if res == UpdateResult.ACCEPTED:
                        self.metrics["items_accepted"] += 1
                    elif res == UpdateResult.DUPLICATE_IDEMPOTENT:
                        self.metrics["items_duplicates"] += 1
                    elif res == UpdateResult.OUT_OF_ORDER_REJECTED:
                        self.metrics["items_out_of_order"] += 1
                    elif res in (UpdateResult.STALE_SESSION_REJECTED, UpdateResult.UNBOUND_SESSION_REJECTED):
                        self.metrics["items_stale_session"] = self.metrics.get("items_stale_session", 0) + 1

                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"Error processing item updates in receiver: {exc}")

    async def _monitor_stale(self) -> None:
        """Periodically check cache for stale items."""
        while self._running:
            try:
                await asyncio.sleep(self.stale_check_interval_s)
                stale_items = self.cache.check_and_update_stale()
                if stale_items:
                    logger.debug(f"Marked {len(stale_items)} items as stale")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"Error checking stale items: {exc}")
