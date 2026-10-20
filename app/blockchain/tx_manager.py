"""Blockchain Transaction Manager: handles nonces, gas, signing, broadcasting,
confirmation tracking, idempotency, and receipt persistence.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import AsyncWeb3
from web3.exceptions import ContractCustomError, ContractLogicError, TransactionNotFound

from app.blockchain.keys import EncryptedLocalKeyManager, KeyManager
from app.core.config import get_settings
from app.core.timeutil import utcnow
from app.models.blockchain import BlockchainTransaction

logger = logging.getLogger(__name__)


class TransactionManager:
    """Manages the full lifecycle of EVM transactions with strict nonce ordering,

    database persistence, idempotency checks, and receipt confirmation.
    """

    def __init__(
        self,
        w3: AsyncWeb3,
        key_manager: KeyManager | None = None,
        chain_id: int | None = None,
    ) -> None:
        self.w3 = w3
        self.key_manager = key_manager or EncryptedLocalKeyManager()
        self.chain_id = chain_id or get_settings().evm_chain_id
        self._nonce_locks: dict[str, asyncio.Lock] = {}

    def _get_nonce_lock(self, address: str) -> asyncio.Lock:
        addr = address.lower()
        if addr not in self._nonce_locks:
            self._nonce_locks[addr] = asyncio.Lock()
        return self._nonce_locks[addr]

    async def execute_transaction(
        self,
        db: AsyncSession,
        from_encrypted_ref: str,
        to_address: str,
        contract_function_call: Any,
        business_entity_type: str,
        business_entity_id: str,
        idempotency_key: str,
        value_wei: int = 0,
        confirmations: int = 1,
        timeout_seconds: float = 30.0,
    ) -> BlockchainTransaction:
        """Execute a contract call within the transaction manager with idempotency and confirmation tracking."""
        # 1. Check idempotency: if already confirmed or pending
        stmt = select(BlockchainTransaction).where(BlockchainTransaction.idempotency_key == idempotency_key)
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing:
            if existing.status == "CONFIRMED":
                logger.info("Idempotency hit for %s (already CONFIRMED)", idempotency_key)
                return existing
            if existing.status == "SUBMITTED" and existing.tx_hash:
                logger.info("Idempotency hit for %s (waiting for SUBMITTED tx %s)", idempotency_key, existing.tx_hash)
                return await self.wait_for_confirmation(db, existing, timeout_seconds=timeout_seconds)

        from_address = self.key_manager.get_address_from_ref(from_encrypted_ref)
        nonce_lock = self._get_nonce_lock(from_address)

        async with nonce_lock:
            # 2. Derive next nonce
            pending_nonce = await self.w3.eth.get_transaction_count(from_address, "pending")

            # Build transaction dict
            tx_data = contract_function_call.build_transaction({
                "from": from_address,
                "value": value_wei,
                "nonce": pending_nonce,
                "chainId": self.chain_id,
            })

            # Estimate gas with safety margin
            try:
                estimated_gas = await self.w3.eth.estimate_gas(tx_data)
                tx_data["gas"] = int(estimated_gas * 1.25)
            except Exception as e:
                logger.warning("Gas estimation failed (%s); using 1,000,000 fallback", e)
                tx_data["gas"] = 1_000_000

            # Set gas price
            gas_price = await self.w3.eth.gas_price
            tx_data["gasPrice"] = gas_price

            method_name = contract_function_call.fn_name if hasattr(contract_function_call, "fn_name") else "unknown"

            # 3. Create or update BlockchainTransaction record in DB
            tx_record = existing or BlockchainTransaction(
                business_entity_type=business_entity_type,
                business_entity_id=str(business_entity_id),
                chain_id=self.chain_id,
                contract_address=to_address,
                method=method_name,
                from_address=from_address,
                nonce=pending_nonce,
                idempotency_key=idempotency_key,
                status="CREATED",
            )
            if not existing:
                db.add(tx_record)
            await db.flush()

            # 4. Sign transaction
            signed_tx = self.key_manager.sign_transaction(from_encrypted_ref, tx_data)
            tx_hash_hex = signed_tx.hash.hex() if hasattr(signed_tx, "hash") else f"0x{signed_tx.raw_transaction.hex()}"
            if not tx_hash_hex.startswith("0x"):
                tx_hash_hex = f"0x{tx_hash_hex}"

            tx_record.status = "SIGNED"
            tx_record.tx_hash = tx_hash_hex
            await db.flush()

            # 5. Broadcast raw transaction
            raw_tx = signed_tx.raw_transaction if hasattr(signed_tx, "raw_transaction") else signed_tx.rawTransaction
            try:
                await self.w3.eth.send_raw_transaction(raw_tx)
                tx_record.status = "SUBMITTED"
                tx_record.submitted_at = utcnow()
                await db.flush()
            except Exception as e:
                logger.error("Failed to broadcast tx %s: %s", tx_hash_hex, e)
                tx_record.status = "DROPPED"
                tx_record.revert_reason = str(e)[:500]
                await db.flush()
                raise

        # 6. Wait for confirmation outside the nonce lock so other calls can proceed
        return await self.wait_for_confirmation(db, tx_record, timeout_seconds=timeout_seconds)

    async def wait_for_confirmation(
        self,
        db: AsyncSession,
        tx_record: BlockchainTransaction,
        timeout_seconds: float = 30.0,
    ) -> BlockchainTransaction:
        """Poll for transaction receipt and update transaction record with block number and status."""
        if not tx_record.tx_hash:
            raise ValueError("Cannot wait for confirmation on transaction without tx_hash")

        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < timeout_seconds:
            try:
                receipt = await self.w3.eth.get_transaction_receipt(tx_record.tx_hash)
                if receipt is not None:
                    status_int = receipt.get("status", 1)
                    tx_record.block_number = receipt.get("blockNumber")
                    tx_record.confirmed_at = utcnow()
                    tx_record.receipt_data = {
                        "blockNumber": receipt.get("blockNumber"),
                        "gasUsed": receipt.get("gasUsed"),
                        "status": status_int,
                        "transactionHash": tx_record.tx_hash,
                    }

                    if status_int == 1:
                        tx_record.status = "CONFIRMED"
                        tx_record.revert_reason = None
                    else:
                        tx_record.status = "REVERTED"
                        tx_record.revert_reason = "Transaction reverted on-chain"

                    await db.flush()
                    return tx_record
            except TransactionNotFound:
                pass
            except Exception as e:
                logger.warning("Error fetching receipt for %s: %s", tx_record.tx_hash, e)

            await asyncio.sleep(0.5)

        logger.warning("Transaction %s confirmation timed out after %ss", tx_record.tx_hash, timeout_seconds)
        return tx_record
