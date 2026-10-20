"""Simulation state persistence helpers (clock, seed, config)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import ensure_utc
from app.models.ops import SimulationState

DEFAULT_START = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
DEFAULT_CONFIG = {"tz_offset_hours": 5.0, "weather_cloud_bias": 0.0, "market_window_minutes": 60,
                  "base_utility_price_multiplier": 1.0}


@dataclass
class SimConfig:
    seed: int
    tz_offset_hours: float
    step_minutes: int
    sim_time: datetime
    market_window_minutes: int
    speed_multiplier: float
    running: bool


async def get_state(session: AsyncSession, microgrid_id: uuid.UUID, create: bool = True) -> SimulationState | None:
    st = (await session.execute(select(SimulationState).where(
        SimulationState.microgrid_id == microgrid_id))).scalar_one_or_none()
    if st is None and create:
        st = SimulationState(microgrid_id=microgrid_id, running=False, seed=42, sim_time=DEFAULT_START,
                             step_minutes=15, speed_multiplier=1.0, config=dict(DEFAULT_CONFIG), steps_executed=0)
        session.add(st)
        await session.flush()
    return st


def to_config(st: SimulationState) -> SimConfig:
    cfg = {**DEFAULT_CONFIG, **(st.config or {})}
    return SimConfig(st.seed, float(cfg["tz_offset_hours"]), st.step_minutes, ensure_utc(st.sim_time),
                     int(cfg["market_window_minutes"]), st.speed_multiplier, st.running)


async def get_config(session: AsyncSession, microgrid_id: uuid.UUID) -> SimConfig:
    return to_config(await get_state(session, microgrid_id))
