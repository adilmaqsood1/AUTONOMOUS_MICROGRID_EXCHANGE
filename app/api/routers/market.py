"""Market clearing, executed trades, and physical delivery commitments."""
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, SessionDep, current_principal, operator, reader
from app.market import service as market_service
from app.models.ops import EnergyCommitment, MarketClearing, Trade
from app.schemas.schemas import (
    ClearingRunRequest, CommitmentResponse, MarketClearingResponse, TradeResponse
)

router = APIRouter(tags=["Market Clearing & Commitments"])


@router.post("/market/clear", response_model=MarketClearingResponse, dependencies=[Depends(operator)])
async def trigger_market_clearing(req: ClearingRunRequest,
                                  principal: Principal = Depends(current_principal),
                                  session: AsyncSession = SessionDep):
    clearing = await market_service.run_clearing(
        session, req.microgrid_id, req.window_start, req.window_end, actor=principal.actor
    )
    await session.commit()
    await session.refresh(clearing)
    return clearing


@router.get("/market/clearings", response_model=List[MarketClearingResponse], dependencies=[Depends(reader)])
async def list_clearings(microgrid_id: Optional[uuid.UUID] = None,
                         limit: int = 50,
                         session: AsyncSession = SessionDep):
    q = select(MarketClearing)
    if microgrid_id:
        q = q.where(MarketClearing.microgrid_id == microgrid_id)
    return (await session.execute(q.order_by(MarketClearing.created_at.desc()).limit(min(limit, 200)))).scalars().all()


@router.get("/market/clearings/{clearing_id}", response_model=MarketClearingResponse, dependencies=[Depends(reader)])
async def get_clearing(clearing_id: uuid.UUID, session: AsyncSession = SessionDep):
    clr = await session.get(MarketClearing, clearing_id)
    if not clr:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "market clearing run not found")
    return clr


# --- Trades
@router.get("/trades", response_model=List[TradeResponse], dependencies=[Depends(reader)])
@router.get("/market/trades", response_model=List[TradeResponse], dependencies=[Depends(reader)])
async def list_trades(microgrid_id: Optional[uuid.UUID] = None,
                      clearing_id: Optional[uuid.UUID] = None,
                      limit: int = 100,
                      session: AsyncSession = SessionDep):
    q = select(Trade)
    if microgrid_id:
        q = q.where(Trade.microgrid_id == microgrid_id)
    if clearing_id:
        q = q.where(Trade.clearing_id == clearing_id)
    return (await session.execute(q.order_by(Trade.created_at.desc()).limit(min(limit, 500)))).scalars().all()


# --- Commitments
@router.get("/commitments", response_model=List[CommitmentResponse], dependencies=[Depends(reader)])
async def list_commitments(microgrid_id: Optional[uuid.UUID] = None,
                           participant_id: Optional[uuid.UUID] = None,
                           status_filter: Optional[str] = None,
                           limit: int = 100,
                           session: AsyncSession = SessionDep):
    q = select(EnergyCommitment)
    if microgrid_id:
        q = q.where(EnergyCommitment.microgrid_id == microgrid_id)
    if participant_id:
        q = q.where((EnergyCommitment.buyer_participant_id == participant_id) |
                    (EnergyCommitment.seller_participant_id == participant_id))
    if status_filter:
        q = q.where(EnergyCommitment.status == status_filter)
    return (await session.execute(q.order_by(EnergyCommitment.delivery_start.desc()).limit(min(limit, 500)))).scalars().all()


@router.get("/commitments/{commitment_id}", response_model=CommitmentResponse, dependencies=[Depends(reader)])
async def get_commitment(commitment_id: uuid.UUID, session: AsyncSession = SessionDep):
    c = await session.get(EnergyCommitment, commitment_id)
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "commitment not found")
    return c


# --- Phase 2: Operating Reserve Market
from app.market import reserve as reserve_service
from app.market import lmp as lmp_service
from app.schemas.schemas import (
    LMPResponse, ReserveCommitmentResponse, ReserveMarketClearResponse,
    ReserveOfferCreate, ReserveOfferResponse, ReserveProcurementRequest
)


@router.post("/market/reserve/offers", response_model=ReserveOfferResponse)
async def post_reserve_offer(req: ReserveOfferCreate,
                             principal: Principal = Depends(current_principal),
                             session: AsyncSession = SessionDep):
    offer = await reserve_service.create_reserve_offer(
        session,
        participant_id=principal.participant_id or req.asset_id,
        asset_id=req.asset_id,
        reserve_type=req.reserve_type,
        capacity_kw=req.capacity_kw,
        response_time_seconds=req.response_time_seconds,
        price_per_kw=req.price_per_kw,
        ws=req.window_start,
        we=req.window_end,
        actor=principal.actor,
    )
    await session.commit()
    await session.refresh(offer)
    return offer


@router.get("/market/reserve/offers", response_model=List[ReserveOfferResponse], dependencies=[Depends(reader)])
async def get_reserve_offers(microgrid_id: Optional[uuid.UUID] = None,
                             participant_id: Optional[uuid.UUID] = None,
                             status_filter: Optional[str] = None,
                             limit: int = 100,
                             session: AsyncSession = SessionDep):
    return await reserve_service.list_reserve_offers(
        session, microgrid_id=microgrid_id, participant_id=participant_id,
        status=status_filter, limit=limit
    )


@router.post("/market/reserve/procure", response_model=ReserveMarketClearResponse, dependencies=[Depends(operator)])
async def procure_operating_reserves(req: ReserveProcurementRequest,
                                     principal: Principal = Depends(current_principal),
                                     session: AsyncSession = SessionDep):
    res = await reserve_service.clear_reserve_market(
        session,
        microgrid_id=req.microgrid_id,
        required_capacity_kw=req.required_capacity_kw,
        max_response_time_seconds=req.max_response_time_seconds,
        ws=req.window_start,
        we=req.window_end,
        actor=principal.actor,
    )
    await session.commit()
    return res


@router.get("/market/reserve/commitments", response_model=List[ReserveCommitmentResponse], dependencies=[Depends(reader)])
async def get_reserve_commitments(microgrid_id: Optional[uuid.UUID] = None,
                                  asset_id: Optional[uuid.UUID] = None,
                                  limit: int = 100,
                                  session: AsyncSession = SessionDep):
    return await reserve_service.list_reserve_commitments(
        session, microgrid_id=microgrid_id, asset_id=asset_id, limit=limit
    )


# --- Phase 2: Locational Marginal Pricing (LMP)
@router.get("/market/lmp", response_model=LMPResponse, dependencies=[Depends(reader)])
async def get_market_lmp(microgrid_id: uuid.UUID, session: AsyncSession = SessionDep):
    return await lmp_service.calculate_nodal_lmps(session, microgrid_id)

