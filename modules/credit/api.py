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
import logging
import os
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
from pydantic import BaseModel, Field, field_validator

import credit_check
import logkit
import notify

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


class JobDetail(JobSummary):
    results: list[ResultItem] = []


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

    def mark_notified(self, job_id: str) -> None:
        with self._conn() as c:
            self._execute(c, "UPDATE jobs SET notified=1 WHERE job_id=?", (job_id,))

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
        notified=bool(j["notified"]))


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
            self.store.set_state(job_id, "failed",
                                 error=f"浏览器启动失败：{type(e).__name__}: {e}"[:500])
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
            self.store.set_state(job_id, "failed",
                                 error=f"抓取中断：{type(e).__name__}: {e}"[:500])
            # 已抓到的部分仍写 CSV，便于排查
            self._write_csv(job_id, rows)
            return

        csv_path = self._write_csv(job_id, rows)
        self.store.set_state(job_id, "done", csv_path=str(csv_path))
        log.info("作业 %s 完成，CSV=%s", job_id, csv_path)

        self._maybe_notify(job_id, rows, req)

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
                errors.append("mail: 未配置 SMTP_HOST")
            else:
                try:
                    use_ssl = os.getenv("SMTP_SSL", "").lower() in ("1", "true", "yes") \
                        or os.getenv("SMTP_PORT", "587") == "465"
                    notify.send_mail(
                        host, int(os.getenv("SMTP_PORT", "465" if use_ssl else "587")),
                        os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASS", ""),
                        [x.strip() for x in mail_to.split(",") if x.strip()],
                        title, md, True, use_ssl=use_ssl)
                except Exception as e:
                    errors.append(f"mail: {e}")
                    log.error("邮件发送失败：%s", e)
        if not errors:
            self.store.mark_notified(job_id)
            log.info("作业 %s 推送成功", job_id)
        else:
            log.error("作业 %s 推送部分失败：%s", job_id, "; ".join(errors))


# ============================================================ FastAPI
@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    logkit.setup_logging("api")
    store = Store(DB_TARGET)
    worker = Worker(store)
    worker.start()
    app.state.store = store
    app.state.worker = worker
    log.info("服务启动：db=%s(%s) out=%s", DB_TARGET, store.backend, OUT_DIR)
    try:
        yield
    finally:
        log.info("服务关闭，等待 worker 退出")
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


@router.get("/jobs/{job_id}", response_model=JobDetail, summary="作业状态",
            tags=["jobs"])
def get_job(job_id: str, request: Request) -> JobDetail:
    store: Store = request.app.state.store
    j = store.get_job(job_id)
    if not j:
        raise HTTPException(404, f"作业不存在：{job_id}")
    s = _to_summary(j, store)
    items = [_row_to_item(r) for r in store.get_results(job_id)]
    return JobDetail(**s.model_dump(), results=items)


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
    return CancelResponse(job_id=job_id, state="cancelled")


# ============================================================ 路由与前端托管
# 顺序不能调：先注册 /api/* 接口，再挂前端兜底。
# Starlette 按注册顺序逐条匹配，先挂 "/" 会让 /api/health 全被兜底接走并返回 HTML。
app.include_router(router)
_mount_frontend(app)

