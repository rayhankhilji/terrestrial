import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The FastAPI backend (uv run uvicorn api.main:app --port 8000) serves /api.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://localhost:8000' },
  },
})
