from __future__ import annotations

import json
import logging
from typing import Any

from aiokafka import AIOKafkaProducer

from instruction_lifecycle_manager.config import settings

logger = logging.getLogger(__name__)


class SecurityEventKafkaPublisher:
    """Publishes security events to Kafka for downstream stream processing."""

    def __init__(self) -> None:
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        if not settings.kafka_enabled:
            logger.info("Kafka publishing disabled")
            return

        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda value: json.dumps(value, default=str).encode("utf-8"),
            key_serializer=lambda key: key.encode("utf-8") if key else None,
        )
        await self._producer.start()
        logger.info(
            "Kafka producer started topic=%s brokers=%s",
            settings.kafka_security_events_topic,
            settings.kafka_bootstrap_servers,
        )

    async def close(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def publish(self, event: dict[str, Any]) -> None:
        if self._producer is None:
            return

        event_id = event.get("event_id", "")
        try:
            await self._producer.send_and_wait(
                settings.kafka_security_events_topic,
                value=event,
                key=event_id or None,
            )
        except Exception:
            logger.exception("failed to publish security event %s to Kafka", event_id)


kafka_publisher = SecurityEventKafkaPublisher()
