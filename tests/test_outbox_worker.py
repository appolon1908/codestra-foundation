from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import httpx

from app.config import get_settings
from app.models import OutboxEvent
from app.outbox_worker import OutboxDispatcher


def _create_outbox_event(client, headers, tenant_id):
    response = client.put(
        f"/v1/tenants/{tenant_id}/entitlements/foundation.test",
        json={"enabled": True},
        headers=headers({"foundation.entitlement.write"}, tenant_id, idem="outbox-entitlement-key"),
    )
    assert response.status_code == 200


def _settings(enabled: bool = True):
    return replace(
        get_settings(),
        outbox_enabled=enabled,
        middleware_event_url="https://middleware.test/v1/events",
        middleware_access_token="test-token",
        middleware_ca_file="test-ca",
        middleware_client_cert_file="test-cert",
        middleware_client_key_file="test-key",
    )


def test_outbox_disabled_has_no_effect(client, headers, tenant, db):
    _create_outbox_event(client, headers, tenant["id"])
    assert OutboxDispatcher(settings=_settings(False)).run_once() == 0
    assert db.query(OutboxEvent).filter(OutboxEvent.tenant_id == tenant["id"]).all()[-1].status == "PENDING"


def test_outbox_delivers_tenant_correlation_and_idempotency_headers(client, headers, tenant, db):
    _create_outbox_event(client, headers, tenant["id"])
    row = (
        db.query(OutboxEvent)
        .filter(OutboxEvent.tenant_id == tenant["id"])
        .order_by(OutboxEvent.created_at.desc())
        .first()
    )
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = request.read().decode()
        return httpx.Response(202, request=request)

    dispatcher = OutboxDispatcher(
        settings=_settings(),
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert dispatcher.run_once() >= 1
    db.expire_all()
    delivered = db.get(OutboxEvent, row.id)
    assert delivered.status == "DELIVERED"
    assert captured["headers"]["idempotency-key"] == f"foundation:event:{row.id}"
    assert captured["headers"]["x-tenant-id"] == tenant["id"]
    assert '"source":"codestra-foundation"' in captured["body"]


def test_timeout_retries_same_event_identity_without_duplicate_row(client, headers, tenant, db):
    _create_outbox_event(client, headers, tenant["id"])
    row = (
        db.query(OutboxEvent)
        .filter(OutboxEvent.tenant_id == tenant["id"])
        .order_by(OutboxEvent.created_at.desc())
        .first()
    )

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("ambiguous middleware outcome", request=request)

    first = OutboxDispatcher(
        settings=_settings(),
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(timeout)),
    )
    assert first.run_once() >= 1
    db.expire_all()
    failed = db.get(OutboxEvent, row.id)
    assert failed.status == "PENDING"
    assert failed.last_error_code == "middleware_timeout"
    failed.available_at = datetime.now(timezone.utc)
    db.commit()

    event_ids: list[str] = []

    def succeed(request: httpx.Request) -> httpx.Response:
        event_ids.append(request.headers["X-Foundation-Event-ID"])
        return httpx.Response(202, request=request)

    second = OutboxDispatcher(
        settings=_settings(),
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(succeed)),
    )
    assert second.run_once() >= 1
    db.expire_all()
    delivered = db.get(OutboxEvent, row.id)
    assert delivered.status == "DELIVERED"
    assert delivered.attempts == 2
    assert event_ids == [row.id]
    assert db.query(OutboxEvent).filter(OutboxEvent.id == row.id).count() == 1
