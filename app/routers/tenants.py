from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import (
    commit_idempotent,
    ensure_mutations_enabled,
    idempotency_replay,
    record_change,
    require_correlation_id,
    require_idempotency_key,
    tenant_or_404,
)
from app.database import db_session
from app.models import AuditEvent, BillingAccount, Entitlement, SuiteSubscription, Tenant
from app.schemas import (
    AccessDecisionOut,
    AuditOut,
    EntitlementOut,
    EntitlementUpsert,
    TenantCreate,
    TenantOut,
    TenantPatch,
)
from app.security import Principal, authorize, current_principal, require_admin

router = APIRouter(prefix="/v1", tags=["foundation"])


def tenant_out(item: Tenant) -> TenantOut:
    return TenantOut(
        id=item.id,
        slug=item.slug,
        name=item.name,
        status=item.status,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def entitlement_out(item: Entitlement) -> EntitlementOut:
    return EntitlementOut(
        id=item.id,
        tenant_id=item.tenant_id,
        entitlement_key=item.entitlement_key,
        enabled=item.enabled,
        limit_value=item.limit_value,
        unit=item.unit,
        effective_at=item.effective_at,
        expires_at=item.expires_at,
        version=item.version,
        updated_at=item.updated_at,
    )


@router.post("/tenants", response_model=TenantOut, status_code=201)
def create_tenant(
    payload: TenantCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=240),
    correlation_id: str = Header(alias="X-Correlation-ID", min_length=8, max_length=180),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    ensure_mutations_enabled()
    require_admin(principal)
    key = require_idempotency_key(idempotency_key)
    correlation = require_correlation_id(correlation_id)
    request = payload.model_dump(mode="json")
    digest, replay = idempotency_replay(
        session,
        tenant_id="__platform__",
        operation="tenant.create",
        idempotency_key=key,
        request_payload=request,
    )
    if replay:
        return replay
    if session.scalar(select(Tenant).where(Tenant.slug == payload.slug)):
        raise HTTPException(status_code=409, detail="tenant_slug_exists")
    tenant = Tenant(slug=payload.slug, name=payload.name)
    session.add(tenant)
    session.flush()
    response = tenant_out(tenant).model_dump(mode="json")
    record_change(
        session,
        tenant_id=tenant.id,
        principal=principal,
        action="tenant.create",
        resource_type="tenant",
        resource_id=tenant.id,
        correlation_id=correlation,
        detail=request,
        event_type="foundation.tenant.created.v1",
        event_payload={"tenant_id": tenant.id, "status": tenant.status},
    )
    return commit_idempotent(
        session,
        tenant_id="__platform__",
        operation="tenant.create",
        idempotency_key=key,
        request_hash=digest,
        status_code=201,
        response=response,
    )


@router.get("/tenants", response_model=list[TenantOut])
def list_tenants(
    status: str | None = Query(default=None, pattern="^(ACTIVE|SUSPENDED|CLOSED)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    require_admin(principal)
    statement = select(Tenant)
    if status:
        statement = statement.where(Tenant.status == status)
    rows = session.scalars(statement.order_by(Tenant.slug).offset(offset).limit(limit)).all()
    return [tenant_out(row) for row in rows]


@router.get("/tenants/{tenant_id}", response_model=TenantOut)
def get_tenant(
    tenant_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    authorize(principal, tenant_id, "foundation.tenant.read")
    return tenant_out(tenant_or_404(session, tenant_id))


@router.patch("/tenants/{tenant_id}", response_model=TenantOut)
def patch_tenant(
    tenant_id: str,
    payload: TenantPatch,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=240),
    correlation_id: str = Header(alias="X-Correlation-ID", min_length=8, max_length=180),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    ensure_mutations_enabled()
    require_admin(principal)
    key = require_idempotency_key(idempotency_key)
    correlation = require_correlation_id(correlation_id)
    request = payload.model_dump(mode="json", exclude_none=True)
    digest, replay = idempotency_replay(
        session,
        tenant_id=tenant_id,
        operation="tenant.patch",
        idempotency_key=key,
        request_payload=request,
    )
    if replay:
        return replay
    tenant = tenant_or_404(session, tenant_id)
    for field, value in request.items():
        setattr(tenant, field, value)
    tenant.updated_at = datetime.now(timezone.utc)
    session.flush()
    response = tenant_out(tenant).model_dump(mode="json")
    record_change(
        session,
        tenant_id=tenant.id,
        principal=principal,
        action="tenant.patch",
        resource_type="tenant",
        resource_id=tenant.id,
        correlation_id=correlation,
        detail=request,
        event_type="foundation.tenant.changed.v1",
        event_payload={"tenant_id": tenant.id, "status": tenant.status},
    )
    return commit_idempotent(
        session,
        tenant_id=tenant_id,
        operation="tenant.patch",
        idempotency_key=key,
        request_hash=digest,
        status_code=200,
        response=response,
    )


@router.put(
    "/tenants/{tenant_id}/entitlements/{entitlement_key}",
    response_model=EntitlementOut,
)
def upsert_entitlement(
    tenant_id: str,
    payload: EntitlementUpsert,
    entitlement_key: str = Path(pattern=r"^[a-z][a-z0-9._-]{1,119}$"),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=240),
    correlation_id: str = Header(alias="X-Correlation-ID", min_length=8, max_length=180),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    ensure_mutations_enabled()
    authorize(principal, tenant_id, "foundation.entitlement.write")
    key = require_idempotency_key(idempotency_key)
    correlation = require_correlation_id(correlation_id)
    request = payload.model_dump(mode="json")
    digest, replay = idempotency_replay(
        session,
        tenant_id=tenant_id,
        operation=f"entitlement.upsert:{entitlement_key}",
        idempotency_key=key,
        request_payload=request,
    )
    if replay:
        return replay
    tenant_or_404(session, tenant_id)
    item = session.scalar(
        select(Entitlement).where(
            Entitlement.tenant_id == tenant_id,
            Entitlement.entitlement_key == entitlement_key,
        )
    )
    effective_at = payload.effective_at or datetime.now(timezone.utc)
    if payload.expires_at is not None and payload.expires_at <= effective_at:
        raise HTTPException(status_code=422, detail="expiry_must_follow_effective_time")
    if item is None:
        if payload.expected_version not in (None, 1):
            raise HTTPException(status_code=409, detail="entitlement_version_conflict")
        item = Entitlement(tenant_id=tenant_id, entitlement_key=entitlement_key)
        session.add(item)
    elif payload.expected_version is not None and item.version != payload.expected_version:
        raise HTTPException(status_code=409, detail="entitlement_version_conflict")
    elif item.id:
        item.version += 1
    item.enabled = payload.enabled
    item.limit_value = payload.limit_value
    item.unit = payload.unit
    item.effective_at = effective_at
    item.expires_at = payload.expires_at
    session.flush()
    response = entitlement_out(item).model_dump(mode="json")
    record_change(
        session,
        tenant_id=tenant_id,
        principal=principal,
        action="entitlement.upsert",
        resource_type="entitlement",
        resource_id=item.id,
        correlation_id=correlation,
        detail=request,
        event_type="foundation.entitlement.changed.v1",
        event_payload={
            "tenant_id": tenant_id,
            "entitlement_key": entitlement_key,
            "enabled": item.enabled,
            "version": item.version,
        },
    )
    return commit_idempotent(
        session,
        tenant_id=tenant_id,
        operation=f"entitlement.upsert:{entitlement_key}",
        idempotency_key=key,
        request_hash=digest,
        status_code=200,
        response=response,
    )


@router.get(
    "/tenants/{tenant_id}/entitlements/{entitlement_key}",
    response_model=EntitlementOut,
)
def get_entitlement(
    tenant_id: str,
    entitlement_key: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    authorize(principal, tenant_id, "foundation.entitlement.read")
    item = session.scalar(
        select(Entitlement).where(
            Entitlement.tenant_id == tenant_id,
            Entitlement.entitlement_key == entitlement_key,
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="entitlement_not_found")
    return entitlement_out(item)


@router.get("/tenants/{tenant_id}/entitlements", response_model=list[EntitlementOut])
def list_entitlements(
    tenant_id: str,
    enabled: bool | None = Query(default=None),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    authorize(principal, tenant_id, "foundation.entitlement.read")
    tenant_or_404(session, tenant_id)
    statement = select(Entitlement).where(Entitlement.tenant_id == tenant_id)
    if enabled is not None:
        statement = statement.where(Entitlement.enabled == enabled)
    rows = session.scalars(statement.order_by(Entitlement.entitlement_key)).all()
    return [entitlement_out(row) for row in rows]


@router.get("/tenants/{tenant_id}/access/{suite_code}", response_model=AccessDecisionOut)
def evaluate_access(
    tenant_id: str,
    suite_code: str = Path(pattern=r"^[A-Z][A-Z0-9_]{1,79}$"),
    entitlement_key: str | None = Query(default=None, pattern=r"^[a-z][a-z0-9._-]{1,119}$"),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    authorize(principal, tenant_id, "foundation.entitlement.read")
    tenant = tenant_or_404(session, tenant_id)
    subscription = session.scalar(
        select(SuiteSubscription).where(
            SuiteSubscription.tenant_id == tenant_id,
            SuiteSubscription.suite_code == suite_code,
        )
    )
    account = session.get(BillingAccount, subscription.account_id) if subscription else None
    entitlement = None
    if entitlement_key:
        entitlement = session.scalar(
            select(Entitlement).where(
                Entitlement.tenant_id == tenant_id,
                Entitlement.entitlement_key == entitlement_key,
            )
        )
    now = datetime.now(timezone.utc)
    entitlement_active = None
    if entitlement is not None:
        effective_at = entitlement.effective_at
        expires_at = entitlement.expires_at
        if effective_at.tzinfo is None:
            effective_at = effective_at.replace(tzinfo=timezone.utc)
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        entitlement_active = bool(
            entitlement.enabled
            and effective_at <= now
            and (expires_at is None or expires_at > now)
        )

    if tenant.status != "ACTIVE":
        allowed, reason = False, f"TENANT_{tenant.status}"
    elif subscription is None:
        allowed, reason = False, "SUBSCRIPTION_NOT_FOUND"
    elif account is None:
        allowed, reason = False, "BILLING_ACCOUNT_NOT_FOUND"
    elif account.status == "CLOSED":
        allowed, reason = False, "BILLING_ACCOUNT_CLOSED"
    elif subscription.status not in {"ACTIVE", "TRIALING"}:
        allowed, reason = False, f"SUBSCRIPTION_{subscription.status}"
    elif entitlement_key and entitlement is None:
        allowed, reason = False, "ENTITLEMENT_NOT_FOUND"
    elif entitlement_key and not entitlement_active:
        allowed, reason = False, "ENTITLEMENT_INACTIVE"
    else:
        allowed, reason = True, "ALLOWED"
    return AccessDecisionOut(
        tenant_id=tenant_id,
        suite_code=suite_code,
        entitlement_key=entitlement_key,
        allowed=allowed,
        reason=reason,
        tenant_status=tenant.status,
        billing_account_status=account.status if account else None,
        subscription_status=subscription.status if subscription else None,
        entitlement_enabled=entitlement_active,
    )


@router.get("/tenants/{tenant_id}/audit", response_model=list[AuditOut])
def list_audit_events(
    tenant_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    authorize(principal, tenant_id, "foundation.audit.read")
    rows = session.scalars(
        select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.created_at.desc()).limit(200)
    ).all()
    return [
        AuditOut(
            id=row.id,
            tenant_id=row.tenant_id,
            actor_subject=row.actor_subject,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            correlation_id=row.correlation_id,
            detail_hash=row.detail_hash,
            created_at=row.created_at,
        )
        for row in rows
    ]
