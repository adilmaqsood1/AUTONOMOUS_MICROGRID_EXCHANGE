import react from '@vitejs/plugin-react'
import path from 'path'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/auth': 'http://127.0.0.1:8000',
      '/microgrids': 'http://127.0.0.1:8000',
      '/participants': 'http://127.0.0.1:8000',
      '/assets': 'http://127.0.0.1:8000',
      '/grid': 'http://127.0.0.1:8000',
      '/meters': 'http://127.0.0.1:8000',
      '/forecasts': 'http://127.0.0.1:8000',
      '/orders': 'http://127.0.0.1:8000',
      '/market': 'http://127.0.0.1:8000',
      '/optimization': 'http://127.0.0.1:8000',
      '/risk': 'http://127.0.0.1:8000',
      '/balancing': 'http://127.0.0.1:8000',
      '/settlement': 'http://127.0.0.1:8000',
      '/provenance': 'http://127.0.0.1:8000',
      '/resilience': 'http://127.0.0.1:8000',
      '/audit': 'http://127.0.0.1:8000',
      '/analytics': 'http://127.0.0.1:8000',
      '/surveillance': 'http://127.0.0.1:8000',
      '/simulation': 'http://127.0.0.1:8000',
      '/blockchain': 'http://127.0.0.1:8000',
      '/wallets': 'http://127.0.0.1:8000',
      '/agents': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
      },
    },
  },
})
