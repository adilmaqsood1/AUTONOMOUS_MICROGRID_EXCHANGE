"""Surveillance service: evaluates market participants, stores alerts, and generates audit reports."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.core.enums import AuditEventType, OrderStatus
from app.core.timeutil import utcnow
from app.models.assets import EnergyAsset
from app.models.core import GridLine, Microgrid, Participant
from app.models.ops import EnergyCommitment, EnergyOrder, MarketClearing, MarketSurveillanceAlert
from app.simulation.state import get_config
from app.surveillance.engine import SurveillanceEngine, SurveillanceFinding
from app.websocket.hub import queue_event


async def run_surveillance_cycle(
    session: AsyncSession,
    microgrid_id: uuid.UUID,
    actor: str = "surveillance_ai",
) -> List[MarketSurveillanceAlert]:
    """Runs a complete market surveillance audit across all participants and assets in the microgrid."""
    mg = await session.get(Microgrid, microgrid_id)
    if not mg:
        raise ValueError("Microgrid not found")

    cfg = await get_config(session, microgrid_id)
    now = cfg.sim_time

    # 1. Fetch assets and participants
    assets = list((await session.execute(
        select(EnergyAsset).where(EnergyAsset.microgrid_id == microgrid_id)
    )).scalars())
    participants = list((await session.execute(
        select(Participant).where(Participant.microgrid_id == microgrid_id)
    )).scalars())

    # 2. Fetch recent orders and lines
    recent_orders = list((await session.execute(
        select(EnergyOrder).where(
            EnergyOrder.microgrid_id == microgrid_id,
            EnergyOrder.created_at >= now - timedelta(hours=24),
        )
    )).scalars())

    lines = list((await session.execute(
        select(GridLine).where(GridLine.microgrid_id == microgrid_id, GridLine.active.is_(True))
    )).scalars())
    congested_lines = [ln for ln in lines if (abs(ln.current_flow_kw or 0.0) / max(ln.capacity_kw, 1.0)) >= 0.90]

    # 3. Fetch historical clearing prices for Z-score calculation
    clearings = list((await session.execute(
        select(MarketClearing)
        .where(MarketClearing.microgrid_id == microgrid_id)
        .order_by(desc(MarketClearing.created_at))
        .limit(50)
    )).scalars())
    hist_prices = [float(c.clearing_price) for c in clearings if c.clearing_price is not None]

    findings: List[SurveillanceFinding] = []

    # Check 1: Capacity Withholding on generator / battery assets
    orders_by_asset = {}
    for o in recent_orders:
        orders_by_asset.setdefault(o.asset_id, []).append(o)

    for asset in assets:
        open_orders = [o for o in orders_by_asset.get(asset.id, []) if o.status in (OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value)]
        finding = SurveillanceEngine.detect_capacity_withholding(asset, open_orders, peak_period=True)
        if finding:
            findings.append(finding)

    # Check 2: Abnormal Bidding (Z-scores) & Congestion Gaming on recent orders
    asset_map = {a.id: a for a in assets}
    for order in recent_orders:
        abnormal = SurveillanceEngine.detect_abnormal_bidding(order, hist_prices)
        if abnormal:
            findings.append(abnormal)

        ast = asset_map.get(order.asset_id)
        if ast:
            cg = SurveillanceEngine.detect_congestion_gaming(order, ast, congested_lines)
            if cg:
                findings.append(cg)

    # Check 3: Persistent Under-Delivery per participant
    for part in participants:
        comm_q = (
            select(EnergyCommitment)
            .where(
                EnergyCommitment.microgrid_id == microgrid_id,
                EnergyCommitment.seller_id == part.id,
                EnergyCommitment.created_at >= now - timedelta(days=7),
            )
            .order_by(desc(EnergyCommitment.created_at))
            .limit(20)
        )
        comms = list((await session.execute(comm_q)).scalars())
        underdel = SurveillanceEngine.detect_persistent_underdelivery(part.id, comms)
        if underdel:
            findings.append(underdel)

    # Persist alerts and record audits
    alerts: List[MarketSurveillanceAlert] = []
    for f in findings:
        alert = MarketSurveillanceAlert(
            microgrid_id=microgrid_id,
            participant_id=f.participant_id,
            asset_id=f.asset_id,
            alert_type=f.alert_type,
            severity=f.severity,
            score=f.score,
            description=f.description,
            evidence=f.evidence,
            status="OPEN",
            timestamp=now,
            created_at=utcnow(),
        )
        session.add(alert)
        alerts.append(alert)

    if alerts:
        await session.flush()
        for a in alerts:
            await audit.record(
                session,
                AuditEventType.RISK_REJECTED,
                "MarketSurveillanceAlert",
                a.id,
                actor,
                {"type": a.alert_type, "severity": a.severity, "score": a.score, "description": a.description},
                microgrid_id,
            )
        queue_event(
            session,
            "SURVEILLANCE_ALERT",
            microgrid_id,
            {"new_alerts_count": len(alerts), "severities": [a.severity for a in alerts]},
        )

    return alerts


async def list_surveillance_alerts(
    session: AsyncSession,
    microgrid_id: Optional[uuid.UUID] = None,
    alert_type: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
) -> List[MarketSurveillanceAlert]:
    q = select(MarketSurveillanceAlert)
    if microgrid_id:
        q = q.where(MarketSurveillanceAlert.microgrid_id == microgrid_id)
    if alert_type:
        q = q.where(MarketSurveillanceAlert.alert_type == alert_type)
    if severity:
        q = q.where(MarketSurveillanceAlert.severity == severity)
    if status:
        q = q.where(MarketSurveillanceAlert.status == status)
    return list((await session.execute(q.order_by(desc(MarketSurveillanceAlert.timestamp)).limit(limit))).scalars())


async def update_alert_status(
    session: AsyncSession,
    alert_id: uuid.UUID,
    new_status: str,
    actor: str = "operator",
) -> MarketSurveillanceAlert:
    alert = await session.get(MarketSurveillanceAlert, alert_id)
    if not alert:
        raise ValueError("Alert not found")
    alert.status = new_status
    await session.flush()
    return alert
