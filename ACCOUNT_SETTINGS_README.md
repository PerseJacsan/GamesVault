# Accounts, dynamic navigation, and settings

## Run locally
1. Create and activate a virtual environment.
2. Install dependencies with `python -m pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and set `SECRET_KEY` to a long random value. Steam API key is optional for the key-free catalogue mode.
4. Run `python app.py` and open `http://127.0.0.1:5000`.
5. Create a local account from the sign-in screen. The published demo login is disabled; its account and collection rows are retained in the supplied database.

## Included
- Hash-based single-page navigation with a small transition; changing sections does not reload the document.
- Register, sign in using username/email, log out, and change password.
- Personal library, wishlist, planner, and statistics are scoped to the signed-in account.
- Passwords are stored as Werkzeug password hashes, never as plain text.
- User settings persist in SQLite and have visible effects: theme, accent colour, compact cards, prices visibility, and default library view.
- Top search is context-aware: full catalogue in Games and current-account collections in Library/Backlog/Wishlist/Planner.
- The recommendation endpoint selects unowned catalogue games and accepts exclusion IDs to avoid repetitive suggestions.
- Existing catalogue data remains shared between accounts; personal tracking data is separate.

## Standalone authentication pages

Authentication pages now use dedicated Flask routes and a separate template.

- `/login` renders the standalone sign-in page.
- `/register` renders the standalone account-creation page.
- `/` redirects signed-out visitors to `/login` and only renders the main GamesVault shell for authenticated users.
- After a successful sign-in or registration, the browser loads `/` as a fresh document.
- Logging out clears the server session and redirects to `/login`; the sidebar, search bar, and profile are never part of the authentication page.

The authentication document uses `static/js/auth.js`; the main authenticated application continues to use `static/js/app.js`.
