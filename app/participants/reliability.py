"""Reliability / reputation scoring. Reputation tightens risk limits; it never relaxes physical ones."""
from __future__ import annotations

from dataclasses import dataclass

PRIOR_SCORE = 0.8
PRIOR_WEIGHT = 5.0  # pseudo-observations at the prior score (Bayesian shrinkage for new participants)


@dataclass(frozen=True)
class ReliabilityMetrics:
    commitments: int
    successful: int
    partial: int
    failed: int
    average_delivery_ratio: float
    imbalance_kwh: float
    score: float


def reliability_score(n_settled: int, delivery_ratio_sum: float) -> float:
    """Posterior mean of the delivery ratio under a Beta-like prior; in [0,1]."""
    return max(0.0, min(1.0, (PRIOR_WEIGHT * PRIOR_SCORE + delivery_ratio_sum) / (PRIOR_WEIGHT + n_settled)))


def metrics(commitments: int, successful: int, partial: int, failed: int, ratio_sum: float,
            imbalance_kwh: float) -> ReliabilityMetrics:
    n = successful + partial + failed
    avg = ratio_sum / n if n else 0.0
    return ReliabilityMetrics(commitments, successful, partial, failed, avg, imbalance_kwh,
                              reliability_score(n, ratio_sum))
