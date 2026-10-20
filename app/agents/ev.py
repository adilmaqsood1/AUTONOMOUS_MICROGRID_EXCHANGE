"""EVAgent: smart charging and V2G that never endanger the owner's departure requirement."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.agents.base import D, Intent, MarketObservation, q6
from app.core.enums import AgentAction

EV_ETA = math.sqrt(0.9)  # one-way efficiency (charger/inverter + pack)


@dataclass
class EVState:
    capacity_kwh: float
    soc_percent: float
    required_soc_percent: float
    reserve_soc_percent: float
    departure_time: datetime | None
    max_charge_kw: float
    max_discharge_kw: float
    v2g_enabled: bool
    connected: bool
    allocated_discharge_kwh: float = 0.0

    @property
    def stored_kwh(self) -> float:
        return self.capacity_kwh * self.soc_percent / 100.0

    @property
    def required_kwh(self) -> float:
        return self.capacity_kwh * self.required_soc_percent / 100.0


def hours_after_window(e: EVState, window_end: datetime) -> float:
    if e.departure_time is None:
        return 24.0
    return max(0.0, (e.departure_time - window_end).total_seconds() / 3600.0)


def max_v2g_kwh(e: EVState, window_start: datetime, window_end: datetime) -> float:
    """Max kWh deliverable to the grid this window s.t. the departure SOC stays reachable.

    After discharging X (stored drops X/eta), the pack must be able to recharge to the
    required SOC in the remaining time: stored_after + eta*rate*hours_after >= required.
    Also never go below the owner's reserve SOC.
    """
    if not (e.v2g_enabled and e.connected) or e.max_discharge_kw <= 0:
        return 0.0
    hours = (window_end - window_start).total_seconds() / 3600.0
    if e.departure_time is not None and e.departure_time <= window_start:
        return 0.0
    h_after = hours_after_window(e, window_end)
    recharge_possible = EV_ETA * e.max_charge_kw * h_after
    cap_by_departure = EV_ETA * (e.stored_kwh + recharge_possible - e.required_kwh)
    cap_by_reserve = EV_ETA * (e.stored_kwh - e.capacity_kwh * e.reserve_soc_percent / 100.0)
    cap_by_power = e.max_discharge_kw * hours
    return max(0.0, min(cap_by_departure, cap_by_reserve, cap_by_power) - e.allocated_discharge_kwh)


def required_charge_now_kwh(e: EVState, window_start: datetime, window_end: datetime) -> float:
    """Minimum kWh drawn from the bus this window to keep departure feasible."""
    hours = (window_end - window_start).total_seconds() / 3600.0
    need_stored = max(0.0, e.required_kwh - e.stored_kwh)
    h_after = hours_after_window(e, window_end)
    later = EV_ETA * e.max_charge_kw * h_after
    must_store_now = max(0.0, need_stored - later)
    return min(must_store_now / EV_ETA, e.max_charge_kw * hours)


class EVAgent:
    name = "EVAgent"

    def decide(self, participant_id: str, asset_id: str, e: EVState, m: MarketObservation,
               deficit_kwh: float = 0.0) -> Intent:
        hours = m.hours
        cur, valley = float(m.expected_price), float(m.expected_valley_price)
        inputs = {"soc": round(e.soc_percent, 3), "required_soc": e.required_soc_percent,
                  "reserve_soc": e.reserve_soc_percent, "v2g_enabled": e.v2g_enabled,
                  "departure_time": e.departure_time.isoformat() if e.departure_time else None,
                  "market_price": str(m.expected_price), "deficit_kwh": round(deficit_kwh, 3)}
        pid = participant_id
        if not e.connected:
            return Intent(self.name, pid, asset_id, AgentAction.HOLD, reason_codes=["EV_NOT_CONNECTED"], inputs=inputs)

        must = required_charge_now_kwh(e, m.window_start, m.window_end)
        room = max(0.0, (e.capacity_kwh - e.stored_kwh) / EV_ETA)
        if must > 1e-6:
            inputs["required_charge_kwh"] = round(must, 4)
            return Intent(self.name, pid, asset_id, AgentAction.CHARGE, q6(D(min(must, room))),
                          m.import_price, ["DEPARTURE_REQUIREMENT"], inputs)

        v2g = max_v2g_kwh(e, m.window_start, m.window_end)
        if v2g > 1e-6 and (deficit_kwh > 0 or cur > valley / 0.9 + 0.05):
            qty = min(v2g, deficit_kwh) if deficit_kwh > 0 else v2g
            reasons = ["V2G_PERMITTED", "DEPARTURE_REQUIREMENT_SAFE"]
            reasons.append("SUPPLY_DEFICIT" if deficit_kwh > 0 else "PEAK_PRICE")
            inputs["v2g_available_kwh"] = round(v2g, 4)
            floor = Decimal(str(round(max(valley / 0.9 + 0.02, 0.0), 6)))
            return Intent(self.name, pid, asset_id, AgentAction.DISCHARGE, q6(D(qty)), floor, reasons, inputs)

        # Smart (opportunistic) charging when the price is at the look-ahead valley
        want = max(0.0, (e.required_kwh - e.stored_kwh)) / EV_ETA
        if want > 1e-6 and cur <= valley * 1.02:
            qty = min(want, room, e.max_charge_kw * hours)
            return Intent(self.name, pid, asset_id, AgentAction.CHARGE, q6(D(qty)),
                          Decimal(str(round(valley * 1.02, 6))), ["SMART_CHARGING_VALLEY_PRICE"], inputs)
        return Intent(self.name, pid, asset_id, AgentAction.HOLD, reason_codes=["WAIT_FOR_CHEAPER_PRICE"], inputs=inputs)
