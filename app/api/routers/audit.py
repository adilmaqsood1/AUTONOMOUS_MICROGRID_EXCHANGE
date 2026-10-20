"""Audit trail and System analytics routers."""
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import service as analytics_service
from app.api.deps import SessionDep, auditor, reader
from app.audit import service as audit_service
from app.models.ops import AuditEvent
from app.schemas.schemas import AuditEventResponse

audit_router = APIRouter(prefix="/audit", tags=["Audit Trail"])
analytics_router = APIRouter(prefix="/analytics", tags=["System Analytics"])


# --- Audit
@audit_router.get("/events", response_model=List[AuditEventResponse], dependencies=[Depends(auditor)])
async def list_audit_events(microgrid_id: Optional[uuid.UUID] = None,
                            entity_type: Optional[str] = None,
                            event_type: Optional[str] = None,
                            limit: int = 100,
                            session: AsyncSession = SessionDep):
    rows = await audit_service.list_events(
        session, microgrid_id=microgrid_id, entity_type=entity_type,
        event_type=event_type, limit=min(limit, 500)
    )
    return rows


# --- Analytics
@analytics_router.get("/summary/{microgrid_id}", response_model=Dict[str, Any], dependencies=[Depends(reader)])
async def get_analytics_summary(microgrid_id: uuid.UUID,
                                since: Optional[datetime] = None,
                                until: Optional[datetime] = None,
                                session: AsyncSession = SessionDep):
    summary = await analytics_service.compute(session, microgrid_id, since=since, until=until)
    return summary


from app.schemas.schemas import LiveControlRoomTelemetry


@analytics_router.get("/control-room/{microgrid_id}", response_model=LiveControlRoomTelemetry, dependencies=[Depends(reader)])
async def get_control_room_status(microgrid_id: uuid.UUID, session: AsyncSession = SessionDep):
    return await analytics_service.get_control_room_telemetry(session, microgrid_id)

