"""检查报告与检查运行状态的数据访问层。

替代旧版两个文件落点：
- ``reports/report_*.json``  → ``reports`` 表
- ``data/last_check_result.json`` / ``data/last_check_error.txt`` → ``check_runs`` 表

为什么状态从「两个文件」改成「单行表」？
    旧版用「文件存在与否」表达状态：``get_check_status`` 只要看到
    ``last_check_result.json`` 存在就返回 ``completed``。这带来两个真问题：
    ① 启动新检查时若忘记删旧文件，前端会立刻看到上一次的结果（该缺陷在
       2026-10-16 用「启动前 unlink」修过，但那是补丁而非根治）；
    ② 写文件写一半被杀会留下损坏 JSON，解析直接 500。
    单行表可以用一次 UPDATE 原子地表达「现在是 running / 结果是这个」。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import CheckRun, Report

__all__ = ["ReportRepository", "CheckRunRepository", "SINGLETON_ID"]

#: ``check_runs`` 是单行表，固定主键
SINGLETON_ID = 1


class ReportRepository:
    """``reports`` 表的读写。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_recent(self, limit: int = 200, offset: int = 0) -> Sequence[Report]:
        """按时间倒序返回报告（最新在前）。

        旧版是 ``sorted(os.listdir(REPORTS_DIR), reverse=True)`` 再逐个读文件；
        文件名形如 ``report_YYYYMMDD_HHMMSS.json``，字典序恰好等于时间序，
        所以「按 filename 倒序」与「按 timestamp 倒序」两种排序等价。
        这里显式按 ``timestamp`` 倒序，不依赖命名的可排序性。
        """
        stmt = (
            select(Report)
            .order_by(Report.timestamp.desc(), Report.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_filename(self, filename: str) -> Report | None:
        """按报告文件名取一条。"""
        result = await self.session.execute(
            select(Report).where(Report.filename == filename)
        )
        return result.scalars().first()

    async def exists(self, filename: str) -> bool:
        """该文件名是否已入库。"""
        return await self.get_by_filename(filename) is not None

    async def add(self, report: Report) -> Report:
        """插入一份报告。"""
        self.session.add(report)
        await self.session.flush()
        return report


class CheckRunRepository:
    """``check_runs`` 单行表的读写。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self) -> CheckRun | None:
        """取当前状态行，不存在返回 ``None``。"""
        return await self.session.get(CheckRun, SINGLETON_ID)

    async def upsert(
        self,
        *,
        status: str,
        result: dict | None = None,
        error: str = "",
        started_at: str = "",
    ) -> CheckRun:
        """写入当前状态（不存在则创建）。

        用「先 get 再改」而不是 ``merge``：``merge`` 会触发一次额外的
        SELECT，而且在同一 session 里语义不如显式判断清晰。
        """
        row = await self.get()
        if row is None:
            row = CheckRun(id=SINGLETON_ID, status=status)
            self.session.add(row)
        row.status = status
        row.result = result
        row.error = error
        if started_at:
            row.started_at = started_at
        await self.session.flush()
        return row

    async def clear(self) -> None:
        """把状态重置成 ``idle`` 并清空载荷。

        用途与旧版「启动新检查前 unlink 两个文件」完全一致——
        但这里是原子的，且不依赖「记得删文件」。
        """
        await self.upsert(status="idle", result=None, error="")
