# Codestra Foundation Suite

Codestra Foundation is the shared authority for tenant state, customer profiles,
deterministic identity resolution, consent, communication preferences, billing
accounts, suite subscriptions, invoices, usage events, and entitlements.

It is deliberately channel-neutral. Klyrow remains the email runtime, Telnexa
remains the SMS runtime, and VICIdial/Asterisk remains the voice runtime.
Middleware remains the only privileged cross-system command boundary.

## Current implementation

- Tenant lifecycle and tenant-scoped authorization
- Encrypted profiles, attributes, identity values, billing tax profiles, and
  idempotent response bodies
- HMAC-indexed email, E.164 phone, and external identities
- Deterministic identity resolution and audited profile merges
- Append-only consent history and effective communication policy evaluation
- Versioned communication preferences with fail-closed subscription rules
- Billing accounts, multi-suite subscriptions, exact-once usage events, and
  line-rounded invoices
- Versioned entitlements and limits
- JWT issuer/audience/scope validation and strict tenant isolation
- Required idempotency and correlation headers for every mutation
- Immutable audit records and transactional Middleware outbox events
- OAuth bearer + mTLS Middleware dispatcher, disabled by default
- PostgreSQL migration with database-enforced append-only consent, usage, and
  audit tables

The service is source-complete for this first vertical slice but is **not
deployed**. Both mutations and outbound Middleware delivery default to disabled.
Existing Klyrow records remain authoritative until the migration and cutover
gates are explicitly approved.

## API

The generated contract is [contracts/openapi.json](contracts/openapi.json).
Primary resources:

```text
POST/PATCH /v1/tenants...
GET/POST   /v1/tenants/{tenant_id}/profiles...
POST       /v1/tenants/{tenant_id}/profiles/resolve
POST/GET   /v1/tenants/{tenant_id}/communications/consents
PUT        /v1/tenants/{tenant_id}/communications/preferences
GET        /v1/tenants/{tenant_id}/communications/effective
PUT/GET    /v1/tenants/{tenant_id}/entitlements/{key}
POST/GET   /v1/tenants/{tenant_id}/billing/accounts...
POST/GET   /v1/tenants/{tenant_id}/billing/subscriptions
POST/GET   /v1/tenants/{tenant_id}/billing/usage
POST/GET   /v1/tenants/{tenant_id}/billing/invoices...
GET        /v1/tenants/{tenant_id}/audit
```

All mutations require `Authorization`, `Idempotency-Key`, and
`X-Correlation-ID`. A valid token does not bypass tenant or capability checks.

## Local validation

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check app tests alembic scripts
```

PostgreSQL is the production database. SQLite exists only for fast local tests.
Apply or validate the schema with:

```bash
alembic upgrade head
alembic check
```

## Safety

- `FOUNDATION_MUTATIONS_ENABLED=false` by default
- `FOUNDATION_OUTBOX_ENABLED=false` by default
- No payment-card data is accepted or stored
- No browser, Klyrow, Telnexa, n8n, Odoo, or provider direct-write bypass
- Secret values are read from external files and never committed
- Deployment is a separate owner-approved action

See [architecture](docs/ARCHITECTURE.md), [data model](docs/DATA_MODEL.md),
[security](docs/SECURITY.md), [Klyrow migration](docs/KLYROW_MIGRATION.md), and
[production gates](docs/PRODUCTION_GATES.md).
