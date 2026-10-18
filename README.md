# 聚统 · Aggregated Query & Inspection System

把两个独立系统合成**一个入口、一套接口、两个功能模块**：

| 模块 | 子路径 | 做什么 | 原始仓库 |
|---|---|---|---|
| WIPO 商标监控 | `/wipo/` | 商标状态与抢注检查、报告归档 | [WIPO_and_Brand](https://github.com/DoonaSuzy/WIPO_and_Brand) |
| 营业执照巡检 | `/credit/` | 信用中国企业登记状态批量查询 | [Business-License](https://github.com/DoonaSuzy/Business-License) |

两个模块的源码放在 `modules/` 下，**保持各自仓库原样**（只对 credit 做了依赖注入改造，
见下文），聚合层是根目录的 `aggregator.py`。

---

## 快速开始

### 1. 环境

Python 3.13，依赖见 `requirements.txt`。两个模块的运行时依赖差异较大
（WIPO 要 SQLAlchemy 2.1 + aiomysql，credit 要 camoufox + ddddocr），
**推荐共用一个 venv**。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> 两个模块的前端依赖互相独立，`node_modules` 不在仓库里，需要各自装一次。

### 2. 构建前端（两套产物，别只建一套）

```powershell
python tools\build_frontends.py
```

这条命令会为每个模块构建**两套**产物：

| 产物 | base | 用途 |
|---|---|---|
| `modules/*/.../dist/` | 默认（`/` 或 `./`） | 模块**独立运行**、独立跑回归 |
| `modules/*/.../dist-agg/` | `/wipo/`、`/credit/` | 被本聚合系统挂在子路径下 |

**为什么必须两套**：Vite 的动态 chunk 引用是相对路径（`import("./OverviewView-x.js")`），
浏览器按**模块 URL** 解析 —— 既不是文档 URL（`<base href>` 无效），也不是托管层
能改写的（chunk 已进主 bundle）。所以子路径只能在**构建期**写进产物。
而模块独立运行时必须用默认 base，否则资源 404、它自己的 E2E 全红。
两个约束互斥，只能各建一套。

`dist-agg/` 已在 `.gitignore` 里，不入库 —— 部署前必须跑一次构建。

### 3. 启动

```powershell
python aggregator.py
# 或
.\.venv\Scripts\python.exe -m uvicorn aggregator:app --host 127.0.0.1 --port 8000 --workers 1
```

打开 <http://127.0.0.1:8000/> 是模块选择页。

> **`--workers` 必须为 1**。credit 的作业队列与 Camoufox 浏览器都在进程内，
> WIPO 的检查状态是 `check_runs` 单行状态机 —— 多进程会让两者都错乱。

### 4. 配置

各模块读自己的 `.env` / `config.json`，位置不变：

- `modules/wipo/config.json`（SMTP、推送渠道）
- `modules/credit/.env`（`SMTP_*`、`MAIL_TO_BUSINESS`、`MAIL_TO_MANAGER`）

数据库默认走 SQLite（开箱即用）；要换 MySQL 见各模块自己的 README。

---

## 架构

```
浏览器
  │
  ├─ /              壳页（模块选择）
  ├─ /wipo/…        WIPO 前端（dist-agg，base=/wipo/）
  ├─ /credit/…      营业执照前端（dist-agg，base=/credit/）
  │
  ├─ /api/trademarks、/api/check、/api/reports …   WIPO 模块接口
  ├─ /api/jobs、/api/health …                     营业执照模块接口
  └─ /health        聚合层健康检查
        │
   aggregator.py  ← 一个 FastAPI app，include 两个模块的 router
        ├── modules/wipo/app/…      （WIPO 的 FastAPI 应用，只取 router）
        └── modules/credit/api.py   （credit 的 router）
```

### 为什么合并成一个 app，而不是 `mount()` 两个子应用

实测三种形态：

| 形态 | 路由 | openapi | 子应用 lifespan |
|---|---|---|---|
| `root.mount("/wipo", wipo_app)` | 可用 | **空**（不合并子应用 paths） | **不执行** |
| 同上 + 手动 `lifespan_context` | 可用 | **空** | 可修好 |
| `include_router(...)` | 可用 | **16 条合并** | 主 lifespan 内手工组合 |

选第三种：前端能用一份 `openapi.json` 生成全部类型，两个模块共享同一个
`app.state` 与中间件栈（request_id、统一错误外壳、CORS 只配一次）。

代价是 credit 的 handler 必须用 `request.app.state` 而不是模块级 `app.state` ——
这是**唯一**对 credit 后端源码的改动，见下节。

### 统一错误外壳

聚合层注册了 WIPO 的异常处理器，credit 原本裸露的 `{"detail": "..."}` 会自动变成：

```json
{"success": false, "error": "作业不存在：nope", "request_id": "…", "code": 1000}
```

两个模块的响应头都带 `X-Request-Id`，与响应体里的 `request_id` 一致。

### 路由一览（16 条，零冲突）

| 模块 | 路径 |
|---|---|
| WIPO | `/api/trademarks`、`/api/trademarks/batch`、`/api/trademarks/{id}`、`/api/check`、`/api/check/status`、`/api/reports`、`/api/reports/{filename}`、`/api/export`、`/api/import`、`/api/schema`、`/health` |
| credit | `/api/health`、`/api/jobs`、`/api/jobs/{id}`、`/api/jobs/{id}/results`、`/api/jobs/{id}/csv` |

---

## 对模块源码的改动

聚合系统**只改了两处**，都记在这里方便回溯。

### 1. `modules/credit/api.py`：`app.state` → `request.app.state`

10 处引用、7 个 handler 加 `request: Request` 参数。

原因：credit 原本是独立 app，handler 直接读模块级 `app.state.store`。
被 `include_router` 进聚合 app 后，`app.state` 指向的还是**原来那个 app**，
而 lifespan 赋值的是新 app → `AttributeError: 'State' object has no attribute 'store'`。

改动后 credit **独立运行行为不变**（自己的 lifespan 仍给 `credit_api.app.state` 赋值），
它的 189 项回归全部通过。

### 2. `modules/credit/frontend/src/router/index.ts`：路由 base 可运行时覆盖

```ts
const ROUTER_BASE: string =
  window.__CREDIT_BASE__ ?? (ENV_BASE.startsWith('/') ? ENV_BASE : '/')
```

产物按默认 base 构建（独立部署），聚合层在返回 index.html 时注入
`window.__CREDIT_BASE__ = "/credit/"`。这样一份源码同时服务两种部署，
不需要为路由 base 维护两个构建配置。

---

## 验证

```powershell
# 聚合层端到端（起 uvicorn + 真实 Chromium，37 项）
python tools\accept_aggregated.py

# 两个模块各自的回归（必须分开跑：两个 tests 目录的 conftest 同名会冲突）
cd modules\wipo    ; python -m pytest tests -q               # 110 项 E2E
cd modules\wipo    ; python -m pytest tests_new -q           # 43 项契约
cd modules\credit  ; python -m pytest test_regression.py -q  # 189 项
```

`tools\accept_aggregated.py` 覆盖：壳页、两个子路径、**深链刷新**、静态资源不串台、
WIPO 新增商标 → 切页 → 表格计数、credit 提交作业 → 跳详情 → 详情页刷新、
跨模块跳转，并断言**全程 0 个 4xx/5xx**。

---

## 已知约束

- **单进程**：`--workers 1`，理由见上。
- **Camoufox 浏览器**：credit 首次跑巡检会启动真实浏览器，容器里需要
  `python -m camoufox fetch` 预下载。
- **`dist-agg/` 不入库**：部署流水线里必须跑 `tools/build_frontends.py`。
- **`modules/` 是上游快照**：上游修 bug 后需要手工同步（未来可改 submodule）。
- **全新克隆先构建再跑 credit 回归**：`modules/credit/test_regression.py` 里
  `test_legacy_unprefixed_paths_are_not_api` 依赖 `frontend/dist` 存在
  （没有 dist 时 SPA 兜底不挂载，`/health` 落到 FastAPI 默认 404 → 回 JSON）。
  这是上游既有行为，本节记录以免误判。WIPO 侧同类测试是优雅 skip，
  两个模块在这一点上不一致。

## 行尾与编码

仓库里有两处**字节级敏感**文件，改动时别让工具把行尾/编码改掉：

| 文件 | 要求 | 守它的测试 |
|---|---|---|
| `modules/credit/install_schedule_task.ps1` | UTF-8 **带 BOM** | `test_regression.py:676` |
| 它生成的 `run_credit_check*.bat` | UTF-8 **无 BOM** + 全 CRLF | `test_regression.py:759` |

`modules/wipo/.gitattributes` 已声明 `* text=auto eol=lf` + `*.ps1 eol=crlf`；
`modules/credit` 没有 `.gitattributes`，靠检出时的 `core.autocrlf` 决定。
在 `core.autocrlf=true` 的机器上全新克隆后实测仍 189 passed（PS1 的 BOM 与
行尾由 `.ps1` 已有字节保住），但**如果你新增了 CRLF 敏感的文本文件，
请在 `modules/credit` 下补一份 `.gitattributes`**。

## 目录

```
aggregated-system/
├── aggregator.py              聚合入口（一个 FastAPI app）
├── credit_api_glue.py         credit 的 lifespan 进出、router re-export
├── requirements.txt           两个模块运行依赖的并集
├── tools/
│   ├── build_frontends.py     构建两套前端产物
│   └── accept_aggregated.py   端到端验收（37 项）
└── modules/
    ├── wipo/                  完整 WIPO 模块（含自己的 tests/、docs/）
    └── credit/                完整 credit 模块（含自己的 test_regression.py）
```
