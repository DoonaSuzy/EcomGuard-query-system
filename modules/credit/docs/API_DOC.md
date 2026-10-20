# 接口契约文档 · 信用中国营业执照巡检

> 本文只记录**本仓库代码的真实契约**：每个对外接口的入参、出参、退出码、失败语义，
> 均从源码逐项核对（附行号）。上游第三方网站接口单列第 7 节，标注"逆向所得、可能变更"。
>
> 版本基准：`credit_check.py` 472 行 / `notify.py` 566 行 / `healthcheck.py` 548 行 /
> `logkit.py` 91 行 / `api.py` 661 行 / `test_regression.py` 2000 行 186 项。
> 最后核对：2026-10-19（含 `unknown` 计入告警、50 条样例实测校正、CI 依赖修复、
> 两级巡检与 12 项自检落地、FastAPI 服务与前端控制台静态托管）。

---

## 0. 组件与依赖方向

```
companies.txt ──► credit_check.py ──► result_<stamp>.csv ──► notify.py ──► webhook / SMTP
                       │                                          │
                       └────────► logs/credit_check.log            └──► logs/notify.log
                                  （均经 logkit.py 统一配置）
run_credit_check.bat = 上述两步的编排器（由 install_schedule_task.ps1 生成）
```

| 组件 | 角色 | 是否可独立运行 |
| --- | --- | --- |
| `credit_check.py` | 采集器：名单 → CSV | 是 |
| `notify.py` | 通知器：CSV → Markdown 报告 + 推送 | 是 |
| `logkit.py` | 库（非入口）：日志配置 | — |
| `run_credit_check.bat` | 编排器（计划任务入口） | 是 |
| `install_schedule_task.ps1` | 安装/卸载计划任务 | 是 |
| `bench_captcha.py` / `bench_ocr_variants.py` | 验证码识别率基准 | 是（需联网） |

---

## 1. `credit_check.py` —— 采集器

### 1.1 命令行契约

```
python credit_check.py [infile] [-o OUT] [--headful] [--selftest]
                       [--min-delay 3.5] [--max-delay 6.5]
                       [--limit 0] [--log-dir DIR]
```

| 参数 | 类型 | 默认 | 语义 | 源码 |
| --- | --- | --- | --- | --- |
| `infile` | 位置参数，可选 | 无 | 企业名单 txt，**UTF-8**，每行一个名称 | `credit_check.py:365` |
| `-o` / `--out` | str | `credit_result_<YYYYmmdd_HHMMSS>.csv` | 输出 CSV 路径 | `:366,394` |
| `--headful` | flag | False | 显示浏览器窗口（调试用） | `:367` |
| `--selftest` | flag | False | 用内置 4 家样本，**忽略 infile** | `:368,380-381` |
| `--min-delay` | float | `3.5` | 两家之间最小等待（秒） | `:369` |
| `--max-delay` | float | `6.5` | 两家之间最大等待（秒） | `:370` |
| `--limit` | int | `0`（不限） | 只查前 N 家 | `:371,388-389` |
| `--log-dir` | str | `./logs` | 日志目录 | `:372` |

**互斥与前置**：`infile` 与 `--selftest` 至少给一个，否则 `ap.error("需要企业名单文件，或用 --selftest")`（`:383-384`，argparse 以**退出码 2** 终止）。

**名单解析规则**（`:385-387`）：
- 按行 `strip()`；
- 空行丢弃；
- `#` 开头的行**整行丢弃**（注释）；
- 不做去重、不做名称归一（归一在匹配阶段做）。

### 1.2 退出码

| 码 | 含义 | 触发点 |
| --- | --- | --- |
| `0` | 跑完并写出 CSV（**即使有企业查询失败也是 0**） | `:446` |
| `1` | 名单为空 | `:390-392` |
| `2` | 参数错误（argparse） | `:384` |

> ⚠️ **契约要点**：单家企业的失败不会改变退出码，只体现在 CSV 的 `备注`/`判定` 列。
> 需要判断"是否有查询失败"必须读 CSV，不能看退出码。

### 1.3 输出 CSV 契约

**编码**：`utf-8-sig`（带 BOM，Excel 双击不乱码）—— `:416`
**行尾**：`newline=""` → Windows 下 `\r\n`
**表头固定 10 列，顺序不可变**（`:414-415`，与 `test_regression.CSV_FIELDS` 同源断言）：

| # | 列名 | 类型 | 可能取值 / 格式 | 空值表现 |
| --- | --- | --- | --- | --- |
| 1 | `输入名称` | str | 名单原文 | — |
| 2 | `匹配名称` | str | 信用中国返回的全称（可能含全角括号） | `""` |
| 3 | `统一社会信用代码` | str | 18 位；优先取搜索接口，缺失回退详情 `headEntity.tyshxydm` | `""` |
| 4 | `登记状态` | str | 站点原文，如 `存续`/`在业`/`注销`/`吊销` | `""` |
| 5 | `判定` | enum | `ok` / `alert` / `notfound` / `unknown` | 恒有值 |
| 6 | `主体类型` | str | 站点 `entityType` 原值（`1`/`2`/`4`/`5`/`6`/`7`/`8`） | `""` |
| 7 | `数据源` | str | 详情 `data.dataSource` 或 `table_name` | `""` |
| 8 | `记录数` | int | 搜索 `data.total`；无结果时 `0` | `0` |
| 9 | `详情URL` | str | 完整 `xyDetail.html?...`（含 percent-encode 的 keyword 与 uuid） | `""` |
| 10 | `备注` | str | 失败/降级原因，多段用 `; ` 连接，**最长 200 字符**（`:344`） | `""` |

**判定枚举语义**（`:112-113`, `:95-103`）：

| 判定 | 中文标签 | 含义 | 判定依据 |
| --- | --- | --- | --- |
| `ok` | 正常 | 登记状态命中在册词表 | `NORMAL = {正常,存续,在业,在营,开业,有效,登记}`（`:64`） |
| `alert` | 异常 | 登记状态命中异常词表 | `ALERT = {吊销,注销,撤销,迁出,停业,清算,歇业,责令关闭,异常,非正常,已注销,已吊销}`（`:65-66`） |
| `notfound` | 未收录 | 搜索 `total=0` | **该索引只收在册主体**，故未收录 ≈ 已注销/吊销（`:289-292`） |
| `unknown` | 待复核 | 状态为空、状态不在两张词表、或无精确同名主体 | `:96-97,103,302-306` |

**优先级**：`alert` 先于 `ok` 判断（`:99-102`），故"注销后又被标记存续"这类脏数据会判为 `alert`（保守侧）。

> ⚠️ **`unknown` 计入告警**（2026-10-18 变更）。`notify.ALERT_VERDICTS` 由 `{alert, notfound}` 扩为
> `{alert, notfound, unknown}`，另设 `HARD_VERDICTS = {alert, notfound}` 用于标题计数与异常表。
> 依据：50 条样例实跑有 12 条因名称不精确落到 `unknown`；若不计入提醒，一批写错的名字会得到
> 「本期无异常」的静默通过。报告里 `unknown` 单列「需人工复核」一节，不混进异常表。

**状态字段回退顺序**（`:67-68`，`:329-333`）：
先查 `headEntity`，再查 `data.entity`；字段名依次尝试
`status` → `org_statu` → `reg_status` → `registration_status` → `business_status` → `operating_status` → `ent_status`；取第一个非空值。

### 1.4 单次检查的内部流程与失败语义

`check_one(name)`（`:234-349`）最多尝试 **2 轮**（`:240`），异常时第 1 轮后 `sleep(4)` 再试（`:346-348`）。

| 阶段 | 失败表现（写入 `备注`） | 是否重试 |
| --- | --- | --- |
| 验证码未通过 | `验证码未通过` | 是（下一轮） |
| 搜索响应 25s 内未到 | `未收到搜索响应` | 是 |
| 服务端 vcode 会话失效（`status=10001`） | 重新过码 + 重发 `searchAjax(1)`；仍失败则 `验证码会话反复失效` | 是 |
| 搜索返回非成功码 | `搜索失败 status=<n> msg=<msg>` | 是 |
| 详情响应 25s 内未到 | `; 详情响应缺失` | 是 |
| 详情取不到状态字段 | `; 未取到状态字段`，判定 `unknown` | 否（正常返回） |
| 无精确同名主体 | `无精确同名主体，未判定状态。候选：A、B、C（请核对全称后重查）` | 否（正常返回） |
| 未捕获异常 | `<ExceptionType>: <msg>`（截断 200） | 第 1 轮后重试 |

> **设计约束（`:297-306`）**：**不做模糊兜底**。同名匹配用 `norm_name()` 归一后**完全相等**才算命中；
> 宁可返回 `unknown` + 候选列表，也不把别家的状态当成输入企业的状态（对销售是假阳性，比查不到更危险）。

`norm_name()`（`:88-92`）：全角 `（）`→半角 `()`、删除全角空格 `\u3000`、删除所有空白、`strip()`。

### 1.5 限速契约

`sleep_between()`（`:351-352`）：每两家之间 `random.uniform(min_delay, max_delay)`，默认 **3.5–6.5 秒**。
最后一家之后不再等待（`i < len(names)` 判断，`:411-412`）。

### 1.6 内置自测样本（`SELFTEST`，`:355-360`）

| 名称 | 预期判定 | 用途 |
| --- | --- | --- |
| 阿里巴巴(中国)有限公司 | `ok` | 在册正例 |
| 腾讯科技(深圳)有限公司 | `ok` | 在册正例（含半角括号归一验证） |
| 石家庄三鹿集团股份有限公司 | `notfound` | 已注销反例 |
| 深圳市快播科技有限公司 | `notfound` | 已注销反例 |

---

## 2. `notify.py` —— 通知器

### 2.1 命令行契约

```
python notify.py CSV [--webhook URL] [--smtp-host H] [--smtp-port 587]
                     [--smtp-user U] [--smtp-pass P] [--mail-to A,B]
                     [--no-tls] [--always] [--log-dir DIR]
```

| 参数 | 默认 | 环境变量回退 | 源码 |
| --- | --- | --- | --- |
| `CSV`（位置） | 必填 | — | `notify.py:129` |
| `--webhook` | `""` | `CREDIT_WEBHOOK` | `:130` |
| `--smtp-host` | `""` | `SMTP_HOST` | `:131` |
| `--smtp-port` | `587` | `SMTP_PORT` | `:132` |
| `--smtp-user` | `""` | `SMTP_USER` | `:133` |
| `--smtp-pass` | `""` | `SMTP_PASS` | `:134` |
| `--mail-to` | `""` | `MAIL_TO`（逗号分隔多收件人） | `:135` |
| `--no-tls` | False | — | `:137` |
| `--always` | False | — | `:138`（无异常也发送） |
| `--log-dir` | `./logs` | — | `:139` |

### 2.2 退出码（**与 credit_check 语义不同，注意区分**）

| 码 | 含义 | 源码 |
| --- | --- | --- |
| `0` | 至少一个通道发送成功且**无任何通道报错** | `:190` |
| `10` | 无需推送（无异常且未加 `--always`）**或** 未配置任何通道（仅打印） | `:160`, `:186` |
| `1` | CSV 不存在 / 所有通道失败 / 部分通道报错 | `:150`, `:190` |

> `run_credit_check.bat:28` 据此判定：**只有 `1` 算失败**，`0` 与 `10` 都算成功。

### 2.3 报告契约

`build_report(rows) -> (title, markdown, need)`（`:54`）：
- `need` = 需要关注的行的子集（`alert` / `notfound` / `unknown`，用于判断是否推送）；
- `title` 与 `markdown` 结构受 `test_regression.TestNotifyReport`（9 项）断言约束；
- **Markdown 全文同时打印到 stdout**（`:154`），因此未配通道时也能在计划任务日志里看到报告。
- 报告分两节：**需关注主体**（`alert`+`notfound` 的表格）与**需人工复核**（`unknown` 列表，附候选全称）；
  标题的「N 家异常」只数 `HARD_VERDICTS`，整批都是 `unknown` 时标题改为「N 家待复核」。

### 2.4 推送通道

| 通道 | 函数 | 触发条件 | 失败聚合 |
| --- | --- | --- | --- |
| Webhook | `send_webhook(url, title, md)`（`:87`） | `--webhook` 非空 | 记入 `errors`，不中断另一通道 |
| SMTP 邮件 | `send_mail(host, port, user, password, to, title, md, use_tls)`（`:111`） | `smtp_host` **且** `mail_to` 均非空 | 同上 |

两通道**互相独立**：一个失败不影响另一个（`:164-181`）。

---

## 3. `logkit.py` —— 日志库

```python
setup_logging(
    name: str,
    *,
    level: int = logging.INFO,
    log_dir: Path | None = None,
    max_bytes: int = MAX_BYTES,          # 5 MiB
    backup_count: int = BACKUP_COUNT,
    console: bool = True,
    console_level: int = logging.WARNING,
) -> logging.Logger                      # logkit.py:33-42

log_path(name: str, log_dir: Path | None = None) -> Path   # logkit.py:88
```

**契约要点**：
- 返回**已配置的 logger**（不是路径；要路径用 `log_path()`）；
- 幂等：重复 `setup_logging` 同名直接返回，不叠加 handler（模块级标记 `_MARK = "_credit_logkit_configured"`，`:30`、`:52-53`）；
- 两个 handler：**文件**（`level=INFO`，轮转 5 MiB）+ **控制台**（默认 `WARNING`，且写 **stderr**）；
- 之所以控制台只到 stderr：脚本本身用 `print` 往 stdout 输出进度与报告，日志若也写 stdout 会污染
  被管道消费的那份输出（`:47-50` 注释原文）；
- **日志内不得出现 traceback**（`TestExitCodes` 断言）——异常必须被捕获后转成 `备注` 文本。

---

## 4A. `api.py` —— HTTP 服务契约

启动：

```
.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000
```

交互式文档：`http://127.0.0.1:8000/docs`（前端控制台在同一端口 `/`）。

### 4A.1 接口清单

| 方法 | 路径 | 语义 | 状态码 |
| --- | --- | --- | --- |
| `GET` | `/api/health` | 健康检查：`db`/`queued`/`time` | 200 `ok` / 200 `degraded`（DB 不可用） |
| `POST` | `/api/jobs` | 提交作业，单 worker 串行消费 | **202**（已登记）/ 422（名单为空或超 200 家） |
| `GET` | `/api/jobs` | 最近作业列表 | 200 |
| `GET` | `/api/jobs/{job_id}` | 状态 + 结果明细 | 200 / **404**（作业不存在） |
| `GET` | `/api/jobs/{job_id}/results` | 明细，可按 `verdict` 过滤 | 200 / 404 |
| `GET` | `/api/jobs/{job_id}/csv` | 下载 CSV（UTF-8 BOM） | 200 / 404 / **409**（未完成） |
| `DELETE` | `/api/jobs/{job_id}` | 取消**排队中**的作业 | 200 / 404 / 409（非 queued） |
| `GET` | `/api/schedules` | 定时巡检列表 | 200 |
| `POST` | `/api/schedules` | 新建定时巡检 | **201** / 422（字段校验失败） |
| `PUT` | `/api/schedules/{schedule_id}` | 修改定时巡检 | 200 / 404 |
| `POST` | `/api/schedules/{schedule_id}/toggle?enabled=` | 启用/停用 | 200 / 404 |
| `POST` | `/api/schedules/{schedule_id}/run` | 立刻跑一次（不顺延下次时间） | **202** / 404 / 409（名单为空） |
| `DELETE` | `/api/schedules/{schedule_id}` | 删除定时巡检 | 200 / 404 |
| `GET` | `/api/schedules/preview` | 预览下次触发时间（不落库） | 200 / 422 |

**`POST /api/jobs` 入参**（`api.py:242` `JobRequest`）：

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `names` | `list[str]` | 必填 | 企业全称**或 18 位统一社会信用代码**，1–200 条；清洗空行/`#` 注释并去重，清洗后为空 → 422 |
| `webhook` | `str \| None` | `None` | 企业微信/钉钉/飞书 webhook，留空则不发；**必须含主机名**（裸 `https://` 视为非法，422 而不是跑完才发现推不出去） |
| `mail_to` | `str \| None` | `None` | 收件人，分隔符支持 `,` `;` 及中文 `，` `；` 与换行；每条都校验 `x@y.z`，任一不合法 → 422；留空则不发邮件 |
| `always_notify` | `bool` | `False` | 无异常也推送（周报用） |
| `min_delay` / `max_delay` | `float` | `3.5` / `6.5` | 两家之间随机间隔区间；`max < min` → 422 |

**为什么是 202 而不是 200**：作业只是入队，抓取要几分钟到十几分钟。
同步接口会让调用方一直挂着直到超时。轮询 `GET /api/jobs/{id}` 看 `state`
（`queued`→`running`→`done`/`failed`/`cancelled`）即可。

**为什么单 worker 串行**（`api.py:7-14` docstring）：抓取依赖一个有状态、
不可并发的资源——一个 Camoufox 实例 + 5 分钟有效期的 `vcode` 会话，
且目标站有瑞数风控，并发等于自伤。`--workers 1` 同理：多进程各自起 worker
会让串行保证失效。

### 4A.1b 定时巡检（`schedule.py` + `/api/schedules`）

销售部原先每周手动把这批公司名复制进国家企业信用信息公示系统，所以「能不能设成每天/每周/每月自动跑」
是使用方提的第一需求。调度逻辑在 `schedule.py`（纯函数 + 一个 30 秒 tick 的线程），HTTP 只是它的读写口。

**为什么不接受 cron 表达式**：目标用户是销售主管，`0 30 9 * * 1` 对他们是纯负担；
本项目也只需要三种节奏。故入参是 `freq ∈ {daily, weekly, monthly}` + `at_time`（`HH:MM`）。

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `name` | `str` | 必填 | 调度名称，1–100 字 |
| `enabled` | `bool` | `True` | 是否启用 |
| `freq` | `daily` / `weekly` / `monthly` | 必填 | 频率 |
| `at_time` | `str` | 必填 | `HH:MM`（24 小时制，`^([01]\d|2[0-3]):[0-5]\d$`），`9:00` 这种 → 422 |
| `weekday` | `int` | `0` | `weekly` 用；**0=周一 … 6=周日** |
| `day` | `int` | `1` | `monthly` 用；**1–28** |
| `names` | `list[str]` | 必填 | 同 `POST /api/jobs`：企业全称**或 18 位统一社会信用代码**，1–200 条 |
| `webhook` / `mail_to` / `always_notify` / `min_delay` / `max_delay` | — | — | 同 `POST /api/jobs`，触发时原样透传 |

**推荐用信用代码而不是全称**：实测「北京字节跳动科技有限公司」按名称查只会得到
候选「跳动字节科技(北京)有限公司」（判定 unknown）；统一社会信用代码是精确键，
命中即 1 条记录。已注销/吊销的主体在信用中国查不到代码，只能留全称——
这类条目巡检结果是 notfound，正是要人看一眼的信号。开箱可用的名单见
`modules/credit/巡检名单.json`（63 家，含 5 家已注销样本）。

**`day` 上限为什么是 28 而不是 31**：「每月 31 号」在 2/4/6/9/11 月不存在，
顺延到月末还是跳过都有坑；限 28 后每个月都有这一天，行为零歧义。真要月末跑用 `weekly` 或 `daily`。

**几处刻意的取舍**（都属于「不说清楚就会被当成 bug」的）：

| 行为 | 选择 | 理由 |
| --- | --- | --- |
| 手动「立刻执行」 | **不顺延** `next_run_at` | 周五点一下看看，不该把周一的定时冲掉 |
| 停用后重新启用 | 按**当前时间**重算下次 | 否则停两周再一开，会立刻补跑一个早已过期的时刻 |
| 修改频率/时刻 | **必须重算** `next_run_at` | 不重算就还按旧节奏跑，改了等于没改 |
| `preview` | 只算不落库 | 用户还在填表，写库会产生半成品配置；也让他保存前就能看见「居然排到下个月」 |
| 服务停机错过触发 | 只**补跑一次** | 停机三天回来补三天，等于把上游打一遍 |
| tick 顺序 | 先推进 `next_run_at` 再提交作业 | 否则提交完成后崩溃重入会把同一时刻触发两次 |

调度线程与 worker 一起挂在 lifespan 上：进入顺序 wipo → credit，退出反序（先停调度再停 worker），
否则收尾时调度还在往队列里塞作业，那些作业会被静默丢弃。`--workers 1` 同样是硬约束：
多进程各自起一个调度线程，同一条调度会被触发多次。

**健康检查**：`GET /api/health` 的 `queued` 会把调度触发的作业一起算进去；调度本身的状态看 `list` 里的
`last_job_id` / `last_state` / `last_error`——推送失败会落到 `last_error`，不再只写日志。

### 4A.2 前端静态托管（`api.py:1142`）

`_mount_frontend(app)` 在**文件末尾、所有 `@app.get` 之后**调用：

| 路径 | 处理 |
| --- | --- |
| `/assets/<file>` | `StaticFiles` 直接吐静态资源 |
| `/api/*` 已注册接口 | 先于兜底匹配，行为不受影响 |
| 其余 GET（`/overview`、`/jobs`、`/jobs/<id>` 深链、`/`） | 回 `dist/index.html`，交 vue-router |

三条边界（`api.py:508-523` docstring）：

1. **必须最后注册**——`app.mount("/")` 写在 `app = FastAPI(...)` 之后会把
   `/api/health`、`/api/jobs` 全吞成 404（实测踩过）；
2. **只接 GET**——`POST /api/jobs` 被兜底接住会静默返回 HTML，前端 openapi-fetch
   解析不了，报错信息还会误导人；
3. **不再需要任何「某前缀是接口」的判断**——接口统一在 `/api` 下，`spa_fallback`
   只需拒绝 `api/` 前缀（返回 404 而不是回 index.html），页面路径与接口天然分离。
   反过来说，若接口仍留在无前缀的 `/jobs`，`/jobs` 这条路径就不可能既回 HTML
   又回 JSON——生产态用户刷新「作业列表」会看到一段 JSON。

**目录穿越防护**：`_is_within()`（`api.py:538-544`）用 `resolve()` 后的真实
路径判断是否仍在 `dist/` 内，`/%2e%2e/%2e%2e/api.py` 之类返回 404。

**降级行为**：`frontend/dist` 不存在（新克隆未构建）时不挂载、不报错，
日志一行 `未找到前端产物 ...`，服务退化为纯 API。可用 `CREDIT_DIST` 指到别处。

---

## 5. `run_credit_check.bat` —— 编排契约
由 `install_schedule_task.ps1` **自动生成，勿手改**（`:4`）。实测行为：

| 步骤 | 命令 | 落盘 |
| --- | --- | --- |
| 1 | `chcp 65001` | — |
| 2 | `mkdir logs`（不存在时） | `logs\` |
| 3 | 取时间戳（经 PowerShell `Get-Date -Format yyyyMMdd_HHmmss`，**不用 wmic**，`:11-15`） | — |
| 4 | `credit_check.py companies.txt -o result_<stamp>.csv` | `result_<stamp>.csv` |
| 5 | `notify.py result_<stamp>.csv` | — |
| 6 | 写 `logs\task.log`：开始/两个退出码 | `logs\task.log` |

**退出码聚合**（`:27-30`）：
```
notify == 1                → exit 1
credit_check != 0          → exit 1
否则                        → exit 0
```

**编码约束**：bat 文件必须**无 BOM** 且**全 CRLF**（`test_regression.TestScheduleScript` 7 项断言）；
PS1 文件必须**带 BOM**（否则 Windows PowerShell 5.1 读中文路径乱码）。
注意 `.bat` 断言针对的是 `New-RunnerBat` 的**写入逻辑**，不是生成物——
生成物在 `.gitignore` 里，CI 干净检出时不存在，靠它做断言会让 CI 必出 skip。

---

## 6. 测试契约

`test_regression.py` —— **186 项，全离线**（不联网、不开浏览器、不调 OCR）。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest test_regression.py -q
# 186 passed in ~6s
```

> 两个清单职责不同，都要覆盖对应场景的顶层 import：
> - `requirements-dev.txt`（CI 用）：`pytest` + `Pillow`（`credit_check.py:37`）
>   + `fastapi`/`httpx`/`pydantic`（`api.py`）。漏装 `fastapi` 会让 11 项 API 测试
>   被 `importorskip` 静默跳过 —— CI 全绿但零覆盖。
> - `requirements.txt`（用户从零重建环境用）：必须含 `fastapi`/`uvicorn`/`pydantic`。
>   曾漏装过，按 README 装完后 `uvicorn api:app` 直接 `ModuleNotFoundError: No module named 'fastapi'`。
> 故 `TestScriptIntegrity` 有三条断言守这件事（含 AST 扫描 `api.py` 顶层 import），
> CI 也会在出现 `skipped` 时直接失败。

覆盖分层：
1. **纯函数层**：`norm_name` / `classify` / `ocr_preprocess` / CSV 字段契约；
2. **输出层**：CSV 编码与列序、notify 报告结构、退出码语义；
3. **日志层**：轮转落盘、幂等、不污染 stdout；
4. **API 层**：FastAPI `/api/health`、`POST /api/jobs`、`GET /api/jobs/{id}`、参数校验与 422/404/409 边界；
5. **前端托管层**：`TestFrontendHosting` 6 项——接口不被 SPA 兜底吞掉、深链回 `index.html`、
   假 id 仍答 404、兜底只接 GET、路径穿越被拒、`/docs` 与 `/openapi.json` 不受影响；
6. **邮件层**：HTML 正文、异常行标红、尖括号转义、465 走 `SMTP_SSL`；
7. **脚本完整性**：依赖锁定 `==`、不指向 MCP venv、gitignore 覆盖、PS1/bat 编码；
8. **样例契约**：`samples/cases.csv` 格式、覆盖全部判定、与 `companies50.txt` 一致。

> 真联网的端到端验证**故意不放进 CI**（依赖第三方站点与 OCR 随机性），用
> `credit_check.py --selftest` 或 50 条样例人工跑。

---

## 7. 上游第三方接口（逆向所得 · 可能随时变更）

> ⚠️ 本节描述的是**目标网站的私有接口**，不是本仓库的承诺。
> 站点改版即失效，届时需按 `请求链路.md` 重新定位。

### 7.1 搜索

```
GET https://public.creditchina.gov.cn/private-api/catalogSearchHome
    ?keyword=<名称>
    &scenes=defaultScenario
    &tableName=credit_xyzx_tyshxydm
    &searchState=2
    &entityType=1,2,4,5,6,7,8
    &templateId=
    &page=1
    &pageSize=10
```
来源：`scripts/companysearch.js:115-133`（`data` 对象逐字段对应）。

**响应关键字段**：

| 路径 | 含义 |
| --- | --- |
| `status` | `1` 或 `200` 为成功（`SEARCH_OK`，`credit_check.py:69`）；`10001` = 验证码失效（`:70`） |
| `data.total` | 命中总数；`0` ⇒ `notfound` |
| `data.page` / `data.totalSize` | 分页信息 |
| `data.list[]` | 结果项 |
| `data.list[].accurate_entity_name` | 主体全称（匹配对象） |
| `data.list[].accurate_entity_name_query` | 详情页 keyword 用（percent-encode 的源） |
| `data.list[].accurate_entity_code` | 统一社会信用代码 |
| `data.list[].entityType` | 主体类型码 |
| `data.list[].uuid` | 详情页必需参数 |
| `data.list[].dataSource` | 数据来源标识 |

### 7.2 详情

```
GET https://public.creditchina.gov.cn/private-api/... getTyshxydmDetailsContent
```
入口为 `xyDetail.html?...`，由页面自行发起；采集器靠**响应 URL 含 `getTyshxydmDetailsContent`** 识别（`credit_check.py:153`）。
**依赖的响应字段**：`data.headEntity.*`、`data.data.entity.*`（状态与代码）、`data.data.dataSource` / `table_name`。

### 7.3 验证码

```
GET  https://public.creditchina.gov.cn/private-api/verify/getVerify?_v=<random>
     → 200 image/jpeg, 95 × 25 px, 约 1.7–2.6 KB
POST https://public.creditchina.gov.cn/private-api/verify/checkVerify
     Content-Type: application/x-www-form-urlencoded
     verifyInput=<4 字符>
     → 成功 {"code":0}；失败 {"msg":"验证码已失效，请重新申请验证码","code":-1}
```
- 内容为 **4 位数字+字母（大小写混合）**，非汉字；
- 通过后客户端写 `vcode=1` cookie，**服务端另有会话级状态**，有效期实测 ≈ 5 分钟
  （`CAPTCHA_TTL = 5*60`，`credit_check.py:72`）；
- 单张图识别失败会**换图重试**，上限 `MAX_CAPTCHA_TRY = 12`（`:71`）。

### 7.4 风控层（瑞数）

首访返回 `412` → 加载 `/<随机路径>/*.js` → 二跳 `200`。由 Camoufox 自动完成，代码不干预。
**技术细节见 `瑞数逆向分析技术报告.md`；完整链路见 `请求链路.md`。**

---

## 8. 变更影响表（改代码时必看）

| 若修改 | 必须同步 |
| --- | --- |
| CSV 列名或列序 | `credit_check.py:414` + `test_regression.CSV_FIELDS` + 本文 1.3 + `README §8` |
| 判定词表 | `NORMAL`/`ALERT` + `test_regression.TestClassify` + 本文 1.3 + `README §3` |
| 退出码语义 | 对应 `main()` + `test_regression.TestExitCodes` + 本文 1.2/2.2 + `run_credit_check.bat:27-30` |
| 新增 CLI 参数 | `argparse` 块 + 本文对应表格 + `README §1` |
| 上游接口参数 | `credit_check.py` 常量 + `scripts/*.js` 对照 + 本文第 7 节 + `请求链路.md` |
| 日志行为 | `logkit.py` + `test_regression.TestLogging` + 本文第 3 节 |
| HTTP 接口/字段 | `api.py` 模型 + 本文 4A + `frontend/src/types.d.ts`（`npm run gen:api` 重新生成） |
| 前端托管顺序 | `api.py` 末尾 `_mount_frontend(app)` + `test_regression.TestFrontendHosting` |
