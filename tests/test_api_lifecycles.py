from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


def _account(client, headers, tenant_id: str) -> dict:
    response = client.post(
        f"/v1/tenants/{tenant_id}/billing/accounts",
        json={
            "legal_name": "Lifecycle LLC",
            "currency": "USD",
            "billing_day": 10,
            "tax_profile": {"country": "US"},
        },
        headers=headers({"foundation.billing.write"}, tenant_id, idem=f"account-{uuid.uuid4()}"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _subscription(client, headers, tenant_id: str, account_id: str, suite_code: str = "ENGAGEMENT") -> dict:
    now = datetime.now(timezone.utc)
    response = client.post(
        f"/v1/tenants/{tenant_id}/billing/subscriptions",
        json={
            "account_id": account_id,
            "suite_code": suite_code,
            "plan_code": f"{suite_code}_PRO",
            "status": "ACTIVE",
            "quota_behavior": "OVERAGE",
            "period_start": now.isoformat(),
            "period_end": (now + timedelta(days=30)).isoformat(),
        },
        headers=headers({"foundation.billing.write"}, tenant_id, idem=f"subscription-{uuid.uuid4()}"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _meter(
    client,
    headers,
    tenant_id: str,
    meter_code: str,
    aggregation: str,
    unit: str = "messages",
) -> dict:
    response = client.post(
        f"/v1/tenants/{tenant_id}/billing/meters",
        json={
            "suite_code": "ENGAGEMENT",
            "meter_code": meter_code,
            "unit": unit,
            "aggregation": aggregation,
        },
        headers=headers({"foundation.billing.write"}, tenant_id, idem=f"meter-{uuid.uuid4()}"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _profile(client, headers, tenant_id: str, email: str) -> dict:
    response = client.post(
        f"/v1/tenants/{tenant_id}/profiles",
        json={
            "external_ref": f"crm-{uuid.uuid4()}",
            "display_name": "Lifecycle User",
            "attributes": {"locale": "en"},
            "identities": [{"kind": "EMAIL", "value": email, "verified": True, "primary": True}],
        },
        headers=headers({"foundation.profile.write"}, tenant_id, idem=f"profile-{uuid.uuid4()}"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_tenant_lists_entitlements_and_effective_access(client, headers, tenant):
    tenant_id = tenant["id"]
    denied = client.get("/v1/tenants", headers=headers({"foundation.tenant.read"}, tenant_id))
    assert denied.status_code == 403
    listed = client.get("/v1/tenants?status=ACTIVE", headers=headers({"foundation.admin"}))
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [tenant_id]

    before = client.get(
        f"/v1/tenants/{tenant_id}/access/ENGAGEMENT",
        headers=headers({"foundation.entitlement.read"}, tenant_id),
    )
    assert before.json()["allowed"] is False
    assert before.json()["reason"] == "SUBSCRIPTION_NOT_FOUND"

    account = _account(client, headers, tenant_id)
    _subscription(client, headers, tenant_id, account["id"])
    entitlement = client.put(
        f"/v1/tenants/{tenant_id}/entitlements/email.send",
        json={"enabled": True, "limit_value": "1000", "unit": "messages"},
        headers=headers({"foundation.entitlement.write"}, tenant_id, idem=f"entitlement-{uuid.uuid4()}"),
    )
    assert entitlement.status_code == 200
    all_entitlements = client.get(
        f"/v1/tenants/{tenant_id}/entitlements?enabled=true",
        headers=headers({"foundation.entitlement.read"}, tenant_id),
    )
    assert [row["entitlement_key"] for row in all_entitlements.json()] == ["email.send"]
    allowed = client.get(
        f"/v1/tenants/{tenant_id}/access/ENGAGEMENT?entitlement_key=email.send",
        headers=headers({"foundation.entitlement.read"}, tenant_id),
    )
    assert allowed.json()["allowed"] is True
    assert allowed.json()["reason"] == "ALLOWED"


def test_profile_update_identity_lifecycle_and_merge_history(client, headers, tenant):
    tenant_id = tenant["id"]
    profile = _profile(client, headers, tenant_id, "lifecycle@example.com")
    patched = client.patch(
        f"/v1/tenants/{tenant_id}/profiles/{profile['id']}",
        json={"display_name": "Updated User", "attributes": {"locale": "de"}, "expected_version": 1},
        headers=headers({"foundation.profile.write"}, tenant_id, idem=f"profile-patch-{uuid.uuid4()}"),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["version"] == 2
    stale = client.patch(
        f"/v1/tenants/{tenant_id}/profiles/{profile['id']}",
        json={"display_name": "Stale", "expected_version": 1},
        headers=headers({"foundation.profile.write"}, tenant_id, idem=f"profile-stale-{uuid.uuid4()}"),
    )
    assert stale.status_code == 409

    added = client.post(
        f"/v1/tenants/{tenant_id}/profiles/{profile['id']}/identities",
        json={"kind": "PHONE", "value": "+15550102030", "verified": False, "primary": True},
        headers=headers({"foundation.profile.write"}, tenant_id, idem=f"identity-add-{uuid.uuid4()}"),
    )
    assert added.status_code == 201, added.text
    identity = added.json()
    revoked = client.patch(
        f"/v1/tenants/{tenant_id}/profiles/{profile['id']}/identities/{identity['id']}",
        json={"status": "REVOKED", "expected_version": 1},
        headers=headers({"foundation.profile.write"}, tenant_id, idem=f"identity-revoke-{uuid.uuid4()}"),
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"
    assert revoked.json()["primary"] is False
    unresolved = client.post(
        f"/v1/tenants/{tenant_id}/profiles/resolve",
        json={"identities": [{"kind": "PHONE", "value": "+15550102030"}]},
        headers=headers({"foundation.profile.read"}, tenant_id),
    )
    assert unresolved.status_code == 404
    listed = client.get(
        f"/v1/tenants/{tenant_id}/profiles",
        headers=headers({"foundation.profile.read"}, tenant_id),
    )
    assert listed.status_code == 200
    assert listed.json()[0]["display_name"] == "Updated User"

    target = _profile(client, headers, tenant_id, "target-lifecycle@example.com")
    merged = client.post(
        f"/v1/tenants/{tenant_id}/profiles/{profile['id']}/merge",
        json={"target_profile_id": target["id"], "reason": "confirmed duplicate lifecycle record"},
        headers=headers({"foundation.profile.write"}, tenant_id, idem=f"merge-{uuid.uuid4()}"),
    )
    assert merged.status_code == 200
    email_identities = [row for row in merged.json()["identities"] if row["kind"] == "EMAIL"]
    assert sum(row["primary"] for row in email_identities) == 1
    history = client.get(
        f"/v1/tenants/{tenant_id}/profiles/{target['id']}/merges",
        headers=headers({"foundation.profile.read"}, tenant_id),
    )
    assert history.status_code == 200
    assert history.json()[0]["source_profile_id"] == profile["id"]


def test_consent_and_preference_readback_urls(client, headers, tenant):
    tenant_id = tenant["id"]
    now = datetime.now(timezone.utc)
    consent = client.post(
        f"/v1/tenants/{tenant_id}/communications/consents",
        json={
            "identity": {"kind": "EMAIL", "value": "preference@example.com"},
            "topic": "marketing",
            "channel": "EMAIL",
            "state": "GRANTED",
            "lawful_basis": "consent",
            "source": "preference-center",
            "occurred_at": now.isoformat(),
            "evidence": {"form": "v1"},
        },
        headers=headers({"foundation.consent.write"}, tenant_id, idem=f"consent-{uuid.uuid4()}"),
    )
    assert consent.status_code == 201, consent.text
    consent_read = client.get(
        f"/v1/tenants/{tenant_id}/communications/consents/{consent.json()['id']}",
        headers=headers({"foundation.consent.read"}, tenant_id),
    )
    assert consent_read.status_code == 200

    preference = client.put(
        f"/v1/tenants/{tenant_id}/communications/preferences",
        json={
            "identity": {"kind": "EMAIL", "value": "preference@example.com"},
            "topic": "marketing",
            "channel": "EMAIL",
            "state": "SUBSCRIBED",
            "source": "preference-center",
        },
        headers=headers({"foundation.preference.write"}, tenant_id, idem=f"preference-{uuid.uuid4()}"),
    )
    assert preference.status_code == 200
    listed = client.get(
        f"/v1/tenants/{tenant_id}/communications/preferences"
        "?identity_kind=EMAIL&identity_value=preference%40example.com",
        headers=headers({"foundation.preference.read"}, tenant_id),
    )
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == preference.json()["id"]
    read = client.get(
        f"/v1/tenants/{tenant_id}/communications/preferences/{preference.json()['id']}",
        headers=headers({"foundation.preference.read"}, tenant_id),
    )
    assert read.status_code == 200


def test_billing_resource_state_machines(client, headers, tenant):
    tenant_id = tenant["id"]
    account = _account(client, headers, tenant_id)
    subscription = _subscription(client, headers, tenant_id, account["id"])
    changed_account = client.patch(
        f"/v1/tenants/{tenant_id}/billing/accounts/{account['id']}",
        json={"status": "PAST_DUE", "expected_version": 1},
        headers=headers({"foundation.billing.write"}, tenant_id, idem=f"account-patch-{uuid.uuid4()}"),
    )
    assert changed_account.status_code == 200
    assert changed_account.json()["version"] == 2
    accounts = client.get(
        f"/v1/tenants/{tenant_id}/billing/accounts?status=PAST_DUE",
        headers=headers({"foundation.billing.read"}, tenant_id),
    )
    assert [row["id"] for row in accounts.json()] == [account["id"]]
    new_suite_while_past_due = client.post(
        f"/v1/tenants/{tenant_id}/billing/subscriptions",
        json={
            "account_id": account["id"],
            "suite_code": "VOICE",
            "plan_code": "VOICE_PRO",
            "status": "ACTIVE",
            "quota_behavior": "OVERAGE",
            "period_start": datetime.now(timezone.utc).isoformat(),
            "period_end": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        },
        headers=headers({"foundation.billing.write"}, tenant_id, idem=f"past-due-suite-{uuid.uuid4()}"),
    )
    assert new_suite_while_past_due.status_code == 409
    close_with_active_subscription = client.patch(
        f"/v1/tenants/{tenant_id}/billing/accounts/{account['id']}",
        json={"status": "CLOSED", "expected_version": 2},
        headers=headers({"foundation.billing.write"}, tenant_id, idem=f"account-close-blocked-{uuid.uuid4()}"),
    )
    assert close_with_active_subscription.status_code == 409
    suspended = client.patch(
        f"/v1/tenants/{tenant_id}/billing/subscriptions/{subscription['id']}",
        json={"status": "SUSPENDED", "expected_version": 1},
        headers=headers({"foundation.billing.write"}, tenant_id, idem=f"subscription-patch-{uuid.uuid4()}"),
    )
    assert suspended.status_code == 200
    cancelled = client.patch(
        f"/v1/tenants/{tenant_id}/billing/subscriptions/{subscription['id']}",
        json={"status": "CANCELLED", "expected_version": 2},
        headers=headers({"foundation.billing.write"}, tenant_id, idem=f"subscription-cancel-{uuid.uuid4()}"),
    )
    assert cancelled.status_code == 200
    invalid = client.patch(
        f"/v1/tenants/{tenant_id}/billing/subscriptions/{subscription['id']}",
        json={"status": "ACTIVE", "expected_version": 3},
        headers=headers({"foundation.billing.write"}, tenant_id, idem=f"subscription-invalid-{uuid.uuid4()}"),
    )
    assert invalid.status_code == 409

    meter = _meter(client, headers, tenant_id, "email.accepted", "SUM")
    meter_changed = client.patch(
        f"/v1/tenants/{tenant_id}/billing/meters/{meter['id']}",
        json={"unit": "accepted_messages", "expected_version": 1},
        headers=headers({"foundation.billing.write"}, tenant_id, idem=f"meter-patch-{uuid.uuid4()}"),
    )
    assert meter_changed.status_code == 200
    stale_meter = client.patch(
        f"/v1/tenants/{tenant_id}/billing/meters/{meter['id']}",
        json={"enabled": False, "expected_version": 1},
        headers=headers({"foundation.billing.write"}, tenant_id, idem=f"meter-stale-{uuid.uuid4()}"),
    )
    assert stale_meter.status_code == 409


def test_usage_aggregation_and_invoice_transition_logic(client, headers, tenant):
    tenant_id = tenant["id"]
    account = _account(client, headers, tenant_id)
    subscription = _subscription(client, headers, tenant_id, account["id"])
    _meter(client, headers, tenant_id, "email.accepted", "SUM")
    _meter(client, headers, tenant_id, "profiles.active", "MAX", unit="profiles")
    now = datetime.now(timezone.utc)
    for index, (meter_code, quantity) in enumerate(
        [("email.accepted", "2"), ("email.accepted", "3"), ("profiles.active", "10"), ("profiles.active", "12")]
    ):
        usage = client.post(
            f"/v1/tenants/{tenant_id}/billing/usage",
            json={
                "account_id": account["id"],
                "subscription_id": subscription["id"],
                "suite_code": "ENGAGEMENT",
                "meter_code": meter_code,
                "quantity": quantity,
                "event_key": f"lifecycle:event:{index}",
                "source_object_id": f"object-{index}",
                "occurred_at": (now + timedelta(seconds=index)).isoformat(),
            },
            headers=headers({"foundation.usage.write"}, tenant_id, idem=f"usage-{uuid.uuid4()}"),
        )
        assert usage.status_code == 201, usage.text
    summary = client.get(
        f"/v1/tenants/{tenant_id}/billing/usage/summary",
        params={
            "period_start": (now - timedelta(seconds=1)).isoformat(),
            "period_end": (now + timedelta(minutes=1)).isoformat(),
        },
        headers=headers({"foundation.billing.read"}, tenant_id),
    )
    assert summary.status_code == 200, summary.text
    quantities = {row["meter_code"]: row["quantity"] for row in summary.json()}
    assert quantities == {"email.accepted": "5.000000", "profiles.active": "12.000000"}

    invoice = client.post(
        f"/v1/tenants/{tenant_id}/billing/invoices",
        json={
            "account_id": account["id"],
            "period_start": now.isoformat(),
            "period_end": (now + timedelta(days=30)).isoformat(),
            "currency": "USD",
            "lines": [{"line_code": "plan", "description": "Plan", "quantity": "1", "unit_amount": "20"}],
        },
        headers=headers({"foundation.billing.write"}, tenant_id, idem=f"invoice-{uuid.uuid4()}"),
    )
    assert invoice.status_code == 201, invoice.text
    pending = client.patch(
        f"/v1/tenants/{tenant_id}/billing/invoices/{invoice.json()['id']}/status",
        json={"status": "PENDING_PAYMENT", "expected_version": 1},
        headers=headers({"foundation.billing.write"}, tenant_id, idem=f"invoice-pending-{uuid.uuid4()}"),
    )
    assert pending.status_code == 200
    assert pending.json()["version"] == 2
    refunded_too_early = client.patch(
        f"/v1/tenants/{tenant_id}/billing/invoices/{invoice.json()['id']}/status",
        json={"status": "REFUNDED", "expected_version": 2},
        headers=headers({"foundation.billing.write"}, tenant_id, idem=f"invoice-refund-{uuid.uuid4()}"),
    )
    assert refunded_too_early.status_code == 409
    invoices = client.get(
        f"/v1/tenants/{tenant_id}/billing/invoices?status=PENDING_PAYMENT",
        headers=headers({"foundation.billing.read"}, tenant_id),
    )
    assert [row["id"] for row in invoices.json()] == [invoice.json()["id"]]
