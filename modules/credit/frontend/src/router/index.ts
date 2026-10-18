import { createRouter, createWebHistory } from 'vue-router'

/**
 * 路由 base 的取值顺序：
 *   1. `window.__CREDIT_BASE__` —— 被聚合系统挂到子路径（如 `/credit/`）时由宿主注入
 *   2. `import.meta.env.BASE_URL` —— 独立构建/运行时
 *
 * 为什么需要第一条：本模块单独跑时部署在站点根，`base` 恒为 `/`；被聚合系统
 * 托管到 `/credit/` 时，构建期常量已经固化无法改变，若仍用 `/` 则
 * `router.push('/overview')` 会跳到 `/overview`，脱离挂载点直接 404。
 * 宿主在返回 index.html 时注入这个全局变量，模块源码无需为两种部署各构建一次。
 *
 * 构建时用 `--base=./`（相对），这样静态资源在子路径与站点根下都能正确解析；
 * 相对形式的 BASE_URL 不能直接交给 vue-router，`./` 归一成 `/` 即可。
 */
declare global {
  interface Window {
    __CREDIT_BASE__?: string
  }
}

const ENV_BASE: string = import.meta.env.BASE_URL
const ROUTER_BASE: string =
  window.__CREDIT_BASE__ ?? (ENV_BASE.startsWith('/') ? ENV_BASE : '/')

export const routes = [
  { path: '/', redirect: '/overview' },
  { path: '/overview', name: 'overview', component: () => import('@/views/OverviewView.vue'), meta: { title: '总览' } },
  { path: '/submit', name: 'submit', component: () => import('@/views/SubmitView.vue'), meta: { title: '提交巡检' } },
  { path: '/jobs', name: 'jobs', component: () => import('@/views/JobsView.vue'), meta: { title: '作业列表' } },
  { path: '/jobs/:jobId', name: 'job-detail', component: () => import('@/views/JobDetailView.vue'), meta: { title: '作业详情' }, props: true },
  // 兜底：输错路径时给一条明确提示，而不是白屏
  { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('@/views/NotFoundView.vue'), meta: { title: '页面不存在' } },
]

export const router = createRouter({
  history: createWebHistory(ROUTER_BASE),
  routes,
  scrollBehavior: (_to, _from, saved) => saved ?? { top: 0 },
})

router.afterEach((to) => {
  const base = '营业执照状态巡检'
  document.title = to.meta.title ? `${base} · ${to.meta.title}` : base
})
