/**
 * 巡检作业状态管理
 * ================
 * 三个 store 各自管一段生命周期：
 *   health  — 后端健康状态（轮询，慢）
 *   jobs    — 作业列表 + 当前作业详情（轮询，只在有活跃作业时快轮）
 *   draft   — 提交页表单草稿（跨路由保留，避免切页面丢输入）
 */
import { defineStore } from 'pinia'
import { client, extractErrorMessage, API_ORIGIN, API_PATH_PREFIX } from '@/api/client'
import type { components } from '@/types'
type HealthResponse = components['schemas']['HealthResponse']
type JobSummary = components['schemas']['JobSummary']
type JobDetail = components['schemas']['JobDetail']

export const useHealthStore = defineStore('health', {
  state: () => ({
    data: null as HealthResponse | null,
    /** 最近一次请求是否失败（后端没起 / 网络断） */
    offline: false,
    lastError: '' as string,
  }),
  getters: {
    ok: (s) => s.data?.status === 'ok' && s.data.db,
  },
  actions: {
    async refresh() {
      try {
        // /api/health 在 openapi.json 里只声明了 200（后端三态都走 200，
        // 用 body.status 表达 ok/degraded），所以 openapi-fetch 的判别联合里
        // error 分支被推断成 never。但运行期仍会撞上 502/503/500（反代、崩溃、
        // 中间层），那条分支正是「顶栏变红并说清楚原因」的依据，不能因为类型
        // 说不可能就删掉。这里按运行期形状显式收窄，把类型缺口留在这一处。
        type HealthRaw =
          | { data: HealthResponse; error?: undefined; response: Response }
          | { data?: undefined; error: unknown; response: Response }
        const res = (await client.GET('/api/health')) as HealthRaw
        if (res.error) {
          this.offline = true
          this.lastError = extractErrorMessage(res.error, res.response.status)
          return
        }
        this.data = res.data ?? null
        this.offline = false
        this.lastError = ''
      } catch (e) {
        // openapi-fetch 在网络层失败时是 throw，不返回 { error }
        this.offline = true
        this.lastError = extractErrorMessage(e)
      }
    },
  },
})

/** 是否属于「还在跑」的状态——决定要不要开快轮询 */
export function isActive(state: string): boolean {
  return state === 'queued' || state === 'running'
}

export const useJobsStore = defineStore('jobs', {
  state: () => ({
    list: [] as JobSummary[],
    listLoading: false,
    listError: '',
    current: null as JobDetail | null,
    currentLoading: false,
    currentError: '',
  }),
  getters: {
    hasActive: (s) => s.list.some((j) => isActive(j.state)),
  },
  actions: {
    async loadList(limit = 50) {
      this.listLoading = true
      this.listError = ''
      try {
        const res = await client.GET('/api/jobs', { params: { query: { limit } } })
        if (res.error) {
          this.listError = extractErrorMessage(res.error, res.response.status)
          return
        }
        this.list = res.data ?? []
      } catch (e) {
        this.listError = extractErrorMessage(e)
      } finally {
        this.listLoading = false
      }
    },

    async loadDetail(jobId: string) {
      this.currentLoading = true
      this.currentError = ''
      try {
        const res = await client.GET('/api/jobs/{job_id}', {
          params: { path: { job_id: jobId } },
        })
        if (res.error) {
          // 404 单独一种文案：用户可能是从旧链接点进来的
          this.currentError = res.response.status === 404
            ? '作业不存在，可能已被清理'
            : extractErrorMessage(res.error, res.response.status)
          this.current = null
          return null
        }
        this.current = res.data ?? null
        return this.current
      } catch (e) {
        this.currentError = extractErrorMessage(e)
        this.current = null
        return null
      } finally {
        this.currentLoading = false
      }
    },

    /** 提交后把新作业插到列表头部，省一次全量刷新 */
    prepend(job: JobSummary) {
      this.list.unshift(job)
    },

    async cancel(jobId: string): Promise<boolean> {
      try {
        const res = await client.DELETE('/api/jobs/{job_id}', {
          params: { path: { job_id: jobId } },
        })
        if (res.error) {
          // 把后端的原因（如 409「作业状态为 running，只能取消 queued」）留给人看
          this.currentError = extractErrorMessage(res.error, res.response.status)
          return false
        }
        await this.loadList()
        if (this.current?.job_id === jobId) await this.loadDetail(jobId)
        return true
      } catch (e) {
        this.currentError = extractErrorMessage(e)
        return false
      }
    },

    /** CSV 走浏览器原生下载，不经过 fetch——FileResponse 直接让浏览器落盘最简单 */
    csvUrl(jobId: string) {
      return `${API_ORIGIN}${API_PATH_PREFIX}/jobs/${encodeURIComponent(jobId)}/csv`
    },
  },
})

/** 提交页草稿 */
export const useDraftStore = defineStore('draft', {
  state: () => ({
    namesText: '',
    webhook: '',
    mailTo: '',
    alwaysNotify: true,
    minDelay: 3.5,
    maxDelay: 6.5,
  }),
  actions: {
    setNames(text: string) { this.namesText = text },
    reset() {
      this.namesText = ''
      this.webhook = ''
      this.mailTo = ''
      this.alwaysNotify = true
      this.minDelay = 3.5
      this.maxDelay = 6.5
    },
  },
})
