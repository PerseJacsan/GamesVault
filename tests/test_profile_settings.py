from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import app as app_module
import core


ROOT = Path(__file__).resolve().parents[1]


class ProfileSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "profile-test.db"
        self.old_db = app_module.DB
        app_module.DB = self.database
        core.DB = self.database
        app_module.init_db()
        self.client = app_module.app.test_client()
        self.client.get("/login")
        with self.client.session_transaction() as state:
            state["user_id"] = 1
            state["_csrf_token"] = "profile-test-csrf"
        self.headers = {"X-CSRF-Token": "profile-test-csrf"}

    def tearDown(self) -> None:
        app_module.DB = self.old_db
        core.DB = self.old_db
        self.temp.cleanup()

    def test_account_information_and_description_load_and_save_as_json(self) -> None:
        response = self.client.get("/api/profile")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/json")
        self.assertTrue({"name", "username", "email", "profile_description"} <= response.json.keys())
        saved = self.client.put(
            "/api/profile", json={"profile_description": "RPGs and local co-op."}, headers=self.headers
        )
        self.assertEqual(saved.status_code, 200)
        reloaded = self.client.get("/api/profile")
        self.assertEqual(reloaded.json["profile_description"], "RPGs and local co-op.")
        self.assertEqual(self.client.put("/api/profile", json={"profile_description": "x" * 281}, headers=self.headers).status_code, 400)

    def test_profile_api_is_authenticated_json_and_routes_are_not_real_social_features(self) -> None:
        anonymous = app_module.app.test_client()
        denied = anonymous.get("/api/profile")
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.mimetype, "application/json")
        self.assertEqual(self.client.get("/api/friends").status_code, 404)
        endpoints = {rule.rule for rule in app_module.app.url_map.iter_rules()}
        self.assertNotIn("/api/presence", endpoints)
        self.assertIn("GET", next(rule.methods for rule in app_module.app.url_map.iter_rules() if rule.rule == "/api/profile"))
        self.assertIn("PUT", next(rule.methods for rule in app_module.app.url_map.iter_rules() if rule.rule == "/api/profile"))

    def test_new_schema_does_not_create_friend_request_tables(self) -> None:
        application = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("CREATE TABLE IF NOT EXISTS friend_requests", application)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS friendships", application)

    def test_account_and_preview_navigation_are_present(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"friends.js", response.data)
        app_js = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
        style = (ROOT / "static/css/style.css").read_text(encoding="utf-8")
        self.assertIn("const renderFriendsPreview=(()=>{", app_js)
        self.assertIn("else if(name==='friends')renderFriendsPreview()", app_js)
        self.assertNotIn("window.friends", app_js)
        self.assertIn("response.headers.get('content-type')", app_js)
        self.assertIn("if(!response.ok)", app_js)
        self.assertIn("unexpected non-JSON response", app_js)
        self.assertIn('href="#account"', app_js)
        self.assertIn('href="#change-password"', app_js)
        self.assertIn('href="#friends"', app_js)
        self.assertIn('href="#contact"', app_js)
        self.assertIn("window.addEventListener('hashchange',route)", app_js)
        self.assertIn("document.addEventListener('DOMContentLoaded',route,{once:true})", app_js)
        self.assertEqual(app_js.count("{name:'"), 5)
        for name in ("Alex Morgan", "Jordan Lee", "Sam Carter", "Riley Chen", "Taylor Brooks"):
            self.assertIn(name, app_js)
        self.assertIn("Sample profiles are displayed for demonstration purposes", app_js)
        self.assertIn(".friend-presence.is-online i", style)
        self.assertIn(".friend-presence.is-away i", style)
        self.assertIn(".preview-friend-card", style)


if __name__ == "__main__":
    unittest.main()
