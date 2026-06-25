from fastapi import Header, HTTPException

from instruction_lifecycle_manager.auth import subject_from_bearer_token
from instruction_lifecycle_manager.config import settings
from instruction_lifecycle_manager.models.api import Subject
from instruction_lifecycle_manager.models.enums import is_valid_owning_lob


def _subject_from_headers(
    x_subject_user_id: str,
    x_subject_title: str,
    x_subject_roles: str,
    x_subject_lob: str | None,
    x_subject_supervisor_id: str | None,
) -> Subject:
    roles = [role.strip() for role in x_subject_roles.split(",") if role.strip()]
    if not roles:
        raise HTTPException(status_code=400, detail="X-Subject-Roles must not be empty")

    if x_subject_lob is not None and not is_valid_owning_lob(x_subject_lob):
        raise HTTPException(
            status_code=400,
            detail="X-Subject-Lob must be FICC, FX, or DESK_<name>",
        )

    return Subject(
        user_id=x_subject_user_id,
        title=x_subject_title,
        lob=x_subject_lob,
        roles=roles,
        supervisor_id=x_subject_supervisor_id,
    )


def get_subject(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
    x_subject_user_id: str | None = Header(default=None, alias="X-Subject-User-Id"),
    x_subject_title: str | None = Header(default=None, alias="X-Subject-Title"),
    x_subject_roles: str | None = Header(default=None, alias="X-Subject-Roles"),
    x_subject_lob: str | None = Header(default=None, alias="X-Subject-Lob"),
    x_subject_supervisor_id: str | None = Header(
        default=None, alias="X-Subject-Supervisor-Id"
    ),
) -> Subject:
    has_bearer = authorization is not None and authorization.lower().startswith("bearer ")
    use_jwt = settings.auth_mode == "jwt" or (settings.auth_mode == "auto" and has_bearer)

    if use_jwt:
        if not has_bearer:
            raise HTTPException(
                status_code=401,
                detail="Authorization Bearer token required",
            )
        if not settings.oidc_issuer_url:
            raise HTTPException(status_code=500, detail="OIDC issuer is not configured")
        token = authorization.split(" ", 1)[1].strip()
        return subject_from_bearer_token(token, session_id=x_session_id)

    if settings.auth_mode == "headers" or not has_bearer:
        missing = [
            name
            for name, value in (
                ("X-Subject-User-Id", x_subject_user_id),
                ("X-Subject-Title", x_subject_title),
                ("X-Subject-Roles", x_subject_roles),
            )
            if not value
        ]
        if missing:
            raise HTTPException(
                status_code=401,
                detail=f"Missing required headers: {', '.join(missing)}",
            )
        return _subject_from_headers(
            x_subject_user_id,
            x_subject_title,
            x_subject_roles,
            x_subject_lob,
            x_subject_supervisor_id,
        )

    raise HTTPException(status_code=401, detail="unsupported authorization mode")
