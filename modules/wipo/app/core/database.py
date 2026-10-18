"""数据库连接与 Session 管理（SQLAlchemy 2.x 异步）。

为什么用异步 ORM？
    FastAPI 的路由是 ``async def``，在协程里跑同步 DB 调用会阻塞整个事件循环
    （单请求慢 → 全体排队）。aiomysql 驱动 + ``AsyncSession`` 让 DB 等待
    真正让出事件循环。

关于 ``pool_pre_ping``（踩过的坑）
    SQLAlchemy 2.0.x 的 aiomysql 方言复用了 PyMySQL 的 ``do_ping``，而它靠
    「本机 PyMySQL 的 ping 默认值」决定调用形态；当默认值是 ``False`` 时会调
    无参 ``ping()``，与 aiomysql 适配器的 ``ping(reconnect)`` 签名不匹配 →
    一开 ``pool_pre_ping`` 首个请求就 500。这里用同样的补丁显式修掉，
    而不是关掉 pre_ping（关掉就失去了「连接被 MySQL 8 小时超时掐断后自动重连」
    的保护，长驻服务会周期性 500）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import DatabaseSettings, get_settings
from app.core.logging import get_logger

__all__ = [
    "Base",
    "get_engine",
    "get_sessionmaker",
    "dispose_engine",
    "session_scope",
    "ping",
    "reset_engine",
]

log = get_logger("db")

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def _patch_aiomysql_pre_ping() -> None:
    """修正 aiomysql 方言的 ``do_ping`` 签名不匹配问题（见模块 docstring）。"""
    try:
        from sqlalchemy.dialects.mysql import aiomysql as _aiomysql
        from sqlalchemy.dialects.mysql.aiomysql import MySQLDialect_aiomysql
    except ImportError:  # pragma: no cover - 未装 aiomysql 时无需补丁
        return

    original = getattr(MySQLDialect_aiomysql, "do_ping", None)
    if original is None or getattr(original, "_wipo_patched", False):
        return

    def do_ping(self: Any, dbapi_connection: Any) -> bool:  # noqa: ANN401
        try:
            dbapi_connection.ping(False)
        except Exception:  # noqa: BLE001 - ping 失败即视为连接失效，交由上层重连
            return False
        return True

    do_ping._wipo_patched = True  # type: ignore[attr-defined]
    MySQLDialect_aiomysql.do_ping = do_ping  # type: ignore[assignment]
    log.debug("已补丁 aiomysql do_ping", module=_aiomysql.__name__)


def build_engine_kwargs(settings: DatabaseSettings) -> dict[str, Any]:
    """按后端类型给出合理的 engine 参数。

    SQLite 不支持连接池大小 / pre_ping 语义不同，这里分开处理，
    让同一份代码既能在 MySQL 上跑生产、也能在测试里用 aiosqlite 落临时库。
    """
    url = settings.url
    if url.startswith("sqlite"):
        return {"echo": settings.echo, "future": True}
    return {
        "echo": settings.echo,
        "future": True,
        "pool_size": settings.pool_size,
        "max_overflow": settings.max_overflow,
        "pool_recycle": settings.pool_recycle,
        "pool_pre_ping": True,
    }


def get_engine() -> AsyncEngine:
    """进程级单例 engine（惰性创建）。"""
    global _engine
    if _engine is None:
        _patch_aiomysql_pre_ping()
        settings = get_settings().database
        _engine = create_async_engine(settings.url, **build_engine_kwargs(settings))
        log.info("数据库 engine 已创建", url=_safe_url(settings.url))
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """进程级单例 session 工厂。"""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,  # commit 后仍可读属性，避免路由里额外查一次
            autoflush=False,
        )
    return _sessionmaker


async def session_scope() -> AsyncIterator[AsyncSession]:
    """依赖注入用的 Session 生成器：请求结束自动关闭并回滚未提交事务。"""
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def make_worker_session_factory() -> async_sessionmaker[AsyncSession]:
    """为**后台线程**单独建一套 engine + session 工厂。

    为什么不能共用进程级单例？
        aiomysql 的连接绑定在创建它的那个事件循环上。检查任务在线程里
        用 ``asyncio.run()`` 跑（它自建一个新的、短命的事件循环），若复用
        主循环的 engine，连接池里已有的连接会因为「属于另一个 loop」而报错。
        因此后台线程自建 engine，用完 dispose。

    这也解释了为什么这里返回工厂而不是 Session：worker 需要在自己的
    loop 里 ``async with`` 才安全。
    """
    _patch_aiomysql_pre_ping()
    settings = get_settings().database
    engine = create_async_engine(settings.url, **build_engine_kwargs(settings))
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def dispose_engine() -> None:
    """释放连接池（lifespan shutdown 里调用）。"""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        log.info("数据库连接池已释放")
    _engine = None
    _sessionmaker = None


def reset_engine() -> None:
    """丢弃缓存的 engine/sessionmaker（测试切换数据库时用）。"""
    global _engine, _sessionmaker
    _engine = None
    _sessionmaker = None


async def ping() -> bool:
    """探活：能跑通 ``SELECT 1`` 就算健康。

    失败**不抛异常**——``/health`` 要能如实报告 ``down``，
    而不是自己 500 掉。
    """
    from sqlalchemy import text

    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 - 探活失败是正常业务分支
        log.warning("数据库探活失败", error=str(exc))
        return False


def _safe_url(url: str) -> str:
    """日志里隐藏口令。"""
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1) if "://" in url else ("", url)
    creds, host = rest.rsplit("@", 1)
    if ":" in creds:
        user, _ = creds.split(":", 1)
        return f"{scheme}://{user}:***@{host}"
    return f"{scheme}://{creds}@{host}"
