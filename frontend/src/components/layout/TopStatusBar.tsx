import React from 'react';
import {
  Search,
  Bell,
  User as UserIcon,
  Play,
  Zap,
} from 'lucide-react';
import { StatusBadge } from '@/components/common/StatusBadge';
import { useMicrogridStore } from '@/stores/microgridStore';
import { useAuthStore } from '@/stores/authStore';
import { useUIStore } from '@/stores/uiStore';

interface TopStatusBarProps {
  operatingMode?: 'GRID_CONNECTED' | 'ISLANDED' | 'EMERGENCY' | 'RECOVERY';
  frequency?: number;
  totalGen?: number;
  totalLoad?: number;
}

export const TopStatusBar: React.FC<TopStatusBarProps> = ({
  operatingMode = 'GRID_CONNECTED',
  frequency,
  totalGen,
  totalLoad,
}) => {
  const { currentMicrogrid } = useMicrogridStore();
  const { user } = useAuthStore();
  const { toggleDemoDirector } = useUIStore();

  const getModeVariant = (mode: string) => {
    switch (mode) {
      case 'GRID_CONNECTED':
        return 'green';
      case 'ISLANDED':
        return 'amber';
      case 'EMERGENCY':
        return 'red';
      default:
        return 'blue';
    }
  };

  return (
    <header className="h-16 bg-white border-b border-slate-200 px-6 flex items-center justify-between select-none z-20 shadow-xs">
      {/* Left: District name & Mode badge matching screenshot */}
      <div className="flex items-center gap-4">
        <div>
          <span className="text-base font-bold text-slate-900 tracking-tight">
            {currentMicrogrid ? currentMicrogrid.name : 'GreenGrid District'}
          </span>
        </div>

        <StatusBadge
          status={operatingMode.replace('_', ' ')}
          variant={getModeVariant(operatingMode)}
          pulse={operatingMode === 'ISLANDED' || operatingMode === 'EMERGENCY'}
        />

        {/* Live Grid Quick Telemetry */}
        <div className="hidden lg:flex items-center gap-3 pl-4 border-l border-slate-200 text-xs font-mono">
          <div className="flex items-center gap-1.5 text-slate-700">
            <Zap className="w-3.5 h-3.5 text-amber-500" />
            <span className="text-slate-400">Freq:</span>
            <span className="text-emerald-600 font-bold">
              {frequency !== undefined ? `${frequency.toFixed(2)} Hz` : '--'}
            </span>
          </div>

          <div className="flex items-center gap-1.5 text-slate-700">
            <span className="text-slate-400">Gen:</span>
            <span className="text-slate-900 font-bold">
              {totalGen !== undefined ? `${Math.round(totalGen).toLocaleString()} kW` : '--'}
            </span>
          </div>

          <div className="flex items-center gap-1.5 text-slate-700">
            <span className="text-slate-400">Load:</span>
            <span className="text-slate-900 font-bold">
              {totalLoad !== undefined ? `${Math.round(totalLoad).toLocaleString()} kW` : '--'}
            </span>
          </div>
        </div>
      </div>

      {/* Middle: Global Search */}
      <div className="hidden md:flex items-center relative w-72 max-w-sm">
        <Search className="w-4 h-4 text-slate-400 absolute left-3 pointer-events-none" />
        <input
          type="text"
          placeholder="Search..."
          className="w-full pl-9 pr-4 py-1.5 rounded-lg bg-slate-100 border border-slate-200 text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:border-blue-500 focus:bg-white transition-colors"
        />
      </div>

      {/* Right: Actions & User */}
      <div className="flex items-center gap-3">
        {/* Scenario / Demo Director Trigger Button */}
        <button
          onClick={toggleDemoDirector}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-50 hover:bg-blue-100 text-blue-700 border border-blue-200 text-xs font-semibold transition-all shadow-xs"
          title="Open Scenario Simulation Bar"
        >
          <Play className="w-3.5 h-3.5 fill-blue-600 text-blue-600" />
          <span>Demo Lab</span>
        </button>

        {/* Alerts Bell */}
        <button
          className="relative p-2 rounded-lg text-slate-500 hover:text-slate-700 hover:bg-slate-100 transition-colors"
          title="Surveillance Notifications"
        >
          <Bell className="w-4 h-4" />
          <span className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-amber-500" />
        </button>

        {/* User Pill */}
        <div className="flex items-center gap-2.5 pl-2 border-l border-slate-200">
          <div className="w-8 h-8 rounded-full bg-slate-100 border border-slate-300 flex items-center justify-center text-slate-700 text-xs font-bold">
            <UserIcon className="w-4 h-4 text-slate-600" />
          </div>
          <div className="hidden xl:block text-left text-xs">
            <div className="font-semibold text-slate-900">{user?.email?.split('@')[0] || 'Operator'}</div>
            <div className="text-[10px] text-slate-500 font-medium">{user?.role || 'Grid Operator'}</div>
          </div>
        </div>
      </div>
    </header>
  );
};
