# -*- coding: utf-8 -*-
"""验证码 OCR 预处理 A/B：原图 vs 3 倍放大灰度，各 20 张，比较提交成功率。"""
import io
import time

import ddddocr
from camoufox.sync_api import Camoufox
from PIL import Image

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


def reopen(page):
    page.evaluate(
        "() => { if (typeof updateVocdeFun === 'function') updateVocdeFun();"
        " const p = document.querySelector('.vcodepop');"
        " if (p) p.style.display = 'block'; }"
    )
    page.wait_for_timeout(500)


def upscale(png: bytes, factor: int = 3) -> bytes:
    im = Image.open(io.BytesIO(png)).convert("L")
    im = im.resize((im.width * factor, im.height * factor), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


with Camoufox(headless=True, locale="zh-CN", os="windows") as browser:
    ctx = browser.new_context(no_viewport=True)
    page = ctx.new_page()
    page.on("response", on_resp)
    ocr = ddddocr.DdddOcr(show_ad=False)
    page.goto(PAGE, wait_until="load", timeout=60000)
    page.wait_for_timeout(4000)
    page.locator("#vcodeimg").wait_for(state="visible", timeout=20000)

    for label, fn in (("raw", lambda b: b), ("up3x", upscale)):
        ok_n = bad_n = 0
        t0 = time.time()
        for n in range(1, N + 1):
            if not ready(page):
                refresh(page)
                continue
            png = page.locator("#vcodeimg").screenshot()
            try:
                code = "".join(c for c in ocr.classification(fn(png)) if c.isalnum())
            except Exception as e:
                print(f"  [{label}] #{n}: OCR异常 {e}")
                refresh(page)
                continue
            if len(code) != 4:
                bad_n += 1
                print(f"  [{label}] #{n}: 无效 {code!r}")
                refresh(page)
                reopen(page)
                continue
            vr.clear()
            page.locator(".vcodepop input").first.fill(code)
            page.locator(".vcodepop .confirm").first.click()
            page.wait_for_timeout(2400)
            r = vr[-1] if vr else {}
            ok = isinstance(r, dict) and r.get("code") == 0
            ok_n += ok
            print(f"  [{label}] #{n}: {code!r} -> {'OK' if ok else 'FAIL'}")
            refresh(page)
            reopen(page)
        print(f"[{label}] 成功 {ok_n}/{N - bad_n} = "
              f"{ok_n / max(1, N - bad_n) * 100:.1f}%  无效识别 {bad_n}  "
              f"耗时 {time.time() - t0:.0f}s\n")
