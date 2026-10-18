# Business License · 营业执照状态自动巡检

批量核查一批企业营业执照的工商登记状态（存续 / 注销 / 吊销），异常自动提醒；每日报业务人员、每月报主管，并顺带自检「巡检服务自己还能不能跑」。

数据源：国家企业信用信息公示系统「信用中国」信用信息查询（`www.creditchina.gov.cn`）。

## 它解决什么

销售部原先手工把公司名 / 统一社会信用代码逐个粘进网站查一遍。本项目把这件事做成一条可定时执行的流水线：

```
名单.txt  →  服务自检  →  浏览器过验证码查站  →  CSV（含判定）  →  有异常才推送
```

## 快速开始

```powershell
cd F:\projects\GO1\reverse-records

# 0) 用本目录自带的独立 venv，不要用系统 Python
.\.venv\Scripts\Activate.ps1

# 1) 准备名单：一行一家企业全称，# 开头为注释
Copy-Item batch8.txt companies.txt

# 2) 批量查询
python credit_check.py companies.txt -o result.csv

# 3) 异常推送（有 alert / notfound / unknown 才推）
python notify.py result.csv --webhook "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
```

想全自动（每日 + 每月双节奏 + 邮件提醒）→ 见主文档 §6。

想用网页控制台（提交作业 / 看进度 / 下载结果）→ 见 [frontend/README.md](frontend/README.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [README-营业执照巡检.md](README-营业执照巡检.md) | **主文档**：用法、判定口径、验证码实测、风控策略、定时巡检、QQ 邮箱配置、已知限制 |
| [frontend/README.md](frontend/README.md) | Web 控制台（Vue 3 + Vite + TS）：开发/构建/部署，构建产物由后端同源托管 |
| [docs/API_DOC.md](docs/API_DOC.md) | 接口契约：CLI 入参、CSV 列、退出码、失败语义、HTTP 接口、上游接口 |
| [请求链路.md](请求链路.md) | 逆向取证：请求契约、验证码闸门、字段来源 |
| [项目分析报告-信用中国营业执照巡检.md](项目分析报告-信用中国营业执照巡检.md) | 需求满足度与工程完备性分析 |
| [瑞数逆向分析技术报告.md](瑞数逆向分析技术报告.md) | 风控层技术报告（架构 / 方法论 / 合规边界） |

## 判定口径（关键）

| 判定 | 含义 | 是否告警 |
| --- | --- | --- |
| `ok` | 登记状态为存续 / 在业 / 正常等 | 否 |
| `alert` | 吊销 / 注销 / 撤销 / 停业 / 清算等 | **是** |
| `notfound` | 全称精确匹配不到任何主体 | **是**（需人工核验） |
| `unknown` | 状态字段为空或不在两张词表内 | **是**（需人工核验） |

> **不做模糊匹配**：名称不完全相等时返回 `unknown` 并列出候选全称。把别家公司的状态当成输入公司的状态，比查不到更危险。

## 工程状态

| 项 | 状态 |
| --- | --- |
| 回归测试 | 186 项全离线，约 6 s（`pytest test_regression.py -q`） |
| CI | GitHub Actions：2 OS × 2 Python 跑离线测试 + 编码/BOM/凭据泄漏检查 |
| 依赖 | 运行时 41 条全 `==` 锁定；CI 只装 `requirements-dev.txt` |
| 定时任务 | `install_schedule_task.ps1`（Windows 计划任务，默认注册**每日 + 每月**双触发器，无需管理员） |
| 告警通道 | 企业微信群机器人 / SMTP 邮件（QQ·163·企业邮）/ 仅打印 |
| 服务自检 | `healthcheck.py` 12 项探针，异常并入日报正文 |
| HTTP 服务 | `api.py`（FastAPI）：`POST /api/jobs` 提交 → 轮询 → 下载 CSV，单 worker 串行 |
| Web 控制台 | `frontend/`（Vue 3 + Vite + TS）：构建后由 `api.py` 同源托管，无 CORS 问题 |

## 合规

只查询公开的工商登记状态，频率按 3.5–6.5 s/家随机延迟，验证码由真实浏览器正常通过，不绕过任何风控。仅用于自有客户/供应商资质核查。详见主文档 §7。
