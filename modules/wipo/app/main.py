"""FastAPI 应用工厂与入口。

对应旧版 ``web_app.py`` 末尾的模块级 ``app = Flask(...)`` —— 这里换成
``create_app()`` 工厂 + ``lifespan``，与 PNA 的 ``newsplatform/main.py`` 同一
套路子。

**中间件顺序（容易踩）**：Starlette 的 ``add_middleware`` 是**后进先出**
（最后添加的最靠近业务），所以先加 CORS、后加 ``RequestContextMiddleware``，
让 ``request_id`` 最早生成，CORS 预检响应也能带上 ``X-Request-Id``。

**为什么用 ``lifespan`` 而不是 ``@app.on_event("startup")``**：后者在
FastAPI 0.109+ 已弃用，且 ``lifespan`` 天然保证 ``yield`` 前的资源一定在
``yield`` 后释放（数据库连接池不会泄漏）。

**数据库表**：启动时 ``create_all``。这是迁移期的取舍——正式生产应该用
Alembic 迁移（依赖已装、pyproject 里也锁了版本），但当前只有三张表、
且不存在需要保留的历史库，``create_all``（幂等、已存在的表不动）足够。
``alembic`` 的接入留到「第一次需要改列」时再做。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.core.config import PROJECT_ROOT, get_settings
from app.core.database import Base, dispose_engine, get_engine
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.views import mount_frontend

__all__ = ["app", "create_app", "lifespan"]

log = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动与关闭钩子。"""
    settings = get_settings()
    configure_logging(settings.debug)
    log.info("启动 %s %s", settings.app_name, settings.app_version)
    for key, value in settings.describe().items():
        log.info("  配置 %-22s = %s", key, value)

    # 建表（幂等）。失败不抛——让 /health 报 down，运维能在启动日志里看见
    # 原因，而不是容器起不来却看不出为什么。
    try:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("数据库表已就绪")
    except Exception as exc:  # noqa: BLE001 - 启动期探活失败不阻塞进程
        log.warning("数据库初始化失败，/health 将报告 down", error=str(exc))

    yield

    log.info("关闭中，释放数据库连接池")
    await dispose_engine()


def create_app() -> FastAPI:
    """构造 FastAPI 应用。"""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "WIPO 商标监控系统的 HTTP 接口。自动检查商标状态与疑似抢注情况，"
            "按月归档报告并在异常时通知。\n\n"
            "**响应外壳保持旧版不变**：成功 ``{\"success\": true, \"data\": ...}``、"
            "失败 ``{\"success\": false, \"error\": ...}``，额外追加 ``request_id``。"
            "改外壳会一次性打断现有前端与 E2E 用例。"
        ),
        docs_url=settings.docs_url,
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # 顺序见模块 docstring：先 CORS，后 RequestContextMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    # /health 与 /api/schema 挂在根上，业务接口在 api_router 里自带 /api 前缀
    app.include_router(api_router)

    mount_frontend(app, PROJECT_ROOT)
    return app


#: uvicorn / gunicorn 的目标对象
app = create_app()


def main() -> None:
    """``python -m app.main`` 的入口。"""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="debug" if settings.debug else "info",
    )


if __name__ == "__main__":
    main()
