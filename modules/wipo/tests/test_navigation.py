"""
页面导航测试

覆盖场景:
    - 应用默认加载到"商标录入"页
    - 通过侧边栏在 4 个页面间切换，标题与激活状态正确联动
    - 顶部商标总数统计正确

断言写法约定:
    - 数据由 async fetch 加载（app.js loadTrademarks），DOM 就绪不等于数据到位，
      一律用 expect(...).to_have_*() 自动重试，不要用 .count()/inner_text() 立即断言
      （历史故障：test_trademark_count_displayed 在 reload 后立刻读 #total-count，
        实测 6 次中有 1 次读到 0，耗时 13ms 与正常 14-20ms 无差异）
"""

from playwright.sync_api import expect

PAGES = [
    ('input', '商标录入', 'page-input'),
    ('list', '商标清单', 'page-list'),
    ('check', '状态检查', 'page-check'),
    ('reports', '检查报告', 'page-reports'),
]


def test_default_page_is_input(open_app):
    """应用默认打开"商标录入"页面"""
    page = open_app
    expect(page.locator('#page-title')).to_have_text('商标录入')
    expect(page.locator('#page-input.active')).to_have_count(1)
    expect(page.locator('.nav-item[data-page="input"].active')).to_have_count(1)
    # 侧边栏品牌标识可见
    assert page.locator('.logo').is_visible()
    assert page.locator('.logo span').inner_text() == '商标监控'


def test_navigate_all_pages(open_app):
    """遍历侧边栏全部导航项，验证标题和页面激活状态联动"""
    page = open_app
    for key, title, section_id in PAGES:
        page.click(f'.nav-item[data-page="{key}"]')
        expect(page.locator('#page-title')).to_have_text(title)
        expect(page.locator(f'#{section_id}.active')).to_have_count(1)
        expect(page.locator(f'.nav-item[data-page="{key}"].active')).to_have_count(1)
        expect(page.locator('.page.active')).to_have_count(1)


def test_trademark_count_displayed(open_app, api):
    """顶部商标总数与数据一致"""
    page = open_app
    api.delete_all()
    api.add('ALPHA-BRAND')
    api.add('BETA-BRAND')
    page.reload()
    expect(page.locator('#total-count')).to_have_text('2')


def test_page_content_visible_after_navigation(open_app):
    """每个页面切换后对应的核心元素可见"""
    page = open_app
    page.click('.nav-item[data-page="list"]')
    assert page.locator('#search-input').is_visible()
    page.click('.nav-item[data-page="check"]')
    assert page.locator('#btn-start-check').is_visible()
    page.click('.nav-item[data-page="reports"]')
    assert page.locator('#reports-list').is_visible()


def test_switch_page_during_check_keeps_state(open_app, api, fake_check_success):
    """检查进行中切换页面，返回后检查状态保持"""
    page = open_app
    api.delete_all()
    api.add('NAV-CHECK', applicant='A')
    api.clear_check_state()
    page.reload()

    # 启动检查
    page.click('.nav-item[data-page="check"]')
    page.click('#btn-start-check')
    page.get_by_text('检查进行中').wait_for()

    # 检查进行中切换到其他页面
    page.click('.nav-item[data-page="list"]')
    expect(page.locator('#page-list.active')).to_have_count(1)

    # 返回检查页，结果应已展示
    page.click('.nav-item[data-page="check"]')
    page.locator('#check-result-card').wait_for(state='visible', timeout=15000)
    expect(page.locator('.summary-card')).to_have_count(4)
