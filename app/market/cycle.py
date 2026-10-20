"""End-to-end market cycle orchestration: agents -> clearing -> physical delivery -> verification
-> balancing -> settlement. Each stage is a separate, auditable service call."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.service import run_agents
from app.balancing.service import cover_shortfalls
from app.market.service import expire_orders, run_clearing
from app.settlement.service import settle_all_verified, verify_deliveries
from app.simulation import engine
from app.simulation.state import get_config


async def advance_to(session: AsyncSession, microgrid_id: uuid.UUID, target: datetime, actor: str = "simulator") -> int:
    n = 0
    while (await get_config(session, microgrid_id)).sim_time < target:
        await engine.step(session, microgrid_id, actor)
        n += 1
    return n


async def run_market_cycle(session: AsyncSession, microgrid_id: uuid.UUID, ws: datetime, we: datetime,
                           actor: str = "market_cycle", advance: bool = True, settle: bool = True,
                           run_agent_orders: bool = True) -> dict:
    out: dict = {"window": [ws, we]}
    if run_agent_orders:
        a = await run_agents(session, microgrid_id, ws, we, actor)
        out["orders"] = len(a["orders"])
        out["decisions"] = a["decisions"]
    clearing = await run_clearing(session, microgrid_id, ws, we, actor)
    out["clearing"] = clearing
    if advance:
        out["steps"] = await advance_to(session, microgrid_id, we, actor)
    now = (await get_config(session, microgrid_id)).sim_time
    v = await verify_deliveries(session, microgrid_id, now)
    out["verification"] = v
    out["balancing"] = await cover_shortfalls(session, microgrid_id, v["shortfalls"], now) if v["shortfalls"] else None
    out["settlements"] = await settle_all_verified(session, microgrid_id) if settle else []
    await expire_orders(session, microgrid_id, now)
    return out
