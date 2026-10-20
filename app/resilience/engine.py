"""Island-mode / recovery decision logic (pure).

Rules protect: critical loads are never shed in favour of lower priorities; shedding order is
SHEDDABLE -> FLEXIBLE -> NORMAL -> HIGH. Flexible loads are first *curtailed* (compensated demand
response) down to their minimum service level before any load is shed. Every decision carries a
reason and is recorded by the service layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.enums import LOAD_PRIORITY_RANK, LoadPriority, OperatingMode

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    OperatingMode.GRID_CONNECTED.value: {OperatingMode.ISLANDED.value, OperatingMode.EMERGENCY.value},
    OperatingMode.ISLANDED.value: {OperatingMode.RECOVERY.value, OperatingMode.EMERGENCY.value},
    OperatingMode.EMERGENCY.value: {OperatingMode.ISLANDED.value, OperatingMode.RECOVERY.value},
    OperatingMode.RECOVERY.value: {OperatingMode.GRID_CONNECTED.value, OperatingMode.ISLANDED.value,
                                    OperatingMode.EMERGENCY.value},
}


def can_transition(frm: str, to: str) -> bool:
    return to in ALLOWED_TRANSITIONS.get(frm, set())


@dataclass
class LoadView:
    id: str
    priority: str
    demand_kw: float
    flexible: bool = False
    max_curtail_kw: float = 0.0
    minimum_service_fraction: float = 0.0
    currently_shed_kw: float = 0.0


@dataclass
class LoadDecision:
    asset_id: str
    action: str       # SHED | CURTAIL | RESTORE | KEEP
    priority: str
    quantity_kw: float
    reason: str


@dataclass
class IslandPlan:
    supply_kw: float
    demand_kw: float
    served_kw: dict[str, float] = field(default_factory=dict)
    decisions: list[LoadDecision] = field(default_factory=list)
    critical_served: bool = True
    unserved_kw: float = 0.0


def plan_island_loads(loads: list[LoadView], supply_kw: float) -> IslandPlan:
    """Allocate limited supply (generation + battery + V2G, no imports) across loads."""
    total = sum(l.demand_kw for l in loads)
    plan = IslandPlan(supply_kw=supply_kw, demand_kw=total)
    served = {l.id: l.demand_kw for l in loads}
    deficit = total - supply_kw
    if deficit <= 1e-9:
        plan.served_kw = served
        plan.decisions = [LoadDecision(l.id, "KEEP", l.priority, 0.0, "SUPPLY_SUFFICIENT") for l in loads]
        return plan

    order = sorted(loads, key=lambda l: (-LOAD_PRIORITY_RANK[LoadPriority(l.priority)], l.id))  # least important first
    # 1) demand response: curtail flexible loads (compensated) first
    for l in order:
        if deficit <= 1e-9:
            break
        if l.flexible and l.max_curtail_kw > 0 and l.priority != LoadPriority.CRITICAL.value:
            floor = l.demand_kw * l.minimum_service_fraction
            cut = min(l.max_curtail_kw, max(0.0, served[l.id] - floor), deficit)
            if cut > 1e-9:
                served[l.id] -= cut
                deficit -= cut
                plan.decisions.append(LoadDecision(l.id, "CURTAIL", l.priority, cut, "ISLAND_SCARCITY_DEMAND_RESPONSE"))
    # 2) shed lowest priority first; never touch CRITICAL unless nothing else remains (then flag)
    for l in order:
        if deficit <= 1e-9:
            break
        if l.priority == LoadPriority.CRITICAL.value:
            continue
        shed = min(served[l.id], deficit)
        if shed > 1e-9:
            served[l.id] -= shed
            deficit -= shed
            plan.decisions.append(LoadDecision(l.id, "SHED", l.priority, shed,
                                               f"ISLAND_SCARCITY_SHED_{l.priority}"))
    if deficit > 1e-9:  # critical loads themselves underserved
        plan.critical_served = False
        for l in order:
            if l.priority == LoadPriority.CRITICAL.value and deficit > 1e-9:
                shed = min(served[l.id], deficit)
                served[l.id] -= shed
                deficit -= shed
                plan.decisions.append(LoadDecision(l.id, "SHED", l.priority, shed, "CRITICAL_LOAD_AT_RISK_INSUFFICIENT_SUPPLY"))
    plan.served_kw = served
    plan.unserved_kw = max(0.0, total - sum(served.values()))
    touched = {d.asset_id for d in plan.decisions}
    plan.decisions += [LoadDecision(l.id, "KEEP", l.priority, 0.0, "FULLY_SERVED") for l in loads if l.id not in touched]
    return plan


def plan_recovery_step(loads: list[LoadView], supply_kw: float, ramp_fraction: float = 0.34) -> list[LoadDecision]:
    """Restore shed load in priority order, bounded by available supply and a ramp limit
    (fraction of total demand per step) so the system is not shocked back to normal at once."""
    total = sum(l.demand_kw for l in loads)
    served_now = sum(l.demand_kw - l.currently_shed_kw for l in loads)
    headroom = max(0.0, supply_kw - served_now)
    ramp_budget = max(total * ramp_fraction, 0.0)
    budget = min(headroom, ramp_budget)
    out: list[LoadDecision] = []
    for l in sorted(loads, key=lambda l: (LOAD_PRIORITY_RANK[LoadPriority(l.priority)], l.id)):
        if budget <= 1e-9:
            break
        if l.currently_shed_kw > 1e-9:
            r = min(l.currently_shed_kw, budget)
            budget -= r
            out.append(LoadDecision(l.id, "RESTORE", l.priority, r, f"RECOVERY_RESTORE_{l.priority}"))
    return out
