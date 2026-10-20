"""PostgreSQL <-> Blockchain Reconciliation Service.
Compares local read models against authoritative on-chain state, detects discrepancies,
safely updates the database, and records audit trail events.
"""
from __future__ import annotations

import logging
from typing import Any

from eth_utils import keccak, to_checksum_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import AsyncWeb3

from app.blockchain.contracts import get_contract_abi, get_deployed_addresses
from app.core.config import get_settings
from app.core.timeutil import utcnow
from app.models.blockchain import ParticipantWallet
from app.models.ops import AuditEvent, EnergyCommitment, RenewableCertificate, ReserveCommitment

logger = logging.getLogger(__name__)

# Map Solidity CommitmentStatus enum to DB string
SOL_TO_DB_STATUS = {
    0: "CREATED",
    1: "FUNDED",
    2: "ACTIVE",
    3: "DELIVERY_REPORTED",
    4: "SETTLED",
    5: "PARTIAL",
    6: "FAILED",
    7: "CANCELLED",
    8: "DISPUTED",
}


class ReconciliationService:
    """Reconciles off-chain PostgreSQL state with authoritative on-chain contracts."""

    def __init__(self, w3: AsyncWeb3 | None = None) -> None:
        settings = get_settings()
        self.w3 = w3 or AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(settings.evm_rpc_url))
        self.deployed_addresses = get_deployed_addresses()

    async def reconcile_all(self, db: AsyncSession, repair: bool = True) -> dict[str, Any]:
        """Perform end-to-end reconciliation across commitments and certificates."""
        try:
            if not await self.w3.is_connected():
                return {
                    "discrepancies_found": 0,
                    "repairs_applied": 0,
                    "discrepancies": [],
                    "status": "node_offline",
                    "timestamp": utcnow().isoformat(),
                }
        except Exception:
            return {
                "discrepancies_found": 0,
                "repairs_applied": 0,
                "discrepancies": [],
                "status": "node_offline",
                "timestamp": utcnow().isoformat(),
            }

        discrepancies: list[dict[str, Any]] = []
        repairs_applied = 0

        # 1. Reconcile Energy Commitments
        settlement_addr = self.deployed_addresses.get("EnergySettlement")
        if settlement_addr:
            contract = self.w3.eth.contract(
                address=to_checksum_address(settlement_addr),
                abi=get_contract_abi("EnergySettlement"),
            )
            stmt = select(EnergyCommitment)
            commitments = (await db.execute(stmt)).scalars().all()

            for comm in commitments:
                ref = comm.blockchain_reference
                if not ref:
                    continue
                cid_bytes = bytes.fromhex(ref[2:]) if ref.startswith("0x") and len(ref) == 66 else keccak(text=ref)
                try:
                    on_chain_data = await contract.functions.getCommitment(cid_bytes).call()
                    on_chain_status_code = on_chain_data[9] if len(on_chain_data) > 9 else None
                    if on_chain_status_code is not None:
                        on_chain_status = SOL_TO_DB_STATUS.get(on_chain_status_code, "UNKNOWN")
                        if comm.status != on_chain_status:
                            disc = {
                                "entity_type": "EnergyCommitment",
                                "id": str(comm.id),
                                "reference": ref,
                                "db_status": comm.status,
                                "chain_status": on_chain_status,
                            }
                            discrepancies.append(disc)

                            if repair:
                                old_status = comm.status
                                comm.status = on_chain_status
                                repairs_applied += 1
                                # Audit event
                                audit = AuditEvent(
                                    event_type="RECONCILIATION_REPAIR",
                                    actor_type="SYSTEM",
                                    actor_id="reconciliation_service",
                                    resource_type="EnergyCommitment",
                                    resource_id=str(comm.id),
                                    description=f"Repaired commitment status from {old_status} to on-chain {on_chain_status}",
                                    payload=disc,
                                )
                                db.add(audit)
                except Exception as e:
                    logger.debug("Commitment %s not found on-chain: %s", ref, e)

        # 2. Reconcile Renewable Certificates
        rec_addr = self.deployed_addresses.get("RenewableEnergyCertificate")
        if rec_addr:
            contract = self.w3.eth.contract(
                address=to_checksum_address(rec_addr),
                abi=get_contract_abi("RenewableEnergyCertificate"),
            )
            stmt = select(RenewableCertificate)
            certs = (await db.execute(stmt)).scalars().all()

            for cert in certs:
                ref = cert.blockchain_reference
                if not ref:
                    continue
                cert_bytes = bytes.fromhex(ref[2:]) if ref.startswith("0x") and len(ref) == 66 else keccak(text=ref)
                try:
                    is_retired_on_chain = await contract.functions.isRetired(cert_bytes).call()
                    if cert.retired != is_retired_on_chain:
                        disc = {
                            "entity_type": "RenewableCertificate",
                            "id": str(cert.id),
                            "reference": ref,
                            "db_retired": cert.retired,
                            "chain_retired": is_retired_on_chain,
                        }
                        discrepancies.append(disc)

                        if repair:
                            cert.retired = is_retired_on_chain
                            repairs_applied += 1
                            audit = AuditEvent(
                                event_type="RECONCILIATION_REPAIR",
                                actor_type="SYSTEM",
                                actor_id="reconciliation_service",
                                resource_type="RenewableCertificate",
                                resource_id=str(cert.id),
                                description=f"Repaired certificate retired status to on-chain {is_retired_on_chain}",
                                payload=disc,
                            )
                            db.add(audit)
                except Exception as e:
                    logger.debug("Certificate %s not found on-chain: %s", ref, e)

        if repair and repairs_applied > 0:
            await db.flush()

        return {
            "discrepancies_found": len(discrepancies),
            "repairs_applied": repairs_applied,
            "discrepancies": discrepancies,
            "timestamp": utcnow().isoformat(),
        }
