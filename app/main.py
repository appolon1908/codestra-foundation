from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.routers import billing, consent, profiles, tenants


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    if settings.auto_create_schema:
        Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Codestra Foundation API",
    version="1.0.0",
    description=(
        "Shared tenant, profile, identity, consent, preference, billing, usage, and entitlement authority. "
        "Effectful cross-system calls remain governed by Codestra Middleware."
    ),
    lifespan=lifespan,
)

app.include_router(tenants.router)
app.include_router(profiles.router)
app.include_router(consent.router)
app.include_router(billing.router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response


@app.get("/healthz", tags=["operations"])
def healthz():
    return {"status": "ok", "service": "codestra-foundation"}


@app.get("/readyz", tags=["operations"])
def readyz():
    settings = get_settings()
    failures: list[str] = []
    if not settings.auth_ready:
        failures.append("jwt_verification_not_configured")
    if not settings.cryptography_ready:
        failures.append("field_cryptography_not_configured")
    if settings.outbox_enabled and not settings.outbox_ready:
        failures.append("outbox_transport_not_configured")
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        failures.append("database_unavailable")
    if failures:
        return JSONResponse(status_code=503, content={"status": "not_ready", "failures": failures})
    return {
        "status": "ready",
        "mutations_enabled": settings.mutations_enabled,
        "environment": settings.environment,
    }


@app.get("/version", tags=["operations"])
def version():
    return {"service": "codestra-foundation", "version": app.version}
