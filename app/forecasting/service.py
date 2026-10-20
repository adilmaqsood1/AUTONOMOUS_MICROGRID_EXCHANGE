"""Forecast persistence: generate, store, retrieve and score forecasts."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AssetType
from app.forecasting import models as fm
from app.models.assets import EnergyAsset
from app.models.meters import MeterReading
from app.models.ops import Forecast
from app.simulation import signals
from app.simulation.state import SimConfig
from app.websocket.hub import queue_event


def _forecaster_for(asset: EnergyAsset, cfg: SimConfig) -> fm.GenerationForecaster:
    if asset.asset_type == AssetType.SOLAR.value:
        return fm.WeatherAdjustedSolarForecaster(cfg.seed, cfg.tz_offset_hours)
    if asset.asset_type == AssetType.WIND.value:
        return fm.WindForecaster()
    return fm.RollingAverageForecaster(cfg.tz_offset_hours)


async def history(session: AsyncSession, asset_id: uuid.UUID, before: datetime, field: str = "generation_kwh",
                  limit: int = 24 * 14) -> list[fm.Observation]:
    q = select(MeterReading).where(MeterReading.asset_id == asset_id, MeterReading.timestamp <= before) \
        .order_by(MeterReading.timestamp.desc()).limit(limit * 4)
    rows = list((await session.execute(q)).scalars())[::-1]
    # aggregate to hourly energy (sum of sub-hour intervals), keyed by interval start
    hourly: dict[datetime, float] = {}
    for r in rows:
        start = r.timestamp - timedelta(minutes=r.interval_minutes)
        h = start.replace(minute=0, second=0, microsecond=0)
        hourly[h] = hourly.get(h, 0.0) + getattr(r, field)
    return [fm.Observation(t, v) for t, v in sorted(hourly.items())][-limit:]


async def generation_forecast(session: AsyncSession, asset: EnergyAsset, window_start: datetime,
                              window_end: datetime, now: datetime, cfg: SimConfig,
                              persist: bool = True) -> Forecast:
    """Latest stored forecast for this asset/window made at or before `now`; generate if absent."""
    q = select(Forecast).where(Forecast.asset_id == asset.id, Forecast.kind == "GENERATION",
                               Forecast.target_time == window_start, Forecast.generated_at <= now) \
        .order_by(Forecast.generated_at.desc()).limit(1)
    existing = (await session.execute(q)).scalars().first()
    if existing and existing.interval_minutes == int((window_end - window_start).total_seconds() // 60):
        return existing
    minutes = int((window_end - window_start).total_seconds() // 60)
    hist = await history(session, asset.id, now)
    cap = (asset.generator.maximum_generation_kw if asset.generator else asset.rated_power_kw) * minutes / 60.0
    pt = _forecaster_for(asset, cfg).forecast(hist, [window_start], minutes, now,
                                               rated_kw=asset.rated_power_kw, cap_kwh=cap)[0]
    f = Forecast(microgrid_id=asset.microgrid_id, asset_id=asset.id, kind="GENERATION", target_time=window_start,
                 interval_minutes=minutes, horizon_minutes=pt.horizon_minutes, predicted_value=pt.predicted_value,
                 lower_bound=pt.lower_bound, upper_bound=pt.upper_bound, confidence=pt.confidence,
                 model_name=pt.model_name, generated_at=now)
    if persist:
        session.add(f)
        await session.flush()
        queue_event(session, "FORECAST_UPDATE", asset.microgrid_id,
                    {"asset_id": asset.id, "kind": "GENERATION", "target_time": window_start,
                     "predicted_value": f.predicted_value, "lower_bound": f.lower_bound,
                     "upper_bound": f.upper_bound, "confidence": f.confidence, "model_name": f.model_name})
    return f


async def demand_forecast(session: AsyncSession, asset: EnergyAsset, window_start: datetime, window_end: datetime,
                          now: datetime, cfg: SimConfig, persist: bool = True) -> Forecast:
    q = select(Forecast).where(Forecast.asset_id == asset.id, Forecast.kind == "DEMAND",
                               Forecast.target_time == window_start, Forecast.generated_at <= now) \
        .order_by(Forecast.generated_at.desc()).limit(1)
    existing = (await session.execute(q)).scalars().first()
    if existing:
        return existing
    minutes = int((window_end - window_start).total_seconds() // 60)
    hist = await history(session, asset.id, now, "consumption_kwh")
    base = asset.load.base_demand_kw if asset.load else asset.rated_power_kw
    cap = (asset.rated_power_kw * 1.5) * minutes / 60.0
    pt = fm.RegressionDemandForecaster(cfg.tz_offset_hours).forecast(hist, [window_start], minutes, now, cap_kwh=cap)[0]
    if len(hist) < 12:  # cold start: prior from the demand model itself (documented fallback)
        shape = (signals.household_demand_multiplier(cfg.seed, window_start, cfg.tz_offset_hours)
                 if (asset.load and asset.load.load_kind == "RESIDENTIAL")
                 else signals.commercial_demand_multiplier(cfg.seed, window_start, cfg.tz_offset_hours)
                 if (asset.load and asset.load.load_kind == "COMMERCIAL")
                 else signals.constant_demand_multiplier(cfg.seed, window_start))
        mean = base * shape * minutes / 60.0
        sd = mean * 0.15
        pt = fm.ForecastPoint(window_start, minutes, mean, max(0.0, mean - 1.645 * sd), mean + 1.645 * sd, 0.7,
                              max(int((window_start - now).total_seconds() // 60), 0), "demand_profile_prior", now)
    f = Forecast(microgrid_id=asset.microgrid_id, asset_id=asset.id, kind="DEMAND", target_time=window_start,
                 interval_minutes=minutes, horizon_minutes=pt.horizon_minutes, predicted_value=pt.predicted_value,
                 lower_bound=pt.lower_bound, upper_bound=pt.upper_bound, confidence=pt.confidence,
                 model_name=pt.model_name, generated_at=now)
    if persist:
        session.add(f)
        await session.flush()
    return f


async def price_forecast(session: AsyncSession, microgrid_id: uuid.UUID, targets: list[datetime], interval_minutes: int,
                         now: datetime, cfg: SimConfig, persist: bool = False) -> list[Forecast]:
    pts = fm.TariffPriceForecaster(cfg.tz_offset_hours).forecast([], targets, interval_minutes, now)
    out = []
    for pt in pts:
        f = Forecast(microgrid_id=microgrid_id, asset_id=None, kind="PRICE", target_time=pt.target_time,
                     interval_minutes=interval_minutes, horizon_minutes=pt.horizon_minutes,
                     predicted_value=pt.predicted_value, lower_bound=pt.lower_bound, upper_bound=pt.upper_bound,
                     confidence=pt.confidence, model_name=pt.model_name, generated_at=now)
        if persist:
            session.add(f)
        out.append(f)
    if persist:
        await session.flush()
    return out


async def record_actuals(session: AsyncSession, microgrid_id: uuid.UUID, now: datetime) -> int:
    """Fill actual_value for GENERATION/DEMAND forecasts whose interval has fully elapsed."""
    q = select(Forecast).where(Forecast.microgrid_id == microgrid_id, Forecast.actual_value.is_(None),
                               Forecast.kind.in_(["GENERATION", "DEMAND"]), Forecast.asset_id.is_not(None))
    n = 0
    for f in (await session.execute(q)).scalars():
        end = f.target_time + timedelta(minutes=f.interval_minutes)
        if end > now:
            continue
        rows = (await session.execute(select(MeterReading).where(
            MeterReading.asset_id == f.asset_id, MeterReading.timestamp > f.target_time,
            MeterReading.timestamp <= end))).scalars().all()
        if sum(r.interval_minutes for r in rows) < f.interval_minutes:
            continue
        field = "generation_kwh" if f.kind == "GENERATION" else "consumption_kwh"
        f.actual_value = sum(getattr(r, field) for r in rows)
        n += 1
    await session.flush()
    return n


def evaluation(rows: list[Forecast]) -> dict:
    done = [f for f in rows if f.actual_value is not None]
    if not done:
        return {"count": 0}
    pts = [fm.ForecastPoint(f.target_time, f.interval_minutes, f.predicted_value, f.lower_bound, f.upper_bound,
                            f.confidence, f.horizon_minutes, f.model_name, f.generated_at) for f in done]
    return {"count": len(done), **fm.evaluate(pts, [f.actual_value for f in done])}
