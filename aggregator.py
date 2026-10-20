"""聚合查询与巡检系统 —— 统一入口。

一个 uvicorn 进程同时服务两个独立模块，前端只有**一套**：

    /*          统一前端（web/dist，Vue 3 + vue-router，SPA 深链兜底）
    /api/*      WIPO 接口（11 条）
    /api/jobs*  营业执照接口（5 条，含 /api/health）

前端从「两个 SPA + iframe 壳」改成了「一套 SPA + 左侧系统列表」：两套独立的
全局 CSS（同名类 .page/.btn/.card/.badge/.toast）不再同页共存，因此不再需要
iframe 隔离，也不再有「切换系统时整页样式跳变」。两个模块的前端源码没有被
复制或重写 —— 它们作为「页面包」被 web/ 用 Vite 别名引用（@wipo、@），
各自仍能独立构建与独立跑回归（见 docs/单前端双后端整合评估.md）。

构建命令：`python tools/build_frontends.py`（一次建三套：web/dist 统一产物，
外加两个模块各自的 dist 与 dist-agg 供模块独立运行/回归使用）。

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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
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
from app.api.v1 import build_api_router
from app.core.exception_handlers import register_exception_handlers
from app.core.middleware import RequestContextMiddleware
from app.main import lifespan as wipo_lifespan

# credit 不直接 import credit_api —— lifespan 的进入/退出被拆成两个函数，
# 便于与 WIPO 的 lifespan 显式串联（见 credit_api_glue.py 的 docstring）。
from credit_api_glue import credit_lifespan_enter, credit_lifespan_exit

__all__ = ["app", "create_app"]

# 统一前端的产物。**只有这一份**参与聚合托管：两个模块的源码被 web/ 以
# Vite 别名引用后打包进同一份 bundle，所以不需要再按子路径各挂一套。
#
# 模块自己的 `dist/`（默认 base）与 `dist-agg/`（带 /wipo/、/credit/ 前缀）
# 依然保留，但用途变了：它们只服务于**模块独立运行**与**模块独立回归**
# （WIPO 的 118 项 E2E 直接打站点根，credit 的 TestFrontendHosting 同理），
# 聚合入口不再引用它们。
UNIFIED_DIST = Path(os.getenv("WEB_DIST", BASE_DIR / "web" / "dist"))


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


def _mount_unified_frontend(app: FastAPI, dist: Path) -> bool:
    """把统一前端挂到站点根。返回是否真的挂上。

    没挂上时接口仍然全部可用（只少一个页面入口），这与统一前的行为一致：
    产物缺失不该让整个服务起不来。
    """
    index = dist / "index.html"
    if not index.is_file():
        return False
    app.mount(
        "",
        SPAFiles(directory=str(dist), prefix=""),
        name="web",
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
        # 分组顺序与说明：合并后的文档里两个模块的接口必须能一眼分清归属，
        # 否则 Swagger 会按路由注册顺序平铺（credit 原先没有 tags，全落进 default）。
        openapi_tags=[
            {"name": "trademarks", "description": "商标清单增删改查（WIPO 模块）"},
            {"name": "check", "description": "商标状态与抢注检查（WIPO 模块）"},
            {"name": "reports", "description": "检查报告归档与详情（WIPO 模块）"},
            {"name": "io", "description": "清单导入导出（WIPO 模块）"},
            {"name": "jobs", "description": "营业执照巡检查询作业（营业执照模块）"},
            {"name": "schedules", "description": "定时巡检的增删改查与手动触发（营业执照模块）"},
            {"name": "system", "description": "两个模块共用的健康检查与接口自描述"},
        ],
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

    # ---- 旧地址重定向（统一前端后这两个子路径不再存在）----
    # 之前是「两个 SPA 各挂一个子路径 + iframe 壳」，所以 /wipo/、/credit/
    # 曾经是真实入口。现在前端只有一套，把它们指到统一前端里对应的首页，
    # 免得旧书签/文档里的链接落到 404。
    # 用 307 保留方法语义；必须精确路径，且必须在 mount 之前注册。
    @app.get("/wipo", include_in_schema=False)
    @app.get("/wipo/", include_in_schema=False)
    async def _legacy_wipo() -> RedirectResponse:
        return RedirectResponse("/input", status_code=307)

    @app.get("/credit", include_in_schema=False)
    @app.get("/credit/", include_in_schema=False)
    async def _legacy_credit() -> RedirectResponse:
        return RedirectResponse("/overview", status_code=307)

    # ---- 前端：统一 SPA 挂在站点根，**必须最后挂** ----
    # Starlette 按注册顺序匹配：接口路由（含 /docs、/openapi.json、/health）
    # 都在上面注册过了，所以根 mount 只兜住剩下的页面路径。
    _mount_unified_frontend(app, UNIFIED_DIST)

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
