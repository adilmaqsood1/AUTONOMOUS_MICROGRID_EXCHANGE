"""DB <-> grid model bridge: build the feasibility model for a delivery window."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import CommitmentStatus
from app.grid.network import GridFeasibility, LineState, NetworkFlowFeasibility
from app.models.assets import EnergyAsset
from app.models.core import GridLine
from app.models.ops import EnergyCommitment

OCCUPYING = (CommitmentStatus.PENDING.value, CommitmentStatus.ACTIVE.value)


async def asset_nodes(session: AsyncSession, asset_ids: list[uuid.UUID]) -> dict[str, str]:
    if not asset_ids:
        return {}
    rows = (await session.execute(select(EnergyAsset.id, EnergyAsset.grid_node_id).where(
        EnergyAsset.id.in_(asset_ids)))).all()
    return {str(i): str(n) for i, n in rows}


async def load_network(session: AsyncSession, microgrid_id: uuid.UUID, window_start: datetime | None = None,
                       window_end: datetime | None = None, exclude_commitment: uuid.UUID | None = None
                       ) -> NetworkFlowFeasibility:
    """Lines (with background flow) + capacity already occupied by commitments overlapping the window."""
    lines = (await session.execute(select(GridLine).where(GridLine.microgrid_id == microgrid_id))).scalars().all()
    net = NetworkFlowFeasibility([LineState(str(l.id), str(l.from_node_id), str(l.to_node_id), l.capacity_kw,
                                            l.current_flow_kw, l.active) for l in lines])
    if window_start and window_end:
        q = select(EnergyCommitment).where(EnergyCommitment.microgrid_id == microgrid_id,
                                           EnergyCommitment.status.in_(OCCUPYING),
                                           EnergyCommitment.delivery_start < window_end,
                                           EnergyCommitment.delivery_end > window_start)
        commits = (await session.execute(q)).scalars().all()
        ids = {c.seller_asset_id for c in commits} | {c.buyer_asset_id for c in commits}
        nodes = await asset_nodes(session, list(ids))
        for c in sorted(commits, key=lambda c: (c.created_at, str(c.id))):
            if exclude_commitment and c.id == exclude_commitment:
                continue
            hrs = (c.delivery_end - c.delivery_start).total_seconds() / 3600.0
            net.request_transfer(nodes[str(c.seller_asset_id)], nodes[str(c.buyer_asset_id)],
                                 float(c.quantity_kwh) / hrs, commit=True)
    return net


def available_kwh(net: GridFeasibility, src: str, dst: str, hours: float) -> float:
    return net.available_transfer_kw(src, dst) * hours
