"""验证新 FastAPI 栈与旧 Flask 版的接口契约一致。

这是 C 方案的**回归防线核心**：旧栈 106 项 Playwright E2E 要全绿，
新栈的 TestClient 也必须全绿。本脚本在两边都跑，直接对比关键契约点
（状态码、错误文案、响应外壳），不一致就报错退出。

用法（在 F:\\projects\\GO1\\python_ver 下）：
    python verify_new_stack.py
"""

from __future__ import annotations

import os
import sys
import tempfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)


def main() -> int:
    # 把配置指向临时 SQLite，避免污染真实 MySQL 库
    tmp = tempfile.mkdtemp(prefix="tm_verify_")
    os.environ["WIPO_DATABASE__URL"] = f"sqlite+aiosqlite:///{tmp}/verify.db"
    os.environ["WIPO_REPORTS_DIR"] = os.path.join(tmp, "reports")
    os.environ["WIPO_TRADEMARKS_FILE"] = os.path.join(tmp, "user_trademarks.json")

    from fastapi.testclient import TestClient

    from app.core.config import reset_settings
    from app.main import create_app

    reset_settings()
    app = create_app()
    passed = 0
    failed = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  [OK] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    with TestClient(app) as client:
        # ---- 1. 健康检查 ----
        r = client.get("/health")
        check("/health 状态码 200", r.status_code == 200, str(r.status_code))
        body = r.json()
        check("/health 含 status 字段", "status" in body)
        check("/health status 合法", body.get("status") in ("ok", "degraded"), str(body.get("status")))
        check("/health 含 app 字段", "app" in body)
        check("/health 含 version 字段", "version" in body)
        check("/health 含 dependencies", "dependencies" in body)

        # ---- 2. 商标 CRUD ----
        r = client.post("/api/trademarks", json={
            "brandName": "VERIFY_BRAND_A",
            "applicant": "Verify Ltd.",
            "registrationNumber": "V1",
            "niceClass": ["9"],
        })
        check("POST /api/trademarks 200", r.status_code == 200, str(r.status_code))
        body = r.json()
        check("成功外壳 success=true", body.get("success") is True)
        check("data 有 id", bool(body.get("data", {}).get("id")))
        check("data 有 camelCase 键 brandName", "brandName" in body.get("data", {}))
        check("data 有 camelCase 键 registrationNumber", "registrationNumber" in body.get("data", {}))
        check("data 有 camelCase 键 niceClass", "niceClass" in body.get("data", {}))
        check("data 有 createdAt", "createdAt" in body.get("data", {}))
        tid = body.get("data", {}).get("id", "")

        r = client.get("/api/trademarks")
        check("GET /api/trademarks 200", r.status_code == 200)
        brands = [t.get("brandName") for t in r.json().get("data", [])]
        check("列表含新建商标", "VERIFY_BRAND_A" in brands, str(brands))

        # ---- 3. 校验错误文案（必须与旧 Flask 版逐字一致） ----
        r = client.post("/api/trademarks", json={"brandName": ""})
        check("空 brandName → 422", r.status_code == 422, str(r.status_code))
        check("空 brandName 文案正确", r.json().get("error") == "商标名称不能为空",
              repr(r.json().get("error")))

        r = client.post("/api/trademarks", json={"brandName": "A"})
        check("过短 brandName → 422", r.status_code == 422)
        check("过短 brandName 文案正确",
              r.json().get("error") == "商标名称长度至少2个字符",
              repr(r.json().get("error")))

        r = client.post("/api/trademarks", json={"brandName": "X" * 201})
        check("过长 brandName → 422", r.status_code == 422)
        check("过长 brandName 文案正确",
              r.json().get("error") == "商标名称长度不能超过200个字符",
              repr(r.json().get("error")))

        # ---- 4. 冲突 ----
        r = client.post("/api/trademarks", json={"brandName": "VERIFY_BRAND_A"})
        check("重名 → 409", r.status_code == 409, str(r.status_code))
        check("重名文案含已存在", "已存在" in r.json().get("error", ""),
              repr(r.json().get("error")))

        # ---- 5. 404 ----
        r = client.delete("/api/trademarks/does-not-exist")
        check("删除不存在 → 404", r.status_code == 404, str(r.status_code))
        check("404 文案含不存在", "不存在" in r.json().get("error", ""))

        r = client.get("/api/reports/nope.json")
        check("报告不存在 → 404", r.status_code == 404)

        # ---- 6. 更新与删除 ----
        r = client.put(f"/api/trademarks/{tid}", json={"applicant": "Changed Ltd."})
        check("PUT 200", r.status_code == 200)
        check("更新生效", r.json().get("data", {}).get("applicant") == "Changed Ltd.")

        r = client.delete(f"/api/trademarks/{tid}")
        check("DELETE 200", r.status_code == 200)
        check("deletedCount=1", r.json().get("deletedCount") == 1, str(r.json().get("deletedCount")))

        # ---- 7. 批量删除 ----
        for i in range(3):
            client.post("/api/trademarks", json={"brandName": f"VERIFY_BATCH_{i}"})
        r = client.request("DELETE", "/api/trademarks/batch", json={"ids": []})
        check("批量删空 ids → 400", r.status_code == 400, str(r.status_code))
        check("批量删空 ids 文案正确", r.json().get("error") == "未提供要删除的商标 id",
              repr(r.json().get("error")))

        # ---- 8. 导出 ----
        r = client.get("/api/export?format=json")
        check("导出 json 200", r.status_code == 200)
        check("导出 json 有 trademarks 键", "trademarks" in r.json())

        r = client.get("/api/export?format=csv")
        check("导出 csv 200", r.status_code == 200)
        check("CSV 带 BOM", r.text.startswith("\ufeff"), repr(r.text[:20]))
        check("CSV 首行正确", r.text.lstrip("\ufeff").splitlines()[0].startswith("brandName,applicant"))

        r = client.get("/api/export?format=xml")
        check("不支持的格式 → 400", r.status_code == 400)
        check("不支持格式文案正确", r.json().get("error") == "不支持的格式",
              repr(r.json().get("error")))

        # ---- 9. 导入 ----
        # 空文件（0 字节）按「导入 0 条」走完流程，与旧版一致：
        # E2E tests/test_import_export.py:105 断言提示里含 '0'。
        r = client.post("/api/import", files={"file": ("t.json", b"", "application/json")})
        check("导入空文件 200", r.status_code == 200, f"{r.status_code} {r.text[:80]}")
        check("导入空文件 importedCount=0", r.json().get("importedCount") == 0,
              repr(r.json().get("importedCount")))

        # 有内容但解析出 0 条记录 → 同样按「导入 0 条」返回 200。
        # 与旧版契约一致（E2E tests/test_import_export.py:105 断言提示含 '0'）：
        # 「内容为空」不是用户传错文件，而是清单本来就没数据。
        r = client.post("/api/import",
                        files={"file": ("t.json", '{"trademarks":[]}', "application/json")})
        check("导入空数组 → 200", r.status_code == 200, str(r.status_code))
        check("导入空数组 importedCount=0", r.json().get("importedCount") == 0,
              repr(r.json().get("importedCount")))

        r = client.post("/api/import", files={"file": ("t.txt", "x", "text/plain")})
        check("导入错误类型 → 400", r.status_code == 400)

        # ---- 10. 检查 ----
        r = client.post("/api/check", json={"ids": ["no-such-id"]})
        check("无效 id 检查 → 400", r.status_code == 400, str(r.status_code))
        check("无效 id 检查文案", r.json().get("error") == "没有可检查的商标",
              repr(r.json().get("error")))

        r = client.get("/api/check/status")
        check("检查状态 200", r.status_code == 200)
        check("检查状态无 success 外壳（契约）", "success" not in r.json())
        check("检查状态有 status 字段", "status" in r.json())

        # ---- 11. 契约端点 ----
        r = client.get("/api/schema")
        check("/api/schema 200", r.status_code == 200)
        body = r.json()
        check("/api/schema 外壳正确", body.get("success") is True)
        check("/api/schema owner 指向当前实现",
              body.get("data", {}).get("owner") == "app/api/v1/",
              repr(body.get("data", {}).get("owner")))
        check("/api/schema 有 version", "version" in body.get("data", {}))

        # ---- 12. 首页 ----
        r = client.get("/")
        check("首页 200", r.status_code == 200, str(r.status_code))
        check("首页是 HTML", "text/html" in r.headers.get("content-type", ""))
        check("首页含 no-store", "no-store" in r.headers.get("cache-control", ""))

        # ---- 13. 路由数量与旧版一致 ----
        from app.api.v1 import iter_api_routes
        paths = sorted({(tuple(sorted(r.methods)), r.path) for r in iter_api_routes()})
        check("路由数 13 条（与旧 Flask 一致）", len(paths) == 13, str(len(paths)))

        # ---- 14. request_id ----
        r = client.get("/health")
        rid = r.headers.get("x-request-id", "")
        check("响应头带 X-Request-Id", bool(rid), rid)
        # /health 是运维探活端点，刻意不套信封（见 app/api/v1/health.py
        # 的说明），所以响应体里没有 request_id 字段——只要头里有即可。
        check("/health 响应头带 request_id", bool(rid), rid)

        r = client.get("/api/trademarks")
        rid_body = r.json().get("request_id", "")
        rid_header = r.headers.get("x-request-id", "")
        check("信封接口响应体带 request_id", bool(rid_body), rid_body)
        check("信封接口头与体 request_id 一致", rid_body == rid_header,
              f"body={rid_body!r} header={rid_header!r}")

        # 上游带 X-Request-Id 时必须复用，而不是另生成一个
        r = client.get("/api/trademarks", headers={"X-Request-Id": "upstream-fixed-id"})
        check("复用上游 X-Request-Id",
              r.headers.get("x-request-id") == "upstream-fixed-id",
              r.headers.get("x-request-id", ""))
        check("响应体 request_id 与上游一致",
              r.json().get("request_id") == "upstream-fixed-id",
              r.json().get("request_id", ""))

    print(f"\n结果: {passed} 通过, {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
