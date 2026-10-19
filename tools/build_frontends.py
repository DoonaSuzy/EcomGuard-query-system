#!/usr/bin/env python3
"""构建两个模块的前端产物（两套：独立运行版 + 聚合子路径版）。

为什么是两套而不是一套：

  Vite 的**动态 chunk 引用是相对路径**（`import("./OverviewView-x.js")`），
  浏览器按**模块 URL** 解析 —— 这既不是文档 URL（加 `<base href>` 无效），
  也不是托管层能改写的东西（chunk 已烧进主 bundle）。所以子路径部署
  只能在**构建期**把 base 写进产物：`vite build --base=/wipo/`。

  而模块自身独立运行时必须用默认 base（WIPO 的 110 项 E2E 直接打站点根，
  credit 的 test_regression.py 也按根路径断言），否则资源 404、测试全红。

  两个约束互斥 → 两套产物：
    dist/      默认 base，模块独立运行 + 独立回归用（**模块仓库里本来就有**）
    dist-agg/  `--base=/wipo/`、`--base=/credit/`，聚合系统托管用

用法：
    python tools/build_frontends.py          # 构建两套
    python tools/build_frontends.py --agg    # 只构建 dist-agg（CI 用）

前置：两个模块的 node_modules 缺失时脚本会自动 `npm ci`（两个模块都有
package-lock.json，版本可复现）；本地手动装：先在各前端目录执行 `npm ci`。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Windows 控制台默认 GBK，打印 ✓ 等字符会 UnicodeEncodeError（实测踩过）；
# CI 的 Linux 无此问题，这里统一兜底成可编码输出。
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
MODULES = ROOT / "modules"

# (模块名, 前端目录, 子路径, 构建命令前缀)
TARGETS = [
    ("wipo", MODULES / "wipo" / "web", "/wipo/", ["npm", "run", "build"]),
    ("credit", MODULES / "credit" / "frontend", "/credit/", ["npm", "run", "build"]),
]


def run(cmd: list[str], cwd: Path) -> None:
    print(f"\n$ {' '.join(cmd)}   (cwd={cwd})")
    proc = subprocess.run(cmd, cwd=str(cwd), shell=(sys.platform == "win32"), check=False)
    if proc.returncode != 0:
        raise SystemExit(f"构建失败，退出码 {proc.returncode}：{' '.join(cmd)}")


def build(module: str, web: Path, prefix: str, base_cmd: list[str], agg_only: bool) -> None:
    if not web.is_dir():
        raise SystemExit(f"找不到前端目录：{web}")
    if not (web / "node_modules").is_dir():
        # CI 环境没有 node_modules（.gitignore 排除），自动 npm ci 补齐；
        # 本地首次运行同样受益。失败信息保留人工指引。
        print(f"  {module}: node_modules 缺失，自动 npm ci …")
        run(["npm", "ci"], web)
        if not (web / "node_modules").is_dir():
            raise SystemExit(
                f"{web} npm ci 后仍缺少 node_modules，请手动执行 `npm ci` 查看报错"
            )

    if not agg_only:
        # 独立运行版：默认 base，模块自己的 dist/
        # （CI 的 E2E job 也需要这套产物——WIPO 的 110 项 E2E 打默认 base）
        shutil.rmtree(web / "dist", ignore_errors=True)
        run(base_cmd, web)

    # 聚合版：子路径 base，dist-agg/
    shutil.rmtree(web / "dist-agg", ignore_errors=True)
    run(base_cmd + ["--", f"--base={prefix}", "--outDir", "dist-agg"], web)

    index = web / "dist-agg" / "index.html"
    if not index.is_file():
        raise SystemExit(f"{module}: dist-agg/index.html 未生成，构建可能被 vite 忽略")
    html = index.read_text(encoding="utf-8")
    if f'"{prefix}assets/' not in html:
        raise SystemExit(
            f"{module}: dist-agg 的 index.html 未带 {prefix} 前缀，"
            f"实际内容：{[s for s in html.split(chr(34)) if 'assets/' in s][:3]}"
        )
    print(f"  ✓ {module} dist-agg 前缀正确（{prefix}assets/）")


def main() -> int:
    ap = argparse.ArgumentParser(description="构建两个模块的前端产物")
    ap.add_argument("--agg", action="store_true", help="只构建聚合用的 dist-agg")
    args = ap.parse_args()

    for module, web, prefix, base_cmd in TARGETS:
        build(module, web, prefix, base_cmd, args.agg)

    print("\n全部构建完成。")
    print("  独立运行：各模块 `python -m uvicorn ...` 读自己的 dist/")
    print("  聚合运行：`python aggregator.py` 读 dist-agg/（本仓库根目录）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
