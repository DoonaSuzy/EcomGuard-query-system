# -*- coding: utf-8 -*-
"""聚合查询与巡检系统 —— 统一入口。

一个 uvicorn 进程同时服务两个独立模块：

    /wipo/*      WIPO 商标监控前端（modules/wipo/web/dist-agg，--base=/wipo/ 构建）
    /credit/*    营业执照巡检前端（modules/credit/frontend/dist-agg，--base=/credit/ 构建）
    /api/*       WIPO 接口（11 条）
    /api/jobs*   营业执照接口（5 条，含 /api/health）
    /           壳页：两个模块的入口卡片

注意两个模块的 `dist-agg/` 与它们各自的 `dist/` 是**两套产物**：`dist/` 用默认 base
（模块独立运行与独立回归用），`dist-agg/` 带子路径 base（本聚合系统托管用）。
两者不能互相顶替，理由见 `WIPO_DIST` 上方的注释。
构建命令：`python tools/build_frontends.py`。

为什么是「单 app + include_router」而不是「mount 两个子 app」
------------------------------------------------------------
``root.mount("/mod/wipo", wipo_app)`` 这种写法实测有两个硬缺陷（见
``docs/单前端双后端整合评估.md`` §2）：

  1. 主应用的 ``openapi()['paths']`` 是**空列表** —— Starlette 的 mount
     不合并子应用的 paths，前端无法一次生成全部类型；
  2. 子应用的 ``lifespan`` **不执行**（实测启动日志只有 ROOT START/STOP），
     ``app.state`` 全是 MISSING，子应用一访问自己的 state 就报错。

``include_router`` 没有这两个问题：路由直接进主 app 的 openapi（实测 16 条、
零重复），lifespan 由本文件的 ``combined_lifespan`` 显式串联。

两个模块的 lifespan 必须显式组合
--------------------------------
WIPO 的 lifespan 负责建表 / 释放连接池；credit 的负责建 Store 与启动
worker 线程。两个都要跑，所以手写 ``combined_lifespan`` 依次进入。

credit 侧还有一个坑：它原来的 handler 读的是**模块级** ``app.state``
（``api.py`` 里的 ``app = FastAPI(...)``），include 进主 app 后那个模块级
app 的 state 不会被赋值 → 500。已在 ``modules/credit/api.py`` 里改成
``request.app.state``（10 处），这里只需保证主 app 的 state 有值。
"""
from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

BASE_DIR = Path(__file__).resolve().parent
MODULES = BASE_DIR / "modules"
WIPO_DIR = MODULES / "wipo"
CREDIT_DIR = MODULES / "credit"

# 两个模块都是「把各自目录当 sys.path 根」写的（credit 里是 `import credit_check`、
# `import logkit`，WIPO 里是 `import monitor`）。所以都要进 sys.path，
# 且必须放在最前面 —— 否则同名模块可能解析到别的目录。
for _p in (str(WIPO_DIR), str(CREDIT_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# WIPO 的默认数据落点是 PROJECT_ROOT/data 与 PROJECT_ROOT/reports，
# 而 PROJECT_ROOT 由 app/core/config.py 推算（= modules/wipo）。
# 这里不改它 —— 让每个模块的数据留在自己的目录里，互相不污染。
if "WIPO_REPORTS_DIR" not in os.environ:
    os.environ["WIPO_REPORTS_DIR"] = str(WIPO_DIR / "reports")

# ---------------------------------------------------------------- 导入两个模块
from app.api.v1 import build_api_router  # noqa: E402
from app.core.exception_handlers import register_exception_handlers  # noqa: E402
from app.core.middleware import RequestContextMiddleware  # noqa: E402
from app.main import lifespan as wipo_lifespan  # noqa: E402

import credit_api_glue  # noqa: E402  (见文件尾部说明)
from credit_api_glue import credit_lifespan_enter, credit_lifespan_exit  # noqa: E402

__all__ = ["app", "create_app"]

WIPO_DIST = Path(os.getenv("WIPO_DIST", WIPO_DIR / "web" / "dist-agg"))
CREDIT_DIST = Path(os.getenv("CREDIT_DIST", CREDIT_DIR / "frontend" / "dist-agg"))

# 两套产物并存，原因：
#   模块自己的 `dist/` 按默认 base（`/` 或 `./`）构建 —— 那是它们**独立运行**
#   与**独立跑回归**的产物（WIPO 的 110 项 E2E 直接打站点根；credit 的
#   test_regression.py 也按根路径断言）。挂到子路径后必须换成
#   `dist-agg/`（构建时带 `--base=/wipo/`、`--base=/credit/`），因为 Vite 的
#   动态 chunk 是按**模块 URL**解析的（`import("./OverviewView-x.js")`），
#   改 HTML 或用 `<base href>` 都纠正不了它 —— 只能是构建期把 base 写进产物。
#   构建命令：python tools/build_frontends.py（两套产物一次建完）。
_WIPO_DIST_STANDALONE = WIPO_DIR / "web" / "dist"
_CREDIT_DIST_STANDALONE = CREDIT_DIR / "frontend" / "dist"


class SPAFiles(StaticFiles):
    """挂在子路径下的 SPA 静态处理器。

    与 WIPO 的 ``SPAStaticFiles`` 同一套路，但多两条：

    1. **确认 index.html 真的存在**。挂一个空目录会让整站 404 而不是白屏，
       排查时容易误以为是路由问题。
    2. **带扩展名的路径缺文件时直接 404**，不回落 index.html。否则浏览器
       请求 `assets/x.js` 拿到 200 + HTML，报错信息完全误导（表现为
       `Unexpected token '<'`，而不是干净的 404）。

    ⚠️ 资源前缀由**构建期**解决（产物必须带 `--base=/wipo/`），不能在
    这里改 HTML：Vite 的动态 chunk 是按模块 URL 解析的
    （`import("./OverviewView-x.js")`），改 HTML 或加 `<base href>` 都纠
    正不了。托管层只负责 SPA 深链兜底。

    回落条件只有「404 且不是 /api 前缀」——其他 4xx/5xx 原样抛出，
    否则真实的权限错误会被伪装成首页。
    """

    def __init__(self, *, directory: str, prefix: str, runtime_base: str = "") -> None:
        super().__init__(directory=directory, html=True)
        self.prefix = prefix.rstrip("/")
        self.runtime_base = runtime_base

    async def get_response(self, path: str, scope: dict):  # type: ignore[override]
        norm = path.replace("\\", "/")
        try:
            resp = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or norm.startswith("api/"):
                raise
            # 静态资源（assets/x.js、favicon.ico）缺文件就该 404
            if Path(norm).suffix and not norm.endswith(".html"):
                raise
            resp = await super().get_response("index.html", scope)

        if "text/html" not in resp.headers.get("content-type", ""):
            return resp
        index = Path(self.directory) / "index.html"
        if not index.is_file():
            return resp
        html = index.read_text(encoding="utf-8")
        if self.runtime_base:
            # vue-router 的 base 是构建期常量。产物默认按站点根构建，
            # 挂到子路径时用这个全局变量在运行时纠正。
            snippet = f'<script>window.{self.runtime_base}="{self.prefix}/"</script>'
            html = html.replace("</head>", f"{snippet}</head>", 1)
        return Response(
            content=html,
            status_code=resp.status_code,
            media_type="text/html",
        )


SHELL_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>聚合查询与巡检系统</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; display: flex; align-items: center;
    justify-content: center; font-family: system-ui, -apple-system,
    "Segoe UI", "Microsoft YaHei", sans-serif;
    background: #f5f6f8; color: #1f2328;
  }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #16181d; color: #e6edf3; }}
    .card {{ background: #1f2329 !important; border-color: #30363d !important; }}
    .card p {{ color: #9198a1 !important; }}
  }}
  .wrap {{ width: min(920px, 92vw); padding: 32px 0; }}
  h1 {{ font-size: 26px; margin: 0 0 8px; letter-spacing: .3px; }}
  .lead {{ margin: 0 0 28px; color: #59636e; font-size: 14px; }}
  .grid {{ display: grid; gap: 18px; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }}
  .card {{
    display: block; padding: 22px 24px; border-radius: 12px;
    background: #fff; border: 1px solid #d8dee4; text-decoration: none;
    color: inherit; transition: transform .15s ease, box-shadow .15s ease;
  }}
  .card:hover {{ transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,.10); }}
  .card h2 {{ margin: 0 0 10px; font-size: 18px; }}
  .card p {{ margin: 0 0 16px; color: #59636e; font-size: 13.5px; line-height: 1.65; }}
  .tag {{
    display: inline-block; padding: 3px 9px; border-radius: 999px;
    font-size: 12px; background: #eef2f6; color: #4a5560;
  }}
  @media (prefers-color-scheme: dark) {{
    .tag {{ background: #262c34; color: #a8b3bd; }}
    .lead {{ color: #9198a1; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <h1>聚合查询与巡检系统</h1>
  <p class="lead">两个独立模块，同一个服务入口。选择要进入的控制台。</p>
  <div class="grid">
    <a class="card" href="/wipo/">
      <h2>WIPO 商标监控</h2>
      <p>批量检查商标法律状态与疑似抢注，归档月度报告并在异常时通知。</p>
      <span class="tag">商标 / 报告 / 通知</span>
    </a>
    <a class="card" href="/credit/">
      <h2>营业执照状态巡检</h2>
      <p>批量查询企业登记状态（存续 / 注销 / 吊销），异常时推送提醒。</p>
      <span class="tag">作业队列 / CSV / 推送</span>
    </a>
  </div>
</div>
</body>
</html>
"""


def _mount_module_frontend(
    app: FastAPI, prefix: str, dist: Path, runtime_base: str = ""
) -> bool:
    """把某个模块的前端产物挂到 ``prefix`` 下。返回是否真的挂上。"""
    index = dist / "index.html"
    if not index.is_file():
        return False
    app.mount(
        prefix,
        SPAFiles(directory=str(dist), prefix=prefix, runtime_base=runtime_base),
        name=prefix.strip("/"),
    )
    return True


def create_app() -> FastAPI:
    @contextlib.asynccontextmanager
    async def combined_lifespan(application: FastAPI) -> AsyncIterator[None]:
        """依次进两个模块的 lifespan，退出时反序关闭。"""
        async with wipo_lifespan(application):
            await credit_lifespan_enter(application)
            try:
                yield
            finally:
                await credit_lifespan_exit()

    app = FastAPI(
        title="聚合查询与巡检系统",
        version="1.0.0",
        description=(
            "两个独立模块的统一入口：\n\n"
            "- **WIPO 商标监控** —— 商标状态与抢注检查（`/api/trademarks`、`/api/check`…）\n"
            "- **营业执照状态巡检** —— 企业登记状态查询（`/api/jobs`、`/api/health`…）\n\n"
            "两个模块的接口前缀不冲突，前端各挂在自己的子路径下。"
        ),
        lifespan=combined_lifespan,
    )

    # 中间件顺序与 WIPO 单模块一致：先 CORS 后 RequestContext，
    # 让 request_id 最早生成、CORS 预检响应也带上 X-Request-Id。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )
    app.add_middleware(RequestContextMiddleware)

    # 统一错误外壳：credit 原生的 {"detail": ...} 会被这里的 handler
    # 转成与 WIPO 一致的 {"success": false, "error": ...}（实测生效）。
    register_exception_handlers(app)

    # ---- 接口：两个模块的 router 都直接进主 app（openapi 会合并）----
    app.include_router(build_api_router())
    from credit_api_glue import router as credit_router

    app.include_router(credit_router)

    # ---- 子路径重定向：/credit 没有尾斜杠时会被壳页路由接走 ----
    # 必须在 mount 之前、且必须是精确路径；用 307 保留方法语义。
    @app.get("/wipo", include_in_schema=False)
    async def _wipo_redirect() -> RedirectResponse:
        return RedirectResponse("/wipo/", status_code=307)

    @app.get("/credit", include_in_schema=False)
    async def _credit_redirect() -> RedirectResponse:
        return RedirectResponse("/credit/", status_code=307)

    @app.get("/", include_in_schema=False)
    async def _shell(request: Request) -> HTMLResponse:
        return HTMLResponse(SHELL_HTML, headers={"Cache-Control": "no-store"})

    # ---- 前端：先挂子路径，最后才挂根（顺序有语义）----
    # 两个模块的 dist 若缺一个，只少一个入口，不影响另一个与全部接口。
    _mount_module_frontend(app, "/wipo", WIPO_DIST)
    # credit 的 vue-router base 是构建期常量，独立部署时是 `/`，
    # 这里通过注入 `window.__CREDIT_BASE__` 在托管层纠正到 `/credit/`。
    _mount_module_frontend(app, "/credit", CREDIT_DIST, runtime_base="__CREDIT_BASE__")

    return app


app = create_app()


def main() -> None:
    """``python aggregator.py`` 的入口。"""
    import uvicorn

    host = os.getenv("AGG_HOST", "127.0.0.1")
    port = int(os.getenv("AGG_PORT", "8000"))
    # --workers 必须是 1：credit 的作业队列与 Camoufox 实例都是进程内的，
    # WIPO 的 check_runs 是单行状态机，多进程都会错乱。
    uvicorn.run("aggregator:app", host=host, port=port, workers=1, log_level="info")


if __name__ == "__main__":
    main()
