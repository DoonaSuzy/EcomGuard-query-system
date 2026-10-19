# 聚统 · Aggregated Query & Inspection System

> 把两个原本独立部署的系统合成**一个服务入口**：一份 OpenAPI、一个进程、两个功能模块。
> 页面按子路径切换（`/wipo/`、`/credit/`），数据层各模块可独立选后端。

[![tests](https://img.shields.io/badge/tests-354%20passed-brightgreen)](#-开发)
[![CI](https://github.com/DoonaSuzy/Aggregated-query-and-inspection-system/actions/workflows/ci.yml/badge.svg)](https://github.com/DoonaSuzy/Aggregated-query-and-inspection-system/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-%3E%3D3.11-blue)](https://www.python.org/)
[![api](https://img.shields.io/badge/API-16%20paths%20%2F%2020%20ops-informational)](#-统一接口)

---

## 📖 项目概述

| 模块 | 子路径 | 做什么 | 原始仓库 |
|---|---|---|---|
| **WIPO 商标监控** | `/wipo/` | 商标法律状态与疑似抢注批量检查、月度报告归档、异常通知 | [WIPO_and_Brand](https://github.com/DoonaSuzy/WIPO_and_Brand) |
| **营业执照巡检** | `/credit/` | 信用中国企业登记状态批量查询（存续 / 注销 / 吊销）、CSV 交付、异常推送 | [Business-License](https://github.com/DoonaSuzy/Business-License) |

两个模块的完整源码放在 `modules/` 下，**保持各自仓库原样**（累计只改了 2 个文件，见[对模块源码的改动](#-对模块源码的改动)）。
聚合层是根目录的 `aggregator.py` + `credit_api_glue.py`，约 329 行。

**现状一览**（2026-10-19 实测）：

| 指标 | 值 |
|---|---:|
| Python 代码 | **14,634 非空行**（75 文件，口径见下注） |
| 前端源码 | **4,683 非空行**（38 文件，含 1 个生成物） |
| 自动化测试 | **354 项**（201 + 43 + 110，口径见下注） |
| 自建断言脚本 | **131 项**（68 + 63） |
| 聚合层端到端 | **37 项**（真 Chromium，全程 0 个 4xx/5xx） |
| 统一 API | **16 条唯一路径 / 20 个 operation**（零冲突） |
| 数据库表 | **5 张**（`trademarks` / `reports` / `check_runs` / `jobs` / `results`） |
| 前端产物 | **4 套**（每模块 2 套：独立运行 + 子路径托管） |

> **代码量口径**（可复现）：`modules/wipo/app` **2,546** + WIPO 顶层 10 个 `.py` **4,498**
> + WIPO `docs`/`scripts` **194** + WIPO 测试 **2,581** + `modules/credit` 顶层 8 个 `.py` **4,173**
> + credit `tools` **42** + 聚合层 **329** + `tools/` **271** = **14,634**。
> 计数只取非空行，排除 `__pycache__` / `node_modules` / `dist*` / `.venv`。
> 注意 WIPO 顶层含 `web_app.py`（**旧版 Flask 入口，644 行，已无任何代码引用**，只剩注释里提到）
> 与 `verify_fixes.py` + `verify_new_stack.py`（917 行，自建断言脚本）。

> **测试口径**（可复现）：三个套件**必须分开跑**，两个 `conftest.py` 同名会冲突
> （`pytest tests tests_new` 报 `ImportError: cannot import name 'FAKE_REPORT' from 'conftest'`）。
> `modules/credit/test_regression.py` **201** + `modules/wipo/tests_new` **43** + `modules/wipo/tests` **110** = **354**。
> 复现：`python -m pytest <路径> -q`（`-q` 的 summary 行能拿到计数，无需 junitxml）。
> 另有两套不经 pytest 的断言：`verify_fixes.py` 输出「合计 68 项断言，通过 68，失败 0」，
> `verify_new_stack.py` 输出「结果: 63 通过, 0 失败」。

> **API 口径**（可复现）：`aggregator.create_app().openapi()['paths']` = 16 键 / 20 个 operation
> （`/api/jobs`、`/api/trademarks` 各有 GET+POST，三个 `{id}` 路径各有两个方法）。
> 复现：`python -c "import aggregator; s=aggregator.create_app().openapi(); print(len(s['paths']), sum(map(len, s['paths'].values())))"`。
> 两个模块的路径**零交集**，这是能合进一个 app 的前提。

### 核心能力

- **单入口双模块**：`/` 是模块选择页，`/wipo/` 与 `/credit/` 各跑自己的 SPA，互不干扰
- **一份 OpenAPI**：16 条路径合并导出，前端可一次 `openapi-typescript` 生成全部类型
- **统一错误外壳**：credit 原本裸露的 `{"detail": …}` 自动变成 `{"success": false, "error": …, "request_id": …}`
- **请求级追踪**：一个 `X-Request-Id` 贯穿两个模块，响应头与响应体一致
- **数据层可选后端**：WIPO 走 MySQL（SQLAlchemy async + aiomysql），credit 走 SQLite 或 MySQL（同一个环境变量两种形态）
- **lifespan 显式串联**：WIPO 的建表/连接池与 credit 的 Store/worker 线程按序进出，互不抢跑

---

## 🛠 技术栈

> 版本号取自**已安装的包**（`import` 后读 `__version__`）与 `node_modules/*/package.json`，
> 不是照抄 requirements 的声明值；两者不一致处会单独注明。

### 聚合层

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | **3.13.15**（实测） | 声明 `>=3.11`（沿用 WIPO 的 `requires-python`） |
| FastAPI | 0.143.0 | 一个 app，`include_router` 合并两个模块 |
| uvicorn | 0.54.0 | **`--workers 1`** 硬约束，见下 |
| structlog | 26.1.0 | 沿用 WIPO 的结构化日志，聚合层复用同一套中间件 |

### 后端（两模块的并集）

| 组件 | 版本 | 用在哪 |
|---|---|---|
| SQLAlchemy[asyncio] | 2.1.4 | WIPO 的 ORM 与连接池 |
| aiomysql / PyMySQL | 0.3.2 / 1.2.3 | WIPO 异步驱动 / credit 同步驱动 |
| pydantic | 2.14.0 | 两模块的模型层（credit 声明 2.14.1，兼容） |
| camoufox | 0.4.11 | credit 的指纹伪装浏览器 |
| ddddocr + onnxruntime | 1.6.1 / 1.31.0 | credit 的验证码识别 |
| playwright | **1.62.0** | WIPO E2E + camoufox 的底层驱动（credit 声明 1.61.0） |
| requests / httpx | 2.34.2 / 0.28.1 | WIPO 外部调用 / 测试客户端 |

> **playwright 为什么取 1.62.0**：camoufox 对它的依赖声明是**无版本上限的裸依赖**（`requires('camoufox')` 输出 `playwright`），
> 实测 1.62.0 下 camoufox 正常工作；而 WIPO 的 E2E 要 1.62.0。取高版本能同时满足。
> **fastapi 为什么取 0.143.0**：credit 声明的是 0.118.3，但它的 202 项回归在 0.143.0 上全绿。

### 前端（两套独立产物）

| 模块 | 框架 | 构建 | 其他 |
|---|---|---|---|
| WIPO | Vue **3.5.44** | Vite **6.4.4** + `@vitejs/plugin-vue` 5.2.4 | 无路由（四个 section 靠 `.page.active` 显隐，`base: './'`） |
| credit | Vue **3.5.44** | Vite **7.3.7** + `@vitejs/plugin-vue` 6.0.10 | vue-router 4.6.4 / pinia 4.1.0 / openapi-fetch 0.17.0 / @vueuse/core 15.0.0 |

### 工具链

| 工具 | 版本 | 用途 |
|---|---|---|
| Node / npm | **v26.7.0 / 11.19.0** | 前端构建 |
| pytest | 9.1.1 | 三个套件的运行器 |
| ruff | 0.17.0 | lint（**各模块用自己的配置跑**，见[质量门禁](#-质量门禁)） |
| openapi-typescript | 7.13.0 | credit 的 `src/types.d.ts` 生成（568 行，生成物） |

---

## 🏗 架构

```
浏览器
  │
  ├─ /              壳页（两个模块入口卡片）
  ├─ /wipo/…        WIPO 前端（modules/wipo/web/dist-agg，--base=/wipo/）
  ├─ /credit/…      营业执照前端（modules/credit/frontend/dist-agg，--base=/credit/）
  │
  ├─ /api/trademarks、/api/check、/api/reports …   WIPO 模块接口（11 条）
  ├─ /api/jobs、/api/health …                     营业执照模块接口（5 条）
  └─ /health        聚合层健康检查
        │
   aggregator.py  ← 一个 FastAPI app，include 两个模块的 router
        ├── modules/wipo/app/…      （WIPO 的 FastAPI 应用，只取 router）
        └── modules/credit/api.py   （credit 的 router）
```

### 为什么是「单 app + `include_router`」，不是 `mount()` 两个子应用

三种形态都实测过：

| 形态 | 路由可用 | 主 app `openapi()` | 子 app lifespan |
|---|---|---|---|
| `root.mount("/wipo", wipo_app)` | ✅ | **空 `[]`**（mount 不合并子应用 paths） | **不执行**（`app.state` 全 MISSING） |
| 同上 + 手动 `lifespan_context` | ✅ | **空 `[]`** | 可修好（`['ROOT START','SUB START','SUB STOP','ROOT STOP']`） |
| `include_router(...)` | ✅ | **16 条合并** | 主 lifespan 内显式串联 |

选第三种：前端能用**一份** `openapi.json` 生成全部类型，两模块共享同一个 `app.state`、
同一套中间件（request_id、统一错误外壳、CORS 只配一次）。
代价是 credit 的 handler 必须改用 `request.app.state` —— 这是对它后端源码的**唯一**改动。

### 两个模块的 lifespan 必须显式组合

WIPO 的 lifespan 负责建表与释放连接池，credit 的负责建 `Store` 与启动 worker 线程。
`aggregator.py` 的 `combined_lifespan` 依次进入、逆序退出，实测关闭日志顺序为
`营业执照模块关闭，等待 worker 退出` → `关闭中，释放数据库连接池` → `数据库连接池已释放`。

### 统一错误外壳

聚合层注册了 WIPO 的异常处理器，credit 原本裸露的响应会被自动包装：

```json
{"success": false, "error": "作业不存在：nope", "request_id": "2cc56c67…", "code": 1000}
```

两模块的响应头都带 `X-Request-Id`，与响应体里的 `request_id` 一致；上游传入的
`X-Request-Id` 会原样透传。**已知缺口**：未处理异常（如 MySQL 不可达导致 500）走
Starlette 最外层 `ServerErrorMiddleware`，此时 `RequestContextMiddleware` 已退出，
`request_id` 为空（该缺陷在 WIPO 模块里同样存在，未修）。

---

## 📁 目录结构

```
aggregated-system/
├── aggregator.py              聚合入口：create_app()、壳页、SPAFiles、combined_lifespan
├── credit_api_glue.py         credit 的 lifespan 拆分进出 + router re-export
├── requirements.txt           两模块运行依赖的并集（58 项，57 项 `==` 锁定）
├── tools/
│   ├── build_frontends.py     构建两套前端产物（dist + dist-agg），带自检
│   └── accept_aggregated.py   端到端验收（起 uvicorn + 真 Chromium，37 项断言）
├── docs/
│   └── 单前端双后端整合评估.md   合并方案的设计依据（三形态实测、改造清单、风险）
└── modules/
    ├── wipo/                  完整 WIPO 模块（自带 tests/、tests_new/、web/、docs/）
    │   ├── app/               新栈 FastAPI（core / api/v1 / models / repositories / services）
    │   ├── web/               Vue 3 前端；dist/ 与 dist-agg/ 是两套产物
    │   └── docs/              该模块自己的 API 文档与项目结构说明
    └── credit/                完整 credit 模块（自带 test_regression.py、frontend/）
        ├── api.py             FastAPI（已改为 request.app.state）
        ├── credit_check.py    Camoufox + ddddocr 巡检核心
        └── frontend/          Vue 3 前端；dist/ 与 dist-agg/ 是两套产物
```

`modules/` 是**上游快照**，不是 submodule：上游修 bug 后需要手工同步。

---

## 🌐 访问地址

服务起来后（默认 `http://127.0.0.1:8000`），以下状态码为 2026-10-19 实测：

| 地址 | 状态 | 类型 |
|---|---:|---|
| `/` | 200 | `text/html`（壳页） |
| `/wipo/` | 200 | `text/html` |
| `/wipo` | **307** | 重定向到 `/wipo/` |
| `/credit/` | 200 | `text/html` |
| `/credit/jobs` | 200 | `text/html`（深链刷新可用） |
| `/credit/jobs/{id}` | 200 | `text/html`（深链刷新可用） |
| `/health` | 200 | `application/json` |
| `/api/health`、`/api/trademarks`、`/api/reports`、`/api/jobs`、`/api/schema` | 200 | `application/json` |
| `/api/nonexistent` | **404** | `application/json`（统一外壳，不回落首页） |

两个 307 是**必须**的：`mount("/credit", …)` 之后 `GET /credit`（无尾斜杠）会被
最后一层兜底接走，返回别的模块的首页。`aggregator.py` 在 mount 之前显式注册了这两个重定向。

---

## 🚀 环境配置

### 前置要求

| 依赖 | 要求 | 检查 |
|---|---|---|
| Python | ≥ 3.11（实测 3.13.15） | `python -V` |
| Node.js | ≥ 18（实测 v26.7.0） | `node -v` |
| MySQL | 8.x（**可选**，WIPO 默认要；credit 默认 SQLite） | `mysql --version` |

### 步骤 1 · 安装 Python 依赖

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt `
    -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 60 --retries 3
```

两模块的依赖差异很大（WIPO 要 SQLAlchemy + aiomysql，credit 要 camoufox + ddddocr），
**推荐共用一个 venv**；`requirements.txt` 头部已写清三处版本冲突的取舍理由。

### 步骤 2 · 装两个浏览器（各自独立）

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium   # WIPO 的 E2E 用
.\.venv\Scripts\python.exe -m camoufox fetch                # credit 的巡检用
```

两者互不影响：WIPO 用 playwright 自带的 Chromium，credit 用 camoufox 自带的 Firefox。

### 步骤 3 · 构建前端（两套产物，别只建一套）

```powershell
python tools\build_frontends.py
```

| 产物 | base | 用途 |
|---|---|---|
| `modules/*/.../dist/` | 默认（`/` 或 `./`） | 模块**独立运行**与独立跑回归 |
| `modules/*/.../dist-agg/` | `/wipo/`、`/credit/` | 被本聚合系统挂在子路径下 |

**为什么必须两套**：Vite 的动态 chunk 引用是相对路径（`import("./OverviewView-x.js")`），
浏览器按**模块 URL** 解析 —— 既不是文档 URL（`<base href>` 改不了它），
也不是托管层能改写的（chunk 名已进主 bundle）。所以子路径只能在**构建期**写进产物。
而模块独立运行时必须用默认 base，否则资源 404、它自己的 E2E 全红。两个约束互斥，只能各建一套。

`dist-agg/` 已在 `.gitignore` 里，不入库 —— **部署前必须跑一次构建**。

### 步骤 4 · 配置

各模块读自己的配置，位置不变：

| 模块 | 配置文件 | 关键项 |
|---|---|---|
| WIPO | `modules/wipo/config.json` | SMTP、钉钉/企微 webhook |
| credit | `modules/credit/.env` | `SMTP_*`、`MAIL_TO_BUSINESS`、`MAIL_TO_MANAGER` |

两份配置文件都被各自 `.gitignore` 排除，仓库里只有 `.env.example` / 空模板。

### 步骤 5 · 数据库

| 模块 | 环境变量 | 默认 | MySQL 写法 |
|---|---|---|---|
| WIPO | `WIPO_DATABASE__URL` | `mysql+aiomysql://root:@127.0.0.1:3306/wipo_trademark` | 本来就是 MySQL |
| credit | `CREDIT_DB` | `data/jobs.db`（SQLite） | `mysql://root:pw@127.0.0.1:3306/credit_check` |

`CREDIT_DB` 靠**有没有 `://`** 区分后端：普通路径 → SQLite，连接串 → MySQL。
同一个变量两种形态，部署时只改一处；默认仍是 SQLite，所以现有测试（都设
`CREDIT_DB=<tmp>/jobs.db`）无需改动。

**同实例分库**（推荐，两库互不影响）：

```sql
CREATE DATABASE wipo_trademark CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE credit_check   CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

```powershell
$env:WIPO_DATABASE__URL = 'mysql+aiomysql://root:pw@127.0.0.1:3306/wipo_trademark?charset=utf8mb4'
$env:CREDIT_DB          = 'mysql://root:pw@127.0.0.1:3306/credit_check'
python aggregator.py
```

**同库分表**也成立（两模块表名零交集：WIPO 是 `trademarks`/`reports`/`check_runs`，
credit 是 `jobs`/`results`），把 `CREDIT_DB` 指向 `wipo_trademark` 即可 ——
但两套 DDL 混在一个库里，出问题时两边一起查，一般不建议。

建表**自动完成**：WIPO 在 lifespan 里 `create_all`，credit 在 `Store.__init__` 里
`CREATE TABLE IF NOT EXISTS`，都幂等，不需要手工跑 SQL。两模块都**没有**接 Alembic
（`alembic` 虽在依赖里，但无 `alembic.ini`、无 `versions/`）—— 改列时要手工 `ALTER` 或删表重建。

> credit 的连接是「每次操作开一条」，没有连接池。作业量是「一天几十条」的批处理，
> 不是高并发在线服务，连接池的收益抵不上它带来的状态问题。

### ⚠️ 易踩的坑

| 现象 | 原因 | 处理 |
|---|---|---|
| 打开页面全白、控制台 `404 /assets/xxx.js` | 用了默认 base 的 `dist/` 去挂子路径 | 跑 `tools/build_frontends.py` 生成 `dist-agg/` |
| `GET /credit` 打开的是 WIPO 首页 | 少了尾斜杠重定向 | 用 `/credit/`，或确认 `aggregator.py` 的两个 307 还在 |
| 深链刷新 404 | SPA 兜底没挂上（`dist-agg/index.html` 不存在） | 先构建；`aggregator.py` 会打「未找到前端产物」日志 |
| `Unexpected token '<'` | 静态资源缺失时错误地回落了 index.html | `SPAFiles` 已对「带扩展名」的路径直接 404 |
| 作业一直「排队中」不动 | 多进程启动，worker 队列在另一个进程里 | `--workers 1` |
| WIPO 接口 500、`/health` 503 | MySQL 连不上（`dependencies.database: down`） | 起 MySQL，或把 `WIPO_DATABASE__URL` 指到可达实例 |
| `pytest tests tests_new` 报 `ImportError: FAKE_REPORT` | 两个 `conftest.py` 同名冲突 | 分开跑 |

---

## ▶️ 运行

```powershell
python aggregator.py
# 或
.\.venv\Scripts\python.exe -m uvicorn aggregator:app --host 127.0.0.1 --port 8000 --workers 1
```

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `AGG_HOST` | `127.0.0.1` | 监听地址 |
| `AGG_PORT` | `8000` | 监听端口 |
| `WIPO_DIST` | `modules/wipo/web/dist-agg` | 子路径产物目录 |
| `CREDIT_DIST` | `modules/credit/frontend/dist-agg` | 子路径产物目录 |
| `WIPO_REPORTS_DIR` | `modules/wipo/reports` | 未设时由 `aggregator.py` 兜底设置 |

> **`--workers` 必须为 1**。credit 的作业队列与 Camoufox 浏览器都在进程内，
> WIPO 的检查状态是 `check_runs` 单行状态机 —— 多进程会让两者都错乱。

---

## 🔌 统一接口

16 条唯一路径 / 20 个 operation，两模块零冲突：

| 方法 | 路径 | 模块 |
|---|---|---|
| `GET` `POST` | `/api/trademarks` | WIPO |
| `DELETE` | `/api/trademarks/batch` | WIPO |
| `PUT` `DELETE` | `/api/trademarks/{trademark_id}` | WIPO |
| `POST` | `/api/check` | WIPO |
| `GET` | `/api/check/status` | WIPO |
| `GET` | `/api/reports` | WIPO |
| `GET` | `/api/reports/{filename}` | WIPO |
| `GET` `POST` | `/api/export` / `/api/import` | WIPO |
| `GET` | `/api/schema` | WIPO |
| `GET` `POST` | `/api/jobs` | credit |
| `GET` `DELETE` | `/api/jobs/{job_id}` | credit |
| `GET` | `/api/jobs/{job_id}/results` | credit |
| `GET` | `/api/jobs/{job_id}/csv` | credit |
| `GET` | `/api/health` | credit |
| `GET` | `/health` | 聚合层（WIPO 的三态健康检查） |

`/api/check/status` 刻意**不带** `success` 外壳（credit 的三个接口同理沿用自己的契约）——
这是为了不推翻两个模块已有的前端契约与回归断言。

---

## 🔧 对模块源码的改动

聚合系统**只改了两个文件**，都记在这里方便回溯。

### 1. `modules/credit/api.py`：`app.state` → `request.app.state`

10 处引用（分布在 7 个 handler），每个 handler 加 `request: Request` 参数。

原因：credit 原本是独立 app，handler 直接读模块级 `app.state.store`。
被 `include_router` 进聚合 app 后，模块级 `app.state` 指向的仍是**原来那个 app**，
而 lifespan 赋值的是新 app → `AttributeError: 'State' object has no attribute 'store'`。

改动后 credit **独立运行行为不变**（自己的 lifespan 仍给 `credit_api.app.state` 赋值），
它的 202 项回归全部通过。

### 2. `modules/credit/frontend/src/router/index.ts`：路由 base 支持运行时覆盖

```ts
const ENV_BASE: string = import.meta.env.BASE_URL
const ROUTER_BASE: string = window.__CREDIT_BASE__ ?? (ENV_BASE.startsWith('/') ? ENV_BASE : '/')
export const router = createRouter({ history: createWebHistory(ROUTER_BASE), routes, ... })
```

产物按默认 base 构建（服务独立部署），聚合层在返回 index.html 时注入
`<script>window.__CREDIT_BASE__="/credit/"</script>`。这样**一份源码服务两种部署**，
不需要为路由 base 维护两套构建配置。

WIPO 前端**零改动**：它没有 vue-router，四个 section 靠 `.page.active` 显隐，
与 URL 无关，所以子路径下天然工作。

---

## 🧪 开发

### 质量门禁

```powershell
# 1. 聚合层端到端（起 uvicorn + 真实 Chromium，37 项断言）
python tools\accept_aggregated.py

# 2. 三个回归套件（必须分开跑，conftest 同名会冲突）
cd modules\wipo    ; python -m pytest tests -q               # 110 项 E2E
cd modules\wipo    ; python -m pytest tests_new -q           # 43 项契约
cd modules\credit  ; python -m pytest test_regression.py -q   # 202 项

# 3. 自建断言（不经 pytest）
cd modules\wipo    ; python verify_fixes.py                  # 68 项
cd modules\wipo    ; python verify_new_stack.py               # 63 项

# 4. lint
cd modules\wipo    ; ruff check app tests tests_new cli.py verify_fixes.py verify_new_stack.py
cd modules\credit  ; python tools\run_ci_lint.py               # UTF-8 + PS1 BOM 两项检查
```

`tools\accept_aggregated.py` 覆盖：壳页两个入口、两个子路径、**深链刷新**、
静态资源不串台、WIPO 新增商标 → 切页 → 表格计数、credit 提交作业 → 跳详情 →
详情页刷新、跨模块跳转，并断言**全程 0 个 4xx/5xx**。

### lint 的正确口径（别照抄「全绿」）

| 范围 | 命令 | 结果 |
|---|---|---|
| WIPO（用自带 `pyproject.toml` 配置） | `ruff check app tests tests_new cli.py verify_fixes.py verify_new_stack.py` | **All checks passed** |
| credit | `python tools\run_ci_lint.py` | 两项 `rc = 0`（它**没有** ruff 配置，裸跑有 85 项上游既有告警） |
| 聚合层 | `ruff check aggregator.py credit_api_glue.py tools` | **15 项未清**（13 fixable：UP009×4、RUF100×6、F401、I001、RUF022、BLE001、PLW1510） |

仓库根**没有统一 ruff 配置**，所以 `ruff check .` 会报 322 项（绝大多数是模块里的上游告警）。
正确做法是「各模块用各自 CI 同款命令分别跑」——聚合层这 15 项是待清理项，不是已通过项。

### 端到端验收为什么不用 pytest

`accept_aggregated.py` 要起独立 uvicorn + 真实 Chromium + 真实作业提交，跑一轮约 2 分钟，
不适合塞进 CI 的每次 push。它现在是一个独立脚本，手动或发布前跑。

### CI（GitHub Actions，`.github/workflows/ci.yml`）

三个 job，按「先快后慢」排列：

| Job | 内容 | 矩阵 |
|---|---|---|
| `lint` | 聚合层 + WIPO 全源码 ruff（零容忍）；credit 测试文件 `--exit-zero` 出报告（19 项上游既有告警，只守「不再新增」） | 单版本 |
| `tests` | credit 201 + WIPO 契约 44 + verify 68/63（**三套分开跑**，conftest 同名冲突） | Python 3.12 / 3.13（3.11 不进：`numpy==2.5.4` 声明 `Requires-Python>=3.12`，首跑实测装不上） |
| `e2e` | 构建两套前端产物（`build_frontends.py`，node_modules 缺失自动 `npm ci`）→ WIPO E2E 110 → 聚合验收 37（真 Chromium） | Python 3.13 |

CI 与本地命令一一对应，口径见上面「质量门禁」。三个注意点：

- **`requirements.txt` 是安装的唯一入口**：它是三模块依赖并集，版本组合已实测互容
  （含 `aiosqlite`，WIPO 的 conftest/verify 覆盖 SQLite 时需要）。
- **E2E job 只装 Playwright chromium**：credit 的 camoufox firefox 只在真实巡检时
  才会启动，聚合验收提交的作业允许 failed（脚本注释写明，断言只验前端链路）。
  依赖在 Linux 的可装性已核验：`ddddocr` 是 universal wheel、`onnxruntime` /
  `opencv-python` 均有 manylinux x86_64 wheel。
- **首跑修过的四类环境差异**（都属「Windows 测不出、Linux 才暴露」）：
  ①`tools/build_frontends.py` 有 shebang 但无可执行位 → `git update-index --chmod=+x`
  （Windows 文件系统无执行位概念，本地永远测不出 EXE001）；②`numpy==2.5.4` 的
  `Requires-Python>=3.12` → 矩阵去掉 3.11；③credit 托管守卫无 dist 时会误红 →
  改显式 `xfail`（`require_dist()`）；④`healthcheck` 的 venv/deps 自检按设计会
  在系统解释器上 FAIL → `run_checks` 支持探针注入（`venv_prefix` /
  `venv_base_prefix` / `deps_finder`），测试注入构造环境、逻辑归逻辑。

---

## 📊 项目状态

### ✅ 已完成

| 能力 | 状态 |
|---|---|
| 单入口 + 两个模块子路径 | ✅ 壳页 2 个入口，深链刷新可用 |
| 后端 router 合并 | ✅ 16 条路径 / 20 个 operation，零冲突 |
| lifespan 串联 | ✅ 顺序正确，关闭逆序 |
| 统一错误外壳 | ✅ credit 的 `{"detail"}` 自动包装 |
| 请求级 request_id | ✅ 响应头与体一致，上游可透传 |
| 两套前端产物 | ✅ `dist` + `dist-agg`，构建脚本带自检 |
| 数据库分库 | ✅ 真 MySQL 8.4.9 端到端跑通（WIPO + credit 各一库） |
| 数据库分表 | ✅ 同库 5 表也跑通（表名零交集） |
| credit 支持 MySQL | ✅ 同一个 `CREDIT_DB` 变量两形态，202 项回归覆盖 |
| 共用 venv | ✅ fastapi 0.143.0 + playwright 1.62.0 + camoufox 0.4.11 实测共存 |

### ⏳ 待开发

| 项 | 说明 |
|---|---|
| 聚合层 pytest 契约测试 | 现在只有 `accept_aggregated.py`（要浏览器，不适合 CI） |
| `modules/` 改 submodule | 现在手工同步上游 |
| 未处理异常的 request_id | 500 路径拿不到 id（WIPO 侧同样存在） |
| 根级 `LICENSE` / `.github/` | 仓库根没有许可证文件与 CI 工作流（模块各自的还在） |
| credit 上游的 lint 基线 | 85 项既有告警，接管前应定基线 |

### 📌 已知风险

- **单进程绑定**：`--workers 1`。要横向扩就先把 credit 的队列外置（Redis 之类），
  并把 WIPO 的 `check_runs` 单行状态机改成带实例标识的模型。
- **`dist-agg/` 不入库**：忘了构建就跑，整站 404（不是白屏 —— `SPAFiles` 会先确认 index.html 存在）。
- **`modules/` 是快照**：与上游仓库会产生漂移，目前没有自动检测。
- **行尾与编码**：`modules/credit/install_schedule_task.ps1` 必须 UTF-8 **带 BOM**，
  它生成的 `.bat` 必须 **无 BOM + 全 CRLF**；`modules/wipo/.gitattributes` 已声明 `* text=auto eol=lf`，
  `modules/credit` 没有 —— 新增 CRLF 敏感文件时要补一份。守这两条的是
  `test_regression.py:676` / `:759`。
- **全新克隆先构建再跑 credit 回归**：`test_legacy_unprefixed_paths_are_not_api` 依赖
  `frontend/dist` 存在（无 dist 时 SPA 兜底不挂载，`/health` 落到 FastAPI 默认 404 → 回 JSON）。
  这是上游既有行为；WIPO 侧同类测试是优雅 skip，两模块在这一点上不一致。
- **credit 的 Camoufox 依赖真实浏览器**：容器部署要先 `python -m camoufox fetch`。

---

## 📚 文档索引

| 文档 | 内容 |
|---|---|
| [`README.md`](README.md) | 本文：入口、架构、部署、门禁 |
| [`docs/单前端双后端整合评估.md`](docs/单前端双后端整合评估.md) | 合并方案的设计依据：三种整合形态实测、前端子路径的三个硬性前提、分库 vs 分表、342 项回归、文件级改造清单、风险与规避 |
| [`modules/wipo/README.md`](modules/wipo/README.md) | WIPO 模块：能力边界、环境变量、三种运行方式、测试策略、常见问题 |
| [`modules/wipo/docs/API_DOC.md`](modules/wipo/docs/API_DOC.md) | WIPO 接口契约 |
| [`modules/wipo/docs/PROJECT_STRUCTURE.md`](modules/wipo/docs/PROJECT_STRUCTURE.md) | WIPO 分层结构与依赖方向 |
| [`modules/credit/README-营业执照巡检.md`](modules/credit/README-营业执照巡检.md) | credit 模块：判定口径、验证码策略、定时巡检、合规边界、实测记录 |
| [`modules/credit/docs/API_DOC.md`](modules/credit/docs/API_DOC.md) | credit 接口契约 |

---

## 🤝 贡献

`modules/` 下的内容属于上游仓库，改动请提到对应仓库：

- WIPO → [DoonaSuzy/WIPO_and_Brand](https://github.com/DoonaSuzy/WIPO_and_Brand)
- credit → [DoonaSuzy/Business-License](https://github.com/DoonaSuzy/Business-License)

聚合层（`aggregator.py`、`credit_api_glue.py`、`tools/`）的改动请提在本仓库。
提 PR 前跑一遍[质量门禁](#-质量门禁)里的四条命令。

## 📄 许可

- `modules/wipo/`：**MIT**（见 [`modules/wipo/LICENSE`](modules/wipo/LICENSE)，`Copyright (c) 2026 DoonaSuzy`）
- `modules/credit/`：上游仓库**没有** LICENSE 文件
- 聚合层（`aggregator.py` / `credit_api_glue.py` / `tools/`）：跟随上游，**尚未单独声明许可**

> ⚠️ 仓库根目前没有 `LICENSE`。若要对外发布，建议在根补一份 MIT（与 WIPO 一致），
> 并确认对 credit 模块的授权范围。
