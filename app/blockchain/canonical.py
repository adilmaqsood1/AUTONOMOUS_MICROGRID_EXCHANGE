"""Canonical Evidence Hashing Specification for Transactive Microgrid Deliveries.

Specification:
1. Keys are strictly sorted alphabetically:
   - "committed_wh" (integer Wh)
   - "commitment_id" (canonical string)
   - "delivered_wh" (integer Wh)
   - "end_time" (ISO 8601 UTC string with Z suffix)
   - "meter_id" (canonical string)
   - "start_time" (ISO 8601 UTC string with Z suffix)
2. No extraneous whitespace: compact JSON serialization (separators=(',', ':')).
3. UTF-8 byte encoding.
4. Hash function: Keccak-256 (EVM native bytes32 standard) or SHA-256 with 0x prefix.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from eth_utils import keccak


def canonicalize_evidence_dict(
    commitment_id: str,
    meter_id: str,
    committed_wh: int,
    delivered_wh: int,
    start_time_iso: str,
    end_time_iso: str,
) -> str:
    """Produce deterministic canonical JSON string."""
    payload = {
        "commitment_id": str(commitment_id).strip(),
        "committed_wh": int(committed_wh),
        "delivered_wh": int(delivered_wh),
        "end_time": str(end_time_iso).strip(),
        "meter_id": str(meter_id).strip(),
        "start_time": str(start_time_iso).strip(),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def compute_canonical_evidence_hash(
    commitment_id: str,
    meter_id: str,
    committed_wh: int,
    delivered_wh: int,
    start_time_iso: str,
    end_time_iso: str,
) -> str:
    """Compute 0x-prefixed Keccak-256 digest of canonicalized evidence payload."""
    canonical_json = canonicalize_evidence_dict(
        commitment_id=commitment_id,
        meter_id=meter_id,
        committed_wh=committed_wh,
        delivered_wh=delivered_wh,
        start_time_iso=start_time_iso,
        end_time_iso=end_time_iso,
    )
    digest_bytes = keccak(text=canonical_json)
    return "0x" + digest_bytes.hex()
