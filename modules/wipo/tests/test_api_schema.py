"""接口契约一致性测试（P3-3）

校验三方一致，防止「改了路由忘了改文档」：
  1. app/api/v1/ 里真实注册的 FastAPI 路由（/api/* 与 /health、/api/schema）
  2. docs/api-schema.json 的 routes 列表（契约里的机器可读副本）
  3. docs/api-schema.json 的 categories[].endpoints（给人看的文档副本）

任一不一致都会让本文件失败，从而在本地与 CI 里被立刻发现。

**迁移到 FastAPI 后本文件的两点变化**：

- 路由来源从 Flask 的 ``app.url_map`` 换成 ``app.api.v1.iter_api_routes()``。
  原因：FastAPI 0.143 的 ``include_router`` 保留子路由器的**引用**而不是
  逐条展开，``app.routes`` 里看到的是 ``_IncludedRouter``（path/methods
  都为空），直接遍历会拿到空清单——那样这个测试就永远通过了，等于没有。
- ``/api/schema`` 端点改用 FastAPI ``TestClient``，并自带隔离的 SQLite
  配置：契约校验不该依赖本机是否跑着 MySQL。
"""
import json
import os
import sys

import pytest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(BASE_DIR, 'docs', 'api-schema.json')
sys.path.insert(0, BASE_DIR)

from app.api.v1 import API_PREFIX, ROUTERS, iter_api_routes  # noqa: E402

# 参数占位符写法差异：代码里用 {trademark_id}，契约统一写成 {id}。
# 契约的占位符命名刻意与实现解耦——后端从 Flask 换成 FastAPI 时路径参数
# 改名了，但对外 URL 形状一个字都没变，契约不该跟着churn。
PLACEHOLDER_MAP = {
    '{trademark_id}': '{id}',
}


def normalize_path(path: str) -> str:
    """把实现里的路径参数名统一成契约风格（{trademark_id} -> {id}）"""
    for old, new in PLACEHOLDER_MAP.items():
        path = path.replace(old, new)
    return path


def live_routes() -> set:
    """从 FastAPI 路由模块读取契约范围内的路由（method + 归一化路径）。

    **范围：只收 ``/api/`` 开头的路由**，与契约文件自身的定位一致
    （``description`` 写的是「Web REST 接口契约」）。``/health`` 是运维
    端点，不在这个契约里——它由 ``test_health_endpoint_is_served`` 单独
    钉住，避免「契约不管、测试也不管，于是没人管」。

    这里刻意**不** import ``app.main``：那样会在模块导入时执行
    ``create_app()`` → ``get_settings()``，把真实配置（含 MySQL 连接串）
    固化进 lru_cache，之后任何隔离手段都失效。只读路由模块本身即可，
    路由清单与配置无关。
    """
    prefixed = {
        id(module)
        for module, is_prefixed in ROUTERS
        if is_prefixed
    }
    routes = set()
    for route in iter_api_routes():
        # iter_api_routes 拿到的是各模块内的相对路径，前缀要自己补上
        for module, _is_prefixed in ROUTERS:
            if route in module.router.routes:
                path = (API_PREFIX if id(module) in prefixed else '') + route.path
                break
        else:
            path = route.path

        if not path.startswith(API_PREFIX + '/'):
            continue

        for method in route.methods:
            if method in ('HEAD', 'OPTIONS'):
                continue
            routes.add((method, normalize_path(path)))
    return routes


def load_schema() -> dict:
    with open(SCHEMA_PATH, encoding='utf-8') as f:
        return json.load(f)


def schema_routes(schema: dict) -> set:
    """契约顶层 routes 列表"""
    return {(r['method'], r['path']) for r in schema['routes']}


def documented_routes(schema: dict) -> set:
    """契约 categories 里给人看的接口清单"""
    routes = set()
    for group in schema['categories'].values():
        for ep in group['endpoints']:
            routes.add((ep['method'], ep['path']))
    return routes


@pytest.fixture()
def fastapi_client(tmp_path_factory, monkeypatch):
    """一个指向临时 SQLite 的 TestClient（契约测试不需要真数据库）。"""
    tmp = tmp_path_factory.mktemp('schema_contract')
    db = (tmp / 'test.db').as_posix()
    monkeypatch.setenv('WIPO_DATABASE__URL', f'sqlite+aiosqlite:///{db}')
    monkeypatch.setenv('WIPO_REPORTS_DIR', str(tmp / 'reports'))
    monkeypatch.setenv('WIPO_TRADEMARKS_FILE', str(tmp / 'user_trademarks.json'))

    from app.core.config import reset_settings
    from app.core.database import dispose_engine, reset_engine

    reset_settings()
    reset_engine()

    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c

    import asyncio

    asyncio.run(dispose_engine())
    reset_settings()
    reset_engine()


def test_schema_file_exists():
    """契约文档必须存在（/api/schema 与文档站都依赖它）"""
    assert os.path.exists(SCHEMA_PATH), '缺少 docs/api-schema.json'


def test_live_routes_match_schema_routes():
    """FastAPI 真实路由与契约 routes 必须完全一致"""
    live = live_routes()
    declared = schema_routes(load_schema())
    missing = live - declared
    extra = declared - live
    assert not missing, f'这些路由已实现但契约未登记: {sorted(missing)}'
    assert not extra, f'这些路由已登记但代码里不存在: {sorted(extra)}'


def test_live_routes_are_not_empty():
    """防「空清单导致断言永真」。

    如果哪天 ``iter_api_routes`` 因为 FastAPI 内部结构变化而返回空，
    上面那条「完全一致」的断言会**照样通过**（空集 == 空集只对得上
    因为契约也为空，但契约为空会先撞上别的断言；真正的风险是两边都
    拿到空集）。这里把 13 条这个数量钉住，让退化立刻暴露。
    """
    live = live_routes()
    assert live, '从 app.api.v1 读不到任何路由——iter_api_routes 可能已失效'
    assert len(live) == 12, f'契约内的 /api/ 路由应为 12 条，实际 {len(live)}: {sorted(live)}'


def test_schema_routes_match_documented_endpoints():
    """契约 routes 与 categories 里的接口清单必须一致"""
    routes = schema_routes(load_schema())
    docs = documented_routes(load_schema())
    assert routes == docs, (
        f'routes 与 categories 不一致: '
        f'仅在 routes: {sorted(routes - docs)}; '
        f'仅在 categories: {sorted(docs - routes)}'
    )


def test_schema_has_required_metadata():
    """契约要有版本与更新时间，便于下游判断是否过期"""
    schema = load_schema()
    for key in ('version', 'updatedAt', 'owner'):
        assert schema.get(key), f'契约缺少 {key} 字段'


def test_schema_owner_points_at_current_implementation():
    """owner 必须指向当前真正提供这些路由的位置。

    迁移前是 ``web_app.py``（Flask），现在是 ``app/api/v1/``（FastAPI）。
    这个断言的意义不只是「值对得上」：owner 是文档站与契约使用者判断
    「该去找谁问」的入口，指向已废弃的文件会让排查走错方向。
    """
    assert load_schema()['owner'] == 'app/api/v1/'


def test_schema_endpoint_has_name_and_description():
    """每条接口都要有名称与描述，否则文档站会渲染出空白项"""
    schema = load_schema()
    for group_name, group in schema['categories'].items():
        assert group.get('name'), f'分类 {group_name} 缺少 name'
        for ep in group['endpoints']:
            assert ep.get('name'), f'{ep.get("path")} 缺少 name'
            assert ep.get('description'), f'{ep.get("path")} 缺少 description'


def test_api_schema_endpoint_returns_contract(fastapi_client):
    """/api/schema 必须能返回契约本身（P3-2）"""
    resp = fastapi_client.get('/api/schema')
    assert resp.status_code == 200
    body = resp.json()
    assert body['success'] is True
    assert body['data']['owner'] == 'app/api/v1/'


def test_health_endpoint_is_served(fastapi_client):
    """/health 不在契约里，但必须真的在。

    PNA 的运维约定是「健康检查不算业务接口，单独一条线管」。这里把它
    钉住，是为了防止有人照着「契约里没有 /health」把它连带删掉——
    那样编排系统（K8s / 负载均衡 / 本机的探活脚本）就失去了判断依据。
    """
    resp = fastapi_client.get('/health')
    assert resp.status_code == 200
    body = resp.json()
    assert body['status'] in ('ok', 'degraded')
    assert 'database' in body['dependencies']
