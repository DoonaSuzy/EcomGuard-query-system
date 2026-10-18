"""统一响应体。

**契约兼容性（关键）**：旧 Flask 版的前端 ``static/js/app.js`` 在 5 处判断
``if (result.success)``（:217 / :271 / :418 / :457 / :495），106 项 Playwright
E2E 用例也依赖同一外壳。因此这里**保留 ``success`` 外壳不动**，只追加
``request_id`` 与 ``X-Request-Id`` 响应头：调用方按老字段继续工作，
排障时多了请求追踪。

成功：``{"success": true, "data": {...}, "request_id": "..."}``
失败：``{"success": false, "error": "...", "request_id": "..."}``

不要改成 PNA 的 ``{"code": 0, "data": ...}`` —— 那会一次性打断前端与 E2E。
``code`` 只作为**附加**业务码存在（可选），不是替代品。
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from fastapi import status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.context import get_request_id

__all__ = [
    "Envelope",
    "PageData",
    "ok",
    "fail",
    "json_ok",
    "json_fail",
    "not_found",
    "bad_request",
]

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    """统一响应体的 Pydantic 形态（仅用于 OpenAPI 文档展示）。"""

    success: bool = Field(True, description="是否成功")
    data: T | None = Field(None, description="业务数据")
    error: str | None = Field(None, description="错误信息（成功时为 null）")
    request_id: str = Field("", description="请求追踪 id，与响应头 X-Request-Id 一致")


class PageData(BaseModel, Generic[T]):
    """分页数据的标准结构。"""

    items: list[T] = Field(default_factory=list)
    total: int = Field(0, description="命中总条数")
    page: int = Field(1, description="当前页码，从 1 开始")
    page_size: int = Field(20, description="每页条数")
    pages: int = Field(0, description="总页数")


def ok(data: Any = None, *, request_id: str | None = None, code: int | None = None) -> dict[str, Any]:
    """构造成功响应体。

    ``code`` 为可选业务码，仅在需要时出现（如批量操作的部分成功提示），
    默认不写——避免给既有解析方制造新字段噪音。
    """
    payload: dict[str, Any] = {
        "success": True,
        "data": data,
        "request_id": request_id if request_id is not None else get_request_id(),
    }
    if code is not None:
        payload["code"] = code
    return payload


def fail(
    error: str,
    *,
    request_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造失败响应体。

    ``extra`` 用于追加**业务必需**的字段（例如批量删除的
    ``deletedCount``），但绝不追加内部细节（堆栈 / 表名 / SQL）。
    """
    payload: dict[str, Any] = {
        "success": False,
        "error": error,
        "request_id": request_id if request_id is not None else get_request_id(),
    }
    if extra:
        payload.update(extra)
    return payload


def json_ok(data: Any = None, *, status_code: int = status.HTTP_200_OK) -> JSONResponse:
    """直接返回 ``JSONResponse``（需要自定义状态码时用）。"""
    return JSONResponse(status_code=status_code, content=ok(data))


def json_fail(error: str, *, status_code: int = status.HTTP_400_BAD_REQUEST,
              extra: dict[str, Any] | None = None) -> JSONResponse:
    """直接返回失败 ``JSONResponse``。"""
    return JSONResponse(status_code=status_code, content=fail(error, extra=extra))


def bad_request(error: str) -> JSONResponse:
    """400 快捷方式（对应旧版的 ``jsonify(...), 400``）。"""
    return json_fail(error, status_code=status.HTTP_400_BAD_REQUEST)


def not_found(error: str) -> JSONResponse:
    """404 快捷方式（对应旧版的 ``jsonify(...), 404``）。"""
    return json_fail(error, status_code=status.HTTP_404_NOT_FOUND)
