"""应用内定时巡检：把「每日 / 每周 / 每月自动跑一批企业」做成常驻线程。
=====================================================================

为什么要有这一个模块
--------------------
此前「定时」只有一条路：``install_schedule_task.ps1`` 往 Windows 任务计划程序
里装一条任务，跑 ``run_credit_check.bat``。它能用，但三个硬伤让它实际上没人用：

  1. **配置不在应用里**：改收件人要打开命令行重新装一遍任务，网页上看不到
     任何一个字，业务人员根本不知道有没有在跑；
  2. **看不到运行历史**：任务计划程序是个黑盒，跑没跑、跑成功没有、发了什么，
     都要去翻 ``logs/`` 里的文本；
  3. **只能整批跑** ``companies.txt``：想「周一查 A 组、月末查 B 组」得改文件。

所以这里在**应用进程内**实现调度：调度配置进数据库、由网页读写、执行结果就是
普通的作业（复用既有 ``jobs`` 表与前端详情页）。

线程模型（与既有 Worker 的关系）
--------------------------------
调度线程**不自己跑抓取**，它只做一件事：到点了就往 ``Worker`` 的队列里塞一个
作业。抓取仍然由那个唯一的 worker 串行执行 —— 这条不变式是硬约束（目标站有
瑞数风控，并发等于自伤），也意味着「调度触发」与「手动提交」天然互斥排队，
不会出现两个浏览器。

三个必须处理对的点（都写过对应回归用例）
----------------------------------------
1. **错月不跳期**：算下一次触发时间时，若本月目标日/目标时刻还没到就用本月的，
   不能无条件下推到下个月（否则「每月 1 号」在 1 号上午启动，第一次要等到
   下月 1 号，整整 31 天的空窗）。
2. **补跑一次**：进程在触发时刻不在线（关机 / 重启 / 服务没起）时，
   下一次启动要能判断「这次本该跑但没跑」。用 ``last_run_at`` 与按时间正推的
   ``next_run_at`` 比较，落后了就立刻补一次，且**只补一次**（连落后一个月
   也只补最近这一次，不追历史）。
3. **时区一致**：库里存的是带时区的 ISO 串，比较前统一 ``astimezone()``，
   避免 UTC 存 / 本地算导致「差 8 小时」这种一旦发生就极难自证的错。
"""
from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Literal

log = logging.getLogger("schedule")

Frequency = Literal["daily", "weekly", "monthly"]

#: 允许的频率。weekly 需要 ``weekday``（0=周一），monthly 需要 ``day``（1-28）。
FREQUENCIES = ("daily", "weekly", "monthly")

FREQ_LABEL = {"daily": "每日", "weekly": "每周", "monthly": "每月"}
WEEKDAY_LABEL = {0: "周一", 1: "周二", 2: "周三", 3: "周四",
                 4: "周五", 5: "周六", 6: "周日"}

#: monthly 的 day 上限取 28 而不是 31。
#: 取 31 时，「每月 31 号」在 2/4/6/9/11 月都不存在，得额外定「顺延到月末」还是
#: 「跳过」，两种语义都有人要，且解释成本远高于收益。限 28 之后每个月都有这一天，
#: 行为毫无歧义 —— 需要月末执行的场景用 weekly 或 daily 即可。
MAX_MONTHLY_DAY = 28


def _now() -> datetime:
    """带本地时区的当前时间。全模块只用这一个入口取「现在」。"""
    return datetime.now().astimezone()


def _iso(dt: datetime) -> str:
    return dt.astimezone().isoformat(timespec="seconds")


def _parse(s: str | None) -> datetime | None:
    """解析库里的时间串；空/坏值返回 None（调用方据此当作「从未跑过」）。

    坏值不能抛：一条手工改坏的记录不应该让整个调度线程停摆。
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        log.warning("时间串无法解析，按「从未执行」处理：%r", s)
        return None
    return dt if dt.tzinfo else dt.astimezone()


def compute_next(
    freq: str,
    at_time: str,
    *,
    weekday: int = 0,
    day: int = 1,
    base: datetime | None = None,
) -> datetime:
    """从 ``base`` 起按频率正推第一个**严格晚于** base 的触发时刻。

    ``base`` 默认当前时间。返回值带本地时区。

    三条分支各自的关键点：
      * daily  ：今天的时刻还没到就用今天，否则明天；
      * weekly ：目标星期几今天还没到就用本周那天，否则下周；
      * monthly：目标日今天还没到就用本月那天，否则下月 —— **这里是错月缺陷的
        原型位置**，无条件下推到下月会让「本月内尚未到来的日子」被整段跳过。
    """
    base = base or _now()
    hh, mm = _split_time(at_time)

    if freq == "daily":
        cand = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand <= base:
            cand += timedelta(days=1)
        return cand

    if freq == "weekly":
        wd = max(0, min(6, int(weekday)))
        ahead = (wd - base.weekday()) % 7
        cand = (base + timedelta(days=ahead)).replace(
            hour=hh, minute=mm, second=0, microsecond=0)
        if cand <= base:
            cand += timedelta(days=7)
        return cand

    if freq == "monthly":
        d = max(1, min(MAX_MONTHLY_DAY, int(day)))
        # 本月目标日可能不存在（比如 day=28 在 2 月一定有，所以这里其实恒存在；
        # 但 day 若被手工改成 31 就可能落到 3 月，故做一次收敛判断）。
        cand = _date_in_month(base.year, base.month, d, hh, mm)
        if cand is None or cand <= base:
            ny, nm = _add_month(base.year, base.month, 1)
            cand = _date_in_month(ny, nm, d, hh, mm)
        assert cand is not None
        return cand

    raise ValueError(f"未知频率：{freq!r}（只支持 {FREQUENCIES}）")


def _split_time(at_time: str) -> tuple[int, int]:
    """``"09:30"`` → ``(9, 30)``。非法值回落到 09:00 而不是抛。

    时间串来自用户输入，调度线程不该因为一条坏配置整体停摆；
    接口层（ScheduleIn）已经用正则先拦过一道。
    """
    try:
        hh_s, _, mm_s = (at_time or "").partition(":")
        hh, mm = int(hh_s), int(mm_s)
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh, mm
    except (TypeError, ValueError):
        pass
    log.warning("时刻无法解析，回落 09:00：%r", at_time)
    return 9, 0


def _add_month(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def _date_in_month(year: int, month: int, day: int,
                   hh: int, mm: int) -> datetime | None:
    """构造某月某日的本地时间；该月没有这一天时返回 None。"""
    try:
        return datetime(year, month, day, hh, mm).astimezone()
    except ValueError:
        return None


def describe(freq: str, at_time: str, *, weekday: int = 0,
             day: int = 1) -> str:
    """给人看的一句话节奏描述（前端直接展示，不自己拼字符串）。"""
    if freq == "weekly":
        # 「每周 周一 09:30」读起来别扭，合成「每周一 09:30」
        wd = WEEKDAY_LABEL.get(int(weekday), str(weekday))
        return f"每{wd} {at_time}"
    if freq == "monthly":
        return f"每月 {int(day)} 日 {at_time}"
    return f"每日 {at_time}"


# ---------------------------------------------------------------- 执行器
class Scheduler:
    """常驻线程：每 30 秒扫一遍启用的调度，到点就往 worker 队列塞作业。

    为什么是「轮询」而不是「每个调度一个定时器」：
      * 调度条数是个位数，30 秒粒度对「每日/每周/每月」完全够用；
      * 定时器方案要处理「配置改了什么」（关闭/改时刻/删掉）时取消并重建，
        容易漏一个 timer 就重复触发；
      * 轮询的状态全在数据库里，进程重启后自然恢复，不需要额外持久化 timer。

    幂等靠 ``next_run_at``：触发前先把它推进到下一次，再提交作业。这样即使
    提交动作抛异常、或线程在两步之间被杀，也不会在同一时刻触发第二次。
    """

    #: 轮询间隔（秒）。比最小可能间隔（1 分钟）小一档，保证误差不超过半分钟。
    TICK = 30.0

    def __init__(self, store: Any, worker: Any) -> None:
        self.store = store
        self.worker = worker
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="credit-scheduler",
                                        daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        # 不 join：TICK 最长 30 秒，退出时不该让进程等它。
        # 线程是 daemon，且 _run 每轮都会重查 _stop。
        if self._thread.is_alive():
            self._thread.join(timeout=2)

    # -- 主循环 ---------------------------------------------------------
    def _run(self) -> None:
        log.info("调度线程启动，轮询间隔 %.0fs", self.TICK)
        while not self._stop:
            try:
                self.tick()
            except Exception:                     # 调度线程不能死
                log.exception("调度轮询异常（本轮跳过）")
            # 用 _stop 分片等待，避免 stop() 时还要等满一个 TICK
            end = time.time() + self.TICK
            while not self._stop and time.time() < end:
                time.sleep(0.5)
        log.info("调度线程退出")

    def tick(self, now: datetime | None = None) -> list[str]:
        """跑一轮。返回本轮触发的 schedule_id 列表（测试直接调这个）。

        单条调度出错只记录、不影响其余条 —— 一条写坏的配置不该让别的调度
        集体停摆。
        """
        now = now or _now()
        fired: list[str] = []
        for s in self.store.list_schedules():
            if not s.get("enabled"):
                continue
            try:
                if self._tick_one(s, now):
                    fired.append(s["schedule_id"])
            except Exception:
                log.exception("调度 %s 执行异常（跳过，不影响其余）",
                              s.get("schedule_id"))
        return fired

    def _tick_one(self, s: dict, now: datetime) -> bool:
        """到点则提交作业并推进 next_run_at。返回是否触发。"""
        sid = s["schedule_id"]
        due = _parse(s.get("next_run_at"))

        # next_run_at 缺失（旧数据 / 手工插入）时补算一次，而不是永远不跑
        if due is None:
            nxt = compute_next(s["freq"], s["at_time"],
                               weekday=s.get("weekday") or 0,
                               day=s.get("day") or 1, base=now)
            self.store.set_schedule_next_run(sid, _iso(nxt), now=now)
            log.info("调度 %s 缺少 next_run_at，已补算为 %s", sid, _iso(nxt))
            return False

        if due > now:
            return False

        # 先把下一次推进再提交：提交之后进程崩溃也不会重复触发
        nxt = compute_next(s["freq"], s["at_time"],
                           weekday=s.get("weekday") or 0,
                           day=s.get("day") or 1, base=now)
        lag = (now - due).total_seconds()
        if lag > 60:
            # 落后说明触发时刻进程不在线（关机/重启/服务没起）。
            # 只补这一次，不追历史：连落后 10 天也只跑 1 次。
            log.warning("调度 %s 原定 %s 已错过 %.0f 分钟，补跑一次",
                        sid, s.get("next_run_at"), lag / 60)
        self.store.set_schedule_next_run(sid, _iso(nxt), advance_marker=True,
                                         now=now)

        names = self._names_of(s)
        if not names:
            log.warning("调度 %s 的企业名单为空，跳过本次", sid)
            self.store.set_schedule_result(sid, last_state="skipped",
                                           last_error="名单为空，未提交作业")
            return False

        req = {
            "names": names,
            "webhook": s.get("webhook") or None,
            "mail_to": s.get("mail_to") or None,
            "always_notify": bool(s.get("always_notify")),
            "min_delay": float(s.get("min_delay") or 3.5),
            "max_delay": float(s.get("max_delay") or 6.5),
            # 便于在作业详情里看出「这条是调度跑出来的」，而不是人去翻时间猜
            "source": "schedule",
            "schedule_id": sid,
            "schedule_name": s.get("name") or "",
        }
        job_id = self.store.create_job(names, req)
        self.worker.submit(job_id)
        self.store.set_schedule_result(sid, last_job_id=job_id,
                                       last_state="queued")
        log.info("调度 %s 触发作业 %s（%d 家）", sid, job_id, len(names))
        return True

    @staticmethod
    def _names_of(s: dict) -> list[str]:
        """从调度记录里取出企业名单（库里存 JSON 数组文本）。"""
        raw = s.get("names") or "[]"
        try:
            val = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            log.warning("调度 %s 的名单不是合法 JSON：%r", s.get("schedule_id"), raw)
            return []
        if not isinstance(val, list):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for x in val:
            n = str(x or "").strip()
            if n and not n.startswith("#") and n not in seen:
                seen.add(n)
                out.append(n)
        return out


# ---------------------------------------------------------------- 一键补算
def ensure_next_run(store: Any) -> int:
    """启动时为所有缺 ``next_run_at`` 的启用调度补算。返回补了几条。

    兼容「调度表刚加、老记录没这个字段」的升级路径；正常新增走
    ``Store.add_schedule`` 时就会算好，这里只是兜底。
    """
    fixed = 0
    for s in store.list_schedules():
        if s.get("next_run_at") or not s.get("enabled"):
            continue
        with contextlib.suppress(Exception):
            nxt = compute_next(s["freq"], s["at_time"],
                               weekday=s.get("weekday") or 0,
                               day=s.get("day") or 1)
            store.set_schedule_next_run(s["schedule_id"], _iso(nxt))
            fixed += 1
    return fixed
