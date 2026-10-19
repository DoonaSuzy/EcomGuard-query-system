/**
 * 与后端的唯一接口层。
 *
 * 路径与字段名**照抄旧版 static/js/app.js**，一个字母都没改：
 * `API_BASE = '/api'`，成功外壳 `{success, data, request_id}`。
 *
 * 两个刻意不做的事：
 * 1. 不统一包 try/catch——让调用方自己决定失败时弹什么 toast，
 *    否则「加载失败」和「删除失败」会共用一句文案，排查时看不出是哪一步。
 * 2. 不把错误外壳翻译成异常——后端失败时返回的是 HTTP 200 + success:false，
 *    翻译成 throw 反而要多写一层判断。这里原样返回 JSON，调用方看 success。
 */

const API_BASE = '/api'

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options)
  return await response.json()
}

function jsonBody(method, payload) {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }
}

/** 商标清单。返回的 data 是**数组本身**（不是 {trademarks: [...]}）。 */
export function fetchTrademarks() {
  return request('/trademarks')
}

/**
 * 新建商标。
 *
 * 后端返回 **200 而不是 201**（E2E 有 `assert response.status == 200`），
 * 这里不校验状态码，只看外壳的 success。
 */
export function createTrademark(payload) {
  return request('/trademarks', jsonBody('POST', payload))
}

export function updateTrademark(id, payload) {
  return request(`/trademarks/${id}`, jsonBody('PUT', payload))
}

export function deleteTrademark(id) {
  return request(`/trademarks/${id}`, { method: 'DELETE' })
}

/** 批量删除。返回体带 `deletedCount`。 */
export function deleteTrademarksBatch(ids) {
  return request('/trademarks/batch', jsonBody('DELETE', { ids }))
}

/**
 * 发起一次检查。
 *
 * body 用 camelCase：{trademarkIds, checkStatus, checkSquatting}。
 * 后端的 CheckRequest 用 alias 兼容 ids/check_status/check_squatting，
 * 但前端一律发 camelCase，避免两套写法并存。
 */
export function startCheck(payload) {
  return request('/check', jsonBody('POST', payload))
}

/**
 * 检查状态轮询。
 *
 * **这个接口不走 success 外壳**，直接返回 {status, result, error}。
 * status 有四种：running / completed / error / idle。
 * idle 是服务起来后还没跑过检查的状态，调用方要把它当「没在跑」处理。
 */
export function fetchCheckStatus() {
  return request('/check/status')
}

export function fetchReports() {
  return request('/reports')
}

export function fetchReportDetail(filename) {
  return request(`/reports/${encodeURIComponent(filename)}`)
}

/**
 * 导入文件。
 *
 * 字段名固定为 `file`；**不要**手动设 Content-Type，
 * 浏览器要自己写 multipart 的 boundary，设了反而会让后端解析不出来。
 */
export function importFile(file) {
  const formData = new FormData()
  formData.append('file', file)
  return request('/import', { method: 'POST', body: formData })
}

/**
 * 导出：拉取接口字节流，再以 Blob 触发下载。
 *
 * **不能再用 `window.location.href = ...`**（旧版与首版 Vue 都是这么写的）：
 * 那等于让浏览器**导航**过去，`format=json` 时后端返回 `application/json`
 * （没有 `Content-Disposition`，不是附件），浏览器会把整个页面替换成一段
 * 裸 JSON 文本 —— 用户点一下「导出 JSON」，应用界面就没了，得手动回退。
 * 只有 `format=csv` 因为带了 `attachment` 头才表现为下载。
 *
 * 用 fetch 还顺带解决两件事：
 * 1. 在聚合系统的 iframe 里同样有效（不改宿主页地址）；
 * 2. 按钮可以显示「导出中…」并捕获失败，而不是静默跳走。
 *
 * 字节原样转发（含 CSV 的 BOM 与 CRLF），不在前端重排——`test_export_csv`
 * 断言的是服务端产物的逐字节内容。
 */
export async function exportTrademarks(format) {
  const response = await fetch(`${API_BASE}/export?format=${format}`)
  if (!response.ok) {
    throw new Error(`导出失败：HTTP ${response.status}`)
  }
  const blob = await response.blob()
  return { blob, filename: format === 'csv' ? 'trademarks.csv' : 'trademarks.json' }
}
