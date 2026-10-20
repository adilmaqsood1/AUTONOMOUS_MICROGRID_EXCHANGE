import React from 'react';
import clsx from 'clsx';

interface StatusBadgeProps {
  status: string;
  variant?: 'green' | 'amber' | 'red' | 'blue' | 'purple' | 'slate';
  pulse?: boolean;
  className?: string;
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({
  status,
  variant = 'green',
  pulse = false,
  className,
}) => {
  const variantStyles = {
    green: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    amber: 'bg-amber-50 text-amber-700 border-amber-200',
    red: 'bg-rose-50 text-rose-700 border-rose-200',
    blue: 'bg-blue-50 text-blue-700 border-blue-200',
    purple: 'bg-purple-50 text-purple-700 border-purple-200',
    slate: 'bg-slate-100 text-slate-700 border-slate-200',
  };

  const dotStyles = {
    green: 'bg-emerald-500',
    amber: 'bg-amber-500',
    red: 'bg-rose-500',
    blue: 'bg-blue-500',
    purple: 'bg-purple-500',
    slate: 'bg-slate-500',
  };

  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold tracking-wide border',
        variantStyles[variant],
        className
      )}
    >
      <span
        className={clsx(
          'w-1.5 h-1.5 rounded-full',
          dotStyles[variant],
          pulse && 'animate-ping'
        )}
      />
      {status}
    </span>
  );
};
