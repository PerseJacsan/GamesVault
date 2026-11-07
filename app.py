"""Flask application wiring.

All shared state (Flask app, DB helpers, init_db) lives in ``core`` so that
``routes``/``services`` can import from ``core`` without a circular import.
This module re-exports it for backward compatibility (``from app import ...``,
``run.py``, ``wsgi.py``, tests) and registers the blueprints.

Run with either:
    python app.py        # direct (no circular import)
    python run.py
    gunicorn wsgi:application
"""
from core import (
    ALLOW_REGISTRATION,
    BASE,
    DB,
    app,
    csrf_token,
    current_user,
    current_user_id,
    game_sql,
    init_db,
    ok,
    rate_limit,
    row,
    rows,
)
from core import _rate_limit_hits
from config import DEBUG, HOST, PORT

from routes.auth import auth_api
from routes.games import games_api
from routes.library import library_routes
from routes.main import main_pages
from services.library import library_api
from services.profile import profile_api

app.register_blueprint(auth_api)
app.register_blueprint(games_api)
app.register_blueprint(library_routes)
app.register_blueprint(main_pages)
app.register_blueprint(library_api)
app.register_blueprint(profile_api)

# Re-export for callers that do `from app import DB` and then mutate it in tests.
# Keep __all__ explicit.
__all__ = [
    "app",
    "DB",
    "BASE",
    "ALLOW_REGISTRATION",
    "init_db",
    "csrf_token",
    "current_user",
    "current_user_id",
    "game_sql",
    "ok",
    "rate_limit",
    "row",
    "rows",
    "conn",
]
from core import conn  # noqa: E402,F401

if __name__ == "__main__":
    init_db()
    app.run(host=HOST, port=PORT, debug=DEBUG)
