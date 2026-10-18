"""
WIPO 商标监控系统 - API 文档 Web 服务

功能说明:
    本模块提供 API 文档的在线浏览和测试功能，运行在独立端口 5001。
    通过可视化界面展示 Web 应用（端口 5000）的所有 REST API 接口，
    并支持在线发送测试请求验证接口行为。

运行方式:
    python docs/docs_server.py
    访问: http://localhost:5001

依赖:
    - Flask: Web 框架
    - requests: 转发测试请求到后端服务

注意:
    使用前需先启动主应用 (python web_app.py)，否则测试功能不可用。
    本模块是独立于项目根 static/ 与 templates/ 的文档站程序：其前端资源
    位于 docs/viewer/static/ 与 docs/viewer/templates/，文件名为
    docs-viewer.js / docs-viewer.css / docs-viewer.html，与主应用同名文件
    不共用（历史上曾同名，导致按文件名检索时无法区分两套程序）。
"""

import json
import os
import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__,
            static_folder='viewer/static',
            template_folder='viewer/templates')

# 接口定义从唯一契约文档读取，不再在源码里手写一份副本。
# 手写副本会与 web_app.py 的路由漂移（增删接口后忘记同步），
# tests/test_api_schema.py 会校验三者一致。
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'api-schema.json')


def load_api_definitions() -> dict:
    """
    读取接口定义契约

    Returns:
        dict: {'categories': {...}} 中的分类定义；文件缺失时返回空字典
              （文档站仍能启动，只是没有可展示的接口）
    """
    try:
        with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
            return json.load(f).get('categories', {})
    except (OSError, ValueError):
        return {}


API_DEFINITIONS = load_api_definitions()


BACKEND_URL = 'http://localhost:5000'

@app.route('/')
def index():
    return render_template('docs-viewer.html', api_definitions=API_DEFINITIONS)

@app.route('/api/definitions')
def get_definitions():
    return jsonify({'success': True, 'data': API_DEFINITIONS})

@app.route('/api/test', methods=['POST'])
def test_api():
    data = request.get_json()
    method = data.get('method', 'GET').upper()
    path = data.get('path', '')
    headers = data.get('headers', {})
    body = data.get('body', '')
    
    url = f"{BACKEND_URL}{path}"
    
    try:
        request_kwargs = {
            'headers': headers,
            'timeout': 30
        }
        
        if method in ['POST', 'PUT', 'PATCH']:
            if body:
                try:
                    request_kwargs['json'] = json.loads(body)
                    request_kwargs['headers']['Content-Type'] = 'application/json'
                except:
                    request_kwargs['data'] = body
        
        response = requests.request(method, url, **request_kwargs)
        
        response_data = None
        try:
            response_data = response.json()
        except:
            response_data = response.text[:2000]
        
        return jsonify({
            'success': True,
            'statusCode': response.status_code,
            'headers': dict(response.headers),
            'data': response_data,
            'time': response.elapsed.total_seconds()
        })
    except requests.exceptions.ConnectionError:
        return jsonify({
            'success': False,
            'error': '无法连接到后端服务，请确认主系统已启动'
        }), 502
    except requests.exceptions.Timeout:
        return jsonify({
            'success': False,
            'error': '请求超时'
        }), 504
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


if __name__ == '__main__':
    print("=" * 50)
    print("WIPO 商标监控系统 - API 文档")
    print("访问: http://localhost:5001")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5001, debug=True)