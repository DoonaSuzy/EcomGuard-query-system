"""跨栈兼容的读取端辅助函数。

这里放的是**新旧两套实现都必须认的读取逻辑**。

它们的共同特征是：处理的是**已经落盘的历史数据**（旧版是报告 JSON 文件，
新版是 reports 表的一行），而不是当前请求。因此新栈不能直接把它们删掉——
删掉的是「报告文件读取路径」，不是「历史报告仍然存在的三种字段形态」。

放在这里而不是留在 ``web_app.py`` 里，是为了让契约测试继续测到「当前线上
真正在跑的读取逻辑」，而不是测一个已被废弃的 Flask 文件里的孤儿函数。
"""

from __future__ import annotations

from monitor import alerts_from_results

__all__ = ["report_alerts_count"]

__version__ = "2.0.0"


def report_alerts_count(data: dict) -> int:
    """从一份报告数据里取告警数量。

    三种历史形态都要兼容，否则旧报告会在列表页显示成 0 条告警：

    1. 新报告直接存 ``alertsCount``（整数）；
    2. 中期报告不存 alerts，只存 ``results``，按 results 派生；
    3. 早期报告连 results 都没有，直接存了整份 ``alerts`` 列表。

    ``isinstance(x, int)`` 对 ``True`` 也成立（bool 是 int 的子类），
    所以必须显式排除 bool——否则某天 ``alertsCount`` 被误写成 ``True``
    会静默变成「1 条告警」。

    Args:
        data: 报告数据字典（来自 reports 行或旧报告 JSON 文件）

    Returns:
        告警数量（int）
    """
    value = data.get("alertsCount")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if data.get("results"):
        return len(alerts_from_results(data["results"]))
    alerts = data.get("alerts")
    return len(alerts) if isinstance(alerts, list) else 0
