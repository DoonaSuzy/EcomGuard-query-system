"""
notification 模块
===================

功能概述
--------
本模块提供商标监控告警的统一通知服务 ``NotificationService``，
支持通过多种渠道将告警信息（如商标异常状态、疑似抢注、待审核案件等）
发送给相关负责人，便于及时响应与处置。

支持的通知渠道
--------------
1. **email**    ：邮件通知（通过 SMTP 发送）
2. **dingtalk** ：钉钉自定义机器人 Webhook
3. **wework**   ：企业微信群机器人 Webhook

配置说明
--------
通知服务通过一个 ``config`` 字典进行初始化。各配置项含义如下：

公共配置（顶层）
~~~~~~~~~~~~~~~
- ``channels`` (list[str])：启用的通知渠道列表，可选值：
  ``["email"]``、``["dingtalk"]``、``["wework"]``，默认 ``["email"]``。
  可同时启用多个渠道，例如 ``["email", "dingtalk"]``。

邮件渠道 ``email``
~~~~~~~~~~~~~~~~~~
- ``email.sender``       (str)：发件人邮箱地址，如 ``"noreply@example.com"``
- ``email.recipients``   (list[str])：收件人邮箱列表
- ``email.smtp_server``  (str)：SMTP 服务器地址，如 ``"smtp.exmail.qq.com"``
- ``email.smtp_port``    (int)：SMTP 端口，默认 ``587``（STARTTLS）
- ``email.username``     (str)：SMTP 登录用户名
- ``email.password``     (str)：SMTP 登录密码或授权码

钉钉渠道 ``dingtalk``
~~~~~~~~~~~~~~~~~~~~~
- ``dingtalk.webhook_url`` (str)：钉钉自定义机器人 Webhook 地址，
  以 ``https://oapi.dingtalk.com/robot/send?access_token=...`` 形式提供。
  需在钉钉群中添加自定义机器人后获取。

企业微信渠道 ``wework``
~~~~~~~~~~~~~~~~~~~~~~~
- ``wework.webhook_url`` (str)：企业微信群机器人 Webhook 地址，
  以 ``https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...`` 形式提供。
  需在企业微信群组中添加群机器人后获取。

使用示例
--------
::

    config = {
        "channels": ["email", "dingtalk"],
        "email": {
            "sender": "noreply@example.com",
            "recipients": ["ops@example.com"],
            "smtp_server": "smtp.exmail.qq.com",
            "smtp_port": 587,
            "username": "noreply@example.com",
            "password": "your-auth-code",
        },
        "dingtalk": {
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=xxx",
        },
    }
    service = NotificationService(config)
    result = service.send_alert(alert={"brandName": "示例商标", ...})
"""

import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError


class NotificationService:
    """通知服务。

    统一封装邮件、钉钉、企业微信等多种告警通知渠道，
    对外提供批量发送与单条发送两类接口。

    Parameters
    ----------
    config : dict, optional
        通知服务配置字典。若为空或 ``None``，则使用默认配置
        （启用 ``email`` 渠道，具体邮件配置缺失将导致发送失败）。
        配置结构详见模块级 docstring 中的“配置说明”章节。
    """

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}

    def send_alerts(self, alerts: List[Dict]) -> Dict:
        """批量发送告警通知（汇总为每个渠道一条消息）。

        把 ``alerts`` 列表汇总成**一条**通知内容，按 ``config["channels"]``
        中配置的渠道各发送一次：5 条告警只发 1 封邮件，而不是 5 封。

        Parameters
        ----------
        alerts : list[dict]
            告警对象列表。每个告警字典应至少包含 ``brandName`` 字段，
            可选字段包括 ``issues``、``abnormalStatuses``、
            ``squattingCases``、``needsReviewCases`` 等，用于格式化通知内容。

        Returns
        -------
        dict
            发送结果汇总，结构如下：

            - ``success`` (list[dict])：成功发送的结果列表，**每个渠道一项**
              （每项包含 ``channel``、``alert``、``alerts``、``count`` 等信息）。
            - ``failed``  (list[dict])：发送失败的结果列表
              （每项包含 ``error``、``channel``、``alert`` 等信息）。
        """
        results = {'success': [], 'failed': []}

        # 无告警时不发送任何消息
        if not alerts:
            return results

        channels = self.config.get('channels', ['email'])

        # 汇总内容只格式化一次，各渠道复用
        digest_text = self._format_alerts_digest(alerts)

        for channel in channels:
            if channel == 'email':
                result = self._send_email_alerts(alerts, digest_text)
            elif channel == 'dingtalk':
                result = self._send_dingtalk_alerts(alerts, digest_text)
            elif channel == 'wework':
                result = self._send_wework_alerts(alerts, digest_text)
            else:
                continue

            if result.get('success'):
                results['success'].append(result)
            else:
                results['failed'].append(result)

        return results

    def send_alert(self, alert: Dict, channel: str = 'email') -> Dict:
        """通过指定渠道发送单条告警。

        Parameters
        ----------
        alert : dict
            告警对象，参见 :meth:`send_alerts` 的 ``alerts`` 参数说明。
        channel : str, default ``'email'``
            发送渠道，可选值：

            - ``'email'``    邮件
            - ``'dingtalk'`` 钉钉
            - ``'wework'``   企业微信

        Returns
        -------
        dict
            发送结果。成功时包含 ``success=True`` 及 ``channel``、``alert``；
            失败时包含 ``success=False`` 及 ``error``、``channel`` 等信息。
            若传入不支持的 ``channel``，将返回 ``{'success': False, 'error': ...}``。
        """
        if channel == 'email':
            return self._send_email_alert(alert)
        elif channel == 'dingtalk':
            return self._send_dingtalk_alert(alert)
        elif channel == 'wework':
            return self._send_wework_alert(alert)
        else:
            return {'success': False, 'error': f'不支持的渠道: {channel}'}

    def _format_alert_text(self, alert: Dict) -> str:
        """将告警对象格式化为纯文本通知内容。

        生成的文本包含商标名称、异常状态、疑似抢注、待审核等关键信息，
        适用于邮件正文、钉钉/企微 text 消息等场景。

        Parameters
        ----------
        alert : dict
            告警对象，支持以下可选字段：

            - ``brandName`` (str)：商标名称（默认 ``'未知'``）
            - ``issues`` (list)：问题列表
            - ``abnormalStatuses`` (list[dict])：异常状态列表，
              每项包含 ``registrationNumber``、``status`` 等字段
            - ``squattingCases`` (list[dict])：疑似抢注案件列表，
              每项包含 ``brandName``、``applicant``、``riskLevel``
            - ``needsReviewCases`` (list[dict])：待审核案件列表，
              字段同 ``squattingCases``

        Returns
        -------
        str
            格式化后的多行通知文本。
        """
        brand = alert.get('brandName', '未知')
        issues = alert.get('issues', [])
        
        lines = [f"【商标监控告警】", f"商标: {brand}"]
        
        if alert.get('abnormalStatuses'):
            lines.append("\n异常状态:")
            for status in alert['abnormalStatuses']:
                lines.append(f"  - 注册号 {status.get('registrationNumber', 'N/A')}: {status.get('status', '未知')}")
        
        if alert.get('squattingCases'):
            lines.append("\n疑似抢注:")
            for case in alert['squattingCases']:
                lines.append(f"  - {case.get('brandName', 'N/A')} (申请人: {case.get('applicant', 'N/A')}, 风险: {case.get('riskLevel', 'N/A')})")
        
        if alert.get('needsReviewCases'):
            lines.append("\n待审核:")
            for case in alert['needsReviewCases']:
                lines.append(f"  - {case.get('brandName', 'N/A')} (申请人: {case.get('applicant', 'N/A')}, 风险: {case.get('riskLevel', 'N/A')})")
        
        return '\n'.join(lines)

    def _format_alerts_digest(self, alerts: List[Dict]) -> str:
        """将多条告警汇总格式化为一条通知内容。

        用于批量发送：把 N 条告警合并为一份带总数与编号的清单，
        避免「5 条告警 = 5 封邮件」的刷屏形态。

        Parameters
        ----------
        alerts : list[dict]
            告警对象列表（参见 :meth:`_format_alert_text`）。

        Returns
        -------
        str
            汇总后的多行通知文本。单条告警时与
            :meth:`_format_alert_text` 内容等价（仅编号不同）。
        """
        total = len(alerts)
        lines = [
            "【商标监控告警】",
            f"共 {total} 个商标存在问题",
        ]

        for index, alert in enumerate(alerts, start=1):
            lines.append("")
            lines.append(f"--- [{index}/{total}] ---")
            lines.append(self._format_alert_text(alert))

        return '\n'.join(lines)

    def _send_email_alerts(self, alerts: List[Dict], digest_text: str) -> Dict:
        """把多条告警汇总为一封邮件发送。

        Parameters
        ----------
        alerts : list[dict]
            告警对象列表，仅用于提取数量与商标名清单。
        digest_text : str
            已汇总的通知正文。

        Returns
        -------
        dict
            发送结果。成功包含 ``success=True``、``channel='email'``、
            ``alerts``（商标名列表）、``count``；失败时额外包含 ``error``。

        Note
        ----
            保留 ``alert`` 字段（逗号分隔的商标名）以兼容既有调用方与文档。
        """
        email_config = self.config.get('email', {})
        brand_names = [a.get('brandName', '未知') for a in alerts]
        summary_subject = self._build_digest_subject(brand_names)

        if not email_config:
            return {'success': False, 'error': '邮件配置缺失', 'channel': 'email',
                    'alert': summary_subject, 'alerts': brand_names, 'count': len(alerts)}

        try:
            msg = MIMEMultipart()
            msg['From'] = email_config.get('sender', '')
            msg['To'] = ', '.join(email_config.get('recipients', []))
            msg['Subject'] = f"【商标监控告警】{len(alerts)} 个商标 · {summary_subject}"

            msg.attach(MIMEText(digest_text, 'plain', 'utf-8'))

            smtp_server = email_config.get('smtp_server', '')
            smtp_port = email_config.get('smtp_port', 587)
            username = email_config.get('username', '')
            password = email_config.get('password', '')

            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(username, password)
                server.send_message(msg)

            return {'success': True, 'channel': 'email', 'alert': summary_subject,
                    'alerts': brand_names, 'count': len(alerts)}
        except Exception as e:
            return {'success': False, 'error': str(e), 'channel': 'email',
                    'alert': summary_subject, 'alerts': brand_names, 'count': len(alerts)}

    @staticmethod
    def _build_digest_subject(brand_names: List[str], limit: int = 3) -> str:
        """把商标名列表压缩为适合放进主題/字段的短摘要。

        Parameters
        ----------
        brand_names : list[str]
            商标名称列表。
        limit : int, default 3
            摘要中最多列出的商标数量，超出部分以 ``等N个`` 概括。

        Returns
        -------
        str
            形如 ``NIKE, SONY, ADIDAS 等 5 个`` 的摘要文本。
        """
        if not brand_names:
            return '无'
        shown = brand_names[:limit]
        summary = ', '.join(str(name) for name in shown)
        if len(brand_names) > limit:
            summary += f" 等 {len(brand_names)} 个"
        return summary

    def _send_dingtalk_alerts(self, alerts: List[Dict], digest_text: str) -> Dict:
        """把多条告警汇总为一条钉钉消息发送。

        Parameters
        ----------
        alerts : list[dict]
            告警对象列表，仅用于提取数量与商标名清单。
        digest_text : str
            已汇总的通知正文。

        Returns
        -------
        dict
            发送结果，字段含义同 :meth:`_send_email_alerts`，
            ``channel`` 为 ``'dingtalk'``。
        """
        dingtalk_config = self.config.get('dingtalk', {})
        webhook_url = dingtalk_config.get('webhook_url', '')
        brand_names = [a.get('brandName', '未知') for a in alerts]
        summary_subject = self._build_digest_subject(brand_names)

        if not webhook_url:
            return {'success': False, 'error': '钉钉 webhook URL 缺失', 'channel': 'dingtalk',
                    'alert': summary_subject, 'alerts': brand_names, 'count': len(alerts)}

        try:
            payload = {
                'msgtype': 'text',
                'text': {'content': digest_text}
            }

            data = json.dumps(payload).encode('utf-8')
            req = Request(webhook_url, data=data, headers={'Content-Type': 'application/json'})
            with urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode('utf-8'))

            if result.get('errcode') == 0:
                return {'success': True, 'channel': 'dingtalk', 'alert': summary_subject,
                        'alerts': brand_names, 'count': len(alerts)}
            else:
                return {'success': False, 'error': result.get('errmsg', '钉钉发送失败'),
                        'channel': 'dingtalk', 'alert': summary_subject,
                        'alerts': brand_names, 'count': len(alerts)}
        except Exception as e:
            return {'success': False, 'error': str(e), 'channel': 'dingtalk',
                    'alert': summary_subject, 'alerts': brand_names, 'count': len(alerts)}

    def _send_wework_alerts(self, alerts: List[Dict], digest_text: str) -> Dict:
        """把多条告警汇总为一条企业微信消息发送。

        Parameters
        ----------
        alerts : list[dict]
            告警对象列表，仅用于提取数量与商标名清单。
        digest_text : str
            已汇总的通知正文。

        Returns
        -------
        dict
            发送结果，字段含义同 :meth:`_send_email_alerts`，
            ``channel`` 为 ``'wework'``。
        """
        wework_config = self.config.get('wework', {})
        webhook_url = wework_config.get('webhook_url', '')
        brand_names = [a.get('brandName', '未知') for a in alerts]
        summary_subject = self._build_digest_subject(brand_names)

        if not webhook_url:
            return {'success': False, 'error': '企微 webhook URL 缺失', 'channel': 'wework',
                    'alert': summary_subject, 'alerts': brand_names, 'count': len(alerts)}

        try:
            payload = {
                'msgtype': 'text',
                'text': {'content': digest_text}
            }

            data = json.dumps(payload).encode('utf-8')
            req = Request(webhook_url, data=data, headers={'Content-Type': 'application/json'})
            with urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode('utf-8'))

            if result.get('errcode') == 0:
                return {'success': True, 'channel': 'wework', 'alert': summary_subject,
                        'alerts': brand_names, 'count': len(alerts)}
            else:
                return {'success': False, 'error': result.get('errmsg', '企微发送失败'),
                        'channel': 'wework', 'alert': summary_subject,
                        'alerts': brand_names, 'count': len(alerts)}
        except Exception as e:
            return {'success': False, 'error': str(e), 'channel': 'wework',
                    'alert': summary_subject, 'alerts': brand_names, 'count': len(alerts)}

    def _send_email_alert(self, alert: Dict) -> Dict:
        """通过 SMTP 发送邮件告警。

        读取 ``config['email']`` 中的 SMTP 相关配置，
        使用 STARTTLS 加密方式登录并发送一封纯文本邮件。

        Parameters
        ----------
        alert : dict
            告警对象（参见 :meth:`_format_alert_text`）。

        Returns
        -------
        dict
            发送结果。成功包含 ``success=True``、``channel='email'``、``alert``；
            失败时额外包含 ``error`` 字段，可能的失败原因：

            - ``邮件配置缺失``：未配置 ``email`` 节点
            - SMTP 连接失败、认证失败、网络异常等
        """
        email_config = self.config.get('email', {})
        if not email_config:
            return {'success': False, 'error': '邮件配置缺失', 'channel': 'email'}
        
        try:
            msg = MIMEMultipart()
            msg['From'] = email_config.get('sender', '')
            msg['To'] = ', '.join(email_config.get('recipients', []))
            msg['Subject'] = f"【商标监控告警】{alert.get('brandName', '未知')}"
            
            body = self._format_alert_text(alert)
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            
            smtp_server = email_config.get('smtp_server', '')
            smtp_port = email_config.get('smtp_port', 587)
            username = email_config.get('username', '')
            password = email_config.get('password', '')
            
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(username, password)
                server.send_message(msg)
            
            return {'success': True, 'channel': 'email', 'alert': alert.get('brandName')}
        except Exception as e:
            return {'success': False, 'error': str(e), 'channel': 'email', 'alert': alert.get('brandName')}

    def _send_dingtalk_alert(self, alert: Dict) -> Dict:
        """通过钉钉自定义机器人 Webhook 发送告警。

        将告警内容封装为钉钉 ``text`` 类型消息，
        通过 ``urllib`` 向配置的 ``webhook_url`` 发送 HTTP POST 请求。

        Parameters
        ----------
        alert : dict
            告警对象（参见 :meth:`_format_alert_text`）。

        Returns
        -------
        dict
            发送结果。成功包含 ``success=True``、``channel='dingtalk'``、
            ``alert``；失败时额外包含 ``error``，可能的失败原因：

            - ``钉钉 webhook URL 缺失``：未配置 ``dingtalk.webhook_url``
            - 钉钉返回 ``errcode != 0``（具体错误见 ``errmsg``）
            - 网络请求超时或异常（超时 10 秒）
        """
        dingtalk_config = self.config.get('dingtalk', {})
        webhook_url = dingtalk_config.get('webhook_url', '')
        
        if not webhook_url:
            return {'success': False, 'error': '钉钉 webhook URL 缺失', 'channel': 'dingtalk'}
        
        try:
            text = self._format_alert_text(alert)
            payload = {
                'msgtype': 'text',
                'text': {'content': text}
            }
            
            data = json.dumps(payload).encode('utf-8')
            req = Request(webhook_url, data=data, headers={'Content-Type': 'application/json'})
            with urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode('utf-8'))
            
            if result.get('errcode') == 0:
                return {'success': True, 'channel': 'dingtalk', 'alert': alert.get('brandName')}
            else:
                return {'success': False, 'error': result.get('errmsg', '钉钉发送失败'), 'channel': 'dingtalk'}
        except Exception as e:
            return {'success': False, 'error': str(e), 'channel': 'dingtalk', 'alert': alert.get('brandName')}

    def _send_wework_alert(self, alert: Dict) -> Dict:
        """通过企业微信群机器人 Webhook 发送告警。

        将告警内容封装为企业微信 ``text`` 类型消息，
        通过 ``urllib`` 向配置的 ``webhook_url`` 发送 HTTP POST 请求。

        Parameters
        ----------
        alert : dict
            告警对象（参见 :meth:`_format_alert_text`）。

        Returns
        -------
        dict
            发送结果。成功包含 ``success=True``、``channel='wework'``、
            ``alert``；失败时额外包含 ``error``，可能的失败原因：

            - ``企微 webhook URL 缺失``：未配置 ``wework.webhook_url``
            - 企微返回 ``errcode != 0``（具体错误见 ``errmsg``）
            - 网络请求超时或异常（超时 10 秒）
        """
        wework_config = self.config.get('wework', {})
        webhook_url = wework_config.get('webhook_url', '')
        
        if not webhook_url:
            return {'success': False, 'error': '企微 webhook URL 缺失', 'channel': 'wework'}
        
        try:
            text = self._format_alert_text(alert)
            payload = {
                'msgtype': 'text',
                'text': {'content': text}
            }
            
            data = json.dumps(payload).encode('utf-8')
            req = Request(webhook_url, data=data, headers={'Content-Type': 'application/json'})
            with urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode('utf-8'))
            
            if result.get('errcode') == 0:
                return {'success': True, 'channel': 'wework', 'alert': alert.get('brandName')}
            else:
                return {'success': False, 'error': result.get('errmsg', '企微发送失败'), 'channel': 'wework'}
        except Exception as e:
            return {'success': False, 'error': str(e), 'channel': 'wework', 'alert': alert.get('brandName')}
