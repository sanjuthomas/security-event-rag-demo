from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8092

    ollama_url: str = "http://host.docker.internal:11434"
    ollama_embedding_model: str = "bge-m3:latest"
    ollama_chat_model: str = "qwen3:30b"
    ollama_timeout_seconds: float = 300.0

    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "instruction_security_events"
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

    @property
    def graph_schema_path(self) -> Path:
        return Path(self.graph_model_dir) / "relationships.cypher"


settings = Settings()
