<script setup lang="ts">
/**
 * 全局错误边界
 * ============
 * 两个层次，缺一不可：
 *  1. errorCaptured —— 捕获本组件及后代在渲染 / 生命周期 / 事件回调里抛出的同步异常。
 *     没有它，一个组件写错会让整棵树卸载、用户看到白屏且无从反馈。
 *  2. window 'error' / 'unhandledrejection' —— 捕获 errorCaptured 管不到的地方：
 *     异步回调、setTimeout、事件监听器里逃逸的异常，以及 Promise 拒绝。
 *
 * 刻意不做「吞掉继续跑」：捕获后渲染一个带重试按钮的兜底界面。
 * 因为状态可能已经半污染，静默恢复比明确报错更难排查。
 */
import { onErrorCaptured, onMounted, onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const err = ref<{ message: string; stack?: string } | null>(null)
const showDetail = ref(false)

onErrorCaptured((e: unknown, _instance, info) => {
  const message = e instanceof Error ? e.message : String(e)
  err.value = { message, stack: e instanceof Error ? e.stack : undefined }
  // 保留现场便于反馈，但不影响页面其它部分交互
  console.error('[ErrorBoundary]', info, e)
  return false // 阻止继续向上冒泡到 window.onerror（避免重复上报）
})

function onWindowError(event: ErrorEvent) {
  if (event.error instanceof Error) {
    err.value = { message: event.error.message, stack: event.error.stack }
  } else {
    err.value = { message: event.message || '未知脚本错误' }
  }
}

function onRejection(event: PromiseRejectionEvent) {
  const reason = event.reason
  err.value = {
    message: reason instanceof Error ? reason.message : String(reason ?? '未处理的 Promise 拒绝'),
    stack: reason instanceof Error ? reason.stack : undefined,
  }
}

onMounted(() => {
  window.addEventListener('error', onWindowError)
  window.addEventListener('unhandledrejection', onRejection)
})
onBeforeUnmount(() => {
  window.removeEventListener('error', onWindowError)
  window.removeEventListener('unhandledrejection', onRejection)
})

function reload() {
  err.value = null
  showDetail.value = false
  // 用 router.go 而不是 location.reload()：SPA 下前者保留 History，后者整页重载
  router.go(0)
}

function goHome() {
  err.value = null
  showDetail.value = false
  router.push('/overview')
}
</script>

<template>
  <div v-if="err" class="boundary" role="alert">
    <div class="card boundary-box">
      <h2>页面出错了</h2>
      <p class="boundary-msg">{{ err.message }}</p>
      <p class="boundary-tip">可以重试或返回总览；若反复出现，请把下面的错误信息反馈给维护人。</p>
      <div class="boundary-actions">
        <button class="btn primary" @click="reload">重试</button>
        <button class="btn" @click="goHome">返回总览</button>
        <button class="btn sm" @click="showDetail = !showDetail">
          {{ showDetail ? '隐藏' : '查看' }}技术细节
        </button>
      </div>
      <pre v-if="showDetail" class="boundary-detail mono">{{ err.stack || err.message }}</pre>
    </div>
  </div>
  <slot v-else />
</template>

<style scoped>
.boundary { padding: 24px; max-width: 720px; margin: 0 auto; }
.boundary-box { margin-top: 40px; }
.boundary-msg { color: var(--c-alert); font-weight: 500; margin: 10px 0 4px; }
.boundary-tip { color: var(--c-text-dim); margin: 0 0 14px; }
.boundary-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.boundary-detail { margin-top: 14px; padding: 10px; background: var(--c-surface-2); border-radius: var(--radius-sm); overflow: auto; max-height: 260px; white-space: pre-wrap; }
</style>
