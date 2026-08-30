from __future__ import annotations

import uuid

from app.models import Identity, Profile


def _profile_payload(email: str, phone: str | None = None, external_ref: str | None = None):
    identities = [{"kind": "EMAIL", "value": email, "verified": True, "primary": True}]
    if phone:
        identities.append({"kind": "PHONE", "value": phone, "verified": False, "primary": True})
    return {
        "external_ref": external_ref,
        "display_name": "Ada Example",
        "attributes": {"locale": "en", "tier": "gold"},
        "identities": identities,
    }


def test_profile_identity_encryption_resolution_and_duplicate_guard(client, headers, tenant, db):
    tenant_id = tenant["id"]
    payload = _profile_payload("Ada.Example@Example.com", "+15551234567", "crm-contact-1")
    first = client.post(
        f"/v1/tenants/{tenant_id}/profiles",
        json=payload,
        headers=headers({"foundation.profile.write"}, tenant_id, idem="profile-create-key"),
    )
    assert first.status_code == 201, first.text
    profile = first.json()
    assert profile["identities"][0]["value"] == "ada.example@example.com"
    assert profile["attributes"]["tier"] == "gold"

    stored_profile = db.get(Profile, profile["id"])
    stored_identities = db.query(Identity).filter(Identity.profile_id == profile["id"]).all()
    assert "Ada Example" not in (stored_profile.display_name_enc or "")
    assert "gold" not in stored_profile.attributes_enc
    assert all("ada.example@example.com" not in item.value_enc for item in stored_identities)

    resolved = client.post(
        f"/v1/tenants/{tenant_id}/profiles/resolve",
        json={"identities": [{"kind": "EMAIL", "value": "ADA.EXAMPLE@example.com"}]},
        headers=headers({"foundation.profile.read"}, tenant_id),
    )
    assert resolved.status_code == 200
    assert resolved.json()["id"] == profile["id"]

    duplicate = client.post(
        f"/v1/tenants/{tenant_id}/profiles",
        json=_profile_payload("ada.example@example.com"),
        headers=headers({"foundation.profile.write"}, tenant_id, idem="profile-duplicate-key"),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "identity_already_assigned"


def test_profile_validation_and_tenant_boundary(client, headers, tenant):
    tenant_id = tenant["id"]
    invalid_phone = client.post(
        f"/v1/tenants/{tenant_id}/profiles",
        json=_profile_payload("valid@example.com", "5551234567"),
        headers=headers({"foundation.profile.write"}, tenant_id, idem="profile-invalid-phone"),
    )
    assert invalid_phone.status_code == 422
    wrong_tenant = client.post(
        f"/v1/tenants/{tenant_id}/profiles",
        json=_profile_payload("other@example.com"),
        headers=headers({"foundation.profile.write"}, str(uuid.uuid4()), idem="profile-wrong-tenant"),
    )
    assert wrong_tenant.status_code == 403


def test_deterministic_profile_merge_moves_identity_links(client, headers, tenant):
    tenant_id = tenant["id"]
    source = client.post(
        f"/v1/tenants/{tenant_id}/profiles",
        json=_profile_payload("source@example.com", external_ref="source"),
        headers=headers({"foundation.profile.write"}, tenant_id, idem="profile-source-key"),
    ).json()
    target = client.post(
        f"/v1/tenants/{tenant_id}/profiles",
        json=_profile_payload("target@example.com", external_ref="target"),
        headers=headers({"foundation.profile.write"}, tenant_id, idem="profile-target-key"),
    ).json()
    merged = client.post(
        f"/v1/tenants/{tenant_id}/profiles/{source['id']}/merge",
        json={"target_profile_id": target["id"], "reason": "verified duplicate customer"},
        headers=headers({"foundation.profile.write"}, tenant_id, idem="profile-merge-key"),
    )
    assert merged.status_code == 200, merged.text
    assert {item["value"] for item in merged.json()["identities"]} == {
        "source@example.com",
        "target@example.com",
    }
    resolved = client.post(
        f"/v1/tenants/{tenant_id}/profiles/resolve",
        json={"identities": [{"kind": "EMAIL", "value": "source@example.com"}]},
        headers=headers({"foundation.profile.read"}, tenant_id),
    )
    assert resolved.json()["id"] == target["id"]
