import concurrent.futures
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from context_forge.cli.commands import cmd_init
from context_forge.knowledge.update import create_knowledge_record, next_record_id
from context_forge.store.audit import append_audit_log
from context_forge.knowledge.candidate import stage_candidate
from context_forge.store.paths import brain_paths, read_text
from context_forge.store.lock import repo_lock


class ConcurrencyTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cmd_init(self.tmp)
        self.p = brain_paths(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_concurrent_id_allocation_and_writes(self):
        """Verify that concurrent threads allocating IDs and writing records do not collide."""
        n_workers = 8

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


if __name__ == "__main__":
    unittest.main()
