# -*- coding: utf-8 -*-
"""聚合系统真机验收：起 uvicorn → httpx 打接口 → Playwright 打两个前端。

覆盖：壳页、两个子路径深链、跨模块切换、真实交互（新增商标 / 提交作业）、
接口 16 条、静态资源不串台、全程无 4xx/5xx。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import httpx
from playwright.sync_api import sync_playwright

ROOT = r"F:\projects\GO1\aggregated-system"
PY = os.path.join(os.environ["TEMP"], "compat_venv", "Scripts", "python.exe")

tmp = tempfile.mkdtemp(prefix="agg_accept_")
env = dict(os.environ)
env.update({
    "PYTHONPATH": ROOT,
    "PYTHONIOENCODING": "utf-8",
    "WIPO_DATABASE__URL": "sqlite+aiosqlite:///" + (tmp + "/agg.db").replace("\\", "/"),
    "WIPO_REPORTS_DIR": tmp + r"\reports",
    "CREDIT_DB": tmp + r"\jobs.db",
    "CREDIT_OUT": tmp + r"\results",
    "HTTPS_PROXY": "", "HTTP_PROXY": "",
})

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]

base = f"http://127.0.0.1:{port}"
proc = subprocess.Popen(
    [PY, "-m", "uvicorn", "aggregator:app", "--host", "127.0.0.1",
     "--port", str(port), "--workers", "1", "--log-level", "warning"],
    cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    text=True, encoding="utf-8", errors="replace",
)

passed = failed = 0


def chk(name: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name}  {extra}")


try:
    # ---- 等端口就绪 ----
    ready = False
    for _ in range(120):
        try:
            urllib.request.urlopen(base + "/health", timeout=1)
            ready = True
            break
        except Exception:
            time.sleep(0.25)
    chk("uvicorn 启动就绪", ready)
    if not ready:
        raise SystemExit(1)

    # ---- HTTP 层 ----
    with httpx.Client(base_url=base, timeout=20) as c:
        for path, want in [("/", 200), ("/wipo/", 200), ("/credit/", 200),
                           ("/credit/jobs", 200), ("/credit/submit", 200),
                           ("/health", 200), ("/api/health", 200),
                           ("/api/trademarks", 200), ("/api/jobs", 200),
                           ("/api/schema", 200)]:
            r = c.get(path)
            chk(f"GET {path} -> {want}", r.status_code == want, r.status_code)
        r = c.get("/wipo", follow_redirects=False)
        chk("GET /wipo -> 307", r.status_code == 307, r.status_code)
        r = c.get("/credit", follow_redirects=False)
        chk("GET /credit -> 307", r.status_code == 307, r.status_code)
        r = c.get("/api/nonexistent")
        chk("GET /api/nonexistent -> 404 JSON", r.status_code == 404
            and r.headers.get("content-type", "").startswith("application/json"),
            r.headers.get("content-type"))

        # 静态资源不串台
        wipo_index = c.get("/wipo/").text
        credit_index = c.get("/credit/").text
        chk("WIPO 首页引用 /wipo/assets", "/wipo/assets/" in wipo_index)
        chk("credit 首页引用 /credit/assets", "/credit/assets/" in credit_index)
        for src in ("/wipo/assets/", "/credit/assets/"):
            name = wipo_index if src.startswith("/wipo") else credit_index
            import re
            m = re.search(re.escape(src) + r'([\w.\-]+\.js)', name)
            if m:
                rr = c.get(src + m.group(1))
                chk(f"静态 {src}{m.group(1)} -> 200", rr.status_code == 200, rr.status_code)

    # ---- 浏览器层 ----
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context()
        page = ctx.new_page()
        bad: list[str] = []
        page.on("response", lambda r: bad.append(f"{r.status} {r.url}")
                if r.status >= 400 else None)

        # 1. 壳页
        page.goto(base + "/", wait_until="load")
        chk("壳页标题", "聚合查询与巡检系统" in page.title(), page.title())
        chk("壳页两个入口", page.locator("a.card").count() == 2,
            page.locator("a.card").count())

        # 2. WIPO 模块
        bad.clear()
        page.goto(base + "/wipo/", wait_until="load")
        page.wait_for_selector("#trademark-form", timeout=15000)
        chk("WIPO 前端渲染", page.locator("#trademark-form").count() == 1)
        chk("WIPO 无 4xx/5xx", not bad, bad[:3])
        page.fill("#brandName", "聚合验收商标")
        page.fill("#applicant", "验收有限公司")
        page.click("#btn-submit")
        page.wait_for_selector(".toast.success", timeout=10000)
        chk("WIPO 新增商标 toast", page.locator(".toast.success").count() >= 1)
        page.click('.nav-item[data-page="list"]')
        page.wait_for_function(
            "() => document.querySelectorAll('.page.active').length === 1")
        chk("WIPO 切到清单页", page.locator("#page-list.active").count() == 1)
        page.wait_for_function(
            "() => document.querySelectorAll('#trademark-table-body tr').length === 1")
        chk("WIPO 表格 1 行", page.locator("#trademark-table-body tr").count() == 1)
        chk("WIPO 计数=1", page.locator("#total-count").inner_text().strip() == "1",
            page.locator("#total-count").inner_text())

        # 深链刷新（WIPO 只停在 /wipo/，但 /wipo/ 本身要能刷）
        bad.clear()
        page.goto(base + "/wipo/", wait_until="load")
        page.wait_for_selector("#trademark-form", timeout=15000)
        chk("WIPO 刷新后无 4xx/5xx", not bad, bad[:3])

        # 3. credit 模块
        bad.clear()
        page.goto(base + "/credit/", wait_until="load")
        page.wait_for_function(
            "() => document.querySelectorAll('#app > *').length >= 1", timeout=15000)
        chk("credit 前端渲染", page.locator("#app > *").count() >= 1,
            page.locator("#app > *").count())
        chk("credit 无 4xx/5xx", not bad, bad[:3])
        chk("credit 标题", "营业执照" in page.title(), page.title())

        # credit 深链（这是 base 最容易出错的地方）
        bad.clear()
        page.goto(base + "/credit/jobs", wait_until="load")
        page.wait_for_function(
            "() => document.querySelectorAll('#app > *').length >= 1", timeout=15000)
        chk("credit /credit/jobs 深链渲染", page.locator("#app > *").count() >= 1,
            page.locator("#app > *").count())
        chk("credit /credit/jobs 无 4xx/5xx", not bad, bad[:3])

        # 提交作业走真实流程
        bad.clear()
        page.goto(base + "/credit/submit", wait_until="load")
        page.wait_for_selector("textarea", timeout=15000)
        page.fill("textarea", "阿里巴巴(中国)有限公司")
        page.locator('button:has-text("提交")').first.click()
        # 作业会失败（这台 compat_venv 里没有 camoufox 的浏览器），
        # 但只要跳转到详情页就说明前端链路通
        try:
            page.wait_for_url("**/credit/jobs/**", timeout=20000)
            chk("credit 提交后跳到详情页", "/credit/jobs/" in page.url, page.url)
            detail_url = page.url
            # 详情页深链刷新（SPA 兜底最易翻车的地方）
            bad.clear()
            page.goto(detail_url, wait_until="load")
            page.wait_for_function(
                "() => document.querySelectorAll('#app > *').length >= 1",
                timeout=15000)
            chk("credit 详情页深链刷新渲染",
                page.locator("#app > *").count() >= 1,
                page.locator("#app > *").count())
            chk("credit 详情页刷新无 4xx/5xx", not bad, bad[:3])
        except Exception as exc:  # noqa: BLE001
            chk("credit 提交后跳到详情页", False, f"{exc} | {page.url}")

        # 4. 跨模块切换（同一 tab）
        page.goto(base + "/", wait_until="load")
        page.click('a.card[href="/wipo/"]')
        page.wait_for_selector("#trademark-form", timeout=15000)
        chk("壳页 -> WIPO 跳转", "/wipo/" in page.url, page.url)
        page.goto(base + "/", wait_until="load")
        page.click('a.card[href="/credit/"]')
        page.wait_for_function(
            "() => document.querySelectorAll('#app > *').length >= 1", timeout=15000)
        chk("壳页 -> credit 跳转", "/credit/" in page.url, page.url)

        browser.close()

finally:
    proc.terminate()
    try:
        out = proc.communicate(timeout=15)[0] or ""
    except subprocess.TimeoutExpired:
        proc.kill()
        out = ""
    if failed:
        print("\n--- server log (tail) ---")
        print("\n".join(out.splitlines()[-30:]))

print(f"\n=== 结果: {passed} PASS / {failed} FAIL ===")
sys.exit(1 if failed else 0)
