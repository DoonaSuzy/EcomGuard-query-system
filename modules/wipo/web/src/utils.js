/**
 * 展示层的小工具。
 *
 * 全部从旧版 static/js/app.js 原样搬过来（formatDate / parseNiceClass /
 * errorCount），因为 E2E 断言的是渲染出的文本，任何一个格式变化都会让
 * 断言失败。
 */

/**
 * 日期格式化：空值返回 '-'，解析失败返回原字符串。
 *
 * 保持 `zh-CN` + 2-digit 的组合：E2E 只断言文案里含不含品牌名，
 * 但视觉上这一列一直是这个格式，改了就是无谓的回归。
 */
export function formatDate(dateStr) {
  if (!dateStr) return '-'
  try {
    const date = new Date(dateStr)
    return date.toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return dateStr
  }
}

/** 尼斯分类：中英文逗号都算分隔符，空串返回空数组。 */
export function parseNiceClass(value) {
  if (!value) return []
  return String(value)
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter(Boolean)
}

export function joinNiceClass(list) {
  return (list || []).join(', ')
}

/**
 * 错误数量的两种历史形态都要认：
 * 旧版把数量直接写在 errors 键上（int），新版是错误详情列表。
 * 直接写 `errors || 0` 时，空数组 [] 是真值，会被渲染成空白而不是 0。
 */
export function errorCount(value) {
  if (Array.isArray(value)) {
    return value.length
  }
  return value || 0
}
