# GamesVault

GamesVault is a local-first game library and catalogue built with Flask, SQLite, HTML, CSS, and vanilla JavaScript. Browse games, organize a personal library, track progress, and discover your next game. Your account and library data are stored in a local SQLite database.

## Features

- Local account registration, login, profile descriptions, and password management
- Game catalogue search, filtering, sorting, details, and artwork
- Library status, playtime, completion, favorites, and personal notes
- Collections, saved views, smart shelves, and last-played tracking
- Wishlist, planner, dashboard statistics, and local recommendations
- Responsive dark interface, accessible navigation, notifications, and page transitions
- Optional Steam catalogue synchronization; the application still runs without a Steam API key
- Friends page with static sample profiles only

## Technology

- Python 3.10 or newer
- Flask 3.x
- SQLite
- `requests` for optional Steam catalogue synchronization
- Vanilla JavaScript and CSS

## Project structure

```text
GamesVault/
├── app.py                    # Flask application, database setup, shared request helpers
├── config.py                 # Environment-backed application configuration
├── run.py                    # Local startup entry point
├── routes/                   # Flask blueprints for auth, pages, games, and library APIs
├── services/                 # Library/profile services and Steam integration
├── templates/                # Flask HTML templates
├── static/                   # CSS, JavaScript, and required artwork/assets
├── scripts/                  # Local database verification, migration, and account tools
├── tests/                    # Application, database, and security tests
├── docs/                     # Structural database schema snapshot
├── instance/                 # Default location for a new local database
├── .env.example              # Safe configuration template
├── requirements.txt
└── requirements-production.txt # Optional Gunicorn server dependency
```

## Screenshots

Screenshots can be added here when available. No screenshots are included in this source tree yet.

## Windows installation

From PowerShell in the project directory:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and replace `SECRET_KEY` with a private random value. You can generate one with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Keep the generated value private. `.env` is excluded from Git.

The optional production WSGI server is installed separately:

```powershell
python -m pip install -r requirements-production.txt
```

## Database and local data

Start GamesVault with:

```powershell
python run.py
```

On startup, `run.py` initializes the schema and applies the app's additive compatibility updates. It does not reset an existing database. If `gamesvault.db` exists beside the source, GamesVault continues using it to preserve existing user data. For a fresh checkout without that legacy file, the default database is `instance/gamesvault.db`.

The current schema has no migration-version table; additive schema updates are applied by `app.init_db()`. Existing achievement and old friendship tables remain in legacy databases for data preservation, but the application does not expose those features.

Set `GAMESVAULT_DB_PATH` in `.env` to choose a different SQLite file. Before upgrading or moving an existing database, make a verified copy. The repository includes `scripts/db_tools.py` for integrity checks and backup/restore operations:

```powershell
python -c "from config import DB_PATH; from scripts.db_tools import verify_database; verify_database(DB_PATH)"
```

SQLite database files, journals, local backups, and the contents of `instance/` are Git-ignored. They remain on your computer and are not part of the public source repository.

## Steam catalogue sync

Steam sync is optional and only runs when you choose **Games → Refresh Steam Catalogue**. It requires an internet connection for that explicit sync action. Without `STEAM_API_KEY`, GamesVault uses the public Steam Store search endpoints where supported. Add a private key to `.env` only if you have one; never put it in frontend JavaScript or commit it. Other features use the local catalogue and database.

Optional settings include:

```dotenv
STEAM_CATALOGUE_TARGET=750
STEAM_DETAIL_BATCH_SIZE=50
# STEAM_SYNC_APPIDS=570,730,1091500
```

## Run and configuration

The default local address is <http://127.0.0.1:5000>. Configuration is read from environment variables or `.env`:

- `SECRET_KEY` — session signing key; set a private random value
- `APP_ENV` — use `development` locally; production mode requires a 32-character secret
- `GAMESVAULT_DB_PATH` — optional database path
- `HOST` and `PORT` — default to `127.0.0.1` and `5000`
- `FLASK_DEBUG` — defaults to off
- `ALLOW_REGISTRATION` — defaults to on in development and off in production
- `SESSION_COOKIE_SECURE` — defaults to off for local HTTP and on in production
- `STEAM_API_KEY` — optional, server-side Steam API key

## Tests

Run the complete local test suite:

```powershell
python -m unittest discover -s tests
```

The tests use temporary database copies and fixtures; they do not require cloud services or credentials.

## Troubleshooting

- **Python command not found:** install Python 3.10+ and use `py` in PowerShell.
- **PowerShell blocks activation:** run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` if that matches your machine's policy, or activate from Command Prompt with `.venv\Scripts\activate.bat`.
- **Port 5000 is occupied:** set `PORT=5001` in `.env` and restart.
- **Database integrity issue:** stop the app, preserve a copy of the database, then run `python scripts/db_tools.py verify <path>` before attempting recovery.
- **Steam refresh fails:** browsing and library features remain available; check connectivity and retry the explicit catalogue refresh later.

## Privacy

GamesVault stores accounts, library activity, preferences, and related records in its local SQLite database. Keep that file and any `.env` outside public uploads and source control. Steam requests are made only for catalogue synchronization and may include normal request metadata.

## Future improvements

Potential follow-up work includes adding screenshots, expanding automated browser coverage, and documenting a formal migration-version table if schema changes become more complex.

## License

No license has been selected for this repository. Until one is added, normal copyright applies and reuse is not granted by this README.
