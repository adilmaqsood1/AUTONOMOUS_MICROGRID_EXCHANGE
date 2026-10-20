"""Resilience and island-mode operations router."""
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, SessionDep, current_principal, operator, reader
from app.models.core import Microgrid
from app.resilience import service as resilience_service
from app.schemas.schemas import OutageDeclare, ResilienceStatus
from app.simulation.state import get_config

router = APIRouter(prefix="/resilience", tags=["Resilience"])


@router.get("/status/{microgrid_id}", response_model=ResilienceStatus, dependencies=[Depends(reader)])
async def get_resilience_status(microgrid_id: uuid.UUID, session: AsyncSession = SessionDep):
    mg = await session.get(Microgrid, microgrid_id)
    if not mg:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "microgrid not found")
    return ResilienceStatus(
        operating_mode=mg.operating_mode,
        external_grid_connected=mg.external_grid_connected,
        max_grid_import_kw=mg.max_grid_import_kw,
        max_grid_export_kw=mg.max_grid_export_kw
    )


@router.post("/outage/{microgrid_id}", response_model=ResilienceStatus, dependencies=[Depends(operator)])
async def trigger_grid_outage(microgrid_id: uuid.UUID,
                              req: OutageDeclare,
                              principal: Principal = Depends(current_principal),
                              session: AsyncSession = SessionDep):
    cfg = await get_config(session, microgrid_id)
    mg = await resilience_service.declare_outage(session, microgrid_id, cfg.sim_time, actor=principal.actor)
    await session.commit()
    await session.refresh(mg)
    return ResilienceStatus(
        operating_mode=mg.operating_mode,
        external_grid_connected=mg.external_grid_connected,
        max_grid_import_kw=mg.max_grid_import_kw,
        max_grid_export_kw=mg.max_grid_export_kw
    )


@router.post("/recovery/begin/{microgrid_id}", response_model=ResilienceStatus, dependencies=[Depends(operator)])
async def trigger_recovery_begin(microgrid_id: uuid.UUID,
                                 principal: Principal = Depends(current_principal),
                                 session: AsyncSession = SessionDep):
    cfg = await get_config(session, microgrid_id)
    mg = await resilience_service.begin_recovery(session, microgrid_id, cfg.sim_time, actor=principal.actor)
    await session.commit()
    await session.refresh(mg)
    return ResilienceStatus(
        operating_mode=mg.operating_mode,
        external_grid_connected=mg.external_grid_connected,
        max_grid_import_kw=mg.max_grid_import_kw,
        max_grid_export_kw=mg.max_grid_export_kw
    )


@router.post("/recovery/complete/{microgrid_id}", response_model=ResilienceStatus, dependencies=[Depends(operator)])
async def trigger_recovery_complete(microgrid_id: uuid.UUID,
                                    principal: Principal = Depends(current_principal),
                                    session: AsyncSession = SessionDep):
    cfg = await get_config(session, microgrid_id)
    mg = await session.get(Microgrid, microgrid_id)
    if not mg:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "microgrid not found")
    await resilience_service.complete_recovery(session, mg, cfg.sim_time, actor=principal.actor)
    await session.commit()
    await session.refresh(mg)
    return ResilienceStatus(
        operating_mode=mg.operating_mode,
        external_grid_connected=mg.external_grid_connected,
        max_grid_import_kw=mg.max_grid_import_kw,
        max_grid_export_kw=mg.max_grid_export_kw
    )
