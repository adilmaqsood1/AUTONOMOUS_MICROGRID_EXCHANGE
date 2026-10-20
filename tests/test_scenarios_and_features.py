"""Integration tests for scenarios, provenance, optimization, agents, and resilience."""
from datetime import timedelta
from decimal import Decimal as D
import pytest
from sqlalchemy import select

from app.core.enums import OperatingMode
from app.core.errors import Conflict, Forbidden
from app.market.cycle import advance_to
from app.models import (
    DecisionRecord, MeterReading, SmartMeter
)
from app.models.meters import ImmutableRecordError
from app.provenance.service import (
    issue_certificate, transfer_certificate, retire_certificate
)
from app.resilience.service import declare_outage, begin_recovery, complete_recovery
from app.simulation.scenarios import inject_preset, cancel, PRESETS, active_modifiers
from app.simulation.state import DEFAULT_START, get_config
from app.optimization.service import run_optimization
from app.agents.service import run_agents
from app.analytics.service import compute as compute_analytics

NOON = DEFAULT_START + timedelta(hours=7)
WS, WE = NOON, NOON + timedelta(hours=1)


async def test_provenance_certificate_lifecycle(session, demo):
    mg_id = demo["microgrid_id"]
    solar_id = demo["assets"]["Solar-1"]
    meter = (await session.execute(select(SmartMeter).where(SmartMeter.asset_id == solar_id))).scalar_one()

    # Reading with generation
    reading = MeterReading(
        meter_id=meter.id, asset_id=solar_id, microgrid_id=mg_id,
        timestamp=WS, generation_kwh=5.0,
        interval_minutes=60, verified=True, received_at=WS
    )
    session.add(reading)
    await session.commit()

    # 1. Issue certificate
    cert = await issue_certificate(session, reading.id, actor="test")
    assert cert is not None
    assert cert.energy_quantity_kwh == D("5.000000")
    assert cert.status == "ISSUED"
    assert cert.blockchain_reference.startswith("0x")

    # Duplicate issuance for same reading must raise Conflict
    with pytest.raises(Conflict):
        await issue_certificate(session, reading.id, actor="test")

    # 2. Transfer certificate to consumer
    owner_p = demo["participants"]["Prosumer-1"]
    consumer_p = demo["participants"]["Consumer-9"]
    tx_cert = await transfer_certificate(session, cert.id, from_owner=owner_p, to_owner=consumer_p, actor="test")
    assert tx_cert.owner_id == consumer_p
    assert tx_cert.status == "TRANSFERRED"

    # 3. Retire certificate
    retired = await retire_certificate(session, cert.id, owner_id=consumer_p, actor="test")
    assert retired.status == "RETIRED"

    # Cannot transfer retired certificate
    with pytest.raises(Conflict):
        await transfer_certificate(session, cert.id, from_owner=consumer_p, to_owner=owner_p, actor="test")


async def test_immutable_meter_reading_protection(session, demo):
    mg_id = demo["microgrid_id"]
    solar_id = demo["assets"]["Solar-1"]
    meter = (await session.execute(select(SmartMeter).where(SmartMeter.asset_id == solar_id))).scalar_one()
    t = WS + timedelta(hours=2)
    reading = MeterReading(
        meter_id=meter.id, asset_id=solar_id, microgrid_id=mg_id,
        timestamp=t, generation_kwh=4.0, interval_minutes=60, verified=True, received_at=t
    )
    session.add(reading)
    await session.commit()

    # Any direct update to MeterReading must raise ImmutableRecordError
    reading.generation_kwh = 10.0
    with pytest.raises(ImmutableRecordError):
        await session.commit()
    await session.rollback()


async def test_scenario_cloud_shock_impact(session, demo):
    mg_id = demo["microgrid_id"]
    await advance_to(session, mg_id, WS)

    # Inject preset B: Cloud Shock
    sc = await inject_preset(session, mg_id, "B", WS)
    assert sc is not None
    assert sc.active is True

    # Verify active modifiers reflects cloud cover shock
    mods = await active_modifiers(session, mg_id, WS, WS + timedelta(minutes=15))
    assert mods.cloud >= 0.85

    # Step simulation
    step_time = WS + timedelta(minutes=15)
    await advance_to(session, mg_id, step_time)

    # Cancel scenario restores
    await cancel(session, sc.id, step_time)
    mods2 = await active_modifiers(session, mg_id, step_time, step_time + timedelta(minutes=15))
    assert mods2.cloud == 0.0


async def test_scenario_grid_outage_and_island_recovery(session, demo):
    mg_id = demo["microgrid_id"]
    await advance_to(session, mg_id, WS)

    # Declare outage
    mg = await declare_outage(session, mg_id, WS, actor="operator")
    assert mg.operating_mode == OperatingMode.ISLANDED.value
    assert not mg.external_grid_connected

    # Step simulation under island mode (resilience plan executes)
    step_time = WS + timedelta(minutes=15)
    await advance_to(session, mg_id, step_time)

    # Begin recovery
    mg = await begin_recovery(session, mg_id, step_time, actor="operator")
    assert mg.operating_mode == OperatingMode.RECOVERY.value

    # Complete recovery
    await complete_recovery(session, mg, step_time + timedelta(minutes=15), actor="operator")
    assert mg.operating_mode == OperatingMode.GRID_CONNECTED.value
    assert mg.external_grid_connected


async def test_agent_autonomous_decision_cycle(session, demo):
    mg_id = demo["microgrid_id"]
    await advance_to(session, mg_id, WS)

    res = await run_agents(session, mg_id, WS, WE, actor="agent_system")
    assert len(res["orders"]) > 0 or len(res["decisions"]) > 0

    # DecisionRecords must be recorded for agent actions
    records = (await session.execute(select(DecisionRecord).where(DecisionRecord.microgrid_id == mg_id))).scalars().all()
    assert len(records) > 0


async def test_optimization_run_persisted(session, demo):
    mg_id = demo["microgrid_id"]
    await advance_to(session, mg_id, WS)

    opt_run, opt_res = await run_optimization(session, mg_id, steps=4, step_minutes=15, start=WS)
    assert opt_run.status.upper() in ("OPTIMAL", "FEASIBLE")
    assert opt_res is not None
    assert "objective_value" in opt_run.result


async def test_analytics_computation(session, demo):
    mg_id = demo["microgrid_id"]
    await advance_to(session, mg_id, WS + timedelta(hours=2))

    analytics = await compute_analytics(session, mg_id, WS, WS + timedelta(hours=2))
    assert "total_generation_kwh" in analytics
    assert "total_consumption_kwh" in analytics
    assert "comparison" in analytics
