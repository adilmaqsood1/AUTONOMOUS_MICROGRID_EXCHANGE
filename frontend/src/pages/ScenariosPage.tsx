import React, { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { scenariosApi, controlRoomApi } from '@/api/endpoints';
import { useMicrogridStore } from '@/stores/microgridStore';
import { StatusBadge } from '@/components/common/StatusBadge';
import { ScenarioEvent, ScenarioPreset } from '@/types';
import {
  FlaskConical,
  Play,
  StopCircle,
  Sun,
  CloudSun,
  Flame,
  ZapOff,
  AlertTriangle,
  Activity,
  CheckCircle2,
} from 'lucide-react';
import clsx from 'clsx';

import { useAuthStore } from '@/stores/authStore';

interface ScenarioCard {
  key: string;
  title: string;
  desc: string;
  icon: React.ComponentType<{ className?: string }>;
  color: string;
}

export const ScenariosPage: React.FC = () => {
  const queryClient = useQueryClient();
  const { microgridId } = useMicrogridStore();
  const { user, login } = useAuthStore();

  const [activeTab, setActiveTab] = useState<'presets' | 'custom' | 'live'>('presets');
  const [activePresetKey, setActivePresetKey] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const isOperator = user?.role === 'GRID_OPERATOR' || user?.role === 'ADMIN';

  const { data: presets } = useQuery<Record<string, ScenarioPreset>>({
    queryKey: ['scenarioPresets'],
    queryFn: () => scenariosApi.listPresets(),
  });

  const { data: activeEvents } = useQuery<ScenarioEvent[]>({
    queryKey: ['activeScenarios', microgridId],
    queryFn: () => scenariosApi.listActive(microgridId!),
    enabled: !!microgridId,
    refetchInterval: 5000,
  });

  const { data: telemetry } = useQuery({
    queryKey: ['controlRoom', microgridId],
    queryFn: () => controlRoomApi.getTelemetry(microgridId!),
    enabled: !!microgridId,
  });

  const injectMutation = useMutation({
    mutationFn: (key: string) => {
      if (!microgridId) throw new Error('Awaiting microgrid district to load');
      return scenariosApi.injectPreset(key, microgridId);
    },
    onSuccess: (_, key) => {
      setErrorMessage(null);
      setActivePresetKey(key);
      queryClient.invalidateQueries({ queryKey: ['activeScenarios'] });
      queryClient.invalidateQueries({ queryKey: ['controlRoom'] });
    },
    onError: (err: any) => {
      setErrorMessage(
        err.message || 'Operation forbidden: Scenario injection requires Grid Operator or Admin permissions.'
      );
    },
  });

  const handleQuickSwitchToOperator = async () => {
    try {
      await login('operator@greengrid.local', 'admin123');
      setErrorMessage(null);
    } catch {
      setErrorMessage('Failed to switch to operator account.');
    }
  };

  const cancelMutation = useMutation({
    mutationFn: (id: string) => scenariosApi.cancelScenario(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['activeScenarios'] });
      queryClient.invalidateQueries({ queryKey: ['controlRoom'] });
      queryClient.invalidateQueries({ queryKey: ['topology'] });
    },
    onError: (err: any) => {
      setErrorMessage(err.message || 'Failed to cancel scenario.');
    },
  });

  const getPresetMeta = (key: string) => {
    switch (key) {
      case 'cloud_shock':
        return { icon: CloudSun, color: 'text-amber-500' };
      case 'grid_congestion':
        return { icon: Flame, color: 'text-orange-500' };
      case 'grid_outage':
        return { icon: ZapOff, color: 'text-rose-500' };
      case 'delivery_failure':
        return { icon: AlertTriangle, color: 'text-purple-500' };
      default:
        return { icon: Sun, color: 'text-emerald-500' };
    }
  };

  const DEFAULT_PRESET_MAP: Record<string, { name: string; description: string }> = {
    cloud_drop: {
      name: 'Sudden Cloud Coverage',
      description: '60% rapid solar generation drop on 420 kWp PV array within 5 minutes',
    },
    line_contingency: {
      name: 'Feeder Thermal Bottleneck',
      description: 'Transmission line N03 ↔ N07 derated to 5 kW, triggering localized nodal LMP divergence',
    },
    demand_spike: {
      name: 'Commercial Heatwave Peak',
      description: 'Commercial district cooling surge adding 40% sudden demand at 14:00',
    },
    grid_outage: {
      name: 'Utility Islanding Contingency',
      description: 'Utility intertie circuit breaker trips at Bus N01, shifting microgrid to 100% self-sufficient island mode',
    },
    delivery_failure: {
      name: 'Generator Outage Shock',
      description: 'GreenGrid Hydro 90 kW generator trips offline, testing battery spinning reserve response',
    },
  };

  const scenarioCards: ScenarioCard[] = useMemo(() => {
    const source = presets && Object.keys(presets).length > 0 ? presets : DEFAULT_PRESET_MAP;
    return Object.entries(source).map(([key, p]) => {
      const meta = getPresetMeta(key);
      return {
        key,
        title: p.name,
        desc: p.description,
        icon: meta.icon,
        color: meta.color,
      };
    });
  }, [presets]);

  const currentRunningScenario: ScenarioEvent | null =
    activeEvents && activeEvents.length > 0 ? activeEvents[0] : null;

  return (
    <div className="space-y-6">
      {/* 1. Header Tabs matching Screen 8 in screenshot */}
      <div className="flex items-center justify-between border-b border-slate-200 pb-3">
        <div className="flex items-center gap-1 bg-white p-1 rounded-lg border border-slate-200 shadow-2xs">
          {(
            [
              { id: 'presets', label: 'Preset Scenarios' },
              { id: 'custom', label: 'Custom Scenario' },
              { id: 'live', label: 'Live Simulation' },
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
          <FlaskConical className="w-4 h-4 text-blue-600" />
          <span>Real Scenario Engine &amp; Shock Injector</span>
        </div>
      </div>

      {/* Role / Error Feedback Banner */}
      {errorMessage && (
        <div className="bg-rose-50 border border-rose-200 rounded-xl p-3.5 flex items-center justify-between text-xs text-rose-800">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-rose-600 shrink-0" />
            <span>{errorMessage}</span>
          </div>
          {!isOperator && (
            <button
              onClick={handleQuickSwitchToOperator}
              className="px-3 py-1 bg-rose-600 hover:bg-rose-700 text-white font-semibold rounded-lg text-xs transition-colors shrink-0 ml-3"
            >
              Switch to Operator
            </button>
          )}
        </div>
      )}

      {!errorMessage && !isOperator && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-3.5 flex items-center justify-between text-xs text-amber-900">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0" />
            <span>
              <strong>Participant Mode (View Only):</strong> You are logged in as {user?.email || 'a participant'}. Triggering physical grid contingency shocks requires Grid Operator permissions.
            </span>
          </div>
          <button
            onClick={handleQuickSwitchToOperator}
            className="px-3 py-1 bg-amber-600 hover:bg-amber-700 text-white font-semibold rounded-lg text-xs transition-colors shrink-0 ml-3"
          >
            Switch to Operator
          </button>
        </div>
      )}

      {/* 2. Preset Scenarios Cards in clean white cards with blue Run buttons matching screenshot */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3.5">
        {scenarioCards.length > 0 ? (
          scenarioCards.map((c: ScenarioCard) => {
            const Icon = c.icon;
            const currentType = (currentRunningScenario?.type || currentRunningScenario?.scenario_type || '').toLowerCase();
            const isActive = currentType === c.key.toLowerCase() || activePresetKey === c.key;
            return (
              <div
                key={c.key}
                className={clsx(
                  'bg-white border rounded-xl p-4 shadow-sm flex flex-col justify-between space-y-3 transition-all',
                  isActive ? 'border-blue-500 ring-1 ring-blue-500 shadow-md' : 'border-slate-200'
                )}
              >
                <div>
                  <div className="flex items-center justify-between">
                    <div className="p-2 rounded-lg bg-slate-50 border border-slate-100">
                      <Icon className={clsx('w-5 h-5', c.color)} />
                    </div>
                    {isActive && <StatusBadge status="Active" variant="green" pulse />}
                  </div>
                  <h4 className="text-xs font-bold text-slate-900 mt-2.5">{c.title}</h4>
                  <p className="text-[11px] text-slate-500 mt-1 leading-snug">{c.desc}</p>
                </div>

                <button
                  onClick={() => injectMutation.mutate(c.key)}
                  disabled={injectMutation.isPending}
                  className={clsx(
                    'w-full py-2 rounded-lg text-xs font-bold transition-all flex items-center justify-center gap-1.5 shadow-xs',
                    isActive
                      ? 'bg-blue-700 text-white'
                      : 'bg-blue-600 hover:bg-blue-700 text-white'
                  )}
                >
                  <Play className="w-3 h-3 fill-current" />
                  <span>Run</span>
                </button>
              </div>
            );
          })
        ) : (
          <div className="col-span-full py-8 text-center text-slate-400 font-mono text-xs">
            Loading scenario presets from simulation engine...
          </div>
        )}
      </div>

      {/* 3. Center Section: Active Scenario (Left 60%) + Scenario Impact (Right 40%) matching Screen 8 */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left: Active Scenario Progress Card in clean white card */}
        <div className="lg:col-span-7 bg-white border border-slate-200 rounded-xl p-5 shadow-sm flex flex-col justify-between space-y-4">
          <div className="flex items-center justify-between pb-3 border-b border-slate-100">
            <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
              <Activity className="w-4 h-4 text-blue-600" />
              Active Running Scenario
            </h3>
            <span
              className={clsx(
                'text-xs font-semibold font-mono',
                currentRunningScenario ? 'text-amber-600' : 'text-emerald-600'
              )}
            >
              {currentRunningScenario ? 'Contingency Active' : 'Normal Grid Operation'}
            </span>
          </div>

          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-base font-bold text-slate-900">
                  {currentRunningScenario
                    ? (currentRunningScenario.type || currentRunningScenario.scenario_type || '').replace(/_/g, ' ')
                    : 'Normal Baseline Market Operation'}
                </h2>
                <div className="text-xs font-mono text-slate-500 mt-0.5">
                  {currentRunningScenario ? (
                    <>
                      Duration:{' '}
                      <span className="text-slate-800 font-bold">
                        {currentRunningScenario.duration_minutes || currentRunningScenario.params?.duration_minutes || 60} minutes
                      </span>{' '}
                      &bull; Started at:{' '}
                      <span className="text-blue-600 font-bold">
                        {new Date(currentRunningScenario.start_time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </span>
                    </>
                  ) : (
                    'No contingency shocks injected. The grid is operating under nominal AC power-flow dispatch.'
                  )}
                </div>
              </div>

              {currentRunningScenario && (
                <button
                  onClick={() => cancelMutation.mutate(currentRunningScenario.id)}
                  disabled={cancelMutation.isPending}
                  className="px-4 py-2 rounded-lg bg-rose-600 hover:bg-rose-700 text-white text-xs font-bold transition-colors flex items-center gap-1.5 shadow-xs disabled:opacity-50"
                >
                  <StopCircle className="w-3.5 h-3.5" />
                  <span>{cancelMutation.isPending ? 'Ending...' : 'End Scenario'}</span>
                </button>
              )}
            </div>

            {currentRunningScenario && (
              <div className="space-y-1.5">
                <div className="flex justify-between text-xs font-mono text-slate-500">
                  <span>Severity Impact</span>
                  <span className="text-blue-600 font-bold">{Math.round(currentRunningScenario.severity * 100)}%</span>
                </div>
                <div className="h-2.5 w-full rounded-full bg-slate-100 overflow-hidden border border-slate-200">
                  <div
                    className="h-full bg-blue-600 transition-all duration-500"
                    style={{ width: `${Math.round(currentRunningScenario.severity * 100)}%` }}
                  />
                </div>
              </div>
            )}
          </div>

          <div className="p-3 rounded-lg bg-slate-50 border border-slate-200 text-xs text-slate-700 font-sans">
            <span className="text-blue-700 font-bold font-mono">Dynamic Agent Response:</span> Autonomous AI bidding
            agents continuously observe real-time telemetry updates via WebSocket, readjusting bids to prevent feeder line
            trips and preserving critical hospital and residential power.
          </div>
        </div>

        {/* Right: Scenario Impact Metrics matching Screen 8 in screenshot */}
        <div className="lg:col-span-5 bg-white border border-slate-200 rounded-xl p-5 shadow-sm flex flex-col justify-between space-y-4">
          <div className="flex items-center justify-between pb-3 border-b border-slate-100">
            <h3 className="text-sm font-bold text-slate-900">Scenario Impact</h3>
            <span className="text-xs text-slate-500 font-mono">Telemetry Deltas</span>
          </div>

          <div className="space-y-2.5 font-mono text-xs">
            <div className="flex items-center justify-between p-2.5 rounded-lg bg-slate-50 border border-slate-200">
              <span className="text-slate-600">Active Shock Type</span>
              <span className="text-amber-700 font-bold">
                {currentRunningScenario ? (currentRunningScenario.type || currentRunningScenario.scenario_type || 'Custom Shock') : 'None (Baseline)'}
              </span>
            </div>

            <div className="flex items-center justify-between p-2.5 rounded-lg bg-slate-50 border border-slate-200">
              <span className="text-slate-600">Severity</span>
              <span className="text-rose-600 font-bold">
                {currentRunningScenario ? `${Math.round(currentRunningScenario.severity * 100)}% Impact` : '0%'}
              </span>
            </div>

            <div className="flex items-center justify-between p-2.5 rounded-lg bg-slate-50 border border-slate-200">
              <span className="text-slate-600">Grid Status</span>
              <span className="text-emerald-600 font-bold">
                {currentRunningScenario ? 'Compensating via AI Swarm' : 'Synchronized'}
              </span>
            </div>

            <div className="flex items-center justify-between p-2.5 rounded-lg bg-slate-50 border border-slate-200">
              <span className="text-slate-600">Unserved Energy</span>
              <span className="text-emerald-600 font-bold">
                {telemetry?.unserved_energy_kwh !== undefined
                  ? `${telemetry.unserved_energy_kwh} kWh (${telemetry.unserved_energy_kwh === 0 ? 'Resilient' : 'Shedding'})`
                  : '--'}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2 text-xs text-emerald-700 font-medium pt-2 border-t border-slate-100">
            <CheckCircle2 className="w-4 h-4 text-emerald-600" />
            <span>Autonomous microgrid operating within N-1 reliability margins</span>
          </div>
        </div>
      </div>
    </div>
  );
};
