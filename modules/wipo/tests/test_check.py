"""
状态检查流程测试

设计说明:
    检查流程会调用真实 WIPO 外部 API，为保证测试确定性与离线可运行，
    通过 monkeypatch 监控引擎（fake_check_success / fake_check_error）
    模拟成功与失败两种结果，验证完整的前端交互链路。

覆盖场景:
    - 无商标时点击检查 -> 提示并跳转录入页
    - 检查启动 -> 按钮禁用、状态提示 -> 轮询 -> 结果卡片渲染
    - 检查结果汇总卡片数值正确
    - 告警详情（异常状态 / 疑似抢注）渲染
    - 检查引擎异常 -> 前端展示失败提示并恢复按钮
    - 检查完成后自动生成报告文件

断言写法约定:
    - 轮询完成后的结果卡片/汇总数由 async 数据驱动，一律用 expect(...).to_have_*()
"""

from playwright.sync_api import expect


def test_check_without_trademarks(open_app, api):
    """无商标数据时点击检查，提示并跳转到录入页"""
    page = open_app
    api.delete_all()
    page.reload()

    page.click('.nav-item[data-page="check"]')
    page.click('#btn-start-check')

    page.get_by_text('请先添加商标').wait_for()
    expect(page.locator('#page-input.active')).to_have_count(1)
    # 未启动任何检查任务
    page.wait_for_timeout(500)
    assert page.locator('#btn-start-check').is_enabled()


def test_check_success_flow(open_app, api, fake_check_success):
    """完整检查流程：启动 -> 轮询 -> 展示结果与告警"""
    page = open_app
    api.delete_all()
    api.add('TESTBRAND', applicant='Test Company Inc.')
    api.clear_check_state()
    reports_before = len(api.reports())
    page.reload()

    page.click('.nav-item[data-page="check"]')
    page.click('#btn-start-check')

    # 检查中状态提示（引擎模拟 1.5s 耗时，此期间断言中间态）
    page.get_by_text('检查进行中').wait_for()
    assert page.locator('#btn-start-check').is_disabled()

    # 轮询完成后结果卡片展示
    page.locator('#check-result-card').wait_for(state='visible', timeout=15000)

    # 汇总卡片：总数 1、告警 1
    cards = page.locator('.summary-card')
    expect(cards).to_have_count(4)
    labels = cards.all_inner_texts()
    joined = '\n'.join(labels)
    assert '检查总数' in joined and '1' in labels[0]
    assert '告警数' in joined and '1' in labels[1]

    # 告警详情：商标名、异常状态、疑似抢注
    alert = page.locator('.alert-item')
    expect(alert).to_have_count(1)
    text = alert.inner_text()
    assert 'TESTBRAND' in text
    assert '状态异常' in text
    assert 'TM0001' in text
    assert 'Expired' in text
    assert '疑似抢注 (1)' in text
    assert 'Other Company Ltd.' in text

    # 按钮已恢复
    assert page.locator('#btn-start-check').is_enabled()
    assert '开始检查' in page.locator('#btn-start-check').inner_text()

    # 报告已自动生成
    reports_after = api.reports()
    assert len(reports_after) == reports_before + 1
    assert reports_after[0]['total'] == 1
    assert reports_after[0]['alertsCount'] == 1


def test_check_status_only_option(open_app, api, fake_check_success):
    """取消勾选抢注检查后仍可启动检查"""
    page = open_app
    api.delete_all()
    api.add('STATUS-ONLY', applicant='A')
    api.clear_check_state()
    page.reload()

    page.click('.nav-item[data-page="check"]')
    page.uncheck('#check-squatting')
    assert not page.is_checked('#check-squatting')
    assert page.is_checked('#check-status')

    page.click('#btn-start-check')
    page.locator('#check-result-card').wait_for(state='visible', timeout=15000)
    expect(page.locator('.summary-card')).to_have_count(4)


def test_check_error_flow(open_app, api, fake_check_error):
    """检查引擎异常时前端展示失败提示并恢复按钮"""
    page = open_app
    api.delete_all()
    api.add('ERROR-BRAND', applicant='A')
    api.clear_check_state()
    page.reload()

    page.click('.nav-item[data-page="check"]')
    page.click('#btn-start-check')

    page.locator('#toast-container').get_by_text('检查失败').first.wait_for()
    assert page.locator('#btn-start-check').is_enabled()
    # 无结果卡片
    assert page.locator('#check-result-card').is_hidden()
