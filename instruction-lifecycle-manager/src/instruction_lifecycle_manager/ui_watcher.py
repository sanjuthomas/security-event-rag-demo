import asyncio
import logging
from datetime import datetime
from typing import Any

from instruction_lifecycle_manager.config import settings
from instruction_lifecycle_manager.database import get_database
from instruction_lifecycle_manager.service import _to_response
from instruction_lifecycle_manager.storage import document_to_versioned_instruction
from instruction_lifecycle_manager.ui_broadcaster import InstructionBroadcaster

logger = logging.getLogger(__name__)


def _parse_timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).replace(tzinfo=None)


def instruction_from_document(document: dict[str, Any]) -> dict[str, Any]:
    record = document_to_versioned_instruction(document)
    return _to_response(record).model_dump(mode="json", by_alias=True)


class InstructionWatcher:
    def __init__(self) -> None:
        self._last_poll_at: datetime | None = None
        self._seen_keys: set[tuple[str, int]] = set()

    @property
    def collection(self):
        return get_database()["instructions"]

    async def connect(self) -> None:
        latest = await self.collection.find_one({"out": None}, sort=[("in", -1)])
        if latest and latest.get("in"):
            self._last_poll_at = _parse_timestamp(latest["in"])

    async def watch(self, broadcaster: InstructionBroadcaster) -> None:
        try:
            await self._watch_change_stream(broadcaster)
        except Exception as exc:
            logger.warning(
                "instruction change stream unavailable (%s); falling back to polling every %ss",
                exc,
                settings.ui_poll_interval_seconds,
            )
            await self._poll_loop(broadcaster)

    async def _watch_change_stream(self, broadcaster: InstructionBroadcaster) -> None:
        pipeline = [{"$match": {"operationType": "insert"}}]
        async with self.collection.watch(pipeline, full_document="updateLookup") as stream:
            logger.info("listening on MongoDB change stream for instructions")
            async for change in stream:
                document = change.get("fullDocument")
                if not document:
                    continue
                instruction = instruction_from_document(document)
                self._remember(instruction)
                await broadcaster.publish(instruction)

    async def _poll_loop(self, broadcaster: InstructionBroadcaster) -> None:
        while True:
            query: dict[str, Any] = {"out": None}
            if self._last_poll_at is not None:
                query["in"] = {"$gt": _format_timestamp(self._last_poll_at)}

            cursor = self.collection.find(query).sort("in", 1)
            async for document in cursor:
                instruction = instruction_from_document(document)
                key = self._instruction_key(instruction)
                if key in self._seen_keys:
                    continue
                self._remember(instruction)
                valid_in = _parse_timestamp(instruction["record_in"])
                if self._last_poll_at is None or valid_in > self._last_poll_at:
                    self._last_poll_at = valid_in
                await broadcaster.publish(instruction)

            await asyncio.sleep(settings.ui_poll_interval_seconds)

    def _instruction_key(self, instruction: dict[str, Any]) -> tuple[str, int]:
        return (instruction["instruction_id"], instruction["version_number"])

    def _remember(self, instruction: dict[str, Any]) -> None:
        self._seen_keys.add(self._instruction_key(instruction))
        valid_in = _parse_timestamp(instruction["record_in"])
        if self._last_poll_at is None or valid_in > self._last_poll_at:
            self._last_poll_at = valid_in


def _format_timestamp(value: datetime) -> str:
    return value.isoformat() + "Z"
