from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from email_validator import EmailNotValidError, validate_email
from fastapi import HTTPException

from app.config import get_settings

E164_RE = re.compile(r"^\+[1-9][0-9]{5,18}$")


def _decode_key(value: str | None, name: str, expected: int = 32) -> bytes:
    if not value:
        raise HTTPException(status_code=503, detail=f"{name}_not_configured")
    try:
        key = base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception as exc:  # pragma: no cover - defensive configuration guard
        raise HTTPException(status_code=503, detail=f"{name}_invalid") from exc
    if len(key) != expected:
        raise HTTPException(status_code=503, detail=f"{name}_invalid")
    return key


def normalize_identity(kind: str, value: str) -> str:
    normalized_kind = kind.upper()
    candidate = value.strip()
    if normalized_kind == "EMAIL":
        try:
            return validate_email(candidate, check_deliverability=False).normalized.lower()
        except EmailNotValidError as exc:
            raise HTTPException(status_code=422, detail="invalid_email_identity") from exc
    if normalized_kind == "PHONE":
        if not E164_RE.fullmatch(candidate):
            raise HTTPException(status_code=422, detail="invalid_phone_identity")
        return candidate
    if normalized_kind == "EXTERNAL":
        if not candidate or len(candidate) > 255:
            raise HTTPException(status_code=422, detail="invalid_external_identity")
        return candidate
    raise HTTPException(status_code=422, detail="unsupported_identity_kind")


def identity_digest(kind: str, normalized: str) -> str:
    settings = get_settings()
    key = _decode_key(settings.identity_hash_key, "identity_hash_key")
    return hmac.new(key, f"{kind.upper()}:{normalized}".encode(), hashlib.sha256).hexdigest()


def encrypt_text(value: str, *, aad: str) -> str:
    settings = get_settings()
    key = _decode_key(settings.field_encryption_key, "field_encryption_key")
    nonce = os.urandom(12)
    encrypted = AESGCM(key).encrypt(nonce, value.encode(), aad.encode())
    return "v1:" + base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def decrypt_text(value: str, *, aad: str) -> str:
    settings = get_settings()
    key = _decode_key(settings.field_encryption_key, "field_encryption_key")
    if not value.startswith("v1:"):
        raise HTTPException(status_code=500, detail="encrypted_value_version_invalid")
    raw = base64.urlsafe_b64decode(value[3:].encode("ascii"))
    return AESGCM(key).decrypt(raw[:12], raw[12:], aad.encode()).decode()


def encrypt_json(value: dict, *, aad: str) -> str:
    return encrypt_text(json.dumps(value, sort_keys=True, separators=(",", ":")), aad=aad)


def decrypt_json(value: str, *, aad: str) -> dict:
    return json.loads(decrypt_text(value, aad=aad))


def payload_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()
