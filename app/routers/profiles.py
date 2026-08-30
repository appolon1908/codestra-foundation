from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
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
from app.schemas import (
    IdentityIn,
    IdentityOut,
    IdentityPatch,
    ProfileCreate,
    ProfileMergeOut,
    ProfileMergeRequest,
    ProfileOut,
    ProfilePatch,
    ProfileResolve,
)
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
                status=item.status,
                version=item.version,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in sorted(profile.identities, key=lambda row: (row.kind, row.id))
        ],
        version=profile.version,
        merged_into_id=profile.merged_into_id,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def identity_out(item: Identity) -> IdentityOut:
    return IdentityOut(
        id=item.id,
        kind=item.kind,
        value=decrypt_text(item.value_enc, aad=f"identity:{item.tenant_id}:{item.id}"),
        verified_at=item.verified_at,
        primary=item.is_primary,
        status=item.status,
        version=item.version,
        created_at=item.created_at,
        updated_at=item.updated_at,
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


@router.get("", response_model=list[ProfileOut])
def list_profiles(
    tenant_id: str,
    external_ref: str | None = Query(default=None, max_length=180),
    include_merged: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    authorize(principal, tenant_id, "foundation.profile.read")
    tenant_or_404(session, tenant_id)
    statement = select(Profile).options(selectinload(Profile.identities)).where(Profile.tenant_id == tenant_id)
    if external_ref is not None:
        statement = statement.where(Profile.external_ref == external_ref)
    if not include_merged:
        statement = statement.where(Profile.merged_into_id.is_(None))
    rows = session.scalars(statement.order_by(Profile.created_at, Profile.id).offset(offset).limit(limit)).all()
    return [profile_out(row) for row in rows]


@router.patch("/{profile_id}", response_model=ProfileOut)
def patch_profile(
    tenant_id: str,
    profile_id: str,
    payload: ProfilePatch,
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
        operation=f"profile.patch:{profile_id}",
        idempotency_key=key,
        request_payload=request,
    )
    if replay:
        return replay
    item = session.scalar(
        select(Profile).where(Profile.tenant_id == tenant_id, Profile.id == profile_id).with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="profile_not_found")
    if item.merged_into_id:
        raise HTTPException(status_code=409, detail="merged_profile_is_read_only")
    if item.version != payload.expected_version:
        raise HTTPException(status_code=409, detail="profile_version_conflict")
    if "external_ref" in payload.model_fields_set:
        if payload.external_ref and session.scalar(
            select(Profile).where(
                Profile.tenant_id == tenant_id,
                Profile.external_ref == payload.external_ref,
                Profile.id != item.id,
            )
        ):
            raise HTTPException(status_code=409, detail="profile_external_ref_exists")
        item.external_ref = payload.external_ref
    if "display_name" in payload.model_fields_set:
        item.display_name_enc = (
            encrypt_text(payload.display_name, aad=f"profile-name:{tenant_id}:{item.id}")
            if payload.display_name
            else None
        )
    if "attributes" in payload.model_fields_set:
        item.attributes_enc = encrypt_json(
            payload.attributes or {}, aad=f"profile-attributes:{tenant_id}:{item.id}"
        )
    item.version += 1
    item.updated_at = datetime.now(timezone.utc)
    session.flush()
    response = profile_out(_profile(session, tenant_id, item.id)).model_dump(mode="json")
    record_change(
        session,
        tenant_id=tenant_id,
        principal=principal,
        action="profile.patch",
        resource_type="profile",
        resource_id=item.id,
        correlation_id=correlation,
        detail=request,
        event_type="foundation.profile.changed.v1",
        event_payload={"tenant_id": tenant_id, "profile_id": item.id, "version": item.version},
    )
    return commit_idempotent(
        session,
        tenant_id=tenant_id,
        operation=f"profile.patch:{profile_id}",
        idempotency_key=key,
        request_hash=digest,
        status_code=200,
        response=response,
    )


@router.post("/{profile_id}/identities", response_model=IdentityOut, status_code=201)
def add_identity(
    tenant_id: str,
    profile_id: str,
    payload: IdentityIn,
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
        operation=f"identity.add:{profile_id}",
        idempotency_key=key,
        request_payload=request,
    )
    if replay:
        return replay
    profile = session.scalar(
        select(Profile).where(Profile.tenant_id == tenant_id, Profile.id == profile_id).with_for_update()
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="profile_not_found")
    if profile.merged_into_id:
        raise HTTPException(status_code=409, detail="merged_profile_is_read_only")
    normalized = normalize_identity(payload.kind, payload.value)
    value_hash = identity_digest(payload.kind, normalized)
    if session.scalar(
        select(Identity).where(
            Identity.tenant_id == tenant_id,
            Identity.kind == payload.kind,
            Identity.value_hash == value_hash,
        )
    ):
        raise HTTPException(status_code=409, detail="identity_already_assigned")
    now = datetime.now(timezone.utc)
    if payload.primary:
        primaries = session.scalars(
            select(Identity).where(
                Identity.tenant_id == tenant_id,
                Identity.profile_id == profile_id,
                Identity.kind == payload.kind,
                Identity.is_primary.is_(True),
            )
        ).all()
        for primary in primaries:
            primary.is_primary = False
            primary.version += 1
            primary.updated_at = now
    identity_id = new_id()
    item = Identity(
        id=identity_id,
        tenant_id=tenant_id,
        profile_id=profile_id,
        kind=payload.kind,
        value_hash=value_hash,
        value_enc=encrypt_text(normalized, aad=f"identity:{tenant_id}:{identity_id}"),
        verified_at=now if payload.verified else None,
        is_primary=payload.primary,
    )
    session.add(item)
    profile.version += 1
    profile.updated_at = now
    session.flush()
    response = identity_out(item).model_dump(mode="json")
    record_change(
        session,
        tenant_id=tenant_id,
        principal=principal,
        action="identity.add",
        resource_type="identity",
        resource_id=item.id,
        correlation_id=correlation,
        detail={"kind": item.kind, "value_hash": item.value_hash, "profile_id": profile_id},
        event_type="foundation.identity.created.v1",
        event_payload={
            "tenant_id": tenant_id,
            "profile_id": profile_id,
            "identity_id": item.id,
            "kind": item.kind,
        },
    )
    return commit_idempotent(
        session,
        tenant_id=tenant_id,
        operation=f"identity.add:{profile_id}",
        idempotency_key=key,
        request_hash=digest,
        status_code=201,
        response=response,
    )


@router.patch("/{profile_id}/identities/{identity_id}", response_model=IdentityOut)
def patch_identity(
    tenant_id: str,
    profile_id: str,
    identity_id: str,
    payload: IdentityPatch,
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
        operation=f"identity.patch:{identity_id}",
        idempotency_key=key,
        request_payload=request,
    )
    if replay:
        return replay
    profile = session.scalar(
        select(Profile).where(Profile.tenant_id == tenant_id, Profile.id == profile_id).with_for_update()
    )
    item = session.scalar(
        select(Identity).where(
            Identity.tenant_id == tenant_id,
            Identity.profile_id == profile_id,
            Identity.id == identity_id,
        ).with_for_update()
    )
    if profile is None or item is None:
        raise HTTPException(status_code=404, detail="identity_not_found")
    if profile.merged_into_id:
        raise HTTPException(status_code=409, detail="merged_profile_is_read_only")
    if item.version != payload.expected_version:
        raise HTTPException(status_code=409, detail="identity_version_conflict")
    now = datetime.now(timezone.utc)
    if payload.status is not None:
        item.status = payload.status
        if payload.status == "REVOKED":
            item.is_primary = False
    if payload.verified is not None:
        item.verified_at = now if payload.verified else None
    if payload.primary is not None:
        if payload.primary and item.status != "ACTIVE":
            raise HTTPException(status_code=422, detail="revoked_identity_cannot_be_primary")
        if payload.primary:
            primaries = session.scalars(
                select(Identity).where(
                    Identity.tenant_id == tenant_id,
                    Identity.profile_id == profile_id,
                    Identity.kind == item.kind,
                    Identity.id != item.id,
                    Identity.is_primary.is_(True),
                )
            ).all()
            for primary in primaries:
                primary.is_primary = False
                primary.version += 1
                primary.updated_at = now
        item.is_primary = payload.primary
    item.version += 1
    item.updated_at = now
    profile.version += 1
    profile.updated_at = now
    session.flush()
    response = identity_out(item).model_dump(mode="json")
    record_change(
        session,
        tenant_id=tenant_id,
        principal=principal,
        action="identity.patch",
        resource_type="identity",
        resource_id=item.id,
        correlation_id=correlation,
        detail={"status": item.status, "verified": bool(item.verified_at), "primary": item.is_primary},
        event_type="foundation.identity.changed.v1",
        event_payload={
            "tenant_id": tenant_id,
            "profile_id": profile_id,
            "identity_id": item.id,
            "status": item.status,
            "version": item.version,
        },
    )
    return commit_idempotent(
        session,
        tenant_id=tenant_id,
        operation=f"identity.patch:{identity_id}",
        idempotency_key=key,
        request_hash=digest,
        status_code=200,
        response=response,
    )


@router.get("/{profile_id}/merges", response_model=list[ProfileMergeOut])
def list_profile_merges(
    tenant_id: str,
    profile_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
):
    authorize(principal, tenant_id, "foundation.profile.read")
    if not session.scalar(select(Profile.id).where(Profile.tenant_id == tenant_id, Profile.id == profile_id)):
        raise HTTPException(status_code=404, detail="profile_not_found")
    rows = session.scalars(
        select(ProfileMerge)
        .where(
            ProfileMerge.tenant_id == tenant_id,
            (ProfileMerge.source_profile_id == profile_id) | (ProfileMerge.target_profile_id == profile_id),
        )
        .order_by(ProfileMerge.created_at)
    ).all()
    return [
        ProfileMergeOut(
            id=row.id,
            tenant_id=row.tenant_id,
            source_profile_id=row.source_profile_id,
            target_profile_id=row.target_profile_id,
            actor_subject=row.actor_subject,
            reason=row.reason,
            created_at=row.created_at,
        )
        for row in rows
    ]


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
                Identity.status == "ACTIVE",
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
    now = datetime.now(timezone.utc)
    source_identities = session.scalars(
        select(Identity).where(Identity.tenant_id == tenant_id, Identity.profile_id == source.id)
    ).all()
    target_primary_kinds = set(
        session.scalars(
            select(Identity.kind).where(
                Identity.tenant_id == tenant_id,
                Identity.profile_id == target.id,
                Identity.status == "ACTIVE",
                Identity.is_primary.is_(True),
            )
        ).all()
    )
    retained_source_primary_kinds: set[str] = set()
    for identity in source_identities:
        if not identity.is_primary or identity.status != "ACTIVE":
            continue
        if identity.kind in target_primary_kinds or identity.kind in retained_source_primary_kinds:
            identity.is_primary = False
            identity.version += 1
            identity.updated_at = now
        else:
            retained_source_primary_kinds.add(identity.kind)
    session.execute(update(Identity).where(Identity.profile_id == source.id).values(profile_id=target.id))
    session.execute(update(Preference).where(Preference.profile_id == source.id).values(profile_id=target.id))
    source.merged_into_id = target.id
    source.version += 1
    target.version += 1
    source.updated_at = now
    target.updated_at = now
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
