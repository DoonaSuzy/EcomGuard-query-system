# 聚合查询与巡检系统 —— Docker 部署说明（GO1）

> 目标机：192.168.101.91（zhu），新克隆的 UBT。
> 本文是 **docker 部署的操作手册**；可行性实测证据见
> [`UBT-docker-部署可行性.md`](UBT-docker-部署可行性.md)。

## 1. 组成

| 文件 | 作用 |
|---|---|
| `Dockerfile` | 三阶段构建：Node 构建三套前端（`web/` 统一前端 + 两个模块）→ 装 Python 依赖 + 装 Camoufox → 运行镜像 |
| `docker-compose.yml` | MySQL 8.4 + 浏览器初始化 + 主服务，含卷与健康检查 |
| `docker_entrypoint.py` | 启动前清理 UBO 残缺目录、拒绝 `AGG_WORKERS>1` |
| `.env.docker.example` | 环境变量样例（复制成 `.env.docker` 填值） |
| `tools/install_camoufox.py` | 从 `vendor/` 里的离线包安装 Camoufox（目标网络到 github.com 不通，`camoufox fetch` 必失败） |
| `web/` | 统一前端源码（`dist/` 不入库，镜像里由构建阶段生成） |

## 2. 部署步骤

```bash
# 1. 拷代码到 91（或直接在 91 上 git clone）
cd /path/to/aggregated-system

# 2. 填环境变量
cp .env.docker.example .env.docker
$EDITOR .env.docker          # 至少填 MYSQL_ROOT_PASSWORD / SMTP_* / MAIL_TO_*
                             # 要给别人用还要把 AGG_BIND 改成 0.0.0.0（见 §3.1）

# 3. 构建并启动（首次会自动拉 1.3 GB 浏览器，需等待）
docker compose --env-file .env.docker up -d --build

# 4. 看状态
docker compose ps
docker compose logs -f app
```

打开 `http://192.168.101.91:8000/`：
- `/` 统一前端，左侧系统列表切「WIPO」与「营业执照」，功能项按系统变化
  （WIPO：`/input` `/list` `/check` `/reports`；营业执照：`/overview` `/submit` `/schedules` `/jobs`）
- `/docs` Swagger（两个模块的接口已合并，按 tag 分组）
- 旧地址 `/wipo`、`/credit` 仍可用，会 307 到 `/input`、`/overview`

> 打不开且 `docker compose ps` 显示 `127.0.0.1:8000->8000/tcp`，
> 就是 `AGG_BIND` 没改，见 §3.1。

## 2b. 已经跑起来过，怎么更新到新版

只在**核心依赖**（requirements / camoufox）变动时才需要重建镜像并清浏览器卷；
纯业务代码改动**必须重建镜像**（代码是 COPY 进镜像的，不是挂载的），
但**不要**加 `-v`——那会删掉 MySQL 数据卷和 Camoufox 缓存卷（1.3 GB，要重下）。

```bash
cd /path/to/aggregated-system
git pull --ff-only

# 前端产物不需要在 91 上构建：Dockerfile 的 frontend stage 用 node 镜像自己建
# （91 主机没有 node，这是刻意的——别照着旧文档在主机上跑 npm）

docker compose --env-file .env.docker up -d --build   # 不加 -v
docker compose ps                                      # 等 app 变成 healthy
docker compose logs app | tail -50                     # 看迁移/调度补算日志
```

**升级到 `always_notify` 默认 True 这一版时，日志里应出现**：

```
营业执照模块已挂载
调度补算 N 条
```

老库的存量调度会在 `Store.__init__` 里被一次性回填成 `always_notify=1`
（标记 `mig_20261020_always_notify_on` 落在 `meta` 表）。
验证：

```bash
curl -s http://127.0.0.1:8000/api/schedules | python -m json.tool | grep always_notify
# 应全部为 true
```

> 回填**只跑一次**。之后在页面上把某条调度关成「只在有异常时推送」，
> 重启不会又被翻开——这是刻意留的能力，不是 bug。

## 3. 八个关键决策（改动前先读）

### 3.1 两个「0.0.0.0」别搞混（最容易白折腾半天的地方）

这里有**两层**绑定，各管一件事，只改一层就是不通：

| 变量 | 在哪生效 | 默认 | 不改会怎样 |
|---|---|---|---|
| `AGG_HOST` | 容器**内** uvicorn 监听地址（`aggregator.py:365`） | `127.0.0.1` | 容器内只监听回环，端口映射出去也连不上 → Dockerfile/compose 已写死 `0.0.0.0` |
| `AGG_BIND` | **宿主机**端口发布地址（compose `ports:`） | `127.0.0.1` | 只有这台服务器自己能访问；**同事用浏览器打开是「连接被拒绝」** |

所以「别人打不开」时先看 `docker ps` 的 PORTS 列：
`127.0.0.1:8000->8000/tcp` 就是没改 `AGG_BIND`，改成 `0.0.0.0` 后应显示
`0.0.0.0:8000->8000/tcp`。

**改完必须重建容器，不能 restart**：端口映射在容器创建时固定，
`docker compose restart` 不会重新绑定。

```bash
sed -i 's/^AGG_BIND=.*/AGG_BIND=0.0.0.0/' .env
docker compose up -d --no-deps app     # 注意服务名是 app，不是容器名 agg-app
```

> 实测踩坑：`docker compose up -d --no-deps agg-app` 报 `no such service: agg-app`
> —— `agg-app` 是 `container_name`，compose 的服务名是 `app`
> （`docker compose config --services` 可查：`camoufox-init` / `mysql` / `app`）。

### 3.2 `AGG_WORKERS=1` 是硬约束
scheduler、worker、Camoufox 实例、`check_runs` 单行状态机全是进程内单实例。
`docker_entrypoint.py` 会拒绝 `AGG_WORKERS>1` 启动——宁肯失败也不错乱。

### 3.3 MySQL 走独立容器，不用开发机的 3307
开发机那个 `mysqld` 是开发期手起的（端口 3307），容器化后应自包含。
建表由应用启动时自动完成（`api.py:633 _mysql_init` 执行三张表的 DDL），
所以 compose 里**不需要** init 脚本，只要 `MYSQL_DATABASE=credit_check`。

注意 `mysql:8.4` 没有 `log_syslog` 变量，别把 5.7 的 `command:` 参数抄过来。

### 3.4 配置不能靠 `.env` 文件
`api.py` 全程不 `load_dotenv`（这是已修的缺陷之一），容器里读不到
`modules/credit/.env`。所有配置走 compose 的 `environment:` 显式注入。

**漏配 SMTP 的后果**：Web 提交的作业会走
`errors.append("mail: 未配置 SMTP_HOST")`，`notified` 恒为 0，
巡检照跑但没人收到邮件。每日巡检的收件人在 `MAIL_TO_BUSINESS`，
每月在 `MAIL_TO_MANAGER`。

### 3.5 Camoufox 浏览器 1.3 GB 放命名卷
镜像里 COPY 了一份，但 compose 挂的命名卷会盖住它。
`camoufox-init` 容器负责检测「卷里没有浏览器」就 `camoufox fetch` 一次。

升级 camoufox / playwright 时：`docker compose down -v`（删卷）再 `up`。

### 3.6 UBO 插件 451 失败必须处理
`camoufox fetch` 拉 UBO 时返回 451，留下没有 `manifest.json` 的残缺目录，
之后 `Camoufox()` 默认加载它 → `InvalidAddonPath: manifest.json is missing`。

**正确解法是 `exclude_addons=[DefaultAddons.UBO]`（已改在 `credit_check.py`）。**

⚠️ 这里踩过一个坑，记下来免得重犯：`addons=[]` **不管用**。
读 camoufox 源码（`camoufox/utils.py:456-493`）：

```python
if addons is None: addons = []
add_default_addons(addons, exclude_addons)   # ← 无条件执行，addons=[] 拦不住
if addons: confirm_paths(addons)              # ← 逐个要求 manifest.json
```

`addons=[]` 只影响自定义插件，默认插件照样被追加。而
`maybe_download_addons` 只要发现 `addons/UBO` **目录存在**就把路径加进去，
不看内容是否完整——所以 451 留下的空目录会让每次启动都抛异常。

双保险：`tools/install_camoufox.py` 装完删一次目录，
`docker_entrypoint.py` 启动时再删一次，运行期靠 `exclude_addons` 兜底。
UBO 只是挡广告，对巡检无功能影响。

### 3.7 `WIPO_DATABASE__URL` 必须显式设成 SQLite
这是最容易漏的一条，漏了的表现是**聚合 `/health` 恒 503**。

WIPO 的 `DatabaseSettings.url` 默认值是
`mysql+aiomysql://root:@127.0.0.1:3306/wipo_trademark`
（`app/core/config.py:105`，为开发机本机 MySQL 写的）。容器里 `127.0.0.1`
没有任何 MySQL，于是每次探活都 `Can't connect to MySQL server on '127.0.0.1'`。

compose 里已写死：

```yaml
WIPO_DATABASE__URL: sqlite+aiosqlite:////app/data/wipo.db
```

注意 **4 个斜杠**（`////` 表示绝对路径）。变量名里的 `__` 是 pydantic 的
嵌套分隔符（`env_prefix=WIPO_` + `env_nested_delimiter=__`）。

### 3.8 健康检查探 `/health`，不是 `/api/health`
两个端点语义不同，别混：

| 端点 | 归属 | 探什么 | 能证明 |
|---|---|---|---|
| `/api/health` | credit 模块 | 只有进程本身 | HTTP 活着 |
| `/health` | WIPO 模块 | 数据库 + 外部 WIPO API | **整个系统能不能干活** |

Dockerfile 的 `HEALTHCHECK` 原先探 `/api/health`，结果 WIPO 库连不上、
聚合 `/health` 一直 503 时，`docker compose ps` 仍显示 `(healthy)` ——
运维看不到故障，是「假健康」。现已改探 `/health`。

代价：`/health` 同时探外部 WIPO API，对方抖动时容器会短暂 unhealthy。
接受这个代价——本系统核心价值就是那个外部接口，它不可用时「健康」是假话。
`restart: unless-stopped` 不会因 unhealthy 重启容器，不会造成重启风暴。

## 4. 与**开发机**旧计划任务的冲突

⚠️ 这里要分清两台机器，别搞混：

| 机器 | 系统 | 有 `CreditChina-LicenseCheck` 吗 |
|---|---|---|
| **开发机**（跑 `F:\projects\GO1`） | Windows | ✅ 有，指向旧仓库 `F:\projects\GO1\reverse-records\run_credit_check.bat` |
| **部署机 192.168.101.91** | Ubuntu 22.04 | ❌ 没有（Linux 没有 Windows 计划任务） |

所以冲突只发生在「开发机上的旧脚本」与「91 容器里的 scheduler」之间：
两者都按每日 09:00 的节奏跑同一批企业，会造成**重复巡检**
（且旧脚本的收件人是占位地址 `业务人员@company.com`，等于白发）。

容器正式接管后，在**开发机**上停掉旧任务：

```powershell
Disable-ScheduledTask -TaskName "CreditChina-LicenseCheck"
# 或彻底删： Unregister-ScheduledTask -TaskName "CreditChina-LicenseCheck" -Confirm:$false
```

## 5. 验证清单

```bash
# 聚合健康（应 200 且 dependencies.database=up；三态 ok/degraded/down）
curl -s http://127.0.0.1:8000/health | python3 -m json.tool

# 统一前端与八个业务路由都在（应全 200）
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/
for p in input list check reports overview submit schedules jobs; do
  printf '%-10s ' "$p"; curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:8000/$p"
done

# 旧地址应 307 到新路由（不是 200，也不是 404）
curl -s -o /dev/null -w '%{http_code} -> %{redirect_url}\n' http://127.0.0.1:8000/wipo
curl -s -o /dev/null -w '%{http_code} -> %{redirect_url}\n' http://127.0.0.1:8000/credit

# Swagger 分组完整（应看到 7 个 tag：check/io/jobs/reports/schedules/system/trademarks）
curl -s http://127.0.0.1:8000/openapi.json | python3 -c "import json,sys; d=json.load(sys.stdin); print('paths=',len(d['paths'])); print(sorted({t for p in d['paths'].values() for op in p.values() for t in op.get('tags',[])}))"

# 定时巡检接口
curl -s http://127.0.0.1:8000/api/schedules | python3 -m json.tool

# 端到端真跑一次（用统一社会信用代码，约 30 秒出结果）
curl -s -X POST http://127.0.0.1:8000/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"names":["9144030071526726XG"],"mail_to":""}'
# 然后轮询 GET /api/jobs/{job_id}，state 应从 queued → running → done，
# results[0] 里应看到 matched_name / credit_code / status / verdict

# 日志里应看到：营业执照模块已挂载 + 调度补算 N 条
docker compose logs app | grep -E "已挂载|调度补算"
```

## 6. 已知限制

- **WIPO 仍用 SQLite**：`check_runs` 是单行状态机，迁移 MySQL 成本与收益
  不成比例，本次不动。数据落在 `app-data` 卷里。
- **镜像体积**：含 1.3 GB Camoufox + Node 构建层，`docker images` 会看到
  多个几百 MB 到 1.4 GB 的镜像与阶段。要瘦身需换多阶段产出或拆分浏览器层。
- **端口只绑 127.0.0.1**：`AGG_BIND=127.0.0.1` 是默认值，
  即只能从 91 本机访问。要让局域网其他机器访问，把它改成 `0.0.0.0`
  并在 `.env.docker` 里确认——这是**刻意的保守默认**，别忘了一起改。
- **时区要显式传**：镜像里没有 `/etc/localtime` 兜底，跑测试或手工
  `docker run` 时要带 `-e TZ=Asia/Shanghai`。compose 已设，不受影响；
  但**不传 TZ 跑测试会有 1 条时区断言失败**（见 §7.4）。
- **邮箱仍是占位地址**：`.env.docker` 里是 `biz@example.com`，
  换真实收件人之前不会真的发信（见 §8）。

## 7. 已完成的实测验证（在 192.168.101.91 上，Docker 29.8.2 / Compose v5.6.0）

> ⚠️ **§7.1–§7.2b 是「统一前端」之前（iframe 壳 + 两个子路径前端）的实测记录**，
> 保留它们作为**容器化本身**（依赖安装、Camoufox、MySQL、端口绑定）的证据 ——
> 那部分与前端形态无关，结论仍然有效。
> 前端形态相关的端点与页面，见 §7.2c（统一前端后重测）。

### 7.1 构建与启动

| 项 | 结果 |
|---|---|
| `docker compose config`（空 `MYSQL_ROOT_PASSWORD`） | ✅ 按设计拒绝：`required variable MYSQL_ROOT_PASSWORD is missing a value` |
| `docker compose config`（填了密码） | ✅ 通过 |
| `docker build --target frontend` | ✅ 三个前端工程都构建成功（`web/` 统一前端 + wipo + credit），自检输出 `✓ wipo dist-agg 前缀正确（/wipo/assets/）`、`✓ credit dist-agg 前缀正确（/credit/assets/）`、`✓ unified web/dist 已生成（资源前缀 /assets/）` |
| 完整镜像构建 `docker compose build app` | ✅ 成功产出 `aggregated-inspection-app:local` |
| `docker compose up -d` | ✅ agg-mysql / agg-app 均 `Up (healthy)` |
| Camoufox / ddddocr 在 Linux 容器内运行 | ✅ 见 [`UBT-docker-部署可行性.md`](UBT-docker-部署可行性.md) |

**构建时踩到并已修的坑**：`node:22-bookworm-slim` 里没有 `python` 这个命令名
（只有 `python3`），`python tools/build_frontends.py` 直接 exit 127。
现在该阶段显式装 `python3` 并调用 `python3`。

### 7.2 端点验收（统一前端**之前**的记录，全部实测返回 200）

| 端点 | 结果 |
|---|---|
| `GET /health` | 200，`{"status":"ok","dependencies":{"database":"up","wipo_api":"up"}}` |
| `GET /`（当时的 iframe 壳页） | 200 |
| `GET /wipo/`（当时是真实前端，现为 307） | 200 |
| `GET /credit/`（当时是真实前端，现为 307） | 200 |
| `GET /docs`（Swagger） | 200 |
| `GET /api/trademarks` | 200 |
| `GET /api/schedules` | 200 |
| `GET /openapi.json` | 21 paths / 7 tags（check、io、jobs、reports、schedules、system、trademarks） |

### 7.2b 跨机访问（从 Windows 主机打过去，不是容器内自测；统一前端**之前**）

容器内 `curl 127.0.0.1` 只能证明「应用活着」，证明不了「别人能用」。
把 `AGG_BIND` 从 `127.0.0.1` 改成 `0.0.0.0` 并重建容器后，从另一台机器实测：

| 地址 | 结果 |
|---|---|
| `http://192.168.101.91:8000/health` | **200** `{"status":"ok","app":"WIPO 商标监控系统",...}` |
| `http://192.168.101.91:8000/wipo/` | **200**（当时是真实前端，现为 307 → `/input`） |
| `http://192.168.101.91:8000/credit/` | **200**（当时是真实前端，现为 307 → `/overview`） |
| `http://192.168.101.91:8000/api/schedules` | **200** |

`Test-NetConnection 192.168.101.91 -Port 8000` → `True`。

> 一个容易误判的中间态：刚 `up -d` 完立刻测会拿到 `000`（容器还在
> `health: starting`），等 20–30 秒再测才是真实结果。我第一次就是这么
> 误判成「绑定没生效」的。

> 关于 ufw：这台机 `ufw` 是 `active`，但 8000 照样能从外部访问——
> Docker 发布端口时走的是 iptables 的 `DOCKER` 链，**绕过 ufw 的 INPUT 规则**。
> 所以「ufw 开着」不等于「端口被挡」，别在这上面绕弯子。
> 反过来说：想让 ufw 真的管住容器端口，得额外配 `DOCKER-USER` 链。

### 7.2c 统一前端后的重测（当前形态，2026-10-20）

用 commit `1910dc9` 重建镜像后，从 **Windows 主机**打 `192.168.101.91:8000` 实测。
（`AGG_BIND` 必须为 `0.0.0.0`，否则容器内自测全 200、跨机全 `10061 拒绝连接`。）

**路由**（八个业务路由 + 首页全 200）：

| 路径 | 状态 | | 路径 | 状态 |
|---|---|---|---|---|
| `/` | 200 | | `/overview` | 200 |
| `/input` | 200 | | `/submit` | 200 |
| `/list` | 200 | | `/schedules` | 200 |
| `/check` | 200 | | `/jobs` | 200 |
| `/reports` | 200 | | | |

**旧地址降级为 307**（不是 404，也不是 200）：

| 旧地址 | 状态 | 目标 |
|---|---|---|
| `/wipo` `/wipo/` | 307 | `/input` |
| `/credit` `/credit/` | 307 | `/overview` |

**接口**：`/health`、`/api/health`、`/api/trademarks`、`/api/schedules`、
`/api/schema`、`/docs`、`/openapi.json` 全 200；`openapi` = **21 paths / 7 tags**。
`/health` 体：`{"status":"ok","dependencies":{"database":"up","wipo_api":"up"}}`。

**前端产物**：`GET /` 的 `index.html` 引用 `/assets/index-tM5D1Es3.js` 与
`/assets/index-j2hDkDOL.css`（绝对前缀，深链刷新不会 404）；
`GET /assets/nope.js` → **404 `application/json`**（没有回落成 index.html）。

**镜像内三套产物都在**（漏任何一套都会表现为「接口正常、页面不对」）：

```
web/dist/index.html
modules/wipo/web/dist/index.html
modules/credit/frontend/dist/index.html
web/dist/assets/index-*.css  → 含 .btn-icon svg 规则
```

**浏览器烟测（Playwright 从 Windows 打 91，17/17 PASS，零 console error）**：

| 断言 | 实测 |
|---|---|
| 默认进 `/input` | ✅ |
| 左侧系统列表 2 项、名为 WIPO / 营业执照 | ✅ |
| 侧栏宽度 240px | ✅ |
| WIPO 功能项 4 个 | ✅ |
| 切到营业执照 → URL `/overview`、`window` 标记存活（**无整页刷新**） | ✅ |
| 切换前后侧栏几何不变（width/x 差 <1px） | ✅ |
| 主按钮 `rgb(79, 70, 229)`、卡片圆角 `12px` | ✅ |
| `/submit` `/schedules` `/jobs` 标题正确 | ✅ |
| 定时巡检页初始不弹配置窗 | ✅ |
| 切回 WIPO → `/input`、录入页可见 | ✅ |

**容器内回归**（新镜像，Linux）：credit `254 passed`（该镜像构建时的计数，
当前主干为 **271**）、WIPO `118 passed`
（WIPO 需先 `python -m playwright install chromium`）。

**真实巡检作业**（端到端，含 Camoufox + ddddocr）：

```
POST /api/jobs {"names":["9144030071526726XG"],"mail_to":""}
→ 202 job_20261020_081859_fbb728
→ state=done, ok=1, alert=0
→ matched_name=腾讯科技(深圳)有限公司 / credit_code=9144030071526726XG
  status=存续 / verdict=ok / data_source=市场监督管理总局
```

### 7.3 端到端真实巡检（决定性证据）

在容器内提交一次真实作业，**走完整链路**：HTTP → 作业队列 → Camoufox 启动
→ 打开国家企业信用信息公示系统 → ddddocr 过验证码 → 解析 → 落库。

```json
POST /api/jobs  {"names": ["9144030071526726XG"], "mail_to": ""}
→ 202 {"job_id":"job_20261020_061302_b6b952","state":"queued","total":1}
```

30 秒后 `state=done`，结果：

```json
{
  "matched_name": "腾讯科技(深圳)有限公司",
  "credit_code": "9144030071526726XG",
  "status": "存续",
  "verdict": "ok",
  "data_source": "市场监督管理总局",
  "record_count": 1
}
```

这一次同时证明了四件事：
1. **Camoufox 在 Linux 容器内能起来**（`exclude_addons` 修复生效，
   `addons` 目录不存在，未再出现 `InvalidAddonPath`）；
2. **ddddocr 能过验证码**；
3. **容器到外网（creditchina.gov.cn）可达**；
4. **统一社会信用代码查询可用**（`9144030071526726XG` → 腾讯，无需企业全称）。

> 注意 `notified: false` 且 `notify_error: null` —— 本次 `mail_to` 传空，
> 属于「没让人发信」，不是发送失败。要验邮件得在 `.env.docker` 里配真 SMTP。

### 7.3b 邮件推送端到端（决定性证据，已真发信）

配上真实 SMTP 后重跑。**注意**：下面这条实测发生在 2026-10-20 之前，
当时 `always_notify` 默认还是 `False`，所以要显式传 `true` 才发得出去；
**现在默认就是 `true`**，不传这个字段也会发。

```json
POST /api/jobs  {"names": ["9144030071526726XG"],
                 "mail_to": "1091158653@qq.com", "always_notify": true}
→ 202 {"job_id":"job_20261020_063412_0dbe8f","state":"queued"}
```

约 10 秒后：

```
state        : done
notified     : True
notify_error : None
```

容器日志（关键三行）：

```
作业 job_20261020_063412_0dbe8f 完成，CSV=/app/data/results/...csv
[mail] 已发送 -> 1091158653@qq.com
作业 job_20261020_063412_0dbe8f 推送成功
```

至此整条链路闭环：**API → 作业队列 → Camoufox → 国家企业信用信息公示系统
→ ddddocr → 解析 → MySQL → SMTP 真发信 → `notified=1`**。

#### 推送闸门：`always_notify` 默认 **开**（2026-10-20 起）

`api.py` 的 `_maybe_notify` 里有一道闸门：

```python
if not need and not req.get("always_notify"):
    log.info("作业 %s 全部正常，跳过推送", job_id)
    return
```

即：**只有出现异常（`alert` / `notfound` / `unknown`）时才推**，
除非 `always_notify` 为真。

`always_notify` 的默认值是 **`True`**（`CheckRequest` / `ScheduleIn` /
两套建表 DDL 都是 1）。原因：巡检结果本身就是交付物，「全部正常」也是
有效信息——研发要每期看到全量名单与各自判定，而不是「没收到信 = 没跑」。
置 `False` 才退回「只在有异常时收」，这是给不想天天收信的人留的口子。

> **升级注意（老库）**：建表默认值只影响**新行**。老库里已有的调度仍是 0，
> `Store.__init__` 会用一次性标记 `mig_20261020_always_notify_on`
> 把所有 `always_notify=0` 的行改成 1，标记落进 `meta` 表后不再重放。
> 所以运营事后手工关掉某一条，重启不会被又翻开。

**历史记录（口径必须标明）**：上面 §7.3b 的实测发生在 2026-10-20 之前，
当时的默认值是 `False`，所以那次的「强制推送」是显式传了
`"always_notify": true` 才发出去的；当时也撞到过用腾讯（`verdict: ok`）
测试却 `notified=false`、一度以为静默失败，查日志才看到「全部正常，跳过推送」。
**现在默认就是发**，同样的操作不需要再传这个字段。

> 判断邮件是否正常，仍然不能只看「有没有收到信」，要看作业详情的
> `notified` + `notify_error` 两个字段：
> `notified=true` = 发了；`notified=false + notify_error=null` = 被跳过
> （只可能是有人显式关了 `always_notify`）；
> `notified=false + notify_error="mail: ..."` = 真失败。

#### ⚠️ 第二个陷阱：`state=done` 的那一刻 `notified` 可能还是 false

`api.py:1064` 的顺序是先写作业终态、再调 `_maybe_notify`，而
`mark_notified` 在通知真正成功后才落库。所以轮询到 `state=done` 就立刻读
`notified`，会拿到一个**几百毫秒的假象 false**。

实测撞到过：轮询脚本一看到 `done` 就查库，得到 `done / notified=0 / notify_error=空`，
与「被跳过」的症状一模一样；等 1 秒再查就是 `notified=1`，日志里也有
「推送成功」。

> 判据：`notify_error` 为空 **且** 日志里既没有「推送成功」也没有
> 「全部正常，跳过推送」时，说明通知还没跑完，等一下再查。

### 7.4 Linux 侧回归（容器内跑 pytest）

用镜像本身跑（镜像里已带 pytest / httpx / Pillow / aiosqlite，无需另装）：

```bash
# 镜像里是构建当时的主干；下面的 254 是那次构建的计数，当前主干为 271
docker run --rm -e PYTHONIOENCODING=utf-8 -e TZ=Asia/Shanghai \
  -w /app/modules/credit aggregated-inspection-app:local \
  python -m pytest test_regression.py -q
# → 254 passed（构建当时）
```

| 模块 | 结果 | 说明 |
|---|---|---|
| credit `test_regression.py` | **254 passed**（当前主干 271） | 与 Windows 侧一致 |
| WIPO `tests/` | **33 passed / 85 errors** | 85 项是 Playwright E2E，需 chromium |

**为什么 WIPO 那 85 项在容器里跑不了**：镜像只装了 Camoufox 浏览器，
没装 Playwright 的 chromium（约 150 MB）。WIPO 的运行期代码**不 import
playwright**（`app/` 下零引用，只有 `response.py` 一句 docstring 提到），
Playwright 只出现在 12 个测试文件里，`requirements.txt` 也没列它——
所以这是**纯测试依赖，不影响部署**。要在容器里跑 WIPO E2E 就临时装：

```bash
docker run --rm ... -w /app/modules/wipo aggregated-inspection-app:local \
  sh -c "playwright install --with-deps chromium && python -m pytest tests -q"
```

**跑测试必须带 `-e TZ=Asia/Shanghai`**：镜像里没有 `/etc/localtime` 兜底，
默认 UTC 会让 `test_update_recomputes_next_run` 的
`next_run_at.endswith("18:00:00+08:00")` 断言失败
（实测得到 `2026-11-05T18:00:00+00:00`）。compose 已设 TZ，线上不受影响。

### 7.5 顺手修掉的两个「测试依赖环境」问题

跑容器回归时暴露的两处**测试自身不够健壮**，已修：

| 问题 | 现象 | 修法 |
|---|---|---|
| 时区硬编码 | 期望 `+08:00`，UTC 环境下失败 | 记录为「必须传 TZ」，compose 已设；文档写明 |
| `test_gitignore_excludes_api_runtime_data` 在镜像里失败 | 它断言 `data/.gitkeep` 存在，但 `.dockerignore` 刻意排除了 `data/`（运行期走卷），镜像里也没有 `.git` | 新增 `_in_git_checkout()`，该断言只在 git 检出内检查 |

> 第二处**不是放宽检查**：CI 与本地都在 git 检出内跑，断言照常生效；
> 只有从镜像里跑时才跳过。已验证本地 `_in_git_checkout() == True`。

### 7.6 SMTP 出网与认证

容器内实测（用 `modules/credit/.env` 里的真实凭据，**只登录不发信**）：

| 项 | 结果 |
|---|---|
| TCP `smtp.qq.com:465` | ✅ OK |
| TCP `smtp.qq.com:587` | ✅ OK |
| **TLS + AUTH 登录** | ✅ **AUTH OK** —— 授权码有效、握手正常 |
| TCP `www.creditchina.gov.cn:443` | ✅ OK（112.84.222.65） |
| TCP `www.gsxt.gov.cn:443` | ✅ OK（124.238.251.173） |

认证测试的做法：凭据从开发机 `.env` 读、以 `0600` 权限的临时 env 文件传给
一次性容器，容器里只做 `smtplib.SMTP_SSL(...).login(...)` 然后 `quit()`，
**不调用 `sendmail`**，跑完立即删除临时文件。

> 也就是说：**「发不出去」的风险已排除**（凭据/网络/TLS 都通）。
> `sendmail` + 真实收件人这一段随后在 §7.3b 用真实作业验证通过。

补充实测（走完整 `send_message`，不只是 login）：

```
noop  : (250, b'OK from 163.204.216.68 to newxmesmtplogicsvrszb51-1.qq.com.')
login : (235, b'Authentication successful')
refused: {}          # 空 dict = 没有任何收件人被拒
quit  : (221, b'Bye.')
```

#### 凭据不对时，QQ 是「直接断连」而不是报错

实测踩到：`.env` 里 `SMTP_USER=probe@example.com`、`SMTP_PASS` 只有 5 位（样例占位值）
时，容器内发信的表现是：

```
connect: (250, b'OK from ...')
smtplib.SMTPServerDisconnected: Connection unexpectedly closed
  ← 断在 s.login() 里的 AUTH 命令
```

`noop` 成功、`login` 阶段被服务器**直接掐断连接**，没有任何 `535 认证失败` 之类的
友好回执。看到这个异常不要去查网络或 TLS（那两步已经通了），直接看
`SMTP_USER` / `SMTP_PASS` 是不是还是样例里的占位值。

> 顺带一个观察：裸域 `creditchina.gov.cn` / `gsxt.gov.cn` **DNS 解析失败**
> （`No address associated with hostname`），只有带 `www.` 的能解析。
> 当前代码用的都是 `www.` 形式，所以不影响；但若以后有人把 URL 改成裸域，
> 会以「DNS 失败」而不是「HTTP 错误」的形式炸，排查方向完全不同。

## 8. 尚未验证 / 下一步

- **收件人要换成真正的业务人员**：§7.3b 验证时是在**单次作业的 `mail_to`** 里
  填了测试邮箱（自收自发，证明链路通），`MAIL_TO_BUSINESS` / `MAIL_TO_MANAGER`
  这两条**定时巡检的默认收件人**始终是样例占位值，从未改过。上线前必须改：
  ```bash
  # .env.docker 里
  MAIL_TO_BUSINESS=<业务人员邮箱>     # 每日巡检收件人
  MAIL_TO_MANAGER=<主管邮箱>          # 每月巡检收件人
  # 改完重建容器
  docker compose --env-file .env.docker up -d --no-deps app
  ```
  另外 `SMTP_PASS` 是 QQ 邮箱的 **16 位授权码**（不是登录密码），
  改密码/关服务后会失效，届时 `notify_error` 会显示 `mail: ...`；
  授权码填错则表现为 `SMTPServerDisconnected`，见 §7.6。
- **开发机旧计划任务冲突**：见 §4，容器接管前先停掉 `CreditChina-LicenseCheck`
  （该任务指向 Windows 侧的旧仓库 `F:\projects\GO1\reverse-records\`，
  与 91 这台 Linux 机不是同一套东西，但**同一个业务时段**，会重复巡检）。
- **WIPO E2E 在容器内**：需临时装 chromium，见 §7.4。
- **91 上还跑着旧的 power-news 全家桶**（9 个容器，占 8080/3000/9090/9000-9001
  与 3306 宿主端口）。本系统用的是 8000 与容器内 MySQL，**不冲突**；
  但 3306 被 power-mysql 占着，如果以后要把 agg-mysql 暴露到宿主 3306 会撞车。
