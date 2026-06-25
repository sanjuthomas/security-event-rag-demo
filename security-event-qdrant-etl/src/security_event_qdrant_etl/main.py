import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from security_event_qdrant_etl.config import settings
from security_event_qdrant_etl.health import component_status
from security_event_qdrant_etl.instruction_client import InstructionClient
from security_event_qdrant_etl.kafka_consumer import SecurityEventKafkaConsumer
from security_event_qdrant_etl.neo4j_client import Neo4jGraphWriter
from security_event_qdrant_etl.ollama_client import OllamaEmbeddingClient
from security_event_qdrant_etl.pipeline import SecurityEventPipeline
from security_event_qdrant_etl.qdrant_store import QdrantHybridStore

__version__ = "0.1.0"

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

instruction_store = InstructionClient()
neo4j_writer = Neo4jGraphWriter()
ollama_client = OllamaEmbeddingClient()
qdrant_store = QdrantHybridStore()
pipeline = SecurityEventPipeline(
    instruction_store=instruction_store,
    neo4j_writer=neo4j_writer,
    ollama_client=ollama_client,
    qdrant_store=qdrant_store,
)
kafka_consumer = SecurityEventKafkaConsumer(pipeline)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=settings.search_default_limit, ge=1, le=50)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=logging.INFO)
    await pipeline.start()
    await kafka_consumer.start()
    try:
        await ollama_client.warmup()
        if qdrant_store.has_collection():
            qdrant_store.ensure_collection(ollama_client.dimension)
    except Exception as exc:
        logger.warning("search backends not fully warmed up yet: %s", exc)
    logger.info("security event ETL and search console started")
    yield
    await kafka_consumer.close()
    await pipeline.close()


app = FastAPI(
    title="Security Event Search Console",
    description="Query Neo4j graph and Qdrant hybrid vectors produced by the ETL pipeline",
    version=__version__,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "UP"}


@app.get("/api/stats")
async def stats() -> dict:
    components = await component_status(
        kafka_consumer=kafka_consumer,
        qdrant_store=qdrant_store,
        neo4j_writer=neo4j_writer,
        ollama_client=ollama_client,
    )
    return {
        "components": components,
        "all_ok": all(component["ok"] for component in components.values()),
    }


@app.get("/api/components")
async def components() -> dict:
    return await component_status(
        kafka_consumer=kafka_consumer,
        qdrant_store=qdrant_store,
        neo4j_writer=neo4j_writer,
        ollama_client=ollama_client,
    )


@app.post("/api/search/vector")
async def search_vector(request: SearchRequest) -> dict:
    try:
        vector = await ollama_client.embed(request.query)
        results = await asyncio.to_thread(
            qdrant_store.search_dense,
            vector,
            limit=request.limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"mode": "vector", "query": request.query, "count": len(results), "results": results}


@app.post("/api/search/bm25")
async def search_bm25(request: SearchRequest) -> dict:
    try:
        results = await asyncio.to_thread(
            qdrant_store.search_bm25,
            request.query,
            limit=request.limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"mode": "bm25", "query": request.query, "count": len(results), "results": results}


@app.post("/api/search/hybrid")
async def search_hybrid(request: SearchRequest) -> dict:
    try:
        vector = await ollama_client.embed(request.query)
        results = await asyncio.to_thread(
            qdrant_store.search_hybrid,
            request.query,
            vector,
            limit=request.limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"mode": "hybrid", "query": request.query, "count": len(results), "results": results}


@app.get("/api/graph/events")
async def graph_search_events(
    q: str = Query(default="", max_length=500),
    action: str = Query(default="", max_length=100),
    limit: int = Query(default=settings.search_default_limit, ge=1, le=50),
) -> dict:
    events = await neo4j_writer.search_events(text=q, action=action, limit=limit)
    return {"count": len(events), "events": events}


@app.get("/api/graph/events/{event_id}")
async def graph_event_detail(event_id: str) -> dict:
    subgraph = await neo4j_writer.get_event_subgraph(event_id)
    if subgraph is None:
        raise HTTPException(status_code=404, detail=f"graph event not found: {event_id}")
    return subgraph


@app.get("/api/graph/instructions/{instruction_id}")
async def graph_instruction_detail(instruction_id: str) -> dict:
    subgraph = await neo4j_writer.get_instruction_subgraph(instruction_id)
    if subgraph is None:
        raise HTTPException(status_code=404, detail=f"graph instruction not found: {instruction_id}")
    return subgraph


def run() -> None:
    uvicorn.run(
        "security_event_qdrant_etl.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
