"""商标清单的数据访问层（repository）。

职责边界
--------
只做「与数据库打交道」：查询、写入、事务。**不写业务规则**（重名判断属于
服务层），也不碰 HTTP。这样服务层的单测可以直接注入一个内存 session，
不需要起 Web 服务。

为什么重复品牌名的冲突判断放在服务层而不是这里？
    「重名」是业务定义（这里定义成**不区分大小写的同名**），而且要给用户
    返回 409 + 具体文案。repository 只负责「按名查有没有」这个事实。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import Trademark

__all__ = ["TrademarkRepository"]


class TrademarkRepository:
    """``trademarks`` 表的读写。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self) -> Sequence[Trademark]:
        """按创建时间返回全部商标（新的在后，与旧版 JSON append 顺序一致）。"""
        result = await self.session.execute(select(Trademark).order_by(Trademark.created_at))
        return result.scalars().all()

    async def count(self) -> int:
        """总条数。"""
        result = await self.session.execute(select(func.count()).select_from(Trademark))
        return int(result.scalar() or 0)

    async def get(self, trademark_id: str) -> Trademark | None:
        """按主键取一条，不存在返回 ``None``。"""
        return await self.session.get(Trademark, trademark_id)

    async def get_many(self, ids: Sequence[str]) -> Sequence[Trademark]:
        """按主键批量取（用于「检查选中的几条」）。"""
        if not ids:
            return []
        result = await self.session.execute(
            select(Trademark).where(Trademark.id.in_(list(ids)))
        )
        return result.scalars().all()

    async def find_by_brand_name(self, brand_name: str) -> Trademark | None:
        """按品牌名查找（**不区分大小写**）。

        用 ``func.lower`` 而不是 Python 侧比较：清单可能有几千条，
        在 SQL 里比较才是 O(1) 索引查询，而不是把全表拉进内存。
        """
        result = await self.session.execute(
            select(Trademark).where(func.lower(Trademark.brand_name) == brand_name.strip().lower())
        )
        return result.scalars().first()

    async def existing_brand_names_lower(self) -> set[str]:
        """全部品牌名的小写集合（导入时批量查重，一次查询代替 N 次）。"""
        result = await self.session.execute(select(func.lower(Trademark.brand_name)))
        return {row for row in result.scalars().all() if row}

    async def add(self, trademark: Trademark) -> Trademark:
        """插入一条并 flush（拿到数据库默认值，但仍等调用方 commit）。"""
        self.session.add(trademark)
        await self.session.flush()
        return trademark

    async def add_many(self, trademarks: Sequence[Trademark]) -> int:
        """批量插入，返回条数。"""
        if not trademarks:
            return 0
        self.session.add_all(list(trademarks))
        await self.session.flush()
        return len(trademarks)

    async def delete(self, trademark: Trademark) -> None:
        """删除单条。"""
        await self.session.delete(trademark)

    async def delete_many(self, ids: Sequence[str]) -> int:
        """按 id 批量删除，返回**实际删除**的条数。

        注意返回的是「删掉了几条」而不是「传了几个 id」——旧版正是用
        ``original_count - len(after)`` 算实际删除数（``web_app.py:400``），
        前端会把这个数字原样显示给用户，多报会让人以为删了不存在的东西。
        """
        if not ids:
            return 0
        rows = await self.get_many(ids)
        for row in rows:
            await self.session.delete(row)
        await self.session.flush()
        return len(rows)
