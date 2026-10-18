"""商标清单的业务逻辑层。

服务层只关心「规则」：校验、去重、导入合并策略。数据存取交给 repository，
HTTP 形态交给路由层。这样 ``tests/`` 里可以脱离 Web 直接测这些规则。
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequest, Conflict, NotFound, ValidationFailed
from app.core.logging import get_logger
from app.models.orm import Trademark
from app.models.schemas import TrademarkCreate, TrademarkUpdate
from app.repositories.trademark_repo import TrademarkRepository

__all__ = ["TrademarkService", "BRAND_NAME_MAX_LEN", "BRAND_NAME_MIN_LEN"]

log = get_logger("service.trademark")

#: 与旧版 web_app.py 的手写校验保持一致的边界。
#: 这三个数字是**契约**：前端提示文案与 E2E 用例都断言过具体数值。
BRAND_NAME_MIN_LEN = 2
BRAND_NAME_MAX_LEN = 200


class TrademarkService:
    """商标清单的增删改查与导入导出。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = TrademarkRepository(session)

    # ------------------------------------------------------------------ 读
    async def list_all(self) -> list[dict[str, Any]]:
        """返回全部商标（旧版 ``GET /api/trademarks`` 的 ``data``）。"""
        rows = await self.repo.list_all()
        return [r.to_dict() for r in rows]

    async def count(self) -> int:
        """总数。"""
        return await self.repo.count()

    async def get_or_404(self, trademark_id: str) -> Trademark:
        """取一条，不存在抛 ``NotFound``（由异常处理器转 404）。"""
        row = await self.repo.get(trademark_id)
        if row is None:
            raise NotFound(f"商标 {trademark_id} 不存在")
        return row

    # ------------------------------------------------------------------ 写
    async def create(self, payload: TrademarkCreate) -> dict[str, Any]:
        """新增一条商标。

        规则（与旧版 ``web_app.add_trademark`` 一致）：
        - 品牌名去除首尾空白；不能为空、不超过 200、不少于 2 —— 前两条由
          Pydantic 模型负责（空值会给「不能为空」这句文案），这里只补
          服务端才能判定的重名；
        - 重名判定 **不区分大小写**（旧版 ``tm['brandName'].lower() == ...``）；
        - 服务端生成 UUID 主键与 ``createdAt``。
        """
        brand_name = payload.brand_name.strip()
        self._assert_brand_name_len(brand_name)

        if await self.repo.find_by_brand_name(brand_name) is not None:
            raise Conflict(f'商标 "{brand_name}" 已存在')

        row = Trademark(
            id=str(uuid.uuid4()),
            brand_name=brand_name,
            applicant=payload.applicant.strip(),
            registration_number=payload.registration_number.strip(),
            nice_class=list(payload.nice_class or []),
            notes=payload.notes.strip(),
            status="pending",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        await self.repo.add(row)
        await self.session.commit()
        log.info("新增商标", trademark_id=row.id, brand_name=row.brand_name)
        return row.to_dict()

    async def update(self, trademark_id: str, payload: TrademarkUpdate) -> dict[str, Any]:
        """更新一条商标（只改传了的字段）。

        重名检查会**排除自己**：把品牌名从 A 改成 A 不该被判冲突。
        """
        row = await self.get_or_404(trademark_id)
        changes = payload.model_dump(exclude_unset=True, by_alias=False)

        if "brand_name" in changes and changes["brand_name"] is not None:
            brand_name = str(changes["brand_name"]).strip()
            self._assert_brand_name_len(brand_name)
            clash = await self.repo.find_by_brand_name(brand_name)
            if clash is not None and clash.id != row.id:
                raise Conflict(f'商标 "{brand_name}" 已存在')
            row.brand_name = brand_name

        if changes.get("applicant") is not None:
            row.applicant = str(changes["applicant"]).strip()
        if changes.get("registration_number") is not None:
            row.registration_number = str(changes["registration_number"]).strip()
        if changes.get("nice_class") is not None:
            row.nice_class = list(changes["nice_class"] or [])
        if changes.get("notes") is not None:
            row.notes = str(changes["notes"]).strip()

        row.updated_at = datetime.now()
        await self.session.commit()
        log.info("更新商标", trademark_id=row.id)
        return row.to_dict()

    async def delete(self, trademark_id: str) -> int:
        """删除一条，返回删除条数（0/1）。"""
        row = await self.get_or_404(trademark_id)
        await self.repo.delete(row)
        await self.session.commit()
        log.info("删除商标", trademark_id=trademark_id)
        return 1

    async def delete_many(self, ids: list[str]) -> int:
        """批量删除，返回**实际删除**的条数。

        旧版语义：不存在的 id 只是被忽略，不报错，且返回数只算真删掉的
        （``web_app.py:400`` 用过滤前后长度差计算）。传空列表返回 0——
        这一点与旧版不同：旧版空 ids 会走「保留不在 ids 中的」分支从而清空
        整个清单。**清空清单必须显式调用**，不能让一个空数组就删库。
        """
        if not ids:
            raise BadRequest("未提供要删除的商标 id")
        deleted = await self.repo.delete_many(ids)
        await self.session.commit()
        log.info("批量删除商标", requested=len(ids), deleted=deleted)
        return deleted

    # ------------------------------------------------------------- 导入导出
    async def export_json(self) -> str:
        """导出为 JSON 文本（旧版 ``GET /api/export?format=json``）。"""
        items = await self.list_all()
        return json.dumps({"trademarks": items}, ensure_ascii=False, indent=2)

    async def export_csv(self) -> str:
        """导出为 CSV 文本。

        列顺序与 ``niceClass`` 的分隔符都与旧版一致
        （``web_app.py:679-686``）：逗号连接。逗号本身是 CSV 分隔符，但
        ``csv.writer`` 会自动给含逗号的字段加引号，读回时 ``csv.DictReader``
        还原成 ``"9,35"`` 再按逗号切分，往返一致。
        换成别的分隔符会打断 ``tests/test_import_export.py:184``
        （断言 CSV 正文含 ``9,35``）。
        """
        items = await self.list_all()
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["brandName", "applicant", "registrationNumber", "niceClass", "notes"])
        for item in items:
            writer.writerow([
                item.get("brandName", ""),
                item.get("applicant", ""),
                item.get("registrationNumber", ""),
                ",".join(str(c) for c in (item.get("niceClass") or [])),
                item.get("notes", ""),
            ])
        return buffer.getvalue()

    async def import_records(
        self, text: str, fmt: str
    ) -> tuple[int, list[dict[str, Any]]]:
        """导入商标，返回 ``(导入条数, 新记录列表)``。

        合并策略沿用旧版：
        - **同名（不区分大小写）跳过**，不报错也不覆盖既有记录；
        - 新记录由服务端补 UUID / ``createdAt`` / ``status=pending``；
        - 文件内部自身重复也去重（旧版靠 ``existing_ids`` 边导入边加）。

        **空内容返回 0 条而不是报错**：旧版对 ``[]`` 与 0 字节文件都返回
        「导入 0 条」，E2E 断言提示里含 '0'（``test_import_empty_file`` /
        ``test_import_zero_byte_file``）。这里不区分「解析出 0 条」与
        「解析失败」——后者已在 ``_parse_import`` 里抛 400 了。
        """
        records = self._parse_import(text, fmt)
        if not records:
            log.info("导入商标：内容为空", fmt=fmt)
            return 0, []

        existing = await self.repo.existing_brand_names_lower()
        imported: list[Trademark] = []
        skipped = 0

        for record in records:
            brand_name = str(record.get("brandName") or "").strip()
            if not brand_name:
                skipped += 1
                continue
            key = brand_name.lower()
            if key in existing:
                skipped += 1
                continue

            existing.add(key)
            imported.append(
                Trademark(
                    id=str(uuid.uuid4()),
                    brand_name=brand_name,
                    applicant=str(record.get("applicant") or "").strip(),
                    registration_number=str(record.get("registrationNumber") or "").strip(),
                    nice_class=self._coerce_nice_class(record.get("niceClass")),
                    notes=str(record.get("notes") or "").strip(),
                    status="pending",
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
            )

        await self.repo.add_many(imported)
        await self.session.commit()
        log.info("导入商标", imported=len(imported), skipped=skipped, fmt=fmt)
        return len(imported), [r.to_dict() for r in imported]

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _assert_brand_name_len(brand_name: str) -> None:
        """长度上下限校验。

        文案与旧版逐字一致（``web_app.py:230/234``），因为前端会把
        ``error`` 字段直接显示给用户，E2E 用例断言过这些字符串。
        """
        if len(brand_name) > BRAND_NAME_MAX_LEN:
            raise ValidationFailed("商标名称长度不能超过200个字符")
        if len(brand_name) < BRAND_NAME_MIN_LEN:
            raise ValidationFailed("商标名称长度至少2个字符")

    @staticmethod
    def _coerce_nice_class(value: Any) -> list[Any]:
        """把 ``niceClass`` 归一成 list（CSV 里是分号分隔的字符串）。"""
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return value
        return [p.strip() for p in str(value).replace(";", ",").split(",") if p.strip()]

    def _parse_import(self, text: str, fmt: str) -> list[dict[str, Any]]:
        """解析导入文本成记录列表。

        ``fmt`` 支持 ``json`` 与 ``csv``；未知格式抛 400 而不是静默返回空
        （旧版会返回「导入 0 条」，让用户以为文件没问题）。
        """
        fmt = (fmt or "").lower().strip()
        if fmt == "json":
            try:
                data = json.loads(text)
            except ValueError as exc:
                raise BadRequest(f"JSON 解析失败: {exc}") from exc
            if isinstance(data, dict):
                data = data.get("trademarks", [])
            if not isinstance(data, list):
                raise BadRequest("JSON 结构应为数组，或含 trademarks 数组的对象")
            return [d for d in data if isinstance(d, dict)]

        if fmt == "csv":
            reader = csv.DictReader(io.StringIO(text))
            return [dict(row) for row in reader]

        raise BadRequest(f"不支持的文件格式: {fmt or '(未指定)'}")
