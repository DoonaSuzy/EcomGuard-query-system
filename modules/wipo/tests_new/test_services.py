"""服务层单元测试：不需要 Web，直接钉业务规则。

这些用例是 C 方案「行为不回归」的核心证据——旧 Flask 栈的 106 项 E2E
证明的是「页面能跑通」，这里证明的是「规则还是原来那些规则」。

覆盖的规则（逐条对应旧版行为）：
- 重名判定不区分大小写（旧版 ``brandName.lower() == ...``）
- 更新时重名检查排除自己
- 导入同名跳过、不覆盖
- 导出 CSV 列序与 niceClass 逗号拼接（E2E 断言过）
- 品牌名长度边界
"""

from __future__ import annotations

import pytest

from app.core.exceptions import BadRequest, Conflict, NotFound, ValidationFailed
from app.services.trademark_service import TrademarkService


async def _mk(service: TrademarkService, name: str, **kw) -> dict:
    """借助 schema 构造一个合法 payload 再落库。"""
    from app.models.schemas import TrademarkCreate

    payload = TrademarkCreate(brandName=name, **kw)
    return await service.create(payload)


@pytest.mark.asyncio
async def test_duplicate_is_case_insensitive(db_session):
    """重名不区分大小写：旧版 ``web_app.py:206`` 就是 lower() 比较。"""
    async with db_session() as s:
        svc = TrademarkService(s)
        await _mk(svc, "CaseTest_A")

        with pytest.raises(Conflict) as ei:
            await _mk(svc, "casetest_a")
        assert "已存在" in str(ei.value)


@pytest.mark.asyncio
async def test_update_same_name_is_not_conflict(db_session):
    """把品牌名从 A 改成 A 不该被判冲突。"""
    async with db_session() as s:
        svc = TrademarkService(s)
        row = await _mk(svc, "SameName")

        from app.models.schemas import TrademarkUpdate

        upd = await svc.update(row["id"], TrademarkUpdate(brandName="SameName"))
        assert upd["brandName"] == "SameName"


@pytest.mark.asyncio
async def test_update_to_other_name_conflicts(db_session):
    async with db_session() as s:
        svc = TrademarkService(s)
        a = await _mk(svc, "NameA")
        await _mk(svc, "NameB")

        from app.models.schemas import TrademarkUpdate

        with pytest.raises(Conflict):
            await svc.update(a["id"], TrademarkUpdate(brandName="NameB"))


@pytest.mark.asyncio
async def test_get_missing_raises_not_found(db_session):
    async with db_session() as s:
        svc = TrademarkService(s)
        with pytest.raises(NotFound):
            await svc.get_or_404("nope")


@pytest.mark.asyncio
async def test_delete_many_empty_raises_bad_request(db_session):
    """空 ids 必须报错，而不是清空整张表。"""
    async with db_session() as s:
        svc = TrademarkService(s)
        with pytest.raises(BadRequest):
            await svc.delete_many([])


@pytest.mark.asyncio
async def test_delete_many_returns_actual_count(db_session):
    """返回实际删除条数，不是传了几个 id（旧版用 original_count - len(after)）。"""
    async with db_session() as s:
        svc = TrademarkService(s)
        a = await _mk(svc, "DelMany_1")
        b = await _mk(svc, "DelMany_2")
        deleted = await svc.delete_many([a["id"], b["id"], "ghost-id"])
        assert deleted == 2


@pytest.mark.asyncio
async def test_import_skips_duplicates_and_self_dupes(db_session):
    """导入：同名跳过；文件内部自身重复也去重。"""
    payload = (
        '{"trademarks":['
        '{"brandName":"ImpDup","applicant":"A"},'
        '{"brandName":"impdup"},'
        '{"brandName":"ImpNew"}'
        ']}'
    )
    async with db_session() as s:
        svc = TrademarkService(s)
        await _mk(svc, "ImpDup")
        imported, _rows = await svc.import_records(payload, "json")
        assert imported == 1


@pytest.mark.asyncio
async def test_import_unknown_format_raises(db_session):
    async with db_session() as s:
        svc = TrademarkService(s)
        with pytest.raises(BadRequest):
            await svc.import_records("whatever", "xml")


@pytest.mark.asyncio
async def test_export_csv_column_order(db_session):
    """CSV 列序是契约（E2E tests/test_import_export.py:181 断言过）。"""
    async with db_session() as s:
        svc = TrademarkService(s)
        await _mk(svc, "CsvCol", applicant="Acme", registrationNumber="R1", niceClass=["9", "35"])
        text = await svc.export_csv()
        assert text.splitlines()[0] == "brandName,applicant,registrationNumber,niceClass,notes"
        # niceClass 以逗号拼接
        assert '"9,35"' in text


@pytest.mark.asyncio
async def test_export_json_shape(db_session):
    async with db_session() as s:
        svc = TrademarkService(s)
        await _mk(svc, "JsonShape")
        text = await svc.export_json()
        import json

        data = json.loads(text)
        assert isinstance(data, dict) and "trademarks" in data
        assert data["trademarks"][0]["brandName"] == "JsonShape"


@pytest.mark.asyncio
async def test_brand_name_length_boundaries(db_session):
    """长度边界：Pydantic 拦长度，服务层兜底。

    注意这里的**分层变化**：旧 Flask 版把长度校验写在路由里（返回
    ``商标名称长度不能超过200个字符``），新版挪到 Pydantic schema。
    测试必须按新分层写，否则测的是一条不存在的代码路径。
    """
    from pydantic import ValidationError as PydValidationError

    from app.models.schemas import TrademarkCreate

    # 201 字符 / 1 字符：Pydantic 层直接拦（max_length=200 / min_length=2）
    with pytest.raises(PydValidationError):
        TrademarkCreate(brandName="X" * 201)
    with pytest.raises(PydValidationError):
        TrademarkCreate(brandName="A")

    # 服务层那道 _assert_brand_name_len 是第二道保险（绕过 schema 直达），
    # 文案必须与旧版逐字一致，前端 #brandName-error 与 E2E 都断言过它
    async with db_session() as s:
        svc = TrademarkService(s)
        with pytest.raises(ValidationFailed) as ei:
            svc._assert_brand_name_len("")
        assert str(ei.value) == "商标名称长度至少2个字符"


@pytest.mark.asyncio
async def test_list_all_orders_by_created_at(db_session):
    async with db_session() as s:
        svc = TrademarkService(s)
        await _mk(svc, "Order_1")
        await _mk(svc, "Order_2")
        rows = await svc.list_all()
        assert len(rows) >= 2
        names = [r["brandName"] for r in rows]
        assert names.index("Order_1") < names.index("Order_2")
