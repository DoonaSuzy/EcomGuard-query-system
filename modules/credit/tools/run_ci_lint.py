"""本地复跑 CI lint 步骤：把 workflow 里的 heredoc python 抽出来实跑。

CI 上这些脚本只在 ubuntu-latest 跑，本地没有 runner 就无法确认它们真的
能跑通。这个脚本读 .github/workflows/ci.yml，取出指定 step 的 python 正文，
在当前目录（含 .venv）下实跑，验证「本地复跑」这条路径不会因为 .venv
里的第三方文件而误报。
"""
from __future__ import annotations

import io
import subprocess
import sys
import textwrap

import yaml

CI = ".github/workflows/ci.yml"


def extract_python(run: str) -> str:
    """从 `python - <<'PY' ... PY` 里取出中间那段。"""
    marker = "<<" + "'PY'"
    if marker not in run:
        raise SystemExit(f"step 不是 heredoc 形式：\n{run[:120]}")
    body = run.split(marker, 1)[1]
    body = body.rsplit("PY", 1)[0]
    return textwrap.dedent(body)


def main() -> int:
    doc = yaml.safe_load(io.open(CI, encoding="utf-8"))
    steps = doc["jobs"]["lint"]["steps"]
    targets = [s for s in steps if "PY" in s.get("run", "") and "python - <<" in s.get("run", "")]
    if not targets:
        raise SystemExit("没找到 heredoc 形式的 step")
    failed = 0
    for st in targets:
        name = st.get("name", "?")
        print("=" * 60)
        print(f"[{name}]")
        r = subprocess.run([sys.executable, "-c", extract_python(st["run"])],
                           capture_output=True, text=True, encoding="utf-8")
        print(r.stdout.strip() or "(no stdout)")
        if r.stderr.strip():
            print("STDERR:", r.stderr.strip()[:500])
        print("rc =", r.returncode)
        failed += r.returncode != 0
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
