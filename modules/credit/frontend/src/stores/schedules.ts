/**
 * 定时巡检状态管理
 * =================
 * 与 jobs store 分开的理由：两者的轮询节奏和失败语义完全不同。
 * 作业列表要快轮（有活跃作业时 5s），调度列表几乎不变（配置类数据），
 * 快轮它只是白打请求；反之调度改完必须立刻看到新的 next_run_at，
 * 作业列表则不需要这种即时性。
 */
import { defineStore } from 'pinia'
import { client, extractErrorMessage } from '@/api/client'
import type { components } from '@/types'

type ScheduleOut = components['schemas']['ScheduleOut']
type ScheduleIn = components['schemas']['ScheduleIn']
type JobSummary = components['schemas']['JobSummary']

/** 新建/修改表单的本地形态（与 ScheduleIn 对齐，但 names 用整块文本） */
export interface ScheduleForm {
  name: string
  enabled: boolean
  freq: 'daily' | 'weekly' | 'monthly'
  at_time: string
  weekday: number
  day: number
  namesText: string
  webhook: string
  mailTo: string
  alwaysNotify: boolean
  minDelay: number
  maxDelay: number
}

export const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

export function emptyForm(): ScheduleForm {
  return {
    name: '',
    enabled: true,
    freq: 'daily',
    // 默认 09:00：与原计划任务脚本的口径一致，业务侧不用重新适应
    at_time: '09:00',
    weekday: 0,
    day: 1,
    namesText: '',
    webhook: '',
    mailTo: '',
    alwaysNotify: false,
    minDelay: 3.5,
    maxDelay: 6.5,
  }
}

/** 整块文本 → 名单数组。与提交页同一套切分口径（换行/中英文逗号/分号）。 */
export function splitNames(text: string): string[] {
  const out: string[] = []
  const seen = new Set<string>()
  for (const raw of text.split(/[\r\n,;，；]+/)) {
    const n = raw.trim()
    if (!n || n.startsWith('#')) continue
    if (seen.has(n)) continue
    seen.add(n)
    out.push(n)
  }
  return out
}

/** 表单 → 接口体 */
export function toPayload(f: ScheduleForm): ScheduleIn {
  return {
    name: f.name.trim(),
    enabled: f.enabled,
    freq: f.freq,
    at_time: f.at_time,
    weekday: f.weekday,
    day: f.day,
    names: splitNames(f.namesText),
    webhook: f.webhook.trim() || null,
    mail_to: f.mailTo.trim() || null,
    always_notify: f.alwaysNotify,
    min_delay: Number(f.minDelay),
    max_delay: Number(f.maxDelay),
  }
}

/** 已有调度 → 表单（编辑时回填） */
export function fromSchedule(s: ScheduleOut): ScheduleForm {
  return {
    name: s.name,
    enabled: s.enabled,
    freq: s.freq,
    at_time: s.at_time,
    weekday: s.weekday ?? 0,
    day: s.day ?? 1,
    // 回填必须把名单摊回多行文本：编辑页要能像提交页一样整块粘改
    namesText: (s.names ?? []).join('\n'),
    webhook: s.webhook ?? '',
    mailTo: s.mail_to ?? '',
    alwaysNotify: s.always_notify ?? false,
    minDelay: s.min_delay ?? 3.5,
    maxDelay: s.max_delay ?? 6.5,
  }
}

export const useSchedulesStore = defineStore('schedules', {
  state: () => ({
    list: [] as ScheduleOut[],
    loading: false,
    error: '',
    /** 正在被操作（启用/停用/删除/立刻执行）的调度 id，用于禁用按钮防连点 */
    busy: '' as string,
  }),
  getters: {
    enabledCount: (s) => s.list.filter((x) => x.enabled).length,
  },
  actions: {
    async load() {
      this.loading = true
      this.error = ''
      try {
        // 同 preview：列表接口只声明了 200，非 2xx 分支被推成 never。
        // 运行期仍会撞 500/502，按形状收窄。
        type ListRaw =
          | { data: ScheduleOut[]; error?: undefined; response: Response }
          | { data?: undefined; error: unknown; response: Response }
        const res = (await client.GET('/api/schedules')) as ListRaw
        if (res.error) {
          this.error = extractErrorMessage(res.error, res.response.status)
          return
        }
        this.list = res.data ?? []
      } catch (e) {
        this.error = extractErrorMessage(e)
      } finally {
        this.loading = false
      }
    },

    async create(payload: ScheduleIn): Promise<ScheduleOut | null> {
      const res = await client.POST('/api/schedules', { body: payload })
      if (res.error) throw new Error(extractErrorMessage(res.error, res.response.status))
      await this.load()
      return res.data ?? null
    },

    async update(id: string, payload: ScheduleIn): Promise<ScheduleOut | null> {
      const res = await client.PUT('/api/schedules/{schedule_id}', {
        params: { path: { schedule_id: id } },
        body: payload,
      })
      if (res.error) throw new Error(extractErrorMessage(res.error, res.response.status))
      await this.load()
      return res.data ?? null
    },

    async toggle(id: string, enabled: boolean): Promise<ScheduleOut | null> {
      this.busy = id
      try {
        const res = await client.POST('/api/schedules/{schedule_id}/toggle', {
          params: { path: { schedule_id: id }, query: { enabled } },
        })
        if (res.error) throw new Error(extractErrorMessage(res.error, res.response.status))
        await this.load()
        return res.data ?? null
      } finally {
        this.busy = ''
      }
    },

    async runNow(id: string): Promise<JobSummary | null> {
      this.busy = id
      try {
        const res = await client.POST('/api/schedules/{schedule_id}/run', {
          params: { path: { schedule_id: id } },
        })
        if (res.error) throw new Error(extractErrorMessage(res.error, res.response.status))
        await this.load()
        return res.data ?? null
      } finally {
        this.busy = ''
      }
    },

    async remove(id: string): Promise<void> {
      this.busy = id
      try {
        const res = await client.DELETE('/api/schedules/{schedule_id}', {
          params: { path: { schedule_id: id } },
        })
        if (res.error) throw new Error(extractErrorMessage(res.error, res.response.status))
        await this.load()
      } finally {
        this.busy = ''
      }
    },

    /**
     * 预览下次触发时间（不落库）。
     *
     * 失败时**返回空串而不是抛**：它绑在输入框上，用户打字过程中大量
     * 中间态（「09:0」）都会 422，抛异常会把表单打成一堆红字。
     *
     * 这里的类型收窄是必须的：/api/schedules/preview 只声明了 200 与 422，
     * openapi-fetch 的判别联合会把「其他非 2xx」判成 never。但运行期仍会撞上
     * 500/502（后端崩、反代超时）——那些也是「预览不出来」而不是「表单错了」，
     * 同样应该吞掉。按运行期形状显式收窄，把类型缺口留在这一处。
     */
    async preview(freq: ScheduleIn['freq'], at_time: string,
                  weekday: number, day: number): Promise<string> {
      type PreviewRaw =
        | { data: { next_run_at: string }; error?: undefined; response: Response }
        | { data?: undefined; error: unknown; response: Response }
      try {
        const res = (await client.GET('/api/schedules/preview', {
          params: { query: { freq, at_time, weekday, day } },
        })) as PreviewRaw
        if (res.error || !res.data) return ''
        return res.data.next_run_at
      } catch {
        return ''
      }
    },
  },
})
