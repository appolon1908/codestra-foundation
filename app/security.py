from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException

from app.config import get_settings


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant_id: str | None
    scopes: frozenset[str]

    def has(self, scope: str) -> bool:
        return "foundation.admin" in self.scopes or scope in self.scopes


def current_principal(authorization: str | None = Header(default=None)) -> Principal:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="bearer_token_required")
    settings = get_settings()
    if not settings.auth_ready:
        raise HTTPException(status_code=503, detail="jwt_verification_not_configured")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_public_key,
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid_bearer_token") from exc
    raw_scopes = claims.get("scope", "")
    scopes = frozenset(raw_scopes.split() if isinstance(raw_scopes, str) else raw_scopes or [])
    tenant_id = claims.get("tenant_id")
    return Principal(subject=str(claims["sub"]), tenant_id=str(tenant_id) if tenant_id else None, scopes=scopes)


def authorize(principal: Principal, tenant_id: str, scope: str) -> None:
    if not principal.has(scope):
        raise HTTPException(status_code=403, detail="insufficient_scope")
    if "foundation.admin" not in principal.scopes and principal.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="tenant_boundary_violation")


def require_admin(principal: Principal) -> None:
    if "foundation.admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail="foundation_admin_required")
