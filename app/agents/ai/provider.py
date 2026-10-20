"""AI Provider abstraction with Mock and OpenAI-compatible implementations and circuit breaker."""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from app.agents.ai.context import AgentReasoningContext
from app.agents.ai.parser import AIParseError, parse_and_validate
from app.agents.ai.prompts import build_agent_prompt
from app.agents.ai.schemas import (
    AgentStrategyProposal,
    ContributingFactor,
    GridAdvisorProposal,
    MarketExplanation,
    MetricReference,
)
from app.core.config import get_settings
from app.core.enums import AgentAction

T = TypeVar("T", bound=BaseModel)


class CircuitBreakerOpenError(RuntimeError):
    """Raised when the AI circuit breaker is open due to repeated errors."""
    pass


class AICircuitBreaker:
    """Provider-level circuit breaker to prevent cascade failures and blocking the market."""
    def __init__(self, failure_threshold: int = 5, cooldown_seconds: float = 30.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: float | None = None
        self.state: str = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self.total_calls = 0
        self.total_latencies: list[float] = []

    def can_execute(self) -> bool:
        if self.state == "CLOSED":
            return True
        if self.state == "OPEN":
            if self.last_failure_time and (time.time() - self.last_failure_time) > self.cooldown_seconds:
                self.state = "HALF_OPEN"
                return True
            return False
        # HALF_OPEN: allow probe
        return True

    def record_success(self, latency_ms: float) -> None:
        self.total_calls += 1
        self.success_count += 1
        self.total_latencies.append(latency_ms)
        if len(self.total_latencies) > 100:
            self.total_latencies.pop(0)
        self.failure_count = 0
        self.state = "CLOSED"

    def record_failure(self) -> None:
        self.total_calls += 1
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "OPEN"

    def get_status(self) -> dict[str, Any]:
        avg_latency = (sum(self.total_latencies) / len(self.total_latencies)) if self.total_latencies else 0.0
        success_rate = (self.success_count / self.total_calls) if self.total_calls > 0 else 1.0
        return {
            "circuit_breaker_state": self.state,
            "failure_count": self.failure_count,
            "success_rate": round(success_rate, 4),
            "average_latency_ms": round(avg_latency, 2),
            "total_calls": self.total_calls,
        }


class AIReasoningProvider(ABC):
    """Abstract interface for AI reasoning models."""

    def __init__(self):
        s = get_settings()
        self.circuit_breaker = AICircuitBreaker(
            failure_threshold=s.ai_circuit_breaker_failure_threshold,
            cooldown_seconds=s.ai_circuit_breaker_cooldown_seconds,
        )

    @abstractmethod
    async def reason(
        self,
        context: AgentReasoningContext,
        output_schema: type[T] = AgentStrategyProposal,
    ) -> tuple[T, dict[str, Any]]:
        """Reasons over context and returns validated structured model + usage telemetry."""
        ...

    def get_health(self) -> dict[str, Any]:
        return self.circuit_breaker.get_status()


class MockAIReasoningProvider(AIReasoningProvider):
    """Hermetic deterministic provider for offline simulation, unit tests, and CI."""

    def __init__(self, force_failure: bool = False, force_delay_seconds: float = 0.0,
                 force_malformed_json: bool = False, adversarial_mode: str | None = None):
        super().__init__()
        self.force_failure = force_failure
        self.force_delay_seconds = force_delay_seconds
        self.force_malformed_json = force_malformed_json
        self.adversarial_mode = adversarial_mode

    async def reason(
        self,
        context: AgentReasoningContext,
        output_schema: type[T] = AgentStrategyProposal,
    ) -> tuple[T, dict[str, Any]]:
        t0 = time.time()
        if not self.circuit_breaker.can_execute():
            raise CircuitBreakerOpenError("AI Circuit breaker is OPEN")

        if self.force_delay_seconds > 0:
            await asyncio.sleep(self.force_delay_seconds)

        if self.force_failure:
            self.circuit_breaker.record_failure()
            raise RuntimeError("Simulated AI Provider API Error (503 Service Unavailable)")

        if self.force_malformed_json:
            self.circuit_breaker.record_failure()
            raise AIParseError("Simulated malformed JSON response: { 'action': BUY ...", raw_text="{malformed")

        # Handle adversarial test modes
        if self.adversarial_mode == "OVER_QUANTITY":
            proposal = AgentStrategyProposal(
                action=AgentAction.SELL,
                confidence=0.95,
                reasoning_summary="Attempting adversarial over-offer of 10000 kWh",
                quantity_kwh=Decimal("10000.0"),
                limit_price=Decimal("0.05"),
                assumptions=["unlimited power"],
                risk_factors=["grid collapse"],
            )
            lat = (time.time() - t0) * 1000
            self.circuit_breaker.record_success(lat)
            return proposal, {"input_tokens": 120, "output_tokens": 60, "estimated_cost": 0.0, "latency_ms": lat}

        if self.adversarial_mode == "BELOW_SOC":
            proposal = AgentStrategyProposal(
                action=AgentAction.DISCHARGE,
                confidence=0.99,
                reasoning_summary="Attempting to drain battery to 0% SOC",
                quantity_kwh=Decimal("50.0"),
                target_soc=0.0,
                assumptions=["drain all storage"],
                risk_factors=["depletion"],
            )
            lat = (time.time() - t0) * 1000
            self.circuit_breaker.record_success(lat)
            return proposal, {"input_tokens": 120, "output_tokens": 60, "estimated_cost": 0.0, "latency_ms": lat}

        # Specialized schema handlers
        if output_schema == GridAdvisorProposal:
            res = self._mock_grid_advisor(context)
            lat = (time.time() - t0) * 1000
            self.circuit_breaker.record_success(lat)
            return res, {"input_tokens": 150, "output_tokens": 80, "estimated_cost": 0.0, "latency_ms": lat}

        if output_schema == MarketExplanation:
            res = self._mock_market_analyst(context)
            lat = (time.time() - t0) * 1000
            self.circuit_breaker.record_success(lat)
            return res, {"input_tokens": 180, "output_tokens": 110, "estimated_cost": 0.0, "latency_ms": lat}

        # Domain participant agent proposals
        proposal = self._generate_participant_proposal(context)
        lat = (time.time() - t0) * 1000
        self.circuit_breaker.record_success(lat)
        return proposal, {"input_tokens": 160, "output_tokens": 90, "estimated_cost": 0.0, "latency_ms": lat}

    def _generate_participant_proposal(self, ctx: AgentReasoningContext) -> AgentStrategyProposal:
        atype = ctx.agent_type.upper()
        ast = ctx.asset_state
        mst = ctx.market_state
        fst = ctx.forecast_state
        gst = ctx.grid_state
        is_islanded = gst.get("island_mode", False)

        cur_price = Decimal(str(mst.get("expected_price", "0.10")))
        peak_price = Decimal(str(mst.get("expected_peak_price", "0.15")))
        import_price = Decimal(str(mst.get("import_price", "0.18")))
        export_price = Decimal(str(mst.get("export_price", "0.06")))

        # 1. Generator Agent
        if "GENERATOR" in atype:
            pred_kwh = Decimal(str(fst.get("predicted_value", ast.get("rated_power_kw", 10.0))))
            lower_kwh = Decimal(str(fst.get("lower_bound", float(pred_kwh) * 0.8)))
            mc = Decimal(str(ast.get("marginal_cost", "0.05")))
            safe_qty = max(Decimal("0"), lower_kwh)
            if cur_price < mc:
                return AgentStrategyProposal(
                    action=AgentAction.HOLD,
                    confidence=0.92,
                    reasoning_summary="Current market price is below generator marginal cost; holding generation.",
                    quantity_kwh=Decimal("0"),
                    assumptions=["price below marginal cost"],
                    risk_factors=["negative operational margin"],
                )
            return AgentStrategyProposal(
                action=AgentAction.SELL,
                confidence=0.88,
                reasoning_summary="Offering risk-adjusted renewable generation surplus into day-ahead/real-time market.",
                quantity_kwh=safe_qty,
                limit_price=max(mc * Decimal("1.02"), export_price),
                assumptions=["generation follows forecast lower bound"],
                risk_factors=["cloud transient uncertainty"],
            )

        # 2. Battery Agent
        if "BATTERY" in atype:
            soc = float(ast.get("soc_percent", 50.0))
            cap = float(ast.get("capacity_kwh", 20.0))
            rated_kw = float(ast.get("rated_power_kw", 5.0))
            min_soc = float(ast.get("minimum_soc_percent", 20.0))
            max_soc = float(ast.get("maximum_soc_percent", 95.0))

            if is_islanded:
                return AgentStrategyProposal(
                    action=AgentAction.HOLD,
                    confidence=0.95,
                    reasoning_summary="Microgrid is islanded; preserving storage for resilience and critical loads.",
                    quantity_kwh=Decimal("0"),
                    target_soc=soc,
                    assumptions=["islanded resilience priority"],
                    risk_factors=["extended grid outage"],
                )
            if cur_price <= export_price * Decimal("1.1") and soc < max_soc - 5:
                charge_qty = Decimal(str(min(rated_kw, (max_soc - soc) / 100.0 * cap)))
                return AgentStrategyProposal(
                    action=AgentAction.CHARGE,
                    confidence=0.85,
                    reasoning_summary="Energy price is low; charging battery to store for future peak arbitrage.",
                    quantity_kwh=charge_qty,
                    limit_price=cur_price * Decimal("1.05"),
                    target_soc=max_soc,
                    assumptions=["evening peak price will exceed current price"],
                    risk_factors=["curtailment if demand drops"],
                )
            if cur_price >= peak_price * Decimal("0.9") and soc > min_soc + 5:
                discharge_qty = Decimal(str(min(rated_kw, (soc - min_soc) / 100.0 * cap)))
                return AgentStrategyProposal(
                    action=AgentAction.DISCHARGE,
                    confidence=0.89,
                    reasoning_summary="Peak price period; discharging stored energy for arbitrage revenue.",
                    quantity_kwh=discharge_qty,
                    limit_price=cur_price * Decimal("0.95"),
                    target_soc=min_soc,
                    assumptions=["price peak is active"],
                    risk_factors=["battery cycle degradation"],
                )
            return AgentStrategyProposal(
                action=AgentAction.OFFER_RESERVE,
                confidence=0.82,
                reasoning_summary="Holding energy while offering operational reserve capacity to the microgrid.",
                reserve_kw=Decimal(str(min(rated_kw, 5.0))),
                limit_price=Decimal("0.04"),
                assumptions=["reserve availability"],
                risk_factors=["activation probability"],
            )

        # 3. EV Agent
        if "EV" in atype:
            soc = float(ast.get("soc_percent", 45.0))
            cap = float(ast.get("capacity_kwh", 40.0))
            req_soc = float(ast.get("required_departure_soc_percent", 80.0))
            hours_to_dep = float(ast.get("hours_until_departure", 4.0))
            charging_rate = float(ast.get("charging_power_kw", 7.0))
            v2g_ok = bool(ast.get("v2g_enabled", False))

            needed_kwh = max(0.0, (req_soc - soc) / 100.0 * cap)
            max_possible_charge = charging_rate * hours_to_dep

            if needed_kwh > max_possible_charge * 0.7 or soc < req_soc:
                charge_qty = Decimal(str(min(charging_rate, needed_kwh)))
                return AgentStrategyProposal(
                    action=AgentAction.CHARGE,
                    confidence=0.96,
                    reasoning_summary="EV departure deadline approaching; prioritizing vehicle charging to guarantee mobility.",
                    quantity_kwh=charge_qty,
                    limit_price=import_price * Decimal("1.1"),
                    target_soc=req_soc,
                    assumptions=["driver departure time is firm"],
                    risk_factors=["undercharging penalty"],
                )
            if v2g_ok and soc > req_soc + 15 and cur_price >= peak_price * Decimal("0.9"):
                v2g_qty = Decimal(str(min(charging_rate, (soc - req_soc) / 100.0 * cap)))
                return AgentStrategyProposal(
                    action=AgentAction.V2G_SELL,
                    confidence=0.84,
                    reasoning_summary="Surplus EV battery headroom above required departure SOC offered via V2G at peak price.",
                    quantity_kwh=v2g_qty,
                    limit_price=cur_price * Decimal("0.95"),
                    assumptions=["excess SOC above travel requirement"],
                    risk_factors=["degradation vs compensation"],
                )
            return AgentStrategyProposal(
                action=AgentAction.HOLD,
                confidence=0.90,
                reasoning_summary="EV is sufficiently charged for mobility schedule; holding idle.",
                quantity_kwh=Decimal("0"),
                assumptions=["sufficient battery buffer"],
                risk_factors=[],
            )

        # 4. Prosumer Agent
        if "PROSUMER" in atype:
            pred_gen = Decimal(str(fst.get("predicted_generation_kwh", "5.0")))
            pred_dem = Decimal(str(fst.get("predicted_demand_kwh", "3.0")))
            net = pred_gen - pred_dem
            if net > Decimal("0.1"):
                return AgentStrategyProposal(
                    action=AgentAction.SELL,
                    confidence=0.87,
                    reasoning_summary="Net self-generation surplus after serving household load.",
                    quantity_kwh=net,
                    limit_price=export_price * Decimal("1.05"),
                    assumptions=["household consumption stable"],
                    risk_factors=["cloud cover variance"],
                )
            if net < -Decimal("0.1"):
                return AgentStrategyProposal(
                    action=AgentAction.BUY,
                    confidence=0.91,
                    reasoning_summary="Purchasing deficit to meet household demand.",
                    quantity_kwh=-net,
                    limit_price=import_price * Decimal("0.98"),
                    assumptions=["utility fallback available"],
                    risk_factors=["local LMP volatility"],
                )
            return AgentStrategyProposal(
                action=AgentAction.HOLD,
                confidence=0.95,
                reasoning_summary="Prosumer generation exactly balances household demand.",
                quantity_kwh=Decimal("0"),
                assumptions=["balanced"],
                risk_factors=[],
            )

        # 5. Consumer & Commercial
        dem_kwh = Decimal(str(fst.get("predicted_value", ast.get("current_demand_kw", "10.0"))))
        flexible = bool(ast.get("flexible", False))
        if cur_price >= Decimal("0.20") and flexible:
            return AgentStrategyProposal(
                action=AgentAction.DEFER_LOAD if "CONSUMER" in atype else AgentAction.ACCEPT_DEMAND_RESPONSE,
                confidence=0.86,
                reasoning_summary="High market price detected; deferring flexible load to reduce expenditure.",
                quantity_kwh=dem_kwh * Decimal("0.3"),
                limit_price=cur_price,
                assumptions=["flexible process can be delayed"],
                risk_factors=["process deadline"],
            )
        return AgentStrategyProposal(
            action=AgentAction.BUY,
            confidence=0.93,
            reasoning_summary="Procuring energy for consumer demand up to import price cap.",
            quantity_kwh=dem_kwh,
            limit_price=import_price * Decimal("0.98"),
            assumptions=["essential load"],
            risk_factors=["price spikes"],
        )

    def _mock_grid_advisor(self, ctx: AgentReasoningContext) -> GridAdvisorProposal:
        gst = ctx.grid_state
        loading = float(gst.get("max_feeder_loading_percent", 65.0))
        is_island = bool(gst.get("island_mode", False))
        if is_island:
            return GridAdvisorProposal(
                recommendation="PREPARE_FOR_ISLANDING",
                urgency="HIGH",
                reasoning_summary="Microgrid is operating in islanded mode; recommend maximizing local reserve procurement.",
                confidence=0.94,
                observed_signals=["grid_breaker_open", "frequency_containment_active"],
            )
        if loading > 85.0:
            return GridAdvisorProposal(
                recommendation="INVESTIGATE_CONGESTION",
                target_feeder=str(gst.get("congested_feeder", "Feeder-B-C")),
                urgency="HIGH",
                recommended_dr_kw=15.0,
                reasoning_summary=f"Feeder loading reached {loading}%; recommend demand response curtailment at downstream nodes.",
                confidence=0.88,
                observed_signals=[f"loading_{loading}_percent"],
            )
        return GridAdvisorProposal(
            recommendation="NORMAL_OPERATION",
            urgency="NORMAL",
            reasoning_summary="Grid voltages, feeder thermal margins, and reserve pools are within safe bounds.",
            confidence=0.98,
            observed_signals=["all_lines_below_80_percent"],
        )

    def _mock_market_analyst(self, ctx: AgentReasoningContext) -> MarketExplanation:
        mst = ctx.market_state
        lmp = float(mst.get("expected_price", 0.095))
        return MarketExplanation(
            headline="Market Cleared Successfully at Equilibrium",
            summary=f"Nodal LMP cleared at ${lmp:.3f}/kWh supported by balanced local renewable supply.",
            contributing_factors=[
                ContributingFactor(factor="Solar generation", direction="INCREASE", impact_estimate="moderate"),
                ContributingFactor(factor="Battery participation", direction="NEUTRAL", impact_estimate="stable"),
            ],
            metric_references=[
                MetricReference(metric_name="clearing_price", supplied_value=lmp),
            ],
            confidence=0.95,
        )


class OpenAICompatibleReasoningProvider(AIReasoningProvider):
    """External LLM provider calling any OpenAI-compatible API (OpenAI, Ollama, vLLM, Groq)."""

    def __init__(self):
        super().__init__()
        s = get_settings()
        self.base_url = s.ai_base_url.rstrip("/")
        self.api_key = s.ai_api_key
        self.model = s.ai_model
        self.timeout = s.ai_timeout_seconds
        self.temperature = s.ai_temperature
        self.max_tokens = s.ai_max_tokens

    async def reason(
        self,
        context: AgentReasoningContext,
        output_schema: type[T] = AgentStrategyProposal,
    ) -> tuple[T, dict[str, Any]]:
        if not self.circuit_breaker.can_execute():
            raise CircuitBreakerOpenError("AI Circuit breaker is OPEN")

        system_prompt, user_content, prompt_ver = build_agent_prompt(
            context.agent_type, context.serialize_for_prompt()
        )

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }

        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                raw_text = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                in_tok = usage.get("prompt_tokens", 0)
                out_tok = usage.get("completion_tokens", 0)
                est_cost = (in_tok * 0.00000015) + (out_tok * 0.0000006)
        except Exception as exc:
            self.circuit_breaker.record_failure()
            raise RuntimeError(f"OpenAI-compatible LLM invocation failed: {exc}") from exc

        try:
            model_instance = parse_and_validate(raw_text, output_schema)
        except AIParseError as exc:
            self.circuit_breaker.record_failure()
            raise exc

        lat_ms = (time.time() - t0) * 1000
        self.circuit_breaker.record_success(lat_ms)
        telemetry = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "estimated_cost": est_cost,
            "latency_ms": lat_ms,
            "prompt_version": prompt_ver,
        }
        return model_instance, telemetry


_global_provider: AIReasoningProvider | None = None


def get_ai_provider() -> AIReasoningProvider:
    """Returns the singleton configured AI provider."""
    global _global_provider
    if _global_provider is None:
        s = get_settings()
        if s.ai_provider.lower() == "openai_compatible" and s.ai_api_key:
            _global_provider = OpenAICompatibleReasoningProvider()
        else:
            _global_provider = MockAIReasoningProvider()
    return _global_provider


def set_ai_provider(provider: AIReasoningProvider | None) -> None:
    """Allows testing or runtime swapping of AI providers."""
    global _global_provider
    _global_provider = provider
