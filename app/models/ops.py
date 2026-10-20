"""Market, commitment, forecast, optimization, risk, balancing, resilience, ledger,
provenance, audit and simulation persistence models."""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, Float, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import CertificateStatus, CommitmentStatus, OrderStatus
from app.db.base import ENERGY, MONEY, Base, IdMixin, TimestampMixin


# ------------------------------------------------------------------ market
class EnergyOrder(Base, IdMixin):
    __tablename__ = "energy_orders"
    __table_args__ = (Index("ix_orders_book", "microgrid_id", "status", "delivery_window_start"),)
    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participants.id"), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("energy_assets.id"))
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"))
    side: Mapped[str] = mapped_column(String(4))
    quantity_kwh: Mapped[Decimal] = mapped_column(ENERGY)
    remaining_quantity_kwh: Mapped[Decimal] = mapped_column(ENERGY)
    limit_price: Mapped[Decimal] = mapped_column(MONEY)
    delivery_window_start: Mapped[datetime] = mapped_column()
    delivery_window_end: Mapped[datetime] = mapped_column()
    renewable_only: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default=OrderStatus.OPEN.value)
    sequence: Mapped[int] = mapped_column(default=0)  # strict time priority tiebreaker
    reject_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column()
    version: Mapped[int] = mapped_column(default=1)
    __mapper_args__ = {"version_id_col": version}


class MarketClearing(Base, IdMixin):
    __tablename__ = "market_clearings"
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    delivery_window_start: Mapped[datetime] = mapped_column()
    delivery_window_end: Mapped[datetime] = mapped_column()
    clearing_price: Mapped[Decimal | None] = mapped_column(nullable=True)
    matched_kwh: Mapped[Decimal] = mapped_column(ENERGY, default=Decimal("0"))
    grid_rejected_kwh: Mapped[Decimal] = mapped_column(ENERGY, default=Decimal("0"))
    risk_rejected_kwh: Mapped[Decimal] = mapped_column(ENERGY, default=Decimal("0"))
    input_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column()


class Trade(Base, IdMixin):
    __tablename__ = "trades"
    clearing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market_clearings.id"), index=True)
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    buy_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("energy_orders.id"))
    sell_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("energy_orders.id"))
    quantity_kwh: Mapped[Decimal] = mapped_column(ENERGY)
    price: Mapped[Decimal] = mapped_column()
    created_at: Mapped[datetime] = mapped_column()


class EnergyCommitment(Base, IdMixin):
    __tablename__ = "energy_commitments"
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    trade_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("trades.id"), nullable=True, unique=True)
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participants.id"), index=True)
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participants.id"), index=True)
    buyer_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("energy_assets.id"))
    seller_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("energy_assets.id"))
    quantity_kwh: Mapped[Decimal] = mapped_column(ENERGY)
    price: Mapped[Decimal] = mapped_column()
    delivery_start: Mapped[datetime] = mapped_column()
    delivery_end: Mapped[datetime] = mapped_column()
    status: Mapped[str] = mapped_column(String(12), default=CommitmentStatus.PENDING.value)
    blockchain_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    locked_buyer_funds: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    locked_seller_collateral: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    delivered_kwh: Mapped[Decimal | None] = mapped_column(ENERGY, nullable=True)
    delivery_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    renewable: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column()
    version: Mapped[int] = mapped_column(default=1)
    __mapper_args__ = {"version_id_col": version}


# ------------------------------------------------------------- intelligence
class Forecast(Base, IdMixin):
    __tablename__ = "forecasts"
    __table_args__ = (Index("ix_forecast_lookup", "microgrid_id", "kind", "target_time"),)
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"))
    asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("energy_assets.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(16))  # GENERATION / DEMAND / PRICE
    target_time: Mapped[datetime] = mapped_column()  # start of forecast interval
    interval_minutes: Mapped[int] = mapped_column(default=60)
    horizon_minutes: Mapped[int] = mapped_column()
    predicted_value: Mapped[float] = mapped_column(Float)  # kWh over interval (price: currency/kWh)
    lower_bound: Mapped[float] = mapped_column(Float)
    upper_bound: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    model_name: Mapped[str] = mapped_column(String(60))
    generated_at: Mapped[datetime] = mapped_column()
    actual_value: Mapped[float | None] = mapped_column(Float, nullable=True)


class OptimizationRun(Base, IdMixin):
    __tablename__ = "optimization_runs"
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    objective: Mapped[dict] = mapped_column(default=dict)
    inputs: Mapped[dict] = mapped_column(default=dict)
    constraints: Mapped[dict] = mapped_column(default=dict)
    result: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(24))
    mode: Mapped[str] = mapped_column(String(24))
    execution_time_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column()


class RiskDecision(Base, IdMixin):
    __tablename__ = "risk_decisions"
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(64))
    approved: Mapped[bool] = mapped_column(Boolean)
    reason_code: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(String(500))
    requested_kwh: Mapped[Decimal] = mapped_column(ENERGY)
    approved_kwh: Mapped[Decimal] = mapped_column(ENERGY)
    checks: Mapped[dict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column()


class DecisionRecord(Base, IdMixin):
    """Machine-readable explanation produced at decision time by the rule/optimizer output."""
    __tablename__ = "decision_records"
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    actor: Mapped[str] = mapped_column(String(80))
    decision: Mapped[str] = mapped_column(String(40))
    quantity_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    reason_codes: Mapped[dict] = mapped_column(default=dict)
    inputs: Mapped[dict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column()


class BalancingAction(Base, IdMixin):
    __tablename__ = "balancing_actions"
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    commitment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("energy_commitments.id"), nullable=True)
    source_type: Mapped[str] = mapped_column(String(24))
    source_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("energy_assets.id"), nullable=True)
    quantity_kwh: Mapped[Decimal] = mapped_column(ENERGY)
    price: Mapped[Decimal] = mapped_column()
    cost: Mapped[Decimal] = mapped_column()
    deficit_kwh: Mapped[Decimal] = mapped_column(ENERGY)
    interval_start: Mapped[datetime] = mapped_column()
    interval_end: Mapped[datetime] = mapped_column()
    explanation: Mapped[dict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column()


class DemandResponseEvent(Base, IdMixin):
    __tablename__ = "demand_response_events"
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("energy_assets.id"))
    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participants.id"))
    avoided_kwh: Mapped[Decimal] = mapped_column(ENERGY)
    compensation: Mapped[Decimal] = mapped_column()
    kind: Mapped[str] = mapped_column(String(24))  # CURTAIL / POSTPONE
    reason: Mapped[str] = mapped_column(String(80))
    interval_start: Mapped[datetime] = mapped_column()
    interval_end: Mapped[datetime] = mapped_column()
    paid: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column()


class LoadSheddingDecision(Base, IdMixin):
    __tablename__ = "load_shedding_decisions"
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("energy_assets.id"))
    action: Mapped[str] = mapped_column(String(12))  # SHED / RESTORE / CURTAIL
    priority: Mapped[str] = mapped_column(String(12))
    quantity_kw: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(200))
    mode: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column()


# ------------------------------------------------------------------ ledger
class LedgerAccount(Base, IdMixin):
    __tablename__ = "ledger_accounts"
    __table_args__ = (UniqueConstraint("owner_id", "kind", name="uq_account_owner_kind"),)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("participants.id"), nullable=True)
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"))
    kind: Mapped[str] = mapped_column(String(24))
    balance: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    version: Mapped[int] = mapped_column(default=1)
    __mapper_args__ = {"version_id_col": version}


class LedgerTransaction(Base, IdMixin):
    __tablename__ = "ledger_transactions"
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"))
    kind: Mapped[str] = mapped_column(String(32))
    reference_type: Mapped[str] = mapped_column(String(32))
    reference_id: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str] = mapped_column(String(300), default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column()


class LedgerEntry(Base, IdMixin):
    __tablename__ = "ledger_entries"
    transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ledger_transactions.id"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ledger_accounts.id"), index=True)
    amount: Mapped[Decimal] = mapped_column()  # signed; sum per transaction == 0


class Settlement(Base, IdMixin):
    __tablename__ = "settlements"
    commitment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("energy_commitments.id"), unique=True)  # one settlement per commitment
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    committed_kwh: Mapped[Decimal] = mapped_column(ENERGY)
    delivered_kwh: Mapped[Decimal] = mapped_column(ENERGY)
    shortfall_kwh: Mapped[Decimal] = mapped_column(ENERGY)
    delivery_ratio: Mapped[float] = mapped_column(Float)
    gross_amount: Mapped[Decimal] = mapped_column()
    seller_payment: Mapped[Decimal] = mapped_column()
    buyer_refund: Mapped[Decimal] = mapped_column()
    penalty: Mapped[Decimal] = mapped_column()
    blockchain_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column()


# -------------------------------------------------------------- provenance
class RenewableCertificate(Base, IdMixin):
    __tablename__ = "renewable_certificates"
    source_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("energy_assets.id"), index=True)
    meter_reading_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meter_readings.id"), unique=True)  # one issuance per verified reading
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"))
    energy_quantity_kwh: Mapped[Decimal] = mapped_column(ENERGY)
    generation_timestamp: Mapped[datetime] = mapped_column()
    renewable_source: Mapped[str] = mapped_column(String(24))
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participants.id"), index=True)
    status: Mapped[str] = mapped_column(String(12), default=CertificateStatus.ISSUED.value)
    blockchain_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column()
    version: Mapped[int] = mapped_column(default=1)
    __mapper_args__ = {"version_id_col": version}


# ------------------------------------------------------------------- audit
class AuditEvent(Base, IdMixin):
    """Append-only (enforced at ORM level + Postgres trigger in the migration)."""
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)
    timestamp: Mapped[datetime] = mapped_column(index=True)
    microgrid_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    actor: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict] = mapped_column(default=dict)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    scope: Mapped[str] = mapped_column(String(40))
    response: Mapped[dict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column()


# -------------------------------------------------------------- simulation
class SimulationState(Base):
    __tablename__ = "simulation_state"
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), primary_key=True)
    running: Mapped[bool] = mapped_column(Boolean, default=False)
    seed: Mapped[int] = mapped_column(default=42)
    sim_time: Mapped[datetime] = mapped_column()
    step_minutes: Mapped[int] = mapped_column(default=15)
    speed_multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    config: Mapped[dict] = mapped_column(default=dict)
    steps_executed: Mapped[int] = mapped_column(default=0)


class ScenarioEvent(Base, IdMixin):
    __tablename__ = "scenario_events"
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    type: Mapped[str] = mapped_column(String(32))
    severity: Mapped[float] = mapped_column(Float, default=1.0)
    start_time: Mapped[datetime] = mapped_column()
    end_time: Mapped[datetime | None] = mapped_column(nullable=True)
    target_asset_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    params: Mapped[dict] = mapped_column(default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column()


# -------------------------------------------------------- operating reserves (Phase 2)
class ReserveOffer(Base, IdMixin):
    """Capacity offered for operating reserve / ancillary services."""
    __tablename__ = "reserve_offers"
    __table_args__ = (Index("ix_reserve_offers_book", "microgrid_id", "status", "window_start"),)
    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participants.id"), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("energy_assets.id"), index=True)
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    reserve_type: Mapped[str] = mapped_column(String(32))  # FREQUENCY_CONTAINMENT, SPINNING_RESERVE, etc.
    capacity_kw: Mapped[float] = mapped_column(Float)
    response_time_seconds: Mapped[float] = mapped_column(Float)
    price_per_kw: Mapped[Decimal] = mapped_column(MONEY)
    window_start: Mapped[datetime] = mapped_column()
    window_end: Mapped[datetime] = mapped_column()
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN, COMMITTED, EXPIRED, CANCELLED
    created_at: Mapped[datetime] = mapped_column()


class ReserveCommitment(Base, IdMixin):
    """Procured reserve capacity obligation."""
    __tablename__ = "reserve_commitments"
    __table_args__ = (Index("ix_reserve_commitments_asset", "asset_id", "window_start"),)
    offer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reserve_offers.id"), index=True)
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("energy_assets.id"), index=True)
    allocated_kw: Mapped[float] = mapped_column(Float)
    clearing_price_per_kw: Mapped[Decimal] = mapped_column(MONEY)
    total_payment: Mapped[Decimal] = mapped_column(MONEY)
    response_time_seconds: Mapped[float] = mapped_column(Float)
    window_start: Mapped[datetime] = mapped_column()
    window_end: Mapped[datetime] = mapped_column()
    status: Mapped[str] = mapped_column(String(20), default="COMMITTED")
    created_at: Mapped[datetime] = mapped_column()


# --------------------------------------------- market surveillance & manipulation (Phase 2)
class MarketSurveillanceAlert(Base, IdMixin):
    """AI Surveillance flag for withholding, gaming, or anomaly."""
    __tablename__ = "market_surveillance_alerts"
    __table_args__ = (Index("ix_surveillance_participant", "participant_id", "timestamp"),)
    microgrid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("microgrids.id"), index=True)
    participant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("participants.id"), nullable=True, index=True)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("energy_assets.id"), nullable=True, index=True)
    alert_type: Mapped[str] = mapped_column(String(40))  # CAPACITY_WITHHOLDING, CONGESTION_GAMING, etc.
    severity: Mapped[str] = mapped_column(String(16))    # LOW, MEDIUM, HIGH, CRITICAL
    score: Mapped[float] = mapped_column(Float)          # 0.0 - 1.0 or anomaly z-score
    description: Mapped[str] = mapped_column(String(500))
    evidence: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN, REVIEWED, DISMISSED
    timestamp: Mapped[datetime] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column()

