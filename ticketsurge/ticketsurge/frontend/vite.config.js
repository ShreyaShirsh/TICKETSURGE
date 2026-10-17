import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Django dev server (daphne / runserver) for `npm run dev`. Under `vercel dev`
// these paths are routed to the backend service before reaching Vite.
const backend = process.env.VITE_DEV_BACKEND || 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': backend,
      '/admin': backend,
      '/health': backend,
      '/ws': { target: backend.replace(/^http/, 'ws'), ws: true },
    },
  },
})
