"""Hybrid AI participant agent strategies with deterministic policy guard and fallback."""
from __future__ import annotations

import asyncio
import time
import uuid
from decimal import Decimal
from typing import Any

from app.agents.ai.context import AgentReasoningContext
from app.agents.ai.guard import AgentPolicyGuard
from app.agents.ai.memory import load_agent_memory
from app.agents.ai.prompts import PROMPT_VERSIONS
from app.agents.ai.provider import AIReasoningProvider, get_ai_provider
from app.agents.ai.schemas import AgentStrategyProposal, GuardResult
from app.agents.base import D, Intent, MarketObservation, q6
from app.agents.battery import BatteryAgent
from app.agents.ev import EVAgent
from app.agents.participants import (
    CommercialAgent,
    ConsumerAgent,
    GenerationView,
    GeneratorAgent,
    ProsumerAgent,
)
from app.core.enums import AgentAction, GuardOutcome, StrategyMode
from app.core.timeutil import utcnow
from app.models.ai import AgentDecision


class HybridAgentRunner:
    """Orchestrates single-agent decision making across DETERMINISTIC, SHADOW_AI, and AI_ASSISTED modes."""

    def __init__(
        self,
        provider: AIReasoningProvider | None = None,
        guard: AgentPolicyGuard | None = None,
        timeout_seconds: float = 3.0,
    ):
        self.provider = provider or get_ai_provider()
        self.guard = guard or AgentPolicyGuard()
        self.timeout_seconds = timeout_seconds

    async def decide_generator(
        self,
        participant_id: str,
        asset_id: str,
        view: GenerationView,
        observation: MarketObservation,
        market_round_id: str,
        mode: StrategyMode = StrategyMode.AI_ASSISTED,
        recent_memory_dict: dict[str, Any] | None = None,
    ) -> tuple[Intent, AgentDecision]:
        t0 = time.time()
        agent_id = f"gen-{participant_id[:8]}"
        deterministic_agent = GeneratorAgent()
        det_intent = deterministic_agent.decide(participant_id, asset_id, view, observation)

        ctx = AgentReasoningContext(
            market_round_id=market_round_id,
            timestamp=observation.window_start,
            participant_id=participant_id,
            agent_type="GENERATOR",
            asset_state={"rated_power_kw": view.rated_kw, "maximum_generation_kw": view.maximum_kw,
                         "marginal_cost": view.marginal_cost, "renewable": view.renewable},
            market_state={"expected_price": float(observation.expected_price),
                          "import_price": float(observation.import_price),
                          "export_price": float(observation.export_price), "hours": observation.hours},
            forecast_state={"predicted_value": view.predicted_kwh, "lower_bound": view.lower_kwh},
            grid_state={"grid_connected": observation.grid_connected, "island_mode": observation.island_mode},
            recent_agent_memory=recent_memory_dict or {},
            hard_constraints={"maximum_kw": view.maximum_kw},
        )
        obs_hash = ctx.compute_hash()

        if mode == StrategyMode.DETERMINISTIC:
            dec = AgentDecision(
                microgrid_id=uuid.uuid4(),
                agent_id=agent_id, agent_type="GENERATOR", mode=mode.value, market_round_id=market_round_id,
                observation_hash=obs_hash, prompt_version="deterministic_rules", provider="none", model="none",
                proposal=det_intent.explanation(), confidence=1.0, reasoning_summary="Deterministic reserve policy rules",
                guard_status="APPROVED", fallback_used=False, final_action=det_intent.action.value,
                final_quantity=float(det_intent.quantity_kwh),
                final_price=float(det_intent.limit_price) if det_intent.limit_price else None,
                latency_ms=(time.time() - t0) * 1000, created_at=utcnow(),
            )
            return det_intent, dec

        # Execute AI reasoning with timeout
        ai_proposal: AgentStrategyProposal | None = None
        guard_res: GuardResult | None = None
        fallback_used = False
        fallback_reason: str | None = None
        telemetry: dict[str, Any] = {}

        try:
            ai_proposal, telemetry = await asyncio.wait_for(
                self.provider.reason(ctx, AgentStrategyProposal),
                timeout=self.timeout_seconds,
            )
            guard_res = self.guard.evaluate(ai_proposal, ctx)
            if guard_res.status in (GuardOutcome.APPROVED, GuardOutcome.CLAMPED):
                chosen_action = guard_res.approved_action
                chosen_qty = guard_res.approved_quantity_kwh
                chosen_price = guard_res.approved_limit_price or det_intent.limit_price
            else:
                fallback_used = True
                fallback_reason = f"Guard rejected: {', '.join(guard_res.reason_codes)}"
        except Exception as exc:
            fallback_used = True
            fallback_reason = f"AI reasoning failure: {type(exc).__name__} - {exc}"

        if mode == StrategyMode.SHADOW_AI:
            # Shadow AI evaluates AI proposal but executes ONLY deterministic decision
            dec = AgentDecision(
                microgrid_id=uuid.uuid4(),
                agent_id=agent_id, agent_type="GENERATOR", mode=mode.value, market_round_id=market_round_id,
                observation_hash=obs_hash, prompt_version=PROMPT_VERSIONS.get("GENERATOR", "v1"),
                provider="ai_provider", model="configured_model",
                proposal=ai_proposal.model_dump(mode="json") if ai_proposal else {},
                confidence=ai_proposal.confidence if ai_proposal else 0.0,
                reasoning_summary=ai_proposal.reasoning_summary if ai_proposal else "Failed",
                guard_status=guard_res.status.value if guard_res else "FAILED",
                guard_reason_codes=guard_res.reason_codes if guard_res else [fallback_reason or "error"],
                fallback_used=False, final_action=det_intent.action.value,
                final_quantity=float(det_intent.quantity_kwh),
                final_price=float(det_intent.limit_price) if det_intent.limit_price else None,
                latency_ms=(time.time() - t0) * 1000, created_at=utcnow(),
            )
            return det_intent, dec

        # AI_ASSISTED mode
        if fallback_used or ai_proposal is None or guard_res is None or guard_res.status == GuardOutcome.REJECTED:
            dec = AgentDecision(
                microgrid_id=uuid.uuid4(),
                agent_id=agent_id, agent_type="GENERATOR", mode=mode.value, market_round_id=market_round_id,
                observation_hash=obs_hash, prompt_version=PROMPT_VERSIONS.get("GENERATOR", "v1"),
                provider="ai_provider", model="configured_model",
                proposal=ai_proposal.model_dump(mode="json") if ai_proposal else {},
                confidence=ai_proposal.confidence if ai_proposal else 0.0,
                reasoning_summary=ai_proposal.reasoning_summary if ai_proposal else "Fallback executed",
                guard_status=guard_res.status.value if guard_res else "FALLBACK",
                guard_reason_codes=guard_res.reason_codes if guard_res else [fallback_reason or "unknown"],
                fallback_used=True, fallback_reason=fallback_reason,
                final_action=det_intent.action.value, final_quantity=float(det_intent.quantity_kwh),
                final_price=float(det_intent.limit_price) if det_intent.limit_price else None,
                latency_ms=(time.time() - t0) * 1000, created_at=utcnow(),
            )
            return det_intent, dec

        # AI Approved/Clamped intent
        ai_intent = Intent(
            agent="GeneratorAIAgent",
            participant_id=participant_id,
            asset_id=asset_id,
            action=guard_res.approved_action,
            quantity_kwh=q6(guard_res.approved_quantity_kwh),
            limit_price=q6(chosen_price) if chosen_price else None,
            reason_codes=["AI_ASSISTED_APPROVED"] + (["CLAMPED"] if guard_res.clamped else []),
            inputs=ctx.to_safe_dict(),
        )
        dec = AgentDecision(
            microgrid_id=uuid.uuid4(),
            agent_id=agent_id, agent_type="GENERATOR", mode=mode.value, market_round_id=market_round_id,
            observation_hash=obs_hash, prompt_version=PROMPT_VERSIONS.get("GENERATOR", "v1"),
            provider="ai_provider", model="configured_model",
            proposal=ai_proposal.model_dump(mode="json"),
            confidence=ai_proposal.confidence,
            reasoning_summary=ai_proposal.reasoning_summary,
            assumptions=ai_proposal.assumptions,
            risk_factors=ai_proposal.risk_factors,
            guard_status=guard_res.status.value,
            guard_reason_codes=guard_res.reason_codes,
            fallback_used=False,
            final_action=ai_intent.action.value,
            final_quantity=float(ai_intent.quantity_kwh),
            final_price=float(ai_intent.limit_price) if ai_intent.limit_price else None,
            latency_ms=(time.time() - t0) * 1000,
            created_at=utcnow(),
        )
        return ai_intent, dec

    async def decide_battery(
        self,
        participant_id: str,
        asset_id: str,
        state: Any,
        observation: MarketObservation,
        market_round_id: str,
        surplus_kwh: float = 0.0,
        deficit_kwh: float = 0.0,
        mode: StrategyMode = StrategyMode.AI_ASSISTED,
        recent_memory_dict: dict[str, Any] | None = None,
    ) -> tuple[Intent, AgentDecision]:
        t0 = time.time()
        agent_id = f"batt-{participant_id[:8]}"
        deterministic_agent = BatteryAgent()
        det_intent = deterministic_agent.decide(participant_id, asset_id, state, observation, surplus_kwh, deficit_kwh)

        ctx = AgentReasoningContext(
            market_round_id=market_round_id,
            timestamp=observation.window_start,
            participant_id=participant_id,
            agent_type="BATTERY",
            asset_state={
                "capacity_kwh": state.capacity_kwh,
                "soc_percent": state.soc_percent,
                "stored_kwh": state.stored_kwh,
                "minimum_soc_percent": state.minimum_soc_percent,
                "maximum_soc_percent": state.maximum_soc_percent,
                "rated_power_kw": state.rated_power_kw,
            },
            market_state={
                "expected_price": float(observation.expected_price),
                "expected_peak_price": float(observation.expected_peak_price),
                "expected_valley_price": float(observation.expected_valley_price),
                "import_price": float(observation.import_price),
                "export_price": float(observation.export_price),
                "hours": observation.hours,
            },
            grid_state={"island_mode": observation.island_mode},
            recent_agent_memory=recent_memory_dict or {},
        )
        obs_hash = ctx.compute_hash()

        if mode == StrategyMode.DETERMINISTIC:
            dec = AgentDecision(
                microgrid_id=uuid.uuid4(),
                agent_id=agent_id, agent_type="BATTERY", mode=mode.value, market_round_id=market_round_id,
                observation_hash=obs_hash, prompt_version="deterministic_rules", provider="none", model="none",
                proposal=det_intent.explanation(), confidence=1.0, reasoning_summary="Deterministic battery rules",
                guard_status="APPROVED", fallback_used=False, final_action=det_intent.action.value,
                final_quantity=float(det_intent.quantity_kwh),
                final_price=float(det_intent.limit_price) if det_intent.limit_price else None,
                latency_ms=(time.time() - t0) * 1000, created_at=utcnow(),
            )
            return det_intent, dec

        ai_proposal, guard_res = None, None
        fallback_used = False
        fallback_reason: str | None = None

        try:
            ai_proposal, _ = await asyncio.wait_for(
                self.provider.reason(ctx, AgentStrategyProposal),
                timeout=self.timeout_seconds,
            )
            guard_res = self.guard.evaluate(ai_proposal, ctx)
            if guard_res.status not in (GuardOutcome.APPROVED, GuardOutcome.CLAMPED):
                fallback_used = True
                fallback_reason = f"Guard rejected: {', '.join(guard_res.reason_codes)}"
        except Exception as exc:
            fallback_used = True
            fallback_reason = f"AI reasoning failure: {type(exc).__name__} - {exc}"

        if mode == StrategyMode.SHADOW_AI:
            dec = AgentDecision(
                microgrid_id=uuid.uuid4(),
                agent_id=agent_id, agent_type="BATTERY", mode=mode.value, market_round_id=market_round_id,
                observation_hash=obs_hash, prompt_version=PROMPT_VERSIONS.get("BATTERY", "v1"),
                provider="ai_provider", model="configured_model",
                proposal=ai_proposal.model_dump(mode="json") if ai_proposal else {},
                confidence=ai_proposal.confidence if ai_proposal else 0.0,
                reasoning_summary=ai_proposal.reasoning_summary if ai_proposal else "Failed",
                guard_status=guard_res.status.value if guard_res else "FAILED",
                guard_reason_codes=guard_res.reason_codes if guard_res else [fallback_reason or "error"],
                fallback_used=False, final_action=det_intent.action.value,
                final_quantity=float(det_intent.quantity_kwh),
                final_price=float(det_intent.limit_price) if det_intent.limit_price else None,
                latency_ms=(time.time() - t0) * 1000, created_at=utcnow(),
            )
            return det_intent, dec

        if fallback_used or ai_proposal is None or guard_res is None or guard_res.status == GuardOutcome.REJECTED:
            dec = AgentDecision(
                microgrid_id=uuid.uuid4(),
                agent_id=agent_id, agent_type="BATTERY", mode=mode.value, market_round_id=market_round_id,
                observation_hash=obs_hash, prompt_version=PROMPT_VERSIONS.get("BATTERY", "v1"),
                provider="ai_provider", model="configured_model",
                proposal=ai_proposal.model_dump(mode="json") if ai_proposal else {},
                confidence=ai_proposal.confidence if ai_proposal else 0.0,
                reasoning_summary=ai_proposal.reasoning_summary if ai_proposal else "Fallback executed",
                guard_status=guard_res.status.value if guard_res else "FALLBACK",
                guard_reason_codes=guard_res.reason_codes if guard_res else [fallback_reason or "unknown"],
                fallback_used=True, fallback_reason=fallback_reason,
                final_action=det_intent.action.value, final_quantity=float(det_intent.quantity_kwh),
                final_price=float(det_intent.limit_price) if det_intent.limit_price else None,
                latency_ms=(time.time() - t0) * 1000, created_at=utcnow(),
            )
            return det_intent, dec

        # Approved AI intent
        price = guard_res.approved_limit_price or det_intent.limit_price
        ai_intent = Intent(
            agent="BatteryAIAgent",
            participant_id=participant_id,
            asset_id=asset_id,
            action=guard_res.approved_action,
            quantity_kwh=q6(guard_res.approved_quantity_kwh),
            limit_price=q6(price) if price else None,
            reason_codes=["AI_ASSISTED_BATTERY"] + (["CLAMPED"] if guard_res.clamped else []),
            inputs=ctx.to_safe_dict(),
        )
        dec = AgentDecision(
            microgrid_id=uuid.uuid4(),
            agent_id=agent_id, agent_type="BATTERY", mode=mode.value, market_round_id=market_round_id,
            observation_hash=obs_hash, prompt_version=PROMPT_VERSIONS.get("BATTERY", "v1"),
            provider="ai_provider", model="configured_model",
            proposal=ai_proposal.model_dump(mode="json"),
            confidence=ai_proposal.confidence,
            reasoning_summary=ai_proposal.reasoning_summary,
            assumptions=ai_proposal.assumptions,
            risk_factors=ai_proposal.risk_factors,
            guard_status=guard_res.status.value,
            guard_reason_codes=guard_res.reason_codes,
            fallback_used=False,
            final_action=ai_intent.action.value,
            final_quantity=float(ai_intent.quantity_kwh),
            final_price=float(ai_intent.limit_price) if ai_intent.limit_price else None,
            latency_ms=(time.time() - t0) * 1000,
            created_at=utcnow(),
        )
        return ai_intent, dec
