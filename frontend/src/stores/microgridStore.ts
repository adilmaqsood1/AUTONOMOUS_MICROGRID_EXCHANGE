import { create } from 'zustand';
import { microgridApi } from '@/api/endpoints';

interface MicrogridState {
  microgridId: string | null;
  currentMicrogrid: { id: string; name: string } | null;
  microgrids: Array<{ id: string; name: string }>;
  fetchMicrogrids: () => Promise<void>;
  setMicrogridId: (id: string) => void;
}

export const useMicrogridStore = create<MicrogridState>((set, get) => ({
  microgridId: null,
  currentMicrogrid: null,
  microgrids: [],

  fetchMicrogrids: async () => {
    try {
      const list = await microgridApi.list();
      set({ microgrids: list });
      if (list.length > 0) {
        set({ microgridId: list[0].id, currentMicrogrid: list[0] });
      }
    } catch (e) {
      console.error('Error fetching microgrids:', e);
    }
  },

  setMicrogridId: (id: string) => {
    const found = get().microgrids.find((m) => m.id === id) || null;
    set({ microgridId: id, currentMicrogrid: found });
  },
}));
