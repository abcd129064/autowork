import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: '/',
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://49.235.34.253:8080', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', chunkSizeWarningLimit: 1200 },
})
