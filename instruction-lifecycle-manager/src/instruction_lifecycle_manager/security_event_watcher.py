import asyncio
import logging
from datetime import datetime
from typing import Any

from instruction_lifecycle_manager.config import settings
from instruction_lifecycle_manager.security_event_broadcaster import SecurityEventBroadcaster
from instruction_lifecycle_manager.security_event_serialization import serialize_security_event
from instruction_lifecycle_manager.security_event_ui_store import SecurityEventUiStore

logger = logging.getLogger(__name__)


class SecurityEventWatcher:
    def __init__(self, store: SecurityEventUiStore) -> None:
        self._store = store

    @property
    def collection(self):
        return self._store.collection

    async def connect(self) -> None:
        await self._store.connect()

    async def watch(self, broadcaster: SecurityEventBroadcaster) -> None:
        try:
            await self._watch_change_stream(broadcaster)
        except Exception as exc:
            logger.warning(
                "security event change stream unavailable (%s); falling back to polling every %ss",
                exc,
                settings.ui_poll_interval_seconds,
            )
            await self._poll_loop(broadcaster)

    async def _watch_change_stream(self, broadcaster: SecurityEventBroadcaster) -> None:
        pipeline = [{"$match": {"operationType": "insert"}}]
        async with self.collection.watch(pipeline) as stream:
            logger.info("listening on MongoDB change stream for security events")
            async for change in stream:
                document = change.get("fullDocument")
                if not document:
                    continue
                event = serialize_security_event(document)
                event_id = event.get("event_id")
                if event_id:
                    self._store.seen_event_ids.add(event_id)
                await broadcaster.publish(event)

    async def _poll_loop(self, broadcaster: SecurityEventBroadcaster) -> None:
        while True:
            query: dict[str, Any] = {}
            if self._store.last_poll_at is not None:
                query["timestamp"] = {"$gt": self._store.last_poll_at}
            cursor = self.collection.find(query).sort("timestamp", 1)
            async for doc in cursor:
                event = serialize_security_event(doc)
                event_id = event.get("event_id")
                if event_id and not self._store.remember_event_id(event_id):
                    continue
                self._store.remember_poll_timestamp(event["timestamp"])
                await broadcaster.publish(event)
            await asyncio.sleep(settings.ui_poll_interval_seconds)
