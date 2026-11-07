from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import app as app_module
import core
from scripts.db_tools import verify_database


ROOT = Path(__file__).resolve().parents[1]


class LibraryExperienceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "library-test.db"
        self.old_db = app_module.DB
        app_module.DB = self.database
        core.DB = self.database
        app_module.init_db()
        self.client = app_module.app.test_client()
        self.client.get("/login")
        with self.client.session_transaction() as state:
            state["user_id"] = 1
            state["_csrf_token"] = "library-test-csrf"
        self.headers = {"X-CSRF-Token": "library-test-csrf"}
        db = sqlite3.connect(self.database)
        try:
            self.game_id = db.execute("SELECT game_id FROM user_games WHERE user_id=1 ORDER BY game_id LIMIT 1").fetchone()[0]
        finally:
            db.close()

    def tearDown(self) -> None:
        app_module.DB = self.old_db
        core.DB = self.old_db
        self.temp.cleanup()

    def test_browse_filters_and_mark_played(self) -> None:
        response = self.client.get("/api/library/browse?sort=last_played")
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.json["count"], 0)
        self.assertEqual(self.client.get("/api/library/browse?sort=bad").status_code, 400)
        played = self.client.post(f"/api/library/{self.game_id}/played", headers=self.headers)
        self.assertEqual(played.status_code, 200)
        self.assertTrue(played.json["last_played_at"])

    def test_authenticated_app_shell_loads_local_feature_module(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"library-experience.js", response.data)
        self.assertIn(b"app.js", response.data)
        self.assertIn(b'aria-label="Search this section"', response.data)

    def test_game_detail_library_state_is_idempotent_and_achievement_api_is_gone(self) -> None:
        catalogue = self.client.get("/api/games").json
        unowned = next(game for game in catalogue if not game.get("status"))
        game_id = unowned["id"]
        detail = self.client.get(f"/api/games/{game_id}").json
        self.assertFalse(detail["in_library"])
        self.assertNotIn("achievements", detail)
        self.assertEqual(self.client.get(f"/api/games/{game_id}/achievements").status_code, 404)
        for _ in range(2):
            self.assertEqual(self.client.post("/api/library", json={"game_id": game_id}, headers=self.headers).status_code, 201)
        owned = self.client.get(f"/api/games/{game_id}").json
        self.assertTrue(owned["in_library"])
        db = sqlite3.connect(self.database)
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM user_games WHERE user_id=1 AND game_id=?", (game_id,)).fetchone()[0], 1)
        finally:
            db.close()

    def test_game_cards_are_keyboard_accessible_links_without_hover_actions_or_achievements(self) -> None:
        app_js = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
        style = (ROOT / "static/css/style.css").read_text(encoding="utf-8")
        self.assertIn('class="card card-link', app_js)
        self.assertIn('href="#game/${game.id}"', app_js)
        self.assertNotIn('class="card-actions"', app_js)
        self.assertNotIn("achievement", app_js.lower())
        self.assertNotIn(".achievement-", style)
        self.assertIn(".card-link:focus-visible", style)

    def test_filter_toggle_and_chart_use_shared_accessible_styles(self) -> None:
        library_js = (ROOT / "static/js/library-experience.js").read_text(encoding="utf-8")
        style = (ROOT / "static/css/style.css").read_text(encoding="utf-8")
        self.assertIn('class="favorite-filter" id="filter-favorite" type="button" aria-pressed="false"', library_js)
        self.assertIn("button.setAttribute('aria-pressed',String(enabled))", library_js)
        self.assertIn('class="filter-clear" id="clear-library"', library_js)
        self.assertIn('class="filter-clear" id="clear-wish"', library_js)
        self.assertIn("border-radius: 6px 6px 2px 2px", style)
        self.assertIn(".filter-clear,.favorite-filter", style)

    def test_collection_membership_is_unique_and_user_scoped(self) -> None:
        response = self.client.post("/api/collections", json={"name": "Weekend"}, headers=self.headers)
        self.assertEqual(response.status_code, 201)
        collection_id = response.json["id"]
        self.assertEqual(self.client.post("/api/collections", json={"name": "weekend"}, headers=self.headers).status_code, 409)
        path = f"/api/collections/{collection_id}/games"
        self.assertEqual(self.client.post(path, json={"game_id": self.game_id}, headers=self.headers).status_code, 200)
        self.client.post(path, json={"game_id": self.game_id}, headers=self.headers)
        detail = self.client.get(f"/api/collections/{collection_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json["count"], 1)
        self.assertEqual(self.client.delete(f"/api/library/{self.game_id}", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get(f"/api/collections/{collection_id}").json["count"], 0)
        with self.client.session_transaction() as state:
            state["user_id"] = 2
        self.assertEqual(self.client.get(f"/api/collections/{collection_id}").status_code, 404)

    def test_saved_view_and_local_discovery(self) -> None:
        saved = self.client.post("/api/saved-views", json={"name": "My backlog", "filters": {"status": "Backlog", "sort": "title", "view": "grid"}}, headers=self.headers)
        self.assertEqual(saved.status_code, 201)
        self.assertEqual(self.client.post("/api/saved-views", json={"name": "Bad", "filters": {"sort": "raw SQL"}}, headers=self.headers).status_code, 400)
        pick = self.client.get("/api/discovery/pick?mode=random&seed=gamesvault-test")
        self.assertEqual(pick.status_code, 200)
        db = sqlite3.connect(self.database)
        try:
            owned = [g[0] for g in db.execute("SELECT game_id FROM user_games WHERE user_id=1")]
        finally:
            db.close()
        self.assertIn(pick.json["game"]["id"], owned)
        verify_database(self.database)


if __name__ == "__main__":
    unittest.main()
