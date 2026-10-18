"""测试夹具：新 FastAPI 栈（C 方案的回归防线）。

与 ``tests/``（旧 Flask 栈）分开的原因见 ``pytest.ini`` 顶部的说明：
迁移期两套并存，各跑各的，避免 fixture 互相干扰。

这里用 FastAPI 的 ``TestClient``（基于 httpx + ASGITransport）跑真实 ASGI
应用——不启真端口、不起浏览器，因此比旧栈的 Playwright E2E 快一个数量级，
适合把业务规则逐条钉住。

**数据隔离**：把配置指向临时 SQLite 库（``aiosqlite`` 已装），
绝不用真实 MySQL 库跑测试。

**关键点 1：每个测试都重新建应用**。``MonitorService`` 会在启动检查时真的
拉起后台线程，跨测试复用同一个 ``app`` 会读到别的测试留下的 ``check_runs``
行，也会让残留线程继续打 WIPO。

**关键点 2：engine 也要跟着换**。``get_engine()`` 是进程级单例，
第一次建好就缓存住了；换临时库后必须 ``reset_engine()``，否则测试会连到
上一个用例（甚至真实 MySQL）的库上。
"""

from __future__ import annotations

import os
import sys

import pytest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """把应用配置指向临时 SQLite，并清掉所有单例缓存。"""
    import tempfile

    tmp = tempfile.mkdtemp(prefix="tm_new_")
    monkeypatch.setenv("WIPO_DATABASE__URL", f"sqlite+aiosqlite:///{tmp}/test.db")
    monkeypatch.setenv("WIPO_REPORTS_DIR", os.path.join(tmp, "reports"))
    monkeypatch.setenv("WIPO_TRADEMARKS_FILE", os.path.join(tmp, "user_trademarks.json"))

    from app.core.config import reset_settings
    from app.core.database import reset_engine

    reset_settings()
    reset_engine()
    yield tmp
    reset_settings()
    reset_engine()


@pytest.fixture()
def client(_isolate_env):
    """一个走完整 lifespan 的 TestClient（建表在启动钩子里完成）。"""
    import asyncio

    from fastapi.testclient import TestClient

    from app.core.database import dispose_engine
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
        asyncio.run(dispose_engine())


@pytest.fixture()
def session_factory(_isolate_env):
    """给服务层单测用的 Session 工厂（不经过 Web 层）。

    必须先建表，否则直接 ``session_scope()`` 会撞上「表不存在」。
    """
    import asyncio

    from app.core.database import Base, get_engine
    from app.models.orm import CheckRun, Report, Trademark  # noqa: F401 - 触发 mapper 注册

    async def _create():
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    return True


@pytest.fixture()
def db_session(session_factory):
    """一个**已建表**的 AsyncSession，用法 ``async with db_session as s:``。

    服务层单测用它直接钉业务规则（不经过 HTTP）。之所以不用
    ``app.core.database.session_scope()``：那个是 ``async def`` 生成器，
    只能被 FastAPI 的 Depends 消费，不能手动 ``async with``。
    """
    from contextlib import asynccontextmanager

    from app.core.database import get_sessionmaker

    @asynccontextmanager
    async def _open():
        factory = get_sessionmaker()
        async with factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    return _open
