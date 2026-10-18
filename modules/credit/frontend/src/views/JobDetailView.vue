<script setup lang="ts">
/**
 * 作业详情页
 * =========
 *  - 活跃作业自动轮询（3s），完成/失败即停；页面不可见时暂停，省得白刷。
 *  - 结果表支持按判定筛选 + 关键词搜索（企业名 / 信用代码 / 登记状态）。
 *  - CSV 下载用 <a href> 直连，由浏览器落盘；未完成时后端返回 409，这里提前置灰。
 *  - 排队中的作业可取消（后端只允许取消 queued）。
 */
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useIntervalFn } from '@vueuse/core'
import { STATE_LABEL } from '@/api/client'
import { useJobsStore } from '@/stores/jobs'
import { useToastStore } from '@/stores/toast'
import VerdictBadge from '@/components/VerdictBadge.vue'
import EmptyState from '@/components/EmptyState.vue'

const route = useRoute()
const jobs = useJobsStore()
const toast = useToastStore()

// props 由路由注入（router/index.ts 里 props: true）
defineProps<{ jobId?: string }>()

const verdict = ref<'all' | 'ok' | 'alert' | 'notfound' | 'unknown'>('all')
const kw = ref('')
const cancelling = ref(false)

const job = computed(() => jobs.current)
const results = computed(() => job.value?.results ?? [])
const active = computed(() => job.value?.state === 'queued' || job.value?.state === 'running')

// 以路由参数为准而不是 props：KeepAlive 缓存后 props 不会随同组件内参数切换更新，
// 而 route.params 始终反映当前 URL。
const currentId = computed(() => String(route.params.jobId || ''))

async function load() {
  const d = await jobs.loadDetail(currentId.value)
  if (!d && jobs.currentError) toast.error(jobs.currentError)
}

onMounted(load)

// 轮询：只在「活跃」且「页面可见」时跑
const { pause, resume } = useIntervalFn(() => {
  if (document.visibilityState === 'hidden') return
  void load()
}, 3000, { immediate: false })

watch(active, (v) => (v ? resume() : pause()), { immediate: true })
onBeforeUnmount(pause)

// 路由在同组件内切换参数时也要重新加载
watch(currentId, () => {
  jobs.current = null
  verdict.value = 'all'
  kw.value = ''
  void load()
})

const filtered = computed(() => {
  let rows = results.value
  if (verdict.value !== 'all') rows = rows.filter((r) => (r.verdict ?? 'unknown') === verdict.value)
  const q = kw.value.trim().toLowerCase()
  if (q) {
    rows = rows.filter((r) =>
      [r.name, r.matched_name, r.credit_code, r.status].some((f) => (f ?? '').toLowerCase().includes(q)))
  }
  return rows
})

const pct = computed(() => {
  if (!job.value?.total) return 0
  return Math.min(100, Math.round(((job.value.finished ?? 0) / job.value.total) * 100))
})

/** 作业状态 → 徽章色调。与 JobsView.vue 的 stateTone 保持同一口径：
 *  done=ok / failed=alert / cancelled=muted / queued·running=primary。
 *  此前详情页把 cancelled 归到 primary（蓝），列表页却是 muted（灰），同一作业两页不同色。
 */
function stateTone(state: string) {
  if (state === 'done') return 'ok'
  if (state === 'failed') return 'alert'
  if (state === 'cancelled') return 'muted'
  return 'primary'
}

function fmtTime(iso?: string | null) {  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

async function cancel() {
  if (!job.value) return
  if (!confirm(`确定取消作业 ${job.value.job_id}？排队中的任务会被移除，已抓取的结果会保留。`)) return
  cancelling.value = true
  const ok = await jobs.cancel(job.value.job_id)
  cancelling.value = false
  if (ok) toast.success('已取消')
  else toast.error('取消失败：只有排队中的作业可以取消')
}

async function downloadCsv() {
  // 先 GET 探测：后端未完成时返回 409，直接 window.open 会把错误 JSON 存成本地文件
  const url = jobs.csvUrl(currentId.value)
  try {
    const res = await fetch(url)
    if (res.status === 409) {
      toast.warning('作业尚未完成，CSV 还没生成')
      return
    }
    if (res.status === 404) {
      toast.error('CSV 文件不存在（可能已被清理）')
      return
    }
    if (!res.ok) {
      toast.error(`下载失败（HTTP ${res.status}）`)
      return
    }
    // 用 blob + a[download] 而不是 window.open：能带上下载文件名，也不会被浏览器拦截
    const blob = await res.blob()
    const a = document.createElement('a')
    const objectUrl = URL.createObjectURL(blob)
    a.href = objectUrl
    a.download = `${currentId.value}.csv`
    document.body.appendChild(a)
    a.click()
    a.remove()
    // 必须延后 revoke：a.click() 只是把下载排进队列，紧接着同步 revoke 有浏览器
    // 在真正读 blob 之前就作废 URL 的风险（MDN 也建议延后释放）。这里是防御性写法，
    // 不是已复现的缺陷——headless 浏览器本身就会丢弃下载，无法用它验证落盘。
    setTimeout(() => URL.revokeObjectURL(objectUrl), 10_000)
  } catch {
    toast.error('下载失败：无法连接后端')
  }
}

const summaryTiles = computed(() => {
  const j = job.value
  if (!j) return []
  return [
    { k: '正常', v: j.ok ?? 0, tone: 'ok' },
    { k: '异常', v: j.alert ?? 0, tone: 'alert' },
    { k: '未收录', v: j.notfound ?? 0, tone: 'warn' },
    { k: '未知', v: j.unknown ?? 0, tone: '' },
  ]
})
</script>

<template>
  <div v-if="jobs.currentLoading && !job" class="card"><div class="skel" style="height: 120px" /></div>

  <div v-else-if="jobs.currentError && !job" class="card err-card">
    <div>
      <h2>无法加载作业</h2>
      <p>{{ jobs.currentError }}</p>
    </div>
    <button class="btn" @click="load">重试</button>
  </div>

  <template v-else-if="job">
    <div class="page-head detail-head">
      <div>
        <h1 class="mono">{{ job.job_id }}</h1>
        <p>
          <span class="badge" :class="stateTone(job.state)">
            <i class="bdot" />{{ STATE_LABEL[job.state] || job.state }}
          </span>
          <span v-if="job.notified" class="badge ok">已推送</span>
          <span class="dim">创建于 {{ fmtTime(job.created_at) }}</span>
        </p>
      </div>
      <div class="head-actions">
        <button v-if="job.state === 'done'" class="btn" @click="downloadCsv">⬇ 下载 CSV</button>
        <button v-if="job.state === 'queued'" class="btn danger" :disabled="cancelling" @click="cancel">
          {{ cancelling ? '取消中…' : '取消作业' }}
        </button>
      </div>
    </div>

    <div class="card">
      <div class="progress-row">
        <div class="bar"><i :style="{ width: pct + '%' }" /></div>
        <span class="pct mono">{{ job.finished }} / {{ job.total }}</span>
      </div>

      <div v-if="job.state === 'running' || job.state === 'queued'" class="live-tip">
        <span class="spin" aria-hidden="true" />
        正在抓取，每 3 秒自动刷新。目标站有风控，请勿重复提交。
      </div>

      <div v-if="job.error" class="err-box">
        <strong>错误信息：</strong>
        <span class="mono">{{ job.error }}</span>
      </div>

      <div class="grid cols-4">
        <div v-for="t in summaryTiles" :key="t.k" class="stat">
          <div class="k">{{ t.k }}</div>
          <div class="v" :class="t.tone">{{ t.v }}</div>
        </div>
      </div>

      <dl class="kv">
        <dt>开始时间</dt><dd>{{ fmtTime(job.started_at) }}</dd>
        <dt>结束时间</dt><dd>{{ fmtTime(job.finished_at) }}</dd>
        <dt>推送状态</dt><dd>{{ job.notified ? '已推送告警' : '未推送（无异常或未配置通道）' }}</dd>
      </dl>
    </div>

    <div class="card">
      <div class="card-title">
        <h2>结果明细 <span class="dim">（{{ filtered.length }} 条）</span></h2>
        <div class="row">
          <select v-model="verdict" aria-label="按判定筛选" class="sm">
            <option value="all">全部判定</option>
            <option value="alert">仅异常</option>
            <option value="ok">仅正常</option>
            <option value="notfound">仅未收录</option>
            <option value="unknown">仅未知</option>
          </select>
          <input v-model="kw" class="search" type="search" placeholder="搜名称 / 信用代码…" aria-label="搜索结果" />
        </div>
      </div>

      <EmptyState
        v-if="!filtered.length"
        icon="⏳"
        :title="active ? '正在抓取，结果会逐条出现' : (results.length ? '没有符合条件的结果' : '暂无结果')"
        :desc="active ? '每一家查完会立刻写入列表。' : (results.length ? '换个筛选条件试试。' : '该作业没有产生任何结果。')"
      />

      <div v-else class="tbl-wrap">
        <table class="tbl">
          <thead>
            <tr>
              <th>#</th><th>企业名称</th><th>匹配名称</th><th>统一社会信用代码</th><th>登记状态</th><th>判定</th><th>数据源</th><th>备注</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(r, i) in filtered" :key="`${r.name}-${i}`">
              <td class="num mono">{{ i + 1 }}</td>
              <td>{{ r.name }}</td>
              <td>{{ r.matched_name || '—' }}</td>
              <td class="mono">{{ r.credit_code || '—' }}</td>
              <td>{{ r.status || '—' }}</td>
              <td><VerdictBadge :verdict="r.verdict" /></td>
              <td class="dim">{{ r.data_source || '—' }}</td>
              <td class="dim">{{ r.note || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </template>
</template>

<style scoped>
.detail-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; }
.head-actions { display: flex; gap: 8px; }
.dim { color: var(--c-text-dim); font-size: 12.5px; }
.err-card { display: flex; justify-content: space-between; align-items: center; gap: 12px; border-color: var(--c-alert); }
.err-card p { margin: 4px 0 0; color: var(--c-text-dim); }
.progress-row { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.progress-row .bar { flex: 1; }
.pct { font-size: 12.5px; color: var(--c-text-dim); white-space: nowrap; }
.live-tip { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--c-primary); background: var(--c-primary-dim); border-radius: var(--radius-sm); padding: 7px 10px; margin-bottom: 14px; }
.spin { width: 11px; height: 11px; border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%; animation: rot 0.8s linear infinite; flex: none; }
@keyframes rot { to { transform: rotate(360deg); } }
.err-box { color: var(--c-alert); background: var(--c-alert-bg); border-radius: var(--radius-sm); padding: 9px 11px; margin-bottom: 14px; font-size: 13px; }
.grid.cols-4 { margin-bottom: 14px; }
.kv { margin: 0; padding-top: 12px; border-top: 1px solid var(--c-border); }
.search { max-width: 220px; }
select.sm { padding: 6px 8px; font-size: 13px; width: auto; }
</style>
