"""
检查选项边界测试

覆盖场景:
    - 仅抢注检查（取消状态）-> 请求体 checkStatus=false
    - 仅状态检查（取消抢注）-> 请求体 checkSquatting=false
    - 双选项全取消 -> 仍可启动
    - 无商标时检查 -> 提示 + 跳转录入页

断言写法约定:
    - 轮询完成后的结果卡片/汇总数由 async 数据驱动，一律用 expect(...).to_have_*()
"""

from playwright.sync_api import expect


def test_status_only_option_sends_correct_payload(open_app, api, fake_check_success, capture_api_requests):
    """仅状态检查（取消抢注）-> 请求体 checkSquatting=false"""
    page = open_app
    api.delete_all()
    api.add('STATUS-ONLY', applicant='A')
    api.clear_check_state()
    page.reload()

    page.click('.nav-item[data-page="check"]')
    page.uncheck('#check-squatting')
    assert page.is_checked('#check-status')
    assert not page.is_checked('#check-squatting')

    page.click('#btn-start-check')
    page.locator('#check-result-card').wait_for(state='visible', timeout=15000)

    # 找到 /api/check 的 POST 请求并验证请求体
    check_requests = [r for r in capture_api_requests
                      if r.method == 'POST' and r.url.endswith('/api/check')]
    assert len(check_requests) == 1
    import json
    payload = json.loads(check_requests[0].post_data)
    assert payload['checkStatus'] is True
    assert payload['checkSquatting'] is False


def test_squatting_only_option_sends_correct_payload(open_app, api, fake_check_success, capture_api_requests):
    """仅抢注检查（取消状态）-> 请求体 checkStatus=false"""
    page = open_app
    api.delete_all()
    api.add('SQUAT-ONLY', applicant='A')
    api.clear_check_state()
    page.reload()

    page.click('.nav-item[data-page="check"]')
    page.uncheck('#check-status')
    assert not page.is_checked('#check-status')
    assert page.is_checked('#check-squatting')

    page.click('#btn-start-check')
    page.locator('#check-result-card').wait_for(state='visible', timeout=15000)

    check_requests = [r for r in capture_api_requests
                      if r.method == 'POST' and r.url.endswith('/api/check')]
    assert len(check_requests) == 1
    import json
    payload = json.loads(check_requests[0].post_data)
    assert payload['checkStatus'] is False
    assert payload['checkSquatting'] is True


def test_both_options_unchecked_still_starts(open_app, api, fake_check_success):
    """双选项全取消 -> 仍可启动"""
    page = open_app
    api.delete_all()
    api.add('BOTH-OFF', applicant='A')
    api.clear_check_state()
    page.reload()

    page.click('.nav-item[data-page="check"]')
    page.uncheck('#check-status')
    page.uncheck('#check-squatting')
    assert not page.is_checked('#check-status')
    assert not page.is_checked('#check-squatting')

    page.click('#btn-start-check')
    page.locator('#check-result-card').wait_for(state='visible', timeout=15000)
    expect(page.locator('.summary-card')).to_have_count(4)


def test_check_without_trademarks_redirects(open_app, api):
    """无商标时检查 -> 提示 + 跳转录入页"""
    page = open_app
    api.delete_all()
    page.reload()

    page.click('.nav-item[data-page="check"]')
    page.click('#btn-start-check')

    page.get_by_text('请先添加商标').wait_for()
    expect(page.locator('#page-input.active')).to_have_count(1)
