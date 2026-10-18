"""校验 requirements.txt 与 pyproject.toml 的依赖清单没有漂移。

两个文件同时存在是历史决定：``pyproject.toml`` 是包元数据的唯一权威，
``requirements.txt`` 是本机「一次装好」的安装快照。双源维护必然漂移，
因此把这条断言变成可执行检查，由 CI 与本地共同把关。

用法::

    python scripts/check_deps_consistency.py

退出码非 0 表示存在漂移，并逐条列出差异。
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"

#: ``[project].dependencies`` 用 PEP 508 声明，requirements.txt 是安装快照。
#: 版本约束符在两侧允许不同写法（``>=`` 与 ``==``），这里只比较包名。
_NAME_RE = re.compile(r"^([A-Za-z0-9._-]+)")


def _package_name(requirement: str) -> str:
    """从依赖声明里取出规范化包名。"""
    match = _NAME_RE.match(requirement.strip())
    if not match:
        raise ValueError(f"无法解析依赖声明: {requirement!r}")
    return match.group(1).lower().replace("_", "-")


def _pyproject_packages() -> tuple[set[str], set[str]]:
    """返回 ``(运行时依赖, 开发依赖)`` 两组包名。"""
    with PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)
    runtime = {_package_name(dep) for dep in data["project"]["dependencies"]}
    dev: set[str] = set()
    for extra_name, extra_deps in data["project"].get("optional-dependencies", {}).items():
        #: 只有 ``dev`` 额外项需要与 requirements-dev.txt 对齐，
        #: 其他额外项（如未来的 ``docs``）不参与安装快照比对。
        if extra_name == "dev":
            dev.update(_package_name(dep) for dep in extra_deps)
    return runtime, dev


def _requirements_packages() -> set[str]:
    names: set[str] = set()
    for raw_line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        names.add(_package_name(line))
    return names


def _dev_requirements_packages() -> set[str]:
    dev_requirements = PROJECT_ROOT / "requirements-dev.txt"
    if not dev_requirements.exists():
        return set()
    names: set[str] = set()
    for raw_line in dev_requirements.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        names.add(_package_name(line))
    return names


def main() -> int:
    runtime_declared, dev_declared = _pyproject_packages()
    runtime_pinned = _requirements_packages()
    dev_pinned = _dev_requirements_packages()

    failures: list[str] = []

    only_pyproject = sorted(runtime_declared - runtime_pinned)
    only_requirements = sorted(runtime_pinned - runtime_declared)
    if only_pyproject or only_requirements:
        if only_pyproject:
            failures.append("运行时依赖只在 pyproject.toml 声明，requirements.txt 缺失：")
            failures.extend(f"  - {name}" for name in only_pyproject)
        if only_requirements:
            failures.append("运行时依赖只在 requirements.txt 固定，pyproject.toml 缺失：")
            failures.extend(f"  + {name}" for name in only_requirements)

    only_dev_pyproject = sorted(dev_declared - dev_pinned)
    only_dev_requirements = sorted(dev_pinned - dev_declared)
    if only_dev_pyproject or only_dev_requirements:
        if only_dev_pyproject:
            failures.append("开发依赖只在 pyproject.toml 声明，requirements-dev.txt 缺失：")
            failures.extend(f"  - {name}" for name in only_dev_pyproject)
        if only_dev_requirements:
            failures.append("开发依赖只在 requirements-dev.txt 固定，pyproject.toml 缺失：")
            failures.extend(f"  + {name}" for name in only_dev_requirements)

    if not failures:
        print(
            f"依赖清单一致：运行时 {len(runtime_declared)} 个包，开发 {len(dev_declared)} 个包。"
        )
        return 0

    print("\n".join(failures))
    print()
    print("改依赖时两侧都要改：pyproject.toml 是元数据，requirements*.txt 是安装快照。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
