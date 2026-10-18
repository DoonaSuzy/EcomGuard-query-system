"""检查相关路由（``/api/check``）。

对应旧版 ``web_app.check_trademarks`` / ``get_check_status``。

状态语义比旧版多一个 ``idle``：旧版用「结果文件存不存在」判断状态，服务刚
起来时两个文件都不存在 → 返回 ``running``，前端于是永远转圈。现在状态存在
``check_runs`` 表里，没跑过就是 ``idle``，前端可以据此显示「尚未检查」。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import MonitorServiceDep
from app.core.response import ok
from app.models.schemas import CheckRequest

__all__ = ["router"]

router = APIRouter(prefix="/check", tags=["check"])


@router.post("", summary="启动商标检查", description="在后台执行状态检查与抢注检测")
async def start_check(payload: CheckRequest, service: MonitorServiceDep) -> dict:
    """旧版 ``POST /api/check``。

    请求体字段沿用旧名：``trademarkIds`` / ``checkStatus`` /
    ``checkSquatting``（前端 ``app.js:488`` 就是这么发的）。
    """
    started = await service.start_check(
        ids=payload.ids,
        check_status=payload.check_status,
        check_squatting=payload.check_squatting,
    )
    return {**ok(None), **started}


@router.get("/status", summary="查询检查状态")
async def get_check_status(service: MonitorServiceDep) -> dict:
    """旧版 ``GET /api/check/status``。

    **注意：这个接口没有 ``success`` 外壳**（旧版就是如此，前端
    ``app.js:517-518`` 直接读 ``data.status`` / ``data.result``）。
    为兼容既有调用方，这里同样返回裸对象——不为了形式统一去破坏契约。
    """
    return await service.get_status()
