from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from security_event_chat import __version__
from security_event_chat.config import settings
from security_event_chat.models import ChatRequest, ChatResponse
from security_event_chat.neo4j import Neo4jClient
from security_event_chat.ollama import OllamaClient
from security_event_chat.qdrant import QdrantSearchClient
from security_event_chat.rag import RagService

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

ollama_client = OllamaClient()
qdrant_client = QdrantSearchClient()
neo4j_client = Neo4jClient()
rag_service: RagService | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global rag_service
    logging.basicConfig(level=logging.INFO)
    qdrant_client.connect()
    await neo4j_client.connect()
    try:
        await ollama_client.embed("warmup")
    except Exception as exc:
        logger.warning("Ollama warmup failed (chat may still work): %s", exc)
    rag_service = RagService(
        ollama=ollama_client,
        qdrant=qdrant_client,
        neo4j=neo4j_client,
    )
    logger.info("security event chat ready on port %s", settings.port)
    yield
    qdrant_client.close()
    await neo4j_client.close()


app = FastAPI(
    title="Security Event Chat",
    description="Natural-language Q&A over security events (vector + BM25 + Neo4j + Ollama)",
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


@app.get("/api/status")
async def status() -> dict:
    return {
        "ollama_chat_model": settings.ollama_chat_model,
        "ollama_embedding_model": settings.ollama_embedding_model,
        "qdrant_collection_exists": qdrant_client.has_collection(),
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    if rag_service is None:
        raise HTTPException(status_code=503, detail="RAG service not ready")
    try:
        return await rag_service.ask(request.message.strip(), request.history)
    except Exception as exc:
        logger.exception("chat failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def run() -> None:
    uvicorn.run(
        "security_event_chat.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
