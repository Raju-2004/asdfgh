import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In dev, forward /api to the local uvicorn server.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://localhost:8000' } },
})
