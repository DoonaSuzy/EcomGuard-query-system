"""旧栈 E2E 的**移植版**：服务端已从 Flask 换成 FastAPI，但前端与 46 个 DOM
选择器契约一个都没动，因此这 13 个 Playwright 用例几乎可以原样保留。

本文件是唯一需要改的地方（所有与后端实现的耦合都在夹具层）。
改完后 ``tests/`` 全部用例仍然测的是「真实用户在浏览器里点页面」，
而不是「FastAPI 怎么返回」——那是 ``tests_new/`` 的职责。

与旧版的四点差异（都已在新栈落地）：
1. **服务进程**：``werkzeug.make_server`` → ``uvicorn.Server``（随机端口）。
2. **数据隔离**：改 ``web_app.DATA_DIR`` 等模块变量 → 设 ``WIPO_DATABASE__URL``
   指向临时 SQLite 文件（新栈用 MySQL/SQLite 而非 data/*.json）。
3. **报告播种**：往 reports/ 目录写 JSON 文件 → 直接写 ``reports`` 表
   （新栈报告存库，不在磁盘上）。
4. **检查状态清理**：删 last_check_result.json → 清 ``check_runs`` 表。

关于为什么**不删掉旧测试、只留 tests_new/**：
    ``tests_new/`` 用 TestClient 不碰浏览器，证明的是「接口行为对」；
    这里证明的是「页面在真实浏览器里跑得通」。前端重写（Vue 3）时，
    后者是唯一能拦住「接口对了但页面点了没反应」的防线。
"""

import asyncio
import os
import shutil
import sys
import tempfile
import threading
import time

import pytest
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

RESULT_DIR = os.path.join(BASE_DIR, 'test-results')

#: 新栈的数据目录（SQLite 库 + 报告落点）都塞进这个临时目录
#: 报告在数据库里，但目录仍要建——/health 与导出功能会用到。


# ==================== 服务器 ====================

@pytest.fixture(scope='session')
def app_env():
    """在隔离的临时目录里启动 FastAPI 应用（随机端口）。

    与旧版一样是 session 级：起一次服务器，所有用例共用。

    **关键点：自建一个专用事件循环并在它上面跑 uvicorn。**
    夹具同时把 ``loop`` / ``loop_thread`` 暴露给用例，这样测试侧的
    数据库操作（清表、播种报告）可以 ``run_coroutine_threadsafe`` 到
    同一个 loop 上执行——新栈的 engine 连接绑定在创建它的 loop 上，
    在别的 loop 里复用会直接报错。
    """
    import asyncio

    import uvicorn

    tmp = tempfile.mkdtemp(prefix='tm_e2e_')
    data_dir = os.path.join(tmp, 'data')
    reports_dir = os.path.join(tmp, 'reports')
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(reports_dir, exist_ok=True)

    db_path = os.path.join(data_dir, 'e2e.db')
    # 必须在 import app.main **之前**设置，否则 get_settings() 的 lru_cache
    # 会先缓存真实 MySQL 配置。
    # URL 一律用正斜杠：Windows 路径里的反斜杠会被 aiosqlite 当成转义符，
    # 报 `unable to open database file`（实测过，不是猜测）。
    os.environ['WIPO_DATABASE__URL'] = 'sqlite+aiosqlite:///' + db_path.replace('\\', '/')
    os.environ['WIPO_REPORTS_DIR'] = reports_dir
    os.environ['WIPO_TRADEMARKS_FILE'] = os.path.join(tmp, 'user_trademarks.json')

    from app.main import app as fastapi_app

    loop = asyncio.new_event_loop()

    config = uvicorn.Config(
        fastapi_app,
        host='127.0.0.1',
        port=0,  # 随机端口
        log_level='warning',
        lifespan='on',
        loop='asyncio',
    )
    server = uvicorn.Server(config)
    # 注意 target 直接是 _serve，不要用 lambda 包一层：lambda 会让
    # 线程内的异常被 pytest 的 thread-exception 警告吞掉，看不出真因。
    thread = threading.Thread(target=_serve, args=(loop, server), daemon=True)
    thread.start()

    # 等服务器就绪（uvicorn 的 started 标志在 serve() 里才置位）
    deadline = time.time() + 20
    while not server.started and thread.is_alive() and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError(f'uvicorn 20 秒内未启动（线程存活={thread.is_alive()}）')

    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield {
            'base_url': f'http://127.0.0.1:{port}',
            'data_dir': data_dir,
            'reports_dir': reports_dir,
            'db_path': db_path,
            'loop': loop,
            'loop_thread': thread,
        }
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        # 只在 loop 还活着时才停它——serve() 正常结束时 loop 已 close，
        # 再 call_soon_threadsafe 会撞 "Event loop is closed"。
        if not loop.is_closed() and thread.is_alive():
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=10)
        if not loop.is_closed():
            loop.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _serve(loop, server):
    """在自建 loop 上跑 uvicorn（供 app_env 使用）。"""
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(server.serve())
    finally:
        loop.close()


@pytest.fixture
def base_url(app_env):
    return app_env['base_url']


# ==================== 浏览器 ====================

@pytest.fixture(scope='session')
def browser():
    """会话级 Chromium 浏览器实例"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser):
    """每个测试一个独立的浏览器上下文页面"""
    context = browser.new_context(viewport={'width': 1440, 'height': 900})
    pg = context.new_page()
    pg.set_default_timeout(15000)
    yield pg
    context.close()


@pytest.fixture
def open_app(page, base_url):
    """打开应用首页并等待页面就绪"""
    page.goto(base_url)
    page.wait_for_selector('#trademark-form')
    return page


# ==================== API 操作助手 ====================

@pytest.fixture
def api(base_url, app_env):
    """通过 HTTP API 直接操作测试数据（快速造数/清数）"""
    import asyncio as _asyncio

    class ApiHelper:
        def __init__(self):
            self.base = base_url
            self.data_dir = app_env['data_dir']
            self.reports_dir = app_env['reports_dir']
            # 这些操作要在**服务器的事件循环**里跑：新栈的 engine 是
            # aiomysql/aiosqlite，连接绑定在创建它的 loop 上。在 pytest
            # 主线程里另起 asyncio.run() 会撞「cannot be called from a
            # running event loop」或跨 loop 连接错误。
            self._loop = app_env['loop']
            self._loop_thread = app_env['loop_thread']

        def _run(self, coro):
            fut = _asyncio.run_coroutine_threadsafe(coro, self._loop)
            return fut.result(timeout=20)

        def clear_check_state(self):
            """清理检查运行状态（新栈存在 check_runs 表，不再是两个文件）。

            直接清表，比发一次空检查更快且不依赖网络。
            """
            from sqlalchemy import delete

            from app.core.database import get_engine
            from app.models.orm import CheckRun

            async def _clear():
                engine = get_engine()
                async with engine.begin() as conn:
                    await conn.execute(delete(CheckRun))

            try:
                self._run(_clear())
            except Exception:
                # 表还没建（服务器刚起、lifespan 未跑完）时忽略
                pass

        def _request(self, method, path, **kwargs):
            r = requests.request(method, f'{self.base}{path}', timeout=15, **kwargs)
            return r

        def list(self):
            return self._request('GET', '/api/trademarks').json()['data']

        def add(self, brand_name, applicant='', registration_number='',
                nice_class=None, notes=''):
            payload = {
                'brandName': brand_name,
                'applicant': applicant,
                'registrationNumber': registration_number,
                'niceClass': nice_class or [],
                'notes': notes,
            }
            return self._request('POST', '/api/trademarks', json=payload).json()

        def delete_all(self):
            ids = [t['id'] for t in self.list()]
            if ids:
                self._request('DELETE', '/api/trademarks/batch', json={'ids': ids})

        def reports(self):
            return self._request('GET', '/api/reports').json()['data']

        def seed_reports(self, reports_dir, reports):
            """写入测试报告。

            reports: {文件名: 报告字典}

            与旧版最大的不同：新栈报告存 ``reports`` 表，不写文件。
            这里直接把字典映射成 ORM 行插进去，字段名保持 camelCase
            与 ``Report.to_payload()`` 的输出一致。
            """
            from sqlalchemy import delete

            from app.core.database import get_engine
            from app.models.orm import Report

            async def _seed():
                engine = get_engine()
                async with engine.begin() as conn:
                    await conn.execute(delete(Report))
                    for name, content in reports.items():
                        # 摘要文件不入库（新栈列表页本来就不该出现它）
                        if name.endswith('.summary.json'):
                            continue
                        # alertsCount 一律从 results 派生：旧版读文件时也是
                        # 这么算的（monitor.alerts_from_results）。若调用方
                        # 手写了一个 alertsCount 而 results 是空的，按派生值
                        # 走才能反映「详情页能还原出几条告警」。
                        results = content.get('results', [])
                        from monitor import alerts_from_results

                        derived_alerts = alerts_from_results(results)
                        await conn.execute(
                            Report.__table__.insert().values(
                                filename=name,
                                timestamp=content.get('timestamp', ''),
                                finished_at=content.get('finishedAt', ''),
                                total=content.get('total', 0),
                                checked=content.get('checked', 0),
                                alerts_count=len(derived_alerts),
                                errors_count=content.get('errorsCount', 0),
                                has_issues=bool(content.get('hasIssues', False)),
                                failed_brands=content.get('failedBrands', []),
                                errors=content.get('errors', []),
                                results=results,
                            )
                        )

            self._run(_seed())

        def clear_reports(self, reports_dir):
            from sqlalchemy import delete

            from app.core.database import get_engine
            from app.models.orm import Report

            async def _clear():
                engine = get_engine()
                async with engine.begin() as conn:
                    await conn.execute(delete(Report))

            self._run(_clear())

    return ApiHelper()


# ==================== 检查引擎 Mock ====================

# 模拟一份包含"状态异常 + 疑似抢注"的检查结果
FAKE_ALERT = {
    'brandName': 'TESTBRAND',
    'issues': ['状态异常', '疑似抢注'],
    'abnormalStatuses': [{
        'registrationNumber': 'TM0001',
        'status': 'Expired',
        'office': 'US',
        'expiryDate': '2020-01-01',
    }],
    'squattingCases': [{
        'brandName': 'TESTBRAND X',
        'applicant': 'Other Company Ltd.',
        'applicantCountryCode': 'GB',
        'status': 'Pending',
        'registrationNumber': 'GB12345',
        'niceClass': ['9'],
        'office': 'GB',
        'score': 95,
        'riskLevel': 'high',
        'reason': '品牌名相似且申请人不同，疑似抢注',
    }],
    'needsReviewCases': [],
    'hasIssue': True,
    'hasSquatting': True,
}

FAKE_RESULT = {
    'brandName': 'TESTBRAND',
    'abnormalStatuses': FAKE_ALERT['abnormalStatuses'],
    'squattingCases': FAKE_ALERT['squattingCases'],
    'needsReviewCases': [],
    'hasIssue': True,
    'hasSquatting': True,
}

FAKE_REPORT = {
    'timestamp': '2026-08-06T10:00:00',
    'total': 1,
    'alerts': [FAKE_ALERT],
    'errors': [],
    'results': [FAKE_RESULT],
}


@pytest.fixture
def fake_check_success():
    """将监控引擎替换为确定性假实现（成功场景，不访问真实 WIPO API）。

    monkeypatch 的是 ``monitor.MonitorEngine.run_check``——新栈的
    ``MonitorService._run_engine`` 在**线程内**动态 import 这个模块，
    因此替换类属性对它同样生效（与旧栈机制一致）。
    """
    from monitor import MonitorEngine

    original = MonitorEngine.run_check

    def fake_run_check(self, trademark_list, check_status=True,
                       check_squatting=True, applicant_names=None):
        # 模拟真实检查耗时，使前端中间态（按钮禁用/进行中提示）可被断言
        time.sleep(1.5)
        self.results = [dict(FAKE_RESULT)]
        self.alerts = [dict(FAKE_ALERT)]
        self.errors = []
        return {
            'total': 1,
            'checked': 1,
            'errors': 0,
            'alerts': self.alerts,
            'hasIssues': True,
            'timestamp': '2026-08-06T10:00:00',
        }

    MonitorEngine.run_check = fake_run_check
    yield
    MonitorEngine.run_check = original


@pytest.fixture
def fake_check_error():
    """模拟检查引擎抛异常的场景（检查失败分支）"""
    from monitor import MonitorEngine

    original = MonitorEngine.run_check

    def boom(self, *args, **kwargs):
        time.sleep(1.5)
        raise RuntimeError('模拟的检查引擎崩溃')

    MonitorEngine.run_check = boom
    yield
    MonitorEngine.run_check = original


# ==================== 数据预置 ====================

@pytest.fixture
def seeded_trademarks(api):
    """预置多个商标数据，供列表/搜索/批量测试使用

    返回: 预置的商标记录列表
    """
    api.delete_all()
    for name, applicant in [
        ('APPLE', 'Apple Inc.'),
        ('GOOGLE', 'Google LLC'),
        ('MICROSOFT', 'Microsoft Corp.'),
    ]:
        api.add(name, applicant=applicant)
    return api.list()


@pytest.fixture
def capture_api_requests(page):
    """拦截并记录页面发出的 API 请求，用于断言请求体

    返回: 请求日志列表，每项为 playwright Request 对象
    """
    requests_log = []
    page.on('request', lambda req: requests_log.append(req))
    return requests_log


# ==================== 失败截图 ====================

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """测试失败时自动截图，便于回归定位"""
    outcome = yield
    report = outcome.get_result()
    if report.when == 'call' and report.failed and 'page' in item.funcargs:
        page = item.funcargs['page']
        shot_dir = os.path.join(RESULT_DIR, 'screenshots')
        os.makedirs(shot_dir, exist_ok=True)
        try:
            page.screenshot(path=os.path.join(shot_dir, f'{item.name}.png'))
        except Exception:
            pass
