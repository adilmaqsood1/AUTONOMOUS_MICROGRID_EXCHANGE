"""AI Agents, Autonomous Market Rounds, and Experiment REST API Router."""
from __future__ import annotations

import uuid
from typing import Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.ai.evaluation import evaluate_decisions_and_outcomes
from app.agents.ai.experiments import ExperimentRunner
from app.agents.ai.memory import load_agent_memory
from app.agents.ai.provider import get_ai_provider
from app.agents.coordinator import AgentCoordinator
from app.api.deps import Principal, SessionDep, current_principal, operator, reader
from app.core.config import get_settings
from app.core.enums import StrategyMode
from app.core.timeutil import utcnow
from app.models.ai import (
    AIAgentConfiguration,
    AgentDecision,
    AgentExperiment,
    AgentMarketRound,
    AgentOutcome,
)
from app.models.core import Participant

router = APIRouter(prefix="/agents", tags=["AI Agents & Autonomous Market"])


class RunMarketRoundRequest(BaseModel):
    microgrid_id: uuid.UUID
    mode: Optional[str] = "AI_ASSISTED"


class RunExperimentRequest(BaseModel):
    microgrid_id: uuid.UUID
    name: str = "ai_vs_deterministic"
    scenario: str = "BASELINE"
    agent_mode: str = "AI_ASSISTED"
    duration_steps: int = 2
    seed: int = 42


@router.get("", dependencies=[Depends(reader)])
async def list_agents(
    microgrid_id: Optional[uuid.UUID] = None,
    session: AsyncSession = SessionDep,
) -> List[dict[str, Any]]:
    """Lists registered AI agent configurations."""
    stmt = select(AIAgentConfiguration)
    if microgrid_id:
        stmt = stmt.where(AIAgentConfiguration.microgrid_id == microgrid_id)
    configs = list((await session.execute(stmt)).scalars())
    return [
        {
            "id": str(c.id),
            "agent_id": c.agent_id,
            "agent_type": c.agent_type,
            "strategy_mode": c.strategy_mode,
            "prompt_version": c.prompt_version,
            "model_name": c.model_name,
            "enabled": c.enabled,
        }
        for c in configs
    ]


@router.get("/ai/status", dependencies=[Depends(reader)])
async def ai_provider_status() -> dict[str, Any]:
    """Returns AI provider health and circuit breaker metrics without exposing credentials."""
    s = get_settings()
    provider = get_ai_provider()
    health = provider.get_health()
    return {
        "configured": True,
        "provider": s.ai_provider,
        "model": s.ai_model,
        "base_url": s.ai_base_url if s.ai_provider != "mock" else "local_hermetic_mock",
        **health,
    }


@router.get("/leaderboard", dependencies=[Depends(reader)])
async def get_agent_leaderboard(
    microgrid_id: Optional[uuid.UUID] = None,
    session: AsyncSession = SessionDep,
) -> List[dict[str, Any]]:
    """Calculates agent performance leaderboard ranked by net reward and reliability."""
    stmt = select(Participant).where(Participant.active.is_(True))
    if microgrid_id:
        stmt = stmt.where(Participant.microgrid_id == microgrid_id)
    participants = list((await session.execute(stmt)).scalars())

    board = []
    for p in participants:
        agent_id = f"{p.type.lower()[:4]}-{str(p.id)[:8]}"
        out_stmt = select(AgentOutcome).where(AgentOutcome.agent_id == agent_id)
        outcomes = list((await session.execute(out_stmt)).scalars())

        tot_reward = sum(o.net_reward for o in outcomes)
        tot_counterfactual = sum(o.counterfactual_reward or o.net_reward for o in outcomes)
        value_add = tot_reward - tot_counterfactual

        board.append({
            "participant_id": str(p.id),
            "name": p.name,
            "agent_id": agent_id,
            "type": p.type,
            "reliability_score": round(p.reliability_score * 100, 1),
            "total_net_reward": round(tot_reward, 2),
            "ai_value_add": round(value_add, 2),
            "deliveries_success": p.deliveries_success,
            "deliveries_failed": p.deliveries_failed,
        })
    board.sort(key=lambda x: x["total_net_reward"], reverse=True)
    return board


@router.get("/market-rounds", dependencies=[Depends(reader)])
async def list_market_rounds(
    microgrid_id: Optional[uuid.UUID] = None,
    limit: int = Query(50, le=200),
    session: AsyncSession = SessionDep,
) -> List[dict[str, Any]]:
    """Lists recent market rounds."""
    stmt = select(AgentMarketRound).order_by(desc(AgentMarketRound.created_at)).limit(limit)
    if microgrid_id:
        stmt = stmt.where(AgentMarketRound.microgrid_id == microgrid_id)
    rounds = list((await session.execute(stmt)).scalars())
    return [
        {
            "id": str(r.id),
            "round_id": r.round_id,
            "microgrid_id": str(r.microgrid_id),
            "snapshot_timestamp": r.snapshot_timestamp.isoformat(),
            "status": r.status,
            "summary": r.summary,
            "created_at": r.created_at.isoformat(),
        }
        for r in rounds
    ]


@router.get("/market-rounds/{round_id}", dependencies=[Depends(reader)])
async def get_market_round_detail(
    round_id: str,
    session: AsyncSession = SessionDep,
) -> dict[str, Any]:
    """Retrieves full decisions, commitments, and settlement outcomes for a round."""
    stmt = select(AgentMarketRound).where(AgentMarketRound.round_id == round_id)
    mr = (await session.execute(stmt)).scalar_one_or_none()
    if not mr:
        raise HTTPException(status_code=404, detail="Market round not found")

    dec_stmt = select(AgentDecision).where(AgentDecision.market_round_id == round_id)
    decisions = list((await session.execute(dec_stmt)).scalars())

    out_stmt = select(AgentOutcome).where(AgentOutcome.market_round_id == round_id)
    outcomes = list((await session.execute(out_stmt)).scalars())

    return {
        "round_id": mr.round_id,
        "snapshot_timestamp": mr.snapshot_timestamp.isoformat(),
        "status": mr.status,
        "summary": mr.summary,
        "decisions": [
            {
                "agent_id": d.agent_id,
                "agent_type": d.agent_type,
                "action": d.final_action,
                "quantity": d.final_quantity,
                "price": d.final_price,
                "confidence": d.confidence,
                "reasoning_summary": d.reasoning_summary,
                "guard_status": d.guard_status,
                "fallback_used": d.fallback_used,
            }
            for d in decisions
        ],
        "outcomes": [
            {
                "agent_id": o.agent_id,
                "net_reward": round(o.net_reward, 4),
                "energy_revenue": round(o.energy_revenue, 4),
                "energy_cost": round(o.energy_cost, 4),
                "penalties": round(o.shortfall_penalty, 4),
                "delivery_ratio": round(o.delivery_ratio, 4),
            }
            for o in outcomes
        ],
    }


@router.post("/market-rounds/run", dependencies=[Depends(operator)])
async def run_market_round(
    req: RunMarketRoundRequest,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = SessionDep,
) -> dict[str, Any]:
    """Executes a full 30-step autonomous market round."""
    coord = AgentCoordinator()
    mode = StrategyMode(req.mode.upper()) if req.mode else StrategyMode.AI_ASSISTED
    res = await coord.run_market_round(
        session, req.microgrid_id, default_mode=mode, actor=principal.actor
    )
    await session.commit()
    return {
        "round_id": res["round_id"],
        "window": res["window"],
        "orders_count": len(res["orders"]),
        "matched_kwh": float(res["clearing"].matched_kwh),
        "clearing_price": float(res["clearing"].clearing_price) if res["clearing"].clearing_price else None,
        "grid_advisor": res["grid_advisor"],
        "market_analyst": res["market_analyst"],
    }


@router.get("/{agent_id}/decisions", dependencies=[Depends(reader)])
async def get_agent_decisions(
    agent_id: str,
    limit: int = Query(50, le=200),
    session: AsyncSession = SessionDep,
) -> List[dict[str, Any]]:
    """Returns recent decisions made by this agent."""
    stmt = select(AgentDecision).where(AgentDecision.agent_id == agent_id).order_by(
        desc(AgentDecision.created_at)
    ).limit(limit)
    decisions = list((await session.execute(stmt)).scalars())
    return [
        {
            "id": str(d.id),
            "market_round_id": d.market_round_id,
            "mode": d.mode,
            "final_action": d.final_action,
            "final_quantity": d.final_quantity,
            "final_price": d.final_price,
            "confidence": d.confidence,
            "reasoning_summary": d.reasoning_summary,
            "guard_status": d.guard_status,
            "fallback_used": d.fallback_used,
            "latency_ms": d.latency_ms,
            "created_at": d.created_at.isoformat(),
        }
        for d in decisions
    ]


@router.get("/{agent_id}/outcomes", dependencies=[Depends(reader)])
async def get_agent_outcomes(
    agent_id: str,
    limit: int = Query(50, le=200),
    session: AsyncSession = SessionDep,
) -> List[dict[str, Any]]:
    """Returns recent realized outcomes and P&L for this agent."""
    stmt = select(AgentOutcome).where(AgentOutcome.agent_id == agent_id).order_by(
        desc(AgentOutcome.created_at)
    ).limit(limit)
    outcomes = list((await session.execute(stmt)).scalars())
    return [
        {
            "id": str(o.id),
            "market_round_id": o.market_round_id,
            "executed_action": o.executed_action,
            "executed_quantity": o.executed_quantity,
            "market_price": o.market_price,
            "net_reward": round(o.net_reward, 4),
            "delivery_ratio": round(o.delivery_ratio, 4),
            "shortfall_penalty": round(o.shortfall_penalty, 4),
            "created_at": o.created_at.isoformat(),
        }
        for o in outcomes
    ]


@router.get("/{agent_id}/memory", dependencies=[Depends(reader)])
async def get_agent_memory(
    agent_id: str,
    microgrid_id: uuid.UUID = Query(...),
    session: AsyncSession = SessionDep,
) -> dict[str, Any]:
    """Returns rolling statistics and episodic memory for this agent."""
    mem = await load_agent_memory(session, microgrid_id, agent_id)
    return {
        "agent_id": agent_id,
        "summary": mem.to_summary_dict(),
        "episodes": mem.episodes,
    }


@router.post("/experiments", dependencies=[Depends(operator)])
async def create_experiment(
    req: RunExperimentRequest,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = SessionDep,
) -> dict[str, Any]:
    """Launches a multi-step simulation experiment."""
    runner = ExperimentRunner()
    exp = await runner.run_experiment(
        session, req.microgrid_id, name=req.name, scenario=req.scenario,
        agent_mode=req.agent_mode, duration_steps=req.duration_steps, seed=req.seed,
    )
    await session.commit()
    return {
        "id": str(exp.id),
        "name": exp.name,
        "scenario": exp.scenario,
        "agent_mode": exp.agent_mode,
        "results": exp.results,
    }


@router.get("/experiments/{experiment_id}", dependencies=[Depends(reader)])
async def get_experiment(
    experiment_id: uuid.UUID,
    session: AsyncSession = SessionDep,
) -> dict[str, Any]:
    """Returns experiment summary."""
    exp = await session.get(AgentExperiment, experiment_id)
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return {
        "id": str(exp.id),
        "name": exp.name,
        "scenario": exp.scenario,
        "agent_mode": exp.agent_mode,
        "results": exp.results,
        "created_at": exp.created_at.isoformat(),
    }


@router.get("/experiments/{experiment_id}/comparison", dependencies=[Depends(reader)])
async def get_experiment_comparison(
    experiment_id: uuid.UUID,
    session: AsyncSession = SessionDep,
) -> dict[str, Any]:
    """Evaluates agent performance in experiment against baseline metrics."""
    exp = await session.get(AgentExperiment, experiment_id)
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")

    round_ids = exp.results.get("round_ids", [])
    dec_stmt = select(AgentDecision).where(AgentDecision.market_round_id.in_(round_ids))
    decisions = list((await session.execute(dec_stmt)).scalars())

    out_stmt = select(AgentOutcome).where(AgentOutcome.market_round_id.in_(round_ids))
    outcomes = list((await session.execute(out_stmt)).scalars())

    comparison = evaluate_decisions_and_outcomes(decisions, outcomes, mode=exp.agent_mode)
    return comparison.to_dict()
