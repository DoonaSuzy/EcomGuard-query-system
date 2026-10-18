# -*- coding: utf-8 -*-
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
import logging

from fastapi import FastAPI

import api as credit_api

__all__ = ["router", "credit_lifespan_enter", "credit_lifespan_exit"]

#: 路由直接复用 credit 的定义（不带任何改动）。
router = credit_api.router

log = logging.getLogger("credit_glue")

#: enter 时记下 worker，exit 时用它来 stop（exit 拿不到 application 参数）。
_worker: credit_api.Worker | None = None


async def credit_lifespan_enter(application: FastAPI) -> None:
    """建 Store、启动 Worker，并挂到主 app 的 state 上。

    与 ``credit_api.lifespan`` 的启动段等价，只是作用对象换成了主 app。
    """
    global _worker
    store = credit_api.Store(credit_api.DB_PATH)
    worker = credit_api.Worker(store)
    worker.start()
    application.state.store = store
    application.state.worker = worker
    _worker = worker
    log.info("营业执照模块已挂载：db=%s out=%s",
             credit_api.DB_PATH, credit_api.OUT_DIR)


async def credit_lifespan_exit() -> None:
    """等 worker 线程退出。

    ``worker.stop()`` 是阻塞的（内部 ``cv.wait``），必须丢到线程池，
    否则会卡住事件循环。与 credit 原 lifespan 的写法一致。
    """
    global _worker
    if _worker is None:
        return
    log.info("营业执照模块关闭，等待 worker 退出")
    try:
        await asyncio.get_running_loop().run_in_executor(None, _worker.stop)
    finally:
        _worker = None
