from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.models import AuditEvent, BillingAccount, IdempotencyRecord, OutboxEvent


def test_pii_is_not_plaintext_in_internal_ledgers(client, headers, tenant, db):
    tenant_id = tenant["id"]
    response = client.post(
        f"/v1/tenants/{tenant_id}/profiles",
        json={
            "display_name": "Sensitive Person",
            "attributes": {"private_note": "secret-note-value"},
            "identities": [{"kind": "EMAIL", "value": "sensitive@example.com", "primary": True}],
        },
        headers=headers({"foundation.profile.write"}, tenant_id, idem="pii-ledger-profile-key"),
    )
    assert response.status_code == 201
    idempotency = db.query(IdempotencyRecord).filter(IdempotencyRecord.tenant_id == tenant_id).all()
    outbox = db.query(OutboxEvent).filter(OutboxEvent.tenant_id == tenant_id).all()
    audit = db.query(AuditEvent).filter(AuditEvent.tenant_id == tenant_id).all()
    combined = " ".join(
        [row.response_json for row in idempotency]
        + [row.payload_json for row in outbox]
        + [row.detail_hash for row in audit]
    )
    assert "sensitive@example.com" not in combined
    assert "Sensitive Person" not in combined
    assert "secret-note-value" not in combined


def test_tax_profile_is_encrypted_at_rest(client, headers, tenant, db):
    tenant_id = tenant["id"]
    response = client.post(
        f"/v1/tenants/{tenant_id}/billing/accounts",
        json={
            "legal_name": "Taxpayer LLC",
            "currency": "USD",
            "billing_day": 20,
            "tax_profile": {"tax_id": "99-1234567", "country": "US"},
        },
        headers=headers({"foundation.billing.write"}, tenant_id, idem="encrypted-tax-profile"),
    )
    assert response.status_code == 201
    stored = db.get(BillingAccount, response.json()["id"])
    assert "99-1234567" not in stored.tax_profile_enc
    assert response.json()["tax_profile"]["tax_id"] == "99-1234567"


def test_openapi_has_no_secret_or_payment_card_fields(client):
    schema = client.get("/openapi.json").json()
    serialized = str(schema).lower()
    for forbidden in ("card_number", "cvv", "private_key", "client_secret", "access_token"):
        assert forbidden not in serialized


def test_postgresql_rejects_raw_consent_mutation(client, headers, tenant, db):
    if db.bind.dialect.name != "postgresql":
        pytest.skip("PostgreSQL trigger is validated in the PostgreSQL CI job")
    tenant_id = tenant["id"]
    response = client.post(
        f"/v1/tenants/{tenant_id}/communications/consents",
        json={
            "identity": {"kind": "EMAIL", "value": "append-only@example.com"},
            "topic": "marketing.news",
            "channel": "EMAIL",
            "state": "GRANTED",
            "lawful_basis": "consent",
            "source": "test",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "evidence": {"version": "1"},
        },
        headers=headers({"foundation.consent.write"}, tenant_id, idem="append-only-consent"),
    )
    assert response.status_code == 201
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE consent_events SET state='REVOKED' WHERE id=:id"), {"id": response.json()["id"]})
        db.commit()
    db.rollback()
