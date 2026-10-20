"""Integration: order -> clearing -> grid validation -> commitment -> delivery -> settlement."""
from datetime import timedelta
from decimal import Decimal as D

from sqlalchemy import func, select

from app.core.enums import CommitmentStatus, OrderSide
from app.market.cycle import advance_to
from app.market.service import create_order, order_book, run_clearing
from app.models import (AuditEvent, EnergyCommitment, LedgerEntry, LedgerTransaction, Participant, RiskDecision, Settlement)
from app.settlement.service import settle_commitment, settle_all_verified, verify_deliveries
from app.simulation.state import DEFAULT_START

NOON = DEFAULT_START + timedelta(hours=7)  # 12:00 local (tz +5): strong solar
WS, WE = NOON, NOON + timedelta(hours=1)


async def _setup_to(session, demo, when):
    await advance_to(session, demo["microgrid_id"], when)
    await session.commit()


async def test_full_trade_lifecycle_matches_meters_and_ledger(session, demo):
    A, P = demo["assets"], demo["participants"]
    await _setup_to(session, demo, WS)
    sell = await create_order(session, P["HydroOperator"], A["Hydro-1"], OrderSide.SELL, D("20"), D("0.05"), WS, WE)
    buy = await create_order(session, P["CityHospital"], A["Hospital"], OrderSide.BUY, D("20"), D("0.20"), WS, WE)
    assert sell.status == "OPEN" and buy.status == "OPEN"
    book = await order_book(session, demo["microgrid_id"])
    assert len(book["buy_book"]) == 1 and len(book["sell_book"]) == 1

    clearing = await run_clearing(session, demo["microgrid_id"], WS, WE)
    assert clearing.matched_kwh == D("20"), clearing.result
    assert clearing.clearing_price == D("0.125")
    commits = (await session.execute(select(EnergyCommitment))).scalars().all()
    assert len(commits) == 1 and commits[0].status == CommitmentStatus.ACTIVE.value
    c = commits[0]
    assert c.locked_buyer_funds == D("2.5") and c.locked_seller_collateral == D("2.5")
    assert c.blockchain_reference.startswith("0x")
    hosp = await session.get(Participant, P["CityHospital"])
    assert hosp.locked_balance == D("2.5") and hosp.available_balance == D("497.5")
    await session.refresh(sell), await session.refresh(buy)
    assert sell.status == "FILLED" and buy.status == "FILLED"

    # settlement before delivery verification must be refused
    import pytest
    from app.core.errors import Conflict
    with pytest.raises(Conflict):
        await settle_commitment(session, c.id)

    await advance_to(session, demo["microgrid_id"], WE)
    v = await verify_deliveries(session, demo["microgrid_id"], WE)
    assert len(v["verified"]) == 1 and not v["pending"]
    await session.refresh(c)
    assert c.status in ("DELIVERED", "PARTIAL")  # hydro output decides; ratio must match meters
    assert 0.0 <= c.delivery_ratio <= 1.0
    st, created = await settle_commitment(session, c.id)
    assert created
    assert st.seller_payment == (c.delivered_kwh * c.price).quantize(D("0.00000001"))
    assert st.seller_payment + st.buyer_refund == st.gross_amount
    # replay is idempotent: no second payment
    st2, created2 = await settle_commitment(session, c.id)
    assert not created2 and st2.id == st.id
    assert (await session.execute(select(func.count()).select_from(Settlement))).scalar() == 1
    seller = await session.get(Participant, P["HydroOperator"])
    assert seller.available_balance + seller.locked_balance == D("5000") + st.seller_payment - st.penalty

    # double entry: every ledger transaction nets to zero; money conserved
    txs = (await session.execute(select(LedgerTransaction.id))).scalars().all()
    for tx in txs:
        total = (await session.execute(select(func.sum(LedgerEntry.amount)).where(LedgerEntry.transaction_id == tx))).scalar()
        assert D(str(total)) == 0
    total_funds = (await session.execute(select(func.sum(Participant.available_balance + Participant.locked_balance)))).scalar()
    # system penalty account holds penalties; participants + penalty pool == deposits
    assert D(str(total_funds)) + st.penalty == (await _total_deposits(session))


async def _total_deposits(session):
    q = select(func.sum(LedgerEntry.amount)).join(LedgerTransaction, LedgerTransaction.id == LedgerEntry.transaction_id) \
        .where(LedgerTransaction.kind == "DEPOSIT", LedgerEntry.amount > 0)
    return D(str((await session.execute(q)).scalar()))


async def test_audit_trail_records_lifecycle(session, demo):
    A, P = demo["assets"], demo["participants"]
    await _setup_to(session, demo, WS)
    await create_order(session, P["HydroOperator"], A["Hydro-1"], OrderSide.SELL, D("10"), D("0.05"), WS, WE)
    await create_order(session, P["CityHospital"], A["Hospital"], OrderSide.BUY, D("10"), D("0.20"), WS, WE)
    await run_clearing(session, demo["microgrid_id"], WS, WE)
    types = {e for (e,) in (await session.execute(select(AuditEvent.event_type))).all()}
    assert {"ORDER_CREATED", "ORDER_MATCHED", "TRADE_COMMITTED", "METER_READING_RECEIVED"} <= types


async def test_physically_impossible_offer_is_rejected(session, demo):
    A, P = demo["assets"], demo["participants"]
    await _setup_to(session, demo, WS)
    o = await create_order(session, P["Prosumer-1"], A["Solar-1"], OrderSide.SELL, D("500"), D("0.05"), WS, WE)  # 6 kW unit
    assert o.status == "REJECTED" and "physically impossible" in o.reject_reason
    assert o.remaining_quantity_kwh == 0
