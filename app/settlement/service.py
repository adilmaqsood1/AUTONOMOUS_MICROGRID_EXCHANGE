"""Commitment lifecycle: create (lock) -> verify delivery (meters) -> settle (ledger + chain)."""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.blockchain.gateway import get_gateway
from app.core.config import get_settings
from app.core.enums import AssetType, AuditEventType, CommitmentStatus, LedgerAccountKind as K
from app.core.errors import Conflict, NotFound
from app.core.timeutil import utcnow
from app.meters.service import window_sums
from app.models.assets import EnergyAsset
from app.models.core import Participant
from app.models.ops import EnergyCommitment, Settlement, Trade
from app.participants.reliability import reliability_score
from app.settlement.calc import compute_settlement, delivery_status
from app.settlement.ledger import Ledger
from app.websocket.hub import queue_event

QTY = Decimal("0.000001")


def metered_field(asset: EnergyAsset) -> str:
    """Which meter register proves delivery for this seller asset type."""
    if asset.asset_type in (AssetType.BATTERY.value, AssetType.EV_BATTERY.value):
        return "battery_discharge_kwh"
    if asset.asset_type == AssetType.GRID_CONNECTION.value:
        return "import_kwh"
    return "generation_kwh"


async def create_commitment(session: AsyncSession, microgrid_id: uuid.UUID, trade: Trade, buyer_asset: EnergyAsset,
                            seller_asset: EnergyAsset, ws: datetime, we: datetime, renewable: bool,
                            actor: str = "market") -> EnergyCommitment:
    s = get_settings()
    notional = (trade.quantity_kwh * trade.price).quantize(Decimal("0.00000001"))
    collateral = (notional * s.collateral_fraction).quantize(Decimal("0.00000001"))
    ledger = Ledger(session, microgrid_id)
    c = EnergyCommitment(microgrid_id=microgrid_id, trade_id=trade.id, buyer_id=buyer_asset.participant_id,
                         seller_id=seller_asset.participant_id, buyer_asset_id=buyer_asset.id,
                         seller_asset_id=seller_asset.id, quantity_kwh=trade.quantity_kwh, price=trade.price,
                         delivery_start=ws, delivery_end=we, status=CommitmentStatus.PENDING.value,
                         renewable=renewable, created_at=utcnow())
    session.add(c)
    await session.flush()
    await ledger.lock(c.buyer_id, notional, "COMMITMENT", c.id, "buyer funds escrow", f"lock-buyer:{c.id}")
    await ledger.lock(c.seller_id, collateral, "COMMITMENT", c.id, "seller delivery collateral", f"lock-seller:{c.id}")
    c.locked_buyer_funds, c.locked_seller_collateral = notional, collateral
    # physical allocations (prevents double-allocating storage capacity)
    q = float(trade.quantity_kwh)
    if seller_asset.battery:
        seller_asset.battery.allocated_discharge_kwh += q
    if seller_asset.ev:
        seller_asset.ev.allocated_discharge_kwh += q
    if buyer_asset.battery:
        buyer_asset.battery.allocated_charge_kwh += q
    gw = get_gateway()
    bp, sp = await session.get(Participant, c.buyer_id), await session.get(Participant, c.seller_id)
    rc = await gw.create_energy_commitment(str(c.id), bp.wallet_id, sp.wallet_id, c.quantity_kwh, c.price)
    await gw.lock_escrow(str(c.id), bp.wallet_id, notional)
    await gw.lock_escrow(str(c.id), sp.wallet_id, collateral)
    c.blockchain_reference = rc.reference
    c.status = CommitmentStatus.ACTIVE.value
    sp.commitments_total += 1
    await session.flush()
    await audit.record(session, AuditEventType.TRADE_COMMITTED, "EnergyCommitment", c.id, actor,
                       {"buyer": c.buyer_id, "seller": c.seller_id, "quantity_kwh": c.quantity_kwh, "price": c.price,
                        "window": [ws, we], "blockchain_reference": rc.reference}, microgrid_id)
    queue_event(session, "TRADE_CREATED", microgrid_id,
                {"commitment_id": c.id, "buyer_id": c.buyer_id, "seller_id": c.seller_id,
                 "quantity_kwh": c.quantity_kwh, "price": c.price, "delivery_start": ws, "delivery_end": we})
    return c


async def verify_deliveries(session: AsyncSession, microgrid_id: uuid.UUID, now: datetime,
                            actor: str = "delivery_verifier") -> dict:
    """Verify every ACTIVE commitment whose window has ended using trusted, complete meter data.

    Commitments of the same seller asset/window share the metered energy FIFO. Incomplete or
    unverified meter data leaves the commitment ACTIVE (never paid on unverified delivery).
    """
    q = select(EnergyCommitment).where(EnergyCommitment.microgrid_id == microgrid_id,
                                       EnergyCommitment.status == CommitmentStatus.ACTIVE.value,
                                       EnergyCommitment.delivery_end <= now).order_by(
        EnergyCommitment.created_at, EnergyCommitment.id).with_for_update()
    commits = list((await session.execute(q)).scalars())
    groups: dict[tuple, list[EnergyCommitment]] = defaultdict(list)
    for c in commits:
        groups[(c.seller_asset_id, c.delivery_start, c.delivery_end)].append(c)
    verified, pending, shortfalls = [], [], []
    for (aid, ws, we), cs in groups.items():
        asset = await session.get(EnergyAsset, aid)
        sums = await window_sums(session, aid, ws, we)
        if not sums.complete or not sums.all_verified:
            pending += [{"commitment_id": str(c.id),
                         "reason": "INCOMPLETE_METER_DATA" if not sums.complete else "UNVERIFIED_METER_DATA"} for c in cs]
            continue
        remaining = Decimal(str(round(sums.totals[metered_field(asset)], 6)))
        gw = get_gateway()
        for c in cs:
            got = min(c.quantity_kwh, max(remaining, Decimal("0"))).quantize(QTY)
            remaining -= got
            ratio = float(got / c.quantity_kwh)
            c.delivered_kwh, c.delivery_ratio = got, ratio
            c.status = delivery_status(Decimal(str(ratio)))
            seller = await session.get(Participant, c.seller_id)
            if c.status == CommitmentStatus.DELIVERED.value:
                seller.deliveries_success += 1
            elif c.status == CommitmentStatus.PARTIAL.value:
                seller.deliveries_partial += 1
            else:
                seller.deliveries_failed += 1
            seller.delivery_ratio_sum += ratio
            seller.imbalance_kwh += float(c.quantity_kwh - got)
            n = seller.deliveries_success + seller.deliveries_partial + seller.deliveries_failed
            seller.reliability_score = reliability_score(n, seller.delivery_ratio_sum)
            # release physical allocation now that the window is over
            q_f = float(c.quantity_kwh)
            if asset.battery:
                asset.battery.allocated_discharge_kwh = max(0.0, asset.battery.allocated_discharge_kwh - q_f)
            if asset.ev:
                asset.ev.allocated_discharge_kwh = max(0.0, asset.ev.allocated_discharge_kwh - q_f)
            ba = await session.get(EnergyAsset, c.buyer_asset_id)
            if ba.battery:
                ba.battery.allocated_charge_kwh = max(0.0, ba.battery.allocated_charge_kwh - q_f)
            proof = await gw.record_meter_proof(str(c.id), str(aid), got, "|".join(map(str, sums.reading_ids))[:200])
            payload = {"committed_kwh": c.quantity_kwh, "delivered_kwh": got, "delivery_ratio": ratio,
                       "proof": proof.reference}
            if c.status != CommitmentStatus.DELIVERED.value:
                await audit.record(session, AuditEventType.DELIVERY_SHORTFALL, "EnergyCommitment", c.id, actor,
                                   {**payload, "shortfall_kwh": c.quantity_kwh - got}, microgrid_id)
                shortfalls.append(c)
            verified.append(c)
    await session.flush()
    return {"verified": verified, "pending": pending, "shortfalls": shortfalls}


async def settle_commitment(session: AsyncSession, commitment_id: uuid.UUID, actor: str = "settlement_engine",
                            idempotency_key: str | None = None) -> tuple[Settlement, bool]:
    """Idempotent. Returns (settlement, created). Requires verified delivery."""
    c = (await session.execute(select(EnergyCommitment).where(EnergyCommitment.id == commitment_id)
                               .with_for_update())).scalar_one_or_none()
    if not c:
        raise NotFound("commitment not found")
    existing = (await session.execute(select(Settlement).where(Settlement.commitment_id == c.id))).scalar_one_or_none()
    if existing:
        return existing, False
    if c.status not in (CommitmentStatus.DELIVERED.value, CommitmentStatus.PARTIAL.value,
                        CommitmentStatus.FAILED.value) or c.delivered_kwh is None:
        raise Conflict("delivery not verified; settlement refused", status=c.status)
    s = get_settings()
    amounts = compute_settlement(c.quantity_kwh, c.delivered_kwh, c.price, c.locked_seller_collateral,
                                 s.imbalance_penalty_multiplier)
    ledger = Ledger(session, c.microgrid_id)
    b_lock = await ledger.account(c.buyer_id, K.LOCKED)
    s_lock = await ledger.account(c.seller_id, K.LOCKED)
    b_av = await ledger.account(c.buyer_id, K.AVAILABLE)
    s_av = await ledger.account(c.seller_id, K.AVAILABLE)
    s_pend = await ledger.account(c.seller_id, K.PENDING_SETTLEMENT)
    s_set = await ledger.account(c.seller_id, K.SETTLED)
    pen = await ledger.account(None, K.SYSTEM_PENALTY)
    legs = [(b_lock, -c.locked_buyer_funds), (s_pend, amounts.seller_payment), (b_av, c.locked_buyer_funds - amounts.seller_payment),
            (s_lock, -c.locked_seller_collateral), (s_av, amounts.collateral_release), (pen, amounts.penalty)]
    await ledger.post("SETTLEMENT", "COMMITMENT", c.id, legs, "delivery settlement", f"settle:{c.id}")
    gw = get_gateway()
    receipt = await gw.settle_trade(str(c.id), amounts.seller_payment, c.locked_buyer_funds - amounts.seller_payment,
                                    amounts.penalty)
    # chain-confirmed: release pending -> settled
    await ledger.post("SETTLEMENT_RELEASE", "COMMITMENT", c.id, [(s_pend, -amounts.seller_payment), (s_set, amounts.seller_payment)],
                      "pending settlement finalised", f"settle-release:{c.id}")
    st = Settlement(commitment_id=c.id, microgrid_id=c.microgrid_id, committed_kwh=c.quantity_kwh,
                    delivered_kwh=amounts.delivered_kwh, shortfall_kwh=amounts.shortfall_kwh,
                    delivery_ratio=float(amounts.delivery_ratio), gross_amount=amounts.gross_amount,
                    seller_payment=amounts.seller_payment, buyer_refund=c.locked_buyer_funds - amounts.seller_payment,
                    penalty=amounts.penalty, blockchain_reference=receipt.reference, created_at=utcnow())
    session.add(st)
    prior_status = c.status
    c.status = CommitmentStatus.SETTLED.value
    await session.flush()
    audit_payload = {
        "commitment_id": c.id, "delivery_status": prior_status, "seller_payment": st.seller_payment,
        "buyer_refund": st.buyer_refund, "penalty": st.penalty, "delivery_ratio": st.delivery_ratio,
        "blockchain_reference": receipt.reference
    }
    if idempotency_key:
        audit_payload["idempotency_key"] = idempotency_key
    await audit.record(session, AuditEventType.SETTLEMENT_COMPLETED, "Settlement", st.id, actor,
                       audit_payload, c.microgrid_id)
    queue_event(session, "SETTLEMENT", c.microgrid_id,
                {"settlement_id": st.id, "commitment_id": c.id, "seller_payment": st.seller_payment,
                 "penalty": st.penalty, "delivery_ratio": st.delivery_ratio, "delivery_status": prior_status})
    return st, True


async def settle_all_verified(session: AsyncSession, microgrid_id: uuid.UUID, actor: str = "settlement_engine"
                              ) -> list[Settlement]:
    q = select(EnergyCommitment.id).where(EnergyCommitment.microgrid_id == microgrid_id,
                                          EnergyCommitment.status.in_([CommitmentStatus.DELIVERED.value,
                                                                       CommitmentStatus.PARTIAL.value,
                                                                       CommitmentStatus.FAILED.value])).order_by(
        EnergyCommitment.created_at, EnergyCommitment.id)
    out = []
    for cid in (await session.execute(q)).scalars().all():
        st, created = await settle_commitment(session, cid, actor)
        if created:
            out.append(st)
    return out
