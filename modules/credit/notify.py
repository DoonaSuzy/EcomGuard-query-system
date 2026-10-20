# -*- coding: utf-8 -*-
"""
异常提醒脚本：读取 credit_check.py 产出的 CSV，把「异常 / 未收录」推给主管。
=====================================================================
支持三种通道（可同时配多个）：
  1. 企业微信 / 钉钉 / 飞书 群机器人 webhook   —— 最省事，推荐
  2. SMTP 邮件（支持 465 SSL / 587 STARTTLS，可发 HTML 富文本）
  3. 仅打印到 stdout（默认，不配任何通道时）

凭据来源优先级：命令行参数 > 环境变量 > 同目录 .env 文件。
    .env 里的值不会覆盖已存在的环境变量（与 python-dotenv 默认行为一致），
    方便在 CI / 计划任务里用真实环境变量临时顶掉本地文件。

QQ 邮箱示例（.env）：
    SMTP_HOST=smtp.qq.com
    SMTP_PORT=465
    SMTP_SSL=1
    SMTP_USER=你的QQ号@qq.com
    SMTP_PASS=邮箱授权码          # 不是QQ密码！在 QQ邮箱→设置→账户 里开启SMTP后生成
    MAIL_TO=主管@company.com,另一个@company.com

用法：
    python notify.py result.csv                     # 只打印摘要
    python notify.py result.csv --webhook https://qyapi.weixin.qq.com/...
    python notify.py result.csv --smtp-ssl --smtp-host smtp.qq.com --smtp-port 465 \
        --smtp-user you@qq.com --smtp-pass 授权码 --mail-to boss@example.com
    python notify.py result.csv --always            # 无异常也发（周报用）

退出码：0=有异常且已发送 / 10=无需推送（无异常，或未配置任何通道） / 1=发送失败
"""
from __future__ import annotations

import argparse
import csv
import html as _html
import json
import logging
import os
import re
import smtplib
import sys
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import logkit

# Windows 控制台默认用 cp936 编码输出，管道/重定向到 UTF-8 消费端时中文会乱码。
# 统一把 stdout/stderr 切到 UTF-8，保证任何调用方式下输出都可读。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 需要人看一眼的判定。注意 unknown 也在这里——「查不出来」绝不能等同于
# 「没问题」：50 条样例实跑中有 12 条因名称不精确落到 unknown，若不计入
# 提醒，一批写错的名字会得到「本期无异常」的静默通过，这是最危险的失败模式。
ALERT_VERDICTS = {"alert", "notfound", "unknown"}
#: 其中「确定异常」的子集，报告里单独成表、标题里单独计数
HARD_VERDICTS = {"alert", "notfound"}
VERDICT_CN = {"ok": "正常", "alert": "状态异常", "notfound": "未收录",
              "unknown": "待复核"}

# 判定 → HTML 里的强调色，让主管一眼看到红黄
_VERDICT_COLOR = {"alert": "#cf222e", "notfound": "#9a6700", "unknown": "#6e7781"}

log = logging.getLogger("notify")


# ---------------------------------------------------------------- .env 加载
def load_dotenv(path: Path | None = None, override: bool = False) -> dict[str, str]:
    """极简 .env 解析，不引入 python-dotenv 依赖。

    支持：KEY=VALUE、# 注释、空行、值两侧的单/双引号、行尾 \\r。
    默认不覆盖已存在的环境变量（override=True 才覆盖）。
    返回实际写入 os.environ 的键值对（便于测试断言）。
    """
    p = path or (Path(__file__).resolve().parent / ".env")
    if not p.is_file():
        return {}
    applied: dict[str, str] = {}
    for raw in p.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        # 兼容 `export KEY=VALUE` 写法
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if not key:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        # 行内注释：仅处理「值后跟两个空格+#」这种明确写法，避免误伤密码里的 #
        if "  #" in val and val[0] not in ("'", '"'):
            val = val.split("  #", 1)[0].rstrip()
        if not override and os.environ.get(key):
            continue
        os.environ[key] = val
        applied[key] = val
    return applied


# ---------------------------------------------------------------- 报告生成
def load_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


#: 全部主体清单的排序权重：异常 → 未收录 → 待复核 → 正常。
#: 同级之间保持输入顺序（sorted 是稳定排序），不额外引入随机性。
VERDICT_ORDER = {"alert": 0, "notfound": 1, "unknown": 2, "ok": 3}


def _roster_table(rows: list[dict]) -> list[str]:
    """全部主体清单：每一家都列出名称、信用代码、登记状态与判定。

    为什么必须**始终**输出（而不是只在异常时出表）：
    早先的正文仅在「确定异常」时生成表格，全部正常时只写一句「本期无异常主体」，
    收件人因此看不到到底查了哪几家、每家的状态是什么——「邮件里没有企业名称」
    就是这么来的。异常与待复核排在最前，保证一眼能扫到要处理的对象。
    """
    ordered = sorted(rows, key=lambda r: VERDICT_ORDER.get(r.get("判定", ""), 9))
    out = [
        "**全部主体清单**（异常与待复核排在前）", "",
        "| 企业名称 | 统一社会信用代码 | 登记状态 | 判定 | 备注 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in ordered:
        name = r.get("匹配名称") or r.get("输入名称") or "-"
        code = r.get("统一社会信用代码") or "-"
        st = r.get("登记状态") or "-"
        verdict = VERDICT_CN.get(r.get("判定", ""), "?")
        note = (r.get("备注") or "").strip() or "-"
        out.append(f"| {name} | {code} | {st} | {verdict} | {note} |")
    out.append("")
    return out


def build_report(rows: list[dict],
                 health: dict | None = None) -> tuple[str, str, list[dict]]:
    """返回 (标题, Markdown 正文, 需提醒行)。

    需提醒行 = alert + notfound + unknown：
      - alert    确定异常，必须处理；
      - notfound 未收录，疑似已注销/吊销，需人工确认；
      - unknown  名称不精确等导致没查出来，**同样需要人看一眼**——
                 把它排除在外会让「一批写错的名字」静默通过。

    health 非空时把 healthcheck.py 的 JSON 结果并进正文（见 append_health_section），
    但不改变 need 的语义——need 只描述营业执照，服务健康由调用方判定是否推送。
    """
    need = [r for r in rows if r.get("判定") in ALERT_VERDICTS]
    hard = [r for r in need if r.get("判定") in HARD_VERDICTS]
    ok = [r for r in rows if r.get("判定") == "ok"]
    unknown = [r for r in rows if r.get("判定") == "unknown"]
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    if hard:
        title = f"营业执照状态巡检 {ts}：{len(hard)} 家异常"
    elif unknown:
        title = f"营业执照状态巡检 {ts}：{len(unknown)} 家待复核"
    else:
        title = f"营业执照状态巡检 {ts}：全部正常"

    lines = [
        f"**巡检时间**：{ts}",
        f"**总数** {len(rows)} ｜ 正常 {len(ok)} ｜ 异常 {len(hard)} ｜ 待复核 {len(unknown)}",
        "",
    ]
    if hard:
        lines += ["**需关注主体**", "", "| 企业名称 | 统一社会信用代码 | 状态 | 判定 |",
                  "| --- | --- | --- | --- |"]
        for r in hard:
            name = r.get("匹配名称") or r.get("输入名称") or "-"
            code = r.get("统一社会信用代码") or "-"
            st = r.get("登记状态") or "-"
            lines.append(f"| {name} | {code} | {st} | {VERDICT_CN.get(r.get('判定',''), '?')} |")
        lines.append("")
    elif not unknown:
        # 全绿时也要说清「下面那张表就是全部结果」：只写「本期无异常主体」
        # 会让人以为正文到此为止，从而漏看紧跟其后的全量清单。
        lines += ["本期无异常主体，全部企业明细见下表。", ""]

    if unknown:
        lines += ["**需人工复核**", "",
                  "以下主体未能确认状态（多为名称不精确）。**这不代表正常**，"
                  "请核对工商登记全称后重查：", ""]
        for r in unknown:
            note = r.get("备注") or "状态为空"
            lines.append(f"- {r.get('输入名称')}：{note}")
        lines.append("")
    lines += _roster_table(rows)
    md = "\n".join(lines)
    if health:
        md = append_health_section(md, health)
    return title, md, need


# ---------------------------------------------------------------- 服务自检并入
#: 自检项 id → 中文名。与 healthcheck.py 的 CHECK_TABLE 保持同源，
#: 这里只做「展示用」的映射，未知 id 原样输出（不静默丢项）。
HEALTH_CN = {
    "python": "Python 版本",
    "venv": "独立 venv",
    "deps": "运行时依赖",
    "browser": "浏览器本体",
    "companies": "企业名单",
    "writable": "目录可写",
    "site": "目标站可达",
    "smtp_config": "邮件配置",
    "recipients": "收件人配置",
    "smtp_tcp": "SMTP 连通",
    "jobs_db": "作业库",
    "heartbeat": "上次运行",
}


def failed_health_checks(health: dict) -> list[dict]:
    """取自杀失败的自检项（含 fatal 与 warn，调用方按 severity 区分）。"""
    return [c for c in (health.get("checks") or []) if not c.get("ok")]


def append_health_section(md: str, health: dict) -> str:
    """把自检结果拼成正文末尾的一节。

    只列**失败与警告**项：全部通过时只在页脚写一行「自检 12 项通过」，
    否则每天一封 12 行清单会让人直接忽略整封邮件。
    """
    summary = health.get("summary") or {}
    bad = failed_health_checks(health)
    total = summary.get("total", len(health.get("checks") or []))
    passed = summary.get("passed", 0)

    lines = [md.rstrip(), "", "---", ""]
    if not bad:
        lines += [f"**服务自检**：{passed}/{total} 项通过，无异常。", ""]
        return "\n".join(lines)

    lines += ["### 服务自检异常", "",
              "巡检服务自身存在问题，可能影响本次结果的完整性：", ""]
    for c in bad:
        name = HEALTH_CN.get(c.get("id", ""), c.get("id", "?"))
        tag = "致命" if c.get("severity") == "fatal" else "警告"
        lines.append(f"- **{name}**（{tag}）：{c.get('detail') or '无详情'}")
    lines += ["", f"其余 {passed}/{total} 项通过。", ""]
    return "\n".join(lines)



# ---------------------------------------------------------------- Markdown→HTML
def _inline(s: str) -> str:
    """转义 HTML 后处理 **粗体**。"""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", _html.escape(s))


def md_to_html(md: str) -> str:
    """把 build_report 产出的受限 Markdown（表格/列表/粗体/段落）转成 HTML。

    只覆盖本项目实际用到的语法，不做通用 Markdown 解析——这样不必引入
    markdown 依赖，也不会因为未知语法把邮件渲染成乱码。
    表格里带判定列时按判定上色，方便主管扫读。
    """
    lines = md.splitlines()
    n = len(lines)
    out: list[str] = []
    i = 0
    while i < n:
        s = lines[i].strip()
        if not s:
            i += 1
            continue

        # 表格：连续以 | 开头的行
        if s.startswith("|"):
            block: list[str] = []
            while i < n and lines[i].strip().startswith("|"):
                block.append(lines[i].strip())
                i += 1
            rows: list[list[str]] = []
            for b in block:
                cells = [c.strip() for c in b.strip("|").split("|")]
                # 跳过分隔行 |---|:--:|
                if cells and all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):
                    continue
                rows.append(cells)
            if not rows:
                continue
            head, body = rows[0], rows[1:]
            out.append('<table style="border-collapse:collapse;font-size:13px;'
                       'margin:10px 0;width:100%">')
            out.append("<thead><tr>" + "".join(
                '<th style="border:1px solid #d0d7de;padding:7px 10px;'
                f'background:#f6f8fa;text-align:left;font-weight:600">{_inline(c)}</th>'
                for c in head) + "</tr></thead><tbody>")
            for r in body:
                tds = []
                for c in r:
                    color = _VERDICT_COLOR.get(
                        next((k for k, v in VERDICT_CN.items() if v == c), ""), "")
                    style = (f"color:{color};font-weight:600" if color else "")
                    tds.append('<td style="border:1px solid #d0d7de;padding:7px 10px;'
                               f'{style}">{_inline(c)}</td>')
                out.append("<tr>" + "".join(tds) + "</tr>")
            out.append("</tbody></table>")
            continue

        # 无序列表
        if s.startswith("- "):
            out.append('<ul style="margin:8px 0 8px 22px;padding:0">')
            while i < n and lines[i].strip().startswith("- "):
                out.append('<li style="margin:4px 0;font-size:13px;line-height:1.6">'
                           f'{_inline(lines[i].strip()[2:])}</li>')
                i += 1
            out.append("</ul>")
            continue

        # 三级标题
        if s.startswith("### "):
            out.append('<h3 style="margin:16px 0 8px;font-size:15px">'
                       f'{_inline(s[4:])}</h3>')
            i += 1
            continue

        out.append('<p style="margin:7px 0;font-size:13px;line-height:1.7">'
                   f'{_inline(s)}</p>')
        i += 1

    body = "\n".join(out)
    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,'
        'Microsoft YaHei,sans-serif;color:#1f2328;max-width:920px;padding:4px">'
        + body + "</div>"
    )


# ---------------------------------------------------------------- 推送通道
def send_webhook(url: str, title: str, md: str) -> None:
    """企业微信/钉钉/飞书通用：先用 markdown 类型，失败退化为 text。"""
    payload = {"msgtype": "markdown", "markdown": {"content": f"### {title}\n\n{md}"}}
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", "replace")
    j = json.loads(body) if body.strip().startswith("{") else {}
    errcode = j.get("errcode", j.get("code", 0))
    if errcode not in (0, None):
        # 飞书/钉钉字段名不同，退化 text 再试一次
        payload = {"msgtype": "text", "text": {"content": f"{title}\n\n{md}"}}
        req = urllib.request.Request(
            url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", "replace")
        j = json.loads(body) if body.strip().startswith("{") else {}
        if j.get("errcode", j.get("code", 0)) not in (0, None):
            raise RuntimeError(f"webhook 返回异常: {body[:300]}")
    print(f"[webhook] 已推送 -> {body[:200]}")


def send_mail(host: str, port: int, user: str, password: str,
              to_addrs: list[str], subject: str, md: str, use_tls: bool,
              use_ssl: bool | None = None, timeout: int = 30) -> None:
    """发邮件。同时带 text/plain 与 text/html 两份正文（客户端择优显示）。

    use_ssl=None 时按端口自动判断：465 → 隐式 SSL，其余走明文+STARTTLS。
    QQ / 163 / 阿里企业邮的 465 端口都是隐式 SSL，用 smtplib.SMTP 连会直接卡死，
    所以必须显式用 SMTP_SSL。
    """
    if use_ssl is None:
        use_ssl = (port == 465)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(to_addrs)
    msg.set_content(md)                                    # 纯文本兜底
    msg.add_alternative(md_to_html(md), subtype="html")    # 富文本正文

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=timeout) as s:
            if user:
                s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=timeout) as s:
            if use_tls:
                s.starttls()
            if user:
                s.login(user, password)
            s.send_message(msg)
    print(f"[mail] 已发送 -> {', '.join(to_addrs)}")


# ---------------------------------------------------------------- 双收件人 / 节奏
#: 两种节奏：daily=每日给业务人员，monthly=每月给主管。
MODE_DAILY = "daily"
MODE_MONTHLY = "monthly"
MODES = (MODE_DAILY, MODE_MONTHLY)

#: 模式 → 收件人环境变量名（不含向后兼容回退）。
RECIPIENT_ENV = {
    MODE_DAILY: "MAIL_TO_BUSINESS",
    MODE_MONTHLY: "MAIL_TO_MANAGER",
}

#: 模式 → 报告标题前缀。邮件主题一眼能分辨「这是日报还是月报」。
MODE_LABEL = {MODE_DAILY: "每日巡检", MODE_MONTHLY: "月度巡检"}


def parse_addrs(raw: str | None) -> list[str]:
    """把 `a@x.com, b@y.com`（也容忍分号/空白分隔）解析成地址列表。

    用正则而不是 split(",")：真实 .env 里出现过用中文逗号、分号、
    甚至换行续写的情况，静默丢掉收件人比报错更难发现。
    """
    if not raw:
        return []
    parts = re.split(r"[,;，；\s]+", raw.strip())
    return [p.strip() for p in parts if p.strip()]


def resolve_recipients(mode: str, env: dict[str, str] | None = None) -> list[str]:
    """按节奏选收件人，带向后兼容回退。

    优先级：`MAIL_TO_BUSINESS`/`MAIL_TO_MANAGER` → 旧 `MAIL_TO`。
    回退是为了让已按旧文档部署的 .env 在升级后仍然能发信，
    而不是因为少配一个新键就彻底静默。
    """
    e = os.environ if env is None else env
    key = RECIPIENT_ENV.get(mode, "MAIL_TO_BUSINESS")
    raw = e.get(key) or e.get("MAIL_TO") or ""
    return parse_addrs(raw)


def should_send(mode: str, need: list[dict], always: bool,
                health: dict | None = None) -> bool:
    """是否值得发这一封。

    daily  ：有执照异常/待复核 **或** 有致命自检失败 **或** --always；
    monthly：有执照异常/待复核 **或** --always（服务健康已在 daily 报过，
             月报不再重复；但 --always 仍可强制发）。

    全绿不发是刻意的：每天一封「全部正常」会被当垃圾邮件，
    真告警反而没人看。代价是「任务被禁用」会静默——用心跳项补偿
    （healthcheck.check_heartbeat 在下一封里带出「距上次 N 小时」）。
    """
    if always:
        return True
    if need:
        return True
    if mode == MODE_DAILY and health:
        summary = health.get("summary") or {}
        if summary.get("failed"):
            return True
    return False


def load_health(path: Path) -> dict | None:
    """读 healthcheck.py 产出的 JSON；缺失或损坏时返回 None（不阻断发信）。"""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def prefixed_title(title: str, mode: str) -> str:
    """给标题加节奏前缀，避免日报与月报在收件箱里长得一样。"""
    label = MODE_LABEL.get(mode)
    if not label:
        return title
    return f"[{label}] {title}"



# ---------------------------------------------------------------- 入口
def main() -> int:
    # .env 必须在构造 argparse 之前加载：下面所有 default=os.getenv(...)
    # 都是在 add_argument 时求值的，晚一步就读不到 .env 里的值。
    applied = load_dotenv()
    if applied:
        log.debug(".env 已加载 %d 项：%s", len(applied), ", ".join(sorted(applied)))

    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="credit_check.py 产出的 CSV")
    ap.add_argument("--webhook", default=os.getenv("CREDIT_WEBHOOK", ""))
    ap.add_argument("--smtp-host", default=os.getenv("SMTP_HOST", ""))
    ap.add_argument("--smtp-port", type=int,
                    default=int(os.getenv("SMTP_PORT", "0") or 0),
                    help="0=自动（--smtp-ssl 时 465，否则 587）")
    ap.add_argument("--smtp-user", default=os.getenv("SMTP_USER", ""))
    ap.add_argument("--smtp-pass", default=os.getenv("SMTP_PASS", ""))
    ap.add_argument("--mail-to", default="",
                    help="逗号分隔的收件人；留空时按 --mode 从环境变量取"
                         "（daily→MAIL_TO_BUSINESS，monthly→MAIL_TO_MANAGER，"
                         "两者都空则回退旧 MAIL_TO）")
    ap.add_argument("--mail-from", default=os.getenv("MAIL_FROM", ""),
                    help="发件人地址（默认取 --smtp-user）")
    ap.add_argument("--smtp-ssl", action="store_true",
                    default=os.getenv("SMTP_SSL", "").lower() in ("1", "true", "yes"),
                    help="隐式 SSL（465 端口，QQ/163 必需）")
    ap.add_argument("--no-tls", action="store_true",
                    help="禁用 STARTTLS（仅内网自建 SMTP 用）")
    ap.add_argument("--always", action="store_true", help="无异常也发送")
    ap.add_argument("--mode", choices=MODES, default=MODE_DAILY,
                    help="节奏：daily=每日给业务人员（默认），monthly=每月给主管")
    ap.add_argument("--health", default=os.getenv("CREDIT_HEALTH_JSON", ""),
                    help="healthcheck.py 产出的 JSON，并入正文；"
                         "daily 下自检致命失败也会触发推送")
    ap.add_argument("--log-dir", default=None, help="日志目录（默认 ./logs）")
    args = ap.parse_args()

    logkit.setup_logging("notify", log_dir=args.log_dir)

    use_ssl = args.smtp_ssl or args.smtp_port == 465
    port = args.smtp_port or (465 if use_ssl else 587)
    sender = args.mail_from or args.smtp_user

    # 收件人：显式 --mail-to 优先，否则按节奏从环境变量解析（含旧 MAIL_TO 回退）
    to_addrs = parse_addrs(args.mail_to) or resolve_recipients(args.mode)
    health = load_health(args.health) if args.health else None

    log.info("启动：mode=%s csv=%s webhook=%s smtp=%s:%s ssl=%s always=%s "
             "health=%s 收件人=%d",
             args.mode, args.csv, bool(args.webhook), args.smtp_host, port,
             use_ssl, args.always, args.health or "-", len(to_addrs))

    path = Path(args.csv)
    if not path.exists():
        print(f"文件不存在: {path}", file=sys.stderr)
        log.error("CSV 不存在: %s", path)
        return 1

    rows = load_rows(path)
    title, md, need = build_report(rows, health=health)
    title = prefixed_title(title, args.mode)
    print(md)
    log.info("汇总：mode=%s 总数=%d 需关注=%d 标题=%s",
             args.mode, len(rows), len(need), title)

    if not should_send(args.mode, need, args.always, health):
        print("\n本期无异常，跳过推送（加 --always 可强制发送）")
        log.info("本期无异常，跳过推送（exit=10）")
        return 10

    sent = 0
    errors = []
    has_mail = bool(args.smtp_host and to_addrs)

    if args.webhook:
        try:
            send_webhook(args.webhook, title, md)
            sent += 1
            log.info("webhook 推送成功")
        except Exception as e:
            errors.append(f"webhook: {e}")
            log.error("webhook 推送失败: %s", e)
    if has_mail:
        try:
            send_mail(args.smtp_host, port, sender, args.smtp_pass,
                      to_addrs, title, md, not args.no_tls, use_ssl=use_ssl)
            sent += 1
            log.info("邮件发送成功 -> %s", ", ".join(to_addrs))
        except Exception as e:
            errors.append(f"mail: {e}")
            log.error("邮件发送失败: %s", e)

    if not args.webhook and not has_mail:
        # 半配置也走这里、也返回 10：这是 README §6 记录的既有契约
        # （「以为加了 --always 就发了、其实一封没发」），有测试锁死，不能改。
        # 配置缺失改由 healthcheck.py 的 smtp_config 致命项负责报告——
        # 它能说清缺的是哪个键，比在这里猜有用。
        lack = ""
        if bool(args.smtp_host) != bool(to_addrs):
            lack = "缺收件人" if not to_addrs else "缺 SMTP_HOST"
            print(f"\n[警告] 邮件通道只配了一半（{lack}），本次未发送。"
                  f"healthcheck 会把它报为致命项。", file=sys.stderr)
        print("\n[提示] 未配置推送通道，仅打印。"
              "加 --webhook 或 --smtp-* 即可推送（也可写在 .env 里）。")
        log.warning("未配置推送通道，仅打印（exit=10）%s",
                    f"；半配置：{lack}" if lack else "")
        return 10

    for e in errors:
        print(f"[error] {e}", file=sys.stderr)
    rc = 0 if sent and not errors else 1
    log.info("结束：sent=%d errors=%d exit=%d", sent, len(errors), rc)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
