import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Inside docker-compose the API is reachable as http://backend:8000.
// Running `npm run dev` on your own machine? Set VITE_API_TARGET=http://localhost:8000
const target = process.env.VITE_API_TARGET || 'http://backend:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    proxy: {
      // The browser calls /api/shipments, the backend serves /shipments.
      '/api': { target, changeOrigin: true, rewrite: (path) => path.replace(/^\/api/, '') },
      '/health': target,
    },
  },
})
