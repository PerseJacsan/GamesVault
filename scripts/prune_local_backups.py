"""Prune expired local daily database backups while preserving a known-good copy."""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

DAILY_BACKUP = re.compile(r"^gamesvault-\d{8}T\d{6}Z\.db$")


def prune_local_backups(directory: Path, keep_days: int, protected: Path) -> list[Path]:
    directory = directory.expanduser().resolve()
    protected = protected.expanduser().resolve()
    if keep_days < 1:
        raise ValueError("keep_days must be at least one")
    if protected.parent != directory or not protected.is_file():
        raise ValueError("protected backup must be an existing file directly inside the backup directory")

    daily = sorted(
        (item for item in directory.iterdir()
         if item.is_file() and not item.is_symlink() and DAILY_BACKUP.fullmatch(item.name)),
        key=lambda item: item.name,
    )
    # Keep the most recent local daily backup even if its mtime is unexpectedly old.
    newest = daily[-1] if daily else protected
    cutoff = time.time() - keep_days * 24 * 60 * 60
    removed: list[Path] = []
    for item in daily:
        if item.resolve() in {protected, newest}:
            continue
        if item.stat().st_mtime < cutoff:
            item.unlink()
            removed.append(item)
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--keep-days", type=int, default=30)
    parser.add_argument("--protect", type=Path, required=True,
                        help="An existing verified backup that must be retained.")
    args = parser.parse_args()
    for path in prune_local_backups(args.directory, args.keep_days, args.protect):
        print(f"Pruned expired daily backup: {path}")


if __name__ == "__main__":
    main()
