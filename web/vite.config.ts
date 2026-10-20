import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

/**
 * 统一监控平台前端。
 *
 * 这个工程**不复制**两个业务系统的源码，而是把它们的 src 目录当作「页面包」
 * 用别名引进来：
 *   @       -> modules/credit/frontend/src   （credit 自己的 import 一行都不用改）
 *   @wipo   -> modules/wipo/web/src          （WIPO 全用相对路径，加别名只是方便宿主引用）
 *   @app    -> web/src                       （本工程自己的壳与样式）
 *
 * 为什么 `@` 给了 credit：credit 的 21 个文件里有 17 处 `@/...` 引用，
 * 而 WIPO 的 18 个文件全部用相对路径（实测 grep `from '@/` 零命中）。
 * 把 `@` 让给 credit 意味着 credit 源码零改动，WIPO 也零改动。
 *
 * 代价与约束：两模块仍各自保留独立的 vite 工程与构建产物（可单独部署、
 * WIPO 的 118 项 E2E 与 credit 的前端托管测试都还指着它们），本工程只是
 * 多出来一个消费者，不取代它们。
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')

  return {
    plugins: [vue()],

    resolve: {
      alias: {
        '@': fileURLToPath(new URL('../modules/credit/frontend/src', import.meta.url)),
        '@wipo': fileURLToPath(new URL('../modules/wipo/web/src', import.meta.url)),
        '@app': fileURLToPath(new URL('./src', import.meta.url)),
      },

      /**
       * 必须 dedupe，否则**生产构建会崩**。
       *
       * 三个前端目录各有一份 node_modules，于是各有自己的 vue 3.5.44
       * （版本相同，但模块实例是三个 → 三个独立运行时）：
       *
       *   web/node_modules/vue                     ← 本工程
       *   modules/wipo/web/node_modules/vue        ← WIPO 的组件从这里 import
       *   modules/credit/frontend/node_modules/vue ← credit 的组件从这里 import
       *
       * rollup 按**引用方所在目录**解析裸导入，所以 WIPO 的 .vue 编译出来绑的是
       * 它自己那份 Vue 的内部符号（`_s` = toDisplayString 之类），而应用实例是
       * 本工程这份 —— 两个运行时的内部对象对不上，实测报
       * `TypeError: Cannot read properties of undefined (reading '_s')`，
       * 页面整片空白（`<div id="app"><!----></div>`）。
       *
       * 开发态不会暴露这个问题：Vite 的依赖预打包把 vue 收成一份共享的
       * optimized chunk，所以 `npm run dev` 一切正常 —— 属于「只在构建后炸」
       * 的那类故障，别用 dev 的绿灯去推断生产可用。
       *
       * @vueuse/core 与 pinia 同理（web 与 credit 各一份）。pinia 尤其隐蔽：
       * createPinia 来自一份、defineStore 来自另一份，会在运行时报
       * 「no active Pinia」。
       */
      dedupe: ['vue', 'vue-router', 'pinia', '@vueuse/core'],
    },

    server: {
      port: Number(env.VITE_DEV_PORT || 5174),
      strictPort: true,
      proxy: {
        // 开发态把 /api 透传给本地 uvicorn（aggregator.py，默认 8000）。
        // 不 rewrite：后端接口本身就在 /api 前缀下，原样转发才与生产态一致。
        '/api': {
          target: env.VITE_API_TARGET || 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
        '/health': {
          target: env.VITE_API_TARGET || 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },

    build: {
      outDir: 'dist',
      emptyOutDir: true,
      chunkSizeWarningLimit: 900,
    },
  }
})
