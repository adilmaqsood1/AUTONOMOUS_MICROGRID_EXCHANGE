"""Simulation and scenario control routers."""
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, SessionDep, current_principal, operator, reader
from app.core.enums import ScenarioType
from app.models.core import Microgrid
from app.models.ops import ScenarioEvent, SimulationState
from app.schemas.schemas import (
    ScenarioEventResponse, ScenarioInjectRequest, ScenarioPresetRequest,
    SimulationResetRequest, SimulationSpeedRequest, SimulationStatusResponse,
    SimulationStepRequest
)
from app.simulation import engine, scenarios
from app.simulation.scenarios import PRESETS
from app.simulation.state import DEFAULT_START, get_config, get_state
from app.simulation.signals import base_temperature, base_wind_speed

router = APIRouter(prefix="/simulation", tags=["Simulation & Scenarios"])


@router.get("/status", response_model=SimulationStatusResponse, dependencies=[Depends(reader)])
async def get_simulation_status(microgrid_id: Optional[uuid.UUID] = None, session: AsyncSession = SessionDep):
    if not microgrid_id:
        mg = (await session.execute(select(Microgrid))).scalars().first()
        if not mg:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no microgrid configured")
        microgrid_id = mg.id
    else:
        mg = await session.get(Microgrid, microgrid_id)
        if not mg:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "microgrid not found")

    st = await get_state(session, microgrid_id)
    cfg = await get_config(session, microgrid_id)
    mods = await scenarios.active_modifiers(session, microgrid_id, cfg.sim_time, cfg.sim_time)
    temp = base_temperature(cfg.seed, cfg.sim_time, cfg.tz_offset_hours)
    wind = base_wind_speed(cfg.seed, cfg.sim_time) * (1.0 - mods.wind_drop)

    return SimulationStatusResponse(
        running=st.running,
        speed_multiplier=st.speed_multiplier,
        sim_time=st.sim_time,
        seed=st.seed,
        operating_mode=mg.operating_mode,
        cloud_cover=min(1.0, cfg.cloud_cover if hasattr(cfg, "cloud_cover") else 0.0 + mods.cloud),
        wind_speed_m_s=round(wind, 2),
        temperature_c=round(temp, 2),
        external_grid_connected=mg.external_grid_connected
    )


@router.post("/start", response_model=Dict[str, Any], dependencies=[Depends(operator)])
async def start_simulation(microgrid_id: Optional[uuid.UUID] = None, session: AsyncSession = SessionDep):
    if not microgrid_id:
        mg = (await session.execute(select(Microgrid))).scalars().first()
        microgrid_id = mg.id
    st = await get_state(session, microgrid_id)
    st.running = True
    await session.commit()
    return {"status": "started", "running": True, "sim_time": st.sim_time}


@router.post("/stop", response_model=Dict[str, Any], dependencies=[Depends(operator)])
async def stop_simulation(microgrid_id: Optional[uuid.UUID] = None, session: AsyncSession = SessionDep):
    if not microgrid_id:
        mg = (await session.execute(select(Microgrid))).scalars().first()
        microgrid_id = mg.id
    st = await get_state(session, microgrid_id)
    st.running = False
    await session.commit()
    return {"status": "stopped", "running": False, "sim_time": st.sim_time}


@router.post("/step", response_model=Dict[str, Any], dependencies=[Depends(operator)])
async def step_simulation(req: SimulationStepRequest,
                          microgrid_id: Optional[uuid.UUID] = None,
                          principal: Principal = Depends(current_principal),
                          session: AsyncSession = SessionDep):
    if not microgrid_id:
        mg = (await session.execute(select(Microgrid))).scalars().first()
        microgrid_id = mg.id
    st = await get_state(session, microgrid_id)
    steps = max(1, req.minutes // st.step_minutes)
    results = []
    for _ in range(steps):
        res = await engine.step(session, microgrid_id, actor=principal.actor)
        results.append(res)
    await session.commit()
    await session.refresh(st)
    return {"steps_executed": len(results), "new_sim_time": st.sim_time}


@router.post("/speed", response_model=Dict[str, Any], dependencies=[Depends(operator)])
async def set_simulation_speed(req: SimulationSpeedRequest,
                               microgrid_id: Optional[uuid.UUID] = None,
                               session: AsyncSession = SessionDep):
    if not microgrid_id:
        mg = (await session.execute(select(Microgrid))).scalars().first()
        microgrid_id = mg.id
    st = await get_state(session, microgrid_id)
    st.speed_multiplier = req.speed
    await session.commit()
    return {"speed_multiplier": st.speed_multiplier}


@router.post("/reset", response_model=Dict[str, Any], dependencies=[Depends(operator)])
async def reset_simulation(req: SimulationResetRequest,
                           microgrid_id: Optional[uuid.UUID] = None,
                           session: AsyncSession = SessionDep):
    if not microgrid_id:
        mg = (await session.execute(select(Microgrid))).scalars().first()
        microgrid_id = mg.id
    st = await get_state(session, microgrid_id)
    st.running = False
    st.sim_time = req.start_time or DEFAULT_START
    if req.seed is not None:
        st.seed = req.seed
    st.steps_executed = 0
    await session.commit()
    return {"status": "reset", "sim_time": st.sim_time, "seed": st.seed}


# --- Scenarios
@router.get("/scenarios", response_model=List[ScenarioEventResponse], dependencies=[Depends(reader)])
async def list_active_scenarios(microgrid_id: Optional[uuid.UUID] = None, session: AsyncSession = SessionDep):
    q = select(ScenarioEvent).where(ScenarioEvent.active.is_(True))
    if microgrid_id:
        q = q.where(ScenarioEvent.microgrid_id == microgrid_id)
    return (await session.execute(q.order_by(ScenarioEvent.start_time.desc()))).scalars().all()


@router.post("/scenarios", response_model=ScenarioEventResponse, dependencies=[Depends(operator)])
async def inject_scenario_endpoint(req: ScenarioInjectRequest,
                                    microgrid_id: Optional[uuid.UUID] = None,
                                    principal: Principal = Depends(current_principal),
                                    session: AsyncSession = SessionDep):
    if not microgrid_id:
        mg = (await session.execute(select(Microgrid))).scalars().first()
        microgrid_id = mg.id
    cfg = await get_config(session, microgrid_id)
    ev = await scenarios.inject(
        session, microgrid_id, req.scenario_type, req.severity, req.duration_minutes,
        cfg.sim_time, params=req.params, actor=principal.actor
    )
    await session.commit()
    await session.refresh(ev)
    return ev


@router.get("/scenarios/presets", response_model=Dict[str, Any], dependencies=[Depends(reader)])
async def list_scenario_presets():
    return {k: {"key": v["key"], "name": v["name"], "description": v["description"], "type": str(v["type"])}
            for k, v in PRESETS.items()}


@router.post("/scenarios/presets/{preset_key}", dependencies=[Depends(operator)])
async def inject_preset_endpoint(preset_key: str,
                                 microgrid_id: Optional[uuid.UUID] = None,
                                 principal: Principal = Depends(current_principal),
                                 session: AsyncSession = SessionDep):
    if not microgrid_id:
        mg = (await session.execute(select(Microgrid))).scalars().first()
        microgrid_id = mg.id
    cfg = await get_config(session, microgrid_id)
    ev = await scenarios.inject_preset(session, microgrid_id, preset_key, cfg.sim_time, actor=principal.actor)
    await session.commit()
    if ev:
        await session.refresh(ev)
        return ScenarioEventResponse.model_validate(ev)
    return {"message": f"Preset {preset_key} executed (Normal nominal market baseline)"}


@router.delete("/scenarios/{scenario_id}", response_model=ScenarioEventResponse, dependencies=[Depends(operator)])
async def cancel_scenario_endpoint(scenario_id: uuid.UUID,
                                   principal: Principal = Depends(current_principal),
                                   session: AsyncSession = SessionDep):
    ev = await session.get(ScenarioEvent, scenario_id)
    if not ev:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scenario not found")
    cfg = await get_config(session, ev.microgrid_id)
    ev = await scenarios.cancel(session, scenario_id, cfg.sim_time, actor=principal.actor)
    await session.commit()
    await session.refresh(ev)
    return ev


@router.post("/demo/blockchain-trade", dependencies=[Depends(operator)])
async def demo_blockchain_trade(session: AsyncSession = SessionDep):
    """Execute an end-to-end transactive trade demonstrating on-chain commitment,

    escrow, delivery attestation, settlement, and REC issuance.
    """
    from decimal import Decimal
    from app.blockchain.gateway import get_gateway

    gw = get_gateway()
    cid = f"DEMO_TRADE_{uuid.uuid4().hex[:8]}"
    cert_id = f"DEMO_REC_{uuid.uuid4().hex[:8]}"

    # Step 1: Create commitment
    rc_commit = await gw.create_energy_commitment(
        commitment_id=cid,
        buyer="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        seller="0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        quantity_kwh=Decimal("25.0"),
        price=Decimal("0.18"),
    )

    # Step 2: Fund escrow
    rc_escrow = await gw.lock_escrow(
        commitment_id=cid,
        account="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        amount=Decimal("4.50"),
    )

    # Step 3: Record meter proof
    rc_proof = await gw.record_meter_proof(
        commitment_id=cid,
        meter_id="METER_SOLAR_COMMUNITY_01",
        delivered_kwh=Decimal("25.0"),
        reading_hash=f"0x{uuid.uuid4().hex}",
    )

    # Step 4: Settle trade
    rc_settle = await gw.settle_trade(
        commitment_id=cid,
        seller_payment=Decimal("4.50"),
        refund=Decimal("0.00"),
        penalty=Decimal("0.00"),
    )

    # Step 5: Issue renewable certificate
    rc_rec = await gw.issue_renewable_certificate(
        certificate_id=cert_id,
        owner="0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        quantity_kwh=Decimal("25.0"),
        source="SOLAR_PV",
        meter_reading_id=f"READING_{uuid.uuid4().hex[:8]}",
    )

    return {
        "status": "completed",
        "commitment_id": cid,
        "certificate_id": cert_id,
        "steps": {
            "commitment": rc_commit,
            "escrow": rc_escrow,
            "delivery_proof": rc_proof,
            "settlement": rc_settle,
            "rec_issuance": rc_rec,
        },
        "verified": await gw.verify_transaction(rc_settle.reference),
    }
