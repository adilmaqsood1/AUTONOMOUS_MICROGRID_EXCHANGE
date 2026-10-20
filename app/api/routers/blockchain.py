"""Blockchain router: status, contract addresses, transactions, reconciliation,
and on-chain entity queries.
"""
from __future__ import annotations

import uuid
from typing import Any

from eth_utils import keccak, to_checksum_address
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import AsyncWeb3
from web3.exceptions import TransactionNotFound

from app.api.deps import get_current_user, get_db
from app.blockchain.contracts import get_contract_abi, get_deployed_addresses
from app.blockchain.gateway import get_gateway
from app.blockchain.reconciliation import ReconciliationService
from app.core.config import get_settings
from app.models.blockchain import BlockchainTransaction
from app.models.core import User

router = APIRouter(prefix="/blockchain", tags=["Blockchain"])


class RegisterParticipantOnChainRequest(BaseModel):
    wallet_address: str
    participant_id: str
    role_name: str


class RegisterAssetOnChainRequest(BaseModel):
    asset_id: str
    owner_address: str
    asset_type: str
    meter_id: str
    renewable: bool


class RegisterMeterOnChainRequest(BaseModel):
    meter_id: str
    asset_id: str
    owner_address: str


@router.get("/status")
async def get_blockchain_status():
    """Retrieve operational status, network connectivity, and deployed contracts."""
    settings = get_settings()
    contracts = get_deployed_addresses()

    if settings.blockchain_mode == "mock":
        return {
            "mode": "mock",
            "connected": True,
            "chain_id": settings.evm_chain_id,
            "latest_block": 100,
            "contracts": contracts,
            "indexer_synced": True,
        }

    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(settings.evm_rpc_url))
    try:
        is_connected = await w3.is_connected()
        latest_block = await w3.eth.block_number if is_connected else 0
        chain_id = await w3.eth.chain_id if is_connected else settings.evm_chain_id
        return {
            "mode": "evm",
            "connected": is_connected,
            "chain_id": chain_id,
            "latest_block": latest_block,
            "contracts": contracts,
            "indexer_synced": True,
        }
    except Exception as e:
        return {
            "mode": "evm",
            "connected": False,
            "chain_id": settings.evm_chain_id,
            "latest_block": 0,
            "contracts": contracts,
            "indexer_synced": False,
            "error": str(e),
        }


@router.get("/contracts")
async def get_contracts_info():
    """List deployed smart contracts and their configured addresses."""
    return get_deployed_addresses()


@router.get("/transactions")
async def list_transactions(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    entity_type: str | None = None,
    current_user: User = Depends(get_current_user),
):
    """Query recent persistent blockchain transactions."""
    stmt = select(BlockchainTransaction).order_by(BlockchainTransaction.created_at.desc()).limit(limit)
    if entity_type:
        stmt = stmt.where(BlockchainTransaction.business_entity_type == entity_type)
    txs = (await db.execute(stmt)).scalars().all()
    return txs


@router.get("/transactions/{tx_hash}")
async def get_transaction(
    tx_hash: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retrieve transaction execution details and receipt."""
    stmt = select(BlockchainTransaction).where(BlockchainTransaction.tx_hash == tx_hash)
    tx_rec = (await db.execute(stmt)).scalar_one_or_none()
    if tx_rec:
        return tx_rec

    # Query directly from node if not in database
    settings = get_settings()
    if settings.blockchain_mode == "evm":
        w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(settings.evm_rpc_url))
        try:
            receipt = await w3.eth.get_transaction_receipt(tx_hash)
            return dict(receipt)
        except TransactionNotFound:
            pass

    raise HTTPException(status_code=404, detail="Transaction not found")


@router.post("/reconcile")
async def trigger_reconciliation(
    repair: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trigger on-demand PostgreSQL <-> Blockchain state reconciliation."""
    service = ReconciliationService()
    result = await service.reconcile_all(db, repair=repair)
    await db.commit()
    return result


@router.post("/participants/register")
async def register_participant_on_chain(
    req: RegisterParticipantOnChainRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Register participant identity on MarketRegistry smart contract."""
    gw = get_gateway()
    if hasattr(gw, "get_contract") and hasattr(gw, "_operator_ref"):
        registry = gw.get_contract("MarketRegistry")
        fn = registry.functions.registerParticipant(
            to_checksum_address(req.wallet_address),
            req.participant_id,
            req.role_name,
        )
        tx_rec = await gw.tx_manager.execute_transaction(
            db=db,
            from_encrypted_ref=gw._ensure_signer_ref(),
            to_address=registry.address,
            contract_function_call=fn,
            business_entity_type="REGISTRATION",
            business_entity_id=req.participant_id,
            idempotency_key=f"REG_PARTICIPANT:{req.participant_id}",
        )
        await db.commit()
        return {"status": tx_rec.status, "tx_hash": tx_rec.tx_hash}
    return {"status": "mock_registered", "participant_id": req.participant_id}


@router.get("/commitments/{commitment_id}")
async def get_commitment_on_chain(
    commitment_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch on-chain state of an energy commitment from EnergySettlement contract."""
    gw = get_gateway()
    if hasattr(gw, "get_contract"):
        settlement = gw.get_contract("EnergySettlement")
        cid_bytes = bytes.fromhex(commitment_id[2:]) if commitment_id.startswith("0x") and len(commitment_id) == 66 else keccak(text=commitment_id)
        data = await settlement.functions.getCommitment(cid_bytes).call()
        return {
            "commitment_id": commitment_id,
            "buyer": data[1],
            "seller": data[2],
            "quantity_wh": data[5],
            "price_per_wh": data[6],
            "escrow_amount": data[9],
            "collateral_amount": data[10],
            "delivered_wh": data[11],
            "status": data[12] if len(data) > 12 else 0,
        }
    return {"commitment_id": commitment_id, "mode": "mock", "status": "ACTIVE"}


@router.get("/certificates/{cert_id}")
async def get_certificate_on_chain(
    cert_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch on-chain state of an EAC from RenewableEnergyCertificate contract."""
    gw = get_gateway()
    if hasattr(gw, "get_contract"):
        rec = gw.get_contract("RenewableEnergyCertificate")
        cert_bytes = bytes.fromhex(cert_id[2:]) if cert_id.startswith("0x") and len(cert_id) == 66 else keccak(text=cert_id)
        data = await rec.functions.getCertificate(cert_bytes).call()
        return {
            "certificate_id": cert_id,
            "generator_asset_id": data[1].hex(),
            "source_type": data[2],
            "quantity_wh": data[3],
            "owner": data[7],
            "status": data[8],
            "retired_at": data[10],
            "purpose": data[11],
        }
    return {"certificate_id": cert_id, "mode": "mock", "status": "ISSUED"}
