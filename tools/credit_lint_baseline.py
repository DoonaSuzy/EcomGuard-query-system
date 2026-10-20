"""credit 模块的 lint 基线守卫：只拦「新增」，不要求一次清零。

## 为什么需要这个脚本

`.github/workflows/ci.yml` 原来那一步是

    python -m ruff check <credit 文件> --exit-zero

注释写的是「本轮 CI 先只守『不再新增』」。但 `--exit-zero` 的语义是
**永远返回 0**（实测 exit code = 0），所以那一步无论新增多少条 error 都是绿的
——「不再新增」的守卫实际不生效，等于没有守卫。

本脚本把「不再新增」落成可执行的判定：

1. 用 `ruff check --output-format json` 拿到当前全部问题；
2. 聚合成 `(文件相对路径, 规则代号) -> 条数` 的多重集；
3. 与仓库内 `tools/credit_lint_baseline.json` 比对：
   - 出现基线里没有的 `(文件, 规则)`            → **失败**（新增了新的问题类型）
   - 某个 `(文件, 规则)` 的条数比基线多          → **失败**（同类问题变多）
   - 条数变少                                    → 通过，并提示可更新基线
4. 退出码：有新增 = 1，否则 0。

这样「顺手改好一处」不会被拦，「顺手引入一处」一定被拦。基线归零是另一件事
（见 docs/P1 清单），不阻塞本次。

## 用法

    python tools/credit_lint_baseline.py              # 守卫（CI 用）
    python tools/credit_lint_baseline.py --report     # 只打印当前分布
    python tools/credit_lint_baseline.py --update     # 把当前状态写成新基线

`--update` 只应该在**确有正当理由**（如整批修完之后基线该下降）时手动跑，
并连同基线文件一起提交。
"""
from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tools" / "credit_lint_baseline.json"

# 受守卫的文件集：credit 目录下**全部手写 Python 文件**。
# 口径是「不挑文件」——漏掉一个文件就等于开了个后门：往里写多少新问题都不会被拦。
# test_regression.py 里的 test_guarded_set_is_all_credit_python_files 会反向
# 校验这份清单与磁盘上的 *.py 完全一致，增删文件忘记同步会直接红。
GUARDED = [
    "modules/credit/api.py",
    "modules/credit/bench_captcha.py",
    "modules/credit/bench_ocr_variants.py",
    "modules/credit/credit_check.py",
    "modules/credit/healthcheck.py",
    "modules/credit/logkit.py",
    "modules/credit/notify.py",
    "modules/credit/roster.py",
    "modules/credit/schedule.py",
    "modules/credit/test_regression.py",
]


def run_ruff() -> list[dict]:
    """跑 ruff 拿 JSON 结果。ruff 不在环境里时明确报错，不静默当成 0 问题。"""
    cmd = [sys.executable, "-m", "ruff", "check", *GUARDED, "--output-format", "json"]
    # check=False 是刻意的：ruff 发现问题时退出码非 0 属正常，结果只从 stdout 的
    # JSON 判定。真正的失败（ruff 没装、参数错）会在下面的 JSON 解析处暴露。
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", check=False)
    # ruff 发现问题时退出码非 0，这是正常的；只有解析不出 JSON 才算真失败。
    try:
        return json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        sys.stderr.write("ruff 输出不是合法 JSON，无法守卫。\n")
        sys.stderr.write("stdout:\n" + (proc.stdout or "")[:2000] + "\n")
        sys.stderr.write("stderr:\n" + (proc.stderr or "")[:2000] + "\n")
        raise SystemExit(2)


def key_of(item: dict) -> tuple[str, str]:
    """把 ruff 的绝对路径裁成相对仓库根的 posix 路径，便于跨机器比对。"""
    raw = (item.get("filename") or "").replace("\\", "/")
    marker = "/" + ROOT.name + "/"
    path = raw.split(marker, 1)[-1] if marker in raw else raw
    # Windows 下 ruff 可能给大写盘符路径，统一取仓库内相对段
    for rel in GUARDED:
        if path.endswith(rel):
            path = rel
            break
    return path, item.get("code") or "?"


def collect() -> collections.Counter:
    return collections.Counter(key_of(it) for it in run_ruff())


def load_baseline() -> collections.Counter:
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    return collections.Counter({(k.split("::", 1)[0], k.split("::", 1)[1]): int(v) for k, v in data.items()})


def new_problems(current: collections.Counter, base: collections.Counter) -> list[str]:
    """返回「新增」问题的描述列表；空列表 = 通过。

    这里是守卫的**唯一判定点**，刻意做成纯函数：
    - 只认「基线里没有的组合」与「同类条数变多」两个方向；
    - 减少、消失一律不算新增（否则没人愿意顺手改，守卫就成了禁止改代码）。

    动机：它替代的那一步是 `ruff check --exit-zero`，而 `--exit-zero` 永远返回 0,
    「不再新增」写在注释里却从不生效。判定逻辑若跟着那个伪装一起糊涂，
    守卫会以同样的方式退化成摆设。
    """
    out = [f"新增组合  {p}  {c}  ×{current[(p, c)]}" for p, c in sorted(set(current) - set(base))]
    out += [f"数量增加  {p}  {c}  {base[(p, c)]} → {current[(p, c)]}"
            for p, c in sorted(set(current) & set(base)) if current[(p, c)] > base[(p, c)]]
    return out


def dump_baseline(counter: collections.Counter) -> str:
    data = {f"{p}::{c}": n for (p, c), n in sorted(counter.items())}
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def report(counter: collections.Counter) -> None:
    print(f"当前 credit lint 问题总计 {sum(counter.values())} 条，分布在 {len(counter)} 个（文件, 规则）组合：")
    by_file: dict[str, int] = collections.defaultdict(int)
    for (path, _code), n in counter.items():
        by_file[path] += n
    for path in GUARDED:
        n = by_file.get(path, 0)
        flag = "OK" if n == 0 else "  "
        print(f"  {flag} {n:>3}  {path}")
    print("\n按规则：")
    by_code: dict[str, int] = collections.defaultdict(int)
    for (_path, code), n in counter.items():
        by_code[code] += n
    for code, n in sorted(by_code.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {n:>3}  {code}")


def main() -> int:
    ap = argparse.ArgumentParser(description="credit 模块 lint 基线守卫")
    ap.add_argument("--update", action="store_true", help="把当前状态写为新基线")
    ap.add_argument("--report", action="store_true", help="只打印当前分布，不做守卫判定")
    args = ap.parse_args()

    current = collect()

    if args.report:
        report(current)
        return 0

    if args.update:
        BASELINE.write_text(dump_baseline(current), encoding="utf-8")
        print(f"基线已更新：{BASELINE.relative_to(ROOT)}（{sum(current.values())} 条 / {len(current)} 组合）")
        return 0

    if not BASELINE.exists():
        sys.stderr.write(f"缺少基线文件 {BASELINE.relative_to(ROOT)}。先跑 --update 并提交。\n")
        return 2

    base = load_baseline()
    problems = new_problems(current, base)
    if problems:
        sys.stderr.write("credit lint 基线守卫：检测到**新增**问题（这是唯一被拦的方向）。\n\n")
        for line in problems:
            sys.stderr.write(f"  {line}\n")
        sys.stderr.write(
            "\n处理方式二选一：\n"
            "  1) 修掉这些新问题（优先）；\n"
            "  2) 确认是既有债务的正当调整，跑 python tools/credit_lint_baseline.py --update 并把基线文件一起提交。\n"
        )
        return 1

    print(f"credit lint 基线守卫通过：当前 {sum(current.values())} 条，未超过基线 {sum(base.values())} 条。")
    shrank = sorted((k, base[k], current[k]) for k in set(current) & set(base) if current[k] < base[k])
    gone = sorted(set(base) - set(current))
    if shrank or gone:
        print("提示：以下问题比基线少了，可以把基线降下来（--update）：")
        for (path, code), before, after in shrank:
            print(f"  减少  {path}  {code}  {before} → {after}")
        for path, code in gone:
            print(f"  消失  {path}  {code}  （基线 {base[(path, code)]} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
