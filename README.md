# GamesVault 🎮

<p align="center">
  <strong>Your games. Your collection. Your next adventure.</strong><br>
  A local-first game catalogue and personal library built with Flask and SQLite.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/Flask-3.x-000000?logo=flask&logoColor=white" alt="Flask 3.x">
  <img src="https://img.shields.io/badge/Database-SQLite-003B57?logo=sqlite&logoColor=white" alt="SQLite">
  <img src="https://img.shields.io/badge/Frontend-Vanilla%20JS-F7DF1E?logo=javascript&logoColor=black" alt="Vanilla JavaScript">
  <img src="https://img.shields.io/badge/Status-Local%20First-6C63FF" alt="Local first">
</p>

<p align="center">
  <a href="https://github.com/PerseJacsan/GamesVault">Repository</a>
  ·
  <a href="https://github.com/PerseJacsan/GamesVault/issues">Report an issue</a>
</p>

---

## ✨ What is GamesVault?

GamesVault gives you one place to browse a game catalogue and manage your personal gaming library. Track what you're playing, what you've finished, and what you want to play next, while keeping account and library data in a local SQLite database.

It is designed as a **local-first application**: you run it on your own machine, and your library stays in your local database.

## 🚀 Features

| Area | What you can do |
| --- | --- |
| **Accounts & profile** | Register, log in, edit your profile description, and manage your password |
| **Game catalogue** | Browse game entries, search, filter, sort, and open detailed game pages |
| **Personal library** | Track Playing, Backlog, Completed, and Dropped status, along with playtime, completion, favourites, ratings, and notes |
| **Collections & views** | Organize games into collections, save filter configurations, and use smart shelves |
| **Wishlist & planner** | Keep a wishlist and plan what to play |
| **Dashboard & discovery** | View library statistics and get local recommendations for what to play next |
| **Steam catalogue sync** | Optionally refresh catalogue information from Steam when you choose to run a sync |
| **Interface** | Responsive dark UI, grid/list browsing, notifications, accessible navigation, and page transitions |

**Friends page note:** the Friends page currently displays static sample profiles. It is not a live social network and does not implement friend accounts or a friend API.

## 🧰 Tech stack

- **Backend:** Python 3.10+, Flask 3.x
- **Database:** SQLite
- **Frontend:** HTML, CSS, vanilla JavaScript
- **HTTP client:** Requests
- **Configuration:** python-dotenv
- **Optional production WSGI server:** Gunicorn

## 🖥️ Screenshots

Screenshots are not included in the repository yet. The section can be expanded with real application captures once they are added.

## 📦 Getting started

### Requirements

- Python 3.10 or newer
- Git (optional, if cloning the repository)
- Windows, macOS, or Linux

### 1. Clone the repository

```bash
git clone https://github.com/PerseJacsan/GamesVault.git
cd GamesVault
```

### 2. Create and activate a virtual environment

**Windows PowerShell**

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 4. Configure the environment

Copy the example configuration:

**Windows PowerShell**

```powershell
Copy-Item .env.example .env
```

**macOS / Linux**

```bash
cp .env.example .env
```

Open `.env` and replace the placeholder `SECRET_KEY` with a private random value. Generate one with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Keep the generated value private. The real `.env` file is excluded from Git.

### 5. Start GamesVault

```bash
python run.py
```

Open **http://127.0.0.1:5000** in your browser.

On first launch, GamesVault creates its SQLite database and initializes the schema. A fresh checkout uses `instance/gamesvault.db`.

## 🗃️ Database and local data

- If a legacy `gamesvault.db` file exists beside the source, GamesVault continues using it to preserve existing data.
- A fresh checkout without that file defaults to `instance/gamesvault.db`.
- Startup applies additive compatibility updates. It does not reset an existing database.
- The current schema does not use a migration-version table.
- Legacy achievement and old friendship tables may remain in older databases for data preservation, but those features are not exposed by the current application.
- Database files, SQLite journals, backups, and the contents of `instance/` are excluded from source control.

Before moving or upgrading an existing database, stop the application and make a verified backup. Database integrity tools are available in `scripts/db_tools.py`.

To verify a database using the configured path:

```bash
python -c "from config import DB_PATH; from scripts.db_tools import verify_database; verify_database(DB_PATH)"
```

You can override the database location by setting `GAMESVAULT_DB_PATH` in `.env`.

## 🎮 Optional Steam catalogue synchronization

Steam synchronization is optional. The application works without a Steam API key.

When you choose **Games → Refresh Steam Catalogue**, GamesVault connects to Steam to retrieve catalogue information. Without `STEAM_API_KEY`, it uses public Steam Store endpoints where supported. If you have a Steam Web API key, put it in the server-side `.env` file only. Never place it in frontend JavaScript or commit it.

Optional configuration:

```dotenv
STEAM_CATALOGUE_TARGET=750
STEAM_DETAIL_BATCH_SIZE=50
# STEAM_SYNC_APPIDS=570,730,1091500
```

The sync is an explicit user action; it is not required for ordinary local library features.

## ⚙️ Configuration

GamesVault reads settings from environment variables or the local `.env` file.

| Variable | Purpose |
|---|---|
| `APP_ENV` | Application mode: `development`, `testing`, or `production` |
| `SECRET_KEY` | Flask session-signing key; use a private random value |
| `GAMESVAULT_DB_PATH` | Optional custom SQLite database path |
| `HOST` / `PORT` | Bind address and port; defaults to `127.0.0.1:5000` |
| `FLASK_DEBUG` | Enables debug mode outside production; defaults to off |
| `ALLOW_REGISTRATION` | Controls account registration; defaults to on in development and off in production |
| `SESSION_COOKIE_SECURE` | Secure-cookie setting; production requires it to be enabled |
| `STEAM_API_KEY` | Optional Steam Web API key, kept server-side |
| `STEAM_CATALOGUE_TARGET` | Target catalogue size for Steam sync |
| `STEAM_DETAIL_BATCH_SIZE` | Number of Steam app IDs handled per detail batch |

For production mode, set a random `SECRET_KEY` of at least 32 characters. Production mode also disables debug mode and requires secure session cookies.

## 🧪 Run the tests

From the project root:

```bash
python -m unittest discover -s tests
```

The tests use temporary database copies and fixtures. They do not require cloud services or Steam credentials.

## 🗂️ Project structure

```text
GamesVault/
├── app.py                      # Flask application wiring and blueprints
├── core.py                     # Shared Flask app and database helpers
├── config.py                   # Environment-backed configuration
├── run.py                      # Local startup entry point
├── wsgi.py                     # WSGI entry point
├── routes/                     # Authentication, game, library, and page routes
├── services/                   # Library, profile, and Steam services
├── templates/                  # HTML templates
├── static/
│   ├── css/                    # Application styles
│   ├── js/                     # Frontend behavior
│   ├── data/                   # Configurable contact links
│   └── images/                 # Static artwork and assets
├── scripts/                    # Database and account maintenance tools
├── tests/                      # Automated tests
├── docs/                       # Database schema snapshot
├── instance/                   # Default location for a fresh local database
├── .env.example                # Safe environment template
├── requirements.txt
└── requirements-production.txt
```

## 🔐 Security and deployment notes

- Passwords are stored as Werkzeug password hashes.
- Personal library APIs require an authenticated session; state-changing requests use CSRF protection.
- Production mode requires a strong secret key, disables debug mode, disables registration by default, and requires secure cookies.
- Login and registration rate limits are process-local and reset when the application restarts.
- SQLite is intended for a local, single-process application. The current setup is not a claim of production readiness for a public multi-user service.
- Do not expose Flask's development server or debug mode to an untrusted network.
- Never commit `.env`, database files, backups, tokens, or secret keys.

See [SECURITY_REVIEW.md](SECURITY_REVIEW.md) for the safeguards and operational boundaries documented in this repository.

## 🛠️ Troubleshooting

- **Python command not found:** install Python 3.10+ and use `py` on Windows.
- **PowerShell blocks activation:** use `.venv\Scripts\activate.bat` from Command Prompt, or adjust the execution policy only if appropriate for your machine.
- **Port 5000 is occupied:** set `PORT=5001` in `.env` and restart.
- **Database integrity issue:** stop the application, preserve a copy of the database, then run the database verification tool before attempting recovery.
- **Steam refresh fails:** local catalogue browsing and library features remain available; check connectivity and retry the explicit refresh later.

## 🧭 Roadmap

- [ ] Add real application screenshots and a short walkthrough
- [ ] Expand automated browser/UI coverage
- [ ] Introduce a formal migration-version strategy if schema complexity grows

## 📄 License

No license has been selected yet. Until a license is added, normal copyright applies and reuse is not granted by this README.

---

<p align="center">
  Built as a local-first home for your game collection.
</p>
