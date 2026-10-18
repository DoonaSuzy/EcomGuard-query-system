import { reactive } from 'vue'

/**
 * 确认弹窗的状态。
 *
 * 只有一个弹窗实例（与旧版一样，全局一个 modalCallback），
 * 所以状态是模块级的单例而不是每次 new。
 *
 * `visible` 决定 `#modal-overlay` 的 display：旧版在隐藏时写 display:none，
 * 这里用 v-show 会残留 inline style，E2E 的 `is_hidden()` 两种都能认，
 * 但用 v-if 更干净——隐藏时节点根本不存在，is_visible() 一定为 false。
 */
export const modal = reactive({
  visible: false,
  title: '确认操作',
  message: '',
  /** 点「确认」时执行的回调；由调用方传入，可能是异步的。 */
  onConfirm: null,
})

/**
 * @param {string} title
 * @param {string} message
 * @param {() => void | Promise<void>} onConfirm
 */
export function showModal(title, message, onConfirm) {
  modal.title = title
  modal.message = message
  modal.onConfirm = onConfirm
  modal.visible = true
}

export function hideModal() {
  modal.visible = false
  modal.onConfirm = null
}

/** 点「确认」：先跑回调（可能是网络请求），再关窗。顺序与旧版一致。 */
export async function confirmModal() {
  const callback = modal.onConfirm
  if (callback) {
    await callback()
  }
  hideModal()
}
