"""Order placement, order management, and order book depth."""
import uuid
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, SessionDep, assert_can_act_for, current_principal, reader, trader
from app.market import service as market_service
from app.models.assets import EnergyAsset
from app.models.ops import EnergyOrder
from app.schemas.schemas import OrderBookResponse, OrderCreate, OrderResponse

router = APIRouter(tags=["Orders & Order Book"])


@router.post("/orders", response_model=OrderResponse)
@router.post("/market/orders", response_model=OrderResponse)
async def create_energy_order(req: OrderCreate,
                              principal: Principal = Depends(current_principal),
                              session: AsyncSession = SessionDep):
    asset = await session.get(EnergyAsset, req.asset_id)
    if not asset:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    assert_can_act_for(principal, asset.participant_id)
    order = await market_service.create_order(
        session, asset.participant_id, req.asset_id, req.side, req.quantity_kwh,
        req.limit_price, req.delivery_window_start, req.delivery_window_end,
        renewable_only=req.renewable_only, actor=principal.actor
    )
    await session.commit()
    await session.refresh(order)
    return order


@router.get("/orders", response_model=List[OrderResponse], dependencies=[Depends(reader)])
async def list_orders(microgrid_id: Optional[uuid.UUID] = None,
                      participant_id: Optional[uuid.UUID] = None,
                      status_filter: Optional[str] = None,
                      limit: int = 100,
                      session: AsyncSession = SessionDep):
    q = select(EnergyOrder)
    if microgrid_id:
        q = q.where(EnergyOrder.microgrid_id == microgrid_id)
    if participant_id:
        q = q.where(EnergyOrder.participant_id == participant_id)
    if status_filter:
        q = q.where(EnergyOrder.status == status_filter)
    q = q.order_by(EnergyOrder.sequence.desc()).limit(min(limit, 500))
    return (await session.execute(q)).scalars().all()


@router.get("/orders/{order_id}", response_model=OrderResponse, dependencies=[Depends(reader)])
async def get_order(order_id: uuid.UUID, session: AsyncSession = SessionDep):
    o = await session.get(EnergyOrder, order_id)
    if not o:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")
    return o


@router.delete("/orders/{order_id}", response_model=OrderResponse)
async def cancel_energy_order(order_id: uuid.UUID,
                              principal: Principal = Depends(current_principal),
                              session: AsyncSession = SessionDep):
    pid = principal.participant_id if not principal.is_privileged else None
    order = await market_service.cancel_order(session, order_id, pid, principal.actor)
    await session.commit()
    await session.refresh(order)
    return order


@router.get("/orderbook", response_model=OrderBookResponse, dependencies=[Depends(reader)])
@router.get("/market/order-book", response_model=OrderBookResponse, dependencies=[Depends(reader)])
async def get_order_book(microgrid_id: uuid.UUID,
                         window_start: Optional[datetime] = None,
                         window_end: Optional[datetime] = None,
                         session: AsyncSession = SessionDep):
    book = await market_service.order_book(session, microgrid_id, window_start, window_end)
    return book
