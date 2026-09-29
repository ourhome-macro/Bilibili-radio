from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask, session

from admin_service import AdminService
from database import LEGACY_OWNER_USER_ID, get_connection
from identity_service import IdentityService
from oidc_auth import OIDCAuth


OIDC_ENV = {
    "AUTH_MODE": "oidc",
    "APP_SECRET_KEY": "test-secret-that-is-not-used-in-production",
    "APP_EXTERNAL_URL": "https://radio.example.test",
    "OIDC_ISSUER_URL": "https://id.example.test",
    "OIDC_CLIENT_ID": "client",
    "OIDC_CLIENT_SECRET": "secret",
    "OIDC_BOOTSTRAP_ADMIN_ISSUER": "",
    "OIDC_BOOTSTRAP_ADMIN_SUBJECT": "",
    "OIDC_ADMIN_GROUP": "",
}


class OIDCConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.identity = IdentityService(Path(self.temp_dir.name) / "identity.sqlite3")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_oidc_requires_fixed_external_origin(self):
        flask_app = Flask(__name__)
        flask_app.secret_key = OIDC_ENV["APP_SECRET_KEY"]
        env = {**OIDC_ENV, "APP_EXTERNAL_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaisesRegex(RuntimeError, "APP_EXTERNAL_URL"):
                OIDCAuth(flask_app, self.identity)

    def test_next_url_rejects_backslash_and_encoded_backslash(self):
        self.assertEqual(OIDCAuth._safe_next_url(r"/\evil.example"), "/")
        self.assertEqual(OIDCAuth._safe_next_url("/%5Cevil.example"), "/")
        self.assertEqual(OIDCAuth._safe_next_url("/#/likes"), "/#/likes")

    def test_callback_replaces_old_session_and_sets_hardened_cookie(self):
        flask_app = Flask(__name__)
        flask_app.secret_key = OIDC_ENV["APP_SECRET_KEY"]
        with patch.dict(os.environ, OIDC_ENV, clear=False):
            oidc = OIDCAuth(flask_app, self.identity)

        user = self.identity.authenticate_oidc(
            issuer=OIDC_ENV["OIDC_ISSUER_URL"],
            subject="listener",
            claims={"name": "Listener"},
        )
        old_token, _ = self.identity.create_session(user["id"])
        oidc.client = Mock()
        oidc.client.authorize_access_token.return_value = {
            "userinfo": {
                "iss": OIDC_ENV["OIDC_ISSUER_URL"],
                "sub": "listener",
                "name": "Listener",
            }
        }

        headers = {"Cookie": f"{oidc.cookie_name}={old_token}"}
        with flask_app.test_request_context("/api/session/callback", headers=headers):
            session["oidc_next"] = "/#/likes"
            response, new_token, _ = oidc.finish_login()

        self.assertEqual(response.location, "/#/likes")
        self.assertIsNone(self.identity.resolve_session(old_token))
        self.assertIsNotNone(self.identity.resolve_session(new_token))
        cookie = response.headers["Set-Cookie"]
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)


class AuthorizationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "authorization.sqlite3"
        self.identity = IdentityService(self.db_path)
        self.admin = AdminService(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_role_promotion_revokes_old_session_and_writes_audit_atomically(self):
        user = self.identity.authenticate_oidc(
            issuer="https://id.example.test",
            subject="listener",
            claims={"name": "Listener"},
        )
        old_token, _ = self.identity.create_session(user["id"])
        updated = self.admin.set_role(
            user["id"],
            "admin",
            actor_user_id=LEGACY_OWNER_USER_ID,
            request_id="request-1",
        )

        self.assertEqual(updated["role"], "admin")
        self.assertIsNone(self.identity.resolve_session(old_token))
        with get_connection(self.db_path) as conn:
            audit = conn.execute(
                "SELECT action, request_id FROM admin_audit_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(audit["action"], "user.role_updated")
        self.assertEqual(audit["request_id"], "request-1")

    def test_unauthenticated_and_regular_users_cannot_read_admin_api(self):
        import app as app_module

        oidc = app_module.oidc_auth
        original_mode = oidc.mode
        original_identity = oidc.identity_service
        try:
            oidc.mode = "oidc"
            oidc.identity_service = self.identity
            with patch.object(app_module, "identity_service", self.identity), patch.object(
                app_module, "admin_service", self.admin
            ):
                client = app_module.app.test_client()
                self.assertEqual(client.get("/api/admin/users").status_code, 401)

                user = self.identity.authenticate_oidc(
                    issuer="https://id.example.test",
                    subject="listener",
                    claims={"name": "Listener"},
                )
                token, _ = self.identity.create_session(user["id"])
                client.set_cookie(oidc.cookie_name, token)
                self.assertEqual(client.get("/api/admin/users").status_code, 403)
        finally:
            oidc.mode = original_mode
            oidc.identity_service = original_identity

    def test_mutation_requires_csrf_and_valid_token_allows_logout(self):
        import app as app_module

        user = self.identity.authenticate_oidc(
            issuer="https://id.example.test",
            subject="listener",
            claims={"name": "Listener"},
        )
        token, _ = self.identity.create_session(user["id"])
        oidc = app_module.oidc_auth
        original_mode = oidc.mode
        original_identity = oidc.identity_service
        try:
            oidc.mode = "oidc"
            oidc.identity_service = self.identity
            client = app_module.app.test_client()
            client.set_cookie(oidc.cookie_name, token)
            self.assertEqual(client.post("/api/session/logout").status_code, 403)
            self.assertEqual(
                client.post(
                    "/api/session/logout",
                    headers={"X-CSRF-Token": oidc.csrf_token(token)},
                ).status_code,
                200,
            )
            self.assertIsNone(self.identity.resolve_session(token))
        finally:
            oidc.mode = original_mode
            oidc.identity_service = original_identity


class MonitoringSecurityTests(unittest.TestCase):
    def test_metrics_require_bearer_token(self):
        import app as app_module

        client = app_module.app.test_client()
        env = {"ENABLE_METRICS": "true", "METRICS_BEARER_TOKEN": "metrics-test-token"}
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(client.get("/internal/metrics").status_code, 401)
            response = client.get(
                "/internal/metrics",
                headers={"Authorization": "Bearer metrics-test-token"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"bilibili_radio_http_requests_total", response.data)

    def test_admin_summary_reads_traffic_from_prometheus(self):
        with tempfile.TemporaryDirectory() as directory:
            admin = AdminService(
                Path(directory) / "admin.sqlite3",
                prometheus_url="http://prometheus:9090",
            )

            def fake_get(_url, *, params, timeout):
                query = params["query"]
                value = "12.5" if "histogram_quantile" in query else (
                    "0.02" if 'status=~"5.."' in query else "42"
                )
                response = Mock()
                response.json.return_value = {
                    "status": "success",
                    "data": {"result": [{"value": [0, value]}]},
                }
                return response

            with patch("admin_service.requests.get", side_effect=fake_get):
                traffic = admin.summary("7d")["traffic"]

        self.assertEqual(traffic["requests"], 42)
        self.assertEqual(traffic["errorRate"], 0.02)
        self.assertEqual(traffic["p95LatencyMs"], 12.5)


if __name__ == "__main__":
    unittest.main()
