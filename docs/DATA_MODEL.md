# Foundation Data Model

| Table | Authority | Key invariant |
|---|---|---|
| `tenants` | Tenant lifecycle | Stable UUID and unique slug |
| `entitlements` | Feature/limit grants | Unique tenant + key; optimistic version |
| `profiles` | Customer record | Tenant isolation; encrypted attributes |
| `identities` | Email/phone/external identity | Unique tenant + kind + HMAC; encrypted value |
| `profile_merges` | Identity-resolution decisions | One immutable merge per source profile |
| `consent_events` | Consent evidence | Append-only; never overwritten |
| `preferences` | Effective communication choices | Unique identity + topic + channel; versioned |
| `billing_accounts` | Legal payer | Currency and billing day capped at 28 |
| `suite_subscriptions` | Commercial suite access | Unique tenant + suite |
| `usage_events` | Billable usage | Append-only; deterministic unique event key |
| `invoices` | Period invoice | Unique account + period end |
| `invoice_lines` | Recomputable invoice detail | Fixed decimal; line-level rounding |
| `idempotency_records` | Mutation replay | Request hash must match; encrypted response |
| `audit_events` | Security/business audit | Append-only; no raw PII |
| `outbox_events` | Middleware event delivery | Same transaction as source mutation |

PostgreSQL triggers reject `UPDATE` and `DELETE` on `consent_events`,
`usage_events`, and `audit_events`. Application-level listeners preserve the
same invariant in local tests.

The initial migration is
`alembic/versions/8d1fc3598816_create_foundation_authority_schema.py`.
