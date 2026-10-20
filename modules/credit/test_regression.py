# -*- coding: utf-8 -*-
"""
回归测试：把已实跑验证过的行为固化成断言。
=========================================================
覆盖三层，全部**离线**（不联网、不开浏览器、不调 OCR）：

  1. 纯函数层：名称归一、状态判定、CSV 字段契约
  2. 输出层：CSV 编码/列序、notify 报告生成、退出码语义
  3. 日志层：轮转文件落盘、幂等、不污染 stdout

真联网的端到端验证不在这里（它依赖第三方站点与 OCR 随机性，
不适合做 CI 断言）；跑 `credit_check.py --selftest` 即可。

运行：
    .venv\\Scripts\\python.exe -m pytest test_regression.py -q
"""
from __future__ import annotations

import csv
import io
import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path

import logkit
import pytest
import roster
from credit_check import (
    ALERT,
    NORMAL,
    SELFTEST,
    STATUS_FIELDS,
    VERDICT_LABEL,
    classify,
    norm_name,
    ocr_preprocess,
)

#: 脚本所在目录。测试不依赖调用者的 cwd（pytest 可能从任意目录启动）。
HERE = Path(__file__).resolve().parent


def _in_git_checkout() -> bool:
    """当前目录是否处在 git 检出内。

    用途：少数断言检查的是**仓库结构**（例如 `data/.gitkeep` 占位文件必须存在），
    这类断言在容器镜像里没有意义——镜像里 `.dockerignore` 刻意排除了 `data/`
    （运行期走卷挂载），也没有 `.git`。

    CI 与本地开发都在检出内跑，所以这些断言照常生效；
    只有从镜像里跑 pytest 时才会跳过，不会因此漏掉真实问题。
    """
    return any((p / ".git").exists() for p in [HERE, *HERE.parents])


@pytest.fixture(autouse=True)
def _isolate_env(request, monkeypatch):
    """让所有测试都不受本机 `.env` 影响（`TestDotenv` 除外）。

    为什么必须有：`notify.main()` 内部会调 `load_dotenv()` 读项目根的 `.env`。
    一旦开发机上真的配好了 `.env`（例如为了验证邮件通道），
    那些靠 `monkeypatch.delenv(...)` 构造「未配置任何通道」场景的用例
    就会拿到真实收件人与 SMTP 凭据 —— 断言从 10 变 0 而失败，
    更糟的是**测试会真的给业务人员/主管发一封邮件**。

    所以这里在 import 层之前就把 `load_dotenv` 换成空操作：
    测试要什么配置就自己 monkeypatch 什么，`.env` 存不存在都不改变语义。

    唯一的例外是 `TestDotenv`——它测的就是 `load_dotenv` 本身，
    短路掉等于把被测函数换掉，测了也白测。
    """
    if request.node.cls.__name__ == "TestDotenv":
        yield
        return
    import notify
    monkeypatch.setattr(notify, "load_dotenv", lambda *a, **k: False)
    yield

# ---------------------------------------------------------------- 纯函数层


class TestNormName:
    """名称归一：决定「精确同名」判定是否成立，是防假阳性的第一道关。"""

    @pytest.mark.parametrize("raw,expected", [
        ("阿里巴巴(中国)有限公司", "阿里巴巴(中国)有限公司"),
        ("阿里巴巴（中国）有限公司", "阿里巴巴(中国)有限公司"),  # 全角括号归一
        (" 阿里巴巴(中国)有限公司 ", "阿里巴巴(中国)有限公司"),  # 首尾空白
        ("阿里巴巴 (中国) 有限公司", "阿里巴巴(中国)有限公司"),  # 中间空白
        ("阿里巴巴\u3000(中国)有限公司", "阿里巴巴(中国)有限公司"),  # 全角空格
    ])
    def test_normalizes(self, raw, expected):
        assert norm_name(raw) == expected

    def test_empty(self):
        assert norm_name("") == ""
        assert norm_name(None) == ""

    def test_fullwidth_and_halfwidth_are_equal(self):
        """实测假阳性的根因：全/半角括号不同导致精确匹配失败。"""
        assert norm_name("京东世纪贸易（有限公司）") == norm_name("京东世纪贸易(有限公司)")


class TestClassify:
    """状态判定四态。实测取值来自信用中国真实响应。"""

    @pytest.mark.parametrize("status", ["存续", "在业", "在营", "开业", "正常", "有效", "登记"])
    def test_ok_states(self, status):
        assert classify(status) == "ok"

    @pytest.mark.parametrize("status", [
        "吊销", "注销", "已注销", "已吊销", "撤销", "迁出",
        "停业", "清算", "歇业", "责令关闭", "异常", "非正常",
    ])
    def test_alert_states(self, status):
        assert classify(status) == "alert"

    @pytest.mark.parametrize("status", ["", None, "   ", "未知状态"])
    def test_unknown_states(self, status):
        assert classify(status) == "unknown"

    def test_alert_wins_over_normal(self):
        """状态串同时含两类词时必须判 alert（保守优先，避免漏报）。"""
        assert classify("存续（已吊销）") == "alert"

    def test_real_samples(self):
        """实测样本：基金会走 credit_mzb_shzz，org_statu='正常'。"""
        assert classify("正常") == "ok"    # 阿里巴巴公益基金会
        assert classify("存续") == "ok"    # 阿里巴巴(中国)有限公司

    def test_normal_and_alert_sets_disjoint(self):
        """两个集合不能有交集，否则判定顺序决定结果，语义不确定。"""
        assert not (NORMAL & ALERT)


class TestStatusFields:
    """状态字段回退顺序：企业表与基金会表字段名不同，必须按序回退。"""

    def test_order_preserved(self):
        assert STATUS_FIELDS[0] == "status"  # headEntity.status 最优先
        assert "org_statu" in STATUS_FIELDS  # 基金会表字段

    def test_headentity_takes_precedence(self):
        head = {"status": "存续"}
        ent = {"org_statu": "正常"}
        got = next((head.get(f) or ent.get(f) for f in STATUS_FIELDS if head.get(f) or ent.get(f)), "")
        assert got == "存续"


class TestVerdictLabel:
    def test_all_four_states_have_label(self):
        for k in ("ok", "alert", "notfound", "unknown"):
            assert k in VERDICT_LABEL

    def test_labels_are_chinese(self):
        assert VERDICT_LABEL["ok"] == "正常"
        assert VERDICT_LABEL["alert"] == "异常"
        assert VERDICT_LABEL["notfound"] == "未收录"


class TestOcrPreprocess:
    def test_upscale_dimensions(self):
        """3 倍放大：95x25 -> 285x75。实测 52.6% vs 原图 45%。"""
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (95, 25), "white").save(buf, format="PNG")
        out = ocr_preprocess(buf.getvalue(), factor=3)
        im = Image.open(io.BytesIO(out))
        assert im.size == (285, 75)

    def test_bad_input_returns_original(self):
        """非图片输入不能抛异常（OCR 失败要能继续换图重试）。"""
        bad = b"not an image"
        assert ocr_preprocess(bad) == bad


# ---------------------------------------------------------------- 输出层

CSV_FIELDS = ["输入名称", "匹配名称", "统一社会信用代码", "登记状态", "判定",
              "主体类型", "数据源", "记录数", "详情URL", "备注"]


class TestCsvContract:
    """CSV 是交付给销售的最终产物，列名/列序/编码都是契约。"""

    def _write(self, tmp_path: Path, rows: list[dict]) -> Path:
        p = tmp_path / "out.csv"
        with p.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            w.writerows(rows)
        return p

    def test_fields_match_credit_check(self):
        """CSV_FIELDS 必须与 credit_check.main() 里的 fields 完全一致。"""
        import re
        src = (HERE / "credit_check.py").read_text(encoding="utf-8")
        m = re.search(r"fields = \[(.*?)\]", src, re.S)
        assert m, "credit_check.py 里找不到 fields 定义"
        got = re.findall(r'"([^"]+)"', m.group(1))
        assert got == CSV_FIELDS

    def test_utf8_bom_for_excel(self, tmp_path):
        """销售用 Excel 打开，无 BOM 会中文乱码。"""
        row = dict.fromkeys(CSV_FIELDS, "")
        row.update({"输入名称": "测试公司", "判定": "ok"})
        p = self._write(tmp_path, [row])
        raw = p.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf"), "缺少 UTF-8 BOM"
        assert "测试公司" in raw.decode("utf-8-sig")

    def test_roundtrip_preserves_chinese(self, tmp_path):
        rows = [
            {"输入名称": "阿里巴巴(中国)有限公司", "匹配名称": "阿里巴巴(中国)有限公司",
             "统一社会信用代码": "91330100799655058B", "登记状态": "存续",
             "判定": "ok", "主体类型": "1", "数据源": "市场监督管理总局",
             "记录数": "21", "详情URL": "https://x", "备注": ""},
            {"输入名称": "石家庄三鹿集团股份有限公司", "匹配名称": "",
             "统一社会信用代码": "", "登记状态": "", "判定": "notfound",
             "主体类型": "", "数据源": "", "记录数": "0", "详情URL": "",
             "备注": "信用中国未收录"},
        ]
        p = self._write(tmp_path, rows)
        with p.open(encoding="utf-8-sig") as f:
            back = list(csv.DictReader(f))
        assert len(back) == 2
        assert back[0]["登记状态"] == "存续"
        assert back[1]["判定"] == "notfound"


class TestNotifyReport:
    """提醒报告：内容与退出码语义是运维契约。"""

    def _rows(self, verdicts: list[str]) -> list[dict]:
        out = []
        for i, v in enumerate(verdicts):
            out.append({"输入名称": f"公司{i}", "匹配名称": f"公司{i}" if v != "notfound" else "",
                        "统一社会信用代码": "X" if v != "notfound" else "",
                        "登记状态": "存续" if v == "ok" else "",
                        "判定": v, "备注": ""})
        return out

    def test_need_contains_alert_notfound_unknown(self):
        """unknown 必须计入 need。

        50 条样例实跑里有 12 条因名称不精确落到 unknown。若 unknown 不触发
        提醒，一批写错的名字会得到「本期无异常」的静默通过——这是最危险的
        失败模式，比漏报一家更糟。
        """
        from notify import build_report
        _, _, need = build_report(self._rows(["ok", "alert", "notfound", "unknown"]))
        assert len(need) == 3
        assert {r["判定"] for r in need} == {"alert", "notfound", "unknown"}

    def test_unknown_alone_still_triggers_need(self):
        """整批都是 unknown 时也必须推送，不能静默。"""
        from notify import build_report
        title, md, need = build_report(self._rows(["unknown", "unknown"]))
        assert len(need) == 2, "unknown 必须进 need，否则整批写错名字会静默通过"
        assert "待复核" in title
        assert "需人工复核" in md
        assert "这不代表正常" in md

    def test_no_alert_yields_empty_need(self):
        from notify import build_report
        _, _, need = build_report(self._rows(["ok", "ok"]))
        assert need == []

    def test_markdown_table_present_when_alerts(self):
        from notify import build_report
        _, md, _ = build_report(self._rows(["alert", "ok"]))
        assert "| 企业名称 |" in md
        assert "| --- |" in md

    def test_no_alert_message(self):
        from notify import build_report
        _, md, _ = build_report(self._rows(["ok"]))
        assert "本期无异常主体" in md

    def test_unknown_listed_for_review(self):
        from notify import build_report
        _, md, _ = build_report(self._rows(["unknown"]))
        assert "需人工复核" in md

    def test_title_counts_hard_only(self):
        """标题里的「异常」只数 alert+notfound，不含待复核。"""
        from notify import build_report
        title, _, _ = build_report(self._rows(["alert", "notfound", "ok"]))
        assert "2 家异常" in title

    def test_hard_and_unknown_split_in_body(self):
        """有确定异常时，unknown 单独一节，不混进异常表。"""
        from notify import build_report
        _, md, _ = build_report(self._rows(["alert", "unknown"]))
        assert "需关注主体" in md and "需人工复核" in md
        # 异常表里只能有 1 行（alert），unknown 不该进表。
        # 注意必须限定在「需关注主体」段内：后面还有一张「全部主体清单」
        # （每一家都列），不限范围会把清单里的行算进来。
        seg = md.split("**需关注主体**", 1)[1].split("**需人工复核**", 1)[0]
        table_rows = [ln for ln in seg.splitlines()
                      if ln.startswith(("| 公司", "| 甲"))]
        assert len(table_rows) == 1, f"异常表应只有 alert 一行，实际 {table_rows}"

    # ---- 全部主体清单（「邮件里看不到企业名称」的回归） ----
    def test_roster_listed_even_when_all_ok(self):
        """全部正常也必须列出每一家，不能只写一句「本期无异常主体」。

        这是用户实测反馈的缺陷：正文只在异常时出表，正常时收件人看不到
        查了哪几家、各自状态是什么。
        """
        from notify import build_report
        _, md, _ = build_report(self._rows(["ok", "ok"]))
        assert "全部主体清单" in md
        assert "| 公司0 |" in md and "| 公司1 |" in md

    def test_roster_has_every_input_row(self):
        """清单数据行数必须等于输入行数，一行都不能丢。"""
        from notify import build_report
        rows = self._rows(["ok", "alert", "notfound", "unknown", "ok"])
        _, md, _ = build_report(rows)
        seg = md.split("**全部主体清单**", 1)[1]
        data = [ln for ln in seg.splitlines()
                if ln.startswith(("| 公司", "| 甲"))]
        assert len(data) == 5, f"清单应含 5 家，实际 {len(data)}：{data}"

    def test_roster_sorts_abnormal_before_normal(self):
        """异常与待复核排在前，正常排在后（同级保持输入顺序）。"""
        from notify import build_report
        _, md, _ = build_report(self._rows(["ok", "alert", "notfound", "unknown"]))
        seg = md.split("**全部主体清单**", 1)[1]
        data = [ln for ln in seg.splitlines()
                if ln.startswith(("| 公司", "| 甲"))]
        # 输入顺序 公司0=ok 公司1=alert 公司2=notfound 公司3=unknown
        assert data[0].startswith("| 公司1 |")   # alert
        assert data[1].startswith("| 公司2 |")   # notfound
        assert data[2].startswith("| 公司3 |")   # unknown
        assert data[3].startswith("| 公司0 |")   # ok

    def test_roster_carries_code_status_and_note(self):
        """未收录行没有代码/状态，但不能没有备注说明。"""
        from notify import build_report
        rows = self._rows(["notfound"])
        rows[0]["备注"] = "信用中国未收录（疑似已注销/吊销）"
        _, md, _ = build_report(rows)
        assert "疑似已注销" in md
        # 未收录行用 "-" 占位，不允许出现空列。
        # 只取清单段内的行：需关注表是 4 列，清单表才是这里要验的 5 列。
        seg = md.split("**全部主体清单**", 1)[1]
        body = next(ln for ln in seg.splitlines() if ln.startswith("| 公司"))
        assert body.count("|") == 6, f"五列表格应有 6 个分隔符：{body}"

    def test_roster_survives_html_rendering(self):
        """转 HTML 后每一家名称都还在（表格渲染不能吃行）。"""
        from notify import build_report, md_to_html
        _, md, _ = build_report(self._rows(["ok", "alert", "notfound"]))
        html = md_to_html(md)
        for i in range(3):
            assert f"公司{i}" in html
        assert "未收录" in html


class TestExitCodes:
    """退出码语义（README §6 已承诺，计划任务按它判成败）。"""

    def test_documented_values(self):
        src = (HERE / "notify.py").read_text(encoding="utf-8")
        assert "0=有异常且已发送" in src
        assert "10=无需推送" in src
        assert "1=发送失败" in src

    def test_missing_csv_returns_1(self, tmp_path, monkeypatch):
        import notify
        monkeypatch.setattr(sys, "argv", ["notify.py", str(tmp_path / "nope.csv")])
        assert notify.main() == 1

    def test_no_channel_returns_10(self, tmp_path, monkeypatch):
        import notify
        p = tmp_path / "r.csv"
        with p.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            row = dict.fromkeys(CSV_FIELDS, "")
            row.update({"输入名称": "A", "判定": "alert", "登记状态": "吊销"})
            w.writerow(row)
        monkeypatch.setattr(sys, "argv", ["notify.py", str(p)])
        monkeypatch.delenv("CREDIT_WEBHOOK", raising=False)
        monkeypatch.delenv("SMTP_HOST", raising=False)
        assert notify.main() == 10

    def test_all_ok_returns_10(self, tmp_path, monkeypatch):
        import notify
        p = tmp_path / "r.csv"
        with p.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            row = dict.fromkeys(CSV_FIELDS, "")
            row.update({"输入名称": "A", "判定": "ok", "登记状态": "存续"})
            w.writerow(row)
        monkeypatch.setattr(sys, "argv", ["notify.py", str(p)])
        monkeypatch.delenv("CREDIT_WEBHOOK", raising=False)
        monkeypatch.delenv("SMTP_HOST", raising=False)
        assert notify.main() == 10

    def test_push_failure_returns_1_and_no_traceback(self, tmp_path, monkeypatch, caplog):
        """webhook 不可达必须返回 1，且日志里不能有整段 traceback。

        实测过一次：log.exception 把 30 行堆栈灌进日志，
        运维无从下手；改成单行后仍要保留「是哪个通道失败」。
        """
        import notify
        p = tmp_path / "r.csv"
        with p.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            row = dict.fromkeys(CSV_FIELDS, "")
            row.update({"输入名称": "A", "判定": "alert", "登记状态": "吊销"})
            w.writerow(row)

        # 指向必然拒连的本机端口，避免任何外网依赖
        monkeypatch.setattr(sys, "argv",
                            ["notify.py", str(p), "--webhook", "http://127.0.0.1:1/hook"])
        monkeypatch.delenv("CREDIT_WEBHOOK", raising=False)
        with caplog.at_level(logging.ERROR):
            rc = notify.main()
        assert rc == 1
        assert "webhook 推送失败" in caplog.text
        # 不允许堆栈：Traceback / File " 都不能出现在日志文本里
        assert "Traceback" not in caplog.text
        assert 'File "' not in caplog.text

    def test_always_flag_sends_even_when_all_ok(self, tmp_path, monkeypatch):
        """--always + 无异常 + 通道失败 → 1（证明它确实尝试发送了）。"""
        import notify
        p = tmp_path / "r.csv"
        with p.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            row = dict.fromkeys(CSV_FIELDS, "")
            row.update({"输入名称": "A", "判定": "ok", "登记状态": "存续"})
            w.writerow(row)
        monkeypatch.setattr(sys, "argv",
                            ["notify.py", str(p), "--webhook", "http://127.0.0.1:1/hook",
                             "--always"])
        monkeypatch.delenv("CREDIT_WEBHOOK", raising=False)
        assert notify.main() == 1

    def test_half_configured_channel_returns_10_even_with_always(
            self, tmp_path, monkeypatch, capsys):
        """只配 SMTP_HOST 不配 MAIL_TO（或反之）时，--always 也救不回 10。

        实测：`--always --smtp-host smtp.qq.com --smtp-user x@qq.com` 且无 MAIL_TO
        → exit 10，屏幕上打印「未配置推送通道，仅打印」。
        这正是「以为加了 --always 就发了、其实一封没发」的坑，README §6 已写明。
        """
        import notify
        p = tmp_path / "r.csv"
        with p.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            row = dict.fromkeys(CSV_FIELDS, "")
            row.update({"输入名称": "A", "判定": "alert", "登记状态": "吊销"})
            w.writerow(row)

        # 半配置：有 host/user，没有 mail_to；也不该真连 smtp（连了就会 1）
        monkeypatch.setattr(sys, "argv",
                            ["notify.py", str(p), "--always",
                             "--smtp-host", "smtp.qq.com",
                             "--smtp-user", "x@qq.com",
                             "--smtp-pass", "fake"])
        monkeypatch.delenv("CREDIT_WEBHOOK", raising=False)
        monkeypatch.delenv("MAIL_TO", raising=False)
        assert notify.main() == 10
        # 必须给出「没配通道」的提示，否则用户会以为已发送
        assert "未配置推送通道" in capsys.readouterr().out


# ---------------------------------------------------------------- 日志层


class TestLogging:
    def _fresh(self, name: str) -> logging.Logger:
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            lg.removeHandler(h)
            h.close()
        if hasattr(lg, logkit._MARK):
            delattr(lg, logkit._MARK)
        return lg

    def test_writes_file(self, tmp_path):
        lg = self._fresh("t_writes")
        logkit.setup_logging("t_writes", log_dir=tmp_path)
        lg.info("hello %s", "world")
        for h in lg.handlers:
            h.flush()
        content = (tmp_path / "t_writes.log").read_text(encoding="utf-8")
        assert "hello world" in content

    def test_idempotent(self, tmp_path):
        """重复 setup 不能叠加 handler（否则一条日志写多遍）。"""
        self._fresh("t_idem")
        logkit.setup_logging("t_idem", log_dir=tmp_path)
        logkit.setup_logging("t_idem", log_dir=tmp_path)
        logkit.setup_logging("t_idem", log_dir=tmp_path)
        lg = logging.getLogger("t_idem")
        file_handlers = [h for h in lg.handlers
                         if isinstance(h, logging.handlers.RotatingFileHandler)]
        assert len(file_handlers) == 1

    def test_console_does_not_pollute_stdout(self, tmp_path, capsys):
        """INFO 不能进 stdout（脚本自身用 print 输出报告，会被污染）。"""
        lg = self._fresh("t_stdout")
        logkit.setup_logging("t_stdout", log_dir=tmp_path)
        lg.info("should not appear on stdout")
        for h in lg.handlers:
            h.flush()
        captured = capsys.readouterr()
        assert "should not appear" not in captured.out

    def test_warning_goes_to_stderr(self, tmp_path, capsys):
        lg = self._fresh("t_stderr")
        logkit.setup_logging("t_stderr", log_dir=tmp_path)
        lg.warning("visible warning")
        for h in lg.handlers:
            h.flush()
        captured = capsys.readouterr()
        assert "visible warning" in captured.err
        assert "visible warning" not in captured.out

    def test_unwritable_dir_degrades(self, tmp_path):
        """日志目录不可写时降级为仅控制台，不能抛异常拖垮巡检。"""
        self._fresh("t_bad")
        bad = tmp_path / "file_not_dir"
        bad.write_text("x")  # 同名文件，mkdir 必失败
        lg = logkit.setup_logging("t_bad", log_dir=bad / "sub")
        lg.info("still works")

    def test_rotation_config(self, tmp_path):
        lg = self._fresh("t_rot")
        logkit.setup_logging("t_rot", log_dir=tmp_path, max_bytes=1024, backup_count=2)
        fh = next(h for h in lg.handlers
                  if isinstance(h, logging.handlers.RotatingFileHandler))
        assert fh.maxBytes == 1024
        assert fh.backupCount == 2


# ---------------------------------------------------------------- 名单解析


class TestNameListParsing:
    """名单文件解析：注释、空行、首尾空白都要正确处理。"""

    def _parse(self, text: str) -> list[str]:
        return [ln.strip() for ln in text.splitlines()
                if ln.strip() and not ln.strip().startswith("#")]

    def test_skips_comments_and_blanks(self):
        text = "# 这是注释\n\n阿里巴巴(中国)有限公司\n\n# 另一条注释\n腾讯科技(深圳)有限公司\n"
        assert self._parse(text) == ["阿里巴巴(中国)有限公司", "腾讯科技(深圳)有限公司"]

    def test_strips_whitespace(self):
        assert self._parse("  华为技术有限公司  \n") == ["华为技术有限公司"]

    def test_selftest_has_four_known_entities(self):
        """selftest 名单是端到端验证的固定输入，不能随意改。"""
        assert len(SELFTEST) == 4
        assert "阿里巴巴(中国)有限公司" in SELFTEST
        assert "腾讯科技(深圳)有限公司" in SELFTEST
        assert "石家庄三鹿集团股份有限公司" in SELFTEST   # 已注销 → notfound
        assert "深圳市快播科技有限公司" in SELFTEST       # 已注销 → notfound


# ---------------------------------------------------------------- 结构完整性


class TestScriptIntegrity:
    def test_no_hardcoded_foreign_venv(self):
        """P0 改造的护栏：脚本与文档都不能再指向 MCP 服务端的 venv。"""
        for name in ("credit_check.py", "notify.py", "logkit.py"):
            src = (HERE / name).read_text(encoding="utf-8")
            assert "camoufox-reverse-mcp" not in src, f"{name} 仍引用 MCP venv"

    def test_requirements_locked(self):
        """所有依赖必须 == 锁定，不允许浮动版本。"""
        lines = [ln for ln in (HERE / "requirements.txt").read_text(encoding="utf-8").splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
        for ln in lines:
            assert "==" in ln, f"未锁定版本: {ln}"

    def test_repo_root_has_readme(self):
        """GitHub 只认仓库根的 README.md / README.rst 等，认不出 README-xxx.md。

        项目主文档叫 README-营业执照巡检.md，若不补一份 README.md，
        仓库首页会显示「No description, website, or topics」+ 空文件列表，
        对交付/交接非常不友好。
        """
        root = HERE / "README.md"
        assert root.exists(), "仓库根缺少 README.md，GitHub 首页会空白"
        text = root.read_text(encoding="utf-8")
        assert "README-营业执照巡检.md" in text, "根 README 应指向主文档"

    def test_requirements_covers_core_packages(self):
        txt = (HERE / "requirements.txt").read_text(encoding="utf-8")
        for pkg in ("camoufox==", "ddddocr==", "playwright==", "Pillow=="):
            assert pkg in txt, f"缺少核心依赖: {pkg}"

    def test_runtime_requirements_covers_api_imports(self):
        """requirements.txt 是「从零重建环境」的唯一清单，必须能起 api.py。

        踩过的坑：requirements.txt 最初只列了浏览器/OCR 那 32 条，
        而 api.py:38 顶层 `from fastapi import FastAPI, ...`。
        按 README 的重建步骤装完后跑 `uvicorn api:app` 直接
            ModuleNotFoundError: No module named 'fastapi'
        ——文档宣传的 HTTP 接口在干净环境里根本起不来。

        与 test_dev_requirements_cover_toplevel_imports 的区别：
        那条管 CI（只装 requirements-dev.txt 跑 pytest）；
        这条管用户侧（装 requirements.txt 跑业务 + HTTP 服务）。
        两个清单都要覆盖 api.py 的顶层依赖，漏任何一个都是真实故障。
        """
        txt = (HERE / "requirements.txt").read_text(encoding="utf-8")
        pkgs = {ln.split("==")[0].strip().lower()
                for ln in txt.splitlines()
                if ln.strip() and not ln.strip().startswith("#") and "==" in ln}
        # api.py 的第三方顶层 import → pip 包名
        need = {"fastapi": "fastapi", "uvicorn": "uvicorn", "pydantic": "pydantic"}
        missing = [pip for key, pip in need.items() if key not in pkgs]
        assert not missing, (
            f"requirements.txt 缺少 {missing}；"
            "缺了按 README 重建环境后 `uvicorn api:app` 会 ModuleNotFoundError")

        # 顶层 import 的所有第三方模块，必须都能在这份清单里找到对应包。
        # 用 ast 取真实 import，避免靠人肉维护清单（改代码忘了改清单就会漏）。
        import ast
        tree = ast.parse((HERE / "api.py").read_text(encoding="utf-8"))
        stdlib = {"__future__", "asyncio", "contextlib", "csv", "logging", "os",
                  "sqlite3", "threading", "time", "uuid", "datetime", "pathlib",
                  "typing", "urllib", "json", "re"}
        # schedule 是本仓库自己的模块（modules/credit/schedule.py），不是 PyPI 的
        # `schedule` 包。这个区别很要紧：两者同名，若哪天有人真的 import 了
        # 第三方 schedule 包，这里的 local 集合会让守卫失明——所以下面另外
        # 断言同目录下确实存在 schedule.py，把「同名」这件事锁死。
        local = {"credit_check", "logkit", "notify", "schedule"}
        assert (HERE / "schedule.py").is_file(), (
            "api.py 顶层 import 了 schedule，但它必须是本目录的 schedule.py；"
            "同名 PyPI 包没有本项目需要的接口")
        # 模块名 → pip 包名（大小写/下划线差异）
        alias = {"pydantic_core": "pydantic_core", "pil": "Pillow"}
        third = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    third.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                third.add(node.module.split(".")[0])
        third -= stdlib | local
        unknown = []
        for mod in sorted(third):
            key = alias.get(mod.lower(), mod.lower())
            if key not in pkgs:
                unknown.append(mod)
        assert not unknown, (
            f"api.py 顶层 import 了 {unknown}，但 requirements.txt 没列；"
            "从零重建环境后会 ImportError")

    def test_dev_requirements_cover_toplevel_imports(self):
        """CI 只装 requirements-dev.txt，它必须覆盖所有**顶层** import。

        踩过的坑：dev 依赖最初只有 pytest，而 credit_check.py:37 顶层
        `from PIL import Image`、api.py 顶层 `from fastapi import ...`。
        结果两种失败：
          - PIL 缺失 → 整个测试模块 collection 就 ERROR，CI 红；
          - fastapi 缺失 → TestApiContract 被 importorskip **静默跳过**，
            CI 绿但 API 零覆盖。后者更危险，必须靠这条断言拦住。
        """
        dev = (HERE / "requirements-dev.txt").read_text(encoding="utf-8")
        dev_pkgs = {ln.split("==")[0].strip().lower()
                    for ln in dev.splitlines()
                    if ln.strip() and not ln.strip().startswith("#") and "==" in ln}
        # 顶层 import 的第三方包名 → pip 包名
        need = {"pillow": "Pillow", "fastapi": "fastapi",
                "pydantic": "pydantic", "httpx": "httpx",
                "pyyaml": "PyYAML"}
        missing = [pip for key, pip in need.items() if key not in dev_pkgs]
        assert not missing, (
            f"requirements-dev.txt 缺少顶层依赖 {missing}；"
            "缺 fastapi 会让 TestApiContract 静默 skip，CI 全绿但零覆盖")
        # 也必须全部 == 锁定
        for ln in dev.splitlines():
            s = ln.strip()
            if s and not s.startswith("#"):
                assert "==" in s, f"dev 依赖未锁定版本: {s}"

    def test_no_module_level_importorskip_hides_coverage(self):
        """不允许用 importorskip 掩盖「依赖没装」——依赖应写进 dev 清单。

        importorskip 只该用于确实可能缺席的可选能力；本项目全部测试依赖
        都是 CI 该装的，静默跳过等于假绿。

        注：断言目标字符串用拼接构造，否则会匹配到本函数自身的源码。
        """
        src = (HERE / "test_regression.py").read_text(encoding="utf-8")
        needle = "importorskip(" + '"fastapi"'
        assert needle not in src, (
            "fastapi 已列入 requirements-dev.txt，不该再 importorskip；"
            "否则依赖装漏时会静默跳过而不是报错")

    def test_venv_is_gitignored(self):
        gi = (HERE / ".gitignore").read_text(encoding="utf-8")
        assert ".venv/" in gi

    def test_bat_and_logs_are_gitignored(self):
        """运行期产物不能入库：bat 内含 webhook 地址，logs 含运行数据。"""
        gi = (HERE / ".gitignore").read_text(encoding="utf-8")
        assert "run_credit_check.bat" in gi, "bat 内含 webhook，必须忽略"
        assert "logs/" in gi, "logs 目录必须忽略"

    def test_gitignore_excludes_credentials(self):
        gi = (HERE / ".gitignore").read_text(encoding="utf-8")
        assert ".env" in gi

    def test_gitignore_excludes_api_runtime_data(self):
        """FastAPI 的作业库是业务数据（含查询过的企业名称），不能入库。

        两个必须同时成立：
        1. `data/*` 存在——写成 `data/` 会让 git 不再进入该目录，
           后面的 `!data/.gitkeep` 例外永远不生效（实测踩过）；
        2. `!data/.gitkeep` 存在——否则新克隆没有 data/ 目录，api.py 启动时建库会多一层意外。

        第 3 条断言（占位文件真实存在）只在 **git 检出**里检查：
        容器镜像里 `data/` 是被 .dockerignore 刻意排除的（运行期走卷），
        镜像里也没有 .git，那条断言在那里没有意义。
        这不是放宽检查——CI 与本地都在 git 检出内跑，仍然会断言。
        """
        gi = (HERE / ".gitignore").read_text(encoding="utf-8")
        assert "data/*" in gi, (
            "必须是 data/* 而不是 data/；目录级排除会让 .gitkeep 例外失效")
        assert "!data/.gitkeep" in gi, "保留 data/ 占位，避免新克隆缺目录"
        if _in_git_checkout():
            assert (HERE / "data" / ".gitkeep").is_file(), "占位文件必须真的存在"

    def test_utf8_reconfigure_present(self):
        """两个入口脚本都必须把 stdout 切 UTF-8（Windows cp936 会乱码）。"""
        for name in ("credit_check.py", "notify.py"):
            src = (HERE / name).read_text(encoding="utf-8")
            assert 'reconfigure(encoding="utf-8"' in src, f"{name} 缺少 UTF-8 重配置"


# ---------------------------------------------------------------- 计划任务脚本

#: Windows PowerShell 5.1 对无 BOM 的 UTF-8 按 GBK 解码，中文与 here-string
#: 会连环解析失败。这个坑在本项目里已经踩了三次（每次 edit 工具回写都会剥掉
#: BOM），所以必须固化成断言，不能靠人记住。
PS1 = HERE / "install_schedule_task.ps1"
BAT = HERE / "run_credit_check.bat"
MONTHLY_BAT = HERE / "run_credit_check_monthly.bat"


class TestScheduleScript:
    def test_ps1_has_utf8_bom(self):
        assert PS1.exists(), "缺少 install_schedule_task.ps1"
        raw = PS1.read_bytes()
        assert raw[:3] == b"\xef\xbb\xbf", (
            "install_schedule_task.ps1 必须存为 UTF-8 with BOM；"
            "用 edit/write 工具改过之后要补回来（见脚本头注释第 3 条）"
        )

    def test_ps1_has_no_escaped_quote_set(self):
        r"""不许出现 `set `"X=...`"` 这类被单引号包住的 shell 转义残留。

        根因：在单引号字符串里写 `" 不会转义，PowerShell 原样输出反引号，
        导致生成的 .bat 里出现 set `"PY=...`" 这样的废行。
        """
        src = PS1.read_text(encoding="utf-8-sig")
        assert 'set `"' not in src, "单引号里写 `\" 会原样输出反引号，改用 here-string"

    def _bat_template(self) -> str:
        """取出 $tpl = @' ... '@ 之间的 .bat 模板正文。"""
        src = PS1.read_text(encoding="utf-8-sig")
        start = src.find("$tpl = @'")
        end = src.find("'@", start)
        assert start > 0 and end > start, "找不到 $tpl here-string"
        return src[start:end]

    def test_ps1_avoids_wmic(self):
        """wmic 在 Win11 24H2 已弃用，本机实测返回空值。"""
        tpl = self._bat_template()
        assert "wmic os get localdatetime /value" not in tpl, \
            ".bat 模板不能调用 wmic（返回空值），改用 powershell Get-Date"

    def test_bat_template_exit_code_semantics(self):
        """bat 模板必须只把 notify 的 1 当失败，10 不算失败。"""
        tpl = self._bat_template()
        assert 'if "%RC_NOTIFY%"=="1" exit /b 1' in tpl
        assert 'if not "%RC_CHECK%"=="0" exit /b 1' in tpl

    def test_bat_template_creates_logs_dir(self):
        """首次运行时 logs\\ 可能不存在，bat 必须自己建。"""
        tpl = self._bat_template()
        assert 'mkdir "%DIR%logs"' in tpl

    def _ps_function_body(self, name: str) -> str:
        """取出 PowerShell 函数体（到第一个顶格 `}` 为止）。

        注意取的是「函数定义全文」，不含调用方；需要跨层看逻辑时用
        `_ps_call_chain`。早期测试直接把函数体切片后断言，一旦把写文件
        逻辑抽成 Write-BatFile 这类小函数，切片就取不到关键字而误报
        ——抽出这个 helper 就是为了让断言跟着**调用链**走，而不是跟着
        某一次代码排版走。
        """
        src = PS1.read_text(encoding="utf-8-sig")
        start = src.find(f"function {name}")
        assert start >= 0, f"找不到 function {name}"
        body = src[start:]
        end = body.find("\n}\n")
        return body[: end + 3] if end >= 0 else body

    def _ps_call_chain(self, name: str, depth: int = 3) -> str:
        """把函数体与它直接调用的本文件内其它函数体拼起来（限定深度）。

        用来断言「A 必须经由 B 使用 C」这类跨层约束：
        New-RunnerBat → Write-BatFile → Build-BatContent。
        只跟进**本文件真的定义过**的函数：正则会把 New-Object、Set-Content
        这类 cmdlet 也匹配成 `Xxx-Yyy` 形状，不筛定义会直接 KeyError。
        """
        src = PS1.read_text(encoding="utf-8-sig")
        defined = set(re.findall(r"^function\s+([A-Za-z][\w-]*)", src, re.M))
        seen: list[str] = []
        frontier = [name]
        for _ in range(depth):
            nxt: list[str] = []
            for fn in frontier:
                if fn in seen or fn not in defined:
                    continue
                seen.append(fn)
                body = self._ps_function_body(fn)
                for callee in re.findall(r"\b([A-Z][A-Za-z]+-[A-Za-z]+)\b", body):
                    if callee in defined and callee not in seen:
                        nxt.append(callee)
            frontier = nxt
        return "\n".join(self._ps_function_body(f) for f in seen)

    def test_new_runner_bat_writes_bom_free_crlf(self):
        r"""New-RunnerBat（经其调用链）必须以 UTF-8 无 BOM + 全 CRLF 写 .bat。

        这里**不能**依赖 run_credit_check.bat 这个生成物：它在 .gitignore 里
        （含 webhook 地址，故意不入库），CI 干净检出时不存在。早先的写法是
        `if not BAT.exists(): pytest.skip(...)`，结果 CI 上必然出现 1 个 skip，
        而 ci.yml 明确禁止 skip（防「依赖漏装导致假绿」）→ 首次 CI 必红。
        所以改为直接断言写入逻辑本身；生成物存在时再补一道字节级校验。

        Windows 版与月度版两个 .bat 都经同一 Write-BatFile 生成，
        因此这里断言调用链而非单个函数体。
        """
        chain = self._ps_call_chain("New-RunnerBat")
        assert "UTF8Encoding $false" in chain, \
            ".bat 必须用 UTF8Encoding($false) 写，带 BOM 会让 @echo off 前多出字符"
        assert '-replace "`r?`n", "`r`n"' in chain, \
            "写入前必须把行尾统一成 CRLF（cmd 对裸 LF 处理不一致）"

        # 生成物存在时（本机跑过安装脚本）再验实际字节。
        for path in (BAT, MONTHLY_BAT):
            if not path.exists():
                continue
            raw = path.read_bytes()
            assert not raw.startswith(b"\xef\xbb\xbf"), \
                f"{path.name} 带 BOM 会让 @echo off 前多字符"
            text = raw.decode("utf-8")
            assert "\n" in text
            assert text.count("\n") == text.count("\r\n"), f"{path.name} 存在裸 LF"

    def test_dryrun_and_install_share_builder(self):
        """DryRun 预览与 Install 必须共用 Build-BatContent，否则预览会骗人。

        改成断言调用链：New-RunnerBat 不再直接调用 Build-BatContent
        （中间隔了 Write-BatFile 以支持 daily/monthly 两份产物），
        只要链上仍然只有**一个**生成真源即可。
        """
        src = PS1.read_text(encoding="utf-8-sig")
        assert "function Build-BatContent" in src
        assert src.count("$tpl = @'") == 1, \
            ".bat 模板只能有一份真源，出现多处 here-string 说明有人又手拼了一版"

        new_runner_chain = self._ps_call_chain("New-RunnerBat")
        assert "Build-BatContent" in new_runner_chain, \
            "New-RunnerBat 调用链里没有 Build-BatContent，说明绕开真源另拼了 .bat"

        dry = self._ps_function_body("Invoke-DryRun")
        assert "Build-BatContent" in dry, "DryRun 未复用 Build-BatContent"

# ---------------------------------------------------------------- 推送层


class TestDotenv:
    """.env 解析：用户要「自己填 key」，这条路必须可靠且不覆盖真实环境变量。"""

    def test_parses_basic_and_strips_quotes(self, tmp_path, monkeypatch):
        from notify import load_dotenv
        p = tmp_path / ".env"
        p.write_text(
            "# comment\n"
            "\n"
            "SMTP_HOST=smtp.qq.com\n"
            "SMTP_USER=\"me@qq.com\"\n"
            "SMTP_PASS=\'secret16chars\'\n"
            "export EXTRA=1\n",
            encoding="utf-8")
        for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "EXTRA"):
            monkeypatch.delenv(k, raising=False)
        applied = load_dotenv(p)
        assert applied["SMTP_HOST"] == "smtp.qq.com"
        assert applied["SMTP_USER"] == "me@qq.com", "双引号应被剥离"
        assert applied["SMTP_PASS"] == "secret16chars", "单引号应被剥离"
        assert applied["EXTRA"] == "1", "export 前缀应被兼容"

    def test_does_not_override_existing_env(self, tmp_path, monkeypatch):
        from notify import load_dotenv
        p = tmp_path / ".env"
        p.write_text("SMTP_HOST=from-file\n", encoding="utf-8")
        monkeypatch.setenv("SMTP_HOST", "from-env")
        applied = load_dotenv(p)
        assert applied == {}, "已存在的环境变量不应被 .env 覆盖"
        import os
        assert os.environ["SMTP_HOST"] == "from-env"

    def test_missing_file_is_silent(self, tmp_path):
        from notify import load_dotenv
        assert load_dotenv(tmp_path / "nope.env") == {}

    def test_env_is_gitignored(self):
        """凭据文件绝不能入库。"""
        gi = (HERE / ".gitignore").read_text(encoding="utf-8")
        assert ".env" in gi
        assert ".env.example" not in gi, ".env.example 是模板，应当入库"

    def test_default_env_path_is_script_dir_not_cwd(self):
        """`.env` 必须按**脚本所在目录**解析，不能按 cwd。

        计划任务从任意目录（甚至 C:\\Windows）启动 bat；若按 cwd 找 .env，
        用户配好的授权码会被静默忽略 → 邮件永远发不出去，且不报配置错，
        只报一句无关的发送失败，极难排查。
        """
        import inspect

        import notify
        src = inspect.getsource(notify.load_dotenv)
        assert "__file__" in src and "resolve().parent" in src, (
            ".env 默认路径必须锚定脚本目录（__file__），不能依赖 cwd")


class TestMailHtml:
    """QQ/163 等客户端对纯文本邮件渲染很差，必须带 HTML 正文。"""

    def _md(self):
        from notify import build_report
        rows = [
            {"输入名称": "甲有限公司", "匹配名称": "甲有限公司",
             "统一社会信用代码": "91110000AAAAAAAA1X", "登记状态": "吊销",
             "判定": "alert"},
            {"输入名称": "乙有限公司", "匹配名称": "乙有限公司",
             "统一社会信用代码": "91110000BBBBBBBB2X", "登记状态": "存续",
             "判定": "ok"},
        ]
        return build_report(rows)[1]

    def test_table_and_escaping(self):
        from notify import md_to_html
        h = md_to_html(self._md())
        assert "<table" in h and "</table>" in h
        assert "甲有限公司" in h
        assert "91110000AAAAAAAA1X" in h

    def test_alert_verdict_is_colored(self):
        from notify import md_to_html
        h = md_to_html(self._md())
        assert "#cf222e" in h, "异常行应标红，主管才能一眼扫到"

    def test_escapes_html_injection(self):
        from notify import build_report, md_to_html
        rows = [{"输入名称": "<script>alert(1)</script>", "匹配名称": "",
                 "统一社会信用代码": "", "登记状态": "", "判定": "notfound"}]
        h = md_to_html(build_report(rows)[1])
        assert "<script>" not in h, "企业名里的尖括号必须转义"
        assert "&lt;script&gt;" in h

    def test_bold_becomes_strong(self):
        from notify import md_to_html
        h = md_to_html("**巡检时间**：x")
        assert "<strong>巡检时间</strong>" in h
        assert "**" not in h

    def test_send_mail_picks_ssl_for_465(self, monkeypatch):
        """465 必须走 SMTP_SSL——用普通 SMTP 连 QQ 会直接卡死到超时。"""
        import notify
        used = {}

        class FakeSSL:
            def __init__(self, host, port, timeout=None):
                used["kind"] = "ssl"
                used["host"], used["port"] = host, port
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def login(self, u, p): used["login"] = (u, p)
            def send_message(self, m): used["sent"] = True

        class FakePlain(FakeSSL):
            def __init__(self, host, port, timeout=None):
                super().__init__(host, port, timeout)
                used["kind"] = "plain"
            def starttls(self): used["tls"] = True

        monkeypatch.setattr(notify.smtplib, "SMTP_SSL", FakeSSL)
        monkeypatch.setattr(notify.smtplib, "SMTP", FakePlain)

        notify.send_mail("smtp.qq.com", 465, "u@qq.com", "pw", ["a@b.c"],
                         "t", "body", True)
        assert used["kind"] == "ssl", "465 端口必须用 SMTP_SSL"
        assert used["port"] == 465
        assert used["login"] == ("u@qq.com", "pw")
        assert used.get("sent") is True

    def test_send_mail_uses_starttls_on_587(self, monkeypatch):
        import notify
        used = {}

        class FakePlain:
            def __init__(self, host, port, timeout=None):
                used["port"] = port
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def starttls(self): used["tls"] = True
            def login(self, u, p): used["login"] = True
            def send_message(self, m): used["sent"] = True

        monkeypatch.setattr(notify.smtplib, "SMTP", FakePlain)
        notify.send_mail("smtp.x.com", 587, "u", "p", ["a@b.c"], "t", "b", True)
        assert used["tls"] is True, "587 应走 STARTTLS"
        assert used["sent"] is True


# ---------------------------------------------------------------- API 层


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """离线 TestClient：只验证路由/校验/存储，不触发浏览器。

    worker 被替换成不消费队列的哑实现——否则作业一提交就会真的去开
    Camoufox 联网抓取，那既慢又会打第三方站点。

    **故意不用 importorskip**：fastapi/httpx 已列入 requirements-dev.txt，
    依赖缺失就该直接报错。用 importorskip 会让 11 项 API 测试静默跳过，
    CI 全绿却零覆盖——比红更危险。
    """
    monkeypatch.setenv("CREDIT_DB", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("CREDIT_OUT", str(tmp_path / "results"))

    import importlib

    import api
    importlib.reload(api)

    class DummyWorker(api.Worker):
        def start(self): pass
        def stop(self): pass
        def submit(self, job_id): pass
        def _process(self, job_id): pass

    monkeypatch.setattr(api, "Worker", DummyWorker)
    from fastapi.testclient import TestClient
    with TestClient(api.app) as c:
        yield c


class TestStoreBackend:
    """存储后端选择与 DSN 解析。

    这些测试**不需要真 MySQL**：只验证「给什么形态的 CREDIT_DB，就走哪个后端」，
    以及 URL 拆解是否符合 pymysql 的期望。真有库时才跑的部分放在
    ``TestStoreMySQL``（用 ``WIPO_TEST_MYSQL_URL`` 之类的开关跳过）。
    """

    def test_path_selects_sqlite(self, tmp_path):
        import api
        s = api.Store(tmp_path / "jobs.db")
        assert s.backend == "sqlite"
        assert s.dsn is None
        assert (tmp_path / "jobs.db").is_file(), "构造时应当建库建表"

    @pytest.mark.parametrize("url,expect", [
        ("mysql://root:pw@127.0.0.1:3306/credit_check",
         {"host": "127.0.0.1", "port": 3306, "user": "root",
          "password": "pw", "database": "credit_check"}),
        ("mysql://root:@127.0.0.1:3307/credit_check",
         {"host": "127.0.0.1", "port": 3307, "user": "root",
          "password": "", "database": "credit_check"}),
        # 从别处拷来的 SQLAlchemy 风格串也要认
        ("mysql+pymysql://u:p@db.local/credit",
         {"host": "db.local", "port": 3306, "user": "u",
          "password": "p", "database": "credit"}),
        # 无端口 → 默认 3306
        ("mysql://root@localhost/credit",
         {"host": "localhost", "port": 3306, "user": "root",
          "password": "", "database": "credit"}),
    ])
    def test_parse_mysql_url(self, url, expect):
        import api
        assert api._parse_mysql_url(url) == expect

    @pytest.mark.parametrize("url", [
        "mysql://root@127.0.0.1:3306",          # 缺库名
        "mysql://root@127.0.0.1:3306/",         # 库名为空
    ])
    def test_parse_mysql_url_rejects_missing_database(self, url):
        import api
        with pytest.raises(ValueError, match="库名"):
            api._parse_mysql_url(url)

    def test_parse_mysql_url_rejects_unknown_scheme(self):
        import api
        with pytest.raises(ValueError, match="协议"):
            api._parse_mysql_url("postgresql://u@h/db")

    def test_url_percent_decoded(self):
        """密码里的特殊字符要 decode，否则会把 %40 当成字面量发给服务端。"""
        import api
        got = api._parse_mysql_url("mysql://u:p%40ss%2Fw@h:3306/db")
        assert got["password"] == "p@ss/w"

    def test_db_path_is_none_for_mysql(self, monkeypatch):
        """MySQL 模式下 DB_PATH 必须是 None。

        否则 ``Path("mysql://...")`` 会得到 ``mysql:\\root:@host\\db`` 这种反斜杠
        垃圾值，被日志和 healthcheck 拿去 stat 会产生误导性输出。
        """
        import importlib
        monkeypatch.setenv("CREDIT_DB", "mysql://root:@127.0.0.1:3307/credit_check")
        import api
        reloaded = importlib.reload(api)
        try:
            assert reloaded.DB_PATH is None
            assert reloaded.DB_TARGET.startswith("mysql://")
        finally:
            monkeypatch.undo()
            importlib.reload(api)


class TestApiContract:
    """业务接口契约。所有接口都在 /api 前缀下（api.py 的 APIRouter(prefix="/api")）。"""

    def test_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["db"] is True

    def test_create_job_dedupes_and_strips(self, client):
        r = client.post("/api/jobs", json={"names": [
            "甲有限公司", "甲有限公司", "   ", "#注释", "乙有限公司"]})
        assert r.status_code == 202
        assert r.json()["total"] == 2, "重复名与空行应在入库前清洗"

    @pytest.mark.parametrize("payload", [
        {"names": []},
        {"names": ["   ", "#注释"]},
        {"names": [f"公司{i}" for i in range(201)]},
        {"names": ["甲"], "min_delay": 10, "max_delay": 1},
        {"names": ["甲"], "min_delay": -1},
    ])
    def test_rejects_bad_payload(self, client, payload):
        assert client.post("/api/jobs", json=payload).status_code == 422

    def test_get_unknown_job_404(self, client):
        assert client.get("/api/jobs/job_nope").status_code == 404
        assert client.get("/api/jobs/job_nope/results").status_code == 404
        assert client.get("/api/jobs/job_nope/csv").status_code == 404

    def test_csv_before_done_is_409(self, client):
        jid = client.post("/api/jobs", json={"names": ["甲"]}).json()["job_id"]
        assert client.get(f"/api/jobs/{jid}/csv").status_code == 409

    def test_cancel_only_when_queued(self, client):
        jid = client.post("/api/jobs", json={"names": ["甲"]}).json()["job_id"]
        # DummyWorker 不改 state，仍是 queued
        assert client.delete(f"/api/jobs/{jid}").status_code == 200
        assert client.get(f"/api/jobs/{jid}").json()["state"] == "cancelled"
        assert client.delete(f"/api/jobs/{jid}").status_code == 409

    def test_openapi_schema_generates(self, client):
        """文档可用性本身是需求的一部分（用户点名要接口文档）。"""
        r = client.get("/openapi.json")
        assert r.status_code == 200
        paths = r.json()["paths"]
        for p in ("/api/health", "/api/jobs", "/api/jobs/{job_id}",
                  "/api/jobs/{job_id}/results", "/api/jobs/{job_id}/csv"):
            assert p in paths, f"缺少路由 {p}"

    def test_no_api_route_is_registered_below_root(self, client):
        """所有业务路由都必须在 /api 下。

        反向断言：openapi.json 里只要还有不带 /api 前缀的业务路径，就说明有人
        漏挂了 router——那会重新制造「页面路径被接口抢占」的老问题。
        """
        paths = client.get("/openapi.json").json()["paths"]
        offenders = [p for p in paths if not p.startswith("/api/")]
        assert not offenders, f"以下路由没挂 /api 前缀：{offenders}"

    def test_legacy_unprefixed_paths_are_not_api(self, client):
        """旧的无前缀路径不能再被接口接走。

        这是 P0 回归守卫：前端页面路径就是 /jobs 与 /jobs/<id>，
        若后端仍在这两个路径上答 JSON，浏览器直接打开/刷新页面会看到
        一个 JSON 查看器而不是控制台。

        需要 dist：无产物时 SPA 兜底没挂载，/health 理所当然答 JSON——
        那不是缺陷而是「前端未构建」的环境状态，归入 _require_dist()
        而不是让 CI 红（Linux runner 不提交 dist，曾据此误判回归）。
        """
        require_dist()
        for p in ("/health", "/jobs"):
            r = client.get(p)
            ct = r.headers.get("content-type", "")
            assert "application/json" not in ct, \
                f"{p} 仍在答 JSON（{ct}），接口未迁到 /api 下"

    def test_openapi_responses_carry_concrete_schemas(self, client):
        """每个 JSON 响应都必须有具体 schema，不能是 additionalProperties: true。

        现状背景：`/api/health` 与 `DELETE /api/jobs/{id}` 早期只写了 `-> dict`，
        FastAPI 只能生成 `additionalProperties: true`，用 openapi-typescript
        之类的工具生成前端类型时得到 `{[key: string]: unknown}` —— 字段名靠猜，
        改字段也不会报类型错。这三条断言把「可机读」钉在回报里，
        而不是靠人肉肉眼读 /docs。
        """
        spec = client.get("/openapi.json").json()
        schemas = spec["components"]["schemas"]

        # 1) /api/health 有具名模型
        health_ref = spec["paths"]["/api/health"]["get"]["responses"]["200"][
            "content"]["application/json"]["schema"]
        assert health_ref == {"$ref": "#/components/schemas/HealthResponse"}, \
            f"/api/health 响应应指向具名模型，实际 {health_ref}"
        hp = schemas["HealthResponse"]["properties"]
        for k in ("status", "db", "queued", "time"):
            assert k in hp, f"HealthResponse 缺字段 {k}"
        assert schemas["HealthResponse"]["required"] == \
            ["status", "db", "queued", "time"]

        # 2) DELETE /api/jobs/{id} 有具名模型，且 state 是字面量
        cancel_ref = spec["paths"]["/api/jobs/{job_id}"]["delete"]["responses"]["200"][
            "content"]["application/json"]["schema"]
        assert cancel_ref == {"$ref": "#/components/schemas/CancelResponse"}, \
            f"DELETE /api/jobs/{{id}} 响应应指向具名模型，实际 {cancel_ref}"
        cstate = schemas["CancelResponse"]["properties"]["state"]
        # 单值 Literal 在 Pydantic 2 里序列化成 const，多值才是 enum
        assert cstate.get("const") == "cancelled" or \
            cstate.get("enum") == ["cancelled"], \
            "取消成功只会返回 cancelled，字面量要钉住"

        # 3) CSV 端点必须声明 text/csv + binary，否则前端会当成 JSON 解析
        csv_content = spec["paths"]["/api/jobs/{job_id}/csv"]["get"]["responses"][
            "200"]["content"]
        assert "text/csv" in csv_content, \
            f"CSV 端点 content 应含 text/csv，实际 {list(csv_content)}"
        assert csv_content["text/csv"]["schema"].get("format") == "binary", \
            "CSV 响应应声明 format=binary"

    def test_openapi_has_no_opaque_response_schema(self, client):
        """全量扫描：任何 2xx JSON 响应都不许是裸 object。

        这条是上面那条的兜底——以后新增路由若又写成 `-> dict`，
        即使没人记得来看上面那条断言，这条也会红。
        """
        spec = client.get("/openapi.json").json()
        opaque = []
        for path, ops in spec["paths"].items():
            for method, op in ops.items():
                if method not in ("get", "post", "delete", "put", "patch"):
                    continue
                for code, resp in op.get("responses", {}).items():
                    if not code.startswith("2"):
                        continue
                    for mime, body in resp.get("content", {}).items():
                        if "json" not in mime:
                            continue
                        sch = body.get("schema") or {}
                        # 具名引用 / 数组 / 空 schema 之外，裸 object 都算不透明
                        if sch.get("type") == "object" and \
                                sch.get("additionalProperties") is True:
                            opaque.append(f"{method.upper()} {path} → {code}")
        assert not opaque, "以下 2xx JSON 响应缺少具名 schema：\n  " + \
            "\n  ".join(opaque)

    def test_csv_endpoint_returns_real_csv_bytes(self, client, tmp_path):
        """CSV 端点要真的回 text/csv，不是被 JSONResponse 包一层。"""
        jid = client.post("/api/jobs", json={"names": ["甲有限公司"]}).json()["job_id"]
        # 直接造一份已完成作业的 CSV，绕过 worker
        p = tmp_path / f"{jid}.csv"
        p.write_text("输入名称,判定\n甲有限公司,ok\n", encoding="utf-8-sig")
        client.app.state.store.set_state(jid, "done", csv_path=str(p))
        r = client.get(f"/api/jobs/{jid}/csv")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "甲有限公司" in r.content.decode("utf-8-sig")


def require_dist():
    """需要真实 dist 的用例先调这个；无产物时显式 xfail 而非静默 skip。

    静默 skip 的问题：托管链路是聚合系统的核心链路，dist 缺失时
    守卫集体蒸发，CI 看到的还是全绿——缺陷恰好在这种「环境退化」
    里活下来。改法：无 dist 时标记 xfail（在报告中可见），但继续
    跑断言——断言失败同样是 xfail，不会误伤；有 dist 时正常断言。
    """
    import api
    if not (api.DIST_DIR / "index.html").is_file():
        pytest.xfail("frontend/dist 未构建（npm run build）——本用例需要真实产物")


class TestFrontendHosting:
    """前端静态托管契约。

    背景：`_mount_frontend` 挂在文件末尾，靠 Starlette 的顺序匹配保证
    「接口优先、前端兜底」。这个顺序极其容易在后续加路由时被破坏——
    一旦有人把挂载挪到 app = FastAPI(...) 之后，/api/health、/api/jobs 会全被
    兜底接走并返回 HTML，而单元测试若不断言它们就一路全绿。

    另一半（更隐蔽）是路径撞车：接口若不带 /api 前缀，就会和 SPA 页面路径
    /jobs、/jobs/<id> 同名，浏览器直接打开这些页面时拿到的是接口 JSON。
    """

    def _skip_if_no_dist(self):
        import api
        if not (api.DIST_DIR / "index.html").is_file():
            pytest.skip("frontend/dist 未构建（npm run build）")

    def _require_dist(self, client):
        """需要真实 dist 的用例先调这个；无产物时显式 xfail 而非静默 skip。"""
        require_dist()

    def test_api_paths_are_not_swallowed(self, client):
        """不依赖 dist：/api 前缀不被兜底吞掉是路由注册顺序问题。"""
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")
        r = client.get("/api/jobs")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_page_routes_are_html_not_json(self, client):
        """页面路径必须回 HTML。

        P0 回归守卫：修复前 GET /jobs 回的是 `[]`（application/json），
        GET /jobs/<id> 回 404 JSON，浏览器里「作业列表」「作业详情」两个
        核心页面等于打不开——只是从站内点进去（不触发服务端请求）才看得到。
        """
        self._require_dist(client)
        for path in ("/jobs", "/jobs/job_20261018_161154_4e8cf8", "/overview"):
            r = client.get(path)
            assert r.status_code == 200, f"{path} 应 200，实际 {r.status_code}"
            ct = r.headers.get("content-type", "")
            assert "text/html" in ct, f"{path} 应回 HTML，实际 {ct}"

    def test_spa_deep_link_falls_back_to_index(self, client):
        self._require_dist(client)
        for path in ("/overview", "/submit", "/jobs", "/no-such-page",
                     "/jobs/job_not_exists_at_all"):
            r = client.get(path)
            # 全是纯前端深链（接口已迁到 /api，不再有任何页面路径与接口同名）
            assert r.status_code == 200, f"{path} 应 200，实际 {r.status_code}"

    def test_registered_job_route_wins_over_fallback(self, client):
        """假 id 必须由接口答 404，不能被兜底成 200——否则前端分不清
        「作业不存在」和「页面写错」。不依赖 dist：这验证的是路由优先级。"""
        r = client.get("/api/jobs/job_definitely_not_exists")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/json")

    def test_fallback_is_get_only(self, client):
        """POST 不能被兜底接走——否则提交作业会静默返回 HTML。
        不依赖 dist：验证的是 202 路由语义。"""
        r = client.post("/api/jobs", json={"names": ["甲"]})
        assert r.status_code == 202

    def test_path_traversal_is_rejected(self, client):
        self._require_dist(client)
        for path in ("/%2e%2e/%2e%2e/etc/passwd",
                     "/%2e%2e/%2e%2e/secrets.json",
                     "/assets/%2e%2e/%2e%2e/api.py"):
            r = client.get(path)
            assert r.status_code == 404, f"{path} 应 404，实际 {r.status_code}"

    def test_docs_and_openapi_still_work(self, client):
        """不依赖 dist：openapi/docs 是 FastAPI 自带路由。"""
        assert client.get("/openapi.json").status_code == 200
        assert client.get("/docs").status_code == 200



class TestSampleCases:
    """50 条样例的完整性约束——它是交付物，不是随手写的名单。"""

    def test_cases_csv_is_wellformed(self):
        p = HERE / "samples" / "cases.csv"
        if not p.is_file():
            pytest.skip("samples/cases.csv 尚未生成")
        with p.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) >= 50, f"样例应不少于 50 条，实际 {len(rows)}"
        for r in rows:
            assert r["输入名称"].strip()
            assert r["预期判定"] in {"ok", "alert", "notfound", "unknown"}

    def test_cases_cover_all_verdicts(self):
        p = HERE / "samples" / "cases.csv"
        if not p.is_file():
            pytest.skip("samples/cases.csv 尚未生成")
        with p.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        verdicts = {r["预期判定"] for r in rows}
        assert "ok" in verdicts, "必须有正常主体作对照"
        assert "notfound" in verdicts, "必须有已注销/未收录主体"
        cats = {r["类别"] for r in rows}
        assert len(cats) >= 5, f"类别覆盖不足：{cats}"

    def test_name_list_matches_cases(self):
        """companies50.txt 必须与 cases.csv 一一对应，否则实跑结果对不上预期。"""
        c = HERE / "samples" / "companies50.txt"
        k = HERE / "samples" / "cases.csv"
        if not (c.is_file() and k.is_file()):
            pytest.skip("样例尚未生成")
        names = [x for x in c.read_text(encoding="utf-8").splitlines()
                 if x.strip() and not x.startswith("#")]
        with k.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert len(names) == len(rows)
        assert [n.strip() for n in names] == [r["输入名称"].strip() for r in rows]

    def test_expected_matches_actual(self):
        """cases.csv 的「预期判定」必须与 expected_actual.csv 的实测「判定」逐行一致。

        README §10 承诺「50/50 一致」。若只改 cases.csv 的预期而不重跑实测，
        这句话立刻变成假的——这正是文档最容易腐化的地方，用断言钉住。
        重跑后两者都要更新，更新不同步会红。
        """
        k = HERE / "samples" / "cases.csv"
        a = HERE / "samples" / "expected_actual.csv"
        if not (k.is_file() and a.is_file()):
            pytest.skip("样例尚未生成")
        with k.open(encoding="utf-8-sig") as f:
            cases = list(csv.DictReader(f))
        with a.open(encoding="utf-8-sig") as f:
            actual = list(csv.DictReader(f))
        assert len(cases) == len(actual), (
            f"cases.csv {len(cases)} 条 vs expected_actual.csv {len(actual)} 条")
        mismatched = [
            (c["输入名称"], c["预期判定"], r["判定"])
            for c, r in zip(cases, actual)
            if c["预期判定"] != r["判定"]
        ]
        assert not mismatched, f"预期与实测不一致：{mismatched}"

    def test_近似名称绝不判成正常(self):
        """C 组（名称近似陷阱）只能落到 unknown / notfound，绝不能是 ok。

        这是防假阳性的核心不变量：把别家的状态当成输入企业的状态，
        对销售来说比「查不到」危险得多。50 条实测里 9 条 unknown、1 条 notfound，
        两者都不代表正常、都会进提醒，所以这个边界可以变，
        但「不许判成 ok」不可以变。
        """
        k = HERE / "samples" / "cases.csv"
        if not k.is_file():
            pytest.skip("samples/cases.csv 尚未生成")
        with k.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        near = [r for r in rows if r["类别"].startswith("C")]
        assert near, "样例必须包含名称近似陷阱组"
        bad = [r["输入名称"] for r in near if r["预期判定"] not in {"unknown", "notfound"}]
        assert not bad, f"名称近似却判成正常/异常（假阳性风险）：{bad}"

    def test_危机但存续不得误报为异常(self):
        """G 组（出过事但工商登记仍存续）必须是 ok。

        乐视/金立/辉山/康得新/熊猫互娱/ofo 都有大新闻，但法人主体没注销。
        把它们报成 alert 就是假阳性——工具只看工商登记状态，不看经营风险。
        """
        k = HERE / "samples" / "cases.csv"
        if not k.is_file():
            pytest.skip("samples/cases.csv 尚未生成")
        with k.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        crisis = [r for r in rows if r["类别"].startswith("G")]
        assert crisis, "样例必须包含「危机但主体存续」组"
        bad = [r["输入名称"] for r in crisis if r["预期判定"] != "ok"]
        assert not bad, f"主体存续却被判成异常（假阳性）：{bad}"


# ---------------------------------------------------------------- 服务自检层
# 全部离线：真实 I/O（HTTP / TCP / 浏览器版本）一律用探针覆盖。
# 这样 CI 不联网也能跑，本地也不依赖「今天信用中国能不能连上」。


class TestHealthcheckProbes:
    def test_python_version_gate(self):
        import healthcheck as hc
        assert hc.check_python((3, 10))[0] is True
        ok, detail = hc.check_python((99, 0))
        assert ok is False and "99" in detail

    def test_venv_detection(self, tmp_path):
        """venv 与 base 相同即判定「不在 venv 内」。"""
        import healthcheck as hc
        assert hc.check_venv(prefix="/a/.venv", base_prefix="/usr")[0] is True
        ok, detail = hc.check_venv(prefix="/usr", base_prefix="/usr")
        assert ok is False and "不在 venv" in detail

    def test_missing_dependency_is_named(self):
        import healthcheck as hc
        ok, detail = hc.check_deps(
            modules={"ddddocr": "ddddocr", "camoufox": "camoufox"},
            finder=lambda name: None if name == "ddddocr" else object())
        assert ok is False
        assert "ddddocr" in detail
        assert "camoufox" not in detail.split("：")[1]

    def test_dep_finder_exception_counts_as_missing(self):
        """find_spec 对坏包会抛 ImportError/ValueError，必须算缺失而不是崩掉。"""
        import healthcheck as hc

        def boom(name):
            raise ValueError("bad spec")

        ok, detail = hc.check_deps(modules={"x": "x"}, finder=boom)
        assert ok is False and "x(x)" in detail

    def test_browser_not_installed(self):
        """pip 装了 camoufox 但没 fetch 浏览器时，必须报『未就绪』。"""
        import healthcheck as hc

        def not_installed():
            raise RuntimeError("CamoufoxNotInstalled")

        ok, detail = hc.check_browser(verstr_fn=not_installed)
        assert ok is False
        assert "未就绪" in detail and "CamoufoxNotInstalled" in detail

    def test_browser_empty_version(self):
        import healthcheck as hc
        ok, detail = hc.check_browser(verstr_fn=lambda: "")
        assert ok is False and "fetch" in detail

    def test_site_http_403_still_means_online(self):
        """403/412 是反爬拒绝，不是网络故障；把它判成失败会造成日常误报。

        实测：本机直连信用中国首页拿到 HTTP 412，站点明明是好的。
        """
        import urllib.error

        import healthcheck as hc

        def opener(req):
            raise urllib.error.HTTPError(req.full_url, 412, "Precondition", {}, None)

        ok, detail = hc.check_site(opener=opener)
        assert ok is True and "412" in detail

    def test_site_connection_error_is_failure(self):
        import healthcheck as hc

        def opener(req):
            raise urllib.error.URLError("dns fail")

        ok, detail = hc.check_site(opener=opener)
        assert ok is False and "不可达" in detail

    def test_smtp_config_reports_each_missing_key(self):
        import healthcheck as hc
        ok, detail = hc.check_smtp_config(env={})
        assert ok is False
        for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
            assert k in detail
        ok2, _ = hc.check_smtp_config(
            env={"SMTP_HOST": "h", "SMTP_USER": "u", "SMTP_PASS": "p"})
        assert ok2 is True

    def test_recipients_fall_back_to_legacy_mail_to(self):
        """旧部署只有 MAIL_TO 时必须仍算「已配置」，否则升级即静默失联。"""
        import healthcheck as hc
        ok, detail = hc.check_recipients(env={"MAIL_TO": "old@x.com"})
        assert ok is True
        assert "业务=已配" in detail and "主管=已配" in detail
        ok2, detail2 = hc.check_recipients(env={})
        assert ok2 is False and "都为空" in detail2

    def test_smtp_tcp_uses_connector(self):
        import healthcheck as hc
        calls = []

        def connector(host, port, timeout):
            calls.append((host, port))

        ok, detail = hc.check_smtp_tcp("smtp.x.com", 465, connector=connector)
        assert ok is True and calls == [("smtp.x.com", 465)]
        assert "465" in detail

    def test_smtp_tcp_empty_host(self):
        import healthcheck as hc
        ok, detail = hc.check_smtp_tcp("", 465)
        assert ok is False and "SMTP_HOST 为空" in detail

    def test_name_list_reader_skips_comments_and_blanks(self, tmp_path):
        import healthcheck as hc
        p = tmp_path / "c.txt"
        p.write_text("# 注释\n\nA有限公司\n  B有限公司  \n", encoding="utf-8")
        assert hc.read_name_list(p) == ["A有限公司", "B有限公司"]

    def test_empty_name_list_is_fatal(self, tmp_path):
        """名单为空必须报致命：巡检会「成功」跑完 0 家并报全部正常。"""
        import healthcheck as hc
        p = tmp_path / "empty.txt"
        p.write_text("# 只有注释\n\n", encoding="utf-8")
        ok, detail = hc.check_companies(p)
        assert ok is False
        assert "名单为空" in detail and "全部正常" in detail

    def test_missing_name_list_is_fatal(self, tmp_path):
        import healthcheck as hc
        ok, detail = hc.check_companies(tmp_path / "nope.txt")
        assert ok is False and "不存在" in detail

    def test_writable_dir(self, tmp_path):
        import healthcheck as hc
        ok, detail = hc.check_writable([tmp_path / "a", tmp_path / "b"])
        assert ok is True
        assert (tmp_path / "a").is_dir()

    def test_unwritable_dir(self, tmp_path):
        """用一个「文件」当目录，必然不可写。"""
        import healthcheck as hc
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        ok, detail = hc.check_writable([blocker / "sub"])
        assert ok is False and "不可写" in detail

    def test_heartbeat_missing_is_ok(self, tmp_path):
        import healthcheck as hc
        ok, detail = hc.check_heartbeat(tmp_path / "none.json")
        assert ok is True and "首次" in detail

    def test_heartbeat_stale_is_failure(self, tmp_path):
        import json as _json
        from datetime import datetime, timedelta

        import healthcheck as hc
        p = tmp_path / "hb.json"
        old = datetime.now() - timedelta(hours=50)
        p.write_text(_json.dumps({"last_run_at": old.isoformat(timespec="seconds")}),
                     encoding="utf-8")
        ok, detail = hc.check_heartbeat(p, max_hours=36)
        assert ok is False
        assert "50" in detail or "49" in detail
        assert "禁用" in detail or "关机" in detail

    def test_heartbeat_fresh_is_ok(self, tmp_path):
        from datetime import datetime, timedelta

        import healthcheck as hc
        p = tmp_path / "hb.json"
        hc.write_heartbeat(p, ok=True, failed=0, warn=0,
                           now=datetime.now() - timedelta(hours=2))
        ok, detail = hc.check_heartbeat(p, max_hours=36)
        assert ok is True and "2" in detail

    def test_heartbeat_corrupt_is_failure(self, tmp_path):
        import healthcheck as hc
        p = tmp_path / "hb.json"
        p.write_text("{not json", encoding="utf-8")
        ok, detail = hc.check_heartbeat(p)
        assert ok is False and "损坏" in detail

    def test_heartbeat_naive_and_aware_never_raises(self, tmp_path):
        """naive/aware 混算会 TypeError；两种存法都必须能算出差值。"""
        import json as _json
        from datetime import datetime, timedelta

        import healthcheck as hc
        p = tmp_path / "hb.json"
        p.write_text(_json.dumps(
            {"last_run_at": (datetime.now() - timedelta(hours=1)).isoformat()}),
            encoding="utf-8")
        ok, _ = hc.check_heartbeat(p, max_hours=36)
        assert ok is True
        # 反过来：心跳带时区、now 不带
        aware = datetime.now().astimezone() - timedelta(hours=1)
        p.write_text(_json.dumps({"last_run_at": aware.isoformat(timespec="seconds")}),
                     encoding="utf-8")
        ok2, _ = hc.check_heartbeat(p, max_hours=36, now=datetime.now())
        assert ok2 is True

    def test_write_heartbeat_is_atomic_and_readable(self, tmp_path):
        import json as _json

        import healthcheck as hc
        p = tmp_path / "sub" / "hb.json"
        hc.write_heartbeat(p, ok=False, failed=2, warn=1)
        data = _json.loads(p.read_text(encoding="utf-8"))
        assert data["ok"] is False and data["failed"] == 2
        assert not (tmp_path / "sub" / "hb.tmp").exists(), "临时文件未清理"

    def test_jobs_db_absent_is_not_failure(self, tmp_path):
        """只用命令行时没有 jobs.db，不能因此报致命。"""
        import healthcheck as hc
        ok, detail = hc.check_jobs_db(tmp_path / "nope.db")
        assert ok is True and "未创建" in detail

    def test_jobs_db_corrupt_is_failure(self, tmp_path):
        import healthcheck as hc
        p = tmp_path / "bad.db"
        p.write_bytes(b"not a sqlite file at all" * 10)
        ok, detail = hc.check_jobs_db(p)
        assert ok is False and "不可读" in detail

    def test_jobs_db_mysql_unreachable_is_failure(self):
        """MySQL 模式下连不上必须是**失败**。

        这一条守的是最容易犯的错：把连接串当路径处理，
        ``Path("mysql://...")`` 得到废路径 → ``.is_file()`` 恒 False →
        报「作业库未创建（仅用命令行时正常）」→ 每日巡检把真实的
        数据库故障当成「没启用」放过去。
        """
        import healthcheck as hc
        ok, detail = hc.check_jobs_db(
            "mysql://root:@127.0.0.1:1/credit_check")  # 1 端口必不通
        assert ok is False, "连不上 MySQL 却被判成正常"
        assert "不可读" in detail

    def test_jobs_db_mysql_bad_host_is_failure(self):
        import healthcheck as hc
        ok, detail = hc.check_jobs_db(
            "mysql://root:@no-such-host.invalid:3306/credit_check")
        assert ok is False and "不可读" in detail


class TestHealthcheckAggregate:
    def _probes(self, tmp_path, **over):
        """一组全绿探针；用 over 覆盖单项以构造失败场景。

        venv 前缀固定注入一对不等值：本类测的是「12 项自检的判定与
        汇总逻辑」，不是运行环境本身——check_venv 在系统 Python 下
        必然 FAIL（那是它的职责，健康检查要能发现跑错解释器），
        CI runner 用系统解释器跑回归时会因此红 5 条。让探针可注入
        与 browser_verstr/site_opener 同款，环境归环境、逻辑归逻辑。
        """
        base = {
            "browser_verstr": lambda: "135.0.1",
            "site_opener": lambda req: _FakeResp(200),
            "smtp_connector": lambda h, p, t: None,
            "companies_reader": lambda p: ["A有限公司"],
            "venv_prefix": "/opt/project-venv",
            "venv_base_prefix": "/usr",
            "deps_finder": lambda mod: object(),
            "writable_dirs": [tmp_path],
            "jobs_db_path": tmp_path / "jobs.db",
            "heartbeat_path": tmp_path / "hb.json",
        }
        base.update(over)
        return base

    def _env(self):
        return {"SMTP_HOST": "smtp.x.com", "SMTP_PORT": "465",
                "SMTP_USER": "u@x.com", "SMTP_PASS": "p",
                "MAIL_TO_BUSINESS": "biz@x.com", "MAIL_TO_MANAGER": "mgr@x.com"}

    def test_all_green(self, tmp_path):
        import healthcheck as hc
        names = tmp_path / "c.txt"
        names.write_text("A有限公司\n", encoding="utf-8")
        results = hc.run_checks(env=self._env(), companies=names,
                                probes=self._probes(tmp_path))
        summary = hc.summarize(results)
        assert summary["total"] == 12
        assert summary["failed"] == 0, [r for r in results if not r.ok]
        assert hc.exit_code(results) == 0

    def test_venv_and_deps_probes_are_wired(self, tmp_path):
        """venv/deps 探针必须可注入且真被 run_checks 消费。

        背景：CI runner 用系统解释器跑回归，check_venv 按设计必 FAIL
        （prefix==base_prefix）——那不是产品缺陷，是「环境归环境、
        逻辑归逻辑」的边界。注入点若被后人删掉，这组测试会在任何
        机器上红，而不是只在 CI 上红，容易被误判成「CI 环境问题」绕过。

        deps 侧不能拿「系统解释器必缺 camoufox」当反证前提：CI 的
        tests job 本来就 pip install 了全部依赖，那台机器上 deps
        是齐的（ef31017 首跑实测翻车）。所以 deps 反证用注入
        「永远返回 None 的 finder」构造，任何环境都成立。
        """
        import healthcheck as hc
        names = tmp_path / "c.txt"
        names.write_text("A有限公司\n", encoding="utf-8")
        # venv 反证：系统解释器下不注入 venv 探针必 FAIL。
        probes = self._probes(tmp_path)
        probes.pop("venv_prefix"); probes.pop("venv_base_prefix")
        if sys.prefix == getattr(sys, "base_prefix", sys.prefix):
            results = hc.run_checks(env=self._env(), companies=names,
                                    probes=probes)
            by_id = {r.id: r for r in results}
            assert not by_id["venv"].ok, "系统解释器下 venv 项应 FAIL（注入点存在性的反证）"
        # deps 反证：注入空 finder（任何模块都找不到），deps 必 FAIL。
        probes = self._probes(tmp_path, deps_finder=lambda mod: None)
        results = hc.run_checks(env=self._env(), companies=names,
                                probes=probes)
        by_id = {r.id: r for r in results}
        assert not by_id["deps"].ok, "注入空 finder 后 deps 项应 FAIL"
        # 注入全绿探针后必须全绿——这在任何解释器下都成立。
        results = hc.run_checks(env=self._env(), companies=names,
                                probes=self._probes(tmp_path))
        failed = [r.id for r in results if not r.ok]
        assert not failed, failed

    def test_one_fatal_failure_sets_exit_1(self, tmp_path):
        import healthcheck as hc
        names = tmp_path / "c.txt"
        names.write_text("A有限公司\n", encoding="utf-8")
        probes = self._probes(
            tmp_path, site_opener=lambda req: (_ for _ in ()).throw(OSError("down")))
        results = hc.run_checks(env=self._env(), companies=names, probes=probes)
        summary = hc.summarize(results)
        assert summary["failed"] == 1
        assert "site" in summary["failed_ids"]
        assert hc.exit_code(results) == 1

    def test_warn_only_does_not_fail_unless_strict(self, tmp_path):
        import healthcheck as hc
        names = tmp_path / "c.txt"
        names.write_text("A有限公司\n", encoding="utf-8")
        env = self._env()
        env.pop("MAIL_TO_BUSINESS")
        env.pop("MAIL_TO_MANAGER")
        probes = self._probes(tmp_path, heartbeat_path=tmp_path / "none.json")
        results = hc.run_checks(env=env, companies=names, probes=probes)
        summary = hc.summarize(results)
        assert summary["failed"] == 0
        assert "recipients" in summary["warn_ids"]
        assert hc.exit_code(results) == 0
        assert hc.exit_code(results, strict=True) == 1

    def test_raising_probe_never_crashes_the_run(self, tmp_path):
        """任何探针抛异常都必须变成该项失败，而不是让自检整体崩掉。"""
        import healthcheck as hc
        names = tmp_path / "c.txt"
        names.write_text("A有限公司\n", encoding="utf-8")

        def explode(*a, **k):
            raise RuntimeError("boom")

        probes = self._probes(tmp_path, browser_verstr=explode,
                              site_opener=explode, smtp_connector=explode)
        results = hc.run_checks(env=self._env(), companies=names, probes=probes)
        assert len(results) == 12
        failed = {r.id for r in results if not r.ok}
        assert {"browser", "site", "smtp_tcp"} <= failed
        for r in results:
            if r.id in failed:
                assert r.detail, f"{r.id} 失败却没留 detail"

    def test_only_filter(self, tmp_path):
        import healthcheck as hc
        results = hc.run_checks(env=self._env(), companies=tmp_path / "x.txt",
                                only={"python"}, probes={})
        assert [r.id for r in results] == ["python"]

    def test_render_table_contains_summary(self, tmp_path):
        import healthcheck as hc
        names = tmp_path / "c.txt"
        names.write_text("A有限公司\n", encoding="utf-8")
        results = hc.run_checks(env=self._env(), companies=names,
                                probes=self._probes(tmp_path))
        text = hc.render_table(results)
        assert "合计 12 项" in text
        assert "致命失败 0" in text

    def test_main_writes_json_and_returns_code(self, tmp_path):
        """main 必须落 JSON 且退出码与汇总一致（供 .bat 判读）。"""
        import json as _json

        import healthcheck as hc
        out = tmp_path / "h.json"
        rc = hc.main.__wrapped__ if hasattr(hc.main, "__wrapped__") else None
        assert rc is None  # 未被装饰；下面用 monkeypatch 走真实入口
        names = tmp_path / "c.txt"
        names.write_text("A有限公司\n", encoding="utf-8")
        old_argv = sys.argv
        try:
            sys.argv = ["healthcheck.py", "--json", str(out),
                        "--companies", str(names),
                        "--only", "python"]
            rc = hc.main()
        finally:
            sys.argv = old_argv
        assert rc == 0
        data = _json.loads(out.read_text(encoding="utf-8"))
        assert data["summary"]["total"] == 1
        assert len(data["checks"]) == 1
        assert data["host"]

    def test_mark_run_writes_heartbeat(self, tmp_path, monkeypatch):
        import json as _json

        import healthcheck as hc
        hb = tmp_path / "hb.json"
        monkeypatch.setattr(hc, "HEARTBEAT_PATH", hb)
        names = tmp_path / "c.txt"
        names.write_text("A有限公司\n", encoding="utf-8")
        old_argv = sys.argv
        try:
            sys.argv = ["healthcheck.py", "--companies", str(names),
                        "--only", "python", "--mark-run"]
            hc.main()
        finally:
            sys.argv = old_argv
        assert _json.loads(hb.read_text(encoding="utf-8"))["ok"] is True


class _FakeResp:
    """urllib 响应的最小替身：只实现 urlopen 用到的协议。"""

    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ---------------------------------------------------------------- 双节奏推送层


class TestNotifyModes:
    def test_parse_addrs_tolerates_all_separators(self):
        """真实 .env 里出现过中文逗号与分号；静默丢收件人极难发现。"""
        import notify
        assert notify.parse_addrs("a@x.com, b@y.com") == ["a@x.com", "b@y.com"]
        assert notify.parse_addrs("a@x.com;b@y.com") == ["a@x.com", "b@y.com"]
        assert notify.parse_addrs("a@x.com，b@y.com") == ["a@x.com", "b@y.com"]
        assert notify.parse_addrs("a@x.com\nb@y.com") == ["a@x.com", "b@y.com"]
        assert notify.parse_addrs("") == []
        assert notify.parse_addrs(None) == []

    def test_resolve_recipients_per_mode(self):
        import notify
        env = {"MAIL_TO_BUSINESS": "biz@x.com", "MAIL_TO_MANAGER": "mgr@x.com"}
        assert notify.resolve_recipients("daily", env) == ["biz@x.com"]
        assert notify.resolve_recipients("monthly", env) == ["mgr@x.com"]

    def test_resolve_recipients_legacy_fallback(self):
        import notify
        env = {"MAIL_TO": "old@x.com"}
        assert notify.resolve_recipients("daily", env) == ["old@x.com"]
        assert notify.resolve_recipients("monthly", env) == ["old@x.com"]

    def test_dedicated_key_beats_legacy(self):
        import notify
        env = {"MAIL_TO": "old@x.com", "MAIL_TO_BUSINESS": "biz@x.com"}
        assert notify.resolve_recipients("daily", env) == ["biz@x.com"]

    def test_should_send_matrix(self):
        import notify
        need = [{"判定": "alert"}]
        bad = {"summary": {"failed": 1}}
        good = {"summary": {"failed": 0}}
        # 全绿不发：每天一封「全部正常」会被当垃圾邮件
        assert notify.should_send("daily", [], False, good) is False
        assert notify.should_send("monthly", [], False, good) is False
        # 有执照异常，两种节奏都发
        assert notify.should_send("daily", need, False, good) is True
        assert notify.should_send("monthly", need, False, good) is True
        # 服务自检致命失败只影响日报（月报不重复报服务健康）
        assert notify.should_send("daily", [], False, bad) is True
        assert notify.should_send("monthly", [], False, bad) is False
        # --always 强制
        assert notify.should_send("daily", [], True, good) is True
        assert notify.should_send("monthly", [], True, good) is True

    def test_should_send_without_health(self):
        import notify
        assert notify.should_send("daily", [], False, None) is False

    def test_prefixed_title(self):
        import notify
        assert notify.prefixed_title("X", "daily").startswith("[每日巡检]")
        assert notify.prefixed_title("X", "monthly").startswith("[月度巡检]")
        assert notify.prefixed_title("X", "nonsense") == "X"

    def test_health_section_lists_only_failures(self):
        import notify
        health = {"summary": {"total": 12, "passed": 10, "failed": 2, "warn": 0},
                  "checks": [
                      {"id": "python", "severity": "fatal", "ok": True, "detail": "3.13"},
                      {"id": "smtp_config", "severity": "fatal", "ok": False,
                       "detail": "缺少配置：SMTP_HOST"},
                      {"id": "companies", "severity": "fatal", "ok": False,
                       "detail": "名单为空"}]}
        rows = [{"输入名称": "A", "判定": "ok"}]
        _, md, _ = notify.build_report(rows, health=health)
        assert "服务自检异常" in md
        assert "邮件配置" in md and "企业名单" in md
        assert "3.13" not in md, "通过项不该出现在正文里"
        assert "10/12" in md

    def test_health_section_all_green_is_one_line(self):
        import notify
        health = {"summary": {"total": 12, "passed": 12, "failed": 0, "warn": 0},
                  "checks": []}
        _, md, _ = notify.build_report([{"输入名称": "A", "判定": "ok"}], health=health)
        assert "12/12 项通过" in md
        assert "服务自检异常" not in md

    def test_load_health_missing_or_corrupt(self, tmp_path):
        import notify
        assert notify.load_health(tmp_path / "nope.json") is None
        p = tmp_path / "bad.json"
        p.write_text("{oops", encoding="utf-8")
        assert notify.load_health(p) is None
        p.write_text('{"summary": {}}', encoding="utf-8")
        assert notify.load_health(p) == {"summary": {}}

    def test_recipients_env_names_are_documented(self):
        """.env.example 必须提到两个新键，否则用户不知道要配什么。"""
        src = (HERE / ".env.example").read_text(encoding="utf-8-sig")
        assert "MAIL_TO_BUSINESS" in src
        assert "MAIL_TO_MANAGER" in src


class TestNotifyMainModes:
    def _csv(self, tmp_path, verdict="alert"):
        import notify as _n
        p = tmp_path / "r.csv"
        with p.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            row = dict.fromkeys(CSV_FIELDS, "")
            row.update({"输入名称": "A", "判定": verdict, "登记状态": "吊销"})
            w.writerow(row)
        return p

    def test_no_recipients_returns_10_not_crash(self, tmp_path, monkeypatch):
        """全新部署（没配任何收件人）时，notify 仍应打印跳过、exit 10。"""
        import notify
        p = self._csv(tmp_path)
        monkeypatch.setattr(sys, "argv", ["notify.py", str(p)])
        for k in ("MAIL_TO", "MAIL_TO_BUSINESS", "MAIL_TO_MANAGER",
                  "SMTP_HOST", "CREDIT_WEBHOOK"):
            monkeypatch.delenv(k, raising=False)
        assert notify.main() == 10

    def test_mode_daily_selects_business_recipient(self, tmp_path, monkeypatch, caplog):
        """--mode daily 应把邮件发给 MAIL_TO_BUSINESS。

        真正发信会联网，所以这里让 send_mail 抛错：只要日志里出现
        「邮件发送成功 -> biz@x.com」就证明选对了人；抛错则证明**没有**
        发给 mgr。两者结合即验证了分流。
        """
        import notify
        p = self._csv(tmp_path)
        sent = {}

        def fake_mail(host, port, user, password, to_addrs, subject, md,
                      use_tls, use_ssl=None, timeout=30):
            sent["to"] = to_addrs
            sent["subject"] = subject

        monkeypatch.setattr(notify, "send_mail", fake_mail)
        monkeypatch.setattr(sys, "argv",
                            ["notify.py", str(p), "--mode", "daily",
                             "--smtp-host", "smtp.x.com", "--smtp-port", "465"])
        monkeypatch.setenv("MAIL_TO_BUSINESS", "biz@x.com")
        monkeypatch.setenv("MAIL_TO_MANAGER", "mgr@x.com")
        monkeypatch.delenv("CREDIT_WEBHOOK", raising=False)
        assert notify.main() == 0
        assert sent["to"] == ["biz@x.com"]
        assert sent["subject"].startswith("[每日巡检]")

    def test_mode_monthly_selects_manager(self, tmp_path, monkeypatch):
        import notify
        p = self._csv(tmp_path)
        sent = {}
        monkeypatch.setattr(
            notify, "send_mail",
            lambda host, port, user, pw, to_addrs, subject, md, tls,
            use_ssl=None, timeout=30: sent.update(to=to_addrs, subject=subject))
        monkeypatch.setattr(sys, "argv",
                            ["notify.py", str(p), "--mode", "monthly",
                             "--smtp-host", "smtp.x.com", "--smtp-port", "465"])
        monkeypatch.setenv("MAIL_TO_BUSINESS", "biz@x.com")
        monkeypatch.setenv("MAIL_TO_MANAGER", "mgr@x.com")
        monkeypatch.delenv("CREDIT_WEBHOOK", raising=False)
        assert notify.main() == 0
        assert sent["to"] == ["mgr@x.com"]
        assert sent["subject"].startswith("[月度巡检]")

    def test_always_flag_overrides_green(self, tmp_path, monkeypatch):
        import notify
        p = self._csv(tmp_path, verdict="ok")
        sent = {}
        monkeypatch.setattr(
            notify, "send_mail",
            lambda host, port, user, pw, to_addrs, subject, md, tls,
            use_ssl=None, timeout=30: sent.update(to=to_addrs))
        monkeypatch.setattr(sys, "argv",
                            ["notify.py", str(p), "--always",
                             "--smtp-host", "smtp.x.com", "--smtp-port", "465",
                             "--mail-to", "x@y.com"])
        monkeypatch.delenv("CREDIT_WEBHOOK", raising=False)
        assert notify.main() == 0
        assert sent["to"] == ["x@y.com"]

    def test_health_fatal_triggers_send_when_rows_green(self, tmp_path, monkeypatch):
        """执照全绿 + 自检致命失败 → 日报仍要发（否则「服务坏了」无人知晓）。"""
        import json as _json

        import notify
        p = self._csv(tmp_path, verdict="ok")
        h = tmp_path / "h.json"
        h.write_text(_json.dumps({
            "summary": {"total": 12, "passed": 9, "failed": 2, "warn": 1},
            "checks": [{"id": "smtp_config", "severity": "fatal", "ok": False,
                        "detail": "缺少配置：SMTP_HOST"}]}), encoding="utf-8")
        sent = {}
        monkeypatch.setattr(
            notify, "send_mail",
            lambda host, port, user, pw, to_addrs, subject, md, tls,
            use_ssl=None, timeout=30: sent.update(md=md))
        monkeypatch.setattr(sys, "argv",
                            ["notify.py", str(p), "--health", str(h),
                             "--smtp-host", "smtp.x.com", "--smtp-port", "465",
                             "--mail-to", "x@y.com"])
        monkeypatch.delenv("CREDIT_WEBHOOK", raising=False)
        assert notify.main() == 0
        assert "服务自检异常" in sent["md"]
        assert "缺少配置：SMTP_HOST" in sent["md"]

    def test_monthly_ignores_health_failures(self, tmp_path, monkeypatch):
        """月报不复述服务健康：执照全绿就该 exit 10，不打扰主管。"""
        import json as _json

        import notify
        p = self._csv(tmp_path, verdict="ok")
        h = tmp_path / "h.json"
        h.write_text(_json.dumps({
            "summary": {"total": 12, "passed": 9, "failed": 2, "warn": 1},
            "checks": [{"id": "smtp_config", "severity": "fatal", "ok": False,
                        "detail": "x"}]}), encoding="utf-8")
        monkeypatch.setattr(sys, "argv",
                            ["notify.py", str(p), "--health", str(h),
                             "--mode", "monthly",
                             "--smtp-host", "smtp.x.com", "--smtp-port", "465",
                             "--mail-to", "x@y.com"])
        monkeypatch.delenv("CREDIT_WEBHOOK", raising=False)
        assert notify.main() == 10

    def test_bad_mode_rejected_by_argparse(self, tmp_path, monkeypatch, capsys):
        import notify
        p = self._csv(tmp_path)
        monkeypatch.setattr(sys, "argv", ["notify.py", str(p), "--mode", "weekly"])
        with pytest.raises(SystemExit) as ei:
            notify.main()
        assert ei.value.code == 2


# ---------------------------------------------------------------- 调度脚本双触发器


class TestScheduleDualTrigger:
    def test_ps1_declares_monthly_params(self):
        src = PS1.read_text(encoding="utf-8-sig")
        assert "$MonthlyDay" in src
        assert "$MonthlyTime" in src
        assert "$NoMonthly" in src

    def test_monthly_day_capped_at_28(self):
        """上限必须是 28：29/30/31 在 2 月不存在，Task Scheduler 不会顺延，
        而是整月静默不触发——这是最难发现的静默失效。"""
        src = PS1.read_text(encoding="utf-8-sig")
        assert "[ValidateRange(1, 28)]" in src
        assert "ValidateRange(1, 31)" not in src

    def test_does_not_use_unavailable_monthly_cmdlet(self):
        """New-ScheduledTaskTrigger 没有 Monthly 参数集，这么写会直接报错。"""
        src = PS1.read_text(encoding="utf-8-sig")
        assert "New-ScheduledTaskTrigger -Monthly" not in src

    def test_uses_xml_merge_route(self):
        """月度触发器只能走「导出 XML → 插第二个 CalendarTrigger → -Xml 注册」。"""
        src = PS1.read_text(encoding="utf-8-sig")
        assert "Export-ScheduledTask" in src
        assert "Register-ScheduledTask -TaskName $TaskName -Xml" in src
        assert "ScheduleByMonth" in src

    def test_xml_merge_builds_month_block_and_drops_duplicate_id(self):
        """Merge-MonthlyTriggerXml 必须产出 ScheduleByMonth，且不残留重复 <Id>。"""
        body = TestScheduleScript()._ps_function_body("Merge-MonthlyTriggerXml")
        assert "<ScheduleByMonth>" in body
        assert "<DaysOfMonth>" in body
        assert "<Months>" in body
        # 克隆出来的触发器带同 Id 会被判非法，函数里必须有剔除动作
        assert "<Id>" in body

    def test_status_reads_triggers_from_xml(self):
        """CIM 对象读不出 DaysOfMonth，Status 必须看导出 XML 才算得对。"""
        body = TestScheduleScript()._ps_function_body("Invoke-Status")
        assert "Export-ScheduledTask" in body
        assert "ScheduleByMonth" in body

    def test_install_falls_back_to_single_trigger(self):
        """月度注册失败时必须回退成单触发器，不能把任务丢在中间态。"""
        body = TestScheduleScript()._ps_function_body("Register-DualTriggerTask")
        assert "回退" in body or "Unregister-ScheduledTask" in body
        assert "回读" in body or "verify" in body

    def test_bat_runs_healthcheck_before_credit_check(self):
        """自检必须在巡检之前：巡检崩了也要有一封「服务坏了」的邮件。"""
        tpl = TestScheduleScript()._bat_template()
        assert "healthcheck.py" in tpl
        assert tpl.index("healthcheck.py") < tpl.index("credit_check.py")

    def test_bat_healthcheck_failure_does_not_abort(self):
        """自检红不能 exit：它本身就是要报出去的内容，中断会导致「没发信」。"""
        tpl = TestScheduleScript()._bat_template()
        assert 'if "%RC_HEALTH%"=="1" exit' not in tpl
        # 退出码判定里只允许出现 notify / check
        tail = tpl[tpl.index("rem 退出码语义"):]
        assert "RC_HEALTH" not in tail

    def test_bat_uses_goto_not_paren_block_for_errorlevel(self):
        r"""括号块里的 %ERRORLEVEL% 在解析时展开，拿到的是旧值。

        这是 cmd 的经典坑：`if exist x ( set RC=%ERRORLEVEL% )` 永远读到
        进入括号之前的值。改用 goto 跳转才能拿到真实退出码。
        """
        tpl = TestScheduleScript()._bat_template()
        assert ":afterhealth" in tpl
        assert "goto :afterhealth" in tpl

    def test_bat_passes_mode_and_health(self):
        """notify 必须收到 --mode 与 --health，否则日报/月报分流和自检段都读不到。

        `--mode` 是通过 __NOTIFY__ 占位符注入的（模板里只留占位符），
        所以断言要落在 Build-BatContent 拼装 $notifyLine 的地方。
        """
        body = TestScheduleScript()._ps_function_body("Build-BatContent")
        assert "--mode" in body
        assert '--health "%HEALTH%"' in body
        # 模板里要保留占位符，否则替换那步会静默不生效
        tpl = TestScheduleScript()._bat_template()
        assert "__NOTIFY__" in tpl
        assert 'set "HEALTH=%DIR%data\\health_%STAMP%.json"' in tpl

    def test_uses_legacy_endlocal_cleanup(self):
        """Uninstall 必须同时清掉两个 .bat，否则残留的月度入口继续被子任务引用。"""
        body = TestScheduleScript()._ps_function_body("Invoke-Uninstall")
        assert "MonthlyBatPath" in body

    def test_dryrun_shows_both_rhythms(self):
        body = TestScheduleScript()._ps_function_body("Invoke-DryRun")
        assert "月触发" in body
        assert "主触发" in body



# ====================================================================
# 应用内定时巡检（schedule.py + /api/schedules）
#
# 这一组用例存在的理由：修的是「界面上根本没有地方设置每日/每周/每月检查」
# 这个具体抱怨，而它背后其实有四类会各自复发的缺陷。一条用例锚一类，
# 而不是笼统测「调度能用」——笼统用例在回归时既不会失败也不会提示哪里坏了。
# ====================================================================
class TestScheduleCompute:
    """时间推算：错月不下推、跨周不跳期、时区不丢。"""

    @staticmethod
    def _base():
        # 2026-10-19 是周一。固定基准，避免用例随当月天数漂移。
        from datetime import datetime
        return datetime(2026, 10, 19, 9, 0).astimezone()

    def test_monthly_keeps_unreached_day_in_current_month(self):
        """本月的目标日还没到，就必须用本月，不能无条件下推到下月。

        这是原先 main.py `_get_next_run` 那个缺陷的原型：第一步无条件
        `month = (month % 12) + 1`，于是 10/19 启动、目标「每月 25 号」，
        第一次要等到 11/25——整整 37 天空窗，而本月 25 号本该跑。
        """
        from schedule import compute_next
        nxt = compute_next("monthly", "09:00", day=25, base=self._base())
        assert (nxt.year, nxt.month, nxt.day) == (2026, 10, 25), (
            f"本月 25 号还没到，下次应为本月 25 日，实际 {nxt.isoformat()}")
        assert nxt > self._base()

    def test_monthly_rolls_over_when_day_passed(self):
        from schedule import compute_next
        nxt = compute_next("monthly", "09:00", day=1, base=self._base())
        assert (nxt.year, nxt.month, nxt.day) == (2026, 11, 1)

    def test_monthly_february_28_always_exists(self):
        """day 上限 28 的意义：2 月一定有这一天，不存在「本月没有该日」。

        若允许 31，2 月就得在「顺延到 28/29」与「跳到 3 月」之间选一种，
        两种都有人要，行为不可无歧义地解释。
        """
        from datetime import datetime

        from schedule import compute_next
        base = datetime(2027, 1, 31, 9, 0).astimezone()   # 1/31 之后
        nxt = compute_next("monthly", "08:00", day=28, base=base)
        assert (nxt.year, nxt.month, nxt.day) == (2027, 2, 28)

    def test_weekly_can_be_within_this_week(self):
        """基准是周一 09:00，目标「周一 23:00」：就在今天，不该跳到下周一。"""
        from schedule import compute_next
        nxt = compute_next("weekly", "23:00", weekday=0, base=self._base())
        assert (nxt.year, nxt.month, nxt.day, nxt.hour) == (2026, 10, 19, 23)

    def test_weekly_rolls_when_time_passed(self):
        from schedule import compute_next
        nxt = compute_next("weekly", "08:00", weekday=0, base=self._base())
        assert (nxt.year, nxt.month, nxt.day) == (2026, 10, 26)

    def test_daily_same_day_and_next_day(self):
        from schedule import compute_next
        assert compute_next("daily", "23:00", base=self._base()).day == 19
        assert compute_next("daily", "08:00", base=self._base()).day == 20

    def test_result_always_carries_timezone(self):
        """结果必须带时区。

        库里存不带时区的 "2026-10-20T09:00:00" 时，无从判断它是本机时间
        还是 UTC；一旦按错的那侧解释，就是「差 8 小时」这种极难自证的错。
        """
        from schedule import compute_next
        for freq, kw in (("daily", {}), ("weekly", {"weekday": 3}),
                         ("monthly", {"day": 5})):
            nxt = compute_next(freq, "07:15", base=self._base(), **kw)
            assert nxt.tzinfo is not None, f"{freq} 返回了无时区时间"

    def test_bad_time_string_falls_back_not_raises(self):
        """坏配置不能让调度线程整体停摆：回落 09:00 而不是抛异常。"""
        from schedule import compute_next
        assert compute_next("daily", "乱写", base=self._base()).hour == 9

    def test_unknown_freq_raises(self):
        """频率是枚举，写错必须响亮失败——静默当成每日会天天误发邮件。"""
        from schedule import compute_next
        with pytest.raises(ValueError):
            compute_next("hourly", "09:00", base=self._base())

    def test_describe_is_human_readable(self):
        from schedule import describe
        assert describe("weekly", "09:30", weekday=0) == "每周一 09:30"
        assert describe("monthly", "09:30", day=25) == "每月 25 日 09:30"
        assert describe("daily", "09:30") == "每日 09:30"


class TestSchedulerTick:
    """触发器：到点触发、同刻不重复、关机后只补一次、停用不跑。"""

    @pytest.fixture()
    def env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CREDIT_DB", str(tmp_path / "jobs.db"))
        monkeypatch.setenv("CREDIT_OUT", str(tmp_path / "out"))
        import importlib

        import api
        importlib.reload(api)
        import schedule as sched
        importlib.reload(sched)
        store = api.Store(api.DB_TARGET)

        class FakeWorker:
            def __init__(self):
                self.q = []

            def submit(self, job_id):
                self.q.append(job_id)

        w = FakeWorker()
        return api, sched, store, w, sched.Scheduler(store, w)

    @staticmethod
    def _at(h, m=0):
        """今天 h:m 的本地时间。

        原来是写死的 ``datetime(2026, 10, 19, h, m)``：那一天恰好是这些测试
        写成的日子，所以能过；过了午夜就变成「注入一个过去了 n 天的 now」，
        ``last_run_at`` 与按那个 now 正推的 ``next_run_at`` 会跨天错位。
        改成相对今天，测试在任何日期跑都表达同一个意图。
        """
        from datetime import datetime
        return datetime.now().astimezone().replace(hour=h, minute=m, second=0,
                                                  microsecond=0)

    def _mk(self, api, sched, store, at_time, next_at):
        return store.add_schedule({
            "name": "测试", "freq": "daily", "at_time": at_time,
            "names": '["甲公司","乙公司"]',
            "next_run_at": sched._iso(next_at)})

    def test_fires_when_due(self, env):
        """到点即触发；**整点那一秒也要触发**。

        判定是 `due <= now` 而不是 `due < now`：轮询间隔 30 秒，
        若要求严格大于，09:00:00 这一轮会判「还没到」，要等 09:00:30
        才跑——多等半分钟不影响业务，但「09:00 触发」应该准点。
        """
        api, sched, store, w, sc = env
        self._mk(api, sched, store, "09:00", self._at(9))
        # 差一分钟：不触发
        assert sc.tick(now=self._at(8, 59)) == []
        assert w.q == []
        # 整点：触发
        fired = sc.tick(now=self._at(9, 0))
        assert len(fired) == 1 and len(w.q) == 1

    def test_does_not_fire_twice_for_same_slot(self, env):
        """先推进 next_run_at 再提交作业，是为了让「提交到一半被杀」

        也不会在同一时刻重复触发。这条断言直接锁住那个顺序。
        """
        api, sched, store, w, sc = env
        self._mk(api, sched, store, "09:00", self._at(9))
        sc.tick(now=self._at(9, 1))
        sc.tick(now=self._at(9, 2))
        assert len(w.q) == 1, "同一时刻被触发了两次"

    def test_advance_writes_both_marker_and_next(self, env):
        api, sched, store, _w, sc = env
        sid = self._mk(api, sched, store, "09:00", self._at(9))
        sc.tick(now=self._at(9, 1))
        s = store.get_schedule(sid)
        assert s["last_run_at"], "last_run_at 没写，补跑判断会失真"
        assert s["next_run_at"] and s["next_run_at"] > s["last_run_at"]

    def test_catch_up_exactly_once_after_downtime(self, env):
        """关机三天后启动：补跑 1 次，不追历史。

        补跑是必要的——「每天检查」若因为重启就整天不查，业务侧会当成没配。
        但只能补 1 次：追 3 次会把目标站打成连续请求，且历史那两天已经没意义。
        """
        api, sched, store, w, sc = env
        self._mk(api, sched, store, "09:00", self._at(9))
        sc.tick(now=self._at(9) + __import__("datetime").timedelta(days=3))
        assert len(w.q) == 1, f"应只补跑 1 次，实际 {len(w.q)} 次"
        sc.tick(now=self._at(9) + __import__("datetime").timedelta(days=3, minutes=1))
        assert len(w.q) == 1, "补跑之后又开始追历史了"

    def test_disabled_never_fires(self, env):
        api, sched, store, w, sc = env
        sid = self._mk(api, sched, store, "09:00", self._at(9))
        store.update_schedule(sid, {"enabled": False})
        assert sc.tick(now=self._at(9, 5)) == []
        assert w.q == []

    def test_missing_next_run_is_backfilled_not_fired(self, env):
        """老记录没有 next_run_at：补算一次，不当场触发。

        当场触发会让「刚升级完就莫名其妙跑一批」——那批作业没人预期，
        而且时间点通常不对。
        """
        _, _, store, w, sc = env
        sid = store.add_schedule({
            "name": "旧记录", "freq": "daily", "at_time": "09:00",
            "names": '["甲"]', "next_run_at": ""})
        fired = sc.tick(now=self._at(9, 5))
        assert fired == [] and len(w.q) == 0
        assert store.get_schedule(sid)["next_run_at"]

    def test_empty_names_skips_and_records_reason(self, env):
        """名单为空时不提交作业，但要把原因记进 last_error。

        静默跳过的问题：用户以为在跑，实际每次都被跳过，界面上什么也没有。
        """
        _, sched, store, w, sc = env
        sid = store.add_schedule({
            "name": "空名单", "freq": "daily", "at_time": "09:00",
            "names": "[]", "next_run_at": sched._iso(self._at(9))})
        assert sc.tick(now=self._at(9, 1)) == []
        assert w.q == []
        s = store.get_schedule(sid)
        assert s["last_state"] == "skipped" and s["last_error"]

    def test_one_broken_schedule_does_not_stop_others(self, env):
        """一条坏配置不该让其余调度集体停摆。"""
        api, sched, store, w, sc = env
        store.add_schedule({
            "name": "坏的", "freq": "daily", "at_time": "09:00",
            "names": "not-json", "next_run_at": sched._iso(self._at(9))})
        self._mk(api, sched, store, "09:00", self._at(9))
        fired = sc.tick(now=self._at(9, 1))
        assert len(fired) == 1 and len(w.q) == 1


class TestScheduleApi:
    """接口契约：路由挂载、前缀、校验、以及「手动触发不顺延」这条业务规则。"""

    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CREDIT_DB", str(tmp_path / "jobs.db"))
        monkeypatch.setenv("CREDIT_OUT", str(tmp_path / "out"))
        import importlib

        import api
        importlib.reload(api)

        class DummyWorker(api.Worker):
            """不消费队列：否则一提交就真去开 Camoufox 联网抓取。"""

            def __init__(self, store):
                super().__init__(store)
                self.q = []

            def start(self):
                pass

            def submit(self, job_id):
                self.q.append(job_id)

            def stop(self):
                pass

        api.Worker = DummyWorker
        from fastapi.testclient import TestClient
        with TestClient(api.app) as c:
            yield c

    @staticmethod
    def _body(**kw):
        body = {"name": "华东每日", "freq": "daily", "at_time": "09:30",
                "names": ["甲公司", "乙公司"]}
        body.update(kw)
        return body

    def test_all_routes_under_api_prefix(self, client):
        """调度接口必须在 /api 下。

        SPA 的页面路径是 /schedules，接口若也占 /schedules，生产态托管 dist 时
        用户刷新页面会拿到 JSON（历史坑：/jobs 曾经就是这个症状：深链打不开）。
        断言读 openapi 而不是 app.routes —— 后者被 include_router 包了一层
        `_IncludedRouter`，没有 .path，按它写会得到 AttributeError 而非断言失败。
        """
        paths = [p for p in client.app.openapi()["paths"] if "schedule" in p]
        assert paths, "没找到任何调度路由"
        for p in paths:
            assert p.startswith("/api/"), f"{p} 没挂 /api 前缀"

    def test_create_list_delete_roundtrip(self, client):
        r = client.post("/api/schedules", json=self._body())
        assert r.status_code == 201, r.text
        d = r.json()
        sid = d["schedule_id"]
        assert d["enabled"] is True and d["count"] == 2
        assert d["names"] == ["甲公司", "乙公司"]
        assert d["human"] == "每日 09:30"
        assert d["next_run_at"], "新建后必须有下次触发时间"
        assert client.get("/api/schedules").json()[0]["schedule_id"] == sid
        assert client.delete(f"/api/schedules/{sid}").status_code == 200
        assert client.get("/api/schedules").json() == []

    def test_comment_lines_stripped_from_names(self, client):
        r = client.post("/api/schedules",
                        json=self._body(names=["甲", "#这行是注释", "乙", "甲"]))
        assert r.json()["names"] == ["甲", "乙"]
        assert r.json()["count"] == 2

    def test_rejects_malformed_time(self, client):
        """9:30 / 25:00 / 空 都要 422，不能静默当成 09:00。

        静默回落只适合「库里已有的坏数据」；用户新填的东西必须当场报错。
        """
        for bad in ("9:30", "25:00", "", "09-30", "0930"):
            r = client.post("/api/schedules", json=self._body(at_time=bad))
            assert r.status_code == 422, f"{bad!r} 应该被拒，实际 {r.status_code}"

    def test_rejects_day_over_28(self, client):
        r = client.post("/api/schedules",
                        json=self._body(freq="monthly", day=31))
        assert r.status_code == 422, "day>28 应被拒（该月可能没有这一天）"

    def test_rejects_reversed_delay(self, client):
        r = client.post("/api/schedules",
                        json=self._body(min_delay=10, max_delay=2))
        assert r.status_code == 422

    def test_toggle_off_then_on_recomputes(self, client):
        sid = client.post("/api/schedules", json=self._body()).json()["schedule_id"]
        off = client.post(f"/api/schedules/{sid}/toggle",
                          params={"enabled": "false"}).json()
        assert off["enabled"] is False
        on = client.post(f"/api/schedules/{sid}/toggle",
                         params={"enabled": "true"}).json()
        assert on["enabled"] is True and on["next_run_at"], (
            "重新启用必须重算下次时间，否则会沿用停用期间的过期时刻")

    def test_run_now_does_not_shift_next_run(self, client):
        """「立刻执行」不顺延正常节奏。

        否则周五临时点一下，周一的定时就整周不跑了——这是最容易写错、
        又最难在界面上发现的一条。
        """
        sid = client.post("/api/schedules", json=self._body()).json()["schedule_id"]
        before = client.get("/api/schedules").json()[0]["next_run_at"]
        r = client.post(f"/api/schedules/{sid}/run")
        assert r.status_code == 202, r.text
        assert r.json()["state"] == "queued"
        after = client.get("/api/schedules").json()[0]["next_run_at"]
        assert after == before, f"手动触发改动了下次时间：{before} → {after}"

    def test_update_recomputes_next_run(self, client):
        sid = client.post("/api/schedules", json=self._body()).json()["schedule_id"]
        r = client.put(f"/api/schedules/{sid}",
                       json=self._body(freq="monthly", day=5, at_time="18:00"))
        j = r.json()
        assert j["human"] == "每月 5 日 18:00"
        # 断言带 +08:00 是刻意的：next_run_at 是「本地墙钟时间」，生产
        # docker-compose 设的就是 TZ=Asia/Shanghai。时区不对时报错要直接
        # 指向根因，否则只看到一个时区串对不上，容易怀疑是算错了。
        assert j["next_run_at"].endswith("18:00:00+08:00"), (
            f"next_run_at={j['next_run_at']!r} 不是 +08:00，进程时区不是东八区"
            f"（TZ={os.environ.get('TZ', '<未设置>')}）。"
            f" Linux/容器：跑测试加 TZ=Asia/Shanghai（与生产 compose 一致）。"
            f" Windows：**不要**设 TZ —— MSVCRT 会把 'Asia/Shanghai' 解析成"
            f" +01:00 这种错误偏移，改为把系统时区设成东八区。")

    def test_preview_does_not_persist(self, client):
        """预览是纯计算：填表过程中不该往库里塞半成品配置。"""
        r = client.get("/api/schedules/preview",
                       params={"freq": "weekly", "at_time": "08:00", "weekday": 2})
        assert r.status_code == 200
        assert r.json()["human"] == "每周三 08:00"
        assert client.get("/api/schedules").json() == []

    def test_404_on_missing_schedule(self, client):
        for r in (client.put("/api/schedules/nope", json=self._body()),
                  client.post("/api/schedules/nope/run"),
                  client.post("/api/schedules/nope/toggle",
                              params={"enabled": "true"}),
                  client.delete("/api/schedules/nope")):
            assert r.status_code == 404, f"{r.request.method} 应 404"

    def test_openapi_declares_schedule_schemas(self, client):
        """每个调度接口都有具名 JSON schema 的 2xx 响应。

        这条守的是仓库已有的 test_openapi_has_no_opaque_response_schema 那条约束：
        返回 dict 而非 response_model 时，前端 openapi-fetch 生成出来是 unknown，
        只能手写 as 断言——错误码路径就是这么漏的（extractErrorMessage 曾把
        所有非 2xx 都当成「网络不可达」）。
        """
        spec = client.get("/openapi.json").json()
        schemas = spec["components"]["schemas"]
        assert {"ScheduleIn", "ScheduleOut", "SchedulePreview",
                "ScheduleDeleteResponse"} <= set(schemas)

        def ref_of(resp):
            """取响应体的具名 schema 引用；数组要往里再走一层 items。

            列表接口的 schema 是 {"type":"array","items":{"$ref":...}}，
            只看顶层 $ref 会把列表接口误判成「没有具名 schema」。
            """
            content = resp.get("content") or {}
            schema = content.get("application/json", {}).get("schema", {})
            if "$ref" in schema:
                return schema["$ref"]
            return schema.get("items", {}).get("$ref", "")

        checked = 0
        for path, ops in spec["paths"].items():
            if "schedule" not in path:
                continue
            for method, op in ops.items():
                if method not in ("get", "post", "put", "delete"):
                    continue
                two_xx = [c for c in op["responses"] if c.startswith("2")]
                assert two_xx, f"{method.upper()} {path} 没有声明 2xx 响应"
                for code in two_xx:
                    ref = ref_of(op["responses"][code])
                    assert ref.startswith("#/components/schemas/"), (
                        f"{method.upper()} {path} {code} 的响应没有具名 schema")
                    checked += 1
        # 逐条数：list 200 / create 201 / preview 200 / update 200 /
        # delete 200 / run 202 / toggle 200 = 7
        assert checked == 7, (
            f"检查到 {checked} 个 2xx 响应，与预期的 7 个接口不符——"
            "新增调度接口时请同步这条口径")


    def test_delete_returns_schema_not_empty_body(self, client):
        """删除返回 {"deleted": true} 而不是裸 204：前端要能区分「删掉了」

        与「本来就不存在」——后者走 404 分支。裸 204 两者无法区分。
        """
        sid = client.post("/api/schedules", json=self._body()).json()["schedule_id"]
        d = client.delete(f"/api/schedules/{sid}")
        assert d.status_code == 200
        assert d.json() == {"deleted": True, "schedule_id": sid}



class TestContactValidation:
    """推送达人（邮箱 / webhook）的校验与归一。

    这一组修的是「填写邮箱和微信那几步有点问题」这个抱怨。原先整条链路
    有三处各自独立、但症状都是「填了没反应」的缺陷：

      1. api.py 从不 load_dotenv() → SMTP_HOST 恒空 → Web 提交的作业
         一封邮件都发不出去，而且只在后台日志里留一行；
      2. 收件人用 mail_to.split(",") 解析，而前端允许中文逗号 →
         `a@x.com，b@y.com` 被当成一个地址静默吞掉；
      3. webhook 前后端都只判 http(s):// 前缀 → 光秃秃的 `https://`
         能提交，跑到推送时才抛异常，同样只在日志里。
    """

    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CREDIT_DB", str(tmp_path / "jobs.db"))
        monkeypatch.setenv("CREDIT_OUT", str(tmp_path / "out"))
        import importlib

        import api

        importlib.reload(api)

        class IdleWorker(api.Worker):
            """不消费队列：邮箱/webhook 校验与真正的浏览器抓取无关。"""

            def __init__(self, store):
                super().__init__(store)
                self.q = []

            def start(self):
                pass

            def submit(self, job_id):
                self.q.append(job_id)

            def stop(self):
                pass

        api.Worker = IdleWorker
        from fastapi.testclient import TestClient
        with TestClient(api.app) as c:
            yield c

    @staticmethod
    def _request_of(client, job_id):
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200
        return r.json()

    def test_chinese_comma_recipients_are_normalized(self, client):
        """中文逗号 / 全角分号 / 空格混用都要归一成英文逗号。

        原先 `split(",")` 只认英文逗号，`a@x.com，b@y.com` 整串被当成
        **一个** 地址交给 send_mail：SMTP 要么报 501，要么只投第一个。
        用户看到的是「填了两个只收到一个」，且日志里没有任何线索。
        """
        import json
        import sqlite3
        r = client.post("/api/jobs", json={
            "names": ["甲"], "mail_to": "a@x.com，b@y.com；  c@z.com"})
        assert r.status_code == 202, r.text
        db = client.app.state.store.path
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        raw = conn.execute("SELECT request FROM jobs ORDER BY rowid DESC LIMIT 1"
                           ).fetchone()["request"]
        assert json.loads(raw)["mail_to"] == "a@x.com,b@y.com,c@z.com"

    def test_rejects_malformed_recipient(self, client):
        """明显不是邮箱的输入必须当场 422，不能等到跑完才失败。"""
        for bad in ("not-an-email", "a@x", "a@@x.com", "a@x.com,,@y.com"):
            r = client.post("/api/jobs", json={"names": ["甲"], "mail_to": bad})
            assert r.status_code == 422, f"{bad!r} 应被拒，实际 {r.status_code}"

    def test_empty_recipient_becomes_null(self, client):
        """只填了分隔符（「,」或空格）等于没填，不该存成空串。"""
        import json
        import sqlite3
        r = client.post("/api/jobs", json={"names": ["甲"], "mail_to": " , ， "})
        assert r.status_code == 202, r.text
        conn = sqlite3.connect(client.app.state.store.path)
        conn.row_factory = sqlite3.Row
        raw = conn.execute("SELECT request FROM jobs ORDER BY rowid DESC LIMIT 1"
                           ).fetchone()["request"]
        assert json.loads(raw)["mail_to"] is None

    def test_rejects_bare_scheme_webhook(self, client):
        """`https://` 光秃秃放行是原先的真实缺陷：能提交，推送时抛异常，

        而异常只进日志 → 界面上永远是「未推送」，无从判断是配错了。
        """
        for bad in ("https://", "http://", "ftp://a.com/hook", "a.com/hook",
                    "https:// /path"):
            r = client.post("/api/jobs", json={"names": ["甲"], "webhook": bad})
            assert r.status_code == 422, f"{bad!r} 应被拒，实际 {r.status_code}"

    def test_accepts_real_webhook(self, client):
        r = client.post("/api/jobs", json={
            "names": ["甲"],
            "webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc"})
        assert r.status_code == 202, r.text

    def test_schedule_inherits_same_validation(self, client):
        """调度用的是同一套校验（ScheduleIn 也走了 parse_addrs 口径）。"""
        base = {"name": "t", "freq": "daily", "at_time": "09:00", "names": ["甲"]}
        assert client.post("/api/schedules",
                           json={**base, "mail_to": "a@x.com，b@y.com"}).status_code == 201
        assert client.post("/api/schedules",
                           json={**base, "mail_to": "nope"}).status_code == 422
        assert client.post("/api/schedules",
                           json={**base, "webhook": "https://"}).status_code == 422


class TestNotifyFailureSurfaced:
    """推送失败必须能从接口上看见，而不是只躺在后台日志里。"""

    @pytest.fixture()
    def store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CREDIT_DB", str(tmp_path / "jobs.db"))
        monkeypatch.setenv("CREDIT_OUT", str(tmp_path / "out"))
        import importlib

        import api

        importlib.reload(api)
        return api, api.Store(api.DB_TARGET)

    def _mkjob(self, api, store):
        req = {"names": ["甲"], "mail_to": "a@x.com", "webhook": None,
               "always_notify": True, "min_delay": 0, "max_delay": 0}
        job_id = store.create_job(["甲"], req)
        store.add_result(job_id, 0, {
            "输入名称": "甲", "匹配名称": "甲", "统一社会信用代码": "91",
            "登记状态": "存续", "判定": "ok", "主体类型": "", "数据源": "",
            "记录数": 1, "详情URL": "", "备注": ""})
        store.set_state(job_id, "done")
        return job_id

    def test_missing_smtp_is_recorded_on_job(self, store, monkeypatch):
        """没配 SMTP_HOST 时，作业上要留下可读原因。

        原先这个分支只 append 到局部 errors 然后 log.error，跑的是一次
        「假装成功」——notified 仍是 0，界面显示「未推送」，
        但用户分不清是没跑完、被跳过还是配错了。
        """
        api, st = store
        monkeypatch.delenv("SMTP_HOST", raising=False)
        job_id = self._mkjob(api, st)
        w = object.__new__(api.Worker)
        w.store = st
        w._maybe_notify(job_id, [{"判定": "ok", "输入名称": "甲"}],
                        {"mail_to": "a@x.com", "webhook": None,
                         "always_notify": True})
        row = st.get_job(job_id)
        assert row["notified"] == 1, "尝试过推送就该置 notified=1"
        assert "SMTP_HOST" in (row["notify_error"] or ""), row["notify_error"]

    def test_success_leaves_no_error(self, store, monkeypatch):
        api, st = store
        job_id = self._mkjob(api, st)

        class FakeNotify:
            @staticmethod
            def build_report(rows):
                return "t", "md", ["x"]

            @staticmethod
            def parse_addrs(raw):
                return [raw]

            @staticmethod
            def send_mail(*a, **kw):
                return None

        monkeypatch.setattr(api, "notify", FakeNotify)
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        w = object.__new__(api.Worker)
        w.store = st
        w._maybe_notify(job_id, [{"判定": "ok"}], {"mail_to": "a@x.com"})
        row = st.get_job(job_id)
        assert row["notified"] == 1 and not row["notify_error"]

    def test_notify_error_appears_in_api(self, store):
        """JobSummary 必须把 notify_error 带给前端，否则等于没记。"""
        api, st = store
        job_id = self._mkjob(api, st)
        st.mark_notified(job_id, error="mail: 未配置 SMTP_HOST")
        summary = api._to_summary(st.get_job(job_id), st)
        assert summary.notify_error == "mail: 未配置 SMTP_HOST"

    def test_old_db_without_column_is_migrated(self, tmp_path):
        """老库（无 notify_error 列）升级后必须能直接跑。

        CREATE TABLE IF NOT EXISTS 对已存在的表完全无操作，加列必须单独做，
        否则 SELECT 会报 no such column——而这是升级时最常见的翻车点，
        本地开发库一看就炸，生产库更是跑不起来。
        """
        import importlib
        import sqlite3

        db = tmp_path / "old.db"
        # 手工建一个「旧版」jobs 表：没有 notify_error
        conn = sqlite3.connect(db)
        conn.executescript("""
        CREATE TABLE jobs (
            job_id TEXT PRIMARY KEY, state TEXT NOT NULL, total INTEGER NOT NULL,
            finished INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
            started_at TEXT, finished_at TEXT, error TEXT,
            notified INTEGER NOT NULL DEFAULT 0, request TEXT NOT NULL,
            csv_path TEXT);
        """)
        conn.commit()
        conn.close()

        import api
        importlib.reload(api)
        st = api.Store(str(db))
        cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(jobs)")}
        assert "notify_error" in cols
        # 迁移后能正常写读
        jid = st.create_job(["甲"], {"names": ["甲"]})
        st.mark_notified(jid, error="boom")
        assert st.get_job(jid)["notify_error"] == "boom"


class TestCreditCodeLookup:
    """按统一社会信用代码查询。

    销售手上两个键都可能：企业全称，或者营业执照上那串 18 位代码。
    原先 check_one 只把输入当名字去搜、再按**名称相等**判定，于是：

      * 直接粘代码进来 → 信用中国对它返回空列表 → 判定 notfound
        → 用户看到「未收录（疑似已注销/吊销）」，而这家公司其实正常存续；
      * 即便搜索返回了带代码的候选，也会因为名称对不上而判 unknown。

    两者都是「查不到」，但业务含义完全不同——一个会让销售误以为客户
    出了问题。这里锁住代码查询路径与两条匹配依据。
    """

    @staticmethod
    def _row(name="深圳市腾讯计算机系统有限公司",
             code="91440300708461136T", et="1"):
        return {"accurate_entity_name": name, "accurate_entity_code": code,
                "accurate_entity_name_query": name, "entityType": et,
                "uuid": "u-1"}

    def test_is_credit_code(self):
        import credit_check as cc
        assert cc.is_credit_code("91440300708461136T")
        assert cc.is_credit_code(" 91440300708461136t ")   # 归一后大写
        # 18 位里含 I/O/S/V/Z 的按 GB 32100 不属于代码字符集
        assert not cc.is_credit_code("91440300708461136I")
        assert not cc.is_credit_code("深圳市腾讯计算机系统有限公司")
        assert not cc.is_credit_code("")
        assert not cc.is_credit_code("9144030070846113")     # 17 位

    def test_matches_by_code_even_when_name_differs(self):
        """按代码查：名称与输入对不上也应命中（输入本来就是代码）。"""
        import credit_check as cc
        rows = [{"accurate_entity_name": "某某无关公司",
                 "accurate_entity_code": "91440300708461136T"}]
        got = cc.pick_target(rows, "91440300708461136T")
        assert got is not None and got["accurate_entity_name"] == "某某无关公司"

    def test_matches_by_name(self):
        import credit_check as cc
        got = cc.pick_target([self._row()], "深圳市腾讯计算机系统有限公司")
        assert got is not None and got["accurate_entity_code"] == "91440300708461136T"

    def test_no_fuzzy_match(self):
        """近似但不等的不许命中——假阳性比查不到更危险。"""
        import credit_check as cc
        rows = [self._row(name="深圳市腾讯计算机系统有限公司")]
        assert cc.pick_target(rows, "腾讯计算机系统有限公司") is None
        assert cc.pick_target(rows, "91440300708461130X") is None

    def test_code_hit_follows_row_order(self):
        """多行都能命中时按列表顺序取第一条——与原先 next() 语义一致。"""
        import credit_check as cc
        rows = [
            {"accurate_entity_name": "甲公司", "accurate_entity_code": "91440300MA5AAAAA1X"},
            {"accurate_entity_name": "乙公司", "accurate_entity_code": "91440300MA5BBBBB2Y"},
        ]
        got = cc.pick_target(rows, "91440300MA5BBBBB2Y")
        assert got is not None and got["accurate_entity_name"] == "乙公司"

    def test_ignores_non_dict_rows(self):
        """搜索响应里混进非 dict（null/字符串）不能炸。"""
        import credit_check as cc
        got = cc.pick_target([None, "oops", self._row()], "91440300708461136T")
        assert got is not None and got["accurate_entity_name"].startswith("深圳市腾讯")

    def test_code_normalization_ignores_case_and_space(self):
        import credit_check as cc
        assert cc.norm_code(" 91440300708461136t ") == "91440300708461136T"
        assert cc.is_credit_code("91440300708461136t")


class TestScheduleResultSync:
    """调度触发的作业，终态必须回写到调度行。

    这是「静默失败」的第二现场：调度行只写过 ``last_state="queued"``
    就再没人更新，用户看到的是「设了每天 09:30，状态一直没变」——
    与「填了邮箱、跑完了、什么也没收到」同属「配了却无从确认」。
    """

    def _make(self, client, names=("甲有限公司",)):
        r = client.post("/api/schedules", json={
            "name": "回写用例", "freq": "daily", "at_time": "09:30",
            "names": list(names),
        })
        assert r.status_code == 201, r.text
        return r.json()["schedule_id"]

    def test_done_state_and_notify_error_reach_schedule_row(self, client):
        import api
        sid = self._make(client)
        r = client.post(f"/api/schedules/{sid}/run")
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]

        store = client.app.state.store
        row = store.get_schedule(sid)
        assert row["last_state"] == "queued" and row["last_job_id"] == job_id

        # 模拟 worker 收尾：done + 推送失败原因
        store.set_state(job_id, "done")
        store.mark_notified(job_id, "mail: 连接超时")
        api._sync_schedule(store, job_id, "done")

        row = store.get_schedule(sid)
        assert row["last_state"] == "done"
        assert row["last_error"] == "mail: 连接超时", (
            "推送失败原因必须落到调度行，否则用户只看到「已完成」")

    def test_failed_job_writes_error(self, client):
        import api
        sid = self._make(client)
        job_id = client.post(f"/api/schedules/{sid}/run").json()["job_id"]

        store = client.app.state.store
        msg = "浏览器启动失败：RuntimeError: boom"
        store.set_state(job_id, "failed", error=msg)
        api._sync_schedule(store, job_id, "failed", msg)

        row = store.get_schedule(sid)
        assert row["last_state"] == "failed"
        assert "boom" in row["last_error"]

    def test_manual_job_does_not_touch_schedules(self, client):
        """普通作业的 request 里没有 schedule_id，回写应当是无操作。"""
        import api
        sid = self._make(client, names=("乙有限公司",))
        before = client.app.state.store.get_schedule(sid)

        job = client.post("/api/jobs", json={"names": ["丙有限公司"]}).json()
        store = client.app.state.store
        store.set_state(job["job_id"], "done")
        api._sync_schedule(store, job["job_id"], "done")  # 不该抛，也不该改

        after = store.get_schedule(sid)
        assert after["last_state"] == before["last_state"]
        assert after["last_job_id"] == before["last_job_id"]

    def test_run_now_does_not_shift_next_run(self, client):
        """「立刻跑一次」刻意不顺延：否则周五点一下，周一的定时就没了。"""
        sid = self._make(client)
        first = client.app.state.store.get_schedule(sid)["next_run_at"]
        client.post(f"/api/schedules/{sid}/run")
        assert client.app.state.store.get_schedule(sid)["next_run_at"] == first


class TestRoster:
    """巡检名单文件的结构契约（巡检名单.json + roster.py）。"""

    def test_roster_file_has_at_least_fifty_entries(self):
        """需求口径：名单「起码 50 家」。少于 50 直接失败，防止误删。"""
        names = roster.load_roster()
        assert len(names) >= 50, f"名单只有 {len(names)} 家，少于承诺的 50"

    def test_roster_entries_are_non_empty_and_unique(self):
        names = roster.load_roster()
        assert all(isinstance(n, str) and n.strip() for n in names)
        assert len(set(names)) == len(names), "名单里有重复条目"

    def test_roster_entries_are_name_or_credit_code(self):
        """每条要么是 18 位统一社会信用代码，要么是企业全称，不能是空壳。"""
        names = roster.load_roster()
        for n in names:
            is_code = bool(re.fullmatch(r"[0-9A-Z]{18}", n))
            is_name = not n.startswith("#")
            assert is_code or is_name, f"无法识别的条目：{n!r}"

    def test_roster_has_deregistered_entries_without_code(self):
        """必须有没有信用代码的全称条目——已注销主体查不到代码，
        这正是要人看一眼的信号；全用代码就无法表达这类主体。"""
        names = roster.load_roster()
        plain = [n for n in names if not re.fullmatch(r"[0-9A-Z]{18}", n)]
        assert plain, "名单里没有任何全称条目，已注销/吊销主体会消失"

    def test_roster_survives_schedule_validation(self, client):
        """整份名单必须能通过 ScheduleIn 的 names 校验（否则调度建不出来）。"""
        from api import ScheduleIn
        out = ScheduleIn(name="名单自检", freq="daily", at_time="09:00",
                         names=roster.load_roster())
        assert len(out.names) == len(roster.load_roster())


# ---------------------------------------------------------------- 「全部正常也推送」默认开
class TestAlwaysNotifyDefaultOn:
    """研发口径：巡检结果本身就是交付物，每期都要看到全量名单。

    早前 ``always_notify`` 默认 False（「只在有异常时发」），于是全绿的那几天
    一封邮件都没有，看起来像巡检没跑——这正是本次改动要消除的静默失败。
    这里锁死默认值与语义，防止以后被"顺手改回去"。
    """

    def test_check_request_defaults_to_true(self):
        import api
        req = api.CheckRequest(names=["某公司"])
        assert req.always_notify is True

    def test_schedule_in_defaults_to_true(self):
        import api
        s = api.ScheduleIn(name="x", freq="daily", at_time="09:00",
                           names=["某公司"])
        assert s.always_notify is True

    def test_schema_default_is_one_for_both_backends(self):
        """建表默认值必须一起改，否则新库插入不传该列时仍会落 0。"""
        import api
        assert "always_notify INTEGER NOT NULL DEFAULT 1" in api.SCHEMA_SQLITE
        assert "always_notify TINYINT(1) NOT NULL DEFAULT 1" in \
            api.SCHEMA_MYSQL_SCHEDULES

    def test_all_ok_job_still_notifies(self, tmp_path, monkeypatch):
        """全绿作业也要真的走一次发送——不是只在字段上写着 True。"""
        import api
        sent = {}

        def fake_mail(host, port, user, pw, addrs, title, md, flag, use_ssl=False):
            sent["title"] = title
            sent["md"] = md

        import notify
        monkeypatch.setattr(notify, "send_mail", fake_mail)
        monkeypatch.setenv("SMTP_HOST", "smtp.x.com")
        monkeypatch.setenv("SMTP_PORT", "465")

        rows = [{"输入名称": "A", "匹配名称": "A", "统一社会信用代码": "X" * 18,
                 "登记状态": "存续", "判定": "ok", "备注": ""}]
        st = api.Store(tmp_path / "jobs.db")
        job_id = st.create_job(["A"], {"mail_to": "a@b.com",
                                       "always_notify": True})
        w = api.Worker(st)          # 构造但不 start()：不需要真跑浏览器
        w._maybe_notify(job_id, rows, {"mail_to": "a@b.com",
                                       "always_notify": True})
        assert "md" in sent, "全绿且 always_notify=True 时必须真的发送"
        assert "全部主体清单" in sent["md"], "全绿时正文必须仍带全量清单"
        assert st.get_job(job_id)["notified"] == 1

    def test_all_ok_job_skips_when_explicitly_off(self, tmp_path, monkeypatch):
        """显式关掉时仍要能静默——用户保留「只在有异常才发」的能力。"""
        import api
        sent = {}
        import notify
        monkeypatch.setattr(notify, "send_mail",
                            lambda *a, **k: sent.setdefault("sent", True))
        rows = [{"输入名称": "A", "判定": "ok", "登记状态": "存续",
                 "统一社会信用代码": "", "匹配名称": "A", "备注": ""}]
        st = api.Store(tmp_path / "jobs.db")
        job_id = st.create_job(["A"], {"mail_to": "a@b.com",
                                       "always_notify": False})
        api.Worker(st)._maybe_notify(job_id, rows,
                                     {"mail_to": "a@b.com",
                                      "always_notify": False})
        assert "sent" not in sent, "always_notify=False 时全绿不应发送"

    def test_legacy_rows_get_backfilled_once(self, tmp_path):
        """老库升级：已有调度行必须从 0 翻成 1；标记落库后重启不再重放。

        建一个新建库的 Store 会立刻打上迁移标记（那时还没有行），所以这里
        手工把标记删掉，模拟「老库根本没有 meta 表/标记」的升级场景。
        """
        import sqlite3

        import api
        db = tmp_path / "jobs.db"
        st = api.Store(db)
        sid = st.add_schedule({
            "name": "老调度", "enabled": True, "freq": "daily", "at_time": "09:00",
            "names": '["A"]', "mail_to": "a@b.com", "always_notify": False,
            "next_run_at": "2026-10-21T09:00:00+08:00"})
        assert st.get_schedule(sid)["always_notify"] == 0, "先确认确实写成了 0"

        with sqlite3.connect(db) as c:      # 抹掉标记 = 回到未升级状态
            c.execute("DELETE FROM meta WHERE key=?", (api.Store._MIG_ALWAYS_NOTIFY,))

        st2 = api.Store(db)                  # 重开 → 触发一次性迁移
        assert st2.get_schedule(sid)["always_notify"] == 1

        # 运营后来手工关掉，重启不能又给翻开（标记已落库）
        st2.update_schedule(sid, {"always_notify": False})
        st3 = api.Store(db)
        assert st3.get_schedule(sid)["always_notify"] == 0, "迁移不可重放"

    def test_job_request_echo_preserves_true(self, tmp_path):
        """详情页回显不能被模型默认值洗成 False。"""
        import api
        req = api.JobRequest(**{"names": ["A"], "always_notify": True,
                                "mail_to": "a@b.com"})
        assert req.always_notify is True
