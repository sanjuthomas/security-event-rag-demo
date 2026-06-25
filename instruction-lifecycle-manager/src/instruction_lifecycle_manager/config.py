from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "ssi_cash_instructions"
    security_events_database: str = "security_events"
    security_events_collection: str = "instruction-lifecycle-manager"
    application_name: str = "instruction-lifecycle-manager"
    opa_url: str = "http://localhost:8181"
    api_prefix: str = "/api/v1"
    oidc_issuer_url: str | None = None
    oidc_internal_url: str | None = None
    oidc_audience: str | None = None
    zitadel_internal_url: str | None = None
    zitadel_service_pat: str | None = None
    zitadel_service_pat_file: Path | None = None
    auth_mode: str = "auto"  # auto | jwt | headers
    ui_initial_instruction_limit: int = 200
    ui_initial_security_event_limit: int = 200
    ui_poll_interval_seconds: float = 2.0
    kafka_enabled: bool = True
    kafka_bootstrap_servers: str = "kafka:9092"
    kafka_security_events_topic: str = "instruction-security-events"
    security_event_excluded_user_ids: str = "etl-reader"

    @property
    def security_event_excluded_user_id_set(self) -> set[str]:
        return {
            user_id.strip()
            for user_id in self.security_event_excluded_user_ids.split(",")
            if user_id.strip()
        }

    @model_validator(mode="after")
    def load_service_pat_from_file(self) -> "Settings":
        if self.zitadel_service_pat or not self.zitadel_service_pat_file:
            return self
        path = self.zitadel_service_pat_file
        if path.is_file():
            self.zitadel_service_pat = path.read_text(encoding="utf-8").strip()
        return self


settings = Settings()
