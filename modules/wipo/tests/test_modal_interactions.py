"""
模态框交互测试

覆盖场景:
    - 删除确认 -> 确认 -> 商标删除 + Toast"删除成功"
    - 删除确认 -> 取消 -> 商标保留
    - 点击遮罩关闭模态框 -> 不执行删除
    - 模态框标题/消息内容正确

断言写法约定:
    - 列表状态由 async 数据驱动，一律用 expect(...).to_have_*()
"""

from playwright.sync_api import expect


def test_delete_confirm_removes_trademark(open_app, api):
    """删除确认 -> 确认 -> 商标删除 + Toast'删除成功'"""
    page = open_app
    api.delete_all()
    api.add('APPLE', applicant='Apple Inc.')
    page.reload()

    # 进入清单页并点击删除
    page.click('.nav-item[data-page="list"]')
    page.locator('.btn-icon.danger[title="删除"]').first.click()

    # 模态框出现
    page.locator('#modal-overlay').wait_for(state='visible')
    assert '确认删除' in page.locator('#modal-title').inner_text()
    assert 'APPLE' in page.locator('#modal-message').inner_text()

    # 点击确认
    page.click('#modal-confirm')

    # Toast 删除成功
    page.locator('#toast-container').get_by_text('删除成功').first.wait_for()
    # 列表为空态
    page.locator('#empty-state').wait_for(state='visible')
    expect(page.locator('#empty-state')).to_contain_text('暂无商标数据')


def test_delete_cancel_keeps_trademark(open_app, api):
    """删除确认 -> 取消 -> 商标保留"""
    page = open_app
    api.delete_all()
    api.add('APPLE', applicant='Apple Inc.')
    page.reload()

    # 进入清单页并点击删除
    page.click('.nav-item[data-page="list"]')
    page.locator('.btn-icon.danger[title="删除"]').first.click()

    # 模态框出现，点击取消
    page.locator('#modal-overlay').wait_for(state='visible')
    page.click('#modal-cancel')

    # 模态框关闭
    page.locator('#modal-overlay').wait_for(state='hidden')
    # 商标仍在
    page.get_by_role('cell', name='APPLE', exact=True).wait_for()
    expect(page.get_by_role('cell', name='APPLE', exact=True)).to_have_count(1)


def test_click_overlay_closes_modal(open_app, api):
    """点击遮罩关闭模态框 -> 不执行删除"""
    page = open_app
    api.delete_all()
    api.add('APPLE', applicant='Apple Inc.')
    page.reload()

    # 进入清单页并点击删除
    page.click('.nav-item[data-page="list"]')
    page.locator('.btn-icon.danger[title="删除"]').first.click()

    # 模态框出现，点击遮罩（非模态框区域）
    page.locator('#modal-overlay').wait_for(state='visible')
    page.click('#modal-overlay', position={'x': 10, 'y': 10})

    # 模态框关闭，商标保留
    page.locator('#modal-overlay').wait_for(state='hidden')
    expect(page.get_by_role('cell', name='APPLE', exact=True)).to_have_count(1)


def test_modal_title_and_message_content(open_app, api):
    """模态框标题/消息内容正确"""
    page = open_app
    api.delete_all()
    api.add('GOOGLE', applicant='Google LLC')
    page.reload()

    # 进入清单页并点击删除
    page.click('.nav-item[data-page="list"]')
    page.locator('.btn-icon.danger[title="删除"]').first.click()

    page.locator('#modal-overlay').wait_for(state='visible')
    assert page.locator('#modal-title').inner_text() == '确认删除'
    assert 'GOOGLE' in page.locator('#modal-message').inner_text()
    assert '不可撤销' in page.locator('#modal-message').inner_text()
