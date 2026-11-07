"""Validated, bounded lock shared by GamesVault database maintenance tools."""

from __future__ import annotations

import os
import argparse
import errno
import math
import stat
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_LOCK_PATH = Path("/var/lib/gamesvault-lock/gamesvault.db.lock")
MAINTENANCE_LOCK_PATH = Path("/run/gamesvault-maintenance.lock")
DEFAULT_TIMEOUT_SECONDS = 300.0


def _production_lock_required() -> bool:
    return os.name == "posix" and os.getenv("APP_ENV", "").strip().lower() == "production"


def validate_lock_path(path: Path, *, production: bool | None = None) -> Path:
    """Reject replaceable or unexpected lock paths in the deployed environment."""
    production = _production_lock_required() if production is None else production
    raw_path = Path(os.path.abspath(os.fspath(path.expanduser())))
    if production:
        if raw_path != DEFAULT_LOCK_PATH:
            raise RuntimeError(f"Production database lock must be {DEFAULT_LOCK_PATH}; got {raw_path}.")
        try:
            import grp
            import pwd
            service_gid = grp.getgrnam("gamesvault").gr_gid
            service_uid = pwd.getpwnam("gamesvault").pw_uid
            parent_stat = raw_path.parent.lstat()
            file_stat = raw_path.lstat()
        except (KeyError, FileNotFoundError) as exc:
            raise RuntimeError(f"Production database lock path is unavailable: {raw_path}: {exc}") from exc
        if not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid != 0 or parent_stat.st_gid != service_gid or stat.S_IMODE(parent_stat.st_mode) != 0o750:
            raise RuntimeError("Production database lock directory must be a real root:gamesvault directory with mode 0750.")
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1 or file_stat.st_uid != 0 or file_stat.st_gid != service_gid or stat.S_IMODE(file_stat.st_mode) != 0o660:
            raise RuntimeError("Production database lock must be a non-symlink root:gamesvault regular file with one link and mode 0660.")
        if os.geteuid() not in {0, service_uid}:
            raise RuntimeError("Database lock caller must be root or the gamesvault service account.")
    else:
        if raw_path.is_symlink():
            raise RuntimeError(f"Database lock may not be a symlink: {raw_path}")
        if not raw_path.exists():
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(raw_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.close(fd)
        if not raw_path.is_file():
            raise RuntimeError(f"Database lock must be a regular file: {raw_path}")
    return raw_path


def _inherited_lock_fd(path: Path) -> int | None:
    """Return the inherited lock descriptor only when it names this lock file."""
    value = os.getenv("GAMESVAULT_DB_LOCK_FD")
    if value is None:
        return None
    try:
        fd = int(value)
        opened = os.fstat(fd)
        current = path.lstat()
    except (ValueError, OSError, FileNotFoundError) as exc:
        raise RuntimeError("Inherited database lock descriptor is unavailable.") from exc
    if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
        raise RuntimeError("Inherited database lock descriptor does not match the configured lock file.")
    if os.name != "nt":
        import fcntl
        try:
            # On the inherited open-file description this is a nonblocking
            # confirmation of the lock, not a second independently held lock.
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Inherited database lock descriptor is not locked.") from exc
    return fd


@contextmanager
def database_operation_lock(
    path: Path,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    *,
    production: bool | None = None,
) -> Iterator[None]:
    if timeout < 0 or not math.isfinite(timeout):
        raise ValueError("lock timeout must be finite and nonnegative")
    path = validate_lock_path(path, production=production)
    if os.getenv("GAMESVAULT_DB_LOCK_ACQUIRED") == "1":
        if _inherited_lock_fd(path) is None:
            raise RuntimeError("Database lock was marked acquired, but no valid inherited lock descriptor is present.")
        # The exec'd operation shares ownership of the open-file description.
        # Do not unlock or close it here; it is released when the last process
        # in the protected operation tree closes the descriptor.
        yield
        return
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        current = path.lstat()
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise RuntimeError(f"Database lock changed during validation: {path}")
        if os.name == "nt" and opened.st_size == 0:
            os.write(fd, b"\0")
            os.fsync(fd)
        _acquire_lock(fd, path, timeout)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def maintenance_operation_lock(timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Iterator[None]:
    """Hold or validate the shared Linux maintenance lock in this process."""
    if os.name != "posix":
        raise RuntimeError("The production maintenance lock is supported only on POSIX systems.")
    inherited = os.getenv("GAMESVAULT_MAINTENANCE_LOCK_HELD") == "1"
    try:
        before = MAINTENANCE_LOCK_PATH.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1:
            raise RuntimeError("Maintenance lock must be a root-owned regular file with one link and mode 0600.")
        if inherited:
            value = os.getenv("GAMESVAULT_MAINTENANCE_LOCK_FD", "")
            fd = int(value)
            opened = os.fstat(fd)
            current = MAINTENANCE_LOCK_PATH.lstat()
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                raise RuntimeError("Inherited maintenance lock descriptor does not match the configured lock file.")
        else:
            fd = os.open(MAINTENANCE_LOCK_PATH, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
            opened = os.fstat(fd)
            current = MAINTENANCE_LOCK_PATH.lstat()
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                os.close(fd)
                raise RuntimeError("Maintenance lock changed during validation.")
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Maintenance lock is unavailable: {exc}") from exc

    if inherited:
        _acquire_lock(fd, MAINTENANCE_LOCK_PATH, 0)
        yield
        return
    try:
        _acquire_lock(fd, MAINTENANCE_LOCK_PATH, timeout)
        yield
    except TimeoutError as exc:
        raise TimeoutError(f"Timed out after {timeout:g}s waiting for maintenance lock {MAINTENANCE_LOCK_PATH}.") from exc
    finally:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _acquire_lock(fd: int, path: Path, timeout: float) -> None:
    if timeout < 0 or not math.isfinite(timeout):
        raise ValueError("lock timeout must be finite and nonnegative")
    deadline = time.monotonic() + timeout
    if os.name == "nt":
        import msvcrt
        while True:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out after {timeout:g}s waiting for database lock {path}.") from exc
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
    import fcntl
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out after {timeout:g}s waiting for database lock {path}.") from exc
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock-file", type=Path, default=Path(os.getenv("GAMESVAULT_DB_LOCK_FILE", DEFAULT_LOCK_PATH)))
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.validate_only:
            validate_lock_path(args.lock_file)
            print(f"Database lock path validated: {args.lock_file}")
            return 0
        if not args.command:
            parser.error("provide a command after --, or use --validate-only")
        command = args.command[1:] if args.command and args.command[0] == "--" else args.command
        path = validate_lock_path(args.lock_file)
        fd = os.open(path, os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(fd)
            current = path.lstat()
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                raise RuntimeError(f"Database lock changed during validation: {path}")
            if os.name == "nt" and opened.st_size == 0:
                os.write(fd, b"\0")
                os.fsync(fd)
            _acquire_lock(fd, path, args.timeout)
            env = os.environ.copy()
            env["GAMESVAULT_DB_LOCK_ACQUIRED"] = "1"
            env["GAMESVAULT_DB_LOCK_FD"] = str(fd)
            os.set_inheritable(fd, True)
            os.execvpe(command[0], command, env)
        except OSError:
            os.close(fd)
            raise
        except BaseException:
            os.close(fd)
            raise
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"Database lock error: {exc}", file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
