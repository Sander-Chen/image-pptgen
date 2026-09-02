import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5178,
    proxy: {
      '/api': {
        target: 'http://localhost:3104',
        changeOrigin: true,
      },
      '/artifacts': {
        target: 'http://localhost:3104',
        changeOrigin: true,
      },
    },
  },
})
