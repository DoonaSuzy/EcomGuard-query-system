<script setup>
import { useStore, setCurrentPage } from '../store'

/**
 * 侧边栏：品牌标识 + 4 个导航项 + 版本号。
 *
 * 导航项保持 `<a href="#" data-page="...">` 的形态——E2E 用
 * `.nav-item[data-page="list"]` 定位并点击，改成 button 或 div 都会让
 * 选择器落空。点击时 preventDefault，避免 `href="#"` 往地址栏塞一个
 * 井号（这不会刷新页面，但会让 URL 变脏）。
 */
const { state } = useStore()

const NAV_ITEMS = [
  {
    page: 'input',
    label: '商标录入',
    path: 'M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z',
  },
  {
    page: 'list',
    label: '商标清单',
    path: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
  },
  {
    page: 'check',
    label: '状态检查',
    path: 'M9 11l3 3L22 4M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11',
  },
  {
    page: 'reports',
    label: '检查报告',
    path: 'M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8',
  },
]

function go(page) {
  setCurrentPage(page)
}
</script>

<template>
  <aside class="sidebar">
    <div class="logo">
      <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path
          d="M12 2L2 7v10l10 5 10-5V7L12 2z"
          stroke="currentColor"
          stroke-width="2"
          stroke-linejoin="round"
        />
        <path d="M12 22V12" stroke="currentColor" stroke-width="2" />
        <path d="M2 7l10 5 10-5" stroke="currentColor" stroke-width="2" />
      </svg>
      <span>商标监控</span>
    </div>
    <nav class="nav">
      <a
        v-for="item in NAV_ITEMS"
        :key="item.page"
        href="#"
        class="nav-item"
        :class="{ active: state.currentPage === item.page }"
        :data-page="item.page"
        @click.prevent="go(item.page)"
      >
        <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path
            :d="item.path"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
        <span>{{ item.label }}</span>
      </a>
    </nav>
    <div class="sidebar-footer">
      <div class="version">v1.0.0</div>
    </div>
  </aside>
</template>
