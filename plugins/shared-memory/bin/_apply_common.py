"""Shared mechanical core for the apply scripts (distill-apply, review-apply).

Extracted per D5: the compare-and-swap read, the store lockfile (with PID-liveness
stale recovery), the resolve-then-verify path guard (parameterized by permitted root),
the doctrine invariant (never delete/modify/mint a doctrine:true file, nor remove its
index line), the remove_index_lines executor, and the no-clobber archive writer.

Both apply scripts import this by adding their own dir to sys.path. It authors no
beliefs; it only guards and executes mechanical ops.
"""
import glob
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time

HOME = os.path.expanduser("~")
STORE = os.path.join(HOME, ".claudemods", "shared-memory")
LOCAL = os.path.join(STORE, "local")

# The store's index files: MEMORY.md, MEMORY.local.md (flat), MEMORY.<domain>.md (general
# domain projection), and MEMORY.local.<domain>.md (personal domain projection, M4 — two
# segments, so the pattern allows an optional `.local` before the optional domain segment).
INDEX_RE = re.compile(r"^MEMORY(\.local)?(\.[\w-]+)?\.md$")


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def parse_frontmatter(text):
    m = re.match(r"\A---\n(.*?)\n---\n?(.*)\Z", text, re.S)
    if not m:
        return None, text
    fields = {}
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        km = re.match(r"(\s*)([A-Za-z_]+):\s*(.*?)\s*(#.*)?$", lines[i])
        if not km:
            i += 1
            continue
        indent, key, val = km.group(1), km.group(2), km.group(3).strip().strip('"')
        if val == "":
            # a YAML block list (supersedes:\n  - a\n  - b) — fold the deeper-indented
            # "- item" lines into the inline [a, b] form so parse_list reads it uniformly
            items, j = [], i + 1
            while j < len(lines):
                lm = re.match(r"(\s+)-\s+(.*?)\s*$", lines[j])
                if lm and len(lm.group(1)) > len(indent):
                    items.append(lm.group(2).strip().strip('"').strip("'"))
                    j += 1
                else:
                    break
            if items:
                fields[key] = "[" + ", ".join(items) + "]"
                i = j
                continue
        fields[key] = val
        i += 1
    return fields, m.group(2)


def cas_read(path, expect):
    """Compare-and-swap read: (text, None) iff the file matches its scan-time hash."""
    try:
        text = open(path, encoding="utf-8").read()
    except (OSError, UnicodeError) as e:   # a non-UTF-8 byte must skip, not crash the apply pass
        return None, f"unreadable: {e}"
    if sha(text) != expect:
        return None, "changed since scan"
    return text, None


# ---- resolve-then-verify path guard -------------------------------------------------

def resolved_under(path, root):
    """True iff path's realpath sits at or under root (expands .., follows symlinks) —
    a `../` or a planted symlink can't escape, unlike a raw startswith."""
    rp, rr = os.path.realpath(path), os.path.realpath(root)
    return rp == rr or rp.startswith(rr + os.sep)


def any_root(path, roots):
    return any(resolved_under(path, r) for r in roots)


def is_doctrine_file(path):
    """True iff the file declares doctrine: true (read on-disk — the store defends its own
    doctrine independent of any plan claim). A MISSING file is not doctrine. An existing but
    unreadable/undecodable file fails CLOSED — treated AS doctrine (protected) — so a non-UTF-8
    byte can never downgrade a doctrine file to deletable (which would bypass the invariant)."""
    try:
        fields, _ = parse_frontmatter(open(path, encoding="utf-8").read())
    except FileNotFoundError:
        return False   # missing → not doctrine
    except (OSError, UnicodeError):
        return True    # exists but unreadable/undecodable → fail closed (protect it)
    return (fields or {}).get("doctrine") == "true"


def index_line_target(line, index_path):
    """The memory file an index line points at (from its [Title](file) link), resolved
    relative to the index's own directory — so a doctrine memory's line can be protected."""
    m = re.search(r"\]\(([^)]+)\)", line)
    return os.path.join(os.path.dirname(index_path), m.group(1)) if m else None


DOMAINS_MAP = os.path.join(STORE, ".domains.json")   # machine-local repo→domain map (M4)


def repo_root(cwd):
    """Canonical repo key for the repo→domain map — the SAME function the loader (read) and
    memory-init --map-repo (write) must use. `git rev-parse --git-common-dir` then
    dirname(realpath()) yields the main repo's root from the repo root, any subdir, OR a
    worktree (a worktree resolves to its main repo — matching the shared memory dir). Falls
    back to realpath(cwd) for a non-git dir."""
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--git-common-dir"],
                           capture_output=True, text=True, timeout=5)
        gcd = r.stdout.strip()
        if r.returncode == 0 and gcd:
            if gcd == ".":
                return os.path.realpath(cwd)   # bare repo: common-dir IS cwd; key on it, so
                                               # sibling bare repos don't collide to their parent
            if not os.path.isabs(gcd):
                gcd = os.path.join(cwd, gcd)
            return os.path.dirname(os.path.realpath(gcd))
    except (OSError, subprocess.SubprocessError):
        pass
    # git unavailable / failed: walk up for a .git marker so a subdir still resolves to its repo
    # root (keying to the bare subdir would make the same repo map-or-unmap by transient git state)
    d = os.path.realpath(cwd)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return os.path.realpath(cwd)
        d = parent


def repo_domains(cwd, store=STORE):
    """The domain set mapped to cwd's repo (empty if unmapped / no map / unreadable — the
    fail-safe default: an unmapped repo loads only the always-load general+personal tier)."""
    try:
        with open(os.path.join(store, ".domains.json"), encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return []
    repos = m.get("repos", {}) if isinstance(m, dict) else {}
    val = repos.get(repo_root(cwd), [])
    return val if isinstance(val, list) else []


def domains_map(store=STORE):
    """The parsed repo→domain map ({} on absence / corruption / non-dict)."""
    try:
        with open(os.path.join(store, ".domains.json"), encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return {}
    return m if isinstance(m, dict) else {}


def any_repo_mapped(store=STORE):
    """True iff at least one repo carries a non-empty domain list — the signal that per-machine
    mapping has begun (so the fresh-clone onboarding prompt should fall silent)."""
    repos = domains_map(store).get("repos")
    if not isinstance(repos, dict):
        return False
    return any(isinstance(v, list) and v for v in repos.values())


def projection_files(store=STORE):
    """Basenames of the domain projection files on disk — MEMORY.<domain>.md and
    MEMORY.local.<domain>.md — excluding the always-load flat indexes MEMORY.md / MEMORY.local.md."""
    return [b for b in (os.path.basename(p) for p in glob.glob(os.path.join(store, "MEMORY.*.md")))
            if b not in ("MEMORY.md", "MEMORY.local.md")]


def unmapped_with_projections(store=STORE):
    """The fresh-clone / second-machine regression state: committed domain projections exist on
    disk but no repo is mapped, so those projections load NOWHERE until a repo is mapped. Shared
    by the loader (first-run onboarding hint) and the doctor (unmapped-with-projections warn) so
    both judge the state identically."""
    return bool(projection_files(store)) and not any_repo_mapped(store)


def is_memory_file(path, store=STORE, local=LOCAL):
    """Positive predicate (not a skip-list): an actual live memory `.md` at the store top
    level or under local/. Excludes the indexes (MEMORY*.md), RETIRED.md, local/archive/
    originals, and the machine-local dot-files — a new non-memory store path can't slip
    through, since only the two memory directories qualify. Shared by eval-memory (the
    post-write eval) and log-recall (the read-telemetry hook), so the body/index boundary
    is defined once. NOTE: this still qualifies doctrine files — a caller that must exclude
    them (log-recall, to dodge the insufficiency inversion) pairs this with is_doctrine_file."""
    p = os.path.abspath(path)
    if not p.endswith(".md"):
        return False
    if os.path.dirname(p) not in (store, local):  # excludes local/archive/, .reflect/, packs
        return False
    base = os.path.basename(p)
    return not (base.startswith(".") or base == "RETIRED.md" or INDEX_RE.match(base))


def project_memory_roots(home):
    """distill-apply's permitted roots, computed LOCALLY at apply: ~/.claude/projects/*/memory
    (worktree session dirs excluded), plus a user-scope autoMemoryDirectory override that
    doesn't resolve under the store. No scan->apply provenance — apply computes its own fence."""
    roots = []
    for d in glob.glob(os.path.join(home, ".claude", "projects", "*", "memory")):
        if "--claude-worktrees-" not in os.path.basename(os.path.dirname(d)):
            roots.append(d)
    try:
        override = json.load(open(os.path.join(home, ".claude", "settings.json"))).get("autoMemoryDirectory")
    except (OSError, ValueError):
        override = None
    if override:
        p = os.path.realpath(os.path.expanduser(override))
        store = os.path.realpath(os.path.join(home, ".claudemods", "shared-memory"))
        if os.path.isdir(p) and not resolved_under(p, store):
            roots.append(p)
    return roots


# ---- store lock with PID-liveness stale recovery ------------------------------------

class LockHeld(Exception):
    pass


EMPTY_LOCK_GRACE = 30  # s — an owner-less lock older than this is an abandoned crash-mid-create

def _lock_stale(lock_path):
    """Stale iff the lock has no live owner. A recorded live PID -> held; a dead PID -> stale.
    A lock with NO verifiable owner (unreadable / empty / pid<=0) is treated as held only briefly:
    acquire_lock writes the pid microseconds after creating the file, so an owner-less lock that
    has sat untouched past EMPTY_LOCK_GRACE is an abandoned create-then-crash (or a legacy file)
    and is recoverable — otherwise a 0-byte lock from a crash between os.open and os.write would
    wedge every future acquirer (and the doctor's --check) forever."""
    try:
        pid = int(json.load(open(lock_path)).get("pid", 0))
    except (OSError, ValueError, TypeError):
        pid = 0
    if pid <= 0:  # no verifiable owner: recover only once it has aged past the write-the-pid window
        try:
            return (time.time() - os.stat(lock_path).st_mtime) > EMPTY_LOCK_GRACE
        except OSError:
            return False
    try:
        os.kill(pid, 0)
        return False   # alive -> held
    except ProcessLookupError:
        return True    # dead -> stale
    except PermissionError:
        return False   # alive, not ours -> held


def acquire_lock(lock_path):
    """Acquire the store lock (O_EXCL), recovering a stale lock at most once. Returns an fd;
    raises LockHeld if a live run holds it.

    Stale recovery is SINGLE-WINNER (M2). The old code did an unconditional `os.unlink(lock_path)`
    which let two recoverers each delete the OTHER's freshly-created live lock -> two holders. A
    plain rename is not enough either: the winner can recreate the lock before a second recoverer's
    rename runs, so that rename would grab the fresh LIVE lock. So `_recover_stale_lock` renames the
    suspect to a private temp (a losing racer hits FileNotFoundError and just retries), then
    RE-CHECKS staleness now that it exclusively holds the temp; it discards the temp only if still
    dead/owner-less, else renames it back and loses.

    KNOWN RESIDUAL (measured, not vanishing): with THREE OR MORE acquirers contending on one stale
    lock, a fresh acquirer that O_EXCL-creates lock_path inside the rename-back window can be
    displaced, yielding two holders — reproduced ~10% of runs at 3-way and routinely at high
    contention. The REPORTED case (N=2, two sessions) is single-winner and fully fixed. Closing the
    residual needs a lock primitive with no re-insert window (a mkdir-based lock, or an O_EXCL
    sentinel guarding lock_path) — tracked as a follow-up. Acceptable meanwhile: this is an advisory
    lock on a single-user store where 3+ concurrent apply on a dead-owner lock is implausible
    (hooks/distill/review are effectively serialized). A crashed run's stale lock is still recovered
    on the next acquire."""
    for attempt in (1, 2):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, (json.dumps({"pid": os.getpid()}) + "\n").encode())
            return fd
        except FileExistsError:
            if attempt == 1 and _lock_stale(lock_path):
                _recover_stale_lock(lock_path)   # raises LockHeld if the file went live in the race
                continue
            raise LockHeld(lock_path)


def _recover_stale_lock(lock_path):
    """Claim the suspect stale lock via an atomic rename to a private temp (the single-winner
    step), discarding it only if it is STILL owner-less/dead once exclusively held; if it turned
    out live (a winner recreated it between our _lock_stale check and our rename), rename it back
    and raise LockHeld so this caller loses rather than destroying a live lock."""
    temp = "%s.stale.%d.%d" % (lock_path, os.getpid(), time.time_ns())
    try:
        os.rename(lock_path, temp)
    except FileNotFoundError:
        return  # another recoverer already moved/cleared it — fall through to a fresh O_EXCL create
    if _lock_stale(temp):        # still dead/owner-less → genuinely stale, drop it
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
        return
    try:                          # went live in the race → put it back and lose
        os.rename(temp, lock_path)
    except OSError:
        try:
            os.unlink(temp)
        except OSError:
            pass
    raise LockHeld(lock_path)


def release_lock(fd, lock_path):
    os.close(fd)
    try:
        os.unlink(lock_path)
    except FileNotFoundError:
        pass


# ---- mechanical op helpers ----------------------------------------------------------

def drop_index_lines(text, lines):
    """(new_text, missing). All-or-nothing: if any named line is absent (CAS passed, so
    it's a plan bug, not a race), missing is non-empty and new_text is None."""
    have = set(text.splitlines())
    missing = [ln for ln in lines if ln not in have]
    if missing:
        return None, missing
    drop = set(lines)
    kept = [l for l in text.splitlines() if l not in drop]
    return "\n".join(kept) + ("\n" if text.endswith("\n") else ""), []


def stamp_frontmatter(text, fields):
    """Merge stamp fields into the file's own frontmatter (one block, original preserved) —
    as distill-apply merges promoted_to; a source without frontmatter gets a fresh block."""
    added = "".join(f"  {k}: {v}\n" for k, v in fields.items())
    if re.search(r"\A---\n.*?\n---\n", text, re.S):
        return re.sub(r"\n---\n", "\n" + added + "---\n", text, count=1)
    return "---\n" + added + "---\n" + text


def atomic_write(path, text):
    """Crash-atomic overwrite: write a same-dir .tmp then os.replace (atomic rename), so a crash
    mid-write leaves the OLD file intact — never a truncated one. Resolves realpath FIRST so a
    symlinked target (a dotfiles-managed project MEMORY.md) is written THROUGH, not detached into a
    fresh regular file ([R9] D4 — matches memory-init._atomic_write; D3 routes PROJECT auto-memory
    files here, any of which may be a symlink). Same convention as gen-projections (tmp+os.replace,
    no fsync). os.replace CONSUMES the tmp (rename onto the target), so — unlike archive_write's
    os.link — no leftover tmp can ever be hardlinked to the target; the fixed-name tmp is safe here.
    Cleans the tmp if the rename itself fails ([R9] D3), symmetric with archive_write."""
    real = os.path.realpath(path)
    tmp = real + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        os.replace(tmp, real)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def archive_write(dest, content):
    """No-clobber + idempotent + crash-atomic: write a FRESH-inode tmp (tempfile.mkstemp, i.e.
    O_CREAT|O_EXCL with a random name), then os.link it into place (atomic; raises FileExistsError
    if dest already exists). A crash mid-write leaves only an orphan tmp — dest is NEVER a partial
    file, so the commit point (dest present) always implies a COMPLETE body. do_retire's recovery
    branch relies on this: it deletes the live source once the stamped archive exists, so a torn
    archive would mean deleting against a truncated backup ([R9] D1 — open('x')+write was non-atomic).
    mkstemp (not a fixed dest+'.tmp' via open('w')) is load-bearing: a crash between os.link and the
    finally-unlink can leave a dest.tmp HARDLINKED to the committed dest; a fixed-name reuse would
    then truncate that shared inode on the next archive and silently overwrite/erase the committed
    archive — a random fresh inode each call can't ([R9] D1 regression caught in R9 review). On a
    collision an identical archive is a no-op (crash-recovery re-archiving succeeds), a different one
    refuses. Returns None on success/no-op, else an error string."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        try:
            os.link(tmp, dest)   # atomic no-clobber commit; a partial write never reaches dest
            return None
        except FileExistsError:
            try:
                same = open(dest, encoding="utf-8").read() == content
            except (OSError, UnicodeError):
                same = False   # an unreadable/undecodable existing archive is not a safe idempotent match
            if same:
                return None  # idempotent re-archive
            return f"archive dest exists with different content: {dest}"
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
