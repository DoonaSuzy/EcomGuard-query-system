# -*- coding: utf-8 -*-
"""
服务自检：确认「巡检本身还能不能跑」，把「服务坏了」变成一封邮件
==================================================================
背景（为什么要这个文件）
------------------------
`credit_check.py` 的产物只能回答「企业状态怎么样」，回答不了
「巡检服务自己是不是好的」：

  - 浏览器组件被卸载 → 巡检直接崩，但崩在计划任务里，没人看见；
  - SMTP 凭据过期 → 邮件发不出去，而「发不出去」这件事本身没法用邮件上报；
  - 名单文件被清空 → 巡检跑完 0 家，报告「全部正常」，是最危险的假绿；
  - 任务被禁用/机器长时间关机 → 根本没有任何输出，静默失效。

所以把「服务可运行性」拆成一组可离线断言的自检项，每日随巡检一起跑，
任一 **致命项** 失败就并入当日告警邮件；邮件通道自己坏了则走 webhook 兜底
（见 notify.py 的降级逻辑）。

设计约束
--------
1. **可离线测试**：所有真实 I/O（HTTP、TCP、浏览器版本探测）都通过
   `probes` 注入，测试传假探针即可，不联网、不开浏览器；
2. **只读**：除 `--mark-run` 写心跳文件外不改任何状态；
3. **不拖垮巡检**：任何一项抛异常都被捕获并记为该项失败（fail-closed），
   绝不让自检自己崩掉整条链路。

用法
----
    python healthcheck.py                       # 人读表格
    python healthcheck.py --json data/health.json   # 机器读，供 notify 并入邮件
    python healthcheck.py --deep                # 额外做 SMTP 登录、浏览器启动
    python healthcheck.py --mark-run            # 记录本次运行时间（心跳）

退出码：0=无致命项失败（允许有警告） / 1=至少一个致命项失败 / 2=参数/环境错误
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import smtplib
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import logkit

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
HEARTBEAT_PATH = DATA_DIR / "last_run.json"
JOBS_DB_PATH = Path(os.getenv("CREDIT_DB", DATA_DIR / "jobs.db"))

#: 目标站可达性探测地址。用首页而不是私有 API：首页 200 即可说明网络与站点在线，
#: 私有 API 还需要 vcode 会话，不适合做「能不能连上」的判断。
SITE_URL = "https://www.creditchina.gov.cn/"
HTTP_TIMEOUT = 12
SMTP_TIMEOUT = 8
#: 心跳超过这个小时数就认为「日常巡检没有按时执行」。
#: 36h = 24h 周期 + 12h 容差（允许一次因关机/休眠错过、第二天补跑）。
HEARTBEAT_MAX_HOURS = 36.0
MIN_PYTHON = (3, 10)

#: 运行时必需的第三方包（pip 名 → import 名）。用 find_spec 探测，
#: 不真正 import：ddddocr 会加载 onnxruntime，import 一次要几百毫秒，
#: 而且会占住模型内存——自检只需要知道「装没装」。
RUNTIME_MODULES = {
    "ddddocr": "ddddocr",
    "camoufox": "camoufox",
    "Pillow": "PIL",
    "requests": "requests",
}

FATAL = "fatal"   # 失败即视为巡检不可用
WARN = "warn"     # 失败只提示，不阻断（如心跳缺失、作业库不存在）


@dataclass
class CheckResult:
    """单项自检结论。字段保持可 JSON 序列化，便于并进邮件报告。"""
    id: str
    name: str
    severity: str
    ok: bool
    detail: str
    ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- 单项自检
# 每个函数都是「纯判定」：输入显式，返回 (ok, detail)。
# 真实 I/O 通过参数或 probes 传入，测试可完全离线。


def check_python(min_version: tuple[int, int] = MIN_PYTHON) -> tuple[bool, str]:
    v = sys.version_info
    ok = (v.major, v.minor) >= min_version
    detail = f"{v.major}.{v.minor}.{v.micro}"
    if not ok:
        detail += f"（要求 >= {min_version[0]}.{min_version[1]}）"
    return ok, detail


def check_venv(prefix: str | None = None,
               base_prefix: str | None = None) -> tuple[bool, str]:
    """确认运行在项目 venv 里。

    踩过的坑：直接用系统 Python 跑会缺 ddddocr（历史上还借用过
    camoufox-reverse-mcp 的 venv，对方重装就把 ddddocr 抹掉）。
    """
    p = sys.prefix if prefix is None else prefix
    b = getattr(sys, "base_prefix", sys.prefix) if base_prefix is None else base_prefix
    if p == b:
        return False, f"当前不在 venv 内（prefix={p}）"
    return True, str(p)


def check_deps(modules: dict[str, str] | None = None,
               finder: Callable[[str], Any] | None = None) -> tuple[bool, str]:
    mods = RUNTIME_MODULES if modules is None else modules
    find = importlib.util.find_spec if finder is None else finder
    missing: list[str] = []
    for pip_name, mod_name in mods.items():
        try:
            if find(mod_name) is None:
                missing.append(f"{pip_name}({mod_name})")
        except (ImportError, ValueError):
            missing.append(f"{pip_name}({mod_name})")
    if missing:
        return False, "缺少依赖：" + ", ".join(missing)
    return True, f"{len(mods)} 个运行时依赖齐备"


def check_browser(verstr_fn: Callable[[], str] | None = None) -> tuple[bool, str]:
    """Camoufox 浏览器二进制是否已安装（只读版本号，不启动浏览器）。

    只看 pip 包装没装是不够的：`pip install camoufox` 之后还要
    `camoufox fetch` 下载几百 MB 的浏览器本体，缺这一步时
    CreditChecker.__enter__ 才炸——那时已经在计划任务里，没人看得见。
    """
    if verstr_fn is None:
        def verstr_fn() -> str:                       # noqa: D401
            from camoufox.pkgman import installed_verstr
            return installed_verstr()
    try:
        ver = verstr_fn()
    except Exception as e:                          # CamoufoxNotInstalled 等
        return False, f"浏览器未就绪：{type(e).__name__}: {e}"
    if not ver:
        return False, "浏览器版本为空（可能未执行 camoufox fetch）"
    return True, f"camoufox {ver}"


def check_site(url: str = SITE_URL, timeout: int = HTTP_TIMEOUT,
               opener: Callable[..., Any] | None = None) -> tuple[bool, str]:
    """目标站可达性。4xx 也算「连得上」（403 是反爬拒绝，不是网络故障）。"""
    open_fn = opener
    if open_fn is None:
        def open_fn(req):                              # noqa: D401
            return urllib.request.urlopen(req, timeout=timeout)
    req = urllib.request.Request(
        url, method="GET",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/131.0.0.0 Safari/537.36"})
    try:
        with open_fn(req) as resp:
            code = getattr(resp, "status", None) or getattr(resp, "code", 0)
            return True, f"HTTP {code}"
    except urllib.error.HTTPError as e:
        # 服务端有响应 = 站点在线，只是这一次被拒
        return True, f"HTTP {e.code}（站点在线，请求被拒）"
    except Exception as e:
        return False, f"不可达：{type(e).__name__}: {e}"


def check_smtp_config(env: dict[str, str] | None = None) -> tuple[bool, str]:
    """邮件凭据是否齐全。缺任何一项，告警邮件都发不出去。"""
    e = os.environ if env is None else env
    need = {"SMTP_HOST": e.get("SMTP_HOST", ""),
            "SMTP_USER": e.get("SMTP_USER", ""),
            "SMTP_PASS": e.get("SMTP_PASS", "")}
    missing = [k for k, v in need.items() if not (v or "").strip()]
    if missing:
        return False, "缺少配置：" + ", ".join(missing)
    port = (e.get("SMTP_PORT") or "465").strip()
    return True, f"{need['SMTP_HOST']}:{port} 用户={need['SMTP_USER']}"


def check_recipients(env: dict[str, str] | None = None) -> tuple[bool, str]:
    """两组收件人至少要有一组，否则「每日给业务、每月给主管」形同虚设。"""
    e = os.environ if env is None else env
    biz = (e.get("MAIL_TO_BUSINESS") or e.get("MAIL_TO") or "").strip()
    mgr = (e.get("MAIL_TO_MANAGER") or e.get("MAIL_TO") or "").strip()
    if not biz and not mgr:
        return False, "MAIL_TO_BUSINESS 与 MAIL_TO_MANAGER 都为空"
    parts = []
    parts.append(f"业务={'已配' if biz else '缺失'}")
    parts.append(f"主管={'已配' if mgr else '缺失'}")
    return True, "，".join(parts)


def check_smtp_tcp(host: str, port: int, timeout: int = SMTP_TIMEOUT,
                   connector: Callable[..., Any] | None = None,
                   user: str = "", password: str = "",
                   use_ssl: bool | None = None,
                   deep: bool = False) -> tuple[bool, str]:
    """SMTP 连通性。

    浅检只做 TCP 连接（验证网络与端口）；`deep=True` 才真正握手 + 登录，
    因为登录会消耗服务商的失败计数，不宜每次巡检都做。
    """
    if not host:
        return False, "SMTP_HOST 为空"
    ssl_mode = (port == 465) if use_ssl is None else use_ssl
    if connector is not None:
        try:
            connector(host, port, timeout)
        except Exception as e:
            return False, f"连接失败：{type(e).__name__}: {e}"
        return True, f"TCP {host}:{port} 可连"
    try:
        if not deep:
            with socket.create_connection((host, port), timeout=timeout):
                return True, f"TCP {host}:{port} 可连"
        cls = smtplib.SMTP_SSL if ssl_mode else smtplib.SMTP
        with cls(host, port, timeout=timeout) as s:
            if not ssl_mode:
                try:
                    s.starttls()
                except Exception:
                    pass
            if user:
                s.login(user, password)
        return True, f"SMTP 登录成功 {host}:{port}"
    except Exception as e:
        return False, f"连接失败：{type(e).__name__}: {e}"


def read_name_list(path: Path) -> list[str]:
    """按与 credit_check 完全一致的规则读名单（跳过空行与 # 注释）。"""
    if not Path(path).is_file():
        return []
    raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    return [ln.strip() for ln in raw.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def check_companies(path: Path, reader: Callable[[Path], list[str]] | None = None
                    ) -> tuple[bool, str]:
    """名单存在且非空。

    这是最容易被忽略的致命项：名单被清空后巡检「成功」跑完 0 家，
    报告「全部正常」，比报错更危险。
    """
    rd = read_name_list if reader is None else reader
    p = Path(path)
    if not p.is_file():
        return False, f"名单不存在：{p}"
    try:
        names = rd(p)
    except Exception as e:
        return False, f"名单读取失败：{type(e).__name__}: {e}"
    if not names:
        return False, f"名单为空（{p}），巡检会跑 0 家并报「全部正常」"
    return True, f"{len(names)} 家"


def check_writable(dirs: list[Path] | None = None) -> tuple[bool, str]:
    """目录可写：日志与结果都落在项目目录下，只读会让巡检静默降级。"""
    targets = [BASE_DIR, BASE_DIR / "logs"] if dirs is None else list(dirs)
    bad: list[str] = []
    for d in targets:
        d = Path(d)
        try:
            d.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".hc_", dir=str(d))
            os.close(fd)
            os.unlink(tmp)
        except OSError as e:
            bad.append(f"{d}（{e.strerror or e}）")
    if bad:
        return False, "不可写：" + "；".join(bad)
    return True, "，".join(str(Path(d)) for d in targets)


def check_heartbeat(path: Path | None = None,
                    max_hours: float = HEARTBEAT_MAX_HOURS,
                    now: datetime | None = None) -> tuple[bool, str]:
    """上次巡检时间是否还在容忍窗口内。

    这一项只能发现「跑晚了」，发现不了「一直没跑」——后者需要外部监控。
    这一点写进 README，不假装能做到。

    `path=None` 时才去读模块级 HEARTBEAT_PATH，**不能**把 HEARTBEAT_PATH
    写成默认参数值：默认值在函数定义时求值并固化，之后改模块变量对已定义
    的函数无效——测试里 monkeypatch 掉路径会静默失效（踩过）。
    """
    p = Path(path or HEARTBEAT_PATH)
    if not p.is_file():
        return True, "尚无心跳（首次运行）"
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        last = datetime.fromisoformat(str(payload.get("last_run_at", "")))
    except Exception as e:
        return False, f"心跳文件损坏：{type(e).__name__}: {e}"
    # 心跳里存的是带时区的本地时间；now 缺省时对齐到同一时区再相减，
    # 否则 aware 与 naive 混算会抛 TypeError（写成一行三元式时踩过）。
    if now is not None:
        cur = now
    elif last.tzinfo is not None:
        cur = datetime.now(last.tzinfo)
    else:
        cur = datetime.now()
    if (cur.tzinfo is None) != (last.tzinfo is None):
        last = last.replace(tzinfo=cur.tzinfo)
    gap = cur - last
    hours = gap.total_seconds() / 3600.0
    if hours > max_hours:
        return False, (f"距上次巡检 {hours:.1f} 小时（阈值 {max_hours:.0f}h），"
                       f"任务可能被禁用或机器长时间关机")
    return True, f"距上次 {hours:.1f} 小时"


def check_jobs_db(path: Path | None = None) -> tuple[bool, str]:
    """HTTP 服务的作业库是否可读（不存在不算失败：可能只用命令行）。"""
    p = Path(path or JOBS_DB_PATH)
    if not p.is_file():
        return True, "作业库未创建（仅用命令行时正常）"
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
        try:
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        finally:
            conn.close()
    except Exception as e:
        return False, f"作业库不可读：{type(e).__name__}: {e}"
    return True, str(p)


def write_heartbeat(path: Path | None = None, *,
                    ok: bool, failed: int, warn: int,
                    now: datetime | None = None) -> Path:
    """记录本次运行（原子写：先写临时文件再 replace，避免半截 JSON）。"""
    p = Path(path or HEARTBEAT_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = (now or datetime.now()).isoformat(timespec="seconds")
    payload = {"last_run_at": ts, "ok": ok, "failed": failed, "warn": warn}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return p


# ---------------------------------------------------------------- 组合执行
CHECK_TABLE: list[tuple[str, str, str, Callable[..., tuple[bool, str]]]] = [
    ("python", "Python 版本", FATAL, check_python),
    ("venv", "独立 venv", FATAL, check_venv),
    ("deps", "运行时依赖", FATAL, check_deps),
    ("browser", "浏览器本体", FATAL, check_browser),
    ("companies", "企业名单", FATAL, check_companies),
    ("writable", "目录可写", FATAL, check_writable),
    ("site", "目标站可达", FATAL, check_site),
    ("smtp_config", "邮件配置", FATAL, check_smtp_config),
    ("recipients", "收件人配置", WARN, check_recipients),
    ("smtp_tcp", "SMTP 连通", FATAL, check_smtp_tcp),
    ("jobs_db", "作业库", WARN, check_jobs_db),
    ("heartbeat", "上次运行", WARN, check_heartbeat),
]


def run_checks(*, env: dict[str, str] | None = None,
               companies: Path | None = None,
               deep: bool = False,
               only: set[str] | None = None,
               probes: dict[str, Callable[..., Any]] | None = None,
               now: datetime | None = None) -> list[CheckResult]:
    """执行全部自检项，返回逐项结果。

    参数
    ----
    env        环境变量映射（默认 os.environ；调用前应已 load_dotenv）
    companies  名单文件路径（默认项目下的 companies.txt）
    deep       是否做 SMTP 登录 + 浏览器启动等重探测
    only       只跑指定 id（调试用）
    probes     探针覆盖表，键为 check id 或探针名，值为可调用对象。
               测试用它在完全离线的条件下覆盖每项 I/O。
    now        注入当前时间，便于测心跳窗口
    """
    e = dict(os.environ if env is None else env)
    pr = dict(probes or {})
    company_path = Path(companies) if companies else (BASE_DIR / "companies.txt")

    host = (e.get("SMTP_HOST") or "").strip()
    port_raw = (e.get("SMTP_PORT") or "465").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 465
    use_ssl = (e.get("SMTP_SSL", "").strip().lower() in ("1", "true", "yes")) or port == 465

    kwargs: dict[str, dict[str, Any]] = {
        "python": {},
        "venv": {},
        "deps": {},
        "browser": {"verstr_fn": pr.get("browser_verstr")},
        "companies": {"path": company_path, "reader": pr.get("companies_reader")},
        "writable": {"dirs": pr.get("writable_dirs")},
        "site": {"opener": pr.get("site_opener")},
        "smtp_config": {"env": e},
        "recipients": {"env": e},
        "smtp_tcp": {"host": host, "port": port, "connector": pr.get("smtp_connector"),
                     "user": e.get("SMTP_USER", ""), "password": e.get("SMTP_PASS", ""),
                     "use_ssl": use_ssl, "deep": deep},
        "jobs_db": {"path": pr.get("jobs_db_path") or JOBS_DB_PATH},
        "heartbeat": {"path": pr.get("heartbeat_path") or HEARTBEAT_PATH,
                      "now": now},
    }

    out: list[CheckResult] = []
    for cid, name, severity, fn in CHECK_TABLE:
        if only and cid not in only:
            continue
        t0 = time.perf_counter()
        try:
            ok, detail = fn(**kwargs.get(cid, {}))
        except TypeError:
            # 探针签名不匹配时降级为「按默认参数跑」，不让自检自己崩
            try:
                ok, detail = fn()
            except Exception as exc:      # noqa: BLE001
                ok, detail = False, f"{type(exc).__name__}: {exc}"
        except Exception as exc:          # noqa: BLE001  单项失败不得中断整体
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        ms = int((time.perf_counter() - t0) * 1000)
        out.append(CheckResult(id=cid, name=name, severity=severity,
                               ok=bool(ok), detail=str(detail), ms=ms))
    return out


def summarize(results: list[CheckResult]) -> dict[str, Any]:
    """汇总：致命失败数、警告数、是否整体健康。"""
    failed = [r for r in results if not r.ok and r.severity == FATAL]
    warns = [r for r in results if not r.ok and r.severity == WARN]
    return {
        "ok": not failed,
        "total": len(results),
        "passed": sum(1 for r in results if r.ok),
        "failed": len(failed),
        "warn": len(warns),
        "failed_ids": [r.id for r in failed],
        "warn_ids": [r.id for r in warns],
    }


def exit_code(results: list[CheckResult], strict: bool = False) -> int:
    """0=无致命失败；1=有致命失败（--strict 时警告也算失败）。"""
    s = summarize(results)
    if s["failed"]:
        return 1
    if strict and s["warn"]:
        return 1
    return 0


def render_table(results: list[CheckResult]) -> str:
    lines = []
    for r in results:
        mark = "OK  " if r.ok else ("FAIL" if r.severity == FATAL else "WARN")
        lines.append(f"[{mark}] {r.name:<10} {r.detail}  ({r.ms}ms)")
    s = summarize(results)
    lines.append("")
    lines.append(f"合计 {s['total']} 项：通过 {s['passed']}，"
                 f"致命失败 {s['failed']}，警告 {s['warn']}")
    return "\n".join(lines)


def main() -> int:
    # .env 必须先加载：下面所有环境变量读取都依赖它
    try:
        import notify as _notify
        _notify.load_dotenv()
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="巡检服务自检")
    ap.add_argument("--json", default=None, help="把结果写入 JSON（供 notify 并入邮件）")
    ap.add_argument("--companies", default=None, help="名单文件（默认 companies.txt）")
    ap.add_argument("--deep", action="store_true", help="额外做 SMTP 登录等重探测")
    ap.add_argument("--only", default="", help="只跑指定项，逗号分隔")
    ap.add_argument("--mark-run", action="store_true", help="记录本次运行时间（心跳）")
    ap.add_argument("--strict", action="store_true", help="警告也算失败")
    ap.add_argument("--log-dir", default=None)
    args = ap.parse_args()

    logkit.setup_logging("healthcheck", log_dir=args.log_dir)
    log = __import__("logging").getLogger("healthcheck")

    only = {x.strip() for x in args.only.split(",") if x.strip()} or None
    companies = Path(args.companies) if args.companies else (BASE_DIR / "companies.txt")
    t0 = time.perf_counter()
    results = run_checks(companies=companies, deep=args.deep, only=only)
    elapsed = int((time.perf_counter() - t0) * 1000)
    summary = summarize(results)

    print(render_table(results))
    rc = exit_code(results, strict=args.strict)
    log.info("自检完成：%s，耗时 %dms，exit=%d", summary, elapsed, rc)

    if args.mark_run:
        p = write_heartbeat(ok=summary["ok"], failed=summary["failed"],
                            warn=summary["warn"])
        log.info("心跳已记录：%s", p)

    if args.json:
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "host": socket.gethostname(),
            "python": sys.version.split()[0],
            "elapsed_ms": elapsed,
            "summary": summary,
            "checks": [r.to_dict() for r in results],
        }
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        log.info("结果已写入 %s", out)
        print(f"\n结果已写入 {out.resolve()}")

    if not summary["ok"]:
        print(f"\n[!] {summary['failed']} 项致命失败："
              f"{', '.join(summary['failed_ids'])}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
