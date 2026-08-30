from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _secret(name: str, file_name: str) -> str | None:
    direct = os.getenv(name)
    if direct:
        return direct.strip()
    path = os.getenv(file_name)
    if not path:
        return None
    return Path(path).read_text(encoding="utf-8").strip()


def _database_url() -> str:
    value = os.getenv("FOUNDATION_DATABASE_URL", "sqlite:///./foundation.db").strip()
    if "{password}" not in value:
        return value
    password = _secret("FOUNDATION_DATABASE_PASSWORD", "FOUNDATION_DATABASE_PASSWORD_FILE")
    if not password:
        raise RuntimeError("foundation_database_password_not_configured")
    return value.replace("{password}", quote_plus(password))


@dataclass(frozen=True)
class Settings:
    environment: str
    database_url: str
    mutations_enabled: bool
    auto_create_schema: bool
    jwt_issuer: str
    jwt_audience: str
    jwt_algorithm: str
    jwt_public_key: str | None
    field_encryption_key: str | None
    identity_hash_key: str | None
    outbox_enabled: bool
    middleware_event_url: str | None
    middleware_access_token: str | None
    middleware_ca_file: str | None
    middleware_client_cert_file: str | None
    middleware_client_key_file: str | None

    @property
    def cryptography_ready(self) -> bool:
        return bool(self.field_encryption_key and self.identity_hash_key)

    @property
    def auth_ready(self) -> bool:
        return bool(self.jwt_public_key and self.jwt_issuer and self.jwt_audience)

    @property
    def outbox_ready(self) -> bool:
        return bool(
            self.middleware_event_url
            and self.middleware_event_url.startswith("https://")
            and self.middleware_access_token
            and self.middleware_ca_file
            and self.middleware_client_cert_file
            and self.middleware_client_key_file
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        environment=os.getenv("FOUNDATION_ENVIRONMENT", "development").strip().lower(),
        database_url=_database_url(),
        mutations_enabled=_bool("FOUNDATION_MUTATIONS_ENABLED"),
        auto_create_schema=_bool("FOUNDATION_AUTO_CREATE_SCHEMA"),
        jwt_issuer=os.getenv("FOUNDATION_JWT_ISSUER", "https://auth.codestra.co/realms/codestra").strip(),
        jwt_audience=os.getenv("FOUNDATION_JWT_AUDIENCE", "codestra-foundation-api").strip(),
        jwt_algorithm=os.getenv("FOUNDATION_JWT_ALGORITHM", "RS256").strip(),
        jwt_public_key=_secret("FOUNDATION_JWT_PUBLIC_KEY", "FOUNDATION_JWT_PUBLIC_KEY_FILE"),
        field_encryption_key=_secret("FOUNDATION_FIELD_ENCRYPTION_KEY", "FOUNDATION_FIELD_ENCRYPTION_KEY_FILE"),
        identity_hash_key=_secret("FOUNDATION_IDENTITY_HASH_KEY", "FOUNDATION_IDENTITY_HASH_KEY_FILE"),
        outbox_enabled=_bool("FOUNDATION_OUTBOX_ENABLED"),
        middleware_event_url=os.getenv("FOUNDATION_MIDDLEWARE_EVENT_URL"),
        middleware_access_token=_secret(
            "FOUNDATION_MIDDLEWARE_ACCESS_TOKEN", "FOUNDATION_MIDDLEWARE_ACCESS_TOKEN_FILE"
        ),
        middleware_ca_file=os.getenv("FOUNDATION_MIDDLEWARE_CA_FILE"),
        middleware_client_cert_file=os.getenv("FOUNDATION_MIDDLEWARE_CLIENT_CERT_FILE"),
        middleware_client_key_file=os.getenv("FOUNDATION_MIDDLEWARE_CLIENT_KEY_FILE"),
    )
