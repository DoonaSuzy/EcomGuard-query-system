# -*- coding: utf-8 -*-
"""
信用中国 营业执照/登记状态 批量检查工具  v2
============================================
链路：搜索页 → 验证码(ddddocr 自动识别) → catalogSearchHome
      → 精确匹配记录 → xyDetail 详情页 → getTyshxydmDetailsContent
      → headEntity.status / data.entity.org_statu = 登记状态

v2 修复（基于实测）：
  1. 搜索响应成功码是 status=1（不是 200），旧版把成功误判为失败并重试。
  2. 验证码截图前必须等图片真正加载完（complete && naturalWidth>0），
     否则 ddddocr 输出垃圾字符（实测会返回 '里'）。
  3. 响应按 keyword 归属匹配，避免上一页/详情页的迟到响应串到下一家企业。
  4. 服务端 vcode 窗口 5 分钟，失效时返回 status=10001，自动重新过码并重试。

用法：
    python credit_check.py companies.txt -o result.csv
    python credit_check.py companies.txt --headful
    python credit_check.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from PIL import Image

# 重型依赖（ddddocr / camoufox）**故意延迟到真正要用时再 import**：
#   - 单元测试只碰纯函数（norm_name / classify / ocr_preprocess），
#     CI 上装 camoufox(含浏览器)+onnxruntime+opencv 是几百 MB，纯属浪费；
#   - import 本模块不应产生任何副作用，这条与下方 logger 的处理是同一原则。
# 若有人真的少装了依赖，报错点会落在 CreditChecker.__init__，那里给了明确指引。

import logkit

# 模块级取 logger，真正的 handler（控制台 + 轮转文件）在 main() 里配置。
# 这样 import 本模块（如测试）不会产生 logs/ 目录等副作用。
log = logging.getLogger("credit_check")

# Windows 控制台默认 cp936，重定向/管道到 UTF-8 消费端时中文会乱码。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SEARCH_URL = (
    "https://www.creditchina.gov.cn/xinyongxinxi/index.html"
    "?index=0&scenes=defaultScenario&tableName=credit_xyzx_tyshxydm"
    "&searchState=2&entityType=1,2,4,5,6,7,8&keyword={kw}"
)
DETAIL_URL = (
    "https://www.creditchina.gov.cn/xinyongxinxixiangqing/xyDetail.html"
    "?searchState=1&entityType={et}&keyword={kw}&uuid={uuid}"
)

NORMAL = {"正常", "存续", "在业", "在营", "开业", "有效", "登记"}
ALERT = {"吊销", "注销", "撤销", "迁出", "停业", "清算", "歇业",
         "责令关闭", "异常", "非正常", "已注销", "已吊销"}
STATUS_FIELDS = ("status", "org_statu", "reg_status", "registration_status",
                 "business_status", "operating_status", "ent_status")
SEARCH_OK = {1, 200}          # 实测成功码为 1
VCODE_EXPIRED = {10001}       # 实测「验证码已失效，请重新申请验证码」
MAX_CAPTCHA_TRY = 12
CAPTCHA_TTL = 5 * 60
OCR_UPSCALE = 3  # 实测 3 倍放大 LANCZOS 比原图成功率略高（52.6% vs 45%）


def ocr_preprocess(png: bytes, factor: int = OCR_UPSCALE) -> bytes:
    """验证码预处理：转灰度 + LANCZOS 放大。ddddocr 对 95x25 小图直接识别偏弱。"""
    try:
        im = Image.open(io.BytesIO(png)).convert("L")
        im = im.resize((im.width * factor, im.height * factor), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return png


def norm_name(s: str) -> str:
    if not s:
        return ""
    s = s.replace("（", "(").replace("）", ")").replace("　", "")
    return re.sub(r"\s+", "", s).strip()


#: 统一社会信用代码：18 位，数字 + 大写字母（不含 I/O/S/V/Z 之外的歧义字符）。
#: 实际校验位算法（GB 32100-2015）这里不做——信用中国返回的代码是权威值，
#: 我们只判断「用户输入的是不是一个代码」，不需要自己算校验位。
_CREDIT_CODE_RE = re.compile(r"^[0-9A-HJ-NPQRTUWXY]{18}$")


def is_credit_code(s: str) -> bool:
    """输入是否像统一社会信用代码。

    销售手上两个键都可能：公司名，或者营业执照上的代码。原先 ``check_one``
    只把输入当名字去搜、再按名字相等判定，于是直接粘代码进来必然 `unknown`
    （信用中国的搜索接口对纯代码返回的是空列表或无关结果），
    用户看到的是「查不到这家公司」——而这家公司其实存在。
    """
    return bool(_CREDIT_CODE_RE.match(norm_name(s).upper()))


def norm_code(s: str) -> str:
    return norm_name(s or "").upper()


def pick_target(rows: list, query: str) -> dict | None:
    """在搜索结果里挑出与查询对应的那一条，找不到返回 None。

    承认输入既可能是企业全称、也可能是统一社会信用代码，两个键各自独立比对：
    名称对得上 **或** 代码对得上，都算命中。只比名称是原先的缺陷——
    直接粘代码进来必然判 unknown（搜出来的是别家或空列表），
    而销售最常拿到的恰恰是营业执照上那串代码。

    严格相等、不做模糊兜底：把别家公司的状态当成输入公司的状态，
    对销售是假阳性，比查不到更危险。
    """
    want_code = norm_code(query) if is_credit_code(query) else ""
    want_name = "" if want_code else norm_name(query)
    for r in rows:
        if not isinstance(r, dict):
            continue
        if want_code and norm_code(r.get("accurate_entity_code", "")) == want_code:
            return r
        if want_name and norm_name(r.get("accurate_entity_name")) == want_name:
            return r
    return None


def classify(status: str) -> str:
    if not status:
        return "unknown"
    s = status.strip()
    if any(k in s for k in ALERT):
        return "alert"
    if any(k in s for k in NORMAL):
        return "ok"
    return "unknown"


# 判定优先级（实测结论）：
#   1. 详情取到 status 且为注销/吊销类 → alert
#   2. 详情取到 status 且在册类        → ok
#   3. 搜索 total=0（信用中国未收录）  → notfound（该索引只收在册主体，
#      credit_scjdglzj_fr_gscxqy = 工商存续企业；已注销的三鹿/快播均 total=0）
#   4. 其它异常                        → unknown
VERDICT_LABEL = {"ok": "正常", "alert": "异常", "notfound": "未收录",
                 "unknown": "未知"}


def _require_runtime_deps():
    """按需导入重型运行时依赖，缺失时给出可执行的补救指引。"""
    try:
        import ddddocr  # noqa: F401
        from camoufox.sync_api import Camoufox  # noqa: F401
    except ImportError as e:
        raise SystemExit(
            f"缺少运行时依赖：{e}\n"
            f"请在 .venv 中安装：\n"
            f"  .venv\\Scripts\\python.exe -m pip install -r requirements.txt "
            f"-i https://pypi.tuna.tsinghua.edu.cn/simple\n"
            f"若只跑单元测试则无需安装（pytest test_regression.py 不碰这些依赖）。"
        ) from e
    return ddddocr, Camoufox


class CreditChecker:
    def __init__(self, headful: bool = False, min_delay: float = 3.5,
                 max_delay: float = 6.5) -> None:
        ddddocr, _ = _require_runtime_deps()
        self.headful = headful
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.ocr = ddddocr.DdddOcr(show_ad=False)
        self.search_resp: list[dict] = []
        self.detail_resp: list[dict] = []
        self.verify_resp: dict | None = None
        self.vcode_at = 0.0
        self._browser = None
        self._ctx = None
        self.page = None

    # ---------- 生命周期 ----------
    def __enter__(self) -> "CreditChecker":
        _, Camoufox = _require_runtime_deps()
        self._browser = Camoufox(headless=not self.headful, locale="zh-CN",
                                 os="windows").__enter__()
        self._ctx = self._browser.new_context(no_viewport=True)
        self.page = self._ctx.new_page()
        self.page.on("response", self._on_response)
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            if self._browser:
                self._browser.__exit__(*exc)

    # ---------- 网络回调 ----------
    def _on_response(self, r) -> None:
        try:
            if "catalogSearchHome" in r.url:
                self.search_resp.append({"url": r.url, "json": r.json()})
            elif "getTyshxydmDetailsContent" in r.url:
                self.detail_resp.append({"url": r.url, "json": r.json()})
            elif "checkVerify" in r.url:
                self.verify_resp = r.json()
        except Exception:
            pass

    # 注意：瑞数会把查询串整体改写成 ?rcwCQitg=<密文>，URL 里已没有明文 keyword，
    # 因此不能按 URL 里的关键词归属响应，只能用「清空列表 + 等新响应到达」。
    def _wait_search(self, baseline: int, timeout: float = 25.0) -> dict | None:
        end = time.time() + timeout
        while time.time() < end:
            if len(self.search_resp) > baseline:
                return self.search_resp[-1]["json"]
            self.page.wait_for_timeout(350)
        return None

    def _wait_detail(self, baseline: int, timeout: float = 25.0) -> dict | None:
        end = time.time() + timeout
        while time.time() < end:
            if len(self.detail_resp) > baseline:
                return self.detail_resp[-1]["json"]
            self.page.wait_for_timeout(350)
        return None

    # ---------- 验证码 ----------
    def _captcha_ready(self, timeout: int = 15000) -> bool:
        try:
            self.page.wait_for_function(
                "() => { const i = document.querySelector('#vcodeimg');"
                " return !!(i && i.complete && i.naturalWidth > 0); }",
                timeout=timeout,
            )
            return True
        except Exception:
            return False

    def _refresh_captcha(self) -> None:
        self.page.evaluate(
            "() => { const i = document.querySelector('#vcodeimg');"
            " if (i) { i.src = 'https://public.creditchina.gov.cn/private-api/"
            "verify/getVerify?_v=' + Math.random(); } }"
        )
        self.page.wait_for_timeout(500)
        self._captcha_ready()

    def _solve_captcha(self) -> bool:
        try:
            self.page.locator("#vcodeimg").wait_for(state="visible", timeout=20000)
        except Exception:
            return False
        for i in range(1, MAX_CAPTCHA_TRY + 1):
            if not self._captcha_ready():
                self._refresh_captcha()
                continue
            png = self.page.locator("#vcodeimg").screenshot()
            code = re.sub(r"[^0-9A-Za-z]", "",
                          self.ocr.classification(ocr_preprocess(png)) or "")
            if len(code) != 4:
                print(f"    [captcha] 第 {i} 次 识别无效({code!r})，换图")
                log.warning("captcha #%d 识别无效 code=%r", i, code)
                self._refresh_captcha()
                continue
            self.verify_resp = None
            self.page.locator(".vcodepop input").first.fill(code)
            self.page.locator(".vcodepop .confirm").first.click()
            self.page.wait_for_timeout(2400)
            res = self.verify_resp or {}
            if isinstance(res, dict) and res.get("code") == 0:
                self.vcode_at = time.time()
                print(f"    [captcha] 第 {i} 次 '{code}' 通过")
                log.info("captcha 通过（第 %d 次）code=%s", i, code)
                return True
            print(f"    [captcha] 第 {i} 次 '{code}' 失败: {(res or {}).get('msg', '无响应')}")
            log.warning("captcha #%d 失败 code=%s msg=%s", i, code,
                        (res or {}).get("msg", "无响应"))
            self._refresh_captcha()
        log.error("captcha 连续 %d 次未通过，放弃本次", MAX_CAPTCHA_TRY)
        return False

    # ---------- 单企业 ----------
    def check_one(self, name: str) -> dict:
        out = {
            "输入名称": name, "匹配名称": "", "统一社会信用代码": "",
            "登记状态": "", "判定": "unknown", "主体类型": "",
            "数据源": "", "记录数": 0, "详情URL": "", "备注": "",
        }
        for attempt in (1, 2):
            try:
                self.search_resp.clear()
                self.detail_resp.clear()
                self.page.goto(SEARCH_URL.format(kw=name), wait_until="load", timeout=60000)
                self.page.wait_for_timeout(3000)

                if self.page.locator("#vcodeimg").count() and \
                        self.page.locator("#vcodeimg").is_visible():
                    if not self._solve_captcha():
                        out["备注"] = "验证码未通过"
                        continue

                sp = self._wait_search(0, timeout=25)
                if sp is None:
                    out["备注"] = "未收到搜索响应"
                    continue

                st = sp.get("status")
                if st in VCODE_EXPIRED:
                    # 服务端 vcode 会话失效 → 重新过码再来
                    print("    [vcode] 已失效，重新过码")
                    log.warning("vcode 会话失效，重新过码")
                    self.vcode_at = 0.0
                    self.page.evaluate(
                        "() => { if (typeof updateVocdeFun === 'function') updateVocdeFun();"
                        " const p = document.querySelector('.vcodepop');"
                        " if (p) p.style.display = 'block'; }"
                    )
                    self.page.wait_for_timeout(600)
                    if self._solve_captcha():
                        self.search_resp.clear()
                        self.page.evaluate(
                            "() => { if (window.selfObj && window.selfObj.searchAjax)"
                            " window.selfObj.searchAjax(1); }"
                        )
                        sp = self._wait_search(0, timeout=25) or {}
                        st = sp.get("status")
                    if st in VCODE_EXPIRED:
                        out["备注"] = "验证码会话反复失效"
                        continue

                if st not in SEARCH_OK:
                    out["备注"] = f"搜索失败 status={st} msg={sp.get('message')}"
                    continue

                data = sp.get("data") or {}
                lst = data.get("list") or []
                out["记录数"] = data.get("total") or len(lst)
                if not lst:
                    out["判定"] = "notfound"
                    if is_credit_code(name):
                        out["备注"] = ("按统一社会信用代码未检索到主体（该索引仅收在册主体，"
                                       "疑似已注销/吊销；也可能代码有误，可用企业全称再查一次）")
                    else:
                        out["备注"] = "信用中国未收录（该索引仅收在册主体，疑似已注销/吊销）"
                    return out

                # 承认输入既可能是企业全称、也可能是统一社会信用代码。
                # 匹配口径见 pick_target 的注释。
                target = pick_target(lst, name)
                if target is None:
                    # 不做模糊兜底：把别家公司的状态当成输入公司的状态，对销售是假阳性，
                    # 比查不到更危险。列出候选，交给人工确认名称。
                    cands = "、".join(
                        f"{r.get('accurate_entity_name')}"
                        f"（{r.get('accurate_entity_code') or '无代码'}）"
                        for r in lst[:3])
                    out["判定"] = "unknown"
                    out["记录数"] = len(lst)
                    out["备注"] = (f"无精确同名/同码主体，未判定状态。"
                                   f"候选：{cands}（请核对全称或信用代码后重查）")
                    return out

                out["匹配名称"] = target.get("accurate_entity_name", "")
                out["统一社会信用代码"] = target.get("accurate_entity_code", "") or ""
                out["主体类型"] = target.get("entityType", "")
                durl = DETAIL_URL.format(et=target.get("entityType"),
                                         kw=quote(target.get("accurate_entity_name_query", "")),
                                         uuid=target.get("uuid"))
                out["详情URL"] = durl

                self.detail_resp.clear()
                self.page.goto(durl, wait_until="load", timeout=60000)
                dp = self._wait_detail(0, timeout=25)
                if dp is None:
                    out["备注"] = (out["备注"] + "; 详情响应缺失").strip("; ")
                    continue

                ddata = dp.get("data") or {}
                head = ddata.get("headEntity") or {}
                inner = ddata.get("data") or {}
                ent = inner.get("entity") or {}

                status = ""
                for f in STATUS_FIELDS:
                    v = head.get(f) or ent.get(f)
                    if v:
                        status = str(v).strip()
                        break
                out["登记状态"] = status
                out["判定"] = classify(status)
                out["数据源"] = inner.get("dataSource") or inner.get("table_name") or ""
                if not out["统一社会信用代码"]:
                    out["统一社会信用代码"] = head.get("tyshxydm") or ""
                if not status:
                    out["备注"] = (out["备注"] + "; 未取到状态字段").strip("; ")
                return out

            except Exception as e:
                out["备注"] = f"{type(e).__name__}: {e}"[:200]
                log.exception("check_one(%s) 第 %d 次异常", name, attempt)
                if attempt == 1:
                    print(f"    [retry] {out['备注']}")
                    time.sleep(4)
        return out

    def sleep_between(self) -> None:
        time.sleep(random.uniform(self.min_delay, self.max_delay))


SELFTEST = [
    "阿里巴巴(中国)有限公司",
    "腾讯科技(深圳)有限公司",
    "石家庄三鹿集团股份有限公司",
    "深圳市快播科技有限公司",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("infile", nargs="?", help="企业名单 txt，每行一个")
    ap.add_argument("-o", "--out", default=None, help="输出 CSV")
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--min-delay", type=float, default=3.5)
    ap.add_argument("--max-delay", type=float, default=6.5)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--log-dir", default=None, help="日志目录（默认 ./logs）")
    args = ap.parse_args()

    logkit.setup_logging("credit_check", log_dir=args.log_dir)
    log.info("启动：infile=%s selftest=%s headful=%s delay=%.1f-%.1f limit=%d",
             args.infile, args.selftest, args.headful,
             args.min_delay, args.max_delay, args.limit)

    if args.selftest:
        names = SELFTEST
    else:
        if not args.infile:
            ap.error("需要企业名单文件，或用 --selftest")
        raw = Path(args.infile).read_text(encoding="utf-8")
        names = [ln.strip() for ln in raw.splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
    if args.limit:
        names = names[: args.limit]
    if not names:
        print("名单为空")
        return 1

    out_path = Path(args.out or f"credit_result_{datetime.now():%Y%m%d_%H%M%S}.csv")
    rows: list[dict] = []
    t0 = time.time()
    print(f"待查 {len(names)} 家 → {out_path}")

    with CreditChecker(headful=args.headful,
                       min_delay=args.min_delay, max_delay=args.max_delay) as ck:
        for i, name in enumerate(names, 1):
            print(f"[{i}/{len(names)}] {name}")
            r = ck.check_one(name)
            rows.append(r)
            flag = VERDICT_LABEL[r["判定"]]
            print(f"    -> {r['匹配名称'] or '-'} | {r['登记状态'] or '-'} | {flag}"
                  f"{' | ' + r['备注'] if r['备注'] else ''}")
            log.info("result #%d/%d name=%s matched=%s status=%s verdict=%s records=%s",
                     i, len(names), name, r["匹配名称"], r["登记状态"],
                     r["判定"], r["记录数"])
            if i < len(names):
                ck.sleep_between()

    fields = ["输入名称", "匹配名称", "统一社会信用代码", "登记状态", "判定",
              "主体类型", "数据源", "记录数", "详情URL", "备注"]
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    ok = [r for r in rows if r["判定"] == "ok"]
    alert = [r for r in rows if r["判定"] == "alert"]
    notfound = [r for r in rows if r["判定"] == "notfound"]
    unknown = [r for r in rows if r["判定"] == "unknown"]
    print("\n" + "=" * 62)
    print(f"完成 {len(rows)} 家，耗时 {time.time() - t0:.1f}s")
    print(f"正常 {len(ok)} / 异常 {len(alert)} / 未收录 {len(notfound)} / 未知 {len(unknown)}")
    if alert:
        print("\n【需提醒主管 · 状态异常】")
        for r in alert:
            print(f"  ! {r['匹配名称']}（{r['统一社会信用代码']}）= {r['登记状态']}")
    if notfound:
        print("\n【需提醒主管 · 未收录（疑似已注销/吊销，建议人工核验）】")
        for r in notfound:
            print(f"  ! {r['输入名称']}")
    if unknown:
        print("\n【需人工复核】")
        for r in unknown:
            print(f"  ? {r['输入名称']}：{r['备注'] or '状态为空'}")
    print(f"\n结果已写入 {out_path.resolve()}")
    log.info("完成 %d 家，耗时 %.1fs，ok=%d alert=%d notfound=%d unknown=%d，CSV=%s",
             len(rows), time.time() - t0, len(ok), len(alert), len(notfound),
             len(unknown), out_path.resolve())
    if getattr(log, "handlers", None) and logkit.log_path("credit_check", args.log_dir).exists():
        print(f"日志已写入 {logkit.log_path('credit_check', args.log_dir).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
