"""Locational Marginal Pricing (LMP) and Feeder Congestion Economics (Phase 2).

LMP decomposes electrical energy value at every bus/node:
    LMP_i = Energy_ref + Congestion_component_i + Loss_component_i

When feeder A -> B saturates at capacity, nodal prices diverge. Downstream deficit nodes
experience price spikes reflecting scarcity, while upstream pockets experience lower
or zero marginal prices.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Tuple

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import OperatingMode
from app.core.timeutil import utcnow
from app.models.assets import EnergyAsset
from app.models.core import GridLine, GridNode, Microgrid
from app.models.ops import MarketClearing, OptimizationRun
from app.simulation.state import get_config


async def calculate_nodal_lmps(
    session: AsyncSession,
    microgrid_id: uuid.UUID,
    timestamp: datetime | None = None,
) -> dict:
    """Calculates nodal LMPs and line congestion rents for the microgrid."""
    mg = await session.get(Microgrid, microgrid_id)
    if not mg:
        raise ValueError("Microgrid not found")

    cfg = await get_config(session, microgrid_id)
    now = timestamp or cfg.sim_time

    # Retrieve all nodes and lines for microgrid
    nodes = list((await session.execute(
        select(GridNode).where(GridNode.microgrid_id == microgrid_id).order_by(GridNode.name)
    )).scalars())
    lines = list((await session.execute(
        select(GridLine).where(GridLine.microgrid_id == microgrid_id, GridLine.active.is_(True))
    )).scalars())

    # Get latest market clearing price as system reference energy price
    latest_clearing = (await session.execute(
        select(MarketClearing)
        .where(MarketClearing.microgrid_id == microgrid_id)
        .order_by(desc(MarketClearing.created_at))
        .limit(1)
    )).scalar_one_or_none()

    ref_price = 0.085  # default base price $/kWh
    if latest_clearing and latest_clearing.clearing_price:
        ref_price = float(latest_clearing.clearing_price)

    # Check latest optimization run to inspect dual shadow prices or line flows
    latest_opt = (await session.execute(
        select(OptimizationRun)
        .where(OptimizationRun.microgrid_id == microgrid_id)
        .order_by(desc(OptimizationRun.created_at))
        .limit(1)
    )).scalar_one_or_none()

    opt_lmps = {}
    if latest_opt and latest_opt.result and "nodal_lmp" in latest_opt.result:
        for node_key, series in latest_opt.result["nodal_lmp"].items():
            if series:
                # Use current step or average of horizon
                opt_lmps[node_key] = series[0]

    # Map node id and names
    node_by_id = {str(n.id): n for n in nodes}
    line_loading_dict = {}
    congestion_rents = []
    total_congestion_rent = 0.0

    # Build nodal price records
    nodal_results = []
    for node in nodes:
        node_id_str = str(node.id)
        # If solver produced shadow price dual, use it; otherwise compute based on connected line saturation
        if node_id_str in opt_lmps and opt_lmps[node_id_str] > 0:
            lmp = opt_lmps[node_id_str]
            congestion_comp = round(lmp - ref_price, 4)
        else:
            # Check lines incident to this node for saturation
            congestion_comp = 0.0
            for ln in lines:
                flow = abs(ln.current_flow_kw or 0.0)
                cap = max(ln.capacity_kw, 1.0)
                utilization = flow / cap
                if utilization >= 0.95:  # Congested feeder
                    if str(ln.to_node_id) == node_id_str:
                        # Downstream receiving node experiences price increase due to feeder congestion
                        congestion_comp += round(ref_price * (utilization - 0.90) * 2.0, 4)
                    elif str(ln.from_node_id) == node_id_str:
                        # Upstream supply node experiences price depression
                        congestion_comp -= round(ref_price * 0.15, 4)

            lmp = round(max(0.01, ref_price + congestion_comp), 4)

        nodal_results.append({
            "node_id": node_id_str,
            "node_name": node.name,
            "lmp_per_kwh": lmp,
            "energy_component": ref_price,
            "congestion_component": congestion_comp,
            "loss_component": 0.0,
        })

    lmp_map = {item["node_id"]: item["lmp_per_kwh"] for item in nodal_results}

    # Line congestion rents
    for ln in lines:
        from_id, to_id = str(ln.from_node_id), str(ln.to_node_id)
        from_name = node_by_id[from_id].name if from_id in node_by_id else from_id
        to_name = node_by_id[to_id].name if to_id in node_by_id else to_id
        line_label = f"{from_name} -> {to_name}"

        lmp_a = lmp_map.get(from_id, ref_price)
        lmp_b = lmp_map.get(to_id, ref_price)
        flow = abs(ln.current_flow_kw or 0.0)
        cap = max(ln.capacity_kw, 1.0)
        loading_pct = round((flow / cap) * 100.0, 2)
        line_loading_dict[line_label] = loading_pct

        # Hourly rent = |LMP_b - LMP_a| * Flow
        rent = round(abs(lmp_b - lmp_a) * flow * 0.25, 4)  # 15-minute interval rent
        total_congestion_rent += rent

        congestion_rents.append({
            "line_id": str(ln.id),
            "from_node": from_name,
            "to_node": to_name,
            "flow_kw": flow,
            "capacity_kw": ln.capacity_kw,
            "loading_percent": loading_pct,
            "congestion_rent": rent,
        })


    return {
        "microgrid_id": microgrid_id,
        "timestamp": now,
        "reference_lmp": ref_price,
        "nodal_lmps": nodal_results,
        "total_congestion_rent": round(total_congestion_rent, 4),
        "lines": congestion_rents,
        "line_loadings": line_loading_dict,
    }
