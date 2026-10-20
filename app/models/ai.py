"""Phase 4 AI Agents & Autonomous Market persistence models."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import StrategyMode
from app.core.timeutil import utcnow
from app.db.base import Base, IdMixin


class AIAgentConfiguration(Base, IdMixin):
    __tablename__ = "ai_agent_configurations"
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    participant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("participants.id"), nullable=True, index=True)
    agent_id: Mapped[str] = mapped_column(String(80), index=True)
    agent_type: Mapped[str] = mapped_column(String(40))
    strategy_mode: Mapped[str] = mapped_column(String(20), default=StrategyMode.AI_ASSISTED.value)
    prompt_version: Mapped[str] = mapped_column(String(40), default="v1")
    model_name: Mapped[str] = mapped_column(String(60), default="gpt-4o-mini")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config_json: Mapped[dict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AgentMemoryModel(Base, IdMixin):
    __tablename__ = "agent_memories"
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    agent_id: Mapped[str] = mapped_column(String(80), index=True)
    summary_stats: Mapped[dict] = mapped_column(default=dict)
    episodes: Mapped[list] = mapped_column(default=list)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class AgentDecision(Base, IdMixin):
    __tablename__ = "agent_decisions"
    __table_args__ = (Index("ix_agent_decisions_round", "market_round_id", "agent_id"),)
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    agent_id: Mapped[str] = mapped_column(String(80), index=True)
    agent_type: Mapped[str] = mapped_column(String(40))
    mode: Mapped[str] = mapped_column(String(20))
    market_round_id: Mapped[str] = mapped_column(String(64), index=True)
    observation_hash: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(60))
    proposal: Mapped[dict] = mapped_column(default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    reasoning_summary: Mapped[str] = mapped_column(String(500), default="")
    assumptions: Mapped[list] = mapped_column(default=list)
    risk_factors: Mapped[list] = mapped_column(default=list)
    guard_status: Mapped[str] = mapped_column(String(20))
    guard_reason_codes: Mapped[list] = mapped_column(default=list)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    final_action: Mapped[str] = mapped_column(String(40))
    final_quantity: Mapped[float] = mapped_column(Float, default=0.0)
    final_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AgentOutcome(Base, IdMixin):
    __tablename__ = "agent_outcomes"
    __table_args__ = (Index("ix_agent_outcomes_round", "market_round_id", "agent_id"),)
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    agent_id: Mapped[str] = mapped_column(String(80), index=True)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_decisions.id"), nullable=True)
    market_round_id: Mapped[str] = mapped_column(String(64), index=True)
    proposed_action: Mapped[str] = mapped_column(String(40))
    executed_action: Mapped[str] = mapped_column(String(40))
    proposed_quantity: Mapped[float] = mapped_column(Float, default=0.0)
    executed_quantity: Mapped[float] = mapped_column(Float, default=0.0)
    market_price: Mapped[float] = mapped_column(Float, default=0.0)
    local_lmp: Mapped[float] = mapped_column(Float, default=0.0)
    energy_bought: Mapped[float] = mapped_column(Float, default=0.0)
    energy_sold: Mapped[float] = mapped_column(Float, default=0.0)
    energy_revenue: Mapped[float] = mapped_column(Float, default=0.0)
    energy_cost: Mapped[float] = mapped_column(Float, default=0.0)
    reserve_revenue: Mapped[float] = mapped_column(Float, default=0.0)
    demand_response_revenue: Mapped[float] = mapped_column(Float, default=0.0)
    battery_degradation_cost: Mapped[float] = mapped_column(Float, default=0.0)
    shortfall_penalty: Mapped[float] = mapped_column(Float, default=0.0)
    other_penalties: Mapped[float] = mapped_column(Float, default=0.0)
    net_reward: Mapped[float] = mapped_column(Float, default=0.0)
    counterfactual_reward: Mapped[float | None] = mapped_column(Float, nullable=True)
    delivery_ratio: Mapped[float] = mapped_column(Float, default=1.0)
    forecast_error: Mapped[float] = mapped_column(Float, default=0.0)
    guard_intervention: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AgentMarketRound(Base, IdMixin):
    __tablename__ = "agent_market_rounds"
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    round_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    snapshot_timestamp: Mapped[datetime] = mapped_column()
    status: Mapped[str] = mapped_column(String(20), default="COMPLETED")
    participating_agents: Mapped[list] = mapped_column(default=list)
    summary: Mapped[dict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AgentExperiment(Base, IdMixin):
    __tablename__ = "agent_experiments"
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    scenario: Mapped[str] = mapped_column(String(40))
    seed: Mapped[int] = mapped_column(default=42)
    agent_mode: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(60))
    prompt_version: Mapped[str] = mapped_column(String(40))
    duration_steps: Mapped[int] = mapped_column(default=4)
    results: Mapped[dict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AIProviderUsage(Base, IdMixin):
    __tablename__ = "ai_provider_usages"
    microgrid_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("microgrids.id"), nullable=True)
    agent_id: Mapped[str] = mapped_column(String(80), index=True)
    market_round_id: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(60))
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
