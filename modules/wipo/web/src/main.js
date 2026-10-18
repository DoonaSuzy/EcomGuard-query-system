import { createApp } from 'vue'
import App from './App.vue'
import './style.css'

// 样式是「原样搬过来的」：web/src/style.css 与 static/css/style.css 内容一致，
// 一个选择器都没改——107 项 E2E 按这些类名定位元素，视觉也依赖它。
createApp(App).mount('#app')
