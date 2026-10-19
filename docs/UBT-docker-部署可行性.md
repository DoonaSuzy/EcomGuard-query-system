# UBT（192.168.101.91）docker 部署可行性 —— 实测取证

> 本文记录「这一版能不能用 docker 部署到 91」这个问题在**动手写 Dockerfile 之前**
> 做的环境侧取证，以及最终结论。
> 部署操作步骤见 [`Docker部署手册.md`](Docker部署手册.md)。

## 1. 问题

91 是从 84（电力新闻与省级新闻的 UBT）**克隆**出来的新机，
克隆时没清空环境与 docker。因此要先弄清三件事：

1. 91 上现有什么（残留的 84 环境能不能复用）；
2. 当前聚合系统（`F:\projects\GO1\aggregated-system`）的依赖在 Linux 上装不装得上；
3. 有没有硬阻塞（浏览器、单进程约束、前端构建）。

## 2. 环境盘点（全部实测）

### 2.1 主机

| 项 | 值 |
|---|---|
| 系统 | Ubuntu 22.04.5 LTS |
| 内核 | 6.8.0-136-generic |
| CPU / 内存 | 4 核 / 3 GiB（可用约 1 GiB） |
| 磁盘 | 59 G，已用 27 G，剩 29 G |
| Docker | 29.8.2（overlayfs，systemd active，root=/var/lib/docker） |
| Compose | v5.6.0 |
| node / npm | **没有** |
| git | 2.34.1 |
| python3 | 3.10.12（主机） |
| mysql 客户端 | 没有 |

> 登录方式：用户给的 `zhu` / 密码**认证失败**（84 与 91 都试过）。
> 最终用本机 `~/.ssh/id_ed25519_vm` 公钥免密登录成功。
> 附注：Windows 上 `SSH_ASKPASS` 不可用（OpenSSH 要求 askpass 是 `.exe`），
> 脚本化登录用 `paramiko` + `Ed25519Key`。

### 2.2 残留的 84 环境（9 个容器在跑）

`power-fastapi` / `power-scheduler` / `power-worker`（镜像 `power-news/app:local`）、
`power-browser`（`mcr.microsoft.com/playwright:v1.49.0-jammy`）、
`power-grafana` / `power-prometheus` / `power-rustfs` / `power-redis` /
`power-mysql`（`mysql:8.4`，8.4.11）。

- 网络：`power-news_power-net`、`newsagg-platform_newsagg-net`
- 卷：7 个
- `power-mysql` 映射到 `0.0.0.0:3306`，库 `power_news`（39 表约 157 万行）、
  `power_news_platform`（13 表 840 行）
- build cache 2.192 GB，可回收 1.218 GB

**结论：这些是 84 的业务数据，不能直接用；容器化要自包含，另起 MySQL 容器。**

## 3. 依赖可装性（在 power-fastapi 容器里 `pip download` 实测）

| 包 | 结果 |
|---|---|
| camoufox 0.4.11 | ✅ `py3-none-any.whl` |
| ddddocr 1.6.1 | ✅ `py3-none-any.whl` |
| onnxruntime 1.31.0 | ✅ `cp312-cp312-manylinux_2_28_x86_64.whl` |
| opencv-python 5.0.0.93 | ✅ `cp37-abi3-manylinux_2_28_x86_64.whl` |
| numpy 2.5.4 | ✅ 镜像里已有 |

→ **Linux x86_64 依赖链完整可装。**
pip 默认走公网（容器内无 `/etc/pip.conf`），构建时应显式指定清华源。

### 3.1 但**不能**复用 `power-news/app:local` 镜像

| 依赖 | 镜像里有 | 聚合系统要 |
|---|---|---|
| fastapi | 0.115.6 | **0.143.0** |
| uvicorn | 0.34.0 | **0.54.0** |
| pydantic | 2.10.4 | **2.14.0** |
| playwright | 1.49.0 | **1.62.0** |
| camoufox | ❌ 无 | 0.4.11 |
| ddddocr | ❌ 无 | 1.6.1 |
| onnxruntime | ❌ 无 | 1.31.0 |
| opencv | ❌ 无 | 5.0.0.93 |

→ **必须自建镜像。**

## 4. 三个硬阻塞（写 Dockerfile 前必须解决）

### 4.1 Camoufox 依赖真实浏览器，且 `os=` 曾硬编码
`credit_check.py` 原先写死 `os="windows"`，容器是 Linux，
camoufox 会按 Windows 的资产名（`camoufox.exe`）去找 → 启动直接失败。
已改为按 `sys.platform` 推导。

浏览器本体约 1.3 GB，`camoufox fetch` 要打 `api.github.com`，
而 91 到 GitHub **完全不通**（实测 curl 10 秒超时、0 字节）。
→ 改为「预置 zip 离线安装」+ 命名卷，见 `tools/install_camoufox.py`。

### 4.2 单进程约束
`aggregator.py` 里 `uvicorn.run(..., workers=1)`；credit 的作业队列、
Camoufox 实例、WIPO 的 `check_runs` 单行状态机**全在进程内**。
→ 多 worker 会静默错乱，`docker_entrypoint.py` 直接拒绝 `AGG_WORKERS>1`。

### 4.3 前端产物必须在 Windows 侧构建
`dist-agg/` 按 `.gitignore` 不入库，而 91 **没有 node**。
→ 构建放进 Dockerfile 的 `frontend` 阶段（`node:22-bookworm-slim`），
不要在 91 上构建。

## 5. 结论

**可行**，前提是自建镜像（不复用 84 的镜像）、浏览器离线安装、
`AGG_WORKERS=1`、前端在镜像内构建。

已于 2026-10-20 在 91 上完整验证：镜像构建成功、两个容器 `healthy`、
8 个端点全 200、**端到端真实巡检跑通**（信用代码 `9144030071526726XG`
→ 腾讯科技(深圳)有限公司 / 存续 / verdict=ok，30 秒）。
详见 [`Docker部署手册.md`](Docker部署手册.md) §7。

## 6. 本次踩到并已修的坑（按发现顺序）

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | 容器内 `/health` 恒 503 | WIPO 默认连 `127.0.0.1` MySQL，容器里没有 | compose 显式设 `WIPO_DATABASE__URL=sqlite+aiosqlite:////app/data/wipo.db` |
| 2 | `docker compose ps` 显示 healthy 但系统挂了 | HEALTHCHECK 探 `/api/health`（只证明进程活着） | 改探 `/health`（含 DB + 外部 API） |
| 3 | 作业失败 `InvalidAddonPath: manifest.json is missing` | `addons=[]` 拦不住默认插件；UBO 下载 451 留空目录 | 改 `exclude_addons=[DefaultAddons.UBO]` |
| 4 | `node:22-bookworm-slim` 里 `python` 命令不存在 | 只有 `python3` | 前端阶段显式装 `python3` 并调用它 |
| 5 | `GET /wipo/` → 307 → 405 | `.dockerignore` 排除了 `dist-agg`，镜像里没静态文件 | 从 `frontend` 阶段 `COPY --from` 取产物 |
