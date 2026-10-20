"""Pure settlement mathematics (Decimal only). No LLM or float arithmetic touches money."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

MONEY_Q = Decimal("0.00000001")
QTY_Q = Decimal("0.000001")
ZERO = Decimal("0")


@dataclass(frozen=True)
class SettlementAmounts:
    committed_kwh: Decimal
    delivered_kwh: Decimal       # paid-for delivery, capped at committed
    shortfall_kwh: Decimal
    delivery_ratio: Decimal
    gross_amount: Decimal        # committed * price
    seller_payment: Decimal      # delivered * price
    buyer_refund: Decimal        # undelivered notional returned to buyer
    penalty: Decimal             # seller imbalance penalty (<= collateral)
    collateral_release: Decimal  # collateral returned to seller after penalty
    status: str                  # DELIVERED | PARTIAL | FAILED


def money(x: Decimal) -> Decimal:
    return x.quantize(MONEY_Q, rounding=ROUND_HALF_EVEN)


def delivery_status(ratio: Decimal, full_tolerance: Decimal = Decimal("0.99")) -> str:
    if ratio >= full_tolerance:
        return "DELIVERED"
    if ratio <= ZERO:
        return "FAILED"
    return "PARTIAL"


def compute_settlement(committed: Decimal, metered_delivery: Decimal, price: Decimal,
                       collateral_locked: Decimal, penalty_multiplier: Decimal) -> SettlementAmounts:
    """seller_payment + buyer_refund == gross; penalty <= collateral; nothing paid without metered energy."""
    if committed <= 0:
        raise ValueError("committed quantity must be positive")
    delivered = min(max(metered_delivery, ZERO), committed).quantize(QTY_Q)
    shortfall = (committed - delivered).quantize(QTY_Q)
    ratio = (delivered / committed)
    gross = money(committed * price)
    payment = money(delivered * price)
    refund = money(gross - payment)
    penalty = min(money(shortfall * price * penalty_multiplier), collateral_locked)
    return SettlementAmounts(committed, delivered, shortfall, ratio, gross, payment, refund, penalty,
                             money(collateral_locked - penalty), delivery_status(ratio))
