from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crypto import decrypt_text, encrypt_text, payload_hash
from app.models import AuditEvent, IdempotencyRecord, OutboxEvent, Tenant, new_id
from app.security import Principal


def ensure_mutations_enabled() -> None:
    if not get_settings().mutations_enabled:
        raise HTTPException(status_code=503, detail="foundation_mutations_disabled")


def tenant_or_404(session: Session, tenant_id: str) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant_not_found")
    return tenant


def require_idempotency_key(value: str | None) -> str:
    if value is None or not 8 <= len(value) <= 240:
        raise HTTPException(status_code=422, detail="valid_idempotency_key_required")
    return value


def require_correlation_id(value: str | None) -> str:
    if value is None or not 8 <= len(value) <= 180:
        raise HTTPException(status_code=422, detail="valid_correlation_id_required")
    return value


def idempotency_replay(
    session: Session,
    *,
    tenant_id: str,
    operation: str,
    idempotency_key: str,
    request_payload: object,
) -> tuple[str, JSONResponse | None]:
    digest = payload_hash(request_payload)
    existing = session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.idempotency_key == idempotency_key,
        )
    )
    if existing is None:
        return digest, None
    if existing.request_hash != digest:
        raise HTTPException(status_code=409, detail="idempotency_key_payload_mismatch")
    decrypted = decrypt_text(
        existing.response_json,
        aad=f"idempotency:{existing.tenant_id}:{existing.id}",
    )
    return digest, JSONResponse(status_code=existing.response_status, content=json.loads(decrypted))


def store_idempotency(
    session: Session,
    *,
    tenant_id: str,
    operation: str,
    idempotency_key: str,
    request_hash: str,
    response_status: int,
    response: dict[str, Any],
) -> None:
    record_id = new_id()
    encoded_response = json.dumps(response, sort_keys=True, separators=(",", ":"), default=str)
    session.add(
        IdempotencyRecord(
            id=record_id,
            tenant_id=tenant_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_status=response_status,
            response_json=encrypt_text(
                encoded_response,
                aad=f"idempotency:{tenant_id}:{record_id}",
            ),
        )
    )


def record_change(
    session: Session,
    *,
    tenant_id: str,
    principal: Principal,
    action: str,
    resource_type: str,
    resource_id: str,
    correlation_id: str,
    detail: object,
    event_type: str,
    event_payload: dict[str, Any],
) -> None:
    session.add(
        AuditEvent(
            tenant_id=tenant_id,
            actor_subject=principal.subject,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            correlation_id=correlation_id,
            detail_hash=payload_hash(detail),
        )
    )
    session.add(
        OutboxEvent(
            tenant_id=tenant_id,
            event_type=event_type,
            aggregate_type=resource_type,
            aggregate_id=resource_id,
            payload_json=json.dumps(event_payload, sort_keys=True, separators=(",", ":"), default=str),
            correlation_id=correlation_id,
        )
    )


def commit_idempotent(
    session: Session,
    *,
    tenant_id: str,
    operation: str,
    idempotency_key: str,
    request_hash: str,
    status_code: int,
    response: dict[str, Any],
) -> dict[str, Any]:
    store_idempotency(
        session,
        tenant_id=tenant_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        response_status=status_code,
        response=response,
    )
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    return response


def serialize(model, builder: Callable[..., Any]) -> dict[str, Any]:
    return builder(model).model_dump(mode="json")
