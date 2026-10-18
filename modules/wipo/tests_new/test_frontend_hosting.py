"""前端托管契约（`app/views.py`）。

这三条断言对应三个**曾经真实存在、且都不报错**的缺陷——它们不会让任何
测试变红，只会让行为静默地不对，所以必须钉住：

1. **`GET /` 必须带 `Cache-Control: no-store`。** 曾经的写法把
   `Mount("/")` 注册在 `APIRoute("/")` 之前，Starlette 按注册顺序匹配，
   于是 mount 把 `/` 一起吞掉，`_serve_index()` 永远不执行，`no-store`
   静默失效（`GET /` 的 cache-control 实测为 `None`）。

2. **未知的非 API 路径要回落到首页。** `SPAStaticFiles` 原先捕获的是
   `fastapi.HTTPException`，而 `StaticFiles.get_response` 抛的是
   `starlette.exceptions.HTTPException`——两者是各自独立的类
   （`issubclass(starlette的, fastapi的)` 为 `False`），`except` 永远不命中，
   回落从未生效过（`/nope` 返回 JSON 404）。

3. **未知的 `/api/*` 路径必须保持 JSON 404，不能回落成 HTML 首页。**
   否则拼错的接口路径会返回 200 + HTML，前端 `.json()` 抛解析错，
   排查方向被彻底带偏。注意 Windows 上 `StaticFiles` 传进来的 `path`
   用 `os.sep`（`'api\\nonexistent'`），只按 `/` 判断的守卫会静默失效。
"""

from __future__ import annotations

import pytest


def _dist_mounted() -> bool:
    """当前仓库是否挂了 Vue 构建产物（新克隆的仓库里 `web/dist` 不存在）。"""
    from app.core.config import PROJECT_ROOT
    from app.views import WEB_DIST

    return (PROJECT_ROOT / WEB_DIST / "index.html").is_file()


def test_index_has_no_store(client):
    """`GET /` 必须是 HTML 且带 no-store（两个分支都走 `_serve_index`）。"""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert resp.headers.get("cache-control") == "no-store"


def test_unknown_api_path_stays_json_404(client):
    """拼错的 API 路径不能被伪装成 200 的 HTML 首页。"""
    resp = client.get("/api/trademaks-typo")
    assert resp.status_code == 404
    assert "text/html" not in resp.headers.get("content-type", "")
    assert resp.headers.get("content-type", "").startswith("application/json")


def test_unknown_api_subpath_stays_json_404(client):
    """带子路径的 API 拼写错误同样保持 JSON 404（覆盖 Windows 反斜杠那条坑）。"""
    resp = client.get("/api/trademarks/nope/deeper")
    assert resp.status_code == 404
    assert resp.headers.get("content-type", "").startswith("application/json")


@pytest.mark.skipif(not _dist_mounted(), reason="未构建 web/dist，没有 SPA 回落")
def test_unknown_ui_path_falls_back_to_index(client):
    """未知的前端路径要回落到首页，而不是 JSON 404。"""
    resp = client.get("/some/deep/ui/path")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.skipif(not _dist_mounted(), reason="未构建 web/dist，无静态产物")
def test_vue_assets_are_served(client):
    """构建产物里的 assets/ 要能取到（不是被首页回落顶掉）。"""
    import re

    html = client.get("/").text
    match = re.search(r"assets/[\w.-]+\.js", html)
    assert match, "首页里没有引用任何 assets/*.js，产物可能不对"
    resp = client.get("/" + match.group(0))
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
