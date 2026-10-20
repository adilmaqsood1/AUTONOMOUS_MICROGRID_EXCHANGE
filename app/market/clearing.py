"""Deterministic double-auction clearing with pluggable trade validation (grid + risk).

Pure domain code: no DB, no clock, no randomness. Same inputs -> byte-identical output.

Mechanism
---------
* BUY book sorted by (limit price DESC, sequence ASC); SELL book by (price ASC, sequence ASC).
* Walk buyers in priority order; each is matched against sellers in priority order while
  ask <= bid. Each candidate fill is passed to a `TradeValidator`, which may approve less
  than requested (grid congestion, risk). Approved quantities become trades; the shortfall is
  recorded as a rejection with a reason code and matching continues with the next seller
  ("alternative local generation").
* Trade price = midpoint of bid and ask (k = 0.5), so every trade is individually rational.
  The headline clearing price is the volume-weighted average trade price.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Callable, Protocol

PRICE_Q = Decimal("0.000001")
QTY_Q = Decimal("0.000001")


@dataclass
class BookOrder:
    id: str
    participant_id: str
    asset_id: str
    node_id: str
    side: str  # BUY | SELL
    quantity: Decimal
    price: Decimal
    sequence: int
    renewable_only: bool = False
    is_renewable: bool = False  # for SELL orders: asset is renewable
    remaining: Decimal | None = None

    def __post_init__(self):
        if self.remaining is None:
            self.remaining = self.quantity


@dataclass
class ValidationResult:
    approved: Decimal
    code: str = "APPROVED"
    message: str = ""


class TradeValidator(Protocol):
    def __call__(self, buy: BookOrder, sell: BookOrder, quantity: Decimal) -> ValidationResult: ...


@dataclass
class ProposedTrade:
    buy_order_id: str
    sell_order_id: str
    buyer_id: str
    seller_id: str
    buyer_asset_id: str
    seller_asset_id: str
    quantity: Decimal
    price: Decimal
    renewable: bool


@dataclass
class Rejection:
    buy_order_id: str
    sell_order_id: str
    requested: Decimal
    approved: Decimal
    code: str
    message: str


@dataclass
class ClearingResult:
    trades: list[ProposedTrade] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    fills: dict[str, Decimal] = field(default_factory=dict)  # order_id -> filled qty
    matched_quantity: Decimal = Decimal("0")
    clearing_price: Decimal | None = None
    input_hash: str = ""

    def rejected_quantity(self, code: str | None = None) -> Decimal:
        return sum((r.requested - r.approved for r in self.rejections
                    if code is None or r.code == code), Decimal("0"))


def _q(x: Decimal, q: Decimal) -> Decimal:
    return x.quantize(q, rounding=ROUND_HALF_EVEN)


def hash_inputs(buys: list[BookOrder], sells: list[BookOrder], extra: dict | None = None) -> str:
    def row(o: BookOrder):
        return [o.id, o.participant_id, o.asset_id, o.node_id, o.side, str(o.quantity),
                str(o.remaining), str(o.price), o.sequence, o.renewable_only, o.is_renewable]
    payload = {"b": [row(o) for o in sorted(buys, key=lambda o: o.id)],
               "s": [row(o) for o in sorted(sells, key=lambda o: o.id)],
               "x": extra or {}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def sort_books(buys: list[BookOrder], sells: list[BookOrder]):
    return (sorted(buys, key=lambda o: (-o.price, o.sequence, o.id)),
            sorted(sells, key=lambda o: (o.price, o.sequence, o.id)))


def _accept_all(buy, sell, qty):  # default validator
    return ValidationResult(qty)


def clear_market(buys: list[BookOrder], sells: list[BookOrder],
                 validator: Callable[[BookOrder, BookOrder, Decimal], ValidationResult] = _accept_all,
                 extra_hash: dict | None = None) -> ClearingResult:
    """Clear on copies of the orders; callers' objects are not mutated."""
    import copy
    b = [copy.copy(o) for o in buys]
    s = [copy.copy(o) for o in sells]
    result = ClearingResult(input_hash=hash_inputs(buys, sells, extra_hash))
    sb, ss = sort_books(b, s)
    for o in sb + ss:
        result.fills[o.id] = Decimal("0")

    for buy in sb:
        for sell in ss:
            if buy.remaining <= 0:
                break
            if sell.remaining <= 0:
                continue
            if sell.price > buy.price:
                break  # sorted ascending: no further seller is acceptable
            if sell.participant_id == buy.participant_id:
                continue
            if buy.renewable_only and not sell.is_renewable:
                continue
            want = _q(min(buy.remaining, sell.remaining), QTY_Q)
            if want <= 0:
                continue
            vr = validator(buy, sell, want)
            approved = _q(min(max(vr.approved, Decimal("0")), want), QTY_Q)
            if approved < want:
                result.rejections.append(Rejection(buy.id, sell.id, want, approved, vr.code, vr.message))
            if approved <= 0:
                continue
            price = _q((buy.price + sell.price) / 2, PRICE_Q)
            result.trades.append(ProposedTrade(
                buy.id, sell.id, buy.participant_id, sell.participant_id, buy.asset_id,
                sell.asset_id, approved, price, sell.is_renewable))
            buy.remaining -= approved
            sell.remaining -= approved
            result.fills[buy.id] += approved
            result.fills[sell.id] += approved

    result.matched_quantity = sum((t.quantity for t in result.trades), Decimal("0"))
    if result.matched_quantity > 0:
        notional = sum((t.quantity * t.price for t in result.trades), Decimal("0"))
        result.clearing_price = _q(notional / result.matched_quantity, PRICE_Q)
    return result
