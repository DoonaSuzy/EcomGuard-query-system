# WIPO 商标监控系统 - 项目文件结构说明

> **文档版本**: 1.1.0
> **更新日期**: 2026-10-16
> **适用范围**: 本仓库根目录 `f:\projects\GO1\python_ver`
>
> 工作区根目录 `f:\projects\GO1` 下原有多个互不相干的独立仓库，已于 2026-10-16
> 移出至 `F:\projects\GO1-excluded\`（非删除，可移回），总览见工作区根 `README.md`
> （**不在本仓库内**）。本仓库与它们无代码级依赖。

---

## 一、目录总览

```
f:\projects\GO1\python_ver\             # 本仓库根目录（独立 git 仓库）
├── README.md                          # 项目总览文档（入口）
├── AGENTS.md                          # AI 助手 / 新人速览（已知坑，改代码前先读）
├── main.py                            # CLI 入口 + 定时调度
├── web_app.py                         # Flask Web 应用（端口 5000）
├── monitor.py                         # 监控引擎（核心业务逻辑）
├── wipo_client.py                     # WIPO BrandDB API 客户端
├── trademark_list.py                  # 商标清单管理（数据层）
├── notification.py                    # 通知服务（邮件/钉钉/企微）
├── cryptojs_aes.py                    # CryptoJS 兼容 AES 加解密
├── verify_fixes.py                    # 缺陷修复断言（67 项，不联网）
├── config.json                        # 运行时配置（含敏感信息，勿提交）
├── config.example.json                # 配置模板（可安全提交）
├── trademarks.json                    # CLI 模式商标清单示例
├── sample_trademarks.json             # 测试用商标数据
├── test_all.py                        # 旧版简单测试脚本（已废弃）
├── install_schedule_task.ps1          # 注册「每月自动检查」Windows 计划任务
├── run_monthly_check.bat              # 计划任务的实际入口（由上面的脚本生成）
├── run_tests.bat                      # Windows 一键运行测试
├── pytest.ini                         # pytest 配置
├── data/                              # 运行时数据（Web 版）
│   ├── user_trademarks.json           # 用户商标数据
│   ├── last_check_result.json         # 最近一次检查结果（新检查前被删除）
│   └── last_check_error.txt           # 上次检查失败原因（存在即表示失败）
├── reports/                           # 检查报告存储
├── docs/                              # 文档目录
│   ├── API_DOCUMENTATION.md           # Web API 接口文档
│   ├── API_DOC.md                     # Python API / CLI 文档
│   ├── PROJECT_STRUCTURE.md           # 本文件（项目结构说明）
│   ├── docs_server.py                 # API 文档站服务（端口 5001，独立程序）
│   └── viewer/                        # 文档站前端资源（独立于下方 static/ 与 templates/）
│       ├── static/css/docs-viewer.css
│       ├── static/js/docs-viewer.js
│       └── templates/docs-viewer.html
├── static/                            # Web 应用静态资源
│   ├── css/style.css                  # 样式表
│   └── js/app.js                      # 前端交互逻辑
├── templates/
│   └── index.html                     # Web 应用主页
├── tests/                             # Playwright E2E 测试套件
│   ├── conftest.py                    # 测试夹具（服务器/浏览器/Mock）
│   ├── test_check.py                  # 检查流程测试
│   ├── test_check_options.py          # 检查选项边界测试
│   ├── test_form.py                   # 表单基础测试
│   ├── test_form_validation.py        # 表单边界校验测试
│   ├── test_edit_flow.py              # 编辑流程测试
│   ├── test_modal_interactions.py     # 模态框交互测试
│   ├── test_batch_operations.py       # 批量操作测试
│   ├── test_search_filter.py          # 搜索过滤测试
│   ├── test_toast_notifications.py# Toast 通知测试
│   ├── test_import_export.py          # 导入导出测试
│   ├── test_reports.py                # 报告页面测试
│   ├── test_navigation.py             # 页面导航测试
│   └── test_list.py                   # 商标清单测试
└── test-results/                      # 测试输出（自动生成，已 gitignore）
    ├── report.html                    # HTML 测试报告
    └── screenshots/                   # 失败截图
```

---

## 二、核心模块说明

### 2.1 入口层

| 文件 | 职责 | 运行方式 |
|------|------|----------|
| `main.py` | CLI 入口，支持 `run`/`schedule`/`init-config` 三个子命令 | `python main.py run trademarks.json` |
| `web_app.py` | Flask Web 应用，提供 REST API 和前端页面 | `python web_app.py` → :5000 |

### 2.2 业务层

| 文件 | 职责 | 依赖 |
|------|------|------|
| `monitor.py` | 监控引擎，协调检查流程、生成告警和报告 | wipo_client, trademark_list, notification |
| `wipo_client.py` | WIPO API 客户端，处理认证、请求、响应解密 | cryptojs_aes, requests |
| `trademark_list.py` | 商标清单加载/保存/查询/筛选 | 无 |
| `notification.py` | 多渠道通知服务 | smtplib, urllib |
| `cryptojs_aes.py` | CryptoJS 兼容 AES-ECB 加解密 | 无 |

### 2.3 数据层

| 路径 | 内容 | 生成方式 |
|------|------|----------|
| `data/user_trademarks.json` | Web 版用户商标数据 | Web 应用 CRUD 操作 |
| `data/last_check_result.json` | 最近一次检查结果 | 异步检查完成后写入 |
| `reports/report_*.json` | 历史检查报告 | 每次检查自动生成 |
| `trademarks.json` | CLI 模式商标清单 | 手动维护 |
| `config.json` | 通知渠道 + 调度配置 | `python main.py init-config` 生成 |

---

## 三、文档体系

| 文档 | 位置 | 内容 | 维护频率 |
|------|------|------|----------|
| 项目总览 | `README.md`（本仓库根） | 项目介绍、快速开始、运行方式 | 功能变更时 |
| AI 助手 / 新人速览 | `AGENTS.md` | 已知坑、数据落点、易混淆点（**改代码前先读**） | 发现新坑时 |
| Web API 文档 | `docs/API_DOCUMENTATION.md` | REST API 接口定义 | 接口变更时 |
| Python API/CLI 文档 | `docs/API_DOC.md` | 类方法、CLI 命令、数据模型 | 代码变更时 |
| 项目结构说明 | `docs/PROJECT_STRUCTURE.md` | 文件位置、用途、关系 | 文件增删时 |
| 配置模板 | `config.example.json` | 配置项说明 | 配置变更时 |

---

## 四、测试体系

| 文件 | 覆盖范围 |
|------|----------|
| `tests/conftest.py` | 测试基础设施（隔离服务器、浏览器、Mock、截图） |
| `tests/test_check.py` | 检查流程（成功/失败/无商标） |
| `tests/test_check_options.py` | 检查选项边界（请求体断言） |
| `tests/test_form.py` | 表单基础（添加/校验/编辑） |
| `tests/test_form_validation.py` | 表单边界（空/短/超长/重复/特殊字符） |
| `tests/test_edit_flow.py` | 编辑流程（回填/提交/重置） |
| `tests/test_modal_interactions.py` | 模态框（确认/取消/遮罩） |
| `tests/test_batch_operations.py` | 批量操作（全选/删除） |
| `tests/test_search_filter.py` | 搜索过滤 |
| `tests/test_toast_notifications.py` | Toast 通知 |
| `tests/test_import_export.py` | 导入导出 |
| `tests/test_reports.py` | 报告页面 |
| `tests/test_navigation.py` | 页面导航 |
| `tests/test_list.py` | 商标清单 |

**运行方式**:
```bash
cd python_ver
python -m pytest tests -v          # 全部测试
python -m pytest tests/test_form.py -v  # 单文件
```
或双击 `run_tests.bat`。

---

## 五、文件命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| Python 模块 | 小写下划线 | `wipo_client.py` |
| 测试文件 | `test_<模块名>.py` | `test_form_validation.py` |
| 配置文件 | `<名称>.json` | `config.example.json` |
| 报告文件 | `report_<时间戳>.json` | `report_20260807_120000.json` |
| 文档 | 大写 + 下划线 | `API_DOCUMENTATION.md` |

---

## 六、版本信息

| 组件 | 版本 |
|------|------|
| Python | 3.13.15 |
| Flask | 最新稳定版 |
| Playwright | 最新稳定版 |
| 系统版本 | v1.0.0 |

---

## 七、废弃文件说明

| 路径 | 状态 | 说明 |
|------|------|------|
| `node_modules/` | 已移出 | 曾位于工作区根，Node.js 版本遗留依赖（约 3 MB），已于 2026-10-16 移至 `F:\projects\GO1-excluded\` |
| `test_all.py` | 废弃 | 旧版简单测试脚本，已被 `tests/` 替代 |
| `python_ver/API_DOC.md` | 已迁移 | 已移入 `docs/` 目录统一管理 |