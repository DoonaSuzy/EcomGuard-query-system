# -*- coding: utf-8 -*-
"""验证码识别率基准：取 N 张验证码，逐张识别并提交，统计首次成功率与平均次数。"""
import statistics
import time

import ddddocr
from camoufox.sync_api import Camoufox

PAGE = ("https://www.creditchina.gov.cn/xinyongxinxi/index.html"
        "?index=0&scenes=defaultScenario&tableName=credit_xyzx_tyshxydm"
        "&searchState=2&entityType=1,2,4,5,6,7,8&keyword=%E9%98%BF%E9%87%8C%E5%B7%B4%E5%B7%B4")
N = 20
vr = []


def on_resp(r):
    try:
        if "checkVerify" in r.url:
            vr.append(r.json())
    except Exception:
        pass


def ready(page, t=15000):
    try:
        page.wait_for_function(
            "() => { const i=document.querySelector('#vcodeimg');"
            " return !!(i && i.complete && i.naturalWidth>0); }", timeout=t)
        return True
    except Exception:
        return False


def refresh(page):
    page.evaluate("() => { const i=document.querySelector('#vcodeimg');"
                  " if(i) i.src='https://public.creditchina.gov.cn/private-api/"
                  "verify/getVerify?_v='+Math.random(); }")
    page.wait_for_timeout(700)


with Camoufox(headless=True, locale="zh-CN", os="windows") as browser:
    ctx = browser.new_context(no_viewport=True)
    page = ctx.new_page()
    page.on("response", on_resp)
    ocr = ddddocr.DdddOcr(show_ad=False)
    page.goto(PAGE, wait_until="load", timeout=60000)
    page.wait_for_timeout(4000)
    page.locator("#vcodeimg").wait_for(state="visible", timeout=20000)

    first_ok = 0
    attempts = []
    empty = 0
    t0 = time.time()
    for n in range(1, N + 1):
        if not ready(page):
            refresh(page)
            continue
        png = page.locator("#vcodeimg").screenshot()
        code = "".join(c for c in ocr.classification(png) if c.isalnum())
        if len(code) != 4:
            empty += 1
            print(f"  #{n}: 无效识别 {code!r}")
            refresh(page)
            continue
        vr.clear()
        page.locator(".vcodepop input").first.fill(code)
        page.locator(".vcodepop .confirm").first.click()
        page.wait_for_timeout(2400)
        r = vr[-1] if vr else {}
        ok = isinstance(r, dict) and r.get("code") == 0
        if ok:
            first_ok += 1
            attempts.append(1)
        print(f"  #{n}: {code!r} -> {'OK' if ok else (r or {}).get('msg')}")
        # 成功后服务端会发 vcode cookie，后续请求不再弹窗；
        # 为了继续取图，直接换图并继续提交（服务端仍会校验新图）
        refresh(page)
        if ok:
            # 成功会关闭弹层，重新打开以便继续
            page.evaluate(
                "() => { if (typeof updateVocdeFun === 'function') updateVocdeFun();"
                " const p = document.querySelector('.vcodepop');"
                " if (p) p.style.display = 'block'; }"
            )
            page.wait_for_timeout(500)

    print("\n" + "=" * 60)
    print(f"样本 {N} 张，无效识别 {empty}，成功 {first_ok}")
    print(f"识别提交成功率 {first_ok}/{N - empty} = "
          f"{(first_ok / max(1, N - empty)) * 100:.1f}%")
    print(f"总耗时 {time.time() - t0:.1f}s")
