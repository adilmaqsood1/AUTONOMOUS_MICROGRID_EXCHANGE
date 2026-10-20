"""Smart meters, meter readings intake, and corrections."""
import uuid
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, SessionDep, current_principal, operator, reader
from app.meters import service as meter_service
from app.models.meters import MeterCorrection, MeterReading, SmartMeter
from app.schemas.schemas import (
    MeterCorrectionCreate, MeterCorrectionResponse, MeterReadingIngest,
    MeterReadingResponse, SmartMeterResponse
)

router = APIRouter(tags=["Smart Meters"])


@router.get("/meters", response_model=List[SmartMeterResponse], dependencies=[Depends(reader)])
async def list_meters(asset_id: Optional[uuid.UUID] = None, session: AsyncSession = SessionDep):
    q = select(SmartMeter)
    if asset_id:
        q = q.where(SmartMeter.asset_id == asset_id)
    return (await session.execute(q.order_by(SmartMeter.meter_identifier))).scalars().all()


@router.get("/meters/{meter_id}", response_model=SmartMeterResponse, dependencies=[Depends(reader)])
async def get_meter(meter_id: uuid.UUID, session: AsyncSession = SessionDep):
    m = await session.get(SmartMeter, meter_id)
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "meter not found")
    return m


@router.get("/meter-readings", response_model=List[MeterReadingResponse], dependencies=[Depends(reader)])
async def list_readings(microgrid_id: Optional[uuid.UUID] = None,
                        asset_id: Optional[uuid.UUID] = None,
                        meter_id: Optional[uuid.UUID] = None,
                        since: Optional[datetime] = None,
                        limit: int = 100,
                        session: AsyncSession = SessionDep):
    q = select(MeterReading)
    if microgrid_id:
        q = q.where(MeterReading.microgrid_id == microgrid_id)
    if asset_id:
        q = q.where(MeterReading.asset_id == asset_id)
    if meter_id:
        q = q.where(MeterReading.meter_id == meter_id)
    if since:
        q = q.where(MeterReading.timestamp >= since)
    q = q.order_by(MeterReading.timestamp.desc()).limit(min(limit, 500))
    return (await session.execute(q)).scalars().all()


@router.post("/meter-readings/ingest", response_model=MeterReadingResponse)
async def ingest_meter_reading(req: MeterReadingIngest,
                               principal: Principal = Depends(current_principal),
                               session: AsyncSession = SessionDep):
    values = {
        "generation_kwh": req.generation_kwh,
        "consumption_kwh": req.consumption_kwh,
        "import_kwh": req.import_kwh,
        "export_kwh": req.export_kwh,
        "battery_charge_kwh": req.battery_charge_kwh,
        "battery_discharge_kwh": req.battery_discharge_kwh,
    }
    meter = (await session.execute(
        select(SmartMeter).where(SmartMeter.meter_identifier == req.meter_identifier)
    )).scalar_one_or_none()
    if not meter:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"meter '{req.meter_identifier}' not found")

    reading = await meter_service.ingest_reading(
        session,
        meter,
        req.timestamp,
        values,
        signature=req.signature,
        interval_minutes=req.interval_minutes,
        actor=principal.actor,
        extra_meta=req.extra_meta,
    )
    await session.commit()
    await session.refresh(reading)
    return reading


@router.post("/meter-readings/corrections", response_model=MeterCorrectionResponse, dependencies=[Depends(operator)])
async def correct_meter_reading(req: MeterCorrectionCreate,
                                principal: Principal = Depends(current_principal),
                                session: AsyncSession = SessionDep):
    correction = await meter_service.correct_reading(
        session, req.reading_id, req.field, req.new_value, req.reason, actor=principal.actor
    )
    await session.commit()
    await session.refresh(correction)
    return correction
