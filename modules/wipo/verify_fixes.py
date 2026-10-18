"""
六项缺陷修复的独立验证脚本（不访问网络、不依赖 pytest）

用法:
    cd F:\\projects\\GO1\\python_ver
    python verify_fixes.py

退出码 0 = 全部通过；非 0 = 有用例失败（失败列表打印在最后）。

覆盖范围:
    项 1  _get_next_run 本月未到日不再被跳过 / config schedule 段被读取
    项 2  check_status 与 check_squatting 使用同一 rows（30），不再漏报
    项 3  API success=False 时留痕（errors / failedBrands），不再假"全部正常"
    项 4  N 条告警按渠道汇总为 1 条消息，而非 N 封邮件
    项 5  我方商标识别为三条件（品牌名 / 申请人 / 注册号），包含匹配有长度保护
    项 6  ABNORMAL_STATUSES 含 Ended；errors 形态为 list；新检查前清理陈旧结果
    项 7  「每月一次」有常驻载体：计划任务安装脚本 + 批处理入口可复现、内容正确

**迁移到 FastAPI 后 6.6-6.10 的验证对象变了**：旧版断言的是
``web_app.py`` 启动时 unlink 两个状态文件，新版断言的是
``check_runs`` 单行表的状态迁移（见 ``verify_check_run_stale_state`` 的说明）。
断言的**意图**没变——「新检查启动后不能还读到上一次的结果」——只是
承载机制从文件换成了数据库。
"""

import json
import os
import shutil
import sys
import tempfile
import threading as _threading
import time as _real_time
from datetime import datetime

import main as main_mod
import monitor as monitor_mod
import notification as notification_mod
import wipo_client as wc_mod

RESULTS = []


def _emit(text: str):
    """输出一行，编码失败时降级（中文 Windows 控制台默认 GBK）。

    脚本自身也打印 ✓/✗ 之类的符号，若不加这层保护，在 GBK 控制台下
    会因为打印断言标题而崩，掩盖真正的断言结果。
    """
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, 'encoding', None) or 'utf-8'
        print(text.encode(encoding, errors='replace').decode(encoding, errors='replace'))


def check(case_id, description, passed, detail=''):
    """记录一条断言结果。"""
    RESULTS.append({'id': case_id, 'desc': description, 'passed': bool(passed), 'detail': detail})
    flag = 'PASS' if passed else 'FAIL'
    line = f"[{flag}] {case_id} {description}"
    if detail and not passed:
        line += f"\n         -> {detail}"
    _emit(line)


# ============================================================================
# 项 1：调度错月
# ============================================================================

def verify_item1():
    _emit("\n=== 项 1：调度错月（main.Scheduler._get_next_run / get_schedule_config）===")
    sched = main_mod.Scheduler()

    # 1.1 本月目标日尚未到来 -> 必须返回本月，而不是推到下个月
    now = datetime(2026, 3, 10, 8, 0, 0)
    nxt = sched._get_next_run(now, day_of_month=15, hour=9, minute=0)
    check('1.1', '本月目标日未到时返回本月（不再无条件跳到下月）',
          nxt == datetime(2026, 3, 15, 9, 0, 0), f'得到 {nxt}')

    # 1.2 同一天但时刻未到 -> 仍返回当天（这正是旧实现延迟最多 31 天的场景）
    now = datetime(2026, 3, 15, 7, 0, 0)
    nxt = sched._get_next_run(now, day_of_month=15, hour=9, minute=0)
    check('1.2', '同日时刻未到时返回当天',
          nxt == datetime(2026, 3, 15, 9, 0, 0), f'得到 {nxt}')

    # 1.3 本月目标时刻已过 -> 顺延到下一月
    now = datetime(2026, 3, 15, 10, 0, 0)
    nxt = sched._get_next_run(now, day_of_month=15, hour=9, minute=0)
    check('1.3', '本月目标时刻已过时顺延到下月',
          nxt == datetime(2026, 4, 15, 9, 0, 0), f'得到 {nxt}')

    # 1.4 跨年
    now = datetime(2026, 12, 20, 10, 0, 0)
    nxt = sched._get_next_run(now, day_of_month=5, hour=9, minute=0)
    check('1.4', '12 月已过目标日时跨年到次年 1 月',
          nxt == datetime(2027, 1, 5, 9, 0, 0), f'得到 {nxt}')

    # 1.5 小月日期钳制：4 月没有 31 日 -> 退化为 4 月 30 日
    now = datetime(2026, 4, 1, 0, 0, 0)
    nxt = sched._get_next_run(now, day_of_month=31, hour=9, minute=0)
    check('1.5', '指定 31 日在 30 天月份退化为月末',
          nxt == datetime(2026, 4, 30, 9, 0, 0), f'得到 {nxt}')

    # 1.6 2 月钳制（非闰年 -> 2 月 28 日）
    now = datetime(2027, 2, 1, 0, 0, 0)
    nxt = sched._get_next_run(now, day_of_month=30, hour=9, minute=0)
    check('1.6', '指定 30 日在 2 月退化为 2 月 28 日（非闰年）',
          nxt == datetime(2027, 2, 28, 9, 0, 0), f'得到 {nxt}')

    # 1.7 结果恒在未来（旧的兜底 if next_run <= now 永不触发的反例：本月未到日）
    now = datetime(2026, 3, 10, 8, 0, 0)
    nxt = sched._get_next_run(now, day_of_month=11, hour=9, minute=0)
    check('1.7', '返回值恒晚于 now（首次检查不再最多延迟 31 天）',
          nxt > now and (nxt - now).days < 2, f'得到 {nxt}，间隔 {(nxt - now).days} 天')

    # 1.8 config.json 的 schedule 段被真正读取
    sched2 = main_mod.Scheduler()
    sched2.config = {'schedule': {'day_of_month': 7, 'hour': 6, 'minute': 30}}
    cfg = sched2.get_schedule_config()
    check('1.8', 'get_schedule_config 读取 config 的 schedule 段',
          cfg == {'day_of_month': 7, 'hour': 6, 'minute': 30}, f'得到 {cfg}')

    # 1.9 坏配置回退默认值，不崩溃
    sched3 = main_mod.Scheduler()
    sched3.config = {'schedule': {'day_of_month': 99, 'hour': 'abc', 'minute': True}}
    cfg = sched3.get_schedule_config()
    check('1.9', '非法 schedule 值回退默认（含布尔 True 不被当成 1）',
          cfg == {'day_of_month': 1, 'hour': 9, 'minute': 0}, f'得到 {cfg}')

    # 1.10 仓库自带 config.json 的 schedule 段确实能被解析出来
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
    sched4 = main_mod.Scheduler(cfg_path)
    cfg = sched4.get_schedule_config()
    check('1.10', '真实 config.json 的 schedule 段可被调度器读取',
          set(cfg.keys()) == {'day_of_month', 'hour', 'minute'}, f'得到 {cfg}')


# ============================================================================
# 项 2：系统性漏报（rows 漂移）
# ============================================================================

def verify_item2():
    _emit("\n=== 项 2：rows 漂移导致的系统性漏报 ===")
    check('2.1', 'DEFAULT_SEARCH_ROWS 为 30',
          wc_mod.DEFAULT_SEARCH_ROWS == 30, f'得到 {wc_mod.DEFAULT_SEARCH_ROWS}')

    client = wc_mod.WipoApiClient()
    captured = []

    def fake_search(brand_name, options=None):
        captured.append(dict(options or {}))
        return {'success': True, 'total': 0, 'start': 0, 'results': []}

    client.search = fake_search
    client.check_status('NIKE')
    client.check_squatting('NIKE', ['Nike Inc.'])

    check('2.2', 'check_status 传给 search 的 rows 为 30（原为 5）',
          captured[0].get('rows') == 30, f'得到 {captured[0].get("rows")}')
    check('2.3', 'check_squatting 传给 search 的 rows 为 30',
          captured[1].get('rows') == 30, f'得到 {captured[1].get("rows")}')
    check('2.4', '两处 rows 取值一致（不再漂移）',
          captured[0].get('rows') == captured[1].get('rows'),
          f'status={captured[0].get("rows")} squatting={captured[1].get("rows")}')

    # 调用方显式指定 rows 时仍被尊重
    captured.clear()
    client.check_status('NIKE', {'rows': 5})
    check('2.5', '调用方显式传入 rows 时仍尊重调用方',
          captured[0].get('rows') == 5, f'得到 {captured[0].get("rows")}')

    # search 自身的默认值也是 30
    real_client = wc_mod.WipoApiClient()
    real_client.session = _FakeSession()
    real_client.search_id = 'f' * 32
    real_client.search('NIKE')
    check('2.6', 'search() 自身默认 rows 为 30',
          real_client.session.last_json.get('rows') == 30,
          f'得到 {real_client.session.last_json.get("rows")}')


class _FakeSession:
    """伪造 requests.Session，只用于捕获 search() 发出的请求体。"""

    def __init__(self):
        self.last_json = {}
        self.headers = {}

    def post(self, url, json=None, headers=None, timeout=None):
        self.last_json = json or {}
        return _FakeResponse()

    def get(self, *args, **kwargs):
        return _FakeResponse()


class _FakeResponse:
    status_code = 200
    text = '{"response": {"docs": [], "numFound": 0, "start": 0}}'


# ============================================================================
# 项 3：静默假"全部正常"
# ============================================================================

class FakeWipoClient:
    """可编程的假客户端：不联网，按需返回成功/失败。"""

    def __init__(self, status_result=None, squatting_result=None):
        self.status_result = status_result
        self.squatting_result = squatting_result
        self.init_called = False
        self.status_options_seen = None

    def init(self):
        self.init_called = True
        return 'fake-search-id'

    def check_status(self, brand_name, options=None):
        self.status_options_seen = options
        if self.status_result is not None:
            return self.status_result
        return {'success': True, 'brandName': brand_name, 'totalFound': 0,
                'ourMarks': [], 'abnormalStatuses': [], 'hasIssue': False}

    def check_squatting(self, brand_name, our_applicant_names, options=None):
        if self.squatting_result is not None:
            return self.squatting_result
        return {'success': True, 'brandName': brand_name, 'totalFound': 0,
                'squattingCases': [], 'needsReviewCases': [], 'hasSquatting': False}


class FakeTmList:
    """最小的 TrademarkList 替身。"""

    def __init__(self, records):
        self.trademarks = records

    def get_brand_names(self):
        return [t['brandName'] for t in self.trademarks if t['brandName']]

    def get_applicant_names(self):
        out = []
        for t in self.trademarks:
            for n in t.get('applicantNames', []):
                if n and n not in out:
                    out.append(n)
        return out


class _NoSleepTime:
    """time 模块的替身：sleep 变空操作，其余属性透传。

    注意不能直接改 real_time.sleep —— monitor.py 里 `import time` 绑定的是
    模块对象本身，那样会污染整个进程的 time.sleep。
    """

    def __init__(self, real_time):
        self._real = real_time

    def sleep(self, *args, **kwargs):
        return None

    def __getattr__(self, name):
        return getattr(self._real, name)


def _silence_sleep():
    """让 monitor 中的请求间隔 sleep 变成空操作，加快验证。"""
    monitor_mod.time = _NoSleepTime(_real_time)


def verify_item3():
    _emit("\n=== 项 3：API 失败时的静默假\"全部正常\" ===")
    _silence_sleep()

    # 3.1-3.4 两个阶段都失败
    failure = {'success': False, 'error': '解密失败', 'message': '响应数据解密失败'}
    client = FakeWipoClient(status_result=dict(failure), squatting_result=dict(failure))
    engine = monitor_mod.MonitorEngine(wipo_client=client,
                                       notification_service=notification_mod.NotificationService())
    tml = FakeTmList([{'brandName': 'TESTBRAND', 'applicant': 'Test Company Inc.',
                       'applicantNames': ['Test Company Inc.'], 'registrationNumber': 'TM0001'}])
    result = engine.run_check(tml, True, True, ['Test Company Inc.'])

    check('3.1', 'run_check 不再因 _failed_brands 缺失而抛 AttributeError（能正常返回）',
          isinstance(result, dict) and 'failedBrands' in result)
    check('3.2', 'API success=False 时 errors 非空（不再假报 0 错误）',
          len(result['errors']) == 2, f'得到 {len(result["errors"])} 条: {result["errors"]}')
    check('3.3', 'errors 带 stage 便于定位失败阶段',
          {e.get('stage') for e in result['errors']} == {'状态检查', '抢注检查'},
          f'得到 {[e.get("stage") for e in result["errors"]]}')
    check('3.4', 'failedBrands 记录检查未完成的品牌',
          result['failedBrands'] == ['TESTBRAND'], f'得到 {result["failedBrands"]}')
    check('3.5', 'errorsCount 与 len(errors) 一致',
          result['errorsCount'] == len(result['errors']) == 2,
          f'errorsCount={result["errorsCount"]} len={len(result["errors"])}')

    # 3.6 报告统计不再写"异常数 0 / 错误数 0"
    report = engine.generate_report()
    check('3.6', '报告错误数与实际失败数一致（不再写 0）',
          '错误数: 2' in report, '报告片段: ' + ' | '.join(
              ln for ln in report.splitlines() if '错误数' in ln or '异常数' in ln))

    # 3.7 报告序列化后 errors 仍是错误详情列表
    summary = engine.get_summary()
    check('3.7', 'get_summary 的错误数量写作 errorsCount（errors 不再一键两义）',
          summary.get('errorsCount') == 2 and 'errors' not in summary,
          f'得到 errorsCount={summary.get("errorsCount")}, '
          f'是否残留 errors 键={"errors" in summary}')

    # 3.8 第二次 run_check 不残留上一次的 failedBrands
    engine3 = monitor_mod.MonitorEngine(wipo_client=dict_fail_client(),
                                        notification_service=notification_mod.NotificationService())
    r1 = engine3.run_check(FakeTmList([{'brandName': 'A', 'applicant': 'X',
                                       'applicantNames': ['X'], 'registrationNumber': ''}]),
                           True, True, ['X'])
    engine3.client = FakeWipoClient()  # 换成健康客户端
    r2 = engine3.run_check(FakeTmList([{'brandName': 'A', 'applicant': 'X',
                                       'applicantNames': ['X'], 'registrationNumber': ''}]),
                           True, True, ['X'])
    check('3.8', '连续两次 run_check 之间 failedBrands 被重置（无状态残留）',
          r1['failedBrands'] == ['A'] and r2['failedBrands'] == [],
          f'第一次 {r1["failedBrands"]}，第二次 {r2["failedBrands"]}')

    # 3.9 未初始化的旧实例也不会崩（防御性写法）
    engine4 = monitor_mod.MonitorEngine(wipo_client=FakeWipoClient(),
                                        notification_service=notification_mod.NotificationService())
    del engine4._failed_brands
    engine4._record_check_failure('LEGACY', '状态检查', {'error': 'x'})
    check('3.9', '缺失 _failed_brands 的旧实例调用失败记录不抛异常',
          engine4._failed_brands == {'LEGACY'}, f'得到 {engine4._failed_brands}')

    # 3.10 中文 Windows 控制台（GBK）下打印 ✗/⚠ 不再抛 UnicodeEncodeError
    #      这条很关键：若打印抛异常，它会冒泡到 run_check 的 except 分支，
    #      把"记录一条检查失败"变成"再抛一个异常"，真实失败信息反而丢失。
    import io

    gbk_stdout = io.TextIOWrapper(io.BytesIO(), encoding='gbk', errors='strict')
    original_stdout = sys.stdout
    sys.stdout = gbk_stdout
    try:
        engine5 = monitor_mod.MonitorEngine(
            wipo_client=FakeWipoClient(status_result=dict(failure),
                                       squatting_result=dict(failure)),
            notification_service=notification_mod.NotificationService())
        r5 = engine5.run_check(
            FakeTmList([{'brandName': 'GBK-BRAND', 'applicant': 'X',
                         'applicantNames': ['X'], 'registrationNumber': ''}]),
            True, True, ['X'])
        gbk_ok = True
    except UnicodeEncodeError as exc:
        gbk_ok = False
        r5 = {'errors': []}
        _emit(f"         -> UnicodeEncodeError: {exc}")
    finally:
        sys.stdout = original_stdout

    check('3.10', 'GBK 控制台下 ✗/⚠ 打印不抛 UnicodeEncodeError（不吞掉真实失败）',
          gbk_ok and len(r5['errors']) == 2,
          f'gbk_ok={gbk_ok}，errors={len(r5["errors"]) if isinstance(r5.get("errors"), list) else r5}')


def dict_fail_client():
    failure = {'success': False, 'error': 'x', 'message': '模拟失败'}
    return FakeWipoClient(status_result=dict(failure), squatting_result=dict(failure))


# ============================================================================
# 项 4：提醒形态（N 条 = N 封）
# ============================================================================

class FakeSMTP:
    """伪造 smtplib.SMTP，记录发信次数。"""

    instances = []

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.messages = []
        self.started_tls = False
        self.logged_in = False
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = True

    def send_message(self, msg):
        self.messages.append(msg)


class FakeUrlOpenResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _extract_body(msg):
    """从 MIME 消息中取出正文文本。

    MIMEMultipart 的正文在 payload 列表里，且 utf-8 的 MIMEText 默认使用
    base64 传输编码，因此必须走 get_payload(decode=True) 再按 utf-8 解码；
    直接读 get_payload() 拿到的是 base64 字符串，会导致"正文里没有商标名"的假失败。
    """
    parts = []
    payload = msg.get_payload()
    if isinstance(payload, list):
        for part in payload:
            raw = part.get_payload(decode=True)
            if raw is None:
                parts.append(str(part.get_payload()))
            else:
                charset = part.get_content_charset() or 'utf-8'
                parts.append(raw.decode(charset, errors='replace'))
    else:
        raw = msg.get_payload(decode=True)
        if raw is None:
            parts.append(str(payload))
        else:
            parts.append(raw.decode(msg.get_content_charset() or 'utf-8', errors='replace'))
    return ''.join(parts)


def verify_item4():
    _emit("\n=== 项 4：提醒形态（N 条告警不再发 N 封）===")
    alerts = [
        {'brandName': f'BRAND{i}', 'issues': ['状态异常'],
         'abnormalStatuses': [{'registrationNumber': f'R{i}', 'status': 'Expired'}]}
        for i in range(5)
    ]

    # 打桩：拦住真实网络
    FakeSMTP.instances = []
    original_smtp = notification_mod.smtplib.SMTP
    original_urlopen = notification_mod.urlopen
    urlopen_calls = []

    def fake_urlopen(req, timeout=None):
        urlopen_calls.append(req)
        return FakeUrlOpenResponse(json.dumps({'errcode': 0}).encode('utf-8'))

    notification_mod.smtplib.SMTP = FakeSMTP
    notification_mod.urlopen = fake_urlopen
    try:
        config = {
            'channels': ['email', 'dingtalk', 'wework'],
            'email': {'smtp_server': 'smtp.test', 'smtp_port': 587,
                      'sender': 'a@test', 'recipients': ['b@test'],
                      'username': 'a@test', 'password': 'pw'},
            'dingtalk': {'webhook_url': 'https://example.invalid/dingtalk'},
            'wework': {'webhook_url': 'https://example.invalid/wework'},
        }
        service = notification_mod.NotificationService(config)
        result = service.send_alerts(alerts)

        sent_messages = sum(len(inst.messages) for inst in FakeSMTP.instances)
        check('4.1', '5 条告警只发 1 封邮件（原为 5 封）',
              sent_messages == 1, f'实际发信 {sent_messages} 封，SMTP 连接 {len(FakeSMTP.instances)} 次')
        check('4.2', '5 条告警只发 1 条钉钉 + 1 条企微（每个渠道 1 条）',
              len(urlopen_calls) == 2, f'实际 webhook 调用 {len(urlopen_calls)} 次')

        # 正文包含全部 5 条，不是只有第一条
        body = _extract_body(FakeSMTP.instances[0].messages[0])
        all_present = all(f'BRAND{i}' in body for i in range(5))
        check('4.3', '汇总正文包含全部 5 条告警（不丢内容）', all_present,
              f'正文前 200 字: {body[:200]!r}')
        check('4.4', '汇总正文带总数与编号',
              '共 5 个商标存在问题' in body and '--- [1/5] ---' in body and '--- [5/5] ---' in body,
              f'正文前 200 字: {body[:200]!r}')

        # 返回契约保持不变（调用方用 len() 取数量）
        check('4.5', '返回结构仍为 {success:[...], failed:[...]}，len() 可用',
              isinstance(result.get('success'), list) and isinstance(result.get('failed'), list)
              and len(result['success']) == 3 and len(result['failed']) == 0,
              f'得到 success={len(result.get("success", []))} failed={len(result.get("failed", []))}')
        check('4.6', '每条结果仍带 channel 与 alert 字段（兼容既有调用方/文档）',
              all('channel' in item and 'alert' in item for item in result['success']),
              f'得到 {result["success"]}')

        # 邮件主题体现是汇总
        subject = FakeSMTP.instances[0].messages[0]['Subject']
        check('4.7', '邮件主题标注为多商标汇总',
              '5 个商标' in subject, f'主题: {subject}')

        # 空列表不发消息
        FakeSMTP.instances = []
        urlopen_calls.clear()
        empty = service.send_alerts([])
        check('4.8', '无告警时不发送任何消息',
              empty == {'success': [], 'failed': []} and not FakeSMTP.instances and not urlopen_calls,
              f'得到 {empty}')

        # 单条发送路径仍然可用（send_alert 未被改动）
        FakeSMTP.instances = []
        single = service.send_alert(alerts[0], 'email')
        check('4.9', '单条发送 API send_alert 仍可用',
              single.get('success') is True and len(FakeSMTP.instances[0].messages) == 1,
              f'得到 {single}')

        # 未知渠道不发送、不崩溃
        FakeSMTP.instances = []
        unknown = notification_mod.NotificationService(
            {'channels': ['telegram'], 'email': config['email']}).send_alerts(alerts)
        check('4.10', '未知渠道被忽略且不抛异常',
              unknown == {'success': [], 'failed': []} and not FakeSMTP.instances,
              f'得到 {unknown}')
    finally:
        notification_mod.smtplib.SMTP = original_smtp
        notification_mod.urlopen = original_urlopen


# ============================================================================
# 项 5：我方商标识别
# ============================================================================

def verify_item5():
    _emit("\n=== 项 5：我方商标识别（三条件 + 包含匹配长度保护）===")

    # 5.1-5.3 check_status 的三条命中路径
    client = wc_mod.WipoApiClient()
    docs = [
        {'brandName': ['NIKE'], 'status': 'Expired', 'applicant': ['Nike Inc.'],
         'registrationNumber': 'R1'},                                   # 品牌名相等
        {'brandName': ['ナイキ'], 'status': 'Ended', 'applicant': ['Nike Inc.'],
         'registrationNumber': 'R2'},                                   # 申请人命中
        {'brandName': ['NIKE-SPORT'], 'status': 'Cancelled', 'applicant': ['Someone Else'],
         'registrationNumber': 'R3'},                                   # 注册号命中
        {'brandName': ['SOMETHING ELSE'], 'status': 'Expired', 'applicant': ['Other Co.'],
         'registrationNumber': 'R9'},                                   # 非我方，应排除
    ]

    def fake_search(brand_name, options=None):
        return {'success': True, 'total': len(docs),
                'start': 0, 'results': [client._normalize_doc(d) for d in docs]}

    client.search = fake_search
    res = client.check_status('NIKE', {
        'ourApplicantNames': ['Nike Inc.'],
        'ourRegistrationNumbers': ['R3'],
    })
    matched_regs = {m.get('registrationNumber') for m in res['ourMarks']}
    check('5.1', '品牌名精确相等命中我方商标', 'R1' in matched_regs, f'命中 {matched_regs}')
    check('5.2', '申请人名称命中我方商标（品牌名不同也能认出）', 'R2' in matched_regs, f'命中 {matched_regs}')
    check('5.3', '注册号命中我方商标', 'R3' in matched_regs, f'命中 {matched_regs}')
    check('5.4', '非我方商标被排除（不误报）', 'R9' not in matched_regs, f'命中 {matched_regs}')
    abnormal_regs = {a['registrationNumber'] for a in res['abnormalStatuses']}
    check('5.5', '异常状态只在命中我方商标中统计',
          abnormal_regs == {'R1', 'R2', 'R3'} and res['hasIssue'] is True,
          f'异常 {abnormal_regs}')

    # 5.6 不传 options（旧行为）时退化为品牌名相等，仍可用
    res_no_opt = client.check_status('NIKE')
    check('5.6', '未传我方标识时退化为品牌名相等判定（向后兼容）',
          {m.get('registrationNumber') for m in res_no_opt['ourMarks']} == {'R1'},
          f'命中 {[m.get("registrationNumber") for m in res_no_opt["ourMarks"]]}')

    # 5.7 空串/短串不产生包含误报（"禾" vs "益禾堂" 的活样本）
    check('5.7', '空串不产生包含误报（修掉 \'\' in x 恒真）',
          wc_mod._is_brand_contained('', '益禾堂') is False
          and wc_mod._is_brand_contained('益禾堂', '') is False)
    check('5.8', '短串（短于 MIN_CONTAINMENT_LEN=4）不产生包含误报',
          wc_mod._is_brand_contained('禾', '益禾堂') is False
          and wc_mod._is_brand_contained('A', 'APPLE') is False,
          f'MIN_CONTAINMENT_LEN={wc_mod.MIN_CONTAINMENT_LEN}')
    check('5.9', '足够长的真包含仍能命中（不是一刀切禁用）',
          wc_mod._is_brand_contained('NIKE', 'NIKE-SPORT') is True)

    # 5.10 状态比对大小写/空白不敏感
    client2 = wc_mod.WipoApiClient()
    docs2 = [{'brandName': ['TESTBRAND'], 'status': '  ended  ', 'applicant': ['T'],
              'registrationNumber': ''}]

    def fake_search2(brand_name, options=None):
        return {'success': True, 'total': 1, 'start': 0,
                'results': [client2._normalize_doc(d) for d in docs2]}

    client2.search = fake_search2
    res2 = client2.check_status('TESTBRAND')
    check('5.10', '异常状态比对大小写/空白不敏感（"  ended  " 命中 Ended）',
          res2['hasIssue'] is True and res2['abnormalStatuses'][0]['status'] == 'ended',
          f'得到 {res2["abnormalStatuses"]}')

    # 5.11 调用链：MonitorEngine 把我方申请人/注册号传进 check_status
    _silence_sleep()
    capture = {}

    class CapturingClient(FakeWipoClient):
        def check_status(self, brand_name, options=None):
            capture['options'] = options
            return {'success': True, 'brandName': brand_name, 'totalFound': 0,
                    'ourMarks': [], 'abnormalStatuses': [], 'hasIssue': False}

    engine = monitor_mod.MonitorEngine(wipo_client=CapturingClient(),
                                       notification_service=notification_mod.NotificationService())
    tml = FakeTmList([{'brandName': 'NIKE', 'applicant': 'Nike Inc.',
                       'applicantNames': ['Nike Inc.', 'Nike Foundation'],
                       'registrationNumber': 'R3'}])
    engine.run_check(tml, True, False, ['Nike Inc.'])
    opts = capture.get('options') or {}
    check('5.11', 'MonitorEngine 把我方申请人传给 check_status（项 5 不再是死代码）',
          opts.get('ourApplicantNames') == ['Nike Inc.', 'Nike Foundation'],
          f'得到 {opts.get("ourApplicantNames")}')
    check('5.12', 'MonitorEngine 把我方注册号传给 check_status',
          opts.get('ourRegistrationNumbers') == ['R3'],
          f'得到 {opts.get("ourRegistrationNumbers")}')


# ============================================================================
# 项 6：ABNORMAL_STATUSES / errors 形态 / 陈旧结果
# ============================================================================

def verify_item6():
    _emit("\n=== 项 6：Ended 状态 / errors 形态 / 陈旧结果清理 ===")

    # 6.1-6.2 Ended 被计入异常
    check('6.1', 'ABNORMAL_STATUSES 包含 Ended',
          'Ended' in wc_mod.ABNORMAL_STATUSES,
          f'得到 {sorted(wc_mod.ABNORMAL_STATUSES)}')
    check('6.2', 'Ended 同时属于合法状态集合',
          'Ended' in wc_mod.VALID_STATUSES)

    # 6.3 run_check 返回 errors 为 list，与 main.py 的迭代方式一致
    _silence_sleep()
    failure = {'success': False, 'error': 'boom', 'message': '模拟失败'}
    engine = monitor_mod.MonitorEngine(
        wipo_client=FakeWipoClient(status_result=dict(failure), squatting_result=dict(failure)),
        notification_service=notification_mod.NotificationService())
    result = engine.run_check(FakeTmList([{'brandName': 'X', 'applicant': 'Y',
                                          'applicantNames': ['Y'], 'registrationNumber': ''}]),
                              True, True, ['Y'])
    check('6.3', 'run_check 的 errors 是错误详情 list（不再是 int）',
          isinstance(result['errors'], list), f'得到类型 {type(result["errors"]).__name__}')
    check('6.4', 'errors 元素可被 main.py 的错误打印循环消费',
          all(isinstance(e, dict) and 'brandName' in e and 'error' in e for e in result['errors']),
          f'得到 {result["errors"]}')

    # 6.5 save_report 写出的 errors 也是同一形态
    tmpdir = tempfile.mkdtemp(prefix='verify_report_')
    try:
        report_path = os.path.join(tmpdir, 'r.json')
        engine.save_report(report_path)
        with open(report_path, encoding='utf-8') as f:
            saved = json.load(f)
        check('6.5', 'save_report 与 run_check 的 errors 形态一致（均为 list）',
              isinstance(saved.get('errors'), list) and saved['errors'] == result['errors'],
              f'得到 {type(saved.get("errors")).__name__}')
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # 6.6-6.9 检查状态：新检查启动后不再读到上一次的结果
    verify_check_run_stale_state()


def verify_check_run_stale_state():
    """新栈版：状态存 ``check_runs`` 单行表，不再靠「记得删两个文件」。

    旧版（web_app.py）的修复是「启动新检查前 unlink last_check_result.json /
    last_check_error.txt」——正确，但依赖每个入口都记得做，且「写一半进程被
    杀」会留下损坏内容。新栈把状态迁移本身做成原子的：``start_check`` 先
    upsert 成 ``running``（result 置 None），旧结果在同一个写入里就没了。

    因此 6.8 的断言对象从「文件不存在」变成「陈旧载荷已随状态一起被替换」，
    6.9 从「不再秒回 completed」变成「返回 running 且 result 为 None」。
    """
    import asyncio

    from fastapi.testclient import TestClient

    from app.core.config import reset_settings
    from app.core.database import dispose_engine, reset_engine
    from app.main import create_app

    tmp = tempfile.mkdtemp(prefix='verify_checkrun_')
    env_keys = ('WIPO_DATABASE__URL', 'WIPO_TRADEMARKS_FILE', 'WIPO_REPORTS_DIR')
    original_env = {k: os.environ.get(k) for k in env_keys}
    original_engine = monitor_mod.MonitorEngine

    # SQLite 路径必须用正斜杠：反斜杠会被当成转义符，报 unable to open database file
    db_path = os.path.join(tmp, 'test.db').replace('\\', '/')
    os.environ['WIPO_DATABASE__URL'] = f'sqlite+aiosqlite:///{db_path}'
    os.environ['WIPO_TRADEMARKS_FILE'] = os.path.join(tmp, 'user_trademarks.json')
    os.environ['WIPO_REPORTS_DIR'] = os.path.join(tmp, 'reports')
    reset_settings()
    reset_engine()

    gate = _threading.Event()

    class SlowEngine:
        """可控的假引擎：卡在 gate 上，直到验证脚本放行。"""

        def __init__(self, *a, **k):
            self.alerts = []
            self.results = []

        def run_check(self, *a, **k):
            gate.wait(timeout=15)
            return {'total': 1, 'checked': 1, 'errors': [], 'errorsCount': 0,
                    'failedBrands': [], 'alerts': [], 'hasIssues': False,
                    'timestamp': '2026-10-16T00:00:00'}

    try:
        monitor_mod.MonitorEngine = SlowEngine

        with TestClient(create_app()) as client:
            # 先造一条商标，否则 POST /api/check 会因「没有可检查的商标」直接 400
            resp = client.post('/api/trademarks', json={'brandName': 'TESTBRAND',
                                                        'applicant': 'Test Company Inc.'})
            check('6.6', '（前置）商标创建成功，检查才有对象可跑',
                  resp.status_code == 200, f'得到 {resp.status_code} {resp.text[:200]}')

            # 制造上次检查留下的陈旧结果
            stale = {'total': 2, 'checked': 2, 'errorsCount': 0,
                     'alerts': [{'brandName': 'STALE', 'issues': ['状态异常']}],
                     'timestamp': '2026-09-01T00:00:00'}
            _seed_check_run(db_path, status='completed', result=stale, error='陈旧错误')

            pre = client.get('/api/check/status').json()
            check('6.7', '陈旧结果存在时状态查询如实返回上一次的结果',
                  pre.get('status') == 'completed'
                  and pre.get('result', {}).get('total') == 2,
                  f'得到 {pre}')

            resp = client.post('/api/check',
                               json={'trademarkIds': [], 'checkStatus': True,
                                     'checkSquatting': True})
            check('6.8', 'POST /api/check 正常启动检查',
                  resp.status_code == 200 and resp.json().get('success') is True,
                  f'得到 {resp.status_code} {resp.text[:200]}')

            status = client.get('/api/check/status').json()
            check('6.9', '新检查启动后状态变为 running，且不再带着上一次的结果',
                  status.get('status') == 'running' and status.get('result') is None,
                  f'得到 {status}')

            # 放行假引擎，等它真正归档，确认这条链路的终点也对
            gate.set()
            final = _wait_for_check_run(client, 'completed', timeout=15)
            check('6.10', '检查完成后状态变为 completed 且写入了本次结果',
                  final.get('status') == 'completed'
                  and final.get('result', {}).get('total') == 1,
                  f'得到 {final}')
    finally:
        monitor_mod.MonitorEngine = original_engine
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_settings()
        try:
            asyncio.run(dispose_engine())
        except Exception:  # noqa: BLE001 - 收尾失败不该掩盖断言结果
            pass
        reset_engine()
        shutil.rmtree(tmp, ignore_errors=True)


def _seed_check_run(db_path, *, status, result, error=''):
    """直接往 SQLite 库里插一条 ``check_runs`` 行（模拟上一次检查的遗留状态）。"""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        conn.execute('DELETE FROM check_runs')
        conn.execute(
            'INSERT INTO check_runs (id, status, result, error, started_at, updated_at) '
            'VALUES (1, ?, ?, ?, ?, ?)',
            (status, json.dumps(result, ensure_ascii=False), error,
             '2026-09-01T00:00:00', '2026-09-01T00:00:00'),
        )
        conn.commit()
    finally:
        conn.close()


def _wait_for_check_run(client, expected, *, timeout):
    """轮询 /api/check/status 直到状态变成 expected（超时返回最后一次结果）。"""
    deadline = _real_time.time() + timeout
    last = {}
    while _real_time.time() < deadline:
        last = client.get('/api/check/status').json()
        if last.get('status') == expected:
            return last
        _real_time.sleep(0.2)
    return last


# ============================================================================

def verify_item7():
    """项 7：「每月一次」的常驻载体（计划任务）可复现且内容正确。

    不要求系统里真的注册了任务（换机器/CI 上不会有），只断言交付物本身
    完整、参数正确、可被重新注册；注册结果由 -Action Status 单独查询。
    """
    _emit("\n=== 项 7：每月一次的常驻载体（install_schedule_task.ps1）===")

    root = os.path.dirname(os.path.abspath(__file__))
    ps1 = os.path.join(root, 'install_schedule_task.ps1')
    bat = os.path.join(root, 'run_monthly_check.bat')

    check('7.1', '安装脚本存在', os.path.isfile(ps1), ps1)
    if not os.path.isfile(ps1):
        return

    with open(ps1, encoding='utf-8-sig') as f:
        src = f.read()

    # 7.2 必须是按月触发的计划任务，而不是一次性任务
    check('7.2', '使用 schtasks /SC MONTHLY 注册按月任务',
          '/SC MONTHLY' in src, '未找到 /SC MONTHLY')

    # 7.3 调度时间必须来自 config.json 的 schedule 段（而不是写死）
    check('7.3', '调度时间读取 config.json 的 schedule 段',
          'schedule' in src and 'day_of_month' in src and 'config.json' in src,
          '未发现 schedule/day_of_month/config.json 的组合')

    # 7.4 提供 DryRun，注册前可先看计划而不改系统
    check('7.4', '提供 DryRun 动作（先看后做）', 'DryRun' in src)

    # 7.5 提供 Uninstall，可回滚
    check('7.5', '提供 Uninstall 动作（可回滚）', "'Uninstall'" in src or 'Uninstall' in src)

    # 7.6 任务调用批处理而不是直接塞长命令行（避开 261 字符上限与引号转义）
    check('7.6', '任务执行入口指向 run_monthly_check.bat',
          'run_monthly_check.bat' in src, '未引用批处理入口')

    # 7.7 默认值必须与 main.py 的 get_schedule_config 一致，否则两边会算出不同时间
    sched = main_mod.Scheduler.__new__(main_mod.Scheduler)
    sched.config = {}
    from_config = sched.get_schedule_config()
    check('7.7', '默认调度与 main.Scheduler.get_schedule_config 一致（每月 1 日 09:00）',
          from_config == {'day_of_month': 1, 'hour': 9, 'minute': 0},
          f'得到 {from_config}')

    # 7.8 批处理必须真实存在且调用 main.py run
    check('7.8', '批处理入口已生成且调用 main.py run', os.path.isfile(bat), bat)
    if os.path.isfile(bat):
        with open(bat, encoding='ascii', errors='replace') as f:
            bat_src = f.read()
        check('7.9', '批处理调用 main.py run 并传入清单/配置/输出目录',
              'main.py run' in bat_src and '--config' in bat_src and '--output' in bat_src,
              bat_src.strip().splitlines()[-2] if bat_src.strip() else '(空)')

    # 7.10 计划任务相关文件必须能被提交（否则换机器无法复现）
    gitignore = os.path.join(root, '.gitignore')
    if os.path.isfile(gitignore):
        with open(gitignore, encoding='utf-8') as f:
            ignored = [ln.strip() for ln in f if ln.strip() and not ln.startswith('#')]
        blocked = [p for p in ('install_schedule_task.ps1', 'run_monthly_check.bat')
                   if p in ignored]
        check('7.10', '.gitignore 未屏蔽计划任务相关文件', not blocked,
              f'被忽略: {blocked}')


def main():
    _emit("=" * 70)
    _emit("WIPO 商标监控系统 - 六项缺陷修复验证")
    _emit("=" * 70)

    verify_item1()
    verify_item2()
    verify_item3()
    verify_item4()
    verify_item5()
    verify_item6()
    verify_item7()

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r['passed']]
    _emit("\n" + "=" * 70)
    _emit(f"合计 {total} 项断言，通过 {total - len(failed)}，失败 {len(failed)}")
    if failed:
        _emit("\n失败清单:")
        for r in failed:
            _emit(f"  - {r['id']} {r['desc']}")
            if r['detail']:
                _emit(f"      {r['detail']}")
    _emit("=" * 70)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
