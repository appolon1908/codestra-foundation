# Klyrow to Foundation Migration

No live data was moved by this implementation. Klyrow remains authoritative
until the following gated migration is reviewed and executed.

## Required sequence

1. Inventory Klyrow `Tenant`, `Profile`, identity, `Consent`, `Preference`,
   billing account/subscription, invoice, and usage records by exact source SHA
   and database backup.
2. Define deterministic source keys and field mappings. Never infer a consent
   grant from engagement or message history.
3. Restore a production-shaped copy into an isolated migration environment.
4. Import tenants, profiles/identities, append-only consent history,
   preferences, billing, subscriptions, invoices, usage, and entitlements in
   that dependency order.
5. Reconcile counts, source-key uniqueness, latest effective consent,
   unsubscribe coverage, invoice totals, usage totals, and tenant isolation.
6. Change Klyrow to read Foundation through Middleware. Consent revocations use
   synchronous write-through invalidation; they are never cached.
7. Run a bounded dual-read comparison. Do not establish two writable consent
   authorities.
8. Freeze Klyrow shared-state writes, drain, reconcile again, and switch the
   authority flag.
9. Retain the Klyrow tables read-only for the approved evidence period.

## Rollback

Before the authority switch, rollback is source-only. After the switch,
rollback requires a reverse delta export from Foundation and an owner-approved
maintenance window. A database rollback must never discard consent revocations
or usage events accepted after cutover.
