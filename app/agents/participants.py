"""Generator, prosumer, consumer, commercial and grid agents."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.agents.base import D, Intent, MarketObservation, q6
from app.core.enums import AgentAction
from app.forecasting.reserve import ReservePolicy, tradable_capacity


@dataclass
class GenerationView:
    rated_kw: float
    maximum_kw: float
    marginal_cost: float
    renewable: bool
    predicted_kwh: float
    lower_kwh: float
    available: bool = True
    already_committed_kwh: float = 0.0


class GeneratorAgent:
    name = "GeneratorAgent"

    def __init__(self, policy: ReservePolicy = ReservePolicy()):
        self.policy = policy

    def decide(self, participant_id: str, asset_id: str, g: GenerationView, m: MarketObservation) -> Intent:
        tc = tradable_capacity(g.predicted_kwh, g.lower_kwh, self.policy)
        physical = g.maximum_kw * m.hours
        qty = max(Decimal("0"), min(tc.tradable_kwh, D(physical)) - D(g.already_committed_kwh))
        inputs = {"predicted_kwh": float(tc.predicted_kwh), "uncertainty_reserve_kwh": float(tc.uncertainty_reserve_kwh),
                  "operational_reserve_kwh": float(tc.operational_reserve_kwh),
                  "tradable_kwh": float(tc.tradable_kwh), "marginal_cost": g.marginal_cost,
                  "market_price": str(m.expected_price)}
        if not g.available:
            return Intent(self.name, participant_id, asset_id, AgentAction.HOLD, reason_codes=["ASSET_UNAVAILABLE"], inputs=inputs)
        floor = max(D(g.marginal_cost) * Decimal("1.02"), m.export_price)
        if qty <= 0:
            return Intent(self.name, participant_id, asset_id, AgentAction.HOLD, reason_codes=["NO_TRADABLE_ENERGY"], inputs=inputs)
        if m.expected_price < D(g.marginal_cost):
            return Intent(self.name, participant_id, asset_id, AgentAction.CURTAIL,
                          reason_codes=["PRICE_BELOW_MARGINAL_COST"], inputs=inputs)
        return Intent(self.name, participant_id, asset_id, AgentAction.SELL, q6(qty), q6(floor),
                      ["FORECAST_RISK_ADJUSTED_SURPLUS"], inputs, renewable_only=False)


class ConsumerAgent:
    """Buys its (non-flexible) demand up to the utility import price; the grid is the fallback."""
    name = "ConsumerAgent"

    def decide(self, participant_id: str, asset_id: str, demand_kwh: float, m: MarketObservation,
               flexible: bool = False, renewable_preference: bool = False) -> Intent:
        inputs = {"demand_kwh": round(demand_kwh, 4), "import_price": str(m.import_price),
                  "market_price": str(m.expected_price)}
        if demand_kwh <= 1e-6:
            return Intent(self.name, participant_id, asset_id, AgentAction.HOLD, reason_codes=["NO_DEMAND"], inputs=inputs)
        limit = m.import_price * (Decimal("0.9") if flexible else Decimal("0.98"))
        if m.island_mode:
            limit = m.import_price * Decimal("1.5")  # no fallback import: willing to pay more
        return Intent(self.name, participant_id, asset_id, AgentAction.BUY, q6(D(demand_kwh)), q6(limit),
                      ["DEMAND_COVERAGE"] + (["FLEXIBLE_LOWER_LIMIT"] if flexible else []),
                      inputs, renewable_only=renewable_preference)


class CommercialAgent:
    """Splits demand into firm and flexible portions; curtails flexible load when price is high."""
    name = "CommercialAgent"

    def __init__(self, curtail_price_threshold: Decimal = Decimal("0.20")):
        self.threshold = curtail_price_threshold

    def decide(self, participant_id: str, asset_id: str, demand_kwh: float, flexible_fraction: float,
               m: MarketObservation) -> list[Intent]:
        flex = demand_kwh * flexible_fraction
        firm = demand_kwh - flex
        out: list[Intent] = []
        inputs = {"demand_kwh": round(demand_kwh, 4), "flexible_fraction": flexible_fraction,
                  "market_price": str(m.expected_price)}
        if firm > 1e-6:
            out.append(Intent(self.name, participant_id, asset_id, AgentAction.BUY, q6(D(firm)),
                              q6(m.import_price * Decimal("0.98")), ["FIRM_DEMAND"], inputs))
        if flex > 1e-6:
            if m.expected_price >= self.threshold:
                out.append(Intent(self.name, participant_id, asset_id, AgentAction.CURTAIL, q6(D(flex)), None,
                                  ["PRICE_ABOVE_CURTAIL_THRESHOLD"], inputs))
            else:
                out.append(Intent(self.name, participant_id, asset_id, AgentAction.BUY, q6(D(flex)),
                                  q6(m.import_price * Decimal("0.85")), ["FLEXIBLE_DEMAND_LOW_LIMIT"], inputs))
        return out


class ProsumerAgent:
    """Aggregates own generation vs demand: SELL surplus (forecast-risk adjusted), BUY deficit."""
    name = "ProsumerAgent"

    def __init__(self, policy: ReservePolicy = ReservePolicy()):
        self.policy = policy

    def decide(self, participant_id: str, gen_asset_id: str | None, load_asset_id: str | None,
               predicted_gen_kwh: float, lower_gen_kwh: float, demand_kwh: float,
               m: MarketObservation, battery_room_kwh: float = 0.0, marginal_cost: float = 0.0) -> list[Intent]:
        tc = tradable_capacity(predicted_gen_kwh, lower_gen_kwh, self.policy)
        net = float(tc.tradable_kwh) - demand_kwh
        inputs = {"tradable_generation_kwh": float(tc.tradable_kwh), "demand_kwh": round(demand_kwh, 4),
                  "net_kwh": round(net, 4), "market_price": str(m.expected_price)}
        if net > 1e-6:
            if battery_room_kwh > 1e-6 and m.expected_peak_price > m.expected_price * Decimal("1.5"):
                store = min(net, battery_room_kwh)
                out = [Intent(self.name, participant_id, gen_asset_id, AgentAction.STORE, q6(D(store)), None,
                              ["SURPLUS_STORED_FOR_PEAK"], inputs)]
                net -= store
                if net <= 1e-6:
                    return out
            else:
                out = []
            floor = max(D(marginal_cost) * Decimal("1.02"), m.export_price)
            out.append(Intent(self.name, participant_id, gen_asset_id, AgentAction.SELL, q6(D(net)), q6(floor),
                              ["SURPLUS_AFTER_OWN_DEMAND"], inputs))
            return out
        if net < -1e-6:
            return [Intent(self.name, participant_id, load_asset_id, AgentAction.BUY, q6(D(-net)),
                           q6(m.import_price * Decimal("0.98")), ["DEFICIT_AFTER_OWN_GENERATION"], inputs)]
        return [Intent(self.name, participant_id, gen_asset_id, AgentAction.HOLD, reason_codes=["BALANCED"], inputs=inputs)]


class GridAgent:
    """Represents the utility tie: standing offer to sell at import price / buy at export price."""
    name = "GridAgent"

    def decide(self, participant_id: str, asset_id: str, import_headroom_kw: float,
               export_headroom_kw: float, m: MarketObservation) -> list[Intent]:
        inputs = {"import_headroom_kw": import_headroom_kw, "export_headroom_kw": export_headroom_kw,
                  "grid_connected": m.grid_connected}
        if not m.grid_connected or m.island_mode:
            return [Intent(self.name, participant_id, asset_id, AgentAction.HOLD,
                           reason_codes=["EXTERNAL_GRID_UNAVAILABLE"], inputs=inputs)]
        return [
            Intent(self.name, participant_id, asset_id, AgentAction.SELL,
                   q6(D(import_headroom_kw * m.hours)), q6(m.import_price), ["UTILITY_SUPPLY_OFFER"], inputs),
            Intent(self.name, participant_id, asset_id, AgentAction.BUY,
                   q6(D(export_headroom_kw * m.hours)), q6(m.export_price), ["UTILITY_FEED_IN_BID"], inputs),
        ]
