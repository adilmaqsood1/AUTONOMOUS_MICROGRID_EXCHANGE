"""Seed: "GreenGrid District" demo microgrid (25 homes, 8 rooftop PV, 2 commercial, 2 wind, 1 hydro,
6 home batteries, 1 community battery, 10 EVs, 1 hospital, 1 utility tie) plus bulk historical meter data.

Run:  python -m app.db.seed          (needs MGX_JWT_SECRET; admin password from MGX_ADMIN_PASSWORD or generated)
History readings are bulk-imported (hourly, deterministic from the seeded signal models) so baseline
forecasters have data; they bypass per-reading audit events by design (documented bulk import).
"""
from __future__ import annotations

import asyncio
import hmac
import hashlib
import os
import secrets
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.core.config import get_settings
from app.core.enums import AssetStatus, AssetType, LoadPriority, NodeType, ParticipantType, UserRole
from app.core.security import hash_password
from app.core.timeutil import utcnow
from app.models import (BatteryProps, EnergyAsset, EVProps, GeneratorProps, GridLine, GridNode, LoadProps, MeterReading,
                        Microgrid, Participant, SmartMeter, User)
from app.settlement.ledger import Ledger
from app.simulation import signals
from app.simulation.state import DEFAULT_START, get_state

SEED = 42
TZ = 5.0


def meter_key(identifier: str) -> str:
    return hmac.new(get_settings().require_secret().encode(), identifier.encode(), hashlib.sha256).hexdigest()


async def seed_demo(session: AsyncSession, admin_password: str, history_hours: int = 72,
                    name: str = "GreenGrid District") -> dict:
    existing = (await session.execute(select(Microgrid).where(Microgrid.name == name))).scalar_one_or_none()
    if existing:
        return {"microgrid_id": existing.id, "already_seeded": True}
    mg = Microgrid(name=name, location_label="Lahore, PK (demo)", max_grid_import_kw=400.0, max_grid_export_kw=200.0)
    session.add(mg)
    await session.flush()

    # ---------------------------------------------------------------- topology
    node_defs = [("GRID_TIE", NodeType.GRID_TIE, 11.0), ("SUBSTATION", NodeType.BUS, 11.0),
                 ("RES_A", NodeType.LOAD, 0.4), ("RES_B", NodeType.LOAD, 0.4), ("COMMERCIAL", NodeType.LOAD, 0.4),
                 ("HOSPITAL", NodeType.LOAD, 0.4), ("WIND_FARM", NodeType.GENERATION, 11.0),
                 ("HYDRO", NodeType.GENERATION, 11.0), ("STORAGE", NodeType.STORAGE, 11.0)]
    nodes: dict[str, GridNode] = {}
    for n, t, v in node_defs:
        nodes[n] = GridNode(microgrid_id=mg.id, name=n, voltage_level=v, node_type=t.value)
        session.add(nodes[n])
    await session.flush()
    # RES_B feeder (30 kW) is the deliberate bottleneck for the congestion scenario
    for a, b, cap in [("GRID_TIE", "SUBSTATION", 400), ("WIND_FARM", "SUBSTATION", 150), ("HYDRO", "SUBSTATION", 100),
                      ("SUBSTATION", "RES_A", 120), ("SUBSTATION", "RES_B", 30), ("SUBSTATION", "COMMERCIAL", 150),
                      ("SUBSTATION", "HOSPITAL", 60), ("SUBSTATION", "STORAGE", 100), ("RES_A", "RES_B", 15)]:
        session.add(GridLine(microgrid_id=mg.id, from_node_id=nodes[a].id, to_node_id=nodes[b].id, capacity_kw=float(cap)))

    parts: dict[str, Participant] = {}
    assets: dict[str, EnergyAsset] = {}
    meters: list[SmartMeter] = []

    def participant(key: str, ptype: ParticipantType, rel=0.85, prefs=None) -> Participant:
        p = Participant(microgrid_id=mg.id, name=key, type=ptype.value, wallet_id=f"0x{uuid.uuid5(uuid.NAMESPACE_DNS, key).hex}",
                        reliability_score=rel, preferences=prefs or {})
        session.add(p)
        parts[key] = p
        return p

    def asset(key: str, p: Participant, node: str, atype: AssetType, rated: float) -> EnergyAsset:
        a = EnergyAsset(id=uuid.uuid4(), participant_id=p.id, microgrid_id=mg.id, grid_node_id=nodes[node].id,
                        asset_type=atype.value, name=key, status=AssetStatus.ONLINE.value, rated_power_kw=rated)
        session.add(a)
        assets[key] = a
        return a

    # create participants first (need ids)
    for i in range(1, 9):
        participant(f"Prosumer-{i}", ParticipantType.PROSUMER, prefs={"renewable_only": False})
    for i in range(9, 26):
        participant(f"Consumer-{i}", ParticipantType.CONSUMER)
    participant("Commercial-1", ParticipantType.COMMERCIAL)
    participant("Commercial-2", ParticipantType.COMMERCIAL)
    participant("WindOperator", ParticipantType.GENERATOR, 0.9)
    participant("HydroOperator", ParticipantType.GENERATOR, 0.95)
    participant("CommunityStorageOperator", ParticipantType.STORAGE_OPERATOR, 0.95)
    for i in range(1, 11):
        participant(f"EVOwner-{i}", ParticipantType.EV_OWNER)
    participant("CityHospital", ParticipantType.CRITICAL_LOAD, 0.99)
    participant("UtilityOperator", ParticipantType.GRID_OPERATOR, 1.0)
    await session.flush()

    # ---------------------------------------------------------------- assets
    kinds = []
    for i in range(1, 26):
        owner = parts[f"Prosumer-{i}"] if i <= 8 else parts[f"Consumer-{i}"]
        node = "RES_A" if i <= 15 else "RES_B"
        a = asset(f"Home-{i:02d}", owner, node, AssetType.LOAD, 4.0)
        base = 1.0 + 0.04 * i
        flex = i % 3 == 0
        session.add(LoadProps(asset_id=a.id, base_demand_kw=base, current_demand_kw=base, flexible=flex,
                              maximum_curtailment_kw=0.4 if flex else 0.0, maximum_delay_minutes=120 if flex else 0,
                              minimum_service_fraction=0.5 if flex else 1.0, compensation_per_kwh=0.06 if flex else 0.0,
                              priority=(LoadPriority.FLEXIBLE if flex else LoadPriority.NORMAL).value, load_kind="RESIDENTIAL"))
    for i, (base, cur) in enumerate([(40.0, 12.0), (55.0, 18.0)], start=1):
        a = asset(f"Commercial-{i}", parts[f"Commercial-{i}"], "COMMERCIAL", AssetType.LOAD, base * 1.5)
        session.add(LoadProps(asset_id=a.id, base_demand_kw=base, current_demand_kw=base, flexible=True,
                              maximum_curtailment_kw=cur, maximum_delay_minutes=60, minimum_service_fraction=0.4,
                              compensation_per_kwh=0.08, priority=LoadPriority.NORMAL.value, load_kind="COMMERCIAL"))
    h = asset("Hospital", parts["CityHospital"], "HOSPITAL", AssetType.LOAD, 70.0)
    session.add(LoadProps(asset_id=h.id, base_demand_kw=45.0, current_demand_kw=45.0, priority=LoadPriority.CRITICAL.value,
                          load_kind="CRITICAL"))
    for i in range(1, 9):
        a = asset(f"Solar-{i}", parts[f"Prosumer-{i}"], "RES_A" if i <= 5 else "RES_B", AssetType.SOLAR, 6.0)
        session.add(GeneratorProps(asset_id=a.id, maximum_generation_kw=6.0, renewable=True, marginal_cost=0.0))
    for i in (1, 2):
        a = asset(f"Wind-{i}", parts["WindOperator"], "WIND_FARM", AssetType.WIND, 60.0)
        session.add(GeneratorProps(asset_id=a.id, maximum_generation_kw=60.0, renewable=True, marginal_cost=0.01))
    a = asset("Hydro-1", parts["HydroOperator"], "HYDRO", AssetType.SMALL_HYDRO, 50.0)
    session.add(GeneratorProps(asset_id=a.id, maximum_generation_kw=50.0, minimum_generation_kw=10.0, renewable=True, marginal_cost=0.015))
    for i in range(1, 7):
        a = asset(f"Battery-{i}", parts[f"Prosumer-{i}"], "RES_A" if i <= 5 else "RES_B", AssetType.BATTERY, 5.0)
        session.add(BatteryProps(asset_id=a.id, capacity_kwh=10.0, current_soc_percent=50.0, minimum_soc_percent=20.0,
                                 maximum_soc_percent=95.0, maximum_charge_kw=5.0, maximum_discharge_kw=5.0,
                                 round_trip_efficiency=0.9, degradation_cost_per_kwh=0.02))
    a = asset("CommunityBattery", parts["CommunityStorageOperator"], "STORAGE", AssetType.BATTERY, 60.0)
    session.add(BatteryProps(asset_id=a.id, capacity_kwh=200.0, current_soc_percent=60.0, minimum_soc_percent=15.0,
                             maximum_soc_percent=95.0, maximum_charge_kw=60.0, maximum_discharge_kw=60.0,
                             round_trip_efficiency=0.92, degradation_cost_per_kwh=0.015, community=True))
    for i in range(1, 11):
        a = asset(f"EV-{i:02d}", parts[f"EVOwner-{i}"], "RES_A" if i <= 6 else "RES_B", AssetType.EV_BATTERY, 7.4)
        v2g = i <= 6
        session.add(EVProps(asset_id=a.id, battery_capacity_kwh=60.0, soc_percent=45.0 + 4 * i,
                            minimum_required_soc_percent=80.0, minimum_reserve_soc_percent=25.0,
                            departure_time=DEFAULT_START + timedelta(hours=14 + i), maximum_charge_rate_kw=7.4,
                            maximum_discharge_rate_kw=7.4 if v2g else 0.0, v2g_enabled=v2g, connected=i <= 8))
    asset("UtilityGrid", parts["UtilityOperator"], "GRID_TIE", AssetType.GRID_CONNECTION, 400.0)
    await session.flush()

    # ---------------------------------------------------------------- meters, funds, users
    for key, a in assets.items():
        ident = f"SM-{a.id.hex[:12]}"
        m = SmartMeter(asset_id=a.id, meter_identifier=ident, public_key=meter_key(ident), trusted=True)
        session.add(m)
        meters.append(m)
    await session.flush()
    ledger = Ledger(session, mg.id)
    for key, p in parts.items():
        amt = Decimal("5000") if p.type in (ParticipantType.GENERATOR.value, ParticipantType.STORAGE_OPERATOR.value,
                                            ParticipantType.GRID_OPERATOR.value) else Decimal("500")
        if p.type == ParticipantType.COMMERCIAL.value:
            amt = Decimal("2000")
        await ledger.deposit(p.id, amt, "seed deposit", f"seed-deposit:{p.id}")
    st = await get_state(session, mg.id)
    st.seed, st.sim_time = SEED, DEFAULT_START
    st.config = {**st.config, "tz_offset_hours": TZ}
    users = {"admin": (UserRole.ADMIN, None), "operator": (UserRole.GRID_OPERATOR, None), "auditor": (UserRole.AUDITOR, None),
             "prosumer1": (UserRole.PARTICIPANT, parts["Prosumer-1"].id), "windop": (UserRole.PARTICIPANT, parts["WindOperator"].id)}
    for u, (role, pid) in users.items():
        session.add(User(email=f"{u}@greengrid.local", password_hash=hash_password(admin_password), role=role.value, participant_id=pid))
    await session.flush()
    await _history(session, mg, assets, meters, history_hours)
    await audit.record(session, "DEMO_SEEDED", "Microgrid", mg.id, "seed", {"assets": len(assets), "participants": len(parts)}, mg.id)
    return {"microgrid_id": mg.id, "assets": {k: a.id for k, a in assets.items()},
            "participants": {k: p.id for k, p in parts.items()}, "already_seeded": False}


async def _history(session: AsyncSession, mg: Microgrid, assets: dict[str, EnergyAsset], meters: list[SmartMeter],
                   hours: int) -> None:
    by_asset = {m.asset_id: m for m in meters}
    now = utcnow()
    loads = [a for a in assets.values() if a.asset_type == AssetType.LOAD.value]
    props = {a.id: (await session.get(LoadProps, a.id)) for a in loads}
    for h in range(hours, 0, -1):
        end = DEFAULT_START - timedelta(hours=h - 1)
        mid = end - timedelta(minutes=30)
        for idx, (key, a) in enumerate(assets.items()):
            v = {}
            if a.asset_type == AssetType.SOLAR.value:
                v["generation_kwh"] = a.rated_power_kw * signals.solar_capacity_factor(SEED, mid, TZ)
            elif a.asset_type == AssetType.WIND.value:
                v["generation_kwh"] = a.rated_power_kw * signals.wind_capacity_factor(signals.base_wind_speed(SEED, mid))
            elif a.asset_type == AssetType.SMALL_HYDRO.value:
                v["generation_kwh"] = a.rated_power_kw * signals.hydro_capacity_factor(SEED, mid)
            elif a in loads:
                lp = props[a.id]
                mult = (signals.constant_demand_multiplier(SEED, mid, idx) if lp.load_kind == "CRITICAL"
                        else signals.commercial_demand_multiplier(SEED, mid, TZ, idx) if lp.load_kind == "COMMERCIAL"
                        else signals.household_demand_multiplier(SEED, mid, TZ, idx))
                v["consumption_kwh"] = lp.base_demand_kw * mult
            else:
                continue
            session.add(MeterReading(meter_id=by_asset[a.id].id, asset_id=a.id, microgrid_id=mg.id, timestamp=end,
                                     interval_minutes=60, verified=True, received_at=now,
                                     verification_meta={"source": "seed_history"}, **v))
    await session.flush()


async def _main() -> None:
    from app.db.base import Base
    from app.db.session import get_engine, session_scope
    pwd = os.environ.get("MGX_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
    async with session_scope() as s:
        res = await seed_demo(s, pwd)
    if res.get("already_seeded"):
        print("demo microgrid already seeded")
        return
    print(f"seeded microgrid {res['microgrid_id']}")
    print(f"users: admin@/operator@/auditor@/prosumer1@/windop@greengrid.local  password: {pwd}")


if __name__ == "__main__":
    asyncio.run(_main())
