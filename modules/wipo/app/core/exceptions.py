"""业务异常层次（三层异常处理的第一层）。

旧 Flask 版把错误码散落在各路由里写 ``jsonify({...}), 400/404/409``；
这里收敛成异常：路由只管抛，由 ``exception_handlers`` 统一翻译成响应体。
好处是「同一个错误在不同路由里必须给出同样的状态码与文案」这件事
由类型系统保证，而不是靠每个 `if` 分支自觉。

业务码约定（与旧版 HTTP 语义对齐，不改变调用方看到的状态码）::

    1000  通用请求错误（400）
    1001  参数校验失败（422）
    2000  资源不存在（404）
    2001  资源冲突 / 重复（409）
    3000  服务器内部错误（500）
    3001  上游依赖失败（502，例如 WIPO 接口不可用）
    3002  服务暂不可用（503，例如数据库未就绪）
"""

from __future__ import annotations

from typing import Any

from fastapi import status

__all__ = [
    "AppError",
    "BadRequest",
    "ValidationFailed",
    "NotFound",
    "Conflict",
    "UpstreamError",
    "ServiceUnavailable",
    "InternalError",
    "CODE_NAMES",
    "code_name",
]


class AppError(Exception):
    """所有业务异常的基类。

    :param message: 面向调用方的中文文案（会原样进 ``error`` 字段）
    :param code: 稳定业务码
    :param status_code: HTTP 状态码
    :param extra: 追加到响应体的业务字段
    """

    code: int = 1000
    status_code: int = status.HTTP_400_BAD_REQUEST

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        status_code: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.extra = extra or {}

    def to_payload(self) -> dict[str, Any]:
        """给 ``exception_handlers`` 用的响应体片段。"""
        payload: dict[str, Any] = {"code": self.code}
        if self.extra:
            payload.update(self.extra)
        return payload


class BadRequest(AppError):
    """请求本身有问题（400）。"""

    code = 1000
    status_code = status.HTTP_400_BAD_REQUEST


class ValidationFailed(AppError):
    """参数校验失败（422）。

    FastAPI 0.143 里 ``HTTP_422_UNPROCESSABLE_ENTITY`` 已弃用（启动时会打
    ``StarletteDeprecationWarning``），改用
    ``HTTP_422_UNPROCESSABLE_CONTENT``。两者数值都是 422，不影响调用方。
    """

    code = 1001
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class NotFound(AppError):
    """资源不存在（404）。"""

    code = 2000
    status_code = status.HTTP_404_NOT_FOUND


class Conflict(AppError):
    """资源冲突，例如商标重名（409）。"""

    code = 2001
    status_code = status.HTTP_409_CONFLICT


class UpstreamError(AppError):
    """上游依赖失败，例如 WIPO 私有接口不可用（502）。"""

    code = 3001
    status_code = status.HTTP_502_BAD_GATEWAY


class ServiceUnavailable(AppError):
    """服务暂不可用，例如数据库未就绪（503）。"""

    code = 3002
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


class InternalError(AppError):
    """未预期的内部错误（500）。文案一律对外模糊化。"""

    code = 3000
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR


#: 业务码 → 人类可读名，供日志与文档使用
CODE_NAMES: dict[int, str] = {
    1000: "bad_request",
    1001: "validation_failed",
    2000: "not_found",
    2001: "conflict",
    3000: "internal_error",
    3001: "upstream_error",
    3002: "service_unavailable",
}


def code_name(code: int) -> str:
    """业务码的稳定名称；未知码回退成 ``unknown_<code>``。"""
    return CODE_NAMES.get(code, f"unknown_{code}")
