"""Resilience service: island / emergency / recovery state machine and load-shedding records."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.core.enums import AuditEventType, LedgerAccountKind as K, OperatingMode
from app.core.errors import Conflict, NotFound
from app.models.assets import EnergyAsset
from app.models.core import Microgrid
from app.models.ops import DecisionRecord, DemandResponseEvent, LoadSheddingDecision
from app.resilience.engine import LoadDecision, can_transition
from app.settlement.ledger import Ledger
from app.websocket.hub import queue_event


async def _lock_mg(session: AsyncSession, microgrid_id: uuid.UUID) -> Microgrid:
    mg = (await session.execute(select(Microgrid).where(Microgrid.id == microgrid_id).with_for_update())).scalar_one_or_none()
    if not mg:
        raise NotFound("microgrid not found")
    return mg


async def transition(session: AsyncSession, mg: Microgrid, to: OperatingMode, now: datetime, actor: str,
                     reason: str) -> None:
    if mg.operating_mode == to.value:
        return
    if not can_transition(mg.operating_mode, to.value):
        raise Conflict(f"illegal mode transition {mg.operating_mode} -> {to.value}")
    frm = mg.operating_mode
    mg.operating_mode = to.value
    await session.flush()
    session.add(DecisionRecord(microgrid_id=mg.id, actor="ResilienceEngine", decision=f"MODE_{to.value}",
                               quantity_kwh=0.0, reason_codes={"codes": [reason]},
                               inputs={"from": frm, "to": to.value}, created_at=now))


async def declare_outage(session: AsyncSession, microgrid_id: uuid.UUID, now: datetime, actor: str = "system") -> Microgrid:
    mg = await _lock_mg(session, microgrid_id)
    mg.external_grid_connected = False
    await audit.record(session, AuditEventType.GRID_OUTAGE, "Microgrid", mg.id, actor, {"at": now}, mg.id)
    queue_event(session, "GRID_OUTAGE", mg.id, {"at": now})
    await transition(session, mg, OperatingMode.ISLANDED, now, actor, "UTILITY_FAILURE")
    await audit.record(session, AuditEventType.ISLAND_MODE_ACTIVATED, "Microgrid", mg.id, actor,
                       {"external_import_kw": 0, "external_export_kw": 0}, mg.id)
    queue_event(session, "ISLAND_MODE", mg.id, {"mode": mg.operating_mode, "external_grid_connected": False})
    return mg


async def begin_recovery(session: AsyncSession, microgrid_id: uuid.UUID, now: datetime, actor: str = "system") -> Microgrid:
    mg = await _lock_mg(session, microgrid_id)
    if mg.operating_mode == OperatingMode.GRID_CONNECTED.value:
        mg.external_grid_connected = True
        return mg
    mg.external_grid_connected = True
    await transition(session, mg, OperatingMode.RECOVERY, now, actor, "UTILITY_RESTORED")
    await audit.record(session, AuditEventType.GRID_RECOVERED, "Microgrid", mg.id, actor, {"at": now}, mg.id)
    queue_event(session, "RECOVERY", mg.id, {"mode": mg.operating_mode, "phase": "STARTED"})
    return mg


async def complete_recovery(session: AsyncSession, mg: Microgrid, now: datetime, actor: str = "system") -> None:
    await transition(session, mg, OperatingMode.GRID_CONNECTED, now, actor, "RECOVERY_COMPLETE")
    await audit.record(session, "RECOVERY_COMPLETED", "Microgrid", mg.id, actor, {"at": now}, mg.id)
    queue_event(session, "RECOVERY", mg.id, {"mode": mg.operating_mode, "phase": "COMPLETED"})


async def apply_load_plan(session: AsyncSession, mg: Microgrid, loads: dict[str, dict], served_kw: dict[str, float],
                          decisions: list[LoadDecision], now: datetime, step_hours: float,
                          actor: str = "resilience_engine") -> list[LoadSheddingDecision]:
    """Persist curtailment changes. `loads[id]` = {asset, props(optional), demand_kw, prev_curtailed_kw, flexible,
    compensation_per_kwh, participant_id}. Records only *changes* so history is not noisy."""
    act = {}
    for d in decisions:
        act.setdefault(d.asset_id, d)
    out: list[LoadSheddingDecision] = []
    ledger = Ledger(session, mg.id)
    for lid, info in loads.items():
        new_c = max(0.0, info["demand_kw"] - served_kw.get(lid, info["demand_kw"]))
        old_c = info["prev_curtailed_kw"]
        delta = new_c - old_c
        if info.get("props") is not None:
            info["props"].curtailed_kw = new_c
        if abs(delta) < 1e-6:
            continue
        d = act.get(lid)
        action = "RESTORE" if delta < 0 else (d.action if d and d.action in ("SHED", "CURTAIL") else "SHED")
        reason = d.reason if (d and delta > 0) else "SUPPLY_RESTORED_RESTORE_BY_PRIORITY"
        row = LoadSheddingDecision(microgrid_id=mg.id, asset_id=uuid.UUID(lid), action=action,
                                   priority=info["priority"], quantity_kw=abs(delta), reason=reason,
                                   mode=mg.operating_mode, created_at=now)
        session.add(row)
        out.append(row)
        if action == "SHED":
            await audit.record(session, AuditEventType.LOAD_SHED, "EnergyAsset", lid, actor,
                               {"quantity_kw": abs(delta), "priority": info["priority"], "reason": reason,
                                "mode": mg.operating_mode}, mg.id)
        elif action == "CURTAIL":
            kwh = Decimal(str(round(abs(delta) * step_hours, 6)))
            comp = (kwh * Decimal(str(info.get("compensation_per_kwh", 0.0)))).quantize(Decimal("0.00000001"))
            session.add(DemandResponseEvent(microgrid_id=mg.id, asset_id=uuid.UUID(lid),
                                            participant_id=info["participant_id"], avoided_kwh=kwh, compensation=comp,
                                            kind="POSTPONE" if info.get("is_ev") else "CURTAIL", reason=reason,
                                            interval_start=now, interval_end=now, paid=comp > 0, created_at=now))
            if comp > 0:
                eq = await ledger.account(None, K.SYSTEM_EQUITY)
                av = await ledger.account(info["participant_id"], K.AVAILABLE)
                await ledger.post("DEMAND_RESPONSE", "ASSET", lid, [(eq, -comp), (av, comp)],
                                  "demand response compensation", f"dr:{lid}:{now.isoformat()}")
            await audit.record(session, AuditEventType.DEMAND_CURTAILED, "EnergyAsset", lid, actor,
                               {"avoided_kwh": kwh, "compensation": comp, "reason": reason}, mg.id)
    await session.flush()
    return out


async def status(session: AsyncSession, microgrid_id: uuid.UUID) -> dict:
    from app.models.assets import LoadProps
    mg = await session.get(Microgrid, microgrid_id)
    if not mg:
        raise NotFound("microgrid not found")
    rows = (await session.execute(select(EnergyAsset, LoadProps).join(LoadProps, LoadProps.asset_id == EnergyAsset.id)
                                  .where(EnergyAsset.microgrid_id == microgrid_id))).all()
    shed = [{"asset_id": str(a.id), "name": a.name, "priority": lp.priority, "shed_kw": lp.curtailed_kw}
            for a, lp in rows if lp.curtailed_kw > 1e-6]
    critical = [(a, lp) for a, lp in rows if lp.priority == "CRITICAL"]
    return {"mode": mg.operating_mode, "external_grid_connected": mg.external_grid_connected,
            "shed_loads": shed, "critical_loads_fully_served": all(lp.curtailed_kw <= 1e-6 for _, lp in critical),
            "critical_load_count": len(critical)}
