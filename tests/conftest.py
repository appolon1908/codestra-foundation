from __future__ import annotations

import base64
import os
import time
import uuid
from collections.abc import Callable

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from sqlalchemy import text

TEST_DATABASE = "/root/codestra-foundation/foundation-test.db"
private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
private_pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
public_pem = private_key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()

defaults = {
    "FOUNDATION_ENVIRONMENT": "test",
    "FOUNDATION_DATABASE_URL": f"sqlite:///{TEST_DATABASE}",
    "FOUNDATION_MUTATIONS_ENABLED": "true",
    "FOUNDATION_AUTO_CREATE_SCHEMA": "true",
    "FOUNDATION_JWT_ISSUER": "https://auth.codestra.test/realms/codestra",
    "FOUNDATION_JWT_AUDIENCE": "codestra-foundation-api",
    "FOUNDATION_JWT_ALGORITHM": "RS256",
    "FOUNDATION_JWT_PUBLIC_KEY": public_pem,
    "FOUNDATION_FIELD_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"F" * 32).decode(),
    "FOUNDATION_IDENTITY_HASH_KEY": base64.urlsafe_b64encode(b"H" * 32).decode(),
}
for name, value in defaults.items():
    os.environ.setdefault(name, value)

from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402

ALL_TENANT_SCOPES = {
    "foundation.tenant.read",
    "foundation.profile.read",
    "foundation.profile.write",
    "foundation.consent.read",
    "foundation.consent.write",
    "foundation.preference.read",
    "foundation.preference.write",
    "foundation.billing.read",
    "foundation.billing.write",
    "foundation.usage.write",
    "foundation.entitlement.read",
    "foundation.entitlement.write",
    "foundation.audit.read",
}


def token(scopes: set[str], tenant_id: str | None = None, subject: str = "test-user") -> str:
    now = int(time.time())
    claims = {
        "sub": subject,
        "iss": os.environ["FOUNDATION_JWT_ISSUER"],
        "aud": os.environ["FOUNDATION_JWT_AUDIENCE"],
        "iat": now,
        "exp": now + 600,
        "scope": " ".join(sorted(scopes)),
    }
    if tenant_id:
        claims["tenant_id"] = tenant_id
    return jwt.encode(claims, private_pem, algorithm="RS256")


@pytest.fixture(autouse=True)
def clean_database():
    if engine.dialect.name == "postgresql":
        Base.metadata.create_all(bind=engine)
        table_names = ", ".join(table.name for table in Base.metadata.sorted_tables)
        with engine.begin() as connection:
            connection.execute(text(f"TRUNCATE TABLE {table_names} CASCADE"))
    else:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def headers() -> Callable[..., dict[str, str]]:
    def build(
        scopes: set[str] | None = None,
        tenant_id: str | None = None,
        *,
        idem: str | None = None,
        subject: str = "test-user",
    ) -> dict[str, str]:
        values = {
            "Authorization": f"Bearer {token(scopes or ALL_TENANT_SCOPES, tenant_id, subject)}",
            "X-Correlation-ID": f"corr-{uuid.uuid4()}",
        }
        if idem is not None:
            values["Idempotency-Key"] = idem
        return values

    return build


@pytest.fixture
def tenant(client, headers) -> dict:
    response = client.post(
        "/v1/tenants",
        json={"slug": f"tenant-{uuid.uuid4().hex[:10]}", "name": "Test Tenant"},
        headers=headers({"foundation.admin"}, idem=f"tenant-{uuid.uuid4()}"),
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session
