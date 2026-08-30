from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.models import Invoice, InvoiceLine, UsageEvent


def _account(client, headers, tenant_id):
    response = client.post(
        f"/v1/tenants/{tenant_id}/billing/accounts",
        json={
            "legal_name": "Example LLC",
            "currency": "USD",
            "billing_day": 15,
            "tax_profile": {"country": "US", "tax_id": "encrypted-value"},
        },
        headers=headers({"foundation.billing.write"}, tenant_id, idem="billing-account-key"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _subscription(client, headers, tenant_id, account_id):
    now = datetime.now(timezone.utc)
    response = client.post(
        f"/v1/tenants/{tenant_id}/billing/subscriptions",
        json={
            "account_id": account_id,
            "suite_code": "ENGAGEMENT",
            "plan_code": "ENGAGEMENT_PRO",
            "status": "ACTIVE",
            "quota_behavior": "OVERAGE",
            "period_start": now.isoformat(),
            "period_end": (now + timedelta(days=30)).isoformat(),
        },
        headers=headers({"foundation.billing.write"}, tenant_id, idem="subscription-create-key"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_usage_is_exactly_once_and_subscription_scoped(client, headers, tenant, db):
    tenant_id = tenant["id"]
    account = _account(client, headers, tenant_id)
    subscription = _subscription(client, headers, tenant_id, account["id"])
    now = datetime.now(timezone.utc)
    usage = {
        "account_id": account["id"],
        "subscription_id": subscription["id"],
        "suite_code": "ENGAGEMENT",
        "meter_code": "email.accepted",
        "quantity": "1",
        "event_key": "klyrow:msg:message-123",
        "source_object_id": "message-123",
        "occurred_at": now.isoformat(),
    }
    first = client.post(
        f"/v1/tenants/{tenant_id}/billing/usage",
        json=usage,
        headers=headers({"foundation.usage.write"}, tenant_id, idem="usage-first-key"),
    )
    second = client.post(
        f"/v1/tenants/{tenant_id}/billing/usage",
        json=usage,
        headers=headers({"foundation.usage.write"}, tenant_id, idem="usage-second-key"),
    )
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert db.query(UsageEvent).count() == 1

    mismatch = usage | {"quantity": "2"}
    conflict = client.post(
        f"/v1/tenants/{tenant_id}/billing/usage",
        json=mismatch,
        headers=headers({"foundation.usage.write"}, tenant_id, idem="usage-mismatch-key"),
    )
    assert conflict.status_code == 409
    assert db.query(UsageEvent).count() == 1

    wrong_subscription = usage | {"subscription_id": str(uuid.uuid4()), "event_key": "klyrow:msg:message-456"}
    rejected = client.post(
        f"/v1/tenants/{tenant_id}/billing/usage",
        json=wrong_subscription,
        headers=headers({"foundation.usage.write"}, tenant_id, idem="usage-wrong-subscription"),
    )
    assert rejected.status_code == 422


def test_invoice_totals_round_once_per_line_and_period_is_unique(client, headers, tenant, db):
    tenant_id = tenant["id"]
    account = _account(client, headers, tenant_id)
    now = datetime.now(timezone.utc)
    invoice = {
        "account_id": account["id"],
        "period_start": now.isoformat(),
        "period_end": (now + timedelta(days=30)).isoformat(),
        "currency": "USD",
        "lines": [
            {"line_code": "included", "description": "Included plan", "quantity": "1", "unit_amount": "10.005"},
            {"line_code": "overage", "description": "Email overage", "quantity": "3", "unit_amount": "0.335"},
        ],
        "discount_amount": "1.00",
        "credit_amount": "0.25",
        "tax_amount": "0.83",
    }
    response = client.post(
        f"/v1/tenants/{tenant_id}/billing/invoices",
        json=invoice,
        headers=headers({"foundation.billing.write"}, tenant_id, idem="invoice-create-key"),
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["subtotal_amount"] == "11.02"
    assert created["total_amount"] == "10.60"
    assert db.query(Invoice).count() == 1
    assert db.query(InvoiceLine).count() == 2

    duplicate = client.post(
        f"/v1/tenants/{tenant_id}/billing/invoices",
        json=invoice,
        headers=headers({"foundation.billing.write"}, tenant_id, idem="invoice-other-key"),
    )
    assert duplicate.status_code == 409
    assert db.query(Invoice).count() == 1

    negative = invoice | {"period_end": (now + timedelta(days=31)).isoformat(), "credit_amount": "1000.00"}
    rejected = client.post(
        f"/v1/tenants/{tenant_id}/billing/invoices",
        json=negative,
        headers=headers({"foundation.billing.write"}, tenant_id, idem="invoice-negative-key"),
    )
    assert rejected.status_code == 422


def test_billing_account_tenant_boundary(client, headers, tenant):
    tenant_id = tenant["id"]
    account = _account(client, headers, tenant_id)
    response = client.get(
        f"/v1/tenants/{tenant_id}/billing/accounts/{account['id']}",
        headers=headers({"foundation.billing.read"}, str(uuid.uuid4())),
    )
    assert response.status_code == 403
