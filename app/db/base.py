"""Declarative base and portable column types (PostgreSQL in prod, SQLite in tests)."""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, Numeric, TypeDecorator, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.timeutil import ensure_utc, utcnow


class UTCDateTime(TypeDecorator):
    """Stores timestamps as timezone-aware UTC; re-attaches UTC on read (SQLite drops tz)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return ensure_utc(value) if value is not None else None

    def process_result_value(self, value, dialect):
        return ensure_utc(value) if value is not None else None


MONEY = Numeric(20, 8, asdecimal=True)      # prices / currency
ENERGY = Numeric(18, 6, asdecimal=True)     # kWh persisted exactly where it affects settlement


class Base(DeclarativeBase):
    type_annotation_map = {
        dict: JSON,
        list: JSON,
        datetime: UTCDateTime,
        uuid.UUID: Uuid,
        Decimal: MONEY,
    }


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
