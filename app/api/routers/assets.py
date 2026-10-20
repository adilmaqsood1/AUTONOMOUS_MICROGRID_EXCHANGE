"""Assets, batteries, and EV devices routers."""
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, SessionDep, assert_can_act_for, current_principal, reader
from app.core.enums import AssetType
from app.models.assets import BatteryProps, EnergyAsset, EVProps
from app.schemas.schemas import AssetResponse, BatteryPropsSchema, EVPropsSchema

router = APIRouter(tags=["Assets"])
battery_router = APIRouter(prefix="/batteries", tags=["Batteries"])
ev_router = APIRouter(prefix="/evs", tags=["Electric Vehicles"])


@router.get("/assets", response_model=List[AssetResponse], dependencies=[Depends(reader)])
async def list_assets(microgrid_id: Optional[uuid.UUID] = None,
                      participant_id: Optional[uuid.UUID] = None,
                      asset_type: Optional[str] = None,
                      session: AsyncSession = SessionDep):
    q = select(EnergyAsset)
    if microgrid_id:
        q = q.where(EnergyAsset.microgrid_id == microgrid_id)
    if participant_id:
        q = q.where(EnergyAsset.participant_id == participant_id)
    if asset_type:
        q = q.where(EnergyAsset.asset_type == asset_type)
    rows = (await session.execute(q.order_by(EnergyAsset.name))).scalars().all()
    return rows


@router.get("/assets/{asset_id}", response_model=AssetResponse, dependencies=[Depends(reader)])
async def get_asset(asset_id: uuid.UUID, session: AsyncSession = SessionDep):
    a = await session.get(EnergyAsset, asset_id)
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    return a


# --- Batteries
@battery_router.get("", response_model=List[AssetResponse], dependencies=[Depends(reader)])
async def list_batteries(microgrid_id: Optional[uuid.UUID] = None, session: AsyncSession = SessionDep):
    q = select(EnergyAsset).where(EnergyAsset.asset_type == AssetType.BATTERY.value)
    if microgrid_id:
        q = q.where(EnergyAsset.microgrid_id == microgrid_id)
    rows = (await session.execute(q.order_by(EnergyAsset.name))).scalars().all()
    return rows


@battery_router.get("/{battery_id}", response_model=AssetResponse, dependencies=[Depends(reader)])
async def get_battery(battery_id: uuid.UUID, session: AsyncSession = SessionDep):
    a = await session.get(EnergyAsset, battery_id)
    if not a or not a.battery:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "battery asset not found")
    return a


# --- Electric Vehicles
@ev_router.get("", response_model=List[AssetResponse], dependencies=[Depends(reader)])
async def list_evs(microgrid_id: Optional[uuid.UUID] = None, session: AsyncSession = SessionDep):
    q = select(EnergyAsset).where(EnergyAsset.asset_type == AssetType.EV_BATTERY.value)
    if microgrid_id:
        q = q.where(EnergyAsset.microgrid_id == microgrid_id)
    rows = (await session.execute(q.order_by(EnergyAsset.name))).scalars().all()
    return rows


@ev_router.get("/{ev_id}", response_model=AssetResponse, dependencies=[Depends(reader)])
async def get_ev(ev_id: uuid.UUID, session: AsyncSession = SessionDep):
    a = await session.get(EnergyAsset, ev_id)
    if not a or not a.ev:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "EV asset not found")
    return a
