"""报告相关路由（``/api/reports``）。

对应旧版 ``web_app.get_reports`` / ``get_report``。

与旧版的差异（都是**向后兼容的增强**，前端无需改动）：

1. 报告列表多返回 ``errorsCount`` 与 ``hasIssues``。旧版只有
   ``filename`` / ``timestamp`` / ``total`` / ``alertsCount``，运维在列表页
   看不出「这份报告是真的没异常，还是当时检查根本不完整」——那两个字段
   正是为这件事加的。
2. 详情页的 ``alerts`` 由服务端从 ``results`` 派生（旧版是读文件时派生）。
   存储里不再重复保留一份 alerts 副本。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.deps import SessionDep
from app.core.exceptions import BadRequest, NotFound
from app.core.response import ok
from app.repositories.report_repo import ReportRepository

__all__ = ["router"]

router = APIRouter(prefix="/reports", tags=["reports"])


def _validate_filename(filename: str) -> str:
    """拒绝任何带路径成分的文件名。

    旧版 ``get_report`` 直接把 URL 段拼进 ``os.path.join(REPORTS_DIR, filename)``
    且没有校验（``web_app.py:629``）——``filename=../../config.json`` 之类
    构造可以读到数据目录之外的文件。迁移到数据库后本来已无文件拼接，但保
    留这层校验，让「非法文件名」返回 400 而不是静默查库得 404。
    """
    if "/" in filename or "\\" in filename or ".." in filename:
        raise BadRequest("非法的报告文件名")
    if not filename.endswith(".json"):
        raise BadRequest("报告文件名必须以 .json 结尾")
    return filename


@router.get("", summary="获取报告列表")
async def list_reports(
    session: SessionDep,
    limit: int = Query(100, ge=1, le=500, description="返回条数上限"),
) -> dict:
    """旧版 ``GET /api/reports``（按时间倒序，最新在前）。

    ``data`` 是**数组本身**，与旧 Flask 版一致（``web_app.py:568-604``
    返回的 ``data`` 就是列表），前端 ``app.js`` 直接 ``data.data`` 遍历。
    """
    rows = await ReportRepository(session).list_recent(limit=limit)
    return ok([
        {
            "filename": r.filename,
            "timestamp": r.timestamp,
            "total": r.total,
            "alertsCount": r.alerts_count,
            "errorsCount": r.errors_count,
            "hasIssues": bool(r.has_issues),
        }
        for r in rows
    ])


@router.get("/{filename}", summary="获取报告详情")
async def get_report(filename: str, session: SessionDep) -> dict:
    """旧版 ``GET /api/reports/<filename>``。

    返回体与旧版报告文件结构一致，**含派生出来的 ``alerts``**，
    所以前端 ``app.js:751`` 的 ``data.alerts || []`` 照旧可用。
    """
    filename = _validate_filename(filename)
    row = await ReportRepository(session).get_by_filename(filename)
    if row is None:
        raise NotFound("报告不存在")
    return ok(row.to_payload())
