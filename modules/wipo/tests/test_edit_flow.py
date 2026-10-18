"""
编辑流程测试

覆盖场景:
    - 点击编辑 -> 表单回填 + 按钮变"更新商标"
    - 编辑后提交 -> 列表数据更新 + Toast"更新成功"
    - 编辑后重置 -> 恢复"添加商标"模式
    - 编辑时清空商标名 -> 校验错误

断言写法约定:
    - 页面/列表状态由 async 数据驱动，一律用 expect(...).to_have_*()
"""

from playwright.sync_api import expect


def test_edit_fills_form_and_switches_page(open_app, api):
    """点击编辑 -> 表单回填 + 按钮变'更新商标'"""
    page = open_app
    api.delete_all()
    api.add('APPLE', applicant='Apple Inc.', registration_number='TM001',
            nice_class=['9', '35'], notes='原始备注')
    page.reload()

    # 进入清单页并点击编辑按钮
    page.click('.nav-item[data-page="list"]')
    page.locator('.btn-icon[title="编辑"]').first.click()

    # 自动切回录入页
    expect(page.locator('#page-input.active')).to_have_count(1)
    assert page.locator('#brandName').input_value() == 'APPLE'
    assert page.locator('#applicant').input_value() == 'Apple Inc.'
    assert page.locator('#registrationNumber').input_value() == 'TM001'
    assert page.locator('#niceClass').input_value() == '9, 35'
    assert page.locator('#notes').input_value() == '原始备注'
    assert '更新商标' in page.locator('#btn-text').inner_text()


def test_edit_submit_updates_list(open_app, api):
    """编辑后提交 -> 列表数据更新 + Toast'更新成功'"""
    page = open_app
    api.delete_all()
    api.add('APPLE', applicant='Apple Inc.', registration_number='TM001')
    page.reload()

    # 进入清单页并点击编辑
    page.click('.nav-item[data-page="list"]')
    page.locator('.btn-icon[title="编辑"]').first.click()

    # 修改字段并提交
    page.fill('#brandName', 'APPLE INC')
    page.fill('#applicant', 'Apple Inc. (Updated)')
    page.click('#btn-submit')

    # Toast 更新成功
    page.locator('#toast-container').get_by_text('更新成功').first.wait_for()

    # 列表数据已更新
    page.click('.nav-item[data-page="list"]')
    page.get_by_role('cell', name='APPLE INC', exact=True).wait_for()
    page.get_by_role('cell', name='Apple Inc. (Updated)', exact=True).wait_for()
    # 旧名称不再存在
    expect(page.get_by_role('cell', name='APPLE', exact=True)).to_have_count(0)


def test_edit_reset_restores_add_mode(open_app, api):
    """编辑后重置 -> 恢复'添加商标'模式"""
    page = open_app
    api.delete_all()
    api.add('APPLE', applicant='Apple Inc.')
    page.reload()

    # 进入清单页并点击编辑
    page.click('.nav-item[data-page="list"]')
    page.locator('.btn-icon[title="编辑"]').first.click()

    # 表单已回填
    assert page.locator('#brandName').input_value() == 'APPLE'

    # 点击重置
    page.click('#btn-cancel')

    # 表单清空 + 按钮恢复
    assert page.locator('#brandName').input_value() == ''
    assert '添加商标' in page.locator('#btn-text').inner_text()


def test_edit_clear_brand_name_shows_error(open_app, api):
    """编辑时清空商标名 -> 校验错误"""
    page = open_app
    api.delete_all()
    api.add('APPLE', applicant='Apple Inc.')
    page.reload()

    # 进入清单页并点击编辑
    page.click('.nav-item[data-page="list"]')
    page.locator('.btn-icon[title="编辑"]').first.click()

    # 清空商标名并提交
    page.fill('#brandName', '')
    page.click('#btn-submit')

    page.locator('#brandName-error').wait_for()
    assert '商标名称不能为空' in page.locator('#brandName-error').inner_text()
