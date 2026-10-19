"""
图标渲染与导出/下载回归测试（对应 2026-10-19 三处缺陷修复）

覆盖:
    1. 清单页操作图标可见（旧版 `.btn-icon` 没给 svg 尺寸，svg 固有尺寸 0×0）
    2. 商标导出 JSON 不再顶掉页面（旧版 `location.href` 会把整页导航成裸 JSON）
    3. 报告可下载 JSON / CSV（此前报告页只有「查看详情」，无任何下载入口）

这三条都断言"用户在浏览器里真正看到/拿到的东西"，不测实现细节：
图标量的是渲染尺寸，导出量的是 download 事件与文件字节，报告量的是 CSV 行。
"""
import csv
import io
import json

from playwright.sync_api import expect

# ---------------------------------------------------------------- 1. 图标可见


def test_list_action_icons_have_size(open_app, api):
    """操作列的编辑/删除图标必须有可见尺寸。

    回归点：`.btn-icon` 只写了 `padding: 6px`，没给 svg 宽高，而模板里的
    `<svg viewBox="0 0 24 24">` 也没有 width/height —— svg 固有尺寸为 0，
    shrink-to-fit 的按钮塌成 12×27，图标整块不可见。
    """
    page = open_app
    api.delete_all()
    api.add('ICON-VISIBLE')
    page.reload()
    page.click('.nav-item[data-page="list"]')
    expect(page.locator('#trademark-table-body tr')).to_have_count(1)

    for title in ('编辑', '删除'):
        box = page.locator(f'#trademark-table-body .btn-icon[title="{title}"]').first.bounding_box()
        assert box is not None, f'{title} 图标按钮不存在'
        assert box['width'] >= 16, f'{title} 按钮宽度 {box["width"]} 太小'
        assert box['height'] >= 16, f'{title} 按钮高度 {box["height"]} 太小'

    # svg 自身也必须有非零渲染尺寸（按钮盒有 padding，光看按钮会漏判）
    for title in ('编辑', '删除'):
        size = page.locator(
            f'#trademark-table-body .btn-icon[title="{title}"] svg'
        ).first.evaluate('el => { const r = el.getBoundingClientRect();'
                         ' return { w: r.width, h: r.height }; }')
        assert size['w'] >= 12, f'{title} 图标 svg 宽 {size["w"]}，图标不可见'
        assert size['h'] >= 12, f'{title} 图标 svg 高 {size["h"]}，图标不可见'


# ------------------------------------------------------- 2. 导出不顶掉页面

def test_export_json_downloads_without_navigating(open_app, api):
    """导出 JSON 必须是"下载文件"，不能把当前页面导航成裸 JSON。

    回归点：旧实现 `window.location.href = '/api/export?format=json'`。
    该分支没有 Content-Disposition（只有 csv 分支带），浏览器会把整页替换成
    JSON 文本 —— 用户点一下"导出 JSON"就离开了应用。
    """
    page = open_app
    api.delete_all()
    api.add('EXPORT-JSON-1', applicant='Applicant', nice_class=['9', '35'])
    page.reload()
    page.click('.nav-item[data-page="input"]')
    url_before = page.url

    with page.expect_download() as dl_info:
        page.click('#btn-export-json')
    download = dl_info.value
    assert download.suggested_filename == 'trademarks.json'

    # 页面没被顶走，且录入表单还在
    assert page.url == url_before, f'导出后 URL 变成 {page.url}'
    expect(page.locator('#trademark-form')).to_have_count(1)

    with open(download.path(), encoding='utf-8') as f:
        data = json.load(f)
    assert [t['brandName'] for t in data['trademarks']] == ['EXPORT-JSON-1']


def test_export_buttons_disable_while_downloading(open_app, api):
    """导出中按钮禁用并改文案，防止连点触发多次下载。"""
    page = open_app
    api.delete_all()
    api.add('EXPORT-BUSY')
    page.reload()
    page.click('.nav-item[data-page="input"]')

    with page.expect_download():
        page.click('#btn-export-csv')
    # 下载结束后必须恢复可用，否则按钮会永久卡在"导出中…"
    expect(page.locator('#btn-export-csv')).to_be_enabled()
    assert page.locator('#btn-export-csv').inner_text().strip() == '导出 CSV'


# ------------------------------------------------------------ 3. 报告下载

def _seed_reports(api, app_env, reports):
    """写入测试报告。

    ``reports`` 是 ``{文件名: 报告字典}``，调用的是 ``api.seed_reports`` 的
    真实实现——注意它**不存 ``alerts`` 字段**：告警由 ``results`` 里的
    ``hasIssue`` / ``hasSquatting`` 派生（``monitor.alerts_from_results``），
    这与生产代码「报告只存 results」的口径一致。所以造数必须把问题写进
    ``results``，写 ``alerts`` 是无效的。
    """
    api.seed_reports(app_env['reports_dir'], reports)


def _result(brand, *, abnormal=None, squatting=None, review=None):
    """构造一条 ``results`` 记录（字段名与 monitor._check_trademark 一致）。"""
    abnormal = abnormal or []
    squatting = squatting or []
    review = review or []
    return {
        'brandName': brand,
        'trademarkId': f'tid-{brand}',
        'abnormalStatuses': abnormal,
        'squattingCases': squatting,
        'needsReviewCases': review,
        'hasIssue': bool(abnormal),
        'hasSquatting': bool(squatting or review),
    }


def _report(results, **overrides):
    """构造一份报告字典。"""
    base = {
        'timestamp': '2026-01-01T00:00:00',
        'finishedAt': '2026-01-01T00:00:10',
        'total': len(results),
        'checked': len(results),
        'errors': [],
        'errorsCount': 0,
        'failedBrands': [],
        'hasIssues': any(r['hasIssue'] or r['hasSquatting'] for r in results),
        'results': results,
    }
    base.update(overrides)
    return base


def _wait_reports(page, count):
    page.click('.nav-item[data-page="reports"]')
    expect(page.locator('.report-item')).to_have_count(count)


def test_report_csv_download(open_app, api, app_env):
    """报告行上的「下载 CSV」产出逐条明细表。"""
    _seed_reports(api, app_env, {
        'report_20260101_000000.json': _report([
            _result('REPORT-CSV', abnormal=[
                {'registrationNumber': 'R-1', 'status': 'Ended',
                 'office': 'JP', 'expiryDate': ''},
            ]),
        ]),
    })
    page = open_app
    page.reload()
    _wait_reports(page, 1)

    with page.expect_download() as dl_info:
        page.locator('.report-item').first.locator(
            '.js-report-download[data-format="csv"]').click()
    download = dl_info.value
    assert download.suggested_filename == 'report_20260101_000000.csv'

    # utf-8-sig：前端导出带 BOM（为 Excel 兼容），读回时剥掉
    with open(download.path(), encoding='utf-8-sig') as f:
        text = f.read()
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ['商标名称', '问题类型', '注册号', '登记状态', '局',
                       '申请人', '申请人国家', '尼斯分类', '风险等级', '说明']
    assert rows[1][0] == 'REPORT-CSV'
    assert rows[1][1] == '状态异常'
    assert rows[1][2] == 'R-1'
    assert rows[1][3] == 'Ended'
    assert rows[1][4] == 'JP'


def test_report_csv_escapes_commas(open_app, api, app_env):
    """含逗号的字段必须整体加引号，否则列会错位。"""
    _seed_reports(api, app_env, {
        'report_20260101_000000.json': _report([
            _result('ACME, INC', review=[{
                'brandName': 'ACME, INC', 'applicant': 'A "B" Co.',
                'applicantCountryCode': 'US', 'status': 'Pending',
                'registrationNumber': '', 'niceClass': [9, 35], 'office': 'US',
                'riskLevel': 'medium', 'reason': '名称相同，需人工确认',
            }]),
        ]),
    })
    page = open_app
    page.reload()
    _wait_reports(page, 1)

    with page.expect_download() as dl_info:
        page.locator('.report-item').first.locator(
            '.js-report-download[data-format="csv"]').click()
    with open(dl_info.value.path(), encoding='utf-8-sig') as f:
        rows = list(csv.reader(io.StringIO(f.read())))

    # 逗号与双引号都被正确保留在同一个单元格里，而不是把行撑成更多列
    assert len(rows[1]) == 10
    assert rows[1][0] == 'ACME, INC'
    assert rows[1][5] == 'A "B" Co.'
    assert rows[1][7] == '9 35'


def test_report_json_download(open_app, api, app_env):
    """报告行上的「下载 JSON」产出与详情接口一致的结构。"""
    _seed_reports(api, app_env, {
        'report_20260102_000000.json': _report([], timestamp='2026-01-02T00:00:00'),
    })
    page = open_app
    page.reload()
    _wait_reports(page, 1)

    with page.expect_download() as dl_info:
        page.locator('.report-item').first.locator(
            '.js-report-download[data-format="json"]').click()
    download = dl_info.value
    assert download.suggested_filename == 'report_20260102_000000.json'

    with open(download.path(), encoding='utf-8') as f:
        data = json.load(f)
    assert data['total'] == 0
    assert data['timestamp'] == '2026-01-02T00:00:00'
    assert data['alerts'] == []


def test_report_download_does_not_open_detail(open_app, api, app_env):
    """点下载不能顺带打开详情卡（下载按钮在整条 .report-item 的 click 里）。"""
    _seed_reports(api, app_env, {
        'report_20260103_000000.json': _report([], timestamp='2026-01-03T00:00:00'),
    })
    page = open_app
    page.reload()
    _wait_reports(page, 1)

    with page.expect_download():
        page.locator('.report-item').first.locator(
            '.js-report-download[data-format="csv"]').click()
    # @click.stop 生效：详情卡保持关闭
    expect(page.locator('#report-detail-card')).to_have_count(0)


def test_report_detail_card_has_download(open_app, api, app_env):
    """打开详情后也能直接下载（不必回到列表行）。"""
    _seed_reports(api, app_env, {
        'report_20260104_000000.json': _report([], timestamp='2026-01-04T00:00:00'),
    })
    page = open_app
    page.reload()
    _wait_reports(page, 1)
    page.locator('.report-item').first.click()
    expect(page.locator('#report-detail-card')).to_have_count(1)

    with page.expect_download() as dl_info:
        page.locator('#report-detail-card .js-report-download[data-format="csv"]').click()
    assert dl_info.value.suggested_filename == 'report_20260104_000000.csv'
