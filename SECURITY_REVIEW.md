# Application security notes

These notes describe application safeguards present in the local codebase. They are not a certification or a substitute for review before exposing the application to a public network.

## Implemented safeguards

- WSGI import does not initialize or mutate SQLite. `run.py` initializes the local database; `scripts/migrate_db.py` makes and verifies a pre-migration backup.
- Production registration defaults off, and production mode requires a random `SECRET_KEY` of at least 32 characters.
- Account passwords are stored as Werkzeug password hashes. `scripts/manage_user.py` prompts for passwords interactively instead of accepting them as command-line arguments.
- Personal API routes require an authenticated session. State-changing requests require a CSRF token.
- Login and registration requests have process-local rate limits. They reset on restart; keep a single application worker unless the limiter is replaced with a shared implementation.
- Session cookies are HttpOnly and SameSite Lax. The secure-cookie default is enabled in production and disabled for local HTTP development.
- Steam catalogue synchronization is optional, authenticated, and rate limited. Its API key is server-side and is not required for public Store catalogue fallback.
- SQLite backup, migration, and restore tools verify database integrity and foreign-key constraints. Backups are kept separate from tracked source files.

## Operational boundaries

- SQLite is intended for a single application process and local database file. Preserve a verified backup before migration or restore.
- Do not expose the development server or debug mode to an untrusted network.
- Never commit `.env`, local databases, backups, access tokens, or secret keys.
- The static Friends page uses sample profiles only; it does not implement a social graph or friend API.
