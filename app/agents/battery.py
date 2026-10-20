"""BatteryAgent: arbitrage / surplus absorption / deficit support with degradation costs.

Efficiency convention: round_trip_efficiency (RTE) splits symmetrically:
eta_in = eta_out = sqrt(RTE). Energy drawn from the bus E_in stores E_in*eta_in;
delivering E_out to the bus consumes E_out/eta_out of stored energy.
`degradation_cost_per_kwh` is charged per kWh *delivered* (one cycle-throughput unit).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from app.agents.base import D, Intent, MarketObservation, q6
from app.core.enums import AgentAction


@dataclass
class BatteryState:
    capacity_kwh: float
    soc_percent: float
    min_soc_percent: float
    max_soc_percent: float
    max_charge_kw: float
    max_discharge_kw: float
    rte: float
    degradation_cost_per_kwh: float
    allocated_discharge_kwh: float = 0.0
    allocated_charge_kwh: float = 0.0
    available: bool = True

    @property
    def eta(self) -> float:
        return math.sqrt(max(min(self.rte, 1.0), 1e-6))

    @property
    def stored_kwh(self) -> float:
        return self.capacity_kwh * self.soc_percent / 100.0


def deliverable_discharge_kwh(b: BatteryState, hours: float, reserve_soc_percent: float | None = None) -> float:
    """kWh deliverable to the bus this window without breaching reserve; net of allocations."""
    if not b.available:
        return 0.0
    floor = max(b.min_soc_percent, reserve_soc_percent or 0.0)
    usable_stored = max(0.0, b.stored_kwh - b.capacity_kwh * floor / 100.0)
    by_energy = usable_stored * b.eta
    by_power = b.max_discharge_kw * hours
    return max(0.0, min(by_energy, by_power) - b.allocated_discharge_kwh)


def acceptable_charge_kwh(b: BatteryState, hours: float) -> float:
    """kWh drawable from the bus this window without exceeding max SOC; net of allocations."""
    if not b.available:
        return 0.0
    room = max(0.0, b.capacity_kwh * b.max_soc_percent / 100.0 - b.stored_kwh)
    by_energy = room / b.eta
    by_power = b.max_charge_kw * hours
    return max(0.0, min(by_energy, by_power) - b.allocated_charge_kwh)


class BatteryAgent:
    name = "BatteryAgent"
    MIN_MARGIN = 0.005  # currency/kWh: required profit over costs to act

    def __init__(self, reserve_extra_percent: float = 10.0):
        self.reserve_extra_percent = reserve_extra_percent

    def decide(self, participant_id: str, asset_id: str, b: BatteryState, m: MarketObservation,
               surplus_kwh: float = 0.0, deficit_kwh: float = 0.0) -> Intent:
        hours = m.hours
        reserve_soc = b.min_soc_percent + self.reserve_extra_percent
        cur = float(m.expected_price)
        peak = float(m.expected_peak_price)
        valley = float(m.expected_valley_price)
        inputs = {
            "soc": round(b.soc_percent, 3), "minimum_reserve": round(reserve_soc, 3),
            "market_price": str(m.expected_price), "expected_peak_price": str(m.expected_peak_price),
            "expected_valley_price": str(m.expected_valley_price), "surplus_kwh": round(surplus_kwh, 3),
            "deficit_kwh": round(deficit_kwh, 3), "degradation_cost_per_kwh": b.degradation_cost_per_kwh,
            "round_trip_efficiency": b.rte,
        }
        pid = participant_id
        if not b.available:
            return Intent(self.name, pid, asset_id, AgentAction.HOLD, reason_codes=["ASSET_UNAVAILABLE"], inputs=inputs)

        can_dis = deliverable_discharge_kwh(b, hours, reserve_soc)
        can_chg = acceptable_charge_kwh(b, hours)
        degr = b.degradation_cost_per_kwh

        # 1) Discharge: supply deficit or price above (recharge cost + degradation)
        discharge_margin = cur - degr - valley / b.rte
        if can_dis > 1e-6 and (deficit_kwh > 0 or discharge_margin > self.MIN_MARGIN):
            reasons = []
            qty = can_dis
            if deficit_kwh > 0:
                reasons.append("SUPPLY_DEFICIT")
                qty = min(qty, deficit_kwh)
            if discharge_margin > self.MIN_MARGIN:
                reasons.append("PEAK_PRICE")
            reasons.append("SOC_ABOVE_RESERVE")
            floor_price = Decimal(str(round(max(valley / b.rte + degr, degr), 6)))
            inputs["discharge_margin"] = round(discharge_margin, 6)
            return Intent(self.name, pid, asset_id, AgentAction.DISCHARGE, q6(D(qty)), floor_price, reasons, inputs)

        # 2) Charge: absorb renewable surplus, or arbitrage vs expected peak
        arb_profit = peak - degr - cur / b.rte
        if can_chg > 1e-6 and (surplus_kwh > 0 or arb_profit > self.MIN_MARGIN):
            reasons = []
            qty = can_chg
            if surplus_kwh > 0:
                reasons.append("RENEWABLE_SURPLUS")
                qty = min(qty, surplus_kwh)
            if arb_profit > self.MIN_MARGIN:
                reasons.append("ARBITRAGE_PROFITABLE")
                if surplus_kwh <= 0:
                    qty = min(qty, can_chg)
            inputs["arbitrage_profit_per_kwh"] = round(arb_profit, 6)
            # willing to pay up to the break-even price (profit after degradation > 0)
            max_price = max((peak - degr) * b.rte, 0.0)
            return Intent(self.name, pid, asset_id, AgentAction.CHARGE, q6(D(qty)),
                          Decimal(str(round(max_price, 6))), reasons, inputs)

        reasons = ["NO_PROFITABLE_ACTION"]
        if arb_profit <= self.MIN_MARGIN and peak > cur:
            reasons.append("DEGRADATION_COST_EXCEEDS_SPREAD")
        inputs["arbitrage_profit_per_kwh"] = round(arb_profit, 6)
        return Intent(self.name, pid, asset_id, AgentAction.HOLD, reason_codes=reasons, inputs=inputs)
