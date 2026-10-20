"""Optimisation service: builds a dispatch LP from live state, stores an auditable run."""
from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.ev import EV_ETA
from app.core.enums import AssetStatus, AssetType, OperatingMode
from app.core.errors import NotFound
from app.forecasting.service import demand_forecast, generation_forecast
from app.models.assets import EnergyAsset
from app.models.core import GridLine, Microgrid
from app.models.ops import OptimizationRun
from app.optimization.dispatch import (
    DispatchProblem, DispatchResult, ForecastScenario, ObjectiveWeights, OptBattery, OptEV, OptGen,
    OptGrid, OptLine, OptLoad, StochasticDispatchProblem, solve_dispatch, solve_stochastic_dispatch
)
from app.simulation import signals
from app.simulation.state import get_config


async def run_optimization(session: AsyncSession, microgrid_id: uuid.UUID, steps: int = 6, step_minutes: int = 60,
                           start: datetime | None = None, weights: dict | None = None
                           ) -> tuple[OptimizationRun, DispatchResult]:
    mg = await session.get(Microgrid, microgrid_id)
    if not mg:
        raise NotFound("microgrid not found")
    cfg = await get_config(session, microgrid_id)
    start = start or cfg.sim_time
    now, dth, tz = cfg.sim_time, step_minutes / 60.0, cfg.tz_offset_hours
    island = (not mg.external_grid_connected) or mg.operating_mode in (OperatingMode.ISLANDED.value, OperatingMode.EMERGENCY.value)
    w = ObjectiveWeights.island() if island else ObjectiveWeights.normal()
    if weights:
        for k, v in weights.items():
            if not hasattr(w, k):
                raise ValueError(f"unknown objective weight {k}")
            setattr(w, k, float(v))
    assets = list((await session.execute(select(EnergyAsset).where(EnergyAsset.microgrid_id == microgrid_id)
                                         .order_by(EnergyAsset.name, EnergyAsset.id))).scalars())
    lines = list((await session.execute(select(GridLine).where(GridLine.microgrid_id == microgrid_id, GridLine.active.is_(True)))).scalars())
    times = [start + timedelta(minutes=step_minutes * i) for i in range(steps)]
    ends = [t + timedelta(minutes=step_minutes) for t in times]
    P = DispatchProblem(nodes=sorted({str(a.grid_node_id) for a in assets} | {str(l.from_node_id) for l in lines} | {str(l.to_node_id) for l in lines}),
                        lines=[OptLine(str(l.id), str(l.from_node_id), str(l.to_node_id), l.capacity_kw) for l in lines],
                        steps=steps, dt_hours=dth, weights=w, mode=mg.operating_mode)
    gid = None
    for a in assets:
        node, aid, ok = str(a.grid_node_id), str(a.id), a.status == AssetStatus.ONLINE.value
        if a.generator:
            if a.generator.renewable:
                av = []
                for t, e in zip(times, ends):
                    f = await generation_forecast(session, a, t, e, now, cfg)
                    # plan on the pessimistic-but-useful midpoint between lower bound and prediction
                    av.append((0.5 * (f.predicted_value + f.lower_bound) / dth) if ok else 0.0)
            else:
                av = [a.generator.maximum_generation_kw if ok else 0.0] * steps
            P.generators.append(OptGen(aid, node, av, a.generator.marginal_cost, a.generator.renewable))
        elif a.battery:
            b = a.battery
            cap = b.capacity_kwh
            P.batteries.append(OptBattery(aid, node, cap, cap * b.current_soc_percent / 100, cap * b.minimum_soc_percent / 100,
                                          cap * b.maximum_soc_percent / 100, b.maximum_charge_kw, b.maximum_discharge_kw,
                                          b.round_trip_efficiency, b.degradation_cost_per_kwh,
                                          reserve_kwh=cap * (b.minimum_soc_percent + (40 if island else 10)) / 100, available=ok))
        elif a.ev:
            e = a.ev
            cap = e.battery_capacity_kwh
            dep = steps - 1
            if e.departure_time:
                dep = max(0, min(steps - 1, int((e.departure_time - start).total_seconds() // (step_minutes * 60))))
            conn = [bool(e.connected and ok and (e.departure_time is None or t < e.departure_time)) for t in times]
            P.evs.append(OptEV(aid, node, cap, cap * e.soc_percent / 100, cap * e.minimum_reserve_soc_percent / 100,
                               cap * e.minimum_required_soc_percent / 100, dep, e.maximum_charge_rate_kw,
                               e.maximum_discharge_rate_kw, e.v2g_enabled, conn))
        elif a.load:
            dem = []
            for t, e in zip(times, ends):
                f = await demand_forecast(session, a, t, e, now, cfg)
                dem.append(f.predicted_value / dth)
            lp = a.load
            P.loads.append(OptLoad(aid, node, dem, lp.priority, lp.maximum_curtailment_kw if lp.flexible else 0.0,
                                   lp.compensation_per_kwh))
        elif a.asset_type == AssetType.GRID_CONNECTION.value:
            gid = (node, aid)
    if gid:
        P.grid = OptGrid(gid[0], mg.max_grid_import_kw, mg.max_grid_export_kw,
                         [signals.utility_price(t, tz) for t in times], [signals.export_price(t, tz) for t in times],
                         connected=not island)
    res = solve_dispatch(P)
    r = lambda xs: [round(x, 4) for x in xs]  # noqa: E731
    run = OptimizationRun(
        microgrid_id=microgrid_id, objective=asdict(w), mode=mg.operating_mode, status=res.status,
        execution_time_ms=res.execution_time_ms, created_at=now,
        inputs={"start": start.isoformat(), "steps": steps, "step_minutes": step_minutes, "generators": len(P.generators),
                "batteries": len(P.batteries), "evs": len(P.evs), "loads": len(P.loads),
                "total_demand_kwh": round(sum(sum(l.demand_kw) for l in P.loads) * dth, 3),
                "import_price": P.grid.import_price if P.grid else [], "grid_connected": P.grid.connected if P.grid else False},
        constraints={"line_capacities_kw": {l.id: l.capacity_kw for l in P.lines},
                     "import_limit_kw": mg.max_grid_import_kw if not island else 0.0,
                     "export_limit_kw": mg.max_grid_export_kw if not island else 0.0,
                     "critical_loads": [l.id for l in P.loads if l.priority == "CRITICAL"],
                     "battery_reserve_kwh": {b.id: round(b.reserve_kwh, 3) for b in P.batteries}},
        result={"objective_value": round(res.objective, 4), "import_kwh": r(res.import_kwh), "export_kwh": r(res.export_kwh),
                "renewable_curtailed_kwh": round(res.renewable_curtailed_kwh, 4),
                "unserved_kwh_total": round(res.total_unserved(), 4),
                "unserved_by_load": {k: round(sum(v), 4) for k, v in res.unserved_kwh.items() if sum(v) > 1e-6},
                "battery_discharge_kwh": {k: r(v) for k, v in res.battery_discharge_kwh.items() if sum(v) > 1e-6},
                "battery_charge_kwh": {k: r(v) for k, v in res.battery_charge_kwh.items() if sum(v) > 1e-6},
                "ev_shortfall_kwh": {k: round(v, 4) for k, v in res.ev_shortfall_kwh.items() if v > 1e-6},
                "nodal_lmp": res.nodal_lmp,
                "line_congestion_rent": res.line_congestion_rent,
                "carbon_emissions_kg": res.carbon_emissions_kg,
                "carbon_cost": res.carbon_cost,
                "battery_dod_percent": res.battery_dod_percent,
                "battery_dod_degradation_cost": res.battery_dod_degradation_cost})
    session.add(run)
    await session.flush()
    return run, res


async def run_stochastic_optimization(
    session: AsyncSession,
    microgrid_id: uuid.UUID,
    steps: int = 6,
    step_minutes: int = 60,
    start: datetime | None = None,
    risk_aversion: float = 0.2,
    custom_scenarios: list[dict] | None = None,
) -> tuple[OptimizationRun, DispatchResult, dict[str, DispatchResult]]:
    """Runs uncertainty-aware stochastic dispatch across multi-scenario generation/load profiles."""
    mg = await session.get(Microgrid, microgrid_id)
    if not mg:
        raise NotFound("microgrid not found")
    cfg = await get_config(session, microgrid_id)
    start = start or cfg.sim_time
    now, dth, tz = cfg.sim_time, step_minutes / 60.0, cfg.tz_offset_hours
    island = (not mg.external_grid_connected) or mg.operating_mode in (OperatingMode.ISLANDED.value, OperatingMode.EMERGENCY.value)
    w = ObjectiveWeights.island() if island else ObjectiveWeights.normal()
    assets = list((await session.execute(select(EnergyAsset).where(EnergyAsset.microgrid_id == microgrid_id)
                                         .order_by(EnergyAsset.name, EnergyAsset.id))).scalars())
    lines = list((await session.execute(select(GridLine).where(GridLine.microgrid_id == microgrid_id, GridLine.active.is_(True)))).scalars())
    times = [start + timedelta(minutes=step_minutes * i) for i in range(steps)]
    ends = [t + timedelta(minutes=step_minutes) for t in times]
    P = DispatchProblem(nodes=sorted({str(a.grid_node_id) for a in assets} | {str(l.from_node_id) for l in lines} | {str(l.to_node_id) for l in lines}),
                        lines=[OptLine(str(l.id), str(l.from_node_id), str(l.to_node_id), l.capacity_kw) for l in lines],
                        steps=steps, dt_hours=dth, weights=w, mode=mg.operating_mode)
    gid = None
    for a in assets:
        node, aid, ok = str(a.grid_node_id), str(a.id), a.status == AssetStatus.ONLINE.value
        if a.generator:
            if a.generator.renewable:
                av = []
                for t, e in zip(times, ends):
                    f = await generation_forecast(session, a, t, e, now, cfg)
                    av.append((0.5 * (f.predicted_value + f.lower_bound) / dth) if ok else 0.0)
            else:
                av = [a.generator.maximum_generation_kw if ok else 0.0] * steps
            P.generators.append(OptGen(aid, node, av, a.generator.marginal_cost, a.generator.renewable))
        elif a.battery:
            b = a.battery
            cap = b.capacity_kwh
            P.batteries.append(OptBattery(aid, node, cap, cap * b.current_soc_percent / 100, cap * b.minimum_soc_percent / 100,
                                          cap * b.maximum_soc_percent / 100, b.maximum_charge_kw, b.maximum_discharge_kw,
                                          b.round_trip_efficiency, b.degradation_cost_per_kwh,
                                          reserve_kwh=cap * (b.minimum_soc_percent + (40 if island else 10)) / 100, available=ok))
        elif a.ev:
            e = a.ev
            cap = e.battery_capacity_kwh
            dep = steps - 1
            if e.departure_time:
                dep = max(0, min(steps - 1, int((e.departure_time - start).total_seconds() // (step_minutes * 60))))
            conn = [bool(e.connected and ok and (e.departure_time is None or t < e.departure_time)) for t in times]
            P.evs.append(OptEV(aid, node, cap, cap * e.soc_percent / 100, cap * e.minimum_reserve_soc_percent / 100,
                               cap * e.minimum_required_soc_percent / 100, dep, e.maximum_charge_rate_kw,
                               e.maximum_discharge_rate_kw, e.v2g_enabled, conn))
        elif a.load:
            dem = []
            for t, e in zip(times, ends):
                f = await demand_forecast(session, a, t, e, now, cfg)
                dem.append(f.predicted_value / dth)
            lp = a.load
            P.loads.append(OptLoad(aid, node, dem, lp.priority, lp.maximum_curtailment_kw if lp.flexible else 0.0,
                                   lp.compensation_per_kwh))
        elif a.asset_type == AssetType.GRID_CONNECTION.value:
            gid = (node, aid)
    if gid:
        P.grid = OptGrid(gid[0], mg.max_grid_import_kw, mg.max_grid_export_kw,
                         [signals.utility_price(t, tz) for t in times], [signals.export_price(t, tz) for t in times],
                         connected=not island)

    scenarios: list[ForecastScenario] = []
    if custom_scenarios:
        for sc in custom_scenarios:
            gen_mults = {g.id: sc.get("solar_multiplier", 1.0) for g in P.generators}
            load_mults = {l.id: sc.get("load_multiplier", 1.0) for l in P.loads}
            scenarios.append(ForecastScenario(
                name=sc.get("name", "scenario"),
                probability=sc.get("probability", 0.25),
                generator_multipliers=gen_mults,
                load_multipliers=load_mults
            ))
    else:
        # 4 scenarios from phase_2.md specification: 115%, 103%, 91%, 72%
        scenarios = [
            ForecastScenario("scenario_1_high_solar", 0.25, {g.id: 1.15 for g in P.generators if g.renewable}),
            ForecastScenario("scenario_2_normal", 0.35, {g.id: 1.03 for g in P.generators if g.renewable}),
            ForecastScenario("scenario_3_dip", 0.25, {g.id: 0.91 for g in P.generators if g.renewable}),
            ForecastScenario("scenario_4_cloud_drop", 0.15, {g.id: 0.72 for g in P.generators if g.renewable}),
        ]

    stoch_prob = StochasticDispatchProblem(base=P, scenarios=scenarios, risk_aversion=risk_aversion)
    hedged_res, sc_dict = solve_stochastic_dispatch(stoch_prob)

    run = OptimizationRun(
        microgrid_id=microgrid_id, objective=asdict(w), mode=mg.operating_mode, status=hedged_res.status,
        execution_time_ms=hedged_res.execution_time_ms, created_at=now,
        inputs={"start": start.isoformat(), "steps": steps, "step_minutes": step_minutes, "generators": len(P.generators),
                "batteries": len(P.batteries), "evs": len(P.evs), "loads": len(P.loads),
                "scenarios": [s.name for s in scenarios], "risk_aversion": risk_aversion},
        constraints={"line_capacities_kw": {l.id: l.capacity_kw for l in P.lines},
                     "battery_reserve_kwh": {b.id: round(b.reserve_kwh, 3) for b in P.batteries}},
        result={"objective_value": round(hedged_res.objective, 4),
                "hedged": True,
                "scenarios_evaluated": list(sc_dict.keys()),
                "scenario_shortfalls": {k: round(v.total_unserved(), 4) for k, v in sc_dict.items()},
                "carbon_emissions_kg": hedged_res.carbon_emissions_kg,
                "carbon_cost": hedged_res.carbon_cost,
                "battery_dod_percent": hedged_res.battery_dod_percent,
                "battery_dod_degradation_cost": hedged_res.battery_dod_degradation_cost,
                "nodal_lmp": hedged_res.nodal_lmp,
                "line_congestion_rent": hedged_res.line_congestion_rent}
    )
    session.add(run)
    await session.flush()
    return run, hedged_res, sc_dict

