# -*- coding: utf-8 -*-
"""容器启动入口：先做启动前的必要清理与校验，再交给 aggregator.py。

为什么需要这一层
---------------
``credit_check.py:168`` 用的是 ``from camoufox.sync_api import Camoufox``。
实测发现一个坑，必须在这里提前处理：

**UBO 插件残缺目录**：``camoufox fetch`` 从 addons.mozilla.org 拉 UBO
时返回 451，在 ``$HOME/.cache/camoufox/addons`` 留下没有 manifest.json
的残目录，之后 ``Camoufox()`` 默认要加载它，直接
``InvalidAddonPath: manifest.json is missing``。这里在启动前删掉它。

另有一处刻意设计：**camoufox 的 addons 参数无法从外部关掉**
（``credit_check.py`` 里没有暴露这个入口），所以只能「让目录不存在」，
 camoufox 发现没有 addons 目录时不会去加载——这是唯一不改业务代码的做法。

顺手做两件事：
- 把 ``PYTHONPATH`` 固定成两个模块目录（与 aggregator.py 内联做的事一致，
  提前做可以让日志与直接 `python -c "import credit_check"` 的排查方式统一）；
- 若 ``AGG_WORKERS`` 被误设为 >1，直接拒绝启动：scheduler / worker /
  ``check_runs`` 单行状态机都是进程内单实例，多进程会静默错乱。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent

# ---- 1. 多 worker 硬约束：宁肯启动失败，也不要静默错乱 --------------------
if os.getenv("AGG_WORKERS", "1") not in ("1", ""):
    sys.exit(
        "AGG_WORKERS 必须是 1：credit 的作业队列与 Camoufox 实例、"
        "WIPO 的 check_runs 单行状态机都是进程内单实例，多 worker 会错乱。"
    )

# ---- 2. 清掉 UBO 残缺目录（451 下载失败的产物）---------------------------
_cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
_addons = _cache / "camoufox" / "addons"
if _addons.is_dir():
    shutil.rmtree(_addons, ignore_errors=True)
    print(f"[entrypoint] 已清理损坏的 camoufox addons 目录：{_addons}", flush=True)

# ---- 3. 让两个模块都能被 import（与 aggregator.py:61-63 同口径）----------
for _p in (str(BASE / "modules" / "wipo"), str(BASE / "modules" / "credit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---- 4. 交给聚合入口 ------------------------------------------------------
# 用 runpy 而不是 import aggregator：aggregator.py 底部是 `python aggregator.py`
# 的直接调用形态，runpy 会正确执行 `if __name__ == "__main__"` 分支。
import runpy  # noqa: E402

runpy.run_path(str(BASE / "aggregator.py"), run_name="__main__")
