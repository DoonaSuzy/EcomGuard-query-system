# syntax=docker/dockerfile:1
# ============================================================================
# 聚合查询与巡检系统 —— 生产镜像（GO1）
#
# 为什么不用仓库里现成的 power-news/app:local：那个镜像与 GO1 无关，
# 是 91 宿主机上原有的。这里从 debian:13-slim 自建，版本与依赖全部可控。
#
# 关键决策（每条都有实测依据，详见 docs/UBT-docker-部署可行性.md）：
#
#   1) opencv 用 **headless**，不用 opencv-python。
#      ddddocr 的依赖声明就是 opencv-python-headless；装成 GUI 版才会
#      需要 libxcb1 那一组 X11 库。headless 下 `import cv2` 直接成功。
#
#   2) apt 装包必须在本阶段用 root 完成，装完再切回 appuser。
#      镜像默认用户非 root 时 apt-get install 直接 Permission denied。
#
#   3) `camoufox fetch` 会在 addons.mozilla.org 拉 UBO 插件，该地址实测
#      返回 451，留下一个残缺目录，之后 Camoufox() 默认加载它 →
#      InvalidAddonPath: manifest.json is missing。
#      这里下载后立刻 rm -rf addons，并在启动参数里显式 addons=[]。
#
#   4) camoufox 浏览器本体 1.3 GB **不在镜像里**。
#      原因：camoufox fetch 从 github.com/daijro/camoufox/releases 下载，
#      构建机到这个域的网络不稳（实测 Connection aborted，重试无差别）。
#      改成由 compose 的 camoufox-init 容器在运行时拉一次、写进命名卷，
#      主机侧可挂代理，也可预先下好放进去 —— 构建期因此完全不依赖外网。
#
#   5) 阶段三只 COPY 源码 + 装 requirements，node 构建在阶段二；
#      改前端会先失效阶段二的缓存（那个大），改 Python 只失效阶段三。
#      这个顺序是刻意让「改后端」的构建最快。
# ============================================================================

# ---------- 阶段一：前端构建（Node） ----------
FROM node:22-bookworm-slim AS frontend

WORKDIR /src

# 先只拷清单，让 npm ci 能命中缓存。
# 注意 COPY 多源到目录时，若模块前端还有别的根级文件（vite.config.ts 等）
# 会在下面 `COPY modules ./modules` 时补齐，这里只放清单就够 npm ci 跑。
# 为什么不用 node:20：两个前端的 vite/vue 版本要求见各自 package.json，
# 用 node:22 是取了较新且 22 仍是 LTS 的版本；若将来引擎要求变化，改这里。
COPY modules/wipo/web/package.json modules/wipo/web/package-lock.json modules/wipo/web/
COPY modules/credit/frontend/package.json modules/credit/frontend/package-lock.json modules/credit/frontend/

# node 官方镜像里命令是 npm/node，**没有 python**。
# build_frontends.py 只需要标准库，所以装一个最小的 python3。
# （实测不装的话 `python tools/build_frontends.py` 直接 exit 127。）
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 \
 && rm -rf /var/lib/apt/lists/*

RUN cd modules/wipo/web && npm ci --no-audit --no-fund \
 && cd ../../credit/frontend && npm ci --no-audit --no-fund

COPY modules ./modules
COPY tools ./tools

# 两套产物都要构建：dist-agg（聚合用）+ dist（模块独立运行用）。
#
# 曾经只构建 dist-agg，理由是「dist/ 在容器里没有消费者」。这个判断是错的，
# 实测代价：
#   a) modules/wipo/app/views.py:35 的 WEB_DIST 是硬编码 `web/dist`，
#      不受 WIPO_DIST 环境变量影响；该目录不存在时 views.py:64 回落到
#      templates/index.html（旧原生 JS 前端）。于是容器里独立跑 WIPO 时
#      服务的是旧前端 —— 新版 Vue 的样式与修复全都不生效。
#   b) 镜像内跑 WIPO 自己的 E2E（110 项打默认 base）时，7 项图标/下载用例
#      因测到旧标记而失败（实测：编辑按钮宽 12 而非 28，即 CSS 完全没加载）。
#      也就是说镜像此前不是 WIPO 测试的忠实环境，只能靠宿主机跑。
# 不带 --agg 时脚本会先建 dist/ 再建 dist-agg/（见 tools/build_frontends.py:68），
# 一次构建两套齐全。
#
# 用 python3 而不是 python：node 官方镜像只带 python3，没有 python 这个别名
# （实测 `python: not found`，exit 127）。这是本阶段唯一的坑。
RUN python3 tools/build_frontends.py


# ---------- 阶段二：Python 依赖 ----------
FROM python:3.13-slim-bookworm AS deps

# 浏览器运行库：debian 13 下这组是实测成立的最小集合。
# 注意这里必须 root（本阶段默认就是），最后一并切用户。
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libgtk-3-0 libnss3 libatk1.0-0 libatk-bridge2.0-0 \
      libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
      libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 libxcb1 \
      libglib2.0-0 libsm6 libxext6 libxrender1 libgl1 libfontconfig1 \
      libdbus-1-3 \
 && rm -rf /var/lib/apt/lists/*

# 国内源：默认 pypi 在构建机上未必通。用 build-arg 可覆盖回官方源。
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_EXTRA_ARGS="--timeout 60 --retries 3"

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
      -i "$PIP_INDEX_URL" $PIP_EXTRA_ARGS

# 浏览器本体 + 验证码模型运行时文件。
#
# 为什么从 GitHub releases 下载会失败：camoufox fetch 默认走
# github.com/daijro/camoufox/releases，构建机的网络到这个域不稳
# （实测 Connection aborted / RemoteDisconnected，重试也一样）。
# 这里**不再在镜像里下载浏览器**，改成：
#   1) 镜像只装 Python 包（camoufox / playwright 等），
#   2) 浏览器本体由 compose 的 camoufox-init 容器在运行时拉一次，
#      写进命名卷；主机侧可以挂代理、也可以先手工下好放进去。
# 这样构建期不依赖 GitHub 可达，是「构建机没有外网」场景下唯一可行的形态。
#
# UBO 下载 451 是已知问题，删掉残缺目录避免 InvalidAddonPath；
# docker_entrypoint 在启动时还会再清一次（卷挂载后可能又出现），双保险。
# 这里仍保留一次 fetch 的**能力**：把下面三行的注释去掉即可在构建期拉取。
#
# RUN python -m camoufox fetch \
#  && rm -rf /root/.cache/camoufox/addons \
#  && test -f /root/.cache/camoufox/camoufox


# ---------- 阶段三：运行镜像 ----------
FROM python:3.13-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="aggregated-query-and-inspection"
LABEL org.opencontainers.image.source="F:/projects/GO1/aggregated-system"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # 聚合入口默认绑 127.0.0.1，容器里必须 0.0.0.0，否则端口映射出去也连不上
    AGG_HOST=0.0.0.0 \
    AGG_PORT=8000 \
    # 硬约束：scheduler 与 worker 都是进程内单实例，多 worker 会重复入队
    AGG_WORKERS=1 \
    # 产物由构建期生成，路径固定
    WIPO_DIST=/app/modules/wipo/web/dist-agg \
    CREDIT_DIST=/app/modules/credit/frontend/dist-agg \
    # camoufox 的家目录：见下方卷挂载
    HOME=/home/appuser

# 与 deps 阶段同一组系统库（运行时也要，浏览器要跑）
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libgtk-3-0 libnss3 libatk1.0-0 libatk-bridge2.0-0 \
      libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
      libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 libxcb1 \
      libglib2.0-0 libsm6 libxext6 libxrender1 libgl1 libfontconfig1 \
      libdbus-1-3 \
 && rm -rf /var/lib/apt/lists/*

# 非 root 运行：apt 已在上面用 root 做完，这里只建普通用户。
# （浏览器在普通用户下可跑，实测 UA 两种 os= 指纹都通。）
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
 && mkdir -p /home/appuser/.cache \
 && chown -R appuser:appuser /home/appuser

COPY --from=deps /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# camoufox 的家目录：deps 阶段没有 fetch，这里只建空目录占位，
# 真正的浏览器本体由 compose 的 camoufox-init 容器在运行时填进命名卷。
# 保留这条 COPY 的替代形态：若将来在构建期 fetch，取消下面注释即可。
# COPY --from=deps /root/.cache/camoufox /home/appuser/.cache/camoufox

WORKDIR /app
COPY aggregator.py credit_api_glue.py docker_entrypoint.py ./
COPY modules ./modules

# 前端产物必须从 frontend 阶段取，**不能**靠上面那条 COPY modules：
# .dockerignore 排除了 **/dist-agg/（避免把本地产物塞进构建上下文），
# 所以上下文里的 modules 是不带 dist-agg 的。少了这两行，
# 镜像里就没有 /wipo/ /credit/ 的静态文件，实测表现为
#   GET /wipo/ → 307 跳 /wipo → 405 Method Not Allowed
# （StaticFiles 目录不存在，Starlette 的 redirect_slashes 把请求打回去）。
COPY --from=frontend /src/modules/wipo/web/dist-agg ./modules/wipo/web/dist-agg
COPY --from=frontend /src/modules/credit/frontend/dist-agg ./modules/credit/frontend/dist-agg

# 独立运行版产物（默认 base）。聚合入口不读它们，但 modules 自己的
# 托管逻辑读：wipo 是 app/views.py:35 的硬编码 `web/dist`，
# credit 是 CREDIT_DIST 的默认值（api.py 里 BASE_DIR/"frontend"/"dist"）。
# 缺了这两行，容器内独立跑任一模块都会掉进旧前端/无前端，
# 镜像也就无法作为模块 E2E 的忠实环境（实测 7 项假失败）。
COPY --from=frontend /src/modules/wipo/web/dist ./modules/wipo/web/dist
COPY --from=frontend /src/modules/credit/frontend/dist ./modules/credit/frontend/dist

# tools/ 运行期也要：compose 的 camoufox-init 用 tools/install_camoufox.py
# 做离线安装（目标网络到 GitHub 不通，不能靠 camoufox fetch）。
COPY tools ./tools

# 数据目录：SQLite 兜底库、WIPO 数据、日志。MySQL 模式见 compose。
RUN mkdir -p /app/data /app/logs && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# 健康检查打 **WIPO 的 /health**（不是 credit 的 /api/health）。
#
# 实测踩过的坑：原先探 /api/health —— 那是 credit 模块的端点，只证明
# 「HTTP 进程活着」，不碰数据库。结果 WIPO 的库连不上、聚合 /health 一直是
# 503 时，容器在 docker compose ps 里仍然显示 (healthy)，运维看不到故障。
#
# /health 是 WIPO 的真实探针，返回
#   {"status":"ok|degraded|down","dependencies":{"database":..,"wipo_api":..}}
# 并带 200/503 状态码，因此「DB 挂了」会如实变成 unhealthy。
#
# 代价：它同时探外部 WIPO API，对方抖动时容器会短暂 unhealthy。
# 这里接受这个代价——本系统的核心价值就是那个外部接口，
# 它不可用时「健康」本身就是假话。restart: unless-stopped 不会因为
# unhealthy 重启容器，所以不会造成重启风暴。
HEALTHCHECK --interval=30s --timeout=8s --start-period=40s --retries=3 \
  CMD python -c "import os,urllib.request; \
      urllib.request.urlopen(os.environ.get('AGG_HEALTH_URL', \
      'http://127.0.0.1:8000/health'), timeout=5)" || exit 1

# 不走 `python aggregator.py`：docker_entrypoint 会先拒绝 AGG_WORKERS>1、
# 清理 UBO 残缺目录，再转交 aggregator。
CMD ["python", "docker_entrypoint.py"]
