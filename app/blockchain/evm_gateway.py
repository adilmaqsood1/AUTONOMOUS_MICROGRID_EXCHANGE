"""EVM implementation of BlockchainGateway using web3.py and deployed Solidity smart contracts."""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from eth_utils import keccak, to_checksum_address
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import AsyncWeb3
from web3.exceptions import TransactionNotFound

from app.blockchain.canonical import compute_canonical_evidence_hash
from app.blockchain.contracts import get_contract_abi, get_deployed_addresses
from app.blockchain.gateway import BlockchainGateway, TxReceipt
from app.blockchain.keys import EncryptedLocalKeyManager, KeyManager
from app.blockchain.tx_manager import TransactionManager
from app.core.config import get_settings
from app.db.session import async_session_factory

logger = logging.getLogger(__name__)


def _to_bytes32(val: str) -> bytes:
    """Convert a string identifier to a 32-byte hash."""
    if val.startswith("0x") and len(val) == 66:
        return bytes.fromhex(val[2:])
    return keccak(text=val)


class EVMBlockchainGateway(BlockchainGateway):
    """Production and local testnet implementation of BlockchainGateway backed by real EVM contracts."""

    def __init__(
        self,
        w3: AsyncWeb3 | None = None,
        key_manager: KeyManager | None = None,
        tx_manager: TransactionManager | None = None,
        operator_ref: str | None = None,
        oracle_ref: str | None = None,
    ) -> None:
        settings = get_settings()
        self.w3 = w3 or AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(settings.evm_rpc_url))
        self.key_manager = key_manager or EncryptedLocalKeyManager(settings.master_key_secret)
        self.tx_manager = tx_manager or TransactionManager(self.w3, self.key_manager, chain_id=settings.evm_chain_id)
        self.deployed_addresses = get_deployed_addresses()

        # Cached contract objects
        self._contracts: dict[str, Any] = {}

        # Operator / Oracle signing key refs
        self._operator_ref = operator_ref
        self._oracle_ref = oracle_ref

        # Fallback local test references
        if not self._operator_ref and settings.deployer_private_key:
            _, self._operator_ref = self.key_manager.create_key_pair()

    def get_contract(self, name: str) -> Any:
        if name not in self._contracts:
            address = self.deployed_addresses.get(name)
            if not address:
                raise ValueError(f"Contract {name} address not configured.")
            abi = get_contract_abi(name)
            self._contracts[name] = self.w3.eth.contract(
                address=to_checksum_address(address),
                abi=abi,
            )
        return self._contracts[name]

    def _ensure_signer_ref(self) -> str:
        if not self._operator_ref:
            _, self._operator_ref = self.key_manager.create_key_pair()
        return self._operator_ref

    async def _run_in_session(self, func) -> Any:
        async with async_session_factory() as session:
            res = await func(session)
            await session.commit()
            return res

    async def create_energy_commitment(
        self,
        commitment_id: str,
        buyer: str,
        seller: str,
        quantity_kwh: Decimal,
        price: Decimal,
    ) -> TxReceipt:
        async def _call(session: AsyncSession) -> TxReceipt:
            settlement_contract = self.get_contract("EnergySettlement")
            signer_ref = self._ensure_signer_ref()

            cid_bytes = _to_bytes32(commitment_id)
            buyer_addr = to_checksum_address(buyer) if buyer.startswith("0x") else self.key_manager.get_address_from_ref(signer_ref)
            seller_addr = to_checksum_address(seller) if seller.startswith("0x") else self.key_manager.get_address_from_ref(signer_ref)

            quantity_wh = int(quantity_kwh * Decimal("1000"))
            # price per Wh in wei (1 GUSD = 10^18 wei)
            price_per_wh_wei = int((price / Decimal("1000")) * Decimal(10**18))
            collateral_amount = int(quantity_wh * price_per_wh_wei * Decimal("0.2"))  # 20% collateral

            # delivery start and end (1h window default)
            now_ts = int(Decimal(str(self.w3.eth.get_block("latest").timestamp if hasattr(self.w3.eth, "_timestamp") else 0)) or 1000)
            delivery_start = now_ts
            delivery_end = now_ts + 3600

            fn_call = settlement_contract.functions.createCommitment(
                cid_bytes,
                buyer_addr,
                seller_addr,
                _to_bytes32(f"ASSET_SELLER_{seller}"),
                _to_bytes32(f"ASSET_BUYER_{buyer}"),
                quantity_wh,
                price_per_wh_wei,
                delivery_start,
                delivery_end,
                collateral_amount,
            )

            tx_rec = await self.tx_manager.execute_transaction(
                db=session,
                from_encrypted_ref=signer_ref,
                to_address=settlement_contract.address,
                contract_function_call=fn_call,
                business_entity_type="COMMITMENT",
                business_entity_id=commitment_id,
                idempotency_key=f"COMMIT:{commitment_id}",
            )

            return TxReceipt(
                reference=tx_rec.tx_hash or "0x",
                operation="create_energy_commitment",
                confirmed=tx_rec.status == "CONFIRMED",
                block_number=tx_rec.block_number or 0,
                payload_hash=cid_bytes.hex(),
            )

        return await self._run_in_session(_call)

    async def lock_escrow(self, commitment_id: str, account: str, amount: Decimal) -> TxReceipt:
        async def _call(session: AsyncSession) -> TxReceipt:
            settlement_contract = self.get_contract("EnergySettlement")
            signer_ref = self._ensure_signer_ref()
            cid_bytes = _to_bytes32(commitment_id)

            fn_call = settlement_contract.functions.fundEscrow(cid_bytes)

            tx_rec = await self.tx_manager.execute_transaction(
                db=session,
                from_encrypted_ref=signer_ref,
                to_address=settlement_contract.address,
                contract_function_call=fn_call,
                business_entity_type="ESCROW",
                business_entity_id=commitment_id,
                idempotency_key=f"ESCROW:{commitment_id}",
            )

            return TxReceipt(
                reference=tx_rec.tx_hash or "0x",
                operation="lock_escrow",
                confirmed=tx_rec.status == "CONFIRMED",
                block_number=tx_rec.block_number or 0,
                payload_hash=cid_bytes.hex(),
            )

        return await self._run_in_session(_call)

    async def record_meter_proof(
        self,
        commitment_id: str,
        meter_id: str,
        delivered_kwh: Decimal,
        reading_hash: str,
    ) -> TxReceipt:
        async def _call(session: AsyncSession) -> TxReceipt:
            oracle_contract = self.get_contract("DeliveryOracle")
            signer_ref = self._oracle_ref or self._ensure_signer_ref()
            cid_bytes = _to_bytes32(commitment_id)
            meter_bytes = _to_bytes32(meter_id)
            delivered_wh = int(delivered_kwh * Decimal("1000"))

            # Canonical evidence hash
            evidence_hash = compute_canonical_evidence_hash(
                commitment_id=commitment_id,
                meter_id=meter_id,
                committed_wh=delivered_wh,
                delivered_wh=delivered_wh,
                start_time_iso="2026-10-07T00:00:00Z",
                end_time_iso="2026-10-07T01:00:00Z",
            )
            evidence_bytes = bytes.fromhex(evidence_hash[2:])

            fn_call = oracle_contract.functions.submitDeliveryAttestation(
                cid_bytes,
                meter_bytes,
                delivered_wh,
                delivered_wh,
                0,
                3600,
                evidence_bytes,
            )

            tx_rec = await self.tx_manager.execute_transaction(
                db=session,
                from_encrypted_ref=signer_ref,
                to_address=oracle_contract.address,
                contract_function_call=fn_call,
                business_entity_type="ATTESTATION",
                business_entity_id=commitment_id,
                idempotency_key=f"ATTEST:{commitment_id}",
            )

            return TxReceipt(
                reference=tx_rec.tx_hash or "0x",
                operation="record_meter_proof",
                confirmed=tx_rec.status == "CONFIRMED",
                block_number=tx_rec.block_number or 0,
                payload_hash=evidence_hash,
            )

        return await self._run_in_session(_call)

    async def settle_trade(
        self,
        commitment_id: str,
        seller_payment: Decimal,
        refund: Decimal,
        penalty: Decimal,
    ) -> TxReceipt:
        async def _call(session: AsyncSession) -> TxReceipt:
            settlement_contract = self.get_contract("EnergySettlement")
            signer_ref = self._ensure_signer_ref()
            cid_bytes = _to_bytes32(commitment_id)

            fn_call = settlement_contract.functions.settleCommitment(cid_bytes)

            tx_rec = await self.tx_manager.execute_transaction(
                db=session,
                from_encrypted_ref=signer_ref,
                to_address=settlement_contract.address,
                contract_function_call=fn_call,
                business_entity_type="SETTLEMENT",
                business_entity_id=commitment_id,
                idempotency_key=f"SETTLE:{commitment_id}",
            )

            return TxReceipt(
                reference=tx_rec.tx_hash or "0x",
                operation="settle_trade",
                confirmed=tx_rec.status == "CONFIRMED",
                block_number=tx_rec.block_number or 0,
                payload_hash=cid_bytes.hex(),
            )

        return await self._run_in_session(_call)

    async def issue_renewable_certificate(
        self,
        certificate_id: str,
        owner: str,
        quantity_kwh: Decimal,
        source: str,
        meter_reading_id: str,
    ) -> TxReceipt:
        async def _call(session: AsyncSession) -> TxReceipt:
            rec_contract = self.get_contract("RenewableEnergyCertificate")
            signer_ref = self._ensure_signer_ref()

            cert_bytes = _to_bytes32(certificate_id)
            owner_addr = to_checksum_address(owner) if owner.startswith("0x") else self.key_manager.get_address_from_ref(signer_ref)
            quantity_wh = int(quantity_kwh * Decimal("1000"))
            meter_bytes = _to_bytes32(meter_reading_id)

            fn_call = rec_contract.functions.issueCertificate(
                cert_bytes,
                owner_addr,
                _to_bytes32(f"ASSET_RENEWABLE_{meter_reading_id}"),
                str(source),
                quantity_wh,
                0,
                3600,
                meter_bytes,
            )

            tx_rec = await self.tx_manager.execute_transaction(
                db=session,
                from_encrypted_ref=signer_ref,
                to_address=rec_contract.address,
                contract_function_call=fn_call,
                business_entity_type="CERTIFICATE",
                business_entity_id=certificate_id,
                idempotency_key=f"REC_ISSUE:{certificate_id}",
            )

            return TxReceipt(
                reference=tx_rec.tx_hash or "0x",
                operation="issue_certificate",
                confirmed=tx_rec.status == "CONFIRMED",
                block_number=tx_rec.block_number or 0,
                payload_hash=meter_bytes.hex(),
            )

        return await self._run_in_session(_call)

    async def transfer_certificate(self, certificate_id: str, from_owner: str, to_owner: str) -> TxReceipt:
        async def _call(session: AsyncSession) -> TxReceipt:
            rec_contract = self.get_contract("RenewableEnergyCertificate")
            signer_ref = self._ensure_signer_ref()

            cert_bytes = _to_bytes32(certificate_id)
            to_addr = to_checksum_address(to_owner) if to_owner.startswith("0x") else self.key_manager.get_address_from_ref(signer_ref)

            fn_call = rec_contract.functions.transferCertificate(cert_bytes, to_addr)

            tx_rec = await self.tx_manager.execute_transaction(
                db=session,
                from_encrypted_ref=signer_ref,
                to_address=rec_contract.address,
                contract_function_call=fn_call,
                business_entity_type="CERTIFICATE_TRANSFER",
                business_entity_id=certificate_id,
                idempotency_key=f"REC_TRANSFER:{certificate_id}:{to_addr}",
            )

            return TxReceipt(
                reference=tx_rec.tx_hash or "0x",
                operation="transfer_certificate",
                confirmed=tx_rec.status == "CONFIRMED",
                block_number=tx_rec.block_number or 0,
                payload_hash=cert_bytes.hex(),
            )

        return await self._run_in_session(_call)

    async def retire_certificate(self, certificate_id: str, owner: str) -> TxReceipt:
        async def _call(session: AsyncSession) -> TxReceipt:
            rec_contract = self.get_contract("RenewableEnergyCertificate")
            signer_ref = self._ensure_signer_ref()
            cert_bytes = _to_bytes32(certificate_id)

            fn_call = rec_contract.functions.retireCertificate(cert_bytes, "Renewable Energy Claim")

            tx_rec = await self.tx_manager.execute_transaction(
                db=session,
                from_encrypted_ref=signer_ref,
                to_address=rec_contract.address,
                contract_function_call=fn_call,
                business_entity_type="CERTIFICATE_RETIRE",
                business_entity_id=certificate_id,
                idempotency_key=f"REC_RETIRE:{certificate_id}",
            )

            return TxReceipt(
                reference=tx_rec.tx_hash or "0x",
                operation="retire_certificate",
                confirmed=tx_rec.status == "CONFIRMED",
                block_number=tx_rec.block_number or 0,
                payload_hash=cert_bytes.hex(),
            )

        return await self._run_in_session(_call)

    async def verify_transaction(self, reference: str) -> bool:
        if not reference or not reference.startswith("0x"):
            return False
        try:
            receipt = await self.w3.eth.get_transaction_receipt(reference)
            return receipt is not None and receipt.get("status") == 1
        except TransactionNotFound:
            return False
        except Exception:
            return False
