from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8092

    ollama_url: str = "http://host.docker.internal:11434"
    ollama_embedding_model: str = "qwen3-embedding:0.6b"
    ollama_chat_model: str = "hmahmood/neo4j-gemma-3-27b-inst-q8"
    ollama_timeout_seconds: float = 300.0

    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "ssi_search_index"
    qdrant_dense_vector_name: str = "dense"
    qdrant_bm25_vector_name: str = "bm25"
    qdrant_bm25_model: str = "qdrant/bm25"

    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "devpassword"
    graph_model_dir: str = "/app/neo4j-graph-model"

    retrieval_limit: int = 15
    rrf_k: int = 60
    max_context_hits: int = 10
    max_conversation_turns: int = 20

    authorization_service_url: str = "http://authorization-service:8094"
    payment_service_url: str = "http://payment-service:8093"
    instruction_service_url: str = "http://instruction-service:8000"
    users_file: Path = Path("/app/zitadel-seed/users.yaml")
    zitadel_url: str = "http://zitadel-proxy"
    zitadel_host_header: str = "localhost"
    zitadel_internal_url: str | None = None
    zitadel_service_pat: str | None = None
    zitadel_service_pat_file: Path | None = None
    oidc_issuer_url: str | None = None
    oidc_internal_url: str | None = None
    oidc_audience: str | None = None
    compliance_roles: str = "COMPLIANCE_ANALYST,COMPLIANCE_OFFICER,PLATFORM_ADMIN"
    default_user_password: str = "Password1!"

    @property
    def compliance_role_set(self) -> set[str]:
        return {role.strip() for role in self.compliance_roles.split(",") if role.strip()}

    @property
    def graph_schema_path(self) -> Path:
        return Path(self.graph_model_dir) / "relationships.cypher"

    @model_validator(mode="after")
    def load_service_pat_from_file(self) -> "Settings":
        if self.zitadel_service_pat or not self.zitadel_service_pat_file:
            return self
        path = self.zitadel_service_pat_file
        if path.is_file():
            self.zitadel_service_pat = path.read_text(encoding="utf-8").strip()
        return self


settings = Settings()
