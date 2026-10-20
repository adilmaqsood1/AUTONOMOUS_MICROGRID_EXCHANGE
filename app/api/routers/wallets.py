"""Wallets router: wallet creation, external challenge-response verification,
and secure participant address querying.

NEVER returns or exposes private keys.
"""
from __future__ import annotations

import datetime
import uuid
from typing import Any

from eth_utils import to_checksum_address
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import AsyncWeb3

from app.api.deps import get_current_user, get_db
from app.blockchain.contracts import get_deployed_addresses
from app.blockchain.keys import (
    EncryptedLocalKeyManager,
    generate_wallet_challenge_nonce,
    verify_wallet_signature,
)
from app.core.config import get_settings
from app.core.timeutil import utcnow
from app.models.blockchain import ParticipantWallet, WalletChallenge
from app.models.core import Participant, User

router = APIRouter(prefix="/wallets", tags=["Wallets"])
dev_funding_router = APIRouter(prefix="/blockchain/dev", tags=["Blockchain Development"])


# Pydantic Schemas - strictly omit private keys
class CreateWalletRequest(BaseModel):
    participant_id: uuid.UUID
    wallet_type: str = Field(default="MANAGED_DEV")  # MANAGED_DEV | EXTERNAL
    external_address: str | None = None


class WalletResponse(BaseModel):
    id: uuid.UUID
    participant_id: uuid.UUID
    address: str
    wallet_type: str
    chain_id: int
    status: str
    created_at: datetime.datetime


class ChallengeRequest(BaseModel):
    participant_id: uuid.UUID
    address: str


class ChallengeResponse(BaseModel):
    challenge_id: uuid.UUID
    nonce: str
    message: str
    expires_at: datetime.datetime


class VerifyChallengeRequest(BaseModel):
    challenge_id: uuid.UUID
    signature: str


class FundWalletRequest(BaseModel):
    address: str
    amount_eth: float = 10.0


@router.post("", response_model=WalletResponse, status_code=status.HTTP_201_CREATED)
async def create_wallet(
    req: CreateWalletRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a managed dev wallet or register an external unverified wallet."""
    # Verify participant exists
    part = await db.get(Participant, req.participant_id)
    if not part:
        raise HTTPException(status_code=404, detail="Participant not found")

    settings = get_settings()
    key_manager = EncryptedLocalKeyManager(settings.master_key_secret)

    if req.wallet_type == "MANAGED_DEV":
        address, encrypted_ref = key_manager.create_key_pair()
        wallet = ParticipantWallet(
            participant_id=req.participant_id,
            address=to_checksum_address(address),
            encrypted_private_key=encrypted_ref,
            wallet_type="MANAGED_DEV",
            chain_id=settings.evm_chain_id,
            status="ACTIVE",
        )
    elif req.wallet_type == "EXTERNAL":
        if not req.external_address:
            raise HTTPException(status_code=400, detail="external_address required for EXTERNAL wallet")
        wallet = ParticipantWallet(
            participant_id=req.participant_id,
            address=to_checksum_address(req.external_address),
            encrypted_private_key=None,
            wallet_type="EXTERNAL",
            chain_id=settings.evm_chain_id,
            status="UNVERIFIED",
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported wallet type: {req.wallet_type}")

    db.add(wallet)
    await db.commit()
    await db.refresh(wallet)
    return wallet


@router.get("/{participant_id}", response_model=list[WalletResponse])
async def get_participant_wallets(
    participant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get wallets linked to a participant."""
    stmt = select(ParticipantWallet).where(ParticipantWallet.participant_id == participant_id)
    wallets = (await db.execute(stmt)).scalars().all()
    return wallets


@router.post("/challenge", response_model=ChallengeResponse)
async def request_wallet_challenge(
    req: ChallengeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Generate a challenge nonce to prove external wallet ownership via signature."""
    checksum_addr = to_checksum_address(req.address)
    nonce = generate_wallet_challenge_nonce()
    expires_at = utcnow() + datetime.timedelta(minutes=10)
    message = (
        f"Microgrid Transactive Exchange Authentication\n"
        f"Participant: {req.participant_id}\n"
        f"Wallet: {checksum_addr}\n"
        f"Nonce: {nonce}\n"
        f"Expires: {expires_at.isoformat()}"
    )

    challenge = WalletChallenge(
        participant_id=req.participant_id,
        address=checksum_addr,
        nonce=nonce,
        message=message,
        expires_at=expires_at,
        used=False,
    )
    db.add(challenge)
    await db.commit()
    await db.refresh(challenge)

    return ChallengeResponse(
        challenge_id=challenge.id,
        nonce=challenge.nonce,
        message=challenge.message,
        expires_at=challenge.expires_at,
    )


@router.post("/verify")
async def verify_wallet_challenge(
    req: VerifyChallengeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Verify signed challenge nonce and mark wallet as ACTIVE."""
    challenge = await db.get(WalletChallenge, req.challenge_id)
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    if challenge.used:
        raise HTTPException(status_code=400, detail="Challenge already used")
    if utcnow() > challenge.expires_at:
        raise HTTPException(status_code=400, detail="Challenge expired")

    valid = verify_wallet_signature(
        address=challenge.address,
        message=challenge.message,
        signature_hex=req.signature,
    )
    if not valid:
        raise HTTPException(status_code=401, detail="Cryptographic signature verification failed")

    challenge.used = True

    # Link or verify wallet in participant_wallets
    stmt = select(ParticipantWallet).where(
        ParticipantWallet.participant_id == challenge.participant_id,
        ParticipantWallet.address == challenge.address,
    )
    wallet = (await db.execute(stmt)).scalar_one_or_none()
    if not wallet:
        wallet = ParticipantWallet(
            participant_id=challenge.participant_id,
            address=challenge.address,
            wallet_type="EXTERNAL",
            chain_id=get_settings().evm_chain_id,
            status="ACTIVE",
        )
        db.add(wallet)
    else:
        wallet.status = "ACTIVE"

    await db.commit()
    return {"verified": True, "address": challenge.address, "status": "ACTIVE"}


@dev_funding_router.post("/fund-wallet")
async def fund_wallet_dev(
    req: FundWalletRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Development-only endpoint to fund a test wallet with local node ETH. Disabled in production."""
    settings = get_settings()
    if settings.environment.lower() != "development":
        raise HTTPException(status_code=403, detail="Endpoint only available in development environment")

    checksum_addr = to_checksum_address(req.address)
    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(settings.evm_rpc_url))

    try:
        accounts = await w3.eth.accounts
        if not accounts:
            return {"status": "skipped", "reason": "No node unlocked accounts"}

        sender = accounts[0]
        tx_hash = await w3.eth.send_transaction({
            "from": sender,
            "to": checksum_addr,
            "value": w3.to_wei(req.amount_eth, "ether"),
        })
        return {
            "status": "funded",
            "recipient": checksum_addr,
            "tx_hash": tx_hash.hex(),
            "amount_eth": req.amount_eth,
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}
