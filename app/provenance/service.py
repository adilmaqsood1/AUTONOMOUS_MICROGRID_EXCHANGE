"""Renewable provenance: certificates only against verified renewable generation, once per reading."""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.blockchain.gateway import get_gateway
from app.core.enums import AuditEventType, CertificateStatus as CS
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.timeutil import utcnow
from app.meters.service import effective_values
from app.models.assets import EnergyAsset
from app.models.core import Participant
from app.models.meters import MeterReading
from app.models.ops import RenewableCertificate
from app.websocket.hub import queue_event

QTY = Decimal("0.000001")


async def issue_certificate(session: AsyncSession, reading_id: uuid.UUID, actor: str = "provenance") -> RenewableCertificate:
    reading = await session.get(MeterReading, reading_id)
    if not reading:
        raise NotFound("meter reading not found")
    asset = await session.get(EnergyAsset, reading.asset_id)
    if not (asset.generator and asset.generator.renewable):
        raise ValidationFailed("only renewable generation can be certified")
    if not reading.verified:
        raise ValidationFailed("meter reading is not verified; cannot issue certificate")
    kwh = Decimal(str(round((await effective_values(session, [reading]))[reading.id]["generation_kwh"], 6))).quantize(QTY)
    if kwh <= 0:
        raise ValidationFailed("no generated energy to certify")
    existing = (await session.execute(select(RenewableCertificate).where(
        RenewableCertificate.meter_reading_id == reading_id))).scalar_one_or_none()
    if existing:
        raise Conflict("certificate already issued for this verified energy", certificate_id=str(existing.id))
    owner = await session.get(Participant, asset.participant_id)
    cert = RenewableCertificate(source_asset_id=asset.id, meter_reading_id=reading_id, microgrid_id=asset.microgrid_id,
                                energy_quantity_kwh=kwh, generation_timestamp=reading.timestamp,
                                renewable_source=asset.asset_type, owner_id=asset.participant_id,
                                status=CS.ISSUED.value, created_at=utcnow())
    try:
        async with session.begin_nested():  # unique(meter_reading_id) is the race-proof guard
            session.add(cert)
            await session.flush()
    except IntegrityError:
        raise Conflict("certificate already issued for this verified energy")
    try:
        rc = await get_gateway().issue_renewable_certificate(str(cert.id), owner.wallet_id, kwh, asset.asset_type, str(reading_id))
    except ValueError as exc:
        raise Conflict(str(exc))
    cert.blockchain_reference = rc.reference
    await session.flush()
    await audit.record(session, AuditEventType.CERTIFICATE_ISSUED, "RenewableCertificate", cert.id, actor,
                       {"asset_id": asset.id, "reading_id": reading_id, "kwh": kwh, "source": asset.asset_type,
                        "blockchain_reference": rc.reference}, asset.microgrid_id)
    queue_event(session, "CERTIFICATE_EVENT", asset.microgrid_id,
                {"certificate_id": cert.id, "status": cert.status, "kwh": kwh})
    return cert


async def _get_locked(session: AsyncSession, cert_id: uuid.UUID) -> RenewableCertificate:
    c = (await session.execute(select(RenewableCertificate).where(RenewableCertificate.id == cert_id)
                               .with_for_update())).scalar_one_or_none()
    if not c:
        raise NotFound("certificate not found")
    return c


async def transfer_certificate(session: AsyncSession, cert_id: uuid.UUID, from_owner: uuid.UUID, to_owner: uuid.UUID,
                               actor: str = "participant") -> RenewableCertificate:
    c = await _get_locked(session, cert_id)
    if c.status in (CS.RETIRED.value, CS.REVOKED.value):
        raise Conflict(f"certificate is {c.status}; it cannot be transferred")
    if c.owner_id != from_owner:
        raise Forbidden("only the current owner can transfer a certificate")
    new = await session.get(Participant, to_owner)
    if not new:
        raise NotFound("recipient not found")
    old = await session.get(Participant, from_owner)
    try:
        rc = await get_gateway().transfer_certificate(str(c.id), old.wallet_id, new.wallet_id)
    except ValueError as exc:
        raise Conflict(str(exc))
    c.owner_id, c.status, c.blockchain_reference = to_owner, CS.TRANSFERRED.value, rc.reference
    await session.flush()
    await audit.record(session, "CERTIFICATE_TRANSFERRED", "RenewableCertificate", c.id, actor,
                       {"from": from_owner, "to": to_owner}, c.microgrid_id)
    queue_event(session, "CERTIFICATE_EVENT", c.microgrid_id, {"certificate_id": c.id, "status": c.status})
    return c


async def retire_certificate(session: AsyncSession, cert_id: uuid.UUID, owner_id: uuid.UUID, actor: str = "participant"
                             ) -> RenewableCertificate:
    c = await _get_locked(session, cert_id)
    if c.status == CS.RETIRED.value:
        raise Conflict("certificate already retired")
    if c.status == CS.REVOKED.value:
        raise Conflict("certificate is revoked")
    if c.owner_id != owner_id:
        raise Forbidden("only the owner can retire a certificate")
    owner = await session.get(Participant, owner_id)
    try:
        rc = await get_gateway().retire_certificate(str(c.id), owner.wallet_id)
    except ValueError as exc:
        raise Conflict(str(exc))
    c.status, c.blockchain_reference = CS.RETIRED.value, rc.reference
    await session.flush()
    await audit.record(session, AuditEventType.CERTIFICATE_RETIRED, "RenewableCertificate", c.id, actor,
                       {"owner": owner_id, "kwh": c.energy_quantity_kwh}, c.microgrid_id)
    queue_event(session, "CERTIFICATE_EVENT", c.microgrid_id, {"certificate_id": c.id, "status": c.status})
    return c


async def revoke_certificate(session: AsyncSession, cert_id: uuid.UUID, reason: str, actor: str) -> RenewableCertificate:
    c = await _get_locked(session, cert_id)
    if c.status == CS.RETIRED.value:
        raise Conflict("a retired certificate cannot be revoked")
    c.status = CS.REVOKED.value
    await session.flush()
    await audit.record(session, "CERTIFICATE_REVOKED", "RenewableCertificate", c.id, actor, {"reason": reason}, c.microgrid_id)
    return c
