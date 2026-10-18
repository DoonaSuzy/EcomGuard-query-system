# AGENTS.md — 给 AI 助手 / 新人的速览

> **适用版本**: 2026-10-16 工作区版本 · **更新日期**: 2026-10-16
> 本文件是这个仓库的"读之前先知道"清单。它记录**代码里看不出来**的事实与已知坑，
> 不是 API 手册（那两份在 `docs/`）。改代码前请先读本文件。

---

## 1. 这是什么

WIPO 商标监控系统：自动调用 **WIPO BrandDB API** 检查商标法律状态与疑似抢注，发现异常后
通过邮件 / 钉钉 / 企业微信通知，每月定时运行一次。替代销售部"每月手动逐个复制商标名到
`branddb.wipo.int/en/similarname` 查询"的流程。

- **运行环境**：Python 3.13（本机实测 3.13.15）
- **技术栈**：FastAPI + SQLAlchemy(async) + MySQL + requests + pycryptodome
- **主入口**：`uvicorn app.main:app`（端口 8000）

> **2026-10-16 迁移**：主 Web 应用已从 Flask（`web_app.py`，端口 5000）迁到 FastAPI
> 分层架构（`app/`，端口 8000），数据存储从 JSON 文件换成 MySQL。
> **`web_app.py` 仍留在仓库里但已不是主入口**——它还在，是因为 CLI 的调度逻辑
> （`main.py`）尚未迁移，且它是迁移溯源的对照物。别再往里加功能。

## 2. 常用命令

```bash
# Web 应用（FastAPI）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# CLI（仍走旧栈：main.py + monitor.py）
python main.py run <清单文件>      # 跑一次检查
python main.py schedule <清单文件> # 前台常驻调度（终端一关就停，生产环境别用，见 §4.1）
python main.py init-config        # 生成配置文件
.\install_schedule_task.ps1 -Action DryRun    # 看会注册什么计划任务（不改系统）
.\install_schedule_task.ps1                   # 注册每月自动检查（推荐方式）
.\install_schedule_task.ps1 -Action Status    # 查看下次运行时间与上次结果

# 测试
python -m pytest tests -q         # E2E 测试（110 用例，需先装 playwright chromium）
python -m pytest tests_new -q     # FastAPI 栈契约 + 服务层单测（43 项）
python verify_fixes.py            # 68 项缺陷修复断言（不联网、不依赖 pytest）
python -m ruff check app tests_new cli.py verify_fixes.py   # 新栈 lint
```

## 3. 目录地图（**易混淆点在这里**）

```
python_ver/
├── app/                        # ← FastAPI 分层架构（主入口，端口 8000）
│   ├── main.py                 # create_app() 工厂 + lifespan
│   ├── views.py                # 前端托管（web/dist 优先，templates/ 兜底）
│   ├── api/v1/                 # 路由层：trademarks / check / reports / io / health
│   ├── core/                   # config / database / deps / exceptions / middleware / response / logging
│   ├── models/                 # orm.py（Trademark/Report/CheckRun）+ schemas.py
│   ├── repositories/           # 数据访问层
│   └── services/               # 业务编排层
├── web/                        # Vue 3 + Vite 前端（构建产物 web/dist）
├── templates/ + static/        # 旧原生前端（Vue 版不可用时的兜底）
├── main.py                     # CLI + 调度器（Scheduler._get_next_run）旧栈
├── monitor.py                  # MonitorEngine：编排单次检查、生成报告
├── wipo_client.py              # WipoApiClient：WIPO 私有 API + AES 解密
├── trademark_list.py           # TrademarkList：JSON/CSV/Excel 载入
├── notification.py             # NotificationService：邮件/钉钉/企微
├── cryptojs_aes.py             # 手写 AES（兼容 CryptoJS 的非常规密钥长度）
├── web_app.py                  # ⚠️ 旧 Flask 应用（端口 5000）— **已不对外服务**
├── tests/                      # E2E（110 项，Playwright，跑在 uvicorn 上）
├── tests_new/                  # FastAPI 栈契约 + 服务层单测（43 项）
└── docs/
    ├── api-schema.json         # ← 接口契约唯一来源（/api/schema 与文档站都读它）
    ├── docs_server.py          # ← 独立文档站程序（端口 5001）
    └── viewer/                 # ← 文档站自己的前端，与主应用前端无关
        ├── static/css/docs-viewer.css
        ├── static/js/docs-viewer.js
        └── templates/docs-viewer.html
```

> ⚠️ **历史坑（已修）**：`docs/` 下的前端资源原先直接叫 `app.js` / `style.css` / `index.html`，
> 与主应用同名但内容完全不同（文档站没有商标 CRUD 逻辑）。按文件名检索时会拿到错的那份，
> 并基于它推出错误结论。2026-10-16 已改名为 `docs-viewer.*` 并迁入 `docs/viewer/`；
> 若你在旧截图 / 旧笔记里看到 `docs/app.py` 或 `docs/static/js/app.js`，指的是现在这两处。

## 4. 数据落点

**FastAPI 栈（当前）** —— 全部在 MySQL 库 `wipo_trademark` 的三张表里：

| 表 | 内容 |
|------|------|
| `trademarks` | 商标清单。`id` 是 UUID 字符串（沿用旧 JSON 格式），brand_name 上有普通索引、(brand_name, applicant) 复合索引 |
| `reports` | 归档报告。仍沿用 `report_YYYYMMDD_HHMMSS.json` 文件名（unique 索引），`alerts_count` 是独立列 |
| `check_runs` | **单行表**（`id` 恒为 1）：running / completed / error / idle 四态 + result 载荷 |

> ⚠️ **不要把 check_runs 当多行表用**。它是 `id=1` 的单例，`CheckRunRepository.upsert()`
> 会无条件覆盖同一行。想加「历史运行记录」请新建表，别改这张。

连接串来自 `WIPO_DATABASE__URL`（默认 `mysql+aiomysql://root:@127.0.0.1:3306/wipo_trademark`）。
**测试环境一律用 `sqlite+aiosqlite:///` 覆盖**——路径必须写正斜杠，反斜杠会被当成转义符，
报 `unable to open database file`。

`config.json` 仍是**通知渠道配置**的唯一来源（含凭据，**勿提交**；模板 `config.example.json`）。
调度配置也在它的 `schedule` 段（`day_of_month` / `hour` / `minute`），默认每月 1 日 09:00。

**旧栈（仅 CLI 路径还在用）**：`data/user_trademarks.json`、`data/last_check_result.json`、
`data/last_check_error.txt`、`reports/*.json`。Web 栈已不再读写这些文件。

### 4.1 「每月一次」由什么承载（重要）

`main.py schedule` 靠 `while True: time.sleep(1)` 阻塞主线程维持 —— **终端一关就停**，
生产环境不要用它。真正让「每月一次」长期成立的是 **Windows 计划任务**：

```
install_schedule_task.ps1   # 注册/卸载/查询计划任务（-Action Install|Uninstall|Status|DryRun）
run_monthly_check.bat       # 由上面的脚本生成，计划任务实际调用的入口
```

- 调度时间从 `config.json` 的 `schedule` 段读取，与 `main.Scheduler.get_schedule_config`
  同一套默认值（每月 1 日 09:00）与取值范围；两边算法必须保持一致，改一处要改两处。
- 计划任务调用 `.bat` 而不是把整条命令行塞进 `schtasks /TR`，为的是避开 261 字符上限
  与多层引号转义。
- 注册方式是「仅当用户登录时运行」，不需要密码。若需「无论是否登录都运行」，请在
  「任务计划程序」图形界面勾选并补录密码 —— 脚本不接收明文密码。
- `install_schedule_task.ps1` **必须保存为 UTF-8 with BOM**：Windows PowerShell 5.1
  对无 BOM 的 UTF-8 会按 GBK 解码，导致中文与 `@"..."@` here-string 解析失败报
  `Unexpected token`。用 VSCode 另存时注意保持 BOM。
- Linux/macOS 对应物是 cron 或 systemd timer，本仓库未提供，需自行编写。

## 5. 已知坑（会主动误导，务必先读）

1. **`errors` 只有一种形态：错误详情列表**。数量一律看 `errorsCount`。历史上
   `get_summary()` 曾把数量写在 `errors` 键上（int），与 list 形态冲突、同一键两义，
   已于 2026-10-16 统一为 `errorsCount`（前端 `errorCount()` 仍兼容传入 int/数组）。
2. **`riskLevel` 的语义反直觉**：`high` = 品牌名**仅相似、非精确匹配**（子串包含）；
   `medium` = 精确匹配但申请人不同；`low` = 精确匹配且存在语言差异。后两者进
   `needsReviewCases`，只有 `high` 进 `squattingCases`。（源码 docstring 曾与实现相反，已修）
3. **WIPO 私有接口**：`wipo_client.py` 走的是 `https://api.branddb.wipo.int` 的非公开接口，
   响应 AES-ECB 加密、密钥前缀硬编码在源码里（`DECRYPT_KEY_PREFIX`）。接口或密钥随时可能失效，
   且该用法可能不符合 WIPO 服务条款——上线前需评估。
4. **检索窗口必须足够大**：`check_status` 曾硬编码 `rows=5`，对 NIKE/SONY 等常见品牌会漏报
   异常记录。现统一用 `DEFAULT_SEARCH_ROWS = 30`，三个调用点共用同一常量，不要单独写死。
5. **检查失败不能静默成"无异常"**：API 返回 `success=False` 时，`monitor._record_check_failure`
   会写入 `errors` 并把品牌记入 `failedBrands`。若删掉这段，失败的商标会被当成"检查通过"，
   报告显示异常数 0 / 错误数 0。
6. **GBK 控制台**：中文 Windows 控制台打印 `⚠`/`✗` 会抛 `UnicodeEncodeError`，且该异常会
   冒泡吞掉真实错误。所有控制台输出走 `monitor._safe_print`。
7. **`docs_server.py` 的 `/api/test` 是无鉴权转发**，可打到后端任意路径；且 `debug=True` +
   `host='0.0.0.0'`。仅限本机使用，别暴露到内网。

### 5.1 FastAPI 栈特有的坑（迁移后新增）

8. **参数校验失败是 422 不是 400**。旧 Flask 版手写 if 返回 400，新版交给 Pydantic
   返回 422。**文案必须逐字保留**（「商标名称不能为空」/「商标名称长度至少2个字符」/
   「商标名称长度不能超过200个字符」）——这三句在前端有两处来源（`static/js/app.js:150`
   的即时校验与服务端 422），改任何一处都会打断 E2E。
   服务端实现在 `app/core/exception_handlers.py` 的 `_first_validation_message()`：
   优先取自定义校验器 ctx 里的中文原文，而不是 Pydantic 的英文模板。
   **注意这两条的口径不同**：前端即时校验用的是「商标名称至少2个字符」，服务端
   Pydantic 校验器用的是「商标名称长度至少2个字符」（多「长度」二字）。
   E2E 断言的是前端那句（`tests/test_form.py:78`），API 测试断言的是服务端那句
   （`tests_new/test_api_basics.py:66`）——**别把两句统一成一句**，那会让其中一套测试红掉。
9. **`app.routes` 里没有路由清单**。FastAPI 的 `include_router` 保留子路由器的**引用**
   （`starlette.routing._IncludedRouter`），`path`/`methods` 全空。想拿真实路由要么
   下钻 `.router.routes`，要么用 `app.openapi()['paths']`。测试里统一走
   `app.api.v1.iter_api_routes()`。**坑的后果很隐蔽**：直接遍历 `app.routes` 会拿到
   空清单 → 契约比对变成"空集==空集"永远通过 → 漂移检测形同虚设。
10. **`RequestValidationError.errors()` 不能直接 JSON 序列化**。Pydantic 的 `ctx` 里
   会带真正的 `ValueError` 实例（自定义 field_validator 抛的那个），直接塞进
   `JSONResponse` 会报 `TypeError: Object of type ValueError is not JSON serializable`，
   于是本该 422 的请求变成 500。统一走 `_jsonable()` 降级。
11. **路由声明顺序有语义**：`DELETE /api/trademarks/batch` 必须声明在
   `DELETE /api/trademarks/{trademark_id}` **之前**，否则 "batch" 会被当成 id 匹配，
   批量删除返回 404。旧版靠 werkzeug 的静态段优先规则规避，FastAPI 没有这套规则。
12. **`GET /api/check/status` 没有 `success` 外壳**（沿用旧契约），前端
   `app.js` 直接读 `data.status` / `data.result`。别"顺手"给它套上外壳。
13. **前端托管（`app/views.py`）三个都不报错的坑**，2026-10-16 全部修掉并补了回归测试
    （`tests_new/test_frontend_hosting.py`，5 项，4 处变异全部 CAUGHT）：
    - **`Mount("/")` 必须注册在 `@app.get("/")` 之后**。Starlette 按注册顺序匹配，
      而 `Mount("/")` 能匹配包括根路径在内的任意路径（remainder 为空也算命中），
      mount 在前会让根路由永不执行，首页的 `cache-control: no-store` 静默失效。
    - **`except` 要捕 starlette 的 `HTTPException`，不是 fastapi 的**。
      `fastapi.HTTPException is starlette.exceptions.HTTPException` → **False**
      （两个独立类，`issubclass` 也不成立），而
      `starlette.staticfiles.StaticFiles.get_response` 抛的是 **starlette** 那个；
      写错则 SPA 回落整段永不执行。
    - **`StaticFiles` 传进来的 `path` 用的是 `os.sep`**，Windows 上是反斜杠
      （`api\\nonexistent`）。做 `/api/` 前缀判断前必须先 `path.replace("\\", "/")`，
      否则拼错 API 路径会被当 UI 路径回落成 200 HTML，前端 `.json()` 抛解析错、
      排查方向被带偏。回落只对 **404 且非 API 前缀**生效（`_is_api_path()`）。

## 6. 报告与检查结果字段

`MonitorEngine.run_check()` 返回（旧栈会写进 `data/last_check_result.json`）：

```
total          本次尝试检查的商标数
checked        成功完成检查的数量
errors         错误详情列表（每项 brandName / error，检查失败时含 stage）
errorsCount    错误数量（= len(errors)）
failedBrands   检查未完成的品牌名列表
alerts         告警列表（只含存在问题或抢注的商标）
hasIssues      是否存在任何告警
timestamp      检查完成时间
checkedAt      Web 层补记的完成时间
```

> **返回给前端时会被裁一刀**：`MonitorService._status_payload()` **刻意不含 `results`**。
> 旧版把整份结果塞进 `last_check_result.json`，前端 2 秒轮询会拖全量载荷。
> 想看每个商标的明细请走 `GET /api/reports/<filename>`。

归档报告 `save_report()` 写同一组字段（`checked` / `errorsCount` / `failedBrands` / `hasIssues`
于 2026-10-16 补齐，此前归档件比 API 少信息，无法区分"检查通过"与"根本没检查成功"），
额外含 `results`（全部逐商标结果）与 `alertsCount`（告警数量），但**不再重复存一份 `alerts`**
（告警就是 results 里有问题的那部分，实测占报告体积约 29%）。读取端 `GET /api/reports/<f>`
在缺 `alerts` 时用 `monitor.alerts_from_results()` 从 results 还原，旧报告自带的 `alerts` 原样
返回，两种格式对前端完全一致。

同时会写一份 `reports/report_<时间戳>.summary.json`（`save_summary()`）：只有统计与告警定位
（品牌名 / trademarkId / issues / 各类案例计数），没有 cases 明细，供看板与 AI 低 token 消费。
`GET /api/reports` **不列** summary 文件，避免同一次检查被算两次。

## 7. 测试

```bash
python -m pytest tests -q          # 110 用例，约 40s
python -m pytest tests_new -q      # FastAPI 栈契约 + 服务层单测（43 项，约 3s）
python verify_fixes.py             # 68 项缺陷修复断言（不联网、不依赖 pytest）
```

E2E 套件用 Playwright 驱动真实 Chromium；`tests/conftest.py` 会把配置指向临时
SQLite 库，**不会污染真实数据**。
首次使用需 `python -m playwright install chromium`（国内可用
`$env:PLAYWRIGHT_DOWNLOAD_HOST="https://npmmirror.com/mirrors/playwright"`）。

### 7.1 新增 E2E 用例的断言写法（硬性约定）

**一律用 `expect(...).to_have_*()`，禁止用 `.count() == N` 或 `inner_text() == X` 做立即断言。**

原因：页面数据全部由 async fetch 加载（`static/js/app.js:126` `loadTrademarks()`、
`web/src/App.vue` 的 `{{ state.trademarks.length }}` 初始值就是 0），
`page.reload()` 只保证 DOM 就绪，不保证 fetch 返回。用 `.count()` 查询的是**那一瞬间**的快照。

历史故障（2026-10-16 修）：`tests/test_navigation.py::test_trademark_count_displayed`
在 `reload()` 后立刻读 `#total-count`，连跑 5 次全量 E2E 有 1 次读到 `0`；
探针复刻其每一步实测 6 次得 `2@20ms | 2@14ms | 2@18ms | 2@14ms | 0@13ms | 2@13ms`
——失败那次 13ms 与正常 14–20ms **无差异**，靠加 `wait_for_timeout` 也修不干净。

正确与错误写法：

```python
# 错误 —— 不自动重试，加载慢一点就红
assert page.locator('#trademark-table-body tr').count() == 3

# 正确 —— Playwright 自动轮询直到满足或超时
expect(page.locator('#trademark-table-body tr')).to_have_count(3)

# 文本同理
expect(page.locator('#total-count')).to_have_text('2')
expect(page.locator('#empty-state')).to_contain_text('暂无商标数据')
```

`to_have_count(0)` 同样适用（断言「元素消失」），语义是「轮询期间始终为 0」。
本仓 E2E 已全量改造完毕，`.count()` 在 `tests/` 下已无业务断言残留。

## 8. 与工作区其余部分的关系

本仓库是独立 git 仓库；工作区根目录 `F:\projects\GO1` **不是** git 仓库。
现有两份 README，职责不同：

- `python_ver/README.md`（本仓库内）—— WIPO 系统的上手文档
- `F:\projects\GO1\README.md`（工作区根，**不在本仓库内**）—— 工作区总览与导航

原同级的 `pd/`（FastAPI 商品爬虫）、`crawler-power_trade-news/`（Selenium 新闻爬虫）
等**互不相干**的项目已于 2026-10-16 移出本工作区，改放 `F:\projects\GO1-excluded\`
（移出而非删除，可随时移回）。本仓库与它们**无任何代码级依赖**。
