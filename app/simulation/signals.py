"""Stateless, seeded environmental/demand signal models.

Every function is a pure function of (seed, timestamp, parameters) so simulations are
reproducible and independent of stepping order. Units: capacity factors in [0,1],
demand multipliers dimensionless, prices in currency/kWh.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np

SOLAR_SUNRISE_H = 6.0
SOLAR_SUNSET_H = 18.0


def local_hour(ts: datetime, tz_offset_hours: float = 0.0) -> float:
    t = ts.astimezone(timezone.utc) + timedelta(hours=tz_offset_hours)
    return t.hour + t.minute / 60.0 + t.second / 3600.0


def is_weekday(ts: datetime, tz_offset_hours: float = 0.0) -> bool:
    return (ts.astimezone(timezone.utc) + timedelta(hours=tz_offset_hours)).weekday() < 5


def _knot(seed: int, channel: int, k: int) -> float:
    return float(np.random.default_rng([abs(seed), channel, k + 10_000_000]).random())


def smooth_noise(seed: int, channel: int, ts: datetime, knot_hours: float = 1.0) -> float:
    """Value in [0,1], linearly interpolated between seeded knots every `knot_hours`."""
    x = ts.timestamp() / 3600.0 / knot_hours
    k = math.floor(x)
    f = x - k
    return _knot(seed, channel, k) * (1 - f) + _knot(seed, channel, k + 1) * f


# ------------------------------------------------------------------ weather
def clear_sky_factor(ts: datetime, tz_offset_hours: float = 0.0) -> float:
    h = local_hour(ts, tz_offset_hours)
    if h <= SOLAR_SUNRISE_H or h >= SOLAR_SUNSET_H:
        return 0.0
    return math.sin(math.pi * (h - SOLAR_SUNRISE_H) / (SOLAR_SUNSET_H - SOLAR_SUNRISE_H)) ** 1.2


def base_cloud_cover(seed: int, ts: datetime) -> float:
    """Underlying (forecastable) cloudiness 0..0.6."""
    return 0.6 * smooth_noise(seed, 1, ts, knot_hours=3.0) ** 1.5


def solar_capacity_factor(seed: int, ts: datetime, tz_offset_hours: float = 0.0,
                          cloud_cover: float | None = None) -> float:
    cc = base_cloud_cover(seed, ts) if cloud_cover is None else cloud_cover
    cc = min(max(cc, 0.0), 1.0)
    return clear_sky_factor(ts, tz_offset_hours) * (1.0 - 0.8 * cc)


def base_wind_speed(seed: int, ts: datetime) -> float:
    """m/s, 2..16, fluctuating on ~2h scale plus faster gusts."""
    slow = smooth_noise(seed, 2, ts, knot_hours=2.0)
    gust = smooth_noise(seed, 3, ts, knot_hours=0.25)
    return 2.0 + 12.0 * slow + 2.0 * gust


def wind_capacity_factor(speed_ms: float) -> float:
    cut_in, rated, cut_out = 3.0, 12.0, 25.0
    if speed_ms < cut_in or speed_ms > cut_out:
        return 0.0
    if speed_ms >= rated:
        return 1.0
    return ((speed_ms - cut_in) / (rated - cut_in)) ** 3


def hydro_capacity_factor(seed: int, ts: datetime) -> float:
    return 0.78 + 0.12 * (smooth_noise(seed, 4, ts, knot_hours=12.0) - 0.5) * 2


def base_temperature(seed: int, ts: datetime, tz_offset_hours: float = 0.0) -> float:
    """Ambient temperature in Celsius with diurnal day-night cycle."""
    h = local_hour(ts, tz_offset_hours)
    diurnal = 18.0 + 8.0 * math.sin(math.pi * (h - 9.0) / 12.0)
    noise = (smooth_noise(seed, 50, ts, knot_hours=4.0) - 0.5) * 4.0
    return diurnal + noise


# ------------------------------------------------------------------- demand
def _gauss(h: float, mu: float, sigma: float) -> float:
    return math.exp(-0.5 * ((h - mu) / sigma) ** 2)


def household_demand_multiplier(seed: int, ts: datetime, tz_offset_hours: float = 0.0,
                                unit: int = 0) -> float:
    h = local_hour(ts, tz_offset_hours)
    shape = 0.35 + 0.65 * _gauss(h, 7.5, 1.4) + 1.0 * _gauss(h, 19.5, 2.0) + 0.2 * _gauss(h, 13, 2.5)
    noise = 1.0 + 0.12 * (smooth_noise(seed, 100 + unit, ts, knot_hours=0.5) - 0.5) * 2
    return shape * noise


def commercial_demand_multiplier(seed: int, ts: datetime, tz_offset_hours: float = 0.0,
                                 unit: int = 0) -> float:
    h = local_hour(ts, tz_offset_hours)
    wd = is_weekday(ts, tz_offset_hours)
    ramp = 1.0 / (1.0 + math.exp(-(h - 8.0) * 2.0)) - 1.0 / (1.0 + math.exp(-(h - 18.0) * 2.0))
    level = 0.25 + (0.75 if wd else 0.25) * ramp
    noise = 1.0 + 0.06 * (smooth_noise(seed, 500 + unit, ts, knot_hours=1.0) - 0.5) * 2
    return level * noise


def constant_demand_multiplier(seed: int, ts: datetime, unit: int = 0) -> float:
    """Critical loads (hospital): flat with small variation."""
    return 0.9 + 0.1 * smooth_noise(seed, 700 + unit, ts, knot_hours=2.0)


# -------------------------------------------------------------------- price
def utility_price(ts: datetime, tz_offset_hours: float = 0.0, spike_multiplier: float = 1.0) -> float:
    """Time-of-use utility tariff (currency/kWh)."""
    h = local_hour(ts, tz_offset_hours)
    if 17.0 <= h < 22.0:
        p = 0.22
    elif 7.0 <= h < 17.0:
        p = 0.14
    else:
        p = 0.08
    return p * spike_multiplier


def export_price(ts: datetime, tz_offset_hours: float = 0.0, spike_multiplier: float = 1.0) -> float:
    return 0.5 * utility_price(ts, tz_offset_hours, spike_multiplier)
