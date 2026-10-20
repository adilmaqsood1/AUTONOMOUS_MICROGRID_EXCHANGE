import React from 'react';
import clsx from 'clsx';
import { ArrowUpRight, ArrowDownRight, Minus } from 'lucide-react';

interface MetricCardProps {
  label: string;
  value: string | number;
  unit?: string;
  change?: string;
  trend?: 'up' | 'down' | 'neutral';
  subtext?: string;
  icon?: React.ReactNode;
  variant?: 'default' | 'highlight' | 'critical';
  className?: string;
}

export const MetricCard: React.FC<MetricCardProps> = ({
  label,
  value,
  unit,
  change,
  trend,
  subtext,
  icon,
  variant = 'default',
  className,
}) => {
  return (
    <div
      className={clsx(
        'bg-white border border-slate-200 rounded-xl p-4 flex flex-col justify-between shadow-sm transition-all duration-150 hover:shadow-md hover:border-slate-300',
        variant === 'highlight' && 'border-blue-300 bg-blue-50/20',
        variant === 'critical' && 'border-rose-300 bg-rose-50/20',
        className
      )}
    >
      <div className="flex items-center justify-between text-xs text-slate-500 font-medium">
        <span>{label}</span>
        {icon && <span className="text-slate-400">{icon}</span>}
      </div>

      <div className="mt-2 flex items-baseline gap-1.5">
        <span className="text-2xl font-bold font-mono tracking-tight text-slate-900">
          {value}
        </span>
        {unit && <span className="text-xs font-mono text-slate-500">{unit}</span>}
      </div>

      {(change || subtext) && (
        <div className="mt-2.5 flex items-center justify-between text-xs">
          {change && (
            <div
              className={clsx(
                'inline-flex items-center gap-0.5 font-semibold font-mono px-1.5 py-0.5 rounded text-[11px]',
                trend === 'up' && 'text-emerald-700 bg-emerald-50 border border-emerald-200/60',
                trend === 'down' && 'text-emerald-700 bg-emerald-50 border border-emerald-200/60',
                trend === 'neutral' && 'text-slate-600 bg-slate-100 border border-slate-200'
              )}
            >
              {trend === 'up' && <ArrowUpRight className="w-3 h-3 text-emerald-600" />}
              {trend === 'down' && <ArrowDownRight className="w-3 h-3 text-emerald-600" />}
              {trend === 'neutral' && <Minus className="w-3 h-3 text-slate-500" />}
              {change}
            </div>
          )}
          {subtext && <span className="text-slate-500 text-[11px] truncate font-medium">{subtext}</span>}
        </div>
      )}
    </div>
  );
};
