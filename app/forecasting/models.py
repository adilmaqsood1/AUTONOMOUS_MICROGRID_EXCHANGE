"""Forecasting interfaces and baseline models.

Forecasts are *never* guaranteed values: every point carries bounds and confidence.
Units: generation/demand forecasts are kWh over the interval; price forecasts are currency/kWh.
Bounds are a ~90% central interval under a normal error assumption (z = 1.645).
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

import numpy as np

from app.simulation import signals

Z90 = 1.645


@dataclass(frozen=True)
class ForecastPoint:
    target_time: datetime
    interval_minutes: int
    predicted_value: float
    lower_bound: float
    upper_bound: float
    confidence: float
    horizon_minutes: int
    model_name: str
    generated_at: datetime

    def __post_init__(self):
        assert self.lower_bound <= self.predicted_value + 1e-9 <= self.upper_bound + 2e-9, "bounds must bracket prediction"


@dataclass(frozen=True)
class Observation:
    time: datetime  # interval start
    value: float    # kWh in interval (or price)


def _mk(target, interval, mean, sigma, floor, cap, horizon, name, generated_at, conf_base=0.9):
    mean = float(min(max(mean, floor), cap if cap is not None else mean))
    lo = max(floor, mean - Z90 * sigma)
    hi = mean + Z90 * sigma
    if cap is not None:
        hi = min(hi, cap)
    # confidence falls as relative uncertainty grows
    rel = sigma / (abs(mean) + 1e-6) if mean > 1e-6 else 1.0
    conf = float(min(0.99, max(0.05, conf_base / (1.0 + rel))))
    return ForecastPoint(target, interval, mean, float(lo), float(hi), conf, horizon, name, generated_at)


def _resid_sigma(values: Sequence[float], default: float) -> float:
    if len(values) < 4:
        return default
    return float(max(np.std(np.diff(np.asarray(values, dtype=float))) / math.sqrt(2), 1e-6))


class GenerationForecaster(ABC):
    name: str

    @abstractmethod
    def forecast(self, history: Sequence[Observation], targets: Sequence[datetime],
                 interval_minutes: int, generated_at: datetime, **ctx) -> list[ForecastPoint]: ...


class DemandForecaster(GenerationForecaster):
    """Same call signature; separate type so implementations can diverge."""


class PriceForecaster(GenerationForecaster):
    pass


class PersistenceForecaster(GenerationForecaster):
    """Next value = last observed value; uncertainty grows ~sqrt(horizon)."""
    name = "persistence"

    def forecast(self, history, targets, interval_minutes, generated_at, **ctx):
        vals = [o.value for o in history]
        last = vals[-1] if vals else 0.0
        base_sigma = _resid_sigma(vals, max(abs(last) * 0.25, 0.05))
        cap = ctx.get("cap_kwh")
        out = []
        for t in targets:
            hm = max(int((t - generated_at).total_seconds() // 60), 0)
            sigma = base_sigma * math.sqrt(1 + hm / max(interval_minutes, 1))
            out.append(_mk(t, interval_minutes, last, sigma, 0.0, cap, hm, self.name, generated_at, 0.8))
        return out


class RollingAverageForecaster(GenerationForecaster):
    """Mean of the same time-of-day slot over history (profile average); falls back to the mean."""
    name = "rolling_average"

    def __init__(self, tz_offset_hours: float = 0.0, floor: float = 0.0):
        self.tz = tz_offset_hours
        self.floor = floor

    def forecast(self, history, targets, interval_minutes, generated_at, **ctx):
        slots: dict[int, list[float]] = {}
        for o in history:
            slots.setdefault(int(signals.local_hour(o.time, self.tz) * 60 // interval_minutes), []).append(o.value)
        allv = [o.value for o in history]
        gmean = float(np.mean(allv)) if allv else 0.0
        gsd = float(np.std(allv)) if len(allv) > 1 else max(abs(gmean) * 0.3, 0.05)
        cap = ctx.get("cap_kwh")
        out = []
        for t in targets:
            k = int(signals.local_hour(t, self.tz) * 60 // interval_minutes)
            vs = slots.get(k, [])
            mean = float(np.mean(vs)) if vs else gmean
            sd = float(np.std(vs)) if len(vs) > 1 else gsd
            hm = max(int((t - generated_at).total_seconds() // 60), 0)
            out.append(_mk(t, interval_minutes, mean, max(sd, 1e-6), self.floor, cap, hm, self.name, generated_at, 0.85))
        return out


class WeatherAdjustedSolarForecaster(GenerationForecaster):
    """Clear-sky curve x rated power x (1 - 0.8 * forecast cloud cover).

    The cloud forecast is the *base* (forecastable) cloudiness; injected shocks (e.g.
    CLOUD_COVER scenarios) are not known to the forecaster, so they appear as forecast error.
    Cloud-forecast error is modelled as growing with horizon.
    """
    name = "weather_adjusted_solar"

    def __init__(self, seed: int, tz_offset_hours: float = 0.0):
        self.seed = seed
        self.tz = tz_offset_hours

    def forecast(self, history, targets, interval_minutes, generated_at, rated_kw: float = 0.0,
                 cloud_forecast=None, **ctx):
        hrs = interval_minutes / 60.0
        out = []
        for t in targets:
            mid = t + timedelta(minutes=interval_minutes / 2)
            cc = cloud_forecast(mid) if cloud_forecast else signals.base_cloud_cover(self.seed, mid)
            cf = signals.solar_capacity_factor(self.seed, mid, self.tz, cloud_cover=cc)
            mean = rated_kw * cf * hrs
            hm = max(int((t - generated_at).total_seconds() // 60), 0)
            cloud_sigma = min(0.35, 0.10 + 0.0008 * hm)  # cloud-cover forecast std (fraction)
            sigma = rated_kw * signals.clear_sky_factor(mid, self.tz) * 0.8 * cloud_sigma * hrs
            cap = rated_kw * hrs
            out.append(_mk(t, interval_minutes, mean, max(sigma, 1e-6) if mean > 0 else 1e-6, 0.0, cap, hm,
                           self.name, generated_at, 0.92))
        return out


class WindForecaster(GenerationForecaster):
    """Persistence-blended climatology for wind (wind speed is not forecastable by this baseline)."""
    name = "wind_blend"

    def forecast(self, history, targets, interval_minutes, generated_at, rated_kw: float = 0.0, **ctx):
        hrs = interval_minutes / 60.0
        vals = [o.value for o in history]
        last = vals[-1] if vals else rated_kw * hrs * 0.3
        clim = float(np.mean(vals)) if vals else rated_kw * hrs * 0.3
        sd = float(np.std(vals)) if len(vals) > 3 else rated_kw * hrs * 0.3
        out = []
        for t in targets:
            hm = max(int((t - generated_at).total_seconds() // 60), 0)
            w = math.exp(-hm / 180.0)  # persistence weight decays with a 3 h time-constant
            mean = w * last + (1 - w) * clim
            sigma = max(sd * (0.4 + 0.6 * (1 - w)), 1e-3)
            out.append(_mk(t, interval_minutes, mean, sigma, 0.0, rated_kw * hrs, hm, self.name, generated_at, 0.8))
        return out


class RegressionDemandForecaster(DemandForecaster):
    """OLS on time-of-day harmonics (+ weekday flag). Residual std gives the interval."""
    name = "harmonic_regression"

    def __init__(self, tz_offset_hours: float = 0.0):
        self.tz = tz_offset_hours

    def _x(self, t: datetime) -> list[float]:
        h = signals.local_hour(t, self.tz)
        w = 2 * math.pi * h / 24.0
        return [1.0, math.sin(w), math.cos(w), math.sin(2 * w), math.cos(2 * w),
                math.sin(3 * w), math.cos(3 * w), 1.0 if signals.is_weekday(t, self.tz) else 0.0]

    def forecast(self, history, targets, interval_minutes, generated_at, **ctx):
        cap = ctx.get("cap_kwh")
        if len(history) < 12:
            return RollingAverageForecaster(self.tz).forecast(history, targets, interval_minutes, generated_at, **ctx)
        X = np.array([self._x(o.time) for o in history])
        y = np.array([o.value for o in history])
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ beta
        sd = float(max(np.std(resid, ddof=min(X.shape[1], len(y) - 1)), 1e-6))
        out = []
        for t in targets:
            hm = max(int((t - generated_at).total_seconds() // 60), 0)
            mean = float(np.array(self._x(t)) @ beta)
            out.append(_mk(t, interval_minutes, mean, sd * (1 + hm / 1440.0), 0.0, cap, hm,
                           self.name, generated_at, 0.9))
        return out


class TariffPriceForecaster(PriceForecaster):
    """Utility tariff is known; uncertainty only reflects local-market premium variability."""
    name = "tariff_price"

    def __init__(self, tz_offset_hours: float = 0.0):
        self.tz = tz_offset_hours

    def forecast(self, history, targets, interval_minutes, generated_at, **ctx):
        out = []
        for t in targets:
            p = signals.utility_price(t, self.tz)
            hm = max(int((t - generated_at).total_seconds() // 60), 0)
            sigma = p * (0.05 + 0.1 * min(hm / 720.0, 1.0))
            out.append(_mk(t, interval_minutes, p, sigma, 0.0, None, hm, self.name, generated_at, 0.9))
        return out


def evaluate(predicted: Sequence[ForecastPoint], actuals: Sequence[float]) -> dict:
    """MAE / RMSE / interval coverage for predicted-vs-actual evaluation."""
    p = np.array([x.predicted_value for x in predicted])
    a = np.array(actuals, dtype=float)
    lo = np.array([x.lower_bound for x in predicted])
    hi = np.array([x.upper_bound for x in predicted])
    return {"mae": float(np.mean(np.abs(p - a))), "rmse": float(np.sqrt(np.mean((p - a) ** 2))),
            "coverage": float(np.mean((a >= lo - 1e-9) & (a <= hi + 1e-9)))}
