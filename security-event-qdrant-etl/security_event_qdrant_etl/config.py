from pathlib import Path
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    security_events_database: str = "security_events"
    security_events_collection: str = "instruction-lifecycle-manager"
    initial_event_limit: int = 200
    poll_interval_seconds: float = 2.0
    host: str = "0.0.0.0"
    port: int = 8090

    kafka_enabled: bool = True
    kafka_bootstrap_servers: str = "kafka:9092"
    kafka_security_events_topic: str = "instruction-security-events"
    kafka_consumer_group: str = "security-event-qdrant-etl"

    ilm_url: str = "http://instruction-lifecycle-manager:8000"
    ilm_api_prefix: str = "/api/v1"

    zitadel_url: str = "http://zitadel-proxy"
    zitadel_host_header: str = "localhost"
    zitadel_service_pat: str = ""
    zitadel_service_pat_file: Path | None = None
    etl_reader_login: str = "etl-reader@ssi.local"
    etl_reader_password: str = "Password1!"

    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "devpassword"
    graph_model_dir: str = "/app/neo4j-graph-model"

    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "instruction_security_events"
    qdrant_dense_vector_name: str = "dense"
    qdrant_bm25_vector_name: str = "bm25"
    qdrant_bm25_model: str = "qdrant/bm25"

    ollama_url: str = "http://host.docker.internal:11434"
    ollama_embedding_model: str = "bge-m3:latest"
    ollama_timeout_seconds: float = 300.0
    search_default_limit: int = 10

    @model_validator(mode="after")
    def load_service_pat_from_file(self) -> Self:
        if self.zitadel_service_pat or not self.zitadel_service_pat_file:
            return self
        path = self.zitadel_service_pat_file
        if path.is_file():
            self.zitadel_service_pat = path.read_text(encoding="utf-8").strip()
        return self

    @property
    def graph_model_dir_path(self) -> Path:
        return Path(self.graph_model_dir)


settings = Settings()
