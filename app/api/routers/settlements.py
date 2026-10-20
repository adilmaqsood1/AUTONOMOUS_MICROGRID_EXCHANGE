"""Settlements and double-entry ledger routers."""
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, SessionDep, current_principal, operator, reader
from app.models.ops import LedgerAccount, LedgerTransaction, Settlement
from app.schemas.schemas import (
    LedgerAccountResponse, LedgerTransactionResponse, SettleCommitmentRequest, SettlementResponse
)
from app.settlement import service as settlement_service

settlement_router = APIRouter(prefix="/settlements", tags=["Settlement"])
ledger_router = APIRouter(prefix="/ledger", tags=["Double-Entry Ledger"])


# --- Settlements
@settlement_router.get("", response_model=List[SettlementResponse], dependencies=[Depends(reader)])
async def list_settlements(microgrid_id: Optional[uuid.UUID] = None,
                           commitment_id: Optional[uuid.UUID] = None,
                           limit: int = 100,
                           session: AsyncSession = SessionDep):
    q = select(Settlement)
    if microgrid_id:
        q = q.where(Settlement.microgrid_id == microgrid_id)
    if commitment_id:
        q = q.where(Settlement.commitment_id == commitment_id)
    return (await session.execute(q.order_by(Settlement.settled_at.desc()).limit(min(limit, 500)))).scalars().all()


@settlement_router.get("/{settlement_id}", response_model=SettlementResponse, dependencies=[Depends(reader)])
async def get_settlement(settlement_id: uuid.UUID, session: AsyncSession = SessionDep):
    s = await session.get(Settlement, settlement_id)
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "settlement not found")
    return s


@settlement_router.post("/settle", response_model=SettlementResponse, dependencies=[Depends(operator)])
async def settle_commitment_endpoint(req: SettleCommitmentRequest,
                                     idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
                                     principal: Principal = Depends(current_principal),
                                     session: AsyncSession = SessionDep):
    st, _ = await settlement_service.settle_commitment(
        session, req.commitment_id, actor=principal.actor, idempotency_key=idempotency_key
    )
    await session.commit()
    await session.refresh(st)
    return st


# --- Ledger
@ledger_router.get("/accounts", response_model=List[LedgerAccountResponse], dependencies=[Depends(reader)])
async def list_ledger_accounts(microgrid_id: Optional[uuid.UUID] = None,
                               participant_id: Optional[uuid.UUID] = None,
                               session: AsyncSession = SessionDep):
    q = select(LedgerAccount)
    if microgrid_id:
        q = q.where(LedgerAccount.microgrid_id == microgrid_id)
    if participant_id:
        q = q.where(LedgerAccount.participant_id == participant_id)
    return (await session.execute(q.order_by(LedgerAccount.kind))).scalars().all()


@ledger_router.get("/transactions", response_model=List[LedgerTransactionResponse], dependencies=[Depends(reader)])
async def list_ledger_transactions(microgrid_id: Optional[uuid.UUID] = None,
                                   limit: int = 100,
                                   session: AsyncSession = SessionDep):
    q = select(LedgerTransaction)
    if microgrid_id:
        q = q.where(LedgerTransaction.microgrid_id == microgrid_id)
    return (await session.execute(q.order_by(LedgerTransaction.created_at.desc()).limit(min(limit, 500)))).scalars().all()
