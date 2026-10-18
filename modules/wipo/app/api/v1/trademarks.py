"""商标清单相关路由（``/api/trademarks``）。

对应旧版 ``web_app.py`` 的 ``get_trademarks`` / ``add_trademark`` /
``update_trademark`` / ``delete_trademark`` / ``delete_trademarks_batch``。

响应外壳保持 ``{"success": true, "data": ...}`` 不变（见 ``app.core.response``
的说明）——前端与 106 项 E2E 用例都依赖它。

**路由顺序**：``DELETE /trademarks/batch`` 与 ``DELETE /trademarks/{id}``
都是单段路径，FastAPI 按声明顺序匹配。若把 ``{id}`` 声明在前，批量删除会被
当成「删除 id 为 batch 的商标」→ 404。因此 ``/batch`` 必须声明在前。
旧版 Flask 靠 ``/batch`` 写在 ``/<id>`` 之前来规避同一问题
（``web_app.py:309`` 在 ``:271`` 之后，靠 werkzeug 的静态段优先规则），
FastAPI 没有这套规则，只能靠声明顺序。
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.core.deps import TrademarkServiceDep
from app.core.response import ok
from app.models.schemas import BatchDeleteRequest, TrademarkCreate, TrademarkUpdate

__all__ = ["router"]

router = APIRouter(prefix="/trademarks", tags=["trademarks"])


@router.get("", summary="获取所有商标", description="返回清单里的全部商标记录")
async def list_trademarks(service: TrademarkServiceDep) -> dict:
    """旧版 ``GET /api/trademarks``。

    返回的 ``data`` 是**数组本身**（不是 ``{"trademarks": [...]}``）——
    前端 ``app.js:131`` 直接 ``state.trademarks = data.data``。
    """
    return ok(await service.list_all())


@router.post("", status_code=status.HTTP_200_OK, summary="新增商标")
async def create_trademark(payload: TrademarkCreate, service: TrademarkServiceDep) -> dict:
    """旧版 ``POST /api/trademarks``。

    成功仍返回 **200** 而非 201：前端只看 ``result.success``，改状态码不影响
    它，但 E2E 用例里有直接的 ``assert response.status == 200``。
    """
    return ok(await service.create(payload))


# ---- /batch 必须早于 /{trademark_id} 声明（见模块 docstring）----
@router.delete("/batch", summary="批量删除商标")
async def delete_trademarks_batch(
    payload: BatchDeleteRequest, service: TrademarkServiceDep
) -> dict:
    """旧版 ``DELETE /api/trademarks/batch``。

    返回体带 ``deletedCount``（前端显示「已删除 N 条」）。
    """
    deleted = await service.delete_many(payload.ids)
    return {**ok(None), "deletedCount": deleted}


@router.put("/{trademark_id}", summary="更新商标")
async def update_trademark(
    trademark_id: str, payload: TrademarkUpdate, service: TrademarkServiceDep
) -> dict:
    """旧版 ``PUT /api/trademarks/<id>``。"""
    return ok(await service.update(trademark_id, payload))


@router.delete("/{trademark_id}", summary="删除商标")
async def delete_trademark(trademark_id: str, service: TrademarkServiceDep) -> dict:
    """旧版 ``DELETE /api/trademarks/<id>``。"""
    deleted = await service.delete(trademark_id)
    return {**ok(None), "deletedCount": deleted}
