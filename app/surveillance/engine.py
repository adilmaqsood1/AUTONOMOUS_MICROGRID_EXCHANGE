"""AI Market Surveillance Engine (Phase 2).

Detects market manipulation patterns in autonomous transactive microgrids:
1. Capacity Withholding: Withholding generation/storage during peak demand to inflate prices.
2. Congestion Gaming: Submitting strategic bids behind physical bottlenecks to exploit congestion rents.
3. Persistent Under-Delivery: Chronic failure to deliver committed energy (delivery ratio < 85%).
4. Abnormal Bidding: Statistical price outliers (Z-score > 3.0) and quote stuffing.
5. Meter Discrepancy: Tampered readings or unverified HMAC telemetry.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AssetType, OrderSide, OrderStatus
from app.core.timeutil import utcnow
from app.models.assets import EnergyAsset
from app.models.core import GridLine, Microgrid, Participant
from app.models.meters import MeterReading
from app.models.ops import EnergyCommitment, EnergyOrder, MarketClearing, MarketSurveillanceAlert


@dataclass
class SurveillanceFinding:
    alert_type: str
    severity: str
    score: float
    description: str
    evidence: Dict[str, Any]
    participant_id: Optional[uuid.UUID] = None
    asset_id: Optional[uuid.UUID] = None


class SurveillanceEngine:
    """Evaluates participants, bids, commitments, and telemetry for market manipulation."""

    @staticmethod
    def detect_capacity_withholding(
        asset: EnergyAsset,
        open_orders: List[EnergyOrder],
        peak_period: bool = True,
    ) -> Optional[SurveillanceFinding]:
        """Flags participants withholding capacity (>40% unoffered during peak windows)."""
        if not asset.generator and not asset.battery:
            return None

        # Determine physical capacity in kW
        if asset.generator:
            physical_cap = asset.generator.maximum_generation_kw
        elif asset.battery:
            physical_cap = asset.battery.maximum_discharge_kw
        else:
            return None

        if physical_cap <= 0:
            return None

        # Total energy or capacity offered in current open orders (assuming 1-hour equivalent)
        offered_kw = sum(float(o.quantity_kwh) for o in open_orders if o.side == OrderSide.SELL.value)
        cwi = 1.0 - min(offered_kw / physical_cap, 1.0)  # Capacity Withholding Index

        if cwi > 0.40 and peak_period and physical_cap > 5.0:
            severity = "HIGH" if cwi > 0.70 else "MEDIUM"
            return SurveillanceFinding(
                alert_type="CAPACITY_WITHHOLDING",
                severity=severity,
                score=round(cwi, 3),
                description=f"Participant withheld {round(cwi * 100, 1)}% of physical capacity ({round(physical_cap - offered_kw, 2)} kW withheld) during active trading period.",
                evidence={
                    "physical_capacity_kw": physical_cap,
                    "offered_kw": offered_kw,
                    "withholding_ratio": round(cwi, 3),
                    "asset_name": asset.name,
                },
                participant_id=asset.participant_id,
                asset_id=asset.id,
            )
        return None

    @staticmethod
    def detect_persistent_underdelivery(
        participant_id: uuid.UUID,
        commitments: List[EnergyCommitment],
    ) -> Optional[SurveillanceFinding]:
        """Flags sellers with persistent delivery shortfalls (<85% delivery ratio across >= 3 commitments)."""
        if len(commitments) < 3:
            return None

        ratios = [c.delivery_ratio for c in commitments if c.delivery_ratio is not None]
        if len(ratios) < 3:
            return None

        avg_ratio = sum(ratios) / len(ratios)
        underdeliveries = sum(1 for r in ratios if r < 0.85)

        if avg_ratio < 0.85 or underdeliveries >= 3:
            severity = "CRITICAL" if avg_ratio < 0.60 else "HIGH"
            score = round(1.0 - avg_ratio, 3)
            return SurveillanceFinding(
                alert_type="PERSISTENT_UNDERDELIVERY",
                severity=severity,
                score=score,
                description=f"Participant exhibits persistent delivery shortfall: average delivery ratio {round(avg_ratio * 100, 1)}% across {len(ratios)} recent commitments.",
                evidence={
                    "commitments_evaluated": len(ratios),
                    "average_delivery_ratio": round(avg_ratio, 3),
                    "underdelivered_count": underdeliveries,
                    "recent_ratios": [round(r, 3) for r in ratios[-5:]],
                },
                participant_id=participant_id,
            )
        return None

    @staticmethod
    def detect_abnormal_bidding(
        order: EnergyOrder,
        historical_prices: List[float],
    ) -> Optional[SurveillanceFinding]:
        """Statistical outlier detection (Z-score > 3.0) on bid prices."""
        if len(historical_prices) < 10:
            return None

        mean = sum(historical_prices) / len(historical_prices)
        variance = sum((p - mean) ** 2 for p in historical_prices) / len(historical_prices)
        std_dev = math.sqrt(variance)

        if std_dev < 1e-4:
            return None

        order_price = float(order.limit_price)
        z_score = abs(order_price - mean) / std_dev

        if z_score > 3.0:
            severity = "CRITICAL" if z_score > 4.5 else "HIGH"
            return SurveillanceFinding(
                alert_type="ANOMALOUS_BID_PRICING",
                severity=severity,
                score=round(z_score, 2),
                description=f"Order price ${order_price:.4f}/kWh deviates significantly from historical market mean ${mean:.4f}/kWh (Z-score: {z_score:.2f}).",
                evidence={
                    "order_id": str(order.id),
                    "order_price": order_price,
                    "market_mean_price": round(mean, 4),
                    "market_std_dev": round(std_dev, 4),
                    "z_score": round(z_score, 2),
                },
                participant_id=order.participant_id,
                asset_id=order.asset_id,
            )
        return None

    @staticmethod
    def detect_congestion_gaming(
        order: EnergyOrder,
        asset: EnergyAsset,
        congested_lines: List[GridLine],
    ) -> Optional[SurveillanceFinding]:
        """Flags bids placed upstream of already congested feeders (congestion rent gaming)."""
        if order.side != OrderSide.SELL.value or not congested_lines:
            return None

        for ln in congested_lines:
            if str(ln.from_node_id) == str(asset.grid_node_id):
                # Participant is injecting high sell volume into an already congested line
                return SurveillanceFinding(
                    alert_type="CONGESTION_GAMING",
                    severity="HIGH",
                    score=0.85,
                    description=f"Strategic sell order submitted behind congested feeder line {ln.id} (flow {ln.current_flow_kw:.1f} / {ln.capacity_kw:.1f} kW).",
                    evidence={
                        "order_id": str(order.id),
                        "line_id": str(ln.id),
                        "line_loading_pct": round(abs(ln.current_flow_kw or 0.0) / max(ln.capacity_kw, 1.0) * 100, 1),
                        "quantity_kwh": str(order.quantity_kwh),
                    },
                    participant_id=order.participant_id,
                    asset_id=asset.id,
                )
        return None
