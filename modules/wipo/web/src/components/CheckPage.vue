<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { startCheck, fetchCheckStatus } from '../api'
import { useStore, setCurrentPage, setCheckRunning, setCheckResult } from '../store'
import { errorCount } from '../utils'
import { showToast } from '../toast'
import AlertItem from './AlertItem.vue'

/**
 * 状态检查页：勾选 → 启动检查 → 轮询 → 渲染结果。
 *
 * 两个地方沿用了旧版看起来别扭、但被测试依赖的行为：
 *
 * 1. **轮询用 setInterval 且切换页面不取消**。用例
 *    `test_switch_page_during_check_keeps_state` 会在检查进行中切到清单页、
 *    再切回来，断言结果卡片仍在。因为四个 section 始终挂载（只是切换
 *    `.active`），本组件不会卸载，interval 自然活着。
 * 2. **进入本页也会启动一次轮询**（旧的 `loadCheckStatus` 就是这么干的）。
 *    它负责这种情况：刷新页面后后端其实已经跑完了检查，
 *    靠这一次轮询把结果拉回来。用 pollTimer 判重，避免叠加多个 interval。
 *
 * POST 体只带 `checkStatus` / `checkSquatting`——与旧版 app.js 一致。
 * 后端 `CheckRequest` 虽然还接受 `trademarkIds`（别名 ids），但旧前端从未
 * 传过，语义是「检查全部商标」，这里保持不变。
 */
const { state } = useStore()

const POLL_INTERVAL_MS = 2000

const checkStatus = ref(true)
const checkSquatting = ref(true)
const pollTimer = ref(null)

const running = computed(() => state.checkRunning)
const alerts = computed(() => (state.checkResult && state.checkResult.alerts) || [])

const summary = computed(() => {
  const result = state.checkResult || {}
  const list = result.alerts || []
  return [
    { key: 'total', cls: 'success', label: '检查总数', value: result.total || 0 },
    { key: 'alerts', cls: 'warning', label: '告警数', value: list.length },
    {
      key: 'issues',
      cls: 'danger',
      label: '异常数',
      value: list.filter((a) => a.issues && a.issues.length > 0).length,
    },
    { key: 'errors', cls: '', label: '错误数', value: errorCount(result.errors) },
  ]
})

function stopPolling() {
  if (pollTimer.value !== null) {
    clearInterval(pollTimer.value)
    pollTimer.value = null
  }
}

async function tick() {
  try {
    const data = await fetchCheckStatus()
    if (data.status === 'completed') {
      stopPolling()
      setCheckResult(data.result || null)
      setCheckRunning(false)
    } else if (data.status === 'error') {
      stopPolling()
      showToast('检查失败', data.error || '未知错误', 'error')
      setCheckRunning(false)
    }
    // 'running' / 'idle' 什么都不做：继续等下一轮。
    // idle 表示后端还没跑过检查，旧版把它当 running 会让转圈永远停不下来。
  } catch {
    stopPolling()
    showToast('请求失败', '请检查网络连接', 'error')
    setCheckRunning(false)
  }
}

function startPolling() {
  if (pollTimer.value !== null) return
  pollTimer.value = setInterval(tick, POLL_INTERVAL_MS)
  // 立刻打一次，省掉第一个 2 秒的空白
  tick()
}

async function handleStartCheck() {
  if (state.trademarks.length === 0) {
    showToast('提示', '请先添加商标', 'warning')
    setCurrentPage('input')
    return
  }

  setCheckRunning(true)
  setCheckResult(null)

  try {
    const result = await startCheck({
      checkStatus: checkStatus.value,
      checkSquatting: checkSquatting.value,
    })
    if (result.success) {
      showToast('检查已启动', '结果将在稍后显示', 'info')
      startPolling()
    } else {
      showToast('启动失败', result.error || '未知错误', 'error')
      setCheckRunning(false)
    }
  } catch {
    showToast('请求失败', '请检查网络连接', 'error')
    setCheckRunning(false)
  }
}

// 进入检查页时补一轮轮询：覆盖「刷新后后端其实已经跑完」的情况。
watch(
  () => state.currentPage,
  (page) => {
    if (page === 'check') startPolling()
  },
  { immediate: true },
)

onBeforeUnmount(stopPolling)
</script>

<template>
  <section id="page-check" class="page" :class="{ active: state.currentPage === 'check' }">
    <div class="card">
      <div class="card-header">
        <h2>商标状态检查</h2>
        <p class="subtitle">调用 WIPO BrandDB API 检查商标状态和疑似抢注情况</p>
      </div>
      <div class="check-options">
        <label class="checkbox-item">
          <input type="checkbox" id="check-status" v-model="checkStatus" />
          <span>检查商标状态</span>
        </label>
        <label class="checkbox-item">
          <input type="checkbox" id="check-squatting" v-model="checkSquatting" />
          <span>检查疑似抢注</span>
        </label>
      </div>
      <div class="check-actions">
        <button
          class="btn btn-primary btn-lg"
          id="btn-start-check"
          :disabled="running"
          @click="handleStartCheck"
        >
          <!-- 运行中换成纯文案书签占位：旧版是用 innerHTML 整体替换的，
               这里用 v-if 达到同样的效果（按钮 recover 后必须重新出现
               「开始检查」字样，测试断言的就是 inner_text）。 -->
          <template v-if="!running">
            <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <polygon points="5,3 19,12 5,21" fill="currentColor" />
            </svg>
            <span>开始检查</span>
          </template>
          <span v-else>检查中...</span>
        </button>
      </div>
      <div class="check-status" id="check-status-display" v-show="running">
        <div class="spinner"></div>
        <span id="check-status-text">检查进行中，请稍候...</span>
      </div>
    </div>

    <div class="card" id="check-result-card" v-if="state.checkResult">
      <div class="card-header">
        <h2>检查结果</h2>
      </div>
      <div class="summary-cards" id="summary-cards">
        <div class="summary-card" :class="item.cls" v-for="item in summary" :key="item.key">
          <div class="label">{{ item.label }}</div>
          <div class="value">{{ item.value }}</div>
        </div>
      </div>
      <div class="alerts-list" id="alerts-list">
        <AlertItem v-for="(alert, index) in alerts" :key="alert.brandName || index" :alert="alert" />
        <div class="empty-state" v-if="alerts.length === 0">
          <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path
              d="M22 11.08V12a10 10 0 11-5.93-9.14"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
            <polyline
              points="22 4 12 14.01 9 11.01"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
          <p>检查完成，无告警</p>
        </div>
      </div>
    </div>
  </section>
</template>
