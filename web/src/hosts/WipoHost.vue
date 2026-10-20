<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { setCurrentPage } from '@wipo/store.js'
import { loadReports, loadTrademarks } from '@wipo/actions.js'
import InputPage from '@wipo/components/InputPage.vue'
import ListPage from '@wipo/components/ListPage.vue'
import CheckPage from '@wipo/components/CheckPage.vue'
import ReportsPage from '@wipo/components/ReportsPage.vue'

/**
 * WIPO 商标监控系统的宿主。
 *
 * 这里刻意**只做三件事**：常驻挂载四个页面、把路由同步进 WIPO 自己的
 * state.currentPage、在启动时拉一次数据。页面内部的逻辑、样式、显隐
 * 机制全部沿用 WIPO 原实现，一行没改。
 *
 * 为什么四个页面必须常驻（不用 v-if / 路由渲染）：
 * 见 modules/wipo/web/src/App.vue:16-24 —— 卸载会中断检查页正在跑的
 * 轮询，报告详情也会在切走时丢失；WIPO 自己的 118 项 E2E 就是按
 * `#page-input.active` 这类选择器断言的，改渲染方式等于推翻那套回归。
 */
const route = useRoute()
const router = useRouter()

const inputPage = ref<{ startEdit?: (tm: unknown) => void } | null>(null)

/** 路由 -> WIPO 状态。immediate 保证首次进入就落到正确的页面。 */
watch(
  () => route.name,
  (name) => {
    if (typeof name === 'string' && ['input', 'list', 'check', 'reports'].includes(name)) {
      setCurrentPage(name)
    }
  },
  { immediate: true },
)

/**
 * 启动就把商标与报告都拉一次。
 *
 * 报告**必须**在这里加载，不能等 ReportsPage 进入页面时再拉：WIPO 的
 * E2E 有一条「写文件 → reload → 点报告页 → 立刻断言 .report-item 计数」，
 * 只在切页时才请求会让断言跑在响应返回之前，拿到 0 条。
 */
onMounted(() => {
  loadTrademarks()
  loadReports()
})

/** 清单页点「编辑」：回填表单并切到录入页（路由跟着走，刷新后仍停在录入页）。 */
function handleEdit(trademark: unknown) {
  inputPage.value?.startEdit?.(trademark)
  void router.push('/input')
}
</script>

<template>
  <div class="wipo-host">
    <InputPage ref="inputPage" />
    <ListPage @edit="handleEdit" />
    <CheckPage />
    <ReportsPage />
  </div>
</template>
