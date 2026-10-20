"""Energy generation and demand forecasting routers."""
import uuid
from typing import List, Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, reader
from app.forecasting import service as forecast_service
from app.models.assets import EnergyAsset
from app.models.ops import Forecast
from app.schemas.schemas import ForecastResponse
from app.simulation.state import get_config

router = APIRouter(prefix="/forecasts", tags=["Forecasting"])


@router.get("", response_model=List[ForecastResponse], dependencies=[Depends(reader)])
async def list_forecasts(microgrid_id: Optional[uuid.UUID] = None,
                         target_id: Optional[uuid.UUID] = None,
                         limit: int = 100,
                         session: AsyncSession = SessionDep):
    q = select(Forecast)
    if microgrid_id:
        q = q.where(Forecast.microgrid_id == microgrid_id)
    if target_id:
        q = q.where(Forecast.target_id == target_id)
    return (await session.execute(q.order_by(Forecast.window_start.desc()).limit(min(limit, 500)))).scalars().all()


@router.get("/generation/{asset_id}", response_model=ForecastResponse, dependencies=[Depends(reader)])
async def get_generation_forecast(asset_id: uuid.UUID,
                                  window_start: datetime,
                                  window_end: datetime,
                                  session: AsyncSession = SessionDep):
    asset = await session.get(EnergyAsset, asset_id)
    if not asset:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    cfg = await get_config(session, asset.microgrid_id)
    fc = await forecast_service.generation_forecast(session, asset, window_start, window_end, cfg.sim_time, cfg)
    await session.commit()
    return fc


@router.get("/demand/{asset_id}", response_model=ForecastResponse, dependencies=[Depends(reader)])
async def get_demand_forecast(asset_id: uuid.UUID,
                              window_start: datetime,
                              window_end: datetime,
                              session: AsyncSession = SessionDep):
    asset = await session.get(EnergyAsset, asset_id)
    if not asset:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    cfg = await get_config(session, asset.microgrid_id)
    fc = await forecast_service.demand_forecast(session, asset, window_start, window_end, cfg.sim_time, cfg)
    await session.commit()
    return fc
