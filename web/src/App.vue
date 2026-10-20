<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { useIntervalFn } from '@vueuse/core'

import { useStore as useWipoStore } from '@wipo/store.js'
import WipoToastContainer from '@wipo/components/ToastContainer.vue'
import WipoModalDialog from '@wipo/components/ModalDialog.vue'

import ErrorBoundary from '@/components/ErrorBoundary.vue'
import HealthPill from '@/components/HealthPill.vue'
import ToastHost from '@/components/ToastHost.vue'
import { useJobsStore } from '@/stores/jobs'

import WipoHost from './hosts/WipoHost.vue'
import { SYSTEMS, systemById, systemOf, titleOf } from './nav'

/**
 * 统一壳：左侧系统列表 + 当前系统的功能导航 + 顶栏 + 内容区。
 *
 * 关键取舍——**外层只切系统，内层各系统保留自己的页面机制**：
 *   WIPO   四个页面常驻挂载，靠 `state.currentPage` + `.page.active` 显隐
 *   credit 五个视图走 vue-router，与原工程一致
 * 不做「把所有页面拍平成一张路由表」的原因见 hosts/WipoHost.vue 的注释：
 * WIPO 的页面卸载会丢轮询与报告详情，且它的 118 项 E2E 依赖那套显隐。
 *
 * 视觉上只有一个侧栏：上半是系统列表（WIPO / 营业执照），下半是当前系统的
 * 功能项。切换系统时整体布局不动，只换内容区与功能项。
 */
const route = useRoute()
const router = useRouter()

const wipo = useWipoStore()
const jobs = useJobsStore()

const system = computed(() => systemOf(typeof route.name === 'string' ? route.name : null))
const systemDef = computed(() => systemById(system.value))
const pageTitle = computed(() => titleOf(typeof route.name === 'string' ? route.name : null))

/** 顶栏右侧：WIPO 显示商标总数，credit 显示健康灯 + 活跃作业 + 新建按钮。 */
const activeCount = computed(
  () => jobs.list.filter((j) => j.state === 'queued' || j.state === 'running').length,
)
const activeLabel = computed(() => (activeCount.value > 0 ? `${activeCount.value} 个作业进行中` : ''))
const pollMs = computed(() => (activeCount.value > 0 ? 5000 : 30000))

function goSystem(id: string) {
  const sys = systemById(id as never)
  void router.push(`/${sys.home}`)
}

function goPage(page: string) {
  void router.push(`/${page}`)
}

/**
 * 只在营业执照系统里轮询作业列表。
 *
 * credit 原来的 App.vue 无条件轮询；放进统一壳之后，用户停在 WIPO 页面时
 * 那份轮询纯属空转（而且每 5s 一次会持续打后端）。这里保留原有的
 * 「有活跃作业 5s、否则 30s」节奏，只是加了系统判定。
 */
useIntervalFn(
  () => {
    if (system.value !== 'credit') return
    if (document.visibilityState === 'visible') void jobs.loadList()
  },
  pollMs,
)

// 首次进入或从 WIPO 切过来时立刻拉一次，避免等一个轮询周期才看到数据
onMounted(() => void jobs.loadList())
watch(system, (s) => {
  if (s === 'credit') void jobs.loadList()
})
</script>

<template>
  <div class="app">
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
        <span>统一监控平台</span>
      </div>

      <!-- 系统列表：左侧以列表形式列出两个系统 -->
      <div class="sys-list" role="tablist" aria-label="业务系统">
        <a
          v-for="s in SYSTEMS"
          :key="s.id"
          href="#"
          class="sys-item"
          :class="{ active: system === s.id }"
          :data-system="s.id"
          role="tab"
          :aria-selected="system === s.id"
          @click.prevent="goSystem(s.id)"
        >
          <span class="sys-name">{{ s.label }}</span>
          <span class="sys-desc">{{ s.desc }}</span>
        </a>
      </div>

      <nav class="nav" :aria-label="`${systemDef.label} 功能`">
        <div class="nav-group-title">{{ systemDef.label }} 功能</div>
        <a
          v-for="p in systemDef.pages"
          :key="p.page"
          href="#"
          class="nav-item"
          :class="{ active: route.name === p.page }"
          :data-page="p.page"
          @click.prevent="goPage(p.page)"
        >
          <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path
              :d="p.icon"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
          <span>{{ p.label }}</span>
        </a>
      </nav>

      <div class="sidebar-footer">
        <div class="version">v1.0.0</div>
      </div>
    </aside>

    <main class="main-content">
      <header class="header">
        <h1 id="page-title">{{ pageTitle }}</h1>
        <div class="header-actions">
          <template v-if="system === 'wipo'">
            <div class="trademark-count">
              共 <span id="total-count">{{ wipo.state.trademarks.length }}</span> 个商标
            </div>
          </template>
          <template v-else>
            <HealthPill />
            <span v-if="activeLabel" class="badge primary">{{ activeLabel }}</span>
            <button class="btn sm primary" @click="router.push('/submit')">新建巡检</button>
          </template>
        </div>
      </header>

      <!-- WIPO：四个页面常驻，内部自管显隐 -->
      <WipoHost v-show="system === 'wipo'" />

      <!-- 营业执照：路由视图 -->
      <div v-show="system === 'credit'" class="sys-credit">
        <ErrorBoundary>
          <RouterView v-slot="{ Component }">
            <KeepAlive :include="['SubmitView']">
              <component :is="Component" />
            </KeepAlive>
          </RouterView>
        </ErrorBoundary>
      </div>
    </main>
  </div>

  <WipoToastContainer />
  <WipoModalDialog />
  <ToastHost />
</template>
