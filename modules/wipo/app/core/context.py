"""请求上下文（``contextvars`` 版 ``flask.g``）。

Flask 用 ``g`` 挂请求级状态，但在异步框架里线程局部变量会串请求；
``contextvars`` 随协程传播，是 asyncio 下唯一正确的选择。

除 ``request_id`` 外还放 ``client_ip`` / ``path``：审计与限流键都要用，
但都不适合塞进函数签名层层传递。
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field

__all__ = [
    "RequestContext",
    "get_request_id",
    "new_request_id",
    "set_context",
    "reset_context",
    "current_context",
    "REQUEST_ID_HEADER",
]

#: 与前端/网关约定一致的追踪头
REQUEST_ID_HEADER = "X-Request-Id"


@dataclass(slots=True)
class RequestContext:
    """一次请求的上下文快照。"""

    request_id: str = ""
    client_ip: str = ""
    path: str = ""
    method: str = ""
    extra: dict[str, str] = field(default_factory=dict)


_CTX: ContextVar[RequestContext | None] = ContextVar("wipo_request_ctx", default=None)


def new_request_id() -> str:
    """生成新的请求 id（32 位十六进制，无连字符，便于日志检索）。"""
    return uuid.uuid4().hex


def set_context(ctx: RequestContext) -> Token[RequestContext | None]:
    """写入当前协程上下文，返回可用于回滚的 token。"""
    return _CTX.set(ctx)


def reset_context(token: Token[RequestContext | None]) -> None:
    """回滚上下文（中间件 finally 里调用）。"""
    _CTX.reset(token)


def current_context() -> RequestContext | None:
    """取当前上下文，不在请求内时为 None。"""
    return _CTX.get()


def get_request_id() -> str:
    """取当前请求 id；不在请求上下文内时返回空串。

    空串而不是新生成——响应体里的 ``request_id`` 必须与 ``X-Request-Id``
    响应头一致，凭空造一个会让两者对不上。
    """
    ctx = _CTX.get()
    return ctx.request_id if ctx else ""
