"""Operational agent memory: rolling statistics and bounded episodic memory."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import utcnow
from app.models.ai import AgentMemoryModel


@dataclass
class DecisionEpisode:
    market_round_id: str
    timestamp: str
    scenario_or_event: str
    proposed_action: str
    executed_action: str
    outcome_summary: str
    net_reward: float
    learnings: str


@dataclass
class AgentMemory:
    agent_id: str
    microgrid_id: uuid.UUID
    max_episodes: int = 15
    max_history_window: int = 50

    total_decisions: int = 0
    accepted_orders: int = 0
    rejected_orders: int = 0
    guard_interventions: int = 0
    fallbacks_count: int = 0

    rolling_net_rewards: list[float] = field(default_factory=list)
    rolling_delivery_ratios: list[float] = field(default_factory=list)
    rolling_forecast_errors: list[float] = field(default_factory=list)

    episodes: list[dict[str, Any]] = field(default_factory=list)

    def record_decision(
        self,
        accepted: bool,
        guard_intervened: bool = False,
        fallback_used: bool = False,
    ) -> None:
        self.total_decisions += 1
        if accepted:
            self.accepted_orders += 1
        else:
            self.rejected_orders += 1
        if guard_intervened:
            self.guard_interventions += 1
        if fallback_used:
            self.fallbacks_count += 1

    def record_outcome(
        self,
        net_reward: float,
        delivery_ratio: float,
        forecast_error: float = 0.0,
        episode: DecisionEpisode | None = None,
    ) -> None:
        self.rolling_net_rewards.append(net_reward)
        if len(self.rolling_net_rewards) > self.max_history_window:
            self.rolling_net_rewards.pop(0)

        self.rolling_delivery_ratios.append(delivery_ratio)
        if len(self.rolling_delivery_ratios) > self.max_history_window:
            self.rolling_delivery_ratios.pop(0)

        self.rolling_forecast_errors.append(forecast_error)
        if len(self.rolling_forecast_errors) > self.max_history_window:
            self.rolling_forecast_errors.pop(0)

        if episode:
            self.episodes.append({
                "market_round_id": episode.market_round_id,
                "timestamp": episode.timestamp,
                "scenario_or_event": episode.scenario_or_event,
                "proposed_action": episode.proposed_action,
                "executed_action": episode.executed_action,
                "outcome_summary": episode.outcome_summary,
                "net_reward": episode.net_reward,
                "learnings": episode.learnings,
            })
            if len(self.episodes) > self.max_episodes:
                self.episodes.pop(0)

    @property
    def average_reward(self) -> float:
        return (sum(self.rolling_net_rewards) / len(self.rolling_net_rewards)) if self.rolling_net_rewards else 0.0

    @property
    def average_delivery_ratio(self) -> float:
        return (sum(self.rolling_delivery_ratios) / len(self.rolling_delivery_ratios)) if self.rolling_delivery_ratios else 1.0

    @property
    def average_forecast_error(self) -> float:
        return (sum(self.rolling_forecast_errors) / len(self.rolling_forecast_errors)) if self.rolling_forecast_errors else 0.0

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "total_decisions": self.total_decisions,
            "accepted_orders": self.accepted_orders,
            "rejected_orders": self.rejected_orders,
            "guard_interventions": self.guard_interventions,
            "fallbacks_count": self.fallbacks_count,
            "average_reward": round(self.average_reward, 4),
            "average_delivery_ratio": round(self.average_delivery_ratio, 4),
            "average_forecast_error": round(self.average_forecast_error, 4),
            "recent_rewards": [round(x, 4) for x in self.rolling_net_rewards[-5:]],
        }


async def load_agent_memory(
    session: AsyncSession,
    microgrid_id: uuid.UUID,
    agent_id: str,
) -> AgentMemory:
    """Loads or creates agent memory from persistence."""
    stmt = select(AgentMemoryModel).where(
        AgentMemoryModel.microgrid_id == microgrid_id,
        AgentMemoryModel.agent_id == agent_id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        mem = AgentMemory(agent_id=agent_id, microgrid_id=microgrid_id)
        return mem

    stats = row.summary_stats or {}
    mem = AgentMemory(
        agent_id=agent_id,
        microgrid_id=microgrid_id,
        total_decisions=stats.get("total_decisions", 0),
        accepted_orders=stats.get("accepted_orders", 0),
        rejected_orders=stats.get("rejected_orders", 0),
        guard_interventions=stats.get("guard_interventions", 0),
        fallbacks_count=stats.get("fallbacks_count", 0),
        rolling_net_rewards=stats.get("rolling_net_rewards", []),
        rolling_delivery_ratios=stats.get("rolling_delivery_ratios", []),
        rolling_forecast_errors=stats.get("rolling_forecast_errors", []),
        episodes=row.episodes or [],
    )
    return mem


async def save_agent_memory(
    session: AsyncSession,
    memory: AgentMemory,
) -> None:
    """Persists updated agent memory to database."""
    stmt = select(AgentMemoryModel).where(
        AgentMemoryModel.microgrid_id == memory.microgrid_id,
        AgentMemoryModel.agent_id == memory.agent_id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    stats = {
        **memory.to_summary_dict(),
        "rolling_net_rewards": memory.rolling_net_rewards,
        "rolling_delivery_ratios": memory.rolling_delivery_ratios,
        "rolling_forecast_errors": memory.rolling_forecast_errors,
    }
    if row is None:
        row = AgentMemoryModel(
            microgrid_id=memory.microgrid_id,
            agent_id=memory.agent_id,
            summary_stats=stats,
            episodes=memory.episodes,
            updated_at=utcnow(),
        )
        session.add(row)
    else:
        row.summary_stats = stats
        row.episodes = memory.episodes
        row.updated_at = utcnow()
    await session.flush()
