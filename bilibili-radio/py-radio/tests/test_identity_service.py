from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from admin_service import AdminService
from database import LEGACY_OWNER_USER_ID
from identity_service import IdentityService
from oidc_auth import OIDCAuth


class IdentityServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "identity.sqlite3"
        self.identity = IdentityService(
            self.db_path,
            session_ttl_seconds=3600,
            session_idle_seconds=1800,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_bootstrap_identity_claims_seeded_legacy_admin(self):
        env = {
            "OIDC_BOOTSTRAP_ADMIN_ISSUER": "https://id.example.test",
            "OIDC_BOOTSTRAP_ADMIN_SUBJECT": "owner-subject",
        }
        with patch.dict(os.environ, env, clear=False):
            user = self.identity.authenticate_oidc(
                issuer=env["OIDC_BOOTSTRAP_ADMIN_ISSUER"],
                subject=env["OIDC_BOOTSTRAP_ADMIN_SUBJECT"],
                claims={"name": "Owner"},
            )

        self.assertEqual(user["id"], LEGACY_OWNER_USER_ID)
        self.assertEqual(user["role"], "admin")
        self.assertEqual(user["roleSource"], "bootstrap")

    def test_normal_oidc_user_is_not_admin(self):
        with patch.dict(
            os.environ,
            {
                "OIDC_BOOTSTRAP_ADMIN_ISSUER": "",
                "OIDC_BOOTSTRAP_ADMIN_SUBJECT": "",
                "OIDC_ADMIN_GROUP": "",
            },
            clear=False,
        ):
            user = self.identity.authenticate_oidc(
                issuer="https://id.example.test",
                subject="regular-user",
                claims={"preferred_username": "listener"},
            )

        self.assertNotEqual(user["id"], LEGACY_OWNER_USER_ID)
        self.assertEqual(user["role"], "user")

    def test_session_is_opaque_resolvable_and_revocable(self):
        token, created_user = self.identity.create_session(LEGACY_OWNER_USER_ID)
        self.assertNotIn(LEGACY_OWNER_USER_ID, token)
        self.assertEqual(self.identity.resolve_session(token)["id"], created_user["id"])

        self.identity.revoke_session(token)
        self.assertIsNone(self.identity.resolve_session(token))

    def test_owner_easter_egg_toggles_both_directions(self):
        admin = AdminService(self.db_path)
        self.assertEqual(admin.toggle_owner_admin(LEGACY_OWNER_USER_ID)["role"], "user")
        self.assertEqual(admin.toggle_owner_admin(LEGACY_OWNER_USER_ID)["role"], "admin")

    def test_csrf_token_is_bound_to_opaque_session(self):
        flask_app = Flask(__name__)
        flask_app.secret_key = "test-secret-that-is-not-used-in-production"
        env = {
            "AUTH_MODE": "oidc",
            "APP_SECRET_KEY": "test-secret-that-is-not-used-in-production",
            "OIDC_ISSUER_URL": "https://id.example.test",
            "OIDC_CLIENT_ID": "client",
            "OIDC_CLIENT_SECRET": "secret",
            "APP_EXTERNAL_URL": "https://radio.example.test",
        }
        with patch.dict(os.environ, env, clear=False):
            oidc = OIDCAuth(flask_app, self.identity)

        token = "opaque-session-token"
        csrf = oidc.csrf_token(token)
        self.assertTrue(oidc.validate_csrf(token, csrf))
        self.assertFalse(oidc.validate_csrf("another-session", csrf))


class AppIdentityEndpointTests(unittest.TestCase):
    def test_disabled_mode_exposes_legacy_owner_and_local_easter_egg(self):
        import app as app_module

        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "local-owner.sqlite3"
            identity = IdentityService(db_path)
            admin = AdminService(db_path)
            oidc = app_module.oidc_auth
            with patch.object(oidc, "identity_service", identity), patch.object(
                app_module, "identity_service", identity
            ), patch.object(app_module, "admin_service", admin):
                client = app_module.app.test_client()
                session_response = client.get("/api/session/me")
                self.assertEqual(session_response.status_code, 200)
                session_data = session_response.get_json()["data"]
                self.assertTrue(session_data["authenticated"])
                self.assertEqual(session_data["user"]["id"], LEGACY_OWNER_USER_ID)

                original_role = session_data["user"]["role"]
                toggle_response = client.post("/api/admin/genshin")
                self.assertEqual(toggle_response.status_code, 200)
                toggled_role = toggle_response.get_json()["data"]["role"]
                self.assertNotEqual(toggled_role, original_role)

                restore_response = client.post("/api/admin/genshin")
                self.assertEqual(restore_response.status_code, 200)
                self.assertEqual(restore_response.get_json()["data"]["role"], original_role)

    def test_health_checks_do_not_require_application_login(self):
        import app as app_module

        client = app_module.app.test_client()
        self.assertEqual(client.get("/health/live").status_code, 200)
        self.assertEqual(client.get("/health/ready").status_code, 200)

    def test_genshin_route_only_toggles_oidc_claimed_legacy_owner(self):
        import app as app_module

        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "app-identity.sqlite3"
            identity = IdentityService(db_path)
            identity.claim_legacy_owner("https://id.example.test", "owner")
            admin = AdminService(db_path)
            owner_token, _user = identity.create_session(LEGACY_OWNER_USER_ID)

            oidc = app_module.oidc_auth
            original_mode = oidc.mode
            original_identity = oidc.identity_service
            try:
                oidc.mode = "oidc"
                oidc.identity_service = identity
                with patch.object(app_module, "identity_service", identity), patch.object(
                    app_module, "admin_service", admin
                ):
                    client = app_module.app.test_client()
                    client.set_cookie(oidc.cookie_name, owner_token)
                    csrf = oidc.csrf_token(owner_token)
                    demoted = client.post(
                        "/api/admin/genshin",
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(demoted.status_code, 200)
                    self.assertEqual(demoted.get_json()["data"]["role"], "user")

                    owner_token, _user = identity.create_session(LEGACY_OWNER_USER_ID)
                    client.set_cookie(oidc.cookie_name, owner_token)
                    csrf = oidc.csrf_token(owner_token)
                    promoted = client.post(
                        "/api/admin/genshin",
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(promoted.status_code, 200)
                    self.assertEqual(promoted.get_json()["data"]["role"], "admin")
            finally:
                oidc.mode = original_mode
                oidc.identity_service = original_identity


if __name__ == "__main__":
    unittest.main()
