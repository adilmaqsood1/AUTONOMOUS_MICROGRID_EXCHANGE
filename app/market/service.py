"""Order intake, order book and grid/risk-constrained clearing cycle."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.core.cache import cache
from app.core.enums import AssetType, AuditEventType, OrderSide, OrderStatus
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.timeutil import ensure_utc, utcnow
from app.grid.service import load_network
from app.market.clearing import BookOrder, clear_market
from app.models.assets import EnergyAsset
from app.models.core import Microgrid, Participant
from app.models.ops import EnergyOrder, MarketClearing, RiskDecision, Trade
from app.risk.service import MarketRiskContext, battery_state, seller_limits
from app.agents.battery import acceptable_charge_kwh
from app.settlement.service import create_commitment
from app.simulation.state import get_config
from app.websocket.hub import queue_event

LIVE = (OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value)
SELL_ASSETS = {AssetType.SOLAR.value, AssetType.WIND.value, AssetType.SMALL_HYDRO.value, AssetType.BATTERY.value,
               AssetType.EV_BATTERY.value, AssetType.GRID_CONNECTION.value}
BUY_ASSETS = {AssetType.LOAD.value, AssetType.BATTERY.value, AssetType.EV_BATTERY.value,
              AssetType.GRID_CONNECTION.value}


async def _next_sequence(session: AsyncSession, microgrid_id: uuid.UUID) -> int:
    cur = (await session.execute(select(func.max(EnergyOrder.sequence)).where(
        EnergyOrder.microgrid_id == microgrid_id))).scalar()
    return (cur or 0) + 1


async def create_order(session: AsyncSession, participant_id: uuid.UUID, asset_id: uuid.UUID, side: OrderSide,
                       quantity_kwh: Decimal, limit_price: Decimal, ws: datetime, we: datetime,
                       renewable_only: bool = False, actor: str = "participant", now: datetime | None = None
                       ) -> EnergyOrder:
    ws, we = ensure_utc(ws), ensure_utc(we)
    asset = await session.get(EnergyAsset, asset_id)
    if not asset:
        raise NotFound("asset not found")
    if asset.participant_id != participant_id:
        raise Forbidden("asset does not belong to participant")
    p = await session.get(Participant, participant_id)
    if not p or not p.active:
        raise ValidationFailed("participant inactive or unknown")
    mg = await session.get(Microgrid, asset.microgrid_id)
    cfg = await get_config(session, mg.id)
    now = now or cfg.sim_time
    if quantity_kwh <= 0 or limit_price <= 0:
        raise ValidationFailed("quantity and limit price must be positive")
    if we <= ws:
        raise ValidationFailed("delivery window end must be after start")
    if we <= now:
        raise ValidationFailed("delivery window already elapsed")

    reject: str | None = None
    hours = (we - ws).total_seconds() / 3600.0
    if side == OrderSide.SELL:
        if asset.asset_type not in SELL_ASSETS:
            reject = f"asset type {asset.asset_type} cannot sell energy"
        else:
            lim = await seller_limits(session, asset, ws, we, now, cfg, mg)
            open_sell = (await session.execute(select(func.coalesce(func.sum(EnergyOrder.remaining_quantity_kwh), 0)).where(
                EnergyOrder.asset_id == asset_id, EnergyOrder.side == "SELL", EnergyOrder.status.in_(LIVE),
                EnergyOrder.delivery_window_start == ws, EnergyOrder.delivery_window_end == we))).scalar()
            physical = lim["physical"] - Decimal(str(open_sell))
            if quantity_kwh > physical:
                reject = (f"physically impossible offer: requested {quantity_kwh} kWh, "
                          f"max deliverable {max(physical, Decimal('0'))} kWh in window")
    else:
        if asset.asset_type not in BUY_ASSETS:
            reject = f"asset type {asset.asset_type} cannot buy energy"
        elif asset.battery and float(quantity_kwh) > acceptable_charge_kwh(battery_state(asset), hours) + 1e-9:
            reject = "battery cannot absorb requested energy (SOC/power limits or existing allocations)"
        elif asset.ev and not asset.ev.connected:
            reject = "EV not connected"

    order = EnergyOrder(participant_id=participant_id, asset_id=asset_id, microgrid_id=mg.id, side=side.value,
                        quantity_kwh=quantity_kwh, remaining_quantity_kwh=quantity_kwh, limit_price=limit_price,
                        delivery_window_start=ws, delivery_window_end=we, renewable_only=renewable_only,
                        status=OrderStatus.REJECTED.value if reject else OrderStatus.OPEN.value,
                        reject_reason=reject, sequence=await _next_sequence(session, mg.id), created_at=utcnow())
    if reject:
        order.remaining_quantity_kwh = Decimal("0")
    session.add(order)
    await session.flush()
    await audit.record(session, AuditEventType.ORDER_CREATED if not reject else AuditEventType.RISK_REJECTED,
                       "EnergyOrder", order.id, actor,
                       {"side": side.value, "quantity_kwh": quantity_kwh, "limit_price": limit_price,
                        "window": [ws, we], "status": order.status, "reject_reason": reject}, mg.id)
    queue_event(session, "ORDER_CREATED", mg.id, {"order_id": order.id, "side": side.value, "status": order.status,
                                                  "quantity_kwh": quantity_kwh, "limit_price": limit_price})
    return order


async def cancel_order(session: AsyncSession, order_id: uuid.UUID, participant_id: uuid.UUID | None,
                       actor: str) -> EnergyOrder:
    o = (await session.execute(select(EnergyOrder).where(EnergyOrder.id == order_id).with_for_update())).scalar_one_or_none()
    if not o:
        raise NotFound("order not found")
    if participant_id and o.participant_id != participant_id:
        raise Forbidden("not your order")
    if o.status not in LIVE:
        raise Conflict(f"order is {o.status}; cannot cancel")
    o.status = OrderStatus.CANCELLED.value
    await session.flush()
    await audit.record(session, "ORDER_CANCELLED", "EnergyOrder", o.id, actor, {"remaining": o.remaining_quantity_kwh},
                       o.microgrid_id)
    return o


async def expire_orders(session: AsyncSession, microgrid_id: uuid.UUID, now: datetime) -> int:
    rows = (await session.execute(select(EnergyOrder).where(
        EnergyOrder.microgrid_id == microgrid_id, EnergyOrder.status.in_(LIVE),
        EnergyOrder.delivery_window_start <= now))).scalars().all()
    for o in rows:
        o.status = OrderStatus.EXPIRED.value
    await session.flush()
    return len(rows)


async def order_book(session: AsyncSession, microgrid_id: uuid.UUID, ws: datetime | None = None,
                     we: datetime | None = None) -> dict:
    q = select(EnergyOrder).where(EnergyOrder.microgrid_id == microgrid_id, EnergyOrder.status.in_(LIVE))
    if ws:
        q = q.where(EnergyOrder.delivery_window_start == ensure_utc(ws))
    if we:
        q = q.where(EnergyOrder.delivery_window_end == ensure_utc(we))
    rows = (await session.execute(q)).scalars().all()
    ser = lambda o: {"id": str(o.id), "participant_id": str(o.participant_id), "asset_id": str(o.asset_id),  # noqa: E731
                     "price": str(o.limit_price), "remaining_kwh": str(o.remaining_quantity_kwh),
                     "sequence": o.sequence, "renewable_only": o.renewable_only,
                     "window_start": o.delivery_window_start.isoformat(), "window_end": o.delivery_window_end.isoformat()}
    buys = sorted([o for o in rows if o.side == "BUY"], key=lambda o: (-o.limit_price, o.sequence, str(o.id)))
    sells = sorted([o for o in rows if o.side == "SELL"], key=lambda o: (o.limit_price, o.sequence, str(o.id)))
    book = {"buy_book": [ser(o) for o in buys], "sell_book": [ser(o) for o in sells]}
    await cache.set_json(f"orderbook:{microgrid_id}", book, ttl=30)
    return book


async def run_clearing(session: AsyncSession, microgrid_id: uuid.UUID, ws: datetime, we: datetime,
                       actor: str = "market_engine", now: datetime | None = None) -> MarketClearing:
    """One clearing cycle for a delivery window. Serialised per microgrid (row lock)."""
    ws, we = ensure_utc(ws), ensure_utc(we)
    mg = (await session.execute(select(Microgrid).where(Microgrid.id == microgrid_id).with_for_update())).scalar_one_or_none()
    if not mg:
        raise NotFound("microgrid not found")
    cfg = await get_config(session, microgrid_id)
    now = now or cfg.sim_time
    audit.set_correlation_id()
    hours = (we - ws).total_seconds() / 3600.0

    orders = list((await session.execute(select(EnergyOrder).where(
        EnergyOrder.microgrid_id == microgrid_id, EnergyOrder.status.in_(LIVE),
        EnergyOrder.delivery_window_start == ws, EnergyOrder.delivery_window_end == we)
        .order_by(EnergyOrder.sequence, EnergyOrder.id).with_for_update())).scalars())
    assets = {a.id: a for a in (await session.execute(select(EnergyAsset).where(
        EnergyAsset.id.in_({o.asset_id for o in orders} or {uuid.uuid4()})))).scalars()}

    def book(o: EnergyOrder) -> BookOrder:
        a = assets[o.asset_id]
        return BookOrder(str(o.id), str(o.participant_id), str(o.asset_id), str(a.grid_node_id), o.side,
                         o.quantity_kwh, o.limit_price, o.sequence, o.renewable_only,
                         bool(a.generator and a.generator.renewable), o.remaining_quantity_kwh)

    book_orders = [book(o) for o in orders]
    buys = [b for b in book_orders if b.side == "BUY"]
    sells = [b for b in book_orders if b.side == "SELL"]

    net = await load_network(session, microgrid_id, ws, we)
    ctx = await MarketRiskContext(session, mg, ws, we, now, cfg, net).load(book_orders)
    await ctx.prepare(book_orders)
    base_flows = {k: (l.capacity_kw, round(l.flow_kw, 6)) for k, l in net.lines.items()}
    result = clear_market(buys, sells, ctx.validator(),
                          extra_hash={"window": [ws.isoformat(), we.isoformat()], "network": base_flows})

    grid_rej = result.rejected_quantity("GRID_CONGESTION")
    risk_rej = result.rejected_quantity() - grid_rej
    clearing = MarketClearing(
        microgrid_id=microgrid_id, delivery_window_start=ws, delivery_window_end=we,
        clearing_price=result.clearing_price, matched_kwh=result.matched_quantity,
        grid_rejected_kwh=grid_rej, risk_rejected_kwh=risk_rej, input_hash=result.input_hash, created_at=now,
        result={"trades": [{"buy_order_id": t.buy_order_id, "sell_order_id": t.sell_order_id,
                            "quantity_kwh": str(t.quantity), "price": str(t.price)} for t in result.trades],
                "rejections": [{"buy_order_id": r.buy_order_id, "sell_order_id": r.sell_order_id,
                                "requested_kwh": str(r.requested), "approved_kwh": str(r.approved),
                                "code": r.code, "message": r.message} for r in result.rejections],
                "buy_orders": len(buys), "sell_orders": len(sells)})
    session.add(clearing)
    await session.flush()
    for rd in ctx.decision_rows():
        session.add(rd)

    by_id = {str(o.id): o for o in orders}
    for oid, filled in result.fills.items():
        o = by_id[oid]
        if filled > 0:
            o.remaining_quantity_kwh = o.remaining_quantity_kwh - filled
            o.status = (OrderStatus.FILLED.value if o.remaining_quantity_kwh <= 0
                        else OrderStatus.PARTIALLY_FILLED.value)
    await session.flush()

    for t in result.trades:
        tr = Trade(clearing_id=clearing.id, microgrid_id=microgrid_id, buy_order_id=uuid.UUID(t.buy_order_id),
                   sell_order_id=uuid.UUID(t.sell_order_id), quantity_kwh=t.quantity, price=t.price, created_at=now)
        session.add(tr)
        await session.flush()
        await audit.record(session, AuditEventType.ORDER_MATCHED, "Trade", tr.id, actor,
                           {"buy_order": t.buy_order_id, "sell_order": t.sell_order_id, "quantity_kwh": t.quantity,
                            "price": t.price, "clearing_id": clearing.id}, microgrid_id)
        await create_commitment(session, microgrid_id, tr, assets[uuid.UUID(t.buyer_asset_id)],
                                assets[uuid.UUID(t.seller_asset_id)], ws, we, t.renewable, actor)

    for r in result.rejections:
        await audit.record(session, AuditEventType.RISK_REJECTED, "TradeCandidate", f"{r.buy_order_id}:{r.sell_order_id}",
                           actor, {"reason_code": r.code, "message": r.message, "requested_kwh": r.requested,
                                   "approved_kwh": r.approved}, microgrid_id)
        queue_event(session, "RISK_EVENT", microgrid_id, {"reason_code": r.code, "requested_kwh": r.requested,
                                                          "approved_kwh": r.approved, "message": r.message})
        if r.code == "GRID_CONGESTION":
            queue_event(session, "GRID_CONGESTION", microgrid_id, {"requested_kwh": r.requested,
                                                                   "approved_kwh": r.approved,
                                                                   "buy_order_id": r.buy_order_id,
                                                                   "sell_order_id": r.sell_order_id})
    queue_event(session, "MARKET_CLEARED", microgrid_id,
                {"clearing_id": clearing.id, "matched_kwh": clearing.matched_kwh,
                 "clearing_price": clearing.clearing_price, "grid_rejected_kwh": grid_rej,
                 "risk_rejected_kwh": risk_rej, "window": [ws, we]})
    await order_book(session, microgrid_id)
    return clearing
