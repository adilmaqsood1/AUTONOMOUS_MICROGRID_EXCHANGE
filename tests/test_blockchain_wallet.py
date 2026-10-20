"""Unit tests for Phase 3 KeyManager, Wallet Challenge-Response, and Security Assertions."""
from __future__ import annotations

import datetime
import uuid
import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from httpx import ASGITransport, AsyncClient

from app.blockchain.canonical import compute_canonical_evidence_hash
from app.blockchain.keys import (
    EncryptedLocalKeyManager,
    TestKeyManager,
    generate_wallet_challenge_nonce,
    verify_wallet_signature,
)
from app.core.timeutil import utcnow
from app.main import app
from app.models.blockchain import ParticipantWallet


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def test_encrypted_local_key_manager_lifecycle():
    km = EncryptedLocalKeyManager("super_secret_test_master_key_12345!")
    addr, enc_ref = km.create_key_pair()

    assert addr.startswith("0x")
    assert len(addr) == 42
    # Ensure plaintext private key hex is NOT visible in the encrypted reference
    assert not enc_ref.startswith("0x")
    assert "0x" not in enc_ref

    # Derive address back from encrypted reference
    derived_addr = km.get_address_from_ref(enc_ref)
    assert derived_addr.lower() == addr.lower()

    # Sign message
    test_msg = "Hello Microgrid Transactive Exchange!"
    signed_msg = km.sign_message(enc_ref, test_msg)
    recovered = Account.recover_message(encode_defunct(text=test_msg), signature=signed_msg.signature)
    assert recovered.lower() == addr.lower()


def test_test_key_manager_lifecycle():
    km = TestKeyManager()
    addr, ref = km.create_key_pair()
    assert addr.startswith("0x")
    assert km.get_address_from_ref(ref).lower() == addr.lower()

    test_msg = "Hermetic Test Payload"
    signed_msg = km.sign_message(ref, test_msg)
    recovered = Account.recover_message(encode_defunct(text=test_msg), signature=signed_msg.signature)
    assert recovered.lower() == addr.lower()


def test_wallet_challenge_nonce_and_signature_verification():
    nonce = generate_wallet_challenge_nonce()
    assert len(nonce) == 64  # 32 bytes hex

    signer = Account.create()
    message = f"Challenge nonce: {nonce}"

    # Sign message using eth_account
    signable = encode_defunct(text=message)
    signed = signer.sign_message(signable)
    sig_hex = signed.signature.hex()

    # Verification should succeed with correct address
    assert verify_wallet_signature(signer.address, message, sig_hex) is True

    # Verification should fail with wrong address or tampered message
    other = Account.create()
    assert verify_wallet_signature(other.address, message, sig_hex) is False
    assert verify_wallet_signature(signer.address, "Tampered message", sig_hex) is False


def test_canonical_evidence_hashing_determinism():
    cid = "TX_COMMIT_999"
    meter = "METER_SOLAR_01"
    start = "2026-10-07T00:00:00Z"
    end = "2026-10-07T01:00:00Z"

    h1 = compute_canonical_evidence_hash(cid, meter, 10000, 9500, start, end)
    h2 = compute_canonical_evidence_hash(cid, meter, 10000, 9500, start, end)

    assert h1.startswith("0x")
    assert len(h1) == 66  # 32-byte hex hash with 0x prefix
    assert h1 == h2  # Deterministic hash

    # Changing delivery by 1 Wh changes hash
    h3 = compute_canonical_evidence_hash(cid, meter, 10000, 9501, start, end)
    assert h1 != h3


@pytest.mark.asyncio
async def test_wallet_api_endpoints(client: AsyncClient, session, demo):
    login_res = await client.post("/auth/login", json={"email": "operator@greengrid.local", "password": "test-password-123"})
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    part1_id = str(demo["participants"]["HydroOperator"])
    part2_id = str(demo["participants"]["CityHospital"])

    # 1. Create MANAGED_DEV wallet
    resp = await client.post(
        "/wallets",
        json={"participant_id": part1_id, "wallet_type": "MANAGED_DEV"},
        headers=headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "address" in data
    assert data["address"].startswith("0x")
    assert data["wallet_type"] == "MANAGED_DEV"
    assert data["status"] == "ACTIVE"
    # CRITICAL: Verify private key is NEVER in API response
    assert "private_key" not in data
    assert "encrypted_private_key" not in data

    # 2. Get participant wallets
    resp = await client.get(f"/wallets/{part1_id}", headers=headers)
    assert resp.status_code == 200
    wallets = resp.json()
    assert len(wallets) >= 1
    assert wallets[0]["address"] == data["address"]
    assert "private_key" not in wallets[0]

    # 3. External wallet challenge-response flow
    external_account = Account.create()
    resp = await client.post(
        "/wallets/challenge",
        json={"participant_id": part2_id, "address": external_account.address},
    )
    assert resp.status_code == 200
    chal = resp.json()
    assert "challenge_id" in chal
    assert "message" in chal
    assert "nonce" in chal

    # Sign challenge message with external account
    signable = encode_defunct(text=chal["message"])
    signed = external_account.sign_message(signable)

    # Verify signature
    resp = await client.post(
        "/wallets/verify",
        json={"challenge_id": chal["challenge_id"], "signature": signed.signature.hex()},
    )
    assert resp.status_code == 200
    assert resp.json()["verified"] is True
    assert resp.json()["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_blockchain_status_and_contracts(client: AsyncClient, session, demo):
    login_res = await client.post("/auth/login", json={"email": "operator@greengrid.local", "password": "test-password-123"})
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Status endpoint
    resp = await client.get("/blockchain/status")
    assert resp.status_code == 200
    status_data = resp.json()
    assert "mode" in status_data
    assert "connected" in status_data
    assert "contracts" in status_data

    # Contracts endpoint
    resp = await client.get("/blockchain/contracts")
    assert resp.status_code == 200
    contracts = resp.json()
    assert "GridUSD" in contracts
    assert "EnergySettlement" in contracts
    assert "MarketRegistry" in contracts

    # Demo trade endpoint
    demo_trade_resp = await client.post("/simulation/demo/blockchain-trade", headers=headers)
    assert demo_trade_resp.status_code == 200
    demo_trade_data = demo_trade_resp.json()
    assert demo_trade_data["status"] == "completed"
    assert "commitment_id" in demo_trade_data
    assert "steps" in demo_trade_data
    assert "commitment" in demo_trade_data["steps"]
    assert "settlement" in demo_trade_data["steps"]
    assert "MarketRegistry" in contracts
