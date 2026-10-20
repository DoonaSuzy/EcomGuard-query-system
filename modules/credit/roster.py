"""
巡检名单：一批典型在册主体 + 一批已注销/吊销主体，用于营业执照定时巡检。

设计口径（与 job 真实结果一致，见 docs/巡检名单.md）：

  1. **有信用代码的一律用代码查**，不用全称。
     实测「北京字节跳动科技有限公司」按名称查只会得到候选「跳动字节科技(北京)有限公司」
     （判定 unknown）；统一社会信用代码是精确键，命中即 1 条记录、9 秒内出结果。
  2. **已注销/吊销的主体没有代码**（信用中国只收在册主体），只能留全称，
     巡检结果是 notfound —— 这本身就是「要有人看一眼」的信号，不是失败。
  3. 每条都可由工具复现：名单生成自 2026-10-20 在 部署实例 上
     真实跑完的一次 63 家批量作业，verdict 分布 ok 56 / unknown 2 / notfound 5。
"""

import json
from pathlib import Path

ROSTER_PATH = Path(__file__).resolve().parent / "巡检名单.json"


def load_roster() -> list[str]:
    """返回调度用的查询键列表（信用代码优先，无代码时为企业全称）。"""
    return json.loads(ROSTER_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    keys = load_roster()
    print(f"{len(keys)} 家")
    for k in keys:
        print(" ", k)
