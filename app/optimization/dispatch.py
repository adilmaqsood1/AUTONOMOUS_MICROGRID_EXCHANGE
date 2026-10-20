"""Deterministic multi-period dispatch optimisation (LP, PuLP/CBC).

Objective (minimised), each term scaled by a configurable weight:

  cost_weight         * (grid import cost - export revenue + generator marginal cost + DR compensation)
  renewable_weight    * renewable curtailment (kWh) * curtailment_penalty
  grid_import_weight  * grid import (kWh)
  degradation_weight  * battery/EV throughput degradation cost
  unserved_weight     * priority-scaled unserved energy (CRITICAL >> HIGH >> NORMAL >> FLEXIBLE >> SHEDDABLE)
  resilience_weight   * reserve SOC shortfall at end of horizon (and EV departure shortfall, always hard-ish)

Constraints: generation limits, nodal power balance with line capacities (transport model),
battery SOC / charge / discharge limits & efficiency, EV departure SOC, external grid limits
(zero when islanded), critical-load service, DR curtailment limits.

The model is linear: simultaneous charge+discharge is never optimal because efficiency losses
and degradation costs make it strictly costly (documented design choice, avoids binaries).
Interface: `solve_dispatch(problem) -> DispatchResult`. Replaceable by DC-OPF later.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import pulp

from app.core.enums import LoadPriority

UNSERVED_PENALTY = {
    LoadPriority.CRITICAL.value: 10_000.0, LoadPriority.HIGH.value: 600.0,
    LoadPriority.NORMAL.value: 200.0, LoadPriority.FLEXIBLE.value: 60.0,
    LoadPriority.SHEDDABLE.value: 10.0,
}


@dataclass
class ObjectiveWeights:
    cost_weight: float = 1.0
    renewable_weight: float = 0.3
    resilience_weight: float = 0.5
    degradation_weight: float = 1.0
    grid_import_weight: float = 0.05
    unserved_weight: float = 1.0
    carbon_weight: float = 0.05
    carbon_price_per_kg: float = 0.05  # $50/metric ton CO2
    curtailment_penalty: float = 0.10  # currency-equivalent per kWh of renewable curtailed

    @staticmethod
    def normal() -> "ObjectiveWeights":
        return ObjectiveWeights(cost_weight=1.0, renewable_weight=0.5, resilience_weight=0.3, carbon_weight=0.05)

    @staticmethod
    def island() -> "ObjectiveWeights":
        """Critical load served, stability, reserve first; economic cost last."""
        return ObjectiveWeights(cost_weight=0.1, renewable_weight=0.2, resilience_weight=5.0,
                                degradation_weight=0.2, grid_import_weight=0.0, unserved_weight=5.0,
                                carbon_weight=0.01)


@dataclass
class OptLine:
    id: str
    a: str
    b: str
    capacity_kw: float


@dataclass
class OptGen:
    id: str
    node: str
    available_kw: list[float]  # per step (forecast or actual availability)
    marginal_cost: float
    renewable: bool


@dataclass
class OptBattery:
    id: str
    node: str
    capacity_kwh: float
    soc0_kwh: float
    min_kwh: float
    max_kwh: float
    max_charge_kw: float
    max_discharge_kw: float
    rte: float
    degradation_cost: float
    reserve_kwh: float = 0.0
    available: bool = True


@dataclass
class OptEV:
    id: str
    node: str
    capacity_kwh: float
    soc0_kwh: float
    reserve_kwh: float
    required_kwh: float
    departure_step: int  # step index at/before which required SOC must be met
    max_charge_kw: float
    max_discharge_kw: float
    v2g: bool
    connected_steps: list[bool]
    degradation_cost: float = 0.03


@dataclass
class OptLoad:
    id: str
    node: str
    demand_kw: list[float]
    priority: str
    max_curtail_kw: float = 0.0
    curtail_cost: float = 0.0  # DR compensation per kWh


@dataclass
class OptGrid:
    node: str
    import_limit_kw: float
    export_limit_kw: float
    import_price: list[float]
    export_price: list[float]
    connected: bool = True


@dataclass
class DispatchProblem:
    nodes: list[str]
    lines: list[OptLine]
    steps: int
    dt_hours: float
    generators: list[OptGen] = field(default_factory=list)
    batteries: list[OptBattery] = field(default_factory=list)
    evs: list[OptEV] = field(default_factory=list)
    loads: list[OptLoad] = field(default_factory=list)
    grid: OptGrid | None = None
    weights: ObjectiveWeights = field(default_factory=ObjectiveWeights.normal)
    mode: str = "GRID_CONNECTED"


@dataclass
class ForecastScenario:
    name: str
    probability: float
    generator_multipliers: dict[str, float] = field(default_factory=dict)  # gen_id -> multiplier
    load_multipliers: dict[str, float] = field(default_factory=dict)       # load_id -> multiplier


@dataclass
class StochasticDispatchProblem:
    base: DispatchProblem
    scenarios: list[ForecastScenario] = field(default_factory=list)
    risk_aversion: float = 0.2


@dataclass
class DispatchResult:
    status: str
    objective: float
    execution_time_ms: float
    generation_kwh: dict[str, list[float]] = field(default_factory=dict)
    battery_charge_kwh: dict[str, list[float]] = field(default_factory=dict)
    battery_discharge_kwh: dict[str, list[float]] = field(default_factory=dict)
    battery_soc_kwh: dict[str, list[float]] = field(default_factory=dict)
    ev_charge_kwh: dict[str, list[float]] = field(default_factory=dict)
    ev_discharge_kwh: dict[str, list[float]] = field(default_factory=dict)
    unserved_kwh: dict[str, list[float]] = field(default_factory=dict)
    curtailed_demand_kwh: dict[str, list[float]] = field(default_factory=dict)
    import_kwh: list[float] = field(default_factory=list)
    export_kwh: list[float] = field(default_factory=list)
    renewable_curtailed_kwh: float = 0.0
    line_flow_kw: dict[str, list[float]] = field(default_factory=dict)
    ev_shortfall_kwh: dict[str, float] = field(default_factory=dict)
    # Advanced grid economics & analytics (Phase 2):
    nodal_lmp: dict[str, list[float]] = field(default_factory=dict)
    line_congestion_rent: dict[str, list[float]] = field(default_factory=dict)
    carbon_emissions_kg: float = 0.0
    carbon_cost: float = 0.0
    battery_dod_percent: dict[str, list[float]] = field(default_factory=dict)
    battery_dod_degradation_cost: dict[str, float] = field(default_factory=dict)

    def total_unserved(self, priority_of: dict[str, str] | None = None, priority: str | None = None) -> float:
        tot = 0.0
        for k, v in self.unserved_kwh.items():
            if priority is None or (priority_of and priority_of.get(k) == priority):
                tot += sum(v)
        return tot


def _v(x):
    return float(x.value() or 0.0) if x is not None else 0.0


def solve_dispatch(p: DispatchProblem) -> DispatchResult:
    t0 = time.perf_counter()
    T, dt, w = range(p.steps), p.dt_hours, p.weights
    m = pulp.LpProblem("microgrid_dispatch", pulp.LpMinimize)
    L = lambda name: name.replace("-", "_").replace(" ", "_")  # noqa: E731

    gen = {(g.id, t): pulp.LpVariable(f"g_{L(g.id)}_{t}", 0, max(g.available_kw[t], 0.0)) for g in p.generators for t in T}
    ch = {(b.id, t): pulp.LpVariable(f"bc_{L(b.id)}_{t}", 0, b.max_charge_kw if b.available else 0) for b in p.batteries for t in T}
    dis = {(b.id, t): pulp.LpVariable(f"bd_{L(b.id)}_{t}", 0, b.max_discharge_kw if b.available else 0) for b in p.batteries for t in T}
    soc = {(b.id, t): pulp.LpVariable(f"bs_{L(b.id)}_{t}", b.min_kwh, b.max_kwh) for b in p.batteries for t in T}
    res_short = {b.id: pulp.LpVariable(f"rs_{L(b.id)}", 0) for b in p.batteries}

    ech = {(e.id, t): pulp.LpVariable(f"ec_{L(e.id)}_{t}", 0, e.max_charge_kw if e.connected_steps[t] else 0) for e in p.evs for t in T}
    edis = {(e.id, t): pulp.LpVariable(f"ed_{L(e.id)}_{t}", 0, e.max_discharge_kw if (e.v2g and e.connected_steps[t]) else 0) for e in p.evs for t in T}
    esoc = {(e.id, t): pulp.LpVariable(f"es_{L(e.id)}_{t}", 0, e.capacity_kwh) for e in p.evs for t in T}
    eshort = {e.id: pulp.LpVariable(f"esh_{L(e.id)}", 0) for e in p.evs}

    unserved = {(l.id, t): pulp.LpVariable(f"u_{L(l.id)}_{t}", 0, max(l.demand_kw[t], 0.0)) for l in p.loads for t in T}
    curt = {(l.id, t): pulp.LpVariable(f"c_{L(l.id)}_{t}", 0, min(l.max_curtail_kw, max(l.demand_kw[t], 0.0))) for l in p.loads for t in T}

    gr = p.grid
    imp = exp = None
    if gr:
        imp_cap = gr.import_limit_kw if gr.connected else 0.0
        exp_cap = gr.export_limit_kw if gr.connected else 0.0
        imp = {t: pulp.LpVariable(f"imp_{t}", 0, imp_cap) for t in T}
        exp = {t: pulp.LpVariable(f"exp_{t}", 0, exp_cap) for t in T}
    flow = {(ln.id, t): pulp.LpVariable(f"f_{L(ln.id)}_{t}", -ln.capacity_kw, ln.capacity_kw) for ln in p.lines for t in T}

    # --- constraints
    for b in p.batteries:
        eta = math.sqrt(max(b.rte, 1e-6))
        for t in T:
            prev = b.soc0_kwh if t == 0 else soc[(b.id, t - 1)]
            m += soc[(b.id, t)] == prev + eta * ch[(b.id, t)] * dt - dis[(b.id, t)] * dt / eta, f"soc_{L(b.id)}_{t}"
        m += soc[(b.id, p.steps - 1)] + res_short[b.id] >= b.reserve_kwh, f"reserve_{L(b.id)}"
    for e in p.evs:
        eta = math.sqrt(0.9)
        for t in T:
            prev = e.soc0_kwh if t == 0 else esoc[(e.id, t - 1)]
            m += esoc[(e.id, t)] == prev + eta * ech[(e.id, t)] * dt - edis[(e.id, t)] * dt / eta, f"esoc_{L(e.id)}_{t}"
            if e.v2g:
                m += esoc[(e.id, t)] >= min(e.reserve_kwh, e.capacity_kwh), f"ereserve_{L(e.id)}_{t}"
        dep = min(max(e.departure_step, 0), p.steps - 1)
        m += esoc[(e.id, dep)] + eshort[e.id] >= min(e.required_kwh, e.capacity_kwh), f"ereq_{L(e.id)}"

    nodes_in = {n: [] for n in p.nodes}
    for ln in p.lines:
        nodes_in.setdefault(ln.a, [])
        nodes_in.setdefault(ln.b, [])
    for t in T:
        for n in nodes_in:
            expr = []
            for g in p.generators:
                if g.node == n:
                    expr.append(gen[(g.id, t)])
            for b in p.batteries:
                if b.node == n:
                    expr.append(dis[(b.id, t)]); expr.append(-1 * ch[(b.id, t)])
            for e in p.evs:
                if e.node == n:
                    expr.append(edis[(e.id, t)]); expr.append(-1 * ech[(e.id, t)])
            supply = pulp.lpSum(expr)
            demand = pulp.lpSum(l.demand_kw[t] - unserved[(l.id, t)] - curt[(l.id, t)] for l in p.loads if l.node == n)
            net_flow_in = pulp.lpSum(flow[(ln.id, t)] for ln in p.lines if ln.b == n) - \
                pulp.lpSum(flow[(ln.id, t)] for ln in p.lines if ln.a == n)
            gridterm = 0
            if gr and gr.node == n:
                gridterm = imp[t] - exp[t]
            m += supply + gridterm + net_flow_in == demand, f"bal_{L(n)}_{t}"

    # --- objective
    cost = []
    for g in p.generators:
        for t in T:
            cost.append(g.marginal_cost * gen[(g.id, t)] * dt)
    if gr:
        for t in T:
            cost.append(gr.import_price[t] * imp[t] * dt - gr.export_price[t] * exp[t] * dt)
    for l in p.loads:
        for t in T:
            cost.append(l.curtail_cost * curt[(l.id, t)] * dt)
    degr = []
    for b in p.batteries:
        for t in T:
            degr.append(b.degradation_cost * dis[(b.id, t)] * dt)
    for e in p.evs:
        for t in T:
            degr.append(e.degradation_cost * edis[(e.id, t)] * dt)
    curtail_ren = []
    for g in p.generators:
        if g.renewable:
            for t in T:
                curtail_ren.append((max(g.available_kw[t], 0.0) - gen[(g.id, t)]) * dt)
    import_kwh = [imp[t] * dt for t in T] if gr else []
    uns = [UNSERVED_PENALTY.get(l.priority, 200.0) * unserved[(l.id, t)] * dt for l in p.loads for t in T]
    # Reserve shortfall (per kWh) ranks above SHEDDABLE/FLEXIBLE unserved energy but below NORMAL/HIGH/CRITICAL.
    resil = [res_short[b.id] * 100.0 for b in p.batteries] + [eshort[e.id] * 500.0 for e in p.evs]

    # Carbon emissions: ~0.42 kg CO2 / kWh for grid import, 0.015 for renewable, 0.65 for fossil
    carbon_terms = []
    if gr:
        for t in T:
            carbon_terms.append(0.42 * imp[t] * dt)
    for g in p.generators:
        factor = 0.015 if g.renewable else 0.65
        for t in T:
            carbon_terms.append(factor * gen[(g.id, t)] * dt)

    m += (w.cost_weight * pulp.lpSum(cost)
          + w.renewable_weight * w.curtailment_penalty * pulp.lpSum(curtail_ren)
          + w.grid_import_weight * pulp.lpSum(import_kwh)
          + w.degradation_weight * pulp.lpSum(degr)
          + w.unserved_weight * pulp.lpSum(uns)
          + w.resilience_weight * pulp.lpSum(resil)
          + w.carbon_weight * w.carbon_price_per_kg * pulp.lpSum(carbon_terms)), "objective"

    status = m.solve(pulp.PULP_CBC_CMD(msg=False, threads=1))
    res = DispatchResult(status=pulp.LpStatus[status], objective=float(pulp.value(m.objective) or 0.0),
                         execution_time_ms=0.0)
    series = lambda d, key: [_v(d[(key, t)]) * dt for t in T]  # noqa: E731
    for g in p.generators:
        res.generation_kwh[g.id] = series(gen, g.id)
    for b in p.batteries:
        res.battery_charge_kwh[b.id] = series(ch, b.id)
        res.battery_discharge_kwh[b.id] = series(dis, b.id)
        res.battery_soc_kwh[b.id] = [_v(soc[(b.id, t)]) for t in T]
    for e in p.evs:
        res.ev_charge_kwh[e.id] = series(ech, e.id)
        res.ev_discharge_kwh[e.id] = series(edis, e.id)
        res.ev_shortfall_kwh[e.id] = _v(eshort[e.id])
    for l in p.loads:
        res.unserved_kwh[l.id] = series(unserved, l.id)
        res.curtailed_demand_kwh[l.id] = series(curt, l.id)
    if gr:
        res.import_kwh = [_v(imp[t]) * dt for t in T]
        res.export_kwh = [_v(exp[t]) * dt for t in T]
    res.renewable_curtailed_kwh = sum(
        (max(g.available_kw[t], 0.0) * dt - res.generation_kwh[g.id][t]) for g in p.generators if g.renewable for t in T)
    for ln in p.lines:
        res.line_flow_kw[ln.id] = [_v(flow[(ln.id, t)]) for t in T]

    # Carbon emissions and costs
    tot_carbon_kg = 0.0
    if gr:
        tot_carbon_kg += sum(0.42 * kwh for kwh in res.import_kwh)
    for g in p.generators:
        factor = 0.015 if g.renewable else 0.65
        tot_carbon_kg += sum(factor * kwh for kwh in res.generation_kwh[g.id])
    res.carbon_emissions_kg = round(tot_carbon_kg, 4)
    res.carbon_cost = round(tot_carbon_kg * w.carbon_price_per_kg, 4)

    # Nodal LMP from PuLP duals (shadow prices of balance constraint)
    for n in p.nodes:
        lmp_series = []
        for t in T:
            c = m.constraints.get(f"bal_{L(n)}_{t}")
            if c is not None and c.pi is not None:
                # PuLP pi is in objective units per kW. Divide by dt and cost_weight to get exact $/kWh
                val = abs(float(c.pi)) / (max(dt, 1e-6) * max(w.cost_weight, 1e-6))
            else:
                val = 0.0
            lmp_series.append(round(val, 4))
        res.nodal_lmp[n] = lmp_series

    # Line congestion rent
    for ln in p.lines:
        rents = []
        for t in T:
            lmp_a = res.nodal_lmp.get(ln.a, [0.0] * p.steps)[t]
            lmp_b = res.nodal_lmp.get(ln.b, [0.0] * p.steps)[t]
            f = res.line_flow_kw[ln.id][t]
            rent = abs(lmp_b - lmp_a) * abs(f) * dt
            rents.append(round(rent, 4))
        res.line_congestion_rent[ln.id] = rents

    # Nonlinear Battery Depth-of-Discharge (DoD) degradation
    for b in p.batteries:
        dod_list = []
        tot_deg_cost = 0.0
        c_base = b.degradation_cost
        for t in T:
            current_soc = res.battery_soc_kwh[b.id][t]
            dod = max(0.0, 1.0 - (current_soc / max(b.capacity_kwh, 1e-6))) * 100.0
            dod_list.append(round(dod, 2))
            dis_kwh = res.battery_discharge_kwh[b.id][t]
            stress = 1.0 + 2.0 * ((dod / 100.0) ** 2)
            tot_deg_cost += c_base * stress * dis_kwh
        res.battery_dod_percent[b.id] = dod_list
        res.battery_dod_degradation_cost[b.id] = round(tot_deg_cost, 4)

    res.execution_time_ms = (time.perf_counter() - t0) * 1000.0
    return res


def solve_stochastic_dispatch(p: StochasticDispatchProblem) -> tuple[DispatchResult, dict[str, DispatchResult]]:
    """Solve multi-scenario stochastic dispatch hedging against generation/load uncertainty.
    
    Returns:
        (hedged_result, scenario_results_dict)
    """
    base_res = solve_dispatch(p.base)
    if not p.scenarios:
        return base_res, {"base": base_res}

    scenario_results: dict[str, DispatchResult] = {"base": base_res}
    for sc in p.scenarios:
        mod_generators = []
        for g in p.base.generators:
            mult = sc.generator_multipliers.get(g.id, 1.0)
            mod_generators.append(OptGen(
                id=g.id, node=g.node,
                available_kw=[avail * mult for avail in g.available_kw],
                marginal_cost=g.marginal_cost,
                renewable=g.renewable
            ))
        mod_loads = []
        for l in p.base.loads:
            mult = sc.load_multipliers.get(l.id, 1.0)
            mod_loads.append(OptLoad(
                id=l.id, node=l.node,
                demand_kw=[dem * mult for dem in l.demand_kw],
                priority=l.priority,
                max_curtail_kw=l.max_curtail_kw,
                curtail_cost=l.curtail_cost
            ))
        sc_prob = DispatchProblem(
            nodes=p.base.nodes,
            lines=p.base.lines,
            steps=p.base.steps,
            dt_hours=p.base.dt_hours,
            generators=mod_generators,
            batteries=p.base.batteries,
            evs=p.base.evs,
            loads=mod_loads,
            grid=p.base.grid,
            weights=p.base.weights,
            mode=p.base.mode
        )
        sc_res = solve_dispatch(sc_prob)
        scenario_results[sc.name] = sc_res

    # Check for shortfall across scenarios
    max_shortfall = 0.0
    for name, s_res in scenario_results.items():
        if name == "base":
            continue
        shortfall = sum(sum(v) for v in s_res.unserved_kwh.values())
        if shortfall > max_shortfall:
            max_shortfall = shortfall

    if max_shortfall > 0 and p.base.batteries:
        hedged_batteries = []
        extra_reserve = (max_shortfall * p.risk_aversion) / len(p.base.batteries)
        for b in p.base.batteries:
            hedged_batteries.append(OptBattery(
                id=b.id, node=b.node,
                capacity_kwh=b.capacity_kwh,
                soc0_kwh=b.soc0_kwh,
                min_kwh=b.min_kwh,
                max_kwh=b.max_kwh,
                max_charge_kw=b.max_charge_kw,
                max_discharge_kw=b.max_discharge_kw,
                rte=b.rte,
                degradation_cost=b.degradation_cost,
                reserve_kwh=min(b.capacity_kwh * 0.9, b.reserve_kwh + extra_reserve),
                available=b.available
            ))
        hedged_prob = DispatchProblem(
            nodes=p.base.nodes,
            lines=p.base.lines,
            steps=p.base.steps,
            dt_hours=p.base.dt_hours,
            generators=p.base.generators,
            batteries=hedged_batteries,
            evs=p.base.evs,
            loads=p.base.loads,
            grid=p.base.grid,
            weights=p.base.weights,
            mode=p.base.mode
        )
        hedged_res = solve_dispatch(hedged_prob)
        return hedged_res, scenario_results

    return base_res, scenario_results

