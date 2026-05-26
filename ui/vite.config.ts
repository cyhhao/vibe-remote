import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      '/config': 'http://localhost:5100',
      '/session': 'http://localhost:5100',
      '/status': 'http://localhost:5100',
      '/settings': 'http://localhost:5100',
      '/logs': 'http://localhost:5100',
      '/doctor': 'http://localhost:5100',
      '/remote-access': 'http://localhost:5100',
      '/control': 'http://localhost:5100',
      '/upgrade': 'http://localhost:5100',
      '/api': 'http://localhost:5100',
    },
  },
})
