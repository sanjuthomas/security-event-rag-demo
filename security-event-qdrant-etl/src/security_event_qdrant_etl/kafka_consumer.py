from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiokafka import AIOKafkaConsumer

from security_event_qdrant_etl.config import settings
from security_event_qdrant_etl.pipeline import SecurityEventPipeline

logger = logging.getLogger(__name__)


class SecurityEventKafkaConsumer:
    def __init__(self, pipeline: SecurityEventPipeline) -> None:
        self.pipeline = pipeline
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if not settings.kafka_enabled:
            logger.info("Kafka consumer disabled")
            return

        self._consumer = AIOKafkaConsumer(
            settings.kafka_security_events_topic,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.kafka_consumer_group,
            enable_auto_commit=True,
            auto_offset_reset="earliest",
            value_deserializer=lambda value: json.loads(value.decode("utf-8")),
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._run())
        logger.info(
            "Kafka consumer started topic=%s group=%s",
            settings.kafka_security_events_topic,
            settings.kafka_consumer_group,
        )

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None

    async def _run(self) -> None:
        assert self._consumer is not None
        try:
            async for message in self._consumer:
                try:
                    await self._handle_message(message.value)
                except Exception:
                    logger.exception(
                        "failed to process Kafka message offset=%s",
                        message.offset,
                    )
        except asyncio.CancelledError:
            raise

    async def _handle_message(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict) or "event_id" not in payload:
            logger.warning("skipping invalid Kafka payload: %s", payload)
            return
        await self.pipeline.process_security_event(payload)
