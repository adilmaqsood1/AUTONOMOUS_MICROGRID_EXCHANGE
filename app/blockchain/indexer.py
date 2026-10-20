"""Blockchain Event Indexer: watches on-chain contract events and updates local PostgreSQL models idempotently."""
from __future__ import annotations

import logging
from typing import Any

from eth_utils import to_checksum_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import AsyncWeb3

from app.blockchain.contracts import get_contract_abi, get_deployed_addresses
from app.core.config import get_settings
from app.models.blockchain import BlockchainTransaction
from app.models.ops import EnergyCommitment, RenewableCertificate, ReserveCommitment

logger = logging.getLogger(__name__)


class BlockchainEventIndexer:
    """Indexes contract events with confirmation depth and idempotency."""

    def __init__(self, w3: AsyncWeb3 | None = None) -> None:
        settings = get_settings()
        self.w3 = w3 or AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(settings.evm_rpc_url))
        self.confirmations_required = settings.block_confirmations_required
        self.deployed_addresses = get_deployed_addresses()
        self.last_indexed_block = 0
        self._processed_logs: set[str] = set()  # tx_hash:log_index

    async def index_events_range(
        self,
        db: AsyncSession,
        from_block: int | None = None,
        to_block: int | None = None,
    ) -> dict[str, int]:
        """Index events across all deployed contracts up to (latest - confirmations_required)."""
        try:
            if not await self.w3.is_connected():
                return {"indexed_events": 0, "status": "node_offline"}
            latest_block = await self.w3.eth.block_number
        except Exception:
            return {"indexed_events": 0, "status": "node_offline"}

        safe_head = max(0, latest_block - self.confirmations_required)

        start = from_block if from_block is not None else self.last_indexed_block + 1
        end = to_block if to_block is not None else safe_head

        if start > end:
            return {"indexed_events": 0, "from_block": start, "to_block": end}

        event_counts = {
            "settlements": 0,
            "certificates": 0,
            "reserves": 0,
        }

        # 1. EnergySettlement events
        settlement_addr = self.deployed_addresses.get("EnergySettlement")
        if settlement_addr:
            c = self.w3.eth.contract(
                address=to_checksum_address(settlement_addr),
                abi=get_contract_abi("EnergySettlement"),
            )
            # Settlement events
            try:
                events = await c.events.CommitmentSettled().get_logs(from_block=start, to_block=end)
                for ev in events:
                    log_key = f"{ev.transactionHash.hex()}:{ev.logIndex}"
                    if log_key not in self._processed_logs:
                        self._processed_logs.add(log_key)
                        cid_hex = ev.args.commitmentId.hex()
                        # Update EnergyCommitment in DB
                        stmt = select(EnergyCommitment).where(EnergyCommitment.blockchain_reference == f"0x{cid_hex}")
                        comm = (await db.execute(stmt)).scalar_one_or_none()
                        if comm:
                            comm.status = "SETTLED"
                        event_counts["settlements"] += 1
            except Exception as e:
                logger.warning("Error indexing EnergySettlement events: %s", e)

        # 2. RenewableEnergyCertificate events
        rec_addr = self.deployed_addresses.get("RenewableEnergyCertificate")
        if rec_addr:
            c = self.w3.eth.contract(
                address=to_checksum_address(rec_addr),
                abi=get_contract_abi("RenewableEnergyCertificate"),
            )
            try:
                retire_events = await c.events.CertificateRetired().get_logs(from_block=start, to_block=end)
                for ev in retire_events:
                    log_key = f"{ev.transactionHash.hex()}:{ev.logIndex}"
                    if log_key not in self._processed_logs:
                        self._processed_logs.add(log_key)
                        cert_hex = ev.args.certificateId.hex()
                        stmt = select(RenewableCertificate).where(RenewableCertificate.blockchain_reference == f"0x{cert_hex}")
                        cert = (await db.execute(stmt)).scalar_one_or_none()
                        if cert:
                            cert.retired = True
                        event_counts["certificates"] += 1
            except Exception as e:
                logger.warning("Error indexing REC events: %s", e)

        self.last_indexed_block = end
        await db.flush()
        return event_counts
