from fastapi import Depends, Header, HTTPException
from platform_auth import is_platform_admin

from ps.auth import subject_from_bearer_token
from ps.config import settings
from ps.models.api import Subject


def _subject_from_headers(
    x_subject_user_id: str,
    x_subject_title: str,
    x_subject_roles: str,
    x_subject_lob: str | None,
    x_subject_supervisor_id: str | None,
    x_subject_groups: str | None,
    x_subject_covering_lobs: str | None,
) -> Subject:
    roles = [r.strip() for r in x_subject_roles.split(",") if r.strip()]
    if not roles:
        raise HTTPException(status_code=400, detail="X-Subject-Roles must not be empty")

    groups = [g.strip() for g in x_subject_groups.split(",") if g.strip()] if x_subject_groups else []
    covering_lobs = (
        [lob.strip() for lob in x_subject_covering_lobs.split(",") if lob.strip()]
        if x_subject_covering_lobs
        else []
    )

    return Subject(
        user_id=x_subject_user_id,
        title=x_subject_title,
        lob=x_subject_lob,
        roles=roles,
        groups=groups,
        supervisor_id=x_subject_supervisor_id,
        covering_lobs=covering_lobs,
    )


def get_subject(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
    x_subject_user_id: str | None = Header(default=None, alias="X-Subject-User-Id"),
    x_subject_title: str | None = Header(default=None, alias="X-Subject-Title"),
    x_subject_roles: str | None = Header(default=None, alias="X-Subject-Roles"),
    x_subject_lob: str | None = Header(default=None, alias="X-Subject-Lob"),
    x_subject_supervisor_id: str | None = Header(default=None, alias="X-Subject-Supervisor-Id"),
    x_subject_groups: str | None = Header(default=None, alias="X-Subject-Groups"),
    x_subject_covering_lobs: str | None = Header(default=None, alias="X-Subject-Covering-Lobs"),
) -> Subject:
    has_bearer = authorization is not None and authorization.lower().startswith("bearer ")
    use_jwt = settings.auth_mode == "jwt" or (settings.auth_mode == "auto" and has_bearer)

    if use_jwt:
        if not has_bearer:
            raise HTTPException(status_code=401, detail="Authorization Bearer token required")
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
            x_subject_groups,
            x_subject_covering_lobs,
        )

    raise HTTPException(status_code=401, detail="unsupported authorization mode")


def get_compliance_subject(subject: Subject = Depends(get_subject)) -> Subject:
    if is_platform_admin(subject):
        return subject
    if not settings.compliance_role_set.intersection(subject.roles):
        raise HTTPException(
            status_code=403,
            detail="COMPLIANCE_ANALYST role required for policy inquiry",
        )
    return subject
