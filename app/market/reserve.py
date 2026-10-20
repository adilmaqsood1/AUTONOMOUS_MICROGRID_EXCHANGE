"""Operating Reserve and Ancillary Services Market Clearing Engine (Phase 2).

Procures capacity (kW) with fast response time guarantees (<2s frequency response,
<30s spinning reserve, <300s supplemental) from batteries, EVs, and flexible generators
prior to grid contingencies.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.core.enums import AssetStatus, AssetType, AuditEventType
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.timeutil import ensure_utc, utcnow
from app.models.assets import EnergyAsset
from app.models.core import Microgrid, Participant
from app.models.ops import ReserveCommitment, ReserveOffer
from app.websocket.hub import queue_event

ALLOWED_RESERVE_TYPES = {
    "FREQUENCY_CONTAINMENT": 2.0,     # Max 2s response
    "SPINNING_RESERVE": 30.0,         # Max 30s response
    "NON_SPINNING": 300.0,            # Max 5 min response
    "REPLACEMENT": 1800.0,            # Max 30 min response
}


async def create_reserve_offer(
    session: AsyncSession,
    participant_id: uuid.UUID,
    asset_id: uuid.UUID,
    reserve_type: str,
    capacity_kw: float,
    response_time_seconds: float,
    price_per_kw: Decimal,
    ws: datetime,
    we: datetime,
    actor: str = "participant",
) -> ReserveOffer:
    ws, we = ensure_utc(ws), ensure_utc(we)
    if reserve_type not in ALLOWED_RESERVE_TYPES:
        raise ValidationFailed(f"Invalid reserve type. Must be one of {list(ALLOWED_RESERVE_TYPES.keys())}")
    max_allowed_response = ALLOWED_RESERVE_TYPES[reserve_type]
    if response_time_seconds > max_allowed_response:
        raise ValidationFailed(f"{reserve_type} requires response time <= {max_allowed_response}s, got {response_time_seconds}s")
    if capacity_kw <= 0 or price_per_kw <= 0:
        raise ValidationFailed("Capacity and price must be strictly positive")
    if we <= ws:
        raise ValidationFailed("Window end must be after window start")

    asset = await session.get(EnergyAsset, asset_id)
    if not asset:
        raise NotFound("Asset not found")
    if asset.participant_id != participant_id:
        raise Forbidden("Asset does not belong to participant")
    if asset.status != AssetStatus.ONLINE.value:
        raise ValidationFailed("Asset must be online to offer operating reserve")

    # Validate physical capacity
    if asset.battery:
        if capacity_kw > asset.battery.maximum_discharge_kw:
            raise ValidationFailed(f"Offer capacity {capacity_kw} kW exceeds battery max discharge {asset.battery.maximum_discharge_kw} kW")
    elif asset.ev:
        if not asset.ev.v2g_enabled or not asset.ev.connected:
            raise ValidationFailed("EV must be connected and V2G-enabled to offer operating reserve")
        if capacity_kw > asset.ev.maximum_discharge_rate_kw:
            raise ValidationFailed(f"Offer capacity {capacity_kw} kW exceeds EV discharge rate {asset.ev.maximum_discharge_rate_kw} kW")
    elif asset.generator:
        if capacity_kw > asset.generator.maximum_generation_kw:
            raise ValidationFailed(f"Offer capacity exceeds generator max rating {asset.generator.maximum_generation_kw} kW")

    offer = ReserveOffer(
        participant_id=participant_id,
        asset_id=asset_id,
        microgrid_id=asset.microgrid_id,
        reserve_type=reserve_type,
        capacity_kw=capacity_kw,
        response_time_seconds=response_time_seconds,
        price_per_kw=price_per_kw,
        window_start=ws,
        window_end=we,
        status="OPEN",
        created_at=utcnow(),
    )
    session.add(offer)
    await session.flush()

    await audit.record(
        session,
        AuditEventType.ORDER_CREATED,
        "ReserveOffer",
        offer.id,
        actor,
        {
            "reserve_type": reserve_type,
            "capacity_kw": capacity_kw,
            "price_per_kw": str(price_per_kw),
            "response_time_seconds": response_time_seconds,
        },
        asset.microgrid_id,
    )
    return offer


async def clear_reserve_market(
    session: AsyncSession,
    microgrid_id: uuid.UUID,
    required_capacity_kw: float,
    max_response_time_seconds: float,
    ws: datetime,
    we: datetime,
    actor: str = "reserve_engine",
) -> dict:
    """Merit-order clearing for operating reserve capacity."""
    ws, we = ensure_utc(ws), ensure_utc(we)
    mg = await session.get(Microgrid, microgrid_id)
    if not mg:
        raise NotFound("Microgrid not found")

    q = (
        select(ReserveOffer)
        .where(
            ReserveOffer.microgrid_id == microgrid_id,
            ReserveOffer.status == "OPEN",
            ReserveOffer.window_start <= ws,
            ReserveOffer.window_end >= we,
            ReserveOffer.response_time_seconds <= max_response_time_seconds,
        )
        .order_by(ReserveOffer.price_per_kw.asc(), ReserveOffer.response_time_seconds.asc(), ReserveOffer.created_at.asc())
    )
    offers = list((await session.execute(q)).scalars())

    procured_kw = 0.0
    clearing_price_per_kw = Decimal("0")
    commitments: List[ReserveCommitment] = []

    for offer in offers:
        if procured_kw >= required_capacity_kw:
            break
        needed = required_capacity_kw - procured_kw
        allocated = min(offer.capacity_kw, needed)
        procured_kw += allocated
        clearing_price_per_kw = max(clearing_price_per_kw, offer.price_per_kw)

        total_pay = Decimal(str(round(allocated, 4))) * clearing_price_per_kw
        comm = ReserveCommitment(
            offer_id=offer.id,
            microgrid_id=microgrid_id,
            asset_id=offer.asset_id,
            allocated_kw=allocated,
            clearing_price_per_kw=clearing_price_per_kw,
            total_payment=total_pay,
            response_time_seconds=offer.response_time_seconds,
            window_start=ws,
            window_end=we,
            status="COMMITTED",
            created_at=utcnow(),
        )
        session.add(comm)
        commitments.append(comm)

        if allocated >= offer.capacity_kw - 1e-6:
            offer.status = "COMMITTED"

    await session.flush()

    total_cost = sum((c.total_payment for c in commitments), Decimal("0"))
    queue_event(
        session,
        "RESERVE_PROCURED",
        microgrid_id,
        {
            "required_kw": required_capacity_kw,
            "procured_kw": procured_kw,
            "clearing_price": str(clearing_price_per_kw),
            "commitments_count": len(commitments),
        },
    )

    return {
        "microgrid_id": microgrid_id,
        "required_kw": required_capacity_kw,
        "procured_kw": procured_kw,
        "clearing_price_per_kw": clearing_price_per_kw,
        "total_cost": total_cost,
        "commitments": commitments,
    }


async def list_reserve_offers(
    session: AsyncSession,
    microgrid_id: Optional[uuid.UUID] = None,
    participant_id: Optional[uuid.UUID] = None,
    status: Optional[str] = None,
    limit: int = 100,
) -> List[ReserveOffer]:
    q = select(ReserveOffer)
    if microgrid_id:
        q = q.where(ReserveOffer.microgrid_id == microgrid_id)
    if participant_id:
        q = q.where(ReserveOffer.participant_id == participant_id)
    if status:
        q = q.where(ReserveOffer.status == status)
    return list((await session.execute(q.order_by(ReserveOffer.created_at.desc()).limit(limit))).scalars())


async def list_reserve_commitments(
    session: AsyncSession,
    microgrid_id: Optional[uuid.UUID] = None,
    asset_id: Optional[uuid.UUID] = None,
    limit: int = 100,
) -> List[ReserveCommitment]:
    q = select(ReserveCommitment)
    if microgrid_id:
        q = q.where(ReserveCommitment.microgrid_id == microgrid_id)
    if asset_id:
        q = q.where(ReserveCommitment.asset_id == asset_id)
    return list((await session.execute(q.order_by(ReserveCommitment.created_at.desc()).limit(limit))).scalars())
