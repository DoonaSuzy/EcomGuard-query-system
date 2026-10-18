<script setup lang="ts">
/**
 * Toast 容器：常驻全局（挂在 App 根），由 useToastStore 驱动
 */
import { useToastStore } from '@/stores/toast'
const toast = useToastStore()
</script>

<template>
  <div class="toasts" aria-live="polite" aria-atomic="false">
    <div v-for="t in toast.items" :key="t.id" class="toast" :class="t.kind" role="status">
      <span class="tbar" />
      <div class="tbody">{{ t.text }}</div>
      <button type="button" :aria-label="'关闭提示'" @click="toast.dismiss(t.id)">✕</button>
    </div>
  </div>
</template>
