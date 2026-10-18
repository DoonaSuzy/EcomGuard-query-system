"""Pydantic 请求 / 响应模型。

约定
----
- 字段名统一 camelCase：**不是偏好，是契约**。旧 Flask 版直接把前端传来的
  ``brandName`` / ``registrationNumber`` / ``niceClass`` 原样存取，前端与
  106 项 E2E 用例都按这些键取值。用 snake_case 会一次性打断它们。
- 长度 / 格式约束写在模型里，替代旧版散落的手写 ``if``（如
  ``web_app.py:226-241`` 的「不能为空 / 不超过 200 / 至少 2」三段校验）。
  注意文案要保持一致，否则 ``tests/test_form_validation.py`` 会失败。
- 写请求用独立模型（``TrademarkCreate`` / ``TrademarkUpdate``），
  不让 ``id`` / ``createdAt`` 这类服务端字段出现在入参里。
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "TrademarkBase",
    "TrademarkCreate",
    "TrademarkUpdate",
    "TrademarkItem",
    "TrademarkListResponse",
    "BatchDeleteRequest",
    "BatchDeleteResponse",
    "CheckRequest",
    "CheckStatusResponse",
    "ReportListItem",
    "ReportListResponse",
    "ImportResponse",
    "HealthResponse",
]

T = TypeVar("T")

#: 与旧版 web_app.py 的手写校验保持一致的边界
BRAND_NAME_MIN_LEN = 2
BRAND_NAME_MAX_LEN = 200


def _as_camel(name: str) -> str:
    """snake_case → camelCase（用于 alias 生成）。"""
    head, *tail = name.split("_")
    return head + "".join(w.title() for w in tail)


class CamelModel(BaseModel):
    """统一开启「按 camelCase 收发、内部仍可用 snake_case」的基类。"""

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=_as_camel,
        str_strip_whitespace=True,
        extra="ignore",
    )


class TrademarkBase(CamelModel):
    """商标的公共字段。"""

    brand_name: str = Field(
        ...,
        min_length=BRAND_NAME_MIN_LEN,
        max_length=BRAND_NAME_MAX_LEN,
        description="商标名称",
    )
    applicant: str = Field("", max_length=500, description="申请人名称")
    registration_number: str = Field("", max_length=100, description="注册号")
    nice_class: list[Any] = Field(default_factory=list, description="尼斯分类")
    notes: str = Field("", description="备注")

    @field_validator("brand_name", mode="before")
    @classmethod
    def _brand_name_required(cls, value: Any) -> Any:
        """空字符串要给「不能为空」而不是「长度过短」。

        旧版是显式三段校验，前端 ``#brandName-error`` 直接显示这句文案
        （``tests/test_form_validation.py`` 断言过）。这里在进模型前先拦，
        保证文案不变。
        """
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError("商标名称不能为空")
        return value

    @field_validator("nice_class", mode="before")
    @classmethod
    def _nice_class_list(cls, value: Any) -> Any:
        """前端历史上可能传字符串（逗号分隔）或数组，统一成数组。"""
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [p.strip() for p in value.split(",") if p.strip()]
        return value


class TrademarkCreate(TrademarkBase):
    """新增商标的请求体。"""


class TrademarkUpdate(CamelModel):
    """更新商标的请求体（字段全部可选，只改传了的）。"""

    brand_name: str | None = Field(
        None, min_length=BRAND_NAME_MIN_LEN, max_length=BRAND_NAME_MAX_LEN
    )
    applicant: str | None = Field(None, max_length=500)
    registration_number: str | None = Field(None, max_length=100)
    nice_class: list[Any] | None = None
    notes: str | None = None

    @field_validator("brand_name", mode="before")
    @classmethod
    def _brand_name_not_blank(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            raise ValueError("商标名称不能为空")
        return value


class TrademarkItem(CamelModel):
    """商标的响应形态（与旧版 JSON 逐字段一致）。"""

    id: str
    brand_name: str
    applicant: str = ""
    registration_number: str = ""
    nice_class: list[Any] = Field(default_factory=list)
    notes: str = ""
    status: str = "pending"
    created_at: str = ""
    updated_at: str = ""


class TrademarkListResponse(CamelModel):
    """``GET /api/trademarks`` 的 ``data``。"""

    trademarks: list[TrademarkItem] = Field(default_factory=list)
    total: int = 0


class BatchDeleteRequest(CamelModel):
    """批量删除请求。"""

    ids: list[str] = Field(default_factory=list, description="要删除的商标 id 列表")


class BatchDeleteResponse(CamelModel):
    """批量删除响应。"""

    deleted_count: int = 0


class CheckRequest(CamelModel):
    """``POST /api/check`` 请求体。

    ``ids`` 为空表示检查全部商标（旧版语义：不传就是全量）。
    """

    ids: list[str] = Field(default_factory=list, description="要检查的商标 id；空=全部")
    check_status: bool = Field(True, description="是否检查商标状态")
    check_squatting: bool = Field(True, description="是否检查抢注情况")


class CheckStatusResponse(CamelModel):
    """``GET /api/check/status`` 的响应。"""

    status: str = Field(..., description="running | completed | error | idle")
    result: dict[str, Any] | None = None
    error: str = ""


class ReportListItem(CamelModel):
    """报告列表项（旧版 ``get_reports`` 的字段）。"""

    filename: str
    timestamp: str = ""
    total: int = 0
    alerts_count: int = 0
    #: 迁移后新增：让列表页能直接看出「本月是真无异常还是检查不完整」。
    #: 旧版没有这两个字段，前端不读它们，因此是安全的增量。
    errors_count: int = 0
    has_issues: bool = False


class ReportListResponse(CamelModel):
    """``GET /api/reports`` 的 ``data``。"""

    reports: list[ReportListItem] = Field(default_factory=list)
    total: int = 0


class ImportResponse(CamelModel):
    """``POST /api/import`` 的响应。"""

    imported_count: int = 0
    skipped_count: int = 0
    errors: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel, Generic[T]):
    """``/health`` 响应（ok / degraded / down 三态）。

    三态语义与 PNA 一致：
    - ``ok``      所有依赖正常；
    - ``degraded`` 数据库正常但 WIPO 上游不可达——**仍返回 200**，
      因为「查不了 WIPO」不等于「服务死了」，容器编排不该据此重启；
    - ``down``    数据库不可用（连清单都读不了），返回 503。
    """

    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(..., description="ok | degraded | down")
    app: str = ""
    version: str = ""
    dependencies: dict[str, Any] = Field(default_factory=dict)
    uptime_seconds: float = 0.0
