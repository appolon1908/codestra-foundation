# Foundation Architecture

## Authority

```text
Browser / product / SDK
          |
          v
     Caddy -> Kong -> Keycloak
                        |
                        v
                   Middleware
                        |
                        v
              Codestra Foundation API
                        |
                        v
                    PostgreSQL
```

Foundation owns shared customer and commercial state. It does not submit email,
SMS, calls, social posts, workflow jobs, CRM mutations, or payment-provider
charges. Effectful cross-system calls continue through Middleware.

Channel runtimes use Foundation through tenant-scoped contracts:

```text
Klyrow  ---- profile / consent / preference / entitlement / usage ----┐
Telnexa ---------------------------------------------------------------├-> Middleware -> Foundation
VICIdial --------------------------------------------------------------┘
```

## Transaction boundary

Every state-changing request performs the business write, idempotency record,
audit record, and event outbox write in one database transaction. A failure in
any part rolls back the whole mutation.

The outbox worker uses the outbox UUID as the event ID and derives a stable
`Idempotency-Key`. A timeout retries the same event identity, never a newly
generated event. Middleware is expected to deduplicate that stable identity.

## Identity

Raw identity values are normalized, encrypted with AES-256-GCM, and indexed by
HMAC-SHA256. Plain email addresses and phone numbers are not present in lookup
indexes, audit events, outbox events, or idempotency records.

Resolution rules are deterministic:

1. Normalize and HMAC every supplied identity.
2. No match returns `404`.
3. Matches pointing to one profile resolve to that profile.
4. Matches pointing to different profiles return `409`; the service never
   guesses.
5. Explicit merges are immutable audit events and move active identity and
   preference links to the selected target.

## Consent and preference policy

Consent is an append-only event stream. `DENIED` and `REVOKED` automatically
produce an effective unsubscribe in the same transaction. A subscription
preference requires a currently effective `GRANTED` consent. No consent or a
DMARC-independent channel revocation fails closed.

The effective policy endpoint evaluates channel-specific and `ALL` records. An
unsubscribe at either scope blocks delivery. Revocations are read from the
authoritative database and are never served from a stale application cache.

## Billing

A billing account represents the legal payer and can fund multiple tenant suite
subscriptions under administrative control. A tenant may have one subscription
per `suite_code`. Usage is append-only and unique on `(tenant_id, event_key)`.
Invoices are unique on `(account_id, period_end)` and money is represented as
fixed-precision decimal values. Rounding occurs once per invoice line.
