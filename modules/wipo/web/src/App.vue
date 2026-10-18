<script setup>
import { ref, onMounted } from 'vue'
import { useStore, setCurrentPage } from './store'
import { loadTrademarks, loadReports } from './actions'
import Sidebar from './components/Sidebar.vue'
import InputPage from './components/InputPage.vue'
import ListPage from './components/ListPage.vue'
import CheckPage from './components/CheckPage.vue'
import ReportsPage from './components/ReportsPage.vue'
import ToastContainer from './components/ToastContainer.vue'
import ModalDialog from './components/ModalDialog.vue'

/**
 * 应用外壳：侧边栏 + 顶栏 + 四个页面 + Toast/Modal。
 *
 * 页面切换**不用 vue-router**：四个 section 始终挂载，靠给当前那个加
 * `.active` 类来显示（CSS 里 `.page{display:none}` / `.page.active{display:block}`）。
 * 这是旧版 templates/index.html 的做法，E2E 直接断言 `#page-input.active`
 * 这样的选择器，改成 URL 路由会把 107 项回归整体推翻。
 *
 * 四个页面**不用 KeepAlive / v-if**：用它们会让非当前页的 section 节点被摘掉，
 * `.page.active` 的计数断言仍然成立，但检查页正在跑的轮询会随组件卸载中断，
 * 报告详情也会在切走后丢失。
 */
const { state, pageTitle } = useStore()

const inputPage = ref(null)

// 启动时就把商标与报告都拉一次，对应旧版 init() 里的
// loadTrademarks() / loadReports()。
//
// 报告**必须**在启动时加载，不能只靠 ReportsPage 进入页面时再拉：
// 用例是「写文件 → page.reload() → 点报告页 → 立刻断言 .report-item 计数」，
// 若只在切页时才发起请求，断言会跑在请求返回之前，拿到 0 条。
// 旧版正是靠 init() 里的这一次加载避开了这个竞态。
onMounted(() => {
  loadTrademarks()
  loadReports()
})

/** 清单页点「编辑」：回填表单并切回录入页。 */
function handleEdit(trademark) {
  inputPage.value?.startEdit(trademark)
  setCurrentPage('input')
}
</script>

<template>
  <div class="app">
    <Sidebar />

    <main class="main-content">
      <header class="header">
        <h1 id="page-title">{{ pageTitle }}</h1>
        <div class="header-actions">
          <div class="trademark-count">共 <span id="total-count">{{ state.trademarks.length }}</span> 个商标</div>
        </div>
      </header>

      <InputPage ref="inputPage" />
      <ListPage @edit="handleEdit" />
      <CheckPage />
      <ReportsPage />
    </main>
  </div>

  <ToastContainer />
  <ModalDialog />
</template>
