"""Balancing service: covers delivery shortfalls with the cheapest feasible replacement energy."""
from __future__ import annotations

import math
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.battery import deliverable_discharge_kwh
from app.agents.ev import EV_ETA, max_v2g_kwh
from app.audit import service as audit
from app.balancing.engine import BalancingCandidate, balance_deficit
from app.core.enums import AssetStatus, AssetType, AuditEventType, BalancingSource, LedgerAccountKind as K, LoadPriority
from app.grid.service import load_network
from app.meters.service import window_sums
from app.models.assets import EnergyAsset
from app.models.core import Microgrid, Participant
from app.models.ops import BalancingAction, DecisionRecord, DemandResponseEvent, EnergyCommitment
from app.risk.service import battery_state, ev_state
from app.settlement.ledger import Ledger
from app.simulation import signals
from app.simulation.state import get_config
from app.websocket.hub import queue_event

D = lambda x: Decimal(str(round(float(x), 6)))  # noqa: E731


async def build_candidates(session: AsyncSession, mg: Microgrid, failed: EnergyCommitment, ws: datetime, we: datetime,
                           now: datetime, tz: float) -> list[BalancingCandidate]:
    hours = (we - ws).total_seconds() / 3600.0
    assets = list((await session.execute(select(EnergyAsset).where(EnergyAsset.microgrid_id == mg.id))).scalars())
    parts = {p.id: p for p in (await session.execute(select(Participant).where(Participant.microgrid_id == mg.id))).scalars()}
    tariff = signals.utility_price(ws, tz)
    committed_by_asset: dict[uuid.UUID, Decimal] = {}
    for c in (await session.execute(select(EnergyCommitment).where(
            EnergyCommitment.microgrid_id == mg.id, EnergyCommitment.delivery_start < we,
            EnergyCommitment.delivery_end > ws, EnergyCommitment.id != failed.id))).scalars():
        committed_by_asset[c.seller_asset_id] = committed_by_asset.get(c.seller_asset_id, Decimal("0")) + c.quantity_kwh
    cands: list[BalancingCandidate] = []
    for a in assets:
        if a.status != AssetStatus.ONLINE.value or a.id == failed.seller_asset_id:
            continue
        p = parts[a.participant_id]
        committed = committed_by_asset.get(a.id, Decimal("0"))
        node = str(a.grid_node_id)
        rel = p.reliability_score
        if a.generator:
            s = await window_sums(session, a.id, ws, we)
            spare = D(s.totals["generation_kwh"]) - committed
            if spare > 0:
                cands.append(BalancingCandidate(BalancingSource.GENERATOR, str(a.id), node, spare,
                                                max(D(a.generator.marginal_cost * 1.1), Decimal("0.03")), 120, rel, str(p.id)))
        elif a.battery:
            bs = battery_state(a)
            avail = D(deliverable_discharge_kwh(bs, hours, bs.min_soc_percent + 10)) - committed
            if avail > 0:
                src = BalancingSource.COMMUNITY_BATTERY if a.battery.community else BalancingSource.HOME_BATTERY
                cands.append(BalancingCandidate(src, str(a.id), node, avail,
                                                D(a.battery.degradation_cost_per_kwh + tariff * 0.6), 5, rel, str(p.id)))
        elif a.ev:
            avail = D(max_v2g_kwh(ev_state(a), ws, we)) - committed
            if avail > 0:
                cands.append(BalancingCandidate(BalancingSource.V2G, str(a.id), node, avail, D(tariff * 0.9), 30, rel, str(p.id)))
        elif a.load and a.load.flexible and a.load.priority != LoadPriority.CRITICAL.value:
            avoidable = max(0.0, min(a.load.maximum_curtailment_kw, a.load.current_demand_kw) - a.load.curtailed_kw) * hours
            if avoidable > 0:
                cands.append(BalancingCandidate(BalancingSource.DEMAND_RESPONSE, str(a.id), node, D(avoidable),
                                                max(D(a.load.compensation_per_kwh), Decimal("0.01")), 60, rel, str(p.id)))
        elif a.asset_type == AssetType.GRID_CONNECTION.value and mg.external_grid_connected:
            head = D(mg.max_grid_import_kw * hours) - committed
            if head > 0:
                cands.append(BalancingCandidate(BalancingSource.EXTERNAL_GRID, str(a.id), node, head,
                                                D(tariff * 1.2), 1, 1.0, str(p.id)))
    return cands


async def cover_shortfalls(session: AsyncSession, microgrid_id: uuid.UUID, shortfalls: list[EnergyCommitment],
                           now: datetime, actor: str = "balancing_engine") -> dict:
    mg = await session.get(Microgrid, microgrid_id)
    cfg = await get_config(session, microgrid_id)
    ledger = Ledger(session, microgrid_id)
    penalty_acc = await ledger.account(None, K.SYSTEM_PENALTY)
    out = {"actions": [], "unmet_kwh": Decimal("0"), "total_cost": Decimal("0"), "covered_kwh": Decimal("0")}
    for c in shortfalls:
        deficit = c.quantity_kwh - (c.delivered_kwh or Decimal("0"))
        if deficit <= 0:
            continue
        ws, we = c.delivery_start, c.delivery_end
        hours = (we - ws).total_seconds() / 3600.0
        buyer_asset = await session.get(EnergyAsset, c.buyer_asset_id)
        net = await load_network(session, microgrid_id, ws, we, exclude_commitment=c.id)
        cands = await build_candidates(session, mg, c, ws, we, now, cfg.tz_offset_hours)
        res = balance_deficit(deficit, str(buyer_asset.grid_node_id), cands, net, hours)
        for al in res.allocations:
            act = BalancingAction(microgrid_id=microgrid_id, commitment_id=c.id, source_type=al.source_type.value,
                                  source_asset_id=uuid.UUID(al.asset_id) if al.asset_id else None,
                                  quantity_kwh=al.quantity_kwh, price=al.price, cost=al.cost, deficit_kwh=deficit,
                                  interval_start=ws, interval_end=we, created_at=now,
                                  explanation={"decision": f"BALANCING_{al.source_type.value}",
                                               "reason_codes": ["DELIVERY_SHORTFALL", "LOWEST_RANKED_SCORE"]
                                               + (["GRID_LIMITED"] if al.grid_limited else []),
                                               "inputs": {"commitment_id": str(c.id), "deficit_kwh": str(deficit),
                                                          "score": al.score, "price": str(al.price)}})
            session.add(act)
            await session.flush()
            asset = await session.get(EnergyAsset, uuid.UUID(al.asset_id))
            q = float(al.quantity_kwh)
            if asset.battery:
                b = asset.battery
                eta = math.sqrt(b.round_trip_efficiency)
                b.current_soc_percent = max(b.minimum_soc_percent, b.current_soc_percent - q / eta / b.capacity_kwh * 100)
                await audit.record(session, AuditEventType.BATTERY_DISPATCHED, "EnergyAsset", asset.id, actor,
                                   {"discharge_kwh": q, "reason": "BALANCING", "balancing_action": act.id}, microgrid_id)
            elif asset.ev:
                e = asset.ev
                e.soc_percent = max(e.minimum_reserve_soc_percent, e.soc_percent - q / EV_ETA / e.battery_capacity_kwh * 100)
                await audit.record(session, AuditEventType.EV_DISPATCHED, "EnergyAsset", asset.id, actor,
                                   {"discharge_kwh": q, "reason": "BALANCING", "balancing_action": act.id}, microgrid_id)
            elif al.source_type == BalancingSource.DEMAND_RESPONSE:
                session.add(DemandResponseEvent(microgrid_id=microgrid_id, asset_id=asset.id, participant_id=asset.participant_id,
                                                avoided_kwh=al.quantity_kwh, compensation=al.cost, kind="CURTAIL",
                                                reason="BALANCING_DEMAND_RESPONSE", interval_start=ws, interval_end=we,
                                                paid=True, created_at=now))
                await audit.record(session, AuditEventType.DEMAND_CURTAILED, "EnergyAsset", asset.id, actor,
                                   {"avoided_kwh": al.quantity_kwh, "compensation": al.cost}, microgrid_id)
            provider = await ledger.account(asset.participant_id, K.AVAILABLE)
            await ledger.post("BALANCING_PAYMENT", "BALANCING_ACTION", act.id, [(penalty_acc, -al.cost), (provider, al.cost)],
                              "balancing energy payment", f"bal:{act.id}")
            session.add(DecisionRecord(microgrid_id=microgrid_id, actor="BalancingEngine",
                                       decision=act.explanation["decision"], quantity_kwh=q,
                                       reason_codes={"codes": act.explanation["reason_codes"]},
                                       inputs=act.explanation["inputs"], created_at=now))
            await audit.record(session, AuditEventType.BALANCING_ACTIVATED, "BalancingAction", act.id, actor,
                               {"source": al.source_type.value, "asset_id": asset.id, "quantity_kwh": al.quantity_kwh,
                                "price": al.price, "cost": al.cost, "commitment_id": c.id}, microgrid_id)
            queue_event(session, "BALANCING_EVENT", microgrid_id,
                        {"action_id": act.id, "source": al.source_type.value, "quantity_kwh": al.quantity_kwh,
                         "cost": al.cost, "commitment_id": c.id})
            out["actions"].append(act)
        out["unmet_kwh"] += res.unmet_kwh
        out["total_cost"] += res.total_cost
        out["covered_kwh"] += res.covered_kwh
        if res.unmet_kwh > 0:
            await audit.record(session, "BALANCING_UNMET", "EnergyCommitment", c.id, actor,
                               {"unmet_kwh": res.unmet_kwh, "skipped": res.skipped}, microgrid_id)
    await session.flush()
    return out
