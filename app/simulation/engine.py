"""Simulation step: advances the clock, evolves physical state and emits meter readings.

Physical dispatch of storage is driven by *commitments* (market outcomes) plus an emergency
controller that only acts when supply is short (island / import-limit). Load shedding follows
the deterministic resilience rules. Nothing here consults an ML model.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.battery import acceptable_charge_kwh, deliverable_discharge_kwh
from app.agents.ev import EV_ETA
from app.audit import service as audit
from app.core.enums import AssetStatus, AssetType, AuditEventType, CommitmentStatus, LoadPriority, MeterStatus, OperatingMode
from app.forecasting.service import record_actuals
from app.meters.service import ingest_reading, sign
from app.models.assets import EnergyAsset
from app.models.core import Microgrid
from app.models.meters import SmartMeter
from app.models.ops import EnergyCommitment, SimulationState
from app.resilience import service as resilience
from app.resilience.engine import LoadView, plan_island_loads, plan_recovery_step
from app.risk.service import battery_state
from app.simulation import scenarios, signals
from app.simulation.state import get_state, to_config
from app.websocket.hub import queue_event

OCCUPYING = (CommitmentStatus.ACTIVE.value, CommitmentStatus.PENDING.value)


def _load_multiplier(a: EnergyAsset, seed: int, mid: datetime, tz: float, idx: int) -> float:
    kind = a.load.load_kind
    if a.load.priority == LoadPriority.CRITICAL.value or kind == "CRITICAL":
        return signals.constant_demand_multiplier(seed, mid, idx)
    if kind == "COMMERCIAL":
        return signals.commercial_demand_multiplier(seed, mid, tz, idx)
    return signals.household_demand_multiplier(seed, mid, tz, idx)


async def step(session: AsyncSession, microgrid_id: uuid.UUID, actor: str = "simulator") -> dict:
    mg = (await session.execute(select(Microgrid).where(Microgrid.id == microgrid_id).with_for_update())).scalar_one()
    st = await get_state(session, microgrid_id)
    cfg = to_config(st)
    t0, dtm = cfg.sim_time, st.step_minutes
    t1, dth = t0 + timedelta(minutes=dtm), dtm / 60.0
    mid = t0 + timedelta(minutes=dtm / 2)
    seed, tz = cfg.seed, cfg.tz_offset_hours

    await scenarios.expire(session, microgrid_id, t1)
    mods = await scenarios.active_modifiers(session, microgrid_id, t0, t1)
    assets = list((await session.execute(select(EnergyAsset).where(EnergyAsset.microgrid_id == microgrid_id)
                                         .order_by(EnergyAsset.name, EnergyAsset.id))).scalars())
    meters = {m.asset_id: m for m in (await session.execute(select(SmartMeter).where(
        SmartMeter.asset_id.in_([a.id for a in assets])))).scalars()}
    commits = list((await session.execute(select(EnergyCommitment).where(
        EnergyCommitment.microgrid_id == microgrid_id, EnergyCommitment.status.in_(OCCUPYING),
        EnergyCommitment.delivery_start < t1, EnergyCommitment.delivery_end > t0)
        .order_by(EnergyCommitment.created_at, EnergyCommitment.id))).scalars())
    online = lambda a: a.status == AssetStatus.ONLINE.value  # noqa: E731

    # ------------------------------------------------ generation (potential)
    gen_kw: dict[uuid.UUID, float] = {}
    for a in assets:
        if not a.generator:
            continue
        g = a.generator
        if not online(a):
            kw = 0.0
        elif a.asset_type == AssetType.SOLAR.value:
            cc = signals.base_cloud_cover(seed, mid) + mods.cloud
            kw = a.rated_power_kw * signals.solar_capacity_factor(seed, mid, tz, cloud_cover=cc)
        elif a.asset_type == AssetType.WIND.value:
            kw = a.rated_power_kw * signals.wind_capacity_factor(signals.base_wind_speed(seed, mid) * (1 - mods.wind_drop))
        elif a.asset_type == AssetType.SMALL_HYDRO.value:
            kw = a.rated_power_kw * signals.hydro_capacity_factor(seed, mid)
        else:
            kw = 0.0
        if not g.renewable and online(a):  # dispatchable: runs only for its commitments
            kw = sum(float(c.quantity_kwh) / ((c.delivery_end - c.delivery_start).total_seconds() / 3600)
                     for c in commits if c.seller_asset_id == a.id)
        gen_kw[a.id] = min(max(kw, 0.0), g.maximum_generation_kw)

    # ------------------------------------------------ demand
    loads: dict[str, dict] = {}
    for i, a in enumerate(x for x in assets if x.load):
        lp = a.load
        d = lp.base_demand_kw * _load_multiplier(a, seed, mid, tz, i) * mods.demand_multiplier
        lp.current_demand_kw = d
        loads[str(a.id)] = {"asset": a, "props": lp, "demand_kw": d, "prev_curtailed_kw": lp.curtailed_kw,
                            "priority": lp.priority, "flexible": lp.flexible,
                            "compensation_per_kwh": lp.compensation_per_kwh, "participant_id": a.participant_id,
                            "max_curtail_kw": min(lp.maximum_curtailment_kw, d),
                            "min_service": lp.minimum_service_fraction}
    # EV smart-charging demand (treated as postponable flexible load)
    ev_assets = [a for a in assets if a.ev]
    for a in ev_assets:
        e = a.ev
        if e.departure_time and e.departure_time <= t0 and e.connected:
            e.connected = False
        if e.connected and online(a) and e.soc_percent < e.minimum_required_soc_percent:
            need_kwh = (e.minimum_required_soc_percent - e.soc_percent) / 100 * e.battery_capacity_kwh / EV_ETA
            kw = min(e.maximum_charge_rate_kw, need_kwh / dth)
            loads[str(a.id)] = {"asset": a, "props": None, "demand_kw": kw, "prev_curtailed_kw": 0.0,
                                "priority": LoadPriority.FLEXIBLE.value, "flexible": True,
                                "compensation_per_kwh": 0.05, "participant_id": a.participant_id,
                                "max_curtail_kw": kw, "min_service": 0.0, "is_ev": True}

    # ------------------------------------------------ commitment-driven storage dispatch
    bat_dis: dict[uuid.UUID, float] = {}
    bat_chg: dict[uuid.UUID, float] = {}
    for c in commits:
        wh = (c.delivery_end - c.delivery_start).total_seconds() / 3600.0
        kwh = float(c.quantity_kwh) * dth / wh
        sa = next((a for a in assets if a.id == c.seller_asset_id), None)
        ba = next((a for a in assets if a.id == c.buyer_asset_id), None)
        if sa and sa.battery and online(sa):
            bs = battery_state(sa)
            cap = max(0.0, (bs.stored_kwh - bs.capacity_kwh * bs.min_soc_percent / 100.0)) * bs.eta - bat_dis.get(sa.id, 0.0)
            bat_dis[sa.id] = bat_dis.get(sa.id, 0.0) + max(0.0, min(kwh, cap, bs.max_discharge_kw * dth - bat_dis.get(sa.id, 0.0)))
        if sa and sa.ev and sa.ev.connected and online(sa) and sa.ev.v2g_enabled:
            e = sa.ev
            stored = e.battery_capacity_kwh * e.soc_percent / 100.0
            cap = max(0.0, stored - e.battery_capacity_kwh * e.minimum_reserve_soc_percent / 100.0) * EV_ETA - bat_dis.get(sa.id, 0.0)
            bat_dis[sa.id] = bat_dis.get(sa.id, 0.0) + max(0.0, min(kwh, cap, e.maximum_discharge_rate_kw * dth - bat_dis.get(sa.id, 0.0)))
        if ba and ba.battery and online(ba):
            bs = battery_state(ba)
            room = max(0.0, bs.capacity_kwh * bs.max_soc_percent / 100.0 - bs.stored_kwh) / bs.eta - bat_chg.get(ba.id, 0.0)
            bat_chg[ba.id] = bat_chg.get(ba.id, 0.0) + max(0.0, min(kwh, room, bs.max_charge_kw * dth - bat_chg.get(ba.id, 0.0)))

    # ------------------------------------------------ system balance & control
    allow_import = mg.external_grid_connected
    import_cap = mg.max_grid_import_kw if allow_import else 0.0
    export_cap = mg.max_grid_export_kw if allow_import else 0.0
    gen_total = sum(gen_kw.values())
    commit_dis_kw = sum(bat_dis.values()) / dth
    commit_chg_kw = sum(bat_chg.values()) / dth
    avail = gen_total + commit_dis_kw + import_cap
    total_demand = sum(l["demand_kw"] for l in loads.values()) + commit_chg_kw
    extra_dis: dict[uuid.UUID, float] = {}
    if total_demand > avail + 1e-9:  # emergency controller: tap storage not already committed
        deficit = total_demand - avail
        cands = []
        for a in assets:
            if a.battery and online(a):
                bs = battery_state(a)
                already = bat_dis.get(a.id, 0.0)
                head = max(0.0, (bs.stored_kwh - bs.capacity_kwh * bs.min_soc_percent / 100.0) * bs.eta - already)
                head = min(head, max(0.0, bs.max_discharge_kw * dth - already))
                cands.append((0 if a.battery.community else 1, a.name, a.id, head))
            elif a.ev and a.ev.connected and online(a) and a.ev.v2g_enabled:
                e = a.ev
                reqk = e.battery_capacity_kwh * e.minimum_required_soc_percent / 100.0
                stored = e.battery_capacity_kwh * e.soc_percent / 100.0
                head = max(0.0, min(stored - reqk, stored - e.battery_capacity_kwh * e.minimum_reserve_soc_percent / 100.0)) * EV_ETA
                head = min(head, e.maximum_discharge_rate_kw * dth)
                cands.append((2, a.name, a.id, head))
        for _, _, aid, head in sorted(cands):
            if deficit <= 1e-9:
                break
            take_kwh = min(head, deficit * dth)
            if take_kwh > 1e-9:
                extra_dis[aid] = take_kwh
                bat_dis[aid] = bat_dis.get(aid, 0.0) + take_kwh
                deficit -= take_kwh / dth
        avail += sum(extra_dis.values()) / dth

    views = [LoadView(k, l["priority"], l["demand_kw"], l["flexible"], l["max_curtail_kw"], l["min_service"],
                      l["prev_curtailed_kw"]) for k, l in loads.items()]
    supply_for_loads = avail - commit_chg_kw
    recovery_done = False
    if mg.operating_mode == OperatingMode.RECOVERY.value:
        # restore from the *currently shed* state in priority order, ramp-limited
        cur_served = {v.id: v.demand_kw - v.currently_shed_kw for v in views}
        for d in plan_recovery_step(views, supply_for_loads):
            cur_served[d.asset_id] += d.quantity_kw
        served_kw, plan_decisions = cur_served, []
        recovery_done = all(abs(cur_served[v.id] - v.demand_kw) < 1e-6 for v in views)
    else:
        plan = plan_island_loads(views, supply_for_loads)
        served_kw, plan_decisions = plan.served_kw, plan.decisions
    await resilience.apply_load_plan(session, mg, loads, served_kw, plan_decisions, t0, dth, actor)

    served_total = sum(served_kw.values())
    net = gen_total + sum(bat_dis.values()) / dth - commit_chg_kw - served_total
    imp_kw = exp_kw = 0.0
    extra_chg: dict[uuid.UUID, float] = {}
    curtail_frac = 0.0
    if net < 0:
        imp_kw = min(-net, import_cap)
    elif net > 1e-9:
        if not allow_import:  # island: absorb surplus in storage first
            left = net * dth
            for a in sorted((x for x in assets if x.battery and online(x)), key=lambda x: (not x.battery.community, x.name)):
                room = acceptable_charge_kwh(battery_state(a), dth) - bat_chg.get(a.id, 0.0)
                put = max(0.0, min(room, left))
                if put > 1e-9:
                    extra_chg[a.id] = put
                    left -= put
            net = left / dth
        exp_kw = min(net, export_cap)
        surplus_unused = max(0.0, net - exp_kw)
        ren_total = sum(gen_kw[a.id] for a in assets if a.generator and a.generator.renewable)
        curtail_frac = min(1.0, surplus_unused / ren_total) if ren_total > 1e-9 else 0.0

    # ------------------------------------------------ state updates
    soc_changes = []
    for a in assets:
        if a.battery:
            b = a.battery
            eta = math.sqrt(b.round_trip_efficiency)
            chg = bat_chg.get(a.id, 0.0) + extra_chg.get(a.id, 0.0)
            dis = bat_dis.get(a.id, 0.0)
            b.current_soc_percent = min(b.maximum_soc_percent, max(0.0, b.current_soc_percent
                                        + (eta * chg - dis / eta) / b.capacity_kwh * 100.0))
            if chg > 1e-6 or dis > 1e-6:
                soc_changes.append({"asset_id": a.id, "soc": round(b.current_soc_percent, 3)})
            if dis > 0.01:
                await audit.record(session, AuditEventType.BATTERY_DISPATCHED, "EnergyAsset", a.id, actor,
                                   {"discharge_kwh": dis, "emergency_controller": a.id in extra_dis}, mg.id)
        if a.ev:
            e = a.ev
            dis = bat_dis.get(a.id, 0.0)
            ev_served_kw = served_kw.get(str(a.id), 0.0) if str(a.id) in loads else 0.0
            e.soc_percent = min(100.0, max(0.0, e.soc_percent + (EV_ETA * ev_served_kw * dth - dis / EV_ETA)
                                           / e.battery_capacity_kwh * 100.0))
            if dis > 0.01:
                await audit.record(session, AuditEventType.EV_DISPATCHED, "EnergyAsset", a.id, actor,
                                   {"discharge_kwh": dis, "v2g": True}, mg.id)
        if a.generator:
            a.generator.current_output_kw = gen_kw[a.id] * (1 - curtail_frac if a.generator.renewable else 1)

    # ------------------------------------------------ meter readings
    for a in assets:
        m = meters.get(a.id)
        if m is None or m.status == MeterStatus.FAILED.value:
            continue
        v = {}
        meta_note = {}
        if a.generator:
            potential = gen_kw[a.id] * dth
            v["generation_kwh"] = potential * (1 - curtail_frac if a.generator.renewable else 1)
            meta_note = {"potential_kwh": potential, "curtailed_kwh": potential - v["generation_kwh"]}
        if str(a.id) in loads:
            l = loads[str(a.id)]
            v["consumption_kwh"] = served_kw.get(str(a.id), 0.0) * dth
            meta_note = {"demand_kwh": l["demand_kw"] * dth, "unserved_kwh": (l["demand_kw"] - served_kw.get(str(a.id), 0.0)) * dth}
        if a.battery or a.ev:
            v["battery_charge_kwh"] = bat_chg.get(a.id, 0.0) + extra_chg.get(a.id, 0.0)
            v["battery_discharge_kwh"] = bat_dis.get(a.id, 0.0)
        if a.asset_type == AssetType.GRID_CONNECTION.value:
            v["import_kwh"], v["export_kwh"] = imp_kw * dth, exp_kw * dth
        f = (mods.anomaly_factor or {}).get(str(m.id))
        if f:
            v = {k: x * f for k, x in v.items()}
        sig = sign(m.public_key, m.meter_identifier, t1, v) if m.public_key else None
        await ingest_reading(session, m, t1, v, sig, dtm, actor, a, extra_meta=meta_note)

    # ------------------------------------------------ microgrid snapshot & clock
    mg.current_generation_kw = sum(a.generator.current_output_kw for a in assets if a.generator)
    mg.current_renewable_kw = sum(a.generator.current_output_kw for a in assets if a.generator and a.generator.renewable)
    mg.current_demand_kw = sum(l["demand_kw"] for l in loads.values())
    mg.current_storage_kwh = sum(battery_state(a).stored_kwh for a in assets if a.battery)
    if recovery_done and mg.operating_mode == OperatingMode.RECOVERY.value:
        await resilience.complete_recovery(session, mg, t1, actor)
    st.sim_time = t1
    st.steps_executed += 1
    await session.flush()
    await record_actuals(session, microgrid_id, t1)
    summary = {"sim_time": t1, "mode": mg.operating_mode, "generation_kw": mg.current_generation_kw,
               "renewable_kw": mg.current_renewable_kw, "demand_kw": mg.current_demand_kw,
               "served_kw": served_total, "import_kw": imp_kw, "export_kw": exp_kw,
               "storage_kwh": mg.current_storage_kwh, "curtailed_fraction": curtail_frac,
               "emergency_dispatch_kwh": sum(extra_dis.values())}
    queue_event(session, "MICROGRID_STATE", microgrid_id, summary)
    if soc_changes:
        queue_event(session, "ASSET_UPDATE", microgrid_id, {"assets": soc_changes})
    return summary
