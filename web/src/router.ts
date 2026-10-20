import { createRouter, createWebHistory } from 'vue-router'
import { titleOf } from './nav'

/**
 * 统一路由表。
 *
 * 两个系统的页面路径天然不冲突，因此可以放进同一张表：
 *   WIPO   : /input /list /check /reports
 *   credit : /overview /submit /schedules /jobs /jobs/:jobId
 * 这是「credit 的视图代码一行都不用改」的前提——它内部那些
 * `router.push('/overview')`、`<router-link to="/jobs/xxx">`、
 * `useRoute().params.jobId` 全部照旧可用。
 *
 * WIPO 的四个页面**不由路由渲染**：它们必须常驻挂载（切走再切回要保留
 * 检查页的轮询与报告详情，见 modules/wipo/web/src/App.vue:16-19 的注释），
 * 所以这里给它们挂一个空组件占位，真正的渲染在 hosts/WipoHost.vue 里，
 * 路由只负责把「当前是哪一页」同步进 WIPO 自己的 state.currentPage。
 */
const BlankView = { name: 'BlankView', render: () => null }

export const routes = [
  { path: '/', redirect: '/input' },

  // ---- WIPO 商标监控（占位，实体由 WipoHost 常驻渲染）----
  { path: '/input', name: 'input', component: BlankView },
  { path: '/list', name: 'list', component: BlankView },
  { path: '/check', name: 'check', component: BlankView },
  { path: '/reports', name: 'reports', component: BlankView },

  // ---- 营业执照状态巡检（实体视图，路径与 credit 原工程一致）----
  { path: '/overview', name: 'overview', component: () => import('@/views/OverviewView.vue') },
  { path: '/submit', name: 'submit', component: () => import('@/views/SubmitView.vue') },
  { path: '/schedules', name: 'schedules', component: () => import('@/views/SchedulesView.vue') },
  { path: '/jobs', name: 'jobs', component: () => import('@/views/JobsView.vue') },
  {
    path: '/jobs/:jobId',
    name: 'job-detail',
    component: () => import('@/views/JobDetailView.vue'),
    props: true,
  },

  { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('@/views/NotFoundView.vue') },
]

export const router = createRouter({
  history: createWebHistory('/'),
  routes,
  scrollBehavior: (_to, _from, saved) => saved ?? { top: 0 },
})

router.afterEach((to) => {
  const t = titleOf(typeof to.name === 'string' ? to.name : null)
  document.title = t ? `统一监控平台 · ${t}` : '统一监控平台'
})
