from pathlib import Path
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8091
    zitadel_url: str = "http://localhost:8080"
    zitadel_host_header: str = ""
    zitadel_service_pat: str = ""
    zitadel_service_pat_file: Path | None = None
    ilm_url: str = "http://localhost:8000"
    ilm_api_prefix: str = "/api/v1"
    users_file: Path = Path(__file__).resolve().parents[3] / "zitadel-seed" / "users.yaml"
    default_password: str = "Password1!"
    email_domain: str = "ssi.local"
    security_events_database: str = "security_events"
    security_events_collection: str = "instruction-lifecycle-manager"
    mongodb_uri: str = "mongodb://localhost:27017"
    verify_security_events: bool = True

    @model_validator(mode="after")
    def load_service_pat_from_file(self) -> Self:
        if self.zitadel_service_pat or not self.zitadel_service_pat_file:
            return self
        path = self.zitadel_service_pat_file
        if path.is_file():
            self.zitadel_service_pat = path.read_text(encoding="utf-8").strip()
        return self
