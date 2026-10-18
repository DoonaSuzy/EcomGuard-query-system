# WIPO 商标监控系统 API 文档

> **适用版本**: 2026-10-16 工作区版本（`errors` 为详情列表、新增 `errorsCount` / `failedBrands` / `hasIssues`）
> **更新日期**: 2026-10-16
> **范围提示**: 本文档描述 Web REST 接口；Python 类 / CLI 接口见 `API_DOC.md`
>
> **基础 URL**: `http://localhost:5000`
>
> **数据格式**: 所有请求和响应均使用 `application/json` 格式（文件上传除外）
>
> **认证**: 当前版本无需认证
>
> **机器可读契约**: `docs/api-schema.json`（唯一来源）。运行时可通过
> `GET /api/schema` 获取；文档站 `GET /api/definitions` 读的也是同一份。
> 接口增删后请同步该文件 —— `tests/test_api_schema.py` 会校验
> 「实现 / 契约 / 本文档」三方一致，不一致即测试失败。

---

## 目录

- [商标管理](#商标管理)
  - [获取商标列表](#获取商标列表)
  - [添加商标](#添加商标)
  - [更新商标](#更新商标)
  - [删除商标](#删除商标)
  - [批量删除商标](#批量删除商标)
- [状态检查](#状态检查)
  - [启动检查](#启动检查)
  - [查询检查状态](#查询检查状态)
- [检查报告](#检查报告)
  - [获取报告列表](#获取报告列表)
  - [获取报告详情](#获取报告详情)
- [导入导出](#导入导出)
  - [导出商标](#导出商标)
  - [导入商标](#导入商标)
- [系统](#系统)
  - [获取接口契约](#获取接口契约)

---

## 商标管理

商标数据的增删改查操作。

### 获取商标列表

获取所有已添加的商标列表。

**请求**

```
GET /api/trademarks
```

**cURL 示例**

```bash
curl -X GET http://localhost:5000/api/trademarks
```

**响应示例**

```json
{
  "success": true,
  "data": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "brandName": "APPLE",
      "applicant": "Apple Inc.",
      "registrationNumber": "TM001",
      "niceClass": ["9", "35"],
      "notes": "备注信息",
      "createdAt": "2026-07-27T12:28:16",
      "status": "pending"
    }
  ]
}
```

**响应字段说明**

| 字段 | 类型 | 描述 |
|------|------|------|
| id | string | 商标唯一标识 (UUID) |
| brandName | string | 商标名称 |
| applicant | string | 申请人名称 |
| registrationNumber | string | 注册号 |
| niceClass | array | 尼斯分类数组 |
| notes | string | 备注信息 |
| createdAt | string | 创建时间 (ISO 8601) |
| status | string | 状态：pending/checked/error |

**状态码**

| 状态码 | 描述 |
|--------|------|
| 200 | 成功 |

---

### 添加商标

添加一个新的商标记录。

**请求**

```
POST /api/trademarks
```

**请求体**

| 字段 | 类型 | 必填 | 描述 |
|------|------|------|------|
| brandName | string | ✅ | 商标名称（2-200 字符） |
| applicant | string | ❌ | 申请人名称 |
| registrationNumber | string | ❌ | 注册号 |
| niceClass | array | ❌ | 尼斯分类数组 |
| notes | string | ❌ | 备注信息 |

**cURL 示例**

```bash
curl -X POST http://localhost:5000/api/trademarks \
  -H "Content-Type: application/json" \
  -d '{
    "brandName": "APPLE",
    "applicant": "Apple Inc.",
    "registrationNumber": "TM001",
    "niceClass": ["9", "35"],
    "notes": "备注"
  }'
```

**响应示例**

```json
{
  "success": true,
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "brandName": "APPLE",
    "applicant": "Apple Inc.",
    "createdAt": "2026-07-27T12:28:16",
    "status": "pending"
  }
}
```

**状态码**

| 状态码 | 描述 |
|--------|------|
| 200 | 添加成功 |
| 400 | 参数错误（名称为空、长度不符） |
| 409 | 商标已存在 |

---

### 更新商标

更新指定商标的信息。

**请求**

```
PUT /api/trademarks/{id}
```

**路径参数**

| 参数 | 类型 | 描述 |
|------|------|------|
| id | string | 商标 ID |

**请求体**

| 字段 | 类型 | 必填 | 描述 |
|------|------|------|------|
| brandName | string | ❌ | 商标名称 |
| applicant | string | ❌ | 申请人 |
| registrationNumber | string | ❌ | 注册号 |
| niceClass | array | ❌ | 尼斯分类 |
| notes | string | ❌ | 备注 |

**cURL 示例**

```bash
curl -X PUT http://localhost:5000/api/trademarks/550e8400-e29b-41d4-a716-446655440000 \
  -H "Content-Type: application/json" \
  -d '{
    "brandName": "APPLE UPDATED"
  }'
```

**响应示例**

```json
{
  "success": true,
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "brandName": "APPLE UPDATED",
    "updatedAt": "2026-07-27T12:28:16"
  }
}
```

**状态码**

| 状态码 | 描述 |
|--------|------|
| 200 | 更新成功 |
| 400 | 参数错误 |
| 404 | 商标不存在 |

---

### 删除商标

删除指定的商标记录。

**请求**

```
DELETE /api/trademarks/{id}
```

**路径参数**

| 参数 | 类型 | 描述 |
|------|------|------|
| id | string | 商标 ID |

**cURL 示例**

```bash
curl -X DELETE http://localhost:5000/api/trademarks/550e8400-e29b-41d4-a716-446655440000
```

**响应示例**

```json
{
  "success": true,
  "message": "删除成功"
}
```

**状态码**

| 状态码 | 描述 |
|--------|------|
| 200 | 删除成功 |
| 404 | 商标不存在 |

---

### 批量删除商标

批量删除多个商标。

**请求**

```
DELETE /api/trademarks/batch
```

**请求体**

| 字段 | 类型 | 必填 | 描述 |
|------|------|------|------|
| ids | array | ✅ | 商标 ID 数组 |

**cURL 示例**

```bash
curl -X DELETE http://localhost:5000/api/trademarks/batch \
  -H "Content-Type: application/json" \
  -d '{
    "ids": ["id1", "id2", "id3"]
  }'
```

**响应示例**

```json
{
  "success": true,
  "deletedCount": 3
}
```

**状态码**

| 状态码 | 描述 |
|--------|------|
| 200 | 删除成功 |
| 400 | 参数错误（ID 列表为空） |

---

## 状态检查

调用 WIPO API 检查商标状态和抢注情况。

### 启动检查

启动商标状态检查任务（异步执行）。

**请求**

```
POST /api/check
```

**请求体**

| 字段 | 类型 | 必填 | 默认值 | 描述 |
|------|------|------|--------|------|
| checkStatus | boolean | ❌ | true | 是否检查状态 |
| checkSquatting | boolean | ❌ | true | 是否检查抢注 |
| trademarkIds | array | ❌ | 全部 | 指定检查的商标 ID |

**cURL 示例**

```bash
curl -X POST http://localhost:5000/api/check \
  -H "Content-Type: application/json" \
  -d '{
    "checkStatus": true,
    "checkSquatting": true
  }'
```

**响应示例**

```json
{
  "success": true,
  "message": "检查已启动，请稍后查看结果"
}
```

**状态码**

| 状态码 | 描述 |
|--------|------|
| 200 | 启动成功 |
| 400 | 没有可检查的商标 |

---

### 查询检查状态

查询检查任务的执行状态和结果。

**请求**

```
GET /api/check/status
```

**cURL 示例**

```bash
curl -X GET http://localhost:5000/api/check/status
```

**响应示例**

```json
{
  "status": "completed",
  "result": {
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
        "hasIssue": true,
        "hasSquatting": true,
        "abnormalStatuses": [
          {
            "registrationNumber": "902462164",
            "status": "Expired",
            "office": "BG"
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
    ]
  }
}
```

**检查状态值**

| 状态 | 描述 |
|------|------|
| running | 检查进行中 |
| completed | 检查完成 |
| error | 检查出错 |

**响应字段说明**

| 字段 | 类型 | 描述 |
|------|------|------|
| total | number | 总检查数 |
| checked | number | 已完成数 |
| errors | array | 错误详情列表，每项含 `brandName` / `error`，检查失败时含 `stage` |
| errorsCount | number | 错误数 |
| failedBrands | array | 检查未完成的商标名列表 |
| hasIssues | boolean | 本次检查是否存在异常或抢注（汇总标志） |
| alerts | array | 告警列表 |

**告警字段说明**

| 字段 | 类型 | 描述 |
|------|------|------|
| brandName | string | 品牌名称 |
| issues | array | 问题列表 |
| hasIssue | boolean | 是否有状态问题 |
| hasSquatting | boolean | 是否有抢注嫌疑 |
| abnormalStatuses | array | 异常状态详情 |
| squattingCases | array | 抢注案例详情 |
| needsReviewCases | array | 中/低风险待人工审核案例（高风险见 squattingCases） |

**抢注风险等级**（与源码 `wipo_client.check_squatting` 实现一致）

| 等级 | 触发条件 | 归属字段 |
|------|----------|----------|
| high | 品牌名仅相似、非精确匹配（子串包含且长度达标） | squattingCases |
| medium | 品牌名精确匹配但申请人不同（无语言差异） | needsReviewCases |
| low | 品牌名精确匹配且双方含中日韩/阿拉伯文（存在语言差异） | needsReviewCases |

**状态码**

| 状态码 | 描述 |
|--------|------|
| 200 | 返回检查状态 |

---

## 检查报告

查看历史检查报告。

### 获取报告列表

获取所有历史检查报告。

**请求**

```
GET /api/reports
```

**cURL 示例**

```bash
curl -X GET http://localhost:5000/api/reports
```

**响应示例**

```json
{
  "success": true,
  "data": [
    {
      "filename": "report_20260727_122816.json",
      "timestamp": "2026-07-27T12:28:16",
      "total": 10,
      "alertsCount": 9
    }
  ]
}
```

**状态码**

| 状态码 | 描述 |
|--------|------|
| 200 | 成功 |

---

### 获取报告详情

获取指定报告的完整内容。

**请求**

```
GET /api/reports/{filename}
```

**路径参数**

| 参数 | 类型 | 描述 |
|------|------|------|
| filename | string | 报告文件名 |

**cURL 示例**

```bash
curl -X GET http://localhost:5000/api/reports/report_20260727_122816.json
```

**响应示例**

```json
{
  "success": true,
  "data": {
    "timestamp": "2026-07-27T12:28:16",
    "total": 10,
    "alertsCount": 0,
    "alerts": [],
    "results": []
  }
}
```

> **报告文件里不再重复存 `alerts`**。告警就是 `results` 中「有问题」的那部分，
> 存两份实测占报告体积约 29% 且可能不一致。新报告只存 `alertsCount` 与 `results`；
> 本接口在缺 `alerts` 时用 `monitor.alerts_from_results()` 从 `results` 还原，
> 旧报告自带的 `alerts` 原样返回 —— 两种格式对调用方完全一致。

**状态码**

| 状态码 | 描述 |
|--------|------|
| 200 | 成功 |
| 404 | 报告不存在 |

> **摘要文件**：每次检查除完整报告外还会写一份
> `reports/report_<时间戳>.summary.json`（只有统计与告警定位：品牌名 / `trademarkId` /
> `issues` / 各类案例计数，无案例明细），供看板与 AI 低 token 消费。
> `GET /api/reports` **不会**列出摘要文件，避免同一次检查被计两次。

---

## 导入导出

商标数据的批量导入和导出。

### 导出商标

导出所有商标数据。

**请求**

```
GET /api/export
```

**查询参数**

| 参数 | 类型 | 必填 | 默认值 | 描述 |
|------|------|------|--------|------|
| format | string | ❌ | json | 导出格式：`json` 或 `csv` |

**cURL 示例**

```bash
# JSON 格式
curl -X GET "http://localhost:5000/api/export?format=json" \
  -o trademarks.json

# CSV 格式
curl -X GET "http://localhost:5000/api/export?format=csv" \
  -o trademarks.csv
```

**响应**

- `format=json`: `application/json` 格式的商标数据
- `format=csv`: `text/csv` 格式的文件下载

**状态码**

| 状态码 | 描述 |
|--------|------|
| 200 | 导出成功 |
| 400 | 不支持的格式 |

---

### 导入商标

上传并导入商标数据文件。

**请求**

```
POST /api/import
```

**请求体 (multipart/form-data)**

| 字段 | 类型 | 必填 | 描述 |
|------|------|------|------|
| file | file | ✅ | JSON 或 CSV 文件 |

**cURL 示例**

```bash
curl -X POST http://localhost:5000/api/import \
  -F "file=@trademarks.json"
```

**响应示例**

```json
{
  "success": true,
  "importedCount": 5,
  "data": []
}
```

**状态码**

| 状态码 | 描述 |
|--------|------|
| 200 | 导入成功 |
| 400 | 参数错误或格式不支持 |

---

## 系统

### 获取接口契约

返回本项目的机器可读接口契约（即 `docs/api-schema.json` 的内容）。

**请求**

```http
GET /api/schema
```

**响应示例**

```json
{
  "success": true,
  "data": {
    "version": "1.1.0",
    "updatedAt": "2026-10-16",
    "owner": "web_app.py",
    "routes": [],
    "categories": {}
  }
}
```

**字段说明**

| 字段 | 类型 | 描述 |
|------|------|------|
| data.version | string | 契约版本 |
| data.updatedAt | string | 契约更新日期（YYYY-MM-DD） |
| data.owner | string | 契约维护者（路由实现所在模块） |
| data.routes | array | 扁平路由清单，元素含 `method` / `path` / `endpoint` |
| data.categories | object | 按业务域分组的接口定义，供文档站渲染 |

> 该接口用于让 AI 助手、前端工具与 CI 直接消费接口清单，
> 无需解析 Markdown 或读取源码。

**状态码**

| 状态码 | 描述 |
|--------|------|
| 200 | 成功 |
| 500 | 契约文件缺失或无法解析 |

---

## 通用状态码说明

| 状态码 | 描述 |
|--------|------|
| 200 | 请求成功 |
| 400 | 请求参数错误 |
| 404 | 资源不存在 |
| 409 | 资源冲突（如重复创建） |
| 500 | 服务器内部错误 |

## 数据模型

### Trademark 对象

```typescript
{
  id: string;              // UUID
  brandName: string;       // 商标名称
  applicant?: string;      // 申请人
  registrationNumber?: string;  // 注册号
  niceClass?: string[];    // 尼斯分类
  notes?: string;          // 备注
  createdAt: string;       // ISO 8601 日期
  updatedAt?: string;      // ISO 8601 日期
  status: 'pending' | 'checked' | 'error';
}
```

### Alert 对象

```typescript
{
  brandName: string;
  issues: string[];
  hasIssue: boolean;
  hasSquatting: boolean;
  abnormalStatuses: Array<{
    registrationNumber: string;
    status: string;       // Expired, Cancelled, Refused 等
    office: string;       // 商标局代码
  }>;
  squattingCases: Array<{
    brandName: string;
    applicant: string;
    riskLevel: 'high' | 'medium' | 'low';
  }>;
  needsReviewCases: Array<{        // 中/低风险待人工审核案例（高风险在上方 squattingCases）
    brandName: string;
    applicant: string;
    applicantCountryCode?: string;
    status?: string;
    registrationNumber?: string;
    niceClass?: string[];
    office?: string;
    score?: number;
    isExactMatch: boolean;         // true=品牌名精确匹配（中/低风险）；false=仅相似（高风险）
    riskLevel: 'high' | 'medium' | 'low';
    reason: string;                // 风险原因（中文说明）
  }>;
}
```
