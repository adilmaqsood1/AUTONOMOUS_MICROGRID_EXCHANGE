"""Real-time balancing market: replace delivery deficits with the best feasible sources.

Candidate ranking (deterministic): score = price * (1 + 0.5*(1-reliability)) + response_h * 0.01.
Each allocation is validated against grid feasibility (path capacity from source to deficit node).
Balancing actions are recorded separately from day-ahead/intraday trades.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.core.enums import BalancingSource
from app.grid.network import GridFeasibility

QTY = Decimal("0.000001")


@dataclass
class BalancingCandidate:
    source_type: BalancingSource
    asset_id: str | None
    node_id: str
    available_kwh: Decimal
    price: Decimal                 # currency / kWh
    response_seconds: float = 60.0
    reliability: float = 1.0
    participant_id: str | None = None

    @property
    def score(self) -> float:
        return float(self.price) * (1 + 0.5 * (1 - self.reliability)) + (self.response_seconds / 3600.0) * 0.01


@dataclass
class BalancingAllocation:
    source_type: BalancingSource
    asset_id: str | None
    participant_id: str | None
    quantity_kwh: Decimal
    price: Decimal
    cost: Decimal
    score: float
    grid_limited: bool = False


@dataclass
class BalancingResult:
    deficit_kwh: Decimal
    allocations: list[BalancingAllocation] = field(default_factory=list)
    unmet_kwh: Decimal = Decimal("0")
    skipped: list[dict] = field(default_factory=list)

    @property
    def total_cost(self) -> Decimal:
        return sum((a.cost for a in self.allocations), Decimal("0"))

    @property
    def covered_kwh(self) -> Decimal:
        return sum((a.quantity_kwh for a in self.allocations), Decimal("0"))


def balance_deficit(deficit_kwh: Decimal, deficit_node: str, candidates: list[BalancingCandidate],
                    network: GridFeasibility | None, window_hours: float) -> BalancingResult:
    res = BalancingResult(deficit_kwh=deficit_kwh)
    remaining = deficit_kwh
    ordered = sorted(candidates, key=lambda c: (c.score, str(c.asset_id), c.source_type.value))
    for c in ordered:
        if remaining <= 0:
            break
        if c.available_kwh <= 0:
            res.skipped.append({"asset_id": c.asset_id, "reason": "NO_AVAILABLE_ENERGY"})
            continue
        qty = min(remaining, c.available_kwh)
        limited = False
        if network is not None and c.node_id != deficit_node:
            tr = network.request_transfer(c.node_id, deficit_node, float(qty) / window_hours, commit=True)
            accepted = (Decimal(str(round(tr.accepted_kw, 9))) * Decimal(str(window_hours))).quantize(QTY)
            if accepted < qty:
                limited = True
            qty = min(qty, accepted)
            if qty <= 0:
                res.skipped.append({"asset_id": c.asset_id, "reason": "GRID_CONGESTION"})
                continue
        qty = qty.quantize(QTY)
        res.allocations.append(BalancingAllocation(c.source_type, c.asset_id, c.participant_id, qty,
                                                   c.price, (qty * c.price).quantize(Decimal("0.000001")),
                                                   c.score, limited))
        remaining -= qty
    res.unmet_kwh = max(remaining, Decimal("0"))
    return res
