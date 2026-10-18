import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { router } from './router'
import './styles/main.css'

const app = createApp(App)
app.use(createPinia())
// 路由放在 pinia 之后：某些守卫里要读 store
app.use(router)
app.mount('#app')
