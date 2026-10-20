"""Pydantic v2 schemas for all microgrid exchange domain objects and API contracts."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import (
    AssetType, CommitmentStatus, OperatingMode, OrderSide, OrderStatus,
    ParticipantType, ScenarioType, UserRole
)


# ---------------------------------------------------------------- Auth
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    participant_id: Optional[uuid.UUID] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str
    microgrid_id: uuid.UUID
    participant_type: ParticipantType = ParticipantType.CONSUMER


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    role: str
    participant_id: Optional[uuid.UUID] = None
    created_at: datetime


# ---------------------------------------------------------------- Microgrid
class MicrogridResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    location_label: str
    operating_mode: str
    external_grid_connected: bool
    max_grid_import_kw: float
    max_grid_export_kw: float
    current_generation_kw: float
    current_demand_kw: float
    current_renewable_kw: float
    current_storage_kwh: float
    status: str
    version: int


class MicrogridUpdate(BaseModel):
    operating_mode: Optional[str] = None
    max_grid_import_kw: Optional[float] = None
    max_grid_export_kw: Optional[float] = None
    external_grid_connected: Optional[bool] = None


# ---------------------------------------------------------------- Participants
class ParticipantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    microgrid_id: uuid.UUID
    name: str
    type: str
    reliability_score: float
    available_balance: Decimal
    locked_balance: Decimal
    active: bool
    preferences: Dict[str, Any]


class ParticipantUpdatePreferences(BaseModel):
    preferences: Dict[str, Any]


# ---------------------------------------------------------------- Assets
class GeneratorPropsSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    maximum_generation_kw: float
    minimum_generation_kw: float
    renewable: bool
    marginal_cost: float
    current_output_kw: float


class BatteryPropsSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    capacity_kwh: float
    current_soc_percent: float
    minimum_soc_percent: float
    maximum_soc_percent: float
    maximum_charge_kw: float
    maximum_discharge_kw: float
    round_trip_efficiency: float
    degradation_cost_per_kwh: float
    community: bool


class EVPropsSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    battery_capacity_kwh: float
    soc_percent: float
    minimum_required_soc_percent: float
    departure_time: Optional[datetime] = None
    maximum_charge_rate_kw: float
    maximum_discharge_rate_kw: float
    v2g_enabled: bool
    connected: bool


class LoadPropsSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    base_demand_kw: float
    current_demand_kw: float
    flexible: bool
    maximum_curtailment_kw: float
    compensation_per_kwh: float
    priority: str
    load_kind: str


class AssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    microgrid_id: uuid.UUID
    participant_id: uuid.UUID
    grid_node_id: uuid.UUID
    name: str
    asset_type: str
    status: str
    rated_power_kw: float
    generator: Optional[GeneratorPropsSchema] = None
    battery: Optional[BatteryPropsSchema] = None
    ev: Optional[EVPropsSchema] = None
    load: Optional[LoadPropsSchema] = None


# ---------------------------------------------------------------- Grid
class GridNodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    microgrid_id: uuid.UUID
    name: str
    node_type: str
    voltage_kv: float = 0.4


class GridLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    microgrid_id: uuid.UUID
    name: str = ""
    from_node_id: uuid.UUID
    to_node_id: uuid.UUID
    capacity_kw: float
    reactance_ohms: float = 0.1
    resistance_ohms: float = 0.05
    active: bool = True


# ---------------------------------------------------------------- Smart Meters & Readings
class SmartMeterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    asset_id: uuid.UUID
    meter_identifier: str
    status: str
    trusted: bool
    last_seen: Optional[datetime] = None


class MeterReadingIngest(BaseModel):
    meter_identifier: str
    timestamp: datetime
    interval_minutes: int = 15
    generation_kwh: float = 0.0
    consumption_kwh: float = 0.0
    import_kwh: float = 0.0
    export_kwh: float = 0.0
    battery_charge_kwh: float = 0.0
    battery_discharge_kwh: float = 0.0
    signature: Optional[str] = None
    extra_meta: Optional[Dict[str, Any]] = None


class MeterReadingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    meter_id: uuid.UUID
    asset_id: uuid.UUID
    microgrid_id: uuid.UUID
    timestamp: datetime
    interval_minutes: int
    generation_kwh: float
    consumption_kwh: float
    import_kwh: float
    export_kwh: float
    battery_charge_kwh: float
    battery_discharge_kwh: float
    verified: bool
    signature: Optional[str] = None
    verification_meta: Dict[str, Any]
    received_at: datetime


class MeterCorrectionCreate(BaseModel):
    reading_id: uuid.UUID
    field: str
    new_value: float
    reason: str


class MeterCorrectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    reading_id: uuid.UUID
    field: str
    old_value: float
    new_value: float
    reason: str
    actor: str
    created_at: datetime


# ---------------------------------------------------------------- Forecasts
class ForecastResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    microgrid_id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    model_name: str
    window_start: datetime
    window_end: datetime
    predicted_value: float
    lower_bound: float
    upper_bound: float
    confidence: float
    created_at: datetime


# ---------------------------------------------------------------- Orders & OrderBook
class OrderCreate(BaseModel):
    asset_id: uuid.UUID
    side: OrderSide
    quantity_kwh: Decimal = Field(..., gt=0)
    limit_price: Decimal = Field(..., gt=0)
    delivery_window_start: datetime
    delivery_window_end: datetime
    renewable_only: bool = False


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    participant_id: uuid.UUID
    asset_id: uuid.UUID
    microgrid_id: uuid.UUID
    side: str
    quantity_kwh: Decimal
    remaining_quantity_kwh: Decimal
    limit_price: Decimal
    delivery_window_start: datetime
    delivery_window_end: datetime
    renewable_only: bool
    status: str
    reject_reason: Optional[str] = None
    sequence: int
    created_at: datetime


class OrderBookEntry(BaseModel):
    id: str
    participant_id: str
    asset_id: str
    price: str
    remaining_kwh: str
    sequence: int
    renewable_only: bool
    window_start: str
    window_end: str


class OrderBookResponse(BaseModel):
    buy_book: List[OrderBookEntry]
    sell_book: List[OrderBookEntry]


# ---------------------------------------------------------------- Market Clearing, Trades & Commitments
class ClearingRunRequest(BaseModel):
    microgrid_id: uuid.UUID
    window_start: datetime
    window_end: datetime


class MarketClearingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    microgrid_id: uuid.UUID
    delivery_window_start: datetime
    delivery_window_end: datetime
    clearing_price: Optional[Decimal] = None
    matched_kwh: Decimal
    grid_rejected_kwh: Decimal
    risk_rejected_kwh: Decimal
    input_hash: str
    created_at: datetime
    result: Dict[str, Any]


class TradeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    clearing_id: uuid.UUID
    microgrid_id: uuid.UUID
    buy_order_id: uuid.UUID
    sell_order_id: uuid.UUID
    quantity_kwh: Decimal
    price: Decimal
    created_at: datetime


class CommitmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    microgrid_id: uuid.UUID
    trade_id: uuid.UUID
    buyer_participant_id: uuid.UUID
    seller_participant_id: uuid.UUID
    buyer_asset_id: uuid.UUID
    seller_asset_id: uuid.UUID
    quantity_kwh: Decimal
    price: Decimal
    delivery_start: datetime
    delivery_end: datetime
    renewable: bool
    locked_buyer_funds: Decimal
    locked_seller_collateral: Decimal
    status: str
    delivered_kwh: Decimal
    delivery_ratio: float
    blockchain_reference: str
    version: int


# ---------------------------------------------------------------- Optimization
class OptimizationTrigger(BaseModel):
    microgrid_id: uuid.UUID
    steps: int = 6
    step_minutes: int = 60
    start: Optional[datetime] = None
    weights: Optional[Dict[str, float]] = None


class OptimizationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    microgrid_id: uuid.UUID
    timestamp: datetime
    objective_type: str
    status: str
    total_cost: Optional[float] = None
    inputs: Dict[str, Any]
    constraints: Dict[str, Any]
    result: Dict[str, Any]
    decision_summary: Dict[str, Any]


# ---------------------------------------------------------------- Risk
class RiskDecisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    microgrid_id: uuid.UUID
    subject_type: str
    subject_id: str
    approved: bool
    reason_code: str
    message: str
    requested_kwh: Decimal
    approved_kwh: Decimal
    checks: Dict[str, Any]
    created_at: datetime


# ---------------------------------------------------------------- Balancing
class BalancingActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    microgrid_id: uuid.UUID
    commitment_id: Optional[uuid.UUID] = None
    action_type: str
    provider_asset_id: Optional[uuid.UUID] = None
    quantity_kwh: Decimal
    cost: Decimal
    timestamp: datetime
    reason: str


class BalancingTrigger(BaseModel):
    microgrid_id: uuid.UUID
    window_end: datetime


# ---------------------------------------------------------------- Demand Response
class DemandResponseEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    microgrid_id: uuid.UUID
    asset_id: uuid.UUID
    start_time: datetime
    end_time: datetime
    curtailed_kw: float
    avoided_kwh: float
    compensation_paid: Decimal
    trigger_reason: str


# ---------------------------------------------------------------- Settlement & Ledger
class SettleCommitmentRequest(BaseModel):
    commitment_id: uuid.UUID


class SettlementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    microgrid_id: uuid.UUID
    commitment_id: uuid.UUID
    seller_participant_id: uuid.UUID
    buyer_participant_id: uuid.UUID
    delivered_kwh: Decimal
    shortfall_kwh: Decimal
    delivery_ratio: float
    gross_amount: Decimal
    seller_payment: Decimal
    buyer_refund: Decimal
    penalty: Decimal
    balancing_cost: Decimal
    settled_at: datetime
    blockchain_reference: str


class LedgerAccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    microgrid_id: uuid.UUID
    participant_id: Optional[uuid.UUID] = None
    kind: str
    currency: str
    balance: Decimal


class LedgerEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    account_id: uuid.UUID
    amount: Decimal


class LedgerTransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    microgrid_id: uuid.UUID
    kind: str
    reference: str
    description: str
    created_at: datetime
    entries: List[LedgerEntryResponse] = []


# ---------------------------------------------------------------- Provenance / Renewable Certificates
class CertificateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    microgrid_id: uuid.UUID
    source_asset_id: uuid.UUID
    owner_id: uuid.UUID
    energy_quantity_kwh: Decimal
    generation_timestamp: datetime
    renewable_source: str
    status: str
    blockchain_reference: str
    created_at: datetime


class CertificateTransfer(BaseModel):
    to_owner: uuid.UUID


class CertificateRetire(BaseModel):
    pass


class CertificateIssueRequest(BaseModel):
    reading_id: uuid.UUID


# ---------------------------------------------------------------- Resilience
class OutageDeclare(BaseModel):
    reason: str = "utility outage"


class ModeTransitionRequest(BaseModel):
    mode: OperatingMode


class ResilienceStatus(BaseModel):
    operating_mode: str
    external_grid_connected: bool
    max_grid_import_kw: float
    max_grid_export_kw: float


# ---------------------------------------------------------------- Simulation
class SimulationStatusResponse(BaseModel):
    running: bool
    speed_multiplier: float
    sim_time: datetime
    seed: int
    operating_mode: str
    cloud_cover: float
    wind_speed_m_s: float
    temperature_c: float
    external_grid_connected: bool


class SimulationStepRequest(BaseModel):
    minutes: int = 15


class SimulationSpeedRequest(BaseModel):
    speed: float = Field(..., gt=0)


class SimulationResetRequest(BaseModel):
    seed: Optional[int] = None
    start_time: Optional[datetime] = None


# ---------------------------------------------------------------- Scenarios
class ScenarioInjectRequest(BaseModel):
    scenario_type: ScenarioType
    severity: float = Field(..., ge=0.0, le=1.0)
    duration_minutes: Optional[int] = None
    params: Optional[Dict[str, Any]] = None


class ScenarioPresetRequest(BaseModel):
    preset_key: str = Field(..., description="A, B, C, D, or E")


class ScenarioEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    microgrid_id: uuid.UUID
    type: str
    severity: float
    start_time: datetime
    end_time: Optional[datetime] = None
    active: bool
    params: Dict[str, Any]
    target_asset_id: Optional[uuid.UUID] = None
    created_at: datetime


# ---------------------------------------------------------------- Audit
class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    microgrid_id: Optional[uuid.UUID] = None
    event_type: str
    entity_type: str
    entity_id: str
    actor: str
    timestamp: datetime
    correlation_id: Optional[str] = None
    payload: Dict[str, Any]


# ---------------------------------------------------------------- Phase 2: Locational Marginal Pricing & Congestion
class LMPNodePrice(BaseModel):
    node_id: str
    node_name: str
    lmp_per_kwh: float
    energy_component: float
    congestion_component: float
    loss_component: float = 0.0


class LMPResponse(BaseModel):
    microgrid_id: uuid.UUID
    timestamp: datetime
    reference_lmp: float
    nodal_lmps: List[LMPNodePrice]


class LineCongestionRent(BaseModel):
    line_id: str
    from_node: str
    to_node: str
    flow_kw: float
    capacity_kw: float
    loading_percent: float
    congestion_rent: float


class CongestionRentsResponse(BaseModel):
    microgrid_id: uuid.UUID
    timestamp: datetime
    total_congestion_rent: float
    lines: List[LineCongestionRent]


# ---------------------------------------------------------------- Phase 2: Operating Reserve Market
class ReserveOfferCreate(BaseModel):
    asset_id: uuid.UUID
    reserve_type: str = Field(..., description="FREQUENCY_CONTAINMENT, SPINNING_RESERVE, NON_SPINNING, REPLACEMENT")
    capacity_kw: float = Field(..., gt=0)
    response_time_seconds: float = Field(..., gt=0)
    price_per_kw: Decimal = Field(..., gt=0)
    window_start: datetime
    window_end: datetime


class ReserveOfferResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    participant_id: uuid.UUID
    asset_id: uuid.UUID
    microgrid_id: uuid.UUID
    reserve_type: str
    capacity_kw: float
    response_time_seconds: float
    price_per_kw: Decimal
    status: str
    window_start: datetime
    window_end: datetime
    created_at: datetime


class ReserveProcurementRequest(BaseModel):
    microgrid_id: uuid.UUID
    required_capacity_kw: float = Field(..., gt=0)
    max_response_time_seconds: float = Field(default=30.0, gt=0)
    window_start: datetime
    window_end: datetime


class ReserveCommitmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    offer_id: uuid.UUID
    microgrid_id: uuid.UUID
    asset_id: uuid.UUID
    allocated_kw: float
    clearing_price_per_kw: Decimal
    total_payment: Decimal
    response_time_seconds: float
    window_start: datetime
    window_end: datetime
    status: str
    created_at: datetime


class ReserveMarketClearResponse(BaseModel):
    microgrid_id: uuid.UUID
    required_kw: float
    procured_kw: float
    clearing_price_per_kw: Decimal
    total_cost: Decimal
    commitments: List[ReserveCommitmentResponse]


# ---------------------------------------------------------------- Phase 2: Stochastic Dispatch
class StochasticScenarioInput(BaseModel):
    name: str
    probability: float
    solar_multiplier: float = 1.0
    wind_multiplier: float = 1.0
    load_multiplier: float = 1.0


class StochasticDispatchRequest(BaseModel):
    microgrid_id: uuid.UUID
    steps: int = 6
    step_minutes: int = 60
    risk_aversion: float = 0.2
    scenarios: Optional[List[StochasticScenarioInput]] = None


class StochasticDispatchResponse(BaseModel):
    microgrid_id: uuid.UUID
    status: str
    base_objective: float
    hedged_objective: float
    execution_time_ms: float
    scenarios_evaluated: List[str]
    scenario_shortfalls_kwh: Dict[str, float]
    carbon_emissions_kg: float
    carbon_cost: float
    battery_dod_percent: Dict[str, List[float]]
    battery_dod_degradation_cost: Dict[str, float]


# ---------------------------------------------------------------- Phase 2: AI Surveillance
class SurveillanceAlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    microgrid_id: uuid.UUID
    participant_id: Optional[uuid.UUID] = None
    asset_id: Optional[uuid.UUID] = None
    alert_type: str
    severity: str
    score: float
    description: str
    evidence: Dict[str, Any]
    status: str
    timestamp: datetime
    created_at: datetime


class SurveillanceReportResponse(BaseModel):
    microgrid_id: uuid.UUID
    evaluated_at: datetime
    total_alerts: int
    high_risk_participants: List[str]
    alerts: List[SurveillanceAlertResponse]


class SurveillanceAlertStatusUpdate(BaseModel):
    status: str = Field(..., description="OPEN, REVIEWED, DISMISSED")


# ---------------------------------------------------------------- Phase 2: Control Room Telemetry
class LiveControlRoomTelemetry(BaseModel):
    microgrid_id: uuid.UUID
    name: str
    operating_mode: str
    external_grid_connected: bool
    grid_import_kw: float
    grid_export_kw: float
    total_generation_kw: float
    total_load_kw: float
    renewable_percent: float
    battery_average_soc_percent: float
    system_frequency_hz: float
    market_clearing_price: Optional[float] = None
    total_reserve_procured_kw: float
    carbon_intensity_g_per_kwh: float
    active_surveillance_alerts: int
    nodal_lmps: Dict[str, float]
    line_loadings_percent: Dict[str, float]

