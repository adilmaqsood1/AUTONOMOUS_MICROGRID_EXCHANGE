import React from 'react';
import { Outlet } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { TopStatusBar } from './TopStatusBar';
import { DemoDirector } from './DemoDirector';
import { useEventStream } from '@/websocket/useEventStream';
import { useQuery } from '@tanstack/react-query';
import { controlRoomApi } from '@/api/endpoints';
import { useMicrogridStore } from '@/stores/microgridStore';

export const AppLayout: React.FC = () => {
  const { isConnected } = useEventStream();
  const { microgridId, fetchMicrogrids } = useMicrogridStore();

  React.useEffect(() => {
    fetchMicrogrids();
  }, [fetchMicrogrids]);

  const { data: telemetry } = useQuery({
    queryKey: ['controlRoom', microgridId],
    queryFn: () => controlRoomApi.getTelemetry(microgridId!),
    enabled: !!microgridId,
    refetchInterval: 5000,
  });

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#F4F6F9] text-slate-900 font-sans antialiased">
      {/* Left Dark Petrol Navigation Sidebar */}
      <Sidebar wsConnected={isConnected} />

      {/* Main Content Area */}
      <div className="flex flex-col flex-1 min-w-0 h-screen overflow-hidden">
        {/* Crisp White Top Bar */}
        <TopStatusBar
          operatingMode={telemetry?.operating_mode || 'GRID_CONNECTED'}
          frequency={telemetry?.system_frequency_hz}
          totalGen={telemetry?.total_generation_kw}
          totalLoad={telemetry?.total_load_kw}
        />

        {/* Scrollable Page Body with Clean Background */}
        <main className="flex-1 overflow-y-auto px-6 py-6 bg-[#F4F6F9]">
          <Outlet />
        </main>
      </div>

      {/* Scenario Demo Director Drawer */}
      <DemoDirector />
    </div>
  );
};
