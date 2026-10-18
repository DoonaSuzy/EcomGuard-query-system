# 巡检控制台前端 · Vue 3 + Vite + TypeScript

营业执照状态巡检的 Web 控制台。**不单独部署**：构建产物由后端 `api.py` 直接托管，
与 `/api` 同源，不存在 CORS。

```
浏览器 ──► http://127.0.0.1:8000/
            ├─ /api/health  /api/jobs/...  → FastAPI 接口
            └─ /overview /jobs /jobs/<id>  → dist/index.html（vue-router history 模式）
```

接口统一在 **`/api` 前缀**下，页面路径 `/jobs`、`/jobs/<id>` 留给前端路由。
这两组不能共用路径：同一路径不可能既回 HTML 又回 JSON，混用会让用户刷新
「作业列表」时看到一段 JSON 而不是控制台。

---

## 快速开始

```bash
# 开发态（热更新，vite 自带 /api 代理）
cd frontend
npm install
npm run dev          # http://127.0.0.1:5173

# 生产态（后端托管）
npm run build        # 产出 frontend/dist/
# 然后启动后端即可访问 http://127.0.0.1:8000/
.venv\Scripts\python.exe -m uvicorn api:app --port 8000
```

> 后端找的是 `frontend/dist`，可用环境变量 `CREDIT_DIST` 指到别处。
> dist 不存在时后端静默降级成纯 API 服务，不影响任何接口。

---

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `npm run dev` | 开发服务器，端口见 `frontend/.env.example` |
| `npm run build` | 类型检查 + 构建（`vue-tsc -b && vite build`） |
| `npm run typecheck` | 只做类型检查，不产出 |
| `npm run gen:api` | 从 `frontend/openapi.json` 重新生成 `src/types.d.ts` |

**改完接口一定要重新生成类型**：

```bash
.venv\Scripts\python.exe -c "import api, json; print(json.dumps(api.app.openapi(), ensure_ascii=False, indent=2))" > frontend/openapi.json
cd frontend && npm run gen:api
```

`src/types.d.ts` 是提交进仓库的（`npm run gen:api` 产物）。这样后端改字段名、改路径，
前端 `npm run build` 先炸，而不是用户先炸。

---

## 目录结构

```
frontend/
├── index.html                Vite 入口 HTML（#app 挂载点）
├── vite.config.ts            别名 @、dev proxy、构建选项
├── openapi.json              后端导出的接口契约（gen:api 的输入）
├── .env.example              开发态变量模板
└── src/
    ├── main.ts               装配：pinia → router → mount
    ├── App.vue               外壳：侧栏 + 顶栏健康灯 + ToastHost + 错误边界
    ├── env.d.ts              VITE_* 变量的类型声明
    ├── router/index.ts       5 条路由（含 404 兜底）
    ├── api/client.ts         openapi-fetch 客户端 + 错误文案翻译
    ├── stores/jobs.ts        health / jobs / draft 三个 store
    ├── stores/toast.ts       轻提示队列
    ├── styles/main.css       设计变量 + 全局样式（294 行）
    ├── components/           EmptyState / ErrorBoundary / HealthPill /
    │                         ToastHost / VerdictBadge
    └── views/                Overview / Submit / Jobs / JobDetail / NotFound
```

---

## 几个关键设计取舍

### 1. 为什么用 openapi-fetch 而不是 axios

类型直接来自 `openapi.json` 生成的 `types.d.ts`：路径拼错、参数漏传、
响应字段名写错，在**编译期**就报错。后端改接口会先炸 `npm run build`，
而不是先炸用户。axios 拿到的是 `any`，等于没类型。

错误文案集中在 `api/client.ts:22 extractErrorMessage()`——把 FastAPI 的
`{detail}` 和 422 的 detail 数组翻成中文，视图层不各自拼字符串。

### 2. 为什么基址写 `/api` 而不是 `http://127.0.0.1:8000`

| 环境 | /api 怎么解析 |
| --- | --- |
| 开发态 | `vite.config.ts` 的 proxy 转发到 `VITE_API_TARGET` |
| 生产态 | 后端同源托管，`/api` 就是本机 8000 端口 |

两种环境浏览器看到的都是同源 `/api`。后端**没有配 CORS 中间件**，
写死绝对地址会被浏览器拦。真要直连后端联调，用 `VITE_API_BASE_URL` 覆盖。

### 3. 轮询分快慢两档

`App.vue` 有活跃作业（queued/running）时 5s 刷一次列表，空闲 30s；
`document.visibilityState === 'hidden'` 时直接不刷。
作业详情页的快轮询同理，只在作业终态前开启——50 家要跑十几分钟，
不能靠用户手动刷新看进度。

实现细节：`useIntervalFn` 的 `interval` 参数是 `MaybeRefOrGetter<number>`，
传 `computed` 本身即可，它内部的 `watch(interval)` 会在值变化时按新间隔 `resume()`。
若传 `pollMs.value`（瞬间求值的数字），间隔会永远停在创建那一刻的值——
这正是修复前「有作业也不加速」的根因。

### 4. 深链刷新不 404

vue-router 用 history 模式，`/jobs/<id>` 在服务器上没有对应文件。
后端 `api.py` 末尾注册了一条 `GET /{full_path:path}` 兜底回 `index.html`。
**它必须是最后注册的路由**——先注册它，`/api/health`、`/api/jobs` 全会被吞成 HTML。
这条顺序由 `test_regression.py::TestFrontendHosting` 的 7 条断言守着。

错误文案同理集中在 `api/client.ts` 的 `extractErrorMessage(err, status)`。
注意 openapi-fetch 的真实契约：`error` 是**裸响应体**（不是 axios 的 `{status, data}`），
状态码只在 `response.status` 上，网络层失败是 **throw** 而不是返回 `{error}`。
按 axios 形状取值会让 404/409/422 全部显示成「网络不可达」。

### 5. 错误边界 + Toast 而不是 alert

`ErrorBoundary.vue` 用 `onErrorCaptured` 接住渲染期异常，显示「出了点问题」
+ 重试按钮，避免整页白屏。`ToastHost` 做轻提示，不打断当前操作。
这两个都不阻塞主流程——巡检是要等十几分钟的后台任务，
弹窗挡着页面比报错本身更烦。

---

## 提交前自查

```bash
cd frontend
npm run typecheck   # 必须零错误
npm run build       # 必须成功
```

后端回归（含前端托管契约）：

```bash
.venv\Scripts\python.exe -m pytest test_regression.py -q
```

---

## 不入库的东西

`frontend/.gitignore`：`node_modules/`、`dist/`、`*.local`、`.vite/`。
`dist/` 是构建产物，随源码重新生成；后端在没有它时自动降级。
