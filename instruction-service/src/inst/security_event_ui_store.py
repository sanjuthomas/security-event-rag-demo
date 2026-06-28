from datetime import datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection

from inst.config import settings
from inst.database import get_security_events_database
from inst.security_event_serialization import serialize_security_event

_NOTABLE_FILTER = {
    "$or": [
        {"severity": "ALERT"},
        {"event.outcome": "failure"},
    ]
}
_NOTABLE_EVENT_CAP = 100


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
        notable_cap = min(limit, _NOTABLE_EVENT_CAP)
        notable_docs = [
            doc
            async for doc in self.collection.find(_NOTABLE_FILTER)
            .sort("timestamp", -1)
            .limit(notable_cap)
        ]
        info_docs = [
            doc
            async for doc in self.collection.find({"severity": "INFO"})
            .sort("timestamp", -1)
            .limit(limit)
        ]
        merged_docs = _merge_recent_documents(notable_docs, info_docs, limit=limit)
        events = [serialize_security_event(doc) for doc in merged_docs]
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


def _merge_recent_documents(
    notable_docs: list[dict[str, Any]],
    info_docs: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    notable_by_id: dict[str, dict[str, Any]] = {}
    for doc in notable_docs:
        event_id = doc.get("event_id")
        if event_id and event_id not in notable_by_id:
            notable_by_id[event_id] = doc
    notable = sorted(notable_by_id.values(), key=_document_timestamp, reverse=True)[:limit]

    info_slots = max(0, limit - len(notable))
    if info_slots == 0:
        return notable

    info_candidates = [
        doc
        for doc in info_docs
        if doc.get("event_id") and doc["event_id"] not in notable_by_id
    ]
    info = sorted(info_candidates, key=_document_timestamp, reverse=True)[:info_slots]
    return sorted(notable + info, key=_document_timestamp, reverse=True)


def _document_timestamp(doc: dict[str, Any]) -> datetime:
    ts = doc.get("timestamp")
    if isinstance(ts, datetime):
        return ts.replace(tzinfo=None) if ts.tzinfo else ts
    if ts:
        return _parse_timestamp(str(ts))
    return datetime.min


def _parse_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).replace(tzinfo=None)
