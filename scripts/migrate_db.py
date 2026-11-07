"""Back up, migrate, and verify the configured GamesVault SQLite database."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from scripts.db_tools import backup_database, restore_database, verify_database  # noqa: E402
from scripts.db_lock import database_operation_lock, maintenance_operation_lock  # noqa: E402


def migrate(database: Path) -> None:
    backup_path = database.parent / "backups" / f"gamesvault-pre-migration-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.db"
    made_backup = database.is_file()
    if made_backup:
        backup_database(database, backup_path)
    try:
        import app  # noqa: PLC0415

        app.init_db()
        verify_database(database)
    except BaseException:
        if made_backup:
            restore_database(backup_path, database)
        raise
    if made_backup:
        print(f"Pre-migration backup retained: {backup_path}")
    else:
        print("Initialized a new catalogue database; no prior database existed to back up.")


def main() -> None:
    import app  # noqa: PLC0415

    database = app.DB
    lock_path = Path(os.getenv("GAMESVAULT_DB_LOCK_FILE", str(database.with_name(database.name + ".lock"))))
    try:
        lock_timeout = float(os.getenv("GAMESVAULT_DB_LOCK_TIMEOUT_SECONDS", "300"))
    except ValueError:
        raise SystemExit("GAMESVAULT_DB_LOCK_TIMEOUT_SECONDS must be a number")

    def migrate_with_db_lock() -> None:
        with database_operation_lock(lock_path, timeout=lock_timeout):
            if sys.platform.startswith("linux") and shutil.which("systemctl"):
                marker = Path("/var/lib/gamesvault-maintenance/active")
                if not marker.is_file():
                    raise SystemExit("Refusing production migration outside an active GamesVault maintenance window.")
                service = subprocess.run(
                    ["systemctl", "is-active", "--quiet", "gamesvault.service"],
                    check=False,
                )
                if service.returncode == 0:
                    raise SystemExit("Refusing database migration while gamesvault.service is active.")
            migrate(database)

    if sys.platform.startswith("linux") and shutil.which("systemctl"):
        try:
            timeout = float(os.getenv("GAMESVAULT_MAINTENANCE_LOCK_TIMEOUT_SECONDS", "300"))
        except ValueError:
            raise SystemExit("GAMESVAULT_MAINTENANCE_LOCK_TIMEOUT_SECONDS must be a number")
        with maintenance_operation_lock(timeout=timeout):
            migrate_with_db_lock()
    else:
        migrate_with_db_lock()


if __name__ == "__main__":
    main()
