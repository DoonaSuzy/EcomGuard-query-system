"""WIPO 商标监控系统 —— FastAPI 版命令行入口。

pyproject.toml 里注册为 ``wipo-monitor``，因此装好后可以直接::

    wipo-monitor serve --host 0.0.0.0 --port 8000
    wipo-monitor run data/user_trademarks.json --no-notify
    wipo-monitor schedule data/user_trademarks.json
    wipo-monitor init-config
    wipo-monitor db-init

**为什么新写一份 CLI 而不是直接用 ``python main.py``**

``main.py`` 是业务内核（``Scheduler`` / 检查 / 通知），与 Web 框架无关，
新栈继续复用它——所以这里**不重写** run / schedule / init-config 的逻辑，
而是把 ``main.main`` 的 argparse 分支搬过来，再补两个 Web 栈特有的子命令：

- ``serve``     启动 FastAPI（等价 ``uvicorn app.main:app``）；
- ``db-init``   手动建表（正常情况下 lifespan 已经建过，这里是运维兜底）。

**注意**：run / schedule 走的仍是旧的文件式商标清单（``main.Scheduler``
面向 ``trademark_file``），而 FastAPI 版已把商标存进 MySQL。两条入口
并存是迁移期的取舍：CLI 适合「临时补跑一次」，Web 端适合日常管理。
"""

from __future__ import annotations

import argparse
import sys

from app.core.config import get_settings

__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wipo-monitor",
        description="WIPO 商标监控系统（FastAPI 分层架构）",
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    # ---- serve ----
    serve = sub.add_parser("serve", help="启动 FastAPI 服务")
    serve.add_argument("--host", default=None, help="监听地址（默认读配置）")
    serve.add_argument("--port", type=int, default=None, help="监听端口（默认 8000）")
    serve.add_argument("--reload", action="store_true", help="开发模式热重载")

    # ---- db-init ----
    sub.add_parser("db-init", help="手动创建数据库表")

    # ---- run / schedule / init-config：复用 main.py 的参数定义 ----
    run = sub.add_parser("run", help="运行一次检查")
    run.add_argument("trademark_file", help="商标清单文件 (JSON/CSV/Excel)")
    run.add_argument("--output", "-o", help="报告输出目录")
    run.add_argument("--config", "-c", help="配置文件路径")
    run.add_argument("--no-status", action="store_true", help="不检查状态")
    run.add_argument("--no-squatting", action="store_true", help="不检查抢注")
    run.add_argument("--no-notify", action="store_true", help="不发送通知")

    sched = sub.add_parser("schedule", help="启动按月定时调度")
    sched.add_argument("trademark_file", help="商标清单文件")
    sched.add_argument("--config", "-c", help="配置文件路径")
    sched.add_argument("--day", "-d", type=int, default=None, help="每月第几天运行")
    sched.add_argument("--hour", type=int, default=None, help="运行小时")
    sched.add_argument("--minute", type=int, default=None, help="运行分钟")

    cfg = sub.add_parser("init-config", help="生成默认配置文件")
    cfg.add_argument("--output", "-o", default="config.json", help="配置文件路径")

    return parser


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=args.host or settings.host,
        port=args.port or settings.port,
        reload=args.reload or settings.debug,
    )
    return 0


def _db_init(_args: argparse.Namespace) -> int:
    """建表。正常情况下 lifespan 已经建过，这里是运维兜底。"""

    import asyncio

    from app.core.database import Base, get_engine
    from app.models import orm as _orm  # noqa: F401 - 导入才会注册到 Base.metadata

    async def _create() -> None:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    print("数据库表已就绪")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "serve":
        return _serve(args)
    if args.command == "db-init":
        return _db_init(args)

    # run / schedule / init-config 的业务逻辑复用 main.py（业务内核，与
    # Web 框架无关）。这里把 argv 原样转交，参数名与 main.py 完全一致。
    from main import main as legacy_main

    argv = sys.argv[1:] if argv is None else argv
    sys.argv = [sys.argv[0], *argv]
    legacy_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
