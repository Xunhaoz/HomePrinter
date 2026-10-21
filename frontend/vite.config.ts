import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In dev, /api goes to the backend (uv run homeprinter) without the prefix,
// the same way nginx serves it in Docker.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    host: true,
    proxy: {
      '/api': {
        target: process.env.API_TARGET ?? 'http://localhost:8000',
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
})
