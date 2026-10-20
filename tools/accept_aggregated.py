"""聚合系统真机验收：起 uvicorn → httpx 打接口 → Playwright 打统一前端。

覆盖：统一前端壳（左侧系统列表 + 顶栏 + 单一 #app）、两套业务页面切换、
UI 统一性（颜色/圆角/侧栏）、真实交互（新增商标 / 提交作业）、深链与旧地址
重定向、静态资源不串台、全程无 4xx/5xx。

**统一前端口径**：整个应用只有一套 SPA，挂在 ``/``；两个业务系统的页面是
它的子页面，路由为

    WIPO       /input /list /check /reports
    营业执照   /overview /submit /schedules /jobs

旧的 ``/wipo/`` ``/credit/`` 子路径已降级为 307 重定向（模块独立运行时仍用它们）。

默认把两个模块的库都指到临时 SQLite（隔离、可重复）。
若外部已设 ``CREDIT_DB``，则**沿用外部值**（用于验证 MySQL 后端）——
脚本不再覆盖它，`CREDIT_OUT` 同理。这条规则是为了让同一份验收能跑两种后端，
而不是「看起来设了 MySQL 其实还在跑 SQLite」。
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import httpx
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 服务端解释器：优先 AGG_ACCEPT_PY，缺省用当前解释器（同一 venv 即可——
# 本脚本只 import httpx / playwright，服务端只 import fastapi 侧依赖，
# 两者在同一份 requirements.txt 里）。Windows 下自动补 Scripts/python.exe。
_default_py = sys.executable
if os.name == "nt" and os.path.basename(_default_py).lower() not in (
        "python.exe", "pythonw.exe"):
    _default_py = os.path.join(os.path.dirname(_default_py), "Scripts", "python.exe")
PY = os.environ.get("AGG_ACCEPT_PY") or _default_py

tmp = tempfile.mkdtemp(prefix="agg_accept_")
env = dict(os.environ)
# 外部显式给了就用外部的（验证 MySQL 后端时必须这样），否则用临时 SQLite。
credit_db = env.get("CREDIT_DB") or (tmp + r"\jobs.db")
credit_out = env.get("CREDIT_OUT") or (tmp + r"\results")
env.update({
    "PYTHONPATH": ROOT,
    "PYTHONIOENCODING": "utf-8",
    "WIPO_DATABASE__URL": "sqlite+aiosqlite:///" + (tmp + "/agg.db").replace("\\", "/"),
    "WIPO_REPORTS_DIR": tmp + r"\reports",
    "CREDIT_DB": credit_db,
    "CREDIT_OUT": credit_out,
    "HTTPS_PROXY": "", "HTTP_PROXY": "",
})
print(f"[accept] CREDIT_DB = {credit_db}")

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


def chk(name: str, cond: bool, extra: object = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name}  {extra}")


try:
    # ---- 等端口就绪 ----
    # 探 /api/health（credit 的探针，只证明进程活着），**不能**探 /health：
    # 后者会真去请求外部 WIPO API，外网抖动时返回 503，会让「服务没起来」
    # 和「外网不通」混成同一个失败，把本地验收卡在第一步。
    ready = False
    for _ in range(120):
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1)
            ready = True
            break
        except OSError:
            # 服务未就绪时 urlopen 抛 URLError（连接拒绝）或 TimeoutError，均属 OSError 子类
            time.sleep(0.25)
    chk("uvicorn 启动就绪", ready)
    if not ready:
        raise SystemExit(1)

    # ================= HTTP 层 =================
    print("\n[HTTP 路由]")
    with httpx.Client(base_url=base, timeout=20) as c:
        # 统一前端的所有深链都必须能直接打开（刷新不能 404）
        for path in ("/", "/input", "/list", "/check", "/reports",
                     "/overview", "/submit", "/schedules", "/jobs",
                     "/api/health", "/api/trademarks",
                     "/api/jobs", "/api/schedules", "/api/schema",
                     "/docs", "/openapi.json"):
            r = c.get(path)
            chk(f"GET {path} -> 200", r.status_code == 200, r.status_code)

        # /health 是 WIPO 的三态探针：database 必须 up（本地），wipo_api 是**外部**依赖。
        # 外网不通时它返回 503 并标 wipo_api=down —— 这不是本地回归，明确区分开，
        # 否则每次断网都会把整个验收判红。
        r = c.get("/health")
        deps = {}
        if r.headers.get("content-type", "").startswith("application/json"):
            deps = r.json().get("dependencies", {})
        db_ok = deps.get("database") == "up"
        if r.status_code == 200:
            chk("GET /health -> 200（database/wipo_api 均 up）", db_ok, deps)
        else:
            chk(f"GET /health -> {r.status_code}，但 database 仍 up"
                f"（wipo_api={deps.get('wipo_api')}，外部依赖，容忍）",
                db_ok, deps)

        # 旧子路径降级为 307，且要跳到正确的统一路由
        for old, new in (("/wipo", "/input"), ("/wipo/", "/input"),
                         ("/credit", "/overview"), ("/credit/", "/overview")):
            r = c.get(old, follow_redirects=False)
            loc = r.headers.get("location", "")
            chk(f"GET {old} -> 307 {new}",
                r.status_code == 307 and loc.endswith(new),
                f"{r.status_code} {loc}")

        # 接口 404 必须是 JSON，不能被 SPA 兜底吞成 index.html
        r = c.get("/api/nonexistent")
        chk("GET /api/nonexistent -> 404 JSON", r.status_code == 404
            and r.headers.get("content-type", "").startswith("application/json"),
            r.headers.get("content-type"))

        # 静态资源缺失必须是 404，**不能**回 index.html —— 否则浏览器会拿 HTML
        # 当 JS 解析，报「Unexpected token '<'」，把问题藏成难查的语法错误。
        r = c.get("/assets/nonexistent.js")
        chk("GET /assets/nonexistent.js -> 404",
            r.status_code == 404, r.status_code)
        chk("缺失资源不回落 index.html",
            "<div id=\"app\">" not in r.text[:200], r.text[:80])

        # 首页引用的资源必须是绝对路径 /assets/（相对路径在深链下会解析错）
        index = c.get("/input").text
        chk("首页引用绝对路径 /assets/", 'src="/assets/' in index,
            index[:200])
        m = re.search(r'src="(/assets/[\w.\-]+\.js)"', index)
        if m:
            rr = c.get(m.group(1))
            chk(f"静态 {m.group(1)} -> 200", rr.status_code == 200, rr.status_code)
        m = re.search(r'href="(/assets/[\w.\-]+\.css)"', index)
        if m:
            rr = c.get(m.group(1))
            chk(f"静态 {m.group(1)} -> 200", rr.status_code == 200, rr.status_code)

        # 接口契约：统一后仍是 21 条 path / 7 个 tag（不多不少）
        spec = c.get("/openapi.json").json()
        chk("openapi 21 条 path", len(spec["paths"]) == 21, len(spec["paths"]))
        tag_names = sorted({t["name"] for t in spec.get("tags", [])})
        chk("openapi 7 个 tag 分组", len(tag_names) == 7, tag_names)
        chk("openapi 无 default 分组", "default" not in tag_names, tag_names)

    # ================= 浏览器层 =================
    console_errors: list[str] = []
    bad_responses: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.on("console",
                lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("response",
                lambda r: bad_responses.append(f"{r.status} {r.url}")
                if r.status >= 400 else None)

        # ---------- 1. 首屏：同一套框架 ----------
        print("\n[首屏 / 统一框架]")
        page.goto(base + "/", wait_until="networkidle")
        chk("默认重定向到 /input", page.url.endswith("/input"), page.url)
        chk("页面标题含统一监控平台", "统一监控平台" in page.title(), page.title())

        sys_items = page.locator(".sys-item")
        chk("左侧系统列表有 2 项", sys_items.count() == 2, sys_items.count())
        labels = [sys_items.nth(i).locator(".sys-name").inner_text()
                  for i in range(sys_items.count())]
        chk("系统名为 WIPO / 营业执照", labels == ["WIPO", "营业执照"], labels)

        # 侧栏几何是「切换不跳变」的量化基准
        sidebar_box = page.locator(".sidebar").bounding_box()
        chk("侧栏宽度 240px",
            sidebar_box is not None and abs(sidebar_box["width"] - 240) < 1,
            sidebar_box)

        chk("WIPO 功能项 4 个", page.locator(".nav-item").count() == 4,
            page.locator(".nav-item").count())
        chk("WIPO 首页可见（商标录入）", page.locator("#page-input").is_visible())
        chk("顶栏显示商标总数", page.locator("#total-count").count() == 1)

        # 整页刷新的探针：切换系统时若真的 reload，这个变量会消失
        page.evaluate("window.__spa_marker = 'alive'")

        # ---------- 2. WIPO 真实交互 ----------
        print("\n[WIPO 交互]")
        page.fill("#brandName", "聚合验收商标")
        page.fill("#applicant", "验收有限公司")
        page.click("#btn-submit")
        page.wait_for_selector(".toast.success", timeout=10000)
        chk("WIPO 新增商标 toast", page.locator(".toast.success").count() >= 1)

        page.click('.nav-item[data-page="list"]')
        page.wait_for_url("**/list", timeout=10000)
        # 作用域限定 .wipo-host：credit 也用了 .page 类（reconcile.css 里有注释），
        # 不加前缀会把营业执照的 section 一起数进来。
        page.wait_for_function(
            "() => document.querySelectorAll('.wipo-host .page.active').length === 1")
        chk("WIPO 切到清单页", page.locator(".wipo-host #page-list.active").count() == 1)
        page.wait_for_function(
            "() => document.querySelectorAll('#trademark-table-body tr').length === 1")
        chk("WIPO 表格 1 行", page.locator("#trademark-table-body tr").count() == 1)
        chk("WIPO 计数=1",
            page.locator("#total-count").inner_text().strip() == "1",
            page.locator("#total-count").inner_text())

        # ---------- 3. 切到营业执照：同一页面、无跳变 ----------
        print("\n[切换到营业执照]")
        page.click('.sys-item[data-system="credit"]')
        page.wait_for_url("**/overview", timeout=10000)
        page.wait_for_timeout(600)

        chk("URL 变为 /overview", page.url.endswith("/overview"), page.url)
        chk("无整页刷新（SPA 标记存活）",
            page.evaluate("window.__spa_marker") == "alive")
        chk("WIPO 内容已隐藏", not page.locator("#page-input").is_visible())
        chk("营业执照内容可见", page.locator(".sys-credit").is_visible())

        sb2 = page.locator(".sidebar").bounding_box()
        chk("侧栏宽度未变",
            sb2 is not None and abs(sb2["width"] - sidebar_box["width"]) < 1,
            f"{sidebar_box} -> {sb2}")
        chk("侧栏位置未变",
            sb2 is not None and abs(sb2["x"] - sidebar_box["x"]) < 1, sb2)

        credit_nav = [page.locator(".nav-item").nth(i).inner_text().strip()
                      for i in range(page.locator(".nav-item").count())]
        chk("功能项已换为营业执照的 4 个",
            credit_nav == ["总览", "提交巡检", "定时巡检", "作业列表"], credit_nav)

        # ---------- 4. UI 是否真的统一成 WIPO 样式 ----------
        print("\n[UI 统一性]")
        # WIPO 主色 #4f46e5 = rgb(79,70,229)；credit 原主色 #2f6df6 = rgb(47,109,246)
        btn_bg = page.locator(".header-actions .btn.primary").first.evaluate(
            "el => getComputedStyle(el).backgroundColor")
        chk("主按钮用 WIPO 靛蓝 #4f46e5", btn_bg == "rgb(79, 70, 229)", btn_bg)

        card_radius = page.evaluate(
            """() => {
                const el = document.querySelector('.sys-credit .card') ||
                           document.querySelector('.sys-credit .stat');
                return el ? getComputedStyle(el).borderRadius : 'no-element';
            }"""
        )
        chk("卡片圆角为 WIPO 的 12px", str(card_radius).startswith("12px"), card_radius)

        side_bg = page.locator(".sidebar").evaluate(
            "el => getComputedStyle(el).backgroundImage")
        chk("侧栏为 WIPO 深靛蓝渐变",
            "gradient" in side_bg and "30, 27, 75" in side_bg, side_bg[:80])
        chk("当前高亮系统为 credit",
            page.locator(".sys-item.active").get_attribute("data-system") == "credit",
            page.locator(".sys-item.active").get_attribute("data-system"))

        # ---------- 5. 营业执照各功能页 ----------
        print("\n[营业执照各页]")
        for page_name, title_kw in (("overview", "总览"), ("submit", "提交巡检"),
                                    ("schedules", "定时巡检"), ("jobs", "作业列表")):
            page.click(f'.nav-item[data-page="{page_name}"]')
            page.wait_for_url(f"**/{page_name}", timeout=10000)
            page.wait_for_timeout(300)
            h1 = page.locator("#page-title").inner_text()
            chk(f"/{page_name} 标题为「{title_kw}」", title_kw in h1, h1)

        # 回归守卫：弹窗开关曾用 editing 的哨兵值兼任（初始 null → 一进页面就弹），
        # 表现为整页被 .modal-mask 盖住、点不动「新建」。
        page.click('.nav-item[data-page="schedules"]')
        page.wait_for_url("**/schedules", timeout=10000)
        page.wait_for_timeout(400)
        chk("定时巡检页初始不弹配置窗", page.locator(".modal-mask").count() == 0,
            page.locator(".modal-mask").count())
        page.locator('button:has-text("新建定时巡检")').first.click()
        page.locator('[aria-label="定时巡检配置"]').wait_for(timeout=5000)
        body = page.locator('[aria-label="定时巡检配置"]').inner_text()
        chk("配置窗有每日/每周/每月",
            all(w in body for w in ("每日", "每周", "每月")), body[:80])
        page.locator('[aria-label="定时巡检配置"] button[aria-label="关闭"]').click()

        # ---------- 6. 提交作业走真实链路 ----------
        print("\n[提交作业]")
        bad_responses.clear()
        page.goto(base + "/submit", wait_until="load")
        # 必须限定 .sys-credit：WIPO 的录入页里也有一个 textarea（#notes），
        # 它被 v-show 隐藏但仍留在 DOM 里，裸 "textarea" 会匹配到那个不可见的，
        # wait_for_selector 等的是 visible，于是超时（实测踩过）。
        page.wait_for_selector(".sys-credit textarea", timeout=15000)
        page.fill(".sys-credit textarea", "阿里巴巴(中国)有限公司")
        page.locator('.sys-credit button:has-text("提交")').first.click()
        # 作业本身会失败（这台机器上未必有 camoufox 浏览器），
        # 但只要跳到详情页就说明「按钮 → 接口 → 路由」链路是通的
        try:
            page.wait_for_url("**/jobs/**", timeout=20000)
            chk("提交后跳到详情页", "/jobs/" in page.url, page.url)
            detail_url = page.url
            page.goto(detail_url, wait_until="load")
            page.wait_for_function(
                "() => document.querySelectorAll('#page-title').length >= 1",
                timeout=15000)
            chk("详情页深链刷新可渲染",
                page.locator("#page-title").count() >= 1,
                page.locator("#app > *").count())
        except Exception as exc:  # noqa: BLE001
            chk("提交后跳到详情页", False, f"{exc} | {page.url}")
        chk("提交链路无 4xx/5xx", not bad_responses, bad_responses[:3])

        # ---------- 7. 切回 WIPO ----------
        print("\n[切回 WIPO]")
        # 上面「提交作业」那一段做过 page.goto（整页导航），探针变量已被清掉，
        # 所以这里必须重新打一次标记，否则这一项会假失败（实测踩过）。
        page.evaluate("window.__spa_marker = 'alive'")
        page.click('.sys-item[data-system="wipo"]')
        page.wait_for_url("**/input", timeout=10000)
        page.wait_for_timeout(400)
        chk("切回后 WIPO 内容可见", page.locator("#page-input").is_visible())
        chk("切回后营业执照已隐藏", not page.locator(".sys-credit").is_visible())
        chk("切回后仍是同一页面（SPA 标记存活）",
            page.evaluate("window.__spa_marker") == "alive")

        for page_name in ("input", "list", "check", "reports"):
            page.click(f'.nav-item[data-page="{page_name}"]')
            page.wait_for_url(f"**/{page_name}", timeout=10000)
            page.wait_for_timeout(250)
            chk(f"/{page_name} 对应 section 可见",
                page.locator(f"#page-{page_name}").is_visible())

        # ---------- 8. 深链与旧地址 ----------
        print("\n[深链与旧地址]")
        bad_responses.clear()
        page.goto(base + "/jobs", wait_until="networkidle")
        chk("深链 /jobs 直接可开",
            page.locator("#page-title").inner_text().strip() == "作业列表",
            page.locator("#page-title").inner_text())
        chk("/jobs 深链无 4xx/5xx", not bad_responses, bad_responses[:3])

        page.goto(base + "/wipo", wait_until="networkidle")
        chk("旧地址 /wipo 跳到 /input", page.url.endswith("/input"), page.url)
        page.goto(base + "/credit", wait_until="networkidle")
        chk("旧地址 /credit 跳到 /overview", page.url.endswith("/overview"), page.url)

        # ---------- 9. 运行时健康 ----------
        print("\n[运行时健康]")
        chk("无页面 JS 报错", not console_errors, console_errors[:3])
        chk("无 5xx 响应", not bad_responses, bad_responses[:3])

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
