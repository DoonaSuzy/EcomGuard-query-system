"""FastAPI 依赖注入。

对应旧 Flask 版里「全局单例 + 直接 import 模块变量」的写法。换成 ``Depends``
之后，测试可以覆盖任意一层（数据库、配置、服务），不必再 monkeypatch 模块级
变量——旧版 ``tests/conftest.py`` 正是靠直接给 ``web_app.DATA_DIR`` 赋值来做
隔离的，那种做法在有并发请求时是全局污染。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import AppSettings, get_settings
from app.core.database import session_scope
from app.services.monitor_service import MonitorService
from app.services.trademark_service import TrademarkService

__all__ = [
    "SessionDep",
    "SettingsDep",
    "TrademarkServiceDep",
    "MonitorServiceDep",
]


async def get_session() -> AsyncIterator[AsyncSession]:
    """每请求一个数据库 Session。"""
    async for session in session_scope():
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[AppSettings, Depends(get_settings)]


def get_trademark_service(session: SessionDep) -> TrademarkService:
    """商标服务。"""
    return TrademarkService(session)


def get_monitor_service(session: SessionDep, settings: SettingsDep) -> MonitorService:
    """监控编排服务。"""
    return MonitorService(session, settings)


TrademarkServiceDep = Annotated[TrademarkService, Depends(get_trademark_service)]
MonitorServiceDep = Annotated[MonitorService, Depends(get_monitor_service)]
