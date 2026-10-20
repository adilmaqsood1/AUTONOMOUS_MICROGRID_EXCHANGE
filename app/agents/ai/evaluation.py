"""Comparative evaluation framework: DETERMINISTIC vs SHADOW_AI vs AI_ASSISTED."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.ai import AgentDecision, AgentOutcome


@dataclass
class ComparisonMetrics:
    mode_under_test: str
    total_decisions: int
    agreements_with_baseline: int
    disagreements_with_baseline: int
    agreement_rate: float
    total_realized_reward: float
    estimated_baseline_reward: float
    ai_incremental_value: float
    guard_rejections: int
    guard_rejection_rate: float
    fallbacks: int
    fallback_rate: float
    average_ai_latency_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode_under_test": self.mode_under_test,
            "total_decisions": self.total_decisions,
            "agreements_with_baseline": self.agreements_with_baseline,
            "disagreements_with_baseline": self.disagreements_with_baseline,
            "agreement_rate": round(self.agreement_rate, 4),
            "total_realized_reward": round(self.total_realized_reward, 4),
            "estimated_baseline_reward": round(self.estimated_baseline_reward, 4),
            "ai_incremental_value": round(self.ai_incremental_value, 4),
            "guard_rejections": self.guard_rejections,
            "guard_rejection_rate": round(self.guard_rejection_rate, 4),
            "fallbacks": self.fallbacks,
            "fallback_rate": round(self.fallback_rate, 4),
            "average_ai_latency_ms": round(self.average_ai_latency_ms, 2),
        }


def evaluate_decisions_and_outcomes(
    decisions: list[AgentDecision],
    outcomes: list[AgentOutcome],
    mode: str = "AI_ASSISTED",
) -> ComparisonMetrics:
    """Evaluates agent performance against baseline decisions."""
    total = len(decisions)
    if total == 0:
        return ComparisonMetrics(
            mode_under_test=mode, total_decisions=0, agreements_with_baseline=0,
            disagreements_with_baseline=0, agreement_rate=1.0, total_realized_reward=0.0,
            estimated_baseline_reward=0.0, ai_incremental_value=0.0, guard_rejections=0,
            guard_rejection_rate=0.0, fallbacks=0, fallback_rate=0.0, average_ai_latency_ms=0.0,
        )

    rejections = sum(1 for d in decisions if d.guard_status == "REJECTED")
    fallbacks = sum(1 for d in decisions if d.fallback_used)
    latencies = [d.latency_ms for d in decisions if d.latency_ms > 0]
    avg_latency = (sum(latencies) / len(latencies)) if latencies else 0.0

    # Rewards
    realized_reward = sum(o.net_reward for o in outcomes)
    baseline_reward = sum(o.counterfactual_reward or o.net_reward for o in outcomes)
    incremental_value = realized_reward - baseline_reward

    # Action agreement
    agreements = sum(1 for d in decisions if d.proposal.get("action") == d.final_action)
    disagreements = total - agreements

    return ComparisonMetrics(
        mode_under_test=mode,
        total_decisions=total,
        agreements_with_baseline=agreements,
        disagreements_with_baseline=disagreements,
        agreement_rate=agreements / total,
        total_realized_reward=realized_reward,
        estimated_baseline_reward=baseline_reward,
        ai_incremental_value=incremental_value,
        guard_rejections=rejections,
        guard_rejection_rate=rejections / total,
        fallbacks=fallbacks,
        fallback_rate=fallbacks / total,
        average_ai_latency_ms=avg_latency,
    )
