import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 商标监控系统的 Vue 3 前端。
//
// 两个刻意的选择，都与后端挂载方式绑定：
//
// 1. `base: './'` —— 产物里的资源路径写成相对路径，因此 dist/ 既能被
//    FastAPI 挂在 `/` 下（`app/views.py` 的 WEB_DIST），也能直接被
//    file:// 打开或挂在任何子前缀下，不需要重新构建。
// 2. `server.proxy` —— dev server 自己不带数据，把 /api 与 /health 转给
//    本地 uvicorn（默认 8000）。不配这一项时页面能开但所有请求都是 404。
//    注意 changeOrigin 与 rewrite 都不需要：后端不校验 Host，路径原样转发即可。
export default defineConfig({
  plugins: [vue()],
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // 单页应用体积很小，关掉分包警告阈值即可，不需要手工 chunk 切分。
    chunkSizeWarningLimit: 1000,
  },
  server: {
    port: 5199,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
