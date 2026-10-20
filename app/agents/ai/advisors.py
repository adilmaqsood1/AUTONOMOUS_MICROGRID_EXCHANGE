"""Grid AI Advisor and Market Analyst explanatory agents."""
from __future__ import annotations

import time
from typing import Any

from app.agents.ai.context import AgentReasoningContext
from app.agents.ai.provider import AIReasoningProvider, get_ai_provider
from app.agents.ai.schemas import GridAdvisorProposal, MarketExplanation
from app.core.timeutil import utcnow


class GridAIAgent:
    """Non-executing advisory intelligence providing grid health assessments and recommendations."""

    def __init__(self, provider: AIReasoningProvider | None = None):
        self.provider = provider or get_ai_provider()

    async def advise(
        self,
        market_round_id: str,
        grid_state: dict[str, Any],
        reserve_state: dict[str, Any] | None = None,
    ) -> GridAdvisorProposal:
        ctx = AgentReasoningContext(
            market_round_id=market_round_id,
            timestamp=utcnow(),
            participant_id="operator-grid-advisor",
            agent_type="GRID_ADVISOR",
            grid_state=grid_state,
            reserve_state=reserve_state or {},
        )
        proposal, _ = await self.provider.reason(ctx, GridAdvisorProposal)
        return proposal


class MarketAnalystAgent:
    """Non-trading explanation agent generating strictly grounded post-clearing explanations."""

    def __init__(self, provider: AIReasoningProvider | None = None):
        self.provider = provider or get_ai_provider()

    async def explain_round(
        self,
        market_round_id: str,
        market_state: dict[str, Any],
        clearing_summary: dict[str, Any],
        grid_metrics: dict[str, Any] | None = None,
    ) -> MarketExplanation:
        ctx = AgentReasoningContext(
            market_round_id=market_round_id,
            timestamp=utcnow(),
            participant_id="public-market-analyst",
            agent_type="MARKET_ANALYST",
            market_state=market_state | clearing_summary,
            grid_state=grid_metrics or {},
        )
        explanation, _ = await self.provider.reason(ctx, MarketExplanation)
        return explanation
