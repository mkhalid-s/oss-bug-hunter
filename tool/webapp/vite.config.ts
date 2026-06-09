import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base=/app/ : FastAPI serves the built app under /app and the assets under
// /app/assets. server.proxy + host:true make `npm run dev` work through the
// VS Code forwarded port (proxying /api to the FastAPI server).
export default defineConfig({
  plugins: [react()],
  base: '/app/',
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    host: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true } },
  },
})
