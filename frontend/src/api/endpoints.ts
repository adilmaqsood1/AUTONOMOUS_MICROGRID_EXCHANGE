import { apiRequest } from './client';
import {
  AIAgent,
  AgentDecision,
  AgentOutcome,
  AIProviderStatus,
  AuthToken,
  BlockchainStatus,
  BlockchainTransaction,
  EnergyAsset,
  GridLine,
  GridNode,
  GridTopology,
  LeaderboardEntry,
  LiveControlRoomTelemetry,
  OrderBook,
  ScenarioEvent,
  ScenarioPreset,
  SurveillanceAlert,
  Trade,
  User,
} from '@/types';

export const authApi = {
  login: (email: string, password: string) =>
    apiRequest<AuthToken>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),
  getMe: () => apiRequest<User>('/auth/me'),
};

export const microgridApi = {
  list: () => apiRequest<Array<{ id: string; name: string }>>('/microgrids'),
};

export const controlRoomApi = {
  getTelemetry: (microgridId: string) =>
    apiRequest<LiveControlRoomTelemetry>(`/analytics/control-room/${microgridId}`),
};

export const gridApi = {
  getTopology: async (microgridId: string): Promise<GridTopology> => {
    try {
      const res = await apiRequest<GridTopology>(`/grid/topology/${microgridId}`);
      if (res && res.nodes) return res;
    } catch {
      // fallback
    }
    const [nodes, lines] = await Promise.all([
      apiRequest<GridNode[]>(`/grid/nodes?microgrid_id=${microgridId}`).catch(() => []),
      apiRequest<GridLine[]>(`/grid/lines?microgrid_id=${microgridId}`).catch(() => []),
    ]);
    return { nodes: nodes || [], lines: lines || [] };
  },
  getCongestionRents: (microgridId: string) =>
    apiRequest<{
      microgrid_id: string;
      timestamp: string;
      total_congestion_rent: number;
      lines: Array<{
        line_id: string;
        name: string;
        flow_kw: number;
        capacity_kw: number;
        loading_percent: number;
        congested: boolean;
        congestion_rent: number;
        shadow_price: number;
      }>;
    }>(`/grid/congestion-rents?microgrid_id=${microgridId}`),
  updateLineCapacity: (lineId: string, capacityKw: number) =>
    apiRequest<GridLine>(`/grid/lines/${lineId}/capacity?capacity_kw=${capacityKw}`, {
      method: 'PATCH',
    }),
};

export const assetsApi = {
  list: (microgridId?: string) =>
    apiRequest<EnergyAsset[]>(`/assets${microgridId ? `?microgrid_id=${microgridId}` : ''}`),
};

export const marketApi = {
  getOrderBook: (microgridId: string) =>
    apiRequest<OrderBook>(`/orderbook?microgrid_id=${microgridId}`),
  getTrades: (microgridId: string) =>
    apiRequest<Trade[]>(`/trades?microgrid_id=${microgridId}`),
  createOrder: (order: {
    participant_id: string;
    asset_id: string;
    side: 'BUY' | 'SELL';
    quantity_kwh: number;
    limit_price: number;
    delivery_window_start: string;
    delivery_window_end: string;
  }) =>
    apiRequest('/orders', {
      method: 'POST',
      body: JSON.stringify(order),
    }),
};

export const agentsApi = {
  list: (microgridId?: string) =>
    apiRequest<AIAgent[]>(`/agents${microgridId ? `?microgrid_id=${microgridId}` : ''}`),
  getStatus: () => apiRequest<AIProviderStatus>('/agents/ai/status'),
  getLeaderboard: (microgridId?: string) =>
    apiRequest<LeaderboardEntry[]>(
      `/agents/leaderboard${microgridId ? `?microgrid_id=${microgridId}` : ''}`
    ),
  getDecisions: (agentId: string) =>
    apiRequest<AgentDecision[]>(`/agents/${agentId}/decisions`),
  getOutcomes: (agentId: string) =>
    apiRequest<AgentOutcome[]>(`/agents/${agentId}/outcomes`),
  getMemory: (agentId: string, microgridId: string) =>
    apiRequest<{ agent_id: string; summary: any; episodes: any[] }>(
      `/agents/${agentId}/memory?microgrid_id=${microgridId}`
    ),
  runMarketRound: (microgridId: string, mode: string = 'AI_ASSISTED') =>
    apiRequest<{
      round_id: string;
      window: string[];
      orders_count: number;
      matched_kwh: number;
      clearing_price?: number;
      grid_advisor: any;
      market_analyst: any;
    }>('/agents/market-rounds/run', {
      method: 'POST',
      body: JSON.stringify({ microgrid_id: microgridId, mode }),
    }),
};

export const optimizationApi = {
  runStochastic: (microgridId: string, riskAversion: number = 0.2) =>
    apiRequest('/optimization/stochastic-run', {
      method: 'POST',
      body: JSON.stringify({
        microgrid_id: microgridId,
        steps: 24,
        step_minutes: 60,
        risk_aversion: riskAversion,
      }),
    }),
};

export const scenariosApi = {
  listPresets: () =>
    apiRequest<Record<string, ScenarioPreset>>('/simulation/scenarios/presets'),
  listActive: (microgridId?: string) =>
    apiRequest<ScenarioEvent[]>(
      `/simulation/scenarios${microgridId ? `?microgrid_id=${microgridId}` : ''}`
    ),
  injectPreset: (presetKey: string, microgridId?: string) =>
    apiRequest(
      `/simulation/scenarios/presets/${presetKey}${
        microgridId ? `?microgrid_id=${microgridId}` : ''
      }`,
      { method: 'POST' }
    ),
  cancelScenario: (scenarioId: string) =>
    apiRequest(`/simulation/scenarios/${scenarioId}`, { method: 'DELETE' }),
};

export const blockchainApi = {
  getStatus: () => apiRequest<BlockchainStatus>('/blockchain/status'),
  getTransactions: (limit: number = 50) =>
    apiRequest<BlockchainTransaction[]>(`/blockchain/transactions?limit=${limit}`),
  getContracts: () => apiRequest<Record<string, string>>('/blockchain/contracts'),
};

export const surveillanceApi = {
  getAlerts: (microgridId?: string) =>
    apiRequest<SurveillanceAlert[]>(
      `/surveillance/alerts${microgridId ? `?microgrid_id=${microgridId}` : ''}`
    ),
};
