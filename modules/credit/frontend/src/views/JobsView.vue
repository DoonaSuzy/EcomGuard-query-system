<script setup lang="ts">
/**
 * 作业列表页
 * =========
 * 支持按状态筛选 + 关键词搜 job_id；有活跃作业时自动快轮询（由 App.vue 统一刷，
 * 这里只负责筛选，避免两处定时器互相打架）。
 */
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'
import { useJobsStore } from '@/stores/jobs'
import { STATE_LABEL } from '@/api/client'
import VerdictBadge from '@/components/VerdictBadge.vue'
import EmptyState from '@/components/EmptyState.vue'

const router = useRouter()
const jobs = useJobsStore()
const { list, listLoading, listError } = storeToRefs(jobs)

const filter = ref<'all' | 'active' | 'done' | 'failed' | 'cancelled'>('all')
const kw = ref('')

const filtered = computed(() => {
  let rows = list.value
  if (filter.value === 'active') rows = rows.filter((j) => j.state === 'queued' || j.state === 'running')
  else if (filter.value === 'done') rows = rows.filter((j) => j.state === 'done')
  else if (filter.value === 'failed') rows = rows.filter((j) => j.state === 'failed')
  else if (filter.value === 'cancelled') rows = rows.filter((j) => j.state === 'cancelled')
  const q = kw.value.trim().toLowerCase()
  if (q) rows = rows.filter((j) => j.job_id.toLowerCase().includes(q))
  return rows
})

function stateTone(state: string) {
  if (state === 'done') return 'ok'
  if (state === 'failed') return 'alert'
  if (state === 'cancelled') return 'muted'
  return 'primary'
}

function fmtTime(iso: string) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function pct(j: { total: number; finished: number }) {
  if (!j.total) return 0
  return Math.min(100, Math.round((j.finished / j.total) * 100))
}
</script>

<template>
  <div class="page-head">
    <h1>作业列表</h1>
    <p>所有巡检作业的执行记录，点「详情」查看逐家结果与下载 CSV。</p>
  </div>

  <div class="toolbar">
    <div class="seg" role="tablist" aria-label="状态筛选">
      <button v-for="f in ([['all','全部'],['active','进行中'],['done','已完成'],['failed','失败'],['cancelled','已取消']] as const)" :key="f[0]"
              class="seg-btn" :class="{ on: filter === f[0] }" role="tab" :aria-selected="filter === f[0]" @click="filter = f[0]">
        {{ f[1] }}
      </button>
    </div>
    <input v-model="kw" class="search" type="search" placeholder="搜索 job_id…" aria-label="搜索作业 ID" />
  </div>

  <div v-if="listError" class="card err-card">
    <strong>加载失败：</strong>{{ listError }}
  </div>

  <EmptyState
    v-else-if="!listLoading && !filtered.length"
    icon="🔍"
    :title="list.length ? '没有符合条件的作业' : '还没有巡检作业'"
    :desc="list.length ? '换个筛选条件或清空搜索词试试。' : '提交一批企业名称即可开始第一次巡检。'"
    :action-text="list.length ? undefined : '去提交巡检'"
    @action="router.push('/submit')"
  />

  <div v-else class="tbl-wrap">
    <table class="tbl">
      <thead>
        <tr>
          <th>作业 ID</th><th>状态</th><th>进度</th><th class="num">正常</th><th class="num">异常</th><th class="num">未收录</th><th class="num">未知</th><th>创建时间</th><th>耗时</th><th>推送</th><th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="j in filtered" :key="j.job_id">
          <td class="mono">{{ j.job_id }}</td>
          <td><span class="badge" :class="stateTone(j.state)">{{ STATE_LABEL[j.state] || j.state }}</span></td>
          <td style="min-width: 110px">
            <div class="bar"><i :style="{ width: pct(j) + '%' }" /></div>
            <span class="pct mono">{{ j.finished }}/{{ j.total }}</span>
          </td>
          <td class="num">{{ j.ok }}</td>
          <td class="num"><VerdictBadge v-if="j.alert" verdict="alert" plain /><span v-else>0</span></td>
          <td class="num">{{ j.notfound }}</td>
          <td class="num">{{ j.unknown }}</td>
          <td class="mono">{{ fmtTime(j.created_at) }}</td>
          <td class="mono">{{ j.finished_at ? duration(j) : '—' }}</td>
          <td>
            <span v-if="j.notified" class="badge ok"><i class="bdot" />已推送</span>
            <span v-else class="tag muted">未推送</span>
          </td>
          <td>
            <button class="btn sm" @click="router.push(`/jobs/${j.job_id}`)">详情</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script lang="ts">
/** 计算耗时：用 started_at→finished_at，缺 started 时退回 created_at→finished_at */
function duration(j: { created_at: string; started_at?: string | null; finished_at?: string | null }) {
  const a = new Date(j.started_at || j.created_at).getTime()
  const b = new Date(j.finished_at as string).getTime()
  if (!Number.isFinite(a) || !Number.isFinite(b) || b < a) return '—'
  const s = Math.round((b - a) / 1000)
  if (s < 60) return `${s} 秒`
  return `${Math.floor(s / 60)} 分 ${s % 60} 秒`
}
</script>

<style scoped>
.toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: var(--gap); flex-wrap: wrap; }
.seg { display: inline-flex; background: var(--c-surface); border: 1px solid var(--c-border); border-radius: var(--radius-sm); overflow: hidden; }
.seg-btn { border: none; background: none; font: inherit; font-weight: 500; color: var(--c-text-dim); padding: 7px 14px; cursor: pointer; }
.seg-btn + .seg-btn { border-left: 1px solid var(--c-border); }
.seg-btn.on { background: var(--c-primary-dim); color: var(--c-primary); }
.search { max-width: 260px; }
.err-card { display: flex; align-items: center; gap: 10px; border-color: var(--c-alert); color: var(--c-alert); }
.pct { display: block; font-size: 11.5px; color: var(--c-text-dim); margin-top: 3px; }
.tag { font-size: 12px; padding: 2px 8px; border-radius: 999px; }
.tag.muted { color: var(--c-muted); background: var(--c-muted-bg); }
</style>
