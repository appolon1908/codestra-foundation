from __future__ import annotations

import uuid

from app.models import AuditEvent, IdempotencyRecord, OutboxEvent


def test_health_auth_and_mutation_fail_closed(client, headers, monkeypatch):
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/v1/tenants/not-real").status_code == 401

    class Disabled:
        mutations_enabled = False

    monkeypatch.setattr("app.api.get_settings", lambda: Disabled())
    response = client.post(
        "/v1/tenants",
        json={"slug": "disabled-tenant", "name": "Disabled"},
        headers=headers({"foundation.admin"}, idem="disabled-tenant-key"),
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "foundation_mutations_disabled"


def test_tenant_idempotency_and_payload_mismatch(client, headers, db):
    key = "tenant-create-idempotent"
    request = {"slug": "alpha-tenant", "name": "Alpha"}
    auth = headers({"foundation.admin"}, idem=key)
    first = client.post("/v1/tenants", json=request, headers=auth)
    replay = client.post("/v1/tenants", json=request, headers=auth)
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()

    mismatch = client.post(
        "/v1/tenants",
        json={"slug": "beta-tenant", "name": "Beta"},
        headers=auth,
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"] == "idempotency_key_payload_mismatch"
    assert db.query(IdempotencyRecord).count() == 1
    assert db.query(AuditEvent).count() == 1
    assert db.query(OutboxEvent).count() == 1


def test_tenant_isolation_and_entitlement_versioning(client, headers, tenant, db):
    tenant_id = tenant["id"]
    own = client.get(
        f"/v1/tenants/{tenant_id}",
        headers=headers({"foundation.tenant.read"}, tenant_id),
    )
    assert own.status_code == 200
    denied = client.get(
        f"/v1/tenants/{tenant_id}",
        headers=headers({"foundation.tenant.read"}, str(uuid.uuid4())),
    )
    assert denied.status_code == 403

    first = client.put(
        f"/v1/tenants/{tenant_id}/entitlements/email.monthly_messages",
        json={"enabled": True, "limit_value": "10000", "unit": "messages"},
        headers=headers({"foundation.entitlement.write"}, tenant_id, idem="entitlement-create-key"),
    )
    assert first.status_code == 200
    assert first.json()["version"] == 1
    second = client.put(
        f"/v1/tenants/{tenant_id}/entitlements/email.monthly_messages",
        json={
            "enabled": True,
            "limit_value": "20000",
            "unit": "messages",
            "expected_version": 1,
        },
        headers=headers({"foundation.entitlement.write"}, tenant_id, idem="entitlement-update-key"),
    )
    assert second.status_code == 200
    assert second.json()["version"] == 2
    conflict = client.put(
        f"/v1/tenants/{tenant_id}/entitlements/email.monthly_messages",
        json={"enabled": False, "expected_version": 1},
        headers=headers({"foundation.entitlement.write"}, tenant_id, idem="entitlement-conflict-key"),
    )
    assert conflict.status_code == 409
    assert db.query(AuditEvent).filter(AuditEvent.tenant_id == tenant_id).count() == 3
