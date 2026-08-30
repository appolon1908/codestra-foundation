from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import and_, or_, select

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import OutboxEvent

MAX_ATTEMPTS = 5
STALE_LEASE = timedelta(minutes=5)


class OutboxDispatcher:
    def __init__(
        self,
        settings: Settings | None = None,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client_factory = client_factory or self._production_client

    def _production_client(self) -> httpx.Client:
        if not self.settings.outbox_ready:
            raise RuntimeError("foundation_outbox_transport_not_configured")
        return httpx.Client(
            verify=self.settings.middleware_ca_file,
            cert=(
                self.settings.middleware_client_cert_file,
                self.settings.middleware_client_key_file,
            ),
            timeout=httpx.Timeout(10.0),
            follow_redirects=False,
        )

    def _claim(self, limit: int) -> list[OutboxEvent]:
        now = datetime.now(timezone.utc)
        stale_before = now - STALE_LEASE
        with SessionLocal() as session:
            rows = session.scalars(
                select(OutboxEvent)
                .where(
                    or_(
                        and_(OutboxEvent.status == "PENDING", OutboxEvent.available_at <= now),
                        and_(OutboxEvent.status == "SENDING", OutboxEvent.last_attempt_at <= stale_before),
                    )
                )
                .order_by(OutboxEvent.created_at)
                .with_for_update(skip_locked=True)
                .limit(limit)
            ).all()
            for row in rows:
                row.status = "SENDING"
                row.attempts += 1
                row.last_attempt_at = now
                row.last_error_code = None
            session.commit()
            return rows

    def _envelope(self, row: OutboxEvent) -> dict:
        return {
            "event_id": row.id,
            "event_type": row.event_type,
            "source": "codestra-foundation",
            "tenant_id": row.tenant_id,
            "correlation_id": row.correlation_id,
            "occurred_at": row.created_at.isoformat(),
            "aggregate": {"type": row.aggregate_type, "id": row.aggregate_id},
            "data": json.loads(row.payload_json),
        }

    def _headers(self, row: OutboxEvent) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.middleware_access_token}",
            "Content-Type": "application/json",
            "Idempotency-Key": f"foundation:event:{row.id}",
            "X-Tenant-ID": row.tenant_id,
            "X-Correlation-ID": row.correlation_id,
            "X-Foundation-Event-ID": row.id,
        }

    def _complete(self, event_id: str) -> None:
        with SessionLocal() as session:
            row = session.get(OutboxEvent, event_id)
            if row is None:
                return
            row.status = "DELIVERED"
            row.last_error_code = None
            session.commit()

    def _fail(self, event_id: str, error_code: str) -> None:
        with SessionLocal() as session:
            row = session.get(OutboxEvent, event_id)
            if row is None:
                return
            row.last_error_code = error_code[:120]
            if row.attempts >= MAX_ATTEMPTS:
                row.status = "DEAD_LETTER"
            else:
                row.status = "PENDING"
                row.available_at = datetime.now(timezone.utc) + timedelta(seconds=min(300, 2**row.attempts))
            session.commit()

    def run_once(self, limit: int = 50) -> int:
        if not self.settings.outbox_enabled:
            return 0
        if not self.settings.outbox_ready:
            raise RuntimeError("foundation_outbox_transport_not_configured")
        rows = self._claim(limit)
        for row in rows:
            try:
                with self.client_factory() as client:
                    response = client.post(
                        self.settings.middleware_event_url,
                        headers=self._headers(row),
                        json=self._envelope(row),
                    )
                if 200 <= response.status_code < 300:
                    self._complete(row.id)
                else:
                    self._fail(row.id, f"middleware_http_{response.status_code}")
            except httpx.TimeoutException:
                self._fail(row.id, "middleware_timeout")
            except httpx.HTTPError:
                self._fail(row.id, "middleware_transport_error")
        return len(rows)


def main() -> None:
    dispatcher = OutboxDispatcher()
    while True:
        count = dispatcher.run_once()
        time.sleep(1 if count else 5)


if __name__ == "__main__":
    main()
