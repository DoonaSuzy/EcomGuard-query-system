<script setup>
import { toasts, dismissToast } from '../toast'

/**
 * Toast 容器。
 *
 * `#toast-container` 必须常驻 DOM（用例直接拿它当父容器做断言），里面
 * 的子元素由 `toasts` 队列驱动，队列空了容器还在。
 *
 * 四种图标是旧版 app.js 里那张 icons 表的内联版本，四种 type 各一。
 */
const ICONS = {
  success:
    '<path d="M22 11.08V12a10 10 0 11-5.93-9.14" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><polyline points="22 4 12 14.01 9 11.01" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
  error:
    '<circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2"/><line x1="15" y1="9" x2="9" y2="15" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><line x1="9" y1="9" x2="15" y2="15" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  warning:
    '<path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><line x1="12" y1="9" x2="12" y2="13" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><line x1="12" y1="17" x2="12.01" y2="17" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  info: '<circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2"/><line x1="12" y1="16" x2="12" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><line x1="12" y1="8" x2="12.01" y2="8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
}
</script>

<template>
  <div class="toast-container" id="toast-container">
    <div class="toast" :class="[toast.type, { hiding: toast.hiding }]" v-for="toast in toasts" :key="toast.id">
      <div class="toast-icon">
        <svg
          viewBox="0 0 24 24"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
          width="20"
          height="20"
          v-html="ICONS[toast.type] || ICONS.info"
        ></svg>
      </div>
      <div>
        <div style="font-weight: 500; margin-bottom: 2px">{{ toast.title }}</div>
        <div style="font-size: 13px">{{ toast.message }}</div>
      </div>
      <button class="toast-close" @click="dismissToast(toast.id)">×</button>
    </div>
  </div>
</template>
