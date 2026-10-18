"""
商标监控模块
============

本模块提供商标监控引擎，用于批量检查商标的状态异常和疑似抢注情况。
整合了 WIPO API 客户端、商标列表和通知服务，支持：

- 商标状态异常检测（如驳回、无效等）
- 商标抢注行为检测（监控竞争对手是否抢注相同或近似商标）
- 告警自动生成与推送
- 监控报告生成与持久化

主要类:
    MonitorEngine: 商标监控引擎，协调各组件完成监控任务

依赖:
    wipo_client: WIPO API 客户端，用于查询商标状态和抢注检测
    trademark_list: 商标列表管理，提供待监控的品牌名称和申请人信息
    notification: 通知服务，用于发送告警
"""

import json
import sys
import time
from datetime import datetime
from typing import List, Dict, Optional

from wipo_client import WipoApiClient
from trademark_list import TrademarkList
from notification import NotificationService


def _safe_print(message: str):
    """
    控制台输出（对编码失败降级，绝不因打印而中断检查）

    Windows 中文控制台默认使用 GBK 编码，无法表示 ✗ / ⚠ 等符号，
    直接 print 会抛 UnicodeEncodeError。若任其抛出，异常会冒泡到
    run_check 的 except 分支，把"记录一条检查失败"变成"再抛一个异常"，
    反而丢掉真实的失败信息。这里在编码失败时降级为可打印形式。
    """
    try:
        print(message)
    except UnicodeEncodeError:
        # errors='replace' 会把不可编码字符替换成 '?'，保证仍能输出
        encoding = getattr(sys.stdout, 'encoding', None) or 'utf-8'
        print(message.encode(encoding, errors='replace').decode(encoding, errors='replace'))


def build_alert(result: Dict) -> Dict:
    """
    从单个检查结果派生一条告警

    这是告警结构的**唯一构造入口**：run_check 生成告警、以及报告读取端
    从 results 还原告警，都走这里，避免两处各写一遍导致结构漂移。

    Args:
        result: 单个商标的检查结果字典（由 _check_trademark 返回）

    Returns:
        告警信息字典（brandName / trademarkId / issues / abnormalStatuses /
        squattingCases / needsReviewCases / hasIssue / hasSquatting）
    """
    alert = {
        'brandName': result['brandName'],
        # 透传记录 id：告警邮件/报告需要能指回清单里的那一条
        'trademarkId': result.get('trademarkId', ''),
        'issues': [],
        'abnormalStatuses': result.get('abnormalStatuses', []),
        'squattingCases': result.get('squattingCases', []),
        'needsReviewCases': result.get('needsReviewCases', []),
        'hasIssue': result.get('hasIssue', False),
        'hasSquatting': result.get('hasSquatting', False)
    }

    # 根据检查结果标记具体问题类型
    if result.get('hasIssue'):
        alert['issues'].append('状态异常')
    if result.get('hasSquatting'):
        alert['issues'].append('疑似抢注')

    return alert


def alerts_from_results(results: List[Dict]) -> List[Dict]:
    """
    从 results 派生告警列表

    告警本质上就是「有问题的那部分 results」，因此报告文件里不再重复存一份
    （实测占报告体积约 29%）。旧报告仍自带 alerts，读取端优先用它；
    新报告只有 results，用本函数还原，两者字段与顺序完全一致。

    Args:
        results: 全部逐商标结果列表

    Returns:
        告警列表（只含有状态异常或疑似抢注的商标，保持 results 的相对顺序）
    """
    return [build_alert(r) for r in (results or [])
            if r.get('hasIssue') or r.get('hasSquatting')]


def _parse_errors_count(value) -> int:
    """
    从历史的 `errors` 字段取值推导错误数量

    兼容三种形态：错误详情列表（现形态）、数量 int（get_summary 旧形态）、
    缺失。用于读取旧报告时不依赖具体形态。

    Args:
        value: 报告里的 errors 字段

    Returns:
        错误数量（int）
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, (list, tuple)):
        return len(value)
    return 0


class MonitorEngine:
    """
    商标监控引擎

    协调 WIPO API 客户端、商标列表和通知服务，执行商标批量监控任务。
    支持检查商标状态异常和疑似抢注行为，并可生成告警和报告。

    Attributes:
        client: WIPO API 客户端实例，用于调用商标查询接口
        notification: 通知服务实例，用于发送告警通知
        results: 检查结果列表，每项包含单个商标的检查详情
        alerts: 告警列表，仅包含存在问题的商标信息
        errors: 错误记录列表，记录检查过程中发生的异常
    """

    def __init__(self, wipo_client: Optional[WipoApiClient] = None,
                 notification_service: Optional[NotificationService] = None):
        """
        初始化监控引擎

        Args:
            wipo_client: WIPO API 客户端实例，为 None 时自动创建新实例
            notification_service: 通知服务实例，为 None 时自动创建新实例

        Note:
            自动创建的实例将使用默认配置，如需自定义配置请传入预初始化的实例
        """
        self.client = wipo_client or WipoApiClient()
        self.notification = notification_service or NotificationService()
        self.results: List[Dict] = []
        self.alerts: List[Dict] = []
        self.errors: List[Dict] = []
        # 检查未完成的品牌集合（状态检查/抢注检查任一失败即计入），
        # 用于在报告和接口中区分「检查通过」与「根本没检查成功」
        self._failed_brands: set = set()
        # 本次检查的「我方商标标识」索引：品牌名 -> {'applicantNames', 'registrationNumbers'}
        # 供 check_status 判断命中的注册是否属于我方，而不是只看品牌名是否相等
        self._our_mark_context: Dict[str, Dict[str, List[str]]] = {}
        # 本次「尝试检查」的商标数（run_check 里赋值）。
        # 单独记一个数，是因为 results 与 _failed_brands 会重叠：
        # 某个商标检查失败时它既会被 append 进 results，也会被加进 _failed_brands，
        # 于是 len(results) + len(_failed_brands) 会把它算两次（实测 2 个商标得出 4）。
        self._brands_attempted: int = 0
        # 品牌名 -> 商标记录 id（UUID）。报告/告警靠它回到用户清单中的那一条。
        self._brand_ids: Dict[str, str] = {}

    def run_check(self, trademark_list: TrademarkList,
                  check_status: bool = True,
                  check_squatting: bool = True,
                  applicant_names: Optional[List[str]] = None) -> Dict:
        """
        执行商标批量检查

        遍历商标列表中的所有品牌名称，逐一检查其状态异常和抢注情况。
        检查过程中会生成告警和错误记录，并在每个商标检查后短暂休眠以避免 API 限流。

        Args:
            trademark_list: 商标列表对象，提供品牌名称和申请人信息
            check_status: 是否检查商标状态异常，默认为 True
            check_squatting: 是否检查疑似抢注，默认为 True
            applicant_names: 申请人名称列表，为 None 时从 trademark_list 获取

        Returns:
            检查结果汇总字典，包含以下字段:
            - total: 总商标数
            - checked: 完成检查的商标数
            - errors: 错误详情列表，每项为 {'brandName', 'stage'?, 'error'}
              （与 save_report 写出的报告文件保持同一形态）
            - errorsCount: 错误数量（int，等价于 len(errors)）
            - failedBrands: 检查未完成的品牌名列表（状态检查或抢注检查失败）
            - alerts: 告警列表
            - hasIssues: 是否存在任何告警
            - timestamp: 检查完成的时间戳 (ISO 格式)

        Raises:
            不抛出异常，所有异常会被捕获并记录到 errors 列表中
        """
        # 清空上一次检查的结果
        self.results = []
        self.alerts = []
        self.errors = []
        self._failed_brands = set()

        # 初始化 WIPO API 客户端（如登录、获取 token 等）
        self.client.init()

        # 获取待监控的品牌名称列表
        brand_names = trademark_list.get_brand_names()

        # 记录本次尝试检查的商标数，供 save_report 写出与 run_check 一致的 total。
        # 用 brand_names 的长度（而不是 len(trademark_list)），因为 run_check 的 total
        # 与循环都基于 brand_names；两者必须同源，否则总表与明细会对不上。
        self._brands_attempted = len(brand_names)

        # 品牌名 -> 商标记录 id（UUID）的索引。
        # 报告与告警原先只带 brandName，同名不同记录无法区分，也无法从报告跳回
        # 用户清单里的那一条。CSV/Excel 载入的记录可能没有 id，取不到时留空。
        self._brand_ids = self._build_brand_id_index(trademark_list)

        # 获取申请人名称列表，优先使用传入参数
        applicant_names = applicant_names or trademark_list.get_applicant_names()

        # 建立「我方商标标识」索引，供 check_status 判断命中的注册是否属于我方。
        # 仅靠品牌名相等会漏判：同一品牌在我方名下可能以不同申请人/注册号出现。
        self._our_mark_context = self._build_our_mark_context(trademark_list)

        # 遍历每个品牌名称执行检查
        for i, brand_name in enumerate(brand_names):
            _safe_print(f"[{i+1}/{len(brand_names)}] 检查商标: {brand_name}")

            try:
                # 执行单个商标的检查
                result = self._check_trademark(
                    brand_name, applicant_names, check_status, check_squatting
                )
                self.results.append(result)

                # 若检查结果存在异常或抢注嫌疑，创建告警
                if result.get('hasIssue') or result.get('hasSquatting'):
                    alert = self._create_alert(result)
                    self.alerts.append(alert)

            except Exception as e:
                # 捕获检查过程中的异常，记录错误信息
                # 该商标的检查同样属于「未完成」，一并计入 failedBrands
                self._failed_brands.add(brand_name)
                self.errors.append({
                    'brandName': brand_name,
                    'error': str(e)
                })
                _safe_print(f"  ✗ 错误: {e}")

            # 请求间隔，防止触发 API 速率限制
            time.sleep(0.5)

        # 返回检查结果汇总
        # errors 统一为「错误详情列表」，与 save_report / 报告文件保持同一形态；
        # 另外提供 errorsCount 便于前端等调用方直接取数量，无需自行判断类型。
        return {
            'total': self._brands_attempted,
            'checked': len(self.results),
            'errors': self.errors,
            'errorsCount': len(self.errors),
            'failedBrands': sorted(self._failed_brands),
            'alerts': self.alerts,
            'hasIssues': len(self.alerts) > 0,
            'timestamp': datetime.now().isoformat()
        }

    def _build_our_mark_context(self, trademark_list: TrademarkList) -> Dict[str, Dict[str, List[str]]]:
        """
        构建「我方商标标识」索引

        为每个品牌名收集我方的申请人名称与注册号，供 check_status 判断
        WIPO 命中的注册是否属于我方。仅按品牌名相等判断会漏判：
        同一品牌在我方名下可能登记在不同申请人名下、或用不同注册号出现。

        Args:
            trademark_list: 商标列表对象

        Returns:
            Dict[str, Dict[str, List[str]]]: 品牌名 ->
                {'applicantNames': [...], 'registrationNumbers': [...]}
        """
        context: Dict[str, Dict[str, List[str]]] = {}
        for t in getattr(trademark_list, 'trademarks', []) or []:
            brand = str(t.get('brandName', '') or '').strip()
            if not brand:
                continue
            entry = context.setdefault(
                brand, {'applicantNames': [], 'registrationNumbers': []}
            )
            # 申请人：既取 applicant 也取 applicantNames（后者可能更全）
            applicant = str(t.get('applicant', '') or '').strip()
            if applicant and applicant not in entry['applicantNames']:
                entry['applicantNames'].append(applicant)
            names = t.get('applicantNames')
            if isinstance(names, list):
                for name in names:
                    name = str(name or '').strip()
                    if name and name not in entry['applicantNames']:
                        entry['applicantNames'].append(name)
            # 注册号
            reg_no = str(t.get('registrationNumber', '') or '').strip()
            if reg_no and reg_no not in entry['registrationNumbers']:
                entry['registrationNumbers'].append(reg_no)
        return context

    def _build_brand_id_index(self, trademark_list: TrademarkList) -> Dict[str, str]:
        """
        构建「品牌名 -> 商标记录 id」索引

        报告与告警原先只带 brandName，同名不同记录无法区分，也无法从报告跳回
        用户清单里的那一条。这里把 Web 版的 UUID 主键透传进来（CSV/Excel 载入的
        记录可能没有 id，那种情况下该品牌名不会出现在索引里）。

        同名出现多条记录时保留首个 id：与 _build_our_mark_context 的行为一致，
        且 run_check 本身也是按品牌名逐个检查，无法区分同名记录。

        Args:
            trademark_list: 商标列表对象

        Returns:
            Dict[str, str]: 品牌名 -> 记录 id
        """
        index: Dict[str, str] = {}
        for t in getattr(trademark_list, 'trademarks', []) or []:
            brand = str(t.get('brandName', '') or '').strip()
            tm_id = str(t.get('id', '') or '').strip()
            if brand and tm_id and brand not in index:
                index[brand] = tm_id
        return index

    def _check_trademark(self, brand_name: str, applicant_names: List[str],
                         check_status: bool, check_squatting: bool) -> Dict:
        """
        检查单个商标的状态和抢注情况

        根据参数选择性地调用 WIPO API 检查商标状态异常和疑似抢注行为。

        Args:
            brand_name: 待检查的商标名称
            applicant_names: 关注的申请人名称列表，用于抢注检测
            check_status: 是否检查状态异常
            check_squatting: 是否检查疑似抢注

        Returns:
            商标检查结果字典，包含以下字段:
            - brandName: 商标名称
            - abnormalStatuses: 异常状态列表
            - squattingCases: 疑似抢注案例列表
            - needsReviewCases: 待审核案例列表
            - hasIssue: 是否存在状态异常
            - hasSquatting: 是否存在疑似抢注
        """
        # 初始化结果字典
        result = {
            'brandName': brand_name,
            # 商标记录 id（Web 版 UUID 主键）。同名不同记录靠它区分；
            # CSV/Excel 载入或旧数据没有 id 时为空字符串，不影响后续字段。
            'trademarkId': self._brand_ids.get(brand_name, ''),
            'abnormalStatuses': [],
            'squattingCases': [],
            'needsReviewCases': [],
            'hasIssue': False,
            'hasSquatting': False
        }

        # 检查商标状态异常
        if check_status:
            # 传入我方标识（申请人 + 注册号），让「命中我方注册」的判定
            # 不再局限于品牌名精确相等
            our_context = self._our_mark_context.get(brand_name, {})
            status_result = self.client.check_status(brand_name, {
                'ourApplicantNames': our_context.get('applicantNames', []),
                'ourRegistrationNumbers': our_context.get('registrationNumbers', []),
            })
            if status_result.get('success'):
                result['abnormalStatuses'] = status_result.get('abnormalStatuses', [])
                result['hasIssue'] = status_result.get('hasIssue', False)
            else:
                # 检查失败必须留下痕迹：否则该商标会被当成"检查通过"，
                # 报告显示异常数 0、错误数 0，形成静默的假"全部正常"
                self._record_check_failure(brand_name, '状态检查', status_result)

        # 检查疑似抢注行为
        if check_squatting:
            squatting_result = self.client.check_squatting(brand_name, applicant_names)
            if squatting_result.get('success'):
                result['squattingCases'] = squatting_result.get('squattingCases', [])
                result['needsReviewCases'] = squatting_result.get('needsReviewCases', [])
                result['hasSquatting'] = squatting_result.get('hasSquatting', False)
            else:
                self._record_check_failure(brand_name, '抢注检查', squatting_result)

        return result

    def _record_check_failure(self, brand_name: str, stage: str, check_result: Dict):
        """
        记录一次检查失败

        当 WIPO API 返回 success=False（认证失败、解密失败、网络异常等）时，
        把失败原因记入 errors 并在结果上打标，确保：
        1. 报告与接口能如实反映"检查未成功"，而不是伪装成"无异常"；
        2. 控制台能看到具体原因，便于排查。

        Args:
            brand_name:   发生失败的商标名称
            stage:        阶段名称（'状态检查' / '抢注检查'）
            check_result: 客户端返回的失败结果字典
        """
        reason = (check_result.get('message')
                  or check_result.get('error')
                  or '未知错误')
        error_info = {
            'brandName': brand_name,
            # 与 result/alert 一致地带上记录 id，便于从错误直接定位到清单记录
            'trademarkId': getattr(self, '_brand_ids', {}).get(brand_name, ''),
            'stage': stage,
            'error': f'{stage}失败: {reason}'
        }
        self.errors.append(error_info)
        # 标记该商标的检查存在未完成的部分
        # （防御性写法：兼容旧实例或被外部替换属性的情况，避免检查整体崩溃）
        failed = getattr(self, '_failed_brands', None)
        if failed is None:
            failed = self._failed_brands = set()
        failed.add(brand_name)
        _safe_print(f"  ⚠ {stage}未完成: {reason}")

    def _create_alert(self, result: Dict) -> Dict:
        """
        根据检查结果创建告警信息

        从检查结果中提取异常状态、抢注案例等信息，
        生成结构化的告警字典，便于后续通知和报告展示。

        Args:
            result: 单个商标的检查结果字典（由 _check_trademark 返回）

        Returns:
            告警信息字典，包含以下字段:
            - brandName: 商标名称
            - issues: 问题描述列表（如 '状态异常'、'疑似抢注'）
            - abnormalStatuses: 异常状态详情
            - squattingCases: 疑似抢注案例
            - needsReviewCases: 待审核案例
            - hasIssue: 是否存在状态异常
            - hasSquatting: 是否存在疑似抢注

        Note:
            实际构造逻辑在模块级 build_alert()，报告读取端从 results
            还原告警时用的是同一个函数。
        """
        return build_alert(result)

    def send_alerts(self) -> Dict:
        """
        发送告警通知

        将已生成的告警列表通过通知服务发送出去。
        若无告警则直接返回，不调用通知服务。

        Returns:
            发送结果字典，包含以下字段:
            - sent: 成功发送的告警数量
            - failed: 发送失败的告警数量

        Note:
            需在 run_check 执行后调用，否则 alerts 列表为空
        """
        if not self.alerts:
            return {'sent': 0, 'failed': 0}

        return self.notification.send_alerts(self.alerts)

    def generate_report(self) -> str:
        """
        生成商标监控报告（文本格式）

        将检查结果格式化为可读性强的文本报告，
        包含总体统计、告警详情和错误信息。

        Returns:
            格式化的报告字符串，可直接输出到控制台或写入文件
        """
        report_lines = []
        # 报告头部
        report_lines.append("=" * 60)
        report_lines.append("商标监控报告")
        report_lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report_lines.append("=" * 60)

        # 统计摘要
        report_lines.append(f"\n总商标数: {len(self.results)}")
        report_lines.append(f"异常数: {len(self.alerts)}")
        report_lines.append(f"错误数: {len(self.errors)}")

        # 告警详情
        if self.alerts:
            report_lines.append("\n--- 告警详情 ---")
            for alert in self.alerts:
                report_lines.append(f"\n商标: {alert['brandName']}")
                report_lines.append(f"问题: {', '.join(alert['issues'])}")

                # 展示异常状态详情
                if alert.get('abnormalStatuses'):
                    report_lines.append("  异常状态:")
                    for status in alert['abnormalStatuses']:
                        report_lines.append(f"    - 注册号 {status.get('registrationNumber', 'N/A')}: {status.get('status', '未知')}")

                # 展示疑似抢注案例
                if alert.get('squattingCases'):
                    report_lines.append("  疑似抢注:")
                    for case in alert['squattingCases']:
                        report_lines.append(f"    - {case.get('brandName', 'N/A')} (申请人: {case.get('applicant', 'N/A')}, 风险: {case.get('riskLevel', 'N/A')})")

                # 展示待审核案例
                if alert.get('needsReviewCases'):
                    report_lines.append("  待审核:")
                    for case in alert['needsReviewCases']:
                        report_lines.append(f"    - {case.get('brandName', 'N/A')} (申请人: {case.get('applicant', 'N/A')}, 风险: {case.get('riskLevel', 'N/A')})")

        # 错误详情
        if self.errors:
            report_lines.append("\n--- 错误详情 ---")
            for error in self.errors:
                report_lines.append(f"  {error['brandName']}: {error['error']}")

        # 报告尾部
        report_lines.append("\n" + "=" * 60)
        return '\n'.join(report_lines)

    def save_report(self, filepath: str):
        """
        保存监控报告为 JSON 文件

        将完整的检查结果（结果、错误、统计）以 JSON 格式写入文件，
        便于后续分析和存档。

        Args:
            filepath: 报告文件的保存路径

        Note:
            文件使用 UTF-8 编码，JSON 输出保留中文字符 (ensure_ascii=False)。
            报告**不再重复存一份 alerts**：告警就是 results 里「有问题」的那部分
            （实测占报告体积约 29%，且两份可能不一致）。只存 `alertsCount`
            供列表页直接取值；需要告警详情时由读取端用 alerts_from_results()
            从 results 还原（见 web_app.get_report）。旧报告自带的 alerts 仍可读。
        """
        # _failed_brands 可能在旧实例或测试替身上缺失，统一走防御性读取
        failed_brands = getattr(self, '_failed_brands', None) or set()
        result_brand_names = {r.get('brandName') for r in self.results}
        report = {
            'timestamp': datetime.now().isoformat(),
            'finishedAt': datetime.now().isoformat(),
            # total 与 run_check 同源：都取本次尝试检查的商标数。
            # 优先用 run_check 记下的精确值；若 save_report 被脱离 run_check 调用
            # （例如测试里替换了 run_check），则退回按 results 与 failedBrands 推导 ——
            # 直接用 len(results) + len(failedBrands) 会把检查失败的商标算两次
            # （实测 2 个商标写出 total=4），故这里只补「有失败但没产出 result」的那部分。
            'total': getattr(self, '_brands_attempted', 0) or (
                len(self.results)
                + len([b for b in failed_brands if b not in result_brand_names])
            ),
            'checked': len(self.results),
            'alertsCount': len(self.alerts),
            'errors': self.errors,
            'errorsCount': len(self.errors),
            'failedBrands': sorted(failed_brands),
            'hasIssues': len(self.alerts) > 0,
            'results': self.results
        }
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    def save_summary(self, filepath: str):
        """
        保存监控摘要为 JSON 文件（低 token 消费）

        归档报告里 `results` 占绝大多数体积（含每条抢注案例的全部字段），
        人和 AI 想快速知道「这次检查怎么样」时并不需要逐条明细。本方法写出
        一份只含统计与告警清单的紧凑版本，供看板 / AI / 邮件正文消费。

        Args:
            filepath: 摘要文件的保存路径（约定形如
                reports/report_<时间戳>.summary.json）
        """
        failed_brands = getattr(self, '_failed_brands', None) or set()
        result_brand_names = {r.get('brandName') for r in self.results}
        summary = {
            'timestamp': datetime.now().isoformat(),
            'finishedAt': datetime.now().isoformat(),
            'total': getattr(self, '_brands_attempted', 0) or (
                len(self.results)
                + len([b for b in failed_brands if b not in result_brand_names])
            ),
            'checked': len(self.results),
            'alertsCount': len(self.alerts),
            'errorsCount': len(self.errors),
            'failedBrands': sorted(failed_brands),
            'hasIssues': len(self.alerts) > 0,
            # 只带定位与结论，不带案例明细
            'alerts': [
                {
                    'brandName': a.get('brandName', ''),
                    'trademarkId': a.get('trademarkId', ''),
                    'issues': list(a.get('issues', [])),
                    'squattingCount': len(a.get('squattingCases', []) or []),
                    'needsReviewCount': len(a.get('needsReviewCases', []) or []),
                    'abnormalStatusCount': len(a.get('abnormalStatuses', []) or []),
                }
                for a in self.alerts
            ],
        }
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

    def get_summary(self) -> Dict:
        """
        获取监控结果摘要

        计算各类统计指标，提供监控任务的高层视图。

        Returns:
            摘要字典，包含以下字段:
            - total_checked: 已检查的商标总数
            - has_issue: 存在状态异常的商标数
            - has_squatting: 存在疑似抢注的商标数
            - errorsCount: 发生错误的商标数（int）
            - alerts_count: 告警数量

        Note:
            本方法原先把数量写在 `errors` 键上（int），与 run_check / 归档报告里
            `errors` = 错误详情列表 的形态冲突，同一个键有两种含义。现统一为
            `errorsCount`（数量 int），全库 `errors` 只有「错误详情列表」一种形态。
        """
        return {
            'total_checked': len(self.results),
            'has_issue': sum(1 for r in self.results if r.get('hasIssue')),
            'has_squatting': sum(1 for r in self.results if r.get('hasSquatting')),
            'errorsCount': len(self.errors),
            'alerts_count': len(self.alerts)
        }