"""
批量操作测试

覆盖场景:
    - 无选中时批量删除按钮禁用
    - 勾选 1 个 -> 按钮启用 + 显示"(1)"
    - 全选 -> 全部勾选 + 按钮显示总数
    - 全选后取消 -> 全部取消 + 按钮禁用
    - 批量删除确认 -> 列表清空 + 空态显示
    - 批量删除取消 -> 商标保留

断言写法约定:
    - 行数据由 async fetch 加载，一律用 expect(...).to_have_count() 自动重试
"""

from playwright.sync_api import expect


def test_batch_delete_button_disabled_by_default(open_app, api, seeded_trademarks):
    """无选中时批量删除按钮禁用"""
    page = open_app
    page.reload()

    page.click('.nav-item[data-page="list"]')
    assert page.locator('#btn-batch-delete').is_disabled()


def test_select_one_enables_batch_delete(open_app, api, seeded_trademarks):
    """勾选 1 个 -> 按钮启用 + 显示'(1)'"""
    page = open_app
    page.reload()

    page.click('.nav-item[data-page="list"]')
    # 勾选第一行的复选框
    page.locator('#trademark-table-body input[type="checkbox"]').first.check()
    assert page.locator('#btn-batch-delete').is_enabled()
    assert '(1)' in page.locator('#btn-batch-delete').inner_text()


def test_select_all_checks_all(open_app, api, seeded_trademarks):
    """全选 -> 全部勾选 + 按钮显示总数"""
    page = open_app
    page.reload()

    page.click('.nav-item[data-page="list"]')
    page.check('#select-all')

    # 所有行复选框都被勾选
    checkboxes = page.locator('#trademark-table-body input[type="checkbox"]')
    expect(checkboxes).to_have_count(3)
    for i in range(3):
        assert checkboxes.nth(i).is_checked()
    assert '(3)' in page.locator('#btn-batch-delete').inner_text()


def test_unselect_all_disables_button(open_app, api, seeded_trademarks):
    """全选后取消 -> 全部取消 + 按钮禁用"""
    page = open_app
    page.reload()

    page.click('.nav-item[data-page="list"]')
    page.check('#select-all')
    assert page.locator('#btn-batch-delete').is_enabled()

    page.uncheck('#select-all')
    assert page.locator('#btn-batch-delete').is_disabled()


def test_batch_delete_confirm_clears_list(open_app, api, seeded_trademarks):
    """批量删除确认 -> 列表清空 + 空态显示"""
    page = open_app
    page.reload()

    page.click('.nav-item[data-page="list"]')
    page.check('#select-all')
    page.click('#btn-batch-delete')

    # 模态框出现
    page.locator('#modal-overlay').wait_for(state='visible')
    assert '3' in page.locator('#modal-message').inner_text()

    page.click('#modal-confirm')

    # Toast 删除成功
    page.locator('#toast-container').get_by_text('删除成功').first.wait_for()
    # 空态显示
    page.locator('#empty-state').wait_for(state='visible')
    assert '暂无商标数据' in page.locator('#empty-state').inner_text()
    # 按钮恢复禁用
    assert page.locator('#btn-batch-delete').is_disabled()

def test_batch_delete_cancel_keeps_trademarks(open_app, api, seeded_trademarks):
    """批量删除取消 -> 商标保留"""
    page = open_app
    page.reload()

    page.click('.nav-item[data-page="list"]')
    page.check('#select-all')
    page.click('#btn-batch-delete')

    page.locator('#modal-overlay').wait_for(state='visible')
    page.click('#modal-cancel')

    page.locator('#modal-overlay').wait_for(state='hidden')
    # 商标仍在
    expect(page.locator('#trademark-table-body tr')).to_have_count(3)
