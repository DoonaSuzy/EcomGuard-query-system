"""报告字段与稳定标识的回归测试（P2-2 / P2-4 / P2-5）

这些断言覆盖三类曾经真实出错、且出错时**不报错只给错数据**的行为：
  - `_normalize_record` 过滤掉 `id`，导致报告/告警里的 `trademarkId` 恒为空
  - `save_report` 的 `total` 把检查失败的商标算两次（2 个商标写出 4）
  - 抢注案例没有稳定标识，同名不同注册无法逐条比对

它们静默失效，所以必须有测试盯着。
"""
import json
import os
import sys

import pytest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import wipo_client  # noqa: E402
from monitor import MonitorEngine  # noqa: E402
from trademark_list import TrademarkList  # noqa: E402


def write_list(path, records):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'trademarks': records}, f, ensure_ascii=False)


# ---------- P2-4：`id` 必须能穿过标准化 ----------

def test_normalize_record_keeps_id():
    """`_normalize_record` 不能丢掉 id（丢了 trademarkId 会恒为空）"""
    tl = TrademarkList()
    out = tl._normalize_record({'id': 'uuid-x', 'brandName': 'B'})
    assert out['id'] == 'uuid-x'


def test_normalize_record_tolerates_missing_id():
    """没有 id 的记录（CSV/Excel 载入）不应报错，给空串即可"""
    tl = TrademarkList()
    out = tl._normalize_record({'brandName': 'B'})
    assert out['id'] == ''


def test_json_load_preserves_id(tmp_path):
    """经 load_from_json 之后 id 仍然在（这是报告溯源的入口）"""
    p = tmp_path / 'list.json'
    write_list(p, [{'id': 'uuid-1', 'brandName': 'B1'}])
    tl = TrademarkList()
    tl.load(str(p))
    assert tl.trademarks[0]['id'] == 'uuid-1'


# ---------- P2-5：抢注案例的稳定标识 ----------

@pytest.mark.parametrize('case,expected', [
    ({'office': 'EM', 'registrationNumber': '1234567'}, 'EM|1234567'),
    ({'office': 'US', 'registrationNumber': '', 'applicationNumber': '88771'}, 'APP|88771'),
    ({'office': 'US', 'registrationNumber': '', 'applicationNumber': ''}, ''),
])
def test_build_case_id(case, expected):
    """caseId 优先级：局别+注册号 → 申请号 → 空"""
    assert wipo_client._build_case_id(case) == expected


def test_build_case_id_is_reproducible():
    """同一份输入必须给出同一标识（报告要能逐条比对，不能含随机成分）"""
    case = {'office': 'EM', 'registrationNumber': '999'}
    assert wipo_client._build_case_id(case) == wipo_client._build_case_id(case)


# ---------- P2-2：total 语义与跨接口一致 ----------

def make_engine(records, failing=()):
    """构造一个 engine，其中 failing 里的品牌会记录一次检查失败"""
    tl = TrademarkList()
    tl.trademarks = [tl._normalize_record(r) for r in records]
    eng = MonitorEngine()
    eng._brand_ids = eng._build_brand_id_index(tl)

    def fake(brand_name, applicant_names, check_status, check_squatting):
        if brand_name in failing:
            # 走真实方法（而不是自己拼 errors 字典），这样 trademarkId 的
            # 写入路径才真的被测到
            eng._record_check_failure(brand_name, '状态检查',
                                      {'success': False, 'error': 'simulated'})
        eng.results.append({
            'brandName': brand_name,
            'trademarkId': eng._brand_ids.get(brand_name, ''),
            'abnormalStatuses': [], 'squattingCases': [], 'needsReviewCases': [],
            'hasIssue': False, 'hasSquatting': False,
        })
        return eng.results[-1]

    # 直接复刻 run_check 的计数逻辑，避免联网与 sleep
    eng._brands_attempted = len(tl.get_brand_names())
    for name in tl.get_brand_names():
        fake(name, [], False, False)
    return eng


RECORDS = [
    {'id': 'uuid-a', 'brandName': 'BRAND_A'},
    {'id': 'uuid-b', 'brandName': 'BRAND_B'},
]


def test_total_counts_attempted_not_double_counted(tmp_path):
    """失败的商标只算一次：total 必须是 2，不是 4"""
    eng = make_engine(RECORDS, failing={'BRAND_B'})
    path = tmp_path / 'r.json'
    eng.save_report(str(path))
    report = json.loads(path.read_text(encoding='utf-8'))
    assert report['total'] == 2, '失败的商标既进 results 又进 failedBrands，不能重复计数'
    assert report['checked'] == 2
    assert report['errorsCount'] == 1
    assert report['failedBrands'] == ['BRAND_B']


def test_report_total_matches_run_check_total(tmp_path):
    """归档报告的 total 必须与 run_check 同源，否则总表与明细对不上"""
    eng = make_engine(RECORDS)
    run_check_total = eng._brands_attempted
    path = tmp_path / 'r.json'
    eng.save_report(str(path))
    assert json.loads(path.read_text(encoding='utf-8'))['total'] == run_check_total


def test_save_report_works_without_run_check(tmp_path):
    """save_report 被脱离 run_check 调用时也要能算出合理的 total（防御性回退）"""
    eng = make_engine(RECORDS)
    del eng._brands_attempted  # 模拟旧实例 / 测试替身
    path = tmp_path / 'r.json'
    eng.save_report(str(path))
    assert json.loads(path.read_text(encoding='utf-8'))['total'] == 2


# ---------- P2-4：trademarkId 要贯穿 result / alert / error ----------

def test_trademark_id_in_results(tmp_path):
    """结果里必须带上清单记录的 id，否则报告无法指回那一条"""
    eng = make_engine(RECORDS)
    path = tmp_path / 'r.json'
    eng.save_report(str(path))
    report = json.loads(path.read_text(encoding='utf-8'))
    ids = [r['trademarkId'] for r in report['results']]
    assert ids == ['uuid-a', 'uuid-b']


def test_trademark_id_in_errors(tmp_path):
    """错误记录也要带 id，便于从失败直接定位到清单记录"""
    eng = make_engine(RECORDS, failing={'BRAND_B'})
    path = tmp_path / 'r.json'
    eng.save_report(str(path))
    report = json.loads(path.read_text(encoding='utf-8'))
    assert [e['trademarkId'] for e in report['errors']] == ['uuid-b']


def test_alert_carries_trademark_id():
    """告警要能指回清单里的那一条"""
    eng = MonitorEngine()
    eng._brand_ids = {'BRAND_A': 'uuid-a'}
    result = {
        'brandName': 'BRAND_A', 'trademarkId': 'uuid-a',
        'abnormalStatuses': ['Expired'], 'squattingCases': [], 'needsReviewCases': [],
        'hasIssue': True, 'hasSquatting': False,
    }
    alert = eng._create_alert(result)
    assert alert['trademarkId'] == 'uuid-a'


# ---------- P2-6：报告不重复存 alerts，改为从 results 派生 ----------

def test_report_omits_alerts_but_keeps_count(tmp_path):
    """归档报告不再存一整份 alerts（约占 29% 体积），但必须留下 alertsCount"""
    records = [dict(r) for r in RECORDS]
    tl = TrademarkList()
    tl.trademarks = [tl._normalize_record(r) for r in records]
    eng = MonitorEngine()
    eng._brand_ids = eng._build_brand_id_index(tl)
    eng._brands_attempted = 2
    eng.results = [
        {'brandName': 'BRAND_A', 'trademarkId': '', 'abnormalStatuses': [{'status': 'Expired'}],
         'squattingCases': [], 'needsReviewCases': [], 'hasIssue': True, 'hasSquatting': False},
        {'brandName': 'BRAND_B', 'trademarkId': '', 'abnormalStatuses': [],
         'squattingCases': [], 'needsReviewCases': [], 'hasIssue': False, 'hasSquatting': True},
        {'brandName': 'BRAND_C', 'trademarkId': '', 'abnormalStatuses': [],
         'squattingCases': [], 'needsReviewCases': [], 'hasIssue': False, 'hasSquatting': False},
    ]
    eng.alerts = [eng._create_alert(r) for r in eng.results[:2]]
    path = tmp_path / 'r.json'
    eng.save_report(str(path))
    report = json.loads(path.read_text(encoding='utf-8'))
    assert 'alerts' not in report, '报告不应再重复存一整份 alerts'
    assert report['alertsCount'] == 2, 'alertsCount 必须留下，否则列表页拿不到告警数'
    assert len(report['results']) == 3


def test_derived_alerts_match_created_alerts(tmp_path):
    """从 results 派生的告警必须与 run_check 当时生成的逐字段一致"""
    from monitor import alerts_from_results

    eng = MonitorEngine()
    eng._brand_ids = {'BRAND_A': 'uuid-a'}
    results = [
        {'brandName': 'BRAND_A', 'trademarkId': 'uuid-a',
         'abnormalStatuses': [{'status': 'Expired'}], 'squattingCases': [],
         'needsReviewCases': [], 'hasIssue': True, 'hasSquatting': True},
        {'brandName': 'BRAND_B', 'trademarkId': '', 'abnormalStatuses': [],
         'squattingCases': [], 'needsReviewCases': [], 'hasIssue': False, 'hasSquatting': False},
    ]
    eng.results = results
    created = [eng._create_alert(r) for r in results if r['hasIssue'] or r['hasSquatting']]
    assert alerts_from_results(results) == created


def test_derived_alerts_preserve_results_order():
    """派生顺序必须与 results 顺序一致，否则告警列表会换序"""
    from monitor import alerts_from_results

    results = [
        {'brandName': f'B{i}', 'hasIssue': i % 2 == 0, 'hasSquatting': False}
        for i in range(5)
    ]
    assert [a['brandName'] for a in alerts_from_results(results)] == ['B0', 'B2', 'B4']


# ---------- P3-4：summary-only 导出 ----------

def test_save_summary_writes_compact_file(tmp_path):
    """摘要文件只给统计与告警定位，不含案例明细（供 AI/看板低 token 消费）"""
    eng = make_engine(RECORDS, failing={'BRAND_B'})
    eng.alerts = [{
        'brandName': 'BRAND_A', 'trademarkId': 'uuid-a', 'issues': ['疑似抢注'],
        'abnormalStatuses': [], 'squattingCases': [{'caseId': 'CN|1'}],
        'needsReviewCases': [{'caseId': 'CN|2'}, {'caseId': 'CN|3'}],
        'hasIssue': False, 'hasSquatting': True,
    }]
    path = tmp_path / 'r.summary.json'
    eng.save_summary(str(path))
    summary = json.loads(path.read_text(encoding='utf-8'))

    assert summary['total'] == 2
    assert summary['checked'] == 2
    assert summary['alertsCount'] == 1
    assert summary['errorsCount'] == 1
    assert summary['failedBrands'] == ['BRAND_B']
    assert summary['hasIssues'] is True
    # 只有计数，没有案例明细
    alert = summary['alerts'][0]
    assert alert['squattingCount'] == 1
    assert alert['needsReviewCount'] == 2
    assert 'squattingCases' not in alert
    assert 'results' not in summary


def test_summary_is_smaller_than_report(tmp_path):
    """摘要文件必须明显小于完整报告（否则没有存在意义）"""
    eng = make_engine(RECORDS * 20, failing={'BRAND_B'})
    for i, r in enumerate(eng.results):
        r['squattingCases'] = [{'caseId': f'CN|{i}', 'applicant': 'X' * 200}] * 5
        r['hasSquatting'] = True
    eng.alerts = [eng._create_alert(r) for r in eng.results]

    full = tmp_path / 'full.json'
    mini = tmp_path / 'mini.json'
    eng.save_report(str(full))
    eng.save_summary(str(mini))
    assert mini.stat().st_size < full.stat().st_size / 2


# ---------- P3-5 / P2-3：errors 只有「错误详情列表」一种形态 ----------

def test_get_summary_reports_count_as_errors_count():
    """get_summary 的数量写在 errorsCount 上，errors 键不再兼职当数量"""
    eng = make_engine(RECORDS, failing={'BRAND_B'})
    summary = eng.get_summary()
    assert summary['errorsCount'] == 1
    assert 'errors' not in summary, 'errors 键必须只表示错误详情列表'


def test_run_check_and_get_summary_agree_on_error_count():
    """两个入口给出的错误数量必须一致（曾一个是 int、一个是 list）"""
    eng = make_engine(RECORDS, failing={'BRAND_A', 'BRAND_B'})
    summary = eng.get_summary()
    assert summary['errorsCount'] == len(eng.errors) == 2


# ---------- 报告列表的告警数：三种历史形态都要能取到 ----------

def test_report_alerts_count_prefers_stored_count():
    """
    `alertsCount` 优先于派生：它是生成时写下的真相

    若这里退回按 results 派生，一旦 results 与当时的告警判定出现任何漂移，
    列表页显示的告警数就会与实际发出的告警不一致，而且不会有任何报错。
    """
    from app.models.api_compat import report_alerts_count

    data = {
        'alertsCount': 7,
        # results 里明显只有 1 条有问题 —— 故意制造分歧以确认取的是 alertsCount
        'results': [{'brandName': 'A', 'hasIssue': True, 'hasSquatting': False}],
    }
    assert report_alerts_count(data) == 7


def test_report_alerts_count_derives_for_legacy_report():
    """没有 alertsCount 的旧报告，按 results 派生（这是 P2-6 之前的历史格式）"""
    from app.models.api_compat import report_alerts_count

    data = {
        'results': [
            {'brandName': 'A', 'hasIssue': True, 'hasSquatting': False},
            {'brandName': 'B', 'hasIssue': False, 'hasSquatting': False},
            {'brandName': 'C', 'hasIssue': False, 'hasSquatting': True},
        ],
    }
    assert report_alerts_count(data) == 2


def test_report_alerts_count_falls_back_to_alerts_list():
    """既无 alertsCount 也无 results 的极旧报告，退回读 alerts 长度"""
    from app.models.api_compat import report_alerts_count

    assert report_alerts_count({'alerts': [{'brandName': 'X'}] * 3}) == 3
    # alerts 为 null / 缺失 / 非列表时不能崩，按 0 处理
    assert report_alerts_count({'alerts': None}) == 0
    assert report_alerts_count({}) == 0


def test_report_alerts_count_ignores_bool():
    """bool 是 int 的子类，True 不能被当成告警数 1"""
    from app.models.api_compat import report_alerts_count

    assert report_alerts_count({'alertsCount': True, 'results': []}) == 0

