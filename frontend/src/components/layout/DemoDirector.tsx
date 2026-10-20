import React, { useState } from 'react';
import { useUIStore } from '@/stores/uiStore';
import { useMicrogridStore } from '@/stores/microgridStore';
import { scenariosApi, agentsApi } from '@/api/endpoints';
import {
  X,
  Play,
  AlertTriangle,
  Sun,
  CloudSun,
  ZapOff,
  Flame,
  CheckCircle2,
  RefreshCw,
} from 'lucide-react';

import { useQuery, useQueryClient } from '@tanstack/react-query';

export const DemoDirector: React.FC = () => {
  const queryClient = useQueryClient();
  const { demoDirectorOpen, toggleDemoDirector } = useUIStore();
  const { microgridId } = useMicrogridStore();

  const [loadingAction, setLoadingAction] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  if (!demoDirectorOpen) return null;

  const { data: presetsRecord } = useQuery({
    queryKey: ['scenarioPresets'],
    queryFn: () => scenariosApi.listPresets(),
  });

  const activeMgId = microgridId;

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ['controlRoom'] });
    queryClient.invalidateQueries({ queryKey: ['topology'] });
    queryClient.invalidateQueries({ queryKey: ['orderBook'] });
    queryClient.invalidateQueries({ queryKey: ['trades'] });
    queryClient.invalidateQueries({ queryKey: ['agents'] });
    queryClient.invalidateQueries({ queryKey: ['agentDecisions'] });
    queryClient.invalidateQueries({ queryKey: ['agentOutcomes'] });
    queryClient.invalidateQueries({ queryKey: ['agentLeaderboard'] });
    queryClient.invalidateQueries({ queryKey: ['activeScenarios'] });
  };

  const handleInjectScenario = async (key: string, name: string) => {
    if (!activeMgId) {
      setSuccessMsg('Waiting for active microgrid to load...');
      return;
    }
    try {
      setLoadingAction(key);
      setSuccessMsg(null);
      await scenariosApi.injectPreset(key, activeMgId);
      invalidateAll();
      setSuccessMsg(`Preset activated: ${name}`);
      setTimeout(() => setSuccessMsg(null), 4000);
    } catch (err: any) {
      console.error(err);
      setSuccessMsg(`Error: ${err.message || 'Action requires Operator role'}`);
      setTimeout(() => setSuccessMsg(null), 5000);
    } finally {
      setLoadingAction(null);
    }
  };

  const handleRunMarketRound = async () => {
    if (!activeMgId) {
      setSuccessMsg('Waiting for active microgrid to load...');
      return;
    }
    try {
      setLoadingAction('run_market');
      setSuccessMsg(null);
      const res = await agentsApi.runMarketRound(activeMgId, 'AI_ASSISTED');
      invalidateAll();
      setSuccessMsg(
        `Round ${res.round_id ? res.round_id.slice(0, 8) : 'Success'} Cleared: ${res.matched_kwh ?? 0} kWh at $${(res.clearing_price ?? 0).toFixed(3)}/kWh (${res.orders_count ?? 0} orders)`
      );
      setTimeout(() => setSuccessMsg(null), 6000);
    } catch (err: any) {
      console.error(err);
      setSuccessMsg(`Market round error: ${err.message || 'Requires Operator/Admin role'}`);
      setTimeout(() => setSuccessMsg(null), 5000);
    } finally {
      setLoadingAction(null);
    }
  };

  const presetsList = presetsRecord
    ? Object.entries(presetsRecord).map(([key, preset]) => ({
        key,
        label: preset.name || key,
        description: preset.description,
      }))
    : [];

  return (
    <div className="fixed bottom-4 left-64 right-8 bg-white/95 border border-slate-300 rounded-xl shadow-2xl backdrop-blur-xl p-4 z-50 animate-in fade-in slide-in-from-bottom duration-200">
      <div className="flex items-center justify-between border-b border-slate-200 pb-2 mb-3">
        <div className="flex items-center gap-2">
          <div className="w-2.5 h-2.5 rounded-full bg-blue-600 animate-ping" />
          <h3 className="text-sm font-bold text-slate-900 tracking-wide flex items-center gap-2">
            Demo Director <span className="text-[10px] text-blue-700 uppercase font-mono px-2 py-0.5 rounded bg-blue-50 border border-blue-200">Real Backend Scenarios</span>
          </h3>
        </div>
        <button
          onClick={toggleDemoDirector}
          className="text-slate-400 hover:text-slate-700 p-1 rounded-lg hover:bg-slate-100 transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          {presetsList.length === 0 ? (
            <span className="text-xs text-slate-400 italic">Loading presets from backend...</span>
          ) : (
            presetsList.map((preset) => {
              const isLoading = loadingAction === preset.key;
              return (
                <button
                  key={preset.key}
                  disabled={!!loadingAction || !activeMgId}
                  onClick={() => handleInjectScenario(preset.key, preset.label)}
                  title={preset.description}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-50 hover:bg-slate-100 border border-slate-200 text-xs font-semibold text-slate-800 transition-all disabled:opacity-50"
                >
                  <Sun className="w-3.5 h-3.5 text-blue-600" />
                  <span>{preset.label}</span>
                  {isLoading && <RefreshCw className="w-3 h-3 animate-spin text-blue-600 ml-1" />}
                </button>
              );
            })
          )}
        </div>

        <div className="flex items-center gap-2">
          <button
            disabled={!!loadingAction}
            onClick={handleRunMarketRound}
            className="flex items-center gap-2 px-4 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-xs font-bold transition-all shadow-md shadow-blue-600/20 disabled:opacity-50"
          >
            {loadingAction === 'run_market' ? (
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Play className="w-3.5 h-3.5 fill-current" />
            )}
            <span>Execute AI Market Round</span>
          </button>
        </div>
      </div>

      {successMsg && (
        <div className="mt-2.5 py-1 px-3 bg-emerald-50 border border-emerald-200 rounded-lg text-xs text-emerald-800 font-mono flex items-center gap-2">
          <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
          <span>{successMsg}</span>
        </div>
      )}
    </div>
  );
};
