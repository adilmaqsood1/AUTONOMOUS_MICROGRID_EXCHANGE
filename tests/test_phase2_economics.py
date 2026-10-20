"""Tests for Phase 2: Market Intelligence + Advanced Grid Economics.

Covers:
1. Nodal Locational Marginal Pricing (LMP) and feeder congestion rents.
2. Operating Reserve & Ancillary Services Market clearing.
3. Uncertainty-aware multi-scenario stochastic dispatch.
4. Nonlinear battery Depth-of-Discharge (DoD) degradation model.
5. Carbon-aware multi-objective optimization.
6. AI Market Surveillance & manipulation detection (withholding, under-delivery, z-score outlier, congestion gaming).
7. Live Microgrid Control Room telemetry.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
import pytest
from sqlalchemy import select

from app.core.timeutil import utcnow
from app.market.lmp import calculate_nodal_lmps
from app.market.reserve import clear_reserve_market, create_reserve_offer, list_reserve_commitments, list_reserve_offers
from app.models.assets import EnergyAsset
from app.models.core import GridLine, GridNode, Microgrid, Participant
from app.models.ops import EnergyCommitment, EnergyOrder
from app.optimization.dispatch import (
    DispatchProblem, ForecastScenario, ObjectiveWeights, OptBattery, OptEV, OptGen,
    OptGrid, OptLine, OptLoad, StochasticDispatchProblem, solve_dispatch, solve_stochastic_dispatch
)
from app.optimization.service import run_optimization, run_stochastic_optimization
from app.surveillance.engine import SurveillanceEngine
from app.surveillance.service import list_surveillance_alerts, run_surveillance_cycle
from app.analytics.service import get_control_room_telemetry


def test_dispatch_lmp_and_carbon_and_dod():
    """Test solver generates nodal LMPs, line congestion rents, carbon emissions, and DoD degradation."""
    p = DispatchProblem(
        nodes=["node_a", "node_b"],
        lines=[OptLine("line_1", "node_a", "node_b", capacity_kw=10.0)],
        steps=2,
        dt_hours=1.0,
        generators=[
            OptGen("gen_solar", "node_a", [20.0, 20.0], marginal_cost=0.02, renewable=True),
        ],
        batteries=[
            OptBattery("bat_1", "node_a", capacity_kwh=50.0, soc0_kwh=40.0, min_kwh=5.0, max_kwh=45.0,
                       max_charge_kw=10.0, max_discharge_kw=10.0, rte=0.9, degradation_cost=0.02),
        ],
        loads=[
            OptLoad("load_b", "node_b", [15.0, 15.0], priority="CRITICAL"),
        ],
        grid=OptGrid("node_a", import_limit_kw=50.0, export_limit_kw=20.0,
                     import_price=[0.10, 0.10], export_price=[0.05, 0.05]),
        weights=ObjectiveWeights.normal(),
    )
    res = solve_dispatch(p)
    assert res.status == "Optimal"
    assert "node_a" in res.nodal_lmp
    assert "node_b" in res.nodal_lmp
    # Feeder is loaded to capacity (10 kW limit with 15 kW demand at node_b)
    assert res.line_flow_kw["line_1"][0] <= 10.0 + 1e-4

    # Carbon emissions calculated
    assert res.carbon_emissions_kg >= 0.0
    assert res.carbon_cost >= 0.0

    # Nonlinear battery DoD percent and degradation cost calculated
    assert "bat_1" in res.battery_dod_percent
    assert "bat_1" in res.battery_dod_degradation_cost
    assert len(res.battery_dod_percent["bat_1"]) == 2


def test_stochastic_dispatch_cloud_drop():
    """Test multi-scenario stochastic dispatch hedges against solar cloud drops."""
    base = DispatchProblem(
        nodes=["node_a"],
        lines=[],
        steps=2,
        dt_hours=1.0,
        generators=[
            OptGen("solar_1", "node_a", [100.0, 100.0], marginal_cost=0.01, renewable=True),
        ],
        batteries=[
            OptBattery("bat_1", "node_a", capacity_kwh=100.0, soc0_kwh=50.0, min_kwh=10.0, max_kwh=90.0,
                       max_charge_kw=20.0, max_discharge_kw=20.0, rte=0.9, degradation_cost=0.02, reserve_kwh=10.0),
        ],
        loads=[
            OptLoad("comm_load", "node_a", [80.0, 80.0], priority="NORMAL"),
        ],
        grid=OptGrid("node_a", import_limit_kw=10.0, export_limit_kw=10.0,
                     import_price=[0.15, 0.15], export_price=[0.04, 0.04]),
    )
    # Define scenarios matching phase_2.md
    scenarios = [
        ForecastScenario("scenario_1_high", 0.25, {"solar_1": 1.15}),
        ForecastScenario("scenario_2_normal", 0.35, {"solar_1": 1.03}),
        ForecastScenario("scenario_3_dip", 0.25, {"solar_1": 0.91}),
        ForecastScenario("scenario_4_cloud_drop", 0.15, {"solar_1": 0.72}),
    ]
    stoch = StochasticDispatchProblem(base=base, scenarios=scenarios, risk_aversion=0.3)
    hedged_res, sc_dict = solve_stochastic_dispatch(stoch)

    assert hedged_res.status == "Optimal"
    assert "scenario_4_cloud_drop" in sc_dict
    assert len(sc_dict) == 5  # base + 4 scenarios


@pytest.mark.asyncio
async def test_reserve_market_procurement(session, demo):
    """Test creating reserve offers and clearing operating reserve market."""
    mg_id = demo["microgrid_id"]
    now = utcnow()
    ws = now + timedelta(minutes=15)
    we = ws + timedelta(minutes=15)

    # Find a battery asset
    assets = list((await session.execute(
        select(EnergyAsset).where(EnergyAsset.microgrid_id == mg_id)
    )).scalars())
    battery_asset = next(a for a in assets if a.battery is not None)

    # Create reserve offer within physical battery max discharge rating
    offered_cap = min(4.0, battery_asset.battery.maximum_discharge_kw)
    offer = await create_reserve_offer(
        session,
        participant_id=battery_asset.participant_id,
        asset_id=battery_asset.id,
        reserve_type="FREQUENCY_CONTAINMENT",
        capacity_kw=offered_cap,
        response_time_seconds=1.5,
        price_per_kw=Decimal("0.03"),
        ws=ws,
        we=we,
    )
    assert offer.id is not None
    assert offer.status == "OPEN"

    # List offers
    offers = await list_reserve_offers(session, microgrid_id=mg_id)
    assert any(o.id == offer.id for o in offers)

    # Clear reserve market for 3 kW
    clearing_res = await clear_reserve_market(
        session,
        microgrid_id=mg_id,
        required_capacity_kw=3.0,
        max_response_time_seconds=2.0,
        ws=ws,
        we=we,
    )
    assert clearing_res["procured_kw"] == 3.0
    assert clearing_res["clearing_price_per_kw"] == Decimal("0.03")
    assert len(clearing_res["commitments"]) == 1

    # Verify commitment was recorded
    comms = await list_reserve_commitments(session, microgrid_id=mg_id)
    assert len(comms) >= 1
    assert comms[0].allocated_kw == 3.0



@pytest.mark.asyncio
async def test_nodal_lmp_and_congestion_rents(session, demo):
    """Test calculation of live nodal LMPs and line congestion rents."""
    mg_id = demo["microgrid_id"]
    lmp_res = await calculate_nodal_lmps(session, mg_id)

    assert "nodal_lmps" in lmp_res
    assert len(lmp_res["nodal_lmps"]) > 0
    assert "lines" in lmp_res
    assert "total_congestion_rent" in lmp_res
    # All nodes have non-negative LMPs
    for n in lmp_res["nodal_lmps"]:
        assert n["lmp_per_kwh"] > 0.0


@pytest.mark.asyncio
async def test_ai_surveillance_engine(session, demo):
    """Test AI Market Surveillance detectors and audit cycles."""
    mg_id = demo["microgrid_id"]
    now = utcnow()

    # 1. Test Capacity Withholding detector
    assets = list((await session.execute(
        select(EnergyAsset).where(EnergyAsset.microgrid_id == mg_id)
    )).scalars())
    gen_asset = next(a for a in assets if a.generator is not None)

    # If open orders offer only 5% of capacity, it triggers capacity withholding
    open_orders = [
        EnergyOrder(
            id=uuid.uuid4(),
            participant_id=gen_asset.participant_id,
            asset_id=gen_asset.id,
            microgrid_id=mg_id,
            side="SELL",
            quantity_kwh=Decimal("1.0"),
            remaining_quantity_kwh=Decimal("1.0"),
            limit_price=Decimal("0.08"),
            delivery_window_start=now,
            delivery_window_end=now + timedelta(hours=1),
            created_at=now,
        )
    ]
    finding = SurveillanceEngine.detect_capacity_withholding(gen_asset, open_orders)
    if gen_asset.generator.maximum_generation_kw > 5.0:
        assert finding is not None
        assert finding.alert_type == "CAPACITY_WITHHOLDING"

    # 2. Test Persistent Under-Delivery detector
    part_id = gen_asset.participant_id
    bad_commitments = [
        EnergyCommitment(
            id=uuid.uuid4(), microgrid_id=mg_id, buyer_id=uuid.uuid4(), seller_id=part_id,
            buyer_asset_id=uuid.uuid4(), seller_asset_id=gen_asset.id, quantity_kwh=Decimal("10"),
            price=Decimal("0.05"), delivery_start=now, delivery_end=now + timedelta(hours=1),
            delivery_ratio=0.50, created_at=now - timedelta(hours=i),
        )
        for i in range(4)
    ]
    underdel_finding = SurveillanceEngine.detect_persistent_underdelivery(part_id, bad_commitments)
    assert underdel_finding is not None
    assert underdel_finding.alert_type == "PERSISTENT_UNDERDELIVERY"

    # 3. Test Abnormal Bidding Z-score
    normal_prices = [0.08, 0.082, 0.079, 0.081, 0.083, 0.080, 0.078, 0.082, 0.081, 0.080]
    outlier_order = EnergyOrder(
        id=uuid.uuid4(), participant_id=part_id, asset_id=gen_asset.id, microgrid_id=mg_id,
        side="SELL", quantity_kwh=Decimal("5"), remaining_quantity_kwh=Decimal("5"),
        limit_price=Decimal("0.45"), delivery_window_start=now, delivery_window_end=now + timedelta(hours=1),
        created_at=now,
    )
    abnormal_finding = SurveillanceEngine.detect_abnormal_bidding(outlier_order, normal_prices)
    assert abnormal_finding is not None
    assert abnormal_finding.alert_type == "ANOMALOUS_BID_PRICING"
    assert abnormal_finding.score > 3.0

    # 4. Run full surveillance cycle
    alerts = await run_surveillance_cycle(session, mg_id)
    assert isinstance(alerts, list)


@pytest.mark.asyncio
async def test_control_room_telemetry(session, demo):
    """Test live control room telemetry generation matching phase_2.md specifications."""
    mg_id = demo["microgrid_id"]
    telemetry = await get_control_room_telemetry(session, mg_id)

    assert telemetry["microgrid_id"] == mg_id
    assert telemetry["operating_mode"] in ("GRID_CONNECTED", "ISLANDED", "EMERGENCY")
    assert "system_frequency_hz" in telemetry
    assert 48.0 < telemetry["system_frequency_hz"] < 52.0
    assert "renewable_percent" in telemetry
    assert "battery_average_soc_percent" in telemetry
    assert "nodal_lmps" in telemetry
    assert "line_loadings_percent" in telemetry
    assert "total_reserve_procured_kw" in telemetry


@pytest.mark.asyncio
async def test_stochastic_optimization_service(session, demo):
    """Test full stochastic optimization service run."""
    mg_id = demo["microgrid_id"]
    run, hedged_res, sc_dict = await run_stochastic_optimization(session, mg_id, steps=2, step_minutes=30)
    assert run.id is not None
    assert hedged_res.status == "Optimal"
    assert "hedged" in run.result
    assert run.result["hedged"] is True
    assert "nodal_lmp" in run.result
    assert "battery_dod_percent" in run.result


# ==============================================================================
# Hand-Calculated Analytical LMP Benchmarks (Economics & Sign Convention Verification)
# ==============================================================================

def test_hand_calculated_lmp_uncongested_network():
    """Hand-calculated benchmark: Uncongested network with zero line congestion.
    
    Analytical Expectation:
      - Gen at A (marginal cost = $0.065/kWh) serves load at B (30 kW) with line cap = 100 kW.
      - Power flows without constraint; marginal cost to serve load at both A and B is $0.065/kWh.
      - LMP_A = LMP_B = $0.065/kWh.
      - Line congestion rent = $0.00.
    """
    w = ObjectiveWeights(cost_weight=1.0, renewable_weight=0.0, resilience_weight=0.0,
                         degradation_weight=0.0, grid_import_weight=0.0, unserved_weight=1.0,
                         carbon_weight=0.0)
    p = DispatchProblem(
        nodes=["Node_A", "Node_B"],
        lines=[OptLine("Line_AB", "Node_A", "Node_B", capacity_kw=100.0)],
        steps=1,
        dt_hours=0.5,  # 30-minute interval
        generators=[OptGen("Gen_A", "Node_A", [50.0], marginal_cost=0.065, renewable=False)],
        loads=[OptLoad("Load_B", "Node_B", [30.0], priority="NORMAL")],
        weights=w,
    )
    res = solve_dispatch(p)
    assert res.status == "Optimal"
    assert res.line_flow_kw["Line_AB"][0] == pytest.approx(30.0, abs=1e-4)

    # Hand-calculated LMP match
    assert res.nodal_lmp["Node_A"][0] == pytest.approx(0.065, abs=1e-4)
    assert res.nodal_lmp["Node_B"][0] == pytest.approx(0.065, abs=1e-4)
    assert res.line_congestion_rent["Line_AB"][0] == pytest.approx(0.0, abs=1e-4)


def test_hand_calculated_lmp_congested_feeder():
    """Hand-calculated benchmark: Constrained feeder with locational price divergence.
    
    Analytical Expectation:
      - Cheap Gen at Node A: $0.04/kWh, max 50 kW.
      - Expensive Gen at Node B: $0.15/kWh, max 50 kW.
      - Feeder Line A -> B: capacity limit = 10 kW.
      - Load at Node B: 25 kW.
      
      Optimal Flow & Generation:
        - 10 kW flows over Line A -> B (saturating the line at capacity limit).
        - 15 kW generated locally by Gen B at Node B.
      
      Locational Marginal Prices (Duals):
        - Incremental load at Node A is supplied by Gen A -> LMP_A = $0.04/kWh.
        - Incremental load at Node B CANNOT cross Line A -> B (saturated), so must be
          supplied by local Gen B -> LMP_B = $0.15/kWh.
        - Price Divergence: LMP_B - LMP_A = $0.11/kWh.
        - Line Congestion Rent = |LMP_B - LMP_A| * Flow * dt
                                = ($0.11/kWh) * (10 kW) * (0.5 h) = $0.55.
    """
    w = ObjectiveWeights(cost_weight=1.0, renewable_weight=0.0, resilience_weight=0.0,
                         degradation_weight=0.0, grid_import_weight=0.0, unserved_weight=1.0,
                         carbon_weight=0.0)
    p = DispatchProblem(
        nodes=["Node_A", "Node_B"],
        lines=[OptLine("Line_AB", "Node_A", "Node_B", capacity_kw=10.0)],
        steps=1,
        dt_hours=0.5,
        generators=[
            OptGen("Gen_A", "Node_A", [50.0], marginal_cost=0.04, renewable=False),
            OptGen("Gen_B", "Node_B", [50.0], marginal_cost=0.15, renewable=False),
        ],
        loads=[OptLoad("Load_B", "Node_B", [25.0], priority="NORMAL")],
        weights=w,
    )
    res = solve_dispatch(p)
    assert res.status == "Optimal"
    assert res.line_flow_kw["Line_AB"][0] == pytest.approx(10.0, abs=1e-4)
    assert res.generation_kwh["Gen_A"][0] == pytest.approx(10.0 * 0.5, abs=1e-4)
    assert res.generation_kwh["Gen_B"][0] == pytest.approx(15.0 * 0.5, abs=1e-4)

    # Exact hand-calculated nodal LMPs
    assert res.nodal_lmp["Node_A"][0] == pytest.approx(0.04, abs=1e-4)
    assert res.nodal_lmp["Node_B"][0] == pytest.approx(0.15, abs=1e-4)

    # Line congestion rent: (0.15 - 0.04) * 10 kW * 0.5 h = 0.55
    assert res.line_congestion_rent["Line_AB"][0] == pytest.approx(0.55, abs=1e-4)


def test_hand_calculated_lmp_marginal_cost_change():
    """Hand-calculated benchmark: Marginal-cost change shifts LMPs 1-to-1.
    
    Analytical Expectation:
      - Gen A marginal cost increases from $0.04 to $0.09/kWh.
      - Line remains saturated at 10 kW.
      - LMP_A increases to $0.09/kWh (1-to-1 shift).
      - LMP_B remains $0.15/kWh (Gen B is still marginal at B).
      - Divergence shrinks to $0.15 - $0.09 = $0.06/kWh.
      - Line Congestion Rent = ($0.06/kWh) * (10 kW) * (0.5 h) = $0.30.
    """
    w = ObjectiveWeights(cost_weight=1.0, renewable_weight=0.0, resilience_weight=0.0,
                         degradation_weight=0.0, grid_import_weight=0.0, unserved_weight=1.0,
                         carbon_weight=0.0)
    p = DispatchProblem(
        nodes=["Node_A", "Node_B"],
        lines=[OptLine("Line_AB", "Node_A", "Node_B", capacity_kw=10.0)],
        steps=1,
        dt_hours=0.5,
        generators=[
            OptGen("Gen_A", "Node_A", [50.0], marginal_cost=0.09, renewable=False),  # Increased to 0.09
            OptGen("Gen_B", "Node_B", [50.0], marginal_cost=0.15, renewable=False),
        ],
        loads=[OptLoad("Load_B", "Node_B", [25.0], priority="NORMAL")],
        weights=w,
    )
    res = solve_dispatch(p)
    assert res.status == "Optimal"
    assert res.nodal_lmp["Node_A"][0] == pytest.approx(0.09, abs=1e-4)
    assert res.nodal_lmp["Node_B"][0] == pytest.approx(0.15, abs=1e-4)
    assert res.line_congestion_rent["Line_AB"][0] == pytest.approx(0.30, abs=1e-4)


def test_hand_calculated_lmp_islanded_scarcity():
    """Hand-calculated benchmark: Islanded generation deficit causes LMP to equal VoLL.
    
    Analytical Expectation:
      - Islanded Node A with demand = 25 kW and Gen capacity = 15 kW.
      - Deficit of 10 kW cannot be served -> unserved energy = 10 kWh (for dt=1h).
      - Marginal unit to meet an additional increment of load is unserved load shed.
      - For NORMAL priority load, VoLL = $200.0/kWh.
      - LMP_A must equal $200.0/kWh exactly.
    """
    w = ObjectiveWeights(cost_weight=1.0, renewable_weight=0.0, resilience_weight=0.0,
                         degradation_weight=0.0, grid_import_weight=0.0, unserved_weight=1.0,
                         carbon_weight=0.0)
    p = DispatchProblem(
        nodes=["Node_A"],
        lines=[],
        steps=1,
        dt_hours=1.0,
        generators=[OptGen("Gen_A", "Node_A", [15.0], marginal_cost=0.08, renewable=False)],
        loads=[OptLoad("Load_A", "Node_A", [25.0], priority="NORMAL")],  # VoLL = 200.0
        weights=w,
    )
    res = solve_dispatch(p)
    assert res.status == "Optimal"
    assert res.unserved_kwh["Load_A"][0] == pytest.approx(10.0, abs=1e-4)
    # LMP equals Value of Lost Load penalty
    assert res.nodal_lmp["Node_A"][0] == pytest.approx(200.0, abs=1e-4)


def test_hand_calculated_lmp_dt_invariance():
    """Hand-calculated benchmark: Time-step (dt) scaling invariance.
    
    Analytical Expectation:
      - Energy price ($/kWh) must remain IDENTICAL whether dispatch runs at
        dt = 0.25h (15 min), dt = 0.5h (30 min), or dt = 1.0h (60 min).
      - Meanwhile, total congestion rent must scale linearly with energy duration dt.
    """
    w = ObjectiveWeights(cost_weight=1.0, renewable_weight=0.0, resilience_weight=0.0,
                         degradation_weight=0.0, grid_import_weight=0.0, unserved_weight=1.0,
                         carbon_weight=0.0)

    for dt in [0.25, 0.5, 1.0, 2.0]:
        p = DispatchProblem(
            nodes=["Node_A", "Node_B"],
            lines=[OptLine("Line_AB", "Node_A", "Node_B", capacity_kw=10.0)],
            steps=1,
            dt_hours=dt,
            generators=[
                OptGen("Gen_A", "Node_A", [50.0], marginal_cost=0.05, renewable=False),
                OptGen("Gen_B", "Node_B", [50.0], marginal_cost=0.18, renewable=False),
            ],
            loads=[OptLoad("Load_B", "Node_B", [25.0], priority="NORMAL")],
            weights=w,
        )
        res = solve_dispatch(p)
        assert res.nodal_lmp["Node_A"][0] == pytest.approx(0.05, abs=1e-4)
        assert res.nodal_lmp["Node_B"][0] == pytest.approx(0.18, abs=1e-4)
        # Rent = (0.18 - 0.05) * 10 * dt = 1.30 * dt
        assert res.line_congestion_rent["Line_AB"][0] == pytest.approx(1.30 * dt, abs=1e-4)

