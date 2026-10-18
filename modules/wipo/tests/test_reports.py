"""
检查报告页面测试

覆盖场景:
    - 无报告时显示空状态
    - 报告列表渲染（文件名、总数、告警数、时间）
    - 点击报告查看详情（汇总 + 告警详情渲染）
    - 返回按钮关闭详情
"""


from conftest import FAKE_REPORT
from playwright.sync_api import expect


def test_reports_empty_state(open_app, api, app_env):
    """无报告时展示空状态提示"""
    page = open_app
    api.clear_reports(app_env['reports_dir'])
    page.reload()
    page.click('.nav-item[data-page="reports"]')

    assert '暂无报告' in page.locator('#reports-list').inner_text()


def _seed_two_reports(api, app_env):
    reports = {
        'report_20260805_090000.json': {
            **FAKE_REPORT,
            'timestamp': '2026-08-05T09:00:00',
            'total': 3,
            'alerts': [],
            'results': [],
        },
        'report_20260806_103000.json': {
            **FAKE_REPORT,
            'timestamp': '2026-08-06T10:30:00',
            'total': 1,
        },
    }
    api.seed_reports(app_env['reports_dir'], reports)
    return reports


def test_reports_list_and_detail(open_app, api, app_env):
    """报告列表按时间倒序，点击后展示详情，可返回"""
    page = open_app
    _seed_two_reports(api, app_env)
    page.reload()
    page.click('.nav-item[data-page="reports"]')

    items = page.locator('.report-item')
    # 报告列表是 init() 与 switchPage() 两条 async 路径加载的，
    # items.count() 不会自动等待，全量跑时偶发拿到 0（见 conftest 顶部说明）。
    # 用 expect 的自动重试等列表渲染完成，单跑与全量跑结果一致。
    expect(items).to_have_count(2)

    # 最新报告在最前（文件名倒序）
    assert 'report_20260806_103000.json' in items.first.inner_text()
    assert '共 1 个商标' in items.first.inner_text()
    assert '1 条告警' in items.first.inner_text()

    # 打开详情
    items.first.click()
    page.locator('#report-detail-card').wait_for(state='visible')
    assert page.locator('#report-detail-title').inner_text() == 'report_20260806_103000.json'

    detail = page.locator('#report-detail').inner_text()
    assert '检查总数' in detail
    assert '告警数' in detail
    assert 'TESTBRAND' in detail
    assert 'Expired' in detail

    # 返回关闭详情
    page.click('#btn-close-report')
    assert page.locator('#report-detail-card').is_hidden()


def test_report_without_alerts(open_app, api, app_env):
    """无告警报告展示'无告警'占位"""
    page = open_app
    api.clear_reports(app_env['reports_dir'])
    api.seed_reports(app_env['reports_dir'], {
        'report_20260805_090000.json': {
            **FAKE_REPORT,
            'timestamp': '2026-08-05T09:00:00',
            'total': 3,
            'alerts': [],
            'results': [],
        },
    })

    page.reload()
    page.click('.nav-item[data-page="reports"]')
    page.locator('.report-item').first.click()

    page.locator('#report-detail-card').wait_for(state='visible')
    assert '无告警' in page.locator('#report-detail').inner_text()


def test_report_with_needs_review_cases(open_app, api, app_env):
    """报告详情渲染待审核案例"""
    page = open_app
    api.clear_reports(app_env['reports_dir'])

    review_result = {
        **FAKE_REPORT['alerts'][0],
        # 新栈的告警由 results 派生（alerts_from_results），needsReviewCases
        # 必须写在 result 上而不是 alert 上，否则前端拿不到。
        'needsReviewCases': [{
            'brandName': 'TESTBRAND PRO',
            'applicant': 'Potential Licensee',
            'applicantCountryCode': 'CN',
            'status': 'Pending',
            'registrationNumber': 'CN12345',
            'niceClass': ['9'],
            'office': 'CN',
            'score': 80,
            'riskLevel': 'medium',
            'reason': '品牌名精确匹配，但申请人不同，需人工确认是否为授权或抢注',
        }],
    }
    api.seed_reports(app_env['reports_dir'], {
        'report_20260806_120000.json': {
            **FAKE_REPORT,
            'timestamp': '2026-08-06T12:00:00',
            'results': [review_result],
        },
    })

    page.reload()
    page.click('.nav-item[data-page="reports"]')
    page.locator('.report-item').first.click()

    page.locator('#report-detail-card').wait_for(state='visible')
    detail = page.locator('#report-detail').inner_text()
    assert '待审核' in detail
    assert 'TESTBRAND PRO' in detail
    assert 'Potential Licensee' in detail
    assert 'medium' in detail


def test_new_format_report_derives_alerts_in_ui(open_app, api, app_env):
    """
    新格式报告（只存 results + alertsCount，不存 alerts）在页面上必须正常渲染

    这是 P2-6 的真实回归面：报告文件里不再有 alerts 字段，
    由后端 GET /api/reports/<f> 从 results 还原。若还原失效，
    列表页告警数会变 0、详情页会显示'无告警'，且不会有任何报错。
    """
    page = open_app
    api.clear_reports(app_env['reports_dir'])

    # 模拟 save_report() 的真实产物：无 alerts 键，只有 alertsCount 与 results
    api.seed_reports(app_env['reports_dir'], {
        'report_20260807_140000.json': {
            'timestamp': '2026-08-07T14:00:00',
            'finishedAt': '2026-08-07T14:00:05',
            'total': 1,
            'checked': 1,
            'alertsCount': 1,
            'errorsCount': 0,
            'failedBrands': [],
            'hasIssues': True,
            'errors': [],
            'results': [{
                'brandName': 'TESTBRAND',
                'trademarkId': 'uuid-new-format',
                'abnormalStatuses': FAKE_REPORT['alerts'][0]['abnormalStatuses'],
                'squattingCases': FAKE_REPORT['alerts'][0]['squattingCases'],
                'needsReviewCases': [],
                'hasIssue': True,
                'hasSquatting': True,
            }],
        },
    })

    page.reload()
    page.click('.nav-item[data-page="reports"]')

    item = page.locator('.report-item').first
    assert 'report_20260807_140000.json' in item.inner_text()
    assert '1 条告警' in item.inner_text(), '列表页告警数必须由 alertsCount/派生得到，不能变 0'

    item.click()
    page.locator('#report-detail-card').wait_for(state='visible')
    detail = page.locator('#report-detail').inner_text()
    assert '无告警' not in detail, '详情页必须从 results 还原出告警，而不是显示无告警'
    assert '告警数' in detail
    assert 'TESTBRAND' in detail
    assert 'Expired' in detail


def test_summary_file_not_listed_as_report(open_app, api, app_env):
    """摘要文件（report_*.summary.json）不能出现在报告列表里，否则同一次检查被算两次"""
    page = open_app
    api.clear_reports(app_env['reports_dir'])
    api.seed_reports(app_env['reports_dir'], {
        'report_20260808_090000.json': {
            **FAKE_REPORT,
            'timestamp': '2026-08-08T09:00:00',
        },
        'report_20260808_090000.summary.json': {
            'timestamp': '2026-08-08T09:00:00',
            'total': 1,
            'alertsCount': 1,
            'alerts': [{'brandName': 'TESTBRAND', 'issues': ['状态异常']}],
        },
    })

    page.reload()
    page.click('.nav-item[data-page="reports"]')

    items = page.locator('.report-item')
    # 同 test_reports_list_and_detail：等列表异步加载完成再计数。
    # 断言是「恰好 1 条」——若摘要文件被当成报告，会重试到超时并失败。
    expect(items).to_have_count(1)
    assert 'summary' not in items.first.inner_text()
