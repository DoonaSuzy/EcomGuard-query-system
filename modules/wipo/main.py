"""
WIPO 商标监控系统 - CLI 入口模块

本模块是整个商标监控系统的命令行入口，支持以下三个子命令：
    - run:       立即执行一次商标监控检查
    - schedule:  启动定时调度器，按月定期自动执行监控任务
    - init-config: 生成默认的配置文件模板

使用示例:
    python main.py run trademarks.json -o ./reports
    python main.py schedule trademarks.json --day 1 --hour 9
    python main.py init-config -o config.json
"""

import json
import os
import sys
import argparse
import time
import threading
import calendar
from datetime import datetime, timedelta
from typing import Optional, Dict, Callable

from monitor import MonitorEngine
from trademark_list import TrademarkList
from notification import NotificationService


class Scheduler:
    """
    任务调度器

    负责协调商标监控的执行流程，支持两种运行模式：
    1. 单次执行（run_once）：立即执行一次完整的监控检查
    2. 定时调度（start_monthly）：在后台线程中按月定时执行监控任务

    主要功能包括：
    - 加载/保存配置文件
    - 调度商标监控引擎执行检查
    - 生成报告并保存
    - 发送告警通知
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        初始化调度器

        Args:
            config_path: 配置文件路径，若提供且文件存在则自动加载配置
        """
        self.config = {}
        if config_path and os.path.exists(config_path):
            self.load_config(config_path)
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def load_config(self, config_path: str):
        """
        从 JSON 文件加载配置

        Args:
            config_path: 配置文件的路径
        """
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)

    def save_config(self, config_path: str):
        """
        将当前配置保存到 JSON 文件

        Args:
            config_path: 配置文件的保存路径
        """
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)

    def run_once(self, trademark_file: str,
                 check_status: bool = True,
                 check_squatting: bool = True,
                 send_notifications: bool = True,
                 output_dir: Optional[str] = None) -> Dict:
        """
        执行一次完整的商标监控检查

        执行流程：
        1. 从文件加载商标清单
        2. 使用监控引擎执行各项检查（状态检查、抢注检查等）
        3. 生成检查报告并输出到控制台
        4. 可选：将报告保存为 JSON 文件
        5. 可选：发送告警通知

        Args:
            trademark_file:   商标清单文件路径（支持 JSON/CSV/Excel 格式）
            check_status:     是否检查商标状态，默认为 True
            check_squatting:  是否检查抢注情况，默认为 True
            send_notifications: 是否发送告警通知，默认为 True
            output_dir:       报告输出目录，若提供则保存报告

        Returns:
            包含检查结果的字典，格式如下:
            {
                'checked': 已检查数量,
                'total': 商标总数,
                'alerts': 告警列表,
                'errors': 错误列表
            }
        """
        # 加载商标清单文件
        tm_list = TrademarkList()
        count = tm_list.load(trademark_file)
        print(f"加载了 {count} 个商标")

        # 初始化监控引擎
        engine = MonitorEngine()
        applicant_names = tm_list.get_applicant_names()

        # 执行商标检查
        print("开始检查...")
        result = engine.run_check(tm_list, check_status, check_squatting, applicant_names)

        # 输出检查结果摘要
        print(f"\n检查完成: {result['checked']}/{result['total']}")
        print(f"告警数: {len(result['alerts'])}")

        # 输出错误详情
        if result['errors']:
            print(f"错误数: {len(result['errors'])}")
            for err in result['errors']:
                print(f"  - {err['brandName']}: {err['error']}")

        # 生成并打印报告
        report = engine.generate_report()
        print("\n" + report)

        # 若指定了输出目录，将报告保存为 JSON 文件
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            report_file = os.path.join(output_dir, f'report_{timestamp}.json')
            engine.save_report(report_file)
            # 同时写一份紧凑摘要，供看板/AI 低 token 消费
            engine.save_summary(
                os.path.join(output_dir, f'report_{timestamp}.summary.json'))
            print(f"\n报告已保存: {report_file}")

        # 若有告警且启用了通知，发送告警通知
        if send_notifications and result['alerts']:
            notification_config = self.config.get('notification', {})
            if notification_config:
                service = NotificationService(notification_config)
                send_result = service.send_alerts(result['alerts'])
                print(f"通知发送: 成功 {len(send_result['success'])}, 失败 {len(send_result['failed'])}")
            else:
                print("\n未配置通知渠道，跳过发送")

        return result

    def get_schedule_config(self) -> Dict:
        """
        从已加载配置中读取 schedule 调度段

        配置缺失、类型非法或超出范围时回退到默认值（每月 1 日 09:00），
        保证「config.json 中写什么就按什么调度」，同时不会因为坏配置而崩溃。

        Returns:
            形如 {'day_of_month': int, 'hour': int, 'minute': int} 的字典
        """
        defaults = {'day_of_month': 1, 'hour': 9, 'minute': 0}
        schedule = self.config.get('schedule') or {}
        if not isinstance(schedule, dict):
            return dict(defaults)

        ranges = {'day_of_month': (1, 31), 'hour': (0, 23), 'minute': (0, 59)}
        result = {}
        for key, (low, high) in ranges.items():
            value = schedule.get(key, defaults[key])
            # 布尔是 int 的子类（True == 1），必须显式拒绝：
            # 配置里写成 true 是笔误，应该回退默认值，而不是被静默当成 1
            if isinstance(value, bool):
                value = defaults[key]
            elif not isinstance(value, int):
                # 兼容 "9" 这类字符串写法；转换失败则回退默认值
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    value = defaults[key]
            if not low <= value <= high:
                value = defaults[key]
            result[key] = value
        return result

    def start_monthly(self, trademark_file: str,
                      day_of_month: int = 1,
                      hour: int = 9,
                      minute: int = 0,
                      callback: Optional[Callable] = None):
        """
        启动按月定时调度任务

        在后台守护线程中运行，每个月在指定的日期和时间自动执行一次商标监控。
        调度逻辑：
        1. 计算下次运行时间（每月指定日的指定时刻）
        2. 先长时间等待（最长60秒一次的轮询）
        3. 接近目标时间后每30秒检查一次，直到到达目标时间
        4. 执行监控任务，完成后重新计算下月运行时间，循环往复

        Args:
            trademark_file: 商标清单文件路径
            day_of_month:   每月第几天运行（1-31），默认 1
            hour:           运行小时（0-23），默认 9
            minute:         运行分钟（0-59），默认 0
            callback:       任务完成后的回调函数，接收本次执行结果作为参数
        """
        self._running = True

        def _run():
            """后台调度线程的主循环函数"""
            while self._running:
                now = datetime.now()

                # 计算下次运行时间
                next_run = self._get_next_run(now, day_of_month, hour, minute)
                wait_seconds = (next_run - now).total_seconds()

                print(f"下次运行时间: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"等待 {wait_seconds/3600:.1f} 小时")

                # 第一阶段：长时间等待，每60秒检查一次是否到达目标时间
                time.sleep(min(wait_seconds, 60))

                # 第二阶段：精确倒计时，每30秒检查一次
                while self._running:
                    now = datetime.now()
                    if now >= next_run:
                        try:
                            # 到达目标时间，执行监控任务
                            result = self.run_once(trademark_file)
                            if callback:
                                callback(result)
                        except Exception as e:
                            print(f"定时任务执行失败: {e}")
                        break
                    time.sleep(30)

        # 启动守护线程执行调度逻辑
        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self):
        """
        停止定时调度器

        将运行标志设为 False，并等待后台线程结束（最多等待5秒）。
        """
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _get_next_run(self, now: datetime, day_of_month: int, hour: int, minute: int) -> datetime:
        """
        计算下次定时运行的时间点

        逻辑说明：
        1. 首先尝试在本月的指定日期运行
        2. 若本月该日期已过，则顺延到下个月
        3. 使用 calendar.monthrange 处理月份天数不同的情况（如2月没有30日）
        4. 若指定日期超过当月最大天数，自动使用当月最后一天

        Args:
            now:          当前时间
            day_of_month: 目标运行日（1-31）
            hour:         目标运行小时
            minute:       目标运行分钟

        Returns:
            下次运行的 datetime 对象
        """
        # 第 1 步：先尝试本月的目标日期
        candidate = self._build_run_time(now.year, now.month, day_of_month, hour, minute)
        if candidate > now:
            # 本月目标时刻尚未到来，本月的这次检查仍然有效
            return candidate

        # 第 2 步：本月该日期已过，顺延到下个月
        if now.month == 12:
            year, month = now.year + 1, 1
        else:
            year, month = now.year, now.month + 1
        return self._build_run_time(year, month, day_of_month, hour, minute)

    @staticmethod
    def _build_run_time(year: int, month: int, day_of_month: int,
                        hour: int, minute: int) -> datetime:
        """
        构造指定年月的目标运行时刻（处理月份天数不足的情况）

        若 day_of_month 超过该月最大天数（如 2 月没有 30 日），
        自动退化为该月最后一天；同时把日期/时间钳制到合法范围内。

        Args:
            year:         年份
            month:        月份（1-12）
            day_of_month: 目标运行日（1-31）
            hour:         目标运行小时（0-23）
            minute:       目标运行分钟（0-59）

        Returns:
            该年月的目标运行 datetime 对象
        """
        last_day = calendar.monthrange(year, month)[1]
        actual_day = min(max(int(day_of_month), 1), last_day)
        actual_hour = min(max(int(hour), 0), 23)
        actual_minute = min(max(int(minute), 0), 59)
        return datetime(year, month, actual_day, actual_hour, actual_minute, 0)


def main():
    """
    命令行主入口函数

    支持的命令行参数：
        run          运行一次商标监控检查
            - trademark_file: 商标清单文件路径（必填）
            - --output/-o:    报告输出目录
            - --config/-c:    配置文件路径
            - --no-status:    跳过状态检查
            - --no-squatting: 跳过抢注检查
            - --no-notify:    不发送通知

        schedule     启动按月定时调度
            - trademark_file: 商标清单文件路径（必填）
            - --config/-c:    配置文件路径
            - --day/-d:       每月第几天运行（默认 1）
            - --hour:         运行小时（默认 9）
            - --minute:       运行分钟（默认 0）

        init-config  生成默认配置文件
            - --output/-o:    配置文件输出路径（默认 config.json）
    """
    parser = argparse.ArgumentParser(description='WIPO 商标监控系统')
    subparsers = parser.add_subparsers(dest='command', help='命令')

    # 运行命令
    run_parser = subparsers.add_parser('run', help='运行一次检查')
    run_parser.add_argument('trademark_file', help='商标清单文件 (JSON/CSV/Excel)')
    run_parser.add_argument('--output', '-o', help='报告输出目录')
    run_parser.add_argument('--config', '-c', help='配置文件路径')
    run_parser.add_argument('--no-status', action='store_true', help='不检查状态')
    run_parser.add_argument('--no-squatting', action='store_true', help='不检查抢注')
    run_parser.add_argument('--no-notify', action='store_true', help='不发送通知')

    # 调度命令
    sched_parser = subparsers.add_parser('schedule', help='启动定时调度')
    sched_parser.add_argument('trademark_file', help='商标清单文件')
    sched_parser.add_argument('--config', '-c', help='配置文件路径')
    # 默认值留空（None），以便与"用户没传该参数"区分开：
    # 未显式指定时回退到配置文件 schedule 段，再回退到内置默认值
    sched_parser.add_argument('--day', '-d', type=int, default=None,
                              help='每月第几天运行 (默认: 读配置，无配置则为 1)')
    sched_parser.add_argument('--hour', type=int, default=None,
                              help='运行小时 (默认: 读配置，无配置则为 9)')
    sched_parser.add_argument('--minute', type=int, default=None,
                              help='运行分钟 (默认: 读配置，无配置则为 0)')

    # 配置命令
    config_parser = subparsers.add_parser('init-config', help='生成配置文件')
    config_parser.add_argument('--output', '-o', default='config.json', help='配置文件路径')

    args = parser.parse_args()

    # 未指定命令时显示帮助信息
    if not args.command:
        parser.print_help()
        return

    # 处理 init-config 命令：生成默认配置模板
    if args.command == 'init-config':
        default_config = {
            'notification': {
                'channels': ['email'],
                'email': {
                    'smtp_server': 'smtp.example.com',
                    'smtp_port': 587,
                    'sender': 'monitor@example.com',
                    'recipients': ['admin@example.com'],
                    'username': '',
                    'password': ''
                },
                'dingtalk': {
                    'webhook_url': ''
                },
                'wework': {
                    'webhook_url': ''
                }
            },
            'schedule': {
                'day_of_month': 1,
                'hour': 9,
                'minute': 0
            }
        }
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, ensure_ascii=False, indent=2)
        print(f"配置文件已生成: {args.output}")
        return

    # 根据配置文件路径创建调度器实例
    scheduler = Scheduler(args.config if hasattr(args, 'config') else None)

    # 处理 run 命令：执行一次监控检查
    if args.command == 'run':
        scheduler.run_once(
            trademark_file=args.trademark_file,
            check_status=not args.no_status,
            check_squatting=not args.no_squatting,
            send_notifications=not args.no_notify,
            output_dir=args.output
        )

    # 处理 schedule 命令：启动定时调度器
    elif args.command == 'schedule':
        # 优先级：命令行显式参数 > 配置文件 schedule 段 > 内置默认值
        schedule = scheduler.get_schedule_config()
        day = args.day if args.day is not None else schedule['day_of_month']
        hour = args.hour if args.hour is not None else schedule['hour']
        minute = args.minute if args.minute is not None else schedule['minute']

        print(f"启动定时调度，每月 {day} 日 {hour}:{minute} 运行")
        scheduler.start_monthly(
            trademark_file=args.trademark_file,
            day_of_month=day,
            hour=hour,
            minute=minute
        )
        print("调度器已启动，按 Ctrl+C 停止...")
        try:
            # 主线程保持运行，等待 KeyboardInterrupt 信号
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            scheduler.stop()
            print("调度器已停止")


if __name__ == '__main__':
    main()