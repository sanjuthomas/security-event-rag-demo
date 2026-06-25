import base64
import json
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from fastapi import HTTPException
from jwt import PyJWKClient

from ilm.config import settings
from ilm.models.api import Subject
from ilm.models.enums import is_valid_owning_lob

METADATA_CLAIM = "urn:zitadel:iam:user:metadata"
USERINFO_METADATA_SCOPE = "urn:zitadel:iam:user:metadata"


@lru_cache
def _oidc_base_url() -> str:
    if not settings.oidc_issuer_url:
        raise RuntimeError("oidc_issuer_url is not configured")
    return (settings.oidc_internal_url or settings.oidc_issuer_url).rstrip("/")


@lru_cache
def _zitadel_base_url() -> str:
    if settings.zitadel_internal_url:
        return settings.zitadel_internal_url.rstrip("/")
    return _oidc_base_url()


def _zitadel_request_headers() -> dict[str, str]:
    if not settings.oidc_issuer_url:
        return {}
    host = urlparse(settings.oidc_issuer_url).netloc
    return {"Host": host} if host else {}


@lru_cache
def _jwks_client() -> PyJWKClient:
    well_known = f"{_oidc_base_url()}/.well-known/openid-configuration"
    with httpx.Client(timeout=10.0) as client:
        config = client.get(well_known, headers=_zitadel_request_headers()).json()
    jwks_uri = config["jwks_uri"]
    internal_host = urlparse(_oidc_base_url())
    issuer_host = urlparse(settings.oidc_issuer_url or "")
    if issuer_host.netloc and internal_host.netloc:
        jwks_uri = jwks_uri.replace(issuer_host.netloc, internal_host.netloc)
    return PyJWKClient(jwks_uri, headers=_zitadel_request_headers())


def _decode_metadata_values(raw: dict[str, Any]) -> dict[str, str]:
    decoded: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            continue
        try:
            decoded[key] = base64.b64decode(value).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            decoded[key] = value
    return decoded


def _parse_roles(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(parsed, list) or not parsed:
        raise ValueError("roles claim is empty or invalid")
    return [str(role) for role in parsed]


def _subject_from_metadata(
    metadata: dict[str, str],
    *,
    fallback_user_id: str | None,
) -> Subject:
    user_id = metadata.get("subject_user_id") or fallback_user_id
    if not user_id:
        raise ValueError("missing subject_user_id")

    title = metadata.get("title")
    if not title:
        raise ValueError("missing title metadata")

    roles_raw = metadata.get("roles")
    if not roles_raw:
        raise ValueError("missing roles metadata")
    roles = _parse_roles(roles_raw)

    lob = metadata.get("lob")
    if lob is not None and not is_valid_owning_lob(lob):
        raise ValueError(f"invalid lob metadata: {lob}")

    supervisor_id = metadata.get("supervisor_id")
    return Subject(
        user_id=user_id,
        given_name=metadata.get("given_name") or None,
        family_name=metadata.get("family_name") or None,
        title=title,
        lob=lob,
        roles=roles,
        supervisor_id=supervisor_id,
    )


def _fetch_userinfo_metadata(access_token: str) -> dict[str, str]:
    userinfo_url = f"{_oidc_base_url()}/oidc/v1/userinfo"
    with httpx.Client(timeout=10.0) as client:
        response = client.get(
            userinfo_url,
            headers={
                **_zitadel_request_headers(),
                "Authorization": f"Bearer {access_token}",
            },
            params={"scope": USERINFO_METADATA_SCOPE},
        )
        response.raise_for_status()
        payload = response.json()

    metadata_raw = payload.get(METADATA_CLAIM)
    if not isinstance(metadata_raw, dict):
        raise ValueError("userinfo response missing metadata claim")
    return _decode_metadata_values(metadata_raw)


def _fetch_user_metadata_from_zitadel(zitadel_user_id: str) -> dict[str, str]:
    if not settings.zitadel_service_pat:
        raise ValueError("zitadel service PAT is not configured")

    with httpx.Client(timeout=10.0) as client:
        response = client.post(
            f"{_zitadel_base_url()}/v2/users/{zitadel_user_id}/metadata/search",
            headers={
                **_zitadel_request_headers(),
                "Authorization": f"Bearer {settings.zitadel_service_pat}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={},
        )
        response.raise_for_status()
        payload = response.json()

    metadata: dict[str, str] = {}
    for entry in payload.get("metadata") or []:
        key = entry.get("key")
        value = entry.get("value")
        if isinstance(key, str) and isinstance(value, str):
            metadata[key] = value
    return _decode_metadata_values(metadata)


def _subject_from_session_token(session_id: str, session_token: str) -> Subject:
    if not settings.zitadel_service_pat:
        raise ValueError("zitadel service PAT is not configured")

    with httpx.Client(timeout=10.0) as client:
        response = client.get(
            f"{_zitadel_base_url()}/v2/sessions/{session_id}",
            headers={
                **_zitadel_request_headers(),
                "Authorization": f"Bearer {settings.zitadel_service_pat}",
                "Accept": "application/json",
            },
            params={"sessionToken": session_token},
        )
        response.raise_for_status()
        payload = response.json()

    session = payload.get("session") or {}
    factors = session.get("factors") or {}
    user = factors.get("user") or {}
    zitadel_user_id = user.get("id")
    if not zitadel_user_id:
        raise ValueError("session response missing user id")

    metadata = _fetch_user_metadata_from_zitadel(zitadel_user_id)
    fallback_user_id = user.get("loginName")
    return _subject_from_metadata(metadata, fallback_user_id=fallback_user_id)


def subject_from_bearer_token(access_token: str, *, session_id: str | None = None) -> Subject:
    claims: dict[str, Any] = {}
    fallback_user_id: str | None = None

    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(access_token)
        decode_options: dict[str, Any] = {"verify_aud": bool(settings.oidc_audience)}
        claims = jwt.decode(
            access_token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=settings.oidc_issuer_url.rstrip("/"),
            audience=settings.oidc_audience or None,
            options=decode_options,
        )
        fallback_user_id = claims.get("preferred_username") or claims.get("sub")
        metadata_claim = claims.get(METADATA_CLAIM)
        if isinstance(metadata_claim, dict) and metadata_claim:
            metadata = _decode_metadata_values(metadata_claim)
            try:
                return _subject_from_metadata(metadata, fallback_user_id=fallback_user_id)
            except ValueError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception:
        pass

    try:
        metadata = _fetch_userinfo_metadata(access_token)
        return _subject_from_metadata(metadata, fallback_user_id=fallback_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception:
        pass

    if session_id:
        try:
            return _subject_from_session_token(session_id, access_token)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=401,
                detail=f"could not resolve user from session token: {exc}",
            ) from exc

    raise HTTPException(status_code=401, detail="invalid access token")
