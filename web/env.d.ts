/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}

// WIPO 的页面包是纯 JS（无类型），统一前端只是消费者，不为它补类型定义。
declare module '@wipo/*'
