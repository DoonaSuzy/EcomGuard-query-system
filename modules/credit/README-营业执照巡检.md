# 营业执照状态自动巡检 · 使用说明

> 面向销售部门：批量核验一批公司主体的工商登记状态，异常自动提醒。
> 数据源：信用中国（creditchina.gov.cn）官方公开数据。合规边界见文末。

## 两级巡检

| 节奏 | 何时 | 查什么 | 通知谁 |
| --- | --- | --- | --- |
| **每日** | 每天 09:00 | ①营业执照状态 ②**巡检服务自身是否健康**（依赖/浏览器/站点/SMTP/名单/心跳） | **业务人员**（`MAIL_TO_BUSINESS`） |
| **每月** | 每月 1 日 09:05 | 营业执照状态汇总 | **主管**（`MAIL_TO_MANAGER`），仅在有告警时发 |

两级共用同一套巡检代码，靠 `--mode daily|monthly` 区分收件人与标题前缀。
「服务自检」只在日报里出现：它回答的是「这套巡检自己还跑不跑得动」，
主管看月报时不需要这份运维噪声。

> 全绿默认不发信（每天一封「全部正常」会被当垃圾邮件）。
> 代价是「任务被禁用」这类问题没有输出——用自检里的**心跳项**补偿。

---

## Web 控制台（可选）

除命令行与计划任务外，还带一个网页控制台：粘贴企业名单 → 提交 → 看进度 → 下载 CSV。

```powershell
# 1) 构建前端（只需一次；改前端才需要重跑）
cd frontend
npm install
npm run build

# 2) 启动后端，浏览器打开 http://127.0.0.1:8000/
cd ..
.\.venv\Scripts\python.exe -m uvicorn api:app --port 8000
```

- 控制台与 `/api` **同源**由 `api.py` 托管，不需要配 CORS、不需要 nginx；
- 开发态（热更新）用 `cd frontend && npm run dev`，vite 自带 `/api` 代理；
- 没构建 `frontend/dist` 时后端静默退化为纯 API 服务，不影响任何接口；
- 页面能力、设计取舍与提交前自查见 [frontend/README.md](frontend/README.md)。

---

## 1. 一句话用法

```bash
# 0) 激活本目录的独立环境（不要用系统 Python）
cd F:\projects\GO1\reverse-records
.\.venv\Scripts\Activate.ps1        # 或直接用绝对路径调用，见下

# 1) 准备名单（每行一个企业全称，# 开头为注释）
#    例：companies.txt

# 2) 批量查询，产出 CSV
python credit_check.py companies.txt -o result.csv

# 3) 异常推送主管（有 alert/notfound/unknown 才推送）
python notify.py result.csv --webhook "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
```

不想激活环境就全程用绝对路径：

```powershell
$py = 'F:\projects\GO1\reverse-records\.venv\Scripts\python.exe'
& $py credit_check.py companies.txt -o result.csv
& $py notify.py result.csv --webhook "https://..."
```

想全自动（含定时）→ 直接看 [§6 定时巡检](#6-定时巡检)。

运行环境（本机已就绪）：

| 项 | 值 |
| --- | --- |
| Python | `F:\projects\GO1\reverse-records\.venv\Scripts\python.exe` |
| 依赖 | `camoufox`、`ddddocr`、`Pillow`，以及 HTTP 接口用的 `fastapi`/`uvicorn`（版本锁定见 `requirements.txt`，41 条） |
| 工作目录 | `F:\projects\GO1\reverse-records` |
| 回归测试 | `.venv\Scripts\python.exe -m pytest test_regression.py -q`（180 项，离线） |

> **本目录自带独立 venv，不要用系统 Python 或别的项目的 venv 跑。**
> 改造前脚本借用 `camoufox-reverse-mcp` MCP 服务端的 venv，那个 venv 归别的项目所有，
> 对方重装就会把 `ddddocr` 抹掉、巡检直接失效。现已隔离。

从零重建环境：

```powershell
cd F:\projects\GO1\reverse-records
& 'C:\Users\10911\AppData\Local\Programs\Python\Python313\python.exe' -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt `
    -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 60 --retries 3
```

> Camoufox 浏览器本体（954 MB，版本 135.0.1-beta.24）装在
> `%LOCALAPPDATA%\camoufox\camoufox\Cache`，**不在 venv 内**，新建 venv 不需重下。
> 若浏览器丢失：`.venv\Scripts\python.exe -m camoufox fetch`。

---

## 2. 数据链路（已逆向证实）

```
搜索页(带 keyword 自动触发)
   └─ GET public.creditchina.gov.cn/private-api/catalogSearchHome
        keyword / tableName=credit_xyzx_tyshxydm / searchState=2 / page / pageSize
        ← { status:1, data:{ total, list:[{accurate_entity_name,
                                            accurate_entity_code, uuid,
                                            entityType, recid}] } }
   ↓ 取 uuid + entityType
详情页 xyDetail.html?searchState=1&entityType=..&keyword=..&uuid=..
   └─ GET .../private-api/getTyshxydmDetailsContent
        ← { data:{ headEntity:{ jgmc, tyshxydm, status, record_source },
                   data:{ table_name, dataSource, entity:{...} } } }
```

**登记状态字段**：`data.headEntity.status`（页面名称旁显示的就是它，取值如「存续」）。

**验证码闸门**：搜索接口被 `vcodeswitch=1` 强制拦截。
- 服务端校验会话，未过码直接返回 `{"status":10001,"message":"验证码已失效，请重新申请验证码！"}`
- 过码成功 → `setCookieExpireInOneMinute("vcode","1")`，实际有效期 **5 分钟**（`5*60*1000`，函数名有误导）
- 即：**过一次码，5 分钟内可连续查数十家**，这是本方案能低成本跑量的关键。

---

## 3. 判定规则（重要，影响告警口径）

| 判定 | 触发条件 | 含义 | 处理 |
| --- | --- | --- | --- |
| `ok` 正常 | status ∈ 存续/在业/在营/开业/有效/正常 | 主体在册 | 无需动作 |
| `alert` 异常 | status 含 吊销/注销/撤销/迁出/停业/清算/歇业/异常 | 主体已退出经营 | **提醒主管** |
| `notfound` 未收录 | 搜索 `total=0` | 信用中国未收录该主体 | **提醒主管 + 人工核验** |
| `unknown` 待复核 | 验证码失败 / 响应缺失 / **无精确同名主体** | 本次没查成 | **提醒主管 + 人工复核** |

**`unknown` 也会触发提醒**（2026-10-18 修正）。50 条样例实跑中有 12 条因名称不精确落到 `unknown`；
若不计入提醒，一批写错的名字会得到「本期无异常」的静默通过——这是最危险的失败模式。
提醒报告把它单列为「需人工复核」一节，并附前 3 个候选全称，**不与确定异常混在一张表**。

### 名称匹配规则（防假阳性）

**只认全称精确相等**（去空白、全角括号归一后比较）。不做模糊兜底。

实测反例（已修复）：

| 输入 | 模糊匹配会误判为 | 实际 |
| --- | --- | --- |
| 京东世纪贸易有限公司 | 上海京东世纪贸易有限公司（存续） | 名称不完整 |
| 字节跳动有限公司 | 跳动字节科技(北京)有限公司（存续） | 名称不完整 |

把别家公司的「存续」当成输入公司的状态，对销售是**假阳性**，比查不到更危险。
现在这类情况返回 `unknown` 并列出候选全称，让人工核对后重查。

### 为什么「未收录」要当告警

实测证据：

| 企业 | 实际状态 | 信用中国搜索结果 |
| --- | --- | --- |
| 石家庄三鹿集团 | 已吊销注销 | `total=0` |
| 深圳市快播科技 | 已注销 | `total=0` |
| 钰诚国际控股集团 | 已吊销 | `total=0` |
| 阿里巴巴(中国) | 存续 | `total=21` |
| 上海冠生园食品 | 存续 | `total=5` |

其详情表名为 `credit_scjdglzj_fr_gscxqy` —— **工商存续企业**。
即该索引只收在册主体，已注销/吊销主体查不到。

> 反向注意：`昆明泛亚有色金属交易所` 虽被处罚但**仍是存续**，返回「存续」是正确结果。
> 行政处罚 ≠ 主体注销，两者不要混为一谈。

---

## 4. 验证码识别实测数据（可复现）

| 方案 | 样本 | 提交成功率 | 平均过码次数 |
| --- | --- | --- | --- |
| ddddocr 原图 | 20 张 | **45.0%**（9/20） | ~2.2 次 |
| ddddocr 3× 放大灰度 | 19 张 | **52.6%**（10/19） | ~1.9 次 |

脚本：`bench_captcha.py`、`bench_ocr_variants.py`（结果可重跑复现）。

结论：单次识别率约五成，但**换图重试**把「过码」变成确定事件——脚本内置 12 次重试，
实测首次查询 1–5 次内必过；一旦通过，5 分钟内不再触发。

**验证码规格**：`GET /private-api/verify/getVerify` → `image/jpeg`，95×25 px，4 位数字+字母（大小写混合）。

---

## 5. 风控与频率策略

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| 企业间间隔 | 3.5–6.5 s 随机 | `--min-delay/--max-delay` 可调 |
| 单企业重试 | 2 次 | 失败退避 4 s |
| 验证码重试 | 12 次 | 每次换图 |
| 过码窗口复用 | 5 分钟 | 窗口内不重复过码 |
| 单次批量建议 | ≤ 50 家 | 超过则分批，批间休息 5–10 分钟 |

实测吞吐：4 家约 36 s（含过码）；窗口内 3 家连续查询约 20 s（3.5–6.5 s/家）。

**降风控要点**（已内建）：
- Camoufox 指纹伪装（非裸 requests），瑞数 412 由浏览器自动通过
- 人类节奏随机间隔，非固定频率
- 只走页面正常浏览路径，不直连接口、不伪造签名

---

## 6. 定时巡检

### 方式 A（推荐）：一键注册计划任务

```powershell
cd F:\projects\GO1\reverse-records

# 1) 先看会做什么，不改系统
.\install_schedule_task.ps1 -Action DryRun -Webhook "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"

# 2) 确认无误后注册（默认：每日 09:00 + 每月 1 日 09:05 双触发器）
.\install_schedule_task.ps1 -Webhook "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"

# 3) 立刻跑一次验证配置
.\install_schedule_task.ps1 -Action RunNow -Webhook "https://..."

# 4) 查下次运行时间与上次结果（含两个触发器的中文描述）
.\install_schedule_task.ps1 -Action Status

# 5) 卸载
.\install_schedule_task.ps1 -Action Uninstall
```

参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `-Companies` | `companies.txt` | 企业名单 |
| `-Webhook` | 空 | 群机器人地址；留空则只发邮件 |
| `-Time` | `09:00` | 每日触发时刻 |
| `-MonthlyDay` | `1` | 每月几号，**上限 28**（见下） |
| `-MonthlyTime` | 每日时刻 +5 分钟 | 月度触发时刻，默认错开避免抢同一个浏览器 |
| `-NoMonthly` | 关 | 只要每日，不注册月度触发器 |
| `-Weekly` + `-DayOfWeek` | 关 / `Monday` | 退回旧的「每周一次」单触发器行为 |
| `-TaskName` | `CreditChina-LicenseCheck` | 任务名 |

`-MonthlyDay` 上限是 **28 而不是 31**：29/30/31 在 2 月不存在，而
Task Scheduler **不会顺延**到下月 1 日，而是整月静默不触发——这是最难发现的失效。

脚本会做四件事：环境自检 → 生成两份 `.bat` → 注册双触发器 → 提示下一步。
不需要管理员权限（「仅当用户登录时运行」模式）。

产物：

| 路径 | 说明 |
| --- | --- |
| `run_credit_check.bat` | **每日**入口，每次 Install/RunNow 按当前参数重建 |
| `run_credit_check_monthly.bat` | **每月**入口，与上面只差 `--mode` |
| `logs\task.log` | 每次运行的开始时间、三个退出码（自检/巡检/推送） |
| `logs\credit_check.log` | 巡检明细（轮转，单文件 5 MB × 5 备份） |
| `data\health_<时间戳>.json` | 当次服务自检的结构化结果 |
| `data\last_run.json` | 心跳，供下次自检判断「上回是否按时跑」 |
| `result_<YYYYmmdd_HHMMSS>.csv` | 每次运行独立结果，不覆盖历史 |

### 方式 B：手工命令

```powershell
$py = 'F:\projects\GO1\reverse-records\.venv\Scripts\python.exe'
$dir = 'F:\projects\GO1\reverse-records'
cd $dir
& $py credit_check.py companies.txt -o "result_$(Get-Date -f yyyyMMdd).csv"
& $py notify.py "result_$(Get-Date -f yyyyMMdd).csv" --webhook "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
```

### 退出码语义（判成败只认 1）

| 退出码 | 含义 | 计划任务里算失败吗 |
| --- | --- | --- |
| `0` | 有异常且已推送 | 否 |
| `10` | 无需推送（无异常 / 已全绿 / 未配置任何通道） | **否** |
| `1` | 发送失败 / 名单缺失 / 推送通道报错 | 是 |

> 把 `10` 当失败会误报——「无异常」本来就不该推送。

`run_credit_check.bat` 已按此实现：只有 `notify` 返回 `1`，或 `credit_check` 非 0，才 `exit /b 1`。

**服务自检的退出码（`healthcheck.py`）不参与任务成败**：它的失败正是要
写进邮件的内容，若在此 `exit 1` 就会跳过 `notify`，变成「有问题所以没发信」。
自检结果落在 `data\health_*.json`，由 `notify.py --health` 读进报告正文。

### 实现备忘（踩过的坑）

| 坑 | 表现 | 做法 |
| --- | --- | --- |
| 命令行超长/引号转义 | `schtasks /TR` 261 字符上限，多层引号易崩 | 计划任务只调 `.bat`，逻辑写在 bat 里 |
| `wmic` 取时间戳 | Win11 24H2 已弃用，本机实测**返回空值** | 改用 `powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"`（159 ms） |
| PS 5.1 读无 BOM 的 UTF-8 | 中文按 GBK 解码 → `Missing closing '}'` 等连锁报错 | `install_schedule_task.ps1` 必须存 UTF-8 **with BOM** |
| `.bat` 加 BOM | 第一行 `@echo off` 前多出字符，cmd 报错 | `.bat` 存 UTF-8 **无 BOM** + CRLF |
| `RunNow` 跑旧配置 | 参数改了但 `.bat` 还是上次的 | `RunNow` 执行前先按本次参数重建 `.bat` |
| **`New-ScheduledTaskTrigger` 没有 Monthly** | 参数集只有 Once/Daily/Weekly/Startup/Logon | 走「注册单触发器 → `Export-ScheduledTask` 取合法 XML → 插第二个 `<CalendarTrigger>` → `Register-ScheduledTask -Xml`」 |
| **CIM `MSFT_TaskMonthlyTrigger` 不能注册** | `Register-ScheduledTask -Trigger` 直接报「参数错误。」 | 只用来**读懂**月度属性，不能用于注册；验证也必须看导出 XML（CIM 只报基类 `MSFT_TaskTrigger`） |
| 克隆触发器带同 `<Id>` | 两个同 Id 的触发器 → XML 非法 | 复制出来的触发器要剥掉 `<Id>`，由系统重新分配 |
| **`StartBoundary` 丢时区** | 月度触发器变成「无偏移」，与主触发器实际时刻可能不同 | 从主触发器抠 `+08:00` 这类偏移，拼在新时间后面 |
| **cmd 括号块里的 `%ERRORLEVEL%`** | `if exist x ( set RC=%ERRORLEVEL% )` 永远读到进块前的值 | 改用 `goto :afterhealth` 跳转；不开 `delayedexpansion`（会改变所有 `%VAR%` 语义） |
| 自检失败就 `exit` | 跳过 `notify` → 有问题反而收不到信 | 自检退出码只写日志，不参与任务成败 |
| `log.exception` | 30 行 traceback 灌进日志 | 改 `log.error("...: %s", e)`，单行可读 |

推送通道三选一（也可并存）：

| 通道 | 参数 | 备注 |
| --- | --- | --- |
| 群机器人 | `--webhook URL` | 企业微信/钉钉/飞书通用，推荐 |
| 邮件 | `--smtp-host/--smtp-user/--smtp-pass/--mail-to` | 支持 STARTTLS |
| 仅打印 | 不传参 | 默认，人工看 |

也可用环境变量：`CREDIT_WEBHOOK` / `SMTP_HOST` / `SMTP_USER` / `SMTP_PASS` / `MAIL_TO`。

### 配 QQ 邮箱推送（推荐，一次性设置）

代码已就绪，**只需填凭据**——凭据只写进 `.env`，该文件已在 `.gitignore` 里，不会入库。

```powershell
cd F:\projects\GO1\reverse-records
Copy-Item .env.example .env
notepad .env      # 改 SMTP_USER / SMTP_PASS / MAIL_TO 三行
```

| 字段 | 填什么 | 说明 |
| --- | --- | --- |
| `SMTP_USER` | `你的QQ号@qq.com` | 发件账号 |
| `SMTP_PASS` | **16 位授权码** | ⚠️ 不是 QQ 登录密码 |
| `MAIL_TO` | `主管@company.com` | 多个用英文逗号分隔 |
| `SMTP_HOST`/`SMTP_PORT`/`SMTP_SSL` | `smtp.qq.com` / `465` / `1` | 模板已填好，不用改 |

**拿授权码**：QQ邮箱网页 → 设置 → 账号 → 开启「POP3/SMTP 服务」→ 按提示发短信 → 得到 16 位授权码。

**验证**（真的发一封，看是否收到）：

```powershell
.\.venv\Scripts\python.exe notify.py samples\expected_actual.csv --always
```

- 退出码 `0` = 已发送；`1` = 发送失败（看 `logs\notify.log`）；`10` = **没有推送**，有两种原因：① 结果全正常且没加 `--always`；② 没配出任何通道（`SMTP_HOST`/`MAIL_TO` 都空，或只配了一半）。
- ⚠️ 所以 `--always` **只解决原因①**。如果 `.env` 没填好（例如 `MAIL_TO` 漏了），即使加了 `--always` 仍然是 `10`——此时屏幕上会打印「未配置推送通道，仅打印」。看到 `10` 先看这行提示，别以为邮件发出去了。
- 465 端口是隐式 SSL，代码走 `smtplib.SMTP_SSL`；若填 587 会自动切 STARTTLS。
- `.env` 优先级低于真实环境变量，方便 CI/计划任务临时顶替。

**接到计划任务**：`-Webhook` 参数只管群机器人；邮件走 `.env`，`run_credit_check.bat` 里调 `notify.py` 时不传 SMTP 参数，会自动读 `.env`。所以配好 `.env` 后**不需要**重新 Install 计划任务。

---

### 用 HTTP 接口跑（可选，给其他系统调用）

脚本适合人工/定时；如果要让 CRM、内部平台按需触发，用自带的 FastAPI 服务。
**它不替代脚本**——内部还是同一个 `credit_check.py`，只是把"提交名单"变成一次 HTTP 调用。

```powershell
cd F:\projects\GO1\reverse-records
.\.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000/docs> 有交互式文档（Swagger，FastAPI 自动生成）。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/api/health` | 健康检查，返回 `{"status":"ok","db":true,"queued":0}` |
| `POST` | `/api/jobs` | 提交作业，**202** 立即返回 `job_id`（不阻塞） |
| `GET` | `/api/jobs` | 作业列表（`?limit=50`） |
| `GET` | `/api/jobs/{id}` | 作业状态 + 全部结果明细 |
| `GET` | `/api/jobs/{id}/results` | 只取明细，可 `?verdict=alert` 过滤 |
| `GET` | `/api/jobs/{id}/csv` | 下载 CSV（未完成返回 **409**） |
| `DELETE` | `/api/jobs/{id}` | 取消**排队中**的作业（已开始返回 **409**） |

> **所有业务接口都在 `/api` 前缀下**，页面路径（`/overview`、`/jobs`、`/jobs/<id>`）留给
> 前端路由。这不是风格问题：`/jobs` 既是「作业列表页」又是「作业列表接口」的写法会让
> 浏览器直接打开/刷新 `/jobs` 时拿到一段 JSON 而不是控制台。加前缀后两者天然分离，
> `GET /openapi.json` 里也不允许出现任何不带 `/api` 的路径（有回归断言守着）。

提交示例：

```powershell
$body = @{
    names  = @('阿里巴巴(中国)有限公司', '石家庄三鹿集团股份有限公司')
    mail_to = '主管@company.com'   # 可选，留空则不发
    always_notify = $false          # 可选，周报场景设 true
} | ConvertTo-Json

$job = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/jobs' -Method Post `
        -Body $body -ContentType 'application/json'
$job.job_id
```

**为什么是异步**：抓取要独占一个 Camoufox 实例，且过码后有 5 分钟有效窗口，天生不能并发。
所以 `POST /api/jobs` 只登记、立即返回，真正的抓取由**单 worker 串行消费**。
好处是接口不会因为几十家跑十几分钟而超时，也避免多个请求同时开浏览器互相踩踏（风控）。
代价是：**同时提交 3 个作业 = 排队串行跑**，不是并行。

数据落 `data/jobs.db`（sqlite，含作业与逐家结果）和 `data/results/<job_id>.csv`。
`data/` 已在 `.gitignore` 里（作业库含查过的企业名称，属业务数据，不入库），只保留 `data/.gitkeep` 占位。

**换 MySQL**：把 `CREDIT_DB` 设成连接串即可，代码不用动：

```powershell
$env:CREDIT_DB = 'mysql://root:pw@127.0.0.1:3306/credit_check'
```

取值靠**有没有 `://`** 区分：普通路径 → SQLite，连接串 → MySQL。两种后端由
`Store` 内部按后端分派（占位符 `?` 在 MySQL 下自动换成 `%s`，
`INSERT OR REPLACE` 换成 `ON DUPLICATE KEY UPDATE`），对外 11 个方法完全一致。
建表仍是自动的，`CREATE TABLE IF NOT EXISTS` 幂等。

需要 `pymysql`（已列进 `requirements.txt`）。连接是「每次操作开一条」，
没有连接池 —— 作业量是批处理，不是高并发在线服务。

> 接口**没有鉴权**，默认只监听 `127.0.0.1`。要暴露到局域网必须自己加一层反向代理与认证，
> 否则等于把"批量查企业"的能力开放给任何人。

#### 前端可用的接口契约（openapi.json）

`GET /openapi.json` 返回 OpenAPI **3.1.0**，5 条路径 / 7 个操作，
可直接喂给 `openapi-typescript` 生成 TypeScript 类型，无需手写接口声明：

```powershell
# 1) 起服务后导出
Invoke-WebRequest 'http://127.0.0.1:8000/openapi.json' -OutFile openapi.json
# 2) 生成类型（注意 --default-non-nullable false，理由见下）
npx openapi-typescript openapi.json --default-non-nullable false -o src/types.d.ts
```

每个 2xx 响应都有**具名 schema**（不是 `additionalProperties: true`），
生成的类型能直接约束字段名与枚举；`TestApiContract` 里有两条断言守这件事：
`test_openapi_responses_carry_concrete_schemas` 与
`test_openapi_has_no_opaque_response_schema`（后者全量扫描所有 2xx JSON 响应）。

> **`--default-non-nullable false` 不是可选项**：Pydantic 的字段默认值会让
> openapi-typescript 默认把该字段生成成**必填**（因为 spec 里它有 `default`）。
> `CheckRequest` 的 `always_notify` / `min_delay` / `max_delay` 在服务端都是可省略的，
> 用默认参数生成后前端必须显式传这三个字段，否则 `tsc` 报 TS2322。
> 加上这个开关后它们才正确变成 `?:`。

**CORS 现状**：后端**没有** CORS 中间件（`app.user_middleware` 为空，
`OPTIONS /api/jobs` 返回 405，响应不带 `access-control-allow-origin`）。
所以本地开发用 Vite 的 dev proxy 把 `/api` 转给后端，**不要**为了图省事给后端加
`allow_origins=["*"]` —— 这个接口没有鉴权，放开 CORS 等于把「批量查企业」的能力
开放给任何网页。生产同理，走同源反向代理。

**静态托管**：后端自己托管 `frontend/dist`——`api.py` 末尾先 `app.include_router(router)`
再 `_mount_frontend(app)`：`/assets` 走 `StaticFiles`，其余路径由
`GET /{full_path:path}` 兜底回 `index.html`（vue-router history 模式的深链刷新）。
顺序反了会让 `/api/health`、`/api/jobs` 全被吞成 HTML；`TestFrontendHosting` 的断言守着这件事。
没有构建产物时后端静默降级成纯 API 服务（`GET /` 返回提示 JSON 404）。

**控制台功能**（`frontend/`，详见该目录 README）：

| 页面 | 能力 |
| --- | --- |
| 总览 `/overview` | 作业统计、最近作业、后端健康灯（15 s 轮询，三态） |
| 提交 `/submit` | 名单多分隔符解析、去重/注释计数、200 家上限、邮箱与 webhook 格式校验、间隔倒挂校验 |
| 作业列表 `/jobs` | 状态分段筛选、job_id 搜索、进度条、耗时 |
| 作业详情 `/jobs/:jobId` | 3 s 轮询活跃作业（页面隐藏时暂停）、判定筛选、关键词搜索、CSV 下载、取消排队作业 |

技术栈：Vue 3.5 + Vite 7 + TypeScript 5.9 + Pinia 4 + Vue Router 4 + openapi-fetch 0.17
（类型直接来自 `openapi.json`，后端改字段名会先炸 `npm run build`，而不是先炸用户）。
错误边界 `ErrorBoundary.vue` 接住渲染期异常避免整页白屏；轻提示用 `ToastHost`，不用 `alert`。

---

## 7. 合规边界

| 项 | 做法 |
| --- | --- |
| 数据来源 | 信用中国官方公开公示信息，属依法公开数据 |
| 访问方式 | 模拟真人浏览，非接口刷量 |
| 频率 | 低频（3.5–6.5 s/家），单批 ≤ 50 家 |
| 用途 | 内部风控核验，不对外转售、不二次分发 |
| 验证码 | 自动识别仅用于自身低频访问，不用于攻击或绕过计费 |
| 建议 | 上线前与法务确认内部使用范围；保留查询日志备查 |

> 若后续名单规模很大（数百家以上），建议改走官方数据服务接口
> （国家企业信用信息公示系统 / 信用中国数据开放），本工具仅适合中小批量巡检。

---

## 8. 文件清单

| 文件 | 作用 |
| --- | --- |
| `credit_check.py` | 主程序：批量查询 + 判定 + CSV |
| `healthcheck.py` | **服务自检**：12 项探针（依赖/浏览器/站点/SMTP/名单/心跳），产出 JSON 供报告引用 |
| `notify.py` | 异常提醒：webhook / 邮件 / 打印；按 `--mode` 分流收件人，可并入自检段 |
| `logkit.py` | 日志落盘（轮转 5 MB × 5，文件 INFO / 控制台 WARNING 且只写 stderr，不污染 stdout） |
| `install_schedule_task.ps1` | 计划任务注册（Install/Status/Uninstall/DryRun/RunNow），默认注册**每日 + 每月**双触发器 |
| `test_regression.py` | 回归测试（**189 项**，全离线） |
| `requirements.txt` | 运行依赖清单（41 条，全部 `==` 锁定；含 `fastapi`/`uvicorn`，故装完即可起 HTTP 接口） |
| `requirements-dev.txt` | CI/开发依赖（pytest + Pillow/fastapi/httpx/pydantic/PyYAML，不装 400 MB 的运行时与浏览器） |
| `tools/run_ci_lint.py` | **本地复跑 CI 的 lint 步骤**（编码/BOM 检查），避免只在 CI 上才知道脚本能不能跑 |
| `api.py` | **FastAPI 服务**：`POST /api/jobs` 提交作业、`GET /api/jobs/{id}` 查结果、`GET /api/health` 健康检查；末尾托管 `frontend/dist`（SPA 深链兜底） |
| `frontend/` | **Web 控制台**（Vue 3 + Vite + TS）：总览 / 提交 / 作业列表 / 作业详情，构建产物由 `api.py` 同源托管；见 `frontend/README.md` |
| `.env.example` | 推送凭据模板（复制成 `.env` 填 QQ 邮箱授权码；含业务/主管两组收件人） |
| `samples/cases.csv` | **50 条样例清单**（7 组，含预期判定与设计意图） |
| `samples/companies50.txt` | 50 家名单（可直接 `credit_check.py samples/companies50.txt`） |
| `samples/expected_actual.csv` | 50 条实测结果（与 `cases.csv` 预期 50/50 一致） |
| `.github/workflows/ci.yml` | GitHub Actions：`windows-latest`/`ubuntu-latest` × Python 3.12/3.13 跑 189 项离线测试，另有 lint job 做语法/编码/BOM/`.env` 检查 |
| `companies.txt` | **你的企业名单（需自建）**：纯文本，一行一家全称，`#` 开头为注释 |
| `batch8.txt` | 名单格式样例（8 家，可直接复制改） |
| `bench_captcha.py` | 验证码识别率基准 |
| `bench_ocr_variants.py` | OCR 预处理 A/B |
| `请求链路.md` | 逆向取证：请求契约 / 验证码闸门 / 字段来源 |
| `docs/API_DOC.md` | **接口契约文档**：CLI 入参、CSV 列契约、退出码、失败语义、上游接口 |
| `瑞数逆向分析技术报告.md` | 瑞数风控层技术报告（架构 / 绕过 / 解密 / 调试 / 合规） |
| `项目分析报告-信用中国营业执照巡检.md` | 需求满足度与工程完备性分析 |
| `captcha-sample.png` | 验证码样本（8 倍放大二值化） |
| `final_selftest.csv` / `batch8_result2.csv` | 自测与实测输出 |

运行期自动生成（已在 `.gitignore` 中）：`run_credit_check.bat`、`logs/`、`result_*.csv`、`.venv/`。

---

## 9. 已知限制

1. **OCR 单次约 50%**：靠重试兜底，极端情况下单家可能耗 20–30 s。
2. **未收录 ≠ 一定注销**：新设企业、名称变更、库同步延迟也会查不到 → 必须人工核验。
3. **分公司干扰**：搜索会返回同名分支，脚本按全名精确匹配；不精确时**不猜**，返回 `unknown` 并列出候选全称供核对。
4. **验证码策略变更风险**：若站点升级为行为验证（滑块/点选），本方案需重做。
5. **`accurate_entity_code` 可能为 null**：已回退取 `headEntity.tyshxydm`。
6. **名字必须写全称**：50 条实测里 C 组 10 条「名称近似陷阱」中，**9 条落到 `unknown`**（列候选供核对），
   **1 条（`阿里巴巴集团有限公司`）落到 `notfound`**——站点干脆没收录这个名称。两者都**不代表正常**，都会进提醒。
   这不是缺陷：猜错比查不到更危险；但意味着**名单质量决定可用率**，输入 `腾讯科技有限公司` 这类不存在的全称只能拿到候选列表。
7. **`notfound` 与 `unknown` 的边界不保证稳定**：同一类「名称不精确」的输入，落到哪个状态取决于站点搜不搜得到近似主体，
   属于外部数据行为，不要依赖这个区分做自动化决策——两者都按「需人工复核」处理。
8. **存续 ≠ 经营正常**：6 条「危机但主体存续」（乐视控股/金立/辉山/康得新/熊猫互娱/ofo）说明本工具
   只看**工商登记状态**，不看经营风险、失信、被执行。需要后者要另接失信被执行人/经营异常名录接口。

---

## 10. 实测记录（可复现）

| 批次 | 规模 | 耗时 | 结果 |
| --- | --- | --- | --- |
| `--selftest` | 4 家 | 36.2 s | 2 正常 / 2 未收录 |
| `batch8.txt` | 8 家 | 89.7 s | 6 正常 / 2 未知（名称不完整） |
| 计划任务 `RunNow` | 3 家 | 29.0 s | 2 正常 / 1 未收录；验证码第 1 次通过 |
| 计划任务 `RunNow`（无 webhook） | 3 家 | 79.8 s | 2 正常 / 1 未收录；`credit_check=0`、`notify=10` → 整体 `0` |
| **`samples/companies50.txt`** | **50 家** | **469.0 s** | **28 正常 / 9 未收录 / 12 待复核 / 1 状态异常** |

吞吐 ≈ **11–27 s/家**（含过码分摊，验证码重试次数波动大）。50 家实测 469 s ≈ **9.4 s/家**。

### 50 条样例的实测结论（`samples/`）

`cases.csv` 的 `预期判定` 列已按实测结果校正，现在与 `expected_actual.csv` **50/50 一致**。
校正过程中发现 4 类反直觉事实，都已写进 `设计意图` 列：

| 发现 | 案例 | 说明 |
| --- | --- | --- |
| **危机 ≠ 注销** | 乐视控股、金立、辉山乳业、康得新、熊猫互娱、ofo（拜克洛克） | 这 6 家都出过大新闻（退市/破产/关停），但**法人主体登记状态仍是「存续」**。把它们报成异常是假阳性 |
| **事业单位可查** | 清华大学 | 走 `entityType=7` + 中央机构编制委员会办公室，状态「正常」 |
| **社会组织可查** | 中国红十字会总会 | 走 `entityType=5` + 民政部，状态「注销」——**能取到非企业主体的状态** |
| **登记名带「市」** | 中国工商银行…北京**市**分行 | 输入漏了「市」字 → 精确匹配失败 → `unknown` 并列候选，不会张冠李戴 |

样例分 7 组：A-正常存续 14 / B-已注销吊销 6 / C-名称近似陷阱 10 / D-书写差异归一 6 /
E-特殊主体 5 / F-边缘案例 3 / G-危机但主体存续 6。

提醒链路已用本地打桩 webhook 实跑验证（`msgtype: markdown`，企业微信/钉钉/飞书同构）：

| 场景 | 退出码 | 行为 |
| --- | --- | --- |
| 有 `alert`/`notfound`/`unknown` + webhook 可达 | 0 | POST 成功，回执 `errcode=0` |
| 有 `alert`/`notfound`/`unknown` + 未配置通道 | 10 | 只打印，不推送 |
| 全 `ok`（无 `--always`） | 10 | 只打印，不推送 |
| webhook 不可达（连接被拒） | 1 | 单行错误进日志，无常堆栈 |

> `unknown` 自 2026-10-18 起也计入「需推送」。50 条实跑里 12 条 `unknown`，若不计入这批就静默通过了。

计划任务链路端到端实跑：`logs\task.log` 记录三个时间点与两个退出码，
`result_<时间戳>.csv` 独立落盘不覆盖历史。

复现命令：

```bash
python credit_check.py --selftest -o final_selftest.csv
python credit_check.py batch8.txt -o batch8_result2.csv
python notify.py batch8_result2.csv
python notify.py final_selftest.csv --webhook <你的群机器人地址>

# 50 条样例（约 8 分钟）
python credit_check.py samples/companies50.txt -o samples/expected_actual.csv

# 计划任务
.\install_schedule_task.ps1 -Action DryRun -Webhook "<地址>"   # 只预览
.\install_schedule_task.ps1 -Webhook "<地址>"                  # 注册
.\install_schedule_task.ps1 -Action RunNow -Webhook "<地址>"   # 立刻跑
.\install_schedule_task.ps1 -Action Status                    # 查状态
```

---

## 11. 回归测试

```powershell
cd F:\projects\GO1\reverse-records
.\.venv\Scripts\python.exe -m pytest test_regression.py -q
```

**189 项全部离线**（不联网、不开浏览器、不调 OCR），约 6 s 跑完。

| 测试类 | 项数 | 覆盖内容 |
| --- | --- | --- |
| `TestClassify` | 26 | 四态判定 `ok/alert/notfound/unknown`；26 项来自 3 组参数化（7 正例 + 12 反例 + 4 未知）加 3 条断言 |
| `TestHealthcheckProbes` | 25 | 12 项自检的单项逻辑：版本闸门、venv 判定、缺依赖点名、find_spec 抛错算缺失、浏览器未 fetch、**HTTP 412 仍算在线**、SMTP 分别报缺键、收件人回退旧 `MAIL_TO`、名单为空致命、心跳缺失/过期/损坏/naive-aware 混算、心跳原子写、作业库不存在不算失败 |
| `TestScheduleDualTrigger` | 13 | 双触发器参数、`-MonthlyDay` 上限 28、不使用不存在的 `-Monthly`、必须走 XML 合并路线、月度块生成与重复 `<Id>` 剔除、Status 从 XML 读触发器、月度失败回退单触发器、自检在巡检之前、自检失败不中断、`goto` 而非括号块、`--mode/--health` 透传、卸载清两份 bat、DryRun 显示双节奏 |
| `TestScriptIntegrity` | 12 | 依赖锁定 `==`、不指向 MCP venv、gitignore 覆盖（含 **`data/*` 与 `.gitkeep` 例外**）、**dev 依赖必须覆盖顶层 import**、**运行依赖必须覆盖 api.py 的 import（AST 校验）**、**禁止 importorskip 掩盖漏装** |
| `TestApiContract` | 16 | FastAPI `/api/health`、`POST /api/jobs`、`GET /api/jobs/{id}`、参数校验与 422、404/409 边界、OpenAPI 路由齐全、**所有业务路由必须在 `/api` 下（无前缀路由一条都不许有）**、**旧的无前缀 `/health`、`/jobs` 不许再答 JSON**、**每个 2xx JSON 响应都必须是具名 schema（不允许 `additionalProperties: true`）**、**CSV 端点声明 `text/csv` + `binary` 且真回 CSV 字节** |
| `TestNotifyModes` | 11 | 收件人分隔符容错（中英文逗号/分号/换行）、按 mode 分流、旧 `MAIL_TO` 回退、专属键优先、`should_send` 六种组合、标题前缀、自检段只列失败项、`.env.example` 记录新键 |
| `TestNotifyReport` | 8 | Markdown 报告结构、`alert/notfound/unknown` 三类进 `need`、异常表与复核节分离 |
| `TestHealthcheckAggregate` | 8 | 12 项汇总、单项失败即 exit 1、警告项不致命（`--strict` 才致命）、任何探针抛异常都降级为该项失败、`--only` 过滤、表格渲染、JSON 落盘、`--mark-run` 写心跳 |
| `TestNormName` | 7 | 名称归一：全角括号、全角空格、首尾/中间空白、空值 |
| `TestExitCodes` | 7 | `0/10/1` 分支、**半配置通道即使加 `--always` 也是 10**、**日志不得含 traceback** |
| `TestScheduleScript` | 7 | PS1 必须带 BOM、bat 无 BOM+全 CRLF、退出码语义、不用 wmic |
| `TestNotifyMainModes` | 7 | `--mode daily` 发给业务、`--mode monthly` 发给主管、无收件人 exit 10、`--always` 强制、自检致命项触发日报、月报忽略自检、非法 mode 被 argparse 拒 |
| `TestLogging` | 6 | 轮转落盘、重复 setup 幂等、不污染 stdout |
| `TestMailHtml` | 6 | 邮件 HTML 正文、异常行标红、尖括号转义、465 走 SMTP_SSL |
| `TestSampleCases` | 6 | 50 条样例格式/覆盖、与 companies.txt 一致、**预期必须等于实测**、**近似名称不许判 ok**、**危机但存续不许误报** |
| `TestDotenv` | 5 | `.env` 加载、`.env.example` 入库而 `.env` 不入库 |
| `TestCsvContract` | 3 | 列序与 `credit_check.py` 一致、UTF-8-BOM、中文回环 |
| `TestNameListParsing` | 3 | 名单解析：注释、空行、去空白 |
| `TestStatusFields` | 2 | 状态字段顺序回退（企业 vs 社会组织字段名不同） |
| `TestVerdictLabel` | 2 | 中文标签映射 |
| `TestFrontendHosting` | 7 | 前端托管契约：接口不被兜底吞掉、**页面路径（`/jobs`、`/jobs/<id>`、`/overview`）必须回 HTML 而不是 JSON**、深链回 index.html、假 id 仍答 404、兜底只接 GET、路径穿越被拒、`/docs` 与 `/openapi.json` 不受影响 |
| `TestOcrPreprocess` | 2 | 放大倍数与输出尺寸 |

> 表内项数为 `pytest --collect-only` 实测值，合计 189，与上一行"189 项全部离线"一致。

**断言有效性已用变异测试验证**：故意注入 24 类缺陷，**24/24 全部被抓出**：

| # | 注入的缺陷 | 被哪个断言抓住 |
| --- | --- | --- |
| 1 | 去掉全角括号归一 | `TestNormName` |
| 2 | 删掉 `alert` 判定分支 | `TestClassify` |
| 3 | CSV 去掉 BOM | `TestCsvContract` |
| 4 | 日志改回 stdout | `TestLogging` |
| 5 | 重复 `setup_logging` 不幂等 | `TestLogging` |
| 6 | 依赖改浮动版本 | `TestScriptIntegrity` |
| 7 | 脚本指回 MCP venv | `TestScriptIntegrity` |
| 8 | 去掉 PS1 的 UTF-8 BOM | `TestScheduleScript` |
| 9 | bat 模板改回 `wmic` | `TestScheduleScript` |
| 10 | bat 把退出码 10 当失败 | `TestScheduleScript` |
| 11 | bat 不再 `mkdir logs` | `TestScheduleScript` |
| 12 | DryRun 自己拼模板（预览骗人） | `TestScheduleScript` |
| 13 | `unknown` 不计入 `need`（静默通过） | `TestNotifyReport`（2 项） |
| 14 | `requirements-dev.txt` 漏掉 fastapi | `TestScriptIntegrity`（API 会静默 skip，CI 假绿） |
| 15 | 在 fixture 里加回 `importorskip("fastapi")` | `TestScriptIntegrity`（同上） |
| 16 | `.env` 改成按 cwd 解析（而非脚本目录） | `TestDotenv`（计划任务下凭据被静默忽略） |
| 17 | 删掉仓库根 `README.md` | `TestScriptIntegrity`（GitHub 首页会空白） |
| 18 | 「半配置通道」改判为已配置（`smtp_host and mail_to` → 只看 `smtp_host`） | `TestExitCodes`（会真去连 SMTP，`--always` 假成功变 exit 1） |
| 19 | 把名称近似陷阱某条预期改成 `ok` | `TestSampleCases`（2 项：预期≠实测 + 假阳性不变量） |
| 20 | 把「危机但存续」某条改成 `alert` | `TestSampleCases`（2 项：预期≠实测 + 误报不变量） |
| 21 | 只改 `cases.csv` 预期、不重跑实测 | `TestSampleCases::test_expected_matches_actual`（文档腐化） |
| 22 | `.gitignore` 把 `data/*` 写回 `data/` | `TestScriptIntegrity`（目录级排除让 `.gitkeep` 例外失效，作业库会被入库） |
| 23 | `.bat` 写入时改用带 BOM 的 `UTF8Encoding $true` | `TestScheduleScript`（cmd 在 `@echo off` 前多出字符） |
| 24 | 去掉 `.bat` 写入前的 CRLF 归一 | `TestScheduleScript`（裸 LF 让 cmd 解析错行） |
| 25 | `requirements.txt` 删掉 `fastapi` | `TestScriptIntegrity::test_runtime_requirements_covers_api_imports`（干净环境 `uvicorn api:app` 直接 ModuleNotFoundError） |
| 26 | `requirements.txt` 删掉 `uvicorn` | 同上（`python -m uvicorn` 找不到模块） |
| 27 | `api.py` 顶层加一个未声明的第三方 import | 同上（AST 扫描出未声明依赖） |

> 第 23、24 条对应的断言原本写的是「若 `run_credit_check.bat` 存在则校验」，
> 不存在就 `pytest.skip`——而该文件在 `.gitignore` 里，CI 干净检出必然缺席，
> 于是 CI 必出 1 个 skip，被 `ci.yml` 的「禁止 skip」规则判红。
> 现改为断言 `New-RunnerBat` → `Write-BatFile` 的**调用链**（沿本文件定义过的函数递归），
> 生成物存在时再补字节级校验；两份 `.bat`（每日/月度）都会验。

测试不是摆设——每一条断言都对应一次真实踩过的坑。

真联网的端到端验证不放进 pytest（依赖第三方站点与 OCR 随机性，不适合做 CI 断言），
跑 `credit_check.py --selftest` 或 `install_schedule_task.ps1 -Action RunNow` 即可。
