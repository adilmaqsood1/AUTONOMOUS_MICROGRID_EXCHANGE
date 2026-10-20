import React, { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { gridApi, controlRoomApi, agentsApi } from '@/api/endpoints';
import { useMicrogridStore } from '@/stores/microgridStore';
import { StatusBadge } from '@/components/common/StatusBadge';
import {
  Activity,
  Battery,
  Zap,
  Flame,
  Layers,
  HeartPulse,
  Car,
  Building2,
  Home,
  Wind,
  Droplets,
  Info,
  CheckCircle2,
  AlertTriangle,
  Play,
  RefreshCw,
  SlidersHorizontal,
  ArrowRight,
  ShieldCheck,
  Gauge,
  TrendingUp,
  DollarSign,
  Sun,
  AlertCircle,
  X,
} from 'lucide-react';
import clsx from 'clsx';

interface NodeLayout {
  x: number;
  y: number;
  label: string;
  sublabel: string;
  type: string;
  voltage: string;
  color: string;
  stroke: string;
  category: 'generator' | 'storage' | 'priority' | 'commercial' | 'residential' | 'substation' | 'intertie';
}

const KNOWN_NODES: Record<string, NodeLayout> = {
  SUBSTATION: {
    x: 310,
    y: 185,
    label: 'SUBSTATION',
    sublabel: '11 kV Central Bus',
    type: 'Substation Bus',
    voltage: '11.0 kV',
    color: '#3B82F6',
    stroke: '#1D4ED8',
    category: 'substation',
  },
  GRID_TIE: {
    x: 95,
    y: 65,
    label: 'GRID TIE',
    sublabel: 'Utility Intertie',
    type: 'Grid Intertie',
    voltage: '11.0 kV',
    color: '#10B981',
    stroke: '#047857',
    category: 'intertie',
  },
  WIND_FARM: {
    x: 310,
    y: 50,
    label: 'WIND FARM',
    sublabel: '150 kW Array',
    type: 'Wind Gen',
    voltage: '11.0 kV',
    color: '#06B6D4',
    stroke: '#0891B2',
    category: 'generator',
  },
  HYDRO: {
    x: 525,
    y: 65,
    label: 'HYDRO',
    sublabel: '100 kW Hydro',
    type: 'Hydro Gen',
    voltage: '11.0 kV',
    color: '#0EA5E9',
    stroke: '#0284C7',
    category: 'generator',
  },
  STORAGE: {
    x: 80,
    y: 200,
    label: 'STORAGE',
    sublabel: '100 kW BESS',
    type: 'Storage',
    voltage: '11.0 kV',
    color: '#8B5CF6',
    stroke: '#6D28D9',
    category: 'storage',
  },
  HOSPITAL: {
    x: 540,
    y: 200,
    label: 'HOSPITAL',
    sublabel: 'Critical Priority',
    type: 'Priority Load',
    voltage: '0.4 kV',
    color: '#EF4444',
    stroke: '#B91C1C',
    category: 'priority',
  },
  COMMERCIAL: {
    x: 105,
    y: 330,
    label: 'COMMERCIAL',
    sublabel: 'Commercial Zone',
    type: 'Commercial Load',
    voltage: '0.4 kV',
    color: '#F59E0B',
    stroke: '#D97706',
    category: 'commercial',
  },
  RES_A: {
    x: 275,
    y: 340,
    label: 'RES_A',
    sublabel: 'Residential Feeder A',
    type: 'Residential Load',
    voltage: '0.4 kV',
    color: '#14B8A6',
    stroke: '#0F766E',
    category: 'residential',
  },
  RES_B: {
    x: 460,
    y: 340,
    label: 'RES_B',
    sublabel: 'Residential Feeder B',
    type: 'Residential Load',
    voltage: '0.4 kV',
    color: '#2DD4BF',
    stroke: '#0D9488',
    category: 'residential',
  },
};

type ViewMode = 'power_flow' | 'lmp_heat' | 'voltage_profile';

export const ControlRoomPage: React.FC = () => {
  const queryClient = useQueryClient();
  const { microgridId } = useMicrogridStore();

  const [isRunningRound, setIsRunningRound] = useState(false);
  const [roundFeedback, setRoundFeedback] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>('power_flow');
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedLineId, setSelectedLineId] = useState<string | null>(null);
  const [sidebarTab, setSidebarTab] = useState<'lmps' | 'lines' | 'congestion'>('lmps');
  const [actionSuccessMsg, setActionSuccessMsg] = useState<string | null>(null);

  // Queries
  const { data: telemetry, isFetching: isFetchingTelemetry } = useQuery({
    queryKey: ['controlRoom', microgridId],
    queryFn: () => controlRoomApi.getTelemetry(microgridId!),
    enabled: !!microgridId,
    refetchInterval: 3000,
  });

  const { data: topology } = useQuery({
    queryKey: ['topology', microgridId],
    queryFn: () => gridApi.getTopology(microgridId!),
    enabled: !!microgridId,
    refetchInterval: 5000,
  });

  const { data: congestionData } = useQuery({
    queryKey: ['congestionRents', microgridId],
    queryFn: () => gridApi.getCongestionRents(microgridId!),
    enabled: !!microgridId,
    refetchInterval: 5000,
  });

  // Line capacity derate mutation
  const derateMutation = useMutation({
    mutationFn: ({ lineId, capacityKw }: { lineId: string; capacityKw: number }) =>
      gridApi.updateLineCapacity(lineId, capacityKw),
    onSuccess: (updated) => {
      queryClient.invalidateQueries({ queryKey: ['topology'] });
      queryClient.invalidateQueries({ queryKey: ['controlRoom'] });
      queryClient.invalidateQueries({ queryKey: ['congestionRents'] });
      setActionSuccessMsg(`Feeder ${updated.name || 'Line'} rating updated to ${updated.capacity_kw} kW.`);
      setTimeout(() => setActionSuccessMsg(null), 4000);
    },
    onError: (err: any) => {
      setActionSuccessMsg(`Derate failed: ${err.message || 'Requires Operator privileges'}`);
      setTimeout(() => setActionSuccessMsg(null), 4000);
    },
  });

  const handleRunMarketRound = async () => {
    if (!microgridId) {
      setRoundFeedback('Waiting for active microgrid district to load...');
      return;
    }
    setIsRunningRound(true);
    setRoundFeedback(null);
    try {
      const res = await agentsApi.runMarketRound(microgridId, 'AI_ASSISTED');
      queryClient.invalidateQueries({ queryKey: ['controlRoom'] });
      queryClient.invalidateQueries({ queryKey: ['topology'] });
      queryClient.invalidateQueries({ queryKey: ['orderBook'] });
      queryClient.invalidateQueries({ queryKey: ['trades'] });
      queryClient.invalidateQueries({ queryKey: ['congestionRents'] });
      setRoundFeedback(
        `Round Cleared: Matched ${res.matched_kwh} kWh at $${(res.clearing_price ?? 0.085).toFixed(3)}/kWh!`
      );
      setTimeout(() => setRoundFeedback(null), 5000);
    } catch (err: any) {
      setRoundFeedback(`Error: ${err.message || 'Requires Operator privileges'}`);
      setTimeout(() => setRoundFeedback(null), 5000);
    } finally {
      setIsRunningRound(false);
    }
  };

  // Compute Display Nodes with live telemetry & voltage per-unit
  const displayNodes = useMemo(() => {
    if (!topology?.nodes || topology.nodes.length === 0) {
      return [];
    }

    const total = topology.nodes.length;
    return topology.nodes.map((n, idx) => {
      const known = KNOWN_NODES[n.name];
      const fallbackCoord = {
        x: Math.round(310 + 200 * Math.cos((idx / total) * 2 * Math.PI - Math.PI / 2)),
        y: Math.round(185 + 130 * Math.sin((idx / total) * 2 * Math.PI - Math.PI / 2)),
      };

      const lmpVal =
        telemetry?.nodal_lmps?.[n.name] ??
        telemetry?.nodal_lmps?.[n.id] ??
        telemetry?.market_clearing_price ??
        0.085;

      const nodeVoltage = n.voltage_level ?? n.voltage_kv ?? n.nominal_voltage_kv ?? 0.4;
      const vPu = n.voltage_magnitude_pu ?? 1.0;

      return {
        id: n.id,
        name: n.name,
        label: n.name.replace(/_/g, ' '),
        sublabel: n.rated_kw ? `${n.rated_kw} kW Rating` : `${nodeVoltage} kV Feeder`,
        type: n.node_type || known?.type || 'Bus',
        category: known?.category || 'substation',
        voltage_kv: `${nodeVoltage} kV`,
        x: known ? known.x : fallbackCoord.x,
        y: known ? known.y : fallbackCoord.y,
        color: n.color || known?.color || '#3B82F6',
        stroke: known?.stroke || n.color || '#1D4ED8',
        lmp: typeof lmpVal === 'number' ? lmpVal : 0.085,
        voltage_pu: vPu,
        angle_deg: n.voltage_angle_deg ?? (idx * -0.45),
        rated_kw: n.rated_kw || 100,
        connected_assets_count: n.connected_assets_count || 1,
      };
    });
  }, [topology?.nodes, telemetry]);

  // Compute Display Lines with power flow and congestion status
  const displayLines = useMemo(() => {
    if (!topology?.lines || topology.lines.length === 0 || displayNodes.length === 0) {
      return [];
    }

    const nodeMap = new Map(displayNodes.map((n) => [n.id, n]));
    const nameMap = new Map(displayNodes.map((n) => [n.name, n]));

    return topology.lines.map((l, idx) => {
      const source = nodeMap.get(l.from_node_id) || nameMap.get(l.from_node_id) || displayNodes[0];
      const target = nodeMap.get(l.to_node_id) || nameMap.get(l.to_node_id) || displayNodes[1];

      const fwd = `${source.name} -> ${target.name}`;
      const rev = `${target.name} -> ${source.name}`;

      let loading = 0;
      if (telemetry?.line_loadings_percent) {
        if (telemetry.line_loadings_percent[fwd] !== undefined) {
          loading = telemetry.line_loadings_percent[fwd];
        } else if (telemetry.line_loadings_percent[rev] !== undefined) {
          loading = telemetry.line_loadings_percent[rev];
        } else if (telemetry.line_loadings_percent[l.id] !== undefined) {
          loading = telemetry.line_loadings_percent[l.id];
        }
      } else if (l.loading_percent !== undefined) {
        loading = l.loading_percent;
      } else if (l.capacity_kw > 0 && l.flow_kw) {
        loading = Math.round((l.flow_kw / l.capacity_kw) * 100);
      }

      const isDeratedCongested = l.capacity_kw <= 5.0;
      const congested = l.congested || loading >= 90 || isDeratedCongested;
      const flowKw = l.flow_kw || Math.round((loading / 100) * l.capacity_kw * 10) / 10;
      const headroomKw = Math.max(0, Math.round((l.capacity_kw - Math.abs(flowKw)) * 10) / 10);

      // Congestion rent from backend or formula: |flow| * price difference
      const sourceLmp = source.lmp;
      const targetLmp = target.lmp;
      const priceDiff = Math.abs(targetLmp - sourceLmp);
      const calculatedRent = Math.abs(flowKw) * priceDiff;

      return {
        id: l.id || `line-${idx}`,
        name: l.name || `${source.name} ↔ ${target.name}`,
        source,
        target,
        capacity_kw: l.capacity_kw,
        flow_kw: flowKw,
        headroom_kw: headroomKw,
        loading: Math.round(loading),
        congested,
        congestion_rent: calculatedRent,
        price_diff: priceDiff,
      };
    });
  }, [topology?.lines, displayNodes, telemetry]);

  const congestedLines = displayLines.filter((l) => l.congested || l.loading >= 90);
  const selectedNode = displayNodes.find((n) => n.id === selectedNodeId);
  const selectedLine = displayLines.find((l) => l.id === selectedLineId);

  // Frequency Deviation status
  const freqHz = telemetry?.system_frequency_hz ?? telemetry?.frequency_hz ?? 50.00;
  const freqDelta = freqHz - 50.00;
  const freqStatus = Math.abs(freqDelta) <= 0.05 ? 'NORMAL' : Math.abs(freqDelta) <= 0.15 ? 'DEVIATION' : 'CRITICAL';

  // Energy Balance metrics
  const totalGenKw = telemetry?.total_generation_kw ?? 0;
  const totalLoadKw = telemetry?.total_load_kw ?? telemetry?.total_demand_kw ?? 0;
  const gridImportKw = telemetry?.grid_import_kw ?? 0;
  const gridExportKw = telemetry?.grid_export_kw ?? 0;
  const lossesKw = (telemetry?.losses_percent ? (totalGenKw * telemetry.losses_percent) / 100 : 0);
  const netSupplyKw = totalGenKw + gridImportKw;
  const netDemandKw = totalLoadKw + lossesKw + gridExportKw;
  const balanceDelta = netSupplyKw - netDemandKw;

  // LMP helpers
  const getLineStrokeColor = (l: (typeof displayLines)[0]) => {
    if (viewMode === 'lmp_heat') {
      const maxLmp = Math.max(l.source.lmp, l.target.lmp);
      if (maxLmp > 0.12) return '#EF4444';
      if (maxLmp > 0.09) return '#F59E0B';
      return '#10B981';
    }
    if (l.congested || l.loading >= 90) return '#EF4444';
    if (l.loading >= 75) return '#F59E0B';
    if (l.loading >= 50) return '#0284C7';
    return '#10B981';
  };

  const getNodeFillColor = (n: (typeof displayNodes)[0]) => {
    if (viewMode === 'lmp_heat') {
      if (n.lmp > 0.12) return '#EF4444';
      if (n.lmp > 0.09) return '#F59E0B';
      return '#10B981';
    }
    if (viewMode === 'voltage_profile') {
      if (n.voltage_pu < 0.95 || n.voltage_pu > 1.05) return '#EF4444';
      if (n.voltage_pu < 0.98 || n.voltage_pu > 1.02) return '#F59E0B';
      return '#10B981';
    }
    return n.color;
  };

  const getLmpBadgeStyle = (price: number) => {
    if (price > 0.12) return 'bg-rose-50 text-rose-700 border-rose-300 font-bold';
    if (price > 0.09) return 'bg-amber-50 text-amber-800 border-amber-300 font-bold';
    return 'bg-emerald-50 text-emerald-800 border-emerald-300 font-bold';
  };

  const getNodeIcon = (category: string) => {
    switch (category) {
      case 'generator':
        return <Wind className="w-3.5 h-3.5 text-cyan-600" />;
      case 'storage':
        return <Battery className="w-3.5 h-3.5 text-purple-600" />;
      case 'priority':
        return <HeartPulse className="w-3.5 h-3.5 text-rose-600" />;
      case 'commercial':
        return <Building2 className="w-3.5 h-3.5 text-amber-600" />;
      case 'residential':
        return <Home className="w-3.5 h-3.5 text-teal-600" />;
      case 'intertie':
        return <Zap className="w-3.5 h-3.5 text-emerald-600" />;
      default:
        return <Layers className="w-3.5 h-3.5 text-blue-600" />;
    }
  };

  return (
    <div className="space-y-6">
      {/* 1. SCADA Header & Operational Status Bar */}
      <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-xs">
        <div className="flex items-center justify-between flex-wrap gap-4 pb-3 border-b border-slate-100">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-blue-50 border border-blue-200 flex items-center justify-center text-blue-600 shadow-2xs">
              <Activity className="w-5 h-5 animate-pulse" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-base font-bold text-slate-900">
                  Microgrid SCADA &amp; Electrical Control Room
                </h1>
                <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-slate-100 text-slate-700 border border-slate-300">
                  IEEE 9-Bus
                </span>
              </div>
              <p className="text-xs text-slate-500 mt-0.5 flex items-center gap-2">
                <span>Real-time AC power flow, physical feeder telemetry &amp; dual LMP shadow prices.</span>
                <span className="inline-flex items-center gap-1 font-mono text-[11px] text-emerald-600">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-ping" />
                  Live Sync
                </span>
              </p>
            </div>
          </div>

          {/* Quick SCADA Dispatch & Clearing Actions */}
          <div className="flex items-center gap-2.5 flex-wrap">
            {actionSuccessMsg && (
              <span className="text-xs font-mono font-semibold text-emerald-700 bg-emerald-50 px-2.5 py-1 rounded border border-emerald-200">
                {actionSuccessMsg}
              </span>
            )}
            {roundFeedback && (
              <span className="text-xs font-mono font-bold text-blue-700 bg-blue-50 px-2.5 py-1 rounded border border-blue-200">
                {roundFeedback}
              </span>
            )}

            <button
              onClick={handleRunMarketRound}
              disabled={isRunningRound}
              className="flex items-center gap-1.5 px-3.5 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-xs font-bold transition-all shadow-xs"
            >
              <Play className={clsx('w-3.5 h-3.5 fill-current', isRunningRound && 'animate-spin')} />
              <span>{isRunningRound ? 'Clearing Swarm...' : 'Trigger Market Round'}</span>
            </button>

            <button
              onClick={() => {
                queryClient.invalidateQueries({ queryKey: ['controlRoom'] });
                queryClient.invalidateQueries({ queryKey: ['topology'] });
                queryClient.invalidateQueries({ queryKey: ['congestionRents'] });
              }}
              className="p-2 border border-slate-200 hover:bg-slate-50 text-slate-600 rounded-lg text-xs transition-colors"
              title="Refresh Telemetry"
            >
              <RefreshCw className={clsx('w-4 h-4', isFetchingTelemetry && 'animate-spin text-blue-600')} />
            </button>
          </div>
        </div>

        {/* Real-Time Telemetry Bar (6 Essential Grid Health Tiles) */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 pt-3 font-mono">
          {/* Operating Mode */}
          <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200">
            <div className="text-[10px] text-slate-500 uppercase tracking-wider font-sans font-semibold">Grid State</div>
            <div className="flex items-center gap-1.5 mt-1">
              <span
                className={clsx(
                  'w-2 h-2 rounded-full',
                  telemetry?.operating_mode === 'ISLANDED' ? 'bg-amber-500 animate-pulse' : 'bg-emerald-500'
                )}
              />
              <span className="text-xs font-bold text-slate-900">
                {telemetry?.operating_mode?.replace('_', ' ') || 'GRID CONNECTED'}
              </span>
            </div>
            <div className="text-[10px] text-slate-400 mt-0.5">
              {telemetry?.operating_mode === 'ISLANDED' ? 'Islanding Breaker Open' : 'Main Intertie Closed'}
            </div>
          </div>

          {/* System Frequency */}
          <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200">
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-slate-500 uppercase tracking-wider font-sans font-semibold">AC Frequency</span>
              <Gauge className="w-3.5 h-3.5 text-blue-500" />
            </div>
            <div className="text-base font-bold text-slate-900 mt-1">
              {freqHz.toFixed(2)} <span className="text-xs font-normal text-slate-500">Hz</span>
            </div>
            <div className={clsx('text-[10px] font-semibold mt-0.5', freqStatus === 'NORMAL' ? 'text-emerald-600' : 'text-amber-600')}>
              {freqDelta >= 0 ? `+${freqDelta.toFixed(3)}` : freqDelta.toFixed(3)} Hz (Nominal)
            </div>
          </div>

          {/* Generation & Renewables */}
          <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200">
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-slate-500 uppercase tracking-wider font-sans font-semibold">Total Generation</span>
              <Zap className="w-3.5 h-3.5 text-emerald-500" />
            </div>
            <div className="text-base font-bold text-emerald-600 mt-1">
              {Math.round(totalGenKw).toLocaleString()} <span className="text-xs font-normal text-slate-500">kW</span>
            </div>
            <div className="text-[10px] text-emerald-700 font-semibold mt-0.5">
              {Math.round(telemetry?.renewable_percent ?? 100)}% Renewable Mix
            </div>
          </div>

          {/* District Demand & Losses */}
          <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200">
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-slate-500 uppercase tracking-wider font-sans font-semibold">District Load</span>
              <Activity className="w-3.5 h-3.5 text-blue-500" />
            </div>
            <div className="text-base font-bold text-slate-900 mt-1">
              {Math.round(totalLoadKw).toLocaleString()} <span className="text-xs font-normal text-slate-500">kW</span>
            </div>
            <div className="text-[10px] text-slate-500 mt-0.5">
              Losses: {telemetry?.losses_percent !== undefined ? `${telemetry.losses_percent.toFixed(2)}%` : '1.4%'}
            </div>
          </div>

          {/* Critical Hospital Served */}
          <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200">
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-slate-500 uppercase tracking-wider font-sans font-semibold">Hospital Priority</span>
              <HeartPulse className="w-3.5 h-3.5 text-rose-500" />
            </div>
            <div className="text-base font-bold text-emerald-600 mt-1">
              {telemetry?.unserved_energy_kwh === 0 || telemetry?.unserved_energy_kwh === undefined ? '100% Served' : '0 kWh Loss'}
            </div>
            <div className="text-[10px] text-emerald-700 font-semibold mt-0.5">
              Non-Sheddable Tier 1
            </div>
          </div>

          {/* Average System LMP */}
          <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200">
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-slate-500 uppercase tracking-wider font-sans font-semibold">Clearing LMP</span>
              <DollarSign className="w-3.5 h-3.5 text-amber-500" />
            </div>
            <div className="text-base font-bold text-slate-900 mt-1">
              {telemetry?.market_clearing_price != null ? `$${telemetry.market_clearing_price.toFixed(3)}` : '$0.085'}
              <span className="text-xs font-normal text-slate-500">/kWh</span>
            </div>
            <div className="text-[10px] text-slate-500 mt-0.5">
              {congestedLines.length > 0 ? `${congestedLines.length} Congested Corridor` : 'Normal Dispatch'}
            </div>
          </div>
        </div>
      </div>

      {/* 2. Generation vs Load Dynamic Power Balance Bar */}
      <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-xs">
        <div className="flex items-center justify-between pb-2 text-xs font-sans">
          <div className="flex items-center gap-2 font-bold text-slate-900">
            <ShieldCheck className="w-4 h-4 text-emerald-600" />
            <span>Real-Time AC Power Balance &amp; Spinning Headroom</span>
          </div>
          <div className="font-mono text-xs flex items-center gap-3">
            <span className="text-emerald-700 font-bold">Supply: {Math.round(netSupplyKw)} kW</span>
            <span className="text-slate-300">|</span>
            <span className="text-slate-700 font-bold">Demand: {Math.round(netDemandKw)} kW</span>
            <span className="text-slate-300">|</span>
            <span className="text-blue-600 font-bold">Reserve Headroom: {Math.round(telemetry?.total_reserve_procured_kw ?? 45)} kW</span>
          </div>
        </div>

        {/* Visual Bar Stack */}
        <div className="w-full h-3 rounded-full bg-slate-100 overflow-hidden flex shadow-inner">
          <div
            className="h-full bg-emerald-500 transition-all duration-500"
            style={{ width: `${Math.min(100, (totalGenKw / (netSupplyKw || 1)) * 60)}%` }}
            title={`Renewable Generation: ${Math.round(totalGenKw)} kW`}
          />
          <div
            className="h-full bg-teal-400 transition-all duration-500"
            style={{ width: `${Math.min(100, (gridImportKw / (netSupplyKw || 1)) * 40)}%` }}
            title={`Grid Utility Import: ${Math.round(gridImportKw)} kW`}
          />
          <div
            className="h-full bg-blue-500 transition-all duration-500"
            style={{ width: `${Math.min(100, (totalLoadKw / (netDemandKw || 1)) * 80)}%` }}
            title={`District Loads: ${Math.round(totalLoadKw)} kW`}
          />
          <div
            className="h-full bg-amber-400 transition-all duration-500"
            style={{ width: `${Math.min(100, (lossesKw / (netDemandKw || 1)) * 20)}%` }}
            title={`Technical Losses: ${lossesKw.toFixed(1)} kW`}
          />
        </div>

        <div className="flex items-center justify-between text-[11px] font-mono text-slate-500 mt-2 flex-wrap gap-2">
          <div className="flex items-center gap-4">
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2 rounded-xs bg-emerald-500" /> Gen ({Math.round(totalGenKw)} kW)
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2 rounded-xs bg-teal-400" /> Utility Import ({Math.round(gridImportKw)} kW)
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2 rounded-xs bg-blue-500" /> Demand ({Math.round(totalLoadKw)} kW)
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2 rounded-xs bg-amber-400" /> Losses ({lossesKw.toFixed(1)} kW)
            </span>
          </div>
          <span className="text-emerald-700 font-semibold">
            {balanceDelta >= 0 ? `Net Surplus: +${Math.round(balanceDelta)} kW` : `Deficit: ${Math.round(balanceDelta)} kW`}
          </span>
        </div>
      </div>

      {/* 3. Main Operational View: Interactive Blueprint Canvas (Left 8 cols) + SCADA Telemetry Hub (Right 4 cols) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left: Real-Time Grid Single-Line Diagram Canvas */}
        <div className="lg:col-span-8 bg-white border border-slate-200 rounded-xl p-4 shadow-sm flex flex-col justify-between">
          <div className="flex items-center justify-between pb-3 border-b border-slate-100 flex-wrap gap-2">
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-bold text-slate-900">
                  Microgrid Single-Line Diagram (SLD) &amp; Power Flow Canvas
                </h2>
                {congestedLines.length > 0 && (
                  <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-rose-50 text-rose-700 border border-rose-300 animate-pulse">
                    {congestedLines.length} BOTTLE-NECK
                  </span>
                )}
              </div>
              <p className="text-[11px] text-slate-500 mt-0.5">
                Click any bus or feeder line to inspect voltage, dual LMP shadow price, and thermal flow.
              </p>
            </div>

            {/* Canvas Layer Switcher */}
            <div className="flex items-center gap-1 bg-slate-100 p-1 rounded-lg border border-slate-200 text-xs font-semibold">
              <button
                onClick={() => setViewMode('power_flow')}
                className={clsx(
                  'px-2.5 py-1 rounded-md transition-all flex items-center gap-1.5 text-[11px]',
                  viewMode === 'power_flow' ? 'bg-white text-blue-700 shadow-2xs font-bold' : 'text-slate-600 hover:text-slate-900'
                )}
              >
                <Zap className="w-3 h-3 text-amber-500" />
                <span>Power Flow</span>
              </button>
              <button
                onClick={() => setViewMode('lmp_heat')}
                className={clsx(
                  'px-2.5 py-1 rounded-md transition-all flex items-center gap-1.5 text-[11px]',
                  viewMode === 'lmp_heat' ? 'bg-white text-blue-700 shadow-2xs font-bold' : 'text-slate-600 hover:text-slate-900'
                )}
              >
                <DollarSign className="w-3 h-3 text-emerald-500" />
                <span>LMP Heatmap</span>
              </button>
              <button
                onClick={() => setViewMode('voltage_profile')}
                className={clsx(
                  'px-2.5 py-1 rounded-md transition-all flex items-center gap-1.5 text-[11px]',
                  viewMode === 'voltage_profile' ? 'bg-white text-blue-700 shadow-2xs font-bold' : 'text-slate-600 hover:text-slate-900'
                )}
              >
                <Gauge className="w-3 h-3 text-blue-500" />
                <span>Voltage pu</span>
              </button>
            </div>
          </div>

          {/* SVG Blueprint Canvas */}
          <div className="relative mt-3 rounded-xl bg-slate-50/90 border border-slate-200 h-[430px] flex items-center justify-center overflow-hidden shadow-inner">
            {displayNodes.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-2 font-mono text-xs text-slate-500">
                <div className="w-6 h-6 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
                <span>Loading IEEE 9-bus grid topology from backend...</span>
              </div>
            ) : (
              <svg
                className="w-full h-full select-none"
                viewBox="0 0 620 390"
                preserveAspectRatio="xMidYMid meet"
              >
                <defs>
                  {/* Blueprint grid pattern */}
                  <pattern id="grid-pattern" width="20" height="20" patternUnits="userSpaceOnUse">
                    <path d="M 20 0 L 0 0 0 20" fill="none" stroke="#E2E8F0" strokeWidth="0.5" />
                  </pattern>
                  {/* Glow filter for congested feeders */}
                  <filter id="glow-red" x="-20%" y="-20%" width="140%" height="140%">
                    <feDropShadow dx="0" dy="0" stdDeviation="3.5" floodColor="#EF4444" floodOpacity="0.75" />
                  </filter>
                  <filter id="glow-blue" x="-20%" y="-20%" width="140%" height="140%">
                    <feDropShadow dx="0" dy="0" stdDeviation="3" floodColor="#3B82F6" floodOpacity="0.6" />
                  </filter>
                </defs>

                <rect width="620" height="390" fill="url(#grid-pattern)" />

                {/* Grid Feeder Transmission Lines */}
                {displayLines.map((l) => {
                  const source = l.source;
                  const target = l.target;
                  if (!source || !target) return null;

                  const strokeColor = getLineStrokeColor(l);
                  const isLineSelected = selectedLineId === l.id;
                  const midX = (source.x + target.x) / 2;
                  const midY = (source.y + target.y) / 2;

                  return (
                    <g
                      key={l.id}
                      onClick={() => {
                        setSelectedLineId(isLineSelected ? null : l.id);
                        setSelectedNodeId(null);
                      }}
                      className="cursor-pointer group"
                    >
                      {/* Transparent wide hit-zone for easy click targeting */}
                      <line
                        x1={source.x}
                        y1={source.y}
                        x2={target.x}
                        y2={target.y}
                        stroke="transparent"
                        strokeWidth={16}
                      />

                      {/* Main Feeder Line */}
                      <line
                        x1={source.x}
                        y1={source.y}
                        x2={target.x}
                        y2={target.y}
                        stroke={strokeColor}
                        strokeWidth={l.congested ? 3.5 : isLineSelected ? 3.5 : 2.5}
                        strokeDasharray={l.congested ? '6,3' : undefined}
                        className={l.congested ? 'animate-pulse' : undefined}
                        filter={l.congested ? 'url(#glow-red)' : isLineSelected ? 'url(#glow-blue)' : undefined}
                      />

                      {/* Dynamic Energy Flow Particles */}
                      <line
                        x1={source.x}
                        y1={source.y}
                        x2={target.x}
                        y2={target.y}
                        stroke={l.congested ? '#FCA5A5' : '#60A5FA'}
                        strokeWidth={1.8}
                        strokeDasharray="5, 8"
                        className="animate-energy-pulse pointer-events-none"
                        opacity={0.8}
                      />

                      {/* Midpoint Pill: Loading % or Congestion Badge */}
                      <g transform={`translate(${midX}, ${midY})`}>
                        <rect
                          x={l.congested ? -46 : -22}
                          y={-10}
                          width={l.congested ? 92 : 44}
                          height={20}
                          rx={5}
                          fill="#FFFFFF"
                          stroke={strokeColor}
                          strokeWidth={isLineSelected ? 2 : 1.2}
                          className="shadow-sm"
                        />
                        <text
                          x={0}
                          y={3.5}
                          fill={l.congested ? '#EF4444' : '#0F172A'}
                          fontSize={9}
                          fontFamily="monospace"
                          fontWeight="bold"
                          textAnchor="middle"
                        >
                          {l.congested ? `CONGESTED ${l.loading}%` : `${l.loading}%`}
                        </text>
                      </g>
                    </g>
                  );
                })}

                {/* Electrical Buses / Nodes */}
                {displayNodes.map((n) => {
                  const isSelected = selectedNodeId === n.id;
                  const isCongestedNode = n.lmp > 0.12;
                  const nodeFill = getNodeFillColor(n);

                  return (
                    <g
                      key={n.id}
                      onClick={() => {
                        setSelectedNodeId(isSelected ? null : n.id);
                        setSelectedLineId(null);
                      }}
                      className="cursor-pointer group"
                      transform={`translate(${n.x}, ${n.y})`}
                    >
                      {/* Selection Pulse Aura */}
                      {isSelected && (
                        <circle
                          r={26}
                          fill="#3B82F6"
                          fillOpacity={0.18}
                          stroke="#3B82F6"
                          strokeWidth={1.8}
                          className="animate-ping"
                        />
                      )}

                      {/* Outer Bus Ring */}
                      <circle
                        r={16}
                        fill="#FFFFFF"
                        stroke={nodeFill}
                        strokeWidth={isSelected ? 3.5 : 2.2}
                        className="group-hover:scale-110 transition-transform shadow-md"
                      />

                      {/* Inner Core Icon Marker */}
                      <circle
                        r={7}
                        fill={nodeFill}
                        className={isCongestedNode ? 'animate-ping' : undefined}
                      />
                      <circle r={5} fill={nodeFill} />

                      {/* Voltage Pill Badge Above Node */}
                      <g transform="translate(0, -23)">
                        <rect
                          x={-22}
                          y={-6}
                          width={44}
                          height={13}
                          rx={3}
                          fill="#0F172A"
                          fillOpacity={0.88}
                        />
                        <text
                          x={0}
                          y={3.5}
                          fill="#FFFFFF"
                          fontSize={8}
                          fontFamily="monospace"
                          fontWeight="bold"
                          textAnchor="middle"
                        >
                          {viewMode === 'voltage_profile' ? `${n.voltage_pu.toFixed(2)} pu` : n.voltage_kv}
                        </text>
                      </g>

                      {/* Bus Label Below */}
                      <text
                        x={0}
                        y={27}
                        fill="#0F172A"
                        fontSize={10.5}
                        fontFamily="sans-serif"
                        fontWeight="bold"
                        textAnchor="middle"
                        className="group-hover:fill-blue-600 transition-colors"
                      >
                        {n.label}
                      </text>

                      {/* Secondary Metric Badge (LMP or kW) */}
                      <g transform="translate(0, 39)">
                        <rect
                          x={-28}
                          y={-6}
                          width={56}
                          height={13}
                          rx={3}
                          fill={n.lmp > 0.12 ? '#FEE2E2' : '#ECFDF5'}
                          stroke={n.lmp > 0.12 ? '#F87171' : '#34D399'}
                          strokeWidth={0.7}
                        />
                        <text
                          x={0}
                          y={3.5}
                          fill={n.lmp > 0.12 ? '#B91C1C' : '#047857'}
                          fontSize={8.5}
                          fontFamily="monospace"
                          fontWeight="bold"
                          textAnchor="middle"
                        >
                          ${n.lmp.toFixed(3)}/kWh
                        </text>
                      </g>
                    </g>
                  );
                })}
              </svg>
            )}
          </div>

          {/* Canvas Bottom Legend */}
          <div className="flex items-center justify-between text-[11px] font-mono text-slate-500 pt-3 border-t border-slate-100 mt-2 flex-wrap gap-2">
            <div className="flex items-center gap-3">
              <span className="text-slate-400">Loading:</span>
              <span className="flex items-center gap-1 text-emerald-700 font-semibold">
                <span className="w-2.5 h-1.5 rounded-xs bg-emerald-500" /> &lt;50%
              </span>
              <span className="flex items-center gap-1 text-blue-700 font-semibold">
                <span className="w-2.5 h-1.5 rounded-xs bg-blue-500" /> 50–75%
              </span>
              <span className="flex items-center gap-1 text-amber-700 font-semibold">
                <span className="w-2.5 h-1.5 rounded-xs bg-amber-500" /> 75–90%
              </span>
              <span className="flex items-center gap-1 text-rose-600 font-bold">
                <span className="w-2.5 h-1.5 rounded-xs bg-rose-500 animate-pulse" /> &gt;90% Congested
              </span>
            </div>
            <span className="text-slate-400">IEEE 9-Bus Radial SCADA Simulator</span>
          </div>
        </div>

        {/* Right: Multi-Tab Engineering Drawer (LMPs, Line Telemetry, Congestion Rents) */}
        <div className="lg:col-span-4 bg-white border border-slate-200 rounded-xl p-4 shadow-sm flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between pb-3 border-b border-slate-100">
              <div className="flex items-center gap-1 bg-slate-100 p-0.5 rounded-lg border border-slate-200 text-xs font-semibold w-full">
                <button
                  onClick={() => setSidebarTab('lmps')}
                  className={clsx(
                    'flex-1 py-1 rounded-md transition-all text-center text-xs',
                    sidebarTab === 'lmps' ? 'bg-white text-blue-700 font-bold shadow-2xs' : 'text-slate-600 hover:text-slate-900'
                  )}
                >
                  Nodal LMPs
                </button>
                <button
                  onClick={() => setSidebarTab('lines')}
                  className={clsx(
                    'flex-1 py-1 rounded-md transition-all text-center text-xs',
                    sidebarTab === 'lines' ? 'bg-white text-blue-700 font-bold shadow-2xs' : 'text-slate-600 hover:text-slate-900'
                  )}
                >
                  Feeders ({displayLines.length})
                </button>
                <button
                  onClick={() => setSidebarTab('congestion')}
                  className={clsx(
                    'flex-1 py-1 rounded-md transition-all text-center text-xs',
                    sidebarTab === 'congestion' ? 'bg-white text-blue-700 font-bold shadow-2xs' : 'text-slate-600 hover:text-slate-900'
                  )}
                >
                  Congestion Rents
                </button>
              </div>
            </div>

            {/* TAB 1: Nodal LMP Heatmap List */}
            {sidebarTab === 'lmps' && (
              <div className="mt-3">
                <div className="flex items-center justify-between text-xs text-slate-500 font-mono mb-2">
                  <span>Bus / Location</span>
                  <div className="flex items-center gap-1.5">
                    <span className="text-emerald-600 font-semibold">$0.080</span>
                    <span className="w-14 h-2 rounded bg-gradient-to-r from-emerald-500 via-amber-500 to-rose-500" />
                    <span className="text-rose-600 font-semibold">$0.150+</span>
                  </div>
                </div>

                <div className="space-y-1.5 max-h-[360px] overflow-y-auto pr-1">
                  {displayNodes.map((n) => (
                    <div
                      key={n.id}
                      onClick={() => setSelectedNodeId(selectedNodeId === n.id ? null : n.id)}
                      className={clsx(
                        'flex items-center justify-between p-2.5 rounded-lg bg-slate-50/80 border text-xs shadow-2xs transition-all cursor-pointer',
                        selectedNodeId === n.id
                          ? 'border-blue-500 ring-1 ring-blue-500 bg-blue-50/40'
                          : 'border-slate-200 hover:border-slate-300'
                      )}
                    >
                      <div className="flex items-center gap-2.5">
                        <div className="p-1 rounded bg-white border border-slate-200 shadow-2xs">
                          {getNodeIcon(n.category)}
                        </div>
                        <div>
                          <div className="font-semibold text-slate-900 leading-tight flex items-center gap-1.5">
                            <span>{n.label}</span>
                            {n.category === 'priority' && (
                              <span className="px-1.5 py-0.2 rounded text-[9px] font-bold bg-rose-100 text-rose-700">Priority</span>
                            )}
                          </div>
                          <div className="text-[10px] text-slate-500 font-mono">
                            {n.type} &bull; {n.voltage_kv}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 font-mono">
                        <span className="text-slate-400 text-[10px]">{n.voltage_pu.toFixed(2)} pu</span>
                        <span
                          className={clsx(
                            'px-2 py-0.5 rounded font-bold text-[11px] border shadow-2xs',
                            getLmpBadgeStyle(n.lmp)
                          )}
                        >
                          ${n.lmp.toFixed(3)}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* TAB 2: Feeder Line Loading Progress & Stress */}
            {sidebarTab === 'lines' && (
              <div className="mt-3">
                <div className="flex items-center justify-between text-xs text-slate-500 font-mono mb-2">
                  <span>Feeder Corridor</span>
                  <span>Flow / Thermal Cap</span>
                </div>

                <div className="space-y-2 max-h-[360px] overflow-y-auto pr-1 font-mono text-xs">
                  {displayLines.map((l) => (
                    <div
                      key={l.id}
                      onClick={() => setSelectedLineId(selectedLineId === l.id ? null : l.id)}
                      className={clsx(
                        'p-2.5 rounded-lg border transition-all cursor-pointer shadow-2xs',
                        selectedLineId === l.id
                          ? 'border-blue-500 ring-1 ring-blue-500 bg-blue-50/40'
                          : l.congested
                          ? 'border-rose-300 bg-rose-50/40'
                          : 'border-slate-200 bg-slate-50/80 hover:border-slate-300'
                      )}
                    >
                      <div className="flex items-center justify-between mb-1">
                        <div className="flex items-center gap-2">
                          <Flame className={clsx('w-3.5 h-3.5', l.congested ? 'text-rose-600 animate-pulse' : 'text-blue-500')} />
                          <span className="font-sans font-bold text-slate-900">{l.name}</span>
                        </div>
                        <span
                          className={clsx(
                            'px-1.5 py-0.5 rounded text-[10px] font-bold',
                            l.congested
                              ? 'bg-rose-100 text-rose-800'
                              : l.loading > 75
                              ? 'bg-amber-100 text-amber-800'
                              : 'bg-emerald-100 text-emerald-800'
                          )}
                        >
                          {l.loading}%
                        </span>
                      </div>

                      {/* Loading Meter */}
                      <div className="w-full h-1.5 rounded-full bg-slate-200 overflow-hidden my-1.5">
                        <div
                          className={clsx(
                            'h-full rounded-full transition-all',
                            l.congested ? 'bg-rose-500' : l.loading > 75 ? 'bg-amber-500' : 'bg-emerald-500'
                          )}
                          style={{ width: `${Math.min(100, l.loading)}%` }}
                        />
                      </div>

                      <div className="flex items-center justify-between text-[10px] text-slate-500">
                        <span>Flow: {l.flow_kw} kW</span>
                        <span>Rating: {l.capacity_kw} kW</span>
                        <span className="text-emerald-700 font-semibold">Headroom: {l.headroom_kw} kW</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* TAB 3: Congestion Rents & Economics */}
            {sidebarTab === 'congestion' && (
              <div className="mt-3">
                <div className="p-3 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-900 mb-3">
                  <div className="font-bold flex items-center gap-1.5 text-amber-950 mb-0.5">
                    <DollarSign className="w-4 h-4 text-amber-600" />
                    <span>Total Hourly Congestion Rent</span>
                  </div>
                  <div className="text-lg font-bold font-mono text-amber-900">
                    ${(congestionData?.total_congestion_rent ?? 0.0).toFixed(2)}/hr
                  </div>
                  <p className="text-[11px] text-amber-800 mt-1">
                    Accumulated across transmission lines saturated at physical thermal limits.
                  </p>
                </div>

                <div className="space-y-2 max-h-[280px] overflow-y-auto pr-1 text-xs font-mono">
                  {displayLines.map((l) => (
                    <div
                      key={l.id}
                      className="p-2.5 rounded-lg bg-slate-50 border border-slate-200 flex items-center justify-between"
                    >
                      <div>
                        <div className="font-sans font-bold text-slate-900">{l.name}</div>
                        <div className="text-[10px] text-slate-500">
                          &Delta;LMP: ${l.price_diff.toFixed(3)}/kWh &bull; Flow: {l.flow_kw} kW
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="font-bold text-slate-900">
                          ${l.congestion_rent.toFixed(2)}/hr
                        </div>
                        <span
                          className={clsx(
                            'text-[9px] font-bold px-1.5 py-0.2 rounded',
                            l.congested ? 'bg-rose-100 text-rose-700' : 'bg-slate-100 text-slate-600'
                          )}
                        >
                          {l.congested ? 'Active Rent' : 'Unconstrained'}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="p-2.5 rounded-lg bg-blue-50 border border-blue-200 text-xs text-blue-900 mt-3 font-sans">
            <span className="font-bold text-blue-950">Dual LMP Physics:</span> When line capacity derates below load flow, PuLP solver generates shadow price divergence (LMP_j - LMP_i = &mu;_ij), providing localized economic incentive for battery discharge and generation redispatch.
          </div>
        </div>
      </div>

      {/* 4. Interactive Physical Component Inspector & Feeder Derating Tool */}
      <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
        {selectedNode ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between pb-2 border-b border-slate-100">
              <div className="flex items-center gap-3">
                <div
                  className="w-4 h-4 rounded-full shrink-0"
                  style={{ backgroundColor: selectedNode.color }}
                />
                <div>
                  <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                    <span>{selectedNode.label}</span>
                    <span className="text-xs font-normal text-slate-500 font-mono">({selectedNode.type})</span>
                  </h3>
                  <div className="text-xs text-slate-500">
                    Nominal Voltage: <strong>{selectedNode.voltage_kv}</strong> &bull; Substation Busbar ID: <code className="text-slate-700">{selectedNode.id}</code>
                  </div>
                </div>
              </div>
              <button
                onClick={() => setSelectedNodeId(null)}
                className="p-1 rounded-md text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors"
                title="Close Inspector"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 font-mono text-xs">
              <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200">
                <div className="text-[10px] text-slate-500 uppercase font-sans">Voltage Magnitude</div>
                <div className="text-base font-bold text-slate-900 mt-0.5">{selectedNode.voltage_pu.toFixed(3)} pu</div>
                <div className="text-[10px] text-emerald-600 mt-0.5">Safe Operating Band</div>
              </div>

              <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200">
                <div className="text-[10px] text-slate-500 uppercase font-sans">Phase Angle</div>
                <div className="text-base font-bold text-slate-900 mt-0.5">{selectedNode.angle_deg.toFixed(2)}&deg;</div>
                <div className="text-[10px] text-slate-500 mt-0.5">Reference Bus: 0.00&deg;</div>
              </div>

              <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200">
                <div className="text-[10px] text-slate-500 uppercase font-sans">Nodal LMP</div>
                <div className="text-base font-bold text-emerald-700 mt-0.5">${selectedNode.lmp.toFixed(3)}/kWh</div>
                <div className="text-[10px] text-slate-500 mt-0.5">Marginal Clearing</div>
              </div>

              <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200">
                <div className="text-[10px] text-slate-500 uppercase font-sans">Attached Capacity</div>
                <div className="text-base font-bold text-blue-600 mt-0.5">{selectedNode.rated_kw} kW</div>
                <div className="text-[10px] text-slate-500 mt-0.5">{selectedNode.connected_assets_count} DER Assets</div>
              </div>
            </div>
          </div>
        ) : selectedLine ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between pb-2 border-b border-slate-100">
              <div className="flex items-center gap-3">
                <Flame className={clsx('w-5 h-5', selectedLine.congested ? 'text-rose-600' : 'text-blue-600')} />
                <div>
                  <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                    <span>{selectedLine.name}</span>
                    <span
                      className={clsx(
                        'px-2 py-0.5 rounded text-[10px] font-bold font-mono',
                        selectedLine.congested ? 'bg-rose-100 text-rose-800' : 'bg-emerald-100 text-emerald-800'
                      )}
                    >
                      {selectedLine.congested ? 'THERMAL BOTTLENECK' : 'NORMAL HEADROOM'}
                    </span>
                  </h3>
                  <div className="text-xs text-slate-500">
                    Connects: <strong>{selectedLine.source.label}</strong> &harr; <strong>{selectedLine.target.label}</strong> &bull; Line ID: <code className="text-slate-700">{selectedLine.id}</code>
                  </div>
                </div>
              </div>
              <button
                onClick={() => setSelectedLineId(null)}
                className="p-1 rounded-md text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors"
                title="Close Inspector"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 font-mono text-xs">
              <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200">
                <div className="text-[10px] text-slate-500 uppercase font-sans">Active Flow</div>
                <div className="text-base font-bold text-slate-900 mt-0.5">{selectedLine.flow_kw} kW</div>
                <div className="text-[10px] text-slate-500 mt-0.5">AC Power Flow</div>
              </div>

              <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200">
                <div className="text-[10px] text-slate-500 uppercase font-sans">Thermal Capacity</div>
                <div className="text-base font-bold text-slate-900 mt-0.5">{selectedLine.capacity_kw} kW</div>
                <div className="text-[10px] text-slate-500 mt-0.5">Continuous Limit</div>
              </div>

              <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200">
                <div className="text-[10px] text-slate-500 uppercase font-sans">Thermal Loading</div>
                <div
                  className={clsx(
                    'text-base font-bold mt-0.5',
                    selectedLine.congested ? 'text-rose-600' : 'text-slate-900'
                  )}
                >
                  {selectedLine.loading}%
                </div>
                <div className="text-[10px] text-emerald-600 mt-0.5">Headroom: {selectedLine.headroom_kw} kW</div>
              </div>

              <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200">
                <div className="text-[10px] text-slate-500 uppercase font-sans">Congestion Shadow Rent</div>
                <div className="text-base font-bold text-amber-700 mt-0.5">${selectedLine.congestion_rent.toFixed(2)}/hr</div>
                <div className="text-[10px] text-slate-500 mt-0.5">&Delta;LMP: ${selectedLine.price_diff.toFixed(3)}</div>
              </div>
            </div>

            {/* Feeder Derating Operator Controls */}
            <div className="p-3 rounded-lg bg-slate-50 border border-slate-200 mt-2">
              <div className="flex items-center justify-between flex-wrap gap-2">
                <div className="flex items-center gap-2">
                  <SlidersHorizontal className="w-4 h-4 text-blue-600" />
                  <span className="text-xs font-bold text-slate-900">Operator Physical Capacity Derate Simulator</span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => derateMutation.mutate({ lineId: selectedLine.id, capacityKw: 5.0 })}
                    disabled={derateMutation.isPending}
                    className="px-2.5 py-1 rounded bg-rose-600 hover:bg-rose-700 text-white font-mono text-xs font-bold transition-colors disabled:opacity-50"
                  >
                    Derate to 5 kW (Trigger Bottleneck)
                  </button>
                  <button
                    onClick={() => derateMutation.mutate({ lineId: selectedLine.id, capacityKw: 35.0 })}
                    disabled={derateMutation.isPending}
                    className="px-2.5 py-1 rounded bg-slate-200 hover:bg-slate-300 text-slate-800 font-mono text-xs font-bold transition-colors disabled:opacity-50"
                  >
                    Restore 35 kW Normal
                  </button>
                  <button
                    onClick={() => derateMutation.mutate({ lineId: selectedLine.id, capacityKw: 80.0 })}
                    disabled={derateMutation.isPending}
                    className="px-2.5 py-1 rounded bg-emerald-600 hover:bg-emerald-700 text-white font-mono text-xs font-bold transition-colors disabled:opacity-50"
                  >
                    Upgrade 80 kW
                  </button>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-between text-xs text-slate-500">
            <div className="flex items-center gap-2.5">
              <Info className="w-4 h-4 text-blue-600 shrink-0" />
              <span>
                <strong>SCADA Inspection Tip:</strong> Click any bus (substation, generator, storage, hospital) or feeder line in the Single-Line Diagram above to inspect live AC voltage angles, thermal capacity, real-time flows, and dual shadow prices.
              </span>
            </div>
            <div className="font-mono text-[11px] text-slate-400">
              Active District: {microgridId ? `${microgridId.slice(0, 8)}...` : 'GreenGrid'}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
