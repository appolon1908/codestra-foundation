# Foundation Data Model

| Table | Authority | Key invariant |
|---|---|---|
| `tenants` | Tenant lifecycle | Stable UUID and unique slug |
| `entitlements` | Feature/limit grants | Unique tenant + key; optimistic version |
| `profiles` | Customer record | Tenant isolation; encrypted attributes |
| `identities` | Email/phone/external identity | Unique tenant + kind + HMAC; encrypted value; versioned ACTIVE/REVOKED lifecycle |
| `profile_merges` | Identity-resolution decisions | One immutable merge per source profile |
| `consent_events` | Consent evidence | Append-only; never overwritten |
| `preferences` | Effective communication choices | Unique identity + topic + channel; versioned |
| `billing_accounts` | Legal payer | Immutable currency; billing day capped at 28; versioned lifecycle |
| `suite_subscriptions` | Commercial suite access | Unique tenant + suite; versioned transition graph |
| `usage_meters` | Usage aggregation contract | Unique tenant + suite + meter; SUM/MAX/PASS_THROUGH |
| `usage_events` | Billable usage | Append-only; deterministic unique event key |
| `invoices` | Period invoice | Unique account + period end; versioned legal status transitions |
| `invoice_lines` | Recomputable invoice detail | Fixed decimal; line-level rounding |
| `idempotency_records` | Mutation replay | Request hash must match; encrypted response |
| `audit_events` | Security/business audit | Append-only; no raw PII |
| `outbox_events` | Middleware event delivery | Same transaction as source mutation |

PostgreSQL triggers reject `UPDATE` and `DELETE` on `consent_events`,
`usage_events`, and `audit_events`. Application-level listeners preserve the
same invariant in local tests.

Migrations are the initial authority schema
`alembic/versions/8d1fc3598816_create_foundation_authority_schema.py` and the
v1 lifecycle extension
`alembic/versions/c24c16d8047a_complete_foundation_v1_lifecycles.py`.
