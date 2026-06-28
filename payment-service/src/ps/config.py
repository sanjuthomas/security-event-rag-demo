from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mongodb_uri: str = "mongodb://localhost:27017/?replicaSet=rs0"
    mongodb_database: str = "ssi_cash_activities"
    mongodb_collection: str = "payments"
    security_events_database: str = "security_events"
    security_events_collection: str = "payment-service"
    application_name: str = "payment-service"

    authorization_service_url: str = "http://localhost:8094"
    ilm_url: str = "http://localhost:8000"
    sequence_service_url: str = "http://localhost:8095"

    # Service account used for OBO delegation calls to ILM
    service_user_id: str = "svc-payment"
    service_user_password: str = "Password1!"

    api_prefix: str = "/api/v1"
    oidc_issuer_url: str | None = None
    oidc_internal_url: str | None = None
    oidc_audience: str | None = None
    zitadel_internal_url: str | None = None
    zitadel_service_pat: str | None = None
    zitadel_service_pat_file: Path | None = None
    auth_mode: str = "auto"
    compliance_roles: str = "COMPLIANCE_ANALYST,COMPLIANCE_OFFICER,PLATFORM_ADMIN"

    kafka_enabled: bool = True
    kafka_bootstrap_servers: str = "kafka:9092"
    kafka_payments_topic: str = "ssi-payments"
    kafka_security_events_topic: str = "payment-security-events"

    ui_initial_security_event_limit: int = 200

    @property
    def compliance_role_set(self) -> set[str]:
        return {role.strip() for role in self.compliance_roles.split(",") if role.strip()}

    @model_validator(mode="after")
    def load_service_pat_from_file(self) -> "Settings":
        if self.zitadel_service_pat or not self.zitadel_service_pat_file:
            return self
        path = self.zitadel_service_pat_file
        if path.is_file():
            self.zitadel_service_pat = path.read_text(encoding="utf-8").strip()
        return self


settings = Settings()
