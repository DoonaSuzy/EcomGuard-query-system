<script setup lang="ts">
/**
 * 总览页
 * =====
 * 一屏答完三个问题：服务是否正常、最近巡检结果如何、异常有哪些要点进去看。
 * 数字全部来自真实 API，不写死；后端不可达时给明确引导而不是一片 0。
 */
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'
import { useJobsStore } from '@/stores/jobs'
import type { components } from '@/types'
import { useToastStore } from '@/stores/toast'
import { STATE_LABEL } from '@/api/client'
import VerdictBadge from '@/components/VerdictBadge.vue'
import EmptyState from '@/components/EmptyState.vue'

type JobSummary = components['schemas']['JobSummary']

const router = useRouter()
const jobs = useJobsStore()
const toast = useToastStore()
const { list, listLoading, listError } = storeToRefs(jobs)

// 列表的首次加载与定时刷新统一由 App.vue 负责（它同时驱动 5s/30s 的节奏），
// 这里不再 onMounted 拉一遍，否则首屏会对 /api/jobs 打两次请求、返回顺序不定时列表闪。

const recent = computed(() => list.value.slice(0, 5))

const totals = computed(() => {
  const done = list.value.filter((j) => j.state === 'done')
  const sum = (pick: (j: JobSummary) => number | undefined) => done.reduce((a, j) => a + (pick(j) ?? 0), 0)
  return {
    jobs: list.value.length,
    running: list.value.filter((j) => j.state === 'running' || j.state === 'queued').length,
    checked: sum((j) => j.finished),
    alert: sum((j) => j.alert),
    notfound: sum((j) => j.notfound),
    unknown: sum((j) => j.unknown),
  }
})

function fmtTime(iso: string) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function stateTone(state: string) {
  if (state === 'done') return 'ok'
  if (state === 'failed') return 'alert'
  if (state === 'cancelled') return 'muted'
  return 'primary'
}

function refresh() {
  jobs.loadList().then(() => {
    if (!jobs.listError) toast.success('已刷新')
    else toast.error(jobs.listError)
  })
}
</script>

<template>
  <div class="page-head">
    <h1>总览</h1>
    <p>按企业全称批量查询国家企业信用信息公示系统登记状态，异常自动推送提醒。</p>
  </div>

  <div v-if="listError" class="card err-card">
    <strong>加载失败：</strong>{{ listError }}
    <button class="btn sm" @click="refresh">重试</button>
  </div>

  <div class="grid cols-4">
    <div class="stat"><div class="k">累计作业</div><div class="v">{{ totals.jobs }}</div></div>
    <div class="stat"><div class="k">已核查企业</div><div class="v">{{ totals.checked }}</div></div>
    <div class="stat"><div class="k">状态异常</div><div class="v" :class="totals.alert ? 'alert' : ''">{{ totals.alert }}</div></div>
    <div class="stat"><div class="k">未收录 / 未知</div><div class="v" :class="totals.notfound || totals.unknown ? 'warn' : ''">{{ totals.notfound }} / {{ totals.unknown }}</div></div>
  </div>

  <div class="card">
    <div class="card-title">
      <h2>最近作业</h2>
      <div class="row">
        <button class="btn sm" :disabled="listLoading" @click="refresh">{{ listLoading ? '刷新中…' : '刷新' }}</button>
        <button class="btn sm primary" @click="router.push('/submit')">＋ 提交巡检</button>
      </div>
    </div>

    <div v-if="listLoading && !list.length" class="tbl-wrap">
      <table class="tbl"><tbody><tr v-for="i in 4" :key="i"><td><div class="skel" style="height: 18px" /></td></tr></tbody></table>
    </div>

    <EmptyState
      v-else-if="!recent.length"
      icon="🗂"
      title="还没有巡检作业"
      desc="提交一批企业名称，系统会串行抓取并把结果汇总成可下载的 CSV。"
      action-text="提交第一批巡检"
      @action="router.push('/submit')"
    />

    <div v-else class="tbl-wrap">
      <table class="tbl">
        <thead>
          <tr>
            <th>作业</th><th>状态</th><th class="num">进度</th><th class="num">正常</th><th class="num">异常</th><th class="num">未收录</th><th>创建时间</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="j in recent" :key="j.job_id">
            <td class="mono">{{ j.job_id }}</td>
            <td><span class="badge" :class="stateTone(j.state)">{{ STATE_LABEL[j.state] || j.state }}</span></td>
            <td class="num">{{ j.finished }}/{{ j.total }}</td>
            <td class="num">{{ j.ok }}</td>
            <td class="num"><VerdictBadge v-if="j.alert" verdict="alert" plain /><span v-else>0</span></td>
            <td class="num">{{ j.notfound }}</td>
            <td class="mono">{{ fmtTime(j.created_at) }}</td>
            <td><button class="btn sm" @click="router.push(`/jobs/${j.job_id}`)">详情</button></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.err-card { display: flex; align-items: center; gap: 10px; border-color: var(--c-alert); color: var(--c-alert); margin-bottom: var(--gap); }
.grid.cols-4 { margin-bottom: var(--gap); }
</style>
