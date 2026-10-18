import { reactive, computed } from 'vue'

/**
 * 全局状态：一个 reactive 对象 + 若干操作函数。
 *
 * 刻意**不引 Pinia**（与 PNA 的约定一致：多一个依赖多一份维护成本）。
 * 这个应用的状态就是「商标数组 + 选中集合 + 当前页」三样，
 * 一个 reactive 对象足够，引入状态管理库只会多一层样板。
 *
 * 状态结构与旧版 static/js/app.js 的 `state` 一一对应：
 *   { trademarks, selectedIds, editingId, currentPage }
 * 唯一的区别是 selectedIds 用 Array 而不是 Set——Vue 3 的响应式代理
 * 对 Set 的支持要到 collection handlers，用数组配合 includes 更直白，
 * 且数据量只有几十条，性能上无差别。
 */

const state = reactive({
  trademarks: [],
  selectedIds: [],
  editingId: null,
  currentPage: 'input',
  // 商标搜索关键词（清单页）：旧版是每次渲染时临时读 DOM 的 value，
  // 这里提到 state 里，让清空/过滤都能靠响应式重算。
  searchTerm: '',
  // 检查运行期的共享状态。旧版把它们散在 #check-status-display 的
  // style 与按钮的 innerHTML 里；提出来之后「切换页面再切回来」
  // 才能原样恢复进度条与结果卡片（E2E 断言切回来时结果仍在）。
  checkRunning: false,
  checkResult: null,
  // 报告详情卡。同上：切走再切回要保持已打开的详情。
  activeReport: null,
  reports: [],
})

/** 页面 key -> 顶栏标题。与旧版 switchPage 里的 titles 表一致。 */
const PAGE_TITLES = {
  input: '商标录入',
  list: '商标清单',
  check: '状态检查',
  reports: '检查报告',
}

/**
 * 组件统一从这里取状态。
 *
 * 返回的是**可写的 reactive 对象**而不是 readonly：这批组件都是内部页面，
 * 加 readonly 只会让「表单字段直接写 state」这类写法变得别扭。
 */
export function useStore() {
  return {
    state,
    // 顶栏标题：由当前页 key 推出来，避免在四处地方各写一遍文案
    pageTitle: computed(() => PAGE_TITLES[state.currentPage] || state.currentPage),
    selectedCount: computed(() => state.selectedIds.length),
  }
}

export function setTrademarks(list) {
  state.trademarks = Array.isArray(list) ? list : []
  // 重新加载即视为一次全量刷新：旧版在这里也会 clear()，
  // 否则已删除的 id 会残留在选中集合里，批量删除的计数就对不上。
  state.selectedIds = []
}

/** 报告列表由后端排好序（新的在前），前端不重排。 */
export function setReports(list) {
  state.reports = Array.isArray(list) ? list : []
}

export function setSearchTerm(term) {
  state.searchTerm = term || ''
}

/** 开始/重置一次检查。result 为 null 时代表「还没有结果」。 */
export function setCheckRunning(running) {
  state.checkRunning = running
}

export function setCheckResult(result) {
  state.checkResult = result
}

/** 报告详情：传 null 表示关闭详情卡。 */
export function setActiveReport(report) {
  state.activeReport = report
}

export function setCurrentPage(page) {
  state.currentPage = page
}

export function isSelected(id) {
  return state.selectedIds.includes(id)
}

export function toggleSelected(id, checked) {
  const next = state.selectedIds.filter((x) => x !== id)
  if (checked) next.push(id)
  state.selectedIds = next
}

export function selectAll(checked) {
  state.selectedIds = checked ? state.trademarks.map((tm) => tm.id) : []
}

export function clearSelection() {
  state.selectedIds = []
}

export function setEditingId(id) {
  state.editingId = id
}
