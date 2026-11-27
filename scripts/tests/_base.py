#!/usr/bin/env python3
"""Shared utilities and test bases for the Teamwork CLI suite."""
import datetime as dt
import contextlib, io, itertools, json, os, re, shutil, subprocess, sys, tempfile, threading, time, unittest, uuid
from unittest import mock
import zipfile
from pathlib import Path
SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parent
FIXTURES = SCRIPTS / "fixtures"
sys.path.insert(0, str(SCRIPTS))
import quota
import tw
TW = Path(os.environ.get("TW_TEST_SCRIPT", SCRIPTS / "tw.py"))
RELEASE = SCRIPTS / "release.py"
WALK = ["planned", "approved", "dispatched", "working", "review", "done"]


def quiz_transcript(path, item, answer):
    stamp=dt.datetime.now().astimezone().isoformat(timespec="seconds")
    rows=[{"type":"user","timestamp":stamp,"origin":{"kind":"human"},"message":{"role":"user","content":item["question"]}},
          {"type":"user","timestamp":stamp,"origin":{"kind":"human"},"message":{"role":"user","content":answer}}]
    Path(path).write_text("\n".join(json.dumps(x,ensure_ascii=False) for x in rows)+"\n",encoding="utf-8")
def walk_args(run, args, state_file, prepare_primary=False):
    """Tests that only care about the end status: step a task through the legal path first (T20 enforces it).
    run(*args) must run tw.py; the final call is left to the caller."""
    if len(args) < 3 or args[0] != "task" or args[1] != "set" or "--status" not in args:
        return
    target = args[args.index("--status") + 1]
    if target == "blocked":
        target_path = WALK[:WALK.index("working") + 1]
    elif target in WALK:
        target_path = WALK[:WALK.index(target)]
    else:
        return
    st = json.loads(Path(state_file).read_text(encoding="utf-8"))
    cur = st["tasks"].get(args[2], {}).get("status")
    if cur not in WALK:
        return
    task = st["tasks"].get(args[2], {})
    if prepare_primary and target in ("review", "done") and st.get("prefs", {}).get("require_primary", True):
        if not task.get("primary"):
            run("task", "set", args[2], "--primary", "The user can see all 12 restored records")
        if (task.get("primary_check") or {}).get("result") not in ("seen", "pending-real"):
            proof = Path(state_file).parent.parent / "primary-proof.txt"
            proof.write_text("real artifact: 12 records", encoding="utf-8")
            run("task", "verify", args[2], "--seen", "--how", "Open the real project artifact", "--evidence", proof.name)
    for step in target_path[WALK.index(cur) + 1:]:
        if step == "approved":
            run("plan", "approve", "--user-said", "ok")  # tasks are approved with the plan (T20)
        else:
            run("task", "set", args[2], "--status", step)
class TwCase(unittest.TestCase):
    def synthetic_bridge(self, session, transcript_path, config_dir=None, second_local_session=None):
        """SYNTHETIC — detail tests only; primary-outcome tests must use fixtures.place()."""
        transcript = Path(transcript_path)
        local_session = f"synthetic-{session.removeprefix('session_')}"
        rows = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines() if line.strip()]
        for row in rows:
            row["sessionId"] = local_session
        transcript.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        config = Path(config_dir) if config_dir else self.dir / ".claude"
        local_sessions = [local_session]
        if second_local_session:
            local_sessions.append(second_local_session)
        bridge_contents = "".join(json.dumps({"session_id": local}, ensure_ascii=False) + "\n"
                                   for local in local_sessions)
        configs = {config, self.dir / ".claude"}
        for root in configs:
            root.mkdir(parents=True, exist_ok=True)
            bridge = root / f"bridge-transcript-cse_{str(session).removeprefix('session_')}.jsonl"
            bridge.write_text(bridge_contents, encoding="utf-8")
        self.env["HOME"] = str(self.dir)
        self.env["CLAUDE_CONFIG_DIR"] = str(config)
        return transcript

    def place_fixture(self, name, session, config_dir=None):
        import importlib.util
        generator = FIXTURES / "regenerate.py"
        spec = importlib.util.spec_from_file_location("teamwork_test_fixtures", generator)
        fixture_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture_mod)
        config = Path(config_dir) if config_dir else self.dir / "claude-config"
        transcript = fixture_mod.place(name, config, session=session)
        self.env["CLAUDE_CONFIG_DIR"] = str(config)
        return transcript

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.env = {k: v for k, v in os.environ.items() if k != "TEAMWORK_DIR"}
        guard_dir = self.dir / "guard-log"
        guard_dir.mkdir()
        self.env["TW_GUARD_LOG"] = str(guard_dir / "guard-errors.jsonl")
        self.tw("init", "demo", "--goal", "test project")
        # Legacy suites here cover non-R71 behavior; PrimaryOutcome owns the enabled-gate coverage.
        self.tw("prefs", "--set", "require_primary=false", "--user-said", "legacy fixture covers other task behavior")

    def tearDown(self):
        self._tmp.cleanup()

    def tw(self, *args, ok=True, walk=True):
        # Existing suites predate R71. Prepare the primary artifact before moving
        # a legacy task into review, and supply the required independent recheck
        # metadata when those suites record a passing verdict.
        if len(args) >= 3 and args[0:2] == ("task", "set"):
            tid = args[2]
            wants_review = ("--status" in args and args[args.index("--status") + 1] in ("review", "done"))
            passing_review = ("--review" in args and args[args.index("--review") + 1] in ("pass", "capped"))
            if (wants_review or passing_review) and (self.dir / ".teamwork" / "state.json").exists():
                self.verify_primary(tid)
            if "--review" in args:
                defaults = [("--tests", "ran"), ("--base", "0000000")]
                if passing_review:
                    defaults += [("--recheck-how", "independent test run"),
                                 ("--recheck-evidence", "Primary output shows 12 records")]
                if "--tests" in args and args[args.index("--tests") + 1] == "not-run":
                    defaults.append(("--tests-why", "legacy fixture did not run tests"))
                for flag, value in defaults:
                    if flag not in args:
                        args = (*args, flag, value)
        if len(args) >= 2 and args[0:1] == ("task-msg",) and "--dispatch" in args:
            tid = args[1]
            if (self.dir / ".teamwork" / "state.json").exists() and not self.state()["tasks"].get(tid, {}).get("primary"):
                self.tw("task", "set", tid, "--primary", "The user can see all 12 restored records")
        if walk:
            walk_args(lambda *x: self.tw(*x, walk=False), args, self.dir / ".teamwork" / "state.json", prepare_primary=True)
        r = subprocess.run([sys.executable, str(TW), *args], cwd=self.dir, env=self.env,
                           capture_output=True, text=True)
        if getattr(self, "smoke_evidence", False) and os.environ.get("TW_SMOKE_EVIDENCE"):
            with open(os.environ["TW_SMOKE_EVIDENCE"], "a", encoding="utf-8") as f:
                f.write(f"$ tw {' '.join(args)}\nexit={r.returncode}\nstdout:\n{r.stdout}stderr:\n{r.stderr}\n\n")
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
        return r

    def state(self):
        return json.loads((self.dir / ".teamwork" / "state.json").read_text(encoding="utf-8"))

    def verify_primary(self, tid):
        task = self.state()["tasks"].get(tid)
        if not task:
            return
        if not task.get("primary"):
            self.tw("task", "set", tid, "--primary", "The user can see all 12 restored records")
        if (task.get("primary_check") or {}).get("result") not in ("seen", "pending-real"):
            proof = self.dir / "primary-proof.txt"
            proof.write_text("A real artifact containing 12 records", encoding="utf-8")
            self.tw("task", "verify", tid, "--seen", "--how", "Run the project artifact", "--evidence",
                    proof.name)

    def context(self):
        out = self.tw("context").stdout
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]


class GitCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name) / "proj"
        self.proj.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.proj / "README").write_text("r\n")
        (self.proj / ".gitignore").write_text(".teamwork/\n")
        self.commit("init")
        self.tw("init", "proj")
        self.tw("thread", "add", "ui")
        self.tw("thread", "add", "api")
        self.tw("plan", "approve", "--user-said", "ok")

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *x):
        return subprocess.run(["git", "-C", str(self.proj), *x], check=True, capture_output=True, text=True)

    def commit(self, msg):
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", msg)

    def tw(self, *args, walk=True, ok=True):
        if len(args) >= 3 and args[0:2] == ("task", "set") and "--review" in args:
            passing = args[args.index("--review") + 1] in ("pass", "capped")
            defaults = [("--tests", "ran"), ("--base", "0000000")]
            if passing:
                defaults += [("--recheck-how", "independent test run"),
                             ("--recheck-evidence", "Primary output shows 12 records")]
            for flag, value in defaults:
                if flag not in args:
                    args = (*args, flag, value)
        if walk and ok:
            walk_args(lambda *x: self.tw(*x, walk=False), args, self.proj / ".teamwork" / "state.json", prepare_primary=True)
        r = subprocess.run([sys.executable, str(TW), "-C", str(self.proj), *args], cwd=self.proj,
                           capture_output=True, text=True)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r



class T80Helpers:
    def cli(self,*args,ok=True):
        p=subprocess.run([sys.executable,str(TW),*map(str,args)],cwd=self.project,env=self.env,text=True,capture_output=True)
        if ok and p.returncode: self.fail(p.stderr or p.stdout)
        if not ok and p.returncode==0: self.fail("expected failure")
        return p

    def state(self): return json.loads((self.project/".teamwork"/"state.json").read_text())

    def sid(self): return str(uuid.uuid4())

    def hook(self,session,source="compact"):
        import contextlib,io
        old=os.environ.copy(); os.environ.update(self.env)
        oldstdin=sys.stdin; output=io.StringIO(); readfd,writefd=os.pipe(); os.write(writefd,json.dumps({"session_id":session,"source":source,"hook_event_name":"SessionStart"}).encode()); os.close(writefd); sys.stdin=os.fdopen(readfd,"r")
        try:
            with contextlib.redirect_stdout(output): tw.cmd_context(None)
        finally: sys.stdin.close(); sys.stdin=oldstdin; os.environ.clear(); os.environ.update(old)
        return json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]

    def record(self,session,rows):
        folder=self.config/"projects"/"-tmp-project"; folder.mkdir(parents=True,exist_ok=True)
        path=folder/f"{session}.jsonl"; path.write_text("\n".join(json.dumps(x,ensure_ascii=False) for x in rows)+"\n",encoding="utf-8"); return path
