"""SQLAlchemy ORM 模型（数据库表定义）。

三张表，对应旧版三个 JSON/文件落点：

===================  ==========================  ============================
旧落点               新表                        说明
===================  ==========================  ============================
data/user_trademarks  ``trademarks``             商标清单（由 Web 增删改查）
reports/*.json        ``reports``                每次检查的完整报告（含 results）
data/last_check_*     ``check_runs``             最近一次检查的运行状态
===================  ==========================  ============================

为什么 ``results`` / ``alerts`` 用 JSON 列而不是拆表？
    报告的 ``results`` 是**一次检查的快照**，字段随 WIPO 返回而变、且从不按
    单条结果查询（永远是「取整份报告」）。拆表只会带来 join 成本与迁移负担，
    却换不到任何查询能力。相比之下商标清单要按 id / 品牌名检索，才值得建表。

注意 ``reports`` 表**不再单独存 alerts**（旧版把 alerts 整份复制一遍，
实测占报告体积 29%）——告警由 ``monitor.alerts_from_results()`` 从
``results`` 派生，这一约定在 v1 就已确立，迁移后保持。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

__all__ = ["Trademark", "Report", "CheckRun"]


class Trademark(Base):
    """商标清单记录。

    ``id`` 沿用旧版的 UUID 字符串主键（而不是自增 int）——前端把 id 写进了
    多处 DOM 属性与 API 路径，换主键形态会打断既有数据与测试。
    """

    __tablename__ = "trademarks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    brand_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    applicant: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    registration_number: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    nice_class: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_trademarks_brand_applicant", "brand_name", "applicant"),)

    def to_dict(self) -> dict[str, Any]:
        """转成与旧版 JSON 结构**逐字段一致**的 dict。

        键名必须是 camelCase（``brandName`` / ``registrationNumber`` /
        ``niceClass`` / ``createdAt``）：前端 ``static/js/app.js`` 与 106 项
        E2E 用例都按这些键取值（如 ``tests/test_list.py`` 读 ``tm.brandName``）。
        """
        return {
            "id": self.id,
            "brandName": self.brand_name,
            "applicant": self.applicant or "",
            "registrationNumber": self.registration_number or "",
            "niceClass": list(self.nice_class or []),
            "notes": self.notes or "",
            "status": self.status or "pending",
            "createdAt": self.created_at.isoformat() if self.created_at else "",
            "updatedAt": self.updated_at.isoformat() if self.updated_at else "",
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Trademark {self.id} {self.brand_name!r}>"


class Report(Base):
    """一次检查的归档报告。"""

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: 与旧版文件名一致（``report_YYYYMMDD_HHMMSS.json``），列表页与详情页
    #: 都用它做标识（``app.js`` 的 ``viewReport(report.filename)``）
    filename: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    timestamp: Mapped[str] = mapped_column(String(40), default="", nullable=False, index=True)
    finished_at: Mapped[str] = mapped_column(String(40), default="", nullable=False)

    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    alerts_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    errors_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    has_issues: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    failed_brands: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    errors: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    results: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def to_payload(self) -> dict[str, Any]:
        """还原成旧版报告文件的 JSON 结构。

        **不含 ``alerts`` 键**在存储里，但对外仍要派生出来——与
        ``monitor.alerts_from_results()`` 的约定一致：读取端按需还原，
        前端 ``app.js:751`` 的 ``data.alerts || []`` 因此无需改动。
        """
        from monitor import alerts_from_results

        results = list(self.results or [])
        alerts = alerts_from_results(results)
        return {
            "timestamp": self.timestamp,
            "finishedAt": self.finished_at,
            "total": self.total,
            "checked": self.checked,
            "alertsCount": self.alerts_count,
            "errors": list(self.errors or []),
            "errorsCount": self.errors_count,
            "failedBrands": list(self.failed_brands or []),
            "hasIssues": bool(self.has_issues),
            "alerts": alerts,
            "results": results,
        }

    def to_summary(self) -> dict[str, Any]:
        """紧凑摘要（对应旧版的 ``report_*.summary.json``）。

        只给统计与告警定位，不带案例明细——供看板 / AI 低 token 消费。
        """
        from monitor import alerts_from_results

        alerts = alerts_from_results(list(self.results or []))
        return {
            "timestamp": self.timestamp,
            "finishedAt": self.finished_at,
            "total": self.total,
            "checked": self.checked,
            "alertsCount": self.alerts_count,
            "errorsCount": self.errors_count,
            "failedBrands": list(self.failed_brands or []),
            "hasIssues": bool(self.has_issues),
            "alerts": [
                {
                    "brandName": a.get("brandName", ""),
                    "trademarkId": a.get("trademarkId", ""),
                    "issues": list(a.get("issues") or []),
                    "abnormalStatusCount": len(a.get("abnormalStatuses") or []),
                    "squattingCount": len(a.get("squattingCases") or []),
                    "needsReviewCount": len(a.get("needsReviewCases") or []),
                }
                for a in alerts
            ],
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Report {self.filename} total={self.total} alerts={self.alerts_count}>"


class CheckRun(Base):
    """最近一次检查的运行状态（替代 ``last_check_result.json`` / ``last_check_error.txt``）。

    用单行表（``id`` 恒为 1）表达「当前状态」，比两个文件可靠：
    文件方案在「写一半进程被杀」时会留下损坏内容，且新旧结果无法原子替换。
    """

    __tablename__ = "check_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: running | completed | error
    status: Mapped[str] = mapped_column(String(16), default="idle", nullable=False)
    #: 完成时的 result 载荷（``/api/check/status`` 原样返回的 ``result``）
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<CheckRun status={self.status}>"
