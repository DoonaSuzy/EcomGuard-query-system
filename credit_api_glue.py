"""credit 模块的接线层。

为什么需要这一层
----------------
``modules/credit/api.py`` 是**独立可运行**的应用（``uvicorn api:app``），它把
三样东西写死在模块级：

    app      = FastAPI(...)     模块级应用对象
    router   = APIRouter(prefix="/api")
    lifespan = 建 Store → 起 Worker → 挂到 app.state

聚合入口要复用其中的 ``router`` 与「建 Store / 起 Worker」的逻辑，但不能复用
那个模块级 ``app``（否则两套 lifespan、两套 state 会打架）。

所以这里做三件事：
  1. 直接 re-export credit 的 ``router``（路由定义一行没改）；
  2. 把 credit lifespan 的**函数体**拆成 ``credit_lifespan_enter/exit``，
     作用对象改成传进来的 application —— 即聚合入口的主 app；
  3. 保持行为完全一致：同样的 ``Store(DB_PATH)``、``Worker(store)``、
     ``worker.start()``、退出时 ``run_in_executor(None, worker.stop)``。

**为什么不直接调用 ``credit_api.lifespan(app)``**：它是个
``@asynccontextmanager``，``async with`` 进来后中间那段 ``yield`` 期间不能插入
另一个 lifespan 的收尾逻辑（WIPO 的 ``dispose_engine`` 也在 finally 里）。
拆成 enter/exit 两个函数才能让 ``combined_lifespan`` 控制嵌套顺序：
WIPO 先进、credit 后进，退出时 credit 先出、WIPO 后出。

**handler 侧已经改好**：``modules/credit/api.py`` 里 10 处 ``app.state.*``
已全部改成 ``request.app.state.*``（7 个 handler 签名加了 ``request: Request``），
所以这里只要往**主 app** 的 state 上挂 store/worker 就够了。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

import api as credit_api
from fastapi import FastAPI

__all__ = ["credit_lifespan_enter", "credit_lifespan_exit", "router"]

#: 路由直接复用 credit 的定义（不带任何改动）。
router = credit_api.router

log = logging.getLogger("credit_glue")

#: enter 时记下 worker / scheduler，exit 时用它们收尾（exit 拿不到 application 参数）。
_worker: credit_api.Worker | None = None
_scheduler: object | None = None


async def credit_lifespan_enter(application: FastAPI) -> None:
    """建 Store、启动 Worker 与调度线程，并挂到主 app 的 state 上。

    与 ``credit_api.lifespan`` 的启动段等价，只是作用对象换成了主 app。
    **两处 lifespan 必须保持同步**：credit 独立运行时走的也是同一套
    「建 Store → 起 Worker → 补算调度 → 起调度线程」顺序，
    漏掉调度线程会让「独立跑 api:app 时定时任务不生效」这种差异
    在聚合入口里看不出来，而恰好是独立部署时最需要它的场景。
    """
    global _worker, _scheduler
    import schedule as sched

    store = credit_api.Store(credit_api.DB_TARGET)
    worker = credit_api.Worker(store)
    worker.start()
    application.state.store = store
    application.state.worker = worker
    fixed = sched.ensure_next_run(store)
    scheduler = sched.Scheduler(store, worker)
    scheduler.start()
    application.state.scheduler = scheduler
    _worker = worker
    _scheduler = scheduler
    log.info("营业执照模块已挂载：db=%s(%s) out=%s 调度补算 %d 条",
             credit_api.DB_TARGET, store.backend, credit_api.OUT_DIR, fixed)


async def credit_lifespan_exit() -> None:
    """先停调度线程，再等 worker 退出。

    顺序有讲究：**先停调度**，否则正在收尾时调度又塞一个新作业进队列，
    worker 会因为「停止标志已置位」直接丢弃它，那条作业就永远停在 queued。
    """
    global _worker, _scheduler
    if _scheduler is not None:
        with contextlib.suppress(Exception):
            _scheduler.stop()
        _scheduler = None
    if _worker is None:
        return
    log.info("营业执照模块关闭，等待 worker 退出")
    try:
        await asyncio.get_running_loop().run_in_executor(None, _worker.stop)
    finally:
        _worker = None
