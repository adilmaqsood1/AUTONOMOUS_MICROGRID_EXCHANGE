export interface User {
  id: string;
  email: string;
  role: 'ADMIN' | 'GRID_OPERATOR' | 'PARTICIPANT' | 'AUDITOR';
  participant_id?: string | null;
}

export interface AuthToken {
  access_token: string;
  token_type: string;
  role: string;
  participant_id?: string | null;
}

export interface LiveControlRoomTelemetry {
  microgrid_id: string;
  name: string;
  operating_mode: 'GRID_CONNECTED' | 'ISLANDED' | 'EMERGENCY' | 'RECOVERY';
  external_grid_connected: boolean;
  grid_import_kw: number;
  grid_export_kw: number;
  total_generation_kw: number;
  total_load_kw: number;
  renewable_percent: number;
  battery_average_soc_percent: number;
  system_frequency_hz: number;
  market_clearing_price?: number | null;
  total_reserve_procured_kw: number;
  carbon_intensity_g_per_kwh: number;
  active_surveillance_alerts: number;
  nodal_lmps: Record<string, number>;
  line_loadings_percent: Record<string, number>;
  losses_percent?: number;
  unserved_energy_kwh?: number;
  frequency_hz?: number;
  total_demand_kw?: number;
  solar_generation_kw?: number;
  wind_generation_kw?: number;
  hydro_generation_kw?: number;
  battery_discharge_kw?: number;
}

export interface GridNode {
  id: string;
  name: string;
  node_type: string;
  voltage_level?: number;
  voltage_kv?: number;
  nominal_voltage_kv?: number;
  voltage_magnitude_pu?: number;
  voltage_angle_deg?: number;
  latitude?: number;
  longitude?: number;
  rated_kw?: number;
  color?: string;
  connected_assets_count?: number;
  lmp?: number;
}

export interface GridLine {
  id: string;
  name: string;
  from_node_id: string;
  to_node_id: string;
  capacity_kw: number;
  flow_kw: number;
  loading_percent: number;
  congested: boolean;
  active: boolean;
}

export interface GridTopology {
  location?: {
    label: string;
    latitude: number;
    longitude: number;
  };
  nodes: GridNode[];
  lines: GridLine[];
}

export interface EnergyAsset {
  id: string;
  microgrid_id: string;
  participant_id: string;
  grid_node_id: string;
  name: string;
  asset_type: string;
  status: string;
  rated_power_kw: number;
  capacity_kw?: number;
  rated_kw?: number;
  generator?: {
    maximum_generation_kw: number;
    minimum_generation_kw: number;
    renewable: boolean;
    marginal_cost: number;
    current_output_kw?: number;
  } | null;
  battery?: {
    capacity_kwh: number;
    current_soc_percent: number;
    minimum_soc_percent: number;
    maximum_soc_percent: number;
    maximum_charge_kw: number;
    maximum_discharge_kw: number;
  } | null;
  load?: {
    base_demand_kw: number;
    current_demand_kw: number;
  } | null;
}

export interface BookOrder {
  id: string;
  participant_id: string;
  asset_id: string;
  price: string;
  remaining_kwh: string;
  sequence: number;
  renewable_only: boolean;
  window_start: string;
  window_end: string;
}

export interface OrderBook {
  buy_book: BookOrder[];
  sell_book: BookOrder[];
}

export interface Trade {
  id: string;
  trade_id?: string;
  clearing_id: string;
  buy_order_id: string;
  sell_order_id: string;
  quantity_kwh: number;
  price: number;
  created_at: string;
}

export interface AIAgent {
  id: string;
  agent_id: string;
  agent_type: string;
  strategy_mode: 'DETERMINISTIC' | 'SHADOW_AI' | 'AI_ASSISTED';
  prompt_version: string;
  model_name: string;
  enabled: boolean;
}

export interface AgentDecision {
  id: string;
  agent_id: string;
  agent_type: string;
  mode: string;
  market_round_id: string;
  confidence: number;
  reasoning_summary: string;
  proposal: {
    action?: string;
    quantity_kwh?: number;
    limit_price?: number;
    reserve_kw?: number;
    target_soc?: number;
  };
  guard_status: 'APPROVED' | 'REJECTED' | 'CLAMPED' | 'FALLBACK';
  guard_reason_codes: string[];
  fallback_used: boolean;
  fallback_reason?: string | null;
  final_action: string;
  final_quantity: number;
  final_price?: number | null;
  latency_ms: number;
  created_at: string;
}

export interface AgentOutcome {
  id: string;
  agent_id: string;
  market_round_id: string;
  executed_action: string;
  executed_quantity: number;
  market_price: number;
  net_reward: number;
  delivery_ratio: number;
  shortfall_penalty: number;
  created_at: string;
}

export interface LeaderboardEntry {
  participant_id: string;
  name: string;
  agent_id: string;
  type: string;
  reliability_score: number;
  total_net_reward: number;
  ai_value_add: number;
  deliveries_success: number;
  deliveries_failed: number;
}

export interface AIProviderStatus {
  configured: boolean;
  provider: string;
  model: string;
  base_url: string;
  circuit_breaker_state: 'CLOSED' | 'OPEN' | 'HALF_OPEN';
  failure_count: number;
  success_rate: number;
  average_latency_ms: number;
  total_calls: number;
}

export interface ScenarioPreset {
  key: string;
  name: string;
  description: string;
  type: string;
}

export interface ScenarioEvent {
  id: string;
  type: string;
  scenario_type?: string;
  severity: number;
  start_time: string;
  end_time?: string | null;
  duration_minutes?: number;
  active: boolean;
  params: Record<string, any>;
}

export interface BlockchainStatus {
  mode: 'mock' | 'evm';
  connected: boolean;
  chain_id: number;
  latest_block: number;
  contracts: Record<string, string>;
  indexer_synced: boolean;
  error?: string;
}

export interface BlockchainTransaction {
  id: string;
  tx_hash: string;
  from_address: string;
  to_address: string;
  business_entity_type: string;
  business_entity_id: string;
  status: 'PENDING' | 'CONFIRMED' | 'FAILED';
  block_number?: number | null;
  gas_used?: number | null;
  created_at: string;
}

export interface ParticipantWallet {
  id: string;
  participant_id: string;
  address: string;
  wallet_type: string;
  balance_grid_usd?: number;
  status: string;
}

export interface SurveillanceAlert {
  id: string;
  participant_id?: string | null;
  asset_id?: string | null;
  alert_type: string;
  severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  score: number;
  description: string;
  evidence: Record<string, any>;
  details?: Record<string, any>;
  status: 'OPEN' | 'REVIEWED' | 'DISMISSED';
  timestamp: string;
}
