from __future__ import annotations

import logging

from ps.config import settings
from ps.database import get_db
from ps.models.payment import Payment

logger = logging.getLogger(__name__)


class PaymentNotFoundError(Exception):
    pass


class PaymentRepository:
    @property
    def _col(self):
        return get_db()[settings.mongodb_collection]

    async def insert(self, payment: Payment) -> None:
        await self._col.insert_one(payment.to_mongo())

    async def find_by_id(self, payment_id: str) -> Payment:
        doc = await self._col.find_one({"payment_id": payment_id})
        if doc is None:
            raise PaymentNotFoundError(payment_id)
        return Payment.from_mongo(doc)

    async def update(self, payment: Payment) -> None:
        await self._col.replace_one(
            {"payment_id": payment.payment_id},
            payment.to_mongo(),
        )

    async def list(
        self,
        *,
        instruction_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[Payment]:
        filt: dict = {}
        if instruction_id:
            filt["instruction_id"] = instruction_id
        if status:
            filt["status"] = status
        cursor = self._col.find(filt).sort("created_at", -1).limit(limit)
        return [Payment.from_mongo(doc) async for doc in cursor]

    async def ensure_indexes(self) -> None:
        await self._col.create_index("payment_id", unique=True)
        await self._col.create_index(
            [("payment_id", 1), ("version_number", 1)],
            unique=True,
            name="payment_id_version_unique",
        )
        await self._col.create_index("instruction_id")
        await self._col.create_index("status")
        await self._col.create_index([("created_at", -1)])
        logger.info("payment collection indexes ensured")
