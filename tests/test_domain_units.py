from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from app.agents.base import MarketObservation
from app.agents.battery import BatteryAgent, BatteryState, deliverable_discharge_kwh
from app.agents.ev import EVAgent, EVState, max_v2g_kwh, required_charge_now_kwh
from app.balancing.engine import BalancingCandidate, balance_deficit
from app.core.enums import AgentAction, BalancingSource
from app.forecasting.reserve import ReservePolicy, tradable_capacity
from app.grid.network import LineState, NetworkFlowFeasibility
from app.risk.engine import RiskInput, evaluate_risk
from app.settlement.calc import compute_settlement

T0 = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)


def obs(cur="0.07", peak="0.20", valley="0.07", hours=1):
    return MarketObservation(T0, T0 + timedelta(hours=hours), D("0.22"), D("0.05"), D(cur), D(peak), D(valley))


# ---------------------------------------------------------------- forecast reserve
def test_tradable_capacity_matches_spec_example():
    tc = tradable_capacity(50.0, 44.0, ReservePolicy(D("1.0"), D("0.08")))
    assert tc.uncertainty_reserve_kwh == D("6") and tc.operational_reserve_kwh == D("4")
    assert tc.tradable_kwh == D("40")
    assert tradable_capacity(10.0, 0.0).tradable_kwh == D("0")  # never negative


# ---------------------------------------------------------------- risk
def test_risk_partial_approval_reports_binding_rule():
    r = evaluate_risk(RiskInput(requested_kwh=D(40), price=D("0.1"), grid_available_kwh=D(20),
                                seller_physical_capacity_kwh=D(100)))
    assert not r.approved and r.reason_code == "GRID_CONGESTION"
    assert r.approved_kwh == D(20) and r.requested_kwh == D(40)


def test_risk_reliability_and_collateral_and_all_pass():
    assert evaluate_risk(RiskInput(D(5), D("0.1"), seller_reliability=0.1)).reason_code == "PARTICIPANT_RELIABILITY"
    r = evaluate_risk(RiskInput(D(50), D("0.1"), buyer_collateral_available=D("2.0")))
    assert r.reason_code == "COLLATERAL" and r.approved_kwh == D(20)
    ok = evaluate_risk(RiskInput(D(5), D("0.1"), seller_physical_capacity_kwh=D(9), grid_available_kwh=D(9)))
    assert ok.approved and ok.reason_code == "APPROVED"
    assert any(c.code.value == "GRID_CONGESTION" for c in ok.checks)  # all rule outputs retained


# ---------------------------------------------------------------- battery
def batt(soc=42.0, **kw):
    d = dict(capacity_kwh=40, soc_percent=soc, min_soc_percent=20, max_soc_percent=95, max_charge_kw=8,
             max_discharge_kw=8, rte=0.9, degradation_cost_per_kwh=0.02)
    d.update(kw)
    return BatteryState(**d)


def test_battery_charges_for_profitable_arbitrage_and_explains():
    i = BatteryAgent().decide("p", "a", batt(), obs("0.07", "0.20", "0.07"), surplus_kwh=20)
    assert i.action == AgentAction.CHARGE and i.quantity_kwh == D("8")
    assert "ARBITRAGE_PROFITABLE" in i.reason_codes and i.inputs["soc"] == 42.0


def test_battery_degradation_kills_thin_spread():
    i = BatteryAgent().decide("p", "a", batt(), obs("0.10", "0.115", "0.10"))
    assert i.action == AgentAction.HOLD and "DEGRADATION_COST_EXCEEDS_SPREAD" in i.reason_codes


def test_battery_never_discharges_below_reserve():
    b = batt(soc=31.0)  # reserve = 20 + 10 = 30 -> only 1% (0.4 kWh stored) usable
    assert deliverable_discharge_kwh(b, 1.0, 30.0) < 0.4
    i = BatteryAgent().decide("p", "a", b, obs("0.25", "0.25", "0.07"), deficit_kwh=27)
    assert i.action == AgentAction.DISCHARGE and float(i.quantity_kwh) < 0.4
    b2 = batt(soc=30.0)
    assert BatteryAgent().decide("p", "a", b2, obs("0.25", "0.25", "0.07"), deficit_kwh=27).action != AgentAction.DISCHARGE


def test_battery_discharge_on_deficit_with_reason_codes():
    i = BatteryAgent().decide("p", "a", batt(soc=81.0), obs("0.21", "0.21", "0.07"), deficit_kwh=27)
    assert i.action == AgentAction.DISCHARGE
    assert {"SUPPLY_DEFICIT", "PEAK_PRICE", "SOC_ABOVE_RESERVE"} <= set(i.reason_codes)
    assert i.quantity_kwh == D("8")  # power-limited (8 kW * 1 h)


# ---------------------------------------------------------------- EV
def ev(**kw):
    d = dict(capacity_kwh=60, soc_percent=80, required_soc_percent=80, reserve_soc_percent=20,
             departure_time=T0 + timedelta(hours=1), max_charge_kw=7.4, max_discharge_kw=7.4,
             v2g_enabled=True, connected=True)
    d.update(kw)
    return EVState(**d)


def test_ev_cannot_sell_when_departure_requirement_binding():
    e = ev()  # at required SOC, leaving at end of window: cannot sell anything
    assert max_v2g_kwh(e, T0, T0 + timedelta(hours=1)) == 0.0
    assert EVAgent().decide("p", "e", e, obs("0.3", "0.3", "0.05")).action != AgentAction.DISCHARGE


def test_ev_can_sell_surplus_above_requirement_and_recharge_slack():
    e = ev(soc_percent=90, departure_time=T0 + timedelta(hours=10))
    v = max_v2g_kwh(e, T0, T0 + timedelta(hours=1))
    assert 0 < v <= 7.4
    e2 = ev(soc_percent=50, departure_time=T0 + timedelta(hours=3))
    must = required_charge_now_kwh(e2, T0, T0 + timedelta(hours=1))
    assert must > 0  # 18 kWh needed, only 2 further hours of charging left


def test_ev_without_v2g_permission_never_discharges():
    e = ev(soc_percent=95, v2g_enabled=False, departure_time=T0 + timedelta(hours=10))
    assert max_v2g_kwh(e, T0, T0 + timedelta(hours=1)) == 0.0


# ---------------------------------------------------------------- settlement
def test_settlement_partial_delivery_example_65_percent():
    s = compute_settlement(D("20"), D("13"), D("0.10"), D("2.0"), D("1.5"))
    assert s.delivery_ratio == D("0.65") and s.status == "PARTIAL"
    assert s.seller_payment == D("1.3") and s.buyer_refund == D("0.7")
    assert s.seller_payment + s.buyer_refund == s.gross_amount
    assert s.penalty == D("1.05") and s.collateral_release == D("0.95")


def test_settlement_no_delivery_no_payment_and_over_delivery_capped():
    s = compute_settlement(D("10"), D("0"), D("0.2"), D("2"), D("1.5"))
    assert s.seller_payment == 0 and s.status == "FAILED" and s.penalty == D("2")  # capped at collateral
    s = compute_settlement(D("10"), D("12"), D("0.2"), D("2"), D("1.5"))
    assert s.delivered_kwh == D("10") and s.penalty == 0 and s.status == "DELIVERED"


# ---------------------------------------------------------------- balancing
def test_balancing_orders_by_cost_respects_grid_and_reports_unmet():
    net = NetworkFlowFeasibility([LineState("l1", "BATT", "DEF", 10.0), LineState("l2", "GRIDN", "DEF", 100.0)])
    cands = [
        BalancingCandidate(BalancingSource.COMMUNITY_BATTERY, "bat", "BATT", D(20), D("0.10"), 1),
        BalancingCandidate(BalancingSource.EXTERNAL_GRID, "grid", "GRIDN", D(100), D("0.26"), 5),
        BalancingCandidate(BalancingSource.DEMAND_RESPONSE, "dr", "DEF", D(2), D("0.05"), 30),
    ]
    r = balance_deficit(D(27), "DEF", cands, net, 1.0)
    got = [(a.source_type, a.quantity_kwh) for a in r.allocations]
    assert got == [(BalancingSource.DEMAND_RESPONSE, D(2)), (BalancingSource.COMMUNITY_BATTERY, D(10)),
                   (BalancingSource.EXTERNAL_GRID, D(15))]
    assert r.unmet_kwh == 0 and r.allocations[1].grid_limited
    assert r.total_cost == D("0.1") + D("1.0") + D("3.9")
    # islanded: no external grid candidate -> unmet deficit reported honestly
    net2 = NetworkFlowFeasibility([LineState("l1", "BATT", "DEF", 10.0), LineState("l2", "GRIDN", "DEF", 100.0)])
    r2 = balance_deficit(D(27), "DEF", cands[:1], net2, 1.0)
    assert r2.unmet_kwh == D(17)
