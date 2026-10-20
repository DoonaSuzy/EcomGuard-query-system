<div align="center">

# WIPO 商标监控系统

**自动检查商标状态与被抢注情况，异常时主动告警，替代每月手动逐个查询的流程**

[![tests](https://img.shields.io/badge/tests-153%20passed-brightgreen)](https://github.com/DoonaSuzy/WIPO_and_Brand/actions)
[![python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/downloads/)
[![ruff](https://img.shields.io/badge/lint-ruff%200.17-blueviolet)](https://docs.astral.sh/ruff/)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![CI](https://github.com/DoonaSuzy/WIPO_and_Brand/actions/workflows/ci.yml/badge.svg)](https://github.com/DoonaSuzy/WIPO_and_Brand/actions/workflows/ci.yml)

</div>

---

## 口径

下面每个数字都附复现命令，避免文档与代码各说一套。

| 口径 | 数值 | 复现命令 |
|------|------|----------|
| 自动化测试 | 153 项全绿（E2E 110 + 契约 43） | `python -m pytest --junitxml=.pytest.xml` 后解析 `testsuite/@tests` |
| 缺陷回归断言 | 130 项（67 + 63） | `python verify_fixes.py` 与 `python verify_new_stack.py` 的末行计数 |
| 代码检查 | 0 error | `python -m ruff check app tests tests_new cli.py scripts verify_fixes.py verify_new_stack.py` |
| Python 代码量 | 339 KB / 40 文件 | `Get-ChildItem app -Recurse -Filter *.py` 加顶层 `.py` 汇总 |
| 前端代码量 | 145 KB / 20 文件 | `web/src`（Vue）+ `static` + `templates` |
| 测试代码量 | 115 KB / 20 文件 | `tests` 与 `tests_new` |
| git 跟踪文件 | 106 → 更多（含新增标准文件） | `git ls-files \| wc -l` |

> **注意**：`pytest -q` 的 summary 行在本机某些终端下抓不到计数，统计口径请用
> `--junitxml` 后读 XML，或直接看 summary 行。

---

## 目录

[项目概述](#项目概述) · [功能特性](#功能特性) · [技术栈](#技术栈) · [快速开始](#快速开始) ·
[环境配置](#环境配置) · [运行方式](#运行方式) · [测试](#测试) · [API](#api) ·
[常见问题](#常见问题) · [项目结构](#项目结构) · [维护说明](#维护说明) ·
[安全](#安全) · [贡献](#贡献) · [许可证](#许可证)

---

## 项目概述

销售部每月需手动把每个商标名逐个复制到
[WIPO Global Brand Database](https://branddb.wipo.int/en/similarname) 查询状态与抢注情况。
流程繁琐、容易遗漏，且「异常」与「疑似抢注」两种判断分散在不同人的脑子里。

本项目把这个流程自动化：

1. 定期（默认每月 1 日 09:00）调用 WIPO BrandDB 接口检查全部商标的法律状态
2. 从同名记录中识别「品牌名相似但申请人不同」的疑似抢注，并按风险分级
3. 发现异常或疑似抢注时，通过邮件 / 钉钉 / 企业微信告警（**同渠道的 N 条聚合为一条**）
4. 生成结构化报告供人工复核，同时导出紧凑摘要供看板或 AI 消费

### 能力边界（先说清楚）

- 判定依据只有**品牌名**与**申请人**，不含尼斯分类、图样比对、引证商标分析
- 抢注判定是启发式规则，不是法律意见；所有命中都应人工复核
- 依赖的 `api.branddb.wipo.int` 是**非公开接口**，随时可能改版或停止服务
- 本项目与 WIPO 无隶属或授权关系，详见 [SECURITY.md](SECURITY.md)

---

## 功能特性

| 功能 | 描述 |
|------|------|
| 商标管理 | 增删改查、批量导入导出（JSON / CSV / Excel，存 MySQL） |
| 状态检查 | 检查商标法律状态，命中异常集合即告警 |
| 抢注检测 | 品牌名匹配 + 申请人识别 + 语言差异，三级风险分级 |
| 告警通知 | 邮件（SMTP）、钉钉、企业微信；同渠道 N 条聚合为一条汇总 |
| 定时调度 | Windows 计划任务常驻（进程不常驻），或前台调试模式 |
| 报告 | 完整报告存库 + 紧凑摘要（体积为完整报告的 10.6%） |

### 异常状态集合

`wipo_client.ABNORMAL_STATUSES`，来自 180 条真实样本的状态词表归纳：

`Expired`（过期）、`Cancelled`（注销）、`Refused`（驳回）、`Abandoned`（放弃）、
`Withdrawn`（撤回）、`Not Registered`（未注册）、`Ended`（已终止）

> `Ended` 曾被漏掉：180 条样本中 38 条是 `Ended`，而旧版集合里没有它，
> 导致注册终止被静默判为正常。这是加进集合的直接原因。

### 抢注风险分级

与 `wipo_client.check_squatting` 的实现逐条对齐：

| 等级 | 判断条件 | 落入字段 |
|------|----------|----------|
| 高风险 | 品牌名仅相似、非精确匹配 | `squattingCases` |
| 中风险 | 品牌名精确匹配但申请人不同 | `needsReviewCases` |
| 低风险 | 品牌名精确匹配且双方含 CJK / 阿拉伯文 | `needsReviewCases` |

---

## 技术栈

| 层 | 选型 | 版本 | 为什么是它 |
|----|------|------|-----------|
| 语言 | Python | 3.11 – 3.13 | 下限由 `requires-python` 决定，CI 三版本验证 |
| Web 框架 | FastAPI | 0.143 | 单一后端框架，不用 Flask |
| ASGI | uvicorn[standard] | 0.54 | |
| 校验 / 配置 | Pydantic + pydantic-settings | 2.14 / 2.15 | 配置从 `config.json` 起步、env 覆盖 |
| ORM | SQLAlchemy 2.1 (async) + aiomysql | — | 异步 ORM，连接池带 pre-ping |
| 数据库 | MySQL | 8.4+ | |
| HTTP 客户端 | requests | 2.34 | 同步即可，检查本身是低频批任务 |
| 加密 | 手写 `cryptojs_aes.py` | — | 非标准 48 字节密钥，标准库无法直接处理 |
| 日志 | structlog | 26.1 | Windows GBK 控制台安全降级 |
| 前端 | Vue 3 + Vite | 3.5 / 6 | 旧原生版 `templates/` 保留作兜底 |
| 测试 | pytest + Playwright | 9.1 / 1.62 | E2E 走真实浏览器 |
| 检查 | ruff | 0.17 | |

**刻意不引入**：Redis、Celery、Prometheus、Grafana、Docker、Alembic、vue-router、
状态管理库。这个项目只有 6 个业务模块和一个每月跑一次的批任务，那些是过度设计。

---

## 快速开始

### 前置条件

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | ≥ 3.11（本机实测 3.13.15） | |
| MySQL | ≥ 8.4 | 测试不需要，测试自动切 SQLite |
| Node.js | ≥ 18 | 仅构建 Vue 前端时需要 |

```sql
CREATE DATABASE IF NOT EXISTS wipo_trademark
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 安装

```bash
git clone git@github.com:DoonaSuzy/WIPO_and_Brand.git
cd WIPO_and_Brand

python -m venv venv
venv\Scripts\activate                       # Linux/macOS: source venv/bin/activate

pip install -e ".[dev]"                     # 运行时 + 测试依赖
python -m playwright install chromium

# 构建前端（可选，跳过会静默降级到旧前端，见下方说明）
cd web && npm ci && npm run build && cd ..
```

### 首次运行

```bash
python cli.py db-init                       # 建表（lifespan 也会自动建，这是兜底）
python cli.py serve                         # 监听 0.0.0.0:8000
```

然后访问：

| 地址 | 内容 |
|------|------|
| http://localhost:8000 | Web 界面 |
| http://localhost:8000/docs | Swagger UI |
| http://localhost:8000/redoc | ReDoc |
| http://localhost:8000/health | 健康检查（含 DB / WIPO 探活） |

> **国内网络**：pip 可加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`；
> Playwright 下载失败先设 `$env:PLAYWRIGHT_DOWNLOAD_HOST="https://npmmirror.com/mirrors/playwright"`；
> npm 可加 `--registry https://registry.npmmirror.com`。

> **关于前端兜底**：`web/dist/` 与 `web/node_modules/` 按 `web/.gitignore` 不入库，
> 新克隆的仓库里没有 Vue 产物。此时 `app/views.py` 的 `_resolve_index()` 回落到仓库内
> 已跟踪的 `templates/index.html`（迁移前的原生版）——页面照常可用，但**跑的不是 Vue 版**。
> 确认当前托管哪一份：
>
> ```bash
> python -c "from app.views import _resolve_index; from pathlib import Path; print(_resolve_index(Path('.')))"
> # 期望 web/dist/index.html；若为 templates/index.html 则是旧版兜底
> ```

---

## 环境配置

配置有三层来源，优先级从高到低：**环境变量 > `config.json` > 代码默认值**。

### config.json

```bash
python cli.py init-config                   # 生成模板
```

```json
{
  "notification": {
    "channels": ["email", "dingtalk"],
    "email": {
      "smtp_server": "smtp.example.com",
      "smtp_port": 587,
      "sender": "monitor@example.com",
      "recipients": ["admin@example.com"],
      "username": "your_username",
      "password": "your_password"
    },
    "dingtalk": { "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=xxx" },
    "wework":    { "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx" }
  },
  "schedule": { "day_of_month": 1, "hour": 9, "minute": 0 }
}
```

> ⚠️ `config.json` 含明文 SMTP 凭据，已被 `.gitignore` 排除，**不要提交**。
> 以下划线开头的键（`"_comment_x"`）会被自动剔除，不会进配置对象。

### 环境变量（全部带 `WIPO_` 前缀）

| 变量 | 描述 | 默认值 |
|------|------|--------|
| `WIPO_HOST` | 监听地址 | `0.0.0.0` |
| `WIPO_PORT` | 监听端口 | `8000` |
| `WIPO_DEBUG` | 调试模式（热重载） | `false` |
| `WIPO_DATABASE__URL` | 数据库连接串 | `mysql+aiomysql://root:@127.0.0.1:3306/wipo_trademark?charset=utf8mb4` |
| `WIPO_TRADEMARKS_FILE` | CLI 侧商标清单 | `data/user_trademarks.json` |
| `WIPO_REPORTS_DIR` | CLI 侧报告目录 | `reports/` |
| `WIPO_DECRYPT_KEY_PREFIX` | WIPO 响应解密密钥前缀 | 内置回退值，见 [SECURITY.md](SECURITY.md) |

> 嵌套字段用**双下划线**分隔（`WIPO_DATABASE__URL` → `settings.database.url`），
> 这是 pydantic-settings 的 `env_nested_delimiter`。

---

## 运行方式

### 方式一：Web 应用（推荐日常使用）

```bash
python cli.py serve
# 或 python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`/health` 三态：

| 状态 | 码 | 含义 |
|------|----|------|
| `ok` | 200 | DB 与 WIPO 均可达 |
| `degraded` | 200 | DB 可用但 WIPO 不可达（刻意不返 503：接口暂时不通 ≠ 服务死了） |
| `down` | 503 | DB 不可用 |

### 方式二：命令行

```bash
python cli.py run trademarks.json -o reports/          # 单次检查
python cli.py run trademarks.json --no-squatting       # 仅查状态
python cli.py run trademarks.json --no-status          # 仅查抢注
python cli.py run trademarks.json -c config.json       # 指定配置
```

### 方式三：每月定时

**推荐注册 Windows 计划任务**（进程不常驻，重启后仍按计划触发）：

```powershell
.\install_schedule_task.ps1 -Action DryRun             # 先看不改
.\install_schedule_task.ps1                           # 注册
.\install_schedule_task.ps1 -Action Status            # 看下次运行时间
.\install_schedule_task.ps1 -Action Uninstall         # 卸载
```

脚本生成 `run_monthly_check.bat` 作为任务入口，注册形式为「仅当用户登录时运行」。

```bash
python cli.py schedule trademarks.json --day 1 --hour 9   # 前台常驻，仅调试用
```

> 时间的权威来源是 `config.json` 的 `schedule` 段；命令行参数优先级更高。

### 生产部署

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

> ⚠️ **worker 数必须为 1**：检查状态由 `check_runs` 单行表承载，多进程下
> 「哪个请求启动的检查」会错乱。横向扩展需换成 Redis，本项目刻意不引入。

---

## 测试

两套测试**不能在同一次 `pytest` 调用里同时收集**（各有 `conftest.py` 且都改 `sys.path`，
混跑会报 `ImportError: cannot import name 'FAKE_REPORT' from 'conftest'`）。

```bash
python -m pytest tests -q          # E2E，118 项，约 46 秒（真实 Chromium）
python -m pytest tests_new -q      # 契约 + 服务层，44 项，约 5 秒
```

Windows 可双击 `run_tests.bat`（自动检查依赖与 Chromium）。

### 写 E2E 用例：断言必须自动等待

E2E 里的数据全部由 async fetch 加载，`page.reload()` 只保证 DOM 就绪、不保证数据返回。
断言一律用 `expect()` 自动重试，**不要用 `.count()` 做立即查询**：

```python
# 错误 —— 加载慢一拍就红
assert page.locator('#trademark-table-body tr').count() == 3

# 正确
expect(page.locator('#trademark-table-body tr')).to_have_count(3)
expect(page.locator('#total-count')).to_have_text('2')
```

这套写法是 2026-10-16 修掉一类真实竞态后统一改造的（全量跑 5 次有 1 次
`test_trademark_count_displayed` 偶发读到 `0`）。新用例请沿用，详见
[CONTRIBUTING.md](CONTRIBUTING.md) 与 [AGENTS.md](AGENTS.md) §7.1。

### 缺陷回归断言

```bash
python verify_fixes.py             # 67 项，旧栈缺陷回归
python verify_new_stack.py         # 63 项，FastAPI 栈契约回归
```

这两个文件**不是覆盖率工具**——每一条对应一个修过的真实缺陷，改了监控逻辑请一并更新。

### 覆盖范围

| 文件 | 覆盖 |
|------|------|
| `test_check.py` / `test_check_options.py` | 检查流程、选项边界 |
| `test_form.py` / `test_form_validation.py` | 表单与校验 |
| `test_edit_flow.py` | 编辑流程 |
| `test_modal_interactions.py` | 模态框 |
| `test_batch_operations.py` | 批量操作 |
| `test_search_filter.py` | 搜索过滤 |
| `test_toast_notifications.py` | Toast |
| `test_import_export.py` | 导入导出（含 `[]` 与 0 字节两种空文件） |
| `test_reports.py` / `test_report_contract.py` | 报告页与字段契约 |
| `test_api_schema.py` | 接口契约三方一致（防路由漂移） |
| `tests_new/test_api_basics.py` | 新栈 HTTP 契约（文案与状态码逐字对齐旧版） |
| `tests_new/test_services.py` | 服务层业务规则 |
| `tests_new/test_frontend_hosting.py` | 前端托管（`no-store`、SPA 回落） |

测试产物在 `test-results/`（HTML 报告 + 失败截图），已被 `.gitignore` 忽略。

---

## API

完整契约以 [`docs/api-schema.json`](docs/api-schema.json) 为**单一来源**，
`GET /api/schema` 与 `tests_new/test_api_schema.py` 都从它读取，CI 有漂移检测。

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/api/trademarks` | 商标列表 |
| POST | `/api/trademarks` | 新增商标 |
| PUT | `/api/trademarks/{id}` | 更新商标 |
| DELETE | `/api/trademarks/{id}` | 删除商标 |
| DELETE | `/api/trademarks/batch` | 批量删除 |
| POST | `/api/check` | 启动检查 |
| GET | `/api/check/status` | 检查状态 |
| GET | `/api/reports` | 报告列表 |
| GET | `/api/reports/{filename}` | 报告详情 |
| GET | `/api/export` | 导出 |
| POST | `/api/import` | 导入 |
| GET | `/api/schema` | 接口契约 |
| GET | `/health` | 健康检查 |

### 响应外壳

```jsonc
// 成功
{"success": true,  "data": ..., "request_id": "..."}
// 失败
{"success": false, "error": ..., "request_id": "..."}
```

> 只有 `GET /api/check/status` 与 `GET /api/export` 不走外壳（前端直接读裸对象 / 触发下载）。
> 改这个外壳会一次性打断前端 4 处 `result.success` 判断与 118 项 E2E。

### 文档

- [Web API 文档](docs/API_DOCUMENTATION.md)
- [Python API / CLI 文档](docs/API_DOC.md)
- [项目结构说明](docs/PROJECT_STRUCTURE.md)
- [AGENTS.md](AGENTS.md) —— **新人/AI 先读这个**，记录代码里看不出来的坑

---

## 常见问题

**Q：解密报 `Padding is incorrect`**
密钥处理与 CryptoJS 不兼容，确认走 `cryptojs_aes.py` 的 `CryptoJSAES`。

**Q：API 返回 401**
未调用 `wipo_client.init()` 拿 `search_id`，`HashSearch` 头就没值。

**Q：端口被占用**
```bash
netstat -ano | findstr :8000
taskkill /PID <PID> /F
```
常见假失败：上一次 uvicorn 没被杀干净（只杀了外层包装进程）。
用 `Get-NetTCPConnection -LocalPort 8000 -State Listen` 查 `OwningProcess`。

**Q：报告显示「异常数 0 / 错误数 0」但我确信有商标过期了**
先看 `failedBrands` 与 `errorsCount`。非空说明**本次检查没跑完**（认证失败、接口改版），
此时的「0 异常」不代表商标正常。

**Q：422 报错是「参数不合法: 商标名称」**
`RequestValidationError` 被压成一句中文。空值 / 超长 / 过短分别给「商标名称不能为空」/
「商标名称长度不能超过200个字符」/「商标名称长度至少2个字符」。若看到 `[object Object]`，
说明 `_jsonable()` 降级失效了，那是回归。

**Q：定时任务不执行**
① 计划任务方式：`.\install_schedule_task.ps1 -Action Status` 看状态是否为 `Ready`；
② `cli.py schedule` 是前台进程，终端关了就停——生产请用计划任务；
③ 查计划任务的「上次运行结果」返回码。

**Q：启动报「数据库初始化失败」**
MySQL 没起或连接串错。服务**不会退出**，`/health` 会返 503 + `"database": "down"`，
真实异常在启动日志里。

---

## 项目结构

```
WIPO_and_Brand/
├── app/                          # FastAPI 应用（Web 层）
│   ├── main.py                   #   应用工厂 + lifespan + uvicorn 入口
│   ├── views.py                  #   静态资源挂载 + 首页
│   ├── core/                     #   配置/上下文/响应/异常/中间件/日志/数据库/依赖
│   ├── api/v1/                   #   路由（trademarks / check / reports / io / health）
│   ├── models/                   #   Pydantic schemas + SQLAlchemy ORM
│   ├── repositories/             #   数据访问（SQL 只出现在这里）
│   └── services/                 #   业务编排
├── web/                          # Vue 3 + Vite 前端（产物 dist/ 不入库）
├── static/ templates/            # 旧原生前端（兜底）
├── monitor.py                    # 监控引擎（核心业务逻辑）
├── wipo_client.py                # WIPO API 客户端 + AES 解密
├── notification.py               # 通知（邮件/钉钉/企微）
├── trademark_list.py             # CLI 侧商标清单
├── cryptojs_aes.py               # CryptoJS 兼容 AES
├── main.py cli.py                # CLI 入口
├── install_schedule_task.ps1     # 注册每月计划任务
├── tests/ tests_new/             # E2E 118 项 / 契约 44 项
├── docs/                         # API 文档 + 契约 schema + 文档站
├── scripts/                      # 依赖一致性检查等辅助脚本
├── verify_fixes.py verify_new_stack.py   # 缺陷回归断言
├── AGENTS.md                     # 已知坑清单（先读它）
├── pyproject.toml                # 元数据 + ruff/pytest 配置
├── requirements*.txt             # 依赖安装快照（与 pyproject 双源，CI 校验）
└── .github/                      # CI + Issue / PR 模板
```

### 数据落点

| 内容 | 位置 |
|------|------|
| 商标清单 / 检查报告 | MySQL 库 `wipo_trademark`（表 `trademarks` / `reports` / `check_runs`） |
| 通知与调度配置 | `config.json`（不入库，需单独备份） |
| CLI 侧清单与报告 | `data/`、`reports/` |

---

## 维护说明

### 日志

- 控制台（structlog，Windows GBK 控制台自动降级，不会因 `⚠` 崩掉）
- `reports/report_*.json` —— 完整报告
- `reports/report_*.summary.json` —— 紧凑摘要（10.6% 体积，供看板/AI 低 token 消费）

### 备份

```bash
mysqldump -u root wipo_trademark > backup_$(date +%Y%m%d).sql
```

`config.json` 含凭据，需单独备份且不入库。

### 改依赖

`pyproject.toml` 与 `requirements*.txt` **两处都要改**，改完跑：

```bash
python scripts/check_deps_consistency.py
```

### 提交前门禁

```bash
python -m ruff check app tests tests_new cli.py scripts verify_fixes.py verify_new_stack.py
python scripts/check_deps_consistency.py
python -m pytest tests_new -q
python -m pytest tests -q
```

CI 在 Python 3.11 / 3.12 / 3.13 三个版本上跑同样的检查。

---

## 安全

涉及凭据、密钥或远程风险的问题，请通过
[GitHub Security Advisory](https://github.com/DoonaSuzy/WIPO_and_Brand/security/advisories/new)
**私下**报告，不要开公开 issue。详见 [SECURITY.md](SECURITY.md)。

---

## 贡献

欢迎 PR。提交前请读 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [AGENTS.md](AGENTS.md)——后者记录了
「改哪里会一次性打断多少东西」这类关键约束。

本项目遵循 [Conventional Commits](https://www.conventionalcommits.org/)。

---

## 许可证

[MIT](LICENSE) © 2026 DoonaSuzy

---

<div align="center">

<sub>本项目与 WIPO 无隶属或授权关系；调用的 BrandDB 接口非公开，随时可能变更。</sub>

</div>
