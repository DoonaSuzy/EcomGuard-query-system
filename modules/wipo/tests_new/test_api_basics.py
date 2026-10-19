"""新 FastAPI 栈的基础接口测试（对照旧 Flask 版的 E2E 语义）。

断言口径与旧版逐条对齐：
- 成功外壳 ``{"success": true, "data": ...}``
- 失败外壳 ``{"success": false, "error": "..."}``
- ``error`` 文案与旧 Flask 版**逐字一致**（空名 / 过短 / 过长 / 重名 / 不存在）
- 状态码与旧版一致（400 / 404 / 409 / 422）

``GET /api/trademarks`` 的 ``data`` 是**数组本身**（不是 ``{trademarks:[...]}``），
这是旧前端 ``static/js/app.js:131`` 写死的解析方式，测试必须照此断言。
"""

from __future__ import annotations


def _err(body: dict) -> str:
    return body.get("error", "")


def _brands(client) -> list[str]:
    resp = client.get("/api/trademarks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    return [t["brandName"] for t in body["data"]]


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert body["app"]
    assert body["version"]


def test_create_and_list_trademark(client):
    resp = client.post("/api/trademarks", json={
        "brandName": "FASTAPI_TEST_A",
        "applicant": "Acme Ltd.",
        "registrationNumber": "US12345",
        "niceClass": ["9", "35"],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["brandName"] == "FASTAPI_TEST_A"
    # camelCase 契约：旧前端读的就是这些键
    assert body["data"]["registrationNumber"] == "US12345"
    assert body["data"]["niceClass"] == ["9", "35"]
    assert body["data"]["id"]

    assert "FASTAPI_TEST_A" in _brands(client)


def test_create_rejects_empty_brand_name(client):
    """文案必须与旧 Flask 版一致：web_app.py:226。"""
    resp = client.post("/api/trademarks", json={"brandName": ""})
    assert resp.status_code == 422
    body = resp.json()
    assert body["success"] is False
    assert _err(body) == "商标名称不能为空"


def test_create_rejects_short_brand_name(client):
    """文案必须与旧 Flask 版一致：web_app.py:234。"""
    resp = client.post("/api/trademarks", json={"brandName": "A"})
    assert resp.status_code == 422
    assert _err(resp.json()) == "商标名称长度至少2个字符"


def test_create_rejects_long_brand_name(client):
    """文案必须与旧 Flask 版一致：web_app.py:230。"""
    resp = client.post("/api/trademarks", json={"brandName": "X" * 201})
    assert resp.status_code == 422
    assert _err(resp.json()) == "商标名称长度不能超过200个字符"


def test_create_rejects_duplicate(client):
    """重名冲突：旧版 409。"""
    payload = {"brandName": "FASTAPI_DUP", "applicant": "A"}
    assert client.post("/api/trademarks", json=payload).status_code == 200
    dup = client.post("/api/trademarks", json=payload)
    assert dup.status_code == 409
    body = dup.json()
    assert body["success"] is False
    assert "已存在" in _err(body)


def test_update_and_delete(client):
    created = client.post("/api/trademarks", json={
        "brandName": "FASTAPI_UPD", "applicant": "Before",
    }).json()["data"]
    tid = created["id"]

    upd = client.put(f"/api/trademarks/{tid}", json={"applicant": "After"})
    assert upd.status_code == 200
    assert upd.json()["data"]["applicant"] == "After"
    # 未传的字段不被抹掉
    assert upd.json()["data"]["brandName"] == "FASTAPI_UPD"

    dele = client.delete(f"/api/trademarks/{tid}")
    assert dele.status_code == 200
    assert dele.json()["deletedCount"] == 1


def test_delete_missing_returns_404(client):
    resp = client.delete("/api/trademarks/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["success"] is False
    assert "不存在" in _err(resp.json())


def test_batch_delete_empty_returns_400(client):
    resp = client.request("DELETE", "/api/trademarks/batch", json={"ids": []})
    assert resp.status_code == 400
    assert _err(resp.json()) == "未提供要删除的商标 id"


def test_check_without_trademarks_returns_400(client):
    """旧版语义：没有可检查的商标时 400（web_app.py:422）。"""
    resp = client.post("/api/check", json={"ids": []})
    assert resp.status_code == 400
    assert resp.json()["success"] is False


def test_check_with_unknown_ids_returns_400(client):
    """传了 ids 却一条都没匹配上 → 400。

    这是比旧版更严的语义：旧版会静默退化成「检查全部商标」，
    用户勾了 3 条却跑了整表。测试把新语义钉住，防止回归。
    """
    resp = client.post("/api/check", json={"ids": ["no-such-id-1", "no-such-id-2"]})
    assert resp.status_code == 400
    assert resp.json()["error"] == "没有可检查的商标"


def test_export_json(client):
    client.post("/api/trademarks", json={"brandName": "FASTAPI_EXPORT"})
    resp = client.get("/api/export?format=json")
    assert resp.status_code == 200
    assert resp.json()["trademarks"]


def test_export_bad_format_returns_400(client):
    resp = client.get("/api/export?format=xml")
    assert resp.status_code == 400
    assert _err(resp.json()) == "不支持的格式"


def test_export_csv_has_bom(client):
    """CSV 必须带 BOM，否则 Excel 打开中文列名乱码。"""
    resp = client.get("/api/export?format=csv")
    assert resp.status_code == 200
    assert resp.text.startswith("\ufeff")
    assert resp.text.lstrip("\ufeff").splitlines()[0].startswith("brandName,applicant")


def test_import_empty_file_is_zero_records(client):
    """空文件按「导入 0 条」走完流程，与旧版一致。

    旧版空文件返回「导入 0 条」，E2E（tests/test_import_export.py:105）
    断言提示里含 '0'，因此这里不能报错。
    """
    resp = client.post("/api/import", files={"file": ("t.json", b"", "application/json")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["importedCount"] == 0


def test_import_empty_array_returns_zero(client):
    """有内容但解析出 0 条 → 同样按「导入 0 条」返回。

    旧版对空数组 / 空 trademarks 都返回「导入 0 条」；E2E
    ``test_import_empty_file`` 断言提示含 '0'。为与旧契约一致，这里
    **不**报 400——「内容为空」不是用户传错文件，而是清单本来就没数据。
    """
    resp = client.post(
        "/api/import",
        files={"file": ("t.json", '{"trademarks":[]}', "application/json")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["importedCount"] == 0

    resp = client.post(
        "/api/import",
        files={"file": ("t.json", "[]", "application/json")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["importedCount"] == 0


def test_import_wrong_type_returns_400(client):
    resp = client.post("/api/import", files={"file": ("t.txt", "x", "text/plain")})
    assert resp.status_code == 400
    assert "仅支持 JSON 和 CSV 格式" in _err(resp.json())


def test_import_json_creates_records(client):
    payload = '{"trademarks":[{"brandName":"FASTAPI_IMPORT_1","applicant":"A"},{"brandName":"FASTAPI_IMPORT_2"}]}'
    resp = client.post(
        "/api/import", files={"file": ("t.json", payload, "application/json")}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["importedCount"] == 2
    brands = _brands(client)
    assert "FASTAPI_IMPORT_1" in brands
    assert "FASTAPI_IMPORT_2" in brands


def test_import_skips_duplicates(client):
    client.post("/api/trademarks", json={"brandName": "FASTAPI_IMPORT_DUP"})
    payload = '{"trademarks":[{"brandName":"FASTAPI_IMPORT_DUP"},{"brandName":"FASTAPI_IMPORT_NEW"}]}'
    resp = client.post(
        "/api/import", files={"file": ("t.json", payload, "application/json")}
    )
    assert resp.status_code == 200
    assert resp.json()["importedCount"] == 1


def test_check_unknown_ids_returns_400(client):
    """传了 ids 却一条都没匹配上 → 400。

    比旧版更严：旧版会静默退化成「检查全部商标」，
    用户勾了 3 条却跑了整表。测试把新语义钉住，防止回归。
    """
    resp = client.post("/api/check", json={"ids": ["no-such-id-1", "no-such-id-2"]})
    assert resp.status_code == 400
    assert _err(resp.json()) == "没有可检查的商标"


def test_check_status_has_no_success_envelope(client):
    """``/api/check/status`` 刻意不套 success 外壳（旧版契约）。

    前端 ``app.js:517-518`` 直接读 ``data.status`` / ``data.result``。
    """
    resp = client.get("/api/check/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "success" not in body
    assert body["status"] in ("idle", "running", "completed", "error")


def test_request_id_header_and_body_agree(client):
    """X-Request-Id 响应头与响应体 request_id 必须一致。"""
    resp = client.get("/api/trademarks")
    assert resp.headers.get("x-request-id") == resp.json()["request_id"]


def test_upstream_request_id_is_reused(client):
    """上游传了 X-Request-Id 就复用，不另生成。"""
    resp = client.get("/api/trademarks", headers={"X-Request-Id": "upstream-fixed-id"})
    assert resp.headers.get("x-request-id") == "upstream-fixed-id"
    assert resp.json()["request_id"] == "upstream-fixed-id"


def test_unhandled_500_keeps_request_id(client, monkeypatch):
    """未处理异常（500）时 X-Request-Id 头与响应体 request_id 仍要一致。

    根因：Starlette 把 (500, Exception) handler 交给最外层
    ServerErrorMiddleware，等异常冒到 handler 时
    RequestContextMiddleware 的 finally 已把 contextvars reset——
    中间件因此在进入内层前把 request_id 同时写进 scope["state"]，
    handler 从 ``request.state.request_id`` 取回。
    """
    from app.services.trademark_service import TrademarkService

    def _boom(_self):
        raise RuntimeError("探针注入的未处理异常")

    # 在 service 方法上注入异常：让失败发生在 handler 调用链里
    # （与真实 500 场景同路径），而不是 FastAPI 依赖解析期。
    # ``raise_server_exceptions=False`` 让 500 走完整 handler 链而不是
    # 直接抛回测试进程（默认 True 只适合「不该有 500」的用例）。
    monkeypatch.setattr(TrademarkService, "list_all", _boom)
    from fastapi.testclient import TestClient as _TC

    with _TC(client.app, raise_server_exceptions=False) as c2:
        resp = c2.get("/api/trademarks")
    assert resp.status_code == 500
    rid = resp.headers.get("x-request-id")
    assert rid, "500 响应头缺失 X-Request-Id"
    assert resp.json()["request_id"] == rid


def test_reports_list(client):
    resp = client.get("/api/reports")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    # ``data`` 是数组本身（与旧 Flask 版一致），不是 {reports:[...]}
    assert isinstance(body["data"], list)


def test_report_detail_missing_returns_404(client):
    resp = client.get("/api/reports/nope.json")
    assert resp.status_code == 404


def test_schema_endpoint(client):
    """契约端点必须仍返回仓库里的 api-schema.json。"""
    resp = client.get("/api/schema")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["owner"] == "app/api/v1/"
    assert body["data"]["version"]
