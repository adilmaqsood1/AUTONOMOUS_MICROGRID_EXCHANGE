"""Core topology models: users, microgrids, participants, grid nodes/lines.

Numeric convention: physical quantities (kW, kWh telemetry, percentages) are float and
documented as such; prices, balances and settled/committed energy are Decimal.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, Float, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import NodeType, OperatingMode, ParticipantType, UserRole
from app.db.base import Base, IdMixin, TimestampMixin


class User(Base, IdMixin, TimestampMixin):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default=UserRole.PARTICIPANT.value)
    participant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("participants.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Microgrid(Base, IdMixin, TimestampMixin):
    __tablename__ = "microgrids"
    name: Mapped[str] = mapped_column(String(120), unique=True)
    location_label: Mapped[str] = mapped_column(String(200), default="")
    operating_mode: Mapped[str] = mapped_column(String(24), default=OperatingMode.GRID_CONNECTED.value)
    external_grid_connected: Mapped[bool] = mapped_column(Boolean, default=True)
    max_grid_import_kw: Mapped[float] = mapped_column(Float, default=0.0)
    max_grid_export_kw: Mapped[float] = mapped_column(Float, default=0.0)
    current_generation_kw: Mapped[float] = mapped_column(Float, default=0.0)
    current_demand_kw: Mapped[float] = mapped_column(Float, default=0.0)
    current_renewable_kw: Mapped[float] = mapped_column(Float, default=0.0)
    current_storage_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    # optimistic locking for mode transitions
    version: Mapped[int] = mapped_column(default=1)
    __mapper_args__ = {"version_id_col": version}


class Participant(Base, IdMixin, TimestampMixin):
    __tablename__ = "participants"
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(24), default=ParticipantType.CONSUMER.value)
    wallet_id: Mapped[str] = mapped_column(String(120), unique=True)
    reliability_score: Mapped[float] = mapped_column(Float, default=0.8)
    available_balance: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    locked_balance: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    preferences: Mapped[dict] = mapped_column(default=dict)
    # reliability counters (Phase: reputation)
    commitments_total: Mapped[int] = mapped_column(default=0)
    deliveries_success: Mapped[int] = mapped_column(default=0)
    deliveries_partial: Mapped[int] = mapped_column(default=0)
    deliveries_failed: Mapped[int] = mapped_column(default=0)
    delivery_ratio_sum: Mapped[float] = mapped_column(Float, default=0.0)
    imbalance_kwh: Mapped[float] = mapped_column(Float, default=0.0)


class GridNode(Base, IdMixin):
    __tablename__ = "grid_nodes"
    __table_args__ = (UniqueConstraint("microgrid_id", "name"),)
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    voltage_level: Mapped[float] = mapped_column(Float, default=0.4)  # kV
    node_type: Mapped[str] = mapped_column(String(16), default=NodeType.BUS.value)

    @property
    def voltage_kv(self) -> float:
        return float(self.voltage_level or 0.4)


class GridLine(Base, IdMixin):
    __tablename__ = "grid_lines"
    __table_args__ = (Index("ix_lines_mg", "microgrid_id"),)
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"))
    from_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("grid_nodes.id"))
    to_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("grid_nodes.id"))
    capacity_kw: Mapped[float] = mapped_column(Float)
    current_flow_kw: Mapped[float] = mapped_column(Float, default=0.0)  # signed: from->to positive
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def name(self) -> str:
        return f"Line-{str(self.id)[:8]}"

    @property
    def reactance_ohms(self) -> float:
        return 0.1

    @property
    def resistance_ohms(self) -> float:
        return 0.05

