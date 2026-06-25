from datetime import datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection

from instruction_lifecycle_manager.config import settings
from instruction_lifecycle_manager.database import get_security_events_database
from instruction_lifecycle_manager.security_event_serialization import serialize_security_event


class SecurityEventUiStore:
    def __init__(self) -> None:
        self._last_poll_at: datetime | None = None
        self._seen_event_ids: set[str] = set()

    @property
    def collection(self) -> AsyncIOMotorCollection:
        return get_security_events_database()[settings.security_events_collection]

    async def connect(self) -> None:
        latest = await self.collection.find_one({}, sort=[("timestamp", -1)])
        if latest and latest.get("timestamp"):
            ts = latest["timestamp"]
            self._last_poll_at = ts if isinstance(ts, datetime) else _parse_timestamp(str(ts))

    async def list_recent(self, *, limit: int) -> list[dict[str, Any]]:
        cursor = self.collection.find({}).sort("timestamp", -1).limit(limit)
        events = [serialize_security_event(doc) async for doc in cursor]
        for event in events:
            self._seen_event_ids.add(event["event_id"])
        if events:
            self._last_poll_at = _parse_timestamp(events[0]["timestamp"])
        return events

    async def get_by_event_id(self, event_id: str) -> dict[str, Any] | None:
        document = await self.collection.find_one({"event_id": event_id})
        if document is None:
            return None
        return serialize_security_event(document)

    def remember_poll_timestamp(self, timestamp: str) -> None:
        parsed = _parse_timestamp(timestamp)
        if self._last_poll_at is None or parsed > self._last_poll_at:
            self._last_poll_at = parsed

    def remember_event_id(self, event_id: str) -> bool:
        if event_id in self._seen_event_ids:
            return False
        self._seen_event_ids.add(event_id)
        return True

    @property
    def last_poll_at(self) -> datetime | None:
        return self._last_poll_at

    @property
    def seen_event_ids(self) -> set[str]:
        return self._seen_event_ids


def _parse_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).replace(tzinfo=None)
