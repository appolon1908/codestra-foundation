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
- Versioned profile updates plus identity add, verification, primary-selection,
  revocation, and merge-history readback
- Append-only consent history and effective communication policy evaluation
- Versioned communication preferences with fail-closed subscription rules
- Versioned billing accounts and multi-suite subscriptions with enforced state
  machines
- Registered SUM/MAX/PASS_THROUGH usage meters, exact-once usage events,
  period summaries, line-rounded invoices, and invoice status transitions
- Versioned entitlements and limits
- Deny-by-default suite access decisions combining tenant, billing-account,
  subscription, and optional entitlement state
- JWT issuer/audience/scope validation and strict tenant isolation
- Required idempotency and correlation headers for every mutation
- Immutable audit records and transactional Middleware outbox events
- OAuth bearer + mTLS Middleware dispatcher, disabled by default
- PostgreSQL migration with database-enforced append-only consent, usage, and
  audit tables

The v1 authority API is source-complete but is **not
deployed**. Both mutations and outbound Middleware delivery default to disabled.
Existing Klyrow records remain authoritative until the migration and cutover
gates are explicitly approved.

## API

The generated OpenAPI contract is [contracts/openapi.json](contracts/openapi.json).
Every method, path, required scope, effect classification, and business rule is
listed in the generated [API catalogue](docs/API_CATALOGUE.md). Its source is
[contracts/foundation-routes.v1.json](contracts/foundation-routes.v1.json), and
CI proves that the route and scope sets exactly match the application source.

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
