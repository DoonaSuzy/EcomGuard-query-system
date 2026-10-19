# -*- coding: utf-8 -*-
"""
营业执照状态巡检 HTTP 服务（FastAPI）
=====================================
把 credit_check.py 的抓取能力包装成 REST API：提交作业 → 轮询状态 → 取结果 → 推送提醒。

为什么是「作业队列 + 单 worker 串行」而不是同步接口
--------------------------------------------------
抓取依赖一个**有状态、不可并发**的资源：
  - 一个 Camoufox 浏览器实例；
  - 过码成功后拿到的 `vcode` 会话只有 5 分钟有效期，复用同一个页面上下文才划算；
  - 目标站有瑞数风控，并发请求等于自己举报自己。
所以 HTTP 层只做「登记作业」，真正抓取由一个常驻 worker 串行消费。
这样 POST /api/jobs 立即返回 job_id，不会因为几十家要跑十几分钟而超时。

启动：
    .venv\\Scripts\\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000
    # 或带自动重载开发
    .venv\\Scripts\\python.exe -m uvicorn api:app --reload --port 8000

交互式文档：http://127.0.0.1:8000/docs
控制台（已构建前端时）：http://127.0.0.1:8000/ —— dist 由本文件尾部挂在 / 下，
与 /api 同源，无需另起 dev server。业务接口统一在 /api 前缀下，
页面路径（/overview、/jobs、/jobs/<id>）不与接口抢路径。
"""
from __future__ import annotations

import asyncio
import contextlib
import csv
import json
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator

import credit_check
import logkit
import notify
import schedule as sched

# ---------------------------------------------------------------- .env 加载
# **必须在下面所有 os.getenv 之前执行**，否则 CREDIT_DB / CREDIT_OUT 读到的是
# 进程环境而不是 .env；更要紧的是 Worker 里发信读的 SMTP_HOST/SMTP_USER/
# SMTP_PASS 全是 os.getenv，不加载 .env 就等于「收件人填了、邮件通道却永远
# 显示未配置」——Web 提交的作业一封邮件都发不出去。
#
# 这个坑原先只在 notify.py 的 main() 和 healthcheck.py 里调 load_dotenv，
# 也就是**只有命令行/计划任务路径**能读到 .env；API 服务路径（应用内的主用法）
# 从来没加载过。前端「填写了邮箱、作业跑完、什么也没收到、也没有任何报错」的
# 现象就是它造成的：_maybe_notify 拿到 mail_to 却在 `if not host` 分支里
# 记一条 "未配置 SMTP_HOST" 的日志，界面上完全看不见。
#
# override=False（load_dotenv 默认）：已存在的环境变量优先，CI/容器里用真实
# 环境变量顶替本地 .env 的用法保持不变。
notify.load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
#: 存储后端选择器。取值有两种形态，靠有无 ``://`` 区分：
#:
#: * 普通路径（默认）→ SQLite，如 ``data/jobs.db``
#: * 连接串 → MySQL，如 ``mysql://root:pw@127.0.0.1:3306/credit_check``
#:
#: 为什么用同一个环境变量而不是新增一个：部署时只需改一处；且默认值仍是
#: SQLite，现有测试（都设 ``CREDIT_DB=<tmp>/jobs.db``）无需改动即可继续跑。
CREDIT_DB = os.getenv("CREDIT_DB", str(BASE_DIR / "data" / "jobs.db"))
#: Store 的入参：SQLite 文件路径，或 mysql:// 连接串。调用方传这个，别传 DB_PATH。
DB_TARGET: str = CREDIT_DB
#: SQLite 模式下的文件路径；MySQL 模式下为 None。
#:
#: 为什么有条件：``Path("mysql://root:@127.0.0.1:3307/credit_check")`` 会得到
#: ``mysql:\root:@127.0.0.1:3307\credit_check``（反斜杠！），下游任何按路径
#: 处理的代码都会拿着一个废值去 stat/日志，排查时极具误导性。
DB_PATH: Path | None = None if "://" in CREDIT_DB else Path(CREDIT_DB)
OUT_DIR = Path(os.getenv("CREDIT_OUT", BASE_DIR / "data" / "results"))

log = logging.getLogger("api")

Verdict = Literal["ok", "alert", "notfound", "unknown"]

#: 单封地址。刻意不追求 RFC 5322 全覆盖——那套正则长得没法维护，而且真正
#: 校验地址是否存在的唯一办法是投递。这里只拦「明显不是邮箱」的输入
#: （漏了 @、域名没有点、混进空格），把「填错了」的反馈提前到提交那一刻。
_ADDR_RE = re.compile(r"^[^@\s,;，；]+@[^@\s,;，；]+\.[^@\s,;，；]+$")

#: webhook 必须是 http(s) 且带主机。``https://`` 这种光秃秃的必须被拒。
_WEBHOOK_RE = re.compile(r"^https?://[^\s/]+\.[^\s/]+(?:[/?#]\S*)?$")



def _norm_mail(v: str | None) -> str | None:
    """校验并**规范化**收件人，多个地址统一成英文逗号分隔。

    为什么必须在这里管：原先 ``_maybe_notify`` 用的是
    ``mail_to.split(",")``，而前端输入框允许中文逗号（它的正则字符类是
    ``[,，]``），于是 ``a@x.com，b@y.com`` 会被当成**一个**地址塞进
    ``send_mail``——SMTP 服务器要么报错、要么只投第一个，用户看到的是
    「填了两个只发了一个」，且没有任何日志能定位。

    这里顺手把逗号、分号、中英文都归一，与 ``notify.parse_addrs`` 同一口径，
    这样库里存的永远是可直接 split 的形态（计划任务读库时也受益）。

    提成模块级函数是因为 ``CheckRequest`` 与 ``ScheduleIn`` 必须共用同一套
    规则：调度建出来的作业最终走的是同一条推送链路，两处校验若各写一份，
    迟早漂移成「手动能填、定时静默失败」。
    """
    import notify as _notify
    addrs = _notify.parse_addrs(v)
    if not addrs:
        return None
    bad = [a for a in addrs if not _ADDR_RE.match(a)]
    if bad:
        raise ValueError(f"收件人格式不正确：{', '.join(bad)}")
    return ",".join(addrs)


def _norm_webhook(v: str | None) -> str | None:
    """webhook 必须是带主机的 http(s) 地址。

    原先前端只判 ``/^https?:\\/\\//``，光秃秃一个 ``https://`` 也能提交；
    服务端又不校验，作业会一路跑到推送那一步才抛
    ``MissingSchema``/``InvalidURL``——而那个异常被
    ``errors.append(f"webhook: {e}")`` 收进日志，界面上看不到，
    用户以为推送了、其实一条都没发出去。
    """
    v = (v or "").strip()
    if not v:
        return None
    if not _WEBHOOK_RE.match(v):
        raise ValueError(
            "webhook 需形如 https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx")
    return v


# ============================================================ 数据模型
class CheckRequest(BaseModel):
    """提交一次巡检作业。"""
    names: list[str] = Field(..., min_length=1, max_length=200,
                            description="企业全称列表，最多 200 家")
    webhook: str | None = Field(None, description="企业微信/钉钉/飞书 webhook，留空则不发")
    mail_to: str | None = Field(None, description="收件人，逗号分隔；留空则不发邮件")
    always_notify: bool = Field(False, description="无异常也推送（周报用）")
    min_delay: float = Field(3.5, ge=0, le=60, description="两家之间最小间隔秒数")
    max_delay: float = Field(6.5, ge=0, le=120, description="两家之间最大间隔秒数")

    @field_validator("names")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        out, seen = [], set()
        for raw in v:
            n = (raw or "").strip()
            if not n or n.startswith("#"):
                continue
            if n not in seen:            # 去重但不改大小写/全半角，归一交给 norm_name
                seen.add(n)
                out.append(n)
        if not out:
            raise ValueError("names 清洗后为空")
        return out

    @field_validator("max_delay")
    @classmethod
    def _order(cls, v: float, info) -> float:
        lo = info.data.get("min_delay", 0)
        if v < lo:
            raise ValueError(f"max_delay({v}) 不能小于 min_delay({lo})")
        return v

    @field_validator("mail_to")
    @classmethod
    def _check_mail(cls, v: str | None) -> str | None:
        return _norm_mail(v)

    @field_validator("webhook")
    @classmethod
    def _check_webhook(cls, v: str | None) -> str | None:
        return _norm_webhook(v)


class ResultItem(BaseModel):
    name: str
    matched_name: str = ""
    credit_code: str = ""
    status: str = ""
    verdict: Verdict = "unknown"
    entity_type: str = ""
    data_source: str = ""
    record_count: int = 0
    detail_url: str = ""
    note: str = ""


class HealthResponse(BaseModel):
    """健康检查响应。

    有显式模型才会出现在 openapi.json 的 schema 里；只写 `-> dict` 时
    FastAPI 只能声明成 `additionalProperties: true`，前端代码生成拿到的是
    `{[key: string]: unknown}`——字段名全靠猜，改字段也不会有类型报错。
    """
    status: Literal["ok", "degraded"]
    db: bool
    queued: int
    time: str


class CancelResponse(BaseModel):
    """取消作业的响应。"""
    job_id: str
    state: Literal["cancelled"]


class JobSummary(BaseModel):
    job_id: str
    state: Literal["queued", "running", "done", "failed", "cancelled"]
    total: int
    finished: int
    ok: int = 0
    alert: int = 0
    notfound: int = 0
    unknown: int = 0
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    notified: bool = False
    notify_error: str | None = Field(
        None, description="推送失败原因；null 表示推送成功或尚未尝试")


class JobRequest(BaseModel):
    """作业提交时的原始参数回显。

    用户提完一轮之后最常见的疑问是「我到底填的哪个邮箱 / 有没有配 webhook」。
    原先这些只在 ``jobs.request``（JSON 列）里躺着，``_to_summary`` 直接丢弃，
    详情页无从显示——于是丢了收件人只能重填一遍。

    ``notify_error`` 与这里配合才能定位问题：先看填了什么，再看推送为什么失败。
    """
    names: list[str] = []
    webhook: str | None = None
    mail_to: str | None = None
    always_notify: bool = False
    min_delay: float | None = None
    max_delay: float | None = None
    schedule_id: str | None = Field(
        None, description="由哪条定时调度创建；手动提交为 null")


class JobDetail(JobSummary):
    results: list[ResultItem] = []
    request: JobRequest | None = Field(
        None, description="提交时的参数回显；老作业可能为 null")


# ------------------------------------------------------------ 调度模型
#: HH:MM。前端原生 input[type=time] 也产出这个形状，两边口径一致。
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ScheduleIn(BaseModel):
    """新建/修改一条定时巡检。

    参数取舍：
      * 不叫 cron：目标用户是销售主管，cron 表达式对他们是纯粹的负担，
        而且本项目只需要「每日/每周/每月」这三种节奏；
      * ``day`` 上限 28 而非 31——「每月 31 号」在 2/4/6/9/11 月不存在，
        要额外定义「顺延到月末」还是「跳过」，两种都有坑。限 28 后每个月
        都有这一天，行为零歧义；真要月末跑用 weekly 或 daily 即可。
    """
    name: str = Field(..., min_length=1, max_length=100,
                      description="调度名称，如「华东区每日巡检」")
    enabled: bool = Field(True, description="是否启用")
    freq: Literal["daily", "weekly", "monthly"] = Field(..., description="频率")
    at_time: str = Field(..., description="触发时刻 HH:MM（24 小时制）")
    weekday: int = Field(0, ge=0, le=6,
                         description="weekly 用：0=周一 … 6=周日")
    day: int = Field(1, ge=1, le=28,
                     description="monthly 用：每月几号（1-28）")
    names: list[str] = Field(..., min_length=1, max_length=200,
                             description="企业全称列表")
    webhook: str | None = Field(None, description="webhook，留空则不发")
    mail_to: str | None = Field(None, description="收件人，逗号分隔")
    always_notify: bool = Field(False, description="无异常也推送")
    min_delay: float = Field(3.5, ge=0, le=60)
    max_delay: float = Field(6.5, ge=0, le=120)

    @field_validator("at_time")
    @classmethod
    def _check_time(cls, v: str) -> str:
        v = (v or "").strip()
        if not _TIME_RE.match(v):
            raise ValueError(f"触发时刻需形如 09:30（24 小时制），收到 {v!r}")
        return v

    @field_validator("names")
    @classmethod
    def _clean_names(cls, v: list[str]) -> list[str]:
        out, seen = [], set()
        for raw in v:
            n = (raw or "").strip()
            if not n or n.startswith("#"):
                continue
            if n not in seen:
                seen.add(n)
                out.append(n)
        if not out:
            raise ValueError("names 清洗后为空")
        return out

    @field_validator("max_delay")
    @classmethod
    def _order_delay(cls, v: float, info) -> float:
        lo = info.data.get("min_delay", 0)
        if v < lo:
            raise ValueError(f"max_delay({v}) 不能小于 min_delay({lo})")
        return v

    # 与 CheckRequest 共用同一套推送达人校验：调度建出来的作业最终走同一条
    # 推送链路，两处规则若各写一份，迟早漂移成「手动能填、定时静默失败」。
    @field_validator("mail_to")
    @classmethod
    def _check_mail(cls, v: str | None) -> str | None:
        return _norm_mail(v)

    @field_validator("webhook")
    @classmethod
    def _check_webhook(cls, v: str | None) -> str | None:
        return _norm_webhook(v)


class ScheduleOut(BaseModel):
    """一条调度的对外形态。

    ``next_run_at`` 与 ``human`` 同时给：前者给程序/排序用，后者直接显示。
    前端不自己拼「每周一 09:30」这类文案——拼错了没人会发现，
    而后端这一份是与实际计算共用同一组常量的。
    """
    schedule_id: str
    name: str
    enabled: bool
    freq: Literal["daily", "weekly", "monthly"]
    at_time: str
    weekday: int = 0
    day: int = 1
    names: list[str] = []
    webhook: str | None = None
    mail_to: str | None = None
    always_notify: bool = False
    min_delay: float = 3.5
    max_delay: float = 6.5
    next_run_at: str | None = None
    last_run_at: str | None = None
    last_job_id: str | None = None
    last_state: str | None = None
    last_error: str | None = None
    created_at: str = ""
    updated_at: str = ""
    human: str = Field("", description="节奏的中文描述，直接展示用")
    count: int = Field(0, description="名单企业数")


class ScheduleDeleteResponse(BaseModel):
    schedule_id: str
    deleted: Literal[True] = True


class SchedulePreview(BaseModel):
    """编辑弹窗的实时预览。

    两个字段都要给：``next_run_at`` 让人看到具体日期（能立刻发现「怎么排到下个月
    去了」这种明显错配），``human`` 用一句话说清节奏（「每周三 09:30」）。
    """
    next_run_at: str
    human: str


# ============================================================ 存储层
#
# 两套 DDL：SQLite 与 MySQL 的列类型、索引语法、自增语义都不同，
# 硬凑成一份会在其中一端报语法错。这里分开写，字段名与顺序保持一致，
# 这样 SQL 语句（除占位符）可以共用。
SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    state       TEXT NOT NULL,
    total       INTEGER NOT NULL,
    finished    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    error       TEXT,
    notified    INTEGER NOT NULL DEFAULT 0,
    notify_error TEXT,
    request     TEXT NOT NULL,
    csv_path    TEXT
);
CREATE TABLE IF NOT EXISTS results (
    job_id      TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    name        TEXT NOT NULL,
    matched_name TEXT,
    credit_code TEXT,
    status      TEXT,
    verdict     TEXT,
    entity_type TEXT,
    data_source TEXT,
    record_count INTEGER,
    detail_url  TEXT,
    note        TEXT,
    PRIMARY KEY (job_id, seq),
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_results_job ON results(job_id, seq);
CREATE TABLE IF NOT EXISTS schedules (
    schedule_id   TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    freq          TEXT NOT NULL,
    at_time       TEXT NOT NULL,
    weekday       INTEGER NOT NULL DEFAULT 0,
    day           INTEGER NOT NULL DEFAULT 1,
    names         TEXT NOT NULL,
    webhook       TEXT,
    mail_to       TEXT,
    always_notify INTEGER NOT NULL DEFAULT 0,
    min_delay     REAL NOT NULL DEFAULT 3.5,
    max_delay     REAL NOT NULL DEFAULT 6.5,
    next_run_at   TEXT,
    last_run_at   TEXT,
    last_job_id   TEXT,
    last_state    TEXT,
    last_error    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""

#: MySQL 侧：TEXT 不能做主键（InnoDB 要求定长前缀或改成 VARCHAR），
#: 所以 job_id 用 VARCHAR(64)；request 存 JSON 文本，用 LONGTEXT 兼容旧数据
#: （不用 JSON 类型——旧库里存的是任意 dict，且我们只整存整取，不做路径查询）。
#: ``IF NOT EXISTS`` 索引在 MySQL 不支持，索引写进 CREATE TABLE。
SCHEMA_MYSQL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id      VARCHAR(64) NOT NULL,
    state       VARCHAR(16) NOT NULL,
    total       INT NOT NULL,
    finished    INT NOT NULL DEFAULT 0,
    created_at  VARCHAR(40) NOT NULL,
    started_at  VARCHAR(40) DEFAULT NULL,
    finished_at VARCHAR(40) DEFAULT NULL,
    error       TEXT,
    notified    TINYINT(1) NOT NULL DEFAULT 0,
    notify_error TEXT,
    request     LONGTEXT NOT NULL,
    csv_path    VARCHAR(1024) DEFAULT NULL,
    PRIMARY KEY (job_id),
    KEY idx_jobs_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

SCHEMA_MYSQL_RESULTS = """
CREATE TABLE IF NOT EXISTS results (
    job_id      VARCHAR(64) NOT NULL,
    seq         INT NOT NULL,
    name        VARCHAR(512) NOT NULL,
    matched_name VARCHAR(512) DEFAULT NULL,
    credit_code VARCHAR(64) DEFAULT NULL,
    status      VARCHAR(64) DEFAULT NULL,
    verdict     VARCHAR(16) DEFAULT NULL,
    entity_type VARCHAR(64) DEFAULT NULL,
    data_source VARCHAR(64) DEFAULT NULL,
    record_count INT DEFAULT NULL,
    detail_url  VARCHAR(1024) DEFAULT NULL,
    note        TEXT,
    PRIMARY KEY (job_id, seq),
    KEY idx_results_job (job_id, seq),
    CONSTRAINT fk_results_job FOREIGN KEY (job_id)
        REFERENCES jobs(job_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

#: 调度表。字段取舍说明：
#:   * ``names`` 存 JSON 数组文本而不是另建关联表——一份调度就是一个名单，
#:     关系是 1:1，拆表只会多一次 join 和一堆级联删除；
#:   * ``next_run_at`` / ``last_run_at`` 存**带时区**的 ISO 串。带时区是关键：
#:     不带时区时，库里一条 "2026-10-20T09:00:00" 到底是本机时间还是 UTC
#:     没有任何线索，一旦算错就是「差 8 小时」这种极难自证的错；
#:   * ``last_state`` / ``last_error`` 冗余记录最近一次结果，让列表页不必
#:     反查 jobs 表（作业可能被清理，调度记录要留痕）。
SCHEMA_MYSQL_SCHEDULES = """
CREATE TABLE IF NOT EXISTS schedules (
    schedule_id   VARCHAR(64) NOT NULL,
    name          VARCHAR(255) NOT NULL,
    enabled       TINYINT(1) NOT NULL DEFAULT 1,
    freq          VARCHAR(16) NOT NULL,
    at_time       VARCHAR(8) NOT NULL,
    weekday       INT NOT NULL DEFAULT 0,
    day           INT NOT NULL DEFAULT 1,
    names         LONGTEXT NOT NULL,
    webhook       VARCHAR(1024) DEFAULT NULL,
    mail_to       VARCHAR(1024) DEFAULT NULL,
    always_notify TINYINT(1) NOT NULL DEFAULT 0,
    min_delay     DOUBLE NOT NULL DEFAULT 3.5,
    max_delay     DOUBLE NOT NULL DEFAULT 6.5,
    next_run_at   VARCHAR(40) DEFAULT NULL,
    last_run_at   VARCHAR(40) DEFAULT NULL,
    last_job_id   VARCHAR(64) DEFAULT NULL,
    last_state    VARCHAR(16) DEFAULT NULL,
    last_error    TEXT,
    created_at    VARCHAR(40) NOT NULL,
    updated_at    VARCHAR(40) NOT NULL,
    PRIMARY KEY (schedule_id),
    KEY idx_schedules_due (enabled, next_run_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _parse_mysql_url(url: str) -> dict[str, Any]:
    """把 ``mysql://user:pw@host:port/db?charset=utf8mb4`` 拆成 pymysql 参数。

    不引 SQLAlchemy：这一层只做「开连接 / 跑 SQL / 关连接」，同步 12 条语句，
    为它引一个 ORM 反而增加依赖面。用 ``urllib.parse`` 拆，标准库即可。

    支持的写法与限制：
    * ``mysql://`` 与 ``mysql+pymysql://`` 都接受（后者方便从别处的配置直接拷过来）
    * 用户名/密码里的特殊字符需 URL 编码（``%40`` 之类），``urlsplit`` 会解码
    * ``charset`` 等 query 参数不在这里处理——固定用 utf8mb4（见 ``_mysql_connect``）
    """
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.split("+")[0]
    if scheme not in ("mysql", "mariadb"):
        raise ValueError(
            f"CREDIT_DB 看起来是连接串但协议不支持：{parsed.scheme!r}；"
            "只支持 mysql:// 或普通文件路径")
    database = (parsed.path or "").lstrip("/")
    if not database:
        raise ValueError(
            f"CREDIT_DB 连接串缺少库名：{url!r}；形如 "
            "mysql://root:pw@127.0.0.1:3306/credit_check")
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3306,
        "user": urllib.parse.unquote(parsed.username or "root"),
        "password": urllib.parse.unquote(parsed.password or ""),
        "database": database,
    }


class Store:
    """作业库封装，支持 SQLite 与 MySQL 两种后端。

    后端由 ``CREDIT_DB`` 的形态决定（见模块顶部注释）：

    * 普通路径 → ``sqlite3``，每次操作开新连接（避免跨线程共享连接，
      SQLite 的 connection 不是线程安全的）
    * ``mysql://...`` → ``pymysql``，同样每次操作开新连接，靠 MySQL 服务端
      承压（作业量是「一天几十条」，不是高并发场景，连接池收益很小）

    两种后端对外暴露**完全相同**的 11 个方法，调用方不需要知道用的是哪个。
    """

    def __init__(self, path: Path | str) -> None:
        raw = str(path)
        self.dsn: dict[str, Any] | None = None
        self.path: Path | None = None
        self.backend: str
        if "://" in raw:
            self.backend = "mysql"
            self.dsn = _parse_mysql_url(raw)
            self._mysql_init()
        else:
            self.backend = "sqlite"
            self.path = Path(raw)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._conn() as c:
                c.executescript(SCHEMA_SQLITE)
                self._sqlite_add_columns(c)

    # -- 列级迁移 -------------------------------------------------------
    #: 建表用 ``CREATE TABLE IF NOT EXISTS``，对**已存在**的表完全无操作。
    #: 于是「给 jobs 加一列」这件事必须单独做一次，否则老库跑新代码时
    #: INSERT/SELECT 会报 "no such column"。这里只补“新增的可空列”，
    #: 不碰类型变更（SQLite 改列类型要重建表，代价与风险都不成比例）。
    _SQLITE_ADDED_COLUMNS = (
        ("jobs", "notify_error", "TEXT"),
    )

    def _sqlite_add_columns(self, conn) -> None:
        for table, column, decl in self._SQLITE_ADDED_COLUMNS:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def _mysql_add_columns(self, conn) -> None:
        """MySQL 侧同样补列；``ADD COLUMN IF NOT EXISTS`` 8.0.29+ 才有，

        而本项目声明支持 8.x 全系，所以先查 information_schema 再决定加不加。
        """
        for table, column, decl in self._SQLITE_ADDED_COLUMNS:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s "
                    "AND COLUMN_NAME=%s", (table, column))
                if cur.fetchone()["n"] == 0:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    # -- 连接管理 -------------------------------------------------------
    def _mysql_connect(self):
        import pymysql
        import pymysql.cursors

        return pymysql.connect(
            **self.dsn, charset="utf8mb4", autocommit=False,
            cursorclass=pymysql.cursors.DictCursor)

    def _mysql_init(self) -> None:
        """建表。MySQL 的 ``CREATE TABLE IF NOT EXISTS`` 幂等，可反复调用。"""
        conn = self._mysql_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_MYSQL)
                cur.execute(SCHEMA_MYSQL_RESULTS)
                cur.execute(SCHEMA_MYSQL_SCHEDULES)
            conn.commit()
            self._mysql_add_columns(conn)
            conn.commit()
        finally:
            conn.close()

    @contextlib.contextmanager
    def _conn(self):
        if self.backend == "mysql":
            conn = self._mysql_connect()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            return
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _execute(self, conn, sql: str, params: tuple = ()):
        """执行一条语句。占位符在 MySQL 下是 %s，SQLite 下是 ?。

        SQL 文本统一用 ``?`` 书写，这里按后端替换，避免两份 SQL 文本漂移。
        """
        if self.backend == "mysql":
            sql = sql.replace("?", "%s")
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur

    def _fetchall(self, conn, sql: str, params: tuple = ()) -> list[dict]:
        cur = self._execute(conn, sql, params)
        rows = cur.fetchall()
        if self.backend == "mysql":
            return [dict(r) for r in rows]
        return [dict(r) for r in rows]

    # -- 作业 -----------------------------------------------------------
    def create_job(self, names: list[str], request: dict) -> str:
        job_id = f"job_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
        with self._conn() as c:
            self._execute(
                c,
                "INSERT INTO jobs(job_id,state,total,created_at,request) "
                "VALUES(?,?,?,?,?)",
                (job_id, "queued", len(names), _now(),
                 __import__("json").dumps(request, ensure_ascii=False)))
        return job_id

    def set_state(self, job_id: str, state: str, *, error: str | None = None,
                  csv_path: str | None = None) -> None:
        sets = ["state=?"]
        vals: list[Any] = [state]
        if state == "running":
            sets.append("started_at=?")
            vals.append(_now())
        if state in ("done", "failed", "cancelled"):
            sets.append("finished_at=?")
            vals.append(_now())
        if error is not None:
            sets.append("error=?")
            vals.append(error)
        if csv_path is not None:
            sets.append("csv_path=?")
            vals.append(csv_path)
        vals.append(job_id)
        with self._conn() as c:
            self._execute(c, f"UPDATE jobs SET {','.join(sets)} WHERE job_id=?",
                          tuple(vals))

    def mark_notified(self, job_id: str, error: str = "") -> None:
        """记录一次推送结果。

        ``error`` 非空表示推送失败：此时 ``notified`` 仍置 1——它的语义是
        「推送已经尝试过」，而不是「推送成功」。这个区分很重要：原先失败路径
        什么都不写，作业列表里那一栏永远显示「未推送」，用户无从判断
        「还没跑完」「被跳过」还是「试了但失败」。失败原因落进
        ``notify_error``，通过 JobSummary 直接显示到界面上。
        """
        with self._conn() as c:
            self._execute(c, "UPDATE jobs SET notified=1, notify_error=? "
                             "WHERE job_id=?", (error or None, job_id))

    def add_result(self, job_id: str, seq: int, row: dict) -> None:
        values = (job_id, seq, row.get("输入名称", ""), row.get("匹配名称", ""),
                  row.get("统一社会信用代码", ""), row.get("登记状态", ""),
                  row.get("判定", "unknown"), row.get("主体类型", ""),
                  row.get("数据源", ""), int(row.get("记录数") or 0),
                  row.get("详情URL", ""), row.get("备注", ""))
        cols = ("job_id,seq,name,matched_name,credit_code,status,verdict,"
                "entity_type,data_source,record_count,detail_url,note")
        with self._conn() as c:
            if self.backend == "mysql":
                # SQLite 的 INSERT OR REPLACE 靠「冲突即删再插」；MySQL 对应写法是
                # ON DUPLICATE KEY UPDATE，语义差别只在自增列（本表没有）。
                self._execute(
                    c,
                    f"INSERT INTO results({cols}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON DUPLICATE KEY UPDATE name=VALUES(name), "
                    "matched_name=VALUES(matched_name), "
                    "credit_code=VALUES(credit_code), status=VALUES(status), "
                    "verdict=VALUES(verdict), entity_type=VALUES(entity_type), "
                    "data_source=VALUES(data_source), "
                    "record_count=VALUES(record_count), "
                    "detail_url=VALUES(detail_url), note=VALUES(note)",
                    values)
            else:
                self._execute(
                    c,
                    f"INSERT OR REPLACE INTO results({cols}) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", values)

    def bump_finished(self, job_id: str) -> None:
        with self._conn() as c:
            self._execute(c, "UPDATE jobs SET finished=finished+1 WHERE job_id=?",
                          (job_id,))

    def get_job(self, job_id: str) -> dict | None:
        with self._conn() as c:
            rows = self._fetchall(c, "SELECT * FROM jobs WHERE job_id=?", (job_id,))
        return rows[0] if rows else None

    def list_jobs(self, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            return self._fetchall(
                c, "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))

    def get_results(self, job_id: str) -> list[dict]:
        with self._conn() as c:
            return self._fetchall(
                c, "SELECT * FROM results WHERE job_id=? ORDER BY seq", (job_id,))

    def counts(self, job_id: str) -> dict[str, int]:
        with self._conn() as c:
            rows = self._fetchall(
                c,
                "SELECT verdict, COUNT(*) n FROM results WHERE job_id=? "
                "GROUP BY verdict", (job_id,))
        return {r["verdict"]: r["n"] for r in rows}

    # -- 调度 -----------------------------------------------------------
    # 这一组方法只做存取，不做时间计算——「下一次什么时候跑」全在 schedule.py，
    # 这样时间逻辑只有一份实现，也才能被单元测试脱离数据库直接跑。
    SCHEDULE_COLS = ("schedule_id,name,enabled,freq,at_time,weekday,day,names,"
                     "webhook,mail_to,always_notify,min_delay,max_delay,"
                     "next_run_at,last_run_at,last_job_id,last_state,last_error,"
                     "created_at,updated_at")

    @staticmethod
    def _norm_schedule(r: dict) -> dict:
        """把后端差异抹平：布尔列在 SQLite 是 0/1，在 MySQL 是 0/1 也可能 None。"""
        out = dict(r)
        out["enabled"] = bool(r.get("enabled"))
        out["always_notify"] = bool(r.get("always_notify"))
        out["weekday"] = int(r.get("weekday") or 0)
        out["day"] = int(r.get("day") or 1)
        out["min_delay"] = float(r.get("min_delay") if r.get("min_delay") is not None else 3.5)
        out["max_delay"] = float(r.get("max_delay") if r.get("max_delay") is not None else 6.5)
        return out

    def list_schedules(self) -> list[dict]:
        with self._conn() as c:
            rows = self._fetchall(
                c, f"SELECT {self.SCHEDULE_COLS} FROM schedules "
                   "ORDER BY created_at")
        return [self._norm_schedule(r) for r in rows]

    def get_schedule(self, schedule_id: str) -> dict | None:
        with self._conn() as c:
            rows = self._fetchall(
                c, f"SELECT {self.SCHEDULE_COLS} FROM schedules "
                   "WHERE schedule_id=?", (schedule_id,))
        return self._norm_schedule(rows[0]) if rows else None

    def add_schedule(self, fields: dict) -> str:
        """新增一条调度。``fields`` 里必须已含 next_run_at（由 schedule.py 算好）。"""
        sid = f"sch_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
        now = _now()
        cols = ("schedule_id,name,enabled,freq,at_time,weekday,day,names,webhook,"
                "mail_to,always_notify,min_delay,max_delay,next_run_at,"
                "created_at,updated_at")
        vals = (sid, fields["name"], int(bool(fields.get("enabled", True))),
                fields["freq"], fields["at_time"], int(fields.get("weekday") or 0),
                int(fields.get("day") or 1), fields["names"],
                fields.get("webhook") or None, fields.get("mail_to") or None,
                int(bool(fields.get("always_notify"))),
                float(fields.get("min_delay") or 3.5),
                float(fields.get("max_delay") or 6.5),
                fields["next_run_at"], now, now)
        with self._conn() as c:
            self._execute(c, f"INSERT INTO schedules({cols}) "
                             f"VALUES({','.join('?' * len(vals))})", vals)
        return sid

    def update_schedule(self, schedule_id: str, fields: dict) -> None:
        """局部更新。只允许改白名单字段，避免拼 SQL 时被注入奇怪列名。"""
        allowed = {"name", "enabled", "freq", "at_time", "weekday", "day", "names",
                   "webhook", "mail_to", "always_notify", "min_delay", "max_delay",
                   "next_run_at"}
        sets: list[str] = []
        vals: list[Any] = []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k in ("enabled", "always_notify"):
                v = int(bool(v))
            elif k in ("weekday", "day"):
                v = int(v or 0)
            elif k in ("min_delay", "max_delay"):
                v = float(v)
            sets.append(f"{k}=?")
            vals.append(v)
        if not sets:
            return
        sets.append("updated_at=?")
        vals.extend([_now(), schedule_id])
        with self._conn() as c:
            self._execute(c, f"UPDATE schedules SET {','.join(sets)} "
                             "WHERE schedule_id=?", tuple(vals))

    def delete_schedule(self, schedule_id: str) -> None:
        with self._conn() as c:
            self._execute(c, "DELETE FROM schedules WHERE schedule_id=?",
                          (schedule_id,))

    def set_schedule_next_run(self, schedule_id: str, next_run_at: str,
                              advance_marker: bool = False) -> None:
        """推进 next_run_at。

        ``advance_marker=True`` 时同时写 last_run_at=now——这一对写入必须原子，
        否则「已推进但没记运行时间」会让补跑判断（compare last_run_at）失真。
        """
        if advance_marker:
            sql = ("UPDATE schedules SET next_run_at=?, last_run_at=?, updated_at=? "
                   "WHERE schedule_id=?")
            params: tuple = (next_run_at, _now(), _now(), schedule_id)
        else:
            sql = "UPDATE schedules SET next_run_at=?, updated_at=? WHERE schedule_id=?"
            params = (next_run_at, _now(), schedule_id)
        with self._conn() as c:
            self._execute(c, sql, params)

    def set_schedule_result(self, schedule_id: str, *, last_job_id: str | None = None,
                            last_state: str | None = None,
                            last_error: str | None = None) -> None:
        sets = ["updated_at=?"]
        vals: list[Any] = [_now()]
        if last_job_id is not None:
            sets.append("last_job_id=?")
            vals.append(last_job_id)
        if last_state is not None:
            sets.append("last_state=?")
            vals.append(last_state)
        if last_error is not None:
            sets.append("last_error=?")
            vals.append(last_error)
        vals.append(schedule_id)
        with self._conn() as c:
            self._execute(c, f"UPDATE schedules SET {','.join(sets)} "
                             "WHERE schedule_id=?", tuple(vals))


def _row_to_item(r: dict) -> ResultItem:
    return ResultItem(
        name=r["name"], matched_name=r["matched_name"] or "",
        credit_code=r["credit_code"] or "", status=r["status"] or "",
        verdict=r["verdict"] or "unknown", entity_type=r["entity_type"] or "",
        data_source=r["data_source"] or "", record_count=r["record_count"] or 0,
        detail_url=r["detail_url"] or "", note=r["note"] or "")


def _to_summary(j: dict, store: Store) -> JobSummary:
    cnt = store.counts(j["job_id"])
    return JobSummary(
        job_id=j["job_id"], state=j["state"], total=j["total"],
        finished=j["finished"], ok=cnt.get("ok", 0), alert=cnt.get("alert", 0),
        notfound=cnt.get("notfound", 0), unknown=cnt.get("unknown", 0),
        created_at=j["created_at"], started_at=j["started_at"],
        finished_at=j["finished_at"], error=j["error"],
        notified=bool(j["notified"]),
        notify_error=j.get("notify_error") or None)


def _sync_schedule(store: Store, job_id: str, state: str,
                   error: str | None = None) -> None:
    """把作业终态回写到触发它的调度行（普通作业直接返回）。

    缺这一步，调度列表会永远停在「排队中」：用户看到的是「设了每天 09:30，
    状态一直没变」，与「填了邮箱、跑完了、什么也没收到」属于同一类静默失败
    —— 配置生效了却无从确认。

    推送失败单独记：``notify_error`` 在 ``_maybe_notify`` 里才写入，
    所以 ``done`` 这一条必须等通知跑完再回写。
    """
    try:
        j = store.get_job(job_id)
        if not j:
            return
        req = json.loads(j["request"] or "{}")
    except (TypeError, ValueError):
        return
    sid = req.get("schedule_id")
    if not sid:
        return
    store.set_schedule_result(sid, last_job_id=job_id, last_state=state,
                              last_error=(error or j.get("notify_error") or "")[:500])


# ============================================================ 作业队列
class Worker:
    """单线程串行消费作业队列。

    用一个常驻线程 + 队列而不是 BackgroundTasks，是因为浏览器实例要跨作业
    复用（省去每次重启 + 重新过码），而 BackgroundTasks 生命周期跟随请求。
    """

    def __init__(self, store: Store) -> None:
        self.store = store
        self.q: list[str] = []
        self.cv = threading.Condition()
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="credit-worker",
                                        daemon=True)
        self._checker = None   # 懒启动，作业来了才开浏览器

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        with self.cv:
            self._stop = True
            self.cv.notify_all()
        self._thread.join(timeout=10)
        if self._checker is not None:
            with contextlib.suppress(Exception):
                self._checker.__exit__(None, None, None)
            self._checker = None

    def submit(self, job_id: str) -> None:
        with self.cv:
            self.q.append(job_id)
            self.cv.notify()

    def _run(self) -> None:
        while True:
            with self.cv:
                while not self.q and not self._stop:
                    self.cv.wait(timeout=1.0)
                if self._stop:
                    return
                job_id = self.q.pop(0)
            try:
                self._process(job_id)
            except Exception as e:               # worker 不能死
                log.exception("作业 %s 未捕获异常", job_id)
                self.store.set_state(job_id, "failed", error=f"{type(e).__name__}: {e}"[:500])

    def _ensure_checker(self, req: dict):
        if self._checker is None:
            log.info("启动浏览器（首次作业）")
            self._checker = credit_check.CreditChecker(
                headful=False,
                min_delay=float(req.get("min_delay", 3.5)),
                max_delay=float(req.get("max_delay", 6.5)))
            self._checker.__enter__()
        else:
            # 每个作业的间隔参数可能不同，直接改实例属性
            self._checker.min_delay = float(req.get("min_delay", 3.5))
            self._checker.max_delay = float(req.get("max_delay", 6.5))
        return self._checker

    def _process(self, job_id: str) -> None:
        j = self.store.get_job(job_id)
        if not j:
            log.error("作业不存在：%s", job_id)
            return
        req = __import__("json").loads(j["request"])
        names: list[str] = req["names"]
        self.store.set_state(job_id, "running")
        log.info("作业 %s 开始，共 %d 家", job_id, len(names))

        try:
            checker = self._ensure_checker(req)
        except BaseException as e:
            msg = f"浏览器启动失败：{type(e).__name__}: {e}"[:500]
            self.store.set_state(job_id, "failed", error=msg)
            _sync_schedule(self.store, job_id, "failed", msg)
            log.error("浏览器启动失败：%s", e)
            return

        rows: list[dict] = []
        try:
            for i, name in enumerate(names):
                log.info("  [%d/%d] %s", i + 1, len(names), name)
                row = checker.check_one(name)
                rows.append(row)
                self.store.add_result(job_id, i, row)
                self.store.bump_finished(job_id)
                if i < len(names) - 1:
                    checker.sleep_between()
        except BaseException as e:
            log.exception("作业 %s 抓取中断", job_id)
            msg = f"抓取中断：{type(e).__name__}: {e}"[:500]
            self.store.set_state(job_id, "failed", error=msg)
            _sync_schedule(self.store, job_id, "failed", msg)
            # 已抓到的部分仍写 CSV，便于排查
            self._write_csv(job_id, rows)
            return

        csv_path = self._write_csv(job_id, rows)
        self.store.set_state(job_id, "done", csv_path=str(csv_path))
        log.info("作业 %s 完成，CSV=%s", job_id, csv_path)

        self._maybe_notify(job_id, rows, req)
        # 放在通知之后：notify_error 是 _maybe_notify 写的，先回写就永远读不到
        _sync_schedule(self.store, job_id, "done")

    def _write_csv(self, job_id: str, rows: list[dict]) -> Path:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        p = OUT_DIR / f"{job_id}.csv"
        fields = ["输入名称", "匹配名称", "统一社会信用代码", "登记状态", "判定",
                  "主体类型", "数据源", "记录数", "详情URL", "备注"]
        with p.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fields})
        return p

    def _maybe_notify(self, job_id: str, rows: list[dict], req: dict) -> None:
        webhook = (req.get("webhook") or "").strip()
        mail_to = (req.get("mail_to") or "").strip()
        if not webhook and not mail_to:
            log.info("作业 %s 未配置推送通道，跳过", job_id)
            return
        title, md, need = notify.build_report(rows)
        # need 现在含 unknown（名称不精确等），所以「没有 need」才等价于
        # 全部 ok；否则一批写错的名字会静默通过。
        if not need and not req.get("always_notify"):
            log.info("作业 %s 全部正常，跳过推送", job_id)
            return
        errors = []
        if webhook:
            try:
                notify.send_webhook(webhook, title, md)
            except Exception as e:
                errors.append(f"webhook: {e}")
                log.error("webhook 推送失败：%s", e)
        if mail_to:
            host = os.getenv("SMTP_HOST", "")
            if not host:
                errors.append("mail: 未配置 SMTP_HOST（在 .env 里设置后重启服务）")
            else:
                try:
                    use_ssl = os.getenv("SMTP_SSL", "").lower() in ("1", "true", "yes") \
                        or os.getenv("SMTP_PORT", "587") == "465"
                    # 用 notify.parse_addrs 而不是 split(",")：库里可能存着
                    # 老作业（写入 CheckRequest 校验之前）用中文逗号分隔的地址。
                    addrs = notify.parse_addrs(mail_to)
                    if not addrs:
                        raise ValueError(f"收件人解析为空：{mail_to!r}")
                    notify.send_mail(
                        host, int(os.getenv("SMTP_PORT", "465" if use_ssl else "587")),
                        os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASS", ""),
                        addrs, title, md, True, use_ssl=use_ssl)
                except Exception as e:
                    errors.append(f"mail: {e}")
                    log.error("邮件发送失败：%s", e)
        if not errors:
            self.store.mark_notified(job_id)
            log.info("作业 %s 推送成功", job_id)
        else:
            # 失败原因必须落库并出现在作业详情里。只写日志的问题：
            # 服务是后台进程，用户看到的永远是「未推送」，无从判断是
            # 还没跑完、被跳过，还是配错了邮箱。
            joined = "；".join(errors)
            self.store.mark_notified(job_id, error=joined)
            log.error("作业 %s 推送部分失败：%s", job_id, joined)



# ============================================================ FastAPI
@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    logkit.setup_logging("api")
    store = Store(DB_TARGET)
    worker = Worker(store)
    worker.start()
    app.state.store = store
    app.state.worker = worker
    # 调度线程：补算缺失的 next_run_at，然后开始轮询。
    # 必须在 worker.start() 之后——它触发的作业要立刻有人消费。
    fixed = sched.ensure_next_run(store)
    scheduler = sched.Scheduler(store, worker)
    scheduler.start()
    app.state.scheduler = scheduler
    log.info("服务启动：db=%s(%s) out=%s 调度补算 %d 条",
             DB_TARGET, store.backend, OUT_DIR, fixed)
    try:
        yield
    finally:
        log.info("服务关闭，等待调度与 worker 退出")
        scheduler.stop()
        await asyncio.get_running_loop().run_in_executor(None, worker.stop)


# ============================================================ 前端静态托管
# 生产态让 FastAPI 直接托管 frontend/dist，与 /api 同源：
#   - 浏览器看到的是 http://127.0.0.1:8000/，不存在 CORS（后端没配 CORS 中间件）；
#   - 页面路径（/overview、/jobs、/jobs/<id>）由 SPA fallback 回 index.html，
#     交给 vue-router 解析；接口全部在 /api 下，两者不抢路径。
# 开发态不走这里：`cd frontend && npm run dev` 用 vite 自带 proxy 转发 /api。
#
# 目录不存在（新克隆还没构建、或只想跑 API）时静默降级——不装不影响接口，
# 装了就多一个控制台，所以这里是「有则用」而不是「必须」。
DIST_DIR = Path(os.getenv("CREDIT_DIST", BASE_DIR / "frontend" / "dist"))

INDEX_HTML = DIST_DIR / "index.html"


def _mount_frontend(app: FastAPI) -> bool:
    """把 dist 挂到 /。返回是否真的挂上。"""
    if not INDEX_HTML.is_file():
        log.info("未找到前端产物 %s，仅提供 API（cd frontend && npm run build 可生成）",
                 INDEX_HTML)
        return False

    # 带指纹的静态资源长期缓存：Vite 每次构建都会换文件名，
    # 缓存旧名不会导致「改了不生效」。
    app.mount(
        "/assets",
        StaticFiles(directory=str(DIST_DIR / "assets")),
        name="assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        """SPA 深链兜底。

        vue-router 用 history 模式，/jobs/<id> 这类地址服务器上并没有对应文件，
        直接 404 会让用户一刷新就丢页面。这里统一回 index.html，由前端路由解析。

        三条边界，缺一条就会出事故：
          1) 必须是最后注册的路由——真实接口都在 /api 前缀下（/api/health、
             /api/jobs/...）且已先注册，Starlette 顺序匹配，走不到这里；
             反过来先注册它，接口就全被吞成 HTML。
          2) 只接受 GET——POST /api/jobs 若被这里接住会静默返回 HTML，
             前端 openapi-fetch 解析不了，报错信息还会误导人。
          3) 不做「/api/xxx 是接口所以不该兜底」的前缀特殊处理——顺序匹配
             已经保证接口先答；唯一的显式排除是下面这行 `api/` 前缀，
             它是给「已删接口的旧链接」和「typo」留的，正常请求碰不到。
             反过来说，页面路径 /jobs、/jobs/<id> 与接口同名也不会冲突了，
             因为接口在 /api/jobs 下——这正是加前缀要解决的问题。
        """
        if full_path.startswith(("api/", "docs", "redoc", "openapi.json")):
            raise HTTPException(404, "接口路径不存在（不应由前端兜底接管）")
        target = DIST_DIR / full_path
        # 目录穿越防护：full_path 来自 URL，可能是 %2e%2e%2f 编码后的 ..
        if full_path and not _is_within(target, DIST_DIR):
            raise HTTPException(404, "非法路径")
        if full_path and target.is_file():
            return FileResponse(str(target))
        return FileResponse(str(INDEX_HTML))

    log.info("前端控制台已托管：%s", DIST_DIR)
    return True


def _is_within(path: Path, root: Path) -> bool:
    """判断 path 是否落在 root 之内（用 resolve 后的真实路径，防 .. 穿越）。"""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


app = FastAPI(
    title="营业执照状态巡检 API",
    version="1.0.0",
    description=(
        "批量查询企业登记状态（存续/注销/吊销）并在异常时推送提醒。\n\n"
        "**使用流程**：`POST /api/jobs` 提交 → `GET /api/jobs/{id}` 轮询 → "
        "`GET /api/jobs/{id}/results` 取明细 → `GET /api/jobs/{id}/csv` 下载。\n\n"
        "**注意**：抓取由单 worker 串行执行，50 家约需 9–20 分钟。"
        "这是刻意的——目标站有瑞数风控，并发等于自伤。"
    ),
    lifespan=lifespan,
)

# 所有业务接口统一挂 /api 前缀。
# 这不是风格问题：前端 SPA 的页面路径是 /jobs（作业列表页）与 /jobs/:jobId
# （详情页），后端接口若也叫 /jobs，同一条路径不可能既回 HTML 又回 JSON——
# 生产态由本文件托管 dist 时，用户直接打开或刷新 /jobs 会拿到接口 JSON
# （列表为空时是一个 JSON 查看器），两个核心页面等于打不开。
# dev 态以前没暴露，是因为 vite proxy 的 rewrite 把 /api 剥掉了，
# 请求 /api/jobs 落到后端 /jobs 上正好对齐；生产态没有这层 rewrite 就崩。
router = APIRouter(prefix="/api")


# 注意：路由挂载与前端托管都在文件末尾（所有 @router.get 之后）。
# Starlette 按注册顺序匹配，"先挂 / 再声明 /api/health" 会让接口被兜底接走。


@router.get("/health", summary="健康检查", response_model=HealthResponse,
            tags=["system"])
def health(request: Request) -> HealthResponse:
    store: Store = request.app.state.store
    try:
        # 不能写 c.execute("SELECT 1")：pymysql 的 Connection 没有 execute，
        # 那种写法只在 SQLite 下成立，会让 MySQL 部署永远 db=false。
        with store._conn() as c:
            store._fetchall(c, "SELECT 1 AS ok")
        db_ok = True
    except Exception:
        db_ok = False
    return HealthResponse(status="ok" if db_ok else "degraded", db=db_ok,
                          queued=len(request.app.state.worker.q), time=_now())


@router.post("/jobs", response_model=JobSummary, status_code=202,
             summary="提交巡检作业", tags=["jobs"])
def create_job(req: CheckRequest, request: Request) -> JobSummary:
    store: Store = request.app.state.store
    job_id = store.create_job(req.names, req.model_dump())
    request.app.state.worker.submit(job_id)
    log.info("收到作业 %s：%d 家", job_id, len(req.names))
    return _to_summary(store.get_job(job_id), store)


@router.get("/jobs", response_model=list[JobSummary], summary="作业列表",
            tags=["jobs"])
def list_jobs(request: Request,
              limit: int = Query(50, ge=1, le=500)) -> list[JobSummary]:
    store: Store = request.app.state.store
    return [_to_summary(j, store) for j in store.list_jobs(limit)]


def _parse_request(raw: str | None) -> JobRequest | None:
    """把 ``jobs.request`` 的 JSON 还原成 JobRequest。

    容错优先：这是展示用的回显字段，库里可能躺着历史作业/半截 JSON，
    解析失败就当没有——绝不能因为回显失败把整个详情页打成 500。
    """
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    try:
        return JobRequest(**obj)
    except ValidationError:
        # 老作业可能带已废弃的键，逐项尽力还原而不是整块丢弃
        keep = {k: obj[k] for k in JobRequest.model_fields if k in obj}
        try:
            return JobRequest(**keep)
        except ValidationError:
            return None


@router.get("/jobs/{job_id}", response_model=JobDetail, summary="作业状态",
            tags=["jobs"])
def get_job(job_id: str, request: Request) -> JobDetail:
    store: Store = request.app.state.store
    j = store.get_job(job_id)
    if not j:
        raise HTTPException(404, f"作业不存在：{job_id}")
    s = _to_summary(j, store)
    items = [_row_to_item(r) for r in store.get_results(job_id)]
    return JobDetail(**s.model_dump(), results=items,
                     request=_parse_request(j.get("request")))


@router.get("/jobs/{job_id}/results", response_model=list[ResultItem],
            summary="结果明细（可按判定过滤）", tags=["jobs"])
def get_results(job_id: str, request: Request,
                verdict: Verdict | None = Query(None, description="只看某类判定")):
    store: Store = request.app.state.store
    if not store.get_job(job_id):
        raise HTTPException(404, f"作业不存在：{job_id}")
    items = [_row_to_item(r) for r in store.get_results(job_id)]
    return [i for i in items if verdict is None or i.verdict == verdict]


@router.get(
    "/jobs/{job_id}/csv",
    summary="下载 CSV",
    tags=["jobs"],
    response_class=FileResponse,
    responses={
        200: {
            "description": "巡检结果 CSV（UTF-8 with BOM，Excel 直接双击不乱码）",
            "content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}},
        },
        404: {"description": "作业不存在"},
        409: {"description": "作业未完成，CSV 尚未生成"},
    },
)
def download_csv(job_id: str, request: Request):
    store: Store = request.app.state.store
    j = store.get_job(job_id)
    if not j:
        raise HTTPException(404, f"作业不存在：{job_id}")
    if not j.get("csv_path") or not Path(j["csv_path"]).is_file():
        raise HTTPException(409, "CSV 尚未生成（作业未完成）")
    return FileResponse(j["csv_path"], media_type="text/csv; charset=utf-8",
                        filename=f"{job_id}.csv")


@router.delete("/jobs/{job_id}", summary="取消排队中的作业",
               response_model=CancelResponse, tags=["jobs"])
def cancel_job(job_id: str, request: Request) -> CancelResponse:
    store: Store = request.app.state.store
    j = store.get_job(job_id)
    if not j:
        raise HTTPException(404, f"作业不存在：{job_id}")
    if j["state"] != "queued":
        raise HTTPException(409, f"作业状态为 {j['state']}，只能取消 queued")
    w: Worker = request.app.state.worker
    with w.cv:
        if job_id in w.q:
            w.q.remove(job_id)
    store.set_state(job_id, "cancelled", error="用户取消")
    # 调度触发的作业被取消，调度行不能一直显示「排队中」
    _sync_schedule(store, job_id, "cancelled", "用户取消")
    return CancelResponse(job_id=job_id, state="cancelled")


# ============================================================ 定时巡检
# 这一组接口解决的是一个具体抱怨：「营业执照没有能设置每日/每周/每月检查的地方」。
#
# 此前「定时」只有 Windows 任务计划程序那条路（install_schedule_task.ps1），
# 配置在命令行里、状态在任务计划程序里、历史在 logs/ 文本里 —— 三样都不在应用内，
# 等于业务人员没有任何可见入口。这里把调度变成一等公民：配置进库、由网页读写、
# 执行结果就是普通作业（复用既有详情页与通知链路）。
#
# 与「手动提交」共用同一个 worker，所以定时任务和人工提交天然串行排队，
# 不会出现两个浏览器同时打目标站（那会直接触发瑞数风控）。
def _schedule_out(s: dict) -> ScheduleOut:
    """库记录 → 对外模型。名单从 JSON 文本还原成数组。"""
    try:
        names = json.loads(s.get("names") or "[]")
        if not isinstance(names, list):
            names = []
    except ValueError:
        names = []
    return ScheduleOut(
        schedule_id=s["schedule_id"], name=s["name"], enabled=s["enabled"],
        freq=s["freq"], at_time=s["at_time"], weekday=s["weekday"],
        day=s["day"], names=names, webhook=s.get("webhook"),
        mail_to=s.get("mail_to"), always_notify=s["always_notify"],
        min_delay=s["min_delay"], max_delay=s["max_delay"],
        next_run_at=s.get("next_run_at"), last_run_at=s.get("last_run_at"),
        last_job_id=s.get("last_job_id"), last_state=s.get("last_state"),
        last_error=s.get("last_error"), created_at=s.get("created_at") or "",
        updated_at=s.get("updated_at") or "",
        human=sched.describe(s["freq"], s["at_time"],
                             weekday=s["weekday"], day=s["day"]),
        count=len(names))


async def _restart_scheduler(request: Request) -> None:
    """配置变更后让调度线程立刻按新配置重算（而不是等下一轮轮询）。

    不是必须的——轮询每 30 秒本来就会读到新配置；但「改了时间、界面上
    next_run_at 还是旧值」会让人以为没保存成功，所以这里主动跑一轮 tick 并把
    next_run_at 重算一遍，让界面立刻自证。
    """
    s: sched.Scheduler | None = getattr(request.app.state, "scheduler", None)
    if s is None:
        return
    await asyncio.get_running_loop().run_in_executor(None, s.tick)


@router.get("/schedules", response_model=list[ScheduleOut],
            summary="定时巡检列表", tags=["schedules"])
def list_schedules(request: Request) -> list[ScheduleOut]:
    store: Store = request.app.state.store
    return [_schedule_out(s) for s in store.list_schedules()]


@router.post("/schedules", response_model=ScheduleOut, status_code=201,
             summary="新建定时巡检", tags=["schedules"])
async def create_schedule(req: ScheduleIn, request: Request) -> ScheduleOut:
    store: Store = request.app.state.store
    fields = req.model_dump()
    fields["names"] = json.dumps(req.names, ensure_ascii=False)
    fields["next_run_at"] = sched._iso(
        sched.compute_next(req.freq, req.at_time,
                           weekday=req.weekday, day=req.day))
    sid = store.add_schedule(fields)
    log.info("新调度 %s：%s（下次 %s）", sid, fields["name"],
             fields["next_run_at"])
    await _restart_scheduler(request)
    return _schedule_out(store.get_schedule(sid))


@router.put("/schedules/{schedule_id}", response_model=ScheduleOut,
            summary="修改定时巡检", tags=["schedules"])
async def update_schedule(schedule_id: str, req: ScheduleIn,
                          request: Request) -> ScheduleOut:
    store: Store = request.app.state.store
    if not store.get_schedule(schedule_id):
        raise HTTPException(404, f"调度不存在：{schedule_id}")
    fields = req.model_dump()
    fields["names"] = json.dumps(req.names, ensure_ascii=False)
    # 改了频率/时刻就必须重算下次触发，否则会按旧节奏继续跑
    fields["next_run_at"] = sched._iso(
        sched.compute_next(req.freq, req.at_time,
                           weekday=req.weekday, day=req.day))
    store.update_schedule(schedule_id, fields)
    log.info("调度 %s 已更新（下次 %s）", schedule_id, fields["next_run_at"])
    await _restart_scheduler(request)
    return _schedule_out(store.get_schedule(schedule_id))


@router.post("/schedules/{schedule_id}/toggle", response_model=ScheduleOut,
             summary="启用/停用定时巡检", tags=["schedules"])
async def toggle_schedule(schedule_id: str, request: Request,
                          enabled: bool = Query(..., description="目标状态")) -> ScheduleOut:
    store: Store = request.app.state.store
    s = store.get_schedule(schedule_id)
    if not s:
        raise HTTPException(404, f"调度不存在：{schedule_id}")
    fields: dict[str, Any] = {"enabled": enabled}
    if enabled:
        # 重新启用时按「现在」重算，避免停用两周后一开就补跑一个过期时刻
        fields["next_run_at"] = sched._iso(
            sched.compute_next(s["freq"], s["at_time"],
                               weekday=s["weekday"], day=s["day"]))
    store.update_schedule(schedule_id, fields)
    await _restart_scheduler(request)
    return _schedule_out(store.get_schedule(schedule_id))


@router.post("/schedules/{schedule_id}/run", response_model=JobSummary,
             status_code=202, summary="立刻跑一次（不影响下次时间）",
             tags=["schedules"])
def run_schedule_now(schedule_id: str, request: Request) -> JobSummary:
    """手动触发一条调度。

    刻意**不动** ``next_run_at``：这是「临时想现在看一下」的需求，
    不该顺延正常节奏——否则周五点了下「立刻执行」，周一的定时就不跑了。
    """
    store: Store = request.app.state.store
    s = store.get_schedule(schedule_id)
    if not s:
        raise HTTPException(404, f"调度不存在：{schedule_id}")
    names = sched.Scheduler._names_of(s)
    if not names:
        raise HTTPException(409, "该调度的企业名单为空，无法执行")
    req = {"names": names, "webhook": s.get("webhook") or None,
           "mail_to": s.get("mail_to") or None,
           "always_notify": bool(s.get("always_notify")),
           "min_delay": float(s.get("min_delay") or 3.5),
           "max_delay": float(s.get("max_delay") or 6.5),
           "source": "schedule", "schedule_id": schedule_id,
           "schedule_name": s.get("name") or ""}
    job_id = store.create_job(names, req)
    w: Worker = request.app.state.worker
    w.submit(job_id)
    store.set_schedule_result(schedule_id, last_job_id=job_id,
                              last_state="queued", last_error="")
    log.info("调度 %s 手动触发作业 %s（%d 家）", schedule_id, job_id, len(names))
    return _to_summary(store.get_job(job_id), store)


@router.delete("/schedules/{schedule_id}",
               response_model=ScheduleDeleteResponse,
               summary="删除定时巡检", tags=["schedules"])
async def delete_schedule(schedule_id: str,
                          request: Request) -> ScheduleDeleteResponse:
    store: Store = request.app.state.store
    if not store.get_schedule(schedule_id):
        raise HTTPException(404, f"调度不存在：{schedule_id}")
    store.delete_schedule(schedule_id)
    log.info("调度 %s 已删除", schedule_id)
    return ScheduleDeleteResponse(schedule_id=schedule_id)


@router.get("/schedules/preview", response_model=SchedulePreview,
            summary="预览改配置后的下次触发时间（不落库）",
            tags=["schedules"])
def preview_schedule(freq: Literal["daily", "weekly", "monthly"] = Query(...),
                     at_time: str = Query(..., description="HH:MM"),
                     weekday: int = Query(0, ge=0, le=6),
                     day: int = Query(1, ge=1, le=28)) -> SchedulePreview:
    """编辑弹窗里「下次：周三 09:30」这样的实时预览。

    **刻意不落库**：用户还在填表，写库会产生一堆半成品配置；
    而且没有预览时，用户改完保存才能看到「居然排到下个月了」。
    """
    if not _TIME_RE.match((at_time or "").strip()):
        raise HTTPException(422, f"触发时刻需形如 09:30，收到 {at_time!r}")
    try:
        nxt = sched.compute_next(freq, at_time.strip(), weekday=weekday, day=day)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return SchedulePreview(next_run_at=sched._iso(nxt),
                           human=sched.describe(freq, at_time.strip(),
                                                weekday=weekday, day=day))


# ============================================================ 路由与前端托管
# 顺序不能调：先注册 /api/* 接口，再挂前端兜底。
# Starlette 按注册顺序逐条匹配，先挂 "/" 会让 /api/health 全被兜底接走并返回 HTML。
app.include_router(router)
_mount_frontend(app)

