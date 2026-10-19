/**
 * HTTP 客户端（openapi-fetch）
 * ============================
 * 为什么是 openapi-fetch 而不是 axios / 手写 fetch：
 *   类型直接来自 openapi.json 生成的 types.d.ts，路径写错、参数漏传、
 *   响应字段名拼错在编译期就报错；后端改接口会先炸 typecheck，而不是先炸用户。
 *
 * 基址用空串（同源）而不是写死 http://127.0.0.1:8000：
 *   - 后端所有业务接口都在 /api 前缀下（api.py 里 APIRouter(prefix="/api")），
 *     而 /api 这段前缀已经写在 openapi.json 生成的 paths 里，所以 baseUrl
 *     不能再是 '/api'——那会拼成 /api/api/health；
 *   - 开发态由 vite.config.ts 的 proxy 转发到后端，浏览器看到的是同源 /api，
 *     不存在 CORS（后端没配 CORS 中间件）；
 *   - 生产态由 FastAPI 把 dist/ 挂在 / 下，同样同源。
 *   前端页面路径是 /jobs（列表页）与 /jobs/:jobId（详情页），接口在 /api/jobs，
 *   两者不抢路径——这也是接口必须带前缀的原因。
 *   想直连后端（比如联调）才需要改成 VITE_API_BASE_URL 覆盖。
 */
import createClient from 'openapi-fetch'
import type { paths } from '@/types'

/**
 * 后端 origin。同源时是空串；联调直连其他端口时用 VITE_API_BASE_URL 覆盖。
 *
 * 注意这里**只有 origin，没有 /api**：路径前缀来自 openapi.json（types.d.ts 里
 * 的键就是 '/api/jobs'），再叠加一次会变成 /api/api/...。CSV 下载这类不走
 * openapi-fetch 的原生请求需要自己补前缀，用 API_PATH_PREFIX。
 */
export const API_ORIGIN: string = import.meta.env.VITE_API_BASE_URL ?? ''

/** 业务接口统一前缀。与 api.py 的 APIRouter(prefix="/api") 对应。 */
export const API_PATH_PREFIX = '/api'

export const client = createClient<paths>({ baseUrl: API_ORIGIN })

/**
 * 把后端的错误响应翻成中文可读文案。
 *
 * openapi-fetch@0.17 的返回约定（读源码 dist/index.mjs 确认，不是按 axios 猜的）：
 *   - 成功：`{ data, response }`
 *   - 非 2xx：`{ error, response }`，其中 `error` 已经是**解析后的响应体本身**
 *     （后端给的是 `{"detail": "..."}`），**不是** `{ status, data }` 包装。
 *   - 状态码只在 `response.status` 上；`error` 上没有 `status` 字段。
 *   - 网络层失败（后端没起、DNS 错）**直接 throw**，不返回 `{ error }`，
 *     调用方必须 try/catch。
 *
 * 传进来的 `err` 允许三种形态，一是为了兼容调用方手上只有 error 的场景，
 * 二是为了让单测能直接喂 `{ status, error }` 而不必造 Response：
 *   1. 裸响应体（openapi-fetch 原样给的）        → 从 detail/message 取
 *   2. `Error` 实例（网络层抛的）                 → 取 message
 *   3. `{ status, error }` 包装（调用方拼的）     → 有 status 先按 status 兜底
 */
export function extractErrorMessage(err: unknown, status?: number): string {
  if (!err) {
    // 没有 body 的非 2xx（如代理回的纯文本 502）：只能靠状态码说话
    return describeStatus(status)
  }

  // 形态 2：网络层抛出的 Error（fetch failed / aborted 等）
  if (err instanceof Error) {
    const msg = err.message || ''
    // fetch 的原始报错对用户没意义（"fetch failed" / "Failed to fetch"）
    if (!msg || /failed to fetch|fetch failed|networkerror|load failed/i.test(msg)) {
      return '网络不可达，请检查后端是否已启动'
    }
    return msg
  }

  // 形态 3：`{ status, error }` 或 `{ status, data }` 包装
  const wrapped = err as { status?: number; error?: unknown; data?: unknown; message?: string }
  const code = status ?? wrapped.status

  // 形态 1：裸响应体（最常见）
  const body = (wrapped.error !== undefined ? wrapped.error : wrapped) as unknown

  if (typeof body === 'string' && body.trim()) return body
  if (body && typeof body === 'object') {
    const d = body as { detail?: unknown; message?: unknown }
    // FastAPI HTTPException：{"detail": "作业不存在：xxx"}
    if (typeof d.detail === 'string' && d.detail.trim()) return d.detail
    if (typeof d.message === 'string' && d.message.trim()) return d.message
    // FastAPI 422：{"detail": [{ loc, msg, type }]}，取第一条并带上字段名
    if (Array.isArray(d.detail) && d.detail.length) {
      const first = d.detail[0] as { loc?: unknown[]; msg?: string }
      const field = Array.isArray(first?.loc)
        ? String(first.loc[first.loc.length - 1] ?? '')
        : ''
      const msg = first?.msg || '请求参数不合法'
      return field && field !== 'body' ? `${field}: ${msg}` : msg
    }
  }

  if (typeof wrapped.message === 'string' && wrapped.message) return wrapped.message
  return describeStatus(code)
}

/** 状态码 → 中文文案（没有可用 body 时的兜底） */
function describeStatus(status?: number): string {
  if (status === 404) return '目标不存在（404）'
  if (status === 409) return '与当前状态冲突（409）'
  if (status === 422) return '请求参数不合法（422）'
  if (status && status >= 500) return `服务端错误（${status}），请稍后重试`
  if (status) return `请求失败（${status}）`
  return '网络不可达，请检查后端是否已启动'
}

/** 判定码 → 中文标签（与后端 credit_check.py 的 VERDICT 语义对齐） */
export const VERDICT_LABEL: Record<string, string> = {
  ok: '正常',
  alert: '异常',
  notfound: '未收录',
  unknown: '未知',
}

/** 作业状态 → 中文标签 */
export const STATE_LABEL: Record<string, string> = {
  queued: '排队中',
  running: '巡检中',
  done: '已完成',
  failed: '失败',
  cancelled: '已取消',
  // 定时巡检专用：名单为空时调度器直接跳过、不建作业（schedule.py 写
  // `last_state = "skipped"`）。缺这条，调度列表会把英文 skipped 漏到界面上。
  skipped: '已跳过',
}

/** 判定码 → 徽章色调（对应 styles 里的 --c-ok/--c-alert/--c-warn/--c-muted） */
export const VERDICT_TONE: Record<string, string> = {
  ok: 'ok',
  alert: 'alert',
  notfound: 'warn',
  unknown: 'muted',
}
