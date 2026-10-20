"""Smart-meter ingestion, verification, corrections and window aggregation."""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.core.enums import AuditEventType, MeterStatus
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.timeutil import ensure_utc, utcnow
from app.models.assets import EnergyAsset
from app.models.meters import MeterCorrection, MeterReading, SmartMeter
from app.websocket.hub import queue_event

FIELDS = ["generation_kwh", "consumption_kwh", "import_kwh", "export_kwh",
          "battery_charge_kwh", "battery_discharge_kwh"]


def canonical_payload(meter_identifier: str, timestamp: datetime, values: dict) -> str:
    return json.dumps({"m": meter_identifier, "t": ensure_utc(timestamp).isoformat(),
                       **{k: round(float(values.get(k, 0.0)), 6) for k in FIELDS}}, sort_keys=True)


def sign(key: str, meter_identifier: str, timestamp: datetime, values: dict) -> str:
    return hmac.new(key.encode(), canonical_payload(meter_identifier, timestamp, values).encode(),
                    hashlib.sha256).hexdigest()


def verify_signature(key: str, meter_identifier: str, timestamp: datetime, values: dict, signature: str | None) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(sign(key, meter_identifier, timestamp, values), signature)


async def ingest_reading(session: AsyncSession, meter: SmartMeter, timestamp: datetime, values: dict,
                         signature: str | None = None, interval_minutes: int = 15, actor: str = "system",
                         asset: EnergyAsset | None = None, extra_meta: dict | None = None) -> MeterReading:
    timestamp = ensure_utc(timestamp)
    for f in FIELDS:
        if float(values.get(f, 0.0)) < 0:
            raise ValidationFailed(f"{f} cannot be negative")
    if meter.status == MeterStatus.DISABLED.value:
        raise Conflict("meter is disabled")
    asset = asset or await session.get(EnergyAsset, meter.asset_id)
    meta: dict = {"checks": [], **(extra_meta or {})}
    verified = meter.trusted
    if meter.public_key:
        ok = verify_signature(meter.public_key, meter.meter_identifier, timestamp, values, signature)
        meta["checks"].append({"signature_valid": ok})
        verified = verified and ok
    hours = interval_minutes / 60.0
    if asset and float(values.get("generation_kwh", 0.0)) > asset.rated_power_kw * hours * 1.25 + 1e-9:
        meta["checks"].append({"physical_plausibility": "GENERATION_EXCEEDS_RATED_POWER"})
        verified = False
    if meter.status == MeterStatus.ANOMALOUS.value:
        meta["checks"].append({"meter_status": "ANOMALOUS"})
        verified = False
    dup = (await session.execute(select(MeterReading.id).where(
        MeterReading.meter_id == meter.id, MeterReading.timestamp == timestamp))).first()
    if dup:
        raise Conflict("reading already exists for this meter and timestamp; submit a correction instead")
    r = MeterReading(meter_id=meter.id, asset_id=meter.asset_id, microgrid_id=asset.microgrid_id,
                     timestamp=timestamp, interval_minutes=interval_minutes, verified=verified, signature=signature,
                     verification_meta=meta, received_at=utcnow(),
                     **{f: float(values.get(f, 0.0)) for f in FIELDS})
    session.add(r)
    meter.last_seen = timestamp
    await session.flush()
    await audit.record(session, AuditEventType.METER_READING_RECEIVED, "MeterReading", r.id, actor,
                       {"meter": meter.meter_identifier, "verified": verified, "timestamp": timestamp,
                        **{f: getattr(r, f) for f in FIELDS}}, asset.microgrid_id)
    queue_event(session, "METER_READING", asset.microgrid_id,
                {"reading_id": r.id, "asset_id": r.asset_id, "timestamp": timestamp, "verified": verified,
                 **{f: getattr(r, f) for f in FIELDS}})
    return r


async def correct_reading(session: AsyncSession, reading_id: uuid.UUID, field_name: str, new_value: float,
                          reason: str, actor: str) -> MeterCorrection:
    if field_name not in FIELDS:
        raise ValidationFailed(f"unknown field {field_name}")
    if new_value < 0:
        raise ValidationFailed("corrected value cannot be negative")
    reading = await session.get(MeterReading, reading_id)
    if not reading:
        raise NotFound("meter reading not found")
    current = (await effective_values(session, [reading]))[reading.id][field_name]
    c = MeterCorrection(reading_id=reading_id, field=field_name, old_value=current, new_value=new_value,
                        reason=reason, actor=actor, created_at=utcnow())
    session.add(c)
    await session.flush()
    await audit.record(session, AuditEventType.METER_READING_CORRECTED, "MeterReading", reading_id, actor,
                       {"field": field_name, "old": current, "new": new_value, "reason": reason},
                       reading.microgrid_id)
    return c


async def effective_values(session: AsyncSession, readings: list[MeterReading]) -> dict[uuid.UUID, dict[str, float]]:
    out = {r.id: {f: getattr(r, f) for f in FIELDS} for r in readings}
    if not readings:
        return out
    q = select(MeterCorrection).where(MeterCorrection.reading_id.in_(list(out))).order_by(
        MeterCorrection.created_at, MeterCorrection.id)
    for c in (await session.execute(q)).scalars():
        out[c.reading_id][c.field] = c.new_value
    return out


@dataclass
class WindowSums:
    asset_id: uuid.UUID
    start: datetime
    end: datetime
    covered_minutes: int = 0
    expected_minutes: int = 0
    all_verified: bool = True
    totals: dict[str, float] = field(default_factory=lambda: {f: 0.0 for f in FIELDS})
    reading_ids: list[uuid.UUID] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.covered_minutes >= self.expected_minutes > 0


async def window_sums(session: AsyncSession, asset_id: uuid.UUID, start: datetime, end: datetime) -> WindowSums:
    """Sum readings whose interval lies inside (start, end] (timestamp = interval end)."""
    start, end = ensure_utc(start), ensure_utc(end)
    ws = WindowSums(asset_id, start, end, expected_minutes=int((end - start).total_seconds() // 60))
    q = select(MeterReading).where(MeterReading.asset_id == asset_id, MeterReading.timestamp > start,
                                   MeterReading.timestamp <= end).order_by(MeterReading.timestamp)
    readings = list((await session.execute(q)).scalars())
    eff = await effective_values(session, readings)
    for r in readings:
        ws.covered_minutes += r.interval_minutes
        ws.all_verified = ws.all_verified and r.verified
        ws.reading_ids.append(r.id)
        for f in FIELDS:
            ws.totals[f] += eff[r.id][f]
    return ws
