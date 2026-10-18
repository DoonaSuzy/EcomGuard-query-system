"""
WIPO BrandDB API 客户端模块

功能说明:
    本模块封装了与 WIPO (世界知识产权组织) BrandDB API 的交互逻辑，
    提供商标搜索、状态检查、抢注检测等核心功能。

核心功能:
    - WipoApiClient: API 客户端主类，处理认证、请求发送、响应解密
    - _has_cjk_or_arabic: 检测文本是否包含中日韩或阿拉伯文字符

技术要点:
    - 使用 HashSearch 请求头进行身份认证
    - 响应数据使用 AES-ECB 模式加密，需要解密后解析
    - 解密密钥由固定前缀 + 动态 searchId 组成
    - 使用 CryptoJS 兼容的 AES 实现进行解密

依赖:
    - requests: HTTP 客户端库
    - cryptojs_aes: CryptoJS 兼容的 AES 加解密实现

作者: 商标监控系统开发团队
版本: 1.0.0
"""

import json
import os
import uuid
import re
import base64
import sys
from typing import Optional, List, Dict, Any

import requests

# 将当前目录添加到 sys.path，确保可以导入同目录下的模块
sys.path.insert(0, sys.path[0])
from cryptojs_aes import CryptoJSAES

# ==================== 常量定义 ====================

# WIPO BrandDB API 基础地址
API_BASE = 'https://api.branddb.wipo.int'

# 解密密钥前缀 - 与 searchId 拼接形成完整密钥
# ==================== 常量定义 ====================

# WIPO BrandDB API 基础地址
API_BASE = 'https://api.branddb.wipo.int'

# 解密密钥前缀 —— 与 searchId 拼接形成完整密钥（共 48 字节）。
# 该前缀是 WIPO BrandDB 前端实现的组成部分，未在公开文档中提供；
# 从环境变量 WIPO_DECRYPT_KEY_PREFIX 读取，未设置时回退到此内置值。
DECRYPT_KEY_PREFIX = os.environ.get('WIPO_DECRYPT_KEY_PREFIX', '8?)i_~Nk6qv0IX;2')

DEFAULT_SEARCH_ROWS = 30

# 包含匹配的最小长度 - 短于该长度的名称不做包含匹配
# 避免 "A" 这类极短关键词（或空串）命中大量无关记录
MIN_CONTAINMENT_LEN = 4

# 合法的商标状态集合 - 所有可能的状态值
VALID_STATUSES = {
    'Registered',       # 已注册
    'Pending',          # 待处理
    'Published',        # 已公布
    'Opposed',          # 被异议
    'Expired',          # 已过期
    'Cancelled',        # 已注销
    'Refused',          # 被驳回
    'Abandoned',        # 已放弃
    'Withdrawn',        # 已撤回
    'Ended',            # 已终止
    'In Process',       # 处理中
    'Not Registered',   # 未注册
    'Registered Protection'  # 注册保护
}

# 异常状态集合 - 需要告警的商标状态
# 这些状态表示商标可能存在问题，需要人工关注
ABNORMAL_STATUSES = {
    'Expired',      # 已过期 - 商标保护期已满
    'Cancelled',    # 已注销 - 商标被主动注销
    'Refused',      # 被驳回 - 注册申请被拒绝
    'Abandoned',    # 已放弃 - 申请人放弃注册
    'Withdrawn',    # 已撤回 - 申请人撤回申请
    'Ended',        # 已终止 - 商标权已终止（WIPO 实际返回值，原先遗漏）
    'Not Registered'  # 未注册 - 未能成功注册
}

# 异常状态的小写/去空白形式 - API 返回的状态值大小写并不稳定，比对时统一归一化
_ABNORMAL_STATUSES_LOWER = {s.strip().lower() for s in ABNORMAL_STATUSES}


# ==================== 辅助函数 ====================

def _has_cjk_or_arabic(text: str) -> bool:
    """
    检查文本是否包含中文/日文/韩文/阿拉伯文字符

    用途: 在抢注检测中判断品牌方和抢注方是否存在语言差异，
    语言差异可能暗示是海外关联方或许可证，而非真正的抢注。

    参数:
        text: 待检测的文本字符串

    返回:
        bool: True 表示包含 CJK 或阿拉伯文字符

    示例:
        >>> _has_cjk_or_arabic("Apple Inc.")  # False
        >>> _has_cjk_or_arabic("苹果公司")    # True
        >>> _has_cjk_or_arabic("شركة")        # True
    """
    # Unicode 范围:
    # \u4e00-\u9fff: 中文汉字
    # \u3040-\u309f: 日文假名
    # \uac00-\ud7af: 韩文
    # \u0600-\u06ff: 阿拉伯文
    # \u0750-\u077f: 阿拉伯文扩展
    pattern = r'[\u4e00-\u9fff\u3040-\u309f\uac00-\ud7af\u0600-\u06ff\u0750-\u077f]'
    return bool(re.search(pattern, text))


def _normalize_text(text) -> str:
    """
    归一化名称文本，用于比较

    统一转小写并折叠连续空白，使 "Apple  Inc." 与 "apple inc." 比较相等。

    参数:
        text: 待归一化的文本（可为 None / 非字符串）

    返回:
        str: 归一化后的字符串；输入为空时返回空串
    """
    if text is None:
        return ''
    return ' '.join(str(text).lower().split())


def _names_match(candidate: str, our_name: str) -> bool:
    """
    判断一个名称（申请人等）是否命中我方名称

    先做归一化后的精确比较；再做包含匹配，但仅当较短一方长度达到
    ``MIN_CONTAINMENT_LEN`` 时才允许，避免极短名称或空串造成大面积误判。

    参数:
        candidate: 待判断的名称（来自 API 结果）
        our_name:  我方名称

    返回:
        bool: True 表示命中
    """
    candidate_norm = _normalize_text(candidate)
    our_norm = _normalize_text(our_name)
    if not candidate_norm or not our_norm:
        return False
    if candidate_norm == our_norm:
        return True
    shorter, longer = sorted((candidate_norm, our_norm), key=len)
    if len(shorter) < MIN_CONTAINMENT_LEN:
        return False
    return shorter in longer


def _is_brand_contained(brand_name: str, doc_brand_name: str) -> bool:
    """
    判断品牌名之间是否为"包含"关系（用于识别变形抢注）

    与精确匹配不同，包含匹配会把 "NIKE" 与 "NIKE SPORTS" 视为相关。
    必须排除空串并限制最短长度，否则 ``'' in x`` 恒为真会导致全量误报。

    参数:
        brand_name:     我方品牌名
        doc_brand_name: API 结果中的品牌名

    返回:
        bool: True 表示存在包含关系
    """
    ours = _normalize_text(brand_name)
    other = _normalize_text(doc_brand_name)
    if not ours or not other:
        return False
    shorter, longer = sorted((ours, other), key=len)
    if len(shorter) < MIN_CONTAINMENT_LEN:
        return False
    return shorter in longer


def _build_case_id(case_info: dict) -> str:
    """
    为一条抢注/待审案例构造稳定标识

    报告与告警需要能被机器逐条比对：只靠 ``brandName`` 无法区分同名不同注册
    （申请人、局别、期号都可能不同），复查时也无法定位回同一条 WIPO 记录。
    这里按「局别 + 注册号 → 申请号 → 空」的优先级拼一个可复现的标识，
    不引入随机数，保证同一份 API 响应多次生成结果一致。

    参数:
        case_info: 已填充的案例字典（读取 office / registrationNumber / applicationNumber）

    返回:
        str: 形如 ``EM|1234567`` / ``APP|1234567`` /  ``''``（都缺失时为空串，
             由调用方决定是否退回 (brandName, applicant) 组合）
    """
    office = str(case_info.get('office', '') or '').strip()
    reg_no = str(case_info.get('registrationNumber', '') or '').strip()
    app_no = str(case_info.get('applicationNumber', '') or '').strip()
    if reg_no:
        return f'{office}|{reg_no}'
    if app_no:
        return f'APP|{app_no}'
    return ''


# ==================== API 客户端类 ====================

class WipoApiClient:
    """
    WIPO BrandDB API 客户端

    负责与 WIPO BrandDB 后端 API 通信，支持商标搜索、状态检查和抢注检测。

    使用方式:
        client = WipoApiClient()
        client.init()  # 初始化（必须先调用）
        result = client.search("APPLE")  # 搜索商标
        status = client.check_status("APPLE")  # 检查状态
        squatting = client.check_squatting("APPLE", ["Apple Inc."])  # 检查抢注

    注意:
        - init() 方法必须在其他方法之前调用
        - searchId 用于 HashSearch 认证和 AES 解密
        - API 响应为 AES-ECB 加密格式，需要解密处理
    """

    def __init__(self):
        """
        初始化 API 客户端

        创建 requests Session 并设置必要的请求头，
        模拟浏览器访问以防止被 API 拦截。
        """
        # 创建 HTTP 会话，保持连接复用
        self.session = requests.Session()

        # 设置请求头模拟浏览器访问
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json',
            'Origin': 'https://branddb.wipo.int',
            'Referer': 'https://branddb.wipo.int/en/similarname'
        })

        # 搜索 ID - 用于认证和解密
        # 格式: 32位十六进制字符串 (uuid4().hex)
        self.search_id: Optional[str] = None

    def init(self) -> str:
        """
        初始化会话和 searchId

        必须在调用其他 API 方法之前执行。
        会生成唯一的 searchId 并访问首页建立会话。

        返回:
            str: 生成的 searchId (32位十六进制字符串)
        """
        # 生成唯一的 searchId
        self.search_id = uuid.uuid4().hex

        # 访问首页建立会话（获取必要的 cookie）
        # 即使请求失败也不影响后续操作
        try:
            self.session.get('https://branddb.wipo.int/en/similarname', timeout=10)
        except Exception:
            pass

        return self.search_id

    def _decrypt(self, response_body: str) -> Optional[Dict]:
        """
        解密 AES-ECB 加密的响应数据

        WIPO API 返回的数据使用 AES-ECB 模式加密，
        密钥由固定前缀和 searchId 拼接而成。

        解密流程:
        1. 尝试直接解析（如果响应未加密）
        2. 使用 CryptoJSAES 进行 AES-ECB 解密
        3. 解析解密后的 JSON 数据

        参数:
            response_body: 响应体字符串（可能是 Base64 编码的密文）

        返回:
            Optional[Dict]: 解密后的 JSON 数据，失败返回 None

        异常:
            解密失败会打印错误信息并返回 None
        """
        # 首先尝试直接解析（响应可能未加密）
        try:
            return json.loads(response_body)
        except (json.JSONDecodeError, ValueError):
            pass

        # 构造解密密钥: 前缀 + searchId
        try:
            key_str = DECRYPT_KEY_PREFIX + (self.search_id or '')
            key = key_str.encode('utf-8')

            # 使用 CryptoJS 兼容的 AES 实现解密
            aes = CryptoJSAES(key)
            decrypted = aes.decrypt_from_base64(response_body)

            # 解析解密后的 JSON
            return json.loads(decrypted.decode('utf-8'))
        except Exception as e:
            print(f"解密异常: {e}")
            return None

    def _normalize_doc(self, doc: Dict) -> Dict:
        """
        规范化文档字段 - 将 API 返回的文档转换为统一格式

        WIPO API 返回的字段中，品牌名和申请人等字段通常是数组格式，
        此方法将其转换为方便使用的格式。

        参数:
            doc: API 返回的原始文档对象

        返回:
            Dict: 规范化后的文档对象，包含以下字段:
                - brandName: 主要品牌名（取数组第一个元素）
                - brandNames: 所有品牌名数组
                - status: 商标状态
                - applicant: 主要申请人（取数组第一个元素）
                - applicants: 所有申请人数组
                - registrationNumber: 注册号
                - niceClass: 尼斯分类数组
                - 其他字段: 原样保留
        """
        return {
            'brandName': doc.get('brandName', [''])[0] if doc.get('brandName') else '',
            'brandNames': doc.get('brandName', []),
            'status': doc.get('status', 'Unknown'),
            'type': doc.get('type', ''),
            'office': doc.get('office', ''),
            'collection': doc.get('collection', ''),
            'markFeature': doc.get('markFeature', ''),
            'applicant': doc.get('applicant', [''])[0] if doc.get('applicant') else '',
            'applicants': doc.get('applicant', []),
            'applicantCountryCode': doc.get('applicantCountryCode', [''])[0] if doc.get('applicantCountryCode') else '',
            'applicantCountryCodes': doc.get('applicantCountryCode', []),
            'registrationNumber': doc.get('registrationNumber', ''),
            'registrationDate': doc.get('registrationDate', ''),
            'applicationNumber': doc.get('applicationNumber', ''),
            'applicationDate': doc.get('applicationDate', ''),
            'expiryDate': doc.get('expiryDate', ''),
            'niceClass': doc.get('niceClass', []),
            'designation': doc.get('designation', []),
            'score': doc.get('score', 0),
            'logoId': doc.get('logo', [''])[0] if doc.get('logo') else '',
            'kind': doc.get('kind', [''])[0] if doc.get('kind') else '',
            'runid': doc.get('runid', ''),
            'st13': doc.get('st13', '')
        }

    def search(self, brand_name: str, options: Optional[Dict] = None) -> Dict:
        """
        搜索商标 - 根据品牌名在 WIPO BrandDB 中搜索

        这是核心搜索方法，会向 API 发送搜索请求并返回结果。
        请求包含 asStructure 格式的搜索条件，使用 HashSearch 头认证。

        参数:
            brand_name: 要搜索的品牌名称
            options: 可选的搜索选项字典，支持以下字段:
                - start: 起始位置，默认 0
                - rows: 返回结果数量，默认 30
                - sort: 排序方式，默认 'score desc'
                - strategy: 搜索策略，默认 'Simple'

        返回:
            Dict: 搜索结果，包含:
                - success (bool): 是否成功
                - total (int): 匹配的总数量
                - start (int): 起始位置
                - results (list): 规范化后的文档数组
                - error (str): 失败时的错误信息

        示例:
            >>> client.search("APPLE")
            >>> client.search("APPLE", {"rows": 10, "start": 0})
        """
        # 确保已初始化
        if not self.search_id:
            self.init()

        # 解析搜索选项
        options = options or {}
        start = options.get('start', 0)
        rows = options.get('rows', DEFAULT_SEARCH_ROWS)
        sort = options.get('sort', 'score desc')
        strategy = options.get('strategy', 'Simple')

        # 构造 asStructure 搜索条件
        # 格式: 嵌套的 JSON 结构，指定搜索字段和策略
        as_structure = {
            '_id': uuid.uuid4().hex[:24],  # 唯一标识符
            'boolean': 'AND',               # 布尔操作符
            'bricks': [{
                '_id': uuid.uuid4().hex[:24],
                'key': 'brandName',          # 搜索字段: 品牌名
                'value': brand_name,          # 搜索值
                'strategy': strategy           # 搜索策略
            }]
        }

        # 构造请求体
        request_body = {
            'asStructure': json.dumps(as_structure),
            'start': start,
            'rows': rows,
            'sort': sort
        }

        # 请求头 - 包含 HashSearch 认证
        headers = {
            'Content-Type': 'application/json',
            'HashSearch': self.search_id  # 认证标识
        }

        try:
            # 发送 POST 请求到搜索 API
            response = self.session.post(
                f'{API_BASE}/search',
                json=request_body,
                headers=headers,
                timeout=30
            )

            # 检查认证状态
            if response.status_code == 401:
                return {
                    'success': False,
                    'error': '认证失败',
                    'message': 'HashSearch 认证失败'
                }

            # 解密响应数据
            data = self._decrypt(response.text)
            if data is None:
                return {
                    'success': False,
                    'error': '解密失败',
                    'message': '响应数据解密失败',
                    'rawResponse': response.text[:200] if response.text else ''
                }

            # 解析搜索结果
            response_data = data.get('response', {})
            docs = response_data.get('docs', [])
            num_found = response_data.get('numFound', 0)

            return {
                'success': True,
                'total': num_found,
                'start': response_data.get('start', 0),
                'results': [self._normalize_doc(doc) for doc in docs]
            }

        except requests.exceptions.RequestException as e:
            # 网络请求异常
            return {
                'success': False,
                'error': '请求失败',
                'message': str(e)
            }

    def search_multiple(self, brand_names: List[str], options: Optional[Dict] = None) -> Dict:
        """
        批量搜索商标 - 依次搜索多个品牌名

        为避免 API 限流，请求之间会有延迟。

        参数:
            brand_names: 品牌名称列表
            options: 搜索选项（同 search 方法），额外支持:
                - delay: 请求间延迟（毫秒），默认 500

        返回:
            Dict: 批量搜索结果，包含:
                - results (dict): 以品牌名为 key 的结果字典
                - errors (list): 错误列表

        注意:
            此方法会占用较长时间，品牌名越多耗时越长。
        """
        import time

        options = options or {}
        delay = options.get('delay', 500)  # 默认延迟 500ms
        results = {}
        errors = []

        for name in brand_names:
            try:
                result = self.search(name, options)
                results[name] = result

                # 记录失败的搜索
                if not result['success']:
                    errors.append({'name': name, 'error': result.get('message', '')})
            except Exception as e:
                errors.append({'name': name, 'error': str(e)})
                results[name] = {'success': False, 'error': str(e)}

            # 请求间延迟，避免触发 API 限流
            time.sleep(delay / 1000.0)

        return {'results': results, 'errors': errors}

    def check_status(self, brand_name: str, options: Optional[Dict] = None) -> Dict:
        """
        检查商标状态 - 搜索指定品牌名并检查是否有异常状态

        会匹配我方商标记录，检查其状态是否属于异常状态集合。

        我方商标的识别依据（任一命中即认为是我方商标）：
        1. 品牌名归一化后完全相等（主要依据）
        2. 申请人名称命中 options['ourApplicantNames']
        3. 注册号命中 options['ourRegistrationNumbers']

        参数:
            brand_name: 要检查的品牌名称
            options: 搜索选项（同 search 方法），另支持:
                - ourApplicantNames (list[str]): 我方申请人名称列表
                - ourRegistrationNumbers (list[str]): 我方注册号列表

        返回:
            Dict: 检查结果，包含:
                - success (bool): 是否成功
                - brandName (str): 检查的品牌名
                - totalFound (int): 找到的总数
                - ourMarks (list): 匹配的我方商标
                - abnormalStatuses (list): 异常状态列表
                - hasIssue (bool): 是否有状态异常

        异常状态字段:
            - registrationNumber: 注册号
            - status: 状态值
            - office: 商标局代码
            - expiryDate: 过期日期
        """
        options = options or {}

        # 与抢注检查取同样的条数：只取前 5 条会把排在后面的异常记录整批截断，
        # 造成"报告显示全部正常"的系统性漏报。调用方显式传入 rows 时仍然尊重调用方。
        search_options = {**options}
        search_options.setdefault('rows', DEFAULT_SEARCH_ROWS)
        result = self.search(brand_name, search_options)

        # 失败时补齐字段，便于上游记录可定位的错误信息并如实上报
        if not result.get('success'):
            return {
                **result,
                'brandName': brand_name,
                'totalFound': 0,
                'ourMarks': [],
                'abnormalStatuses': [],
                'hasIssue': False
            }

        # 归一化我方标识，用于比对
        brand_name_norm = _normalize_text(brand_name)
        our_applicants = [n for n in (options.get('ourApplicantNames') or []) if n]
        our_reg_numbers = {
            _normalize_text(n) for n in (options.get('ourRegistrationNumbers') or [])
            if _normalize_text(n)
        }

        # 筛选我方商标记录
        our_marks = []
        for doc in result['results']:
            is_same_brand = bool(brand_name_norm) and \
                _normalize_text(doc.get('brandName', '')) == brand_name_norm

            is_ours = is_same_brand

            # 品牌名可能被改写（如本地语言名），此时靠申请人/注册号认定我方商标
            if not is_ours and our_applicants:
                doc_applicants = [doc.get('applicant', '')] + list(doc.get('applicants', []))
                is_ours = any(
                    _names_match(applicant, our_name)
                    for applicant in doc_applicants
                    for our_name in our_applicants
                )

            if not is_ours and our_reg_numbers:
                doc_reg = _normalize_text(doc.get('registrationNumber', ''))
                is_ours = bool(doc_reg) and doc_reg in our_reg_numbers

            if is_ours:
                our_marks.append(doc)

        # 检查每个匹配记录的状态是否异常（状态值大小写/空白不敏感）
        abnormal_statuses = []
        for mark in our_marks:
            status = str(mark.get('status', '') or '').strip()
            if status.lower() in _ABNORMAL_STATUSES_LOWER:
                abnormal_statuses.append({
                    'registrationNumber': mark.get('registrationNumber', ''),
                    'status': status,
                    'office': mark.get('office', ''),
                    'expiryDate': mark.get('expiryDate', '')
                })

        return {
            'success': True,
            'brandName': brand_name,
            'totalFound': result['total'],
            'ourMarks': our_marks,
            'abnormalStatuses': abnormal_statuses,
            'hasIssue': len(abnormal_statuses) > 0
        }

    def check_squatting(self, brand_name: str, our_applicant_names: List[str], 
                        options: Optional[Dict] = None) -> Dict:
        """
        检查疑似抢注 - 搜索商标并进行多维度抢注风险分析

        抢注检测逻辑:
        1. 搜索指定品牌名获取相关结果
        2. 识别我方商标（通过品牌名+申请人匹配）
        3. 排除我方商标后，分析剩余结果的抢注风险
        4. 根据品牌名匹配程度和语言差异确定风险等级

        风险等级划分（与下方实现、reason 字段一致）:
        - low (低风险):   品牌名精确匹配 且 双方含中日韩/阿拉伯文（存在语言差异，
                          申请人身份需人工核验）→ 计入 needsReviewCases
        - medium (中风险): 品牌名精确匹配 但申请人不同（无语言差异）
                          → 计入 needsReviewCases
        - high (高风险):  品牌名仅相似、非精确匹配（子串包含且长度达标）
                          → 计入 squattingCases

        参数:
            brand_name: 要检查的品牌名称
            our_applicant_names: 我方申请人名称列表（用于识别我方商标）
            options: 搜索选项，额外支持:
                - brandCoreKeyword: 品牌核心关键词（用于宽松匹配）

        返回:
            Dict: 检查结果，包含:
                - success (bool): 是否成功
                - squattingCases (list): 高风险抢注案例
                - needsReviewCases (list): 中/低风险待审核案例
                - hasSquatting (bool): 是否存在抢注嫌疑
                - highRiskCount (int): 高风险数量
                - mediumRiskCount (int): 中风险数量

        抢注案例字段:
            - brandName: 抢注品牌名
            - applicant: 抢注申请人
            - applicantCountryCode: 申请人国家代码
            - status: 商标状态
            - registrationNumber: 注册号
            - niceClass: 尼斯分类
            - office: 商标局
            - score: 匹配分数
            - riskLevel: 风险等级
            - reason: 风险原因
        """
        options = options or {}

        # 获取较多结果以进行全面分析（与状态检查共用同一取值）
        result = self.search(brand_name, {**options, 'rows': options.get('rows', DEFAULT_SEARCH_ROWS)})

        # 失败时补齐字段，便于上游记录错误并如实上报，而不是被静默当成"无异常"
        if not result.get('success'):
            return {
                **result,
                'brandName': brand_name,
                'totalFound': 0,
                'squattingCases': [],
                'needsReviewCases': [],
                'hasSquatting': False,
                'highRiskCount': 0,
                'mediumRiskCount': 0
            }

        # 初始化结果容器
        squatting_cases = []      # 高风险抢注案例
        needs_review_cases = []   # 中/低风险待审核案例

        # 归一化我方申请人名称（小写、折叠空白）与品牌名
        our_names = [n for n in (_normalize_text(n) for n in our_applicant_names) if n]
        brand_name_norm = _normalize_text(brand_name)

        # 品牌核心关键词 - 用于宽松匹配
        brand_core_key = _normalize_text(options.get('brandCoreKeyword', brand_name))

        # 分析每个搜索结果
        for doc in result['results']:
            doc_brand_name = _normalize_text(doc.get('brandName', ''))
            doc_applicant = _normalize_text(doc.get('applicant', ''))

            # 判断品牌名匹配程度
            is_exact_brand_match = bool(brand_name_norm) and doc_brand_name == brand_name_norm
            # 包含匹配需排除空串并限制最短长度，否则 '' in x 恒真会造成大量误报
            is_brand_contained = _is_brand_contained(brand_name, doc.get('brandName', ''))

            # ========== 识别我方商标 ==========

            # 方式1: 申请人名称精确/包含匹配（复用带长度保护的匹配逻辑）
            is_ours_by_applicant = bool(our_names) and any(
                _names_match(applicant, our_name)
                for applicant in ([doc.get('applicant', '')] + list(doc.get('applicants', [])))
                for our_name in our_names
            )

            # 方式2: 宽松匹配 - 品牌核心关键词 + 公司后缀
            # 适用于申请人名称中包含品牌关键词的情况
            if not is_ours_by_applicant and brand_core_key:
                core_parts = [p for p in brand_core_key.replace('-', ' ').split() if len(p) > 2]
                if core_parts:
                    # 非 CJK/阿拉伯文时才进行此匹配
                    if not _has_cjk_or_arabic(doc_applicant):
                        applicant_words = [w for w in re.split(r'[\s,\.]+', doc_applicant) if len(w) > 1]
                        has_core_keyword = any(
                            core in w or w in core
                            for core in core_parts
                            for w in applicant_words
                        )
                        # 常见公司后缀
                        company_suffixes = ['inc', 'corp', 'ltd', 'llc', 'limited', 
                                          'corporation', 'co', 'cv', 'bv', 'gmbh']
                        if has_core_keyword and any(suffix in doc_applicant for suffix in company_suffixes):
                            is_ours_by_applicant = True

            # ========== 跳过我方商标 ==========
            if is_exact_brand_match and is_ours_by_applicant:
                continue

            # ========== 判定抢注风险 ==========

            # 仅对品牌名匹配的结果进行分析
            is_suspicious = is_exact_brand_match or is_brand_contained

            if is_suspicious and not is_ours_by_applicant:
                # 检测语言差异
                doc_has_cjk_arabic = _has_cjk_or_arabic(doc_applicant)
                our_has_cjk_arabic = any(_has_cjk_or_arabic(n) for n in our_names)

                # 构造案例信息
                case_info = {
                    'brandName': doc.get('brandName', ''),
                    'applicant': doc.get('applicant', ''),
                    'applicantCountryCode': doc.get('applicantCountryCode', ''),
                    'status': doc.get('status', ''),
                    'registrationNumber': doc.get('registrationNumber', ''),
                    'niceClass': doc.get('niceClass', []),
                    'office': doc.get('office', ''),
                    'score': doc.get('score', 0),
                    'isExactMatch': is_exact_brand_match,
                    # 稳定标识与溯源字段：报告需要能被机器逐条比对，
                    # 只靠 brandName 无法区分同名不同注册（申请人/局别/期号都可能不同），
                    # 也无法在后续复查时定位到同一条 WIPO 记录。
                    'applicationNumber': doc.get('applicationNumber', ''),
                    'applicationDate': doc.get('applicationDate', ''),
                    'logoId': doc.get('logoId', ''),
                    'runid': doc.get('runid', ''),
                    'st13': doc.get('st13', ''),
                }
                # caseId：优先用「局别 + 注册号」，否则退回申请号，
                # 两者都没有时留空，由调用方决定是否用 (brandName, applicant) 兜底。
                case_info['caseId'] = _build_case_id(case_info)

                # 风险等级判定
                if is_exact_brand_match and (doc_has_cjk_arabic or our_has_cjk_arabic):
                    # 低风险: 精确匹配但有语言差异（可能是海外关联方）
                    case_info['riskLevel'] = 'low'
                    case_info['reason'] = '品牌名精确匹配，但存在语言差异，可能是海外关联方或许可证'
                    needs_review_cases.append(case_info)
                elif is_exact_brand_match:
                    # 中风险: 精确匹配但申请人不同
                    case_info['riskLevel'] = 'medium'
                    case_info['reason'] = '品牌名精确匹配，但申请人不同，需人工确认是否为授权或抢注'
                    needs_review_cases.append(case_info)
                else:
                    # 高风险: 品牌名相似且申请人不同
                    case_info['riskLevel'] = 'high'
                    case_info['reason'] = '品牌名相似且申请人不同，疑似抢注'
                    squatting_cases.append(case_info)

        return {
            'success': True,
            'brandName': brand_name,
            'totalFound': result['total'],
            'squattingCases': squatting_cases,
            'needsReviewCases': needs_review_cases,
            'hasSquatting': len(squatting_cases) > 0 or len(needs_review_cases) > 0,
            'highRiskCount': len(squatting_cases),
            'mediumRiskCount': len(needs_review_cases)
        }
