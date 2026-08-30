from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TenantCreate(StrictModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,78}[a-z0-9]$", max_length=80)
    name: str = Field(min_length=1, max_length=200)


class TenantPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal["ACTIVE", "SUSPENDED", "CLOSED"] | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if self.name is None and self.status is None:
            raise ValueError("at_least_one_field_required")
        return self


class TenantOut(StrictModel):
    id: str
    slug: str
    name: str
    status: str
    created_at: datetime
    updated_at: datetime


class EntitlementUpsert(StrictModel):
    enabled: bool = True
    limit_value: Decimal | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=40)
    effective_at: datetime | None = None
    expires_at: datetime | None = None
    expected_version: int | None = Field(default=None, ge=1)


class EntitlementOut(StrictModel):
    id: str
    tenant_id: str
    entitlement_key: str
    enabled: bool
    limit_value: Decimal | None
    unit: str | None
    effective_at: datetime
    expires_at: datetime | None
    version: int
    updated_at: datetime


class IdentityIn(StrictModel):
    kind: Literal["EMAIL", "PHONE", "EXTERNAL"]
    value: str = Field(min_length=1, max_length=320)
    verified: bool = False
    primary: bool = False


class IdentityOut(StrictModel):
    id: str
    kind: str
    value: str
    verified_at: datetime | None
    primary: bool


class ProfileCreate(StrictModel):
    external_ref: str | None = Field(default=None, max_length=180)
    display_name: str | None = Field(default=None, max_length=200)
    attributes: dict[str, Any] = Field(default_factory=dict)
    identities: list[IdentityIn] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_identities_and_primaries(self):
        identities = [
            (item.kind, item.value.strip().lower() if item.kind == "EMAIL" else item.value.strip())
            for item in self.identities
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate_identity_in_request")
        primary_kinds = [item.kind for item in self.identities if item.primary]
        if len(primary_kinds) != len(set(primary_kinds)):
            raise ValueError("multiple_primary_identities_for_kind")
        return self


class ProfileOut(StrictModel):
    id: str
    tenant_id: str
    external_ref: str | None
    display_name: str | None
    attributes: dict[str, Any]
    identities: list[IdentityOut]
    version: int
    merged_into_id: str | None
    created_at: datetime
    updated_at: datetime


class ProfileResolve(StrictModel):
    identities: list[IdentityIn] = Field(min_length=1, max_length=20)


class ProfileMergeRequest(StrictModel):
    target_profile_id: str = Field(min_length=36, max_length=36)
    reason: str = Field(min_length=3, max_length=500)


class IdentityReference(StrictModel):
    kind: Literal["EMAIL", "PHONE", "EXTERNAL"]
    value: str = Field(min_length=1, max_length=320)


class ConsentCreate(StrictModel):
    identity: IdentityReference
    topic: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,119}$")
    channel: Literal["ALL", "EMAIL", "SMS", "VOICE", "SOCIAL"]
    state: Literal["GRANTED", "DENIED", "REVOKED"]
    lawful_basis: str = Field(min_length=2, max_length=80)
    source: str = Field(min_length=2, max_length=200)
    occurred_at: datetime
    evidence: dict[str, Any] = Field(default_factory=dict)


class ConsentOut(StrictModel):
    id: str
    tenant_id: str
    profile_id: str | None
    identity_reference: str
    topic: str
    channel: str
    state: str
    lawful_basis: str
    source: str
    evidence_hash: str
    occurred_at: datetime
    created_at: datetime


class PreferenceUpsert(StrictModel):
    identity: IdentityReference
    topic: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,119}$")
    channel: Literal["ALL", "EMAIL", "SMS", "VOICE", "SOCIAL"]
    state: Literal["SUBSCRIBED", "UNSUBSCRIBED"]
    frequency: str | None = Field(default=None, max_length=40)
    source: str = Field(min_length=2, max_length=200)
    expected_version: int | None = Field(default=None, ge=1)


class PreferenceOut(StrictModel):
    id: str
    tenant_id: str
    profile_id: str | None
    identity_reference: str
    topic: str
    channel: str
    state: str
    frequency: str | None
    source: str
    version: int
    updated_at: datetime


class EffectivePreferenceOut(StrictModel):
    tenant_id: str
    identity_reference: str
    topic: str
    channel: str
    allowed: bool
    reason: str
    consent_state: str | None
    preference_state: str | None


class BillingAccountCreate(StrictModel):
    legal_name: str = Field(min_length=1, max_length=200)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    billing_day: int = Field(ge=1, le=28)
    tax_profile: dict[str, Any] = Field(default_factory=dict)
    external_customer_ref: str | None = Field(default=None, max_length=180)


class BillingAccountOut(StrictModel):
    id: str
    owner_tenant_id: str
    legal_name: str
    currency: str
    billing_day: int
    status: str
    tax_profile: dict[str, Any]
    external_customer_ref: str | None
    created_at: datetime


class SubscriptionCreate(StrictModel):
    account_id: str = Field(min_length=36, max_length=36)
    suite_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,79}$")
    plan_code: str = Field(pattern=r"^[A-Z][A-Z0-9_.-]{1,79}$")
    status: Literal["TRIALING", "ACTIVE", "PAST_DUE", "SUSPENDED", "CANCELLED"] = "ACTIVE"
    quota_behavior: Literal["OVERAGE", "BLOCK", "AUTO_UPGRADE"] = "OVERAGE"
    period_start: datetime
    period_end: datetime
    trial_end: datetime | None = None

    @model_validator(mode="after")
    def valid_period(self):
        if self.period_end <= self.period_start:
            raise ValueError("period_end_must_follow_period_start")
        if self.status == "TRIALING" and self.trial_end is None:
            raise ValueError("trial_end_required")
        return self


class SubscriptionOut(StrictModel):
    id: str
    account_id: str
    tenant_id: str
    suite_code: str
    plan_code: str
    status: str
    quota_behavior: str
    period_start: datetime
    period_end: datetime
    trial_end: datetime | None
    created_at: datetime


class UsageCreate(StrictModel):
    account_id: str = Field(min_length=36, max_length=36)
    subscription_id: str = Field(min_length=36, max_length=36)
    suite_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,79}$")
    meter_code: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,119}$")
    quantity: Decimal = Field(gt=0, max_digits=24, decimal_places=6)
    event_key: str = Field(min_length=8, max_length=240)
    source_object_id: str = Field(min_length=1, max_length=180)
    occurred_at: datetime


class UsageOut(StrictModel):
    id: str
    account_id: str
    subscription_id: str
    tenant_id: str
    suite_code: str
    meter_code: str
    quantity: Decimal
    event_key: str
    source_object_id: str
    occurred_at: datetime
    created_at: datetime


class InvoiceLineIn(StrictModel):
    line_code: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(ge=0, max_digits=24, decimal_places=6)
    unit_amount: Decimal = Field(ge=0, max_digits=18, decimal_places=6)


class InvoiceCreate(StrictModel):
    account_id: str = Field(min_length=36, max_length=36)
    period_start: datetime
    period_end: datetime
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    lines: list[InvoiceLineIn] = Field(min_length=1, max_length=500)
    discount_amount: Decimal = Field(default=Decimal("0.00"), ge=0, decimal_places=2)
    credit_amount: Decimal = Field(default=Decimal("0.00"), ge=0, decimal_places=2)
    tax_amount: Decimal = Field(default=Decimal("0.00"), ge=0, decimal_places=2)

    @model_validator(mode="after")
    def valid_period(self):
        if self.period_end <= self.period_start:
            raise ValueError("period_end_must_follow_period_start")
        return self


class InvoiceLineOut(StrictModel):
    id: str
    line_code: str
    description: str
    quantity: Decimal
    unit_amount: Decimal
    line_amount: Decimal


class InvoiceOut(StrictModel):
    id: str
    account_id: str
    period_start: datetime
    period_end: datetime
    currency: str
    status: str
    subtotal_amount: Decimal
    discount_amount: Decimal
    credit_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    lines: list[InvoiceLineOut]
    created_at: datetime


class AuditOut(StrictModel):
    id: str
    tenant_id: str
    actor_subject: str
    action: str
    resource_type: str
    resource_id: str
    correlation_id: str
    detail_hash: str
    created_at: datetime
