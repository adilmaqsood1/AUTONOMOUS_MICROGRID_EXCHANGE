"""Forecast-risk adjustment: how much energy may a generator safely offer?

tradable = predicted - uncertainty_reserve - operational_reserve  (floored at 0)

uncertainty_reserve  = factor * (predicted - lower_bound)   # downside half-width of the interval
operational_reserve  = fraction * predicted

Both knobs are configurable (see Settings) via `ReservePolicy`.
"""
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ReservePolicy:
    uncertainty_factor: Decimal = Decimal("1.0")
    operational_fraction: Decimal = Decimal("0.08")


@dataclass(frozen=True)
class TradableCapacity:
    predicted_kwh: Decimal
    uncertainty_reserve_kwh: Decimal
    operational_reserve_kwh: Decimal
    tradable_kwh: Decimal


def tradable_capacity(predicted: float, lower: float, policy: ReservePolicy = ReservePolicy()) -> TradableCapacity:
    p = Decimal(str(round(predicted, 6)))
    lo = Decimal(str(round(lower, 6)))
    unc = max(Decimal("0"), (p - lo)) * policy.uncertainty_factor
    ops = p * policy.operational_fraction
    tradable = max(Decimal("0"), p - unc - ops)
    q = Decimal("0.000001")
    return TradableCapacity(p.quantize(q), unc.quantize(q), ops.quantize(q), tradable.quantize(q))
