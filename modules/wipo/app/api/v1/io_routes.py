"""导入导出路由（``/api/export`` / ``/api/import``）。

对应旧版 ``web_app.export_trademarks`` / ``import_trademarks``。

**响应形态刻意保持旧样式**，不套 ``{"success": true, "data": ...}`` 外壳：

- ``GET /api/export?format=json`` 旧版直接返回 ``{"trademarks": [...]}``；
- ``GET /api/export?format=csv`` 返回带 ``Content-Disposition`` 的文件下载；
- ``POST /api/import`` 返回 ``{"success": true, "importedCount": N, "data": [...]}``。

理由：前端 ``app.js:291`` 用 ``window.location.href = '/api/export?format=...'``
直接触发浏览器下载，后端加外壳会让下载到的文件变成一层 JSON 包；
而 ``tests/test_import_export.py:161`` 断言 ``data['trademarks']`` 存在、
:181 断言 CSV 首行是 ``brandName,applicant,registrationNumber,niceClass,notes``。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import JSONResponse, Response

from app.core.deps import TrademarkServiceDep
from app.core.exceptions import BadRequest
from app.core.response import ok

__all__ = ["router"]

router = APIRouter(tags=["io"])


@router.get("/export", summary="导出商标数据")
async def export_trademarks(
    service: TrademarkServiceDep,
    format: str = Query("json", description="导出格式：json 或 csv"),  # noqa: A002 - 沿用旧参数名
) -> Response:
    """旧版 ``GET /api/export``（浏览器直接下载）。"""
    fmt = (format or "json").lower()
    if fmt == "json":
        return JSONResponse(content=json.loads(await service.export_json()))
    if fmt == "csv":
        text = await service.export_csv()
        # 加 BOM 让 Excel 正确识别 UTF-8 中文，否则中文列名与商标名会乱码。
        # 注意本文件读回时用 utf-8-sig 解码，BOM 不会混进第一个字段名。
        return Response(
            content="\ufeff" + text,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="trademarks.csv"'},
        )
    raise BadRequest("不支持的格式")


@router.post("/import", summary="导入商标数据")
async def import_trademarks(
    service: TrademarkServiceDep,
    file: UploadFile | None = File(None),  # noqa: B008 - FastAPI 依赖注入的固定写法
) -> dict:
    """旧版 ``POST /api/import``（multipart 上传 JSON/CSV 文件）。"""
    if file is None:
        raise BadRequest("请上传文件")

    raw = await file.read()
    text = raw.decode("utf-8-sig") if raw else ""  # utf-8-sig 兼容带 BOM 的导出文件
    if not text.strip():
        # 旧版空文件会返回「导入 0 条」；E2E 用例断言提示里含 '0'
        # （tests/test_import_export.py:105 / :125），因此这里也按「0 条」处理。
        # 若不显式判空，0 字节文件会掉进 import_records 的解析失败分支，
        # 把「空文件」误报成「内容为空或格式不正确」——那是真实回归过的坑。
        return {**ok(None), "importedCount": 0, "data": []}

    try:
        decoded = text
    except UnicodeDecodeError as exc:  # pragma: no cover - decode 已在上面失败
        raise BadRequest(f"文件编码不是 UTF-8: {exc}") from exc

    name = (file.filename or "").lower()
    if name.endswith(".json"):
        fmt = "json"
    elif name.endswith(".csv"):
        fmt = "csv"
    else:
        raise BadRequest("仅支持 JSON 和 CSV 格式")

    imported, records = await service.import_records(decoded, fmt)
    return {**ok(None), "importedCount": imported, "data": records}
