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
    port: int = 8090

    oidc_issuer_url: str | None = None
    oidc_internal_url: str | None = None
    oidc_audience: str | None = None
    zitadel_internal_url: str | None = None
    zitadel_service_pat: str | None = None
    zitadel_service_pat_file: Path | None = None
    auth_mode: str = "auto"

    kafka_enabled: bool = True
    kafka_bootstrap_servers: str = "kafka:9092"
    kafka_instruction_security_events_topic: str = "instruction-security-events"
    kafka_instruction_security_events_consumer_group: str = "instruction-security-event-etl"
    kafka_instruction_topic: str = "ssi-instructions"
    kafka_instruction_consumer_group: str = "ssi-instruction-etl"
    kafka_payment_security_events_topic: str = "payment-security-events"
    kafka_payment_security_events_consumer_group: str = "payment-security-event-etl"
    kafka_payments_topic: str = "ssi-payments"
    kafka_payments_consumer_group: str = "payment-fact-etl"

    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "devpassword"
    graph_model_dir: str = "/app/neo4j-graph-model"

    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "ssi_search_index"
    qdrant_dense_vector_name: str = "dense"
    qdrant_bm25_vector_name: str = "bm25"
    qdrant_bm25_model: str = "qdrant/bm25"

    ollama_url: str = "http://host.docker.internal:11434"
    ollama_embedding_model: str = "qwen3-embedding:0.6b"
    ollama_chat_model: str = "llama3:8b"
    ollama_timeout_seconds: float = 300.0
    search_default_limit: int = 10

    @property
    def graph_schema_path(self):
        return self.graph_model_dir_path / "relationships.cypher"

    @model_validator(mode="after")
    def load_service_pat_from_file(self) -> "Settings":
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
