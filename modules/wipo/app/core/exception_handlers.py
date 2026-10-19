"""全局异常处理器（三层异常处理的第二、三层）。

注册顺序即匹配优先级——FastAPI 按「最具体的异常类」优先匹配：

1. ``AppError``              → 业务异常，带稳定业务码
2. ``HTTPException``         → 路由里手动抛的 HTTP 语义错误
3. ``RequestValidationError``→ Pydantic 参数校验失败（422）
4. ``Exception``             → 兜住一切未预期异常，**不回显内部细节**

第 4 条是安全边界：生产环境把堆栈 / 表名 / SQL 回给客户端等于免费送攻击面。
只回「服务器内部错误」+ ``request_id``，细节写日志（带完整堆栈），
排障靠 request_id 对齐。

**契约一致性**：所有错误响应都走 ``fail()``，因此永远是
``{"success": false, "error": "...", ...}`` —— 与旧 Flask 版手工
``jsonify({'success': False, 'error': ...})`` 的形状一致。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import REQUEST_ID_HEADER, get_request_id
from app.core.exceptions import AppError, code_name
from app.core.logging import get_logger
from app.core.response import fail

__all__ = ["register_exception_handlers"]

log = get_logger("errors")


def _with_request_header(payload: JSONResponse, request: Request) -> JSONResponse:
    """把请求 id 同时写进响应头，便于前端与网关对齐日志。

    取值优先级：``request.state.request_id``（中间件在进入内层前写进
    ``scope["state"]``）> contextvars。contextvars 在未处理异常冒到
    ServerErrorMiddleware 之前已被中间件 finally reset，因此 500 兜底
    只能从 ``request.state`` 取回——两者必须一致。
    """
    request_id = getattr(request.state, "request_id", "") or get_request_id()
    if request_id:
        payload.headers[REQUEST_ID_HEADER] = request_id
    return payload


def _request_id_for_body(request: Request) -> str:
    """给 ``fail()`` 用的 request_id：优先 ``request.state``，回退 contextvars。

    与 ``_with_request_header`` 同源取值，保证 500 响应体与响应头一致。
    """
    return getattr(request.state, "request_id", "") or get_request_id()


def register_exception_handlers(app: FastAPI) -> None:
    """把四层处理器挂到应用上。"""

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        """业务异常：文案直接给调用方，因为它是刻意写给人看的。"""
        if exc.status_code >= 500:
            log.error(
                "业务异常",
                code=exc.code,
                name=code_name(exc.code),
                path=request.url.path,
                message=exc.message,
            )
        else:
            log.info(
                "业务异常",
                code=exc.code,
                name=code_name(exc.code),
                path=request.url.path,
                message=exc.message,
            )
        body = fail(exc.message, extra=exc.to_payload())
        return _with_request_header(JSONResponse(status_code=exc.status_code, content=body), request)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """HTTP 语义错误（404 路由不存在、405 方法不对等）。"""
        detail = exc.detail if isinstance(exc.detail, str) else "请求失败"
        log.info("HTTP 异常", status=exc.status_code, path=request.url.path, detail=detail)
        body = fail(detail)
        return _with_request_header(JSONResponse(status_code=exc.status_code, content=body), request)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        """参数校验失败：把 Pydantic 的错误压成一句中文，不让前端解析嵌套结构。

        旧 Flask 版是逐个 ``if`` 手写校验并返回**一句**中文（例如
        「商标名称不能为空」）。这里保持同样的对外形态——``error`` 是字符串
        而不是数组——否则前端 ``result.error`` 会渲染成 ``[object Object]``。

        ``extra['errors']`` 是给日志 / 排障看的明细，**必须可 JSON 序列化**：
        Pydantic 的 ``exc.errors()`` 里 ``ctx`` 会带真正的 ``ValueError``
        实例（例如自定义校验器抛出的），直接塞进 ``JSONResponse`` 会
        ``TypeError: Object of type ValueError is not JSON serializable``
        —— 于是「参数不合法」这个本该 422 的请求变成 500。实测踩过。
        """
        message = _first_validation_message(exc)
        log.info("参数校验失败", path=request.url.path, errors=len(exc.errors()))
        body = fail(message, extra={"code": 1001, "errors": _jsonable(exc.errors())})
        return _with_request_header(
            JSONResponse(status_code=422, content=body), request
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        """兜底：不回显内部细节，细节只进日志。

        ``fail(request_id=...)`` 显式传 id：此刻 contextvars 已被中间件
        finally reset，不传的话响应体是空串，与响应头对不上。
        """
        log.exception("未处理异常", path=request.url.path, exc_type=type(exc).__name__)
        body = fail(
            "服务器内部错误，请稍后重试",
            request_id=_request_id_for_body(request),
            extra={"code": 3000},
        )
        return _with_request_header(JSONResponse(status_code=500, content=body), request)


def _jsonable(value: Any) -> Any:
    """把任意对象转成可 JSON 序列化的形态。

    ``exc.errors()`` 的 ``ctx`` 里可能是 ``ValueError`` 实例（自定义
    ``field_validator`` 抛出的就是它），必须降级成字符串。
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)


#: Pydantic 的英文错误类型 → 中文前缀，覆盖本项目实际会出现的几种
_ERROR_PREFIX: dict[str, str] = {
    "missing": "缺少必填字段",
    "string_too_short": "内容过短",
    "string_too_long": "内容过长",
    "int_parsing": "需要整数",
    "float_parsing": "需要数字",
    "list_type": "需要数组",
    "string_type": "需要字符串",
    "bool_parsing": "需要布尔值",
}

#: 字段名 → 中文名，让「内容过长: brandName」这种报错能说人话
_FIELD_LABELS: dict[str, str] = {
    "brandName": "商标名称",
    "applicant": "申请人",
    "registrationNumber": "注册号",
    "niceClass": "尼斯分类",
    "notes": "备注",
    "ids": "id",
    "id": "id",
}

#: 长度类错误的完整文案，逐条对齐旧 Flask 版的手写校验。
#: 为什么必须逐字对齐：``tests/test_form_validation.py`` 断言的正是这三句
#: 原文（web_app.py:226 / :230 / :234），改成别的说法 E2E 就会失败。
_LENGTH_MESSAGES: dict[tuple[str, str], str] = {
    ("brandName", "string_too_short"): "商标名称长度至少2个字符",
    ("brandName", "string_too_long"): "商标名称长度不能超过200个字符",
}


def _first_validation_message(exc: RequestValidationError) -> str:
    """把第一条校验错误翻成一句中文。

    取值顺序（越靠前越优先）：

    1. **自定义校验器抛的 ``ValueError``** 直接采用它的消息。
       Pydantic 会把它原样放进 ``ctx['error']``，所以「商标名称不能为空」
       这句在模型里写一次、这里就能原样带出去，不需要再翻译一遍。
    2. **长度类错误**查 ``_LENGTH_MESSAGES`` 拿到旧版原文。
    3. 兜底 ``前缀: 字段中文名``。
    """
    errors = exc.errors()
    if not errors:
        return "请求参数不合法"
    first = errors[0]
    err_type = str(first.get("type", ""))
    loc = [str(p) for p in first.get("loc", []) if p != "body"]
    field = loc[-1] if loc else ""

    # 1. 自定义校验器的消息（model_validator / field_validator 抛的 ValueError）
    #    ``ctx['error']`` 里装的是真正的 ValueError 实例（探针实测：
    #    ``ctx={'error': ValueError('商标名称不能为空')}``），不是字符串，
    #    因此 ``isinstance(custom, str)`` 永远为假。从实例里取消息即可，
    #    这样模型里写一次的文案能原样带到响应体，不必在这里再翻译一遍。
    ctx = first.get("ctx") or {}
    custom = ctx.get("error") if isinstance(ctx, dict) else None
    if custom is not None:
        message = getattr(custom, "message", None) or str(custom)
        if message:
            return message

    # 2. 长度类错误对齐旧版原文
    legacy = _LENGTH_MESSAGES.get((field, err_type))
    if legacy:
        return legacy

    # 3. 兜底
    label = _FIELD_LABELS.get(field, field) or "参数"
    prefix = _ERROR_PREFIX.get(err_type, "参数不合法")
    return f"{prefix}: {label}"
