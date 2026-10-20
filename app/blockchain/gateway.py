"""Blockchain abstraction. Business logic depends only on `BlockchainGateway`.

The chain is NOT a database: it enforces economic commitments (escrow, settlement, certificate
custody). Our PostgreSQL ledger stays the system of record; references are stored for verification.
`MockBlockchainGateway` is deterministic: tx hash = sha256(sequence-independent canonical payload),
so the same operation always yields the same reference (idempotent, replayable in tests).
An EVM implementation can implement the same interface later without touching callers.
"""
from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class TxReceipt:
    reference: str
    operation: str
    confirmed: bool
    block_number: int
    payload_hash: str


class BlockchainGateway(ABC):
    @abstractmethod
    async def create_energy_commitment(self, commitment_id: str, buyer: str, seller: str,
                                       quantity_kwh: Decimal, price: Decimal) -> TxReceipt: ...
    @abstractmethod
    async def lock_escrow(self, commitment_id: str, account: str, amount: Decimal) -> TxReceipt: ...
    @abstractmethod
    async def record_meter_proof(self, commitment_id: str, meter_id: str, delivered_kwh: Decimal,
                                 reading_hash: str) -> TxReceipt: ...
    @abstractmethod
    async def settle_trade(self, commitment_id: str, seller_payment: Decimal, refund: Decimal,
                           penalty: Decimal) -> TxReceipt: ...
    @abstractmethod
    async def issue_renewable_certificate(self, certificate_id: str, owner: str, quantity_kwh: Decimal,
                                          source: str, meter_reading_id: str) -> TxReceipt: ...
    @abstractmethod
    async def transfer_certificate(self, certificate_id: str, from_owner: str, to_owner: str) -> TxReceipt: ...
    @abstractmethod
    async def retire_certificate(self, certificate_id: str, owner: str) -> TxReceipt: ...
    @abstractmethod
    async def verify_transaction(self, reference: str) -> bool: ...


class MockBlockchainGateway(BlockchainGateway):
    def __init__(self) -> None:
        self._txs: dict[str, TxReceipt] = {}
        self._block = 0
        self.certificate_owner: dict[str, str] = {}
        self.retired: set[str] = set()
        self.issued_readings: set[str] = set()

    def _submit(self, op: str, payload: dict) -> TxReceipt:
        canonical = json.dumps({"op": op, **payload}, sort_keys=True, default=str)
        h = hashlib.sha256(canonical.encode()).hexdigest()
        ref = "0x" + h
        existing = self._txs.get(ref)
        if existing:
            return existing  # idempotent replay
        self._block += 1
        rc = TxReceipt(ref, op, True, self._block, h)
        self._txs[ref] = rc
        return rc

    async def create_energy_commitment(self, commitment_id, buyer, seller, quantity_kwh, price):
        return self._submit("create_energy_commitment", dict(id=commitment_id, buyer=buyer, seller=seller,
                                                              qty=str(quantity_kwh), price=str(price)))

    async def lock_escrow(self, commitment_id, account, amount):
        return self._submit("lock_escrow", dict(id=commitment_id, account=account, amount=str(amount)))

    async def record_meter_proof(self, commitment_id, meter_id, delivered_kwh, reading_hash):
        return self._submit("record_meter_proof", dict(id=commitment_id, meter=meter_id,
                                                        delivered=str(delivered_kwh), proof=reading_hash))

    async def settle_trade(self, commitment_id, seller_payment, refund, penalty):
        return self._submit("settle_trade", dict(id=commitment_id, pay=str(seller_payment),
                                                  refund=str(refund), penalty=str(penalty)))

    async def issue_renewable_certificate(self, certificate_id, owner, quantity_kwh, source, meter_reading_id):
        if meter_reading_id in self.issued_readings:
            raise ValueError("certificate already issued for this meter reading (on-chain uniqueness)")
        self.issued_readings.add(meter_reading_id)
        self.certificate_owner[certificate_id] = owner
        return self._submit("issue_certificate", dict(id=certificate_id, owner=owner, qty=str(quantity_kwh),
                                                       src=source, reading=meter_reading_id))

    async def transfer_certificate(self, certificate_id, from_owner, to_owner):
        if certificate_id in self.retired:
            raise ValueError("retired certificate cannot be transferred")
        if self.certificate_owner.get(certificate_id) not in (None, from_owner):
            raise ValueError("sender is not the certificate owner")
        self.certificate_owner[certificate_id] = to_owner
        return self._submit("transfer_certificate", dict(id=certificate_id, frm=from_owner, to=to_owner))

    async def retire_certificate(self, certificate_id, owner):
        if certificate_id in self.retired:
            raise ValueError("certificate already retired")
        self.retired.add(certificate_id)
        return self._submit("retire_certificate", dict(id=certificate_id, owner=owner))

    async def verify_transaction(self, reference):
        rc = self._txs.get(reference)
        return bool(rc and rc.confirmed)


_gateway: BlockchainGateway | None = None


def get_gateway() -> BlockchainGateway:
    global _gateway
    if _gateway is not None:
        return _gateway

    from app.core.config import get_settings
    settings = get_settings()

    if settings.blockchain_mode == "evm":
        from app.blockchain.evm_gateway import EVMBlockchainGateway
        _gateway = EVMBlockchainGateway()
    else:
        _gateway = MockBlockchainGateway()

    return _gateway


def set_gateway(gw: BlockchainGateway | None) -> None:
    global _gateway
    _gateway = gw
