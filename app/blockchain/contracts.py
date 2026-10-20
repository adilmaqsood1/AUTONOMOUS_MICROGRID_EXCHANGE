"""Helpers for loading smart contract ABIs, bytecodes, and deployed addresses."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import get_settings

_ARTIFACTS_FILE = Path(__file__).resolve().parent.parent.parent / "blockchain" / "artifacts" / "contract_artifacts.json"
_DEPLOYED_FILE = Path(__file__).resolve().parent.parent.parent / "blockchain" / "deployed_addresses.json"

_CACHED_ARTIFACTS: dict[str, Any] | None = None


def get_artifacts_manifest() -> dict[str, Any]:
    global _CACHED_ARTIFACTS
    if _CACHED_ARTIFACTS is None:
        if not _ARTIFACTS_FILE.exists():
            raise FileNotFoundError(f"Smart contract artifacts not found at {_ARTIFACTS_FILE}. Run node compile.js first.")
        _CACHED_ARTIFACTS = json.loads(_ARTIFACTS_FILE.read_text(encoding="utf-8"))
    return _CACHED_ARTIFACTS


def get_contract_abi(contract_name: str) -> list[dict[str, Any]]:
    manifest = get_artifacts_manifest()
    if contract_name not in manifest:
        raise KeyError(f"Contract {contract_name} not found in artifacts manifest.")
    return manifest[contract_name]["abi"]


def get_contract_bytecode(contract_name: str) -> str:
    manifest = get_artifacts_manifest()
    if contract_name not in manifest:
        raise KeyError(f"Contract {contract_name} not found in artifacts manifest.")
    return manifest[contract_name]["bytecode"]


def get_deployed_addresses() -> dict[str, str]:
    """Retrieve deployed contract addresses from environment or deployed_addresses.json."""
    settings = get_settings()
    addresses: dict[str, str] = {
        "GridUSD": settings.grid_usd_address,
        "MarketRegistry": settings.market_registry_address,
        "EnergySettlement": settings.energy_settlement_address,
        "DeliveryOracle": settings.delivery_oracle_address,
        "RenewableEnergyCertificate": settings.eac_contract_address,
        "ReserveSettlement": settings.reserve_settlement_address,
    }

    # If any address is empty and deployed_addresses.json exists, fall back to file
    if not all(addresses.values()) and _DEPLOYED_FILE.exists():
        try:
            deployed = json.loads(_DEPLOYED_FILE.read_text(encoding="utf-8"))
            file_contracts = deployed.get("contracts", {})
            for name in list(addresses.keys()):
                if not addresses[name] and name in file_contracts:
                    addresses[name] = file_contracts[name]
        except Exception:
            pass

    return addresses
