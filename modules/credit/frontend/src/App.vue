<script setup lang="ts">
/**
 * 应用外壳：侧栏导航 + 顶栏（健康灯 + 活跃作业提示）
 * 用 router-link 的 active class 高亮当前页；index 页高亮「总览」。
 */
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useIntervalFn } from '@vueuse/core'
import { useJobsStore } from '@/stores/jobs'
import ErrorBoundary from '@/components/ErrorBoundary.vue'
import HealthPill from '@/components/HealthPill.vue'
import ToastHost from '@/components/ToastHost.vue'

const router = useRouter()
const jobs = useJobsStore()

const NAV = [
  { to: '/overview', label: '总览', icon: '📊' },
  { to: '/submit', label: '提交巡检', icon: '➕' },
  { to: '/schedules', label: '定时巡检', icon: '⏰' },
  { to: '/jobs', label: '作业列表', icon: '🗂' },
]

// 有活跃作业时 5s 刷一次列表，否则 30s——避免没有任务时也空转
const activeCount = computed(() => jobs.list.filter((j) => j.state === 'queued' || j.state === 'running').length)
const activeLabel = computed(() => (activeCount.value > 0 ? `${activeCount.value} 个作业进行中` : ''))
const pollMs = computed(() => (activeCount.value > 0 ? 5000 : 30000))

// 首屏只由 App 触发一次 loadList；各页面（Overview/Jobs）不再各自 onMounted 拉一遍，
// 否则同一份数据首屏要打两到三次请求，且并发返回顺序不定时列表会闪。
onMounted(() => void jobs.loadList())

// useIntervalFn 的 interval 是 MaybeRefOrGetter：直接传 computed，它内部的
// watch(interval) 会在值变化时按新间隔 resume()。传 pollMs.value（瞬间求值的数字）
// 则永远停在创建那一刻的 30s——这是原先「有作业也不加速」的根因。
useIntervalFn(() => {
  if (document.visibilityState === 'visible') void jobs.loadList()
}, pollMs)
</script>

<template>
  <div class="app-shell">
    <aside class="app-side">
      <div class="app-brand"><span class="dot" />巡检控制台</div>
      <nav class="nav-list" aria-label="主导航">
        <router-link v-for="n in NAV" :key="n.to" :to="n.to" class="nav-link" active-class="router-link-active">
          <span class="ico" aria-hidden="true">{{ n.icon }}</span>{{ n.label }}
        </router-link>
      </nav>
      <div class="side-foot">
        <span class="ver mono">v1.0.0</span>
      </div>
    </aside>

    <div class="app-main">
      <header class="app-top">
        <HealthPill />
        <span v-if="activeLabel" class="badge primary">{{ activeLabel }}</span>
        <span class="grow" />
        <button class="btn sm" @click="router.push('/submit')">新建巡检</button>
      </header>
      <main class="page">
        <ErrorBoundary>
          <RouterView v-slot="{ Component }">
            <KeepAlive :include="['SubmitView']">
              <component :is="Component" />
            </KeepAlive>
          </RouterView>
        </ErrorBoundary>
      </main>
    </div>
  </div>

  <ToastHost />
</template>

<style scoped>
.nav-list { display: flex; flex-direction: column; gap: 2px; }
.side-foot { margin-top: auto; padding-top: 16px; color: var(--c-text-dim); }
.ver { font-size: 11.5px; }

/* 窄屏时侧栏横滚，导航保持一排 */
@media (max-width: 900px) {
  .nav-list { flex-direction: row; }
}
</style>
