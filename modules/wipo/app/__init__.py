"""WIPO 商标监控系统 —— 应用包（FastAPI 分层架构）。

分层约定（参照 Power News Aggregator 的 ``newsplatform/``）::

    app/
      core/          配置 / 上下文 / 响应体 / 异常 / 日志 / 依赖注入
      api/v1/        路由模块（按功能域拆分，APIRouter 聚合）
      models/        Pydantic 请求响应模型 + SQLAlchemy ORM
      repositories/  数据访问（MySQL）
      services/      业务编排

顶层平铺的 ``wipo_client.py`` / ``monitor.py`` / ``notification.py`` /
``trademark_list.py`` / ``cryptojs_aes.py`` 是**业务内核**，与 Web 框架无关，
本包只在其上包一层可测试的 Web 层。
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "2.0.0"
