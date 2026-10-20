import { create } from 'zustand';
import { User } from '@/types';
import { authApi } from '@/api/endpoints';

interface AuthState {
  token: string | null;
  user: User | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  fetchUser: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  token: localStorage.getItem('mgx_token'),
  user: null,
  isLoading: false,

  login: async (email: string, password: string) => {
    set({ isLoading: true });
    try {
      const res = await authApi.login(email, password);
      localStorage.setItem('mgx_token', res.access_token);
      set({ token: res.access_token });
      await get().fetchUser();
      // Dynamically load active microgrids
      const { useMicrogridStore } = await import('@/stores/microgridStore');
      await useMicrogridStore.getState().fetchMicrogrids();
    } finally {
      set({ isLoading: false });
    }
  },

  logout: () => {
    localStorage.removeItem('mgx_token');
    set({ token: null, user: null });
  },

  fetchUser: async () => {
    try {
      const user = await authApi.getMe();
      set({ user });
    } catch {
      get().logout();
    }
  },
}));
