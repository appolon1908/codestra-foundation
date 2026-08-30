# Foundation Security

## Authentication and authorization

- Bearer JWTs are verified against an exact issuer, audience, algorithm,
  signature, expiry, and required claims.
- Tenant users must carry a matching `tenant_id` claim.
- Scopes are resource-specific; Middleware authorization never replaces
  Foundation business authorization.
- Cross-tenant access requires the explicit `foundation.admin` scope.

## Data protection

- Profile names/attributes, identity values, tax profiles, and idempotent
  responses use AES-256-GCM.
- Identity lookup uses an independent HMAC-SHA256 key.
- Encryption and hashing keys must be independent and supplied through secret
  files.
- Audit/outbox payloads use opaque IDs or HMAC references, not raw recipient
  addresses.
- No card number, CVV, private key, provider token, or OAuth client secret is
  accepted by the API.

## Network boundary

- The committed Compose file binds the API to `127.0.0.1:18100` only.
- PostgreSQL is on an internal Docker network with no host port.
- Middleware event delivery requires HTTPS, an OAuth bearer credential, a
  trusted CA, and a client certificate/key.
- Redirects are disabled on Middleware delivery.

## Fail-closed controls

`FOUNDATION_MUTATIONS_ENABLED` and `FOUNDATION_OUTBOX_ENABLED` are independent
and both default to `false`. Readiness fails when a requested security transport
is not fully configured.
