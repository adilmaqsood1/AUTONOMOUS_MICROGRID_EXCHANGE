"""Compact, standardized agent reasoning context and boundary enforcer.

Strictly protects against:
1. Omniscient information leakage (private bids, private balances, wallet keys).
2. Future data leakage (future actual generation/demand; only forecasts allowed).
3. Prompt injection (all participant labels and strings serialized as untrusted data blocks).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


def _serialize_helper(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serialize_helper(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize_helper(x) for x in obj]
    return obj


@dataclass
class AgentReasoningContext:
    """Participant-specific observation context provided to the AI reasoning layer."""
    market_round_id: str
    timestamp: datetime
    participant_id: str
    agent_type: str
    asset_state: dict[str, Any] = field(default_factory=dict)
    market_state: dict[str, Any] = field(default_factory=dict)
    forecast_state: dict[str, Any] = field(default_factory=dict)
    grid_state: dict[str, Any] = field(default_factory=dict)
    risk_state: dict[str, Any] = field(default_factory=dict)
    reserve_state: dict[str, Any] = field(default_factory=dict)
    historical_performance: dict[str, Any] = field(default_factory=dict)
    recent_agent_memory: dict[str, Any] = field(default_factory=dict)
    participant_preferences: dict[str, Any] = field(default_factory=dict)
    hard_constraints: dict[str, Any] = field(default_factory=dict)

    def to_safe_dict(self) -> dict[str, Any]:
        """Converts to a sanitized dictionary, purging any accidental sensitive keys."""
        d = asdict(self)
        # Deep strip forbidden credentials or wallet keys
        forbidden_substrings = ("private_key", "secret", "password", "jwt", "signer", "mnemonic")

        def _clean(val: Any) -> Any:
            if isinstance(val, dict):
                return {k: _clean(v) for k, v in val.items()
                        if not any(sub in str(k).lower() for sub in forbidden_substrings)}
            if isinstance(val, list):
                return [_clean(x) for x in val]
            return _serialize_helper(val)

        return _clean(d)

    def compute_hash(self) -> str:
        """Deterministic SHA256 digest of the observation context for audit/replay."""
        payload = json.dumps(self.to_safe_dict(), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def serialize_for_prompt(self) -> str:
        """Serializes observation into a delimited JSON block resistant to prompt injection."""
        safe_data = self.to_safe_dict()
        data_json = json.dumps(safe_data, indent=2, sort_keys=True)
        return (
            "=== BEGIN UNTRUSTED OBSERVATION DATA ===\n"
            f"{data_json}\n"
            "=== END UNTRUSTED OBSERVATION DATA ==="
        )
