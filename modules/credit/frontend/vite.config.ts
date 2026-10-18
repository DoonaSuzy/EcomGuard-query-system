import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  // 只暴露 VITE_ 前缀的变量给客户端；其余（如 .env 里的凭据）绝不进 bundle
  const env = loadEnv(mode, process.cwd(), 'VITE_')

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      port: Number(env.VITE_DEV_PORT || 5173),
      strictPort: true,
      proxy: {
        // 开发态把 /api 透传到本地后端，避开 CORS——后端没有配 CORS 中间件，
        // 直接跨源请求浏览器会拦。生产态由后端静态托管同源，不需要这条。
        //
        // 不做 rewrite：后端接口本身就在 /api 前缀下（api.py 的 APIRouter(prefix="/api")），
        // 原样转发才能与生产态契约一致。以前这里剥掉 /api 去凑后端无前缀的 /jobs，
        // 结果 dev 一切正常、生产态整站 404——dev 与 prod 的路径契约必须一致。
        '/api': {
          target: env.VITE_API_TARGET || 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      // 后端 FastAPI 不托管带指纹的资源，关掉 hash 便于排查「哪个文件没更新」
      assetsInlineLimit: 4096,
      chunkSizeWarningLimit: 800,
    },
  }
})
