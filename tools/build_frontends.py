#!/usr/bin/env python3
"""构建前端产物（三套：统一前端 + 两个模块各自的独立版与子路径版）。

## 一、统一前端（聚合入口唯一使用的产物）

    web/dist/   默认 base。`python aggregator.py` 只读这一套。

统一前端的源码在仓库根的 ``web/``，但它**不复制**两个模块的页面代码 ——
通过 Vite 别名（``@wipo`` → WIPO 的 web/src、``@`` → credit 的 frontend/src）
直接引用，所以两个模块改了自己的页面，统一前端重新构建即生效。

## 二、模块自己的两套产物（只服务模块独立运行与独立回归）

  Vite 的**动态 chunk 引用是相对路径**（`import("./OverviewView-x.js")`），
  浏览器按**模块 URL** 解析 —— 这既不是文档 URL（加 `<base href>` 无效），
  也不是托管层能改写的东西（chunk 已烧进主 bundle）。所以子路径部署
  只能在**构建期**把 base 写进产物。

  而模块自身独立运行时必须用默认 base（WIPO 的 E2E 直接打站点根，
  credit 的 test_regression.py 也按根路径断言），否则资源 404、测试全红。

  两个约束互斥 → 两套产物：
    dist/      默认 base，模块独立运行 + 独立回归用
    dist-agg/  `--base=/wipo/`、`--base=/credit/`，供模块以子路径单独托管
               （聚合入口已不再使用，保留是因为模块独立部署仍可能需要）

用法：
    python tools/build_frontends.py          # 构建三套（CI 用这个）
    python tools/build_frontends.py --agg    # 跳过模块的 dist/，仍建统一前端

前置：node_modules 缺失时脚本会自动 `npm ci`（三个前端目录都有
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

# 统一前端（聚合入口唯一使用的产物）
UNIFIED_WEB = ROOT / "web"

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

    # 子路径版：带 --base=/wipo/ 之类的产物，供模块以子路径单独托管。
    # 聚合入口已不再使用它（统一前端只读 web/dist），保留是为了模块独立部署。
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


def build_unified() -> None:
    """构建统一前端到 ``web/dist``。

    这一套是聚合入口**唯一**读取的产物，所以无论带不带 ``--agg`` 都要建 ——
    漏建会让 ``python aggregator.py`` 起来之后整站 404（接口正常、页面全无），
    属于最难排查的那种「看起来启动了」的故障。
    """
    web = UNIFIED_WEB
    if not web.is_dir():
        raise SystemExit(f"找不到统一前端目录：{web}")
    if not (web / "node_modules").is_dir():
        print("  unified: node_modules 缺失，自动 npm ci …")
        run(["npm", "ci"], web)
        if not (web / "node_modules").is_dir():
            raise SystemExit(
                f"{web} npm ci 后仍缺少 node_modules，请手动执行 `npm ci` 查看报错"
            )

    shutil.rmtree(web / "dist", ignore_errors=True)
    run(["npm", "run", "build"], web)

    index = web / "dist" / "index.html"
    if not index.is_file():
        raise SystemExit("unified: web/dist/index.html 未生成，构建可能被 vite 忽略")
    html = index.read_text(encoding="utf-8")
    # 统一前端挂在站点根，所以资源必须是绝对路径 /assets/。
    # 若哪天被改成相对路径 ./assets/，深链（/jobs/xxx）下就会 404 —— 这里挡住。
    if 'src="/assets/' not in html:
        raise SystemExit(
            "unified: web/dist/index.html 的资源不是绝对路径 /assets/，"
            f"实际内容：{[s for s in html.split(chr(34)) if 'assets/' in s][:3]}"
        )
    print("  ✓ unified web/dist 已生成（资源前缀 /assets/）")


def main() -> int:
    ap = argparse.ArgumentParser(description="构建统一前端与两个模块的前端产物")
    ap.add_argument(
        "--agg",
        action="store_true",
        help="跳过两个模块的 dist/（独立运行版），仍会构建统一前端与 dist-agg",
    )
    args = ap.parse_args()

    for module, web, prefix, base_cmd in TARGETS:
        build(module, web, prefix, base_cmd, args.agg)

    # 统一前端最后建：它引用两个模块的源码，模块自己的产物先出更利于定位问题
    build_unified()

    print("\n全部构建完成。")
    print("  聚合运行：`python aggregator.py` 读 web/dist（统一前端）")
    print("  独立运行：各模块 `python -m uvicorn ...` 读自己的 dist/")
    print("  子路径托管：模块的 dist-agg/（聚合入口已不使用）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
