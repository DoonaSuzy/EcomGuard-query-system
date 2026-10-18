"""请求上下文中间件。

职责只有两件：
1. 为每个请求生成 ``request_id`` 并写入 ``contextvars``（响应体与日志都从这里取）；
2. 回写 ``X-Request-Id`` 响应头。

为什么用**纯 ASGI 中间件**而不是 ``BaseHTTPMiddleware``？
    ``BaseHTTPMiddleware`` 会在每个请求外层再套一个 task，而
    ``contextvars`` 的传播在那个边界上曾经有坑（子任务里 set 的值
    回到外层看不到）。纯 ASGI 中间件与请求同协程，set/reset 一定成对生效。
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import (
    REQUEST_ID_HEADER,
    RequestContext,
    new_request_id,
    reset_context,
    set_context,
)

__all__ = ["RequestContextMiddleware"]


def _client_ip(scope: Scope) -> str:
    """从 scope 里取客户端 IP（优先代理头，便于反代后仍能看到真实来源）。"""
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    forwarded = headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = scope.get("client")
    if client:
        return str(client[0])
    return ""


class RequestContextMiddleware:
    """把请求 id 与请求元信息放进 ``contextvars``。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        # 尊重上游传来的 request id（网关/前端已生成时不另造一个）
        request_id = headers.get(REQUEST_ID_HEADER.lower(), "") or new_request_id()
        ctx = RequestContext(
            request_id=request_id,
            client_ip=_client_ip(scope),
            path=scope.get("path", ""),
            method=scope.get("method", ""),
        )
        token = set_context(ctx)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                raw_headers: list[tuple[bytes, bytes]] = list(message.get("headers", []))
                if not any(k.lower() == REQUEST_ID_HEADER.lower().encode() for k, _ in raw_headers):
                    raw_headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message = {**message, "headers": raw_headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            reset_context(token)
