from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api import (
    commit_idempotent,
    ensure_mutations_enabled,
    idempotency_replay,
    record_change,
    require_correlation_id,
    require_idempotency_key,
    tenant_or_404,
)
from app.crypto import decrypt_json, encrypt_json, payload_hash
from app.database import db_session
from app.models import BillingAccount, Invoice, InvoiceLine, SuiteSubscription, UsageEvent, new_id
from app.schemas import (
    BillingAccountCreate,
    BillingAccountOut,
    InvoiceCreate,
    InvoiceLineOut,
    InvoiceOut,
    SubscriptionCreate,
    SubscriptionOut,
    UsageCreate,
    UsageOut,
)
from app.security import Principal, authorize, current_principal

router = APIRouter(prefix="/v1/tenants/{tenant_id}/billing", tags=["billing-usage"])
CENT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _account(session: Session, account_id: str) -> BillingAccount:
    item = session.get(BillingAccount, account_id)
    if item is None:
        raise HTTPException(status_code=404, detail="billing_account_not_found")
    return item


def _account_access(account: BillingAccount, tenant_id: str, principal: Principal) -> None:
    if "foundation.admin" not in principal.scopes and account.owner_tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="billing_account_tenant_boundary_violation")


def account_out(item: BillingAccount) -> BillingAccountOut:
    return BillingAccountOut(
        id=item.id,
        owner_tenant_id=item.owner_tenant_id,
        legal_name=item.legal_name,
        currency=item.currency,
        billing_day=item.billing_day,
        status=item.status,
        tax_profile=decrypt_json(item.tax_profile_enc, aad=f"billing-tax:{item.owner_tenant_id}:{item.id}"),
        external_customer_ref=item.external_customer_ref,
        created_at=item.created_at,
    )


def subscription_out(item: SuiteSubscription) -> SubscriptionOut:
    return SubscriptionOut(
        id=item.id,
        account_id=item.account_id,
        tenant_id=item.tenant_id,
        suite_code=item.suite_code,
        plan_code=item.plan_code,
        status=item.status,
        quota_behavior=item.quota_behavior,
        period_start=item.period_start,
        period_end=item.period_end,
        trial_end=item.trial_end,
        created_at=item.created_at,
    )


def usage_out(item: UsageEvent) -> UsageOut:
    return UsageOut(
        id=item.id,
        account_id=item.account_id,
        subscription_id=item.subscription_id,
        tenant_id=item.tenant_id,
        suite_code=item.suite_code,
        meter_code=item.meter_code,
        quantity=item.quantity,
        event_key=item.event_key,
        source_object_id=item.source_object_id,
        occurred_at=item.occurred_at,
        created_at=item.created_at,
    )


def invoice_out(item: Invoice) -> InvoiceOut:
    return InvoiceOut(
        id=item.id,
        account_id=item.account_id,
        period_start=item.period_start,
        period_end=item.period_end,
        currency=item.currency,
        status=item.status,
        subtotal_amount=item.subtotal_amount,
        discount_amount=item.discount_amount,
        credit_amount=item.credit_amount,
        tax_amount=item.tax_amount,
        total_amount=item.total_amount,
        lines=[
            InvoiceLineOut(
                id=line.id,
                line_code=line.line_code,
                description=line.description,
                quantity=line.quantity,
                unit_amount=line.unit_amount,
                line_amount=line.line_amount,
            )
            for line in item.lines
        ],
        created_at=item.created_at,
    )


@router.post("/accounts", response_model=BillingAccountOut, status_code=201)
def create_account(
    tenant_id: str,
    payload: BillingAccountCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=240),
    correlation_id: str = Header(alias="X-Correlation-ID", min_length=8, max_length=180),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    ensure_mutations_enabled()
    authorize(principal, tenant_id, "foundation.billing.write")
    key = require_idempotency_key(idempotency_key)
    correlation = require_correlation_id(correlation_id)
    request = payload.model_dump(mode="json")
    request_digest, replay = idempotency_replay(
        session,
        tenant_id=tenant_id,
        operation="billing_account.create",
        idempotency_key=key,
        request_payload=request,
    )
    if replay:
        return replay
    tenant_or_404(session, tenant_id)
    account_id = new_id()
    item = BillingAccount(
        id=account_id,
        owner_tenant_id=tenant_id,
        legal_name=payload.legal_name,
        currency=payload.currency,
        billing_day=payload.billing_day,
        tax_profile_enc=encrypt_json(payload.tax_profile, aad=f"billing-tax:{tenant_id}:{account_id}"),
        external_customer_ref=payload.external_customer_ref,
    )
    session.add(item)
    session.flush()
    response = account_out(item).model_dump(mode="json")
    record_change(
        session,
        tenant_id=tenant_id,
        principal=principal,
        action="billing_account.create",
        resource_type="billing_account",
        resource_id=item.id,
        correlation_id=correlation,
        detail={"currency": item.currency, "billing_day": item.billing_day},
        event_type="foundation.billing.account-created.v1",
        event_payload={"tenant_id": tenant_id, "account_id": item.id, "currency": item.currency},
    )
    return commit_idempotent(
        session,
        tenant_id=tenant_id,
        operation="billing_account.create",
        idempotency_key=key,
        request_hash=request_digest,
        status_code=201,
        response=response,
    )


@router.get("/accounts/{account_id}", response_model=BillingAccountOut)
def get_account(
    tenant_id: str,
    account_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    authorize(principal, tenant_id, "foundation.billing.read")
    account = _account(session, account_id)
    _account_access(account, tenant_id, principal)
    return account_out(account)


@router.post("/subscriptions", response_model=SubscriptionOut, status_code=201)
def create_subscription(
    tenant_id: str,
    payload: SubscriptionCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=240),
    correlation_id: str = Header(alias="X-Correlation-ID", min_length=8, max_length=180),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    ensure_mutations_enabled()
    authorize(principal, tenant_id, "foundation.billing.write")
    key = require_idempotency_key(idempotency_key)
    correlation = require_correlation_id(correlation_id)
    request = payload.model_dump(mode="json")
    request_digest, replay = idempotency_replay(
        session,
        tenant_id=tenant_id,
        operation=f"subscription.create:{payload.suite_code}",
        idempotency_key=key,
        request_payload=request,
    )
    if replay:
        return replay
    tenant_or_404(session, tenant_id)
    account = _account(session, payload.account_id)
    _account_access(account, tenant_id, principal)
    if session.scalar(
        select(SuiteSubscription).where(
            SuiteSubscription.tenant_id == tenant_id,
            SuiteSubscription.suite_code == payload.suite_code,
        )
    ):
        raise HTTPException(status_code=409, detail="suite_subscription_exists")
    item = SuiteSubscription(
        account_id=payload.account_id,
        tenant_id=tenant_id,
        suite_code=payload.suite_code,
        plan_code=payload.plan_code,
        status=payload.status,
        quota_behavior=payload.quota_behavior,
        period_start=payload.period_start,
        period_end=payload.period_end,
        trial_end=payload.trial_end,
    )
    session.add(item)
    session.flush()
    response = subscription_out(item).model_dump(mode="json")
    record_change(
        session,
        tenant_id=tenant_id,
        principal=principal,
        action="subscription.create",
        resource_type="suite_subscription",
        resource_id=item.id,
        correlation_id=correlation,
        detail=request,
        event_type="foundation.subscription.created.v1",
        event_payload={
            "tenant_id": tenant_id,
            "subscription_id": item.id,
            "suite_code": item.suite_code,
            "status": item.status,
        },
    )
    return commit_idempotent(
        session,
        tenant_id=tenant_id,
        operation=f"subscription.create:{payload.suite_code}",
        idempotency_key=key,
        request_hash=request_digest,
        status_code=201,
        response=response,
    )


@router.get("/subscriptions", response_model=list[SubscriptionOut])
def list_subscriptions(
    tenant_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    authorize(principal, tenant_id, "foundation.billing.read")
    tenant_or_404(session, tenant_id)
    rows = session.scalars(
        select(SuiteSubscription).where(SuiteSubscription.tenant_id == tenant_id).order_by(SuiteSubscription.suite_code)
    ).all()
    return [subscription_out(row) for row in rows]


@router.post("/usage", response_model=UsageOut, status_code=201)
def record_usage(
    tenant_id: str,
    payload: UsageCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=240),
    correlation_id: str = Header(alias="X-Correlation-ID", min_length=8, max_length=180),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    ensure_mutations_enabled()
    authorize(principal, tenant_id, "foundation.usage.write")
    key = require_idempotency_key(idempotency_key)
    correlation = require_correlation_id(correlation_id)
    request = payload.model_dump(mode="json")
    request_digest, replay = idempotency_replay(
        session,
        tenant_id=tenant_id,
        operation=f"usage.record:{payload.event_key}",
        idempotency_key=key,
        request_payload=request,
    )
    if replay:
        return replay
    tenant_or_404(session, tenant_id)
    account = _account(session, payload.account_id)
    _account_access(account, tenant_id, principal)
    subscription = session.get(SuiteSubscription, payload.subscription_id)
    if (
        subscription is None
        or subscription.tenant_id != tenant_id
        or subscription.account_id != payload.account_id
        or subscription.suite_code != payload.suite_code
    ):
        raise HTTPException(status_code=422, detail="usage_subscription_mismatch")
    if payload.occurred_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="occurred_at_timezone_required")
    usage_digest = payload_hash(request)
    existing = session.scalar(
        select(UsageEvent).where(UsageEvent.tenant_id == tenant_id, UsageEvent.event_key == payload.event_key)
    )
    if existing:
        if existing.payload_hash != usage_digest:
            raise HTTPException(status_code=409, detail="usage_event_key_payload_mismatch")
        response = usage_out(existing).model_dump(mode="json")
        committed = commit_idempotent(
            session,
            tenant_id=tenant_id,
            operation=f"usage.record:{payload.event_key}",
            idempotency_key=key,
            request_hash=request_digest,
            status_code=200,
            response=response,
        )
        return JSONResponse(status_code=200, content=committed)
    item = UsageEvent(
        account_id=payload.account_id,
        subscription_id=payload.subscription_id,
        tenant_id=tenant_id,
        suite_code=payload.suite_code,
        meter_code=payload.meter_code,
        quantity=payload.quantity,
        event_key=payload.event_key,
        source_object_id=payload.source_object_id,
        payload_hash=usage_digest,
        occurred_at=payload.occurred_at,
    )
    session.add(item)
    session.flush()
    response = usage_out(item).model_dump(mode="json")
    record_change(
        session,
        tenant_id=tenant_id,
        principal=principal,
        action="usage.record",
        resource_type="usage_event",
        resource_id=item.id,
        correlation_id=correlation,
        detail={
            "event_key": item.event_key,
            "suite_code": item.suite_code,
            "meter_code": item.meter_code,
            "quantity": str(item.quantity),
        },
        event_type="foundation.usage.recorded.v1",
        event_payload={
            "tenant_id": tenant_id,
            "usage_event_id": item.id,
            "suite_code": item.suite_code,
            "meter_code": item.meter_code,
            "quantity": str(item.quantity),
            "event_key": item.event_key,
        },
    )
    return commit_idempotent(
        session,
        tenant_id=tenant_id,
        operation=f"usage.record:{payload.event_key}",
        idempotency_key=key,
        request_hash=request_digest,
        status_code=201,
        response=response,
    )


@router.get("/usage", response_model=list[UsageOut])
def list_usage(
    tenant_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    authorize(principal, tenant_id, "foundation.billing.read")
    rows = session.scalars(
        select(UsageEvent).where(UsageEvent.tenant_id == tenant_id).order_by(UsageEvent.occurred_at.desc()).limit(500)
    ).all()
    return [usage_out(row) for row in rows]


@router.post("/invoices", response_model=InvoiceOut, status_code=201)
def create_invoice(
    tenant_id: str,
    payload: InvoiceCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=240),
    correlation_id: str = Header(alias="X-Correlation-ID", min_length=8, max_length=180),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    ensure_mutations_enabled()
    authorize(principal, tenant_id, "foundation.billing.write")
    key = require_idempotency_key(idempotency_key)
    correlation = require_correlation_id(correlation_id)
    request = payload.model_dump(mode="json")
    request_digest, replay = idempotency_replay(
        session,
        tenant_id=tenant_id,
        operation=f"invoice.create:{payload.account_id}:{payload.period_end.isoformat()}",
        idempotency_key=key,
        request_payload=request,
    )
    if replay:
        return replay
    account = _account(session, payload.account_id)
    _account_access(account, tenant_id, principal)
    if account.currency != payload.currency:
        raise HTTPException(status_code=422, detail="invoice_currency_mismatch")
    if session.scalar(
        select(Invoice).where(Invoice.account_id == payload.account_id, Invoice.period_end == payload.period_end)
    ):
        raise HTTPException(status_code=409, detail="invoice_period_exists")
    calculated_lines = [(line, _money(line.quantity * line.unit_amount)) for line in payload.lines]
    subtotal = _money(sum((amount for _, amount in calculated_lines), Decimal("0.00")))
    discount = _money(payload.discount_amount)
    credit = _money(payload.credit_amount)
    tax = _money(payload.tax_amount)
    total = _money(subtotal - discount - credit + tax)
    if total < 0:
        raise HTTPException(status_code=422, detail="invoice_total_cannot_be_negative")
    item = Invoice(
        account_id=payload.account_id,
        period_start=payload.period_start,
        period_end=payload.period_end,
        currency=payload.currency,
        status="DRAFT",
        subtotal_amount=subtotal,
        discount_amount=discount,
        credit_amount=credit,
        tax_amount=tax,
        total_amount=total,
    )
    session.add(item)
    session.flush()
    for line, line_amount in calculated_lines:
        session.add(
            InvoiceLine(
                invoice_id=item.id,
                line_code=line.line_code,
                description=line.description,
                quantity=line.quantity,
                unit_amount=line.unit_amount,
                line_amount=line_amount,
            )
        )
    session.flush()
    item = session.scalar(select(Invoice).options(selectinload(Invoice.lines)).where(Invoice.id == item.id))
    response = invoice_out(item).model_dump(mode="json")
    record_change(
        session,
        tenant_id=tenant_id,
        principal=principal,
        action="invoice.create",
        resource_type="invoice",
        resource_id=item.id,
        correlation_id=correlation,
        detail={"account_id": item.account_id, "period_end": item.period_end, "total": str(item.total_amount)},
        event_type="foundation.invoice.created.v1",
        event_payload={
            "tenant_id": tenant_id,
            "invoice_id": item.id,
            "account_id": item.account_id,
            "currency": item.currency,
            "total_amount": str(item.total_amount),
            "status": item.status,
        },
    )
    return commit_idempotent(
        session,
        tenant_id=tenant_id,
        operation=f"invoice.create:{payload.account_id}:{payload.period_end.isoformat()}",
        idempotency_key=key,
        request_hash=request_digest,
        status_code=201,
        response=response,
    )


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def get_invoice(
    tenant_id: str,
    invoice_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    authorize(principal, tenant_id, "foundation.billing.read")
    item = session.scalar(select(Invoice).options(selectinload(Invoice.lines)).where(Invoice.id == invoice_id))
    if item is None:
        raise HTTPException(status_code=404, detail="invoice_not_found")
    account = _account(session, item.account_id)
    _account_access(account, tenant_id, principal)
    return invoice_out(item)
