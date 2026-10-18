"""结构化日志。

用 ``structlog`` 输出「键值对」而不是拼字符串，这样同一条日志既能给人读，
也能被日志系统按 ``request_id`` / ``brand_name`` 直接检索。

**控制台编码兜底**：本机中文 Windows 控制台是 GBK，直接 print 含 ``⚠`` / ``✗``
的内容会抛 ``UnicodeEncodeError`` 并**吞掉真实错误信息**（业务内核
``monitor._safe_print`` 就是为此存在的）。这里在 handler 层做同样的替换，
避免 Web 层的日志成为新的崩溃源。
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.core.context import get_request_id

__all__ = ["get_logger", "configure_logging"]

_configured = False


def _inject_request_id(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """把当前请求 id 注入每条日志，省得每个调用点手工传。"""
    request_id = get_request_id()
    if request_id:
        event_dict.setdefault("request_id", request_id)
    return event_dict


def _safe_console_handler() -> logging.Handler:
    """GBK 控制台安全的 handler。

    直接包一层 StreamHandler 并重写 ``emit``：编码失败时用 replace 降级，
    而不是让日志本身抛异常。
    """

    class _SafeStreamHandler(logging.StreamHandler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                super().emit(record)
            except UnicodeEncodeError:
                try:
                    msg = self.format(record)
                    encoding = getattr(self.stream, "encoding", None) or "ascii"
                    fallback = msg.encode(encoding, "replace").decode(encoding, "replace")
                    self.stream.write(fallback + self.terminator)
                    self.flush()
                except Exception:  # noqa: BLE001 - 日志失败绝不能影响主流程
                    self.handleError(record)

    return _SafeStreamHandler(stream=sys.stdout)


def configure_logging(debug: bool = False) -> None:
    """初始化 structlog（幂等）。"""
    global _configured
    if _configured:
        return

    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.handlers.clear()
    handler = _safe_console_handler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    root.setLevel(level)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _inject_request_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            # 彩色控制台输出；非 TTY 时 structlog 自动退化为纯文本
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str = "wipo") -> Any:
    """取一个 logger（必要时先初始化）。"""
    if not _configured:
        configure_logging()
    return structlog.get_logger(name)
