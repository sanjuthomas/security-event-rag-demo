from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from telemetry import (
    configure_telemetry,
    get_logger,
    instrument_app,
    shutdown_telemetry,
)

from chat_application import __version__
from chat_application.config import settings
from chat_application.dependencies import get_compliance_subject
from chat_application.models import ChatRequest, ChatResponse
from chat_application.neo4j import Neo4jClient
from chat_application.ollama import OllamaClient
from chat_application.qdrant import QdrantSearchClient
from chat_application.rag import RagService
from chat_application.subject import Subject
from chat_application.users import compliance_users
from chat_application.zitadel_auth import ZitadelAuthClient, login_name_for_user

logger = get_logger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

ollama_client = OllamaClient()
qdrant_client = QdrantSearchClient()
neo4j_client = Neo4jClient()
rag_service: RagService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global rag_service
    configure_telemetry("ssi-chat", service_version=__version__)
    instrument_app(app)
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
    logger.info("PolicyPilot ready on port %s", settings.port)
    yield
    qdrant_client.close()
    await neo4j_client.close()
    shutdown_telemetry()


app = FastAPI(
    title="PolicyPilot",
    description="PolicyPilot — natural-language policy Q&A over security events (vector + BM25 + Neo4j + Ollama)",
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


class LoginRequest(BaseModel):
    user_id: str = Field(min_length=1)
    password: str = Field(min_length=1)


@app.get("/api/compliance-users")
async def list_compliance_users() -> dict:
    users = compliance_users(settings.users_file, allowed_roles=settings.compliance_role_set)
    return {
        "users": [
            {
                "user_id": user.user_id,
                "display_name": f"{user.family_name}, {user.given_name}",
                "title": user.title,
            }
            for user in users
        ]
    }


@app.post("/api/auth/login")
async def auth_login(request: LoginRequest) -> dict:
    if not settings.zitadel_service_pat:
        raise HTTPException(status_code=503, detail="ZITADEL service PAT not configured")
    client = ZitadelAuthClient()
    login_name = login_name_for_user(request.user_id)
    try:
        session = await client.login(login_name, request.password)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"login failed: {exc}") from exc
    return {
        "user_id": session.user_id,
        "session_id": session.session_id,
        "session_token": session.session_token,
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    _subject: Subject = Depends(get_compliance_subject),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> ChatResponse:
    if rag_service is None:
        raise HTTPException(status_code=503, detail="RAG service not ready")

    bearer_token = authorization.split(" ", 1)[1].strip() if authorization else None

    try:
        return await rag_service.ask(
            request.message.strip(),
            request.history,
            mode=request.mode,
            bearer_token=bearer_token,
            session_id=x_session_id,
        )
    except Exception as exc:
        logger.exception("chat failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def run() -> None:
    uvicorn.run(
        "chat_application.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
