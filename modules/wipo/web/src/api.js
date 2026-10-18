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
 * 导出：直接触发浏览器下载。
 *
 * 这个接口返回的是文件（json/csv）而不是外壳，所以不能用 fetch。
 * 旧版也是 `window.location.href = ...`，沿用即可。
 */
export function exportDataUrl(format) {
  window.location.href = `${API_BASE}/export?format=${format}`
}
