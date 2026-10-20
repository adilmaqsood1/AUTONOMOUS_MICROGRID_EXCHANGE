"""Optimization runs and Risk assessments routers."""
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, operator, reader
from app.models.ops import OptimizationRun, RiskDecision
from app.optimization import service as opt_service
from app.schemas.schemas import (
    OptimizationRunResponse, OptimizationTrigger, RiskDecisionResponse
)

opt_router = APIRouter(prefix="/optimization", tags=["Optimization"])
risk_router = APIRouter(prefix="/risk", tags=["Risk Engine"])


# --- Optimization
@opt_router.post("/run", response_model=OptimizationRunResponse, dependencies=[Depends(operator)])
async def trigger_optimization(req: OptimizationTrigger, session: AsyncSession = SessionDep):
    run, _ = await opt_service.run_optimization(
        session, req.microgrid_id, steps=req.steps, step_minutes=req.step_minutes,
        start=req.start, weights=req.weights
    )
    await session.commit()
    await session.refresh(run)
    return run


@opt_router.post("/stochastic-run", dependencies=[Depends(operator)])
async def trigger_stochastic_optimization(req: dict, session: AsyncSession = SessionDep):
    mg_id = uuid.UUID(req["microgrid_id"])
    steps = req.get("steps", 6)
    step_minutes = req.get("step_minutes", 60)
    risk_aversion = float(req.get("risk_aversion", 0.2))
    scenarios = req.get("scenarios")

    run, hedged_res, sc_dict = await opt_service.run_stochastic_optimization(
        session, mg_id, steps=steps, step_minutes=step_minutes,
        risk_aversion=risk_aversion, custom_scenarios=scenarios
    )
    await session.commit()
    await session.refresh(run)
    return {
        "microgrid_id": mg_id,
        "status": hedged_res.status,
        "base_objective": run.result.get("objective_value", 0.0),
        "hedged_objective": hedged_res.objective,
        "execution_time_ms": hedged_res.execution_time_ms,
        "scenarios_evaluated": list(sc_dict.keys()),
        "scenario_shortfalls_kwh": {k: round(v.total_unserved(), 4) for k, v in sc_dict.items()},
        "carbon_emissions_kg": hedged_res.carbon_emissions_kg,
        "carbon_cost": hedged_res.carbon_cost,
        "battery_dod_percent": hedged_res.battery_dod_percent,
        "battery_dod_degradation_cost": hedged_res.battery_dod_degradation_cost,
    }



@opt_router.get("/runs", response_model=List[OptimizationRunResponse], dependencies=[Depends(reader)])
async def list_optimization_runs(microgrid_id: Optional[uuid.UUID] = None,
                                 limit: int = 50,
                                 session: AsyncSession = SessionDep):
    q = select(OptimizationRun)
    if microgrid_id:
        q = q.where(OptimizationRun.microgrid_id == microgrid_id)
    return (await session.execute(q.order_by(OptimizationRun.timestamp.desc()).limit(min(limit, 200)))).scalars().all()


@opt_router.get("/runs/{run_id}", response_model=OptimizationRunResponse, dependencies=[Depends(reader)])
async def get_optimization_run(run_id: uuid.UUID, session: AsyncSession = SessionDep):
    run = await session.get(OptimizationRun, run_id)
    if not run:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "optimization run not found")
    return run


# --- Risk Decisions
@risk_router.get("/decisions", response_model=List[RiskDecisionResponse], dependencies=[Depends(reader)])
async def list_risk_decisions(microgrid_id: Optional[uuid.UUID] = None,
                              limit: int = 100,
                              session: AsyncSession = SessionDep):
    q = select(RiskDecision)
    if microgrid_id:
        q = q.where(RiskDecision.microgrid_id == microgrid_id)
    return (await session.execute(q.order_by(RiskDecision.created_at.desc()).limit(min(limit, 500)))).scalars().all()
