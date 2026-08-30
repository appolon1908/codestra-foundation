# Foundation Production Gates

Source completion does not authorize deployment or data migration.

- [ ] Repository protection and required CI checks enabled
- [ ] PostgreSQL migration tested on a production-shaped disposable database
- [ ] Backup, restore, point-in-time recovery, and key-rotation rehearsed
- [ ] Keycloak clients/scopes and Kong route matrix reviewed
- [ ] Middleware OAuth2 + mTLS event delivery and replay verified live
- [ ] Tenant isolation and administrative access matrix independently reviewed
- [ ] DPIA/data-retention schedule and data-subject export/delete workflow approved
- [ ] Klyrow source mapping, count reconciliation, and effective-consent comparison green
- [ ] Telnexa and VICIdial integration contracts reviewed
- [ ] Billing/accounting owner approves invoice and usage definitions
- [ ] Alerting, SLOs, runbooks, and incident ownership staffed
- [ ] `FOUNDATION_MUTATIONS_ENABLED` enabled only in the approved environment
- [ ] `FOUNDATION_OUTBOX_ENABLED` enabled only after Middleware canary
- [ ] Rollback rehearsal completed

Until every relevant gate is evidenced, the service remains source-only and
disabled.
