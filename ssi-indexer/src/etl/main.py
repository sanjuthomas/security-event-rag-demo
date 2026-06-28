import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from cypher_gen import (
    load_graph_schema,
    normalize_read_only_cypher,
    validate_read_only_cypher,
)
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from telemetry import (
    configure_telemetry,
    get_logger,
    instrument_app,
    shutdown_telemetry,
)

from etl.admin import get_admin_subject
from etl.auth_routes import router as auth_router
from etl.config import settings
from etl.health import component_status
from etl.instruction_consumer import InstructionKafkaConsumer
from etl.instruction_pipeline import InstructionPipeline
from etl.instruction_security_event_consumer import (
    InstructionSecurityEventKafkaConsumer,
)
from etl.instruction_security_event_pipeline import InstructionSecurityEventPipeline
from etl.neo4j_client import Neo4jGraphWriter
from etl.ollama_client import OllamaEmbeddingClient
from etl.payment_consumer import (
    PaymentFactKafkaConsumer,
    PaymentSecurityEventKafkaConsumer,
)
from etl.payment_pipeline import PaymentFactPipeline, PaymentSecurityEventPipeline
from etl.qdrant_store import QdrantHybridStore

__version__ = "0.2.0"

logger = get_logger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

neo4j_writer = Neo4jGraphWriter()
ollama_client = OllamaEmbeddingClient()
qdrant_store = QdrantHybridStore()

instruction_security_event_pipeline = InstructionSecurityEventPipeline(
    neo4j_writer=neo4j_writer,
    ollama_client=ollama_client,
    qdrant_store=qdrant_store,
)
instruction_pipeline = InstructionPipeline(
    neo4j_writer=neo4j_writer,
    ollama_client=ollama_client,
    qdrant_store=qdrant_store,
)

payment_security_event_pipeline = PaymentSecurityEventPipeline(
    neo4j_writer=neo4j_writer,
    ollama_client=ollama_client,
    qdrant_store=qdrant_store,
)
payment_fact_pipeline = PaymentFactPipeline(
    neo4j_writer=neo4j_writer,
    ollama_client=ollama_client,
    qdrant_store=qdrant_store,
)

instruction_security_event_consumer = InstructionSecurityEventKafkaConsumer(
    instruction_security_event_pipeline
)
instruction_consumer = InstructionKafkaConsumer(instruction_pipeline)
payment_security_event_consumer = PaymentSecurityEventKafkaConsumer(payment_security_event_pipeline)
payment_fact_consumer = PaymentFactKafkaConsumer(payment_fact_pipeline)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=settings.search_default_limit, ge=1, le=50)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_telemetry("ssi-indexer", service_version=__version__)
    instrument_app(app)
    await neo4j_writer.connect()
    qdrant_store.connect()

    await instruction_security_event_consumer.start()
    await instruction_consumer.start()
    await payment_security_event_consumer.start()
    await payment_fact_consumer.start()

    try:
        await ollama_client.warmup()
        if qdrant_store.has_collection():
            qdrant_store.ensure_collection(ollama_client.dimension)
    except Exception as exc:
        logger.warning("search backends not fully warmed up yet: %s", exc)

    logger.info("ssi-indexer started (quad consumers: instruction events, instruction facts, payment events, payment facts)")
    yield

    await instruction_security_event_consumer.close()
    await instruction_consumer.close()
    await payment_security_event_consumer.close()
    await payment_fact_consumer.close()
    await neo4j_writer.close()
    await ollama_client.close()
    qdrant_store.close()
    shutdown_telemetry()


app = FastAPI(
    title="Security Event Search Console",
    description="Query Neo4j graph and Qdrant hybrid vectors produced by the ETL pipeline",
    version=__version__,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

api_router = APIRouter(dependencies=[Depends(get_admin_subject)])


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict:
    components = await component_status(
        instruction_security_event_consumer=instruction_security_event_consumer,
        qdrant_store=qdrant_store,
        neo4j_writer=neo4j_writer,
        ollama_client=ollama_client,
    )
    overall = "UP" if all(c["ok"] for c in components.values()) else "DEGRADED"
    return {"status": overall, "components": components}


@api_router.get("/stats")
async def stats() -> dict:
    components = await component_status(
        instruction_security_event_consumer=instruction_security_event_consumer,
        qdrant_store=qdrant_store,
        neo4j_writer=neo4j_writer,
        ollama_client=ollama_client,
    )
    return {
        "components": components,
        "all_ok": all(component["ok"] for component in components.values()),
    }


@api_router.post("/search/vector")
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


@api_router.post("/search/bm25")
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


@api_router.post("/search/hybrid")
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


@api_router.get("/graph/events")
async def graph_search_events(
    q: str = Query(default="", max_length=500),
    action: str = Query(default="", max_length=100),
    limit: int = Query(default=settings.search_default_limit, ge=1, le=50),
) -> dict:
    events = await neo4j_writer.search_events(text=q, action=action, limit=limit)
    return {"count": len(events), "events": events}


@api_router.get("/graph/events/{event_id}")
async def graph_event_detail(event_id: str) -> dict:
    subgraph = await neo4j_writer.get_event_subgraph(event_id)
    if subgraph is None:
        raise HTTPException(status_code=404, detail=f"graph event not found: {event_id}")
    return subgraph


@api_router.get("/graph/instructions/{instruction_id}")
async def graph_instruction_detail(instruction_id: str) -> dict:
    subgraph = await neo4j_writer.get_instruction_subgraph(instruction_id)
    if subgraph is None:
        raise HTTPException(status_code=404, detail=f"graph instruction not found: {instruction_id}")
    return subgraph


class CypherGenerateRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    mode: str = Field(default="events", pattern="^(events|instructions|payments)$")


class CypherRunRequest(BaseModel):
    cypher: str = Field(min_length=1, max_length=4096)


@api_router.post("/cypher/generate")
async def cypher_generate(request: CypherGenerateRequest) -> dict:
    """Translate natural language to a read-only Cypher query via Ollama."""
    schema = load_graph_schema(settings.graph_schema_path)
    try:
        cypher = await ollama_client.generate_cypher(
            request.question,
            schema,
            mode=request.mode,
        )
        cypher = normalize_read_only_cypher(cypher)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Cypher generation failed: {exc}") from exc

    valid = True
    error: str | None = None
    try:
        validate_read_only_cypher(cypher)
    except ValueError as exc:
        valid = False
        error = str(exc)

    return {
        "question": request.question,
        "mode": request.mode,
        "cypher": cypher,
        "valid": valid,
        "error": error,
        "model": settings.ollama_chat_model,
    }


@api_router.post("/cypher/run")
async def cypher_run(request: CypherRunRequest) -> dict:
    """Validate and execute a read-only Cypher query against Neo4j."""
    try:
        validate_read_only_cypher(request.cypher)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Cypher validation failed: {exc}") from exc

    try:
        rows = await neo4j_writer.run_read_cypher(request.cypher)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Neo4j query failed: {exc}") from exc

    return {"cypher": request.cypher, "row_count": len(rows), "rows": rows}


app.include_router(auth_router)
app.include_router(api_router, prefix="/api")


def run() -> None:
    uvicorn.run(
        "etl.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
