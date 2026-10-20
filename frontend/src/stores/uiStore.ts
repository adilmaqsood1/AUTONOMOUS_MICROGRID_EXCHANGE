import { create } from 'zustand';

interface UIState {
  sidebarCollapsed: boolean;
  demoDirectorOpen: boolean;
  toggleSidebar: () => void;
  toggleDemoDirector: () => void;
  setDemoDirectorOpen: (open: boolean) => void;
}

export const useUIStore = create<UIState>((set) => ({
  sidebarCollapsed: false,
  demoDirectorOpen: false,

  toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
  toggleDemoDirector: () => set((state) => ({ demoDirectorOpen: !state.demoDirectorOpen })),
  setDemoDirectorOpen: (open) => set({ demoDirectorOpen: open }),
}));
