from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select, update
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
from app.crypto import decrypt_json, decrypt_text, encrypt_json, encrypt_text, identity_digest, normalize_identity
from app.database import db_session
from app.models import Identity, Preference, Profile, ProfileMerge, new_id
from app.schemas import IdentityOut, ProfileCreate, ProfileMergeRequest, ProfileOut, ProfileResolve
from app.security import Principal, authorize, current_principal

router = APIRouter(prefix="/v1/tenants/{tenant_id}/profiles", tags=["profiles"])


def _profile(session: Session, tenant_id: str, profile_id: str) -> Profile:
    profile = session.scalar(
        select(Profile)
        .options(selectinload(Profile.identities))
        .where(Profile.tenant_id == tenant_id, Profile.id == profile_id)
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="profile_not_found")
    return profile


def profile_out(profile: Profile) -> ProfileOut:
    return ProfileOut(
        id=profile.id,
        tenant_id=profile.tenant_id,
        external_ref=profile.external_ref,
        display_name=(
            decrypt_text(profile.display_name_enc, aad=f"profile-name:{profile.tenant_id}:{profile.id}")
            if profile.display_name_enc
            else None
        ),
        attributes=decrypt_json(profile.attributes_enc, aad=f"profile-attributes:{profile.tenant_id}:{profile.id}"),
        identities=[
            IdentityOut(
                id=item.id,
                kind=item.kind,
                value=decrypt_text(item.value_enc, aad=f"identity:{item.tenant_id}:{item.id}"),
                verified_at=item.verified_at,
                primary=item.is_primary,
            )
            for item in sorted(profile.identities, key=lambda row: (row.kind, row.id))
        ],
        version=profile.version,
        merged_into_id=profile.merged_into_id,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


@router.post("", response_model=ProfileOut, status_code=201)
def create_profile(
    tenant_id: str,
    payload: ProfileCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=240),
    correlation_id: str = Header(alias="X-Correlation-ID", min_length=8, max_length=180),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    ensure_mutations_enabled()
    authorize(principal, tenant_id, "foundation.profile.write")
    key = require_idempotency_key(idempotency_key)
    correlation = require_correlation_id(correlation_id)
    request = payload.model_dump(mode="json")
    digest, replay = idempotency_replay(
        session,
        tenant_id=tenant_id,
        operation="profile.create",
        idempotency_key=key,
        request_payload=request,
    )
    if replay:
        return replay
    tenant_or_404(session, tenant_id)
    if payload.external_ref and session.scalar(
        select(Profile).where(Profile.tenant_id == tenant_id, Profile.external_ref == payload.external_ref)
    ):
        raise HTTPException(status_code=409, detail="profile_external_ref_exists")

    prepared: list[tuple[str, str, str, bool, bool]] = []
    for item in payload.identities:
        normalized = normalize_identity(item.kind, item.value)
        digest_value = identity_digest(item.kind, normalized)
        existing = session.scalar(
            select(Identity).where(
                Identity.tenant_id == tenant_id,
                Identity.kind == item.kind,
                Identity.value_hash == digest_value,
            )
        )
        if existing:
            raise HTTPException(status_code=409, detail="identity_already_assigned")
        prepared.append((item.kind, normalized, digest_value, item.verified, item.primary))

    profile_id = new_id()
    profile = Profile(
        id=profile_id,
        tenant_id=tenant_id,
        external_ref=payload.external_ref,
        display_name_enc=(
            encrypt_text(payload.display_name, aad=f"profile-name:{tenant_id}:{profile_id}")
            if payload.display_name
            else None
        ),
        attributes_enc=encrypt_json(payload.attributes, aad=f"profile-attributes:{tenant_id}:{profile_id}"),
    )
    session.add(profile)
    for kind, normalized, digest_value, verified, primary in prepared:
        identity_id = new_id()
        session.add(
            Identity(
                id=identity_id,
                tenant_id=tenant_id,
                profile_id=profile_id,
                kind=kind,
                value_hash=digest_value,
                value_enc=encrypt_text(normalized, aad=f"identity:{tenant_id}:{identity_id}"),
                verified_at=datetime.now(timezone.utc) if verified else None,
                is_primary=primary,
            )
        )
    session.flush()
    profile = _profile(session, tenant_id, profile_id)
    response = profile_out(profile).model_dump(mode="json")
    record_change(
        session,
        tenant_id=tenant_id,
        principal=principal,
        action="profile.create",
        resource_type="profile",
        resource_id=profile.id,
        correlation_id=correlation,
        detail=request,
        event_type="foundation.profile.created.v1",
        event_payload={"tenant_id": tenant_id, "profile_id": profile.id, "version": profile.version},
    )
    return commit_idempotent(
        session,
        tenant_id=tenant_id,
        operation="profile.create",
        idempotency_key=key,
        request_hash=digest,
        status_code=201,
        response=response,
    )


@router.get("/{profile_id}", response_model=ProfileOut)
def get_profile(
    tenant_id: str,
    profile_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    authorize(principal, tenant_id, "foundation.profile.read")
    return profile_out(_profile(session, tenant_id, profile_id))


@router.post("/resolve", response_model=ProfileOut)
def resolve_profile(
    tenant_id: str,
    payload: ProfileResolve,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    authorize(principal, tenant_id, "foundation.profile.read")
    tenant_or_404(session, tenant_id)
    profile_ids: set[str] = set()
    for item in payload.identities:
        normalized = normalize_identity(item.kind, item.value)
        row = session.scalar(
            select(Identity).where(
                Identity.tenant_id == tenant_id,
                Identity.kind == item.kind,
                Identity.value_hash == identity_digest(item.kind, normalized),
            )
        )
        if row:
            profile_ids.add(row.profile_id)
    if not profile_ids:
        raise HTTPException(status_code=404, detail="profile_not_resolved")
    if len(profile_ids) != 1:
        raise HTTPException(status_code=409, detail="identity_resolution_conflict")
    profile = _profile(session, tenant_id, profile_ids.pop())
    if profile.merged_into_id:
        profile = _profile(session, tenant_id, profile.merged_into_id)
    return profile_out(profile)


@router.post("/{profile_id}/merge", response_model=ProfileOut)
def merge_profile(
    tenant_id: str,
    profile_id: str,
    payload: ProfileMergeRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=240),
    correlation_id: str = Header(alias="X-Correlation-ID", min_length=8, max_length=180),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    ensure_mutations_enabled()
    authorize(principal, tenant_id, "foundation.profile.write")
    if payload.target_profile_id == profile_id:
        raise HTTPException(status_code=422, detail="profile_cannot_merge_into_itself")
    key = require_idempotency_key(idempotency_key)
    correlation = require_correlation_id(correlation_id)
    request = payload.model_dump(mode="json")
    digest, replay = idempotency_replay(
        session,
        tenant_id=tenant_id,
        operation=f"profile.merge:{profile_id}",
        idempotency_key=key,
        request_payload=request,
    )
    if replay:
        return replay
    source = session.scalar(
        select(Profile).where(Profile.tenant_id == tenant_id, Profile.id == profile_id).with_for_update()
    )
    target = session.scalar(
        select(Profile).where(Profile.tenant_id == tenant_id, Profile.id == payload.target_profile_id).with_for_update()
    )
    if source is None or target is None:
        raise HTTPException(status_code=404, detail="profile_not_found")
    if source.merged_into_id or target.merged_into_id:
        raise HTTPException(status_code=409, detail="profile_already_merged")
    session.execute(update(Identity).where(Identity.profile_id == source.id).values(profile_id=target.id))
    session.execute(update(Preference).where(Preference.profile_id == source.id).values(profile_id=target.id))
    source.merged_into_id = target.id
    source.version += 1
    target.version += 1
    source.updated_at = datetime.now(timezone.utc)
    target.updated_at = datetime.now(timezone.utc)
    session.add(
        ProfileMerge(
            tenant_id=tenant_id,
            source_profile_id=source.id,
            target_profile_id=target.id,
            actor_subject=principal.subject,
            reason=payload.reason,
        )
    )
    session.flush()
    response = profile_out(_profile(session, tenant_id, target.id)).model_dump(mode="json")
    record_change(
        session,
        tenant_id=tenant_id,
        principal=principal,
        action="profile.merge",
        resource_type="profile",
        resource_id=source.id,
        correlation_id=correlation,
        detail=request,
        event_type="foundation.profile.merged.v1",
        event_payload={
            "tenant_id": tenant_id,
            "source_profile_id": source.id,
            "target_profile_id": target.id,
        },
    )
    return commit_idempotent(
        session,
        tenant_id=tenant_id,
        operation=f"profile.merge:{profile_id}",
        idempotency_key=key,
        request_hash=digest,
        status_code=200,
        response=response,
    )
