# Foundation API catalogue

This file is generated from `contracts/foundation-routes.v1.json`; do not edit it by hand.

## Base URLs

- Local: `http://127.0.0.1:18100`
- Container network: `http://foundation-api:18100`
- Production: **NOT_ASSIGNED_OR_DEPLOYED**

No production DNS name or deployment is claimed by this repository.

## Protocol rules

- Authentication: Bearer JWT with exact issuer, audience and algorithm validation unless scope is PUBLIC
- Effectful headers: `Authorization, Idempotency-Key, X-Correlation-ID`
- Deletion: No hard-delete API exists for authoritative records; lifecycle state preserves history

## Routes

| Method | URL | Required scope | Effect | Logic |
|---|---|---|---|---|
| GET | `/healthz` | `PUBLIC` | READ | Liveness only; does not claim dependency readiness. |
| GET | `/readyz` | `PUBLIC` | READ | Checks database, JWT, field encryption and enabled outbox configuration. |
| GET | `/version` | `PUBLIC` | READ | Returns the service name and API version. |
| POST | `/v1/tenants` | `foundation.admin` | MUTATION | Creates a unique tenant and atomically writes audit, outbox and encrypted idempotency records. |
| GET | `/v1/tenants` | `foundation.admin` | READ | Lists tenants with status filtering and bounded pagination. |
| GET | `/v1/tenants/{tenant_id}` | `foundation.tenant.read` | READ | Reads one tenant within the caller tenant boundary. |
| PATCH | `/v1/tenants/{tenant_id}` | `foundation.admin` | MUTATION | Changes tenant name or lifecycle status with audit and event evidence. |
| PUT | `/v1/tenants/{tenant_id}/entitlements/{entitlement_key}` | `foundation.entitlement.write` | MUTATION | Versioned entitlement upsert with effective and expiry validation. |
| GET | `/v1/tenants/{tenant_id}/entitlements/{entitlement_key}` | `foundation.entitlement.read` | READ | Reads one entitlement by canonical key. |
| GET | `/v1/tenants/{tenant_id}/entitlements` | `foundation.entitlement.read` | READ | Lists tenant entitlements with optional enabled filtering. |
| GET | `/v1/tenants/{tenant_id}/access/{suite_code}` | `foundation.entitlement.read` | READ | Combines tenant, billing-account, subscription and optional time-bounded entitlement state into a deny-by-default access decision. |
| GET | `/v1/tenants/{tenant_id}/audit` | `foundation.audit.read` | READ | Returns the latest 200 append-only, PII-free audit records. |
| POST | `/v1/tenants/{tenant_id}/profiles` | `foundation.profile.write` | MUTATION | Creates an encrypted profile with one or more tenant-unique normalized identities. |
| GET | `/v1/tenants/{tenant_id}/profiles` | `foundation.profile.read` | READ | Lists profiles with bounded pagination and optional external-reference and merged-record filters. |
| GET | `/v1/tenants/{tenant_id}/profiles/{profile_id}` | `foundation.profile.read` | READ | Reads and decrypts one profile for an authorized tenant caller. |
| PATCH | `/v1/tenants/{tenant_id}/profiles/{profile_id}` | `foundation.profile.write` | MUTATION | Optimistically updates encrypted profile fields; merged profiles are read-only. |
| POST | `/v1/tenants/{tenant_id}/profiles/resolve` | `foundation.profile.read` | READ | Resolves active normalized identities to exactly one canonical profile and follows a merge pointer. |
| POST | `/v1/tenants/{tenant_id}/profiles/{profile_id}/identities` | `foundation.profile.write` | MUTATION | Adds an encrypted tenant-unique identity and enforces one primary identity per kind. |
| PATCH | `/v1/tenants/{tenant_id}/profiles/{profile_id}/identities/{identity_id}` | `foundation.profile.write` | MUTATION | Versioned verification, primary-selection or revocation; revoked identities cannot resolve or become primary. |
| POST | `/v1/tenants/{tenant_id}/profiles/{profile_id}/merge` | `foundation.profile.write` | MUTATION | Moves identity and preference links once, marks the source read-only and records immutable merge evidence. |
| GET | `/v1/tenants/{tenant_id}/profiles/{profile_id}/merges` | `foundation.profile.read` | READ | Lists immutable merge history where the profile was source or target. |
| POST | `/v1/tenants/{tenant_id}/communications/consents` | `foundation.consent.write` | MUTATION | Appends immutable consent evidence; denial or revocation forces an unsubscribe without downgrading history. |
| GET | `/v1/tenants/{tenant_id}/communications/consents` | `foundation.consent.read` | READ | Lists consent history for a normalized identity without exposing identity values in storage. |
| GET | `/v1/tenants/{tenant_id}/communications/consents/{consent_id}` | `foundation.consent.read` | READ | Reads one immutable consent event inside the tenant boundary. |
| PUT | `/v1/tenants/{tenant_id}/communications/preferences` | `foundation.preference.write` | MUTATION | Versioned preference upsert; subscription requires an effective granted consent. |
| GET | `/v1/tenants/{tenant_id}/communications/preferences` | `foundation.preference.read` | READ | Lists preferences for a normalized identity with optional topic and channel filters. |
| GET | `/v1/tenants/{tenant_id}/communications/preferences/{preference_id}` | `foundation.preference.read` | READ | Reads one current preference inside the tenant boundary. |
| GET | `/v1/tenants/{tenant_id}/communications/effective` | `foundation.preference.read` | READ | Deny-by-default effective communication decision combining consent and preference precedence. |
| POST | `/v1/tenants/{tenant_id}/billing/accounts` | `foundation.billing.write` | MUTATION | Creates a billing account with encrypted tax profile and immutable currency. |
| GET | `/v1/tenants/{tenant_id}/billing/accounts` | `foundation.billing.read` | READ | Lists tenant-owned billing accounts with status filtering. |
| GET | `/v1/tenants/{tenant_id}/billing/accounts/{account_id}` | `foundation.billing.read` | READ | Reads one billing account after explicit account ownership enforcement. |
| PATCH | `/v1/tenants/{tenant_id}/billing/accounts/{account_id}` | `foundation.billing.write` | MUTATION | Versioned account update with ACTIVE, PAST_DUE and terminal CLOSED transition rules. |
| POST | `/v1/tenants/{tenant_id}/billing/subscriptions` | `foundation.billing.write` | MUTATION | Creates one suite subscription per tenant and validates billing periods and trials. |
| GET | `/v1/tenants/{tenant_id}/billing/subscriptions` | `foundation.billing.read` | READ | Lists all suite subscriptions for the tenant. |
| GET | `/v1/tenants/{tenant_id}/billing/subscriptions/{subscription_id}` | `foundation.billing.read` | READ | Reads one tenant suite subscription. |
| PATCH | `/v1/tenants/{tenant_id}/billing/subscriptions/{subscription_id}` | `foundation.billing.write` | MUTATION | Versioned plan, quota, period and lifecycle update with a terminal cancellation state. |
| POST | `/v1/tenants/{tenant_id}/billing/meters` | `foundation.billing.write` | MUTATION | Registers a unique suite meter and its SUM, MAX or PASS_THROUGH aggregation contract. |
| GET | `/v1/tenants/{tenant_id}/billing/meters` | `foundation.billing.read` | READ | Lists registered meter definitions with suite and enabled filters. |
| GET | `/v1/tenants/{tenant_id}/billing/meters/{meter_id}` | `foundation.billing.read` | READ | Reads one tenant meter definition. |
| PATCH | `/v1/tenants/{tenant_id}/billing/meters/{meter_id}` | `foundation.billing.write` | MUTATION | Versioned meter update or disablement; historical usage is never changed. |
| POST | `/v1/tenants/{tenant_id}/billing/usage` | `foundation.usage.write` | MUTATION | Records append-only usage exactly once after account, subscription, suite and enabled-meter validation. |
| GET | `/v1/tenants/{tenant_id}/billing/usage` | `foundation.billing.read` | READ | Lists bounded raw usage with suite, meter and half-open period filters. |
| GET | `/v1/tenants/{tenant_id}/billing/usage/summary` | `foundation.billing.read` | READ | Aggregates a half-open period using each meter's declared SUM, MAX or PASS_THROUGH rule. |
| POST | `/v1/tenants/{tenant_id}/billing/invoices` | `foundation.billing.write` | MUTATION | Creates one invoice per account period with line-level decimal rounding and recomputable totals. |
| GET | `/v1/tenants/{tenant_id}/billing/invoices` | `foundation.billing.read` | READ | Lists tenant-owned invoices with account and status filters. |
| GET | `/v1/tenants/{tenant_id}/billing/invoices/{invoice_id}` | `foundation.billing.read` | READ | Reads an invoice and all immutable calculated lines after account ownership validation. |
| PATCH | `/v1/tenants/{tenant_id}/billing/invoices/{invoice_id}/status` | `foundation.billing.write` | MUTATION | Versioned legal invoice transition; paid may become refunded, terminal refunded and void states cannot reopen. |

## State-preserving removals

There are deliberately no `DELETE` routes. Identities are revoked, preferences are unsubscribed, entitlements and meters are disabled, subscriptions are cancelled, accounts are closed, and invoices are voided or refunded. Consent, usage, audit, invoice lines, and merge evidence remain append-only.
