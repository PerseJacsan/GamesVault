"""Create or reset a GamesVault account without putting passwords in shell history."""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
if Path("/etc/gamesvault.env").is_file():
    load_dotenv("/etc/gamesvault.env", override=False)
sys.path.insert(0, str(ROOT))
from scripts.db_lock import database_operation_lock  # noqa: E402
from config import DB_PATH  # noqa: E402

DB = DB_PATH
DB_LOCK_FILE = Path(os.getenv("GAMESVAULT_DB_LOCK_FILE", str(DB.with_name(DB.name + ".lock"))))
try:
    DB_LOCK_TIMEOUT_SECONDS = float(os.getenv("GAMESVAULT_DB_LOCK_TIMEOUT_SECONDS", "300"))
except ValueError:
    raise SystemExit("GAMESVAULT_DB_LOCK_TIMEOUT_SECONDS must be a number")


def password_pair() -> str:
    password = getpass.getpass("Password (12-128 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if len(password) < 12 or len(password) > 128:
        raise SystemExit("Password must contain 12-128 characters.")
    if password != confirmation:
        raise SystemExit("Passwords did not match.")
    return password


def create_user() -> None:
    name = input("Display name: ").strip()
    username = input("Username: ").strip().lower()
    email = input("Email: ").strip().lower()
    if len(name) < 2 or not re.fullmatch(r"[a-z0-9_.-]{3,24}", username):
        raise SystemExit("Enter a display name and a username of 3–24 letters, numbers, dots, underscores or hyphens.")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise SystemExit("Enter a valid email address.")
    password = password_pair()
    with database_operation_lock(DB_LOCK_FILE, timeout=DB_LOCK_TIMEOUT_SECONDS):
        with sqlite3.connect(DB) as db:
            db.execute(
                "INSERT INTO users(name,username,email,password_hash) VALUES(?,?,?,?)",
                (name, username, email, generate_password_hash(password)),
            )
    print("Account created.")


def set_password(username: str) -> None:
    password = password_pair()
    with database_operation_lock(DB_LOCK_FILE, timeout=DB_LOCK_TIMEOUT_SECONDS):
        with sqlite3.connect(DB) as db:
            result = db.execute(
                "UPDATE users SET password_hash=? WHERE lower(username)=?",
                (generate_password_hash(password), username.strip().lower()),
            )
            if result.rowcount != 1:
                raise SystemExit("No matching account was found.")
    print("Password updated.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create-user", help="Create an account interactively")
    reset = commands.add_parser("set-password", help="Set an account password interactively")
    reset.add_argument("username")
    args = parser.parse_args()
    if args.command == "create-user":
        create_user()
    else:
        set_password(args.username)


if __name__ == "__main__":
    main()
