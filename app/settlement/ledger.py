"""Double-entry internal ledger.

Rules enforced here (the only place balances change):
  * every transaction's legs sum to exactly zero;
  * participant accounts can never go negative; SYSTEM_* accounts may (operator float);
  * transactions with an idempotency key are applied at most once;
  * Participant.available_balance / locked_balance are denormalised mirrors updated
    atomically in the same DB transaction (available = AVAILABLE + SETTLED, locked = LOCKED).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import LedgerAccountKind as K
from app.core.errors import ValidationFailed
from app.core.timeutil import utcnow
from app.models.core import Participant
from app.models.ops import LedgerAccount, LedgerEntry, LedgerTransaction

SYSTEM_KINDS = {K.SYSTEM_PENALTY.value, K.SYSTEM_EQUITY.value}
ZERO = Decimal("0")


class Ledger:
    def __init__(self, session: AsyncSession, microgrid_id: uuid.UUID):
        self.s = session
        self.mg = microgrid_id

    async def account(self, owner_id: uuid.UUID | None, kind: K) -> LedgerAccount:
        q = select(LedgerAccount).where(LedgerAccount.owner_id == owner_id if owner_id else
                                        LedgerAccount.owner_id.is_(None),
                                        LedgerAccount.kind == kind.value,
                                        LedgerAccount.microgrid_id == self.mg).with_for_update()
        acc = (await self.s.execute(q)).scalar_one_or_none()
        if acc is None:
            acc = LedgerAccount(owner_id=owner_id, microgrid_id=self.mg, kind=kind.value, balance=ZERO)
            self.s.add(acc)
            await self.s.flush()
        return acc

    async def balance(self, owner_id: uuid.UUID | None, kind: K) -> Decimal:
        return (await self.account(owner_id, kind)).balance

    async def post(self, kind: str, ref_type: str, ref_id, legs: list[tuple[LedgerAccount, Decimal]],
                   description: str = "", idempotency_key: str | None = None) -> tuple[LedgerTransaction, bool]:
        """Returns (transaction, created). created=False means an identical idempotent request replayed."""
        if idempotency_key:
            existing = (await self.s.execute(select(LedgerTransaction).where(
                LedgerTransaction.idempotency_key == idempotency_key))).scalar_one_or_none()
            if existing:
                return existing, False
        legs = [(a, amt) for a, amt in legs if amt != 0]
        if sum((amt for _, amt in legs), ZERO) != 0:
            raise ValueError("ledger transaction legs must sum to zero")
        # aggregate per account to validate final balances
        delta: dict[uuid.UUID, Decimal] = {}
        accounts: dict[uuid.UUID, LedgerAccount] = {}
        for a, amt in legs:
            accounts[a.id] = a
            delta[a.id] = delta.get(a.id, ZERO) + amt
        for aid, d in delta.items():
            a = accounts[aid]
            if a.kind not in SYSTEM_KINDS and a.balance + d < 0:
                raise ValidationFailed("insufficient funds", account_kind=a.kind, owner_id=str(a.owner_id),
                                       balance=str(a.balance), required=str(-d))
        tx = LedgerTransaction(microgrid_id=self.mg, kind=kind, reference_type=ref_type, reference_id=str(ref_id),
                               description=description, idempotency_key=idempotency_key, created_at=utcnow())
        self.s.add(tx)
        await self.s.flush()
        for a, amt in legs:
            self.s.add(LedgerEntry(transaction_id=tx.id, account_id=a.id, amount=amt))
        for aid, d in delta.items():
            accounts[aid].balance = accounts[aid].balance + d
        await self.s.flush()
        for owner in {a.owner_id for a in accounts.values() if a.owner_id}:
            await self._sync_mirror(owner)
        return tx, True

    async def _sync_mirror(self, owner_id: uuid.UUID) -> None:
        rows = (await self.s.execute(select(LedgerAccount).where(LedgerAccount.owner_id == owner_id))).scalars()
        bal = {r.kind: r.balance for r in rows}
        p = await self.s.get(Participant, owner_id)
        p.available_balance = bal.get(K.AVAILABLE.value, ZERO) + bal.get(K.SETTLED.value, ZERO)
        p.locked_balance = bal.get(K.LOCKED.value, ZERO)

    # ------------------------------------------------------------- operations
    async def deposit(self, participant_id: uuid.UUID, amount: Decimal, ref: str = "deposit",
                      idempotency_key: str | None = None):
        if amount <= 0:
            raise ValidationFailed("deposit must be positive")
        eq = await self.account(None, K.SYSTEM_EQUITY)
        av = await self.account(participant_id, K.AVAILABLE)
        return await self.post("DEPOSIT", "PARTICIPANT", participant_id, [(eq, -amount), (av, amount)], ref,
                               idempotency_key)

    async def lock(self, participant_id: uuid.UUID, amount: Decimal, ref_type: str, ref_id, description: str = "",
                   idempotency_key: str | None = None):
        """Move funds into LOCKED, preferring AVAILABLE then SETTLED."""
        av = await self.account(participant_id, K.AVAILABLE)
        st = await self.account(participant_id, K.SETTLED)
        lk = await self.account(participant_id, K.LOCKED)
        if av.balance + st.balance < amount:
            raise ValidationFailed("insufficient funds to lock", required=str(amount),
                                   available=str(av.balance + st.balance))
        from_av = min(av.balance, amount)
        from_st = amount - from_av
        return await self.post("LOCK", ref_type, ref_id,
                               [(av, -from_av), (st, -from_st), (lk, amount)], description, idempotency_key)

    async def available_funds(self, participant_id: uuid.UUID) -> Decimal:
        av = await self.account(participant_id, K.AVAILABLE)
        st = await self.account(participant_id, K.SETTLED)
        return av.balance + st.balance
