"""Microgrid and participant management routers."""
import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, SessionDep, assert_can_act_for, current_principal, operator, reader
from app.models.core import Microgrid, Participant
from app.schemas.schemas import (
    MicrogridResponse, MicrogridUpdate, ParticipantResponse, ParticipantUpdatePreferences
)

microgrid_router = APIRouter(prefix="/microgrids", tags=["Microgrids"])
participant_router = APIRouter(prefix="/participants", tags=["Participants"])


# --- Microgrids
@microgrid_router.get("", response_model=List[MicrogridResponse], dependencies=[Depends(reader)])
async def list_microgrids(session: AsyncSession = SessionDep):
    rows = (await session.execute(select(Microgrid).order_by(Microgrid.name))).scalars().all()
    return rows


@microgrid_router.get("/{microgrid_id}", response_model=MicrogridResponse, dependencies=[Depends(reader)])
async def get_microgrid(microgrid_id: uuid.UUID, session: AsyncSession = SessionDep):
    mg = await session.get(Microgrid, microgrid_id)
    if not mg:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "microgrid not found")
    return mg


@microgrid_router.patch("/{microgrid_id}", response_model=MicrogridResponse, dependencies=[Depends(operator)])
async def update_microgrid(microgrid_id: uuid.UUID, req: MicrogridUpdate, session: AsyncSession = SessionDep):
    mg = await session.get(Microgrid, microgrid_id)
    if not mg:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "microgrid not found")
    if req.operating_mode is not None:
        mg.operating_mode = req.operating_mode
    if req.max_grid_import_kw is not None:
        mg.max_grid_import_kw = req.max_grid_import_kw
    if req.max_grid_export_kw is not None:
        mg.max_grid_export_kw = req.max_grid_export_kw
    if req.external_grid_connected is not None:
        mg.external_grid_connected = req.external_grid_connected
    await session.commit()
    await session.refresh(mg)
    return mg


# --- Participants
@participant_router.get("", response_model=List[ParticipantResponse], dependencies=[Depends(reader)])
async def list_participants(microgrid_id: uuid.UUID | None = None, session: AsyncSession = SessionDep):
    q = select(Participant)
    if microgrid_id:
        q = q.where(Participant.microgrid_id == microgrid_id)
    rows = (await session.execute(q.order_by(Participant.name))).scalars().all()
    return rows


@participant_router.get("/{participant_id}", response_model=ParticipantResponse, dependencies=[Depends(reader)])
async def get_participant(participant_id: uuid.UUID, session: AsyncSession = SessionDep):
    p = await session.get(Participant, participant_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "participant not found")
    return p


@participant_router.patch("/{participant_id}/preferences", response_model=ParticipantResponse)
async def update_preferences(participant_id: uuid.UUID, req: ParticipantUpdatePreferences,
                             principal: Principal = Depends(current_principal),
                             session: AsyncSession = SessionDep):
    assert_can_act_for(principal, participant_id)
    p = await session.get(Participant, participant_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "participant not found")
    p.preferences = {**p.preferences, **req.preferences}
    await session.commit()
    await session.refresh(p)
    return p
