"""Market Surveillance & Manipulation Detection Router (Phase 2)."""
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, SessionDep, current_principal, operator, reader
from app.schemas.schemas import (
    SurveillanceAlertResponse, SurveillanceAlertStatusUpdate, SurveillanceReportResponse
)
from app.surveillance import service as surveillance_service

router = APIRouter(prefix="/surveillance", tags=["Market Surveillance & Manipulation"])


@router.post("/evaluate/{microgrid_id}", response_model=List[SurveillanceAlertResponse], dependencies=[Depends(operator)])
async def evaluate_market_surveillance(
    microgrid_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = SessionDep,
):
    alerts = await surveillance_service.run_surveillance_cycle(
        session, microgrid_id=microgrid_id, actor=principal.actor
    )
    await session.commit()
    for a in alerts:
        await session.refresh(a)
    return alerts


@router.get("/alerts", response_model=List[SurveillanceAlertResponse], dependencies=[Depends(reader)])
async def list_surveillance_alerts(
    microgrid_id: Optional[uuid.UUID] = None,
    alert_type: Optional[str] = None,
    severity: Optional[str] = None,
    status_filter: Optional[str] = None,
    limit: int = 100,
    session: AsyncSession = SessionDep,
):
    return await surveillance_service.list_surveillance_alerts(
        session,
        microgrid_id=microgrid_id,
        alert_type=alert_type,
        severity=severity,
        status=status_filter,
        limit=limit,
    )


@router.patch("/alerts/{alert_id}", response_model=SurveillanceAlertResponse, dependencies=[Depends(operator)])
async def update_surveillance_alert_status(
    alert_id: uuid.UUID,
    req: SurveillanceAlertStatusUpdate,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = SessionDep,
):
    alert = await surveillance_service.update_alert_status(
        session, alert_id=alert_id, new_status=req.status, actor=principal.actor
    )
    await session.commit()
    await session.refresh(alert)
    return alert


@router.get("/report/{microgrid_id}", response_model=SurveillanceReportResponse, dependencies=[Depends(reader)])
async def get_surveillance_report(
    microgrid_id: uuid.UUID,
    session: AsyncSession = SessionDep,
):
    alerts = await surveillance_service.list_surveillance_alerts(
        session, microgrid_id=microgrid_id, limit=200
    )
    high_risk_parts = set()
    for a in alerts:
        if a.severity in ("HIGH", "CRITICAL") and a.participant_id:
            high_risk_parts.add(str(a.participant_id))

    from app.core.timeutil import utcnow
    return {
        "microgrid_id": microgrid_id,
        "evaluated_at": utcnow(),
        "total_alerts": len(alerts),
        "high_risk_participants": sorted(list(high_risk_parts)),
        "alerts": alerts,
    }
