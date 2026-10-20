"""Agent base types. Agents are pure: observation in, intents (never state changes) out."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.core.enums import AgentAction


@dataclass
class MarketObservation:
    window_start: datetime
    window_end: datetime
    import_price: Decimal           # utility buy-from-grid price, currency/kWh
    export_price: Decimal           # utility feed-in price
    expected_price: Decimal         # expected local clearing price in this window
    expected_peak_price: Decimal    # max expected price in look-ahead horizon
    expected_valley_price: Decimal  # min expected price in look-ahead horizon
    grid_connected: bool = True
    island_mode: bool = False

    @property
    def hours(self) -> float:
        return (self.window_end - self.window_start).total_seconds() / 3600.0


@dataclass
class Intent:
    """An agent's proposal. Becomes an order only after the service validates it."""
    agent: str
    participant_id: str
    asset_id: str | None
    action: AgentAction
    quantity_kwh: Decimal = Decimal("0")
    limit_price: Decimal | None = None
    reason_codes: list[str] = field(default_factory=list)
    inputs: dict = field(default_factory=dict)
    renewable_only: bool = False

    def explanation(self) -> dict:
        return {"decision": self.action.value, "quantity_kwh": float(self.quantity_kwh),
                "reason_codes": self.reason_codes, "inputs": self.inputs}


def D(x) -> Decimal:
    return Decimal(str(round(float(x), 6)))


def q6(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.000001"))
