"""
Toast 通知交互测试

覆盖场景:
    - 成功操作 -> Toast 出现 + 内容正确
    - Toast 自动消失（4s）
    - 手动关闭 Toast
    - 错误操作 -> 错误类型 Toast

断言写法约定:
    - Toast 消失属「由无到有/由有到无」的断言，用 expect(...).to_have_count() 自动重试，
      不要用 .count() 立即断言
"""

from playwright.sync_api import expect


def test_success_toast_appears(open_app, api):
    """成功操作 -> Toast 出现 + 内容正确"""
    page = open_app
    api.delete_all()
    page.reload()

    page.fill('#brandName', 'TOAST-TEST')
    page.click('#btn-submit')

    toast = page.locator('#toast-container .toast.success').first
    toast.wait_for()
    assert '添加成功' in toast.inner_text()
    assert 'TOAST-TEST' in toast.inner_text()


def test_toast_auto_dismisses(open_app, api):
    """Toast 自动消失（4s）"""
    page = open_app
    api.delete_all()
    page.reload()

    page.fill('#brandName', 'AUTO-DISMISS')
    page.click('#btn-submit')

    toast = page.locator('#toast-container .toast.success').first
    toast.wait_for()

    # 等待 Toast 自动消失（4s + 300ms 动画）
    page.wait_for_timeout(5000)
    expect(page.locator('#toast-container .toast')).to_have_count(0)


def test_toast_manual_close(open_app, api):
    """手动关闭 Toast"""
    page = open_app
    api.delete_all()
    page.reload()

    page.fill('#brandName', 'MANUAL-CLOSE')
    page.click('#btn-submit')

    toast = page.locator('#toast-container .toast.success').first
    toast.wait_for()

    # 点击关闭按钮
    toast.locator('.toast-close').click()
    expect(page.locator('#toast-container .toast')).to_have_count(0)


def test_error_toast_appears(open_app, api):
    """错误操作 -> 错误类型 Toast"""
    page = open_app
    api.delete_all()
    api.add('DUP-NAME', applicant='A')
    page.reload()

    # 提交重复商标名
    page.fill('#brandName', 'dup-name')
    page.click('#btn-submit')

    toast = page.locator('#toast-container .toast.error').first
    toast.wait_for()
    assert '操作失败' in toast.inner_text()
    assert '已存在' in toast.inner_text()
