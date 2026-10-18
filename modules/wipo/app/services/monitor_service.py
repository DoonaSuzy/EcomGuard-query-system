"""商标检查的业务编排层。

这是旧版 ``web_app.check_trademarks`` 里那段内嵌 ``run_check_async()`` 的正规化
版本。原来它是一个定义在路由函数**内部**的闭包，靠捕获外层局部变量工作——
既没法单测，也没法被 CLI / 计划任务复用。现在拆成服务：

    MonitorService        —— 路由层调用它启动检查、查询状态
    MonitorService._run   —— 后台线程里真正跑检查并落库

为什么仍然是线程，而不是 FastAPI 的 ``BackgroundTasks`` / async task？
    真正耗时的是 ``MonitorEngine.run_check``：同步 ``requests`` 调 WIPO
    （每个商标 ~1.8s，还有 0.5s 限流 sleep），内部全是阻塞调用。放进事件
    循环会卡死整个服务。用线程跑阻塞代码是正确选择；数据库写入则在该线程
    自己的事件循环里完成（见 ``make_worker_session_factory`` 的说明）。

状态存储从「两个文件」换成 ``check_runs`` 单行表后，旧版那个「启动新检查
前必须记得 unlink 旧结果文件」的补丁就不再需要——状态迁移本身就是原子的。
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import AppSettings, get_settings
from app.core.database import make_worker_session_factory
from app.core.exceptions import BadRequest
from app.core.logging import get_logger
from app.repositories.report_repo import CheckRunRepository, ReportRepository
from app.repositories.trademark_repo import TrademarkRepository

__all__ = ["MonitorService"]

log = get_logger("service.monitor")


class MonitorService:
    """启动检查、记录状态、归档报告。"""

    def __init__(self, session: AsyncSession, settings: AppSettings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.trademark_repo = TrademarkRepository(session)
        self.report_repo = ReportRepository(session)
        self.run_repo = CheckRunRepository(session)

    # ---------------------------------------------------------------- 启动
    async def start_check(
        self,
        ids: list[str],
        check_status: bool = True,
        check_squatting: bool = True,
    ) -> dict[str, Any]:
        """校验参数并把检查丢进后台线程。

        旧版在「没选中任何商标」时直接报 400（``web_app.py:443``）。这里沿用，
        但语义更严一点：**传了 ids 却一条都没匹配上**同样报 400——旧版会静默
        地退化成「检查全部商标」，用户勾了 3 条却跑了整表，这在几十个商标时
        是明显的行为错误。
        """
        if ids:
            rows = await self.trademark_repo.get_many(ids)
            if not rows:
                raise BadRequest("没有可检查的商标")
            brand_records = [r.to_dict() for r in rows]
        else:
            rows = await self.trademark_repo.list_all()
            if not rows:
                raise BadRequest("没有可检查的商标")
            brand_records = [r.to_dict() for r in rows]

        started_at = datetime.now().isoformat()
        # 状态先落 running：这样前端轮询立刻拿到「正在跑」，
        # 而不是旧版那种「旧结果文件还在 → 立刻说 completed」。
        await self.run_repo.upsert(status="running", result=None, error="", started_at=started_at)
        await self.session.commit()

        thread = threading.Thread(
            target=self._run_in_thread,
            args=(brand_records, check_status, check_squatting),
            name="wipo-check",
            daemon=True,
        )
        thread.start()
        log.info("检查已在后台启动", trademarks=len(brand_records), ids=bool(ids))
        return {"message": "检查已启动，请稍后查看结果"}

    # ---------------------------------------------------------------- 查询
    async def get_status(self) -> dict[str, Any]:
        """返回当前检查状态。

        与旧版的三态（running / completed / error）保持一致；额外多一个
        ``idle`` 表示「服务起来后还没跑过检查」——旧版这种情况下会返回
        ``running``，前端于是永远转圈直到用户手动刷新。
        """
        row = await self.run_repo.get()
        if row is None:
            return {"status": "idle", "result": None, "error": ""}
        return {
            "status": row.status,
            "result": row.result,
            "error": row.error or "",
        }

    # ------------------------------------------------------------ 后台执行
    def _run_in_thread(
        self,
        brand_records: list[dict[str, Any]],
        check_status: bool,
        check_squatting: bool,
    ) -> None:
        """线程入口：自建事件循环跑异步落库逻辑。

        ``asyncio.run`` 在这个线程里创建独立的事件循环；数据库连接也用
        该循环专属的 engine（不能复用主流程的池子）。
        """
        try:
            asyncio.run(self._execute(brand_records, check_status, check_squatting))
        except Exception as exc:  # noqa: BLE001 - 后台线程不能把异常抛给任何人
            log.error("检查执行失败", error=str(exc))
            try:
                asyncio.run(self._mark_error(str(exc)))
            except Exception:  # noqa: BLE001 - 连错误状态都写不进去时只能记日志
                log.error("写入错误状态失败", exc_info=True)

    async def _execute(
        self,
        brand_records: list[dict[str, Any]],
        check_status: bool,
        check_squatting: bool,
    ) -> None:
        """真正执行检查并落库（在后台线程的独立事件循环里跑）。"""
        factory = make_worker_session_factory()
        try:
            result = await asyncio.to_thread(
                self._run_engine, brand_records, check_status, check_squatting
            )
            async with factory() as session:
                await self._persist(session, result)
        finally:
            await factory.kw["bind"].dispose()

    def _run_engine(
        self,
        brand_records: list[dict[str, Any]],
        check_status: bool,
        check_squatting: bool,
    ) -> dict[str, Any]:
        """在阻塞上下文里跑监控引擎（不碰数据库）。"""
        from monitor import MonitorEngine
        from trademark_list import TrademarkList

        tm_list = TrademarkList()
        tm_list.trademarks = brand_records
        applicant_names = list({
            tm.get("applicant", "") for tm in brand_records if tm.get("applicant")
        })

        engine = MonitorEngine()
        result = engine.run_check(tm_list, check_status, check_squatting, applicant_names)
        result["checkedAt"] = datetime.now().isoformat()
        result["alerts"] = engine.alerts
        result["results"] = engine.results
        return result

    async def _persist(self, session: AsyncSession, result: dict[str, Any]) -> None:
        """把结果写入 ``reports`` 与 ``check_runs``。"""
        from app.models.orm import Report

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"report_{timestamp}.json"

        report = Report(
            filename=filename,
            timestamp=result.get("timestamp", datetime.now().isoformat()),
            finished_at=result.get("checkedAt", datetime.now().isoformat()),
            total=int(result.get("total", 0) or 0),
            checked=int(result.get("checked", 0) or 0),
            alerts_count=len(result.get("alerts") or []),
            errors_count=int(result.get("errorsCount", 0) or 0),
            has_issues=bool(result.get("hasIssues")),
            failed_brands=list(result.get("failedBrands") or []),
            errors=list(result.get("errors") or []),
            results=list(result.get("results") or []),
        )
        await ReportRepository(session).add(report)
        await CheckRunRepository(session).upsert(
            status="completed",
            result=self._status_payload(result),
            error="",
        )
        await session.commit()
        log.info(
            "检查完成并归档",
            report=filename,
            total=report.total,
            alerts=report.alerts_count,
            errors=report.errors_count,
        )
        await self._notify(result)

    @staticmethod
    def _status_payload(result: dict[str, Any]) -> dict[str, Any]:
        """``/api/check/status`` 返回的 ``result`` 载荷。

        **刻意不含 ``results``**：旧版把整份结果（含每条抢注案例的完整字段）
        塞进 ``last_check_result.json``，前端每 2 秒轮询一次就整份拖一遍——
        报告越大越慢。前端只需要摘要与告警清单，明细去
        ``GET /api/reports/{filename}`` 取。
        """
        return {
            "total": result.get("total", 0),
            "checked": result.get("checked", 0),
            "errors": result.get("errors", []),
            "errorsCount": result.get("errorsCount", 0),
            "failedBrands": result.get("failedBrands", []),
            "alerts": result.get("alerts", []),
            "hasIssues": result.get("hasIssues", False),
            "timestamp": result.get("timestamp", ""),
            "checkedAt": result.get("checkedAt", ""),
        }

    async def _notify(self, result: dict[str, Any]) -> None:
        """按配置发送告警（发不出去不影响检查结果已归档这一事实）。"""
        alerts = result.get("alerts") or []
        channels = self.settings.notification.channels
        if not alerts or not channels:
            return
        try:
            from notification import NotificationService

            config = self.settings.notification.model_dump()
            sent = await asyncio.to_thread(NotificationService(config).send_alerts, alerts)
            log.info(
                "告警已发送",
                success=len(sent.get("success") or []),
                failed=len(sent.get("failed") or []),
            )
        except Exception as exc:  # noqa: BLE001 - 通知失败不该让检查失败
            log.warning("告警发送失败", error=str(exc))

    async def _mark_error(self, message: str) -> None:
        """把失败状态写进 ``check_runs``（后台线程的兜底路径）。"""
        factory: async_sessionmaker[AsyncSession] = make_worker_session_factory()
        try:
            async with factory() as session:
                await CheckRunRepository(session).upsert(status="error", error=message)
                await session.commit()
        finally:
            await factory.kw["bind"].dispose()
