"""
商标录入表单测试

覆盖场景:
    - 完整表单填写与提交（含中文、尼斯分类解析）
    - 表单校验：空名称、单字符、超长、重复商标
    - 字符计数器实时更新
    - 重置按钮清空表单
    - 编辑回填与更新流程
"""

import uuid


def _unique(prefix):
    return f'{prefix}-{uuid.uuid4().hex[:6]}'.upper()


def test_add_trademark_with_all_fields(open_app, api):
    """完整填写表单（含中文商标、多尼斯分类）提交成功"""
    page = open_app
    api.delete_all()

    page.fill('#brandName', '益禾堂')
    page.fill('#applicant', '测试申请人有限公司')
    page.fill('#registrationNumber', 'TM2026-001')
    page.fill('#niceClass', '9, 35,42')
    page.fill('#notes', '品牌监控测试备注')
    page.click('#btn-submit')

    # 成功提示
    page.get_by_text('添加成功').wait_for()
    page.get_by_text('已添加').wait_for()

    # 表单已重置
    assert page.input_value('#brandName') == ''
    assert page.locator('#brandName-count').inner_text() == '0/200'

    # 数据已持久化且尼斯分类正确解析为列表
    records = api.list()
    assert len(records) == 1
    tm = records[0]
    assert tm['brandName'] == '益禾堂'
    assert tm['applicant'] == '测试申请人有限公司'
    assert tm['registrationNumber'] == 'TM2026-001'
    assert tm['niceClass'] == ['9', '35', '42']
    assert tm['notes'] == '品牌监控测试备注'


def test_add_trademark_missing_name(open_app, api):
    """空商标名称提交被拦截，显示校验错误且不产生记录"""
    page = open_app
    api.delete_all()

    page.click('#btn-submit')
    assert page.locator('#brandName-error').inner_text() == '商标名称不能为空'
    assert page.locator('#brandName').evaluate("el => el.classList.contains('error')")
    assert api.list() == []


def test_add_trademark_whitespace_name(open_app, api):
    """仅空白字符的名称同样被拦截"""
    page = open_app
    api.delete_all()

    page.fill('#brandName', '   ')
    page.click('#btn-submit')
    assert page.locator('#brandName-error').inner_text() == '商标名称不能为空'
    assert api.list() == []


def test_add_trademark_too_short(open_app, api):
    """单字符名称触发实时校验错误"""
    page = open_app
    api.delete_all()

    page.fill('#brandName', 'A')
    assert page.locator('#brandName-error').inner_text() == '商标名称至少2个字符'
    page.click('#btn-submit')
    # 提交后仍被拦截
    assert page.locator('#brandName-error').inner_text() == '商标名称至少2个字符'
    assert api.list() == []


def test_add_trademark_duplicate(open_app, api):
    """重复商标名返回 409，前端展示错误提示且不重复入库"""
    page = open_app
    api.delete_all()

    brand = _unique('DUP')
    api.add(brand)

    page.fill('#brandName', brand)
    page.click('#btn-submit')

    page.get_by_text('操作失败').wait_for()
    assert '已存在' in page.locator('#brandName-error').inner_text()
    assert len([t for t in api.list() if t['brandName'] == brand]) == 1


def test_brand_name_char_counter(open_app):
    """商标名称字符计数器实时更新"""
    page = open_app
    page.fill('#brandName', 'ABC')
    assert page.locator('#brandName-count').inner_text() == '3/200'
    page.fill('#brandName', '')
    assert page.locator('#brandName-count').inner_text() == '0/200'


def test_reset_button_clears_form(open_app):
    """重置按钮清空所有字段与校验状态"""
    page = open_app
    page.fill('#brandName', 'X')
    page.fill('#applicant', '申请人')
    page.fill('#registrationNumber', 'REG1')
    page.fill('#niceClass', '9, 35')
    page.fill('#notes', '备注')
    page.click('#btn-cancel')

    for selector in ('#brandName', '#applicant', '#registrationNumber',
                     '#niceClass', '#notes'):
        assert page.input_value(selector) == ''
    assert page.locator('#brandName-count').inner_text() == '0/200'
    assert page.locator('#brandName-error').inner_text() == ''
    assert page.locator('#btn-text').inner_text() == '添加商标'


def test_edit_trademark_flow(open_app, api):
    """列表页点击编辑 -> 表单回填 -> 修改提交 -> 更新生效"""
    page = open_app
    api.delete_all()
    api.add('ORIGINAL-NAME', applicant='原申请人',
            registration_number='REG-001', nice_class=['9', '35'])

    page.reload()
    page.click('.nav-item[data-page="list"]')

    # 点击编辑按钮（行内第一个图标按钮）
    page.locator('#trademark-table-body tr').first.locator(
        '.btn-icon[title="编辑"]').click()

    # 回填校验
    assert page.input_value('#brandName') == 'ORIGINAL-NAME'
    assert page.input_value('#applicant') == '原申请人'
    assert page.input_value('#registrationNumber') == 'REG-001'
    assert page.input_value('#niceClass') == '9, 35'
    assert page.locator('#btn-text').inner_text() == '更新商标'

    # 修改并提交
    page.fill('#brandName', 'UPDATED-NAME')
    page.click('#btn-submit')
    page.get_by_text('更新成功').wait_for()

    records = api.list()
    assert len(records) == 1
    assert records[0]['brandName'] == 'UPDATED-NAME'
    assert records[0]['applicant'] == '原申请人'

    # 列表页展示新名称
    page.click('.nav-item[data-page="list"]')
    assert page.locator('#trademark-table-body').inner_text().find('UPDATED-NAME') >= 0
    assert page.locator('#trademark-table-body').inner_text().find('ORIGINAL-NAME') < 0


def test_edit_requires_valid_name(open_app, api):
    """编辑时清空名称同样被校验拦截"""
    page = open_app
    api.delete_all()
    api.add('KEEP-NAME')
    page.reload()
    page.click('.nav-item[data-page="list"]')
    page.locator('#trademark-table-body tr').first.locator(
        '.btn-icon[title="编辑"]').click()
    page.fill('#brandName', '')
    page.click('#btn-submit')
    assert page.locator('#brandName-error').inner_text() == '商标名称不能为空'
    assert api.list()[0]['brandName'] == 'KEEP-NAME'
