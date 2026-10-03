import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// /api: FastAPI over the processed pipeline output (make api, :8000)
// /live: the live service, REST + WebSocket (make live, :8001)
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/live': { target: 'http://localhost:8001', ws: true },
    },
  },
})
