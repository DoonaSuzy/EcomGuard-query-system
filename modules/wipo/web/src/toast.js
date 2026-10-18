import { reactive } from 'vue'

/**
 * Toast 队列。
 *
 * 关键行为与旧版一致，E2E 直接断言这两点：
 * - 每条 toast 4 秒后自动消失（test_toast_notifications.test_toast_auto_dismisses
 *   等 5 秒后断言 `#toast-container .toast` 计数为 0）
 * - 点 `.toast-close` 立即移除
 *
 * 移除时分两步：先加 `.hiding` 类触发 CSS 里的 slideOut 动画，300ms 后再
 * 从 DOM 里摘掉。这个 300ms 的缓冲不能省——直接移除就没有滑出动画了。
 */

const AUTO_DISMISS_MS = 4000
const HIDE_ANIMATION_MS = 300

export const toasts = reactive([])

let nextId = 1

/**
 * @param {string} title 主文案
 * @param {string} message 副文案
 * @param {'success'|'error'|'warning'|'info'} type 决定左边框颜色与图标
 */
export function showToast(title, message, type = 'info') {
  const id = nextId++
  toasts.push({ id, title, message, type, hiding: false })

  setTimeout(() => removeToast(id), AUTO_DISMISS_MS)
  return id
}

function removeToast(id) {
  const toast = toasts.find((t) => t.id === id)
  if (!toast || toast.hiding) return
  toast.hiding = true
  setTimeout(() => {
    const index = toasts.findIndex((t) => t.id === id)
    if (index !== -1) toasts.splice(index, 1)
  }, HIDE_ANIMATION_MS)
}

/** 关闭按钮用：立即走同一套消失流程。 */
export function dismissToast(id) {
  removeToast(id)
}
