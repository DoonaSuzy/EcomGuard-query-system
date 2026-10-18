<script setup lang="ts">
/**
 * 健康指示灯
 * ==========
 * 轮询 /health：ok → 绿灯；degraded（DB 不可用）→ 黄灯；请求失败（后端没起）→ 红灯。
 * 用 useIntervalFn 而不是 setInterval：切走页面时 pause，回来 resume，避免后台空转。
 */
import { computed } from 'vue'
import { useIntervalFn } from '@vueuse/core'
import { useHealthStore } from '@/stores/jobs'

const health = useHealthStore()

const tone = computed(() => {
  if (health.offline) return 'alert'
  if (!health.data) return 'muted'
  return health.data.db && health.data.status === 'ok' ? 'ok' : 'warn'
})

const text = computed(() => {
  if (health.offline) return '后端不可达'
  if (!health.data) return '检测中…'
  if (!health.data.db) return '数据库异常'
  if (health.data.status === 'ok') return '服务正常'
  return '服务降级'
})

const title = computed(() => {
  if (health.offline) return `无法连接后端：${health.lastError}`
  if (!health.data) return '正在检测后端状态'
  return `排队中 ${health.data.queued} 个作业 · 最近更新 ${health.data.time}`
})

useIntervalFn(() => void health.refresh(), 15000, { immediate: true, immediateCallback: true })
</script>

<template>
  <span class="hbadge" :class="tone" :title="title">
    <i class="dot" />
    <span class="txt">{{ text }}</span>
  </span>
</template>

<style scoped>
.hbadge { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; font-weight: 500; padding: 3px 10px; border-radius: 999px; border: 1px solid var(--c-border); background: var(--c-surface); }
.hbadge .dot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
.hbadge.ok { color: var(--c-ok); }
.hbadge.ok .dot { background: var(--c-ok); }
.hbadge.warn { color: var(--c-warn); }
.hbadge.warn .dot { background: var(--c-warn); }
.hbadge.alert { color: var(--c-alert); }
.hbadge.alert .dot { background: var(--c-alert); }
.hbadge.muted { color: var(--c-text-dim); }
.hbadge.muted .dot { background: var(--c-text-dim); }
</style>
