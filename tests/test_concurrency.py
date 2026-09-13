import concurrent.futures
import multiprocessing as mp
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from context_forge.cli.commands import cmd_init
from context_forge.knowledge.update import create_knowledge_record, next_record_id
from context_forge.store.audit import append_audit_log
from context_forge.knowledge.candidate import stage_candidate
from context_forge.store.paths import brain_paths, read_text
from context_forge.store.lock import repo_lock, get_lock_path, LockTimeoutError


def _proc_hold_lock(repo_path: str, ready_event: Any, release_event: Any, stale_timeout: float) -> None:
    from pathlib import Path
    from context_forge.store.lock import repo_lock
    with repo_lock(Path(repo_path), stale_timeout=stale_timeout):
        ready_event.set()
        while not release_event.is_set():
            time.sleep(0.05)


def _proc_try_acquire(repo_path: str, timeout: float, stale_timeout: float, out_queue: Any) -> None:
    from pathlib import Path
    from context_forge.store.lock import repo_lock, LockTimeoutError
    try:
        with repo_lock(Path(repo_path), timeout=timeout, stale_timeout=stale_timeout):
            out_queue.put("ACQUIRED")
    except (LockTimeoutError, TimeoutError):
        out_queue.put("TIMEOUT")
    except Exception as e:
        out_queue.put(f"ERROR: {type(e).__name__}: {e}")


def _proc_writer(repo_path: str, count: int, error_queue: Any) -> None:
    from pathlib import Path
    from context_forge.knowledge.update import create_knowledge_record
    try:
        for i in range(count):
            create_knowledge_record(
                repo=Path(repo_path),
                kind="decision",
                title=f"Concurrent ADR {i}",
                body=f"Content for concurrent decision {i}",
                authority="user_explicit",
                evidence="User statement",
                scope=[f"src/worker_{i}.py"],
                accept=True,
            )
            time.sleep(0.01)
    except Exception as exc:
        error_queue.put(f"writer_error: {type(exc).__name__}: {exc}")


def _proc_indexer(repo_path: str, count: int, error_queue: Any) -> None:
    from pathlib import Path
    from context_forge.cli.commands import cmd_index
    try:
        for _ in range(count):
            cmd_index(Path(repo_path))
            time.sleep(0.02)
    except Exception as exc:
        error_queue.put(f"indexer_error: {type(exc).__name__}: {exc}")


def _proc_freshness(repo_path: str, count: int, error_queue: Any) -> None:
    from pathlib import Path
    from context_forge.traceability.freshness import update_repository_freshness
    try:
        for _ in range(count):
            update_repository_freshness(Path(repo_path))
            time.sleep(0.02)
    except Exception as exc:
        error_queue.put(f"freshness_error: {type(exc).__name__}: {exc}")


class ConcurrencyTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cmd_init(self.tmp)
        self.p = brain_paths(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_concurrent_id_allocation_and_writes(self):
        """Verify that concurrent threads allocating IDs and writing records do not collide.

        Uses 4 workers because each record creation holds repo_lock through
        ID allocation + file write + FTS rebuild, making total serialized time
        proportional to worker count × per-record time.
        """
        n_workers = 4

        def worker(idx: int) -> int:
            return create_knowledge_record(
                repo=self.tmp,
                kind="decision",
                title=f"Concurrent Decision {idx}",
                body=f"Body of concurrent decision {idx}",
                authority="user_explicit",
                evidence="User explicit direction",
                scope=[f"module_{idx}.py"],
                accept=True,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as executor:
            results = list(executor.map(worker, range(n_workers)))

        for res in results:
            self.assertEqual(res, 0)

        # Check all records created in decisions/
        files = list(self.p["decisions"].glob("ADR-*.md"))
        self.assertEqual(len(files), n_workers, f"Expected {n_workers} distinct ADR records, found {len(files)}")

        ids = set()
        for f in files:
            text = read_text(f)
            for line in text.splitlines():
                if line.startswith("id:"):
                    rec_id = line.split(":", 1)[1].strip()
                    self.assertNotIn(rec_id, ids, f"Duplicate ID allocated: {rec_id}")
                    ids.add(rec_id)
        self.assertEqual(len(ids), n_workers)

    def test_concurrent_audit_appends(self):
        """Verify that concurrent writers to audit log never lose entries or clobber the log."""
        n_entries = 12

        def writer(idx: int):
            append_audit_log(
                self.p,
                action=f"action_{idx}",
                record=self.tmp / f"file_{idx}.py",
                detail=f"Audit detail payload {idx}",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(writer, range(n_entries)))

        audit_files = list(self.p["audit"].glob("knowledge-*.md"))
        self.assertEqual(len(audit_files), 1)
        audit_text = read_text(audit_files[0])

        for idx in range(n_entries):
            self.assertIn(f"action_{idx}", audit_text, f"Missing audit entry for action_{idx}")
            self.assertIn(f"Audit detail payload {idx}", audit_text)

    def test_concurrent_candidate_staging(self):
        """Verify that concurrent staging operations do not corrupt pending storage."""
        n_cands = 6

        def stage_worker(idx: int) -> str:
            return stage_candidate(
                p=self.p,
                event=f"event_{idx}",
                session=f"session_{idx}",
                delta={"decisions": [f"decided approach {idx}"], "blockers": [], "next_steps": []},
                redacted=False,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            ids = list(executor.map(stage_worker, range(n_cands)))

        self.assertEqual(len(set(ids)), n_cands)
        pending = list(self.p["pending"].glob("*.json"))
        self.assertEqual(len(pending), n_cands)

    def test_reentrant_lock_same_process(self):
        """Nested lock acquisition in the same process/thread must succeed cleanly."""
        with repo_lock(self.tmp) as p1:
            self.assertTrue(p1.exists())
            with repo_lock(self.tmp) as p2:
                self.assertEqual(p1, p2)
                self.assertTrue(p2.exists())
            # Lock should still exist after exiting inner scope
            self.assertTrue(p1.exists())
        # Lock should be cleaned up after exiting outer scope
        self.assertFalse(p1.exists())

    def test_corrupt_lock_reclamation_after_timeout(self):
        """Unreadable/corrupt lock files must be safely reclaimed after exceeding stale_timeout."""
        lock_file = get_lock_path(self.tmp)
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.write_text("{corrupt: json content", encoding="utf-8")

        # Set mtime to past (> 5s ago)
        past = time.time() - 10.0
        os.utime(lock_file, (past, past))

        with repo_lock(self.tmp, timeout=2.0, stale_timeout=1.0) as p:
            self.assertTrue(p.exists())

    def test_live_owner_never_stolen_and_dead_owner_reclaimed(self):
        """Cross-process lock held by alive owner is NEVER stolen even past stale_timeout.
        Dead owner lock IS safely reclaimed.
        """
        ready_event = mp.Event()
        release_event = mp.Event()
        out_queue = mp.Queue()

        # Step 1: Process A acquires lock with short stale_timeout (0.5s)
        proc_a = mp.Process(
            target=_proc_hold_lock,
            args=(str(self.tmp), ready_event, release_event, 0.5),
        )
        proc_a.start()
        try:
            self.assertTrue(ready_event.wait(timeout=5.0), "Process A failed to acquire lock")

            # Sleep longer than stale_timeout (0.5s) so lock age > stale_timeout
            time.sleep(0.8)
            self.assertTrue(proc_a.is_alive(), "Process A must still be running")

            # Step 2: Process B tries to acquire lock with short timeout (0.2s)
            # Since Process A is still alive, Process B MUST NOT acquire even though age > 0.5s!
            proc_b = mp.Process(
                target=_proc_try_acquire,
                args=(str(self.tmp), 0.2, 0.5, out_queue),
            )
            proc_b.start()
            proc_b.join(timeout=5.0)

            res = out_queue.get(timeout=2.0)
            self.assertEqual(res, "TIMEOUT", "Process B improperly stole lock from live Process A!")

            # Step 3: Now terminate Process A (simulating sudden crash/dead owner)
            proc_a.terminate()
            proc_a.join(timeout=5.0)
            self.assertFalse(proc_a.is_alive())

            # Step 4: Process C attempts to acquire lock
            # Since owner PID is now dead, lock MUST be safely reclaimed!
            proc_c = mp.Process(
                target=_proc_try_acquire,
                args=(str(self.tmp), 2.0, 0.5, out_queue),
            )
            proc_c.start()
            proc_c.join(timeout=5.0)

            res_c = out_queue.get(timeout=2.0)
            self.assertEqual(res_c, "ACQUIRED", f"Failed to reclaim lock from dead owner: {res_c}")
        finally:
            release_event.set()
            if proc_a.is_alive():
                proc_a.terminate()
                proc_a.join(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
