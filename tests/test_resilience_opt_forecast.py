from datetime import datetime, timedelta, timezone

from app.forecasting import models as fm
from app.optimization.dispatch import (DispatchProblem, ObjectiveWeights, OptBattery, OptGen, OptGrid, OptLine, OptLoad,
                                       solve_dispatch)
from app.resilience.engine import LoadView, can_transition, plan_island_loads, plan_recovery_step
from app.simulation import signals

T0 = datetime(2026, 6, 1, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- island planning
def loads():
    return [LoadView("hospital", "CRITICAL", 40), LoadView("homes", "NORMAL", 50),
            LoadView("hvac", "FLEXIBLE", 20, flexible=True, max_curtail_kw=10, minimum_service_fraction=0.5),
            LoadView("pool", "SHEDDABLE", 15), LoadView("office", "HIGH", 30)]


def test_island_sheds_lowest_priority_first_and_protects_critical():
    plan = plan_island_loads(loads(), supply_kw=100)  # demand 155 -> deficit 55
    assert plan.served_kw["hospital"] == 40
    assert plan.served_kw["pool"] == 0                 # shed first
    assert plan.served_kw["hvac"] == 0                 # curtailed (DR) to min service, then shed
    assert plan.served_kw["homes"] == 30 and plan.served_kw["office"] == 30
    assert plan.critical_served
    assert abs(sum(plan.served_kw.values()) - 100) < 1e-6
    acts = {}
    for d in plan.decisions:
        acts.setdefault(d.asset_id, []).append(d.action)
    assert acts["hvac"] == ["CURTAIL", "SHED"]          # DR attempted before shedding
    assert acts["pool"] == ["SHED"] and acts["hospital"] == ["KEEP"]


def test_island_critical_flagged_when_supply_below_critical():
    plan = plan_island_loads(loads(), supply_kw=30)
    assert not plan.critical_served and plan.served_kw["hospital"] == 30
    assert plan.served_kw["office"] == 0 and plan.served_kw["homes"] == 0


def test_recovery_restores_in_priority_order_with_ramp_limit():
    ls = [LoadView("hospital", "CRITICAL", 40, currently_shed_kw=0), LoadView("office", "HIGH", 30, currently_shed_kw=30),
          LoadView("homes", "NORMAL", 50, currently_shed_kw=50), LoadView("pool", "SHEDDABLE", 15, currently_shed_kw=15)]
    step = plan_recovery_step(ls, supply_kw=1000, ramp_fraction=0.34)  # ramp budget = 0.34 * 135 = 45.9
    assert [d.asset_id for d in step] == ["office", "homes"]
    assert abs(sum(d.quantity_kw for d in step) - 45.9) < 1e-6
    step_limited = plan_recovery_step(ls, supply_kw=50, ramp_fraction=1.0)  # serving 40 now -> 10 kW headroom
    assert abs(sum(d.quantity_kw for d in step_limited) - 10) < 1e-6 and step_limited[0].asset_id == "office"


def test_mode_transitions():
    assert can_transition("GRID_CONNECTED", "ISLANDED")
    assert not can_transition("GRID_CONNECTED", "RECOVERY")
    assert can_transition("ISLANDED", "RECOVERY") and can_transition("RECOVERY", "GRID_CONNECTED")
    assert not can_transition("ISLANDED", "GRID_CONNECTED")  # must pass through RECOVERY


# ---------------------------------------------------------------- optimisation
def problem(connected=True, weights=None, solar=(0, 0, 0, 0)):
    return DispatchProblem(
        nodes=["A", "B"], lines=[OptLine("ab", "A", "B", 100)], steps=4, dt_hours=1.0,
        generators=[OptGen("solar", "A", list(solar), 0.0, True), OptGen("diesel", "A", [30.0] * 4, 0.30, False)],
        batteries=[OptBattery("bat", "B", 100, 60, 10, 95, 20, 20, 0.9, 0.02, reserve_kwh=40)],
        loads=[OptLoad("hospital", "B", [20.0] * 4, "CRITICAL"), OptLoad("pool", "B", [20.0] * 4, "SHEDDABLE")],
        grid=OptGrid("A", 50, 50, [0.20] * 4, [0.05] * 4, connected=connected),
        weights=weights or ObjectiveWeights.normal(), mode="GRID_CONNECTED" if connected else "ISLANDED")


def test_dispatch_grid_connected_serves_everything_and_uses_solar_first():
    r = solve_dispatch(problem(solar=(20, 20, 20, 20)))
    assert r.status == "Optimal"
    assert r.total_unserved() < 1e-6
    assert sum(r.generation_kwh["solar"]) > 70       # free renewable fully used
    assert sum(r.generation_kwh["diesel"]) < 1e-6    # dearer than the grid
    assert max(sum(x) for x in zip(*[r.line_flow_kw["ab"]])) <= 100 + 1e-6


def test_dispatch_island_never_imports_and_protects_critical_load():
    # supply: diesel 30 kW only; demand 40 kW -> sheddable pool must be cut, hospital fully served
    r = solve_dispatch(problem(connected=False, weights=ObjectiveWeights.island()))
    assert r.status == "Optimal"
    assert sum(r.import_kwh) < 1e-6 and sum(r.export_kwh) < 1e-6
    assert sum(r.unserved_kwh["hospital"]) < 1e-6
    assert sum(r.unserved_kwh["pool"]) > 0 or sum(r.battery_discharge_kwh["bat"]) > 0
    assert sum(r.battery_soc_kwh["bat"][-1:]) >= 40 - 1e-6 or sum(r.unserved_kwh["pool"]) > 0


def test_dispatch_respects_battery_soc_and_is_deterministic():
    a, b = solve_dispatch(problem(solar=(0, 50, 50, 0))), solve_dispatch(problem(solar=(0, 50, 50, 0)))
    assert abs(a.objective - b.objective) < 1e-9
    assert all(10 - 1e-6 <= s <= 95 + 1e-6 for s in a.battery_soc_kwh["bat"])


# ---------------------------------------------------------------- forecasting + signals
def test_forecast_points_have_bounds_confidence_horizon():
    noon = T0 + timedelta(hours=12)
    pts = fm.WeatherAdjustedSolarForecaster(7).forecast([], [noon, noon + timedelta(hours=6)], 60, T0, rated_kw=10)
    for p in pts:
        assert p.lower_bound <= p.predicted_value <= p.upper_bound <= 10.0 + 1e-9
        assert 0 < p.confidence <= 1 and p.model_name == "weather_adjusted_solar"
    assert pts[1].horizon_minutes > 0 and pts[0].predicted_value > 0
    night = fm.WeatherAdjustedSolarForecaster(7).forecast([], [T0 + timedelta(hours=1)], 60, T0, rated_kw=10)[0]
    assert night.predicted_value == 0.0


def test_signals_are_seeded_and_realistic():
    assert signals.smooth_noise(1, 1, T0) == signals.smooth_noise(1, 1, T0)
    assert signals.smooth_noise(1, 1, T0) != signals.smooth_noise(2, 1, T0)
    assert signals.solar_capacity_factor(1, T0 + timedelta(hours=1)) == 0.0
    assert signals.solar_capacity_factor(1, T0 + timedelta(hours=12), cloud_cover=0.0) > 0.95
    assert signals.solar_capacity_factor(1, T0 + timedelta(hours=12), cloud_cover=0.9) < 0.35
    h = lambda hr: signals.household_demand_multiplier(1, T0 + timedelta(hours=hr))  # noqa: E731
    assert h(19.5) > h(3) and h(7.5) > h(3)
    wk = datetime(2026, 6, 3, tzinfo=timezone.utc)  # Wednesday
    assert signals.commercial_demand_multiplier(1, wk + timedelta(hours=13)) > 2 * signals.commercial_demand_multiplier(1, wk + timedelta(hours=2))
    assert signals.wind_capacity_factor(2) == 0 and signals.wind_capacity_factor(13) == 1


def test_regression_demand_forecast_learns_daily_shape():
    hist = [fm.Observation(T0 + timedelta(hours=i), 5 + 3 * signals.household_demand_multiplier(3, T0 + timedelta(hours=i)))
            for i in range(24 * 7)]
    t = T0 + timedelta(days=7, hours=19)
    p = fm.RegressionDemandForecaster().forecast(hist, [t, t - timedelta(hours=16)], 60, T0 + timedelta(days=7))
    assert p[0].predicted_value > p[1].predicted_value
