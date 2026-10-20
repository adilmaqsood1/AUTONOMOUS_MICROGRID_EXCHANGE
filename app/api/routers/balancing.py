"""Real-time balancing actions and demand response events."""
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, SessionDep, current_principal, operator, reader
from app.balancing import service as balancing_service
from app.models.ops import BalancingAction, DemandResponseEvent
from app.schemas.schemas import (
    BalancingActionResponse, BalancingTrigger, DemandResponseEventResponse
)

balancing_router = APIRouter(prefix="/balancing", tags=["Balancing Market"])
dr_router = APIRouter(prefix="/demand-response", tags=["Demand Response"])


# --- Balancing
@balancing_router.get("", response_model=List[BalancingActionResponse], dependencies=[Depends(reader)])
async def list_balancing_actions(microgrid_id: Optional[uuid.UUID] = None,
                                 limit: int = 100,
                                 session: AsyncSession = SessionDep):
    q = select(BalancingAction)
    if microgrid_id:
        q = q.where(BalancingAction.microgrid_id == microgrid_id)
    return (await session.execute(q.order_by(BalancingAction.timestamp.desc()).limit(min(limit, 500)))).scalars().all()


@balancing_router.post("/balance", response_model=List[BalancingActionResponse], dependencies=[Depends(operator)])
async def trigger_balancing_cycle(req: BalancingTrigger,
                                  principal: Principal = Depends(current_principal),
                                  session: AsyncSession = SessionDep):
    actions = await balancing_service.balance_window(session, req.microgrid_id, req.window_end, actor=principal.actor)
    await session.commit()
    return actions


# --- Demand Response
@dr_router.get("", response_model=List[DemandResponseEventResponse], dependencies=[Depends(reader)])
async def list_dr_events(microgrid_id: Optional[uuid.UUID] = None,
                         limit: int = 100,
                         session: AsyncSession = SessionDep):
    q = select(DemandResponseEvent)
    if microgrid_id:
        q = q.where(DemandResponseEvent.microgrid_id == microgrid_id)
    return (await session.execute(q.order_by(DemandResponseEvent.start_time.desc()).limit(min(limit, 500)))).scalars().all()
