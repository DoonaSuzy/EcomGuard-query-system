"""健康检查与接口契约路由。

``/health`` 的三态设计（借鉴 PNA 的做法）:
    - ``ok``       数据库可用、WIPO 可达；
    - ``degraded`` 数据库可用但 WIPO 不可达 —— **返回 200**。
      查不了 WIPO 不等于服务死了：容器编排若因为上游抖动就重启本服务，
      只会让正在跑的检查被腰斩。
    - ``down``     数据库不可用（连商标清单都读不出来）—— 返回 503，
      这时服务确实没有意义，应该被重启或摘流量。

``/api/schema`` 沿用旧版：返回 ``docs/api-schema.json``。迁移后这份文件与
FastAPI 自动生成的 OpenAPI 并存，职责不同 ——
    - ``/openapi.json`` 由代码生成，描述**本进程实际注册**的接口；
    - ``docs/api-schema.json`` 是仓库里的契约文件，被 CI（
      ``tests/test_api_schema.py``）校验与文档站消费。
两者都要有：前者保证「文档不会撒谎」，后者保证「契约有版本历史、可 diff」。
"""

from __future__ import annotations

import json
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import PROJECT_ROOT
from app.core.deps import SettingsDep
from app.core.response import ok

__all__ = ["router"]

router = APIRouter(tags=["system"])

#: 进程启动时间，用于计算 uptime
_STARTED_AT = time.time()

SCHEMA_FILE = PROJECT_ROOT / "docs" / "api-schema.json"


@router.get("/health", summary="健康检查")
async def health(settings: SettingsDep) -> JSONResponse:
    """探活：数据库 + WIPO 上游。

    返回 200（ok/degraded）或 503（down），响应体始终是同一结构，
    让监控能同时从状态码和体里读结论。
    """
    from app.core.database import ping as db_ping

    db_ok = await db_ping()
    wipo_ok = await _probe_wipo()

    if not db_ok:
        status = "down"
        code = 503
    elif not wipo_ok:
        status = "degraded"
        code = 200
    else:
        status = "ok"
        code = 200

    payload = {
        "status": status,
        "app": settings.app_name,
        "version": settings.app_version,
        "uptime_seconds": round(time.time() - _STARTED_AT, 3),
        "dependencies": {
            "database": "up" if db_ok else "down",
            "wipo_api": "up" if wipo_ok else "down",
        },
    }
    return JSONResponse(content=payload, status_code=code)


@router.get("/api/schema", summary="获取机器可读的接口契约")
async def get_api_schema() -> JSONResponse:
    """旧版 ``GET /api/schema``。

    契约文件缺失/损坏都返回 500 —— 这不是「服务不可用」，而是**部署不完整**，
    运维必须看见，不能静默降级成空对象。
    """
    if not SCHEMA_FILE.exists():
        return JSONResponse(
            content={"success": False, "error": "接口契约文件不存在: docs/api-schema.json"},
            status_code=500,
        )
    try:
        schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return JSONResponse(
            content={"success": False, "error": f"接口契约文件无法解析: {exc}"},
            status_code=500,
        )
    return JSONResponse(content=ok(schema))


async def _probe_wipo(timeout: float = 3.0) -> bool:
    """轻量探测 WIPO 接口是否可达。

    只做一次 HEAD/GET 到 base URL，**不做完整登录**：健康检查会被轮询，
    每次跑一遍 AES 解密握手既慢又会给对方造成无谓压力。
    探测失败只影响 ``degraded``/``ok`` 的判定，绝不抛出。
    """
    import asyncio

    def _probe() -> bool:
        try:
            import requests

            from wipo_client import API_BASE

            resp = requests.get(API_BASE, timeout=timeout)
            return resp.status_code < 500
        except Exception:  # noqa: BLE001 - 探测失败即视为不可达
            return False

    return await asyncio.to_thread(_probe)
