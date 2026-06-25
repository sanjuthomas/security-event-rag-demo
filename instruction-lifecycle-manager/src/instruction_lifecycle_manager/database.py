from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from instruction_lifecycle_manager.config import settings

_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.mongodb_uri)
    return _client


def get_database() -> AsyncIOMotorDatabase:
    return get_client()[settings.mongodb_database]


def get_security_events_database() -> AsyncIOMotorDatabase:
    return get_client()[settings.security_events_database]


async def connect() -> None:
    client = get_client()
    await client.admin.command("ping")
    db = get_database()
    collection = db.instructions
    await collection.create_index(
        [("instruction_id", 1), ("version_number", 1)],
        unique=True,
    )
    await collection.create_index(
        [("instruction_id", 1)],
        unique=True,
        partialFilterExpression={"out": None},
        name="instruction_id_current_unique",
    )
    await collection.create_index("status")
    await collection.create_index("owning_lob")
    await collection.create_index("wire_scope")
    await collection.create_index("in")

    security_events = get_security_events_database()[settings.security_events_collection]
    await security_events.create_index("timestamp")
    await security_events.create_index("severity")
    await security_events.create_index("event.action")
    await security_events.create_index("event.outcome")
    await security_events.create_index("actor.user_id")
    await security_events.create_index("resource.id")


async def close() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
