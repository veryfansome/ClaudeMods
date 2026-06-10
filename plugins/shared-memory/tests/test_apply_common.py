#!/usr/bin/env python3
"""Tests for bin/_apply_common.py shared core — Layer 1.

M2 (single-winner stale-lock recovery: dead-PID / aged-ownerless recovery, live held, and the
double-holder race that the old unconditional unlink allowed), and the [17] UTF-8 fail-soft
sweep at the shared-core read sites (cas_read degrades; is_doctrine_file fails CLOSED). No model
calls, no real store touched. Lock tests use a reaped child PID (never a guessed number).
"""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
AC = SourceFileLoader("ac_under_test", os.path.join(BIN, "_apply_common.py")).load_module()


def reaped_pid():
    """A definitely-dead, reaped PID (fork+exit+waitpid) — os.kill(pid,0) raises ProcessLookupError."""
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    return pid


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="ac-test-")
        self.lock = os.path.join(self.d, ".distill.lock")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def write_lock(self, content):
        with open(self.lock, "w") as f:
            f.write(content)


class Lock(Base):
    def test_live_pid_lock_is_held(self):
        self.write_lock(json.dumps({"pid": os.getpid()}) + "\n")   # our own pid is alive
        with self.assertRaises(AC.LockHeld):
            AC.acquire_lock(self.lock)
        self.assertTrue(os.path.exists(self.lock), "held lock was removed")

    def test_dead_pid_lock_recovered(self):
        self.write_lock(json.dumps({"pid": reaped_pid()}) + "\n")
        fd = AC.acquire_lock(self.lock)   # dead owner → recovered
        try:
            self.assertTrue(os.path.exists(self.lock))
            self.assertEqual(json.load(open(self.lock))["pid"], os.getpid(), "recovered lock not re-owned")
        finally:
            AC.release_lock(fd, self.lock)
        self.assertFalse(os.path.exists(self.lock), "release left the lock")

    def test_aged_ownerless_lock_recovered(self):
        self.write_lock("")                                   # 0-byte (crash between create and pid-write)
        old = time.time() - (AC.EMPTY_LOCK_GRACE + 60)
        os.utime(self.lock, (old, old))                       # aged past the grace
        fd = AC.acquire_lock(self.lock)
        try:
            self.assertEqual(json.load(open(self.lock))["pid"], os.getpid())
        finally:
            AC.release_lock(fd, self.lock)

    def test_fresh_ownerless_lock_is_held(self):
        self.write_lock("")                                   # fresh 0-byte → held (avoids the create/write race)
        with self.assertRaises(AC.LockHeld):
            AC.acquire_lock(self.lock)

    def test_recover_never_destroys_a_live_lock(self):
        # M2 core property: a recoverer that (after deciding stale) grabs a lock that has since
        # gone LIVE must put it back and lose — never destroy it. Drive _recover_stale_lock
        # directly against a live lock (simulates the second racer arriving after the winner
        # recreated). Old unconditional-unlink code would delete the live lock here.
        self.write_lock(json.dumps({"pid": os.getpid()}) + "\n")
        before = open(self.lock).read()
        with self.assertRaises(AC.LockHeld):
            AC._recover_stale_lock(self.lock)
        self.assertTrue(os.path.exists(self.lock), "recovery destroyed a live lock (double-holder bug)")
        self.assertEqual(open(self.lock).read(), before, "live lock content changed by recovery")
        # no recovery-temp left behind
        leftovers = [f for f in os.listdir(self.d) if f.startswith(".distill.lock.stale.")]
        self.assertEqual(leftovers, [], f"leftover recovery temp: {leftovers}")

    def test_two_acquirers_single_winner_smoke(self):
        # N=2 invariant (the REPORTED M2 case — two sessions): both decide the same stale lock is
        # stale (barrier at _lock_stale), then race recovery; exactly one holds, the other LockHeld.
        # NOTE: SMOKE TEST — scheduling makes this pass even on the pre-fix unconditional-unlink
        # code, so it does NOT by itself prove the race is fixed. The authoritative deterministic
        # guard is test_recover_never_destroys_a_live_lock. (The >=3-way residual is documented in
        # acquire_lock and tracked as a follow-up.)
        self.write_lock(json.dumps({"pid": reaped_pid()}) + "\n")
        barrier = threading.Barrier(2, timeout=5)
        orig = AC._lock_stale

        def stale_barrier(p):
            r = orig(p)
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
            return r

        results = {}

        def worker(tag):
            try:
                results[tag] = ("held", AC.acquire_lock(self.lock))
            except AC.LockHeld:
                results[tag] = ("lockheld", None)
            except Exception as e:  # noqa
                results[tag] = ("error", repr(e))

        AC._lock_stale = stale_barrier
        try:
            ts = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B")]
            for t in ts:
                t.start()
            for t in ts:
                t.join(8)
        finally:
            AC._lock_stale = orig
        held = [(t, r[1]) for t, r in results.items() if r[0] == "held"]
        for _, fd in held:
            if fd is not None:
                os.close(fd)
        self.assertEqual(len(held), 1, f"expected exactly one holder, got {results}")
        self.assertTrue(any(r[0] == "lockheld" for r in results.values()), results)


class SweepReads(Base):
    def write_bytes(self, name, data):
        p = os.path.join(self.d, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_cas_read_non_utf8_degrades(self):
        p = self.write_bytes("x.md", b"\xff not utf-8\n")
        text, err = AC.cas_read(p, "any-hash")
        self.assertIsNone(text)
        self.assertIn("unreadable", err)   # skip branch, not a crash

    def test_is_doctrine_file_fails_closed_on_bad_byte(self):
        # an existing but undecodable file is treated AS doctrine (protected) — a bad byte can
        # never downgrade a doctrine file to deletable
        bad = self.write_bytes("bad.md", b"---\nname: x\nmetadata:\n  doctrine: true\n---\n\xff\n")
        self.assertTrue(AC.is_doctrine_file(bad), "undecodable file not fail-closed")
        # a non-doctrine file with a bad byte is likewise protected (fail-closed)
        badnd = self.write_bytes("bad2.md", b"---\nname: y\n---\n\xff body\n")
        self.assertTrue(AC.is_doctrine_file(badnd))

    def test_archive_write_bad_byte_dest_degrades(self):
        # [17]: a pre-existing UNDECODABLE archive dest must yield the "different content" error on
        # the idempotent read-back, not a UnicodeDecodeError crash
        dest = os.path.join(self.d, "arch.md")
        with open(dest, "wb") as f:
            f.write(b"\xff old\n")
        err = AC.archive_write(dest, "new content\n")
        self.assertIsNotNone(err)
        self.assertIn("different content", err)

    def test_is_doctrine_file_normal_cases(self):
        doc = self.write_bytes("d.md", b"---\nname: d\nmetadata:\n  doctrine: true\n---\nbody\n")
        nod = self.write_bytes("n.md", b"---\nname: n\nmetadata:\n  type: feedback\n---\nbody\n")
        self.assertTrue(AC.is_doctrine_file(doc))
        self.assertFalse(AC.is_doctrine_file(nod))
        self.assertFalse(AC.is_doctrine_file(os.path.join(self.d, "missing.md")), "missing → not doctrine")


def _boom(*a, **k):
    raise OSError("injected mid-write failure")


class AtomicWrites(Base):
    """[R9] D1-D3: the apply write channel (deindex, distill-apply slim/remove_index_lines, and
    archive_write) must be crash-atomic — a bare open('w')/open('x')+write truncates or partially
    creates a COMMITTED file. R4 hardened only the installer; these guards pin the store channel."""

    def test_atomic_write_replaces_content_no_tmp_left(self):
        p = os.path.join(self.d, "idx.md")
        with open(p, "w") as f:
            f.write("old\n")
        AC.atomic_write(p, "new content\n")
        self.assertEqual(open(p).read(), "new content\n")
        self.assertFalse(os.path.exists(p + ".tmp"), "os.replace left a .tmp behind")

    def test_atomic_write_preserves_file_on_failed_replace(self):
        # fault injection: a crash mid-rewrite must leave the committed index UNCHANGED — a bare
        # open('w') would already have truncated it. Proves atomicity, not just mechanism.
        p = os.path.join(self.d, "MEMORY.md")
        with open(p, "w") as f:
            f.write("# index\n- [a](a.md) — x\n- [b](b.md) — y\n")
        before = open(p).read()
        real = os.replace
        os.replace = _boom
        try:
            with self.assertRaises(OSError):
                AC.atomic_write(p, "clobbered\n")
        finally:
            os.replace = real
        self.assertEqual(open(p).read(), before, "atomic_write truncated the live index on a failed rename")
        self.assertFalse(os.path.exists(p + ".tmp"), "[R9 W-3] atomic_write leaked a .tmp on a failed replace")

    def test_atomic_write_writes_through_symlink(self):
        # [R9] D4: a symlinked target (a dotfiles-managed project MEMORY.md) must be written THROUGH
        # (realpath-resolved), not detached into a fresh regular file that orphans the link target.
        target = os.path.join(self.d, "real_index.md")
        link = os.path.join(self.d, "MEMORY.md")
        with open(target, "w") as f:
            f.write("old\n")
        os.symlink(target, link)
        AC.atomic_write(link, "new content\n")
        self.assertTrue(os.path.islink(link), "symlink was detached into a regular file")
        self.assertEqual(open(target).read(), "new content\n", "write did not reach the symlink target")

    def test_archive_write_stale_hardlinked_tmp_preserves_committed_dest(self):
        # [R9 W-2] regression guard for the D-1 shared-inode data loss: a crash between os.link and
        # the finally-unlink can leave a dest.tmp HARDLINKED to the committed archive. A fixed-name
        # tmp reused via open('w') would truncate that shared inode on the NEXT archive → silently
        # overwrite the committed archive AND report success. A fresh-inode mkstemp per call can't.
        dest = os.path.join(self.d, "archive", "feedback_x.md")
        os.makedirs(os.path.dirname(dest))
        with open(dest, "w") as f:
            f.write("ORIGINAL committed archive\n")
        os.link(dest, dest + ".tmp")   # crash residue: tmp hardlinked to the committed archive
        err = AC.archive_write(dest, "DIFFERENT content\n")
        self.assertIsNotNone(err, "no-clobber bypassed via a stale hardlinked tmp (D-1 data loss)")
        self.assertIn("different content", err)
        self.assertEqual(open(dest).read(), "ORIGINAL committed archive\n",
                         "the committed archive was silently overwritten")

    def test_deindex_routes_through_atomic_write(self):
        # [R9 W-1] the recurring failure mode is a call site silently reverting to a truncating
        # open('w') while the primitive stays fixed — invisible to end-state tests. Pin routing:
        # patch ac.atomic_write to raise; a site that calls it leaves the committed index intact,
        # a reverted open('w') would truncate it (and never raise → assertRaises fails).
        import sys
        sys.path.insert(0, BIN)
        ra = SourceFileLoader("review_apply_probe", os.path.join(BIN, "review-apply")).load_module()
        idx = os.path.join(self.d, "MEMORY.md")
        with open(idx, "w") as f:
            f.write("# Index\n- [a](a.md) — x\n- [b](b.md) — y\n")
        before = open(idx).read()
        real = ra.ac.atomic_write
        ra.ac.atomic_write = _boom
        try:
            with self.assertRaises(OSError):
                ra.deindex(idx, ["- [a](a.md) — x"])   # drops one line → must call atomic_write
        finally:
            ra.ac.atomic_write = real
        self.assertEqual(open(idx).read(), before,
                         "deindex did not route through atomic_write (committed index mutated by a bare write)")

    def test_archive_write_no_partial_on_failed_commit(self):
        # D1: a crash after the tmp write but before the os.link commit must leave NO archive at
        # dest — do_retire deletes the live source once the stamped archive exists, so a partial
        # dest (old open('x')+write) would mean deleting the body against a truncated backup.
        dest = os.path.join(self.d, "archive", "feedback_x.md")
        real = os.link
        os.link = _boom
        try:
            with self.assertRaises(OSError):
                AC.archive_write(dest, "the full lesson body\n")
        finally:
            os.link = real
        self.assertFalse(os.path.exists(dest), "a partial archive reached dest (non-atomic commit)")
        leftover = [f for f in os.listdir(os.path.dirname(dest)) if f.endswith(".tmp")]
        self.assertEqual(leftover, [], f"archive tmp not cleaned up: {leftover}")

    def test_archive_write_idempotent_same_content(self):
        dest = os.path.join(self.d, "archive", "feedback_x.md")
        self.assertIsNone(AC.archive_write(dest, "body\n"))
        self.assertIsNone(AC.archive_write(dest, "body\n"), "idempotent re-archive must be a no-op")
        self.assertEqual(open(dest).read(), "body\n")
        self.assertFalse(os.path.exists(dest + ".tmp"))

    def test_archive_write_refuses_different_content_dest_intact(self):
        dest = os.path.join(self.d, "archive", "feedback_x.md")
        self.assertIsNone(AC.archive_write(dest, "original\n"))
        err = AC.archive_write(dest, "different\n")
        self.assertIsNotNone(err)
        self.assertIn("different content", err)
        self.assertEqual(open(dest).read(), "original\n", "no-clobber violated on a differing re-archive")
        self.assertFalse(os.path.exists(dest + ".tmp"), "tmp left after a refused re-archive")


if __name__ == "__main__":
    unittest.main()
