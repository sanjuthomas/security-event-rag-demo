from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8094
    api_prefix: str = "/api/v1"

    opa_url: str = "http://localhost:8181"
    users_file: Path = Path("/app/zitadel-seed/users.yaml")

    oidc_issuer_url: str | None = None
    oidc_internal_url: str | None = None
    oidc_audience: str | None = None
    zitadel_internal_url: str | None = None
    zitadel_service_pat: str | None = None
    zitadel_service_pat_file: Path | None = None
    auth_mode: str = "auto"

    compliance_roles: str = "COMPLIANCE_ANALYST,COMPLIANCE_OFFICER,PLATFORM_ADMIN"
    authorized_service_user_ids: str = "svc-instruction,svc-payment"

    @property
    def compliance_role_set(self) -> set[str]:
        return {role.strip() for role in self.compliance_roles.split(",") if role.strip()}

    @property
    def authorized_service_user_id_set(self) -> set[str]:
        return {
            user_id.strip()
            for user_id in self.authorized_service_user_ids.split(",")
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
