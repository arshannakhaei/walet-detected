import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the dashboard runs on :5173 and forwards API calls to the
// Python server on :8765. In production FastAPI serves the built files itself.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: { '/api': 'http://127.0.0.1:8765' },
  },
  build: {
    chunkSizeWarningLimit: 1500,
  },
})
