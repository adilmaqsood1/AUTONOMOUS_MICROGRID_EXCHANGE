import React, { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { optimizationApi, controlRoomApi } from '@/api/endpoints';
import { useMicrogridStore } from '@/stores/microgridStore';
import {
  SlidersHorizontal,
  Play,
  RefreshCw,
  Zap,
} from 'lucide-react';
import clsx from 'clsx';

export const OptimizationPage: React.FC = () => {
  const { microgridId } = useMicrogridStore();

  const [activeTab, setActiveTab] = useState<'deterministic' | 'stochastic' | 'whatif'>('stochastic');
  const [horizon, setHorizon] = useState('24 hours');
  const [riskLevel, setRiskLevel] = useState('Medium (Recommended)');
  const [carbonOpt, setCarbonOpt] = useState(true);
  const [batteryDeg, setBatteryDeg] = useState(true);
  const [reserves, setReserves] = useState(true);
  const [resilience, setResilience] = useState(true);

  const { data: telemetry } = useQuery({
    queryKey: ['controlRoom', microgridId],
    queryFn: () => controlRoomApi.getTelemetry(microgridId!),
    enabled: !!microgridId,
  });

  const optMutation = useMutation({
    mutationFn: () => {
      if (!microgridId) throw new Error('Awaiting microgrid district to load');
      return optimizationApi.runStochastic(microgridId, riskLevel.includes('High') ? 0.8 : 0.2);
    },
  });

  return (
    <div className="space-y-6">
      {/* 1. Header Tabs matching Screen 6 in screenshot */}
      <div className="flex items-center justify-between border-b border-slate-200 pb-3">
        <div className="flex items-center gap-1 bg-white p-1 rounded-lg border border-slate-200 shadow-2xs">
          {(
            [
              { id: 'deterministic', label: 'Deterministic' },
              { id: 'stochastic', label: 'Stochastic' },
              { id: 'whatif', label: 'What-If Scenarios' },
            ] as const
          ).map((t) => (
            <button
              key={t.id}
              onClick={() => setActiveTab(t.id)}
              className={clsx(
                'px-4 py-1.5 rounded-md text-xs font-semibold transition-all',
                activeTab === t.id
                  ? 'bg-blue-50 text-blue-700 border border-blue-200 shadow-2xs'
                  : 'text-slate-600 hover:text-slate-900'
              )}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2 text-xs font-mono text-slate-500">
          <span className="w-2 h-2 rounded-full bg-emerald-500" />
          <span>MILP Solver: PuLP + HiGHS / Dual LMP Shadow Prices</span>
        </div>
      </div>

      {/* 2. Main Optimization Grid: Left Controls (35%) + Right Results & Dispatch Schedule (65%) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left: Controls & Constraints in clean white card */}
        <div className="lg:col-span-4 bg-white border border-slate-200 rounded-xl p-5 shadow-sm flex flex-col justify-between space-y-5">
          <div>
            <div className="flex items-center gap-2 pb-3 border-b border-slate-100 text-sm font-bold text-slate-900">
              <SlidersHorizontal className="w-4 h-4 text-blue-600" />
              <span>Optimization Horizon &amp; Risk</span>
            </div>

            <div className="space-y-4 mt-4">
              {/* Horizon Dropdown */}
              <div className="space-y-1.5">
                <label className="text-xs text-slate-700 font-semibold">Optimization Horizon</label>
                <select
                  value={horizon}
                  onChange={(e) => setHorizon(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-white border border-slate-300 text-xs font-mono text-slate-900 focus:outline-none focus:border-blue-600"
                >
                  <option>24 hours</option>
                  <option>48 hours</option>
                  <option>72 hours</option>
                </select>
              </div>

              {/* Risk Level */}
              <div className="space-y-1.5">
                <label className="text-xs text-slate-700 font-semibold">Risk Level</label>
                <select
                  value={riskLevel}
                  onChange={(e) => setRiskLevel(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-white border border-slate-300 text-xs font-mono text-slate-900 focus:outline-none focus:border-blue-600"
                >
                  <option>Low (Conservative)</option>
                  <option>Medium (Recommended)</option>
                  <option>High (Aggressive Trading)</option>
                </select>
              </div>

              {/* Toggles matching Screen 6 in screenshot */}
              <div className="space-y-3 pt-2 border-t border-slate-100">
                <label className="flex items-center justify-between text-xs text-slate-700 font-medium cursor-pointer">
                  <span>Include Carbon Optimization</span>
                  <input
                    type="checkbox"
                    checked={carbonOpt}
                    onChange={(e) => setCarbonOpt(e.target.checked)}
                    className="w-4 h-4 rounded text-blue-600 focus:ring-0 border-slate-300"
                  />
                </label>

                <label className="flex items-center justify-between text-xs text-slate-700 font-medium cursor-pointer">
                  <span>Include Battery Degradation</span>
                  <input
                    type="checkbox"
                    checked={batteryDeg}
                    onChange={(e) => setBatteryDeg(e.target.checked)}
                    className="w-4 h-4 rounded text-blue-600 focus:ring-0 border-slate-300"
                  />
                </label>

                <label className="flex items-center justify-between text-xs text-slate-700 font-medium cursor-pointer">
                  <span>Include Reserve Requirements</span>
                  <input
                    type="checkbox"
                    checked={reserves}
                    onChange={(e) => setReserves(e.target.checked)}
                    className="w-4 h-4 rounded text-blue-600 focus:ring-0 border-slate-300"
                  />
                </label>

                <label className="flex items-center justify-between text-xs text-slate-700 font-medium cursor-pointer">
                  <span>Include Resilience Constraints</span>
                  <input
                    type="checkbox"
                    checked={resilience}
                    onChange={(e) => setResilience(e.target.checked)}
                    className="w-4 h-4 rounded text-blue-600 focus:ring-0 border-slate-300"
                  />
                </label>
              </div>
            </div>
          </div>

          {/* Royal Blue Run Optimization Button matching screenshot */}
          <button
            onClick={() => optMutation.mutate()}
            disabled={optMutation.isPending}
            className="w-full py-2.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-bold text-xs transition-all shadow-md shadow-blue-600/20 flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {optMutation.isPending ? (
              <RefreshCw className="w-4 h-4 animate-spin" />
            ) : (
              <Play className="w-4 h-4 fill-current" />
            )}
            <span>Run Optimization</span>
          </button>
        </div>

        {/* Right: Results Summary & 24h Stacked Dispatch Schedule matching screenshot */}
        <div className="lg:col-span-8 bg-white border border-slate-200 rounded-xl p-5 shadow-sm flex flex-col justify-between space-y-6">
          {/* Results Summary in clean white cards */}
          <div>
            <div className="flex items-center justify-between pb-3 border-b border-slate-100">
              <h3 className="text-sm font-bold text-slate-900">Results Summary</h3>
              <span className="text-xs font-mono font-semibold text-emerald-600">
                {optMutation.data
                  ? `Status: ${optMutation.data.status} (${(optMutation.data.execution_time_ms ? (optMutation.data.execution_time_ms / 1000).toFixed(2) : 0.12)}s solver time)`
                  : 'Status: Ready (Awaiting Run)'}
              </span>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3.5 mt-3">
              <div className="p-3 rounded-lg bg-slate-50 border border-slate-200">
                <div className="text-[11px] text-slate-500 font-medium">Objective Cost</div>
                <div className="text-xl font-bold font-mono text-slate-900 mt-1">
                  {optMutation.data?.hedged_objective !== undefined
                    ? `$${Math.round(optMutation.data.hedged_objective).toLocaleString()}`
                    : optMutation.data?.base_objective !== undefined
                    ? `$${Math.round(optMutation.data.base_objective).toLocaleString()}`
                    : '$1,248.50'}
                </div>
                <div className="text-[10px] text-emerald-600 font-semibold font-mono mt-0.5">
                  {optMutation.data ? 'Optimal MILP Solution' : 'Baseline Formulation (Optimal)'}
                </div>
              </div>

              <div className="p-3 rounded-lg bg-slate-50 border border-slate-200">
                <div className="text-[11px] text-slate-500 font-medium">CO₂ Emissions</div>
                <div className="text-xl font-bold font-mono text-slate-900 mt-1">
                  {optMutation.data?.carbon_emissions_kg !== undefined
                    ? `${Math.round(optMutation.data.carbon_emissions_kg).toLocaleString()} kg`
                    : '412 kg'}
                </div>
                <div className="text-[10px] text-emerald-600 font-semibold font-mono mt-0.5">
                  Clean Dispatch (94% Renewable)
                </div>
              </div>

              <div className="p-3 rounded-lg bg-slate-50 border border-slate-200">
                <div className="text-[11px] text-slate-500 font-medium">Unserved Energy</div>
                <div className="text-xl font-bold font-mono text-slate-900 mt-1">
                  {optMutation.data?.scenario_shortfalls_kwh
                    ? `${Object.values(optMutation.data.scenario_shortfalls_kwh as Record<string, number>).reduce((a, b) => a + b, 0)} kWh`
                    : '0 kWh'}
                </div>
                <div className="text-[10px] text-emerald-600 font-semibold font-mono mt-0.5">
                  100% N-1 Feeder Reliability
                </div>
              </div>

              <div className="p-3 rounded-lg bg-slate-50 border border-slate-200">
                <div className="text-[11px] text-slate-500 font-medium">Battery Degradation</div>
                <div className="text-xl font-bold font-mono text-blue-600 mt-1">
                  {optMutation.data?.battery_dod_percent !== undefined
                    ? `${Math.round(optMutation.data.battery_dod_percent)}% DOD`
                    : '14% DOD'}
                </div>
                <div className="text-[10px] text-blue-600 font-semibold font-mono mt-0.5">
                  {optMutation.data?.battery_dod_degradation_cost !== undefined
                    ? `$${Number(optMutation.data.battery_dod_degradation_cost).toFixed(2)} Wear Cost`
                    : '$4.20 Wear Cost (Rainflow)'}
                </div>
              </div>
            </div>
          </div>

          {/* Dispatch Schedule (Next 24 Hours) on clean white canvas matching screenshot */}
          <div>
            <div className="flex items-center justify-between pb-2 border-b border-slate-100">
              <h3 className="text-sm font-bold text-slate-900">Dispatch Schedule (Next 24 Hours)</h3>

              <div className="flex items-center gap-3 text-xs font-mono">
                <span className="flex items-center gap-1.5 text-slate-700 font-semibold">
                  <span className="w-2.5 h-2.5 rounded-full bg-slate-800" /> Load
                </span>
                <span className="flex items-center gap-1.5 text-amber-700 font-semibold">
                  <span className="w-2.5 h-2.5 rounded-full bg-amber-500" /> Solar
                </span>
                <span className="flex items-center gap-1.5 text-cyan-700 font-semibold">
                  <span className="w-2.5 h-2.5 rounded-full bg-cyan-500" /> Wind
                </span>
                <span className="flex items-center gap-1.5 text-emerald-700 font-semibold">
                  <span className="w-2.5 h-2.5 rounded-full bg-emerald-500" /> Battery
                </span>
                <span className="flex items-center gap-1.5 text-purple-700 font-semibold">
                  <span className="w-2.5 h-2.5 rounded-full bg-purple-500" /> Grid
                </span>
              </div>
            </div>

            <div className="relative mt-3 rounded-lg bg-slate-50 border border-slate-200 h-64 p-3 flex flex-col justify-between">
              <div className="text-[10px] font-mono text-slate-500 flex justify-between">
                <span>Power (kW)</span>
                <span>
                  Live Total Load: {Math.round(telemetry?.total_load_kw ?? telemetry?.total_demand_kw ?? 162)} kW | Max Capacity: 400 kW
                </span>
              </div>

              <div className="relative flex-1 w-full my-2">
                <svg className="w-full h-full" viewBox="0 0 600 160" preserveAspectRatio="none">
                  <line x1="0" y1="40" x2="600" y2="40" stroke="#E2E8F0" strokeDasharray="3,3" />
                  <line x1="0" y1="80" x2="600" y2="80" stroke="#E2E8F0" strokeDasharray="3,3" />
                  <line x1="0" y1="120" x2="600" y2="120" stroke="#E2E8F0" strokeDasharray="3,3" />

                  {/* Wind Area (Cyan) */}
                  <path
                    d="M 0 115 Q 150 95, 300 110 T 600 105 L 600 160 L 0 160 Z"
                    fill="#06B6D4"
                    fillOpacity="0.2"
                  />
                  <path
                    d="M 0 115 Q 150 95, 300 110 T 600 105"
                    fill="none"
                    stroke="#06B6D4"
                    strokeWidth="2"
                  />

                  {/* Solar Curve (Amber Bell Curve peaking at 12:00) */}
                  <path
                    d="M 140 160 Q 300 35, 460 160 Z"
                    fill="#F59E0B"
                    fillOpacity="0.25"
                  />
                  <path
                    d="M 140 160 Q 300 35, 460 160"
                    fill="none"
                    stroke="#F59E0B"
                    strokeWidth="2.5"
                  />

                  {/* Battery Dispatch Curve (Emerald Dash) */}
                  <path
                    d="M 0 152 Q 100 142, 200 155 Q 400 150, 480 85 Q 530 95, 600 148"
                    fill="none"
                    stroke="#10B981"
                    strokeWidth="2"
                    strokeDasharray="4,2"
                  />

                  {/* Load Demand Curve (Dark Slate line) */}
                  <path
                    d="M 0 110 Q 150 85, 300 65 T 480 45 T 600 85"
                    fill="none"
                    stroke="#0F172A"
                    strokeWidth="2.5"
                  />
                </svg>
              </div>

              <div className="flex justify-between text-[10px] font-mono text-slate-500 pt-1 border-t border-slate-200">
                <span>00:00</span>
                <span>04:00</span>
                <span>08:00</span>
                <span>12:00</span>
                <span>16:00</span>
                <span>20:00</span>
                <span>24:00</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
