# 更新日志

本项目的所有重要变更都记录在此文件。格式遵循
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循
[语义化版本](https://semver.org/lang/zh-CN/)。

版本 v0.7.x ~ v1.9.x 的改动未在此逐条留档（本文件随 2.0.0 建立），
可从 2.0.0 之前的提交历史中追溯。

## [2.0.0] - 2026-10-16

从 Flask + JSON 文件整体迁移到 FastAPI + MySQL，是破坏性变更。

### 新增

- `app/` 分层取代单文件 Flask 应用：`core/`（配置、上下文、响应、异常、中间件、
  日志、数据库）、`api/v1/`（路由）、`services/`、`repositories/`、`models/`，
  业务内核 `monitor.py` / `wipo_client.py` / `notification.py` 不变
- MySQL 持久化：表 `trademarks` / `reports` / `check_runs`（后者是 id 恒为 1 的单例，
  替代 `last_check_result.json` 的「写一半被杀会留损坏内容」问题）
- Vue 3 + Vite 前端（`web/`），构建产物 `web/dist`；旧原生版 `templates/` 保留作兜底
- `GET /api/schema` 与 `docs/api-schema.json`：接口契约单一来源，带 CI 漂移检测
- 报告稳定标识 `trademarkId` / `caseId`，`total` 语义修正（检查失败的商标不再被双算）
- 紧凑摘要导出 `*.summary.json`，完整报告不再重复存一份 `alerts`（体积降 45.8%）
- 计划任务常驻载体 `install_schedule_task.ps1`（注册 Windows 计划任务 `WIPO_Trademark_Monthly_Check`）
- 43 项 TestClient 契约测试（`tests_new/`），原有 110 项 Playwright E2E 全部保留

### 修复

- 调度错月：目标日当月永不被采用，首次检查最多延迟 31 天
- 系统性漏报：状态检查硬编码 `rows=5`，实测 NIKE/SONY/ADIDAS 等 12 个品牌漏 13 条异常
- 接口失败被吞成「全部正常」：认证失效时主管收到「本月无异常」
- 告警扇出：5 条告警 × 3 渠道 = 15 条消息，现聚合为每渠道一条汇总
- 我方商标误判：申请人与注册号不参与比对，自有商标被报「疑似抢注」
- `Ended`（注册终止）状态未计入异常集合
- 检查状态面板元素绑定错误：绑到 checkbox 而非状态展示 div，转圈动画永不出现
- 前端托管两个静默缺陷（`Mount("/")` 注册顺序、starlette/fastapi HTTPException 混淆）

### 变更

- 响应外壳保留 `{success, data, error}` 并追加 `request_id`，未改为 `{code, data}`
- 入口从 `python web_app.py` 改为 `uvicorn app.main:app`
- `docs/` 下三个同名文件重命名消歧（`docs_viewer.js` / `docs-viewer.css` / `docs-viewer.html`）

## [1.0.0] - 2026-05

Flask 初版：单文件 `web_app.py`，JSON 文件存储，原生 HTML/CSS/JS 前端。

[2.0.0]: https://github.com/DoonaSuzy/WIPO_and_Brand/releases/tag/v2.0.0
[1.0.0]: https://github.com/DoonaSuzy/WIPO_and_Brand/releases/tag/v1.0.0
