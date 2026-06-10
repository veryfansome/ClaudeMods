#!/usr/bin/env python3
"""Tests for reflect-scan + reflect-end — Layer 1: the mining front of /memory-reflect.

Covers the four salience markers on their channels (concession / interruption /
permission-decline / repeated-tool-failure), the genuine-turn noise filter (injected
blocks + compaction summaries excluded), permission-decline's startswith discipline (a
genuine denial vs a tool_result that merely quotes the string), the no-excerpts invariant,
the pointer grammar round-tripping through distill-scan's classifier (the shared contract),
per-path watermark handling (a resume-ended session stays mineable and re-mines only
appended records; a hook-less session is still discovered by the dir scan), swept-path
tolerance, and idempotent re-runs. No model calls.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
SCAN = os.path.join(BIN, "reflect-scan")
DISTILL_SCAN = os.path.join(BIN, "distill-scan")
END = os.path.join(SCRIPTS, "reflect-end")

from importlib.machinery import SourceFileLoader
AC = SourceFileLoader("ac_probe", os.path.join(BIN, "_apply_common.py")).load_module()

DENIAL = "The user doesn't want to proceed with this tool use. The tool use was rejected."


# --- transcript record builders -------------------------------------------------------
def asst_text(t):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": t}]}}


def asst_tool(tid, name):
    return {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "tool_use", "id": tid, "name": name}]}}


def user_text(s):
    return {"type": "user", "message": {"role": "user", "content": s}}


def tool_result(tid, content, is_error=False):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": content, "is_error": is_error}]}}


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="rf-test-")
        self.store = os.path.join(self.home, ".claudemods", "shared-memory")
        self.proj = os.path.join(self.home, ".claude", "projects", "-proj")
        os.makedirs(self.store)
        os.makedirs(self.proj)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def transcript(self, name, records):
        p = os.path.join(self.proj, name)
        with open(p, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        return p

    def append_records(self, name, records):
        with open(os.path.join(self.proj, name), "a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def record_end(self, tpath, reason):
        rp = os.path.join(self.store, ".reflect")
        os.makedirs(rp, exist_ok=True)
        with open(os.path.join(rp, "ends.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"transcript_path": tpath, "reason": reason, "ts": 0}) + "\n")

    def scan(self):
        r = subprocess.run([sys.executable, SCAN], env=dict(os.environ, HOME=self.home),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def queue(self):
        p = os.path.join(self.store, ".candidates.log")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [l.rstrip("\n") for l in f]

    def markers(self):
        return sorted(l.rsplit(":", 1)[1] for l in self.queue() if l.startswith("reflect-pointer:"))

    def watermark(self):
        p = os.path.join(self.store, ".reflect", "watermark.json")
        if not os.path.exists(p):
            return {}
        with open(p, encoding="utf-8") as f:
            return json.load(f)


class Markers(Base):
    def test_concession_fires_on_assistant_text(self):
        self.transcript("a.jsonl", [asst_text("You're right, I overclaimed there.")])
        self.scan()
        self.assertEqual(self.markers(), ["concession"])

    def test_interruption_fires_on_genuine_user_turn(self):
        self.transcript("a.jsonl", [user_text("[Request interrupted by user] stop")])
        self.scan()
        self.assertEqual(self.markers(), ["interruption"])

    def test_permission_decline_startswith_only(self):
        # a genuine denial BEGINS with the string; a tool_result that only quotes it
        # (e.g. a grep of this design) must NOT fire.
        self.transcript("a.jsonl", [
            asst_tool("t1", "Bash"), tool_result("t1", DENIAL, is_error=True),
            asst_tool("t2", "Bash"),
            tool_result("t2", "found: the user doesn't want to proceed with this tool use\n(in a file)"),
        ])
        self.scan()
        self.assertEqual(self.markers(), ["permission-decline"])

    def test_three_consecutive_errors_emit_once(self):
        # G4 once-per-run: a run of 3+ consecutive same-tool errors emits EXACTLY ONE
        # repeated-tool-failure (the `err_len == 2` guard, not `>= 2`) — a `>= 2` regression
        # would emit again on the 3rd error, which every prior test (capping at 2) misses
        self.transcript("a.jsonl", [
            asst_tool("t1", "Bash"), tool_result("t1", "e", is_error=True),
            asst_tool("t2", "Bash"), tool_result("t2", "e", is_error=True),
            asst_tool("t3", "Bash"), tool_result("t3", "e", is_error=True)])
        self.scan()
        self.assertEqual(self.markers(), ["repeated-tool-failure"])

    def test_repeated_tool_failure_needs_two_consecutive_same_tool(self):
        # one error → nothing; two consecutive same-tool errors → one hit
        self.transcript("a.jsonl", [
            asst_tool("t1", "Bash"), tool_result("t1", "boom", is_error=True),
            asst_tool("t2", "Read"), tool_result("t2", "ok"),          # resets (success)
            asst_tool("t3", "Bash"), tool_result("t3", "boom", is_error=True),
            asst_tool("t4", "Bash"), tool_result("t4", "boom", is_error=True),  # 2nd consecutive Bash
        ])
        self.scan()
        self.assertEqual(self.markers(), ["repeated-tool-failure"])

    def test_two_errors_different_tools_do_not_fire(self):
        self.transcript("a.jsonl", [
            asst_tool("t1", "Bash"), tool_result("t1", "boom", is_error=True),
            asst_tool("t2", "Read"), tool_result("t2", "boom", is_error=True),
        ])
        self.scan()
        self.assertEqual(self.markers(), [])

    def test_noise_filter_excludes_injected_and_compaction(self):
        # marker-looking strings inside a command block, a system-reminder, and a compaction
        # summary are NOT genuine turns → no interruption hit
        self.transcript("a.jsonl", [
            user_text("<command-name>/foo</command-name> [Request interrupted by user]"),
            user_text("<system-reminder> [Request interrupted by user] </system-reminder>"),
            user_text("This session is being continued from a previous conversation. "
                      "[Request interrupted by user] was seen earlier."),
        ])
        self.scan()
        self.assertEqual(self.markers(), [])

    def test_interruption_fires_on_list_form_user_content(self):
        # a GENUINE interrupt is recorded as a content LIST with a text block, not a string
        self.transcript("a.jsonl", [{"type": "user", "message": {"role": "user",
            "content": [{"type": "text", "text": "[Request interrupted by user]"}]}}])
        self.scan()
        self.assertEqual(self.markers(), ["interruption"])

    def test_consecutive_denials_are_not_repeated_tool_failure(self):
        # a permission denial is is_error, but it is a user choice, not a technical failure —
        # two in a row fire permission-decline, never repeated-tool-failure
        self.transcript("a.jsonl", [
            asst_tool("t1", "Bash"), tool_result("t1", DENIAL, is_error=True),
            asst_tool("t2", "Bash"), tool_result("t2", DENIAL, is_error=True),
        ])
        self.scan()
        self.assertEqual(self.markers(), ["permission-decline", "permission-decline"])

    def test_unresolved_tool_errors_do_not_match_as_same_tool(self):
        # two errors whose tool_use_id never resolves (no tool_use record) → tool name '' —
        # an empty name is not a matchable identity, so they must not fire repeated-tool-failure
        self.transcript("a.jsonl", [
            tool_result("ghost1", "boom", is_error=True),
            tool_result("ghost2", "boom", is_error=True),
        ])
        self.scan()
        self.assertEqual(self.markers(), [])


class PointerContract(Base):
    def test_no_excerpts_pointer_shape(self):
        self.transcript("a.jsonl", [asst_text("You're right.")])
        self.scan()
        line = self.queue()[0]
        self.assertTrue(line.startswith("reflect-pointer:"))
        path, off, marker = line[len("reflect-pointer:"):].rsplit(":", 2)
        self.assertTrue(path.endswith("a.jsonl"))
        self.assertTrue(off.isdigit())
        self.assertEqual(marker, "concession")
        self.assertNotIn("You're right", line)  # the pointer carries no transcript text

    def test_pointer_roundtrips_through_distill_scan(self):
        # the shared grammar: a reflect-scan-emitted pointer must classify as passthrough
        self.transcript("a.jsonl", [asst_text("You're right.")])
        self.scan()
        r = subprocess.run([sys.executable, DISTILL_SCAN], env=dict(os.environ, HOME=self.home),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        q = json.loads(r.stdout)["queue"]
        passthrough = [e for e in q if e["kind"] == "passthrough"]
        self.assertEqual(len(passthrough), 1, q)
        self.assertTrue(passthrough[0]["entry"].startswith("reflect-pointer:"))


class Watermark(Base):
    def test_offset_is_record_count_and_idempotent(self):
        recs = [asst_text("hi"), asst_text("You're right."), asst_text("bye")]
        tp = self.transcript("a.jsonl", recs)
        self.scan()
        self.assertEqual(self.watermark()[tp]["offset"], 3)  # 0-based record index past the end
        n_before = len(self.queue())
        self.scan()  # re-run: nothing new
        self.assertEqual(len(self.queue()), n_before)

    def test_hookless_session_discovered_by_dir_scan(self):
        # no ends.jsonl record at all — the transcript-dir scan still finds and mines it
        self.transcript("a.jsonl", [asst_text("You're right.")])
        out = self.scan()
        self.assertEqual(out["pointers_emitted"], 1)
        self.assertEqual(self.markers(), ["concession"])

    def test_resume_session_stays_mineable_and_remines_appended(self):
        tp = self.transcript("a.jsonl", [asst_text("hello")])  # no marker yet
        self.record_end(tp, "resume")
        out1 = self.scan()
        self.assertEqual(out1["pointers_emitted"], 0)
        self.assertFalse(self.watermark()[tp]["final"], "resume-ended session marked final")
        # the session resumes and appends a correction; a per-path offset re-mines only the new tail
        self.append_records("a.jsonl", [asst_text("You're right, my mistake.")])
        out2 = self.scan()
        self.assertEqual(out2["pointers_emitted"], 1, "appended-after-resume content not mined")
        self.assertEqual(self.markers(), ["concession"])

    def test_terminal_reason_marks_final(self):
        tp = self.transcript("a.jsonl", [asst_text("bye")])
        self.record_end(tp, "clear")
        self.scan()
        self.assertTrue(self.watermark()[tp]["final"])

    def test_terminal_then_resume_remines_appended_tail(self):
        # [R9] D4: a transcript terminally ended (final=True) that is later RESUMED (a fresh
        # 'resume' end) must re-open and mine the appended tail. The stored `final` was previously
        # honored BEFORE the current reason was read, silently dropping the resumed correction.
        tp = self.transcript("a.jsonl", [asst_text("hello")])   # no marker yet
        self.record_end(tp, "clear")                            # terminal end → final=True
        out1 = self.scan()
        self.assertEqual(out1["pointers_emitted"], 0)
        self.assertTrue(self.watermark()[tp]["final"], "terminal end should mark final")
        # the user resumes the terminally-ended session and appends a correction to the same file
        self.record_end(tp, "resume")
        self.append_records("a.jsonl", [asst_text("You're right, my mistake.")])
        out2 = self.scan()
        self.assertEqual(out2["pointers_emitted"], 1, "resumed tail of a terminally-ended session not mined")
        self.assertEqual(self.markers(), ["concession"])
        self.assertFalse(self.watermark()[tp]["final"], "a resumed session must not stay final")

    def test_swept_recorded_path_skipped_and_advanced(self):
        missing = os.path.join(self.proj, "gone.jsonl")  # recorded but never created
        self.record_end(missing, "logout")
        out = self.scan()  # must not raise
        self.assertIn(missing, out["swept"])
        self.assertTrue(self.watermark()[missing]["final"])

    def test_swept_resume_ended_path_settles_and_does_not_reemit(self):
        # [R9 D-2/W-4]: a resume-reasoned transcript that is swept (deleted) must settle ONCE. Its
        # ends.jsonl reason stays 'resume' forever (append-only), so without gating the skip on the
        # file's absence a swept resumed path would re-mine (→None→swept) and re-emit every scan.
        missing = os.path.join(self.proj, "gone.jsonl")   # recorded (resume) but never created
        self.record_end(missing, "resume")
        out1 = self.scan()
        self.assertIn(missing, out1["swept"])
        self.assertTrue(self.watermark()[missing]["final"])
        out2 = self.scan()  # second pass must NOT re-surface it
        self.assertNotIn(missing, out2["swept"], "a swept resume-ended path re-emitted on the next scan")


class FailSoft(Base):
    def test_non_dict_transcript_line_does_not_crash(self):
        # a bare scalar/array line is valid JSON but not a record — must be skipped, not fatal
        p = os.path.join(self.proj, "a.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write("42\n")
            f.write('"a bare string"\n')
            f.write(json.dumps(asst_text("You're right.")) + "\n")
        self.scan()  # must not raise
        self.assertEqual(self.markers(), ["concession"])

    def test_dict_record_with_non_dict_message_does_not_crash(self):
        # M4-tail review M1: a valid record whose `message` is a scalar (42/str/list) must not
        # crash mine() — else the pass aborts before the watermark advances and the poison
        # transcript re-crashes every future run, permanently blocking ALL mining (poison pill)
        self.transcript("poison.jsonl", [{"type": "user", "message": 42},
                                         {"type": "assistant", "message": "a string"}])
        self.transcript("good.jsonl", [asst_text("You're right.")])
        out = self.scan()  # must not raise; rc 0 asserted inside scan()
        self.assertEqual(out["pointers_emitted"], 1, "the sibling concession must still be mined")
        self.assertEqual(self.markers(), ["concession"])
        self.assertTrue(self.watermark(), "watermark must advance despite the poison transcript")

    def test_non_dict_ends_line_does_not_abort_the_pass(self):
        # a non-object line in ends.jsonl must not blow up latest_reasons() and lose the pass
        rp = os.path.join(self.store, ".reflect")
        os.makedirs(rp, exist_ok=True)
        with open(os.path.join(rp, "ends.jsonl"), "w", encoding="utf-8") as f:
            f.write("42\n")
        self.transcript("a.jsonl", [asst_text("You're right.")])
        out = self.scan()
        self.assertEqual(out["pointers_emitted"], 1)

    def test_path_with_newline_emits_no_forged_line(self):
        # a legal-but-pathological path with a newline must not split into a second queue line
        try:
            self.transcript("a\nb.jsonl", [asst_text("You're right.")])
        except OSError:
            self.skipTest("filesystem rejects newline in filename")
        out = self.scan()
        self.assertEqual(out["pointers_emitted"], 0)
        self.assertTrue(all(l.startswith("reflect-pointer:") for l in self.queue()))


class Grammar(unittest.TestCase):
    def setUp(self):
        import importlib.util
        from importlib.machinery import SourceFileLoader
        loader = SourceFileLoader("reflect_scan", SCAN)  # SCAN has no .py extension
        spec = importlib.util.spec_from_loader("reflect_scan", loader)
        self.rs = importlib.util.module_from_spec(spec)
        loader.exec_module(self.rs)

    def test_pointer_roundtrips_including_colon_paths(self):
        for path in ("/Users/me/a.jsonl", "/Users/me/a:b/c.jsonl", "/p/ends:in:colon.jsonl"):
            for marker in ("concession", "interruption", "permission-decline", "repeated-tool-failure"):
                got = self.rs.parse_pointer(self.rs.make_pointer(path, 42, marker))
                self.assertEqual(got, {"session_path": path, "offset": 42, "marker": marker})

    def test_parse_pointer_rejects_non_pointer(self):
        self.assertIsNone(self.rs.parse_pointer("/some/file.md"))
        self.assertIsNone(self.rs.parse_pointer("reflect-pointer:/a.jsonl:notanumber:concession"))

    def test_prefix_is_the_shared_contract_with_distill_scan(self):
        # the emitter and distill-scan's classifier must agree on the exact token, or a mined
        # pointer falls through to 'stale' and is lost once the watermark advances
        with open(SCAN, encoding="utf-8") as f:
            scan_src = f.read()
        with open(DISTILL_SCAN, encoding="utf-8") as f:
            distill_src = f.read()
        self.assertIn('POINTER_PREFIX = "reflect-pointer:"', scan_src)
        self.assertIn('"reflect-pointer:"', distill_src)


class ReflectEnd(Base):
    def end(self, payload):
        return subprocess.run([sys.executable, END], input=json.dumps(payload),
                              env=dict(os.environ, HOME=self.home), capture_output=True, text=True)

    def ends_records(self):
        p = os.path.join(self.store, ".reflect", "ends.jsonl")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(l) for l in f]

    def test_appends_end_record(self):
        r = self.end({"transcript_path": "/x/abc.jsonl", "reason": "resume"})
        self.assertEqual(r.returncode, 0)
        recs = self.ends_records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["transcript_path"], "/x/abc.jsonl")
        self.assertEqual(recs[0]["reason"], "resume")
        self.assertIn("ts", recs[0])

    def test_malformed_payload_is_silent(self):
        r = subprocess.run([sys.executable, END], input="not json",
                           env=dict(os.environ, HOME=self.home), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.ends_records(), [])

    def test_no_transcript_path_is_silent(self):
        r = self.end({"reason": "clear"})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.ends_records(), [])

    def test_non_dict_payload_is_silent(self):
        # well-formed but non-object JSON (a bare number) must not traceback the SessionEnd hook
        r = subprocess.run([sys.executable, END], input="42",
                           env=dict(os.environ, HOME=self.home), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.ends_records(), [])


class StraddleWatermark(Base):
    def test_error_run_straddling_watermark_emits_once(self):
        # G4: a consecutive-error run whose 1st error is BELOW a start>0 watermark and 2nd is at/
        # above must still emit exactly ONE repeated-tool-failure at the straddling offset — mine()
        # tracks err_len from FILE START, not rebased on the watermark. (A regression that rebased
        # on `start` would see only 1 error past the mark and emit nothing.)
        self.transcript("s.jsonl", [asst_tool("t1", "Bash"), tool_result("t1", "boom", is_error=True)])
        self.scan()   # 1st pass: err_len=1, no marker; watermark advances past the 1st error (offset 2)
        self.append_records("s.jsonl", [asst_tool("t2", "Bash"), tool_result("t2", "boom", is_error=True)])
        self.scan()
        self.assertEqual(self.markers(), ["repeated-tool-failure"])
        ptr = next(l for l in self.queue() if l.startswith("reflect-pointer:"))
        self.assertEqual(ptr.rsplit(":", 2)[1], "3", ptr)   # emitted at the 2nd (straddling) error's record offset


class DomainMissInputs(Base):
    def test_scanned_entry_carries_cwd_and_repo_domains(self):
        # Unit 8: each scanned session surfaces its cwd + the repo's mapped domains, so the skill's
        # domain-miss audit knows what did (not) load beyond the always-on general+personal tier
        repo = os.path.join(self.home, "work", "r"); os.makedirs(repo)
        subprocess.run(["git", "-C", repo, "init", "-q"])
        with open(os.path.join(self.store, ".domains.json"), "w") as f:
            json.dump({"version": 1, "repos": {AC.repo_root(repo): ["gcp", "security"]}}, f)
        self.transcript("s.jsonl", [
            {"cwd": repo, "type": "user", "message": {"role": "user", "content": "hi"}},
            asst_text("ok")])
        e = next(x for x in self.scan()["scanned"] if x["path"].endswith("s.jsonl"))
        self.assertEqual(e["cwd"], repo)
        self.assertEqual(sorted(e["domains"]), ["gcp", "security"])

    def test_unrecoverable_cwd_is_failsoft(self):
        # no cwd anywhere in the transcript → cwd null, domains [] — never a crash
        self.transcript("s.jsonl", [asst_text("ok")])
        e = next(x for x in self.scan()["scanned"] if x["path"].endswith("s.jsonl"))
        self.assertIsNone(e["cwd"])
        self.assertEqual(e["domains"], [])


if __name__ == "__main__":
    unittest.main()
