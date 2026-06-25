#!/usr/bin/env python3
"""Load users from users.yaml into ZITADEL via the v2 User Management API.

Requires a service-account Personal Access Token with user.write (e.g. Org Owner PAT).

Environment:
  ZITADEL_URL   Base URL (default: http://localhost:8080)
  ZITADEL_PAT   Bearer token for the management API (required)
  ZITADEL_ORG_ID  Optional org scope header (fetched from /management/v1/orgs/me if omitted)

Usage:
  cd zitadel-seed && pip install -e . && zitadel-seed
  zitadel-seed --file users.yaml --dry-run
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    zitadel_url: str = "http://localhost:8080"
    zitadel_pat: str = ""
    zitadel_org_id: str | None = None


class SeedDefaults(BaseModel):
    password: str = "Password1!"
    email_domain: str = "ssi.local"


class SeedUser(BaseModel):
    user_id: str
    given_name: str
    family_name: str
    title: str
    roles: list[str] = Field(min_length=1)
    lob: str | None = None
    supervisor_id: str | None = None

    @field_validator("roles")
    @classmethod
    def normalize_roles(cls, value: list[str]) -> list[str]:
        return [role.strip() for role in value if role.strip()]


class SeedFile(BaseModel):
    defaults: SeedDefaults = Field(default_factory=SeedDefaults)
    users: list[SeedUser]


class ZitadelManagementClient:
    def __init__(self, base_url: str, pat: str, org_id: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {pat}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if org_id:
            self._headers["x-zitadel-orgid"] = org_id

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> dict[str, Any]:
        with httpx.Client(timeout=30.0) as client:
            response = client.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers,
                json=json_body,
            )
        if response.status_code not in expected:
            detail = response.text.strip() or response.reason_phrase
            raise RuntimeError(
                f"{method} {path} failed ({response.status_code}): {detail}"
            )
        if not response.content:
            return {}
        return response.json()

    def resolve_org_id(self) -> str:
        payload = self._request("GET", "/management/v1/orgs/me")
        org_id = payload.get("org", {}).get("id")
        if not org_id:
            raise RuntimeError("Could not resolve organization id from /management/v1/orgs/me")
        return org_id

    def find_user_by_username(self, username: str) -> dict[str, Any] | None:
        payload = self._request(
            "POST",
            "/v2/users",
            json_body={
                "query": {"limit": 1, "asc": True},
                "queries": [
                    {
                        "userNameQuery": {
                            "userName": username,
                            "method": "TEXT_QUERY_METHOD_EQUALS",
                        }
                    }
                ],
            },
        )
        results = payload.get("result") or []
        return results[0] if results else None

    def create_human_user(
        self,
        user: SeedUser,
        *,
        password: str,
        email: str,
    ) -> str:
        metadata = _metadata_entries(user)
        payload = self._request(
            "POST",
            "/v2/users/human",
            json_body={
                "username": user.user_id,
                "profile": {
                    "givenName": user.given_name,
                    "familyName": user.family_name,
                    "displayName": f"{user.given_name} {user.family_name}",
                },
                "email": {"email": email, "isVerified": True},
                "password": {"password": password, "changeRequired": False},
                "metadata": metadata,
            },
            expected=(200, 201),
        )
        user_id = payload.get("userId") or payload.get("id")
        if not user_id:
            raise RuntimeError(f"Create user {user.user_id}: missing user id in response")
        return user_id

    def update_human_user(
        self,
        zitadel_user_id: str,
        user: SeedUser,
        *,
        email: str,
    ) -> None:
        self._request(
            "PUT",
            f"/v2/users/human/{zitadel_user_id}",
            json_body={
                "username": user.user_id,
                "profile": {
                    "givenName": user.given_name,
                    "familyName": user.family_name,
                    "displayName": f"{user.given_name} {user.family_name}",
                },
                "email": {"email": email, "isVerified": True},
            },
        )

    def set_user_metadata(self, zitadel_user_id: str, user: SeedUser) -> None:
        self._request(
            "POST",
            f"/v2/users/{zitadel_user_id}/metadata",
            json_body={"metadata": _metadata_entries(user)},
        )


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _metadata_entries(user: SeedUser) -> list[dict[str, str]]:
    entries: dict[str, str] = {
        "subject_user_id": user.user_id,
        "given_name": user.given_name,
        "family_name": user.family_name,
        "title": user.title,
        "roles": json.dumps(user.roles, separators=(",", ":")),
    }
    if user.lob is not None:
        entries["lob"] = user.lob
    if user.supervisor_id is not None:
        entries["supervisor_id"] = user.supervisor_id
    return [{"key": key, "value": _b64(value)} for key, value in entries.items()]


def load_seed(path: Path) -> SeedFile:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return SeedFile.model_validate(raw)


def seed_users(
    client: ZitadelManagementClient,
    seed: SeedFile,
    *,
    dry_run: bool = False,
) -> None:
    for user in seed.users:
        email = f"{user.user_id}@{seed.defaults.email_domain}"
        action = "create"
        existing = client.find_user_by_username(user.user_id) if not dry_run else None
        if existing:
            action = "update"
            zitadel_user_id = existing["userId"]
        else:
            zitadel_user_id = "(new)"

        print(
            f"[{action}] {user.user_id}: {user.given_name} {user.family_name} "
            f"({user.title}) roles={','.join(user.roles)}"
            + (f" lob={user.lob}" if user.lob else "")
            + (f" supervisor={user.supervisor_id}" if user.supervisor_id else "")
        )

        if dry_run:
            continue

        if existing:
            client.update_human_user(zitadel_user_id, user, email=email)
            client.set_user_metadata(zitadel_user_id, user)
        else:
            zitadel_user_id = client.create_human_user(
                user,
                password=seed.defaults.password,
                email=email,
            )
            print(f"  -> zitadel user id: {zitadel_user_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed ZITADEL users from YAML")
    parser.add_argument(
        "--file",
        type=Path,
        default=Path(__file__).with_name("users.yaml"),
        help="Path to seed YAML (default: users.yaml next to this script)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without calling ZITADEL",
    )
    args = parser.parse_args(argv)

    settings = Settings()
    seed = load_seed(args.file)

    if args.dry_run:
        seed_users(
            ZitadelManagementClient(settings.zitadel_url, "dry-run"),
            seed,
            dry_run=True,
        )
        print(f"Done ({len(seed.users)} users, dry-run).")
        return 0

    if not settings.zitadel_pat:
        print("error: ZITADEL_PAT is required (service account PAT with user.write)", file=sys.stderr)
        return 1

    client = ZitadelManagementClient(
        settings.zitadel_url,
        settings.zitadel_pat,
        org_id=settings.zitadel_org_id,
    )

    if not settings.zitadel_org_id:
        org_id = client.resolve_org_id()
        client._headers["x-zitadel-orgid"] = org_id
        print(f"Using organization id: {org_id}")

    seed_users(client, seed, dry_run=False)
    print(f"Done ({len(seed.users)} users).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
