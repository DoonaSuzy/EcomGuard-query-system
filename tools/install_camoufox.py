#!/usr/bin/env python3
"""离线安装 Camoufox 浏览器本体。

为什么需要这个脚本（而不是直接用 `python -m camoufox fetch`）：
    目标机器（192.168.101.91）到 github.com / api.github.com 完全不通
    （实测 curl 10 秒超时、0 字节）。而 camoufox fetch 的流程是
    「打 api.github.com 列 releases → 下 1.2 GB 资产 → 解压」，
    在这个网络里必然失败，且失败得很慢（每次都要等超时）。

    所以做成两条路：
      1) 有预置 zip（vendor/camoufox-*-lin.x86_64.zip）→ 直接解压，零外网；
      2) 没有预置 zip → 回退到官方 fetch（适合能连 GitHub 的网络）。

    这与 pkgman.CamoufoxFetcher.install() 的行为保持一致：
      unzip(zip, INSTALL_DIR) + 写 version.json + chmod -R 755
    version.json 的 version/release 拆分复用 pkgman 里的**同一个正则**，
    避免自己另写一套解析导致和官方逻辑不一致。

用法：
    python3 tools/install_camoufox.py [--cache-dir DIR] [--vendor-dir DIR]
退出码：0 已就绪；非 0 失败（compose 会因此拦住 app 启动）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# 与 camoufox/pkgman.py 完全一致的正则（CamoufoxFetcher.__init__ 里的 self.pattern）。
# 保持一致的唯一目的是让 version.json 里的 version/release 拆分结果与官方一致，
# 否则 Version.from_path() 读出来可能被判为不受支持。
_ASSET_RE = re.compile(r"camoufox-(?P<version>.+)-(?P<release>.+)-lin\.x86_64\.zip")

# pkgman.LAUNCH_FILE['lin']，用来判断「装好了没有」
_LAUNCH_BIN = "camoufox-bin"


def _log(msg: str) -> None:
    print(f"[install_camoufox] {msg}", flush=True)


def _default_cache_dir() -> Path:
    """与 pkgman.INSTALL_DIR 同源：platformdirs.user_cache_dir("camoufox")。"""
    try:
        from platformdirs import user_cache_dir

        return Path(user_cache_dir("camoufox"))
    except ImportError:  # pragma: no cover - platformdirs 一定在依赖里
        return Path.home() / ".cache" / "camoufox"


def already_installed(cache: Path) -> bool:
    """判据与 camoufox 运行期一致：可执行文件在，且 version.json 能解析。

    只看目录非空是不够的：中断的解压会留下半套文件，
    camoufox 启动时才报错，排查成本高得多。
    """
    if not (cache / _LAUNCH_BIN).exists():
        return False
    try:
        data = json.loads((cache / "version.json").read_text("utf-8"))
    except (OSError, ValueError):
        return False
    return bool(data.get("release"))


def install_from_zip(zip_path: Path, cache: Path) -> None:
    """解压预置 zip 到 cache，并写 version.json。"""
    m = _ASSET_RE.match(zip_path.name)
    if not m:
        raise SystemExit(
            f"zip 文件名不符合 camoufox 资产命名，无法推导版本：{zip_path.name}\n"
            f"期望形如 camoufox-<version>-<release>-lin.x86_64.zip"
        )

    # 清掉旧内容：与官方 install() 的 cleanup() 一致，
    # 避免旧版本残留文件混在新版本里。
    if cache.exists():
        _log(f"清理旧目录 {cache}")
        shutil.rmtree(cache)
    cache.mkdir(parents=True, exist_ok=True)

    _log(f"解压 {zip_path.name} -> {cache}")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(cache)

    # version.json 由我们自己写：官方是 orjson.dumps，这里用标准库等价实现。
    payload = {"version": m.group("version"), "release": m.group("release")}
    (cache / "version.json").write_text(json.dumps(payload), encoding="utf-8")
    _log(f"已写 version.json {payload}")

    # 官方 install() 在非 win 平台会 chmod -R 755；
    # 少了这步 camoufox-bin 可能没有执行位，启动时报 Permission denied。
    for p in [cache, *cache.rglob("*")]:
        try:
            os.chmod(p, 0o755)
        except OSError:
            pass


def fetch_from_network(cache: Path) -> None:
    """回退路径：调用官方 fetch（需要能连 GitHub）。"""
    _log("未找到预置 zip，回退到官方 fetch（需要能访问 GitHub）")
    env = dict(os.environ)
    # 官方 fetch 把 INSTALL_DIR 固定为 user_cache_dir("camoufox")，
    # 由 HOME/XDG_CACHE_HOME 决定；这里显式对齐 cache 的父目录。
    env.setdefault("XDG_CACHE_HOME", str(cache.parent))
    rc = subprocess.call([sys.executable, "-m", "camoufox", "fetch"], env=env)
    if rc != 0:
        raise SystemExit(f"camoufox fetch 失败（退出码 {rc}）")


def main() -> int:
    ap = argparse.ArgumentParser(description="离线安装 Camoufox 浏览器本体")
    ap.add_argument("--cache-dir", default=None, help="安装目录（默认 user_cache_dir）")
    ap.add_argument("--vendor-dir", default="vendor", help="预置 zip 所在目录")
    ap.add_argument("--force", action="store_true", help="已安装也重新装")
    args = ap.parse_args()

    cache = Path(args.cache_dir) if args.cache_dir else _default_cache_dir()
    _log(f"安装目录 {cache}")

    if not args.force and already_installed(cache):
        _log("已安装，跳过")
        return 0

    vendor = Path(args.vendor_dir)
    zips = sorted(vendor.glob("camoufox-*-lin.x86_64.zip")) if vendor.is_dir() else []
    if zips:
        _log(f"发现预置包 {zips[0].name}（{zips[0].stat().st_size / 1024 / 1024:.0f} MB）")
        install_from_zip(zips[0], cache)
    else:
        fetch_from_network(cache)

    # addons（uBlock Origin）在部分网络下返回 451，留下残缺目录后
    # Camoufox() 默认加载它会抛 InvalidAddonPath: manifest.json is missing。
    # 这里无条件删掉；运行期由 credit_check 显式 exclude_addons=[UBO] 兜底
    # （注意 addons=[] 不管用：camoufox 的 add_default_addons 是无条件执行的）。
    addons = cache / "addons"
    if addons.exists():
        _log("删除不完整的 addons 目录（UBO 下载 451 的已知后果）")
        shutil.rmtree(addons, ignore_errors=True)

    if not already_installed(cache):
        raise SystemExit("安装后校验失败：camoufox-bin 或 version.json 缺失")

    _log("就绪")
    return 0


if __name__ == "__main__":
    sys.exit(main())
