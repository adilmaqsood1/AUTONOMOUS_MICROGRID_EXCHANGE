"""Scenario injection: scenarios genuinely change simulation inputs or physical asset state."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.core.enums import AssetStatus, AssetType, AuditEventType, MeterStatus, ScenarioType as S
from app.core.errors import NotFound, ValidationFailed
from app.models.assets import EnergyAsset
from app.models.core import GridLine, Microgrid
from app.models.meters import SmartMeter
from app.models.ops import ScenarioEvent
from app.resilience import service as resilience


PRESETS = {
    "A": {
        "key": "A", "name": "Normal Market",
        "description": "Baseline nominal market clearing with balanced solar and load",
        "type": None, "severity": 0.0, "duration_minutes": 0, "params": {}
    },
    "B": {
        "key": "B", "name": "Cloud Shock",
        "description": "Sudden 85% reduction in solar PV generation due to cloud cover",
        "type": S.CLOUD_COVER, "severity": 0.85, "duration_minutes": 60, "params": {}
    },
    "C": {
        "key": "C", "name": "Grid Congestion",
        "description": "Feeder line capacity derated by 60%, creating bottleneck between nodes",
        "type": S.LINE_CONGESTION, "severity": 0.6, "duration_minutes": 60, "params": {}
    },
    "D": {
        "key": "D", "name": "Grid Outage",
        "description": "Utility substation disconnect triggering island mode and load shedding",
        "type": S.GRID_OUTAGE, "severity": 1.0, "duration_minutes": 120, "params": {}
    },
    "E": {
        "key": "E", "name": "Delivery Failure",
        "description": "Generator trip during committed delivery window inducing balancing & penalties",
        "type": S.GENERATOR_FAILURE, "severity": 1.0, "duration_minutes": 60, "params": {}
    },
}


ALIAS_MAP = {
    "NORMAL_MARKET": "A",
    "CLOUD_SHOCK": "B",
    "GRID_CONGESTION": "C",
    "GRID_OUTAGE": "D",
    "DELIVERY_FAILURE": "E",
}


@dataclass
class Modifiers:
    cloud: float = 0.0
    wind_drop: float = 0.0
    demand_multiplier: float = 1.0
    price_multiplier: float = 1.0
    anomaly_factor: dict | None = None  # meter_id -> factor


async def inject_preset(session: AsyncSession, microgrid_id: uuid.UUID, preset_key: str, now: datetime,
                        actor: str = "operator") -> ScenarioEvent | None:
    normalized_key = preset_key.upper().strip()
    normalized_key = ALIAS_MAP.get(normalized_key, normalized_key)
    if normalized_key not in PRESETS:
        raise ValidationFailed(f"unknown preset {preset_key}, valid: {list(PRESETS.keys())} or {list(ALIAS_MAP.keys())}")
    cfg = PRESETS[normalized_key]
    if cfg["type"] is None:
        return None  # Normal operations
    return await inject(session, microgrid_id, cfg["type"], cfg["severity"], cfg["duration_minutes"],
                        now, cfg["params"], actor)


async def cancel(session: AsyncSession, scenario_id: uuid.UUID, now: datetime, actor: str = "operator") -> ScenarioEvent:
    ev = await session.get(ScenarioEvent, scenario_id)
    if not ev:
        raise NotFound("scenario not found")
    if ev.active:
        await _revert(session, ev, now)
        await session.flush()
        await audit.record(session, "SCENARIO_CANCELLED", "ScenarioEvent", ev.id, actor, {}, ev.microgrid_id)
    return ev


async def inject(session: AsyncSession, microgrid_id: uuid.UUID, stype: S, severity: float, duration_minutes: int | None,
                 now: datetime, params: dict | None = None, actor: str = "operator") -> ScenarioEvent:
    if not 0.0 <= severity <= 1.0:
        raise ValidationFailed("severity must be within [0, 1]")
    params = dict(params or {})
    mg = await session.get(Microgrid, microgrid_id)
    if not mg:
        raise NotFound("microgrid not found")
    instant = {S.GRID_OUTAGE, S.GRID_RESTORED, S.EV_FLEET_ARRIVAL}
    end = now + timedelta(minutes=duration_minutes) if duration_minutes else None
    if stype in instant and stype != S.GRID_OUTAGE:
        end = now
    ev = ScenarioEvent(microgrid_id=microgrid_id, type=stype.value, severity=severity, start_time=now, end_time=end,
                       params=params, active=True, created_at=now)
    if "asset_id" in params:
        ev.target_asset_id = uuid.UUID(str(params["asset_id"]))
    session.add(ev)
    await session.flush()
    await _apply_start(session, mg, ev, now, actor)
    await audit.record(session, AuditEventType.SCENARIO_INJECTED, "ScenarioEvent", ev.id, actor,
                       {"type": stype.value, "severity": severity, "duration_minutes": duration_minutes,
                        "params": ev.params}, microgrid_id)
    return ev


async def _first_asset(session, mg_id, types: set[str], explicit: uuid.UUID | None, prefer_community=False):
    if explicit:
        a = await session.get(EnergyAsset, explicit)
        if not a:
            raise NotFound("target asset not found")
        return a
    rows = (await session.execute(select(EnergyAsset).where(
        EnergyAsset.microgrid_id == mg_id, EnergyAsset.asset_type.in_(types),
        EnergyAsset.status == AssetStatus.ONLINE.value).order_by(EnergyAsset.name))).scalars().all()
    if prefer_community:
        for a in rows:
            if a.battery and a.battery.community:
                return a
    if not rows:
        raise NotFound("no suitable target asset for scenario")
    return rows[0]


async def _apply_start(session: AsyncSession, mg: Microgrid, ev: ScenarioEvent, now: datetime, actor: str) -> None:
    t = S(ev.type)
    if t == S.GRID_OUTAGE:
        await resilience.declare_outage(session, mg.id, now, actor)
    elif t == S.GRID_RESTORED:
        await resilience.begin_recovery(session, mg.id, now, actor)
        for o in (await session.execute(select(ScenarioEvent).where(
                ScenarioEvent.microgrid_id == mg.id, ScenarioEvent.type == S.GRID_OUTAGE.value,
                ScenarioEvent.active.is_(True)))).scalars():
            o.active = False
        ev.active = False
    elif t == S.BATTERY_FAILURE:
        a = await _first_asset(session, mg.id, {AssetType.BATTERY.value}, ev.target_asset_id, prefer_community=True)
        a.status = AssetStatus.FAILED.value
        ev.target_asset_id = a.id
    elif t == S.GENERATOR_FAILURE:
        a = await _first_asset(session, mg.id, {AssetType.SOLAR.value, AssetType.WIND.value, AssetType.SMALL_HYDRO.value},
                               ev.target_asset_id)
        a.status = AssetStatus.FAILED.value
        ev.target_asset_id = a.id
    elif t == S.LINE_CONGESTION:
        if ev.target_asset_id:
            line = await session.get(GridLine, ev.target_asset_id)
        else:
            line = (await session.execute(select(GridLine).where(GridLine.microgrid_id == mg.id).order_by(
                GridLine.capacity_kw, GridLine.id))).scalars().first()
        if not line:
            raise NotFound("no grid line")
        ev.params = {**ev.params, "line_id": str(line.id), "original_capacity_kw": line.capacity_kw}
        line.capacity_kw = line.capacity_kw * (1.0 - ev.severity)
        ev.target_asset_id = line.id
    elif t == S.EV_FLEET_ARRIVAL:
        evs = (await session.execute(select(EnergyAsset).where(
            EnergyAsset.microgrid_id == mg.id, EnergyAsset.asset_type == AssetType.EV_BATTERY.value
        ).order_by(EnergyAsset.name))).scalars().all()
        n = max(1, int(round(len(evs) * ev.severity)))
        arrived = []
        for a in [x for x in evs if not x.ev.connected][:n]:
            a.ev.connected = True
            a.ev.soc_percent = min(a.ev.soc_percent, 35.0)
            a.ev.departure_time = now + timedelta(hours=8)
            arrived.append(str(a.id))
        ev.params = {**ev.params, "arrived": arrived}
    elif t in (S.METER_FAILURE, S.METER_ANOMALY):
        if ev.target_asset_id:
            m = (await session.execute(select(SmartMeter).where(SmartMeter.asset_id == ev.target_asset_id))).scalars().first()
        else:
            gen = await _first_asset(session, mg.id, {AssetType.SOLAR.value, AssetType.WIND.value, AssetType.SMALL_HYDRO.value}, None)
            m = (await session.execute(select(SmartMeter).where(SmartMeter.asset_id == gen.id))).scalars().first()
        if not m:
            raise NotFound("meter not found")
        m.status = MeterStatus.FAILED.value if t == S.METER_FAILURE else MeterStatus.ANOMALOUS.value
        ev.params = {**ev.params, "meter_id": str(m.id)}
    await session.flush()


async def _revert(session: AsyncSession, ev: ScenarioEvent, now: datetime) -> None:
    t = S(ev.type)
    if t == S.GRID_OUTAGE:
        await resilience.begin_recovery(session, ev.microgrid_id, now, "scenario_timer")
    elif t in (S.BATTERY_FAILURE, S.GENERATOR_FAILURE) and ev.target_asset_id:
        a = await session.get(EnergyAsset, ev.target_asset_id)
        if a:
            a.status = AssetStatus.ONLINE.value
    elif t == S.LINE_CONGESTION and ev.target_asset_id:
        line = await session.get(GridLine, ev.target_asset_id)
        if line:
            line.capacity_kw = ev.params["original_capacity_kw"]
    elif t in (S.METER_FAILURE, S.METER_ANOMALY) and ev.params.get("meter_id"):
        m = await session.get(SmartMeter, uuid.UUID(ev.params["meter_id"]))
        if m:
            m.status = MeterStatus.ACTIVE.value
    ev.active = False


async def expire(session: AsyncSession, microgrid_id: uuid.UUID, now: datetime) -> int:
    n = 0
    for ev in (await session.execute(select(ScenarioEvent).where(
            ScenarioEvent.microgrid_id == microgrid_id, ScenarioEvent.active.is_(True),
            ScenarioEvent.end_time.is_not(None), ScenarioEvent.end_time <= now))).scalars():
        await _revert(session, ev, now)
        n += 1
    await session.flush()
    return n


async def active_modifiers(session: AsyncSession, microgrid_id: uuid.UUID, t0: datetime, t1: datetime) -> Modifiers:
    m = Modifiers(anomaly_factor={})
    for ev in (await session.execute(select(ScenarioEvent).where(
            ScenarioEvent.microgrid_id == microgrid_id, ScenarioEvent.active.is_(True),
            ScenarioEvent.start_time < t1))).scalars():
        if ev.end_time is not None and ev.end_time <= t0:
            continue
        t = S(ev.type)
        if t == S.CLOUD_COVER:
            m.cloud = min(1.0, m.cloud + ev.severity)
        elif t == S.WIND_DROP:
            m.wind_drop = min(1.0, m.wind_drop + ev.severity)
        elif t == S.DEMAND_SPIKE:
            m.demand_multiplier *= 1.0 + ev.severity
        elif t == S.PRICE_SPIKE:
            m.price_multiplier *= 1.0 + 3.0 * ev.severity
        elif t == S.METER_ANOMALY and ev.params.get("meter_id"):
            m.anomaly_factor[ev.params["meter_id"]] = 1.0 + ev.severity
    return m
