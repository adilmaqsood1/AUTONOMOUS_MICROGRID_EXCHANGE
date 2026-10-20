"""Deterministic pre-trade risk engine. Every proposed commitment must pass through here.

Each rule is a pure function returning a *limit* in kWh (how much of the request it permits).
approved = min(requested, all limits). The binding rule (smallest limit; ties resolved by the
canonical order below) provides the reason code. All rule outputs are retained for audit.
Reputation can only tighten constraints; it never relaxes physical limits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from app.core.enums import RiskCode

ZERO = Decimal("0")
INF = Decimal("Infinity")


@dataclass
class RiskInput:
    requested_kwh: Decimal
    price: Decimal
    seller_physical_capacity_kwh: Decimal | None = None   # rated power * window - already committed
    seller_forecast_tradable_kwh: Decimal | None = None    # forecast-risk adjusted - already committed
    battery_available_kwh: Decimal | None = None           # deliverable above reserve, net of allocations
    ev_available_kwh: Decimal | None = None                # deliverable w/o breaking departure SOC
    grid_available_kwh: Decimal | None = None              # path capacity over the window
    seller_reliability: float = 1.0
    min_reliability: float = 0.3
    buyer_collateral_available: Decimal | None = None
    seller_collateral_available: Decimal | None = None
    collateral_fraction: Decimal = Decimal("1.0")
    grid_import_headroom_kwh: Decimal | None = None        # only when seller is the utility tie
    reserve_margin_headroom_kwh: Decimal | None = None     # system-level spinning reserve
    extra: dict = field(default_factory=dict)


@dataclass
class CheckResult:
    code: RiskCode
    limit_kwh: Decimal | None  # None == not applicable
    passed: bool
    message: str


@dataclass
class RiskDecisionResult:
    approved: bool
    reason_code: str
    message: str
    requested_kwh: Decimal
    approved_kwh: Decimal
    checks: list[CheckResult]

    def as_dict(self) -> dict:
        return {
            "approved": self.approved, "reason_code": self.reason_code, "message": self.message,
            "requested_kwh": float(self.requested_kwh), "approved_kwh": float(self.approved_kwh),
            "checks": [{"code": c.code.value, "limit_kwh": None if c.limit_kwh is None else float(c.limit_kwh),
                        "passed": c.passed, "message": c.message} for c in self.checks],
        }


def _limit(code: RiskCode, req: Decimal, limit: Decimal | None, msg_fail: str, msg_ok: str) -> CheckResult:
    if limit is None:
        return CheckResult(code, None, True, "not applicable")
    limit = max(limit, ZERO)
    ok = limit >= req
    return CheckResult(code, limit, ok, msg_ok if ok else msg_fail)


def rule_seller_capacity(i: RiskInput):
    return _limit(RiskCode.SELLER_CAPACITY, i.requested_kwh, i.seller_physical_capacity_kwh,
                  "Seller lacks physical capacity for the delivery window", "within physical capacity")


def rule_forecast_risk(i: RiskInput):
    return _limit(RiskCode.FORECAST_RISK, i.requested_kwh, i.seller_forecast_tradable_kwh,
                  "Request exceeds forecast-risk adjusted tradable energy", "within forecast-safe energy")


def rule_battery_reserve(i: RiskInput):
    return _limit(RiskCode.BATTERY_RESERVE, i.requested_kwh, i.battery_available_kwh,
                  "Discharge would violate battery reserve / allocation", "battery reserve preserved")


def rule_ev_departure(i: RiskInput):
    return _limit(RiskCode.EV_DEPARTURE_REQUIREMENT, i.requested_kwh, i.ev_available_kwh,
                  "Sale would make EV departure SOC requirement infeasible", "EV departure SOC safe")


def rule_grid_congestion(i: RiskInput):
    return _limit(RiskCode.GRID_CONGESTION, i.requested_kwh, i.grid_available_kwh,
                  "Requested transfer exceeds available path capacity", "path capacity available")


def rule_reliability(i: RiskInput):
    ok = i.seller_reliability >= i.min_reliability
    return CheckResult(RiskCode.PARTICIPANT_RELIABILITY, None if ok else ZERO, ok,
                       "reliability acceptable" if ok else
                       f"Seller reliability {i.seller_reliability:.2f} below minimum {i.min_reliability:.2f}")


def rule_collateral(i: RiskInput):
    # buyer must fund price * qty; seller must post collateral fraction of notional
    if i.buyer_collateral_available is None and i.seller_collateral_available is None:
        return CheckResult(RiskCode.COLLATERAL, None, True, "not applicable")
    limits = []
    if i.price > 0:
        if i.buyer_collateral_available is not None:
            limits.append(i.buyer_collateral_available / i.price)
        if i.seller_collateral_available is not None and i.collateral_fraction > 0:
            limits.append(i.seller_collateral_available / (i.price * i.collateral_fraction))
    lim = min(limits) if limits else INF
    return _limit(RiskCode.COLLATERAL, i.requested_kwh, lim, "Insufficient collateral/funds",
                  "collateral sufficient")


def rule_grid_import(i: RiskInput):
    return _limit(RiskCode.GRID_IMPORT_LIMIT, i.requested_kwh, i.grid_import_headroom_kwh,
                  "External grid import limit exceeded", "within import limit")


def rule_reserve_margin(i: RiskInput):
    return _limit(RiskCode.RESERVE_MARGIN, i.requested_kwh, i.reserve_margin_headroom_kwh,
                  "Commitment would erode required system reserve margin", "reserve margin preserved")


RULES: list[Callable[[RiskInput], CheckResult]] = [
    rule_seller_capacity, rule_forecast_risk, rule_battery_reserve, rule_ev_departure,
    rule_grid_congestion, rule_reliability, rule_collateral, rule_grid_import, rule_reserve_margin,
]


def evaluate_risk(i: RiskInput) -> RiskDecisionResult:
    checks = [r(i) for r in RULES]
    limits = [(c.limit_kwh, idx, c) for idx, c in enumerate(checks) if c.limit_kwh is not None]
    approved_kwh = min([i.requested_kwh] + [l for l, _, _ in limits])
    approved_kwh = max(approved_kwh, ZERO)
    if approved_kwh >= i.requested_kwh:
        return RiskDecisionResult(True, RiskCode.APPROVED.value, "All risk checks passed",
                                  i.requested_kwh, i.requested_kwh, checks)
    binding = min(((l, idx, c) for l, idx, c in limits if l < i.requested_kwh), key=lambda t: (t[0], t[1]))[2]
    return RiskDecisionResult(False, binding.code.value, binding.message, i.requested_kwh, approved_kwh, checks)
