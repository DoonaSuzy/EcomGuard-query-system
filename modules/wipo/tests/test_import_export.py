"""
批量导入 / 导出测试

覆盖场景:
    - CSV 文件导入
    - JSON 文件导入（数组结构与带 trademarks 键的结构）
    - 导入时自动跳过重复商标
    - 非法文件类型报错
    - 空文件（``[]`` 与 0 字节两种）导入
    - 导出 JSON / CSV 内容正确
"""

import csv


def _write_csv(path, rows):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['brandName', 'applicant', 'registrationNumber', 'niceClass', 'notes'])
        writer.writerows(rows)


def test_import_csv(open_app, api, tmp_path):
    """从 CSV 文件批量导入商标"""
    page = open_app
    api.delete_all()

    csv_file = tmp_path / 'import.csv'
    _write_csv(csv_file, [
        ['IMPORT-A', 'Applicant A', 'R-A', '9, 35', '备注A'],
        ['IMPORT-B', 'Applicant B', 'R-B', '42', ''],
        ['品牌中文C', '', '', '', ''],
    ])

    page.set_input_files('#import-file', str(csv_file))
    page.get_by_text('导入成功').wait_for()

    records = {t['brandName']: t for t in api.list()}
    assert len(records) == 3
    assert records['IMPORT-A']['applicant'] == 'Applicant A'
    assert records['IMPORT-A']['niceClass'] == ['9', '35']
    assert records['品牌中文C']['applicant'] == ''


def test_import_json_array(open_app, api, tmp_path):
    """从 JSON 数组结构文件导入"""
    page = open_app
    api.delete_all()

    import json
    json_file = tmp_path / 'import.json'
    json_file.write_text(json.dumps([
        {'brandName': 'JSON-ONE', 'applicant': 'A1', 'registrationNumber': 'R1',
         'niceClass': ['9'], 'notes': 'x'},
        {'brandName': 'JSON-TWO', 'applicant': 'A2'},
    ]), encoding='utf-8')

    page.set_input_files('#import-file', str(json_file))
    page.get_by_text('导入成功').wait_for()

    assert len(api.list()) == 2


def test_import_skips_duplicates(open_app, api, tmp_path):
    """已存在的同名商标在导入时被跳过"""
    page = open_app
    api.delete_all()
    api.add('EXIST-BRAND')

    import json
    json_file = tmp_path / 'dup.json'
    json_file.write_text(json.dumps([
        {'brandName': 'EXIST-BRAND'},
        {'brandName': 'NEW-BRAND'},
    ]), encoding='utf-8')

    page.set_input_files('#import-file', str(json_file))
    page.get_by_text('导入成功').wait_for()

    records = [t['brandName'] for t in api.list()]
    assert sorted(records) == ['EXIST-BRAND', 'NEW-BRAND']


def test_import_invalid_file_type(open_app, tmp_path):
    """不支持的文件类型导入失败并提示"""
    page = open_app

    bad_file = tmp_path / 'import.txt'
    bad_file.write_text('not a trademark', encoding='utf-8')

    page.set_input_files('#import-file', str(bad_file))
    page.get_by_text('导入失败').wait_for()
    assert '仅支持 JSON 和 CSV' in page.locator('#import-status').inner_text()


def test_import_empty_file(open_app, api, tmp_path):
    """空文件导入 -> 导入 0 个商标

    ``[]`` 是**合法 JSON 但内容为空**：服务层解析成功、只是 0 条记录。
    """
    page = open_app
    api.delete_all()

    empty_file = tmp_path / 'empty.json'
    empty_file.write_text('[]', encoding='utf-8')

    page.set_input_files('#import-file', str(empty_file))
    page.get_by_text('导入成功').wait_for()
    assert '0' in page.locator('#import-status').inner_text()
    assert len(api.list()) == 0


def test_import_zero_byte_file(open_app, api, tmp_path):
    """**零字节**文件导入 -> 同样按「导入 0 条」处理，不能报错。

    与 test_import_empty_file 的区别：那个是内容为 ``[]`` 的合法 JSON，
    这个是 0 字节。旧 Flask 版两种情况都返回「导入 0 条」。

    新栈这里曾真实回归过一次：``UploadFile.read()`` 对 0 字节文件返回
    ``b''``，若不在路由层显式判空并短路，就会掉进 JSON 解析失败分支，
    把「空文件」误报成「内容为空或格式不正确」（400）。
    """
    page = open_app
    api.delete_all()

    empty_file = tmp_path / 'zero.json'
    empty_file.write_bytes(b'')

    page.set_input_files('#import-file', str(empty_file))
    page.get_by_text('导入成功').wait_for()
    assert '0' in page.locator('#import-status').inner_text()
    assert len(api.list()) == 0


def test_import_json_with_trademarks_key(open_app, api, tmp_path):
    """JSON 带 trademarks 键结构导入"""
    page = open_app
    api.delete_all()

    import json
    json_file = tmp_path / 'wrapped.json'
    json_file.write_text(json.dumps({
        'trademarks': [
            {'brandName': 'WRAPPED-ONE', 'applicant': 'A1'},
            {'brandName': 'WRAPPED-TWO', 'applicant': 'A2'},
        ]
    }), encoding='utf-8')

    page.set_input_files('#import-file', str(json_file))
    page.get_by_text('导入成功').wait_for()

    assert len(api.list()) == 2


def test_import_csv_special_characters(open_app, api, tmp_path):
    """CSV 导入含中文/逗号/引号等特殊字符"""
    page = open_app
    api.delete_all()

    csv_file = tmp_path / 'special.csv'
    _write_csv(csv_file, [
        ['中文商标', '测试公司', 'R-中', '9, 35', '含,逗号备注'],
        ['QUOTED"BRAND', 'O\'Brien Co.', '', '', ''],
    ])

    page.set_input_files('#import-file', str(csv_file))
    page.get_by_text('导入成功').wait_for()

    records = {t['brandName']: t for t in api.list()}
    assert len(records) == 2
    assert records['中文商标']['applicant'] == '测试公司'
    assert records['中文商标']['notes'] == '含,逗号备注'
    assert records['QUOTED"BRAND']['applicant'] == "O'Brien Co."


def test_export_json(open_app, api):
    """导出 JSON 包含全部商标数据"""
    page = open_app
    api.delete_all()
    api.add('EXP-JSON-1', applicant='Applicant', nice_class=['9', '35'])
    api.add('EXP-JSON-2')

    with page.expect_response(
            lambda r: r.request.method == 'GET' and 'format=json' in r.url) as resp_info:
        page.click('#btn-export-json')
    data = resp_info.value.json()
    assert len(data['trademarks']) == 2
    brands = {t['brandName'] for t in data['trademarks']}
    assert brands == {'EXP-JSON-1', 'EXP-JSON-2'}


def test_export_csv(open_app, api):
    """导出 CSV 文件头与内容正确"""
    page = open_app
    api.delete_all()
    api.add('EXP-CSV-1', applicant='CSV Applicant', nice_class=['9', '35'])

    with page.expect_download() as dl_info:
        page.click('#btn-export-csv')
    download = dl_info.value
    assert download.suggested_filename == 'trademarks.csv'

    content = ''
    # utf-8-sig：新版导出为兼容 Excel 加了 BOM（见 io_routes.py:47），
    # 读回时必须剥掉，否则第一列名会变成 '\ufeffbrandName'
    with open(download.path(), encoding='utf-8-sig') as f:
        content = f.read()
    lines = content.strip().splitlines()
    assert lines[0] == 'brandName,applicant,registrationNumber,niceClass,notes'
    assert 'EXP-CSV-1' in content
    assert 'CSV Applicant' in content
    assert '9,35' in content
