# WIPO 商标监控系统 - 接口文档

> **适用版本**: 2026-10-16 工作区版本（含 rows=30 检索、Ended 异常状态、我方商标三条件识别、告警汇总）
> **更新日期**: 2026-10-16
> **范围提示**: 本文档描述 Python 类 / CLI 接口；Web REST 接口见 `API_DOCUMENTATION.md`

## 概述

WIPO 商标监控系统是一个自动化商标状态检查和抢注检测工具，通过调用 WIPO BrandDB API 获取商标数据，自动识别异常状态和疑似抢注情况，并支持邮件、钉钉、企微等多渠道通知。

---

## 目录

- [CLI 命令接口](#cli-命令接口)
- [WipoApiClient 类](#wipoclient-类)
- [TrademarkList 类](#trademarklist-类)
- [MonitorEngine 类](#monitorengine-类)
- [NotificationService 类](#notificationservice-类)
- [Scheduler 类](#scheduler-类)
- [数据模型](#数据模型)
- [配置文件](#配置文件)

---

## CLI 命令接口

### 1. 运行检查

```bash
python main.py run <trademark_file> [options]
```

**参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `trademark_file` | string | 是 | 商标清单文件路径，支持 JSON/CSV/Excel 格式 |
| `--output`, `-o` | string | 否 | 报告输出目录，默认不保存文件 |
| `--config`, `-c` | string | 否 | 配置文件路径（用于通知） |
| `--no-status` | flag | 否 | 跳过状态检查 |
| `--no-squatting` | flag | 否 | 跳过抢注检查 |
| `--no-notify` | flag | 否 | 不发送通知 |

**示例：**
```bash
# 运行完整检查
python main.py run trademarks.json -o reports/

# 仅检查抢注
python main.py run trademarks.json --no-status

# 不发送通知
python main.py run trademarks.json -o reports/ --no-notify
```

---

### 2. 启动定时调度

```bash
python main.py schedule <trademark_file> [options]
```

**参数：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `trademark_file` | string | 是 | - | 商标清单文件路径 |
| `--config`, `-c` | string | 否 | - | 配置文件路径 |
| `--day`, `-d` | int | 否 | 取 config 的 `schedule.day_of_month`，缺省 1 | 每月第几天运行 |
| `--hour` | int | 否 | 取 config 的 `schedule.hour`，缺省 9 | 运行时间（小时） |
| `--minute` | int | 否 | 取 config 的 `schedule.minute`，缺省 0 | 运行时间（分钟） |

调度时间取值的优先级为：**命令行参数 > 配置文件 `schedule` 段 > 内置默认值**。
因此不传 `--day/--hour/--minute` 时，会读取配置文件中的 `schedule`。

指定的日期若在当月不存在（如 31 日遇到 30 天的月份、30 日遇到 2 月），
会自动退化为该月最后一天，而不是跳到下个月。

**示例：**
```bash
# 每月 1 号 9 点运行
python main.py schedule trademarks.json

# 每月 15 号 14 点 30 分运行
python main.py schedule trademarks.json --day 15 --hour 14 --minute 30
```

---

### 3. 生成配置文件

```bash
python main.py init-config [options]
```

**参数：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `--output`, `-o` | string | 否 | `config.json` | 配置文件输出路径 |

**示例：**
```bash
python main.py init-config -o config.json
```

---

## WipoApiClient 类

WIPO API 客户端，封装与 WIPO BrandDB 搜索 API 的交互。

### 初始化

```python
from wipo_client import WipoApiClient

client = WipoApiClient()
```

### 方法

#### `init() -> str`

初始化客户端，生成 `searchId` 并建立会话。

**返回值：** `str` - 生成的 searchId (UUID hex)

**示例：**
```python
search_id = client.init()
# 形如 '6f1a2b3c4d5e6f708192a3b4c5d6e7f8'（32 位 UUID hex，每次调用都不同）
# 该值由服务端返回，不是可复用的固定凭证
```

---

#### `search(brand_name: str, options: dict = None) -> dict`

搜索商标，返回匹配结果。

**参数：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `brand_name` | string | 是 | - | 要搜索的商标名 |
| `options` | dict | 否 | `{}` | 搜索选项 |

**options 字段：**

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `start` | int | 0 | 结果起始位置（分页） |
| `rows` | int | 30 | 返回结果数量 |
| `sort` | string | `'score desc'` | 排序方式 |
| `strategy` | string | `'Simple'` | 搜索策略 |

**返回值：**
```json
{
  "success": true,
  "total": 13459,
  "start": 0,
  "results": [
    {
      "brandName": "APPLE",
      "status": "Registered",
      "office": "US",
      "applicant": "Apple Inc.",
      "registrationNumber": "TM123456",
      "niceClass": ["9", "35"],
      "score": 12.85,
      // ... 其他字段
    }
  ]
}
```

**示例：**
```python
result = client.search('APPLE', {'rows': 50})
if result['success']:
    print(f"找到 {result['total']} 条结果")
    for doc in result['results']:
        print(f"  - {doc['brandName']} ({doc['status']})")
```

---

#### `search_multiple(brand_names: list, options: dict = None) -> dict`

批量搜索多个商标名。

**参数：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `brand_names` | list[str] | 是 | - | 商标名列表 |
| `options` | dict | 否 | `{}` | 搜索选项，支持 `delay`（毫秒） |

**返回值：**
```json
{
  "results": {
    "APPLE": { "success": true, "total": 13459, ... },
    "MICROSOFT": { "success": true, "total": 8900, ... }
  },
  "errors": [
    { "name": "INVALID", "error": "请求失败" }
  ]
}
```

**示例：**
```python
result = client.search_multiple(['APPLE', 'MICROSOFT', 'TESLA'])
for name, data in result['results'].items():
    print(f"{name}: {data['total']} 条结果")
```

---

#### `check_status(brand_name: str, options: dict = None) -> dict`

检查指定商标的状态，识别异常状态。

**异常状态包括：** `Expired`, `Cancelled`, `Refused`, `Abandoned`, `Withdrawn`, `Ended`, `Not Registered`

**参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `brand_name` | string | 是 | 要检查的商标名 |
| `options` | dict | 否 | 搜索选项，另见下方「我方商标识别」 |

**options 字段（用于识别「我方商标」）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `ourApplicantNames` | list[str] | 我方申请人名称，与检索结果的 applicant / applicants 比对（大小写与空白不敏感） |
| `ourRegistrationNumbers` | list[str] | 我方注册号，归一化后精确匹配 |
| `rows` | int | 返回结果数量，默认为 `30`（`DEFAULT_SEARCH_ROWS`） |

只有当检索结果满足**任一**条件时，才被认定为我方商标并纳入异常状态统计：
品牌名归一化后相等、申请人名称匹配、或注册号命中。
三者为「或」关系；均未提供时退化为「品牌名相等」判定。

**返回值：**
```json
{
  "success": true,
  "brandName": "APPLE",
  "totalFound": 13459,
  "ourMarks": [
    {
      "brandName": "APPLE",
      "status": "Registered",
      "registrationNumber": "TM123456",
      // ...
    }
  ],
  "abnormalStatuses": [
    {
      "registrationNumber": "TM999999",
      "status": "Expired",
      "office": "US",
      "expiryDate": "2024-01-01"
    }
  ],
  "hasIssue": true
}
```

**示例：**
```python
status = client.check_status('APPLE')
if status['hasIssue']:
    for issue in status['abnormalStatuses']:
        print(f"警告: {issue['registrationNumber']} 状态为 {issue['status']}")
```

---

#### `check_squatting(brand_name: str, our_applicant_names: list, options: dict = None) -> dict`

检查指定商标是否存在疑似抢注情况。

**抢注检测逻辑：**
- **高风险 (high)**：品牌名相似且申请人不同，疑似抢注
- **中风险 (medium)**：品牌名精确匹配但申请人不同，需人工确认
- **低风险 (low)**：品牌名精确匹配但存在语言差异（中日韩/阿拉伯文），可能是海外关联方

**参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `brand_name` | string | 是 | 要检查的商标名 |
| `our_applicant_names` | list[str] | 是 | 我方申请人名称列表（用于识别我方商标） |
| `options` | dict | 否 | 搜索选项，支持 `brandCoreKeyword` |

**返回值：**
```json
{
  "success": true,
  "brandName": "APPLE",
  "totalFound": 13459,
  "squattingCases": [
    {
      "brandName": "APPLE APPLE APPLE APPLE",
      "applicant": "Charles M. Pfister",
      "applicantCountryCode": "US",
      "status": "Pending",
      "registrationNumber": "",
      "niceClass": [35],
      "office": "US",
      "score": 12.85,
      "isExactMatch": false,
      "riskLevel": "high",
      "reason": "品牌名相似且申请人不同，疑似抢注"
    }
  ],
  "needsReviewCases": [
    {
      "brandName": "APPLE",
      "applicant": "ابل انك",
      "riskLevel": "low",
      "reason": "品牌名精确匹配，但存在语言差异"
    }
  ],
  "hasSquatting": true,
  "highRiskCount": 15,
  "mediumRiskCount": 6
}
```

**示例：**
```python
applicants = ['Apple Inc.', 'Apple Computer, Inc.']
result = client.check_squatting('APPLE', applicants)
print(f"高风险: {result['highRiskCount']} 条")
print(f"待审核: {result['mediumRiskCount']} 条")
```

---

## TrademarkList 类

商标清单管理，支持多种文件格式的加载和导出。

### 初始化

```python
from trademark_list import TrademarkList

tm_list = TrademarkList()
```

### 方法

#### `load(filepath: str) -> int`

从文件加载商标清单，自动识别格式。

**支持格式：**
- JSON (`.json`)
- CSV (`.csv`)
- Excel (`.xlsx`, `.xls`)

**参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `filepath` | string | 是 | 文件路径 |

**返回值：** `int` - 加载的商标数量

**示例：**
```python
count = tm_list.load('trademarks.json')
print(f"加载了 {count} 个商标")
```

---

#### `load_from_json(filepath: str) -> int`

从 JSON 文件加载。

**JSON 格式：**
```json
{
  "trademarks": [
    {
      "brandName": "APPLE",
      "applicant": "Apple Inc.",
      "registrationNumber": "TM001",
      "niceClass": ["9", "35"],
      "notes": "备注"
    }
  ]
}
```

---

#### `load_from_csv(filepath: str) -> int`

从 CSV 文件加载。

**CSV 格式：**
```csv
brandName,applicant,registrationNumber,niceClass,notes
APPLE,Apple Inc.,TM001,"9,35",苹果主品牌
```

---

#### `load_from_excel(filepath: str) -> int`

从 Excel 文件加载（需安装 `openpyxl`）。

**依赖安装：**
```bash
pip install openpyxl
```

---

#### `save_to_json(filepath: str)`

将商标清单保存为 JSON 文件。

**参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `filepath` | string | 是 | 保存路径 |

---

#### `get_brand_names() -> list[str]`

获取所有品牌名列表。

**返回值：** `list[str]` - 品牌名列表

**示例：**
```python
names = tm_list.get_brand_names()
# ['APPLE', 'MICROSOFT', 'TESLA']
```

---

#### `get_applicant_names() -> list[str]`

获取所有申请人名称列表（用于抢注检测时识别我方商标）。

**返回值：** `list[str]` - 申请人名称列表

---

#### `filter_by(predicate: callable) -> TrademarkList`

根据条件筛选商标。

**参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `predicate` | callable | 是 | 筛选函数，接收商标记录字典，返回 bool |

**返回值：** `TrademarkList` - 筛选后的新实例

**示例：**
```python
# 筛选包含 "TECH" 的商标
filtered = tm_list.filter_by(lambda t: 'TECH' in t['brandName'])
```

---

## MonitorEngine 类

监控引擎，协调 WIPO API 客户端和商标清单，执行完整的检查流程。

### 初始化

```python
from monitor import MonitorEngine

engine = MonitorEngine()

# 或自定义依赖
engine = MonitorEngine(
    wipo_client=custom_client,
    notification_service=custom_notification
)
```

### 方法

#### `run_check(trademark_list: TrademarkList, check_status: bool = True, check_squatting: bool = True, applicant_names: list = None) -> dict`

执行商标检查，遍历清单中的所有商标。

**参数：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `trademark_list` | TrademarkList | 是 | - | 商标清单 |
| `check_status` | bool | 否 | True | 是否检查状态 |
| `check_squatting` | bool | 否 | True | 是否检查抢注 |
| `applicant_names` | list[str] | 否 | 自动获取 | 我方申请人名称 |

**返回值：**
```json
{
  "total": 10,
  "checked": 10,
  "errors": [
    { "brandName": "NIKE", "stage": "状态检查", "error": "状态检查失败: 响应数据解密失败" }
  ],
  "errorsCount": 1,
  "failedBrands": ["NIKE"],
  "alerts": [
    {
      "brandName": "BYD",
      "issues": ["状态异常", "疑似抢注"],
      "abnormalStatuses": [
        {
          "registrationNumber": "902462164",
          "status": "Expired"
        }
      ],
      "squattingCases": [
        {
          "brandName": "BYD BYD AUTO",
          "applicant": '"ИЙСТ МОТОР КОРПОРЕЙШЪН" ООД',
          "riskLevel": "high"
        }
      ]
    }
  ],
  "hasIssues": true,
  "timestamp": "2026-07-27T12:28:16"
}
```

**示例：**
```python
tm_list = TrademarkList()
tm_list.load('trademarks.json')

engine = MonitorEngine()
result = engine.run_check(tm_list)

print(f"检查完成: {result['checked']}/{result['total']}")
print(f"告警数: {len(result['alerts'])}")
```

---

#### `generate_report() -> str`

生成可读的文本格式报告。

**返回值：** `str` - 格式化的报告文本

**示例：**
```python
report = engine.generate_report()
print(report)
```

**输出示例：**
```
============================================================
商标监控报告
生成时间: 2026-07-27 12:28:16
============================================================

总商标数: 10
异常数: 9
错误数: 0

--- 告警详情 ---

商标: APPLE
问题: 疑似抢注
  疑似抢注:
    - APPLE APPLE APPLE APPLE (申请人: Charles M. Pfister, 风险: high)
...
```

---

#### `save_report(filepath: str)`

保存 JSON 格式的完整报告。

**参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `filepath` | string | 是 | 保存路径 |

**示例：**
```python
engine.save_report('reports/report_20260727.json')
```

> 报告写 `results`（全部逐商标结果）与 `alertsCount`，**不再重复存 `alerts`**
> （告警就是 `results` 里有问题的那部分，存两份实测占报告体积约 29%）。
> 需要告警列表时用 `monitor.alerts_from_results(report['results'])` 还原，
> 结果与 `run_check()` 的 `alerts` 逐字段一致。

---

#### `save_summary(filepath: str)`

保存 JSON 格式的紧凑摘要，供看板 / AI 低 token 消费。

摘要只含统计与告警定位 —— `timestamp` / `finishedAt` / `total` / `checked` /
`alertsCount` / `errorsCount` / `failedBrands` / `hasIssues`，以及 `alerts`（每项只有
`brandName` / `trademarkId` / `issues` / `squattingCount` / `needsReviewCount` /
`abnormalStatusCount`），**不含案例明细与 `results`**。

**示例：**
```python
engine.save_summary('reports/report_20260727.summary.json')
```

> Web 版与 CLI 每次检查都会自动写出 `report_<时间戳>.summary.json`。
> `GET /api/reports` 不会列出这类文件，避免同一次检查被计两次。

---

#### `get_summary() -> dict`

获取检查结果摘要（内存中的统计，不落盘）。

**返回值：**
```json
{
  "total_checked": 10,
  "has_issue": 2,
  "has_squatting": 8,
  "errorsCount": 0,
  "alerts_count": 9
}
```

> 全库 `errors` 只有一种形态：**错误详情列表**；数量一律看 `errorsCount`。
> `get_summary()` 早期曾把数量写在 `errors` 键上（int），同一个键两种含义，
> 已于 2026-10-16 统一为 `errorsCount`。需要落盘的摘要请用 `save_summary()`。

---

#### `send_alerts() -> dict`

通过配置的通知渠道发送告警。

**返回值：**
```json
{
  "sent": 5,
  "failed": 0
}
```

> 注意：`MonitorEngine.send_alerts()` 在**没有告警**时返回上面的 `{"sent": 0, "failed": 0}`；
> 有告警时它直接透传 `NotificationService.send_alerts()` 的返回值
> （`{"success": [...], "failed": [...]}`），因此调用方应优先读取 `success`/`failed`。
> 参考 `main.py` 与 `web_app.py` 的用法：`len(send_result['success'])`。

---

## NotificationService 类

通知服务，支持多种通知渠道。

### 初始化

```python
from notification import NotificationService

# 无配置（仅支持手动调用）
service = NotificationService()

# 带配置
config = {
    'channels': ['email', 'dingtalk'],
    'email': { ... },
    'dingtalk': { ... }
}
service = NotificationService(config)
```

### 方法

#### `send_alerts(alerts: list[dict]) -> dict`

批量发送告警。

**同一渠道的告警会汇总为一条消息**：无论 `alerts` 有多少条，每个渠道只发送
一次（一封邮件 / 一条钉钉 / 一条企微），正文按 `--- [i/N] ---` 分段列出全部告警，
邮件主题形如 `【商标监控告警】5 个商标 · BRAND0, BRAND1, BRAND2 等 5 个`。
（改造前是「N 条告警发 N 封邮件」。）

**参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `alerts` | list[dict] | 是 | 告警列表 |

**返回值：**
```json
{
  "success": [
    {
      "success": true,
      "channel": "email",
      "alert": "BRAND0, BRAND1, BRAND2 等 5 个",
      "alerts": ["BRAND0", "BRAND1", "BRAND2", "BRAND3", "BRAND4"],
      "count": 5
    }
  ],
  "failed": []
}
```

`success` / `failed` 的每个元素都带 `channel`、`alert`（兼容旧字段，现为商标名摘要）、
`alerts`（本次汇总覆盖的商标名列表）与 `count`（汇总条数）。
`alerts` 为空时直接返回 `{"success": [], "failed": []}`，不发送任何消息。

---

#### `send_alert(alert: dict, channel: str = 'email') -> dict`

发送单条告警到指定渠道。

**参数：**

| 参数 | 类型 | 必填 | 可选值 | 说明 |
|------|------|------|--------|------|
| `alert` | dict | 是 | - | 告警对象 |
| `channel` | string | 否 | `'email'`, `'dingtalk'`, `'wework'` | 通知渠道 |

---

#### `_format_alert_text(alert: dict) -> str`

格式化告警为文本（供各渠道使用）。

**返回值：** `str` - 格式化的告警文本

---

## Scheduler 类

定时任务调度器，支持每月自动运行检查。

### 初始化

```python
from main import Scheduler

# 无配置
scheduler = Scheduler()

# 带配置
scheduler = Scheduler('config.json')
```

### 方法

#### `run_once(trademark_file: str, check_status: bool = True, check_squatting: bool = True, send_notifications: bool = True, output_dir: str = None) -> dict`

运行一次完整检查（包含加载、检查、报告、通知）。

**参数：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `trademark_file` | string | 是 | - | 商标清单文件 |
| `check_status` | bool | 否 | True | 检查状态 |
| `check_squatting` | bool | 否 | True | 检查抢注 |
| `send_notifications` | bool | 否 | True | 发送通知 |
| `output_dir` | string | 否 | None | 报告输出目录 |

**返回值：** 与 `MonitorEngine.run_check()` 相同

**示例：**
```python
result = scheduler.run_once('trademarks.json', output_dir='reports/')
```

---

#### `start_monthly(trademark_file: str, day_of_month: int = 1, hour: int = 9, minute: int = 0, callback: callable = None)`

启动每月定时调度（后台线程）。

**参数：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `trademark_file` | string | 是 | - | 商标清单文件 |
| `day_of_month` | int | 否 | 1 | 每月第几天运行 |
| `hour` | int | 否 | 9 | 运行小时 |
| `minute` | int | 否 | 0 | 运行分钟 |
| `callback` | callable | 否 | None | 运行完成回调函数 |

**示例：**
```python
def on_complete(result):
    print(f"检查完成，告警数: {len(result['alerts'])}")

scheduler.start_monthly('trademarks.json', day_of_month=1, callback=on_complete)
```

---

#### `stop()`

停止定时调度。

---

## 数据模型

### 商标记录 (Trademark)

```json
{
  "brandName": "APPLE",
  "applicant": "Apple Inc.",
  "applicantNames": ["Apple Inc.", "Apple Computer, Inc."],
  "registrationNumber": "TM001",
  "niceClass": ["9", "35"],
  "notes": "备注",
  "status": ""
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `brandName` | string | 是 | 商标名 |
| `applicant` | string | 否 | 申请人名称 |
| `applicantNames` | list[str] | 否 | 申请人名称列表 |
| `registrationNumber` | string | 否 | 注册号 |
| `niceClass` | list | 否 | 尼斯分类 |
| `notes` | string | 否 | 备注 |
| `status` | string | 否 | 状态 |

---

### 搜索结果 (SearchResult)

```json
{
  "brandName": "APPLE",
  "brandNames": ["APPLE", "Apple"],
  "status": "Registered",
  "type": "word",
  "office": "US",
  "collection": "USPTO",
  "markFeature": "",
  "applicant": "Apple Inc.",
  "applicants": ["Apple Inc."],
  "applicantCountryCode": "US",
  "applicantCountryCodes": ["US"],
  "registrationNumber": "TM123456",
  "registrationDate": "2020-01-01",
  "applicationNumber": "APPL20200001",
  "applicationDate": "2019-06-01",
  "expiryDate": "2030-01-01",
  "niceClass": ["9", "35"],
  "designation": [],
  "score": 12.85,
  "logoId": "",
  "kind": "",
  "runid": "",
  "st13": ""
}
```

---

### 告警对象 (Alert)

```json
{
  "brandName": "BYD",
  "issues": ["状态异常", "疑似抢注"],
  "abnormalStatuses": [
    {
      "registrationNumber": "902462164",
      "status": "Expired",
      "office": "BG",
      "expiryDate": ""
    }
  ],
  "squattingCases": [
    {
      "brandName": "BYD BYD AUTO",
      "applicant": '"ИЙСТ МОТОР КОРПОРЕЙШЪН" ООД',
      "applicantCountryCode": "BG",
      "status": "Pending",
      "registrationNumber": "",
      "niceClass": [12],
      "office": "BG",
      "score": 8.5,
      "isExactMatch": false,
      "riskLevel": "high",
      "reason": "品牌名相似且申请人不同，疑似抢注"
    }
  ],
  "needsReviewCases": [],
  "hasIssue": true,
  "hasSquatting": true
}
```

---

## 配置文件

### config.json 格式

```json
{
  "notification": {
    "channels": ["email", "dingtalk", "wework"],
    "email": {
      "smtp_server": "smtp.example.com",
      "smtp_port": 587,
      "sender": "monitor@example.com",
      "recipients": ["admin@example.com", "manager@example.com"],
      "username": "your-username",
      "password": "your-password"
    },
    "dingtalk": {
      "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=xxx"
    },
    "wework": {
      "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
    }
  },
  "schedule": {
    "day_of_month": 1,
    "hour": 9,
    "minute": 0
  }
}
```

### 配置说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `notification.channels` | list | 启用的通知渠道 |
| `notification.email` | object | 邮件配置（SMTP） |
| `notification.dingtalk.webhook_url` | string | 钉钉机器人 Webhook |
| `notification.wework.webhook_url` | string | 企微机器人 Webhook |
| `schedule` | object | 调度配置，由 `python main.py schedule` 读取 |
| `schedule.day_of_month` | int | 每月第几天运行，1-31（当月无该日期时退化为月末） |
| `schedule.hour` | int | 运行时间（小时），0-23 |
| `schedule.minute` | int | 运行时间（分钟），0-59 |

`schedule` 段中非整数或超出范围的值会被忽略并回退到内置默认值（1 日 9:00）。

---

## 完整使用示例

### Python API 方式

```python
from trademark_list import TrademarkList
from wipo_client import WipoApiClient
from monitor import MonitorEngine
from notification import NotificationService

# 1. 加载商标清单
tm_list = TrademarkList()
tm_list.load('trademarks.json')

# 2. 初始化客户端
client = WipoApiClient()

# 3. 创建监控引擎
engine = MonitorEngine(wipo_client=client)

# 4. 运行检查
applicant_names = tm_list.get_applicant_names()
result = engine.run_check(tm_list, applicant_names=applicant_names)

# 5. 查看结果
report = engine.generate_report()
print(report)

# 6. 保存报告
engine.save_report('reports/report.json')

# 7. 发送通知
config = {
    'channels': ['dingtalk'],
    'dingtalk': {
        'webhook_url': 'https://oapi.dingtalk.com/robot/send?access_token=xxx'
    }
}
service = NotificationService(config)
service.send_alerts(result['alerts'])
```

### 定时任务方式

```python
from main import Scheduler

scheduler = Scheduler('config.json')

# 立即运行一次
result = scheduler.run_once('trademarks.json', output_dir='reports/')

# 启动每月定时调度
scheduler.start_monthly('trademarks.json', day_of_month=1, hour=9)
```

---

## 依赖库

| 库 | 版本要求 | 用途 |
|----|---------|------|
| `requests` | >= 2.25.0 | HTTP 请求 |
| `openpyxl` | >= 3.0.0 | Excel 文件支持（可选） |

**安装依赖：**
```bash
pip install requests openpyxl
```

---

## 注意事项

1. **API 频率限制**：WIPO API 可能有频率限制，建议每次请求间隔 500ms 以上
2. **网络要求**：需要能访问 `https://branddb.wipo.int`
3. **数据准确性**：抢注检测基于品牌名和申请人匹配，可能存在误报，建议人工复核
4. **语言差异**：系统已处理中日韩、阿拉伯文等多语言场景
5. **密钥安全**：AES 解密密钥由固定前缀 + 动态 searchId 组成，无需额外配置

---

## 常见问题

### Q: 运行时提示 "HashSearch 认证失败"？
A: 客户端会自动初始化 `searchId`，请确保已调用 `client.init()` 或直接使用 `engine.run_check()`。

### Q: 如何只检查部分商标？
A: 使用 `TrademarkList.filter_by()` 筛选后再传入 `MonitorEngine.run_check()`。

### Q: 报告中的 "高风险" 和 "待审核" 有什么区别？
A: 高风险表示品牌名相似但申请人不同，疑似故意抢注；待审核表示可能是海外关联方或授权使用，需人工确认。

### Q: 支持 Windows 任务计划程序吗？
A: 可以使用 `schedule` 命令启动后台线程，或通过 Windows 任务计划程序定期调用 `python main.py run trademarks.json`。