import React from 'react';
import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  Activity,
  ArrowLeftRight,
  Bot,
  SlidersHorizontal,
  Link2,
  BarChart3,
  FlaskConical,
  Settings,
  Leaf,
  LogOut,
  Radio,
} from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';
import { useMicrogridStore } from '@/stores/microgridStore';
import clsx from 'clsx';

interface SidebarProps {
  wsConnected?: boolean;
}

export const Sidebar: React.FC<SidebarProps> = ({ wsConnected = false }) => {
  const { user, logout } = useAuthStore();
  const { currentMicrogrid } = useMicrogridStore();

  const navItems = [
    { label: 'Dashboard', path: '/', icon: LayoutDashboard },
    { label: 'Live Grid', path: '/control-room', icon: Activity },
    { label: 'Market', path: '/market', icon: ArrowLeftRight },
    { label: 'AI Agents', path: '/agents', icon: Bot },
    { label: 'Optimization', path: '/optimization', icon: SlidersHorizontal },
    { label: 'Blockchain', path: '/blockchain', icon: Link2 },
    { label: 'Analytics', path: '/analytics', icon: BarChart3 },
    { label: 'Scenarios', path: '/scenarios', icon: FlaskConical },
    { label: 'Settings', path: '/settings', icon: Settings },
  ];

  return (
    <aside className="w-56 flex-shrink-0 bg-[#112330] border-r border-[#1B3245] flex flex-col justify-between select-none">
      <div>
        {/* Brand Header matching screenshot */}
        <div className="h-16 flex items-center gap-3 px-5 border-b border-[#1B3245]">
          <div className="w-8 h-8 rounded-lg bg-emerald-500/20 border border-emerald-500/40 flex items-center justify-center text-emerald-400">
            <Leaf className="w-4 h-4 fill-emerald-400 text-emerald-400" />
          </div>
          <div>
            <div className="text-base font-bold tracking-tight text-white flex items-center gap-1 font-sans">
              Green<span className="text-emerald-400">Grid</span>
            </div>
            <div className="text-[10px] text-slate-400 font-mono tracking-wider uppercase">
              Microgrid Exchange
            </div>
          </div>
        </div>

        {/* Microgrid Selector Badge */}
        <div className="px-3.5 py-3">
          <div className="px-3 py-2 rounded-lg bg-[#0C1A24] border border-[#1B3245] flex items-center justify-between">
            <div className="overflow-hidden">
              <div className="text-[10px] uppercase font-mono text-slate-400">Active District</div>
              <div className="text-xs font-semibold text-slate-200 truncate">
                {currentMicrogrid ? currentMicrogrid.name : 'GreenGrid District'}
              </div>
            </div>
            <div
              className={clsx(
                'w-2 h-2 rounded-full',
                wsConnected ? 'bg-emerald-400 shadow-[0_0_8px_#10B981]' : 'bg-amber-400'
              )}
              title={wsConnected ? 'Live WebSocket Sync' : 'Reconnecting...'}
            />
          </div>
        </div>

        {/* Navigation Links matching screenshot */}
        <nav className="px-2.5 space-y-1">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.path}
                to={item.path}
                className={({ isActive }) =>
                  clsx(
                    'flex items-center gap-3 px-3.5 py-2.5 rounded-lg text-xs font-medium transition-all duration-150',
                    isActive
                      ? 'bg-blue-600 text-white font-semibold shadow-sm'
                      : 'text-slate-300 hover:text-white hover:bg-[#1A3347]'
                  )
                }
              >
                <Icon className="w-4 h-4 flex-shrink-0" />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </nav>
      </div>

      {/* Footer Info & User */}
      <div className="p-3 border-t border-[#1B3245] space-y-2">
        <div className="flex items-center justify-between px-3 py-2 rounded-lg bg-[#0C1A24] text-xs">
          <div className="flex items-center gap-2 overflow-hidden">
            <div className="w-6 h-6 rounded-full bg-blue-600/30 border border-blue-400/40 flex items-center justify-center text-[10px] font-bold text-blue-300">
              {user?.email?.charAt(0).toUpperCase() || 'OP'}
            </div>
            <div className="truncate text-left">
              <div className="text-[11px] font-medium text-slate-200 truncate">
                {user?.email?.split('@')[0] || 'Operator'}
              </div>
              <div className="text-[10px] font-mono text-emerald-400 uppercase">
                {user?.role || 'GRID_OPERATOR'}
              </div>
            </div>
          </div>
          <button
            onClick={() => logout()}
            title="Log Out"
            className="p-1 text-slate-400 hover:text-rose-400 transition-colors"
          >
            <LogOut className="w-3.5 h-3.5" />
          </button>
        </div>

        <div className="flex items-center justify-between text-[10px] font-mono text-slate-400 px-2">
          <span className="flex items-center gap-1 text-emerald-400">
            <Radio className="w-2.5 h-2.5 animate-pulse" />
            99.98% Live
          </span>
          <span>v2.4.0</span>
        </div>
      </div>
    </aside>
  );
};
