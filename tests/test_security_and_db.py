from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import app as app_module
import core
from scripts.db_tools import backup_database, restore_database, verify_database

ROOT = Path(__file__).resolve().parents[1]
app = app_module.app
_rate_limit_hits = app_module._rate_limit_hits


class SecurityRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.db"
        db = sqlite3.connect(self.db_path)
        try:
            db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT, username TEXT, email TEXT, password_hash TEXT)")
        finally:
            db.close()
        self.old_db = app_module.DB
        self.old_registration = app.config["ALLOW_REGISTRATION"]
        self.old_cookie_secure = app.config["SESSION_COOKIE_SECURE"]
        app_module.DB = self.db_path
        core.DB = self.db_path
        app.config["ALLOW_REGISTRATION"] = False
        app.config["SESSION_COOKIE_SECURE"] = False
        _rate_limit_hits.clear()
        self.client = app.test_client()
        page = self.client.get("/login")
        self.assertEqual(page.status_code, 200)
        import re
        match = re.search(rb'<meta name="csrf-token" content="([^"]+)"', page.data)
        self.assertIsNotNone(match)
        self.csrf = match.group(1).decode("ascii")
        cookie = page.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("; Secure", cookie)

    def tearDown(self) -> None:
        app_module.DB = self.old_db
        core.DB = self.old_db
        app.config["ALLOW_REGISTRATION"] = self.old_registration
        app.config["SESSION_COOKIE_SECURE"] = self.old_cookie_secure
        _rate_limit_hits.clear()
        self.temp.cleanup()

    def test_private_steam_routes_require_login(self) -> None:
        self.assertEqual(self.client.get("/api/steam/status").status_code, 401)
        self.assertEqual(self.client.post("/api/steam/sync").status_code, 401)

    def test_state_changing_auth_routes_require_csrf_token(self) -> None:
        response = self.client.post("/api/auth/login", json={"identity": "nobody", "password": "bad"})
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            "/api/auth/login",
            json={"identity": "nobody", "password": "bad"},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 401)

    def test_login_rate_limit_returns_429(self) -> None:
        for _ in range(5):
            response = self.client.post(
                "/api/auth/login",
                json={"identity": "nobody", "password": "bad"},
                headers={"X-CSRF-Token": self.csrf},
            )
            self.assertEqual(response.status_code, 401)
        response = self.client.post(
            "/api/auth/login",
            json={"identity": "nobody", "password": "bad"},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response.headers)

    def test_production_registration_is_disabled(self) -> None:
        self.assertEqual(self.client.get("/register").status_code, 404)
        response = self.client.post(
            "/api/auth/register",
            json={"name": "New User", "username": "newuser", "email": "new@example.test", "password": "long-enough-password"},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 404)

    def test_wsgi_import_does_not_initialize_or_mutate_database(self) -> None:
        absent_db = Path(self.temp.name) / "should-not-be-created.db"
        env = os.environ.copy()
        env.update({
            "APP_ENV": "production",
            "SECRET_KEY": "test-only-secret-that-is-more-than-32-characters",
            "ALLOW_REGISTRATION": "0",
            "SESSION_COOKIE_SECURE": "1",
            "GAMESVAULT_DB_PATH": str(absent_db),
        })
        subprocess.run([sys.executable, "-c", "import wsgi"], cwd=ROOT, env=env, check=True)
        self.assertFalse(absent_db.exists())


class DatabaseBackupTests(unittest.TestCase):
    def test_backup_verify_restore_preserves_database(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / "source.db"
            backup = root / "backup.db"
            target = root / "restored.db"
            db = sqlite3.connect(original)
            try:
                db.execute("CREATE TABLE preserve_me(value TEXT NOT NULL)")
                db.execute("INSERT INTO preserve_me VALUES(?)", ("safe value",))
                db.commit()
            finally:
                db.close()
            backup_database(original, backup)
            verify_database(backup)
            db = sqlite3.connect(target)
            try:
                db.execute("CREATE TABLE preserve_me(value TEXT NOT NULL)")
                db.execute("INSERT INTO preserve_me VALUES(?)", ("newer value",))
                db.commit()
            finally:
                db.close()
            restore_database(backup, target)
            db = sqlite3.connect(target)
            try:
                self.assertEqual(db.execute("SELECT value FROM preserve_me").fetchone()[0], "safe value")
            finally:
                db.close()
            self.assertTrue(list(root.glob("restored.db.pre-restore-*.db")))

    def test_schema_migration_preserves_existing_user_rows(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "migration-copy.db"
            # Seed a fresh DB (no shipped user data) via init_db in a subprocess.
            seed_env = os.environ.copy()
            seed_env.update({
                "APP_ENV": "testing",
                "SECRET_KEY": "test-only-secret-that-is-more-than-32-characters",
                "ALLOW_REGISTRATION": "1",
                "GAMESVAULT_DB_PATH": str(database),
            })
            subprocess.run(
                [sys.executable, "-c", "from app import init_db; init_db()"],
                cwd=ROOT, env=seed_env, check=True, capture_output=True, text=True,
            )
            self.assertTrue(database.is_file())
            tables = ("users", "user_games", "wishlist", "game_plans", "user_achievements", "user_settings", "user_platforms")

            def snapshot() -> dict[str, list[tuple]]:
                db = sqlite3.connect(database)
                try:
                    columns = {
                        "users": "id,name,username,email,password_hash,created_at",
                        "user_games": "id,user_id,game_id,status,hours_played,personal_rating,notes,completion,favorite,priority,added_at",
                        "wishlist": "id,user_id,game_id,added_at",
                        "game_plans": "id,user_id,game_id,plan_date,planned_hours",
                        "user_achievements": "id,user_id,achievement_id,completed",
                        "user_settings": "user_id,key,value",
                        "user_platforms": "user_id,platform_id",
                    }
                    return {table: db.execute(f"SELECT {columns[table]} FROM [{table}] ORDER BY rowid").fetchall() for table in tables}
                finally:
                    db.close()

            before = snapshot()
            env = os.environ.copy()
            env.update({
                "APP_ENV": "production",
                "SECRET_KEY": "test-only-secret-that-is-more-than-32-characters",
                "ALLOW_REGISTRATION": "0",
                "SESSION_COOKIE_SECURE": "1",
                "GAMESVAULT_DB_PATH": str(database),
            })
            subprocess.run([sys.executable, "scripts/migrate_db.py"], cwd=ROOT, env=env, check=True, capture_output=True, text=True)
            self.assertEqual(snapshot(), before)
            db = sqlite3.connect(database)
            try:
                self.assertIn("last_played_at", {row[1] for row in db.execute("PRAGMA table_info(user_games)")})
                self.assertTrue(all(row[0] is None for row in db.execute("SELECT last_played_at FROM user_games")))
            finally:
                db.close()
            verify_database(database)


if __name__ == "__main__":
    unittest.main()
