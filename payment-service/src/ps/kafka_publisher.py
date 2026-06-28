from __future__ import annotations

import json
import logging
from typing import Any

from aiokafka import AIOKafkaProducer

from ps.config import settings

logger = logging.getLogger(__name__)


class PaymentKafkaPublisher:
    def __init__(self) -> None:
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        if not settings.kafka_enabled:
            logger.info("Kafka publishing disabled")
            return
        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
        )
        await self._producer.start()
        logger.info(
            "Kafka producer started payments=%s events=%s",
            settings.kafka_payments_topic,
            settings.kafka_security_events_topic,
        )

    async def close(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def publish_payment(self, fact: dict[str, Any]) -> None:
        """Publish full cumulative payment state to ssi-payments."""
        if self._producer is None:
            return
        payment_id = fact.get("payment_id", "")
        try:
            await self._producer.send_and_wait(
                settings.kafka_payments_topic,
                value=fact,
                key=payment_id or None,
            )
        except Exception:
            logger.exception("failed to publish payment %s to Kafka", payment_id)

    async def publish_security_event(self, event: dict[str, Any]) -> None:
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
            logger.exception("failed to publish payment security event %s to Kafka", event_id)


kafka_publisher = PaymentKafkaPublisher()
