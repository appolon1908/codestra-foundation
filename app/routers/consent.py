from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import or_, select
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
from app.crypto import identity_digest, normalize_identity, payload_hash
from app.database import db_session
from app.models import ConsentEvent, Identity, Preference
from app.schemas import ConsentCreate, ConsentOut, EffectivePreferenceOut, PreferenceOut, PreferenceUpsert
from app.security import Principal, authorize, current_principal

router = APIRouter(prefix="/v1/tenants/{tenant_id}/communications", tags=["consent-preferences"])


def _identity_context(session: Session, tenant_id: str, kind: str, value: str) -> tuple[str, str | None]:
    normalized = normalize_identity(kind, value)
    digest = identity_digest(kind, normalized)
    identity = session.scalar(
        select(Identity).where(
            Identity.tenant_id == tenant_id,
            Identity.kind == kind,
            Identity.value_hash == digest,
        )
    )
    return digest, identity.profile_id if identity else None


def _latest_consent(session: Session, tenant_id: str, digest: str, topic: str, channel: str) -> ConsentEvent | None:
    return session.scalar(
        select(ConsentEvent)
        .where(
            ConsentEvent.tenant_id == tenant_id,
            ConsentEvent.identity_hash == digest,
            ConsentEvent.topic == topic,
            or_(ConsentEvent.channel == channel, ConsentEvent.channel == "ALL"),
        )
        .order_by(ConsentEvent.occurred_at.desc(), ConsentEvent.created_at.desc())
        .limit(1)
    )


def _effective_preference(session: Session, tenant_id: str, digest: str, topic: str, channel: str) -> Preference | None:
    rows = session.scalars(
        select(Preference)
        .where(
            Preference.tenant_id == tenant_id,
            Preference.identity_hash == digest,
            Preference.topic == topic,
            or_(Preference.channel == channel, Preference.channel == "ALL"),
        )
        .order_by(Preference.updated_at.desc())
    ).all()
    return next((row for row in rows if row.state == "UNSUBSCRIBED"), rows[0] if rows else None)


def consent_out(item: ConsentEvent) -> ConsentOut:
    return ConsentOut(
        id=item.id,
        tenant_id=item.tenant_id,
        profile_id=item.profile_id,
        identity_reference=f"hmac-sha256:{item.identity_hash}",
        topic=item.topic,
        channel=item.channel,
        state=item.state,
        lawful_basis=item.lawful_basis,
        source=item.source,
        evidence_hash=item.evidence_hash,
        occurred_at=item.occurred_at,
        created_at=item.created_at,
    )


def preference_out(item: Preference) -> PreferenceOut:
    return PreferenceOut(
        id=item.id,
        tenant_id=item.tenant_id,
        profile_id=item.profile_id,
        identity_reference=f"hmac-sha256:{item.identity_hash}",
        topic=item.topic,
        channel=item.channel,
        state=item.state,
        frequency=item.frequency,
        source=item.source,
        version=item.version,
        updated_at=item.updated_at,
    )


@router.post("/consents", response_model=ConsentOut, status_code=201)
def append_consent(
    tenant_id: str,
    payload: ConsentCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=240),
    correlation_id: str = Header(alias="X-Correlation-ID", min_length=8, max_length=180),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    ensure_mutations_enabled()
    authorize(principal, tenant_id, "foundation.consent.write")
    key = require_idempotency_key(idempotency_key)
    correlation = require_correlation_id(correlation_id)
    request = payload.model_dump(mode="json")
    digest_request, replay = idempotency_replay(
        session,
        tenant_id=tenant_id,
        operation="consent.append",
        idempotency_key=key,
        request_payload=request,
    )
    if replay:
        return replay
    tenant_or_404(session, tenant_id)
    if payload.occurred_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="occurred_at_timezone_required")
    if payload.occurred_at > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise HTTPException(status_code=422, detail="occurred_at_too_far_in_future")
    digest, profile_id = _identity_context(session, tenant_id, payload.identity.kind, payload.identity.value)
    event = ConsentEvent(
        tenant_id=tenant_id,
        profile_id=profile_id,
        identity_hash=digest,
        topic=payload.topic,
        channel=payload.channel,
        state=payload.state,
        lawful_basis=payload.lawful_basis,
        source=payload.source,
        evidence_hash=payload_hash(payload.evidence),
        occurred_at=payload.occurred_at,
        actor_subject=principal.subject,
    )
    session.add(event)
    session.flush()
    if payload.state in {"DENIED", "REVOKED"}:
        preference = session.scalar(
            select(Preference).where(
                Preference.tenant_id == tenant_id,
                Preference.identity_hash == digest,
                Preference.topic == payload.topic,
                Preference.channel == payload.channel,
            )
        )
        if preference is None:
            preference = Preference(
                tenant_id=tenant_id,
                profile_id=profile_id,
                identity_hash=digest,
                topic=payload.topic,
                channel=payload.channel,
                state="UNSUBSCRIBED",
                source=f"consent:{event.id}",
            )
            session.add(preference)
        else:
            preference.state = "UNSUBSCRIBED"
            preference.source = f"consent:{event.id}"
            preference.frequency = None
            preference.version += 1
            preference.updated_at = datetime.now(timezone.utc)
    session.flush()
    response = consent_out(event).model_dump(mode="json")
    record_change(
        session,
        tenant_id=tenant_id,
        principal=principal,
        action="consent.append",
        resource_type="consent",
        resource_id=event.id,
        correlation_id=correlation,
        detail={
            "identity_hash": digest,
            "topic": event.topic,
            "channel": event.channel,
            "state": event.state,
            "evidence_hash": event.evidence_hash,
        },
        event_type="foundation.consent.changed.v1",
        event_payload={
            "tenant_id": tenant_id,
            "consent_id": event.id,
            "identity_reference": f"hmac-sha256:{digest}",
            "topic": event.topic,
            "channel": event.channel,
            "state": event.state,
        },
    )
    return commit_idempotent(
        session,
        tenant_id=tenant_id,
        operation="consent.append",
        idempotency_key=key,
        request_hash=digest_request,
        status_code=201,
        response=response,
    )


@router.get("/consents", response_model=list[ConsentOut])
def list_consents(
    tenant_id: str,
    identity_kind: str = Query(pattern="^(EMAIL|PHONE|EXTERNAL)$"),
    identity_value: str = Query(min_length=1, max_length=320),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    authorize(principal, tenant_id, "foundation.consent.read")
    digest, _ = _identity_context(session, tenant_id, identity_kind, identity_value)
    rows = session.scalars(
        select(ConsentEvent)
        .where(ConsentEvent.tenant_id == tenant_id, ConsentEvent.identity_hash == digest)
        .order_by(ConsentEvent.occurred_at.desc())
        .limit(200)
    ).all()
    return [consent_out(row) for row in rows]


@router.put("/preferences", response_model=PreferenceOut)
def upsert_preference(
    tenant_id: str,
    payload: PreferenceUpsert,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=240),
    correlation_id: str = Header(alias="X-Correlation-ID", min_length=8, max_length=180),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    ensure_mutations_enabled()
    authorize(principal, tenant_id, "foundation.preference.write")
    key = require_idempotency_key(idempotency_key)
    correlation = require_correlation_id(correlation_id)
    request = payload.model_dump(mode="json")
    request_digest, replay = idempotency_replay(
        session,
        tenant_id=tenant_id,
        operation=f"preference.upsert:{payload.topic}:{payload.channel}",
        idempotency_key=key,
        request_payload=request,
    )
    if replay:
        return replay
    tenant_or_404(session, tenant_id)
    digest, profile_id = _identity_context(session, tenant_id, payload.identity.kind, payload.identity.value)
    if payload.state == "SUBSCRIBED":
        latest = _latest_consent(session, tenant_id, digest, payload.topic, payload.channel)
        if latest is None or latest.state != "GRANTED":
            raise HTTPException(status_code=422, detail="active_granted_consent_required")
    item = session.scalar(
        select(Preference).where(
            Preference.tenant_id == tenant_id,
            Preference.identity_hash == digest,
            Preference.topic == payload.topic,
            Preference.channel == payload.channel,
        )
    )
    if item is None:
        if payload.expected_version not in (None, 1):
            raise HTTPException(status_code=409, detail="preference_version_conflict")
        item = Preference(
            tenant_id=tenant_id,
            profile_id=profile_id,
            identity_hash=digest,
            topic=payload.topic,
            channel=payload.channel,
            state=payload.state,
            frequency=payload.frequency,
            source=payload.source,
        )
        session.add(item)
    else:
        if payload.expected_version is not None and item.version != payload.expected_version:
            raise HTTPException(status_code=409, detail="preference_version_conflict")
        item.profile_id = profile_id or item.profile_id
        item.state = payload.state
        item.frequency = payload.frequency
        item.source = payload.source
        item.version += 1
        item.updated_at = datetime.now(timezone.utc)
    session.flush()
    response = preference_out(item).model_dump(mode="json")
    record_change(
        session,
        tenant_id=tenant_id,
        principal=principal,
        action="preference.upsert",
        resource_type="preference",
        resource_id=item.id,
        correlation_id=correlation,
        detail={
            "identity_hash": digest,
            "topic": item.topic,
            "channel": item.channel,
            "state": item.state,
            "version": item.version,
        },
        event_type="foundation.preference.changed.v1",
        event_payload={
            "tenant_id": tenant_id,
            "preference_id": item.id,
            "identity_reference": f"hmac-sha256:{digest}",
            "topic": item.topic,
            "channel": item.channel,
            "state": item.state,
            "version": item.version,
        },
    )
    return commit_idempotent(
        session,
        tenant_id=tenant_id,
        operation=f"preference.upsert:{payload.topic}:{payload.channel}",
        idempotency_key=key,
        request_hash=request_digest,
        status_code=200,
        response=response,
    )


@router.get("/effective", response_model=EffectivePreferenceOut)
def effective_preference(
    tenant_id: str,
    identity_kind: str = Query(pattern="^(EMAIL|PHONE|EXTERNAL)$"),
    identity_value: str = Query(min_length=1, max_length=320),
    topic: str = Query(pattern=r"^[a-z0-9][a-z0-9._-]{0,119}$"),
    channel: str = Query(pattern="^(EMAIL|SMS|VOICE|SOCIAL)$"),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    authorize(principal, tenant_id, "foundation.preference.read")
    tenant_or_404(session, tenant_id)
    digest, _ = _identity_context(session, tenant_id, identity_kind, identity_value)
    consent = _latest_consent(session, tenant_id, digest, topic, channel)
    preference = _effective_preference(session, tenant_id, digest, topic, channel)
    if consent is None:
        allowed, reason = False, "NO_CONSENT"
    elif consent.state != "GRANTED":
        allowed, reason = False, f"CONSENT_{consent.state}"
    elif preference and preference.state == "UNSUBSCRIBED":
        allowed, reason = False, "PREFERENCE_UNSUBSCRIBED"
    else:
        allowed, reason = True, "GRANTED"
    return EffectivePreferenceOut(
        tenant_id=tenant_id,
        identity_reference=f"hmac-sha256:{digest}",
        topic=topic,
        channel=channel,
        allowed=allowed,
        reason=reason,
        consent_state=consent.state if consent else None,
        preference_state=preference.state if preference else None,
    )
