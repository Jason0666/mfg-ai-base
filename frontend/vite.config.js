import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 后端默认 http://localhost:8000；容器内通过 VITE_API_BASE 覆盖
// API_PROXY_TARGET 仅改 dev 代理目标（不进入浏览器 bundle，用于多实例联调）
const apiBase = process.env.API_PROXY_TARGET || 'http://localhost:8000'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: apiBase,
        changeOrigin: true,
      },
    },
  },
})
