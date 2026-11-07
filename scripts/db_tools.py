"""Consistent SQLite backup, verification, and guarded restore tools."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
import sqlite3
import shutil
import subprocess
import tempfile
import sys
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.db_lock import database_operation_lock, maintenance_operation_lock  # noqa: E402
ENV_FILE = Path("/etc/gamesvault.env")
if ENV_FILE.is_file() and os.access(ENV_FILE, os.R_OK):
    load_dotenv(ENV_FILE, override=False)

from config import DB_PATH  # noqa: E402

DEFAULT_DB = DB_PATH
LIVE_DB_PATH = Path("/var/lib/gamesvault/gamesvault.db")
MAINTENANCE_MARKER = Path("/var/lib/gamesvault-maintenance/active")
APP_GUARD = Path("/etc/systemd/system/gamesvault.service.d/maintenance.conf")


def integrity(path: Path) -> tuple[str, list[tuple]]:
    db = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        return db.execute("PRAGMA integrity_check").fetchone()[0], db.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        db.close()


def backup_database(source: Path, destination: Path) -> None:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"Database not found: {source}")
    if destination.exists():
        raise SystemExit(f"Refusing to overwrite backup: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    check, violations = integrity(destination)
    if check != "ok" or violations:
        destination.unlink(missing_ok=True)
        raise SystemExit(f"Backup verification failed (integrity={check}, foreign-key violations={len(violations)}).")
    print(f"Backup verified: {destination} ({destination.stat().st_size} bytes)")


def verify_database(path: Path) -> None:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"Database not found: {path}")
    check, violations = integrity(path)
    print(f"integrity_check={check}; foreign_key_violations={len(violations)}; bytes={path.stat().st_size}")
    if check != "ok" or violations:
        raise SystemExit(1)


def check_backup_space(database: Path, minimum_free_bytes: int, copies: int = 2,
                       target_dir: Path | None = None) -> None:
    database = database.expanduser().resolve()
    if not database.is_file():
        raise SystemExit(f"Database not found: {database}")
    if minimum_free_bytes < 0 or copies < 1:
        raise SystemExit("minimum free space must be nonnegative and copies must be at least one")
    target_dir = (target_dir or database.parent).expanduser().resolve()
    free = shutil.disk_usage(target_dir).free
    required = database.stat().st_size * copies + minimum_free_bytes
    print(f"backup_space_free={free}; required={required}; source_bytes={database.stat().st_size}")
    if free < required:
        raise SystemExit("Insufficient free disk space for a local backup and verification copy; no backup was started.")


def restore_database(source: Path, target: Path) -> None:
    source = source.expanduser().resolve()
    target = target.expanduser().resolve()
    verify_database(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    target_size = target.stat().st_size if target.is_file() else 0
    required_space = source.stat().st_size + target_size + 512 * 1024 * 1024
    free_space = shutil.disk_usage(target.parent).free
    print(f"restore_space_free={free_space}; required={required_space}")
    if free_space < required_space:
        raise SystemExit("Insufficient free disk space for restore temporary copy and safety backup; target was not replaced.")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".restore", dir=target.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        dst = sqlite3.connect(temporary)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        verify_database(temporary)
        if target.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            safety_copy = target.with_name(f"{target.name}.pre-restore-{stamp}.db")
            backup_database(target, safety_copy)
        os.replace(temporary, target)
        verify_database(target)
        print(f"Restore completed: {target}")
    finally:
        temporary.unlink(missing_ok=True)


def is_live_production_target(target: Path) -> bool:
    if os.getenv("APP_ENV", "").strip().lower() != "production":
        return False
    resolved = target.expanduser().resolve()
    return resolved in {DEFAULT_DB.resolve(), LIVE_DB_PATH.resolve()}


@contextmanager
def production_restore_maintenance(target: Path):
    """Require the installed maintenance gate for restores of the live DB."""
    if not is_live_production_target(target):
        yield
        return

    with maintenance_operation_lock():
        marker = MAINTENANCE_MARKER
        if marker.is_symlink() or not marker.is_file():
            raise SystemExit("Refusing live database restore: maintenance marker is not active.")
        marker_stat = marker.stat()
        if os.name == "posix" and (marker_stat.st_uid != 0 or marker_stat.st_mode & 0o777 != 0o644 or marker_stat.st_nlink != 1):
            raise SystemExit("Refusing live database restore: maintenance marker ownership or permissions are unsafe.")
        if APP_GUARD.is_symlink() or not APP_GUARD.is_file() or \
                "ConditionPathExists=!/var/lib/gamesvault-maintenance/active" not in APP_GUARD.read_text(encoding="utf-8"):
            raise SystemExit("Refusing live database restore: installed app-start guard is missing or invalid.")

        reload_result = subprocess.run(["systemctl", "daemon-reload"], check=False, capture_output=True, text=True)
        if reload_result.returncode != 0:
            raise SystemExit("Refusing live database restore: systemd could not reload the maintenance app-start guard.")
        load = subprocess.run(
            ["systemctl", "show", "--property=LoadState", "--value", "gamesvault.service"],
            check=False, capture_output=True, text=True,
        )
        active = subprocess.run(
            ["systemctl", "show", "--property=ActiveState", "--value", "gamesvault.service"],
            check=False, capture_output=True, text=True,
        )
        if load.returncode != 0 or load.stdout.strip() not in {"loaded", "masked"} or \
                active.returncode != 0 or active.stdout.strip() not in {"inactive", "failed"}:
            raise SystemExit("Refusing live database restore: gamesvault.service is not confirmed stopped.")
        yield


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--database", type=Path, default=DEFAULT_DB)
    backup.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("database", type=Path)
    space = commands.add_parser("check-space")
    space.add_argument("--database", type=Path, default=DEFAULT_DB)
    space.add_argument("--minimum-free-bytes", type=int, default=512 * 1024 * 1024)
    space.add_argument("--copies", type=int, default=2)
    space.add_argument("--target-dir", type=Path, help="Filesystem receiving the local backup and verification copy.")
    restore = commands.add_parser("restore")
    restore.add_argument("backup", type=Path)
    restore.add_argument("--target", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    lock_path = Path(os.getenv("GAMESVAULT_DB_LOCK_FILE", str(DEFAULT_DB.with_name(DEFAULT_DB.name + ".lock"))))
    try:
        lock_timeout = float(os.getenv("GAMESVAULT_DB_LOCK_TIMEOUT_SECONDS", "300"))
    except ValueError:
        parser.error("GAMESVAULT_DB_LOCK_TIMEOUT_SECONDS must be a number")

    def run_database_operation(
        operation,
        operation_lock_path: Path = lock_path,
        *,
        production_lock: bool | None = None,
    ) -> None:
        with database_operation_lock(operation_lock_path, timeout=lock_timeout, production=production_lock):
            operation()

    if args.command == "backup":
        run_database_operation(lambda: backup_database(args.database, args.output))
    elif args.command == "verify":
        verify_database(args.database)
    elif args.command == "check-space":
        check_backup_space(args.database, args.minimum_free_bytes, args.copies, args.target_dir)
    else:
        restore_lock = lock_path if is_live_production_target(args.target) else args.target.with_name(args.target.name + ".lock")
        with production_restore_maintenance(args.target):
            run_database_operation(
                lambda: restore_database(args.backup, args.target),
                restore_lock,
                production_lock=True if is_live_production_target(args.target) else False,
            )


if __name__ == "__main__":
    main()
