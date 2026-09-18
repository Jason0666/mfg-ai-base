# -*- coding: utf-8 -*-
"""M3 生产化测试：鉴权/角色/模块权限/审计持久化/统计/PDF导出/降级/限流。

两种模式：
- DEMO（settings 原值）：登录可用、业务接口不拦、admin 接口开放
- PROD（monkeypatch settings.DEMO_MODE=False）：匿名 401、viewer 白名单 403、
  admin 独占 /api/admin/*、限流 429

运行：cd backend && python -m pytest tests/test_m3.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.main import _rate_buckets, app  # noqa: E402

DEMO_ORIG = settings.DEMO_MODE


@pytest.fixture(scope="module")
def demo_client():
    with TestClient(app) as c:
        yield c


def _login(client, username, password):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    body = resp.json()
    return resp, body


# ---------------------------------------------------------------------------
# 纯单元：口令哈希与 JWT
# ---------------------------------------------------------------------------
class TestAuthPrimitives:
    def test_password_hash_roundtrip(self):
        from app.core.auth import hash_password, verify_password

        h = hash_password("Admin@123")
        assert h.startswith("pbkdf2$120000$")
        assert verify_password("Admin@123", h)
        assert not verify_password("wrong", h)
        assert not verify_password("Admin@123", "garbage")

    def test_jwt_roundtrip(self):
        from app.core.auth import decode_token, issue_token

        tok = issue_token(3, "analyst", "analyst")
        payload = decode_token(tok)
        assert payload and payload["uid"] == 3 and payload["role"] == "analyst"

    def test_jwt_expired_and_tampered(self):
        from app.core.auth import decode_token, issue_token

        expired = issue_token(1, "admin", "admin", ttl_seconds=-10)
        assert decode_token(expired) is None
        tok = issue_token(1, "admin", "admin")
        assert decode_token(tok[:-2] + "xx") is None
        assert decode_token("not.a.jwt") is None


# ---------------------------------------------------------------------------
# DEMO 模式（§5.3：业务接口关闭鉴权，登录接口真实可用）
# ---------------------------------------------------------------------------
class TestDemoMode:
    def test_health(self, demo_client):
        r = demo_client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["code"] == 0

    def test_seed_users_bootstrapped(self):
        from app.core.db import get_user_by_username

        for u in ("admin", "analyst", "viewer"):
            user = get_user_by_username(u)
            assert user and user["role"] in ("admin", "analyst", "viewer")

    def test_login_ok_and_failed(self, demo_client):
        resp, body = _login(demo_client, "admin", "Admin@123")
        assert resp.status_code == 200 and body["code"] == 0
        assert body["data"]["user"]["role"] == "admin"
        assert body["data"]["token"].count(".") == 2
        resp, body = _login(demo_client, "admin", "wrong-password")
        assert body["code"] == 1

    def test_demo_invoke_not_blocked(self, demo_client):
        # DEMO 模式：无 token 的 invoke 也不应 401/403（空 question 走兜底文案）
        r = demo_client.post(
            "/api/modules/m04_sop_qa/invoke",
            json={"inputs": {"question": ""}, "stream": False},
        )
        assert r.status_code == 200, r.text
        assert r.status_code != 401 and r.status_code != 403

    def test_demo_admin_open(self, demo_client):
        assert demo_client.get("/api/admin/stats").status_code == 200
        assert demo_client.get("/api/admin/audit").status_code == 200

    def test_audit_persisted_to_db(self, demo_client):
        from app.core.audit import query_audit

        # 前面的登录测试已写入 action=login 记录
        data = query_audit(action="login", limit=10)
        assert data["total"] >= 1
        assert any(it["action"] == "login" for it in data["items"])

    def test_stats_shape(self, demo_client):
        from app.core.audit import stats

        s = stats()
        for key in ("total_invocations", "success", "degraded", "failed",
                    "avg_latency_ms", "by_module", "trend_7d"):
            assert key in s
        assert len(s["trend_7d"]) == 7

    def test_query_audit_filters(self, demo_client):
        from app.core.audit import query_audit

        data = query_audit(module="m03_quality_8d", limit=5)
        assert all(it["module_code"] == "m03_quality_8d" for it in data["items"])
        data = query_audit(q="login", limit=5)
        assert isinstance(data["items"], list)


# ---------------------------------------------------------------------------
# PDF 导出（M3）
# ---------------------------------------------------------------------------
class TestPdfExport:
    def test_render_to_pdf_magic(self):
        from app.core.template import render_to_pdf

        tpl = str(Path(__file__).resolve().parents[1] / "app/modules/m03_quality_8d/seed/template_8d.yaml")
        data = {
            "meta": [("报告编号", "8D-TEST-0001"), ("异常现象", "注塑气泡")],
            "sections": {
                "D1": {"paragraphs": ["组长：测试"]},
                "D2": {"bullets": ["不良率上升"], "tables": [
                    {"title": "表", "headers": ["A", "B"], "rows": [["1", "2"]]}]},
            },
            "appendix": [],
        }
        out = Path("./data/exports/_test_m3.pdf")
        try:
            render_to_pdf(tpl if Path(tpl).exists() else None, data, str(out))
            content = out.read_bytes()
            assert content[:4] == b"%PDF"
            assert len(content) > 1000
        finally:
            out.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# PROD 模式（DEMO_MODE=false）
# ---------------------------------------------------------------------------
class TestProdMode:
    @pytest.fixture(scope="class")
    def prod(self, demo_client):
        """同一 app，切换 PROD 模式（中间件按请求时 settings 判断）。"""
        settings.DEMO_MODE = False
        _rate_buckets.clear()
        yield
        settings.DEMO_MODE = DEMO_ORIG
        _rate_buckets.clear()

    def test_anonymous_invoke_401(self, prod, demo_client):
        r = demo_client.post("/api/modules/m04_sop_qa/invoke",
                             json={"inputs": {"question": ""}})
        assert r.status_code == 401

    def test_anonymous_admin_401(self, prod, demo_client):
        assert demo_client.get("/api/admin/audit").status_code == 401
        assert demo_client.get("/api/admin/stats").status_code == 401

    def test_anonymous_me_and_public_paths(self, prod, demo_client):
        assert demo_client.get("/api/health").status_code == 200
        assert demo_client.get("/api/registry/modules").status_code == 200
        r = demo_client.get("/api/auth/me").json()
        assert r["code"] == 1  # PROD 下匿名 me 返回未登录

    def test_roles_login(self, prod, demo_client):
        for user, pwd, role in [("admin", "Admin@123", "admin"),
                                ("analyst", "Analyst@123", "analyst"),
                                ("viewer", "Viewer@123", "viewer")]:
            resp, body = _login(demo_client, user, pwd)
            assert body["code"] == 0, f"{user} login failed: {body}"
            assert body["data"]["user"]["role"] == role

    def test_me_with_token(self, prod, demo_client):
        _, body = _login(demo_client, "viewer", "Viewer@123")
        token = body["data"]["token"]
        r = demo_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.json()["data"]["username"] == "viewer"

    def test_admin_role_gate(self, prod, demo_client):
        _, body = _login(demo_client, "analyst", "Analyst@123")
        headers = {"Authorization": f"Bearer {body['data']['token']}"}
        r = demo_client.get("/api/admin/audit", headers=headers)
        assert r.status_code == 403
        _, body = _login(demo_client, "admin", "Admin@123")
        headers = {"Authorization": f"Bearer {body['data']['token']}"}
        r = demo_client.get("/api/admin/audit?module=m03_quality_8d&status=success&limit=10",
                            headers=headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert "items" in data and "total" in data
        r = demo_client.get("/api/admin/stats", headers=headers)
        assert r.status_code == 200 and "trend_7d" in r.json()["data"]

    def test_viewer_module_whitelist_403(self, prod, demo_client):
        from app.core.db import get_user_by_username, grant_module

        viewer = get_user_by_username("viewer")
        # 给 viewer 配置白名单（只含 m03）→ 访问 m04 应 403
        grant_module(viewer["id"], "m03_quality_8d")
        _, body = _login(demo_client, "viewer", "Viewer@123")
        headers = {"Authorization": f"Bearer {body['data']['token']}"}
        r = demo_client.post("/api/modules/m04_sop_qa/invoke",
                             json={"inputs": {"question": ""}}, headers=headers)
        assert r.status_code == 403
        r = demo_client.post("/api/modules/m03_quality_8d/invoke",
                             json={"inputs": {}}, headers=headers)
        assert r.status_code != 403 and r.status_code != 401
        # 清空白名单恢复默认全放行
        from app.core.db import get_conn, ph

        conn = get_conn()
        try:
            conn.execute(f"DELETE FROM app_module_permission WHERE user_id={ph()}", (viewer["id"],))
            conn.commit()
        finally:
            conn.close()

    def test_upload_requires_login(self, prod, demo_client):
        r = demo_client.post("/api/files/upload", files={"file": ("a.txt", b"hi")})
        assert r.status_code == 401

    def test_rate_limit_429(self, prod, demo_client):
        _rate_buckets.clear()
        settings.RATE_LIMIT_PER_MINUTE = 3
        try:
            codes = []
            for _ in range(5):
                r = demo_client.get("/api/health")  # health 不限流
                codes.append(r.status_code)
            assert all(c == 200 for c in codes)
            codes = []
            for _ in range(5):
                r = demo_client.get("/api/registry/modules")
                codes.append(r.status_code)
            assert 429 in codes
        finally:
            settings.RATE_LIMIT_PER_MINUTE = 20
            _rate_buckets.clear()

    def test_llm_degraded_placeholder(self, prod, demo_client):
        """LLM Key 清空时 invoke 不崩、走降级占位文案（SSE 全程完成）。"""
        key_orig = settings.LLM_API_KEY
        settings.LLM_API_KEY = ""
        try:
            _, body = _login(demo_client, "admin", "Admin@123")
            headers = {"Authorization": f"Bearer {body['data']['token']}"}
            r = demo_client.post("/api/modules/m04_sop_qa/invoke",
                                 json={"inputs": {"question": "银纹怎么处理"},
                                       "stream": False},
                                 headers=headers)
            assert r.status_code == 200
            assert "event: result" in r.text  # 走完检索→降级→结构化输出全链路
        finally:
            settings.LLM_API_KEY = key_orig
