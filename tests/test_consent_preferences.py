from __future__ import annotations

from datetime import datetime, timezone

from app.models import ConsentEvent, IdempotencyRecord, Preference

IDENTITY = {"kind": "EMAIL", "value": "recipient@example.com"}


def _effective(client, headers, tenant_id, channel="EMAIL"):
    return client.get(
        f"/v1/tenants/{tenant_id}/communications/effective",
        params={
            "identity_kind": "EMAIL",
            "identity_value": "recipient@example.com",
            "topic": "marketing.news",
            "channel": channel,
        },
        headers=headers({"foundation.preference.read"}, tenant_id),
    )


def _consent(state: str, channel: str = "EMAIL"):
    return {
        "identity": IDENTITY,
        "topic": "marketing.news",
        "channel": channel,
        "state": state,
        "lawful_basis": "consent",
        "source": "preference-center",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "evidence": {"form_version": "v3", "ip_reference": "hash:abc"},
    }


def test_consent_and_preference_effective_policy(client, headers, tenant, db):
    tenant_id = tenant["id"]
    assert _effective(client, headers, tenant_id).json()["reason"] == "NO_CONSENT"
    subscribe = {
        "identity": IDENTITY,
        "topic": "marketing.news",
        "channel": "EMAIL",
        "state": "SUBSCRIBED",
        "source": "preference-center",
    }
    missing_consent = client.put(
        f"/v1/tenants/{tenant_id}/communications/preferences",
        json=subscribe,
        headers=headers({"foundation.preference.write"}, tenant_id, idem="pref-no-consent"),
    )
    assert missing_consent.status_code == 422

    grant_headers = headers({"foundation.consent.write"}, tenant_id, idem="consent-grant-key")
    grant_payload = _consent("GRANTED")
    granted = client.post(
        f"/v1/tenants/{tenant_id}/communications/consents",
        json=grant_payload,
        headers=grant_headers,
    )
    replay = client.post(
        f"/v1/tenants/{tenant_id}/communications/consents",
        json=grant_payload,
        headers=grant_headers,
    )
    assert granted.status_code == 201
    assert replay.status_code == 201
    assert db.query(ConsentEvent).count() == 1

    preferred = client.put(
        f"/v1/tenants/{tenant_id}/communications/preferences",
        json=subscribe,
        headers=headers({"foundation.preference.write"}, tenant_id, idem="pref-subscribe-key"),
    )
    assert preferred.status_code == 200
    assert _effective(client, headers, tenant_id).json()["allowed"] is True

    revoked = client.post(
        f"/v1/tenants/{tenant_id}/communications/consents",
        json=_consent("REVOKED"),
        headers=headers({"foundation.consent.write"}, tenant_id, idem="consent-revoke-key"),
    )
    assert revoked.status_code == 201
    effective = _effective(client, headers, tenant_id).json()
    assert effective["allowed"] is False
    assert effective["reason"] == "CONSENT_REVOKED"
    assert db.query(Preference).one().state == "UNSUBSCRIBED"
    assert db.query(IdempotencyRecord).count() >= 4


def test_global_revocation_blocks_every_channel_and_history_is_append_only(client, headers, tenant, db):
    tenant_id = tenant["id"]
    response = client.post(
        f"/v1/tenants/{tenant_id}/communications/consents",
        json=_consent("REVOKED", "ALL"),
        headers=headers({"foundation.consent.write"}, tenant_id, idem="consent-global-revoke"),
    )
    assert response.status_code == 201
    sms = _effective(client, headers, tenant_id, "SMS").json()
    assert sms["allowed"] is False
    assert sms["reason"] == "CONSENT_REVOKED"
    history = client.get(
        f"/v1/tenants/{tenant_id}/communications/consents",
        params={"identity_kind": "EMAIL", "identity_value": "recipient@example.com"},
        headers=headers({"foundation.consent.read"}, tenant_id),
    )
    assert history.status_code == 200
    assert len(history.json()) == db.query(ConsentEvent).count() == 1


def test_consent_validation_rejects_bad_identity_and_missing_scope(client, headers, tenant):
    tenant_id = tenant["id"]
    bad = _consent("GRANTED")
    bad["identity"] = {"kind": "PHONE", "value": "555-0100"}
    response = client.post(
        f"/v1/tenants/{tenant_id}/communications/consents",
        json=bad,
        headers=headers({"foundation.consent.write"}, tenant_id, idem="bad-phone-consent"),
    )
    assert response.status_code == 422
    denied = client.post(
        f"/v1/tenants/{tenant_id}/communications/consents",
        json=_consent("GRANTED"),
        headers=headers({"foundation.profile.write"}, tenant_id, idem="missing-consent-scope"),
    )
    assert denied.status_code == 403
