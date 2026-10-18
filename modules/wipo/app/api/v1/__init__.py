"""v1 路由聚合。

对应旧 Flask 版的「在同一份 ``web_app.py`` 里连续堆 13 个 ``@app.route``」。
这里每个功能域一个模块，再统一挂到 ``api_router`` 上（等价于 PNA 的
``newsplatform/api/v1/__init__.py``）。

**为什么不直接在 main.py 里逐个 include？**
    路由清单集中在一处，``tests/test_api_schema.py`` 与
    ``docs/api-schema.json`` 的对照只需要看这一份元组；新增功能域时
    「忘挂路由」是这里唯一可能出错的地方，而它一眼可查。

**两个前缀不同的路由模块**：业务接口挂在 ``/api`` 下，而 ``/health`` 与
``/api/schema`` 是运维/元信息接口（PNA 也把 health 放在 ``/api/v1/health``
之外，这里沿用旧版路径 ``/health``）。所以 ``health`` 模块不设 prefix，
由它自己声明完整路径。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import check, health, io_routes, reports, trademarks

__all__ = ["api_router", "API_PREFIX", "ROUTERS", "iter_api_routes"]

#: 业务接口前缀。前端 ``static/js/app.js:4`` 写死 ``const API_BASE = '/api'``，
#: 因此这里不能改成 ``/api/v1``——路径版本化留给「未来真有 v2 时新增一份
#: routers 元组」去做，而不是现在就打断前端。
API_PREFIX = "/api"

#: (模块, 是否加 /api 前缀)。顺序即 OpenAPI 文档里的展示顺序。
ROUTERS: tuple[tuple[object, bool], ...] = (
    (trademarks, True),
    (check, True),
    (reports, True),
    (io_routes, True),
    (health, False),  # /health 与 /api/schema 自带完整路径
)


def build_api_router() -> APIRouter:
    """构造聚合后的 ``api_router``。

    坑（实测）：``app.routes`` 里这 5 个 include 进来的子路由器表现为
    ``starlette.routing._IncludedRouter`` 而不是普通 ``APIRoute``，且
    ``methods`` / ``path`` 都为空。这不是 bug——FastAPI 0.143 起
    ``include_router`` 改成「保留引用」而不是「逐条展开」（省掉重复注册的
    开销），要拿真实路由清单必须下钻 ``router.routes[i].router.routes``
    或者直接用 ``app.url_path_for`` / ``app.openapi()``。

    **不要**试图给 ``include_router`` 传 ``methods`` / ``name`` /
    ``response_model`` 来「修正」它——那些不是它的参数，会直接
    ``TypeError``。
    """
    router = APIRouter()
    for module, prefixed in ROUTERS:
        kwargs = {"prefix": API_PREFIX} if prefixed else {}
        router.include_router(module.router, **kwargs)
    return router


def iter_api_routes():
    """展开成真实的 ``APIRoute`` 迭代器（含前缀修正后的完整路径）。

    给测试与文档脚本用：它们需要的是「实际注册了哪些路径与方法」，
    而不是 ``_IncludedRouter`` 容器。
    """
    from fastapi.routing import APIRoute

    for module, _prefixed in ROUTERS:
        for route in module.router.routes:
            if isinstance(route, APIRoute):
                yield route


api_router = build_api_router()
