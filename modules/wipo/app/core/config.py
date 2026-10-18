"""应用配置（Pydantic BaseSettings）。

对应旧 Flask 版的「读 ``config.json`` 手写 dict」——这里换成 Pydantic 模型：
类型即文档，缺失/类型错误在**启动时**立刻暴露，而不是等某个请求打进来才 500。

配置来源与优先级（高 → 低）::

    环境变量（WIPO_ 前缀）  >  config.json  >  内置默认值

为什么保留 config.json 而不改成纯环境变量？
    这个系统的 SMTP / 钉钉 / 企微凭据本来就放在 ``config.json``（已被 .gitignore
    忽略、从未进版本库），换成纯 env 会让既有部署全部失效。所以
    ``json_source`` 读文件、env 覆盖敏感项，两者并存。

敏感值（SMTP 密码）**不进日志**——``describe()`` 负责脱敏。
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "AppSettings",
    "ScheduleSettings",
    "NotificationSettings",
    "EmailSettings",
    "WebhookSettings",
    "DatabaseSettings",
    "get_settings",
    "reset_settings",
    "PROJECT_ROOT",
    "ENV_PREFIX",
    "CONFIG_FILE",
]

ENV_PREFIX = "WIPO_"

#: 项目根目录（本文件位于 <root>/app/core/config.py）
PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: 兼容旧的相对路径读取点（main.py / web_app.py 曾用 __file__ 拼绝对路径）
CONFIG_FILE = PROJECT_ROOT / "config.json"


class ScheduleSettings(BaseModel):
    """每月检查的调度配置。"""

    day_of_month: int = Field(1, ge=1, le=31, description="每月第几天运行，1-31")
    hour: int = Field(9, ge=0, le=23, description="运行小时，0-23")
    minute: int = Field(0, ge=0, le=59, description="运行分钟，0-59")

    @field_validator("day_of_month", "hour", "minute", mode="before")
    @classmethod
    def _reject_bool(cls, value: Any) -> Any:
        """``True`` 是 ``int`` 的子类，若不拦住会被静默当成 1。

        这与 ``main.Scheduler.get_schedule_config`` 的校验口径保持一致：
        bool 一律回落默认值，而不是接受它当数字。
        """
        if isinstance(value, bool):
            raise ValueError("布尔值不是合法的调度数值")
        return value


class EmailSettings(BaseModel):
    """SMTP 邮件渠道配置。"""

    smtp_server: str = ""
    smtp_port: int = 587
    sender: str = ""
    recipients: list[str] = Field(default_factory=list)
    username: str = ""
    password: str = ""


class WebhookSettings(BaseModel):
    """Webhook 渠道配置（钉钉 / 企业微信）。"""

    webhook_url: str = ""


class NotificationSettings(BaseModel):
    """通知渠道配置。"""

    channels: list[str] = Field(default_factory=list)
    email: EmailSettings = Field(default_factory=EmailSettings)
    dingtalk: WebhookSettings = Field(default_factory=WebhookSettings)
    wework: WebhookSettings = Field(default_factory=WebhookSettings)


class DatabaseSettings(BaseModel):
    """数据库连接配置。

    默认连本机 MySQL 8.4（实测 root 空密码可连）。生产环境请用
    ``WIPO_DATABASE_URL`` 覆盖，不要在版本库里放真实凭据。
    """

    url: str = "mysql+aiomysql://root:@127.0.0.1:3306/wipo_trademark?charset=utf8mb4"
    echo: bool = False
    pool_size: int = 5
    max_overflow: int = 10
    pool_recycle: int = 3600  # MySQL 默认 wait_timeout 8 小时，留足余量


class AppSettings(BaseSettings):
    """应用总配置。"""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 应用元信息 ----
    app_name: str = "WIPO 商标监控系统"
    app_version: str = "2.0.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    api_prefix: str = "/api"
    docs_url: str = "/docs"

    # ---- 业务配置（来自 config.json）----
    config_file: str = str(CONFIG_FILE)
    trademarks_file: str = str(PROJECT_ROOT / "data" / "user_trademarks.json")
    reports_dir: str = str(PROJECT_ROOT / "reports")

    # ---- 数据库 ----
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)

    # ---- 通知与调度 ----
    notification: NotificationSettings = Field(default_factory=NotificationSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)

    #: 标记是否真的读到了 config.json（供 /health 与启动日志显示）
    config_loaded: bool = False

    @classmethod
    def load(cls) -> AppSettings:
        """先读 ``config.json``，再让环境变量覆盖。

        ``config.json`` 缺失或损坏时**不阻断启动**：用内置默认值继续，
        并把 ``config_loaded`` 置 False，让 ``/health`` 显示出来。
        理由与 PNA 的 lifespan 探活一致——配置有问题要让运维在启动阶段看见，
        而不是首个请求 500。
        """
        payload: dict[str, Any] = {}
        cfg_path = CONFIG_FILE
        loaded = False
        if cfg_path.exists():
            try:
                raw = json.loads(cfg_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    # 去掉 _comment_* 说明键
                    payload = {k: v for k, v in raw.items() if not k.startswith("_comment")}
                    loaded = True
            except (OSError, ValueError):
                loaded = False

        # ``env_nested_delimiter="__"`` 的坑（实测）：pydantic-settings 只把
        # 嵌套 env 灌进**模型字段**，不会写进 ``os.environ``。而这里
        # ``cls(**payload)`` 走的是构造参数，env 嵌套值根本不参与。
        # 所以显式把 ``WIPO_DATABASE__URL`` 之类的 env 读出来塞进 payload，
        # 否则「用环境变量指向测试 SQLite」这件事会静默失效——
        # 表现为测试连上了真实 MySQL 库。
        payload = _apply_env_overrides(payload)
        settings = cls(**payload)
        object.__setattr__(settings, "config_loaded", loaded)
        return settings

    def describe(self) -> dict[str, str]:
        """脱敏后的配置摘要，供启动日志与 ``/health`` 展示。

        口令一律只显示「是否已配置」，绝不回显明文——这些值会进日志、
        进 CI 输出、进运维截图。
        """

        def _mask(value: str) -> str:
            return "已配置" if value else "未配置"

        return {
            "app": f"{self.app_name} {self.app_version}",
            "debug": str(self.debug),
            "host:port": f"{self.host}:{self.port}",
            "config.json": "已加载" if self.config_loaded else "缺失，使用默认值",
            "database": _redact_url(self.database.url),
            "notify.channels": ",".join(self.notification.channels) or "(未启用)",
            "notify.email.sender": self.notification.email.sender or "(未配置)",
            "notify.email.password": _mask(self.notification.email.password),
            "notify.dingtalk": "已配置" if self.notification.dingtalk.webhook_url else "未配置",
            "notify.wework": "已配置" if self.notification.wework.webhook_url else "未配置",
            "schedule": (
                f"每月 {self.schedule.day_of_month} 日 "
                f"{self.schedule.hour:02d}:{self.schedule.minute:02d}"
            ),
        }


def _redact_url(url: str) -> str:
    """隐藏数据库 URL 里的口令。"""
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" not in rest:
        return url
    creds, host = rest.rsplit("@", 1)
    if ":" in creds:
        user, _ = creds.split(":", 1)
        return f"{scheme}://{user}:***@{host}"
    return f"{scheme}://{creds}@{host}"


#: 需要从环境变量显式读取的嵌套配置。
#: pydantic-settings 的 ``env_nested_delimiter`` 只对「BaseSettings 自己收集
#: env」有效；``AppSettings.load()`` 是手工 ``cls(**payload)``，嵌套 env 不
#: 会自己出现，必须在这里点名要。漏一个的后果是静默用错库（实测踩过）。
_ENV_NESTED_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("database", ("url", "echo")),
    ("notification", ("channels",)),
    ("schedule", ("day_of_month", "hour", "minute")),
)


def _apply_env_overrides(payload: dict[str, Any]) -> dict[str, Any]:
    """把 ``WIPO_<段>__<字段>`` 形式的环境变量灌进 payload。

    只覆盖**已存在的段**，不凭空创建：段本身（database / notification /
    schedule）的类型是 BaseModel，缺段时用默认工厂。
    """
    merged = {k: dict(v) if isinstance(v, dict) else v for k, v in payload.items()}
    for section, fields in _ENV_NESTED_KEYS:
        bucket = merged.get(section)
        if not isinstance(bucket, dict):
            continue
        for field in fields:
            env_name = f"{ENV_PREFIX}{section.upper()}__{field.upper()}"
            raw = os.environ.get(env_name)
            if raw is None or raw == "":
                continue
            # 段内类型由 Pydantic 负责转换；这里只保证「值被看见」。
            bucket[field] = _coerce_env_value(raw)
    return merged


def _coerce_env_value(raw: str) -> Any:
    """环境变量都是字符串，把明显的布尔/数字还原成对应类型。

    Pydantic 之后也会做一次校验（``_reject_bool`` 等），这里只做「看起来像
    布尔/数字」的宽松转换，避免 ``"false"`` 被当成真值字符串传进 bool 字段。
    """
    lowered = raw.strip().lower()
    if lowered in ("true", "1", "yes", "on"):
        return True
    if lowered in ("false", "0", "no", "off"):
        return False
    if lowered.lstrip("-").isdigit():
        return int(lowered)
    return raw


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """进程级单例配置。"""
    return AppSettings.load()


def reset_settings() -> None:
    """清缓存（测试用）。"""
    get_settings.cache_clear()


# 便于类型标注：``SettingsDep`` 用的就是这个类型
Settings = AppSettings
