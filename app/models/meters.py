"""Smart meters and immutable meter readings."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import MeterStatus
from app.db.base import Base, IdMixin, TimestampMixin


class SmartMeter(Base, IdMixin, TimestampMixin):
    __tablename__ = "smart_meters"
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("energy_assets.id"), index=True)
    meter_identifier: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(16), default=MeterStatus.ACTIVE.value)
    last_seen: Mapped[datetime | None] = mapped_column(nullable=True)
    trusted: Mapped[bool] = mapped_column(Boolean, default=True)
    public_key: Mapped[str | None] = mapped_column(String(256), nullable=True)


class MeterReading(Base, IdMixin):
    """Immutable. Any ORM UPDATE/DELETE is rejected; corrections go to MeterCorrection."""
    __tablename__ = "meter_readings"
    __table_args__ = (UniqueConstraint("meter_id", "timestamp", name="uq_reading_meter_ts"),)
    meter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("smart_meters.id"), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("energy_assets.id"), index=True)
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(index=True)  # interval END
    interval_minutes: Mapped[int] = mapped_column(default=15)
    generation_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    consumption_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    import_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    export_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    battery_charge_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    battery_discharge_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    verified: Mapped[bool] = mapped_column(Boolean, default=True)
    signature: Mapped[str | None] = mapped_column(String(256), nullable=True)
    verification_meta: Mapped[dict] = mapped_column(default=dict)
    received_at: Mapped[datetime] = mapped_column()


class MeterCorrection(Base, IdMixin):
    """Append-only correction record; effective value = latest correction else original."""
    __tablename__ = "meter_corrections"
    reading_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meter_readings.id"), index=True)
    field: Mapped[str] = mapped_column(String(32))
    old_value: Mapped[float] = mapped_column(Float)
    new_value: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(500))
    actor: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column()


class ImmutableRecordError(RuntimeError):
    pass


@event.listens_for(MeterReading, "before_update")
def _reject_update(mapper, connection, target):  # pragma: no cover - exercised in tests
    raise ImmutableRecordError("meter readings are immutable; create a MeterCorrection")


@event.listens_for(MeterReading, "before_delete")
def _reject_delete(mapper, connection, target):  # pragma: no cover
    raise ImmutableRecordError("meter readings cannot be deleted")
