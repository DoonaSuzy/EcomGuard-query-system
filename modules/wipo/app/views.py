"""前端托管（首页模板 + 静态资源）。

旧 Flask 版由 ``render_template('templates/index.html')`` 与
``static_folder='static'`` 自动托管，前后端同域。FastAPI 没有内建模板渲染，
这里显式挂载，并处理三件事：

1. **``/static`` 前缀不能变**：``templates/index.html`` 里写的是
   ``/static/css/style.css`` 与 ``/static/js/app.js``（绝对路径），
   换前缀会 404。
2. **``GET /`` 要返回真正渲染好的 HTML**。``index.html`` 里没有任何
   Jinja2 变量（实测 ``grep '{{|{%|url_for'`` 零命中），所以**不需要
   模板引擎**——直接读文件返回即可。省掉 Jinja2 依赖、也省掉「模板语法
   误伤前端代码」的风险。若将来首页需要注入变量，再换成
   ``Jinja2Templates``。
3. **新旧前端共存时的取用顺序**：``web/dist/index.html``（Vue 3 构建产物）
   优先，不存在时回落 ``templates/index.html``（原生 JS 版）。
   这样迁移期两套前端可以并存——E2E 打的是 ``/``，谁在前面就测谁，
   切换不需要改后端代码，也不会出现「前端换了但后端还指向旧的」的错位。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

__all__ = ["mount_frontend", "SPAStaticFiles", "INDEX_FILE"]

INDEX_FILE = Path("templates") / "index.html"

#: Vue 3 + Vite 的构建产物目录。名字与 vite.config.js 的 build.outDir 对齐。
WEB_DIST = Path("web") / "dist"

#: 不参与 SPA 回落的路径前缀。与 ``app/api/v1/__init__.py`` 的 API_PREFIX
#: 保持一致（这里不 import 它，避免 views ← api 的反向依赖）。
API_PREFIXES = ("api/", "health", "docs", "redoc", "openapi.json", "static/")


def _is_api_path(path: str) -> bool:
    """判断静态处理器收到的是不是「本该由 API 处理的路径」。

    命中时未知路径返回 JSON 404，而不是伪装成 200 的 HTML 首页。

    **注意 ``path`` 用 ``os.sep`` 而不是 ``/``**：StaticFiles 会先把路径
    规范化成 ``posixpath``/``os.path`` 的形式，在 Windows 上收到的实际是
    ``'api\\nonexistent'``。只按 ``/`` 判断的话这个守卫会静默失效
    （踩过：``/api/nonexistent`` 返回了 200 的 HTML）。
    """
    normalized = path.replace("\\", "/")
    return normalized.startswith(API_PREFIXES)


def _resolve_index(root: Path) -> Path | None:
    """按「Vue 产物优先、原生版兜底」的顺序找首页。

    **为什么要兜底而不是直接切换**：107 项 E2E 是在原生版上写出来的，
    它们全部按 46 个 DOM 选择器定位元素。Vue 版必须先把这些选择器
    一一复现，才能切过去。兜底让你可以在「产物还没就绪」时照常跑测试，
    而不是拿到一堆 404 去猜是路由没注册还是构建失败。
    """
    for candidate in (root / WEB_DIST / "index.html", root / INDEX_FILE):
        if candidate.is_file():
            return candidate
    return None


def mount_frontend(app: FastAPI, root: Path) -> None:
    """把静态资源与首页挂到应用上。

    :param root: 项目根目录（应含 ``templates/`` 与 ``static/``）
    """
    static_dir = root / "static"
    if static_dir.is_dir():
        # name="static" 决定 URL 前缀为 /static，与 index.html 里的引用一致
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Vue 产物里自带 assets/ 等目录，用 SPAStaticFiles 托管：
    # 单页应用的任何子路径都要回落到 index.html 交给前端路由。
    dist_dir = root / WEB_DIST
    if dist_dir.is_dir():
        dist_index = dist_dir / "index.html"

        # **顺序：先声明 GET /，再挂载 Mount("/")。**
        #
        # Starlette 按注册顺序匹配。Mount("/") 会匹配包括 "/" 在内的**任何**
        # 路径（其 remainder 为空也算命中），而 APIRoute("/") 只匹配精确的
        # "/"。所以：
        #   - 先声明 APIRoute("/")："/" 命中它，子路径不匹配它 → 落到 Mount，
        #     首页拿到 _serve_index() 的 Cache-Control: no-store，静态资源照常。
        #   - 反过来（先 mount）：Mount("/") 把 "/" 一起吞掉，_dist_index 永远
        #     不会执行，no-store 静默失效——前端产物有哈希名不怕缓存，但
        #     index.html 本身被缓存就会让用户停留在旧构建上。
        @app.get("/", include_in_schema=False)
        async def _dist_index() -> FileResponse:
            return _serve_index(dist_index)

        app.mount("/", SPAStaticFiles(directory=str(dist_dir), html=True), name="web")
        return

    index_path = root / INDEX_FILE
    if not index_path.is_file():
        # 静态产物缺失时给一句能排障的话，而不是 404 让人以为路由没注册
        @app.get("/", include_in_schema=False)
        async def _missing_index() -> HTMLResponse:
            return HTMLResponse(
                "<h1>前端产物缺失</h1>"
                f"<p>未找到 <code>{index_path}</code>。本服务仍提供 API："
                '<a href="/docs">/docs</a></p>',
                status_code=200,
            )
        return

    @app.get("/", include_in_schema=False)
    async def _index() -> FileResponse:
        """首页。

        ``no-store`` 是刻意的：前端 ``app.js`` 有 744 行且不含版本号查询串，
        开发时若被浏览器缓存，改了 JS 却看不到效果，会把「没生效」误判成
        「改动有 bug」。
        """
        return _serve_index(index_path)


def _serve_index(path: Path) -> FileResponse:
    """统一的首页响应。

    ``no-store`` 是刻意的：前端资源不含版本号查询串，开发时若被浏览器
    缓存，改了 JS 却看不到效果，会把「没生效」误判成「改动有 bug」。
    """
    return FileResponse(
        path,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


class SPAStaticFiles(StaticFiles):
    """单页应用的静态处理器（用于 Vue 构建产物）。

    原生 JS 版是「一个 index.html + 多 section 切换」（``app.js`` 自管 4 个
    ``.page`` 的显隐），不存在前端路由。Vue 版同样刻意不做 URL 路由
    （110 项 E2E 直接断言 ``#page-input.active`` 这类选择器，URL 变化不是
    契约的一部分），所以这里的回落只是给「用户手输或收藏了某个路径」兜底。

    **两个必须做对的地方**（都踩过）：

    1. **要捕获 ``starlette.exceptions.HTTPException``，不是 FastAPI 那个。**
       ``StaticFiles.get_response`` 抛的是 starlette 的异常，而
       ``fastapi.HTTPException`` 是它的**子类**（``issubclass`` 为 False，
       两者是各自独立的类）。捕获 FastAPI 那个的话，``except`` 永远不命中，
       回落静默失效——表现为未知路径返回 JSON 404 而不是首页，且不报任何错。

    2. **``/api/`` 开头的一律不回落。** 本类挂在 ``/`` 上且注册在所有 API
       路由之后，所以一个拼错的 ``/api/trademaks`` 会落到这里。若无条件回落，
       它会被伪装成 200 的 HTML 首页——前端 ``app.js`` 拿到 HTML 去
       ``.json()`` 会抛解析错，排查方向会被彻底带偏。未知 API 路径必须
       保持 JSON 404。

    **回落有边界**：只有 404 才回落到 ``index.html``，其他 4xx/5xx
    原样抛出——否则一个真实的权限错误也会被伪装成首页。
    """

    async def get_response(self, path: str, scope: dict):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and not _is_api_path(path):
                return await super().get_response("index.html", scope)
            raise
