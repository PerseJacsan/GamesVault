from __future__ import annotations

import multiprocessing
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import db_tools as db_tools_module
from scripts.db_tools import check_backup_space, restore_database
from scripts.db_lock import database_operation_lock, validate_lock_path
from scripts.prune_local_backups import prune_local_backups

ROOT = Path(__file__).resolve().parents[1]


def _hold_database_lock(path: str, acquired, release) -> None:
    with database_operation_lock(Path(path), timeout=5):
        acquired.set()
        release.wait(10)


class LocalBackupRetentionTests(unittest.TestCase):
    def test_prune_removes_only_expired_daily_files_and_keeps_known_good(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            old_daily = directory / "gamesvault-20200101T020000Z.db"
            protected = directory / "gamesvault-20261027T020000Z.db"
            migration = directory / "gamesvault-pre-migration-20200101T020000Z.db"
            manual = directory / "operator-copy.db"
            for path in (old_daily, protected, migration, manual):
                path.write_bytes(b"fixture")
            old = 1_600_000_000
            for path in (old_daily, migration, manual):
                os.utime(path, (old, old))

            removed = prune_local_backups(directory, keep_days=30, protected=protected)

            self.assertEqual(removed, [old_daily])
            self.assertTrue(protected.exists())
            self.assertTrue(migration.exists())
            self.assertTrue(manual.exists())

    def test_backup_space_guard_rejects_insufficient_space(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "db.sqlite"
            database.write_bytes(b"x" * 100)
            with patch("scripts.db_tools.shutil.disk_usage", return_value=SimpleNamespace(free=699)):
                with self.assertRaises(SystemExit):
                    check_backup_space(database, minimum_free_bytes=500, copies=2)

    def test_restore_space_guard_leaves_target_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            candidate = directory / "candidate.db"
            target = directory / "target.db"
            for path, value in ((candidate, "new"), (target, "old")):
                database = sqlite3.connect(path)
                database.execute(f"CREATE TABLE data(value TEXT)")
                database.execute("INSERT INTO data VALUES(?)", (value,))
                database.commit()
                database.close()
            with patch("scripts.db_tools.shutil.disk_usage", return_value=SimpleNamespace(free=0)):
                with self.assertRaises(SystemExit):
                    restore_database(candidate, target)
            database = sqlite3.connect(target)
            try:
                self.assertEqual(database.execute("SELECT value FROM data").fetchone()[0], "old")
            finally:
                database.close()


class DatabaseLockSafetyTests(unittest.TestCase):
    def test_database_lock_wait_is_bounded_and_released(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            lock = Path(folder) / "db.lock"
            context = multiprocessing.get_context("spawn")
            acquired = context.Event()
            release = context.Event()
            holder = context.Process(target=_hold_database_lock, args=(str(lock), acquired, release))
            holder.start()
            try:
                self.assertTrue(acquired.wait(10), "lock holder did not acquire the lock")
                with self.assertRaisesRegex(TimeoutError, "Timed out.*database lock"):
                    with database_operation_lock(lock, timeout=0.1):
                        self.fail("contending lock unexpectedly acquired")
            finally:
                release.set()
                holder.join(10)
                if holder.is_alive():
                    holder.terminate()
                    holder.join(5)
            self.assertEqual(holder.exitcode, 0)
            with database_operation_lock(lock, timeout=1):
                pass

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "fork"), "requires POSIX process and flock semantics")
    def test_exec_lock_survives_command_exit_until_forked_writer_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            lock = directory / "db.lock"
            release = directory / "release"
            child_pid_file = directory / "child.pid"
            mutation = directory / "mutation-complete"
            child_code = """
import os
import pathlib
import time
pid = os.fork()
if pid == 0:
    pathlib.Path(os.environ['CHILD_PID_FILE']).write_text(str(os.getpid()))
    while not pathlib.Path(os.environ['RELEASE_FILE']).exists():
        time.sleep(0.02)
    pathlib.Path(os.environ['MUTATION_FILE']).write_text('done')
    os._exit(0)
os._exit(0)
"""
            env = os.environ.copy()
            env.pop("GAMESVAULT_DB_LOCK_ACQUIRED", None)
            env.pop("GAMESVAULT_DB_LOCK_FD", None)
            env.update({"CHILD_PID_FILE": str(child_pid_file), "RELEASE_FILE": str(release), "MUTATION_FILE": str(mutation)})
            process = subprocess.Popen([
                sys.executable, str(ROOT / "scripts/db_lock.py"), "--lock-file", str(lock), "--timeout", "2",
                "--", sys.executable, "-c", child_code,
            ], cwd=ROOT, env=env)
            child_pid = None
            try:
                self.assertEqual(process.wait(timeout=10), 0)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and not child_pid_file.exists():
                    time.sleep(0.02)
                self.assertTrue(child_pid_file.exists(), "forked operation did not publish its PID")
                child_pid = int(child_pid_file.read_text(encoding="utf-8"))
                with self.assertRaisesRegex(TimeoutError, "Timed out.*database lock"):
                    with database_operation_lock(lock, timeout=0.15):
                        self.fail("lock released while the forked writer remained alive")
                release.write_text("continue", encoding="utf-8")
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and not mutation.exists():
                    time.sleep(0.02)
                self.assertTrue(mutation.exists(), "forked operation did not finish after release")
                with database_operation_lock(lock, timeout=2):
                    pass
            finally:
                if not release.exists():
                    release.write_text("cleanup", encoding="utf-8")
                if child_pid is not None:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline and not mutation.exists():
                        time.sleep(0.02)

    @unittest.skipUnless(os.name == "posix", "requires POSIX exec and flock semantics")
    def test_sigkill_terminates_the_lock_owning_operation_process(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            lock = directory / "db.lock"
            ready = directory / "ready"
            mutation = directory / "mutation-complete"
            command = "import os,pathlib,time; r=pathlib.Path(%r); m=pathlib.Path(%r); r.write_text(str(os.getpid())); time.sleep(30); m.write_text('done')" % (str(ready), str(mutation))
            env = os.environ.copy()
            env.pop("GAMESVAULT_DB_LOCK_ACQUIRED", None)
            env.pop("GAMESVAULT_DB_LOCK_FD", None)
            process = subprocess.Popen([
                sys.executable, str(ROOT / "scripts/db_lock.py"), "--lock-file", str(lock), "--timeout", "2",
                "--", sys.executable, "-c", command,
            ], cwd=ROOT, env=env)
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and not ready.exists():
                    time.sleep(0.02)
                self.assertTrue(ready.exists(), "protected operation did not start")
                self.assertEqual(process.pid, int(ready.read_text(encoding="utf-8")))
                os.kill(process.pid, 9)
                process.wait(timeout=10)
                self.assertFalse(mutation.exists(), "operation continued after its lock-owning process was killed")
                with database_operation_lock(lock, timeout=2):
                    pass
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)

    def test_lock_validator_rejects_symlinks_and_non_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            target = directory / "target.lock"
            target.touch()
            with self.assertRaisesRegex(RuntimeError, "regular file"):
                validate_lock_path(directory, production=False)
            if os.name != "nt":
                link = directory / "linked.lock"
                link.symlink_to(target)
                with self.assertRaisesRegex(RuntimeError, "symlink"):
                    validate_lock_path(link, production=False)

    def test_production_lock_path_requires_root_managed_directory_and_file(self) -> None:
        if os.name != "posix" or os.geteuid() != 0:
            self.skipTest("production owner/mode checks require a POSIX root test process")
        import scripts.db_lock as db_lock_module

        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            directory.chmod(0o750)
            lock = directory / "gamesvault.db.lock"
            lock.touch()
            lock.chmod(0o660)
            fake_grp = SimpleNamespace(getgrnam=lambda _name: SimpleNamespace(gr_gid=os.getgid()))
            fake_pwd = SimpleNamespace(getpwnam=lambda _name: SimpleNamespace(pw_uid=os.geteuid()))
            with patch.object(db_lock_module, "DEFAULT_LOCK_PATH", lock), \
                 patch.dict("sys.modules", {"grp": fake_grp, "pwd": fake_pwd}):
                validate_lock_path(lock, production=True)
                lock.chmod(0o666)
                with self.assertRaisesRegex(RuntimeError, "mode 0660"):
                    validate_lock_path(lock, production=True)

    def test_production_lock_validation_checks_owner_type_links_and_modes(self) -> None:
        source = (ROOT / "scripts/db_lock.py").read_text(encoding="utf-8")
        self.assertIn("raw_path != DEFAULT_LOCK_PATH", source)
        self.assertIn("parent_stat.st_uid != 0", source)
        self.assertIn("parent_stat.st_gid != service_gid", source)
        self.assertIn("stat.S_ISREG(file_stat.st_mode)", source)
        self.assertIn("file_stat.st_nlink != 1", source)
        self.assertIn("stat.S_IMODE(file_stat.st_mode) != 0o660", source)
        self.assertIn("getattr(os, \"O_NOFOLLOW\", 0)", source)



class DatabaseRestoreSafetyTests(unittest.TestCase):
        def test_direct_live_restore_refuses_without_maintenance_protocol(self) -> None:
            with tempfile.TemporaryDirectory() as folder:
                directory = Path(folder)
                target = directory / "live.db"
                target.write_bytes(b"keep")
                with patch.object(db_tools_module, "DEFAULT_DB", target), \
                     patch.object(db_tools_module, "LIVE_DB_PATH", target), \
                     patch.object(db_tools_module, "MAINTENANCE_MARKER", directory / "missing-marker"), \
                     patch.object(db_tools_module, "maintenance_operation_lock", return_value=nullcontext()), \
                     patch.dict(os.environ, {"APP_ENV": "production", "GAMESVAULT_DB_PATH": str(target)}, clear=True), \
                     patch("sys.argv", ["db_tools.py", "restore", str(directory / "candidate.db"), "--target", str(target)]):
                    with self.assertRaisesRegex(SystemExit, "maintenance marker is not active"):
                        db_tools_module.main()
                self.assertEqual(target.read_bytes(), b"keep")

        def test_direct_live_restore_succeeds_with_guard_and_stopped_service(self) -> None:
            with tempfile.TemporaryDirectory() as folder:
                directory = Path(folder)
                source = directory / "candidate.db"
                target = directory / "live.db"
                for path, value in ((source, "candidate"), (target, "old")):
                    connection = sqlite3.connect(path)
                    connection.execute("CREATE TABLE data(value TEXT)")
                    connection.execute("INSERT INTO data VALUES(?)", (value,))
                    connection.commit()
                    connection.close()
                marker = directory / "active"
                marker.write_text("maintenance\n", encoding="utf-8")
                guard = directory / "maintenance.conf"
                guard.write_text("ConditionPathExists=!/var/lib/gamesvault-maintenance/active\n", encoding="utf-8")

                def systemctl_result(command, **_kwargs):
                    value = "loaded" if "LoadState" in " ".join(command) else "inactive"
                    return SimpleNamespace(returncode=0, stdout=value, stderr="")

                with patch.object(db_tools_module, "DEFAULT_DB", target), \
                     patch.object(db_tools_module, "LIVE_DB_PATH", target), \
                     patch.object(db_tools_module, "MAINTENANCE_MARKER", marker), \
                     patch.object(db_tools_module, "APP_GUARD", guard), \
                     patch.object(db_tools_module, "maintenance_operation_lock", return_value=nullcontext()), \
                     patch.object(db_tools_module, "database_operation_lock", side_effect=lambda *_a, **_kw: nullcontext()), \
                     patch("scripts.db_tools.subprocess.run", side_effect=systemctl_result), \
                     patch.dict(os.environ, {"APP_ENV": "production", "GAMESVAULT_DB_PATH": str(target), "GAMESVAULT_MAINTENANCE_LOCK_HELD": "1"}, clear=True), \
                     patch("sys.argv", ["db_tools.py", "restore", str(source), "--target", str(target)]):
                    db_tools_module.main()
                connection = sqlite3.connect(target)
                try:
                    self.assertEqual(connection.execute("SELECT value FROM data").fetchone()[0], "candidate")
                finally:
                    connection.close()
                self.assertTrue(list(directory.glob("live.db.pre-restore-*.db")))

        def test_live_restore_refuses_service_active_after_guard_reload(self) -> None:
            with tempfile.TemporaryDirectory() as folder:
                directory = Path(folder)
                target = directory / "live.db"
                marker = directory / "active"
                marker.write_text("maintenance\n", encoding="utf-8")
                guard = directory / "maintenance.conf"
                guard.write_text("ConditionPathExists=!/var/lib/gamesvault-maintenance/active\n", encoding="utf-8")

                def systemctl_result(command, **_kwargs):
                    if "daemon-reload" in command:
                        return SimpleNamespace(returncode=0, stdout="", stderr="")
                    value = "loaded" if "LoadState" in " ".join(command) else "active"
                    return SimpleNamespace(returncode=0, stdout=value, stderr="")

                with patch.object(db_tools_module, "DEFAULT_DB", target), \
                     patch.object(db_tools_module, "LIVE_DB_PATH", target), \
                     patch.object(db_tools_module, "MAINTENANCE_MARKER", marker), \
                     patch.object(db_tools_module, "APP_GUARD", guard), \
                     patch.object(db_tools_module, "maintenance_operation_lock", return_value=nullcontext()), \
                     patch("scripts.db_tools.subprocess.run", side_effect=systemctl_result), \
                     patch.dict(os.environ, {"APP_ENV": "production", "GAMESVAULT_DB_PATH": str(target), "GAMESVAULT_MAINTENANCE_LOCK_HELD": "1"}, clear=True):
                    with self.assertRaisesRegex(SystemExit, "not confirmed stopped"):
                        with db_tools_module.production_restore_maintenance(target):
                            self.fail("restore gate accepted an active service")

        def test_live_restore_gate_reloads_persistent_start_guard_before_status_check(self) -> None:
            source = (ROOT / "scripts/db_tools.py").read_text(encoding="utf-8")
            guard_check = source.index('"ConditionPathExists=!/var/lib/gamesvault-maintenance/active"')
            reload_guard = source.index('["systemctl", "daemon-reload"]')
            active_check = source.index('["systemctl", "show", "--property=ActiveState"')
            maintenance_lock = source.index("with maintenance_operation_lock():")
            self.assertLess(maintenance_lock, guard_check)
            self.assertLess(guard_check, reload_guard)
            self.assertLess(reload_guard, active_check)

        def test_direct_temporary_restore_remains_supported(self) -> None:
            with tempfile.TemporaryDirectory() as folder:
                directory = Path(folder)
                source = directory / "candidate.db"
                target = directory / "temporary.db"
                for path, value in ((source, "candidate"), (target, "old")):
                    connection = sqlite3.connect(path)
                    connection.execute("CREATE TABLE data(value TEXT)")
                    connection.execute("INSERT INTO data VALUES(?)", (value,))
                    connection.commit()
                    connection.close()
                live_db = directory / "configured-live.db"
                with patch.object(db_tools_module, "DEFAULT_DB", live_db), \
                     patch.dict(os.environ, {"APP_ENV": "production", "GAMESVAULT_DB_PATH": str(live_db)}, clear=True), \
                     patch("sys.argv", ["db_tools.py", "restore", str(source), "--target", str(target)]):
                    db_tools_module.main()
                connection = sqlite3.connect(target)
                try:
                    self.assertEqual(connection.execute("SELECT value FROM data").fetchone()[0], "candidate")
                finally:
                    connection.close()
                self.assertTrue(list(directory.glob("temporary.db.pre-restore-*.db")))


if __name__ == "__main__":
    unittest.main()
