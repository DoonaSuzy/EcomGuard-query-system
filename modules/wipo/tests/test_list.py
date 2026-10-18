"""
商标清单与删除流程测试

覆盖场景:
    - 清单表格渲染
    - 搜索过滤（命中/无结果/清空）
    - 全选/取消全选与批量删除按钮状态
    - 单条删除：确认弹窗（确认/取消/点击遮罩关闭）
    - 批量删除：选中多条确认删除
    - 空列表占位状态

断言写法约定:
    - 行数据由 async fetch 加载，一律用 expect(...).to_have_count() 自动重试
"""

from playwright.sync_api import expect


def _seed(api, names):
    api.delete_all()
    for name in names:
        api.add(name, applicant=f'{name} Applicant Co.')


def test_list_renders_rows(open_app, api):
    """清单页按数据渲染行内容"""
    page = open_app
    _seed(api, ['ROW-ALPHA', 'ROW-BETA'])
    page.reload()
    page.click('.nav-item[data-page="list"]')

    rows = page.locator('#trademark-table-body tr')
    expect(rows).to_have_count(2)
    assert rows.first.locator('.brand-name').inner_text() == 'ROW-ALPHA'
    assert rows.nth(1).locator('.brand-name').inner_text() == 'ROW-BETA'
    # 列结构: 0=勾选 1=名称 2=申请人 3=注册号 4=尼斯分类 5=备注 6=时间 7=操作
    assert rows.first.locator('td').nth(2).inner_text() == 'ROW-ALPHA Applicant Co.'


def test_search_filters_rows(open_app, api):
    """搜索框过滤：命中、无结果提示、清空恢复"""
    page = open_app
    _seed(api, ['APPLE', 'SAMSUNG', 'HUAWEI'])
    page.reload()
    page.click('.nav-item[data-page="list"]')

    page.fill('#search-input', 'samsung')
    expect(page.locator('#trademark-table-body tr')).to_have_count(1)
    assert page.locator('#trademark-table-body .brand-name').inner_text() == 'SAMSUNG'

    page.fill('#search-input', 'NOPE-BRAND')
    expect(page.locator('#trademark-table-body tr')).to_have_count(0)
    assert '未找到匹配的商标' in page.locator('#empty-state').inner_text()

    page.fill('#search-input', '')
    expect(page.locator('#trademark-table-body tr')).to_have_count(3)
    assert page.locator('#empty-state').is_hidden()


def test_empty_state_when_no_data(open_app, api):
    """无数据时展示空状态提示"""
    page = open_app
    _seed(api, [])
    page.reload()
    page.click('.nav-item[data-page="list"]')
    expect(page.locator('#empty-state')).to_contain_text('暂无商标数据')
    expect(page.locator('#trademark-table-body tr')).to_have_count(0)


def test_select_all_toggles_batch_delete(open_app, api):
    """全选/取消全选联动批量删除按钮"""
    page = open_app
    _seed(api, ['SEL-ONE', 'SEL-TWO'])
    page.reload()
    page.click('.nav-item[data-page="list"]')

    # 初始禁用
    assert page.locator('#btn-batch-delete').is_disabled()

    # 全选
    page.check('#select-all')
    assert page.locator('#btn-batch-delete').inner_text().strip() == '批量删除 (2)'
    assert not page.locator('#btn-batch-delete').is_disabled()
    expect(page.locator('#trademark-table-body input[type="checkbox"]:checked')).to_have_count(2)

    # 取消全选
    page.uncheck('#select-all')
    assert page.locator('#btn-batch-delete').is_disabled()
    expect(page.locator('#trademark-table-body input[type="checkbox"]:checked')).to_have_count(0)


def test_single_delete_confirm(open_app, api):
    """单条删除：确认弹窗后商标被移除"""
    page = open_app
    _seed(api, ['DEL-KEEP', 'DEL-REMOVE'])
    page.reload()
    page.click('.nav-item[data-page="list"]')

    # 删除第二行
    page.locator('#trademark-table-body tr').nth(1).locator(
        '.btn-icon[title="删除"]').click()

    # 弹窗内容
    assert page.locator('#modal-overlay').is_visible()
    assert page.locator('#modal-title').inner_text() == '确认删除'
    assert 'DEL-REMOVE' in page.locator('#modal-message').inner_text()

    page.click('#modal-confirm')
    page.get_by_text('删除成功').wait_for()

    records = [t['brandName'] for t in api.list()]
    assert records == ['DEL-KEEP']


def test_single_delete_cancel(open_app, api):
    """单条删除：点击取消不删除"""
    page = open_app
    _seed(api, ['DEL-ONE'])
    page.reload()
    page.click('.nav-item[data-page="list"]')

    page.locator('#trademark-table-body tr').first.locator(
        '.btn-icon[title="删除"]').click()
    assert page.locator('#modal-overlay').is_visible()

    page.click('#modal-cancel')
    assert page.locator('#modal-overlay').is_hidden()
    assert [t['brandName'] for t in api.list()] == ['DEL-ONE']


def test_single_delete_overlay_dismiss(open_app, api):
    """单条删除：点击遮罩空白处关闭弹窗"""
    page = open_app
    _seed(api, ['DEL-TWO'])
    page.reload()
    page.click('.nav-item[data-page="list"]')

    page.locator('#trademark-table-body tr').first.locator(
        '.btn-icon[title="删除"]').click()
    assert page.locator('#modal-overlay').is_visible()

    # 点击弹窗外部区域（遮罩左上角）
    page.locator('#modal-overlay').click(position={'x': 5, 'y': 5})
    assert page.locator('#modal-overlay').is_hidden()
    assert len(api.list()) == 1


def test_batch_delete_flow(open_app, api):
    """批量删除：选中多条 -> 确认 -> 全部移除并显示空状态"""
    page = open_app
    _seed(api, ['BATCH-A', 'BATCH-B', 'BATCH-C'])
    page.reload()
    page.click('.nav-item[data-page="list"]')

    # 勾选前两行
    page.locator('#trademark-table-body tr').nth(0).locator('input[type="checkbox"]').check()
    page.locator('#trademark-table-body tr').nth(1).locator('input[type="checkbox"]').check()
    assert page.locator('#btn-batch-delete').inner_text().strip() == '批量删除 (2)'

    page.click('#btn-batch-delete')
    assert '2' in page.locator('#modal-message').inner_text()
    page.click('#modal-confirm')
    page.get_by_text('删除成功').wait_for()

    records = [t['brandName'] for t in api.list()]
    assert records == ['BATCH-C']
    # 批量删除按钮回到禁用态
    assert page.locator('#btn-batch-delete').is_disabled()
