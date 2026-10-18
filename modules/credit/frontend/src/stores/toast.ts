/**
 * 全局提示（Toast）
 * ================
 * 用 Pinia 存队列而不是裸 ref，是为了让「非组件上下文」（如 store 里的请求失败分支、
 * 路由守卫）也能直接 push。组件里通过 useToastStore() 消费。
 */
import { defineStore } from 'pinia'

export type ToastKind = 'info' | 'success' | 'error' | 'warning'

export interface Toast {
  id: number
  kind: ToastKind
  text: string
  /** 该条是否常驻（需要用户手动关），false 时自动倒计时消失 */
  sticky: boolean
}

let seq = 0

export const useToastStore = defineStore('toast', {
  state: () => ({ items: [] as Toast[] }),
  actions: {
    push(text: string, kind: ToastKind = 'info', sticky = false, ttl = 5000) {
      const id = ++seq
      this.items.push({ id, kind, text, sticky })
      // 队列有上限，避免某个页面疯狂报错把 DOM 撑爆
      if (this.items.length > 5) this.items.splice(0, this.items.length - 5)
      if (!sticky && ttl > 0) {
        setTimeout(() => this.dismiss(id), ttl)
      }
      return id
    },
    success(t: string) { return this.push(t, 'success') },
    error(t: string, sticky = true) { return this.push(t, 'error', sticky) },
    warning(t: string) { return this.push(t, 'warning') },
    info(t: string) { return this.push(t, 'info') },
    dismiss(id: number) {
      const i = this.items.findIndex((x) => x.id === id)
      if (i >= 0) this.items.splice(i, 1)
    },
  },
})
