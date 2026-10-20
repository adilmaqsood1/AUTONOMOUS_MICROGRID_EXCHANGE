"""Energy assets (composition: base row + one 1:1 properties table per capability)."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import AssetStatus, LoadPriority
from app.db.base import Base, IdMixin, TimestampMixin


class EnergyAsset(Base, IdMixin, TimestampMixin):
    __tablename__ = "energy_assets"
    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participants.id"), index=True)
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    grid_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("grid_nodes.id"), index=True)
    asset_type: Mapped[str] = mapped_column(String(24))
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default=AssetStatus.ONLINE.value)
    rated_power_kw: Mapped[float] = mapped_column(Float, default=0.0)

    generator: Mapped["GeneratorProps | None"] = relationship(uselist=False, lazy="selectin")
    battery: Mapped["BatteryProps | None"] = relationship(uselist=False, lazy="selectin")
    ev: Mapped["EVProps | None"] = relationship(uselist=False, lazy="selectin")
    load: Mapped["LoadProps | None"] = relationship(uselist=False, lazy="selectin")


class GeneratorProps(Base):
    __tablename__ = "generator_props"
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("energy_assets.id"), primary_key=True)
    maximum_generation_kw: Mapped[float] = mapped_column(Float)
    minimum_generation_kw: Mapped[float] = mapped_column(Float, default=0.0)
    renewable: Mapped[bool] = mapped_column(Boolean, default=True)
    marginal_cost: Mapped[float] = mapped_column(Float, default=0.0)  # currency/kWh
    current_output_kw: Mapped[float] = mapped_column(Float, default=0.0)


class BatteryProps(Base):
    __tablename__ = "battery_props"
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("energy_assets.id"), primary_key=True)
    capacity_kwh: Mapped[float] = mapped_column(Float)
    current_soc_percent: Mapped[float] = mapped_column(Float, default=50.0)
    minimum_soc_percent: Mapped[float] = mapped_column(Float, default=20.0)
    maximum_soc_percent: Mapped[float] = mapped_column(Float, default=95.0)
    maximum_charge_kw: Mapped[float] = mapped_column(Float)
    maximum_discharge_kw: Mapped[float] = mapped_column(Float)
    round_trip_efficiency: Mapped[float] = mapped_column(Float, default=0.9)
    degradation_cost_per_kwh: Mapped[float] = mapped_column(Float, default=0.02)
    community: Mapped[bool] = mapped_column(Boolean, default=False)
    # kWh already promised to commitments / dispatch (prevents double allocation)
    allocated_discharge_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    allocated_charge_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    version: Mapped[int] = mapped_column(default=1)
    __mapper_args__ = {"version_id_col": version}


class EVProps(Base):
    __tablename__ = "ev_props"
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("energy_assets.id"), primary_key=True)
    battery_capacity_kwh: Mapped[float] = mapped_column(Float)
    soc_percent: Mapped[float] = mapped_column(Float, default=50.0)
    minimum_required_soc_percent: Mapped[float] = mapped_column(Float, default=80.0)
    minimum_reserve_soc_percent: Mapped[float] = mapped_column(Float, default=20.0)
    departure_time: Mapped[datetime | None] = mapped_column(nullable=True)
    maximum_charge_rate_kw: Mapped[float] = mapped_column(Float, default=7.4)
    maximum_discharge_rate_kw: Mapped[float] = mapped_column(Float, default=0.0)
    v2g_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    connected: Mapped[bool] = mapped_column(Boolean, default=True)
    allocated_discharge_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    version: Mapped[int] = mapped_column(default=1)
    __mapper_args__ = {"version_id_col": version}


class LoadProps(Base):
    __tablename__ = "load_props"
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("energy_assets.id"), primary_key=True)
    current_demand_kw: Mapped[float] = mapped_column(Float, default=0.0)
    base_demand_kw: Mapped[float] = mapped_column(Float, default=0.0)
    flexible: Mapped[bool] = mapped_column(Boolean, default=False)
    maximum_curtailment_kw: Mapped[float] = mapped_column(Float, default=0.0)
    maximum_delay_minutes: Mapped[int] = mapped_column(default=0)
    minimum_service_fraction: Mapped[float] = mapped_column(Float, default=0.0)
    compensation_per_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    priority: Mapped[str] = mapped_column(String(12), default=LoadPriority.NORMAL.value)
    load_kind: Mapped[str] = mapped_column(String(16), default="RESIDENTIAL")
    curtailed_kw: Mapped[float] = mapped_column(Float, default=0.0)  # currently shed/curtailed
