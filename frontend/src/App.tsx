import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ProtectedRoute } from '@/components/common/ProtectedRoute';
import { AppLayout } from '@/components/layout/AppLayout';
import { LoginPage } from '@/pages/LoginPage';
import { DashboardPage } from '@/pages/DashboardPage';
import { ControlRoomPage } from '@/pages/ControlRoomPage';
import { MarketPage } from '@/pages/MarketPage';
import { AgentsPage } from '@/pages/AgentsPage';
import { OptimizationPage } from '@/pages/OptimizationPage';
import { BlockchainPage } from '@/pages/BlockchainPage';
import { ScenariosPage } from '@/pages/ScenariosPage';
import { AnalyticsPage } from '@/pages/AnalyticsPage';
import { SettingsPage } from '@/pages/SettingsPage';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 3000,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          {/* Public Login Route */}
          <Route path="/login" element={<LoginPage />} />

          {/* Protected Routes: Must be authenticated to access */}
          <Route element={<ProtectedRoute />}>
            <Route element={<AppLayout />}>
              <Route index element={<DashboardPage />} />
              <Route path="control-room" element={<ControlRoomPage />} />
              <Route path="market" element={<MarketPage />} />
              <Route path="agents" element={<AgentsPage />} />
              <Route path="optimization" element={<OptimizationPage />} />
              <Route path="blockchain" element={<BlockchainPage />} />
              <Route path="scenarios" element={<ScenariosPage />} />
              <Route path="analytics" element={<AnalyticsPage />} />
              <Route path="settings" element={<SettingsPage />} />
            </Route>
          </Route>

          {/* Catch-all redirect to index or login */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
