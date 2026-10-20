import { createApp } from 'vue'
import { createPinia } from 'pinia'

/**
 * 样式加载顺序是**语义性的**，改动前请先读 reconcile.css 顶部的说明：
 *
 *   1. credit 的 main.css  —— 它那 21 个源文件全部依赖 var(--c-*)，必须先定义
 *   2. WIPO 的 style.css   —— 作为统一后的设计基准，同名选择器（.card/.btn/
 *                             .badge/.page/.toast/.form-group…）由它胜出
 *   3. reconcile.css       —— 把 credit 的「无前缀修饰符」写法（.btn.primary、
 *                             .badge.ok）和 var(--c-*) 映射到 WIPO 的取值上
 *   4. shell.css           —— 只放统一壳新增的那几个类（系统列表、分组标题）
 *
 * 反过来（WIPO 先、credit 后）会让 credit 的配色与 10px 圆角覆盖掉 WIPO，
 * 那就不是「统一成 WIPO 样式」而是「统一成 credit 样式」了。
 */
import '@/styles/main.css'
import '@wipo/style.css'
import './styles/reconcile.css'
import './styles/shell.css'

import App from './App.vue'
import { router } from './router'

const app = createApp(App)

// credit 的 4 个 store 用 pinia defineStore；WIPO 的 store.js 是纯 reactive，
// 不需要 pinia 但也不会被它影响，所以统一挂上即可。
app.use(createPinia())
app.use(router)
app.mount('#app')
