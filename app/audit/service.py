"""Append-only audit trail. Events are never updated or deleted (ORM guards + DB trigger in migration)."""
from __future__ import annotations

import contextvars
import uuid
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AuditEventType
from app.core.timeutil import utcnow
from app.models.meters import ImmutableRecordError
from app.models.ops import AuditEvent

_correlation: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_id", default=None)


def set_correlation_id(value: str | None = None) -> str:
    cid = value or uuid.uuid4().hex
    _correlation.set(cid)
    return cid


def get_correlation_id() -> str:
    return _correlation.get() or set_correlation_id()


@event.listens_for(AuditEvent, "before_update")
def _no_update(mapper, connection, target):
    raise ImmutableRecordError("audit events are append-only")


@event.listens_for(AuditEvent, "before_delete")
def _no_delete(mapper, connection, target):
    raise ImmutableRecordError("audit events cannot be deleted")


async def record(session: AsyncSession, event_type: AuditEventType | str, entity_type: str, entity_id: Any,
                 actor: str = "system", payload: dict | None = None, microgrid_id: uuid.UUID | None = None,
                 correlation_id: str | None = None) -> AuditEvent:
    ev = AuditEvent(
        timestamp=utcnow(), microgrid_id=microgrid_id, entity_type=entity_type, entity_id=str(entity_id),
        event_type=event_type.value if isinstance(event_type, AuditEventType) else event_type,
        actor=actor, payload=_jsonable(payload or {}), correlation_id=correlation_id or get_correlation_id())
    session.add(ev)
    await session.flush()
    return ev


def _jsonable(obj):
    from datetime import date, datetime
    from decimal import Decimal
    from enum import Enum
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    return obj


async def list_events(session: AsyncSession, entity_type: str | None = None, entity_id: str | None = None,
                      event_type: str | None = None, correlation_id: str | None = None,
                      microgrid_id: uuid.UUID | None = None, limit: int = 200) -> list[AuditEvent]:
    q = select(AuditEvent).order_by(AuditEvent.timestamp.desc(), AuditEvent.id).limit(limit)
    if entity_type:
        q = q.where(AuditEvent.entity_type == entity_type)
    if entity_id:
        q = q.where(AuditEvent.entity_id == entity_id)
    if event_type:
        q = q.where(AuditEvent.event_type == event_type)
    if correlation_id:
        q = q.where(AuditEvent.correlation_id == correlation_id)
    if microgrid_id:
        q = q.where(AuditEvent.microgrid_id == microgrid_id)
    return list((await session.execute(q)).scalars())
