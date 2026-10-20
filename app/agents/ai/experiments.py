"""AgentExperiment runner: executes multi-interval experiments comparing agent strategies."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.coordinator import AgentCoordinator
from app.core.enums import StrategyMode
from app.core.timeutil import utcnow
from app.models.ai import AgentDecision, AgentExperiment, AgentOutcome


class ExperimentRunner:
    """Executes multi-interval controlled simulations under specified strategy configurations."""

    def __init__(self):
        self.coordinator = AgentCoordinator()

    async def run_experiment(
        self,
        session: AsyncSession,
        microgrid_id: uuid.UUID,
        name: str,
        scenario: str = "BASELINE",
        agent_mode: str = "AI_ASSISTED",
        duration_steps: int = 2,
        seed: int = 42,
    ) -> AgentExperiment:
        mode = StrategyMode(agent_mode.upper())
        round_ids: list[str] = []

        for step_idx in range(duration_steps):
            rid = f"exp-{name[:8]}-s{step_idx}-{uuid.uuid4().hex[:4]}"
            res = await self.coordinator.run_market_round(
                session, microgrid_id, round_id=rid, default_mode=mode, actor=f"experiment:{name}"
            )
            round_ids.append(rid)

        # Gather aggregate decisions and outcomes across the experiment
        dec_stmt = select(AgentDecision).where(AgentDecision.market_round_id.in_(round_ids))
        decisions = list((await session.execute(dec_stmt)).scalars())

        out_stmt = select(AgentOutcome).where(AgentOutcome.market_round_id.in_(round_ids))
        outcomes = list((await session.execute(out_stmt)).scalars())

        total_net_reward = sum(o.net_reward for o in outcomes)
        total_energy_traded = sum(o.executed_quantity for o in outcomes)
        total_penalties = sum(o.shortfall_penalty for o in outcomes)
        rejections = sum(1 for d in decisions if d.guard_status == "REJECTED")
        fallbacks = sum(1 for d in decisions if d.fallback_used)

        results_summary = {
            "duration_steps": duration_steps,
            "total_decisions": len(decisions),
            "total_energy_traded_kwh": round(total_energy_traded, 2),
            "total_net_reward": round(total_net_reward, 2),
            "total_penalties": round(total_penalties, 2),
            "guard_rejections": rejections,
            "fallbacks_count": fallbacks,
            "round_ids": round_ids,
        }

        experiment = AgentExperiment(
            microgrid_id=microgrid_id,
            name=name,
            scenario=scenario,
            seed=seed,
            agent_mode=agent_mode.upper(),
            model="gpt-4o-mini",
            prompt_version="v1",
            duration_steps=duration_steps,
            results=results_summary,
            created_at=utcnow(),
        )
        session.add(experiment)
        await session.flush()
        return experiment
