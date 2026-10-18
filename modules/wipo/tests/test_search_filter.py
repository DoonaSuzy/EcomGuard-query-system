"""
搜索过滤测试

覆盖场景:
    - 按商标名搜索 -> 过滤结果
    - 按申请人搜索 -> 过滤结果
    - 无匹配 -> 显示"未找到匹配的商标"
    - 清空搜索 -> 恢复全部
    - 搜索不区分大小写

断言写法约定:
    - 行数据由 async fetch 加载，一律用 expect(...).to_have_count() 自动重试
"""

from playwright.sync_api import expect


def test_search_by_brand_name(open_app, api, seeded_trademarks):
    """按商标名搜索 -> 过滤结果"""
    page = open_app
    page.reload()

    page.click('.nav-item[data-page="list"]')
    page.fill('#search-input', 'APPLE')

    # 只显示 APPLE
    rows = page.locator('#trademark-table-body tr')
    expect(rows).to_have_count(1)
    assert 'APPLE' in rows.first.inner_text()
    assert 'GOOGLE' not in page.locator('#trademark-table-body').inner_text()


def test_search_by_applicant(open_app, api, seeded_trademarks):
    """按申请人搜索 -> 过滤结果"""
    page = open_app
    page.reload()

    page.click('.nav-item[data-page="list"]')
    page.fill('#search-input', 'Google')

    rows = page.locator('#trademark-table-body tr')
    expect(rows).to_have_count(1)
    assert 'GOOGLE' in rows.first.inner_text()


def test_search_no_match_shows_empty(open_app, api, seeded_trademarks):
    """无匹配 -> 显示'未找到匹配的商标'"""
    page = open_app
    page.reload()

    page.click('.nav-item[data-page="list"]')
    page.fill('#search-input', 'NONEXISTENT')

    page.locator('#empty-state').wait_for(state='visible')
    expect(page.locator('#empty-state')).to_contain_text('未找到匹配的商标')


def test_clear_search_restores_all(open_app, api, seeded_trademarks):
    """清空搜索 -> 恢复全部"""
    page = open_app
    page.reload()

    page.click('.nav-item[data-page="list"]')
    page.fill('#search-input', 'APPLE')
    expect(page.locator('#trademark-table-body tr')).to_have_count(1)

    page.fill('#search-input', '')
    expect(page.locator('#trademark-table-body tr')).to_have_count(3)


def test_search_case_insensitive(open_app, api, seeded_trademarks):
    """搜索不区分大小写"""
    page = open_app
    page.reload()

    page.click('.nav-item[data-page="list"]')
    page.fill('#search-input', 'apple')

    rows = page.locator('#trademark-table-body tr')
    expect(rows).to_have_count(1)
    assert 'APPLE' in rows.first.inner_text()
