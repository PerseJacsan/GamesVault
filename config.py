"""Environment-backed configuration for local GamesVault runs."""

from __future__ import annotations

import os
import secrets
import warnings
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
if APP_ENV not in {"development", "production", "testing"}:
    raise RuntimeError(
        f"Invalid APP_ENV {APP_ENV!r}: expected 'development', 'production' or 'testing'."
    )
ALLOW_REGISTRATION = os.getenv(
    "ALLOW_REGISTRATION", "0" if APP_ENV == "production" else "1"
).strip().lower() in {"1", "true", "yes", "on"}
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
_PLACEHOLDER_SECRET = "replace-with-a-long-random-secret-of-at-least-32-characters"
if APP_ENV == "production":
    if len(SECRET_KEY) < 32 or SECRET_KEY == _PLACEHOLDER_SECRET:
        raise RuntimeError(
            "Production requires SECRET_KEY to be set to a random value of at least 32 characters. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
if not SECRET_KEY:
    # Dev/Testing only: generate an ephemeral random secret so sessions are not
    # forgeable from a known hard-coded string. Restarts invalidate sessions,
    # which is safe here. Configure a stable SECRET_KEY for longer sessions.
    SECRET_KEY = secrets.token_urlsafe(48)
    if APP_ENV == "development":
        warnings.warn(
            "SECRET_KEY is not set; using a random ephemeral development secret. "
            "Set SECRET_KEY in .env for persistent sessions.",
            stacklevel=2,
        )

legacy_database = BASE_DIR / "gamesvault.db"
instance_database = BASE_DIR / "instance" / "gamesvault.db"
DB_PATH = Path(
    os.getenv("GAMESVAULT_DB_PATH", str(legacy_database if legacy_database.exists() else instance_database))
).expanduser().resolve()

SESSION_COOKIE_SECURE = os.getenv(
    "SESSION_COOKIE_SECURE", "1" if APP_ENV == "production" else "0"
).strip().lower() in {"1", "true", "yes", "on"}
if APP_ENV == "production":
    if not SESSION_COOKIE_SECURE:
        raise RuntimeError("Production requires SESSION_COOKIE_SECURE to be enabled (it cannot be disabled).")
    SESSION_COOKIE_SECURE = True
DEBUG = os.getenv("FLASK_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
if APP_ENV == "production":
    DEBUG = False
HOST = os.getenv("HOST", "127.0.0.1")
try:
    PORT = int(os.getenv("PORT", "5000"))
except ValueError:
    raise RuntimeError("PORT must be a number between 1 and 65535.") from None
if not 1 <= PORT <= 65535:
    raise RuntimeError("PORT must be a number between 1 and 65535.")
