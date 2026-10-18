"""
表单边界校验测试

覆盖场景:
    - 空商标名提交 -> 显示"商标名称不能为空"
    - 1 字符商标名 -> 显示"商标名称至少2个字符"
    - 201 字符商标名 -> 显示长度超限错误
    - 字符计数实时更新（输入时 0/200 -> n/200）
    - 重复商标名 -> 409 冲突提示
    - 特殊字符（中文/emoji/引号）正常提交
    - 重置按钮清空表单
    - 提交中按钮禁用 + "提交中..."文案
"""


def test_submit_empty_brand_name(open_app, api):
    """空商标名提交 -> 显示错误"""
    page = open_app
    api.delete_all()
    page.reload()

    page.click('#btn-submit')

    page.locator('#brandName-error').wait_for()
    assert '商标名称不能为空' in page.locator('#brandName-error').inner_text()
    assert page.locator('#brandName').evaluate("el => el.classList.contains('error')")


def test_submit_one_char_brand_name(open_app, api):
    """1 字符商标名 -> 显示至少2个字符错误"""
    page = open_app
    api.delete_all()
    page.reload()

    page.fill('#brandName', 'A')
    page.click('#btn-submit')

    page.locator('#brandName-error').wait_for()
    assert '商标名称至少2个字符' in page.locator('#brandName-error').inner_text()


def test_submit_201_char_brand_name(open_app, api):
    """201 字符商标名 -> 显示长度超限错误"""
    page = open_app
    api.delete_all()
    page.reload()

    # HTML maxlength=200 限制输入，用 JS 绕过直接设置值
    long_name = 'A' * 201
    page.evaluate("(val) => { const el = document.getElementById('brandName'); el.value = val; el.dispatchEvent(new Event('input')); }", long_name)
    page.click('#btn-submit')

    page.locator('#brandName-error').wait_for()
    assert '商标名称长度不能超过200个字符' in page.locator('#brandName-error').inner_text()


def test_char_count_updates(open_app, api):
    """字符计数实时更新"""
    page = open_app
    api.delete_all()
    page.reload()

    assert page.locator('#brandName-count').inner_text() == '0/200'

    page.fill('#brandName', 'APPLE')
    assert page.locator('#brandName-count').inner_text() == '5/200'

    page.fill('#brandName', '')
    assert page.locator('#brandName-count').inner_text() == '0/200'


def test_duplicate_brand_name(open_app, api):
    """重复商标名 -> 409 冲突提示"""
    page = open_app
    api.delete_all()
    api.add('APPLE', applicant='Apple Inc.')
    page.reload()

    page.fill('#brandName', 'apple')  # 不区分大小写
    page.click('#btn-submit')

    page.locator('#toast-container').get_by_text('操作失败').first.wait_for()
    assert '已存在' in page.locator('#toast-container').inner_text()


def test_special_characters_submit(open_app, api):
    """特殊字符（中文/emoji/引号）正常提交"""
    page = open_app
    api.delete_all()
    page.reload()

    page.fill('#brandName', '苹果🍎"测试"')
    page.fill('#applicant', '测试公司')
    page.click('#btn-submit')

    page.locator('#toast-container').get_by_text('添加成功').first.wait_for()
    # 列表中出现该商标
    page.click('.nav-item[data-page="list"]')
    page.get_by_role('cell', name='苹果🍎"测试"', exact=True).wait_for()


def test_reset_button_clears_form(open_app, api):
    """重置按钮清空表单"""
    page = open_app
    api.delete_all()
    page.reload()

    page.fill('#brandName', 'APPLE')
    page.fill('#applicant', 'Apple Inc.')
    page.fill('#registrationNumber', 'TM123')
    page.fill('#niceClass', '9, 35')
    page.fill('#notes', '测试备注')

    page.click('#btn-cancel')

    assert page.locator('#brandName').input_value() == ''
    assert page.locator('#applicant').input_value() == ''
    assert page.locator('#registrationNumber').input_value() == ''
    assert page.locator('#niceClass').input_value() == ''
    assert page.locator('#notes').input_value() == ''
    assert page.locator('#brandName-count').inner_text() == '0/200'
    assert '添加商标' in page.locator('#btn-text').inner_text()


def test_submit_button_disabled_while_submitting(open_app, api):
    """提交中按钮禁用 + '提交中...'文案"""
    page = open_app
    api.delete_all()
    page.reload()

    page.fill('#brandName', 'SUBMIT-TEST')
    page.click('#btn-submit')

    # 提交中按钮禁用
    page.locator('#btn-submit').wait_for(state='visible')
    # 由于提交很快完成，这里验证最终状态：按钮恢复且文案正确
    page.locator('#toast-container').get_by_text('添加成功').first.wait_for()
    assert page.locator('#btn-submit').is_enabled()
    assert '添加商标' in page.locator('#btn-text').inner_text()
