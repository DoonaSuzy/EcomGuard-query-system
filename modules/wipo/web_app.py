"""
WIPO 商标监控系统 - Web 应用模块

本模块基于 Flask 框架构建 Web 应用，提供商标监控的 HTTP 接口服务。

功能概述：
    - 用户商标的增删改查（CRUD）操作
    - 商标批量删除、导入、导出功能
    - 异步商标检查（状态监控、抢注检测）
    - 检查结果查询与报告管理

运行端口：5000（默认）

API 路由列表：
    GET    /                           - 首页
    GET    /api/schema                 - 获取机器可读的接口契约
    GET    /api/trademarks             - 获取所有商标列表
    POST   /api/trademarks             - 添加新商标
    PUT    /api/trademarks/<id>        - 更新指定商标信息
    DELETE /api/trademarks/<id>        - 删除指定商标
    DELETE /api/trademarks/batch       - 批量删除商标
    POST   /api/check                  - 启动异步商标检查任务
    GET    /api/check/status           - 查询检查任务状态
    GET    /api/reports                - 获取所有检查报告列表
    GET    /api/reports/<filename>     - 获取指定报告详情
    GET    /api/export                 - 导出商标数据（JSON/CSV）
    POST   /api/import                 - 导入商标数据（JSON/CSV）

依赖模块：
    wipo_client    - WIPO API 客户端
    trademark_list  - 商标列表数据结构
    monitor         - 监控引擎（检查逻辑）
"""

import json
import os
import sys
import uuid
import time
import threading
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory

# 将当前目录插入到 sys.path 首位，确保可以正确导入同目录下的自定义模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wipo_client import WipoApiClient
from trademark_list import TrademarkList
from monitor import MonitorEngine, alerts_from_results
from notification import NotificationService

# 初始化 Flask 应用，配置静态文件和模板目录
app = Flask(__name__, static_folder='static', template_folder='templates')
# 禁用 JSON 响应的 ASCII 编码，确保中文字符正常显示
app.config['JSON_AS_ASCII'] = False

# 数据存储路径配置
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')          # 用户数据目录
TRADEMARKS_FILE = os.path.join(DATA_DIR, 'user_trademarks.json')                   # 商标数据持久化文件
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')   # 检查报告存储目录
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')  # 通知配置
# 接口契约（唯一来源，由 /api/schema 暴露；docs 文档站与 CI 校验共用）
SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'docs', 'api-schema.json')

# 确保数据目录和报告目录存在，不存在则自动创建
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)


def _report_alerts_count(data: dict) -> int:
    """
    从一份报告数据里取告警数量

    新报告只存 `alertsCount`，不再重复存整份 alerts；旧报告没有该字段，
    按 results 派生（与 run_check 的判定一致），再退回读 alerts 的长度。
    三种形态都兼容，避免历史报告在列表页显示成 0 条告警。

    Args:
        data: 报告文件解析出的字典

    Returns:
        告警数量（int）
    """
    if isinstance(data.get('alertsCount'), int) and not isinstance(data.get('alertsCount'), bool):
        return data['alertsCount']
    if data.get('results'):
        return len(alerts_from_results(data['results']))
    alerts = data.get('alerts')
    return len(alerts) if isinstance(alerts, list) else 0


def load_trademarks():
    """
    加载用户商标数据

    从 JSON 文件中读取所有已保存的商标信息。如果数据文件不存在，
    则返回一个空的商标列表结构。

    Returns:
        dict: 包含 'trademarks' 键的字典，值为商标对象列表
              格式: {'trademarks': [{'id', 'brandName', 'applicant', ...}, ...]}
    """
    if os.path.exists(TRADEMARKS_FILE):
        # 以 UTF-8 编码读取 JSON 文件
        with open(TRADEMARKS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    # 文件不存在时返回空结构
    return {'trademarks': []}


def save_trademarks(data):
    """
    保存用户商标数据

    将商标数据以 JSON 格式写入持久化文件，采用 UTF-8 编码以支持中文。

    Args:
        data (dict): 包含 'trademarks' 键的字典，值为商标对象列表
    """
    # ensure_ascii=False: 允许 JSON 中直接输出中文，而不是 Unicode 转义序列
    # indent=2: JSON 格式化缩进，便于人工阅读
    with open(TRADEMARKS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.route('/')
def index():
    """
    首页路由

    功能：渲染前端首页模板

    HTTP 方法：GET

    Returns:
        str: 渲染后的 HTML 页面内容（index.html 模板）
    """
    return render_template('index.html')


@app.route('/api/schema', methods=['GET'])
def get_api_schema():
    """
    获取机器可读的接口契约

    功能：返回 docs/api-schema.json 的内容，供 AI 助手、前端工具和 CI
          校验消费，使项目具备「机器可读的能力声明」——不必再靠读
          docs_server.py 里的手写拷贝来推断接口。

    HTTP 方法：GET

    Returns:
        tuple: (JSON 响应体, HTTP 状态码)
            - 成功 (200): {'success': True, 'data': {契约文档}}
            - 契约缺失 (500): {'success': False, 'error': '...'}
    """
    if not os.path.exists(SCHEMA_FILE):
        return jsonify({
            'success': False,
            'error': '接口契约文件不存在: docs/api-schema.json'
        }), 500
    try:
        with open(SCHEMA_FILE, 'r', encoding='utf-8') as f:
            schema = json.load(f)
    except (OSError, ValueError) as e:
        return jsonify({
            'success': False,
            'error': f'接口契约文件无法解析: {e}'
        }), 500
    return jsonify({'success': True, 'data': schema})


@app.route('/api/trademarks', methods=['GET'])
def get_trademarks():
    """
    获取所有商标列表

    功能：从本地数据文件中加载并返回用户的所有商标信息

    HTTP 方法：GET

    Returns:
        tuple: (JSON 响应体, HTTP 状态码)
            - 成功 (200): {'success': True, 'data': [商标对象数组]}
    """
    # 从持久化文件加载商标数据
    data = load_trademarks()
    # 返回商标列表，data 字段包含所有商标对象
    return jsonify({'success': True, 'data': data['trademarks']})


@app.route('/api/trademarks', methods=['POST'])
def add_trademark():
    """
    添加新商标

    功能：创建一条新的商标记录并保存到本地数据文件

    HTTP 方法：POST

    请求体 (JSON):
        - brandName (str, 必填): 商标名称，长度 2-200 字符
        - applicant (str): 申请人名称
        - registrationNumber (str): 注册号
        - niceClass (list[str]): 尼斯分类列表
        - notes (str): 备注信息

    Returns:
        tuple: (JSON 响应体, HTTP 状态码)
            - 成功 (200): {'success': True, 'data': 新商标对象}
            - 失败 (400): {'success': False, 'error': 错误信息}
            - 冲突 (409): {'success': False, 'error': '商标已存在'}
    """
    # 解析请求体中的 JSON 数据
    data = request.get_json()
    
    # 提取并清洗各字段，去除首尾空白字符
    brand_name = (data.get('brandName') or '').strip()          # 商标名称（必填）
    applicant = (data.get('applicant') or '').strip()          # 申请人
    registration_number = (data.get('registrationNumber') or '').strip()  # 注册号
    nice_class = data.get('niceClass', [])                     # 尼斯分类（默认为空列表）
    notes = (data.get('notes') or '').strip()                  # 备注
    
    # 校验：商标名称不能为空
    if not brand_name:
        return jsonify({'success': False, 'error': '商标名称不能为空'}), 400
    
    # 校验：商标名称长度上限
    if len(brand_name) > 200:
        return jsonify({'success': False, 'error': '商标名称长度不能超过200个字符'}), 400
    
    # 校验：商标名称长度下限
    if len(brand_name) < 2:
        return jsonify({'success': False, 'error': '商标名称长度至少2个字符'}), 400
    
    # 检查是否存在同名商标（不区分大小写）
    existing = load_trademarks()
    for tm in existing['trademarks']:
        if tm['brandName'].lower() == brand_name.lower():
            # 409 Conflict：资源冲突，商标已存在
            return jsonify({'success': False, 'error': f'商标 "{brand_name}" 已存在'}), 409
    
    # 构建新商标对象，自动生成唯一 ID 和创建时间
    new_tm = {
        'id': str(uuid.uuid4()),                                # UUID 作为主键
        'brandName': brand_name,
        'applicant': applicant,
        'registrationNumber': registration_number,
        'niceClass': nice_class if isinstance(nice_class, list) else [],  # 确保为列表类型
        'notes': notes,
        'createdAt': datetime.now().isoformat(),                # ISO 格式创建时间
        'status': 'pending'                                     # 初始状态：待检查
    }
    
    # 将新商标追加到列表并持久化保存
    existing['trademarks'].append(new_tm)
    save_trademarks(existing)
    
    return jsonify({'success': True, 'data': new_tm})


@app.route('/api/trademarks/<trademark_id>', methods=['PUT'])
def update_trademark(trademark_id):
    """
    更新指定商标信息

    功能：根据商标 ID 更新其字段，仅更新请求体中包含的字段

    HTTP 方法：PUT

    Args:
        trademark_id (str): 商标的唯一标识符（UUID）

    请求体 (JSON) - 所有字段均可选：
        - brandName (str): 商标名称
        - applicant (str): 申请人名称
        - registrationNumber (str): 注册号
        - niceClass (list[str]): 尼斯分类列表
        - notes (str): 备注信息

    Returns:
        tuple: (JSON 响应体, HTTP 状态码)
            - 成功 (200): {'success': True, 'data': 更新后的商标对象}
            - 未找到 (404): {'success': False, 'error': '商标不存在'}
            - 校验失败 (400): {'success': False, 'error': 错误信息}
    """
    # 解析请求体数据并加载现有商标列表
    data = request.get_json()
    existing = load_trademarks()
    
    # 在列表中查找指定 ID 的商标
    found = None
    for tm in existing['trademarks']:
        if tm['id'] == trademark_id:
            found = tm
            break
    
    # 未找到对应商标，返回 404
    if not found:
        return jsonify({'success': False, 'error': '商标不存在'}), 404
    
    # 条件更新：仅更新请求体中包含的字段
    if 'brandName' in data:
        brand_name = data['brandName'].strip()
        if not brand_name:
            return jsonify({'success': False, 'error': '商标名称不能为空'}), 400
        if len(brand_name) > 200:
            return jsonify({'success': False, 'error': '商标名称长度不能超过200个字符'}), 400
        found['brandName'] = brand_name
    
    if 'applicant' in data:
        found['applicant'] = data['applicant'].strip()
    if 'registrationNumber' in data:
        found['registrationNumber'] = data['registrationNumber'].strip()
    if 'niceClass' in data:
        # 确保 niceClass 为列表类型
        found['niceClass'] = data['niceClass'] if isinstance(data['niceClass'], list) else []
    if 'notes' in data:
        found['notes'] = data['notes'].strip()
    
    # 记录更新时间并持久化保存
    found['updatedAt'] = datetime.now().isoformat()
    save_trademarks(existing)
    
    return jsonify({'success': True, 'data': found})


@app.route('/api/trademarks/<trademark_id>', methods=['DELETE'])
def delete_trademark(trademark_id):
    """
    删除指定商标

    功能：根据商标 ID 从本地数据中删除该商标记录

    HTTP 方法：DELETE

    Args:
        trademark_id (str): 商标的唯一标识符（UUID）

    Returns:
        tuple: (JSON 响应体, HTTP 状态码)
            - 成功 (200): {'success': True, 'message': '删除成功'}
            - 未找到 (404): {'success': False, 'error': '商标不存在'}
    """
    # 加载现有商标数据
    existing = load_trademarks()
    
    # 遍历查找目标商标的索引位置
    found = None
    for i, tm in enumerate(existing['trademarks']):
        if tm['id'] == trademark_id:
            found = i
            break
    
    # 未找到对应商标
    if found is None:
        return jsonify({'success': False, 'error': '商标不存在'}), 404
    
    # 从列表中移除该商标并持久化保存
    del existing['trademarks'][found]
    save_trademarks(existing)
    
    return jsonify({'success': True, 'message': '删除成功'})


@app.route('/api/trademarks/batch', methods=['DELETE'])
def batch_delete_trademarks():
    """
    批量删除商标

    功能：根据提供的 ID 列表批量删除商标

    HTTP 方法：DELETE

    请求体 (JSON):
        - ids (list[str]): 要删除的商标 ID 列表

    Returns:
        tuple: (JSON 响应体, HTTP 状态码)
            - 成功 (200): {'success': True, 'deletedCount': 删除数量}
            - 失败 (400): {'success': False, 'error': '请选择要删除的商标'}
    """
    # 解析请求体获取待删除的 ID 列表
    data = request.get_json()
    ids = data.get('ids', [])
    
    # 校验：ID 列表不能为空
    if not ids:
        return jsonify({'success': False, 'error': '请选择要删除的商标'}), 400
    
    # 加载现有数据并记录原始数量
    existing = load_trademarks()
    original_count = len(existing['trademarks'])
    
    # 通过列表过滤实现批量删除：保留不在 ids 中的商标
    existing['trademarks'] = [tm for tm in existing['trademarks'] if tm['id'] not in ids]
    save_trademarks(existing)
    
    # 计算实际删除的数量
    deleted_count = original_count - len(existing['trademarks'])
    return jsonify({'success': True, 'deletedCount': deleted_count})


@app.route('/api/check', methods=['POST'])
def check_trademarks():
    """
    启动异步商标检查任务

    功能：在后台线程中执行商标监控检查，包括状态检查和抢注检测。
          检查完成后结果保存到 data/last_check_result.json，可通过
          /api/check/status 接口查询进度。

    HTTP 方法：POST

    请求体 (JSON):
        - checkStatus (bool): 是否检查商标状态，默认 True
        - checkSquatting (bool): 是否检测抢注行为，默认 True
        - trademarkIds (list[str]): 指定检查的商标 ID 列表，为空则检查全部

    Returns:
        tuple: (JSON 响应体, HTTP 状态码)
            - 成功启动 (200): {'success': True, 'message': '检查已启动'}
            - 无商标 (400): {'success': False, 'error': '没有可检查的商标'}
    """
    # 解析请求参数，设置默认值
    data = request.get_json() or {}
    check_status = data.get('checkStatus', True)       # 是否检查商标状态
    check_squatting = data.get('checkSquatting', True)  # 是否检测抢注
    trademark_ids = data.get('trademarkIds', [])       # 指定检查的商标 ID 列表
    
    # 加载现有商标数据
    existing = load_trademarks()
    
    # 根据请求参数筛选待检查的商标
    if trademark_ids:
        # 指定了 ID 列表，仅检查匹配的商标
        trademarks = [tm for tm in existing['trademarks'] if tm['id'] in trademark_ids]
    else:
        # 未指定，检查所有商标
        trademarks = existing['trademarks']
    
    # 校验：至少需要有一个商标可供检查
    if not trademarks:
        return jsonify({'success': False, 'error': '没有可检查的商标'}), 400
    
    # 构建商标列表对象供监控引擎使用
    tm_list = TrademarkList()
    tm_list.trademarks = trademarks
    
    # 提取去重后的申请人名称列表（用于抢注检测）
    applicant_names = list(set(
        tm['applicant'] for tm in trademarks if tm.get('applicant')
    ))
    
    # 初始化监控引擎
    engine = MonitorEngine()
    
    # 加载通知配置（用于检查完成后发送告警）
    notification_config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                notification_config = json.load(f).get('notification', {})
        except Exception:
            notification_config = {}
    
    # 启动新检查前清理上一次的状态文件。
    # 否则 /api/check/status 会因为「旧结果文件仍存在」立刻返回 completed，
    # 前端会把上一次的陈旧结果当成本次检查的结果展示。
    for stale_name in ('last_check_result.json', 'last_check_error.txt'):
        stale_path = os.path.join(DATA_DIR, stale_name)
        if os.path.exists(stale_path):
            try:
                os.unlink(stale_path)
            except OSError:
                pass

    # 定义异步检查任务的执行函数
    def run_check_async():
        """
        异步检查任务执行函数

        在后台线程中运行，执行以下操作：
        1. 调用监控引擎执行检查（状态检查 + 抢注检测）
        2. 将检查结果保存为带时间戳的报告文件
        3. 将最新检查结果写入 last_check_result.json
        4. 若有告警且配置了通知渠道，发送告警通知
        5. 若发生异常，将错误信息写入 last_check_error.txt
        """
        try:
            # 执行检查并获取结果
            result = engine.run_check(tm_list, check_status, check_squatting, applicant_names)
            # 记录检查完成时间
            result['checkedAt'] = datetime.now().isoformat()
            
            # 生成带时间戳的报告文件名并保存
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            report_file = os.path.join(REPORTS_DIR, f'report_{timestamp}.json')
            engine.save_report(report_file)
            # 同时写出一份紧凑摘要，供看板/AI 低 token 消费（明细在完整报告里）
            engine.save_summary(os.path.join(REPORTS_DIR, f'report_{timestamp}.summary.json'))
            
            # 将最新检查结果写入固定路径，供状态查询接口读取
            with open(os.path.join(DATA_DIR, 'last_check_result.json'), 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            
            # 若有告警且配置了通知渠道，发送告警通知
            if result.get('alerts') and notification_config:
                try:
                    service = NotificationService(notification_config)
                    send_result = service.send_alerts(result['alerts'])
                    print(f"通知发送: 成功 {len(send_result['success'])}, 失败 {len(send_result['failed'])}")
                except Exception as notify_e:
                    print(f"通知发送失败: {notify_e}")
        except Exception as e:
            # 检查过程中发生异常，记录错误信息
            with open(os.path.join(DATA_DIR, 'last_check_error.txt'), 'w', encoding='utf-8') as f:
                f.write(str(e))
    
    # 启动后台守护线程执行检查任务
    # daemon=True: 主线程退出时自动终止该线程
    thread = threading.Thread(target=run_check_async, daemon=True)
    thread.start()
    
    # 立即返回，告知客户端检查已启动
    return jsonify({'success': True, 'message': '检查已启动，请稍后查看结果'})


@app.route('/api/check/status', methods=['GET'])
def get_check_status():
    """
    查询异步检查任务状态

    功能：通过检查结果文件和错误文件是否存在来判断检查任务的执行状态。
          轮询此接口可实现前端对异步任务进度的跟踪。

    HTTP 方法：GET

    Returns:
        tuple: (JSON 响应体, HTTP 状态码)
            - 检查完成 (200): {'status': 'completed', 'result': 检查结果数据}
            - 检查出错 (200): {'status': 'error', 'error': 错误信息}
            - 正在执行 (200): {'status': 'running'}
    """
    # 定义结果文件和错误文件路径
    result_file = os.path.join(DATA_DIR, 'last_check_result.json')
    error_file = os.path.join(DATA_DIR, 'last_check_error.txt')
    
    # 检查是否存在成功的结果文件
    if os.path.exists(result_file):
        # 读取并返回检查结果
        with open(result_file, 'r', encoding='utf-8') as f:
            result = json.load(f)
        return jsonify({'status': 'completed', 'result': result})
    
    # 检查是否存在错误文件（检查过程中发生异常）
    if os.path.exists(error_file):
        # 读取错误信息并返回，同时删除错误文件以便下次检查
        with open(error_file, 'r', encoding='utf-8') as f:
            error = f.read()
        os.unlink(error_file)  # 清理错误文件
        return jsonify({'status': 'error', 'error': error})
    
    # 结果文件和错误文件都不存在，说明检查仍在进行中
    return jsonify({'status': 'running'})


@app.route('/api/reports', methods=['GET'])
def get_reports():
    """
    获取所有检查报告列表

    功能：扫描 reports 目录，返回所有已生成的检查报告的摘要信息，
          按文件名倒序排列（最新的在前）。

    HTTP 方法：GET

    Returns:
        tuple: (JSON 响应体, HTTP 状态码)
            - 成功 (200): {'success': True, 'data': [报告摘要数组]}
    """
    reports = []
    # 倒序排列，最新的报告排在前面
    for filename in sorted(os.listdir(REPORTS_DIR), reverse=True):
        # 只列完整报告；summary 文件（report_*.summary.json）是给看板/AI
        # 消费的紧凑副本，不是一次检查的独立记录，混进来会重复计数
        if not filename.endswith('.json') or filename.endswith('.summary.json'):
            continue
        filepath = os.path.join(REPORTS_DIR, filename)
        # 读取报告文件获取摘要信息
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # 提取报告的关键元信息
        reports.append({
            'filename': filename,                              # 报告文件名
            'timestamp': data.get('timestamp', ''),           # 时间戳
            'total': data.get('total', 0),                     # 检查总数
            # 告警数量：新报告只存 alertsCount（不重复存 alerts）；
            # 旧报告没有该字段，退回按 results 派生、再退回按 alerts 取长度
            'alertsCount': _report_alerts_count(data)
        })
    return jsonify({'success': True, 'data': reports})


@app.route('/api/reports/<filename>', methods=['GET'])
def get_report(filename):
    """
    获取指定报告详情

    功能：根据文件名获取完整的检查报告内容

    HTTP 方法：GET

    Args:
        filename (str): 报告文件名（如 report_20250727_143000.json）

    Returns:
        tuple: (JSON 响应体, HTTP 状态码)
            - 成功 (200): {'success': True, 'data': 报告完整数据}
            - 未找到 (404): {'success': False, 'error': '报告不存在'}

    Note:
        报告文件不再重复存 `alerts`（新格式只存 results 与 alertsCount）。
        为保持前端契约不变，这里在缺 `alerts` 时用 alerts_from_results()
        从 results 还原，旧报告自带的 alerts 原样返回 —— 两种格式的调用方
        看到的响应结构完全一致。
    """
    # 拼接报告文件的完整路径
    filepath = os.path.join(REPORTS_DIR, filename)
    # 检查文件是否存在
    if not os.path.exists(filepath):
        return jsonify({'success': False, 'error': '报告不存在'}), 404
    
    # 读取并返回报告完整内容
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 新格式报告不含 alerts：从 results 派生，保证响应结构与旧报告一致
    if 'alerts' not in data:
        data['alerts'] = alerts_from_results(data.get('results', []))
        data.setdefault('alertsCount', len(data['alerts']))

    return jsonify({'success': True, 'data': data})


@app.route('/api/export', methods=['GET'])
def export_trademarks():
    """
    导出商标数据

    功能：将用户商标数据导出为指定格式（JSON 或 CSV）供下载

    HTTP 方法：GET

    查询参数:
        - format (str): 导出格式，可选值 'json'（默认）或 'csv'

    Returns:
        - JSON 格式 (200): 返回商标数据的 JSON 响应
        - CSV 格式 (200): 返回 CSV 文件下载响应
        - 不支持的格式 (400): {'success': False, 'error': '不支持的格式'}
    """
    # 获取导出格式参数，默认为 JSON
    format_type = request.args.get('format', 'json')
    # 加载现有商标数据
    existing = load_trademarks()
    
    if format_type == 'json':
        # 直接返回 JSON 格式的完整数据
        return jsonify(existing)
    elif format_type == 'csv':
        # 动态导入 CSV 相关模块
        import csv
        from io import StringIO
        # 使用内存中的 StringIO 对象构建 CSV 内容
        output = StringIO()
        writer = csv.writer(output)
        # 写入 CSV 表头
        writer.writerow(['brandName', 'applicant', 'registrationNumber', 'niceClass', 'notes'])
        # 逐条写入商标数据行
        for tm in existing['trademarks']:
            writer.writerow([
                tm['brandName'],
                tm.get('applicant', ''),
                tm.get('registrationNumber', ''),
                ','.join(tm.get('niceClass', [])),    # 尼斯分类用逗号连接
                tm.get('notes', '')
            ])
        # 将指针移至文件开头以便读取
        output.seek(0)
        # 返回带 Content-Disposition 头的文件下载响应
        from flask import Response
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=trademarks.csv'}
        )
    else:
        # 不支持的格式
        return jsonify({'success': False, 'error': '不支持的格式'}), 400


@app.route('/api/import', methods=['POST'])
def import_trademarks():
    """
    导入商标数据

    功能：从上传的文件（JSON 或 CSV 格式）中导入商标数据。
          自动跳过已存在的同名商标（不区分大小写），避免重复。

    HTTP 方法：POST

    请求体 (multipart/form-data):
        - file (File): 上传的文件，支持 .json 和 .csv 格式

    Returns:
        tuple: (JSON 响应体, HTTP 状态码)
            - 成功 (200): {'success': True, 'importedCount': 导入数量, 'data': 导入的商标数组}
            - 失败 (400): {'success': False, 'error': 错误信息}
    """
    # 校验：请求中必须包含文件
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': '请上传文件'}), 400
    
    file = request.files['file']
    
    # 根据文件扩展名选择不同的解析方式
    if file.filename.endswith('.json'):
        # JSON 格式解析
        content = file.read().decode('utf-8')
        data = json.loads(content)
        # 兼容两种 JSON 结构：带 'trademarks' 键的对象或直接是数组
        trademarks = data.get('trademarks', data) if isinstance(data, dict) else data
    elif file.filename.endswith('.csv'):
        # CSV 格式解析
        import csv
        from io import StringIO
        content = file.read().decode('utf-8')
        reader = csv.DictReader(StringIO(content))
        trademarks = []
        # 逐行读取 CSV 并构建商标对象
        for row in reader:
            trademarks.append({
                'id': str(uuid.uuid4()),
                'brandName': row.get('brandName', ''),
                'applicant': row.get('applicant', ''),
                'registrationNumber': row.get('registrationNumber', ''),
                'niceClass': [c.strip() for c in row.get('niceClass', '').split(',') if c.strip()],
                'notes': row.get('notes', ''),
                'createdAt': datetime.now().isoformat(),
                'status': 'pending'
            })
    else:
        # 不支持的文件格式
        return jsonify({'success': False, 'error': '仅支持 JSON 和 CSV 格式'}), 400
    
    # 加载现有数据并构建已有商标名称的集合（用于去重检查）
    existing = load_trademarks()
    existing_ids = {tm['brandName'].lower() for tm in existing['trademarks']}
    
    # 逐条处理导入的商标，跳过已存在的同名商标
    imported = []
    for tm in trademarks:
        brand_name = tm.get('brandName', '').strip()
        # 仅导入名称非空且不存在的商标
        if brand_name and brand_name.lower() not in existing_ids:
            # 为新商标分配唯一 ID 和创建时间
            tm['id'] = str(uuid.uuid4())
            tm['createdAt'] = datetime.now().isoformat()
            tm['status'] = 'pending'
            existing['trademarks'].append(tm)
            imported.append(tm)
            existing_ids.add(brand_name.lower())  # 添加到已存在集合，防止重复
    
    # 持久化保存合并后的数据
    save_trademarks(existing)
    
    return jsonify({
        'success': True,
        'importedCount': len(imported),
        'data': imported
    })


if __name__ == '__main__':
    # 打印启动横幅信息
    print("=" * 50)
    print("WIPO 商标监控系统 - Web 版")
    print("访问: http://localhost:5000")
    print("=" * 50)
    # 启动 Flask 开发服务器
    # host='0.0.0.0': 监听所有网络接口，允许外部设备访问
    # port=5000: 服务端口号
    # debug=True: 开启调试模式，支持热重载和详细错误信息
    app.run(host='0.0.0.0', port=5000, debug=True)