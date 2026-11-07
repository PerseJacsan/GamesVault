"""Start GamesVault with its local SQLite schema initialized."""

from config import DEBUG, HOST, PORT
from app import app, init_db


def main() -> None:
    init_db()
    app.run(host=HOST, port=PORT, debug=DEBUG, use_reloader=False)


if __name__ == "__main__":
    main()
