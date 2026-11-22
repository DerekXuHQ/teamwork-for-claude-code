#!/usr/bin/env python3
"""Tests for tw.py. Stdlib only; every test works in a temp project folder.

  python3 scripts/test_tw.py
"""

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts/: quota.py next to tw.py
import quota  # noqa: E402

TW = Path(__file__).resolve().parent / "tw.py"
# bash-guard logs its own errors: the tests never write (or count) the real ~/.cache/teamwork/guard-errors.jsonl
_GUARD_LOG_DIR = tempfile.TemporaryDirectory(prefix="tw-guard-log-")
os.environ["TW_GUARD_LOG"] = str(Path(_GUARD_LOG_DIR.name) / "guard-errors.jsonl")
WALK = ["planned", "approved", "dispatched", "working", "review", "done"]


def walk_args(run, args, state_file):
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
    for step in target_path[WALK.index(cur) + 1:]:
        if step == "approved":
            run("plan", "approve", "--user-said", "ok")  # tasks are approved with the plan (T20)
        else:
            run("task", "set", args[2], "--status", step)


class TwCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.env = {k: v for k, v in os.environ.items() if k != "TEAMWORK_DIR"}
        self.tw("init", "demo", "--goal", "test project")

    def tearDown(self):
        self._tmp.cleanup()

    def tw(self, *args, ok=True, walk=True):
        if walk:
            walk_args(lambda *x: self.tw(*x, walk=False), args, self.dir / ".teamwork" / "state.json")
        r = subprocess.run([sys.executable, str(TW), *args], cwd=self.dir, env=self.env,
                           capture_output=True, text=True)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def state(self):
        return json.loads((self.dir / ".teamwork" / "state.json").read_text(encoding="utf-8"))

    def context(self):
        out = self.tw("context").stdout
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]


class SessionVersions(TwCase):
    def run_context(self, data):
        return subprocess.run([sys.executable, str(TW), "context"], cwd=self.dir, env=self.env,
                              input=data, capture_output=True, text=True)

    def test_context_records_and_reports_versions(self):
        r = self.run_context('{"session_id":"session_01Zorblax"}')
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = [json.loads(x) for x in (self.dir / ".teamwork" / "session-versions.jsonl").read_text().splitlines()]
        self.assertEqual(rows[-1]["session"], "session_01Zorblax")
        self.assertIn("teamwork version:", r.stdout)
        self.tw("thread", "add", "api")
        st = self.state()
        st["threads"]["api"]["session_id"] = "s-old"
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st))
        (self.dir / ".teamwork" / "session-versions.jsonl").write_text(
            json.dumps({"session": "s-old", "version": "old release", "at": "now", "cwd": str(self.dir)}) + "\n")
        got = json.loads(self.tw("version", "--sessions").stdout)
        self.assertEqual(got["old_sessions"][0]["thread"], "api")
        self.tw("prefs", "--set", "skill_edge=/tmp/x")
        self.assertIn("tests unreleased teamwork", self.context())
        self.assertEqual(self.run_context("garbage").returncode, 0)


class OpeningAndSelfcheck(TwCase):
    def test_title_with_goal(self):
        self.tw("lead", "--session", "lead-session")
        out = json.loads(self.tw("lead").stdout)
        self.assertEqual(out["title"], f"[{self.state()['prefs']['short_name']}] lead · test project")

    def test_policy_preferences_require_user_words(self):
        self.assertNotEqual(self.tw("prefs", "--set", "spawn_policy=free", ok=False).returncode, 0)
        self.tw("prefs", "--set", "spawn_policy=free", "--user-said", "let it proceed")
        self.assertEqual(self.state()["prefs"]["spawn_policy"], "free")
        self.assertNotEqual(self.tw("prefs", "--set", "spawn_policy=wild", "--user-said", "yes", ok=False).returncode, 0)

    def test_opening_keys_unanswered_and_other_project_answer(self):
        out = json.loads(self.tw("opening", "--json").stdout)
        self.assertEqual([x["key"] for x in out["questions"]], ["spawn_policy", "perm_policy", "gpt_policy"])
        self.assertEqual(out["unanswered_now"], ["spawn_policy", "perm_policy", "gpt_policy"])
        sibling = self.dir / "sibling"; sibling.mkdir()
        r = subprocess.run([sys.executable, str(TW), "init", "other"], cwd=sibling, env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        subprocess.run([sys.executable, str(TW), "prefs", "--set", "spawn_policy=free", "--user-said", "free is fine"], cwd=sibling, env=self.env, check=True, capture_output=True)
        out = json.loads(self.tw("opening", "--json").stdout)
        self.assertEqual(out["questions"][0]["last"]["value"], "free")
        beside = self.dir.parent / f"{self.dir.name}-Beta-folder"; beside.mkdir()  # a sibling folder, other name
        subprocess.run([sys.executable, str(TW), "init", "beta"], cwd=beside, env=self.env, check=True, capture_output=True)
        r = subprocess.run([sys.executable, str(TW), "opening", "--json"], cwd=beside, env=self.env, capture_output=True, text=True)
        self.assertEqual(json.loads(r.stdout)["questions"][0]["last"]["value"], "free")

    def test_selfcheck_hooks_need_real_hook_entries(self):
        c = self.dir / ".claude"; c.mkdir(exist_ok=True)
        (c / "settings.local.json").write_text(json.dumps({"permissions": {"allow": ["Bash(python3 tw.py context)"]},
            "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "python3 tw.py bash-guard"}]}]}}))
        hooks = lambda: next(x for x in json.loads(self.tw("selfcheck", "--json").stdout)["items"] if x["item"] == "hooks")
        self.assertFalse(hooks()["ok"])  # an allow rule naming context is not a SessionStart hook
        (c / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": "python3 /x/tw.py context"}]}]}}))
        self.assertTrue(hooks()["ok"])
        out = json.loads(self.tw("selfcheck", "--desktop", "--json").stdout)
        self.assertIn("on the desktop", out["first_line"])

    def test_selfcheck_mode_model_brief_and_zero_exit(self):
        out = json.loads(self.tw("selfcheck", "--mode", "default", "--model", "configured", "--served-model", "served", "--json").stdout)
        self.assertIn("please switch this session to auto once", out["first_line"])
        self.assertFalse(next(x for x in out["items"] if x["item"] == "model")["ok"])
        self.tw("lead", "--session", "lead-session", "--mode", "auto")
        out = json.loads(self.tw("selfcheck", "--mode", "auto", "--json").stdout)
        self.assertTrue(next(x for x in out["items"] if x["item"] == "mode")["ok"])
        brief = self.tw("selfcheck", "--mode", "auto", "--brief").stdout
        self.assertNotIn("✓ mode", brief)
        self.assertEqual(self.tw("selfcheck", "--mode", "default").returncode, 0)


class Releases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"; self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.repo / "SKILL.md").write_text("release one\n")
        (self.repo / "scripts").mkdir()
        shutil.copy2(TW, self.repo / "scripts" / "tw.py")
        shutil.copy2(TW.parent / "quota.py", self.repo / "scripts" / "quota.py")  # tw.py imports it
        self.commit("first")
        self.home = self.root / "home"; self.home.mkdir()
        self.configs = [self.home / ".claude", self.home / ".claude-b"]
        for c in self.configs:
            (c / "skills").mkdir(parents=True)
            (c / "skills" / "teamwork").symlink_to(self.repo)
        self.env = {**os.environ, "HOME": str(self.home), "TW_NOW": "20261121-120000"}
        self.env.pop("CLAUDE_CONFIG_DIR", None)

    def tearDown(self): self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True)

    def commit(self, message):
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", message)

    def release(self, *args):
        return subprocess.run([sys.executable, str(TW.parent / "release.py"), "--repo", str(self.repo), *args],
                              env=self.env, capture_output=True, text=True)

    def test_publish_rollback_edge_and_status(self):
        dry = self.release("publish")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertTrue(all((c / "skills" / "teamwork").resolve() == self.repo for c in self.configs))
        self.assertFalse((self.configs[0] / "teamwork-releases").exists())
        self.assertEqual(self.release("publish", "--apply").returncode, 0)
        first = json.loads((self.configs[0] / "teamwork-releases" / "index.json").read_text())["current"]
        self.assertEqual((self.configs[1] / "skills" / "teamwork").resolve(),
                         self.configs[1] / "teamwork-releases" / first)
        self.assertTrue((self.configs[0] / "teamwork-releases" / first / "RELEASE.json").exists())
        (self.repo / "SKILL.md").write_text("release two\n"); self.commit("second")
        self.env["TW_NOW"] = "20261121-120100"
        self.assertEqual(self.release("publish", "--apply").returncode, 0)
        second = json.loads((self.configs[0] / "teamwork-releases" / "index.json").read_text())
        self.assertEqual(second["previous"], first)
        self.assertEqual(self.release("rollback", "--apply").returncode, 0)
        idx = json.loads((self.configs[0] / "teamwork-releases" / "index.json").read_text())
        self.assertEqual((idx["current"], idx["history"]), (first, [first]))  # one step back, not a swap
        self.assertNotEqual(self.release("rollback", "--apply").returncode, 0)  # nothing further back
        ver = subprocess.run([sys.executable, str(self.configs[0] / "skills" / "teamwork" / "scripts" / "tw.py"), "version"],
                             capture_output=True, text=True)
        self.assertIn("release " + first, ver.stdout)
        self.assertIn("newer release", ver.stdout)
        (self.repo / "SKILL.md").write_text("third\n"); self.commit("third")
        report = json.loads(self.release("status", "--json").stdout)
        self.assertIn("commits behind main", report["configs"][0]["freshness"])
        self.assertEqual(self.release("edge", "--apply").returncode, 0)
        self.assertEqual((self.configs[0] / "skills" / "teamwork").resolve(), self.repo)

    def test_real_directory_is_refused(self):
        link = self.configs[0] / "skills" / "teamwork"
        link.unlink(); link.mkdir(); (link / "keep").write_text("safe")
        result = self.release("publish", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((link / "keep").exists())
        self.assertEqual((self.configs[1] / "skills" / "teamwork").resolve(), self.repo)  # the other one did not move

    def test_all_or_none_and_safe_snapshots(self):
        self.assertEqual(self.release("publish", "--apply").returncode, 0)
        (self.repo / "SKILL.md").write_text("two\n"); self.commit("two")
        self.env["TW_NOW"] = "20261121-120300"
        self.assertEqual(self.release("publish", "--apply").returncode, 0)
        idx = lambda c: json.loads((c / "teamwork-releases" / "index.json").read_text())
        first, second = idx(self.configs[0])["history"]
        gone = self.configs[1] / "teamwork-releases" / first
        gone.rename(gone.with_name("moved-away"))  # one config dir lost the snapshot rollback needs
        r = self.release("rollback", "--apply")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((self.configs[0] / "skills" / "teamwork").resolve().name, second)  # neither moved
        self.assertEqual(idx(self.configs[0])["current"], second)
        self.assertIn("already", self.release("publish", "--apply").stdout)  # same commit: nothing to publish
        (self.repo / "evil").symlink_to("../../..")
        self.commit("link out")
        self.env["TW_NOW"] = "20261121-120400"
        r = self.release("publish", "--apply")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("links outside", r.stderr)

    def test_partial_snapshot_is_never_linked(self):
        sha = self.git("rev-parse", "HEAD").stdout.strip()
        half = self.configs[0] / "teamwork-releases" / f"r20261121-120000-{sha[:7]}"
        half.mkdir(parents=True)  # an interrupted copy: no SKILL.md, no RELEASE.json
        r = self.release("publish", "--apply")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a complete snapshot", r.stderr)
        self.assertTrue(all((c / "skills" / "teamwork").resolve() == self.repo for c in self.configs))

    def test_hook_does_not_wait_for_end_of_input(self):
        proj = self.root / "proj"
        subprocess.run([sys.executable, str(TW), "-C", str(proj), "init", "demo", "--goal", "x"], check=True,
                       capture_output=True)
        pr = subprocess.Popen([sys.executable, str(TW), "context"], cwd=proj, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        pr.stdin.write(b'{"session_id": "session_01Zorblax"}')
        pr.stdin.flush()  # left open, as a host might
        try:
            pr.wait(timeout=5)  # the output is small: it fits the pipe without being read
        finally:
            pr.stdin.close()
        self.assertEqual(pr.returncode, 0)
        self.assertIn("session_01Zorblax", (proj / ".teamwork" / "session-versions.jsonl").read_text())

    def test_rollback_to_edge_and_apply_flag(self):
        self.assertEqual(self.release("publish", "--apply").returncode, 0)
        self.assertEqual(self.release("edge", "--apply").returncode, 0)
        self.env["TW_NOW"] = "20261121-120200"
        self.assertEqual(self.release("publish", "--apply").returncode, 0)
        r = self.release("rollback", "--apply")  # previous is edge: back to the live clone
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(all((c / "skills" / "teamwork").resolve() == self.repo for c in self.configs))


class RouteByTopic(TwCase):
    """T34 (R41): work inside a thread's focus/owns goes to it; work inside none suggests a new thread; a task whose
    title shares no topic word with its owner gets a topic_mismatch warning (never a block)."""

    def setUp(self):
        super().setUp()
        self.tw("thread", "add", "docs", "--focus", "user guide and API docs", "--owns", "docs/,guide")
        self.tw("thread", "add", "ui", "--focus", "settings screen", "--owns", "web/")
        self.tw("thread", "add", "ledger", "--focus", "请求台账 request ledger", "--owns", "ledger")

    def route(self, text):
        return json.loads(self.tw("route", text).stdout)

    def test_t42_plural_pairs_and_misrouted_tasks(self):
        """T42: singular and plural meet (house/houses, policy/policies, bus/buses); a task routed to the wrong
        thread does not pull more of that topic there."""
        tok = lambda x: json.loads(self.tw("route", x).stdout)
        self.tw("thread", "add", "infra", "--focus", "bus routes and house policies")
        for x in ("one bus", "the house", "a policy"):
            self.assertEqual(tok(x)["best"], "infra", x)
        self.tw("thread", "add", "media", "--focus", "movies and cookies")
        self.assertEqual(tok("the movie cookie")["best"], "media")
        for i in range(2, 8):  # many tasks on one shared word do not outweigh a thread whose focus is the request
            self.tw("task", "add", f"T{i}", "--owner", "docs", "--title", f"guide house {i}")
        self.assertEqual(tok("house policies")["best"], "infra")
        self.tw("task", "add", "T1", "--owner", "docs", "--title", "partner outreach list")  # routed by mistake
        self.assertIsNone(tok("partner outreach list again")["best"])

        self.assertEqual(self.route("write the install guide")["best"], "docs")
        self.assertEqual(self.route("台账导出")["best"], "ledger")  # Chinese pairs match inside longer words
        self.assertNotIn("suggest", self.route("write the install guide"))

    def test_inside_none_suggests_a_new_thread(self):
        r = self.route("build a partner list for outreach")
        self.assertIsNone(r["best"])
        self.assertEqual((r["suggest"], r["suggest_name"]), ("new_thread", "partner-list"))
        r = self.route("build the zorblax")  # `ui` is not a word of `build`
        self.assertEqual(r.get("suggest"), "new_thread")

    def test_topic_mismatch_warns_never_blocks(self):
        r = self.tw("task", "add", "T1", "--title", "partner list for outreach", "--owner", "docs")
        self.assertIn("topic_mismatch", r.stderr)
        self.assertIn("one topic per thread", r.stderr.lower())
        self.assertEqual(self.state()["tasks"]["T1"]["owner"], "docs")  # kept as given
        r = self.tw("task", "add", "T2", "--title", "update the user guide intro", "--owner", "docs")
        self.assertNotIn("topic_mismatch", r.stderr)
        r = self.tw("task", "add", "T3", "--title", "partner list for outreach")
        self.assertNotIn("topic_mismatch", r.stderr)  # no owner yet
        r = self.tw("task", "set", "T3", "--owner", "ui")
        self.assertIn("topic_mismatch", r.stderr)
        r = self.tw("task", "set", "T2", "--owner", "ledger")
        self.assertIn("topic_mismatch", r.stderr)

    def test_r1_noise_and_false_matches(self):
        self.assertNotIn("topic_mismatch", self.tw("task", "add", "T1", "--owner", "docs").stderr)  # no title yet
        self.tw("thread", "add", "review", "--focus", "code review")
        self.assertNotIn("topic_mismatch", self.tw("task", "add", "T2", "--title", "review PR 12",
                                                   "--owner", "review").stderr)  # its own name counts
        self.tw("thread", "add", "quixbar", "--focus", "用户的设置页面")
        self.tw("thread", "add", "zorblax", "--focus", "我们的合作伙伴名单")
        for text in ("我的设计稿", "我们要发布新版本"):  # function words are not topics
            self.assertEqual(self.route(text).get("suggest"), "new_thread", text)
        self.assertEqual(self.route("设置页面加暗色")["best"], "quixbar")
        self.assertEqual(self.route("我们需要一个合作伙伴名单")["best"], "zorblax")
        self.assertIn("topic_mismatch", self.tw("task", "add", "T3", "--title", "我的设计", "--owner", "quixbar").stderr)
        self.assertEqual(self.route("guides for onboarding")["best"], "docs")  # plural
        self.tw("thread", "add", "forms", "--focus", "申请流程 请求台账 status analysis process")
        for title in ("申请表单", "请求重试", "statuses page", "analyses export", "processes list"):
            self.assertNotIn("topic_mismatch", self.tw("task", "add", "F" + str(len(title)) + title[:2].encode().hex(),
                                                       "--title", title, "--owner", "forms").stderr, title)
        r = self.route("fix it")  # no topic words: no suggestion either way
        self.assertNotIn("suggest", r)
        self.assertIn("topic_words", r)
        self.tw("thread", "add", "blank")
        self.assertIn("has no focus or owns", self.tw("task", "add", "T4", "--title", "pricing table", "--owner", "blank").stderr)
        r = self.tw("task", "set", "T2", "--title", "partner list for outreach")  # a new title is checked too
        self.assertIn("topic_mismatch", r.stderr)
        r = self.tw("task", "set", "T2", "--owner", "docs", "--rounds", "2", ok=False)  # refused: no warning
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("topic_mismatch", r.stderr)


class LessonsCommands(TwCase):
    """T39: the small commands behind IL-1, IL-16, IL-22, IL-34 and IL-36."""

    def test_memory_sections_and_temporary(self):
        self.tw("memory", "fact one")
        self.tw("memory", "--never-raise", "pricing tiers")
        self.tw("memory", "fact two", "--temporary")
        self.tw("memory", "--user-owned", "brand name")
        self.tw("memory", "--never-raise", "dark mode")
        text = (self.dir / ".teamwork" / "MEMORY.md").read_text(encoding="utf-8")
        nr, uo = text.index("## Never raise"), text.index("## User-owned")
        self.assertLess(text.index("fact one"), nr)
        self.assertLess(text.index("fact two"), nr)  # facts stay above the special sections
        self.assertIn("temporary: confirm", text[text.index("fact two"):nr])
        self.assertTrue(nr < text.index("pricing tiers") < text.index("dark mode") < uo < text.index("brand name"))
        self.assertIn("pricing tiers", self.context())  # pasted into every new thread

    def test_memory_section_added_to_an_old_file(self):
        (self.dir / ".teamwork" / "MEMORY.md").write_text("# old\n\n- a\n", encoding="utf-8")
        self.tw("memory", "--user-owned", "logo")
        self.tw("memory", "b")
        lines = (self.dir / ".teamwork" / "MEMORY.md").read_text(encoding="utf-8").split("\n")
        self.assertEqual(lines[:3], ["# old", "", "- a"])
        self.assertTrue(lines[3].startswith("- b "), lines)
        self.assertTrue(lines[5].startswith("## User-owned"), lines)

    def test_memory_hand_edited_heading_newlines_and_order(self):
        mem = self.dir / ".teamwork" / "MEMORY.md"
        mem.write_text("# m\n\n- a\n\n## never raise\n\n- old topic\n\n## Decisions\n\n- d1\n", encoding="utf-8")
        self.tw("memory", "--never-raise", "n2")
        self.tw("memory", "line1\n## User-owned (x)\ninjected")
        text = mem.read_text(encoding="utf-8")
        self.assertEqual(text.lower().count("## never raise"), 1)  # matched by its first words
        self.assertLess(text.index("old topic"), text.index("n2"))
        self.assertLess(text.index("n2"), text.index("## Decisions"))
        self.assertNotIn("\n## User-owned (x)", text)  # one line: no injected heading
        self.assertIn("- line1 ## User-owned (x) injected", text)
        mem.write_text("# m\n", encoding="utf-8")
        self.tw("memory", "--user-owned", "u1")
        self.tw("memory", "--never-raise", "n1")
        text = mem.read_text(encoding="utf-8")
        self.assertLess(text.index("## Never raise"), text.index("## User-owned"))  # fixed order
        self.assertNotEqual(self.tw("memory", "x", "--never-raise", "--user-owned", ok=False).returncode, 0)

    def test_lead_prints_its_title_and_tags(self):
        out = json.loads(self.tw("lead", "--session", "session_0quixbar").stdout)
        short = self.state()["prefs"]["short_name"]
        self.assertEqual((out["title"], out["tags"]), (f"[{short}] lead · test project", [f"teamwork:{short}", "teamwork-thread:lead"]))

    def test_route_path_names_the_owner(self):
        self.tw("thread", "add", "web", "--owns", "web/**")
        self.tw("thread", "add", "api", "--owns", "api/")
        self.tw("thread", "add", "data")
        self.tw("task", "add", "T1", "--title", "migrations", "--owner", "data", "--touches", "db/migrations/**")
        out = json.loads(self.tw("route", "--path", "web/a.ts", "--path", "api/x/y.py", "--path", "db/migrations/1.sql",
                                 "--path", "README.md").stdout)["owners"]
        self.assertEqual(out["web/a.ts"], ["web"])
        self.assertEqual(out["api/x/y.py"], ["api"])
        self.assertIsNone(out["db/migrations/1.sql"])  # T1 is not active yet: an approved task holds no lease
        self.assertIsNone(out["README.md"])
        self.tw("task", "set", "T1", "--status", "dispatched")
        out = json.loads(self.tw("route", "--path", "db/migrations/1.sql").stdout)["owners"]
        self.assertEqual(out["db/migrations/1.sql"], ["data"])
        self.assertNotEqual(self.tw("route", ok=False).returncode, 0)
        out = json.loads(self.tw("route", "--path", str(self.dir.resolve() / "web" / "a.ts")).stdout)["owners"]
        self.assertEqual(list(out.values()), [["web"]])  # absolute path inside the project
        self.assertNotEqual(self.tw("route", "fix the page", "--path", "web/a.ts", ok=False).returncode, 0)
        self.tw("thread", "set", "web", "--status", "archived")
        self.assertIsNone(json.loads(self.tw("route", "--path", "web/a.ts").stdout)["owners"]["web/a.ts"])

    def test_default_after_minutes_pref(self):
        self.assertEqual(self.state()["prefs"]["default_after_minutes"], 30)
        self.assertNotEqual(self.tw("prefs", "--set", "default_after_minutes=abc", ok=False).returncode, 0)
        self.tw("prefs", "--set", "default_after_minutes=45")
        self.assertEqual(self.state()["prefs"]["default_after_minutes"], 45)


class LessonsFeatures(TwCase):
    """T40: IL-3 merge queue, IL-13/38 the user's view, IL-20 known issues, IL-31 models."""

    def setUp(self):
        super().setUp()
        self.tw("thread", "add", "ui", "--focus", "pages", "--model", "model-q")
        self.tw("plan", "approve", "--user-said", "ok")

    def test_models_per_thread_and_task(self):
        st = self.state()
        self.assertEqual(st["threads"]["ui"]["model"], "model-q")
        self.tw("task", "add", "T1", "--title", "pages one", "--owner", "ui")
        self.tw("task", "add", "T2", "--title", "pages two", "--owner", "ui", "--model", "model-z", "--no-scripts")
        models = json.loads(self.tw("ready").stdout)["models"]
        self.assertEqual(models, {"T1": "model-q", "T2": "model-z (by hand, no scripts)"})
        self.tw("task", "set", "T2", "--model", "")
        self.assertEqual(json.loads(self.tw("ready").stdout)["models"]["T2"], "model-q (by hand, no scripts)")
        sa = json.loads(self.tw("thread", "add", "api", "--model", "model-a").stdout)["create_session"]
        self.assertEqual(sa["model"], "model-a")
        self.tw("thread", "set", "api", "--model", "")
        self.assertNotIn("model", json.loads(self.tw("thread", "add", "web").stdout)["create_session"])

    def test_merge_queue(self):
        self.tw("task", "add", "T1", "--title", "pages", "--owner", "ui", "--accept", "renders")
        for st_ in ("dispatched", "working", "review"):
            self.tw("task", "set", "T1", "--status", st_)
        self.assertEqual(json.loads(self.tw("ready").stdout)["merge_waiting"], [])
        self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "ui/subagent", "--checks", "pass")
        w = json.loads(self.tw("ready").stdout)
        self.assertEqual([x["task"] for x in w["merge_waiting"]], ["T1"])
        self.assertNotIn("merge_overdue", w)
        self.assertIn("Waiting to merge: 1 branch", self.tw("board").stdout)
        self.env["TW_NOW"] = "2099-01-01T00:00:00+00:00"
        self.assertIn("merge_overdue", json.loads(self.tw("ready").stdout))

    def test_issues(self):
        self.tw("issue", "add", "main is red", "--owner", "ui")
        self.assertIn("Known issues", self.tw("board").stdout)
        r = self.tw("issue", "set", "I1", "--status", "fixed-on-branch", ok=False)
        self.assertNotEqual(r.returncode, 0)  # a fix names its branch and commit
        self.tw("issue", "set", "I1", "--status", "fixed-on-branch", "--ref", "tw/demo/ui@abc123")
        self.assertNotEqual(self.tw("issue", "set", "I1", "--status", "verified", ok=False).returncode, 0)  # not merged
        self.tw("issue", "set", "I1", "--status", "open")
        self.assertEqual(self.state()["issues"][0]["ref"], "")  # reopened: no fix yet
        self.tw("issue", "set", "I1", "--status", "fixed-on-branch", "--ref", "tw/demo/ui@abc124")
        self.tw("issue", "set", "I1", "--status", "merged")
        self.tw("issue", "set", "I1", "--status", "verified")
        self.assertNotIn("I1", self.tw("issue", "list").stdout)
        self.assertIn("I1", self.tw("issue", "list", "--all").stdout)
        self.assertNotEqual(self.tw("issue", "add", "x", "--owner", "nobody", ok=False).returncode, 0)
        self.assertNotEqual(self.tw("issue", "set", "I1", "--status", "gone", ok=False).returncode, 0)
        self.tw("issue", "add", "login is slow")  # T42: open -> merged names the commit too
        r = self.tw("issue", "set", "I2", "--status", "merged", ok=False)
        self.assertIn("--ref", r.stderr)
        self.tw("issue", "set", "I2", "--status", "merged", "--ref", "main@def456")

    def test_board_request_text_is_escaped(self):
        self.tw("req", "add", "keep ~~old~~ *and* new_name", "--where", "https://example.invalid/a_b")
        self.tw("req", "set", "R1", "--followup", "ask about __this__")
        b = self.tw("board").stdout
        self.assertIn("keep \\~\\~old\\~\\~ \\*and\\* new\\_name", b)
        self.assertIn("<https://example.invalid/a_b>", b)  # a link stays as typed
        self.assertIn("\\_\\_this\\_\\_", b)

    def test_share_view_in_the_users_language_and_escaped(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("req", "add", "做一个 ~~登录~~ 页", "--phase", "测试版", "--category", "功能")
        self.tw("deliverable", "add", "**定价** 文档", "--link", "https://example.invalid/d2")
        out = self.tw("board", "--share").stdout
        for word in ("进展一览", "## 交付物", "## 你提的事", "## 全部需求", "待办", "草稿", "### 测试版 / 功能"):
            self.assertIn(word, out)
        self.assertIn("\\~\\~登录\\~\\~", out)  # the user's ~~ is text, not a strike-through
        self.assertIn("\\*\\*定价\\*\\*", out)
        self.tw("req", "set", "R1", "--where", "https://x.example/a_b")
        self.assertIn("<https://x.example/a_b>", self.tw("board", "--share").stdout)  # links stay whole
        self.tw("task", "add", "T1", "--title", "pages", "--owner", "ui", "--no-scripts")
        self.tw("task", "set", "T1", "--scripts-ok")
        self.assertNotIn("no_scripts", self.state()["tasks"]["T1"])

    def test_share_view_and_rebuild_keeps_them(self):
        self.tw("req", "add", "a login page", "--phase", "beta", "--category", "feature")
        self.tw("req", "add", "no ads", "--category", "constraint")
        self.tw("req", "drop", "R2", "--user-said", "never mind")
        self.tw("deliverable", "add", "Pricing doc", "--link", "https://example.invalid/d1", "--missing", "charts",
                "--req", "R1")
        self.tw("deliverable", "set", "D1", "--status", "final")
        self.assertNotEqual(self.tw("deliverable", "set", "D1", "--status", "done", ok=False).returncode, 0)
        self.tw("issue", "add", "slow page")
        out = self.tw("board", "--share").stdout
        self.assertTrue(out.index("## Deliverables") < out.index("## Your requests") < out.index("## Every requirement"))
        self.assertIn("**Pricing doc** — final — https://example.invalid/d1 (missing: charts)", out)
        self.assertIn("### beta / feature", out)
        self.assertIn("~~R2: no ads~~ — dropped", out)
        self.assertNotIn("R2 — ", out.split("## Your requests")[1].split("##")[0])  # ended: not an open request
        self.assertTrue((self.dir / ".teamwork" / "SHARE.md").exists())
        (self.dir / ".teamwork" / "state.json").unlink()
        sess = self.dir / "sessions.json"
        sess.write_text("[]")
        self.tw("rebuild", "--sessions", str(sess))
        rb = json.loads((self.dir / ".teamwork" / "state.json.rebuilt").read_text())
        self.assertEqual([x["status"] for x in rb["deliverables"]], ["final"])
        self.assertEqual([x["id"] for x in rb["issues"]], ["I1"])


class ThreadProtocolLessons(TwCase):
    """T41: IL-18 the hook finds the board from a worktree and reminds the thread; IL-19 tasks first; IL-21/23/26."""

    def test_context_from_a_worktree_picks_the_deepest_active_seat_and_survives_a_broken_board(self):
        self.tw("thread", "add", "ui", "--focus", "pages")
        self.tw("thread", "add", "old", "--focus", "pages")
        base = self.dir.parent / f"{self.dir.name}-worktrees"
        wt = base / "ui" / "inner"
        wt.mkdir(parents=True)
        stf = self.dir / ".teamwork" / "state.json"
        st = json.loads(stf.read_text())
        st["threads"]["ui"]["worktree"] = str(base / "ui")
        st["threads"]["old"]["worktree"] = str(wt)
        st["threads"]["old"]["status"] = "archived"
        stf.write_text(json.dumps(st))
        r = subprocess.run([sys.executable, str(TW), "context"], cwd=wt, env=self.env, capture_output=True, text=True)
        self.assertIn("You are thread ui", r.stdout)  # the archived seat's deeper path does not win
        st["threads"]["ui"]["worktree"] = 5
        stf.write_text(json.dumps(st))
        r = subprocess.run([sys.executable, str(TW), "context"], cwd=wt, env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)  # a non-string path is skipped
        stf.write_text("{not json")
        r = subprocess.run([sys.executable, str(TW), "context"], cwd=wt, env=self.env, capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_context_from_a_worktree(self):
        self.tw("thread", "add", "ui", "--focus", "pages")
        self.tw("issue", "add", "main is red", "--owner", "ui")
        wt = self.dir.parent / f"{self.dir.name}-worktrees" / "ui" / "web"
        wt.mkdir(parents=True)
        r = subprocess.run([sys.executable, str(TW), "context"], cwd=wt, env=self.env, capture_output=True, text=True)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("You are thread ui", ctx)
        self.assertIn("] <TYPE> ui -> <recipient>", ctx)
        self.assertIn("main is red", ctx)  # the known issues reach the thread
        self.assertIn("the lead's view", ctx)
        self.assertNotIn("req set", ctx.split("the lead's view")[0])  # no lead-only instructions above the board
        nested = self.dir.parent / f"{self.dir.name}-worktrees" / "ui" / "sub-worktrees" / "x"
        nested.mkdir(parents=True)
        r = subprocess.run([sys.executable, str(TW), "context"], cwd=nested, env=self.env, capture_output=True, text=True)
        self.assertIn("You are thread ui", r.stdout)  # keeps walking past an unrelated *-worktrees folder
        self.tw("thread", "set", "ui", "--status", "archived")
        r = subprocess.run([sys.executable, str(TW), "context"], cwd=wt, env=self.env, capture_output=True, text=True)
        self.assertNotIn("You are thread", r.stdout)
        other = self.dir.parent / f"{self.dir.name}-worktrees" / "nobody"
        other.mkdir()
        r = subprocess.run([sys.executable, str(TW), "context"], cwd=other, env=self.env, capture_output=True, text=True)
        self.assertNotIn("You are thread", r.stdout)  # not a seat: board only, no reminder

    def test_protocol_reads_tasks_first_and_has_the_rules(self):
        self.tw("thread", "add", "ui", "--focus", "pages")
        text = self.tw("worker-prompt", "ui").stdout
        heads = [ln for ln in text.splitlines() if ln.startswith("## ")]
        self.assertEqual(heads[0], "## Your tasks")
        self.assertLess(heads.index("## Working rules"), heads.index("## Project instructions"))
        self.assertLess(heads.index("## STATUS block (end EVERY turn with it)"), heads.index("## Project memory"))
        for want in ("FIXED <branch>@<commit>", "processes closed", "waiting_on: limit", "re-read Language"):
            self.assertIn(want, text)
        self.assertNotIn("{{", text)


class Language(TwCase):
    def setUp(self):
        super().setUp()
        self.tw("thread", "add", "ui", "--kind", "rc", "--owns", "web/")

    def test_init_has_empty_language_pref(self):
        self.assertEqual(self.state()["prefs"]["language"], "")

    def test_worker_prompt_unset_language(self):
        out = self.tw("worker-prompt", "ui").stdout
        self.assertIn("## Language", out)
        self.assertIn("not set: use the language of the user's latest message", out)
        self.assertIn("headers keep their fixed English keywords", out)

    def test_worker_prompt_and_hook_carry_current_language(self):
        self.tw("prefs", "--set", "language=zh")
        out = self.tw("worker-prompt", "ui").stdout
        self.assertIn("Current conversation language: Chinese (中文) (prefs.language=zh)", out)
        self.assertIn("broadcasts a FOLLOWUP to every thread", out)
        self.assertIn("[tw:demo] TASK lead ->", out)
        ctx = self.context()
        self.assertIn("prefs.language=zh", ctx)
        # The rule comes before the board, so truncation never drops it.
        self.assertLess(ctx.index("Language (most important rule)"), ctx.index("Prefs:"))

    def test_unknown_code_passes_through(self):
        self.tw("prefs", "--set", "language=pt-BR")
        self.assertIn("pt-BR (prefs.language=pt-BR)", self.tw("worker-prompt", "ui").stdout)

    def test_old_project_without_language_key(self):
        st = self.state()
        del st["prefs"]["language"]
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.assertIn("not set", self.tw("worker-prompt", "ui").stdout)
        self.assertIn("not set", self.context())


class ShortName(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_tw(self, *args, ok=True):
        r = subprocess.run([sys.executable, str(TW), "-C", str(self.dir), *args], cwd=self.dir,
                           capture_output=True, text=True)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def state(self):
        return json.loads((self.dir / ".teamwork" / "state.json").read_text(encoding="utf-8"))

    def test_default_initials_and_titles(self):
        self.run_tw("init", "teamwork-for-claude-code")
        self.assertEqual(self.state()["prefs"]["short_name"], "TFCC")
        out = json.loads(self.run_tw("thread", "add", "core", "--focus", "skill").stdout)
        self.assertEqual(out["title"], "[TFCC] core · skill")
        self.assertIn("teamwork:TFCC", out["tags"])
        self.assertEqual(out["branch"], "tw/teamwork-for-claude-code/core")  # branches keep the full name
        self.assertNotIn("warning", out)
        wp = self.run_tw("worker-prompt", "core").stdout
        self.assertIn("[tw:TFCC] <TYPE> core ->", wp)
        self.assertIn("[tw:TFCC] TASK lead ->", wp)

    def test_short_project_name_kept(self):
        self.run_tw("init", "shop")
        self.assertEqual(self.state()["prefs"]["short_name"], "shop")

    def test_explicit_and_invalid_short(self):
        self.run_tw("init", "big-data-pipeline", "--short", "BDP2")
        self.assertEqual(self.state()["prefs"]["short_name"], "BDP2")
        r = self.run_tw("init", "x-y", "--short", "TOOLONG7", "--force", ok=False)
        self.assertNotEqual(r.returncode, 0)

    def test_old_project_without_short_name_unchanged(self):
        self.run_tw("init", "legacy-project")
        st = self.state()
        del st["prefs"]["short_name"]
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        out = json.loads(self.run_tw("thread", "add", "ui").stdout)
        self.assertEqual(out["title"], "[legacy-project] ui")
        self.assertIn("teamwork:legacy-project", out["tags"])
        self.assertIn("[tw:legacy-project] <TYPE> ui ->", self.run_tw("worker-prompt", "ui").stdout)

    def test_setting_short_name_retitles_threads(self):
        self.run_tw("init", "legacy-project")
        self.run_tw("thread", "add", "ui", "--focus", "web")
        r = self.run_tw("prefs", "--set", "short_name=LGP")
        self.assertIn('"ui": "[LGP] ui · web"', r.stderr)
        self.assertEqual(self.state()["threads"]["ui"]["title"], "[LGP] ui · web")
        self.assertNotEqual(self.run_tw("prefs", "--set", "short_name=WAYTOOLONG", ok=False).returncode, 0)

    def test_long_title_warns(self):
        self.run_tw("init", "shop")
        out = json.loads(self.run_tw("thread", "add", "ui", "--focus", "checkout flow and payment screens").stdout)
        self.assertIn("warning", out)


class ThreadStart(unittest.TestCase):
    """T9: worktree (I4), permission mode + tools (I1), header id rule (I6)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name) / "proj"
        self.proj.mkdir()
        g = lambda *x: subprocess.run(["git", "-C", str(self.proj), *x], check=True, capture_output=True)
        g("init", "-q", "-b", "main")
        (self.proj / "a.txt").write_text("a\n")
        g("add", ".")
        g("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "init")
        self.tw("init", "proj")

    def tearDown(self):
        self._tmp.cleanup()

    def tw(self, *args, ok=True):
        r = subprocess.run([sys.executable, str(TW), "-C", str(self.proj), *args], cwd=self.proj,
                           capture_output=True, text=True)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def test_thread_add_prints_session_args_and_worktree(self):
        out = json.loads(self.tw("thread", "add", "ui").stdout)
        self.assertIn("mcp__claude-code-remote__send_message", out["create_session"]["extra_allowed_tools"])
        self.assertEqual(out["create_session"]["permission_mode"], "auto")  # T36: auto unless the user said otherwise
        self.assertTrue(out["worktree"].endswith("proj-worktrees/ui"))
        self.tw("prefs", "--set", "thread_permission_mode=auto")
        out = json.loads(self.tw("thread", "add", "api").stdout)
        self.assertEqual(out["create_session"]["permission_mode"], "auto")
        self.assertNotEqual(self.tw("prefs", "--set", "thread_permission_mode=yolo", ok=False).returncode, 0)

    def test_cloud_thread_has_no_worktree(self):
        out = json.loads(self.tw("thread", "add", "docs", "--kind", "cloud",
                                  "--cloud-reason", "Quixbar research only, no files, no rc messages").stdout)
        self.assertNotIn("worktree", out)
        self.assertNotEqual(self.tw("worktree", "docs", ok=False).returncode, 0)
        self.assertIn("container's checkout", self.tw("worker-prompt", "docs").stdout)

    def test_worktree_created_once(self):
        self.tw("thread", "add", "ui")
        dry = json.loads(self.tw("worktree", "ui", "--dry-run").stdout)
        self.assertFalse(dry["created"])
        wt = Path(dry["worktree"])
        self.assertFalse(wt.exists())
        r = json.loads(self.tw("worktree", "ui").stdout)
        self.assertTrue(r["created"])
        self.assertTrue((wt / "a.txt").exists())
        branch = subprocess.run(["git", "-C", str(wt), "branch", "--show-current"], capture_output=True, text=True)
        self.assertEqual(branch.stdout.strip(), "tw/proj/ui")
        again = json.loads(self.tw("worktree", "ui").stdout)
        self.assertFalse(again["created"])
        wp = self.tw("worker-prompt", "ui").stdout
        self.assertIn(str(wt), wp)
        self.assertIn("worktree add -b tw/proj/ui", wp)

    def test_header_id_rule_in_prompt(self):
        self.tw("thread", "add", "ui")
        self.assertIn("exactly one task id from the board", self.tw("worker-prompt", "ui").stdout)


class Stuck(TwCase):
    """T3: working threads silent longer than prefs.stuck_minutes show up as possibly stuck."""

    T0 = "2026-11-15T10:00:00+00:00"

    def at(self, minutes):
        self.env["TW_NOW"] = (dt.datetime.fromisoformat(self.T0) + dt.timedelta(minutes=minutes)).isoformat()

    def setUp(self):
        super().setUp()
        self.at(0)
        self.tw("thread", "add", "ui", "--session", "s1", "--status", "working")
        self.tw("thread", "add", "api", "--session", "s2", "--status", "waiting")

    def ready(self):
        return json.loads(self.tw("ready").stdout)["stuck"]

    def test_default_threshold_and_board_group(self):
        self.assertEqual(self.state()["prefs"]["stuck_minutes"], 30)
        self.at(29)
        self.assertEqual(self.ready(), {})
        self.at(45)
        self.assertEqual(self.ready(), {"ui": 45})  # api is waiting, not working: never "stuck"
        board = self.tw("board").stdout
        self.assertIn("Possibly stuck", board)
        self.assertIn("no report for 45 min", board)
        self.assertEqual(board.count("[demo] ui"), 1)  # listed once, not also under Working
        self.assertEqual(json.loads(self.tw("open").stdout)[0]["stuck_minutes"], 45)

    def test_report_resets_timer_plain_set_does_not(self):
        self.at(40)
        self.tw("thread", "set", "ui", "--summary", "lead note")  # not a report from the thread
        self.assertIn("ui", self.ready())
        self.tw("thread", "set", "ui", "--from-status", "--summary", "step 2 of 3")
        self.assertEqual(self.ready(), {})
        self.at(40 + 31)
        self.assertEqual(self.ready(), {"ui": 31})

    def test_custom_threshold_and_off(self):
        self.tw("prefs", "--set", "stuck_minutes=10")
        self.at(11)
        self.assertIn("ui", self.ready())
        self.tw("prefs", "--set", "stuck_minutes=0")
        self.at(500)
        self.assertEqual(self.ready(), {})

    def test_old_project_without_pref_uses_default(self):
        st = self.state()
        del st["prefs"]["stuck_minutes"]
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.at(31)
        self.assertIn("ui", self.ready())


class Requests(TwCase):
    """T14: every user request is tracked until reported and closed."""

    def setUp(self):
        super().setUp()
        self.tw("thread", "add", "ui")
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "ui")

    def req(self, r):
        return self.state()["requests"][r]

    def test_lifecycle_and_rules(self):
        out = json.loads(self.tw("req", "add", "make the button blue").stdout)
        self.assertEqual(out["R1"]["status"], "open")
        self.assertNotEqual(self.tw("req", "set", "R1", "--status", "delivered", ok=False).returncode, 0)
        self.assertNotEqual(self.tw("req", "set", "R1", "--status", "closed", ok=False).returncode, 0)
        self.tw("req", "link", "R1", "T1", "T2")
        self.assertEqual(self.req("R1")["status"], "dispatched")
        self.assertNotEqual(self.tw("req", "link", "R1", "T9", ok=False).returncode, 0)
        self.tw("task", "set", "T1", "--accept", "x", "--status", "done", "--review", "pass", "--reviewer", "rev")
        self.assertEqual(self.req("R1")["status"], "dispatched")  # T2 still open
        self.tw("task", "set", "T2", "--status", "working")
        r = self.tw("task", "set", "T2", "--status", "review")  # review is enough: the result exists
        self.assertIn("request R1 delivered", r.stderr)
        self.assertEqual(self.req("R1")["status"], "delivered")
        self.assertNotEqual(self.tw("req", "close", "R1", "--user-said", "ok", ok=False).returncode, 0)  # not reported
        self.tw("req", "set", "R1", "--status", "reported", "--followup", "ask if blue is right")
        self.assertNotEqual(self.tw("req", "close", "R1", ok=False).returncode, 0)  # needs the user's words
        self.assertNotEqual(self.tw("req", "close", "R1", "--confirmed", ok=False).returncode, 0)
        self.assertIn("reported_at", self.req("R1"))
        # T2 is in review with no verdict: the user would accept unchecked work
        self.assertNotEqual(self.tw("req", "close", "R1", "--user-said", "looks good", ok=False).returncode, 0)
        self.tw("task", "set", "T2", "--accept", "x", "--review", "pass", "--reviewer", "rev")
        self.tw("req", "close", "R1", "--user-said", "looks good")
        self.assertEqual(self.req("R1")["closed_reason"], "accepted")
        self.assertEqual(self.req("R1")["user_said"][-1]["text"], "looks good")
        self.assertNotEqual(self.tw("req", "drop", "R1", "--user-said", "x", ok=False).returncode, 0)  # ended

    def test_dropped_tasks_do_not_hold_a_request_back(self):
        """T37: a request waits only on linked tasks that still count; all of them dropped is not a delivery."""
        self.tw("task", "add", "T3", "--owner", "ui")
        self.tw("req", "add", "make the button blue", "--tasks", "T1,T2")
        self.tw("task", "set", "T1", "--status", "working")
        self.tw("task", "set", "T1", "--status", "review")
        self.assertEqual(self.req("R1")["status"], "dispatched")  # T2 still open
        r = self.tw("task", "set", "T2", "--status", "dropped", "--user-said", "not needed")
        self.assertIn("request R1 delivered", r.stderr)  # dropping the last open task delivers the rest
        self.assertEqual(self.req("R1")["status"], "delivered")
        self.tw("req", "set", "R1", "--status", "reported")
        self.tw("task", "set", "T1", "--accept", "x", "--review", "pass", "--reviewer", "rev")
        self.tw("req", "close", "R1", "--user-said", "good")  # the dropped T2 does not block the close
        self.assertEqual(self.req("R1")["status"], "closed")
        # every linked task dropped: not delivered, by itself or by hand
        self.tw("req", "add", "dark mode", "--tasks", "T3")
        self.tw("task", "set", "T3", "--status", "dropped", "--user-said", "skip it")
        self.assertEqual(self.req("R2")["status"], "dispatched")
        r = self.tw("req", "set", "R2", "--status", "delivered", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("all linked tasks are dropped", r.stderr)
        self.assertIn("dropped", self.tw("req", "link", "R2", "T3", ok=False).stderr)  # no linking dead work
        self.assertNotEqual(self.tw("req", "add", "x", "--tasks", "T2", ok=False).returncode, 0)

    def test_a_drop_that_delivers_an_unlinked_request_survives_rebuild(self):
        """A move made by the scan over all requests is in the log, so rebuild keeps it."""
        self.tw("req", "add", "blue", "--tasks", "T1,T2")
        self.tw("task", "set", "T1", "--status", "working")
        self.tw("task", "set", "T1", "--status", "review")
        stf = self.dir / ".teamwork" / "state.json"
        st = json.loads(stf.read_text())
        st["tasks"]["T2"]["status"] = "dropped"  # an older ledger: dropped before the rule, request left dispatched
        stf.write_text(json.dumps(st))
        self.tw("task", "add", "T3", "--owner", "ui")
        self.tw("task", "set", "T3", "--status", "working")
        self.tw("task", "set", "T3", "--status", "review")  # unrelated move; the scan delivers R1
        self.assertEqual(self.req("R1")["status"], "delivered")
        ev = [json.loads(x) for x in (self.dir / ".teamwork" / "log.jsonl").read_text().splitlines()][-1]
        self.assertEqual(ev["snap_reqs"]["R1"]["status"], "delivered")
        self.assertNotIn("_moved_reqs", self.state())

    def test_declined_closes_from_any_state(self):
        self.tw("req", "add", "maybe a dark mode")
        self.assertNotEqual(self.tw("req", "drop", "R1", ok=False).returncode, 0)
        self.tw("req", "drop", "R1", "--user-said", "not now")
        self.assertEqual(self.req("R1")["status"], "dropped")
        self.assertEqual(json.loads(self.tw("req", "list").stdout), {})
        self.assertIn("R1", json.loads(self.tw("req", "list", "--all").stdout))

    def test_board_hook_and_ready_show_unreported(self):
        self.tw("req", "add", "fix login", "--tasks", "T1")
        self.tw("req", "add", "done thing")
        self.tw("req", "add", "dropped")
        self.tw("req", "close", "R3", "--declined", "--user-said", "skip it")
        self.tw("task", "set", "T1", "--accept", "x", "--status", "done", "--review", "pass", "--reviewer", "rev")
        board = self.tw("board").stdout
        self.assertLess(board.index("## Your requests (not closed)"), board.index("## Overview"))
        self.assertIn("**R1** [delivered (report it!)] fix login", board)
        self.assertNotIn("dropped", board)
        ctx = self.context()
        self.assertIn("Delivered, not yet reported to the user: R1: fix login", ctx)
        self.assertEqual(json.loads(self.tw("ready").stdout)["unreported_requests"], {"R1": "fix login"})
        self.tw("req", "set", "R1", "--status", "reported")
        self.assertNotIn("not yet reported", self.context())

    def test_chinese_labels(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("req", "add", "按钮改成蓝色")
        self.tw("thread", "set", "ui", "--session", "s1", "--status", "working")
        board = self.tw("board").stdout
        for zh in ("## 你提的事（未关闭）", "[待办]", "## 总览", "**进行中**", "## 计划"):
            self.assertIn(zh, board)

    def test_import_markdown_ledger(self):
        md = self.dir / "REQUESTS.md"
        md.write_text(
            "| # | 用户说的 | 落到哪里 | 状态 | 回访 |\n|---|---|---|---|---|\n"
            "| R1 | 克隆仓库 | — | 已关闭 | — |\n"
            "| R2 | 写成文件 | T1（framework） | 已派 | 英文版先审 |\n"
            "| R3 | 看一下示例应用 | 摘要发了 | 已交付 | 要回报 |\n"
            "| R4 | research 都改进去 | T1–T2 | 已派 | 逐个回报 |\n", encoding="utf-8")
        dry = json.loads(self.tw("req", "import", str(md), "--dry-run").stdout)
        self.assertEqual(set(dry["would_import"]), {"R1", "R2", "R3", "R4"})
        self.assertNotIn("requests", {k for k, v in self.state().items() if v})  # dry run wrote nothing
        self.tw("req", "import", str(md))
        st = self.state()["requests"]
        self.assertEqual(st["R1"]["status"], "closed")
        self.assertEqual(st["R2"]["tasks"], ["T1"])
        self.assertEqual(st["R2"]["followup"], "英文版先审")
        self.assertEqual(st["R3"]["status"], "delivered")
        self.assertEqual(st["R4"]["tasks"], ["T1", "T2"])
        again = json.loads(self.tw("req", "import", str(md)).stdout)
        self.assertEqual(again["imported"], [])  # idempotent: existing ids are skipped
        self.assertEqual(json.loads(self.tw("req", "add", "next one").stdout).keys(), {"R5"})


class Review(TwCase):
    """T10: independent review gate before done."""

    def setUp(self):
        super().setUp()
        self.tw("thread", "add", "ui")
        self.tw("task", "add", "T1", "--owner", "ui", "--accept", "button is blue")

    def task(self):
        return self.state()["tasks"]["T1"]

    def test_done_needs_independent_pass(self):
        self.assertTrue(self.state()["prefs"]["require_review"])
        self.assertNotEqual(self.tw("task", "set", "T1", "--status", "done", ok=False).returncode, 0)
        r = self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "ui", ok=False)
        self.assertIn("must be someone without its context", r.stderr)
        self.assertNotEqual(self.tw("task", "set", "T1", "--review", "pass", ok=False).returncode, 0)
        self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "lead-subagent")
        self.tw("task", "set", "T1", "--status", "done")
        self.assertEqual(self.task()["status"], "done")
        self.assertIn("pass (lead-subagent)", self.tw("board").stdout)

    def test_fail_sends_back_and_checks_gate(self):
        self.tw("task", "set", "T1", "--status", "review")
        r = self.tw("task", "set", "T1", "--review", "fail", "--reviewer", "rev", "--review-note", "still red")
        self.assertIn("back to working", r.stderr)
        self.assertEqual(self.task()["status"], "working")
        self.assertNotEqual(self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "rev",
                                    ok=False).returncode, 0)  # a verdict only on work that is in review
        self.tw("task", "set", "T1", "--status", "review")
        self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "rev", "--checks", "fail")
        self.assertNotEqual(self.tw("task", "set", "T1", "--status", "done", ok=False).returncode, 0)
        self.tw("task", "set", "T1", "--status", "done", "--checks", "pass")
        self.assertEqual(self.task()["checks"], "pass")

    def test_skip_review_is_recorded(self):
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "docs typo", "--user-said", "ok")
        self.assertEqual(self.task()["review"]["result"], "skipped")
        self.assertEqual(self.task()["review"]["note"], "docs typo")

    def test_bool_pref_on_missing_key(self):
        st = self.state()
        del st["prefs"]["require_review"]
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.assertNotEqual(self.tw("prefs", "--set", "require_review=false", ok=False).returncode, 0)
        self.tw("prefs", "--set", "require_review=false", "--user-said", "no reviews for this demo")
        self.assertIs(self.state()["prefs"]["require_review"], False)
        self.tw("prefs", "--set", "require_review=true")
        self.assertIs(self.state()["prefs"]["require_review"], True)

    def test_project_without_pref_is_gated(self):
        st = self.state()
        del st["prefs"]["require_review"]  # an old state or a rebuild without the key: gated (fail closed)
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.assertNotEqual(self.tw("task", "set", "T1", "--status", "done", ok=False).returncode, 0)


class Guard(unittest.TestCase):
    """T16: private words/paths never leave the machine in commits."""

    SECRET = "Zorblax"  # stands in for a private product name; deliberately made up

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.remote = root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        self.proj = root / "proj"
        self.proj.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.proj / ".gitignore").write_text(".teamwork/\n")
        (self.proj / "a.txt").write_text("hello\n")
        self.commit("init")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-q", "origin", "main")
        self.tw("init", "proj")
        (self.proj / ".teamwork" / "PRIVATE.md").write_text(f"# private\n- {self.SECRET.lower()}\npath: secrets/*\n")

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *x):
        return subprocess.run(["git", "-C", str(self.proj), *x], check=True, capture_output=True, text=True)

    def commit(self, msg):
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", msg)

    def tw(self, *args):
        return subprocess.run([sys.executable, str(TW), "-C", str(self.proj), *args], cwd=self.proj,
                              capture_output=True, text=True)

    def test_init_ignores_private_files(self):
        ig = (self.proj / ".teamwork" / ".gitignore").read_text()
        self.assertIn("PRIVATE.md", ig)
        self.assertIn("private/", ig)
        self.assertTrue((self.proj / ".teamwork" / "private").is_dir())

    def test_clean_passes(self):
        (self.proj / "b.txt").write_text("nothing to see\n")
        self.commit("b")
        r = self.tw("guard")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("guard: ok", r.stdout)

    def test_hit_in_worktree_and_history_masked(self):
        (self.proj / "b.txt").write_text(f"look at {self.SECRET}\n")
        r = self.tw("guard")
        self.assertEqual(r.returncode, 1)
        self.assertIn("worktree b.txt", r.stdout)
        self.assertNotIn(self.SECRET.lower(), r.stdout.lower())  # the word itself is never printed
        self.commit("add b")
        self.assertIn("history", self.tw("guard").stdout)

    def test_hit_only_in_history(self):
        (self.proj / "b.txt").write_text(f"{self.SECRET} notes\n")
        self.commit("oops")
        (self.proj / "b.txt").write_text("cleaned\n")
        self.commit("clean up")
        r = self.tw("guard")
        self.assertEqual(r.returncode, 1)
        self.assertIn("history", r.stdout)
        self.assertNotIn("worktree b.txt", r.stdout)

    def test_private_path(self):
        (self.proj / "secrets").mkdir()
        (self.proj / "secrets" / "k.txt").write_text("x\n")
        r = self.tw("guard")
        self.assertEqual(r.returncode, 1)
        self.assertIn("*****/k.txt", r.stdout)  # the rule's own folder name is private too
        self.assertNotIn("secrets", r.stdout)

    def guard(self, *a):
        return self.tw("guard", *a)

    def tw_mod(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        return tw

    def test_t25_escaped_and_packed_words(self):
        import bz2, io, lzma, zipfile, zlib
        w = "蓝鲸星"
        docx = io.BytesIO()
        with zipfile.ZipFile(docx, "w") as z:
            z.writestr("word/document.xml", "<w:r><w:t>Zor</w:t></w:r><w:r><w:t>blax</w:t></w:r>")
        stream = zlib.compress(b"BT /F1 12 Tf [(Zor) -20 (blax)] TJ ET")
        files = {
            "fixture.json": json.dumps({"name": w}).encode(),  # ensure_ascii: \u84dd...
            "notes.rtf": b"{\\rtf1 \\uc1\\u34013 ?\\u40120 ?\\u26143 ?}",
            "page.html": "&#34013;&#x9cb8;&#26143;".encode(),
            "link.txt": b"https://x.invalid/?q=%5A%6F%72%62%6C%61%78",
            "report.docx": docx.getvalue(),
            "report.pdf": b"%PDF-1.4\n1 0 obj<</Filter/FlateDecode>>stream\n" + stream + b"\nendstream endobj",
            "dump.bz2": bz2.compress(b"abc" * 50 + b" Zorblax" * 20),
            "dump.xz": lzma.compress(b"abc" * 50 + b" Zorblax" * 20),
        }
        (self.proj / ".teamwork" / "PRIVATE.md").write_text(f"- Zorblax\n- {w}\n")
        for name, data in files.items():
            (self.proj / name).write_bytes(data)
            self.commit("add " + name)
            r = self.guard("--base", "origin/main")
            self.assertEqual(r.returncode, 1, name + ": " + r.stdout + r.stderr)
            self.git("reset", "-q", "--hard", "origin/main")
            self.git("clean", "-qfd")
        (self.proj / "plain.json").write_text(json.dumps({"a": "caf\u00e9 \u00e5"}) + " &amp; %41 <b>ok</b>\n")
        self.commit("clean escapes")
        self.assertEqual(self.guard("--base", "origin/main").returncode, 0)

    def test_t25_empty_pre_push_input_fails_closed(self):
        r = self.pre_push("")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("input=$(cat)", r.stdout + r.stderr)
        # the hook install.py writes owns stdin: empty means git had nothing to push ("Everything up-to-date")
        for stdin in ("", "\n"):
            r = subprocess.run([sys.executable, str(TW), "-C", str(self.proj), "guard", "--pre-push", "--remote",
                                "origin", "--stdin-owned"], cwd=self.proj, input=stdin, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("nothing to push", r.stdout)

    def test_t25_paths_and_quoted_entries_stay_whole(self):
        tw = self.tw_mod()
        pm = self.proj / ".teamwork" / "PRIVATE.md"
        pm.write_text('- /Users/zorbuser\n- ~/quixbar-notes\n- C:\\Users\\zorb\n- "Quixbar, Ltd"\n- `a/b`\n- Zorblax/Quixbar\n')
        terms, _, _ = tw.load_private(self.proj / ".teamwork")
        for t in ("/Users/zorbuser", "~/quixbar-notes", "C:\\Users\\zorb", "Quixbar, Ltd", "a/b"):
            self.assertIn(t, terms)
        for t in ("Users", "zorbuser", "quixbar-notes", "Ltd", "a", "b"):
            self.assertNotIn(t, terms)
        self.assertIn("Zorblax", terms)  # a plain 'X/Y' line still adds both
        pm.write_text("1. /srv/zorblax, /opt/quixbar\n- /\n- ~\n- /a/zorb,/b/quix\n")  # a list of paths: each one whole
        terms, _, _ = tw.load_private(self.proj / ".teamwork")
        self.assertEqual(terms, ["/srv/zorblax", "/opt/quixbar", "/a/zorb", "/b/quix"])  # a bare / or ~ never counts
        # a note after a path is dropped, as for any entry; a quoted entry keeps its own '#'; one character never counts
        pm.write_text('- /Users/zorbuser - my home\n- /srv/quixbar (old box)\n- ~/lanjing: notes\n- "Quixbar #1"\n'
                      '- "x"\n')
        terms, _, _ = tw.load_private(self.proj / ".teamwork")
        self.assertEqual(terms, ["/Users/zorbuser", "/srv/quixbar", "~/lanjing", "Quixbar #1"])

    def test_t25_home_path_is_built_in(self):
        home = Path(self._tmp.name) / "home" / "quixuser"
        home.mkdir(parents=True)
        env = {**os.environ, "HOME": str(home)}
        (self.proj / "cfg.json").write_text(json.dumps({"hook": f"python3 {home}/x/tw.py context"}))
        self.commit("absolute path")
        r = subprocess.run([sys.executable, str(TW), "-C", str(self.proj), "guard", "--base", "origin/main"],
                           cwd=self.proj, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("quixuser", r.stdout)

    def test_t25_hashed_list_and_cloud_kit(self):
        (self.proj / ".teamwork" / "PRIVATE.md").write_text("- Zorblax\n- 蓝鲸星\n")
        j1 = json.loads(self.guard("--hash-list").stdout)
        j2 = json.loads(self.guard("--hash-list").stdout)
        self.assertNotEqual(j1["salt"], j2["salt"])  # a new salt every time
        blob = json.dumps(j1)
        for w in ("zorblax", "蓝鲸星", "\\u84dd"):
            self.assertNotIn(w, blob.lower())
        self.assertGreater(len(j1["hashes"]), 2)  # decoys pad the list
        self.assertNotIn("len", "".join(j1))  # no lengths stored
        kit = self.guard("--hash-list", "--kit").stdout
        self.assertNotIn("Zorblax", kit)
        root = Path(self._tmp.name)
        (root / "tw-guard.py").write_text(kit)
        (root / "h.json").write_text(json.dumps(j1))
        cloud = root / "cloud"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(cloud)], check=True)
        g = lambda *x: subprocess.run(["git", "-C", str(cloud), "-c", "user.name=t", "-c", "user.email=t@e.invalid",
                                       *x], capture_output=True, text=True)
        g("checkout", "-qb", "work")
        (cloud / "n.md").write_text("the 蓝鲸星 plan\n")
        g("add", "-A")
        g("commit", "-qm", "n")
        kitrun = lambda: subprocess.run([sys.executable, "-I", str(root / "tw-guard.py"), "origin/main"], cwd=cloud,
                                        capture_output=True, text=True)
        hashed = lambda: subprocess.run([sys.executable, str(TW), "guard", "--hashed", str(root / "h.json"),
                                         "--repo", str(cloud), "--base", "origin/main"], cwd=cloud,
                                        capture_output=True, text=True)
        self.assertEqual(kitrun().returncode, 1)
        r = hashed()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)  # works without any .teamwork folder
        self.assertNotIn("蓝鲸星", r.stdout)
        g("reset", "-q", "--hard", "origin/main")
        (cloud / "ok.md").write_text("Quixbar is fine\n")
        g("add", "-A")
        g("commit", "-qm", "ok")
        self.assertEqual(kitrun().returncode, 0, kitrun().stdout)
        self.assertEqual(hashed().returncode, 0)
        # run from a subfolder: an untracked file at the top is still read
        (cloud / "sub").mkdir()
        (cloud / "top.md").write_text("蓝鲸星\n")
        r = subprocess.run([sys.executable, "-I", str(root / "tw-guard.py"), "origin/main"], cwd=cloud / "sub",
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        (cloud / "top.md").unlink()
        # any git error fails closed: a base that does not exist, or no repo at all
        for args, cwd in ((["origin/nope"], cloud), (["origin/main"], root)):
            r = subprocess.run([sys.executable, "-I", str(root / "tw-guard.py"), *args], cwd=cwd,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 1, (args, r.stdout, r.stderr))
            self.assertNotIn("guard: ok", r.stdout)
        # decoys are random values, never hashes of a string: hex-heavy files (lock files, SHAs) do not trip them
        tw = self.tw_mod()
        import secrets
        noise = "\n".join(secrets.token_hex(20) for _ in range(3000)).encode()
        for _ in range(10):
            j = json.loads(self.guard("--hash-list").stdout)
            self.assertFalse(tw.HashedList(j["salt"], j["hashes"], j["pre"])(noise.lower()))
            self.assertTrue(all(len(p) == 3 for p in j["pre"]))  # 4096 buckets: the prefix is not named
        # no home path in the list: it would give the cloud thread a guessable user name
        home = root / "home" / "quixuser"
        home.mkdir(parents=True)
        r = subprocess.run([sys.executable, str(TW), "-C", str(self.proj), "guard", "--hash-list"], cwd=self.proj,
                           env={**os.environ, "HOME": str(home)}, capture_output=True, text=True)
        j = json.loads(r.stdout)
        self.assertFalse(tw.HashedList(j["salt"], j["hashes"], j["pre"])(str(home).lower().encode()))

    def test_t25_install_keeps_absolute_paths_out_of_commits(self):
        inst = Path(__file__).resolve().parent / "install.py"
        cfg = Path(self._tmp.name) / "cfg"
        run = lambda: subprocess.run([sys.executable, str(inst), "--apply", "--link", "--no-allow", "--config-dir",
                                      str(cfg), "--project", str(self.proj), "--pre-push"],
                                     capture_output=True, text=True)
        r = run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.proj / ".claude" / "settings.json").exists())
        self.assertTrue((self.proj / ".claude" / "settings.local.json").exists())
        self.assertIn(".claude/settings.local.json", (self.proj / ".git" / "info" / "exclude").read_text())
        self.assertNotIn("settings", self.git("status", "--porcelain").stdout)
        # a tracked hooks folder would carry the hook (absolute paths) into a commit: refused
        (self.proj / ".git" / "hooks" / "pre-push").unlink()
        self.git("config", "core.hooksPath", ".githooks")
        r = run()
        self.assertIn("refused", r.stdout)
        self.assertFalse((self.proj / ".githooks" / "pre-push").exists())
        # another tool's hook: the printed lines save stdin once
        self.git("config", "--unset", "core.hooksPath")
        (self.proj / ".git" / "hooks" / "pre-push").write_text("#!/bin/sh\ngit lfs pre-push \"$@\"\n")
        out = run().stdout
        self.assertIn("input=$(cat)", out)
        self.assertIn("--stdin-owned || exit 1", out)  # the saved input is the guard's alone: up-to-date pushes pass

    def test_round5_star_globs_keep_their_stars(self):
        pm = self.proj / ".teamwork" / "PRIVATE.md"
        pm.write_text("- quixbar\npath: *.pem\n- path: notes_*\n- path: `*.key`\n**path: dump_*.md**\n")
        import importlib.util
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        _, paths, _ = tw.load_private(self.proj / ".teamwork")
        self.assertEqual(paths, ["*.pem", "notes_*", "*.key", "dump_*.md"])
        for name in ["server.pem", "notes_client.md", "deep/id.key", "dump_x.md"]:
            f = self.proj / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x\n")
            self.commit("add " + name)
            r = self.guard("--base", "origin/main")
            self.assertEqual(r.returncode, 1, name + ": " + r.stdout + r.stderr)
            self.git("reset", "-q", "--hard", "origin/main")
            self.git("clean", "-qfd")
        self.assertEqual(self.guard("--base", "origin/main").returncode, 0)

    def test_review_binary_and_encodings(self):
        (self.proj / "l1.txt").write_bytes(("caf\xe9 " + self.SECRET).encode("latin-1"))
        self.assertEqual(self.guard().returncode, 1)
        (self.proj / "l1.txt").unlink()
        (self.proj / "u16.txt").write_bytes(("x " + self.SECRET).encode("utf-16"))
        self.assertEqual(self.guard().returncode, 1)
        (self.proj / "u16.txt").unlink()
        (self.proj / "img.png").write_bytes(b"\x89PNG\x00\x01" + self.SECRET.encode() + b"\x00")
        self.commit("bin")
        (self.proj / "img.png").unlink()
        self.commit("rm bin")
        r = self.guard()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("history", r.stdout)

    def test_review_gitattributes_nodiff(self):
        (self.proj / ".gitattributes").write_text("*.dat -diff\n")
        (self.proj / "x.dat").write_text(self.SECRET + "\n")
        self.commit("dat")
        (self.proj / "x.dat").unlink()
        self.commit("rm dat")
        self.assertEqual(self.guard().returncode, 1)

    def test_review_names_and_chinese(self):
        (self.proj / ".teamwork" / "PRIVATE.md").write_text("- 蓝鲸星\n1. Quixbar\n**Snorf**  # product\n")
        for name, body in (("文件.txt", "Quixbar inside"), ("蓝鲸星.txt", "ok"), ("notes-snorf.md", "ok")):
            (self.proj / name).write_text(body)
            r = self.guard()
            self.assertEqual(r.returncode, 1, name)
            for w in ("蓝鲸星", "quixbar", "snorf"):
                self.assertNotIn(w, r.stdout.lower())  # nothing printed in clear, file names included
            (self.proj / name).unlink()
        self.assertEqual(self.guard().returncode, 0)

    def test_review_ref_not_checked_out(self):
        self.git("checkout", "-q", "-b", "feat")
        (self.proj / "f.txt").write_text(self.SECRET)
        self.commit("feat")
        self.git("checkout", "-q", "main")
        r = self.guard("--base", "main", "--ref", "feat")
        self.assertEqual(r.returncode, 1)
        self.assertIn("tree feat", r.stdout)

    def test_review_missing_list_fails_closed(self):
        (self.proj / ".teamwork" / "PRIVATE.md").unlink()
        r = self.guard()
        self.assertEqual(r.returncode, 1)
        self.assertIn("no private list", r.stdout + r.stderr)
        (self.proj / ".teamwork" / "PRIVATE.md").write_text("# nothing private\n")
        self.assertEqual(self.guard().returncode, 0)

    def test_review_branch_name(self):
        r = subprocess.run([sys.executable, str(TW), "-C", str(self.proj), "guard", "--pre-push"], cwd=self.proj,
                           input=f"refs/heads/x {'a' * 40} refs/heads/{self.SECRET.lower()}-wip {'0' * 40}\n",
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("ref name", r.stdout)

    def test_pre_push_hook_blocks(self):
        inst = Path(__file__).resolve().parent / "install.py"
        cfg = Path(self._tmp.name) / "cfg"
        r = subprocess.run([sys.executable, str(inst), "--apply", "--link", "--no-allow", "--config-dir", str(cfg),
                            "--project", str(self.proj), "--pre-push"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        hook = self.proj / ".git" / "hooks" / "pre-push"
        self.assertTrue(hook.exists())
        self.assertIn('--remote "$1"', hook.read_text())
        lines = hook.read_text().splitlines()  # an older teamwork hook (no --remote) is upgraded in place
        hook.write_text("\n".join(lines[:2] + [lines[2].replace(' --remote "$1"', "")]) + "\n")
        subprocess.run([sys.executable, str(inst), "--apply", "--link", "--no-allow", "--config-dir", str(cfg),
                        "--project", str(self.proj), "--pre-push"], capture_output=True, text=True, check=True)
        self.assertIn('--remote "$1"', hook.read_text())
        (self.proj / "c.txt").write_text(f"{self.SECRET}\n")
        self.commit("c")
        push = subprocess.run(["git", "-C", str(self.proj), "push", "origin", "main"], capture_output=True, text=True)
        self.assertNotEqual(push.returncode, 0)
        self.assertIn("guard: FAILED", push.stdout + push.stderr)
        self.git("reset", "-q", "--hard", "HEAD~1")
        (self.proj / "d.txt").write_text("fine\n")
        self.commit("d")
        push = subprocess.run(["git", "-C", str(self.proj), "push", "origin", "main"], capture_output=True, text=True)
        self.assertEqual(push.returncode, 0, push.stdout + push.stderr)


    def pre_push(self, stdin, remote="origin"):
        return subprocess.run([sys.executable, str(TW), "-C", str(self.proj), "guard", "--pre-push",
                               *(["--remote", remote] if remote else [])],
                              cwd=self.proj, input=stdin, capture_output=True, text=True)

    def test_round2_first_push_to_another_remote(self):
        (self.proj / "b.txt").write_text(f"{self.SECRET}\n")
        self.commit("old leak, already on origin")
        self.git("push", "-q", "origin", "main")
        (self.proj / "b.txt").write_text("clean\n")
        self.commit("clean")
        self.git("push", "-q", "origin", "main")
        other = Path(self._tmp.name) / "mirror.git"
        subprocess.run(["git", "init", "-q", "--bare", str(other)], check=True)
        self.git("remote", "add", "mirror", str(other))
        head = self.git("rev-parse", "HEAD").stdout.strip()
        line = f"refs/heads/main {head} refs/heads/main {'0' * 40}\n"
        self.assertEqual(self.pre_push(line, "mirror").returncode, 1)  # mirror never had the old commit
        self.assertEqual(self.pre_push(line, None).returncode, 1)  # remote unknown: whole history
        self.assertEqual(self.pre_push(line, "origin").returncode, 0)  # origin already has all of it

    def test_round2_encodings_everywhere(self):
        (self.proj / ".teamwork" / "PRIVATE.md").write_text("蓝鲸星\nQuixbär\n")
        for name, data in (("gb.txt", "注意 蓝鲸星".encode("gb18030")), ("l1.txt", "x Quixbär".encode("latin-1")),
                           ("u16.txt", "x 蓝鲸星".encode("utf-16-le")), ("up.txt", "QUIXBÄR".encode("utf-8"))):
            (self.proj / name).write_bytes(data)
            self.commit(name)
            self.assertEqual(self.guard().returncode, 1, f"worktree/tree {name}")
            (self.proj / name).unlink()
            self.commit("rm " + name)
            r = self.guard()
            self.assertEqual(r.returncode, 1, f"history {name}")
            self.assertIn("history", r.stdout)
            self.git("reset", "-q", "--hard", "origin/main")

    def test_round2_list_formats(self):
        (self.proj / ".teamwork" / "PRIVATE.md").write_text(
            "| word | why |\n|---|---|\n| Zeta9 | client |\n"
            "- [ ] Snorf\n- [x] Blorpo\nQuixbar, Flimwax\nFlurbo - our client\nGronky: internal\n")
        terms = None
        import importlib.util
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        terms, _, _ = tw.load_private(self.proj / ".teamwork")
        for w in ("Zeta9", "Snorf", "Blorpo", "Quixbar", "Flimwax", "Flurbo", "Gronky"):
            self.assertIn(w, terms)
        self.assertNotIn("word", terms)  # table header skipped
        self.assertTrue(tw._path_rule_hit("secrets/k.txt", "/Secrets/"))
        self.assertTrue(tw._path_rule_hit("docs/Notes.md", "notes.md"))
        self.assertTrue(tw._path_rule_hit("a/b/key.pem", "**/*.pem"))
        self.assertFalse(tw._path_rule_hit("docs/secrets/x", "/secrets/"))

    def test_round2_author_tag_and_branch_names(self):
        (self.proj / "e.txt").write_text("e\n")
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", f"user.email={self.SECRET.lower()}@example.invalid",
                 "commit", "-qm", "clean message")
        r = self.guard()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.git("reset", "-q", "--hard", "origin/main")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "tag", "-a", "v1", "-m",
                 f"release for {self.SECRET}")
        head = self.git("rev-parse", "HEAD").stdout.strip()
        tag = self.git("rev-parse", "v1").stdout.strip()
        self.assertEqual(self.pre_push(f"refs/tags/v1 {tag} refs/tags/v1 {'0' * 40}\n").returncode, 1)
        self.git("checkout", "-q", "-b", f"{self.SECRET.lower()}-wip")
        r = self.guard()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("ref name", r.stdout)
        self.assertNotIn(self.SECRET.lower(), r.stdout.lower())

    def test_round2_private_folder_name_masked(self):
        (self.proj / ".teamwork" / "private" / "Quixbar-notes.txt").write_text("x")
        self.git("add", "-f", ".teamwork/private/Quixbar-notes.txt")  # someone force-added it
        r = self.guard()
        self.assertEqual(r.returncode, 1)
        self.assertIn("private/*****", r.stdout)
        self.assertNotIn("quixbar", r.stdout.lower())


    def test_round3_stale_remote_url(self):
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty",
                 "-m", f"fix for {self.SECRET} customer")
        self.git("push", "-q", "origin", "main")  # on the old (private) remote
        public = Path(self._tmp.name) / "public.git"
        subprocess.run(["git", "init", "-q", "--bare", str(public)], check=True)
        self.git("remote", "set-url", "origin", str(public))  # refs/remotes/origin/* are now stale
        head = self.git("rev-parse", "HEAD").stdout.strip()
        r = self.pre_push(f"refs/heads/main {head} refs/heads/main {'0' * 40}\n", "origin")
        self.assertEqual(r.returncode, 1, r.stdout)

    def test_round4_history_zip_pushurl_list_encoding(self):
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", f"<w>hello {self.SECRET} quixbar</w>" * 50)
        (self.proj / "d.docx").write_bytes(buf.getvalue())
        self.commit("add doc")
        self.git("rm", "-q", "d.docx")
        self.commit("drop doc")
        r = self.tw("guard")
        self.assertEqual(r.returncode, 1, r.stdout)  # only in history, compressed
        self.git("reset", "-q", "--hard", "origin/main")
        # pushurl differs from the fetch URL: compare against what the push server has
        (self.proj / "b.txt").write_text(f"{self.SECRET}\n")
        self.commit("leak")
        (self.proj / "b.txt").write_text("clean\n")
        self.commit("clean")
        self.git("push", "-q", "origin", "main")
        public = Path(self._tmp.name) / "public2.git"
        subprocess.run(["git", "init", "-q", "--bare", str(public)], check=True)
        self.git("config", "remote.origin.pushurl", str(public))
        head = self.git("rev-parse", "HEAD").stdout.strip()
        self.assertEqual(self.pre_push(f"refs/heads/main {head} refs/heads/main {'0' * 40}\n").returncode, 1)
        self.git("config", "--unset", "remote.origin.pushurl")
        # a list saved with a BOM or as UTF-16 still works; undecodable fails closed
        self.git("reset", "-q", "--hard", "HEAD~2")
        (self.proj / "c.txt").write_text(f"{self.SECRET}\n")
        priv = self.proj / ".teamwork" / "PRIVATE.md"
        for data in (b"\xef\xbb\xbf" + self.SECRET.encode(), self.SECRET.encode("utf-16"), b"\xff\xfa\x80"):
            priv.write_bytes(data)
            r = self.tw("guard")
            self.assertEqual(r.returncode, 1, data[:4])
            self.assertIn("UTF-8" if data[:2] == b"\xff\xfa" else "FAILED", r.stdout + r.stderr)

    def test_round3_tags_on_blobs_and_nested(self):
        blob = subprocess.run(["git", "-C", str(self.proj), "hash-object", "-w", "--stdin"],
                              input=f"{self.SECRET} plan", capture_output=True, text=True).stdout.strip()
        self.git("tag", "lw", blob)
        r = self.pre_push(f"refs/tags/lw {blob} refs/tags/lw {'0' * 40}\n")
        self.assertEqual(r.returncode, 1, r.stdout)
        g = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        self.git(*g, "tag", "-a", "inner", "-m", f"{self.SECRET} inner")
        self.git(*g, "tag", "-a", "outer", "inner", "-m", "clean")
        outer = self.git("rev-parse", "outer").stdout.strip()
        self.git("tag", "-d", "inner")
        self.assertEqual(self.pre_push(f"refs/tags/outer {outer} refs/tags/outer {'0' * 40}\n").returncode, 1)

    def test_round3_parser_nfd_replace_zip_symlink(self):
        import importlib.util, unicodedata, zipfile, io, os as _os
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        (self.proj / ".teamwork" / "PRIVATE.md").write_text(
            "Name: Zorblax\n> Quixbar\n* [x] ~~Snorfle~~\n<!-- Blorpo -->\nFlimwax/Gronky\nCrème\n")
        terms, _, _ = tw.load_private(self.proj / ".teamwork")
        for w in ("Zorblax", "Quixbar", "Snorfle", "Blorpo", "Flimwax", "Gronky"):
            self.assertIn(w, terms)
        (self.proj / "n.txt").write_text(unicodedata.normalize("NFD", "la Crème"))
        self.assertEqual(self.guard().returncode, 1)
        (self.proj / "n.txt").unlink()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", "<w>Zorblax roadmap</w>" * 50)
        (self.proj / "r.docx").write_bytes(buf.getvalue())
        self.assertEqual(self.guard().returncode, 1)
        (self.proj / "r.docx").unlink()
        _os.symlink("/nonexistent/Quixbar-dir", self.proj / "ln")
        self.assertEqual(self.guard().returncode, 1)
        (self.proj / "ln").unlink()
        self.assertEqual(self.guard().returncode, 0)
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty",
                 "-m", "Zorblax secret msg")
        bad = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--amend",
                 "--allow-empty", "-m", "clean copy")
        good = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("reset", "-q", "--hard", bad)
        self.git("replace", bad, good)  # local view says clean; the server would get the original
        self.assertEqual(self.guard().returncode, 1)


class Leases(TwCase):
    """T11: a ready task that overlaps an active task's paths is held back."""

    def setUp(self):
        super().setUp()
        for t in ("ui", "api"):
            self.tw("thread", "add", t)
        self.tw("plan", "approve", "--user-said", "ok")

    def ready(self):
        return json.loads(self.tw("ready").stdout)

    def test_declared_overlap_blocks(self):
        self.tw("task", "add", "T1", "--owner", "ui", "--touches", "web/**")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "web/login.js,api/x.py")
        self.tw("task", "add", "T3", "--owner", "api", "--touches", "api/**")
        self.tw("task", "set", "T1", "--status", "working")
        r = self.ready()
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["with"], "T1")
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["paths"], ["web/**"])
        self.assertIn("T3", r["ready"])
        self.assertNotIn("T2", r["ready"])
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.assertIn("T2", self.ready()["ready"])

    def test_same_owner_not_blocked_and_no_touches_listed(self):
        self.tw("task", "add", "T1", "--owner", "ui", "--touches", "web/**")
        self.tw("task", "add", "T2", "--owner", "ui", "--touches", "web/a.js")
        self.tw("task", "add", "T3", "--owner", "api")
        self.tw("task", "set", "T1", "--status", "working")
        r = self.ready()
        self.assertEqual(r["blocked_by_lease"], {})
        self.assertEqual(r["no_touches"], ["T3"])

    def test_overlap_rules(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        self.assertTrue(tw.paths_overlap("web/", "web/sub/a.js"))
        self.assertTrue(tw.paths_overlap("web/*.js", "web/**"))
        self.assertTrue(tw.paths_overlap("./api/x.py", "api/x.py"))
        self.assertFalse(tw.paths_overlap("web/**", "api/x.py"))
        self.assertFalse(tw.paths_overlap("docs/a.md", "docs/b.md"))
        # review findings
        self.assertTrue(tw.paths_overlap("web", "web/app.js"))
        self.assertTrue(tw.paths_overlap("web", "web/"))
        self.assertTrue(tw.paths_overlap("web/**", "web"))
        self.assertFalse(tw.paths_overlap(".github/x", "github/x"))
        self.assertFalse(tw.paths_overlap("webapp/x", "web"))
        # round 2
        self.assertTrue(tw.paths_overlap("**/config.py", "config.py"))
        self.assertTrue(tw.paths_overlap("web/**/a.js", "web/a.js"))
        self.assertTrue(tw.paths_overlap(".", "api/x.py"))
        self.assertTrue(tw.paths_overlap("web/./a/../b.js", "web/b.js"))
        self.assertTrue(tw.paths_overlap("/web/", "web/b.js"))
        self.assertFalse(tw.paths_overlap("Web/a.js", "web/b.js"))
        # round 3: glob against glob
        for a, b in (("**/*.py", "api/"), ("**/*.py", "api"), ("api/**/*.py", "**/x.py"), ("*.py", "web/*"),
                     ("*/config.py", "api/*.py"), ("docs/*.md", "*/README.md"), ("web/{a,b}.py", "web/a.py"),
                     ("Web/App.py", "web/app.py"), ("app/[id]/page.js", "app/[id]/")):
            self.assertTrue(tw.paths_overlap(a, b), (a, b))
        self.assertTrue(tw.paths_overlap("web/*", "web/sub/a.js"))  # 'web/*' may be a folder
        self.assertTrue(tw.paths_overlap("{a,b}" * 20 + "/x", "b" * 20 + "/x"))  # too many braces: widened
        self.assertFalse(tw.paths_overlap("web/*.css", "web/a.py"))
        self.assertNotEqual(self.tw("task", "add", "T9", "--touches", "../x", ok=False).returncode, 0)

    def test_round8_brace_widening(self):
        import importlib.util
        import time
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        many = "src/{" + ",".join(f"a{i}" for i in range(70)) + ",deep/q}.py"
        self.assertTrue(tw.paths_overlap(many, "src/deep/q.py"))  # widened across folders
        self.assertTrue(tw.paths_overlap("x/{z,{" + ",".join(f"a{i}" for i in range(70)) + "}}", "x/a5"))
        t0 = time.time()
        self.assertTrue(tw.paths_overlap("{a,b}" * 24 + "/x", "a" * 24 + "/x"))
        self.assertLess(time.time() - t0, 2)

    def test_two_ready_tasks_do_not_collide(self):
        self.tw("task", "add", "T1", "--owner", "ui", "--touches", "web/app.js")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "web/**")
        r = self.ready()
        self.assertEqual(r["ready"], ["T1"])
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["with"], "T1")


class LeasesGit(unittest.TestCase):
    """T11: the real diff of an active branch counts, even when undeclared."""

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
        if walk and ok:
            walk_args(lambda *x: self.tw(*x, walk=False), args, self.proj / ".teamwork" / "state.json")
        r = subprocess.run([sys.executable, str(TW), "-C", str(self.proj), *args], cwd=self.proj,
                           capture_output=True, text=True)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def test_branch_diff_blocks(self):
        self.tw("task", "add", "T1", "--owner", "ui")  # declared nothing
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.tw("task", "set", "T1", "--status", "working")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "shared").mkdir()
        (self.proj / "shared" / "config.py").write_text("x = 1\n")
        self.commit("ui touches shared")
        self.git("checkout", "-q", "main")
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["paths"], ["shared/config.py"])


class LeasesGitReview(LeasesGit):
    """T11 review findings: base only on origin / wrong base, done-but-unmerged keeps its lease."""

    test_branch_diff_blocks = None

    def ui_branch_changes_shared(self):
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "shared").mkdir()
        (self.proj / "shared" / "config.py").write_text("x = 1\n")
        self.commit("T1: ui touches shared")
        self.git("checkout", "-q", "main")

    def test_wrong_base_warns(self):
        st = json.loads((self.proj / ".teamwork" / "state.json").read_text())
        st["repo"]["base"] = "master"
        (self.proj / ".teamwork" / "state.json").write_text(json.dumps(st))
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared")
        self.tw("task", "set", "T1", "--status", "working")
        self.ui_branch_changes_shared()
        r = json.loads(self.tw("ready").stdout)
        self.assertTrue(any("base branch master not found" in w for w in r["lease_warnings"]))

    def test_done_but_unmerged_still_holds(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared")  # bare folder
        self.ui_branch_changes_shared()
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["status"], "done")
        self.git("merge", "-q", "tw/proj/ui")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["ready"])


    def test_round8_quoted_file_names(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "dir/")
        self.tw("task", "set", "T1", "--status", "working")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "dir").mkdir()
        for n in ('a"b.py', "c\td.py", "e\\f.py"):
            (self.proj / "dir" / n).write_text("x\n")
        self.commit("T1: odd names")
        self.git("checkout", "-q", "main")
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(sorted(r["blocked_by_lease"]["T2"][0]["paths"]),
                         sorted(["dir/a\"b.py", "dir/c\td.py", "dir/e\\f.py"]))

    def test_round10_brace_cli_and_subfolder_project(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "web/{zorblax,quixbar}.js, api/x.py")
        st = json.loads((self.proj / ".teamwork" / "state.json").read_text())
        self.assertEqual(st["tasks"]["T2"]["touches"], ["web/{zorblax,quixbar}.js", "api/x.py"])
        self.assertNotEqual(self.tw("task", "add", "T9", "--touches", "{a,../x}", ok=False).returncode, 0)
        self.tw("task", "set", "T1", "--status", "working")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "web").mkdir()
        (self.proj / "web" / "quixbar.js").write_text("x\n")
        self.commit("T1: quixbar")
        self.git("checkout", "-q", "main")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])
        # a project in a subfolder of the repo: --touches are relative to the project folder
        sub = self.proj / "pkg"
        sub.mkdir()
        run = lambda *x: subprocess.run([sys.executable, str(TW), "-C", str(sub), *x], cwd=sub,
                                        capture_output=True, text=True)
        self.assertEqual(run("init", "pkg").returncode, 0)
        for x in (("thread", "add", "ui"), ("thread", "add", "api"), ("plan", "approve", "--user-said", "ok"),
                  ("task", "add", "T1", "--owner", "ui"), ("task", "add", "T2", "--owner", "api",
                                                            "--touches", "src/zorblax.ts")):
            self.assertEqual(run(*x).returncode, 0, x)
        for x in ("approved", "dispatched"):
            run("task", "set", "T1", "--status", x)
        self.git("checkout", "-q", "-b", "tw/pkg/ui")
        (sub / "src").mkdir()
        (sub / "src" / "zorblax.ts").write_text("x\n")
        self.commit("T1: zorblax")
        self.git("checkout", "-q", "main")
        r = json.loads(run("ready").stdout)
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["paths"], ["src/zorblax.ts"])

    def test_round11_earlier_task_squash_not_held_by_later_ones(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.tw("task", "add", "T4", "--owner", "ui", "--touches", "zz/q.js")
        self.ui_branch_changes_shared()
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.git("merge", "-q", "--squash", "tw/proj/ui")
        self.commit("T1: squash")
        (self.proj / "shared" / "config.py").write_text("x = 9\n")
        self.commit("T5: later edit on main")
        self.tw("task", "set", "T4", "--status", "working")  # the same branch goes on with T4
        r = json.loads(self.tw("ready").stdout)
        self.assertNotIn("T2", r["blocked_by_lease"], r)
        self.assertNotEqual(self.tw("task", "add", "T9", "--touches", "web/{a,b.js, api/x.py", ok=False).returncode, 0)

    def test_round12_stale_origin_copy_after_squash(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "shared").mkdir()
        (self.proj / "shared" / "config.py").write_text("x = 0\n")
        self.commit("T1: wip")
        self.git("update-ref", "refs/remotes/origin/tw/proj/ui", "HEAD")  # pushed WIP, now behind
        (self.proj / "shared" / "config.py").write_text("x = 1\n")
        self.commit("T1: final")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.git("merge", "-q", "--squash", "tw/proj/ui")
        self.commit("T1: squash")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["ready"])
        self.assertNotEqual(self.tw("task", "add", "T9", "--touches", "web/}a,b{.js, api/x.py", ok=False).returncode, 0)

    def test_round13_diverged_stale_copy_and_deleted_branch(self):
        def commit_at(msg, when):
            self.git("add", "-A")
            subprocess.run(["git", "-C", str(self.proj), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                            "commit", "-qm", msg], check=True, capture_output=True,
                           env={**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when})
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "shared").mkdir()
        (self.proj / "shared" / "config.py").write_text("x = 0\n")
        commit_at("T1: wip", "2030-01-01T10:00:00Z")
        self.git("update-ref", "refs/remotes/origin/tw/proj/ui", "HEAD")  # pushed WIP
        (self.proj / "shared" / "config.py").write_text("x = 1\n")
        self.git("add", "-A")
        subprocess.run(["git", "-C", str(self.proj), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-q", "--amend", "-m", "T1: final"], check=True, capture_output=True,
                       env={**os.environ, "GIT_COMMITTER_DATE": "2030-01-01T11:00:00Z"})  # diverged from origin
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.git("merge", "-q", "--squash", "tw/proj/ui")
        commit_at("T1: zorblax (#12)", "2030-01-01T12:00:00Z")
        # T25 / T11-b: diverged copies both hold (work on either side is never dropped), so the stale pushed WIP
        # still holds after the amended copy landed, until it is pushed over, deleted or pruned
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])
        self.git("branch", "-q", "-D", "tw/proj/ui")  # the host deleted the branch: only the stale origin copy left
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])  # unsure: held ...
        self.git("update-ref", "-d", "refs/remotes/origin/tw/proj/ui")  # ... until `git fetch --prune`
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["ready"])

    def test_t25_past_owners_branch_still_holds(self):
        """T11-a: a task handed to another thread keeps the lease of what the earlier owner left unmerged."""
        self.tw("thread", "add", "db")
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.tw("task", "set", "T1", "--status", "working")
        self.ui_branch_changes_shared()
        self.tw("task", "set", "T1", "--owner", "db")  # db has no branch yet; ui's work is still unmerged
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["paths"], ["shared/config.py"])

    def test_t29_past_owner_holds_only_this_tasks_commits(self):
        """T29: after a handover, the earlier owner's branch holds only what its commits for this task changed;
        that thread's other work on the same branch is not counted against its own next task."""
        self.tw("thread", "add", "db")
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T5", "--owner", "ui")
        self.tw("task", "add", "T6", "--owner", "ui", "--touches", "zorb/quixbar.py")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.tw("task", "set", "T1", "--status", "working")
        self.ui_branch_changes_shared()  # T1: shared/config.py
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "zorb").mkdir()
        (self.proj / "zorb" / "quixbar.py").write_text("q = 1\n")
        self.commit("T5: zorblax on the same branch")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T1", "--owner", "db")
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["paths"], ["shared/config.py"])  # T1's own work still holds
        self.assertIn("T6", r["ready"], r)  # ui's other work is not held by db's T1
        self.assertNotIn("T6", r["blocked_by_lease"])

    def test_t29_untagged_and_merge_commits_still_hold(self):
        """T29 review: a `WIP:` commit (RECALL), a fixup! and a merge on the earlier owner's branch keep holding;
        a T2 commit is not T29's (id prefix)."""
        self.tw("thread", "add", "db")
        for t in ("T29", "T2"):
            self.tw("task", "add", t, "--owner", "ui")
        self.tw("task", "add", "T7", "--owner", "api", "--touches", "w/zorblax.py, w/fix.py, m/side.py")
        self.tw("task", "add", "T8", "--owner", "ui", "--touches", "w/two.py")
        self.tw("task", "set", "T29", "--status", "working")
        self.git("checkout", "-q", "-b", "side")
        (self.proj / "m").mkdir()
        (self.proj / "m" / "side.py").write_text("s = 1\n")
        self.commit("side work")
        self.git("checkout", "-q", "main")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "w").mkdir()
        (self.proj / "w" / "zorblax.py").write_text("z = 1\n")
        self.commit("WIP: recall, half of the Zorblax parser")
        (self.proj / "w" / "fix.py").write_text("f = 1\n")
        self.commit("fixup! T29: quixbar")
        (self.proj / "w" / "two.py").write_text("t = 1\n")
        self.commit("T2: other work")
        self.git("merge", "-q", "--no-ff", "-m", "T29: merge side", "side")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T29", "--owner", "db")
        r = json.loads(self.tw("ready").stdout)
        held = sorted(p for b in r["blocked_by_lease"]["T7"] for p in b["paths"])
        self.assertEqual(held, ["m/side.py", "w/fix.py", "w/zorblax.py"], r)
        self.assertIn("T8", r["ready"], r)  # T2's file is not held by T29

    def test_t29_other_lead_naming_this_task_and_renames_hold(self):
        """T29 review r2: `T5 and T29: ...` still names T29; a file T29 made that a T5 commit renamed still holds."""
        self.tw("thread", "add", "db")
        for t in ("T29", "T5"):
            self.tw("task", "add", t, "--owner", "ui")
        self.tw("task", "add", "T7", "--owner", "api", "--touches", "q/both.py, q/new.py")
        self.tw("task", "set", "T29", "--status", "working")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "q").mkdir()
        (self.proj / "q" / "both.py").write_text("b = 1\n")
        self.commit("T5 and T29: shared Quixbar")
        (self.proj / "q" / "old.py").write_text("o = 1\n" * 20)
        self.commit("T29: zorblax module")
        self.git("mv", "q/old.py", "q/new.py")
        self.commit("T5: rename")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T29", "--owner", "db")
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(sorted(p for b in r["blocked_by_lease"]["T7"] for p in b["paths"]), ["q/both.py", "q/new.py"])

    def test_t29_tagged_merge_and_near_names_hold(self):
        """T29 review r3: a merge titled `T5: ...` (evil edits included) and `T5: T29-related` still hold."""
        self.tw("thread", "add", "db")
        for t in ("T29", "T5"):
            self.tw("task", "add", t, "--owner", "ui")
        self.tw("task", "add", "T7", "--owner", "api", "--touches", "e/evil.py, e/side.py, e/near.py")
        self.tw("task", "set", "T29", "--status", "working")
        self.git("checkout", "-q", "-b", "side")
        (self.proj / "e").mkdir()
        (self.proj / "e" / "side.py").write_text("s = 1\n")
        self.commit("T5: side")
        self.git("checkout", "-q", "main")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        self.git("merge", "-q", "--no-ff", "--no-commit", "side")
        (self.proj / "e" / "evil.py").write_text("e = 1\n")
        self.commit("T5: merge side")
        (self.proj / "e" / "near.py").write_text("n = 1\n")
        self.commit("T5: T29-related Zorblax")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T29", "--owner", "db")
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(sorted(p for b in r["blocked_by_lease"]["T7"] for p in b["paths"]),
                         ["e/evil.py", "e/near.py", "e/side.py"])

    def test_t29_many_tasks_stay_fast(self):
        """T29 review r2: the branch log is parsed once, not once per task with a regex per task id."""
        import time
        self.tw("thread", "add", "db")
        self.tw("task", "add", "T1", "--owner", "ui")
        st = json.loads((self.proj / ".teamwork" / "state.json").read_text())
        base = st["tasks"]["T1"]
        for i in range(600):
            st["tasks"][f"T{100 + i}"] = {**base, "owner": "db", "past_owners": ["ui"], "status": "working"}
        (self.proj / ".teamwork" / "state.json").write_text(json.dumps(st))
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "z").mkdir()
        for i in range(40):
            (self.proj / "z" / f"f{i}.py").write_text(f"x = {i}\n")
            self.commit(f"T{100 + i}: zorblax {i}")
        self.git("checkout", "-q", "main")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import tw
        t0 = time.time()
        held = tw._held_paths(json.loads((self.proj / ".teamwork" / "state.json").read_text()))
        self.assertLess(time.time() - t0, 5)
        self.assertEqual(held["T105"], ["z/f5.py"])

    def test_t25_diverged_copies_both_hold(self):
        """T11-b: local and origin diverged with different work on each side: both sides count, not only the newer."""
        def commit_at(msg, when):
            self.git("add", "-A")
            subprocess.run(["git", "-C", str(self.proj), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                            "commit", "-qm", msg], check=True, capture_output=True,
                           env={**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when})
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "zorb/quixbar.py")
        self.tw("task", "set", "T1", "--status", "working")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "zorb").mkdir()
        (self.proj / "zorb" / "quixbar.py").write_text("q = 1\n")
        commit_at("T1: pushed from the other seat", "2030-01-01T10:00:00Z")
        self.git("update-ref", "refs/remotes/origin/tw/proj/ui", "HEAD")
        self.git("reset", "-q", "--hard", "main")
        (self.proj / "zorb").mkdir(exist_ok=True)
        (self.proj / "zorb" / "lanjing.py").write_text("l = 1\n")
        commit_at("T1: newer local work", "2030-01-01T11:00:00Z")
        self.git("checkout", "-q", "main")
        r = json.loads(self.tw("ready").stdout)
        self.assertIn("T2", r["blocked_by_lease"], r)  # the older origin copy's file still holds

    def test_round14_unpushed_work_after_older_copy_squashed(self):
        def commit_at(msg, when):
            self.git("add", "-A")
            subprocess.run(["git", "-C", str(self.proj), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                            "commit", "-qm", msg], check=True, capture_output=True,
                           env={**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when})
        for fix_id in ("T4", "T1"):  # a later task's work, or the done task's own review fix, not pushed
            self.tearDown()
            self.setUp()
            self.tw("task", "add", "T1", "--owner", "ui")
            self.tw("task", "add", "T4", "--owner", "ui", "--touches", "zz/q.js")
            self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/zorb.py")
            self.git("checkout", "-q", "-b", "tw/proj/ui")
            (self.proj / "shared").mkdir()
            (self.proj / "shared" / "zorb.py").write_text("a = 1\n")
            commit_at("T1: zorblax", "2030-01-01T10:00:00Z")
            self.git("update-ref", "refs/remotes/origin/tw/proj/ui", "HEAD")  # pushed
            pushed = self.git("rev-parse", "HEAD").stdout.strip()
            (self.proj / "shared" / "zorb.py").write_text("a = 2\n")
            commit_at(f"{fix_id}: more on zorb", "2030-01-01T11:30:00Z")  # local only
            self.git("checkout", "-q", "main")
            self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
            if fix_id == "T4":
                self.tw("task", "set", "T4", "--status", "working")
            self.git("merge", "-q", "--squash", pushed)
            commit_at("T1: Zorblax (#3)", "2030-01-01T12:00:00Z")
            self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"], fix_id)

    def test_round15_combined_squash_and_amended_fix(self):
        def commit_at(msg, when, *extra):
            self.git("add", "-A")
            subprocess.run(["git", "-C", str(self.proj), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                            "commit", "-q", *extra, "-m", msg], check=True, capture_output=True,
                           env={**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when})
        lines = lambda *x: "".join(f"{v}\n" for v in x)
        for case in ("plain", "later-task", "amended"):
            self.tearDown()
            self.setUp()
            (self.proj / "shared").mkdir()
            (self.proj / "shared" / "zorb.py").write_text(lines(1, 2, 3, 4, 5, 6, 7))
            commit_at("base file", "2030-01-01T09:00:00Z")
            self.tw("task", "add", "T1", "--owner", "ui")
            self.tw("task", "add", "T4", "--owner", "ui", "--touches", "zz/q.js")
            self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/zorb.py")
            self.git("checkout", "-q", "-b", "tw/proj/ui")
            (self.proj / "shared" / "zorb.py").write_text(lines("one", 2, 3, 4, 5, 6, 7))
            commit_at("T1: zorblax", "2030-01-01T10:00:00Z")
            pushed = self.git("rev-parse", "HEAD").stdout.strip()
            self.git("update-ref", "refs/remotes/origin/tw/proj/ui", pushed)
            if case == "later-task":
                (self.proj / "shared" / "zorb.py").write_text(lines("one", 2, 3, "four", 5, 6, 7))
                commit_at("T4: quixbar", "2030-01-01T11:00:00Z")
            if case == "amended":
                (self.proj / "shared" / "zorb.py").write_text(lines("ONE", 2, 3, 4, 5, 6, 7))
                commit_at("T1: zorblax", "2030-01-01T11:00:00Z", "--amend")
            self.git("checkout", "-q", "main")
            (self.proj / "shared" / "zorb.py").write_text(lines(1, 2, 3, 4, 5, 6, "seven"))
            commit_at("lead: line 7", "2030-01-01T10:30:00Z")
            self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
            if case == "later-task":
                self.tw("task", "set", "T4", "--status", "working")
            (self.proj / "shared" / "zorb.py").write_text(lines("one", 2, 3, 4, 5, 6, "seven"))  # host squash
            commit_at("T1: Zorblax (#3)", "2030-01-01T12:00:00Z")
            r = json.loads(self.tw("ready").stdout)
            if case == "plain":
                self.assertIn("T2", r["ready"], case)  # the squash folded in the base's own edit
            else:
                self.assertIn("T2", r["blocked_by_lease"], case)  # later work / an unpushed fix is not on the base

    def test_round2_stale_origin_rename_recalled(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared")
        self.ui_branch_changes_shared()
        self.git("update-ref", "refs/remotes/origin/main", "main")  # origin/main from before the merge
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.git("merge", "-q", "tw/proj/ui")  # merged locally, not pushed: local main is newer
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["ready"])
        # a rename moves files out of a leased folder: both names count
        self.git("checkout", "-q", "-b", "tw/proj/api2", "main")
        self.tw("thread", "add", "api2")
        self.tw("task", "add", "T3", "--owner", "api2")
        self.tw("task", "add", "T4", "--owner", "ui", "--touches", "shared/**")
        (self.proj / "moved.py").write_text((self.proj / "shared" / "config.py").read_text())
        (self.proj / "shared" / "config.py").unlink()
        self.commit("T3: move config")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T3", "--status", "working")
        r = json.loads(self.tw("ready").stdout)
        self.assertIn("shared/config.py", r["blocked_by_lease"]["T4"][0]["paths"])
        self.tw("task", "set", "T3", "--status", "recalled")  # WIP stays on the branch
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(r["blocked_by_lease"]["T4"][0]["status"], "recalled")

    def test_round3_squash_merge_releases(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared")
        self.ui_branch_changes_shared()
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.git("merge", "-q", "--squash", "tw/proj/ui")
        self.commit("T1: squash of tw/proj/ui")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["ready"])

    def test_round8_real_files_and_later_fix(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        F = tw.RealFile
        for g, f in (("*.css", "zorb/app.py"), ("**/*.test.js", "quix/main.go"), ("src/**/*.ts", "src/app.py"),
                     ("*.md", "README"), ("zorb/*.py", "zorb/back\\sl.txt")):
            self.assertFalse(tw.paths_overlap(g, F(f)), (g, f))
        for g, f in (("**/*.py", "a/b.py"), ("zorb/", "zorb/x/y.py"), ("zorb", "zorb/a.py"), ("*.md", "d/x.md"),
                     ("zorb/*.py", "zorb/back\\sl.py"), ("a/[*]b", "a/*b")):
            self.assertTrue(tw.paths_overlap(g, F(f)), (g, f))
        self.assertTrue(tw.paths_overlap(F("Zorb/A.py"), F("zorb/a.py")))
        for g, f in (("app/[id]/page.tsx", "app/[id]/page.tsx"), ("app/[id]/", "app/[id]/x.ts"),
                     ("src/routes/[slug]/**", "src/routes/[slug]/+page.svelte"), ("{a,b}.py", "{a,b}.py"),
                     ("*blax", "src/zorblax/gen.py")):
            self.assertTrue(tw.paths_overlap(g, F(f)), (g, f))
            self.assertTrue(tw.paths_overlap(g, f), (g, f))  # declared and real agree
        # squash-merged, then a review fix on the branch that is not merged: still held
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared")
        self.ui_branch_changes_shared()
        self.git("merge", "-q", "--squash", "tw/proj/ui")
        self.commit("T1: squash of tw/proj/ui")
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "shared" / "config.py").write_text("x = 2\n")
        self.commit("T1: review fix")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])

    def test_round4_squash_then_base_edits_and_topic_blocked(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared")
        self.ui_branch_changes_shared()
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.git("merge", "-q", "--squash", "tw/proj/ui")
        self.commit("T1: squash of tw/proj/ui")
        (self.proj / "shared" / "config.py").write_text("x = 2\n")  # main edits the file afterwards
        self.commit("later edit")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["ready"])
        self.tw("task", "add", "T3", "--owner", "ui", "--topics", "t", "--touches", "z.py")
        self.tw("task", "add", "T4", "--owner", "api", "--topics", "t")
        self.tw("thread", "add", "db")
        self.tw("task", "add", "T5", "--owner", "db", "--touches", "z.py")
        self.tw("task", "set", "T4", "--status", "working")
        r = json.loads(self.tw("ready").stdout)
        self.assertIn("T3", r["blocked_by_topic"])
        self.assertIn("T5", r["ready"])  # T3 will not start, so it holds no lease

    def test_round5_mention_is_not_a_merge(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.ui_branch_changes_shared()
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        (self.proj / "NOTES").write_text("n\n")
        self.commit("plan: T1 review pending, do not merge yet")  # names T1, touches nothing of it
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])
        (self.proj / "shared").mkdir(exist_ok=True)
        (self.proj / "shared" / "config.py").write_text("other\n")
        self.commit("Revert T1 partially")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])

    def test_round5_one_pass_no_collisions(self):
        for n in ("web", "db"):
            self.tw("thread", "add", n)
        self.tw("task", "add", "X", "--owner", "api", "--touches", "a/")
        self.tw("task", "add", "A", "--owner", "ui", "--topics", "t", "--touches", "a/")
        self.tw("task", "add", "B", "--owner", "web", "--topics", "t", "--touches", "b/")
        self.tw("task", "add", "C", "--owner", "db", "--touches", "b/x.py")
        self.tw("task", "set", "X", "--status", "working")
        r = json.loads(self.tw("ready").stdout)
        self.assertIn("A", r["blocked_by_lease"])
        self.assertIn("B", r["ready"])
        self.assertIn("C", r["blocked_by_lease"])  # B is offered, so it holds b/

    def test_round6_bracket_names_and_mention_edits(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "docs/*.md")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "docs").mkdir()
        (self.proj / "docs" / "notes[draft.md").write_text("d\n")
        self.commit("T1: odd name")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T1", "--status", "working")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])  # no crash, still a clash
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "t", "--user-said", "ok")
        (self.proj / "docs").mkdir(exist_ok=True)
        (self.proj / "docs" / "notes[draft.md").write_text("prep\n")
        self.commit("Prepare docs for T1 (not merged yet)")  # names T1 and edits the file, but is not T1
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])

class HandoverGit(LeasesGit):
    test_branch_diff_blocks = None

    def test_origin_commits_not_hidden_by_local_branch(self):
        self.git("checkout", "-q", "-b", "tmp")
        (self.proj / "w.txt").write_text("w\n")
        self.commit("T1: pushed work")
        sha = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("checkout", "-q", "main")
        self.git("update-ref", "refs/remotes/origin/tw/proj/ui", sha)
        self.git("branch", "tw/proj/ui", "main")  # local branch has nothing
        out = self.tw("handover", "ui").stdout
        self.assertIn("T1: pushed work", out)
        self.assertIn("local and origin differ", out)
        self.assertIn("[origin/tw/proj/ui]", out)


    def test_round2_missing_base_keeps_configured_name(self):
        st = json.loads((self.proj / ".teamwork" / "state.json").read_text())
        st["repo"]["base"] = "nope"
        (self.proj / ".teamwork" / "state.json").write_text(json.dumps(st))
        out = self.tw("handover", "ui").stdout
        self.assertIn("(base nope)", out)
        self.assertIn("base branch nope not found", out)


class Trace(LeasesGit):
    """T17: follow a request to tasks, threads, sessions, commits and report time."""

    test_branch_diff_blocks = None  # inherited fixture only

    def test_trace_request_end_to_end(self):
        self.tw("thread", "set", "ui", "--session", "sess-ui", "--status", "working")
        self.tw("task", "add", "T1", "--owner", "ui", "--title", "blue button")
        self.tw("task", "add", "T2", "--owner", "ui", "--title", "other work")
        self.tw("req", "add", "make the button blue", "--tasks", "T1")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "b.css").write_text("blue\n")
        self.commit("T1: blue button")
        (self.proj / "c.css").write_text("x\n")
        self.commit("T2: unrelated")
        self.git("checkout", "-q", "main")
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        k = t["tasks"][0]
        self.assertEqual((k["task"], k["thread"], k["session_id"], k["branch"]), ("T1", "ui", "sess-ui", "tw/proj/ui"))
        self.assertEqual(len(k["commits"]), 1)  # only its own commits
        self.assertTrue(any("T2: unrelated" in c for c in t["other_branch_work"]["tw/proj/ui"]))
        self.git("merge", "-q", "--no-ff", "tw/proj/ui", "-m", "Merge ui (T1)")
        self.tw("task", "set", "T1", "--accept", "x", "--status", "done", "--review", "pass", "--reviewer", "rev")
        self.tw("req", "set", "R1", "--status", "reported")
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        self.assertTrue(t["reported_at"])
        commits = t["tasks"][0]["commits"]
        self.assertTrue(any("T1: blue button" in c for c in commits))
        self.assertFalse(any("T2: unrelated" in c for c in commits))  # finished task: only its own commits
        evs = [e["ev"] for e in t["events"]]
        self.assertIn("req-add", evs)
        self.assertIn("task-set", evs)
        text = self.tw("trace", "R1").stdout
        self.assertIn("R1 [reported] make the button blue", text)
        self.assertIn("session sess-ui", text)

    def test_board_cross_references(self):
        self.tw("task", "add", "T1", "--owner", "ui", "--title", "blue button")
        self.tw("req", "add", "make the button blue", "--tasks", "T1")
        board = self.tw("board").stdout
        self.assertIn("**R1** [dispatched] make the button blue — T1", board)
        row = [ln for ln in board.splitlines() if ln.startswith("| T1 |")][0]
        self.assertTrue(row.rstrip().endswith("| R1 |"))

    def test_review_reassigned_task_keeps_commits(self):
        self.tw("task", "add", "T1", "--owner", "ui", "--title", "x")
        self.tw("req", "add", "x", "--tasks", "T1")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "early.txt").write_text("e\n")
        self.commit("T1: early work by ui")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T1", "--owner", "api")
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        self.assertEqual(t["tasks"][0]["owners"], ["ui", "api"])
        self.assertTrue(any("T1: early work by ui" in c for c in t["tasks"][0]["commits"]))

    def test_review_bad_log_lines_wrong_base_zh_closed(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("req", "add", "x", "--tasks", "T1")
        with open(self.proj / ".teamwork" / "log.jsonl", "ab") as f:
            f.write(b'[1,2]\n"str"\n42\n{bad\n\xff\xfe\n')
        st = json.loads((self.proj / ".teamwork" / "state.json").read_text())
        st["repo"]["base"] = "master"
        (self.proj / ".teamwork" / "state.json").write_text(json.dumps(st))
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        self.assertTrue(t["warnings"])
        self.tw("prefs", "--set", "language=zh")
        self.tw("req", "drop", "R1", "--user-said", "不做了")
        text = self.tw("trace", "R1").stdout
        self.assertIn("[已放弃]", text)
        self.assertIn("线程 ui", text)
        self.assertIn("关闭 20", text)
        self.assertIn("| R1✗ |", self.tw("board").stdout)

    def test_trace_without_git(self):
        subprocess.run(["rm", "-rf", str(self.proj / ".git")], check=True)
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("req", "add", "x", "--tasks", "T1")
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        self.assertEqual(t["tasks"][0]["commits"], [])


    def test_round2_ids_dedupe_base_text(self):
        self.tw("thread", "set", "ui", "--session", "s1")
        self.tw("thread", "reseat", "ui", "--session", "s2", "--reason", "failed")
        for t in ("T1", "T2", "T3"):
            self.tw("task", "add", t, "--owner", "ui", "--title", t)
        self.tw("req", "add", "two tasks", "--tasks", "T1,T2")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        for msg in ("T1: one", "T2: two", "T1.5: half step", "T3: other"):
            (self.proj / (msg[:4].replace(":", "").replace(".", "_") + ".txt")).write_text(msg)
            self.commit(msg)
        self.git("checkout", "-q", "main")
        self.git("update-ref", "refs/remotes/origin/tw/proj/ui", "tw/proj/ui")
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        t1 = t["tasks"][0]["commits"]
        self.assertEqual(len(t1), 1, t1)  # T1.5 is not T1
        other = [c for cs in t["other_branch_work"].values() for c in cs]
        self.assertEqual(sorted(c.split(" ", 2)[2] for c in other), ["T1.5: half step", "T3: other"])  # once each
        with open(self.proj / ".teamwork" / "log.jsonl", "a") as f:
            f.write(json.dumps({"ev": "custom", "ids": "R1,R12"}) + "\n" + json.dumps({"ev": "x", "ids": "R12"}) + "\n")
        evs = [e["ev"] for e in json.loads(self.tw("trace", "R1", "--json").stdout)["events"]]
        self.assertIn("custom", evs)
        self.assertNotIn("x", evs)
        self.tw("req", "drop", "R1", "--user-said", "stop", "--note", "superseded by R2")
        text = self.tw("trace", "R1").stdout
        self.assertIn("superseded by R2", text)
        self.assertIn("sessions: s1 (failed) -> s2", text)

    def test_round3_sub_ids_and_bad_log_fields(self):
        for t in ("T1", "T1a"):
            self.tw("task", "add", t, "--owner", "ui")
        self.tw("req", "add", "one", "--tasks", "T1")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        for msg in ("T1: main", "T1a: sub", "T1-2: part", "T1_x: other"):
            (self.proj / (str(abs(hash(msg))) + ".txt")).write_text(msg)
            self.commit(msg)
        self.git("checkout", "-q", "main")
        with open(self.proj / ".teamwork" / "log.jsonl", "a") as f:
            for e in ({"ids": 42}, {"task": "T1", "snap_task": "str"}, {"task": "T1", "owner": ["ui"]}):
                f.write(json.dumps(e) + "\n")
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        self.assertEqual([c.split(" ", 4)[4] for c in t["tasks"][0]["commits"]], ["T1: main"])

    def test_round2_init_detects_base_and_lead_base(self):
        self.git("branch", "-m", "main", "trunk")
        other = self.proj.parent / "p2"
        subprocess.run(["git", "clone", "-q", str(self.proj), str(other)], check=True, capture_output=True)
        subprocess.run([sys.executable, str(TW), "-C", str(other), "init", "p2", "--path", str(other)],
                       check=True, capture_output=True, cwd=other)
        self.assertEqual(json.loads((other / ".teamwork" / "state.json").read_text())["repo"]["base"], "trunk")
        self.tw("lead", "--base", "trunk")
        self.assertEqual(json.loads((self.proj / ".teamwork" / "state.json").read_text())["repo"]["base"], "trunk")


class Seats(TwCase):
    """T12: a thread is a seat; sessions come and go, history is kept."""

    def setUp(self):
        super().setUp()
        self.tw("thread", "add", "ui", "--session", "s-old", "--status", "working", "--owns", "web/")
        self.tw("task", "add", "T1", "--owner", "ui", "--title", "blue button", "--accept", "button is blue")

    def seat(self):
        return self.state()["threads"]["ui"]

    def test_reseat_keeps_history_and_roster_shows_current(self):
        self.assertEqual([h["session_id"] for h in self.seat()["sessions"]], ["s-old"])
        self.assertNotEqual(self.tw("thread", "reseat", "ui", ok=False).returncode, 0)  # needs --session
        self.tw("thread", "reseat", "ui", "--session", "s-new", "--reason", "context full")
        t = self.seat()
        self.assertEqual(t["session_id"], "s-new")
        self.assertEqual(t["status"], "starting")
        self.assertEqual([h["session_id"] for h in t["sessions"]], ["s-old", "s-new"])
        self.assertEqual(t["sessions"][0]["reason"], "context full")
        self.assertTrue(t["sessions"][0]["to"])
        self.assertNotIn("to", t["sessions"][1])
        roster = self.tw("roster").stdout
        self.assertIn("session_id=s-new", roster)
        self.assertNotIn("s-old", roster)
        self.assertNotEqual(self.tw("thread", "reseat", "ui", "--session", "s-new", ok=False).returncode, 0)

    def test_set_session_records_history_for_old_threads(self):
        st = self.state()
        del st["threads"]["ui"]["sessions"]  # thread created before T12
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.tw("thread", "set", "ui", "--session", "s2")
        self.assertEqual([h["session_id"] for h in self.seat()["sessions"]], ["s-old", "s2"])

    def test_review_reseat_with_status_resets_timer_and_logs(self):
        self.env["TW_NOW"] = "2026-01-01T00:00:00+00:00"
        self.tw("thread", "set", "ui", "--from-status")
        self.env["TW_NOW"] = "2026-03-01T00:00:00+00:00"
        self.tw("thread", "reseat", "ui", "--session", "s-new", "--status", "working", "--reason", "failed")
        self.assertEqual(json.loads(self.tw("ready").stdout)["stuck"], {})
        log = (self.dir / ".teamwork" / "log.jsonl").read_text()
        ev = [json.loads(x) for x in log.splitlines() if '"thread-reseat"' in x][-1]
        self.assertEqual((ev["session"], ev["prev_session"], ev["reason"]), ("s-new", "s-old", "failed"))

    def test_review_handover_prefers_active_task_and_no_git(self):
        self.tw("task", "add", "T2", "--owner", "ui", "--title", "active one")
        self.tw("task", "set", "T2", "--status", "working")
        out = self.tw("handover", "ui").stdout
        self.assertIn("TASK lead -> ui T2", out)
        self.assertIn("Unmerged commits on the branch: unknown", out)  # temp project is not a git repo

    def test_handover_brief(self):
        self.tw("thread", "reseat", "ui", "--session", "s-new", "--reason", "failed")
        r = self.tw("handover", "ui")
        self.assertIn("--leaving s-old", r.stderr)  # already reseated: the tool says how to name the old session
        out = self.tw("handover", "ui", "--leaving", "s-old").stdout
        self.assertIn("[tw:demo] TASK lead -> ui T1", out)
        self.assertIn("from an earlier session (s-old, left: failed)", out)
        self.assertIn("T1 [planned] blue button — done when: button is blue", out)
        self.assertIn("<paste here>", out)


    def test_round2_handover_before_reseat_names_leaving_session(self):
        self.env["TW_NOW"] = "2026-01-01T00:00:00+00:00"
        self.tw("thread", "reseat", "ui", "--session", "s2", "--reason", "ctx full")
        self.assertIn("(s-old, left: ctx full)", self.tw("handover", "ui", "--leaving", "s-old").stdout)
        self.env["TW_NOW"] = "2026-01-01T01:00:00+00:00"
        self.tw("thread", "set", "ui", "--status", "working", "--from-status")  # s2 reported, then failed
        out = self.tw("handover", "ui", "--reason", "failed").stdout  # SKILL step 6: handover before reseat
        self.assertIn("from an earlier session (s2, left: failed)", out)
        out = self.tw("handover", "ui", "--leaving", "s-old").stdout
        self.assertIn("(s-old, left: ctx full)", out)

    def test_round3_manual_set_shared_session_first_seat(self):
        self.env["TW_NOW"] = "2026-01-01T00:00:00+00:00"
        self.tw("thread", "set", "ui", "--from-status")
        self.env["TW_NOW"] = "2026-01-01T01:00:00+00:00"
        self.tw("thread", "set", "ui", "--session", "s-B")  # switched by hand, not reseat
        self.assertIn("(s-old, left: session changed)", self.tw("handover", "ui", "--leaving", "s-old").stdout)
        self.assertIn("(s-B, left: died early)", self.tw("handover", "ui", "--reason", "died early").stdout)
        self.tw("thread", "add", "api", "--session", "s-X", "--status", "working")
        r = self.tw("thread", "reseat", "ui", "--session", "s-X", ok=False)
        self.assertIn("already holds the api seat", r.stderr)
        self.assertNotEqual(self.tw("thread", "set", "ui", "--session", "s-X", ok=False).returncode, 0)
        self.assertNotEqual(self.tw("handover", "ui", "--leaving", "nobody", ok=False).returncode, 0)
        self.tw("thread", "add", "pl")
        self.assertIn("You start the pl seat (no earlier session)", self.tw("handover", "pl").stdout)

    def test_round4_revive_lead_starting_leaving(self):
        self.tw("thread", "add", "api", "--session", "s-X", "--status", "working")
        self.tw("thread", "set", "api", "--status", "archived")
        self.tw("thread", "reseat", "ui", "--session", "s-X", "--reason", "r")
        self.assertNotEqual(self.tw("thread", "set", "api", "--status", "working", ok=False).returncode, 0)
        self.tw("lead", "--session", "L1")
        self.assertNotEqual(self.tw("thread", "add", "x2", "--session", "L1", ok=False).returncode, 0)
        self.assertNotEqual(self.tw("lead", "--session", "s-X", "--force", ok=False).returncode, 0)
        self.tw("thread", "add", "st", "--session", "A", "--status", "starting")
        self.assertIn("(A, left: failed to start)", self.tw("handover", "st", "--reason", "failed to start").stdout)
        self.env["TW_NOW"] = "2026-01-01T00:00:00+00:00"
        self.tw("thread", "add", "cy", "--session", "c1", "--status", "working")
        self.tw("thread", "reseat", "cy", "--session", "c2", "--reason", "context full")
        self.env["TW_NOW"] = "2026-01-01T01:00:00+00:00"
        self.tw("thread", "reseat", "cy", "--session", "c1", "--reason", "back")
        self.assertIn("(c1, left: context full)", self.tw("handover", "cy", "--leaving", "c1").stdout)

    def test_round5_lead_revive_and_rebuild_keeps_return(self):
        self.tw("thread", "set", "ui", "--status", "archived")
        self.tw("lead", "--session", "s-old", "--force")
        self.assertNotEqual(self.tw("thread", "set", "ui", "--status", "idle", ok=False).returncode, 0)

    def test_round2_reseat_status_and_log_reason(self):
        for bad in ("planned", "archived", "done"):
            self.assertNotEqual(self.tw("thread", "reseat", "ui", "--session", "s2", "--status", bad,
                                        ok=False).returncode, 0, bad)
        self.assertEqual(self.seat()["session_id"], "s-old")  # nothing changed
        self.tw("thread", "reseat", "ui", "--session", "s2")
        log = (self.dir / ".teamwork" / "log.jsonl").read_text()
        ev = [json.loads(x) for x in log.splitlines() if '"thread-reseat"' in x][-1]
        self.assertEqual(ev["reason"], self.seat()["sessions"][0]["reason"])


class Rebuild(LeasesGit):
    """T13: state.json can be rebuilt from log.jsonl, the session list and branches."""

    test_branch_diff_blocks = None

    def populate(self):
        self.tw("lead", "--session", "lead-1")
        self.tw("thread", "set", "ui", "--session", "s-ui", "--status", "working", "--summary", "step 2")
        self.tw("task", "add", "T1", "--owner", "ui", "--title", "blue button", "--accept", "is blue",
                "--touches", "web/**")
        self.tw("task", "set", "T1", "--status", "working")
        self.tw("req", "add", "make it blue", "--tasks", "T1")
        self.tw("prefs", "--set", "stuck_minutes=15")

    def test_delete_and_rebuild(self):
        self.populate()
        tw_dir = self.proj / ".teamwork"
        before = json.loads((tw_dir / "state.json").read_text())
        (tw_dir / "state.json").unlink()
        out = json.loads(self.tw("rebuild").stdout)
        self.assertFalse((tw_dir / "state.json").exists())  # preview only by default
        self.assertTrue((tw_dir / "state.json.rebuilt").exists())
        self.assertEqual(out["threads"], ["api", "ui"])
        self.assertEqual(out["tasks"], ["T1"])
        self.assertEqual(out["requests"], ["R1"])
        self.tw("rebuild", "--apply")
        after = json.loads((tw_dir / "state.json").read_text())
        for key in ("threads", "tasks", "requests", "lead", "plan_approved"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(after["prefs"]["stuck_minutes"], 15)
        self.assertIn("make it blue", self.tw("board").stdout)

    def test_apply_keeps_backup_and_reports_diff(self):
        self.populate()
        out = json.loads(self.tw("rebuild").stdout)
        self.assertEqual(out["vs_current"]["tasks"], {"missing": [], "extra": [], "changed": {}})
        out = json.loads(self.tw("rebuild", "--apply").stdout)
        self.assertTrue(Path(out["backup"]).exists())

    def test_review_plan_approve_and_delivered_survive(self):
        tw_dir = self.proj / ".teamwork"
        st = json.loads((tw_dir / "state.json").read_text())
        st["plan_approved"] = False
        (tw_dir / "state.json").write_text(json.dumps(st))
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api")
        self.tw("plan", "approve", "--user-said", "ok")
        self.tw("req", "add", "x", "--tasks", "T1")
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        before = json.loads((tw_dir / "state.json").read_text())
        self.assertEqual(before["requests"]["R1"]["status"], "delivered")
        (tw_dir / "state.json").unlink()
        self.tw("rebuild", "--apply")
        after = json.loads((tw_dir / "state.json").read_text())
        self.assertEqual(after["tasks"]["T2"]["status"], before["tasks"]["T2"]["status"])
        self.assertEqual(after["requests"]["R1"]["status"], "delivered")
        self.assertEqual(after.get("roster_version"), before.get("roster_version"))

    def test_review_bad_lines_diff_backups_ignore(self):
        self.populate()
        tw_dir = self.proj / ".teamwork"
        with open(tw_dir / "log.jsonl", "ab") as f:
            f.write(b'42\n[1]\n{"ev": "worktree"}\n\xe4\xb8\n')
        st = json.loads((tw_dir / "state.json").read_text())
        st["tasks"]["T1"]["title"] = "edited by hand"
        (tw_dir / "state.json").write_text(json.dumps(st))
        out = json.loads(self.tw("rebuild").stdout)
        self.assertIn("title", out["vs_current"]["tasks"]["changed"]["T1"])
        self.assertTrue(any("unreadable" in n for n in out["notes"]))
        b1 = json.loads(self.tw("rebuild", "--apply").stdout)["backup"]
        b2 = json.loads(self.tw("rebuild", "--apply").stdout)["backup"]
        self.assertNotEqual(b1, b2)
        ig = (tw_dir / ".gitignore").read_text()
        self.assertIn("state.json.rebuilt", ig)
        self.assertIn("state.json.bak-*", ig)

    def test_sessions_and_branches_fill_gaps(self):
        tw_dir = self.proj / ".teamwork"
        (tw_dir / "log.jsonl").unlink()  # worst case: no log at all
        (tw_dir / "state.json").unlink()
        self.git("branch", "tw/proj/docs")
        sessions = self.proj.parent / "sessions.json"
        sessions.write_text(json.dumps({"data": [
            {"id": "s-old", "tags": ["teamwork:proj", "teamwork-thread:ui"], "created_at": "2026-11-01"},
            {"id": "s-new", "tags": ["teamwork:proj", "teamwork-thread:ui"], "created_at": "2026-11-02",
             "environment_kind": "bridge"},
            {"id": "s-cloud", "tags": ["teamwork:proj", "teamwork-thread:research"],
             "environment_kind": "anthropic_cloud"},
            {"id": "other", "tags": ["teamwork:another", "teamwork-thread:ui"]},
        ]}))
        out = json.loads(self.tw("rebuild", "--project", "proj", "--sessions", str(sessions), "--apply").stdout)
        st = json.loads((tw_dir / "state.json").read_text())
        self.assertEqual(st["threads"]["ui"]["session_id"], "s-new")
        self.assertEqual([h["session_id"] for h in st["threads"]["ui"]["sessions"]], ["s-old", "s-new"])
        self.assertEqual(st["threads"]["research"]["kind"], "cloud")
        self.assertIn("docs", st["threads"])
        self.assertTrue(any("restored from branch" in n for n in out["notes"]))


    def test_round4_session_list_change_bumps_roster(self):
        self.tw("thread", "set", "ui", "--session", "s1", "--status", "working")
        self.tw("thread", "set", "api", "--session", "s2", "--status", "idle")
        self.tw("thread", "set", "all", "--roster-sent")
        f = self.proj.parent / "f.json"
        f.write_text(json.dumps([{"id": "s9", "tags": ["teamwork:proj", "teamwork-thread:api"],
                                  "created_at": "2030-01-01"}]))
        out = json.loads(self.tw("rebuild", "--apply", "--sessions", str(f)).stdout)
        self.assertTrue(any("broadcast ROSTER" in n for n in out["notes"]))
        self.assertIn("ui", json.loads(self.tw("ready").stdout)["roster_not_sent"])

    def test_round6_session_list_never_shares(self):
        self.tw("lead", "--session", "S-lead")
        self.tw("thread", "set", "ui", "--session", "N", "--status", "working")
        f = self.proj.parent / "f.json"
        f.write_text(json.dumps([{"id": "S-lead", "tags": ["teamwork:proj", "teamwork-thread:ui"],
                                  "created_at": "2030-01-01"}]))
        out = json.loads(self.tw("rebuild", "--sessions", str(f)).stdout)
        st = json.loads((self.proj / ".teamwork" / "state.json.rebuilt").read_text())
        self.assertEqual(st["threads"]["ui"]["session_id"], "N")
        self.assertTrue(any("held by the lead" in n for n in out["notes"]))

    def test_round8_closed_stints_survive_rebuild(self):
        self.tw("lead", "--session", "s_LEAD")
        self.tw("thread", "add", "zorblax", "--session", "s_A")
        self.tw("thread", "set", "zorblax", "--status", "archived")
        self.tw("thread", "add", "quixbar", "--session", "s_A")
        self.tw("thread", "add", "blue", "--session", "s_B")
        self.tw("thread", "set", "blue", "--status", "archived")
        self.tw("lead", "--session", "s_B", "--force")
        sp = self.proj / ".teamwork" / "state.json"
        before = json.loads(sp.read_text())["threads"]
        self.assertTrue(all(h.get("to") for h in before["blue"]["sessions"]))  # moved to the lead
        sp.unlink()
        self.tw("rebuild", "--apply")
        after = json.loads(sp.read_text())["threads"]
        for n in ("zorblax", "blue"):
            self.assertEqual([(h["session_id"], bool(h.get("to"))) for h in after[n]["sessions"]],
                             [(h["session_id"], bool(h.get("to"))) for h in before[n]["sessions"]], n)

    def test_round9_retired_session_stays_retired(self):
        self.tw("lead", "--session", "L1")
        self.tw("thread", "add", "zorb", "--session", "S1")
        self.tw("thread", "reseat", "zorb", "--session", "S2", "--reason", "failed")
        f = self.proj.parent / "s.json"
        f.write_text(json.dumps([{"id": "S1", "tags": ["teamwork:proj", "teamwork-thread:zorb"],
                                  "created_at": "2020-01-01T10:00:00Z"},
                                 {"id": "S2", "tags": ["teamwork:proj", "teamwork-thread:zorb"],
                                  "created_at": "2020-01-01T11:00:00Z", "status": "archived"}]))
        self.tw("rebuild", "--sessions", str(f), "--apply")
        z = json.loads((self.proj / ".teamwork" / "state.json").read_text())["threads"]["zorb"]
        self.assertEqual(z["session_id"], "S2")
        self.assertEqual([h["session_id"] for h in z["sessions"] if not h.get("to")], ["S2"])
        # reviving a seat whose stint ended (its session went to a seat that is archived now) is refused
        self.tw("thread", "add", "quix", "--session", "S3")
        self.tw("thread", "set", "quix", "--status", "archived")
        self.tw("thread", "add", "blip", "--session", "S3")
        self.tw("thread", "set", "blip", "--status", "archived")
        self.assertNotEqual(self.tw("thread", "set", "quix", "--status", "idle", ok=False).returncode, 0)
        self.assertNotEqual(self.tw("thread", "set", "quix", "--session", "S3", "--status", "idle",
                                    ok=False).returncode, 0)  # naming its own old session changes nothing

    def test_round_t13_short_name_and_headless_log(self):
        sub = Path(self._tmp.name) / "qz"
        sub.mkdir()
        run = lambda *x: subprocess.run([sys.executable, str(TW), "-C", str(sub), *x], cwd=sub,
                                        capture_output=True, text=True)
        self.assertEqual(run("init", "quixbar-zorblax").returncode, 0)
        run("lead", "--session", "L1")
        run("thread", "add", "ui", "--kind", "cloud", "--session", "S1", "--cloud-reason", "web research")
        run("req", "add", "blue whale star")
        run("prefs", "--set", "language=en")
        tw = sub / ".teamwork"
        # the log loses its head: requests before the next project-carrying event survive
        lines = (tw / "log.jsonl").read_text().splitlines()
        (tw / "log.jsonl").write_text("\n".join(lines[1:]) + "\n")
        (tw / "state.json").unlink()
        r = run("rebuild")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertIn("R1", out["requests"])
        self.assertTrue(any("does not start with init" in n for n in out["notes"]))
        # the whole folder is lost: the session list alone, tagged with the short name, brings the seat back
        import shutil
        shutil.rmtree(tw)
        tw.mkdir()
        ss = sub / "s.json"
        ss.write_text(json.dumps([{"id": "S1", "tags": ["teamwork:QZ", "teamwork-thread:ui"],
                                   "created_at": "2020-01-01T00:00:00Z"}]))
        out = json.loads(run("rebuild", "--project", "quixbar-zorblax", "--sessions", str(ss)).stdout)
        self.assertEqual(out["threads"], ["ui"])
        ss.write_text(json.dumps([{"id": "S1", "tags": ["teamwork:ZB", "teamwork-thread:ui"]}]))
        out = json.loads(run("rebuild", "--project", "quixbar-zorblax", "--sessions", str(ss)).stdout)
        self.assertTrue(any("--short" in n for n in out["notes"]))
        out = json.loads(run("rebuild", "--project", "quixbar-zorblax", "--short", "ZB", "--sessions",
                             str(ss)).stdout)
        self.assertEqual(out["threads"], ["ui"])
        # the real list_sessions shape: wrapped, session_status, has_more
        row = lambda i, st, d: {"id": i, "session_status": st, "created_at": d,
                                "tags": ["teamwork:QZ", "teamwork-thread:ui"]}
        ss.write_text(json.dumps({"ccr": {"data": [row("S1", "SESSION_STATUS_IDLE", "2020-01-01T00:00:00Z"),
                                                   row("S9", "SESSION_STATUS_ARCHIVED", "2021-01-01T00:00:00Z")],
                                          "has_more": False}}))
        r = run("rebuild", "--project", "quixbar-zorblax", "--sessions", str(ss))
        self.assertEqual(json.loads((tw / "state.json.rebuilt").read_text())["threads"]["ui"]["session_id"], "S1",
                         r.stdout)
        ss.write_text(json.dumps({"ccr": {"data": [], "has_more": True}}))
        self.assertNotEqual(run("rebuild", "--project", "quixbar-zorblax", "--sessions", str(ss)).returncode, 0)
        ss.write_text(json.dumps({"rows": [{"id": "S1"}]}))
        out = json.loads(run("rebuild", "--project", "quixbar-zorblax", "--sessions", str(ss)).stdout)
        self.assertTrue(any("none with a teamwork tag" in n for n in out["notes"]))

    def test_t25_rebuild_stays_in_its_project(self):
        """T13-a: the initials are not this project's label once it has its own short name, and a session another
        lead created is not one of this project's threads."""
        sub = Path(self._tmp.name) / "qzb"
        sub.mkdir()
        run = lambda *x: subprocess.run([sys.executable, str(TW), "-C", str(sub), *x], cwd=sub,
                                        capture_output=True, text=True)
        self.assertEqual(run("init", "quixbar-zorblax", "--short", "ZB").returncode, 0)
        run("lead", "--session", "L1")
        ss = sub / "s.json"
        row = lambda i, t, n, parent=None: {"id": i, "tags": [f"teamwork:{t}", f"teamwork-thread:{n}"],
                                            "created_at": "2020-01-01T00:00:00Z",
                                            **({"parent_session_id": parent} if parent else {})}
        ss.write_text(json.dumps([row("S1", "ZB", "ui", "L1"), row("S7", "QZ", "api"),  # QZ: another project
                                  row("S8", "ZB", "db", "L9")]))  # right tag, but another lead made it
        r = run("rebuild", "--sessions", str(ss))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["threads"], ["ui"], out)
        self.assertTrue(any("S8" in n and "never led" in n for n in out["notes"]), out["notes"])
        # a project still on its default initials (QZ): a QZ session is ours only with a second sign
        sub2 = Path(self._tmp.name) / "qzd"
        sub2.mkdir()
        run2 = lambda *x: subprocess.run([sys.executable, str(TW), "-C", str(sub2), *x], cwd=sub2,
                                         capture_output=True, text=True)
        self.assertEqual(run2("init", "quixbar-zorblax").returncode, 0)
        run2("lead", "--session", "L1")
        ss.write_text(json.dumps([row("S7", "QZ", "api"), row("S3", "QZ", "ui", "L1")]))
        out = json.loads(run2("rebuild", "--sessions", str(ss)).stdout)
        self.assertEqual(out["threads"], ["ui"], out)
        self.assertTrue(any("S7" in n and "initials" in n for n in out["notes"]), out["notes"])

    def test_round_t13_pages_corrupt_state_old_short(self):
        sub = Path(self._tmp.name) / "zq"
        sub.mkdir()
        run = lambda *x: subprocess.run([sys.executable, str(TW), "-C", str(sub), *x], cwd=sub,
                                        capture_output=True, text=True)
        run("init", "zorblax-quixbar")
        run("thread", "add", "ui", "--session", "S1")
        run("prefs", "--set", "short_name=ZQX")
        tw = sub / ".teamwork"
        row = lambda i, n, t="ZQ": {"id": i, "tags": [f"teamwork:{t}", f"teamwork-thread:{n}"],
                                    "created_at": "2020-01-01T00:00:00Z"}
        page = lambda rows, more: {"ccr": {"data": rows, "has_more": more}, "meta": {}}
        ss = sub / "s.json"
        # pages as a list, and pages written one after another; an earlier short name (ZQ) still matches
        for text in (json.dumps([page([row("S1", "ui")], True), page([row("S2", "api")], False)]),
                     json.dumps(page([row("S1", "ui")], True)) + "\n" + json.dumps(page([row("S2", "api")], False))):
            ss.write_text(text)
            r = run("rebuild", "--sessions", str(ss))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["threads"], ["api", "ui"])
        ss.write_text(json.dumps([page([row("S1", "ui")], False), page([row("S2", "api")], True)]))
        self.assertNotEqual(run("rebuild", "--sessions", str(ss)).returncode, 0)  # stopped before the end
        # a corrupted state.json: rebuild still previews and applies, with a copy of the broken file
        for bad in ('{"threads": {"ui": ', "[]", "", '{"threads": []}'):
            (tw / "state.json").write_text(bad)
            r = run("rebuild")
            self.assertEqual(r.returncode, 0, (bad, r.stderr))
            self.assertTrue(any("unreadable" in n for n in json.loads(r.stdout)["notes"]))
        r = run("rebuild", "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ui", json.loads((tw / "state.json").read_text())["threads"])

    def test_round7_rebuild_history_is_true(self):
        self.tw("lead", "--session", "L")
        self.tw("thread", "set", "ui", "--session", "A", "--status", "working")
        self.tw("thread", "set", "api", "--session", "B", "--status", "working")
        tag = lambda i, d: {"id": i, "tags": ["teamwork:proj", "teamwork-thread:ui"], "created_at": d}
        f = self.proj.parent / "f.json"
        f.write_text(json.dumps([tag("L", "2020-01-01"), tag("B", "2020-01-02"), tag("A", "2020-01-03"),
                                 tag("A5", "2999-01-01")]))
        self.tw("rebuild", "--sessions", str(f), "--apply")
        hist = [h["session_id"] for h in json.loads((self.proj / ".teamwork" / "state.json").read_text())
                ["threads"]["ui"]["sessions"]]
        self.assertEqual(hist, ["A"])  # not the lead's, not api's, not a newer never-seated one
        self.tw("thread", "set", "api", "--status", "archived")
        self.tw("thread", "add", "c", "--session", "B")
        api = json.loads((self.proj / ".teamwork" / "state.json").read_text())["threads"]["api"]
        self.assertTrue(all(h.get("to") for h in api["sessions"] if h["session_id"] == "B"))

    def test_round2_bad_names_and_files(self):
        tw_dir = self.proj / ".teamwork"
        self.git("branch", "tw/proj/a/b")
        sessions = self.proj.parent / "s2.json"
        sessions.write_text(json.dumps([{"id": "x", "tags": ["teamwork:proj", "teamwork-thread:../evil"]}, 7]))
        out = json.loads(self.tw("rebuild", "--sessions", str(sessions)).stdout)
        self.assertTrue(any("invalid thread name" in n for n in out["notes"]))
        self.assertTrue(any("ignored branch" in n for n in out["notes"]))
        self.assertNotIn("../evil", out["threads"])
        sessions.write_text("{not json")
        r = self.tw("rebuild", "--sessions", str(sessions), ok=False)
        self.assertIn("cannot read --sessions", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_round2_roster_sent_all_and_small_rebuild_guard(self):
        self.populate()
        self.tw("thread", "set", "all", "--roster-sent")
        tw_dir = self.proj / ".teamwork"
        before = json.loads((tw_dir / "state.json").read_text())
        out = json.loads(self.tw("rebuild", "--apply").stdout)
        after = json.loads((tw_dir / "state.json").read_text())
        self.assertEqual({k: v.get("roster_seen") for k, v in after["threads"].items()},
                         {k: v.get("roster_seen") for k, v in before["threads"].items()})
        self.assertEqual(out["vs_current"]["other"], [])
        (tw_dir / "log.jsonl").write_text("")  # log lost: the rebuild would wipe the board
        r = self.tw("rebuild", "--apply", ok=False)
        self.assertIn("refused", r.stdout + r.stderr)
        self.assertEqual(json.loads((tw_dir / "state.json").read_text())["tasks"], after["tasks"])
        self.tw("rebuild", "--apply", "--force")


class ThreadsAreSessions(TwCase):
    """T24: a thread is a create_session session, rc by default; cloud needs a reason; one language all the way down."""

    def raw(self, *args):
        return self.tw(*args, ok=False, walk=False)

    def test_subagent_ids_never_hold_a_seat(self):
        for sid in ("a0aeaf22707df6481", "agent-zorblax", "subagent:quixbar", "toolu_01Quixbar", "task_7"):
            r = self.raw("thread", "add", "ui", "--session", sid)
            self.assertNotEqual(r.returncode, 0, sid)
            self.assertIn("create_session", r.stderr)
            self.assertNotIn("ui", self.state()["threads"])
        self.tw("thread", "add", "ui")
        for act in ("set", "reseat"):
            self.assertNotEqual(self.raw("thread", act, "ui", "--session", "a1b2c3d4e5f6a7b8").returncode, 0, act)
        self.assertNotEqual(self.raw("lead", "--session", "agent-lead").returncode, 0)
        # subagents addressed by name: a thread or role name, or a short agent id, is not a session
        for sid in ("ui", "UI", "zorb", "lead", "reviewer", "agent123", "a0aeaf22707", "Explore", "general-purpose"):
            self.assertNotEqual(self.raw("thread", "add", "zorb", "--session", sid).returncode, 0, sid)
        self.assertNotIn("zorb", self.state()["threads"])
        self.assertNotEqual(self.raw("lead", "--session", "ui").returncode, 0)
        self.assertEqual(self.raw("thread", "set", "ui", "--session", "  session_01Zorblax ").returncode, 0)
        self.assertEqual(self.state()["threads"]["ui"]["session_id"], "session_01Zorblax")  # stored stripped
        self.assertEqual(self.raw("thread", "add", "qa", "--session", "cse_01Quixbar").returncode, 0)

    def test_rc_by_default_cloud_needs_reason(self):
        self.assertEqual(self.state()["threads"].get("x"), None)
        self.tw("thread", "add", "ui")
        self.assertEqual(self.state()["threads"]["ui"]["kind"], "rc")
        for extra in ((), ("--cloud-reason", "   ")):
            r = self.raw("thread", "add", "docs", "--kind", "cloud", *extra)
            self.assertNotEqual(r.returncode, 0, extra)
            self.assertIn("cannot message rc sessions", r.stderr)
        self.assertNotIn("docs", self.state()["threads"])
        self.tw("thread", "add", "docs", "--kind", "cloud", "--cloud-reason", "Quixbar market notes, no files")
        self.assertEqual(self.state()["threads"]["docs"]["cloud_reason"], "Quixbar market notes, no files")
        # while the user edits the plan (planned, never had a session) the kind can change, with the same rules
        self.assertNotEqual(self.raw("thread", "set", "ui", "--kind", "cloud").returncode, 0)
        self.assertNotEqual(self.raw("thread", "set", "ui", "--kind", "cloud", "--cloud-reason", " ").returncode, 0)
        self.assertEqual(self.raw("thread", "set", "ui", "--kind", "cloud", "--cloud-reason", "Quixbar notes").returncode, 0)
        t = self.state()["threads"]["ui"]
        self.assertEqual((t["kind"], t["cloud_reason"], t.get("worktree")), ("cloud", "Quixbar notes", None))
        self.assertNotEqual(self.raw("thread", "set", "ui", "--kind", "rc", "--cloud-reason", "x").returncode, 0)
        self.assertEqual(self.raw("thread", "set", "ui", "--kind", "rc").returncode, 0)
        t = self.state()["threads"]["ui"]
        self.assertEqual(t["kind"], "rc")
        self.assertNotIn("cloud_reason", t)
        self.assertTrue(t.get("worktree"))
        # once it has had a session the kind is fixed
        self.tw("thread", "set", "ui", "--session", "session_01Zorblax", "--status", "working")
        self.assertNotEqual(self.raw("thread", "set", "ui", "--kind", "cloud", "--cloud-reason", "x").returncode, 0)
        self.assertNotEqual(self.raw("thread", "set", "ui", "--cloud-reason", "").returncode, 0)
        self.assertNotEqual(self.raw("thread", "set", "ui", "--kind", "rc").returncode, 0)
        # a NEED-THREAD is recorded once per (thread, name); a repeated STATUS line is refused
        self.tw("ask", "Zorblax search needs its own seat", "--from", "docs", "--need-thread", "search")
        r = self.raw("ask", "Zorblax search needs its own seat", "--from", "docs", "--need-thread", "Search")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("already question 1", r.stderr)
        self.tw("ask", "--answer", "1", "given to ui")
        self.assertIn("(answered)", self.raw("ask", "again", "--from", "docs", "--need-thread", "search").stderr)
        self.tw("ask", "Quixbar index seat", "--from", "ui", "--need-thread", "search")  # another asker is new
        self.assertNotEqual(self.raw("thread", "add", "web", "--kind", "rc", "--cloud-reason", "Quixbar").returncode, 0)
        # a cloud thread asks for a new thread in its STATUS block, never by messaging the lead
        cp = self.tw("worker-prompt", "docs").stdout
        self.assertIn("need_thread:", cp.split("<tw-status>")[1])
        self.assertIn("cannot message the lead", cp)
        self.assertEqual(self.state()["threads"]["ui"]["kind"], "rc")

    def test_threads_cannot_start_sessions_and_language_reaches_subagents(self):
        self.tw("thread", "add", "ui")
        out = json.loads(self.tw("thread", "add", "api").stdout)
        tools = out["create_session"]["extra_allowed_tools"]
        for bad in ("create_session", "interrupt_session", "archive_session"):
            self.assertFalse(any(bad in t for t in tools), bad)
        self.tw("prefs", "--set", "language=zh")
        prompt = self.tw("worker-prompt", "ui").stdout
        self.assertIn("subagents", prompt.split("## Team roster")[0])  # in the language rule itself
        self.assertIn("NEED-THREAD", prompt)
        self.assertIn("subagent -> you -> lead -> user", prompt)


class ReviewRounds(TwCase):
    """T23: review_max_rounds; capped verdict at the limit; one-command follow-ups; rounds in the REVIEW table."""

    def setUp(self):
        super().setUp()
        self.tw("thread", "add", "ui")
        self.tw("task", "add", "T1", "--owner", "ui", "--accept", "Zorblax renders", "--touches", "web/**")
        self.tw("plan", "approve", "--user-said", "ok")
        for st in ("dispatched", "working", "review"):
            self.tw("task", "set", "T1", "--status", st)

    def raw(self, *args):
        return self.tw(*args, ok=False, walk=False)

    def test_t25_done_checks_final_pass_remaining_gate_stale_followups(self):
        ok = lambda *x: self.assertEqual(self.raw(*x).returncode, 0, x)
        no = lambda *x: self.assertNotEqual(self.raw(*x).returncode, 0, x)
        rv = ("task", "set", "T1", "--reviewer", "ui/subagent")
        # T23-b: pass with remaining findings needs 2+ rounds, green checks and no blocking finding
        no(*rv, "--review", "pass", "--remaining", "Quixbar edge", "--checks", "pass", "--no-blocking")  # round 1
        ok(*rv, "--review", "fail")
        ok("task", "set", "T1", "--status", "review")
        no(*rv, "--review", "pass", "--remaining", "Quixbar edge", "--no-blocking")  # checks not green
        no(*rv, "--review", "pass", "--remaining", "Quixbar edge", "--checks", "pass")  # blocking not ruled out
        ok(*rv, "--review", "pass", "--remaining", "Quixbar edge", "--checks", "pass", "--no-blocking")
        ok("task", "followups", "T1", "--apply")
        self.assertIn("T1-f1", self.state()["tasks"])
        # T23-c: the verdict is voided by rework: its follow-up is marked, shown, and the mark survives rebuild
        r = self.raw("task", "set", "T1", "--status", "working")
        self.assertIn("T1-f1", r.stderr)
        self.assertEqual(self.state()["tasks"]["T1-f1"]["parent_verdict_voided"], "rework after review")
        self.assertIn("T1-f1 (verdict voided)", self.raw("task", "reviews", "T1").stdout)
        reb = json.loads((self.dir / ".teamwork" / "state.json").read_text())
        self.raw("rebuild", "--apply")
        self.assertEqual(self.state()["tasks"]["T1-f1"].get("parent_verdict_voided"), "rework after review")
        self.assertEqual(set(self.state()["tasks"]), set(reb["tasks"]))
        # T23-c: a new verdict recorded straight over a pass also voids it (and keeps it in past_reviews) ...
        ok("task", "set", "T1", "--status", "review", "--checks", "pass")
        ok(*rv, "--review", "pass", "--rounds", "3", "--remaining", "Quixbar edge", "--checks", "pass", "--no-blocking")
        self.assertNotIn("parent_verdict_voided", self.state()["tasks"]["T1-f1"])  # same finding again: mark cleared
        ok(*rv, "--review", "fail")
        k = self.state()["tasks"]
        self.assertIn("replaced", k["T1-f1"]["parent_verdict_voided"])
        self.assertEqual(k["T1"]["past_reviews"][-1]["result"], "pass")
        # T23-a: once done, checks are final (a regression is new work), so a capped/pass verdict cannot be voided
        ok("task", "set", "T1", "--status", "review", "--checks", "pass")
        ok(*rv, "--review", "pass")
        ok("task", "set", "T1", "--status", "done")
        no("task", "set", "T1", "--checks", "fail")
        no("task", "set", "T1", "--checks", "none")
        ok("task", "set", "T1", "--checks", "pass")  # unchanged: fine
        self.assertEqual(self.state()["tasks"]["T1"]["review"]["result"], "pass")

    def test_capped_only_at_the_limit_with_green_checks(self):
        ok = lambda *x: self.assertEqual(self.raw(*x).returncode, 0, x)
        no = lambda *x: self.assertNotEqual(self.raw(*x).returncode, 0, x)
        cap = ("task", "set", "T1", "--review", "capped", "--reviewer", "ui/subagent")
        self.tw("req", "add", "Zorblax page", "--tasks", "T1")
        no(*cap, "--no-blocking", "--checks", "pass", "--remaining", "Quixbar edge")  # round 1 of 3
        no("task", "set", "T1", "--review", "fail", "--reviewer", "ui/subagent", "--remaining", "x")  # pass/capped only
        ok("task", "set", "T1", "--review", "fail", "--reviewer", "ui/subagent")  # round 1 -> working
        ok("task", "set", "T1", "--status", "review")
        ok("task", "set", "T1", "--review", "fail", "--reviewer", "ui/subagent")  # round 2
        ok("task", "set", "T1", "--status", "review")
        self.assertEqual(self.state()["tasks"]["T1"]["review_rounds"], 2)
        no(*cap, "--no-blocking", "--remaining", "Quixbar edge")  # checks not recorded
        ok("task", "set", "T1", "--checks", "fail")
        no(*cap, "--no-blocking", "--remaining", "Quixbar edge")  # checks failing
        no(*cap, "--checks", "pass", "--remaining", "Quixbar edge")  # blocking not ruled out
        no(*cap, "--checks", "pass", "--no-blocking")  # nothing remaining: that is a pass
        no(*cap, "--checks", "pass", "--no-blocking", "--remaining", " ; ")
        no("task", "set", "T1", "--review", "capped", "--reviewer", "ui", "--checks", "pass", "--no-blocking",
           "--remaining", "x")  # still the owner rule
        self.assertEqual(self.state()["tasks"]["T1"]["review_rounds"], 2)  # refused attempts are not rounds
        ok(*cap, "--checks", "pass", "--no-blocking", "--remaining", "Quixbar edge case; 蓝鲸星 input")
        k = self.state()["tasks"]["T1"]
        self.assertEqual((k["review_rounds"], k["review"]["round"]), (3, 3))
        self.assertEqual(k["review"]["remaining"], ["Quixbar edge case", "蓝鲸星 input"])
        ok("req", "set", "R1", "--status", "reported")
        ok("task", "set", "T1", "--status", "done")  # capped counts as a pass
        ok("req", "close", "R1", "--user-said", "fine")

    def test_followups_one_command_and_reviews_table(self):
        for bad in ("three", "²", "٣", "-1", "100", " 3", ""):
            self.assertNotEqual(self.raw("prefs", "--set", f"review_max_rounds={bad}").returncode, 0, bad)
        self.assertNotEqual(self.raw("prefs", "--set", "review_max_rounds=1").returncode, 0)  # lower: user's call
        self.tw("prefs", "--set", "review_max_rounds=1", "--user-said", "one round is enough for Zorblax")
        self.assertNotEqual(self.raw("task", "followups", "T1").returncode, 0)  # nothing capped yet
        self.tw("req", "add", "Zorblax page", "--tasks", "T1")
        self.tw("task", "set", "T1", "--review", "capped", "--reviewer", "rev", "--checks", "pass", "--no-blocking",
                "--remaining", "Quixbar edge case; 蓝鲸星 input; Quixbar edge case")  # duplicate dropped
        dry = json.loads(self.tw("task", "followups", "T1").stdout)
        self.assertEqual([t["id"] for t in dry["tasks"]], ["T1-f1", "T1-f2"])
        self.assertNotIn("T1-f1", self.state()["tasks"])  # dry run by default
        self.tw("task", "followups", "T1", "--apply")
        st = self.state()
        f1 = st["tasks"]["T1-f1"]
        self.assertEqual((f1["owner"], f1["followup_of"], f1["from_requests"]), ("ui", "T1", ["R1"]))
        self.assertIn("Quixbar edge case", f1["accept"])
        self.assertEqual(f1["touches"], ["web/**"])
        self.assertEqual(f1["deps"], ["T1"])  # starts from the merged parent
        self.assertNotIn("T1-f1", self.tw("ready").stdout)
        self.assertIn("(R1)", self.tw("board").stdout)  # weak link on the board
        tr = json.loads(self.tw("trace", "R1", "--json").stdout)
        self.assertEqual([f["task"] for f in tr["followups"]], ["T1-f1", "T1-f2"])
        self.assertIn("does not hold R1", self.tw("trace", "R1").stdout)
        self.assertEqual(st["requests"]["R1"]["status"], "delivered")  # follow-ups do not hold the request open
        again = json.loads(self.tw("task", "followups", "T1", "--apply").stdout)
        self.assertEqual(again["tasks"], [])  # idempotent
        table = self.tw("task", "reviews").stdout
        row = next(l for l in table.splitlines() if l.startswith("| T1 |"))
        self.assertEqual(self.tw("task", "reviews", "T1-f1").stdout.strip(), "(no reviews recorded)")
        self.assertIn("capped", row)
        self.assertIn("1/1", row)
        self.assertIn("T1-f1,T1-f2", row)
        # the log replays the follow-ups (rebuild after a lost state.json)
        (self.dir / ".teamwork" / "state.json").unlink()
        self.tw("rebuild")
        rb = json.loads((self.dir / ".teamwork" / "state.json.rebuilt").read_text())
        self.assertEqual(rb["tasks"]["T1-f2"]["followup_of"], "T1")
        self.assertEqual(rb["tasks"]["T1"]["review"]["result"], "capped")

    def test_rounds_come_from_the_thread_and_restart(self):
        ok = lambda *x: self.assertEqual(self.raw(*x).returncode, 0, x)
        no = lambda *x: self.assertNotEqual(self.raw(*x).returncode, 0, x)
        cap = ("task", "set", "T1", "--review", "capped", "--reviewer", "ui/subagent", "--checks", "pass",
               "--no-blocking", "--remaining", "Quixbar | edge")
        for bad in (("task", "add", "--title", "Zorblax"), ("task", "set", "--status", "working"),
                    ("task", "followups"), ("task", "set", "T1", "--rounds", "2"),
                    ("task", "set", "T1", "--review", "pass", "--reviewer", "rev", "--no-blocking"),
                    ("task", "set", "T1", "--review", "pass", "--reviewer", "rev", "--rounds", "0")):
            no(*bad)
        self.assertNotIn("-", self.state()["tasks"])
        # the thread ran 3 rounds and reports capped: the lead records it once, with the thread's count
        ok(*cap, "--rounds", "3")
        self.assertEqual(self.state()["tasks"]["T1"]["review_rounds"], 3)
        self.assertIn("Quixbar \\| edge", self.tw("task", "reviews").stdout)  # table stays intact
        no("task", "set", "T1", "--review", "pass", "--reviewer", "rev", "--rounds", "2")  # only goes up
        # checks leave green: capped no longer stands
        ok("task", "set", "T1", "--checks", "none")
        self.assertNotEqual((self.state()["tasks"]["T1"].get("review") or {}).get("result"), "capped")
        no("task", "set", "T1", "--status", "done")
        ok(*cap)  # round 4 >= 3
        # a new 'done when' restarts the count: capped needs a full set of rounds again
        ok("task", "set", "T1", "--accept", "Zorblax renders in Quixbar mode")
        self.assertNotIn("review_rounds", self.state()["tasks"]["T1"])
        no(*cap)
        ok(*cap, "--rounds", "3")
        # so does a new owner
        self.tw("thread", "add", "api")
        ok("task", "set", "T1", "--owner", "api")
        self.assertNotIn("review_rounds", self.state()["tasks"]["T1"])
        self.assertIsNone(self.state()["tasks"]["T1"].get("review"))
        no("task", "set", "T1", "--review", "capped", "--reviewer", "rev", "--checks", "pass", "--no-blocking",
           "--remaining", "x")

    def test_round2_owner_change_rework_and_pass_with_remaining(self):
        ok = lambda *x: self.assertEqual(self.raw(*x).returncode, 0, x)
        no = lambda *x: self.assertNotEqual(self.raw(*x).returncode, 0, x)
        self.tw("thread", "add", "api")
        # a plain pass survives an owner change (only the round count restarts)
        ok("task", "set", "T1", "--review", "pass", "--reviewer", "rev", "--checks", "pass", "--rounds", "2")
        ok("task", "set", "T1", "--owner", "api")
        k = self.state()["tasks"]["T1"]
        self.assertEqual((k["review"]["result"], k.get("review_rounds")), ("pass", None))
        # rework after review: the green check is for the old code
        ok("task", "set", "T1", "--review", "fail", "--reviewer", "rev", "--rounds", "3")
        self.assertEqual(self.state()["tasks"]["T1"]["checks"], "none")
        ok("task", "set", "T1", "--status", "review")
        no("task", "set", "T1", "--review", "capped", "--reviewer", "rev", "--no-blocking", "--remaining", "Quixbar")
        ok("task", "set", "T1", "--checks", "pass")
        ok("task", "set", "T1", "--status", "working")
        self.assertEqual(self.state()["tasks"]["T1"]["checks"], "none")
        ok("task", "set", "T1", "--status", "review", "--checks", "pass")
        # the 'two rounds, nothing new' pass keeps its open important findings as follow-ups
        ok("task", "set", "T1", "--review", "pass", "--reviewer", "rev", "--rounds", "4", "--remaining", "Zorblax edge",
           "--no-blocking")
        out = json.loads(self.tw("task", "followups", "T1", "--apply").stdout)
        self.assertEqual([t["id"] for t in out["tasks"]], ["T1-f1"])
        self.assertIn("pass", self.tw("task", "reviews", "T1").stdout)
        self.assertNotEqual(self.raw("task", "reviews", "T9").returncode, 0)
        # the limit shown is the one the verdict was recorded under
        ok("task", "add", "T2", "--owner", "ui", "--accept", "Quixbar works")
        for st in ("dispatched", "working", "review"):
            ok("task", "set", "T2", "--status", st)
        ok("task", "set", "T2", "--review", "capped", "--reviewer", "rev", "--rounds", "3", "--checks", "pass",
           "--no-blocking", "--remaining", "蓝鲸星")
        self.tw("prefs", "--set", "review_max_rounds=9")
        self.assertIn("3/3", next(l for l in self.tw("task", "reviews").stdout.splitlines() if l.startswith("| T2 |")))
        # a capped verdict does not survive an owner change
        ok("task", "set", "T2", "--owner", "api")
        self.assertIsNone(self.state()["tasks"]["T2"].get("review"))

    def test_worker_prompt_states_the_limit(self):
        self.tw("prefs", "--set", "review_max_rounds=4")
        p = self.tw("worker-prompt", "ui").stdout
        self.assertIn("**4** rounds", p)
        self.assertIn("capped", p)
        self.assertIn("rounds: N", p)
        self.tw("prefs", "--set", "review_max_rounds=0")
        self.assertIn("does not apply", self.tw("worker-prompt", "ui").stdout)


class FewerPrompts(TwCase):
    """T27: install.py pre-approves scratch folders and always asks for irreversible repo actions; the protocol
    tells every session how not to cause prompts."""

    def install(self, *extra):
        inst = Path(__file__).resolve().parent / "install.py"
        return subprocess.run([sys.executable, str(inst), "--link", "--config-dir", str(self.dir / "cfg"), *extra],
                              capture_output=True, text=True, env={**os.environ, "TMPDIR": str(self.dir / "tmpd")})

    def test_install_dry_run_then_apply(self):
        (self.dir / "tmpd").mkdir()
        cfg = self.dir / "cfg"
        cfg.mkdir()
        (cfg / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash(git *)"],
                                                                      "ask": ["Bash(zorblax-wipe:*)"]}}))
        dry = self.install()
        self.assertEqual(dry.returncode, 0, dry.stderr)
        for want in ("additionalDirectories: adding /tmp", str(self.dir / "tmpd"), "ask: adding Bash(gh repo delete:*)",
                     "Bash(gh repo edit *--visibility*)", "would write"):
            self.assertIn(want, dry.stdout)
        self.assertNotIn("additionalDirectories", (cfg / "settings.json").read_text())  # dry run writes nothing
        proj = self.dir / "zorblax"
        proj.mkdir()
        self.assertEqual(self.install("--apply", "--project", str(proj)).returncode, 0)
        perms = json.loads((cfg / "settings.json").read_text())["permissions"]
        self.assertIn("/tmp", perms["additionalDirectories"])
        self.assertIn(str((self.dir / "tmpd").resolve()), perms["additionalDirectories"])
        self.assertIn(str(proj.resolve().parent / "zorblax-worktrees"), perms["additionalDirectories"])
        self.assertEqual(perms["ask"][0], "Bash(zorblax-wipe:*)")  # the user's own rules stay, first
        self.assertIn("Bash(gh api *-X DELETE*)", perms["ask"])
        self.assertIn("Bash(git *)", perms["allow"])
        self.assertTrue(any(r.startswith("Bash(python3 ") and r.endswith('/scripts/tw.py" *)') for r in perms["allow"]))
        again = self.install("--apply", "--project", str(proj)).stdout
        self.assertIn("ask: nothing to add", again)
        self.assertEqual(json.loads((cfg / "settings.json").read_text())["permissions"], perms)  # idempotent

    def test_ordinary_work_allowed_and_default_mode_opt_in(self):
        """T36 (R44): after install, project edits, tw.py and messages do not ask; the user-level default mode changes
        only with --default-mode, and user-level rules let no big decision through in other sessions."""
        (self.dir / "tmpd").mkdir()
        cfg = self.dir / "cfg"
        proj = self.dir / "zorblax"
        proj.mkdir()
        dry = self.install("--project", str(proj))
        self.assertIn("defaultMode is not set", dry.stdout)
        self.assertEqual(self.install("--apply", "--project", str(proj)).returncode, 0)
        perms = json.loads((cfg / "settings.json").read_text())["permissions"]
        self.assertNotIn("defaultMode", perms)
        pj = str(proj.resolve())
        for want in (f"Edit(/{pj}/**)", f"Write(/{pj}/**)", f"Edit(/{pj}-worktrees/**)",
                     "mcp__claude-code-remote__send_message"):
            self.assertIn(want, perms["allow"])
        self.assertFalse([r for r in perms["allow"] if r.startswith("Bash(git")])  # r2: no git allow at user level
        self.assertTrue(self.rule_hits(perms["ask"], "gh repo delete quixbar/zorblax --yes"))  # still asked
        out = self.install("--apply", "--project", str(proj), "--default-mode", "auto").stdout
        self.assertIn("defaultMode: auto", out)
        self.assertEqual(json.loads((cfg / "settings.json").read_text())["permissions"]["defaultMode"], "auto")

    def test_folder_rules_in_served_project_and_worktrees(self):
        """T36 (R44, R45): RC threads start in the served folder; its settings.local.json, the project's and each
        worktree's get the rule set and the bash-guard hook."""
        (self.dir / "tmpd").mkdir()
        served, proj = self.dir / "served", self.dir / "served" / "zorblax"
        proj.mkdir(parents=True)
        wt = self.dir / "served" / "zorblax-worktrees" / "ui"
        wt.mkdir(parents=True)
        (wt / ".git").write_text("gitdir: /nowhere\n")
        (served / ".claude").mkdir()
        (served / ".claude" / "settings.local.json").write_text(json.dumps(
            {"permissions": {"allow": ["mcp__quixbar__*"], "ask": ["Bash(zorblax-wipe:*)"]}}))
        r = self.install("--apply", "--project", str(proj), "--served", str(served))
        self.assertEqual(r.returncode, 0, r.stderr)
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import install
        rf = "-r" + "f"
        for folder in (served, proj, wt):
            sl = json.loads((folder / ".claude" / "settings.local.json").read_text())
            perms = sl["permissions"]
            self.assertEqual(perms["defaultMode"], "auto")
            self.assertIn(str(proj.resolve()), perms["additionalDirectories"])
            self.assertIn(str(proj.resolve().parent / "zorblax-worktrees"), perms["additionalDirectories"])
            self.assertFalse([d for d in perms["additionalDirectories"] if d == str(Path.home())])
            for tool in ("Bash", "Edit", "Write", "Read", "mcp__claude-code-remote__send_message",
                         "mcp__claude_ai_Claude_Docs__*", "CronCreate", "ScheduleWakeup"):
                self.assertIn(tool, perms["allow"])
            hooks = [h["command"] for g in sl["hooks"]["PreToolUse"] if g.get("matcher") == "Bash" for h in g["hooks"]]
            self.assertTrue(any(c.endswith('tw.py" bash-guard || true') for c in hooks), hooks)  # r3: never exits 2
            for cmd in ("git status", "git -C /tmp/q commit -m x", "ls /tmp", "python3 scripts/tw.py ready"):
                self.assertFalse(self.rule_hits(perms["ask"] + perms["deny"], cmd), cmd)
            for cmd in (f"rm {rf} /", f"rm {rf} ~", f"rm {rf} $HOME", f'rm {rf} "$HOME"'):
                self.assertTrue(self.rule_hits(perms["deny"], cmd), cmd)
            for cmd in (f"rm {rf} /tmp/z", f"rm {rf} ~/proj/tmp"):
                self.assertFalse(self.rule_hits(perms["deny"], cmd), cmd)
            for tool in ("mcp__claude-code-remote__archive_session", "mcp__claude-code-remote__update_environment"):
                self.assertIn(tool, perms["ask"])  # ask beats allow (mcp__claude-code-remote__*)
            self.assertFalse([r for r in perms["ask"] + perms["deny"] if r.startswith("Bash(*")])
        kept = json.loads((served / ".claude" / "settings.local.json").read_text())["permissions"]
        self.assertEqual((kept["allow"][0], kept["ask"][0]), ("mcp__quixbar__*", "Bash(zorblax-wipe:*)"))  # merged
        again = self.install("--apply", "--project", str(proj), "--served", str(served)).stdout
        self.assertIn("PreToolUse hook: already present", again)
        self.assertNotIn("adding", again.split("3.1")[1])  # idempotent

    def test_broken_folder_settings_stop_before_any_write(self):
        (self.dir / "tmpd").mkdir()
        served = self.dir / "served"
        (served / ".claude").mkdir(parents=True)
        for bad in ('{"permissions": {"allow": "Bash"}}', '{"hooks": []}', '{"hooks": {"PreToolUse": ["x"]}}',
                    '{"hooks": {"PreToolUse": [{"hooks": "x"}]}}'):
            (served / ".claude" / "settings.local.json").write_text(bad)
            r = self.install("--apply", "--served", str(served))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("nothing was written", r.stderr + r.stdout)
            self.assertFalse((self.dir / "cfg" / "settings.json").exists())
            self.assertFalse((self.dir / "cfg" / "skills").exists())  # not even the skill link

    def test_superseded_text_rules_listed_then_dropped_on_request(self):
        """r3: older Bash ask rules the hook now judges fire on commit-message words; the dry run lists them and only
        --drop-superseded removes them (a .bak is kept); the user's other rules stay."""
        (self.dir / "tmpd").mkdir()
        served = self.dir / "served"
        (served / ".claude").mkdir(parents=True)
        mine = ["Bash(zorblax-wipe:*)", "Bash(*quixctl *)", "Bash(git status*)"]
        old = ["Bash(rm -r *)", "Bash(git push * " + "--for" + "ce *)", "Bash(git reset --" + "hard*)"]
        (served / ".claude" / "settings.local.json").write_text(json.dumps({"permissions": {"ask": mine + old}}))
        out = self.install("--apply", "--served", str(served)).stdout
        self.assertIn("3 older text rules", out)
        self.assertIn("Bash(*quixctl *) start with a wildcard", out)
        ask = json.loads((served / ".claude" / "settings.local.json").read_text())["permissions"]["ask"]
        self.assertTrue(set(old) <= set(ask))  # kept until asked
        out = self.install("--apply", "--served", str(served), "--drop-superseded").stdout
        self.assertIn("removing 3 rules", out)
        ask = json.loads((served / ".claude" / "settings.local.json").read_text())["permissions"]["ask"]
        self.assertFalse(set(old) & set(ask))
        self.assertEqual(ask[:3], mine)
        self.assertTrue((served / ".claude" / "settings.local.json.bak").exists())

    def test_hook_never_blocks_when_tw_is_missing_and_path_is_absolute(self):
        (self.dir / "tmpd").mkdir()
        served = self.dir / "served"
        served.mkdir()
        r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "install.py"), "--link", "--apply",
                            "--config-dir", "cfg2", "--served", str(served)], capture_output=True, text=True,
                           cwd=self.dir, env={**os.environ, "TMPDIR": str(self.dir / "tmpd")})
        self.assertEqual(r.returncode, 0, r.stderr)
        sl = json.loads((served / ".claude" / "settings.local.json").read_text())
        cmd = sl["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertTrue(Path(cmd.split('"')[1]).is_absolute() and "cfg2" in cmd, cmd)  # works from any folder
        broken = cmd.replace("tw.py", "zorblax-missing.py")
        r = subprocess.run(["sh", "-c", broken], input="{}", capture_output=True, text=True, cwd="/")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    @staticmethod
    def rule_hits(rules, cmd):
        """Rough model of Claude Code's Bash rules: `X:*` is a prefix match, otherwise `*` matches anything."""
        import fnmatch
        hits = []
        for r in rules:
            body = r[len("Bash("):-1]
            if body.endswith(":*") and cmd.startswith(body[:-2]) or fnmatch.fnmatchcase(cmd, body):
                hits.append(r)
        return hits

    def test_ask_rules_catch_the_users_decisions(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import install
        for cmd in ("gh repo delete quixbar/zorblax --yes", "gh repo archive quixbar/zorblax",
                    "gh repo rename zorb -R quixbar/zorblax", "gh repo edit quixbar/zorblax --visibility public",
                    "gh repo edit --visibility=private", "gh api -X DELETE repos/quixbar/zorblax",
                    "gh api repos/quixbar/zorblax -X DELETE", "gh api --method=DELETE repos/quixbar/zorblax",
                    "gh api repos/quixbar/zorblax --method DELETE", "gh api -XDELETE repos/quixbar/zorblax",
                    "gh api -X PATCH repos/quixbar/zorblax -f visibility=public",
                    "gh api repos/quixbar/zorblax -F private=false", "gh api -X PATCH repos/q/z -f default_branch=dev",
                    "gh api -X=DELETE repos/q/z", "gh repo edit quixbar/zorblax --default-branch dev"):
            self.assertTrue(self.rule_hits(install.ASK, cmd), cmd)
        # no git push rules (the host blocks force-push and deletion of main): a thread's routine pushes never ask
        force, short = "--for" + "ce", "-" + "f"  # spelled apart: these shapes trip other plugins' guards (T36)
        for cmd in ("git push -u origin tw/zorblax/core", "git push --force-with-lease origin tw/zorblax/core",
                    f"git push {force} origin tw/zorblax/core", f"git -C /tmp/zorblax push {short} origin tw/zorblax/core",
                    "git push origin main", "gh repo view quixbar/zorblax", "gh api repos/quixbar/zorblax",
                    "git push --follow-tags origin main", "git push origin --delete tw/zorblax/core",
                    f'git commit -m "T9: push {force} after rebase"', "gh pr list"):
            self.assertFalse(self.rule_hits(install.ASK, cmd), cmd)
        self.assertFalse([r for r in install.ASK if "push" in r])
        self.assertIn("Block force pushes", install.HOST_PROTECTION)

    def test_no_allow_and_malformed_settings(self):
        (self.dir / "tmpd").mkdir()
        cfg = self.dir / "cfg"
        cfg.mkdir()
        r = self.install("--apply", "--no-allow")
        self.assertEqual(r.returncode, 0, r.stderr)
        perms = json.loads((cfg / "settings.json").read_text())["permissions"]
        self.assertEqual(set(perms), {"ask"})  # only the safety net
        for bad in (json.dumps({"permissions": []}), json.dumps({"permissions": {"ask": "Bash(rm:*)"}}), "{zorblax"):
            (cfg / "settings.json").write_text(bad)
            r = self.install("--apply")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("fix the file by hand", r.stdout + r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertEqual((cfg / "settings.json").read_text(), bad)  # left as it was

    def test_odd_tmpdir_never_adds_root_or_home(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import install
        for odd in ("//", "/", str(Path.home()), "zorblax/rel"):
            old = os.environ.get("TMPDIR")
            os.environ["TMPDIR"] = odd
            try:
                notes = []
                dirs = install.temp_dirs(notes)
            finally:
                os.environ.pop("TMPDIR") if old is None else os.environ.__setitem__("TMPDIR", old)
            for d in dirs:
                self.assertNotEqual(Path(d).resolve(), Path("/"), odd)
                self.assertNotEqual(Path(d).resolve(), Path.home().resolve(), odd)
            self.assertTrue(notes, odd)

    def test_protocol_and_skill_say_how(self):
        self.tw("thread", "add", "ui")
        p = self.tw("worker-prompt", "ui").stdout
        for want in ("Do not make the user click", "/tmp", "Edit or Write tool", "git -C",
                     "only the user's own words"):
            self.assertIn(want, p)
        skill = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text()
        for want in ("Keep prompts rare", "The user's own decisions", "force-pushing the base branch",
                     "visibility", "Bash(sleep *)", "send_later"):
            self.assertIn(want, skill)


class AuditFollowups(TwCase):
    """T26: framework-audit leftovers (Y-1, Y-3 .. Y-22) are written down where the lead and threads read them."""

    def test_protocol_covers_cloud_handoff_ack_health_and_task_list(self):
        self.tw("thread", "add", "ui", "--kind", "cloud", "--cloud-reason", "web research only")
        p = self.tw("worker-prompt", "ui").stdout
        for want in ("A cloud thread can message only cloud peers",  # Y-1
                     "You received a TASK, FOLLOWUP or HANDOFF",  # Y-20
                     "doubles as a health check",  # Y-17
                     "`T3 working; T5 waiting`"):  # Y-10
            self.assertIn(want, p)

    def test_skill_states_transitions(self):
        skill = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text()
        for want in ("`waiting_on: <thread>` (a peer, not the user) → keep `working`",  # Y-3
                     "TW plan reopen", "TW roster --thread", "TW worktree <t> --dry-run", 'TW log "',  # Y-4
                     "Statuses that follow from events",  # Y-5
                     "as a `TASK` when the work is new to the receiver",  # Y-7
                     "a task you thought of yourself, with `propose_before_start=true`, is proposed",  # Y-9
                     "once it confirms it stopped, `TW thread set <t> --status idle`",  # Y-11
                     "**Intake, sized to the ask.**",  # Y-15
                     "Never `done` from a report",  # Y-18
                     "disconnected for a second sweep in a row"):  # Y-19
            self.assertIn(want, skill)
        self.assertEqual(skill.count('"mcp__claude-code-remote__send_message", "mcp__claude-code-remote__get_session"'), 0)  # Y-12

    def test_report_paths_go_through_working(self):
        """SKILL step 5.3: blocked -> review and review -> blocked are refused; the lead goes through working."""
        skill = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text()
        self.assertIn("A task that cannot move straight to the new status goes to `working` first", skill)
        self.assertIn("The ACK of a cloud thread is its STATUS block", skill)
        self.tw("thread", "add", "ui", "--kind", "cloud", "--cloud-reason", "web research only")
        self.assertIn("your ACK is the STATUS block", self.tw("worker-prompt", "ui").stdout)
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("plan", "approve", "--user-said", "ok")
        for s in ("dispatched", "working", "blocked"):
            self.tw("task", "set", "T1", "--status", s)
        self.assertNotEqual(self.tw("task", "set", "T1", "--status", "review", ok=False).returncode, 0)
        self.tw("task", "set", "T1", "--status", "working")
        self.tw("task", "set", "T1", "--status", "review")
        self.assertNotEqual(self.tw("task", "set", "T1", "--status", "blocked", ok=False).returncode, 0)
        self.tw("task", "set", "T1", "--status", "working")
        self.tw("task", "set", "T1", "--status", "blocked")
        self.assertEqual(self.state()["tasks"]["T1"]["status"], "blocked")

    def test_blank_question_refused(self):
        self.assertNotEqual(self.tw("ask", "   ", ok=False).returncode, 0)  # Y-8
        self.assertNotEqual(self.tw("ask", "--answer", "9", ok=False).returncode, 0)
        self.assertEqual(self.state()["questions"], [])

    def test_loose_thread_mode_warns(self):
        r = self.tw("prefs", "--set", "thread_permission_mode=bypassPermissions", "--user-said", "Quixbar: no checks")
        self.assertIn("more permissive than its creator", r.stderr)  # Y-22
        self.assertNotIn("more permissive", self.tw("prefs", "--set", "thread_permission_mode=acceptEdits",
                                                    "--user-said", "Quixbar: edits only").stderr)


class GuardSafeShapes(unittest.TestCase):
    """T36: nothing the skill tells a session to run, and nothing its scripts run, trips other plugins' guards."""

    # The shapes common safety hooks ask about (recursive force delete, hard reset, forced clean / branch delete /
    # push, checkout over the work tree). Written as regexes, so this file does not match itself.
    G = r"git\s+(?:-C\s+\S+\s+)?"  # `git -C <path> ...` is the protocol's own spelling
    SHAPES = [r"\brm\s+-[a-zA-Z]*[rR][a-zA-Z]*f", r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*[rR]", r"\brm\s+.*-[rR]\b.*\s-f\b",
              r"\brm\s+.*-f\b.*\s-[rR]\b", r"\brm\s+.*--recursive\b.*--force\b", r"\brm\s+.*--force\b.*--recursive\b",
              G + r"reset\s+(.*\s)?--hard", G + r"clean\s+(.*\s)?-[a-zA-Z]*f", G + r"push\s+.*--force([^-]|$)",
              G + r"push(.*\s)?-f[a-zA-Z]*\b", G + r"push\s+(.*\s)?\+\S", G + r"checkout\s+(.*\s)?--\s+\.",
              G + r"branch\s+(.*\s)?-D"]
    SKIP = (".git", ".teamwork", ".claude", "docs/field-tests")  # local settings are rules, not commands; field tests quote what went wrong, word for word

    def test_no_guard_tripping_shapes(self):
        root = Path(__file__).resolve().parent.parent
        hits = []
        paths = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in (".py", ".md", ".txt", ".sh", ".json",
                                                                                  ".yml", ".yaml")
                       and not any(str(p.relative_to(root)).startswith(x) for x in self.SKIP))
        self.assertIn(root / "SKILL.md", paths)
        for path in paths:
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for rx in self.SHAPES:
                    if re.search(rx, line, 0 if "branch" in rx else re.I):
                        hits.append(f"{path.relative_to(root)}:{i}: {line.strip()[:100]}")
        self.assertEqual(hits, [])

    def test_shapes_still_match_what_they_should(self):
        bad = ["rm " + "-rf /tmp/x", "rm -r " + "-f x", "rm " + "-Rf x", "rm " + "-vrf x", "git reset " + "--hard HEAD",
               "git clean " + "-fd", "git clean -xd" + "f", "git branch " + "-D x", "git push origin " + "--for" + "ce",
               "git push " + "-" + "f origin x", "git push -" + "fu origin x", "git push origin " + "+main",
               "git -C /tmp/q push " + "-" + "f origin x", "git -C /tmp/q reset " + "--hard", "git reset HEAD~1 " + "--hard"]
        ok = ["rm -r /tmp/x", "git push --force-with-lease origin x", "git branch -d x", "git switch -C x main",
              "git worktree remove --force /tmp/wt"]
        hit = lambda c: any(re.search(rx, c, 0 if "branch" in rx else re.I) for rx in self.SHAPES)
        self.assertTrue(all(hit(c) for c in bad))
        self.assertFalse([c for c in ok if hit(c)])

    def test_protocol_lists_the_shapes_and_alternatives(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        env = {k: v for k, v in os.environ.items() if k != "TEAMWORK_DIR"}
        subprocess.run([sys.executable, str(TW), "init", "demo", "--goal", "g"], cwd=d, env=env, check=True,
                       capture_output=True)
        subprocess.run([sys.executable, str(TW), "thread", "add", "ui"], cwd=d, env=env, check=True, capture_output=True)
        wp = subprocess.run([sys.executable, str(TW), "worker-prompt", "ui"], cwd=d, env=env, capture_output=True,
                            text=True).stdout
        for want in ("safety hooks", "`git push --force-with-lease`", "`git switch -C", "Never disguise",
                     "this list included"):
            self.assertIn(want, wp)
        skill = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Command shapes that trip guards", skill)
        self.assertIn("never edit the user's plugins or hooks", skill)


class BashGuard(unittest.TestCase):
    """T36: the PreToolUse hook judges each command's argv: big decisions ask, wiping / or ~ is denied, everything
    else (words in messages, heredocs, grep arguments included) passes silently."""

    F, FORCE, HARD, RF = "-" + "f", "--for" + "ce", "--" + "hard", "-r" + "f"

    def judge(self, cmd):
        r = subprocess.run([sys.executable, str(TW), "bash-guard"], input=json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": cmd}}), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] if r.stdout.strip() else None

    def test_ordinary_work_passes(self):
        F, FORCE, HARD = self.F, self.FORCE, self.HARD
        for cmd in ("git status", "git -C /tmp/q commit -m x", "git push -u origin tw/q/ui",
                    "git push --force-with-lease origin tw/q/ui", "git push --force-with-lease=tw/q/ui:abc origin x",
                    "git -C /tmp/q push origin x", "git branch -d tw/q/ui", "git switch -C tw/q/ui origin/main",
                    "git restore --staged quix.py", "git clean -n", "rm zorb.txt", f"rm {F} zorb.txt", "ls /tmp",
                    "python3 scripts/tw.py ready", "launchctl list", "brew install quixbar", "git checkout main",
                    "git stash list", "git worktree list", "mkdir -p /tmp/z && cd /tmp/z && git init -q"):
            self.assertIsNone(self.judge(cmd), cmd)

    def test_t42_no_heredoc_inside_arithmetic_or_expansions(self):
        """`<<` in (( )), $(( )), $[ ], ${ } is a shift or text: the next lines are commands; a quoted `)` does not
        end $( )."""
        HARD = self.HARD
        for cmd in (f"(( x = 1 << 2 ))\ngit reset {HARD}\n2", f"for (( i=1<<2; i<9; i++ )); do :; done\ngit reset {HARD}\n2",
                    f"echo ${{x:-<<EOF}}\ngit reset {HARD}\nEOF}}", f"echo $[ 1 <<2 ]\ngit reset {HARD}\n2",
                    f'echo "$((1<<2))"\ngit reset {HARD}\n2', f"echo $(echo ')' ; git reset {HARD})",
                    f'echo $(echo ")" ; git reset {HARD})', f"echo ${{x:-$(git reset {HARD})}}"):
            self.assertEqual(self.judge(cmd), "ask", cmd)
        self.assertIsNone(self.judge(f"echo $(cat <<EOF\ngit reset {HARD}\nEOF\n)\nls"))  # a real heredoc in $( )

    def test_t42_heredoc_delimiters_read_as_the_shell_does(self):
        HARD = self.HARD
        for cmd in (f'cat <<E"O"F\nbody\nEOF\ngit reset {HARD}', f'cat <<"EO F"\nx\nEO F\ngit reset {HARD}',
                    f"cat << EOF\ngit reset {HARD}", f'cat <<A <<B\na\nA\nb\nB\ngit reset {HARD}',
                    f"cat <<$'EOF'\nbody\nEOF\ngit reset {HARD}\n$EOF", f'cat <<$"EOF"\nb\nEOF\ngit reset {HARD}',
                    f"cat <<$'E\\x4fF'\nhi\nEOF\ngit reset {HARD}\nE\\x4fF", f'cat <<"E\\$F"\nhi\nE$F\ngit reset {HARD}\nE\\$F'):
            self.assertEqual(self.judge(cmd), "ask", cmd)  # after the body, or an unclosed one: judged
        for cmd in (f"cat <<'EOF'\ngit reset {HARD}\nEOF\nls", f"cat <<-EOF\n\tgit reset {HARD}\n\tEOF\nls"):
            self.assertIsNone(self.judge(cmd), cmd)  # inside a closed body: data

    def test_words_in_messages_heredocs_and_greps_pass(self):
        F, FORCE, HARD, RF = self.F, self.FORCE, self.HARD, self.RF
        words = (f"launchctl and DROP TABLE, sudo, shutdown, reboot, pkill, rm {RF} /, git push {FORCE}, push {F}u, "
                 f"+main, :old, reset {HARD}, clean {F}, worktree remove, stash drop, branch -" + "D, checkout -- ., "
                 "restore ., brew uninstall")
        for cmd in (f'git commit -m "T36: {words}"', f'git -C /tmp/zorblax commit -m "fix checkout flow -- handle cart"',
                    f'git -C /tmp/zorblax commit -m "docs: worktree remove step; stash drop; restore .env; push {F}ail"',
                    f'git -C /tmp/zorblax commit -m "retry push with + backoff; reset with {HARD}; clean {F}x"',
                    f'git -C /tmp/zorblax log --grep "use branch -" + "D sparingly"', f"grep -n '{words}' notes.md",
                    f"cat > /tmp/zorblax.md <<'EOF'\n{words}\nrm {RF} ~\nEOF\necho done",
                    f'python3 scripts/tw.py ask "{words}"', f"echo 'git push {FORCE}' | wc -c"):
            self.assertIsNone(self.judge(cmd), cmd)

    def test_big_decisions_ask_in_every_spelling(self):
        F, FORCE, HARD, RF = self.F, self.FORCE, self.HARD, self.RF
        for cmd in (f"git push {FORCE} origin main", f"git push origin main {FORCE}", f"git push {F} origin x",
                    f"git push {F}u origin x", "git push origin " + "+main", "git push origin :old",
                    "git push origin --delete old", "git push --mirror", f"git -C /tmp/q push {F} origin x",
                    f"git -c core.x=y -C /tmp/q push origin x {FORCE}", f"git reset {HARD} HEAD~1",
                    f"git reset HEAD~1 {HARD}", f"git -c a=b reset {HARD}", "git clean -xd" + "f",
                    f"git clean -d {F}", "git branch -" + "D x", "git branch --delete " + FORCE + " x",
                    "git branch " + F + " main HEAD~3", "git checkout .", "git checkout -- quix.py",
                    f"git checkout {F} main", "git switch --discard-changes main", "git restore quix.py",
                    "git restore .", "git stash drop", "git -C /tmp/q stash clear", "git worktree remove /tmp/w",
                    "rm -r /tmp/z", f"rm {RF} /tmp/z", "rm -R /tmp/z", "rm -vr" + "f /tmp/z", "/bin/rm -r /tmp/z",
                    "rm --recursive " + FORCE + " /tmp/z", "find /tmp/z -delete", "sudo ls", "shutdown -h now",
                    "launchctl unload x.plist", "launchctl kickstart -k system/x", "killall quixbar", "pkill zorb",
                    "diskutil eraseDisk x", "brew uninstall quixbar", "brew services restart quixbar",
                    "npm uninstall -g quixbar", "psql -c 'DROP TABLE zorb'", "gh repo delete quixbar/zorblax",
                    "gh repo edit q/z --visibility public", "gh api -X DELETE repos/q/z",
                    "cd /tmp/q && git push origin x " + FORCE, "git status; git reset " + HARD,
                    "X=1 git push " + FORCE):
            self.assertEqual(self.judge(cmd), "ask", cmd)

    def test_wiping_root_or_home_is_denied(self):
        RF, F = self.RF, self.F
        for cmd in (f"rm {RF} /", f"rm {RF} /*", f"rm {RF} ~", f"rm {RF} ~/", f"rm {RF} ~/*", f'rm {RF} "$HOME"',
                    f"rm {RF} $HOME", "rm -R" + F[1] + " ${HOME}", f"rm -r {F} /", f"sudo rm {RF} /",
                    f"rm {RF} {Path.home()}"):
            self.assertEqual(self.judge(cmd), "deny", cmd)
        self.assertEqual(self.judge(f"rm {RF} ~/proj/tmp"), "ask")

    def test_r3_spellings(self):
        F, FORCE, HARD, RF = self.F, self.FORCE, self.HARD, self.RF
        for cmd in (f"if [ -d q ]; then rm {RF} q; fi", f"for f in a b; do rm {RF} $f; done", f'bash -c "rm {RF} q"',
                    "sh -lc 'git clean -xd" + "f'", f'eval "git reset {HARD}"', "x=`git clean -d" + F + "`",
                    f"echo $(git reset {HARD})", f"timeout 5 git reset {HARD}", f"nice -n 5 git reset {HARD}",
                    f"env -i FOO=1 git reset {HARD}", f"git push \\\n{FORCE} origin q", f"rm \\\n{RF} q",
                    "find . -execdir rm {} \\;", f"xargs -n 1 rm {RF}", f"sudo -u root git reset {HARD}",
                    f"{{ git reset {HARD}; }}", "git push origin " + "+HEAD:main", f"ls 2>&1 | head; git reset {HARD}"):
            self.assertEqual(self.judge(cmd), "ask", cmd)
        self.assertEqual(self.judge(f'bash -c "rm {RF} ~"'), "deny")
        for cmd in (f'git commit -m "T9: tidy guard\nrm {RF} build was slow"', "git commit -m 'subject\nsudo is not it'",
                    f'python3 tw.py ask "two lines\ngit clean -d{F} here?" --from ui',
                    'gh pr create --body "notes:\ngit branch -' + 'D old"', "find . -name '*.py' -exec grep -l rm {} +",
                    "ls &> /tmp/q", f"cat <<-EOF\n\tgit reset {HARD}\n\tEOF", f'echo "a <<EOF b"', "ls # rm -r q",
                    f"git commit -m \"$(cat <<'EOF'\nreset {HARD} and rm {RF} notes\nEOF\n)\""):
            self.assertIsNone(self.judge(cmd), cmd)
        self.assertEqual(self.judge(f'echo "a <<EOF b"\ngit reset {HARD}'), "ask")
        sys.path.insert(0, str(TW.parent))
        import tw
        t = time.time()
        tw.big_decision("echo " + "a" * 2_000_000)
        self.assertLess(time.time() - t, 3)

    def test_other_tools_and_bad_input_pass(self):
        for raw in ("[]", '"x"', '{"tool_input": "x"}', '{"tool_input": {"command": ["a"]}}', "", "null",
                    '{"tool_input": {"command": null}}'):
            r = subprocess.run([sys.executable, str(TW), "bash-guard"], input=raw, capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout), (0, ""), raw)
        r = subprocess.run([sys.executable, str(TW), "bash-guard"], input=b"\xff\xfe{", capture_output=True)
        self.assertEqual((r.returncode, r.stdout), (0, b""))
        r = subprocess.run([sys.executable, str(TW), "bash-guard"], input="not json", capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        r = subprocess.run([sys.executable, str(TW), "bash-guard"], input=json.dumps(
            {"tool_name": "Edit", "tool_input": {"command": "sudo x"}}), capture_output=True, text=True)
        self.assertEqual(r.stdout, "")
        self.assertIsNone(self.judge('git commit -m "unbalanced'))


class WorktreeSettings(LeasesGit):
    """T36: a new worktree gets the project's local permission rules, kept out of git."""

    test_branch_diff_blocks = None

    def test_worktree_copies_local_settings(self):
        (self.proj / ".claude").mkdir()
        (self.proj / ".claude" / "settings.local.json").write_text('{"permissions": {"allow": ["Bash"]}}')
        self.tw("thread", "add", "zorb")
        out = json.loads(self.tw("worktree", "zorb").stdout)
        wt = Path(out["worktree"])
        self.addCleanup(lambda: __import__("shutil").rmtree(wt.parent, True))
        self.assertEqual(json.loads((wt / ".claude" / "settings.local.json").read_text())["permissions"]["allow"],
                         ["Bash"])
        self.assertEqual(self.git("-C", str(wt), "status", "--porcelain").stdout.strip(), "")  # not shown to git


class PermissionModes(TwCase):
    """T36 (R44): threads get auto all the way down; a lead below it is reported, never a silent downgrade."""

    def ready(self):
        return json.loads(self.tw("ready").stdout)

    def test_default_is_auto_and_old_empty_pref_too(self):
        self.assertEqual(self.state()["prefs"]["thread_permission_mode"], "auto")
        st = self.state()
        st["prefs"]["thread_permission_mode"] = ""  # a project made before T36
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st))
        out = json.loads(self.tw("thread", "add", "ui").stdout)
        self.assertEqual(out["create_session"]["permission_mode"], "auto")

    def test_lead_below_auto_is_told_with_affected_threads(self):
        self.tw("thread", "add", "ui", "--session", "session_zorblax1")
        out = json.loads(self.tw("lead", "--mode", "default").stdout)
        self.assertIn("Tell the user", out["permission_warning"])
        self.assertIn("Switch this session to auto", out["permission_warning"])
        self.assertIn("Threads affected: ui", out["permission_warning"])
        add = json.loads(self.tw("thread", "add", "api").stdout)["create_session"]
        self.assertEqual(add["permission_mode"], "auto")  # still asked for: create_session refuses out loud
        self.assertIn("Tell the user", add["permission_warning"])
        self.assertIn("Tell the user", self.ready()["permission_warning"])
        self.assertIn("permissions:", self.context())
        out = json.loads(self.tw("lead", "--mode", "auto").stdout)
        self.assertNotIn("permission_warning", out)
        self.assertNotIn("permission_warning", self.ready())

    def test_unknown_lead_mode_asks_for_a_self_check(self):
        self.assertIn("lead --mode", self.ready()["permission_warning"])

    def test_threads_running_below_are_listed(self):
        self.tw("lead", "--mode", "auto")
        self.tw("thread", "add", "ui", "--session", "session_quixbar1", "--mode", "default")
        self.assertIn("ui", self.ready()["permission_warning"])
        self.tw("thread", "set", "ui", "--mode", "auto")
        self.assertNotIn("permission_warning", self.ready())

    def test_tighter_thread_mode_needs_the_users_words(self):
        r = self.tw("prefs", "--set", "thread_permission_mode=default", ok=False)
        self.assertIn("--user-said", r.stderr)
        self.tw("prefs", "--set", "thread_permission_mode=default", "--user-said", "Zorblax: ask me")
        self.assertEqual(self.state()["prefs"]["thread_permission_mode"], "default")

    def test_handover_names_the_mode(self):
        self.tw("thread", "add", "ui", "--session", "session_zorblax2")
        self.assertIn("permission_mode='auto'", self.tw("handover", "ui").stderr)

    def test_standing_grants_need_the_users_words(self):
        self.assertIn("--user-said", self.tw("prefs", "--set", "standing_grants=merge", ok=False).stderr)
        self.assertIn("not delete", self.tw("prefs", "--set", "standing_grants=delete", "--user-said", "x",
                                            ok=False).stderr)
        self.tw("prefs", "--set", "standing_grants=merge,archive", "--user-said", "Quixbar: merge and archive")
        self.assertEqual(self.state()["prefs"]["standing_grants"], ["merge", "archive"])
        self.tw("prefs", "--set", "standing_grants=merge")  # narrowing needs no words
        self.assertEqual(self.state()["prefs"]["standing_grants"], ["merge"])


class ThreadBranchAndPrivateScratch(TwCase):
    """T32: `thread set --branch` no longer exits 0 while changing nothing; private scratch has a home."""

    def branch(self, t="db"):
        return self.state()["threads"][t]["branch"]

    def test_set_branch_while_planned(self):
        self.tw("thread", "add", "db")
        v = self.state().get("roster_version", 0)
        self.tw("thread", "set", "db", "--branch", "quix/zorblax")
        self.assertEqual(self.branch(), "quix/zorblax")
        self.assertGreater(self.state().get("roster_version", 0), v)

    def test_set_branch_refused_once_seated(self):
        self.tw("thread", "add", "db", "--session", "session_01Zorblax")
        r = self.tw("thread", "set", "db", "--branch", "quix/zorblax", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("branch is fixed", r.stderr)
        self.assertEqual(self.branch(), "tw/demo/db")
        self.assertNotEqual(self.tw("thread", "reseat", "db", "--session", "session_01Quixbar", "--branch", "x/y",
                                    ok=False).returncode, 0)

    def test_branch_names_checked_and_not_shared(self):
        self.tw("thread", "add", "db")
        for bad in ("-x", "a..b", "a b", "x/", "x.lock", ""):
            self.assertNotEqual(self.tw("thread", "set", "db", "--branch", bad, ok=False).returncode, 0, bad)
        self.assertNotEqual(self.tw("thread", "add", "ui", "--branch", "tw/demo/db", ok=False).returncode, 0)
        self.tw("thread", "add", "ui")
        self.assertNotEqual(self.tw("thread", "set", "ui", "--branch", "tw/demo/db", ok=False).returncode, 0)
        self.assertEqual(self.branch(), "tw/demo/db")

    def test_branch_clashes_and_bad_names(self):
        self.tw("thread", "add", "db")
        self.tw("thread", "add", "web")
        for bad in ("tw/demo/db", "TW/DEMO/DB", "tw/demo/db/x", "tw", "main", "origin/main", "HEAD", "a.lock/b",
                    "a;b", "x/.y"):
            r = self.tw("thread", "set", "web", "--branch", bad, ok=False)
            self.assertNotEqual(r.returncode, 0, bad)
        # ui's default name, before ui exists: refused (T38 M1), so ui can still be added later
        self.assertNotEqual(self.tw("thread", "set", "web", "--branch", "tw/demo/ui", ok=False).returncode, 0)
        self.tw("thread", "add", "ui")
        self.assertNotEqual(self.tw("thread", "add", "q3", "--branch", "main", ok=False).returncode, 0)
        self.tw("thread", "add", "s1", "--session", "session_01Zorblax")
        self.tw("thread", "set", "s1", "--branch", "tw/demo/s1")  # its own branch again: a no-op, allowed

    def test_branch_fixed_once_worktree_exists(self):
        subprocess.run(["git", "-C", str(self.dir), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(self.dir), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-q", "--allow-empty", "-m", "init"], check=True)
        self.tw("thread", "add", "db")
        self.addCleanup(shutil.rmtree, str(Path(self.state()["threads"]["db"]["worktree"]).parent), True)
        self.tw("worktree", "db")
        r = self.tw("thread", "set", "db", "--branch", "quix/zorblax", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("worktree", r.stderr)
        st = self.state()
        st["threads"]["db"]["branch"] = "quix/zorblax"  # a ledger edited by hand
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st))
        r = self.tw("worktree", "db", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("is on tw/demo/db", r.stderr)

    def test_t32_leftovers_namespace_base_remotes_missing_field(self):
        """T38: M1 tw/<project>/<other>, M3 tw/<project> itself, M2 inside the base and other remotes, M6 no field."""
        self.tw("thread", "add", "db")
        for bad in ("tw/demo/zorb", "tw/demo/web/x", "tw/demo", "TW/Demo", "main/x"):
            r = self.tw("thread", "set", "db", "--branch", bad, ok=False)
            self.assertNotEqual(r.returncode, 0, bad)
        self.tw("thread", "set", "db", "--branch", "tw/demo/db")  # its own default name is fine
        self.tw("thread", "set", "db", "--branch", "quix/db")
        subprocess.run(["git", "-C", str(self.dir), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(self.dir), "remote", "add", "upstream", "https://example.invalid/x.git"],
                       check=True)
        self.assertNotEqual(self.tw("thread", "set", "db", "--branch", "upstream/main", ok=False).returncode, 0)
        self.assertNotEqual(self.tw("thread", "set", "db", "--branch", "Upstream/x", ok=False).returncode, 0)
        subprocess.run(["git", "-C", str(self.dir), "remote", "add", "tw", "https://example.invalid/y.git"], check=True)
        self.tw("thread", "add", "ui")  # a remote named tw does not block the default branches
        stf = self.dir / ".teamwork" / "state.json"
        st = json.loads(stf.read_text())
        del st["threads"]["db"]["branch"]  # a ledger edited by hand
        stf.write_text(json.dumps(st))
        self.assertIn("tw/demo/db", self.tw("worker-prompt", "db").stdout)  # falls back to the default name
        self.tw("thread", "set", "db", "--branch", "quix/db2")
        self.assertEqual(self.branch(), "quix/db2")

    def test_t32_leftovers_detached_head_and_orphaned_branch(self):
        """T38: M4 a detached worktree is refused; M5 a removed worktree's branch is not orphaned by a rename."""
        subprocess.run(["git", "-C", str(self.dir), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(self.dir), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-q", "--allow-empty", "-m", "init"], check=True)
        self.tw("thread", "add", "web")
        wt = self.state()["threads"]["web"]["worktree"]
        self.addCleanup(shutil.rmtree, str(Path(wt).parent), True)
        self.tw("worktree", "web")
        subprocess.run(["git", "-C", wt, "checkout", "-q", "--detach"], check=True)
        r = self.tw("worktree", "web", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("detached", r.stderr)
        subprocess.run(["git", "-C", str(self.dir), "worktree", "remove", wt], check=True)
        r = self.tw("thread", "set", "web", "--branch", "quix/web", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("orphan", r.stderr)
        self.assertEqual(self.branch("web"), "tw/demo/web")
        self.tw("thread", "set", "web", "--session", "session_01Zorblax")
        r = self.tw("thread", "set", "web", "--branch", "quix/web", ok=False)
        self.assertIn("planned", r.stderr)  # a seated thread hears the real reason

    def test_private_scratch_is_ignored_and_documented(self):
        self.tw("thread", "add", "ui")
        p = self.tw("worker-prompt", "ui").stdout
        self.assertIn("**Private scratch never goes to `/tmp`**", p)
        self.assertIn(f"{self.state()['repo']['path']}/.teamwork/private/ui/tmp/", p)
        self.assertIn("never a `.teamwork/` inside your worktree", p)
        subprocess.run(["git", "-C", str(self.dir), "init", "-q"], check=True)
        f = self.dir / ".teamwork" / "private" / "ui" / "tmp" / "zorblax.txt"
        f.parent.mkdir(parents=True)
        f.write_text("x")
        r = subprocess.run(["git", "-C", str(self.dir), "check-ignore", "-q", str(f)])
        self.assertEqual(r.returncode, 0)  # ignored by .teamwork/.gitignore from init


class Preconditions(TwCase):
    """T20 / framework I28: illegal transitions are rejected in code, not stored."""

    def setUp(self):
        super().setUp()
        self.tw("thread", "add", "ui")
        self.tw("task", "add", "T1", "--owner", "ui")

    def status(self, t="T1"):
        return self.state()["tasks"][t]["status"]

    def raw(self, *args):
        return self.tw(*args, ok=False, walk=False)

    def test_task_machine(self):
        r = self.raw("task", "set", "T1", "--status", "working")  # planned -> working skips steps
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("can move to approved, dropped", r.stderr)
        self.assertEqual(self.status(), "planned")  # rejected, not stored
        self.assertIn("plan approve", self.raw("task", "set", "T1", "--status", "approved").stderr)
        self.tw("plan", "approve", "--user-said", "ok")
        for s in ("dispatched", "working"):
            self.assertEqual(self.raw("task", "set", "T1", "--status", s).returncode, 0, s)
        r = self.raw("task", "set", "T1", "--status", "done", "--review", "pass", "--reviewer", "rev")
        self.assertIn("done only from review", r.stderr)
        self.assertEqual(self.raw("task", "set", "T1", "--status", "review").returncode, 0)
        self.assertEqual(self.raw("task", "set", "T1", "--accept", "x", "--status", "done", "--review", "pass",
                                  "--reviewer", "rev").returncode, 0)
        self.assertNotEqual(self.raw("task", "set", "T1", "--status", "working").returncode, 0)  # done is final

    def test_recall_and_drop(self):
        self.tw("plan", "approve", "--user-said", "ok")
        for s in ("dispatched", "recalled", "dispatched", "working", "recalled"):
            self.assertEqual(self.raw("task", "set", "T1", "--status", s).returncode, 0, s)
        self.assertIn("user's decision", self.raw("task", "set", "T1", "--status", "dropped").stderr)
        self.assertEqual(self.raw("task", "set", "T1", "--status", "dropped", "--user-said", "skip it").returncode, 0)
        self.tw("task", "add", "T2", "--owner", "ui")
        for s in ("dispatched", "working"):
            self.raw("task", "set", "T2", "--status", s)
        self.assertNotEqual(self.raw("task", "set", "T2", "--status", "dropped").returncode, 0)  # recall first

    def test_request_machine(self):
        self.tw("req", "add", "answer me")
        self.assertEqual(self.raw("req", "set", "R1", "--status", "reported").returncode, 0)  # answered in place
        self.tw("req", "add", "plan first")
        self.raw("req", "set", "R2", "--status", "proposed")
        self.raw("req", "link", "R2", "T1")  # proposed stays proposed until the user says yes
        r = self.raw("req", "set", "R2", "--status", "dispatched")
        self.assertIn("user's decision", r.stderr)
        self.assertEqual(self.raw("req", "set", "R2", "--status", "dispatched", "--user-said", "yes go").returncode, 0)
        self.assertEqual(self.state()["requests"]["R2"]["user_said"][0]["text"], "yes go")
        self.assertNotEqual(self.raw("req", "set", "R2", "--status", "reported").returncode, 0)  # not delivered
        self.assertNotEqual(self.raw("req", "set", "R2", "--status", "delivered").returncode, 0)  # no tasks done
        self.tw("req", "add", "x")
        self.tw("req", "drop", "R3", "--user-said", "never mind")
        self.assertNotEqual(self.raw("req", "link", "R3", "T1").returncode, 0)  # ended


    def test_round1_stale_review_deps_undeliver(self):
        self.tw("plan", "approve", "--user-said", "ok")
        self.assertNotEqual(self.raw("task", "set", "T1", "--review", "pass", "--reviewer", "rev").returncode, 0)
        for s in ("dispatched", "working", "review"):
            self.raw("task", "set", "T1", "--status", s)
        self.tw("req", "add", "x", "--tasks", "T1")
        self.assertEqual(self.state()["requests"]["R1"]["status"], "delivered")
        self.raw("task", "set", "T1", "--review", "pass", "--reviewer", "rev")
        self.raw("task", "set", "T1", "--status", "working")  # rework after the pass
        self.assertEqual(self.state()["requests"]["R1"]["status"], "dispatched")  # no longer delivered
        self.raw("task", "set", "T1", "--status", "review")
        self.assertNotEqual(self.raw("task", "set", "T1", "--status", "done").returncode, 0)  # old pass is void
        self.raw("task", "set", "T1", "--review", "pass", "--reviewer", "api")
        self.tw("thread", "add", "api")
        self.raw("task", "set", "T1", "--owner", "api")  # the reviewer now owns it
        self.assertNotEqual(self.raw("task", "set", "T1", "--status", "done").returncode, 0)
        self.assertNotEqual(self.raw("task", "set", "T1", "--review", "pass", "--reviewer", "api thread").returncode, 0)
        self.tw("task", "add", "T2", "--owner", "ui", "--deps", "T1")
        r = self.raw("task", "set", "T2", "--status", "dispatched")
        self.assertIn("waits for T1", r.stderr)
        self.tw("req", "add", "y")
        self.raw("req", "set", "R2", "--status", "reported")
        self.assertNotEqual(self.raw("req", "close", "R2", "--user-said", "  ").returncode, 0)  # blank words


    def test_round8_hyphen_owner_and_stale_report(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        for rv in ("research-2-thread", "the research-2", "research-2:self", "Research-2"):
            self.assertFalse(tw.reviewer_independent(rv, "research-2"), rv)
        for rv in ("research-20", "research", "research-2/subagent", "zorblax"):
            self.assertTrue(tw.reviewer_independent(rv, "research-2"), rv)
        self.tw("plan", "approve", "--user-said", "ok")
        for s in ("dispatched", "working", "review"):
            self.raw("task", "set", "T1", "--status", s)
        self.tw("req", "add", "quixbar", "--tasks", "T1")
        self.raw("req", "set", "R1", "--status", "reported")
        self.raw("task", "set", "T1", "--status", "working")  # review failed after the report
        self.assertNotEqual(self.raw("req", "close", "R1", "--user-said", "fine").returncode, 0)
        self.assertEqual(self.state()["requests"]["R1"]["status"], "dispatched")  # the report is stale
        self.raw("task", "set", "T1", "--status", "review")
        self.assertEqual(self.state()["requests"]["R1"]["status"], "delivered")  # report the new result first
        self.assertNotEqual(self.raw("req", "close", "R1", "--user-said", "fine").returncode, 0)
        self.assertNotEqual(self.raw("task", "set", "T1", "--status", "done", "--review", "pass",
                                     "--reviewer", "rev").returncode, 0)  # no 'done when' to check against
        # an earlier owner wrote part of it: never its reviewer
        self.tw("thread", "add", "zorblax")
        self.tw("task", "set", "T1", "--accept", "Quixbar works", "--owner", "zorblax")
        for rv in ("ui", "ui-thread"):
            self.assertNotEqual(self.raw("task", "set", "T1", "--review", "pass", "--reviewer", rv,
                                         "--status", "done").returncode, 0, rv)
        self.assertEqual(self.raw("task", "set", "T1", "--review", "pass", "--reviewer", "ui/subagent",
                                  "--status", "done").returncode, 0)

    def test_round11_link_reopens_delivered_and_ownerless_review(self):
        ok = lambda *x: self.assertEqual(self.raw(*x).returncode, 0, x)
        no = lambda *x: self.assertNotEqual(self.raw(*x).returncode, 0, x)
        req = lambda r="R1": self.state()["requests"][r]["status"]
        self.tw("thread", "add", "qa")
        ok("task", "set", "T1", "--accept", "Quixbar works")
        ok("task", "add", "T2", "--owner", "qa", "--accept", "Zorblax works")
        ok("plan", "approve", "--user-said", "ok")
        for st in ("dispatched", "working", "review"):
            ok("task", "set", "T1", "--status", st)
        ok("task", "set", "T1", "--review", "pass", "--reviewer", "rev")
        ok("task", "set", "T2", "--status", "dispatched")
        ok("req", "add", "zorblax", "--tasks", "T1")
        self.assertEqual(req(), "delivered")
        ok("req", "link", "R1", "T2")  # unfinished work joins: the delivery no longer holds
        self.assertEqual(req(), "dispatched")
        no("req", "set", "R1", "--status", "reported")
        no("req", "close", "R1", "--user-said", "fine")
        self.assertEqual(req(), "dispatched")
        # no owner: a review cannot be recorded
        ok("task", "set", "T1", "--owner", "")
        no("task", "set", "T1", "--review", "pass", "--reviewer", "rev")

    def test_round9_each_gate_pinned(self):
        ok = lambda *x: self.assertEqual(self.raw(*x).returncode, 0, x)
        no = lambda *x: self.assertNotEqual(self.raw(*x).returncode, 0, x)
        no("plan", "approve")  # the user's words are needed
        ok("plan", "approve", "--user-said", "ok")
        self.tw("thread", "add", "qa")
        for st in ("dispatched", "working", "review"):
            ok("task", "set", "T1", "--status", st)
        ok("req", "add", "zorblax", "--tasks", "T1")
        ok("req", "set", "R1", "--status", "reported")
        ok("task", "set", "T1", "--review", "pass", "--reviewer", "qa")
        no("req", "close", "R1", "--user-said", "fine")  # passed against an empty 'done when'
        ok("task", "set", "T1", "--accept", "Quixbar works", "--review", "pass", "--reviewer", "qa")
        ok("task", "set", "T1", "--checks", "fail")
        no("req", "close", "R1", "--user-said", "fine")  # checks failing
        ok("task", "set", "T1", "--checks", "pass")
        ok("task", "set", "T1", "--owner", "qa")  # the reviewer became the owner: the pass is void
        self.assertNotEqual((self.state()["tasks"]["T1"].get("review") or {}).get("result"), "pass")
        no("req", "close", "R1", "--user-said", "fine")
        no("task", "set", "T1", "--status", "done")
        no("task", "set", "T1", "--review", "pass", "--reviewer", "ui")  # an earlier owner cannot review
        ok("task", "set", "T1", "--review", "pass", "--reviewer", "rev")
        ok("task", "set", "T1", "--status", "working")  # rework voids the pass
        ok("task", "set", "T1", "--status", "review")
        self.assertNotEqual((self.state()["tasks"]["T1"].get("review") or {}).get("result"), "pass")
        no("task", "set", "T1", "--status", "done")
        ok("task", "set", "T1", "--review", "fail", "--reviewer", "rev")
        ok("task", "set", "T1", "--status", "review")
        ok("req", "set", "R1", "--status", "reported")
        no("req", "close", "R1", "--user-said", "fine")  # a recorded fail
        ok("req", "add", "quixbar question")
        no("req", "close", "R2", "--user-said", "fine")  # never reported
        # an owner cleared after the pass: neither close nor done
        ok("task", "set", "T1", "--review", "pass", "--reviewer", "rev")
        ok("task", "set", "T1", "--owner", "")
        no("req", "close", "R1", "--user-said", "fine")
        no("task", "set", "T1", "--status", "done")
        ok("task", "set", "T1", "--owner", "qa")
        ok("task", "set", "T1", "--review", "pass", "--reviewer", "rev")
        no("task", "set", "T1", "--status", "done", "--skip-review", " ", "--user-said", "ok")  # blank reason
        ok("task", "set", "T1", "--status", "done")
        no("task", "set", "T1", "--owner", "rev")  # a done task is final
        no("task", "set", "T1", "--accept", "")
        no("plan", "approve", "--user-said", "  ")  # blank words

    def test_round2_more_bypasses(self):
        self.tw("plan", "approve", "--user-said", "ok")
        for s in ("dispatched", "working", "review"):
            self.raw("task", "set", "T1", "--status", s)
        self.tw("task", "add", "T2", "--owner", "ui")
        self.tw("req", "add", "x", "--tasks", "T1")
        self.raw("req", "set", "R1", "--status", "reported")
        self.assertNotEqual(self.raw("req", "link", "R1", "T2").returncode, 0)  # reopening is the user's call
        self.assertEqual(self.raw("req", "link", "R1", "T2", "--user-said", "also do T2").returncode, 0)
        self.assertEqual(self.state()["requests"]["R1"]["status"], "dispatched")
        self.assertNotEqual(self.raw("req", "close", "R1", "--user-said", "ok").returncode, 0)
        self.assertNotEqual(self.raw("task", "set", "T1", "--status", "done", "--skip-review", "x").returncode, 0)
        for who in ("ui-thread", "the ui", "Ui:self"):
            self.assertNotEqual(self.raw("task", "set", "T1", "--review", "pass", "--reviewer", who).returncode, 0, who)
        self.tw("task", "add", "T3", "--owner", "ui")
        self.tw("task", "add", "T4", "--owner", "ui")
        self.assertNotEqual(self.raw("task", "set", "T3", "--status", "dispatched", "--deps", "T4").returncode, 0)
        self.tw("plan", "reopen")
        self.assertIn("reopened", self.raw("task", "set", "T3", "--status", "dispatched").stderr)


    def test_round3_gate_words_deps_accept_checks(self):
        self.assertNotEqual(self.raw("plan", "approve").returncode, 0)  # needs the user's words
        self.tw("plan", "approve", "--user-said", "go")
        self.assertNotEqual(self.raw("prefs", "--set", "require_review=nope").returncode, 0)
        self.assertNotEqual(self.raw("prefs", "--set", "require_review=").returncode, 0)
        self.tw("task", "add", "T2", "--owner", "ui", "--deps", "T1")
        self.assertNotEqual(self.raw("task", "set", "T2", "--status", "dispatched", "--deps", "").returncode, 0)
        self.tw("task", "add", "T3")
        self.assertIn("no owner", self.raw("task", "set", "T3", "--status", "dispatched").stderr)
        for st_ in ("dispatched", "working", "review"):
            self.raw("task", "set", "T1", "--status", st_)
        self.raw("task", "set", "T1", "--review", "pass", "--reviewer", "rev")
        self.raw("task", "set", "T1", "--accept", "something else")  # new "done when": old verdict void
        self.assertNotEqual(self.raw("task", "set", "T1", "--status", "done").returncode, 0)
        self.raw("task", "set", "T1", "--checks", "fail")
        r = self.raw("task", "set", "T1", "--status", "done", "--skip-review", "x", "--user-said", "ok")
        self.assertIn("checks failed", r.stderr)
        self.tw("req", "add", "q")
        self.tw("req", "drop", "R1", "--user-said", "no")
        self.assertNotEqual(self.raw("req", "set", "R1", "--text", "rewritten").returncode, 0)


    def test_round4_same_command_and_blank_values(self):
        self.tw("plan", "approve", "--user-said", "go")
        self.tw("thread", "add", "api")
        for st_ in ("dispatched", "working", "review"):
            self.raw("task", "set", "T1", "--status", st_)
        r = self.raw("task", "set", "T1", "--owner", "", "--review", "pass", "--reviewer", "ui", "--status", "done")
        self.assertNotEqual(r.returncode, 0)  # clearing the owner does not make its author independent
        self.assertNotEqual(self.raw("task", "set", "T1", "--review", "pass", "--reviewer", " ").returncode, 0)
        self.raw("task", "set", "T1", "--review", "pass", "--reviewer", "rev")
        self.assertNotEqual(self.raw("task", "set", "T1", "--status", "done", "--accept", "B").returncode, 0)
        self.raw("task", "set", "T1", "--checks", "fail")
        self.assertNotEqual(self.raw("task", "set", "T1", "--checks", "none").returncode, 0)
        self.assertNotEqual(self.raw("task", "set", "T1", "--status", "done", "--skip-review", " ",
                                     "--user-said", "ok").returncode, 0)
        self.tw("prefs", "--set", "require_review=off", "--user-said", "fine skip it")
        self.assertEqual(self.state()["prefs_user_said"][-1]["text"], "fine skip it")
        self.assertIn("fine skip it", (self.dir / ".teamwork" / "log.jsonl").read_text())
        self.tw("req", "add", "z")
        self.assertIn("no tasks", self.raw("req", "set", "R1", "--status", "dispatched").stderr)


class TopicsRosterAsk(TwCase):
    """T19: related tasks never run in parallel; ROSTER broadcasts are tracked; questions numbered from 1."""

    def setUp(self):
        super().setUp()
        self.tw("thread", "add", "docs", "--session", "s-docs", "--status", "working")
        self.tw("thread", "add", "kit", "--session", "s-kit", "--status", "idle")
        self.tw("plan", "approve", "--user-said", "ok")

    def ready(self):
        return json.loads(self.tw("ready").stdout)

    def test_topic_overlap_blocks(self):
        self.tw("task", "add", "T1", "--owner", "docs", "--topics", "Mechanism,request-ledger")
        self.tw("task", "add", "T2", "--owner", "kit", "--topics", "mechanism")
        self.tw("task", "add", "T3", "--owner", "kit", "--topics", "installer")
        self.tw("task", "add", "T4", "--owner", "docs", "--topics", "mechanism")  # same thread: sequential
        self.tw("task", "set", "T1", "--status", "working")
        r = self.ready()
        self.assertEqual(r["blocked_by_topic"]["T2"][0]["with"], "T1")
        self.assertEqual(r["blocked_by_topic"]["T2"][0]["topics"], ["mechanism"])
        self.assertIn("why", r["blocked_by_topic"]["T2"][-1])
        self.assertEqual(sorted(r["ready"]), ["T3", "T4"])

    def test_roster_reminder(self):
        self.assertEqual(self.ready()["roster_not_sent"], ["docs"])  # kit was added after docs
        self.tw("thread", "set", "all", "--roster-sent")
        self.assertEqual(self.ready()["roster_not_sent"], [])
        self.tw("thread", "add", "wl2", "--session", "s-wl2", "--status", "starting")
        self.assertEqual(sorted(self.ready()["roster_not_sent"]), ["docs", "kit"])  # new thread knows itself
        self.tw("thread", "set", "docs", "--roster-sent")
        self.assertEqual(self.ready()["roster_not_sent"], ["kit"])
        self.assertFalse([x for x in json.loads(self.tw("open").stdout) if x["thread"] == "kit"][0]["roster_sent"])
        self.tw("thread", "set", "all", "--roster-sent")
        self.tw("thread", "set", "kit", "--owns", "kit/")  # ownership changed: everyone needs a new ROSTER
        self.assertEqual(sorted(self.ready()["roster_not_sent"]), ["docs", "kit", "wl2"])
        self.assertNotEqual(self.tw("thread", "add", "all", ok=False).returncode, 0)

    def test_owner_subagent_may_review(self):
        self.tw("task", "add", "T1", "--owner", "docs")
        self.assertNotEqual(self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "docs", ok=False).returncode, 0)
        self.tw("task", "set", "T1", "--accept", "x", "--review", "pass", "--reviewer", "docs/subagent", "--status", "done")
        self.assertEqual(self.state()["tasks"]["T1"]["review"]["reviewer"], "docs/subagent")

    def test_ask_numbers_from_one(self):
        self.tw("ask", "blue or green?", "--from", "docs")
        self.assertIn("- [1] (docs) blue or green?", self.tw("board").stdout)
        self.tw("ask", "--answer", "1")
        self.assertTrue(self.state()["questions"][0]["answered"])
        self.tw("ask", "second?", "--from", "kit")
        self.tw("ask", "--answer", "2", "green")
        self.assertEqual(self.state()["questions"][1]["answer"], "green")
        r = self.tw("ask", "--answer", "3", ok=False)
        self.assertIn("numbered 1..2", r.stderr)
        self.assertNotEqual(self.tw("ask", "--answer", "0", ok=False).returncode, 0)


    def test_round2_topics_reviewer_answers_roster(self):
        self.tw("task", "add", "T1", "--owner", "docs", "--topics", "Request Ledger")
        self.tw("task", "add", "T2", "--owner", "kit", "--topics", "request_ledger")
        r = self.ready()  # both ready, neither active: still must not run in parallel
        self.assertIn("T2", r["blocked_by_topic"])
        self.assertNotIn("T2", r["ready"])
        for bad in ("Docs", "./docs", "docs/", "docs/x/.."):
            self.assertNotEqual(self.tw("task", "set", "T1", "--review", "pass", "--reviewer", bad,
                                        ok=False).returncode, 0, bad)
        self.tw("ask", "q?", "--from", "docs")
        self.tw("ask", "--answer", "1", "yes")
        r = self.tw("ask", "--answer", "1", "no", ok=False)
        self.assertIn("already answered", r.stderr)
        self.assertEqual(self.state()["questions"][0]["answer"], "yes")
        self.assertNotIn("status", self.tw("roster").stdout.lower().replace("tw-status", ""))


    def test_round3_lead_takeover_needs_roster(self):
        self.tw("lead", "--session", "L1")
        self.tw("thread", "set", "all", "--roster-sent")
        self.assertEqual(self.ready()["roster_not_sent"], [])
        self.tw("lead", "--session", "L2", "--force")
        self.assertEqual(sorted(self.ready()["roster_not_sent"]), ["docs", "kit"])
        self.tw("thread", "set", "kit", "--status", "resolved")
        self.assertEqual(self.ready()["roster_not_sent"], ["docs"])  # same rule as `open`


class MergeRehearsal(LeasesGit):
    """T33: `merge` rehearses in a temp worktree; the live checkout only ever moves by fast-forward."""

    test_branch_diff_blocks = None

    def setUp(self):
        super().setUp()
        self.git("config", "user.name", "t")
        self.git("config", "user.email", "t@example.invalid")
        (self.proj / "zorb.py").write_text("a = 1\n")
        (self.proj / "check.sh").write_text("grep -q 'a = ' zorb.py\n")
        self.commit("base")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "quix.py").write_text("q = 1\n")
        self.commit("T1: quixbar")
        self.git("checkout", "-q", "main")
        self.main0 = self.head()

    def head(self, ref="main"):
        return self.git("rev-parse", ref).stdout.strip()

    def m(self, *x, ok=True):
        return self.tw("merge", "tw/proj/ui", "--sha", self.head("tw/proj/ui"), *x, ok=ok)

    def merge(self, *x, ok=True):
        return self.m("--test", "sh check.sh", *x, ok=ok)

    def no_temp_worktrees(self):
        self.assertNotIn("tw-merge-", self.git("worktree", "list").stdout)

    def test_rehearsal_changes_nothing(self):
        r = json.loads(self.merge().stdout)
        self.assertEqual((r["checks"], r["applied"]), ("pass", False))
        self.assertEqual(self.head(), self.main0)
        self.assertFalse((self.proj / "quix.py").exists())
        self.no_temp_worktrees()

    def test_apply_fast_forwards_live_checkout(self):
        r = json.loads(self.merge("--apply").stdout)
        self.assertTrue(r["applied"])
        self.assertEqual(self.head(), r["merged"])
        self.assertTrue((self.proj / "quix.py").exists())  # the live files moved with the ref
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=no").stdout, "")
        self.assertIn('"ev": "merge"', (self.proj / ".teamwork" / "log.jsonl").read_text())
        self.no_temp_worktrees()

    def test_conflict_leaves_live_untouched(self):
        (self.proj / "quix.py").write_text("q = 2\n")
        self.commit("T2: other quix")
        before = self.head()
        r = self.merge("--apply", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("quix.py", r.stdout)
        self.assertEqual(self.head(), before)
        self.assertNotIn("<<<<<<<", (self.proj / "quix.py").read_text())
        self.no_temp_worktrees()

    def test_red_checks_leave_live_untouched(self):
        r = self.m("--test", "exit 3", "--apply", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("checks failed", r.stderr)
        self.assertEqual(self.head(), self.main0)
        self.no_temp_worktrees()

    def test_needs_a_check_and_a_squash_subject(self):
        self.assertIn("no check to run", self.m(ok=False).stderr)
        self.assertIn("--squash needs --message", self.merge("--squash", ok=False).stderr)
        self.tw("prefs", "--set", "check_cmd=sh check.sh")
        r = json.loads(self.m("--squash", "--message", "T1: quixbar (squash)", "--apply").stdout)
        self.assertEqual(self.git("log", "-1", "--format=%s %P").stdout.split()[0:2], ["T1:", "quixbar"])
        self.assertEqual(len(self.git("log", "-1", "--format=%P").stdout.split()), 1)  # one parent: a squash
        self.assertTrue(r["applied"])
        r = json.loads(self.m("--squash", "--message", "T1: again", "--no-test", "docs only").stdout)
        self.assertIn("nothing to merge", r["note"])  # an empty squash is not a failure

    def test_dirty_live_base_refused(self):
        (self.proj / "zorb.py").write_text("a = 9\n")
        r = self.merge("--apply", ok=False)
        self.assertIn("uncommitted changes", r.stderr)
        self.assertEqual(self.head(), self.main0)

    def test_base_not_checked_out_moves_ref(self):
        self.git("checkout", "-q", "-b", "elsewhere")
        r = json.loads(self.merge("--apply").stdout)
        self.assertEqual(self.head("main"), r["merged"])
        self.assertEqual(self.head("HEAD"), self.main0)  # the checkout on another branch did not move

    def test_only_the_reviewed_sha_merges(self):
        reviewed = self.head("tw/proj/ui")
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "late.py").write_text("z = 1\n")
        self.commit("T1: pushed after the review")
        self.git("checkout", "-q", "main")
        r = self.tw("merge", "tw/proj/ui", "--sha", reviewed[:9], "--test", "sh check.sh", "--apply", ok=False)
        self.assertIn("not the reviewed", r.stderr)
        self.assertIn("pushed after the review", json.loads(r.stdout)["extra_commits"][0])
        self.assertEqual(self.head(), self.main0)
        self.git("checkout", "-q", "-b", "stray")
        (self.proj / "stray.py").write_text("s = 1\n")
        self.commit("stray")
        self.git("checkout", "-q", "main")
        r = self.tw("merge", "tw/proj/ui", "--sha", self.head("stray"), "--test", "sh check.sh", ok=False)
        self.assertIn("not on tw/proj/ui", r.stderr)
        self.assertIn("no such commit", self.tw("merge", "tw/proj/ui", "--sha", "abcdef0", "--no-test", "x",
                                                ok=False).stderr)
        for name in ("tw/proj/ui", "stray", "HEAD", f"{reviewed}~0", "main", " "):  # names never stand for a review
            r = self.tw("merge", "tw/proj/ui", "--sha", name, "--no-test", "x", ok=False)
            self.assertIn("commit id", r.stderr, name)
        self.git("branch", "deadbeef", "stray")  # a branch named like hex is still not that commit
        self.assertIn("no such commit", self.tw("merge", "tw/proj/ui", "--sha", "deadbeef", "--no-test", "x",
                                                ok=False).stderr)
        self.assertTrue(json.loads(self.tw("merge", "tw/proj/ui", "--sha", f" {self.head('tw/proj/ui').upper()} ",
                                           "--no-test", "x").stdout)["merged"])
        self.assertNotEqual(self.tw("merge", "tw/proj/ui", "--no-test", "x", ok=False).returncode, 0)  # --sha required

    def test_holder_switching_branch_during_checks_refused(self):
        r = self.merge_with_check(f"git -C {self.proj} checkout -q -b zorblax", ok=False)
        self.assertRegex(r.stderr, "changed meanwhile|no longer on main")
        self.assertEqual(self.head(), self.main0)
        self.assertEqual(self.head("zorblax"), self.main0)  # the other branch was not fast-forwarded
        self.assertNotIn('"ev": "merge"', (self.proj / ".teamwork" / "log.jsonl").read_text())
        self.no_temp_worktrees()

    def test_base_checked_out_during_checks_refused(self):
        self.git("checkout", "-q", "-b", "side")
        r = self.merge_with_check(f"git -C {self.proj} checkout -q main", ok=False)
        self.assertIn("changed meanwhile", r.stderr)
        self.assertEqual(self.head(), self.main0)
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=no").stdout, "")

    def test_holder_changing_during_checks_refused(self):
        for cmd, why in ((f"echo junk > {self.proj / 'zorb.py'}", "uncommitted changes"),
                         (f"git -C {self.proj} commit -q --allow-empty -m moved", "moved to")):
            r = self.merge_with_check(cmd, ok=False)
            self.assertIn(why, r.stderr)
            self.assertFalse(json.loads(r.stdout)["applied"])
            self.git("checkout", "-q", "--", "zorb.py")
            self.no_temp_worktrees()
        self.assertNotIn('"ev": "merge"', (self.proj / ".teamwork" / "log.jsonl").read_text())

    def test_rebase_started_during_checks_refused(self):
        r = self.merge_with_check(f"git -C {self.proj} rebase -q --exec false HEAD~1 >/dev/null 2>&1; true", ok=False)
        self.assertRegex(r.stderr, "no longer on main|changed meanwhile")
        self.git("rebase", "--abort")
        self.assertEqual(self.head(), self.main0)

    def test_ignored_local_file_in_the_way_refused(self):
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "quixbar.env").write_text("from the branch\n")
        self.git("add", "-f", "quixbar.env")
        self.commit("T1: env")
        self.git("checkout", "-q", "main")
        (self.proj / ".git" / "info" / "exclude").write_text("*.env\n")
        (self.proj / "quixbar.env").write_text("MY LOCAL\n")
        r = self.merge("--apply", ok=False)
        self.assertIn("quixbar.env", json.loads(r.stdout)["untracked_in_the_way"])
        self.assertEqual((self.proj / "quixbar.env").read_text(), "MY LOCAL\n")

    def test_sigterm_during_checks_cleans_up(self):
        flag = self.proj.parent / "started"
        p = subprocess.Popen([sys.executable, str(TW), "-C", str(self.proj), "merge", "tw/proj/ui", "--sha",
                              self.head("tw/proj/ui"), "--test", f"touch {flag}; sleep 30", "--apply"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(300):
            if flag.exists():
                break
            time.sleep(0.05)
        p.terminate()
        out, err = p.communicate(timeout=20)
        self.assertIn("nothing changed", err)
        self.assertEqual(self.head(), self.main0)
        self.no_temp_worktrees()
        self.assertEqual([x for x in Path(tempfile.gettempdir()).glob("tw-merge-*/pid")
                          if x.read_text() == str(p.pid)], [])  # its temp folder is gone too

    def test_sigterm_during_fast_forward_waits_for_it(self):
        flag = self.proj.parent / "ff-started"
        hook = self.proj / ".git" / "hooks" / "post-merge"
        hook.write_text(f'#!/bin/sh\ncase "$(pwd -P)" in "{self.proj.resolve()}") touch "{flag}"; sleep 2;; esac\n')
        hook.chmod(0o755)
        p = subprocess.Popen([sys.executable, str(TW), "-C", str(self.proj), "merge", "tw/proj/ui", "--sha",
                              self.head("tw/proj/ui"), "--test", "sh check.sh", "--apply"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(400):
            if flag.exists():
                break
            time.sleep(0.05)
        self.assertTrue(flag.exists())
        p.terminate()
        out, err = p.communicate(timeout=20)
        self.assertNotIn("nothing changed", err)
        self.assertTrue(json.loads(out)["applied"])  # the report is printed before the waiting signal acts
        self.assertNotEqual(self.head(), self.main0)
        self.assertTrue((self.proj / "quix.py").exists())
        self.assertIn('"ev": "merge"', (self.proj / ".teamwork" / "log.jsonl").read_text())
        self.no_temp_worktrees()

    def test_t42_folder_and_file_prefix_clashes_refused(self):
        """T42: a local ignored folder where the merge adds a file, and a local ignored file where it adds a folder."""
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "bld").write_text("tracked file\n")
        (self.proj / "cfg").mkdir()
        (self.proj / "cfg" / "a").write_text("tracked\n")
        self.git("add", "-f", "bld", "cfg/a")
        self.commit("T1: bld and cfg")
        self.git("checkout", "-q", "main")
        (self.proj / ".git" / "info" / "exclude").write_text("bld\ncfg\n")
        (self.proj / "bld").mkdir()
        (self.proj / "bld" / "x").write_text("LOCAL\n")
        (self.proj / "cfg").write_text("LOCAL\n")
        r = self.merge("--apply", ok=False)
        self.assertEqual(sorted(json.loads(r.stdout)["untracked_in_the_way"]), ["bld/x", "cfg"])
        self.assertEqual(((self.proj / "bld" / "x").read_text(), (self.proj / "cfg").read_text()), ("LOCAL\n",) * 2)

    def test_t42_non_ascii_paths_in_the_way(self):
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "中文").write_text("tracked\n")
        self.commit("T1: zh")
        self.git("checkout", "-q", "main")
        (self.proj / "中文").mkdir()
        (self.proj / "中文" / "k").write_text("LOCAL\n")
        r = self.merge("--apply", ok=False)
        self.assertEqual(json.loads(r.stdout)["untracked_in_the_way"], ["中文/k"])
        self.assertEqual(self.head(), self.main0)

    def test_t42_ignored_file_appearing_during_checks_refused(self):
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "k.secret").write_text("from the branch\n")
        self.git("add", "-f", "k.secret")
        self.commit("T1: secret")
        self.git("checkout", "-q", "main")
        (self.proj / ".git" / "info" / "exclude").write_text("*.secret\nrace.sh\n")
        r = self.merge_with_check(f"echo LOCAL > {self.proj / 'k.secret'}", ok=False)
        self.assertIn("appeared meanwhile", r.stderr)
        self.assertEqual((self.proj / "k.secret").read_text(), "LOCAL\n")
        self.assertEqual(self.head(), self.main0)

    def test_t42_check_timeout_and_no_stdin(self):
        stf = self.proj / ".teamwork" / "state.json"
        st = json.loads(stf.read_text())
        st["prefs"]["check_timeout_minutes"] = 0.02  # about a second
        stf.write_text(json.dumps(st))
        r = self.m("--test", "sleep 30", ok=False)
        self.assertIn("were stopped", r.stderr)
        self.no_temp_worktrees()
        self.assertEqual(json.loads(self.m("--test", "cat").stdout)["checks"], "pass")  # stdin is empty, not ours
        self.assertNotEqual(self.tw("prefs", "--set", "check_timeout_minutes=x", ok=False).returncode, 0)

    def test_t42_prunable_base_worktree_is_nobody(self):
        self.git("checkout", "-q", "-b", "zorb-side")
        side = Path(tempfile.mkdtemp()) / "m"
        self.git("worktree", "add", "-q", str(side), "main")
        shutil.rmtree(side)  # its folder is gone: the worktree is prunable
        r = json.loads(self.merge("--apply").stdout)
        self.assertTrue(r["applied"])  # the first run, not after a misleading "changed meanwhile"
        self.assertEqual(self.head(), r["merged"])

    def test_t42_live_or_young_leftovers_are_kept(self):
        tmp = Path(tempfile.mkdtemp(prefix="tw-merge-"))
        self.git("worktree", "add", "-q", "--detach", str(tmp / "wt"), "main")
        (tmp / "pid").write_text("1")  # alive, not ours to signal: PermissionError means running
        young = Path(tempfile.mkdtemp(prefix="tw-merge-"))
        self.git("worktree", "add", "-q", "--detach", str(young / "wt"), "main")  # no pid file, just made
        self.merge()
        self.assertTrue((tmp / "wt").exists() and (young / "wt").exists())
        for w in (tmp, young):
            self.git("worktree", "remove", "--force", str(w / "wt"))

    def test_killed_run_leftover_is_cleaned_next_time(self):
        tmp = Path(tempfile.mkdtemp(prefix="tw-merge-"))
        self.git("worktree", "add", "-q", "--detach", str(tmp / "wt"), "main")
        (tmp / "pid").write_text("999999999")  # no such process
        self.merge()
        self.assertFalse(tmp.exists())
        self.no_temp_worktrees()

    def merge_with_check(self, cmd, ok=True):
        (self.proj / "race.sh").write_text(cmd + "\n")  # untracked: not in the rehearsal, so call it by path
        return self.m("--test", f"sh {self.proj / 'race.sh'}", "--apply", ok=ok)

    def test_check_cmd_is_text_and_no_test_wins(self):
        self.tw("prefs", "--set", "check_cmd=true")
        self.assertEqual(json.loads((self.proj / ".teamwork" / "state.json").read_text())["prefs"]["check_cmd"], "true")
        self.tw("prefs", "--set", "check_cmd=exit 5")
        r = json.loads(self.m("--no-test", "docs only").stdout)
        self.assertEqual(r["checks"], "none (docs only)")

    def test_untracked_file_in_the_way_refused_before_checks(self):
        (self.proj / "quix.py").write_text("mine\n")
        r = self.m("--test", "exit 7", "--apply", ok=False)
        self.assertIn("untracked files", r.stderr)
        self.assertEqual(json.loads(r.stdout)["untracked_in_the_way"], ["quix.py"])
        self.assertEqual((self.proj / "quix.py").read_text(), "mine\n")
        self.assertEqual(self.head(), self.main0)


class H1Followups(TwCase):
    def test_held_transitions_and_zh_board(self):
        self.tw("req", "add", "材料待补")
        self.tw("req", "set", "R1", "--status", "held", "--followup", "请发材料")
        st = self.state(); st["prefs"]["language"] = "zh"
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st))
        self.assertIn("等你的材料", self.tw("board").stdout)
        self.tw("req", "set", "R1", "--status", "open")
        self.assertNotEqual(self.tw("req", "set", "R1", "--status", "closed", ok=False).returncode, 0)
        self.tw("req", "set", "R1", "--status", "proposed")
        self.tw("req", "set", "R1", "--status", "held")  # paused while proposed
        self.assertEqual(self.state()["requests"]["R1"]["held_from"], "proposed")
        r = self.tw("req", "set", "R1", "--status", "dispatched", ok=False)  # still the user's yes
        self.assertIn("--user-said", r.stderr)
        self.assertNotEqual(self.tw("req", "set", "R1", "--status", "open", ok=False).returncode, 0)
        self.tw("req", "set", "R1", "--status", "proposed")
        self.assertNotIn("held_from", self.state()["requests"]["R1"])
        st = self.state()  # work finished while the request was held: resuming delivers it
        st["tasks"]["T1"] = {"title": "x", "status": "review", "thread": None}
        st["requests"]["R2"] = {**st["requests"]["R1"], "status": "held", "held_from": "dispatched", "tasks": ["T1"]}
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st))
        self.tw("req", "set", "R2", "--status", "dispatched")
        self.assertEqual(self.state()["requests"]["R2"]["status"], "delivered")
        st = self.state()  # the user asks for rework of a reported request: it stays dispatched
        st["requests"]["R3"] = {**st["requests"]["R2"], "status": "reported"}
        st["requests"]["R3"].pop("held_from", None)
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st))
        self.tw("req", "set", "R3", "--status", "dispatched", "--user-said", "改一下")
        self.assertEqual(self.state()["requests"]["R3"]["status"], "dispatched")

    def test_check_msg_findings_and_clean(self):
        self.tw("memory", "Never topic", "--never-raise")
        self.tw("memory", "AI", "--never-raise")
        self.tw("memory", "Old pricing idea — dropped in March", "--never-raise", "--temporary")
        self.tw("memory", "Do not ever mention the long sentence form of this rule", "--never-raise")
        self.tw("memory", "User choice", "--user-owned")
        lead, api, old = "session_01Lead1abcdefghijkl", "session_01Api1abcdefghijklm", "session_01Old1abcdefghijklm"
        st = self.state(); st["lead"]["session_id"] = lead
        st["threads"]["api"] = {"status": "working", "session_id": api, "sessions": [{"session_id": old}]}
        for t in ("T1", "T3", "T4", "T41", "T3-f1-f1"):
            st["tasks"][t] = {"title": t, "status": "working", "thread": "api"}
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st))
        short = st["prefs"]["short_name"]
        msg = self.dir / "message.txt"

        def check(text, *extra, ok=True):
            msg.write_text(text)
            r = self.tw("check-msg", str(msg), "--json", *extra, ok=ok)
            return r.returncode, {(x["kind"], x["level"]) for x in json.loads(r.stdout)["findings"]}
        quiet = lambda t: check(t)[1] - {("unchecked", "warning")}  # the long MEMORY entry warns every time
        code, kinds = check(f"[tw:{short}] TASK lead -> api T1\nTo: <session id>\nGoal: TBD (ask user)\n"
                            "Done when:\nNever topic and User choice; ask session_01Unknown9abcdefghij\n", ok=False)
        self.assertEqual(code, 1)
        self.assertTrue({("placeholder", "problem"), ("unknown-session", "problem"),
                         ("never-raise", "warning"), ("user-owned", "warning")} <= kinds, kinds)
        self.assertIn(("unchecked", "warning"), kinds)  # the long entry is reported as not checkable
        # messages.md's own templates: the full form is all holes, the short form is a real task.
        doc = (Path(__file__).resolve().parents[1] / "references" / "messages.md").read_text()
        blocks = re.findall(r"```\n(\[tw:shop\] TASK.*?)```", doc, re.S)
        st["prefs"]["short_name"] = "shop"; st["threads"]["ui"] = {"status": "working", "session_id": api}
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st))
        self.assertEqual(check(blocks[0])[0], 0)
        self.assertIn(("placeholder", "problem"), check(blocks[1], ok=False)[1])
        short = "shop"
        for head in ("TASK lead -> api T1", "[tw:x] TASK lead -> api T1", "[tw:shop] TASK lead -> <thread> T1",
                     "[tw:shop] TASK lead -> api", "[tw:shop] TASK lead -> api T2,T7", "[tw:shop] TASK lead -> nosuch T3"):
            self.assertIn(("header", "problem"), check(head + "\nGoal: x.\nDone when: y.\n", ok=False)[1], head)
        self.assertEqual(check("\n  [TW:shop] ANSWER lead -> api T4\nok\n")[0], 0)
        # Normal task text passes.
        for body in ("Goal: build the TODO list page with <button>, <Text>, <order-row>, Array<number>, "
                     "useState<string | null> and {{ user.name }}; add a session_id column, a session_token1234abcd "
                     f"dir and ask {old}; keep main and contain it.\nRead first: docs/spec.md\nDone when: tests pass.",
                     "Goal: 把 README.md 里的安装步骤改成中文版本。\nDone when: 改完。",
                     "Goal: 按 docs/spec.md §3 重做订单页。\n完成标准：截图两种宽度。"):
            code, kinds = check(f"[tw:{short}] TASK lead -> api T1\n{body}\n")
            self.assertEqual(code, 0, (body, kinds))
        for body in ("T3 已合并，下一个做 docs/plan.md 里的第 4 项。", "Yes, ship by 2026/11/21.", "Yes. See docs/a.md",
                     "先 rebase 到 main，再详见 CHANGELOG.md 里条目的格式"):
            self.assertEqual(check(f"[tw:{short}] ANSWER lead -> api T1\n{body}\n")[0], 0, body)
        for body in ("Goal:\n  make the list fast.\nDone when:\n- tests pass\n- screenshots",
                     "**Goal:** make it fast.\n1. Done when: `make all` passes.",
                     "## Goal\nmake it fast.\n**Done when**: ok.",
                     "Goal: TODO list page with width < 600 and height > 400, <a href>, <Link to>, <string, number>.\n"
                     "Done when: if count < 3 then route lead -> ui."):
            code, kinds = check(f"[tw:{short}] TASK lead -> api T3-f1-f1\n{body}\n")
            self.assertEqual(code, 0, (body, kinds))
        for body in ("Goal: ...\nDone when: ok.", "Goal: x.\nDone when: ask <Thread> on <session-id>.",
                     "Goal: x.\nDone when: session_<id> answers.", "Goal: TODO: write\nDone when: ok."):
            self.assertIn(("placeholder", "problem"), check(f"[tw:{short}] TASK lead -> api T1\n{body}\n", ok=False)[1],
                          body)
        self.assertIn(("header", "problem"), check(f"[tw:{short}] TASK lead -> api T99\nGoal: x.\nDone when: y.\n",
                                                   ok=False)[1])  # not on the board
        code, kinds = check(f"[tw:{short}] TASK lead -> api T1\nYour task is in .teamwork/tasks/T3.md. Read it.\n",
                            ok=False)
        self.assertIn(("fields", "problem"), kinds)  # a pointer TASK has no Goal / Done when
        code, kinds = check(f"[tw:{short}] TASK lead -> api T1\nGoal: 不要再提 old pricing idea 了，AI 也别提。\n"
                            "Done when: ok.\n")
        self.assertEqual(code, 0)
        self.assertEqual([k for k in kinds if k[0] == "never-raise"], [("never-raise", "warning")])  # one item
        self.assertIn("Old pricing idea", self.tw("check-msg", str(msg)).stdout)
        for prompt in ("Read ~/.claude/skills/teamwork/references/protocol.md and follow it.",
                       "Read templates/worker-protocol and follow it.",
                       "你是 ui 线程。你的协议在 ~/.claude/x/worker-protocol.md：完整读一遍，照里面每条规则做。"):
            self.assertEqual(check(prompt, "--prompt", ok=False)[1], {("pointer", "problem")}, prompt)
        real = self.tw("worker-prompt", "api", ok=False).stdout or ("You are thread api; the rules follow. " * 40)
        self.assertEqual(check(real, "--prompt")[0], 0)  # a real prompt is not a pointer
        st["prefs"]["language"] = ""
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st))
        self.assertEqual(quiet(f"[tw:{short}] ANSWER lead -> api T1\n这是中文回答，项目没设语言。\n"), set())
        st["prefs"]["language"] = "zh"
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st))
        code, kinds = check(f"[tw:{short}] INFO lead -> all -\nOnly English prose here, nothing else at all.\n")
        self.assertEqual((code, kinds - {("unchecked", "warning")}), (0, {("language", "warning")}))
        self.assertEqual(quiet(f"[tw:{short}] ANSWER lead -> api T41\nT41/T42 已合并，见看板。\n"), set())
        self.assertEqual(quiet(f"[tw:{short}] ACK lead -> api T1\nok\n"), set())

    def test_info_issue_and_thread_filter(self):
        self.tw("thread", "add", "api", "--session", "session_api1")
        self.tw("thread", "add", "old")
        self.tw("thread", "set", "old", "--status", "archived")
        self.assertIn("broadcast it: tw.py info --issue I1", self.tw("issue", "add", "broken", "--owner", "api").stdout)
        got = json.loads(self.tw("info", "hello", "--issue", "I1", "--json").stdout)
        self.assertIn("[tw:demo] INFO lead -> all -", got["message"])
        self.assertIn("broken", got["message"])
        self.assertEqual(got["to"], [{"thread": "api", "session_id": "session_api1"}])

    def test_qa_board_and_done(self):
        st = self.state(); st["qa_pending"] = [{"branch": "tw/demo/x", "sha": "1234567890"}]
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st))
        self.assertIn("Merged, regression not run yet", self.tw("board").stdout)
        self.assertIn("Merged, regression not run yet", self.tw("board", "--share").stdout)
        self.assertNotEqual(self.tw("qa", "done", ok=False).returncode, 0)  # say what the round covered
        self.assertNotEqual(self.tw("qa", "done", "tw/demo/other", ok=False).returncode, 0)
        st = self.state(); st["qa_pending"].append({"branch": "tw/demo/y", "sha": "abcdef0123"})
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st))
        st = self.state(); st["qa_pending"][0]["to"] = "fedcba9876"
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st))
        self.assertIn("still waiting: tw/demo/y@abcdef0", self.tw("qa", "done", "fedcba9", "--note", "checked").stdout)
        self.assertEqual([x["branch"] for x in self.state()["qa_pending"]], ["tw/demo/y"])
        self.tw("qa", "done", "--all")
        self.assertEqual(self.state()["qa_pending"], [])
        self.assertNotIn("Merged, regression not run yet", self.tw("board").stdout)

    def test_merge_apply_adds_qa_pending(self):
        git = lambda *args: subprocess.run(["git", "-C", str(self.dir), *args], check=True,
                                           capture_output=True, text=True)
        git("init", "-q", "-b", "main")
        git("config", "user.name", "t"); git("config", "user.email", "t@example.invalid")
        (self.dir / "base.txt").write_text("base\n"); git("add", "base.txt"); git("commit", "-qm", "base")
        git("checkout", "-q", "-b", "tw/demo/ui")
        (self.dir / "change.txt").write_text("merged\n"); git("add", "change.txt"); git("commit", "-qm", "T1: change")
        sha = git("rev-parse", "HEAD").stdout.strip()
        git("checkout", "-q", "main")
        out = self.tw("merge", "tw/demo/ui", "--sha", sha, "--test", "true", "--apply")
        self.assertTrue(json.loads(out.stdout)["applied"])
        pending = self.state()["qa_pending"]
        self.assertEqual((pending[0]["branch"], pending[0]["sha"], pending[0]["base"]),
                         ("tw/demo/ui", sha, "main"))
        self.tw("rebuild")  # preview: the journal carries the list
        rebuilt = json.loads((self.dir / ".teamwork" / "state.json.rebuilt").read_text())
        self.assertEqual([x["sha"] for x in rebuilt["qa_pending"]], [sha])
        self.tw("qa", "done", "tw/demo/ui")
        self.tw("rebuild")
        self.assertEqual(json.loads((self.dir / ".teamwork" / "state.json.rebuilt").read_text())["qa_pending"], [])

    def test_context_lead_reminder_and_worktree_suppression(self):
        self.tw("lead", "--session", "session_lead1")
        self.assertIn("If you are this project's lead", self.context())
        worktrees = self.dir.parent / (self.dir.name + "-worktrees")
        work = worktrees / "api"
        work.mkdir(parents=True)
        result = subprocess.run([sys.executable, str(TW), "context"], cwd=work, env=self.env,
                                capture_output=True, text=True)
        self.assertNotIn("If you are this project's lead", result.stdout)


class Quota(TwCase):
    NOW = 2_000_000_000

    def setUp(self):
        super().setUp()
        self.cfg = self.dir / "fake-config"
        self.codex = self.dir / "fake-codex"
        self.cfg.mkdir()
        (self.codex / "sessions" / "nested").mkdir(parents=True)

    def event(self, windows, timestamp=None):
        payload = {"timestamp": timestamp or self.NOW - 20,
                   "rate_limit_info": {"unifiedWindows": windows}}
        (self.cfg / "bridge-transcript-cse_fixture.jsonl").write_text(json.dumps(payload) + "\n")

    def win(self, value, reset=None):
        return {"utilization": value, "resetsAt": reset or self.NOW + 86400}

    def read(self):
        return quota.read_quota([str(self.cfg)], str(self.codex), self.NOW)

    def test_event_windows_order_spacing_and_expired_data(self):
        self.event({"seven_day": {"resetsAt": self.NOW - 1, "utilization": 0.4},
                    "five_hour": {"resetsAt": self.NOW + 5, "utilization": 0.2}})
        self.assertNotIn("total", self.read()["claude"])
        self.event({"seven_day": {"resetsAt" : 2000000100, "utilization" : 0.695},
                    "five_hour": self.win(0.81)})
        result = self.read()["claude"]
        self.assertAlmostEqual(result["total"]["pct"], 69.5)
        self.assertAlmostEqual(result["five_hour"]["pct"], 81)

    def test_bad_json_empty_file_and_bad_values_are_missing(self):
        p = self.cfg / "bridge-transcript-cse_fixture.jsonl"
        for content in ("{bad\n", "", '{"unifiedWindows":{"seven_day":{"utilization":1.2.3}}}\n'):
            p.write_text(content)
            self.assertNotIn("total", self.read()["claude"])

    def test_epoch_milliseconds_absurd_and_string_times(self):
        self.event({"seven_day": {"utilization": 0.5, "resetsAt": str(self.NOW + 800)},
                    "five_hour": {"utilization": 0.5, "resetsAt": (self.NOW + 1000) * 1000}})
        c = self.read()["claude"]
        self.assertIn("total", c)
        self.assertIn("five_hour", c)
        self.assertIsNone(quota.epoch(10 ** 10000))

    def test_cache_only_fable_name_and_done_flag(self):
        cache = {"fetchedAtMs": self.NOW * 1000,
                 "utilization": {"seven_day": {"utilization": 71, "resets_at": self.NOW + 3600},
                                 "limits": [{"kind": "weekly_scoped", "scope": {"model": {"display_name": "Fable"}},
                                             "percent": 96, "resets_at": self.NOW + 3600}]}}
        (self.cfg / ".claude.json").write_text(json.dumps({"cachedUsageUtilization": cache}))
        result = self.read()
        self.assertEqual(result["claude"]["scoped"]["src"], "cache")
        self.assertEqual(quota.tier(result, {}, self.NOW), ("save", ["fable-done"]))

    def test_inferred_scoped_and_pre_reset_tier(self):
        self.event({"seven_day": self.win(0.5, self.NOW + 3600)})
        c = self.read()["claude"]
        self.assertEqual(c["scoped"]["src"], "inferred")
        self.assertEqual(quota.tier({"claude": c}, {}, self.NOW)[0], "pre-reset-burn")

    def test_codex_weekly_choice_expired_primary_and_string_fields(self):
        p = self.codex / "sessions" / "nested" / "s.jsonl"
        p.write_text(json.dumps({"rate_limits": {"primary": {"used_percent": 22, "window_minutes": 300,
             "resets_at": self.NOW + 100}}}) + "\n")
        self.assertFalse(self.read()["gpt"]["weekly"])
        p.write_text(json.dumps({"rate_limits": {"primary": {"used_percent": "88", "window_minutes": "300",
             "resets_at": self.NOW + 100}, "secondary": {"used_percent": 40, "window_minutes": 10080,
             "resets_at": self.NOW + 200}}}) + "\n")
        self.assertTrue(self.read()["gpt"]["weekly"])
        p.write_text(json.dumps({"rate_limits": {"primary": {"used_percent": "bad", "window_minutes": "x",
             "resets_at": "bad"}}}) + "\n")
        self.assertTrue(self.read()["gpt"].get("expired"))

    def test_tier_boundaries_flags_and_over_100(self):
        for value, expected in ((69.5, "normal"), (89.5, "burn-fable"), (94.9, "lead-only"),
                                (101, "lead-only")):
            reading = {"claude": {"total": {"pct": value, "resets_at": self.NOW + 400000},
                                   "scoped": {"pct": 50}, "five_hour": {"pct": 80}}, "gpt": {}}
            tier, flags = quota.tier(reading, {}, self.NOW)
            self.assertEqual(tier, expected)
            self.assertIn("5h-tight", flags)
        self.assertEqual(quota.tier({}, {}, self.NOW)[0], "unknown")

    def test_quota_changed_first_repeat_and_unknown_does_not_overwrite(self):
        self.event({"seven_day": self.win(0.8), "five_hour": self.win(0.9),
                    "seven_day_overage_included": self.win(0.96)})
        first = json.loads(self.tw("quota", "--config-dir", str(self.cfg), "--codex-dir", str(self.codex), "--json").stdout)
        self.assertEqual(first["changed"], "yes")
        second = json.loads(self.tw("quota", "--config-dir", str(self.cfg), "--codex-dir", str(self.codex), "--json").stdout)
        self.assertEqual(second["changed"], "no")
        saved = (self.dir / ".teamwork" / "quota-state.json").read_text()
        (self.cfg / "bridge-transcript-cse_fixture.jsonl").write_text("bad json\n")
        unknown = json.loads(self.tw("quota", "--config-dir", str(self.cfg), "--codex-dir", str(self.codex), "--json").stdout)
        self.assertEqual(unknown["tier"], "unknown")
        self.assertEqual((self.dir / ".teamwork" / "quota-state.json").read_text(), saved)

    def test_quota_never_fails_on_broken_project_files(self):
        self.event({"seven_day": self.win(0.8)})
        q = lambda: self.tw("quota", "--config-dir", str(self.cfg), "--codex-dir", str(self.codex), "--json")
        state = self.dir / ".teamwork" / "quota-state.json"
        for bad in ('{"tier": "burn-fable", "flags": [1, "a"]}', '{"tier": "burn-fable", "flags": 5}', "[1]"):
            state.write_text(bad)
            self.assertEqual(json.loads(q().stdout)["changed"], "yes")  # broken = no earlier run
        st_file = self.dir / ".teamwork" / "state.json"
        good = st_file.read_text()
        st_file.write_text(json.dumps({**json.loads(good), "prefs": ["x"]}))
        self.assertEqual(json.loads(q().stdout)["tier"], "burn-fable")
        st_file.write_text("[1]")
        self.assertEqual(json.loads(q().stdout)["tier"], "burn-fable")
        st_file.write_text(good)
        state.write_text('{"tier": "normal", "flags": []}')
        log_file = self.dir / ".teamwork" / "log.jsonl"
        log_file.rename(self.dir / "log.moved") if log_file.exists() else None
        log_file.mkdir()  # the journal cannot be written: a note, not a crash
        self.assertTrue(any("log not written" in n for n in json.loads(q().stdout)["notes"]))

    def test_quota_folders_read_apart_and_float_threshold(self):
        other = self.dir / "fake-config-2"
        other.mkdir()
        (other / "bridge-transcript-cse_other.jsonl").write_text(json.dumps(
            {"timestamp": self.NOW - 10, "rate_limit_info": {"unifiedWindows": {"seven_day": self.win(0.95)}}}) + "\n")
        self.event({"seven_day": self.win(0.10), "seven_day_overage_included": self.win(0.99)}, timestamp=self.NOW - 5)
        out = json.loads(self.tw("quota", "--config-dir", str(self.cfg), "--config-dir", str(other), "--codex-dir",
                                 str(self.codex), "--json").stdout)
        self.assertEqual(out["tier"], "lead-only")  # the other account is at 95%: never mixed with this one
        self.event({"seven_day": self.win(0.57)})
        self.assertEqual(quota.tier(self.read(), {"quota_burn_at": 57}, self.NOW)[0], "burn-fable")
        self.assertNotEqual(self.tw("prefs", "--set", "quota_burn_at=95", ok=False).returncode, 0)  # above lead-only

    def test_quota_stale_cache_folder_does_not_hide_a_live_one(self):
        old = self.dir / "fake-config-old"
        old.mkdir()
        real_now = time.time()
        cache = {"fetchedAtMs": (real_now - 10 * 3600) * 1000,
                 "utilization": {"seven_day": {"utilization": 95, "resets_at": real_now + 86400}}}
        (old / ".claude.json").write_text(json.dumps({"cachedUsageUtilization": cache}))
        self.event({"seven_day": self.win(0.75)})
        out = json.loads(self.tw("quota", "--config-dir", str(old), "--config-dir", str(self.cfg), "--codex-dir",
                                 str(self.codex), "--json").stdout)
        self.assertEqual(out["tier"], "burn-fable")

    def test_quota_old_event_and_newer_cache(self):
        real_now = time.time()
        reset = real_now + 2 * 86400
        (self.cfg / "bridge-transcript-cse_fixture.jsonl").write_text(json.dumps(
            {"timestamp": real_now - 72 * 3600,
             "rate_limit_info": {"unifiedWindows": {"seven_day": {"utilization": 0.4, "resetsAt": reset}}}}) + "\n")
        q = lambda: json.loads(self.tw("quota", "--config-dir", str(self.cfg), "--codex-dir", str(self.codex),
                                       "--json").stdout)
        self.assertEqual(q()["tier"], "unknown")  # three days old: not a reading to route by
        cache = {"fetchedAtMs": (real_now - 600) * 1000,
                 "utilization": {"seven_day": {"utilization": 85, "resets_at": reset}}}
        (self.cfg / ".claude.json").write_text(json.dumps({"cachedUsageUtilization": cache}))
        self.assertEqual(q()["tier"], "burn-fable")  # the newer, higher reading of the same window

    def test_quota_unreadable_prefs_and_text_notes(self):
        self.event({"seven_day": self.win(0.75)})
        q = lambda *x: self.tw("quota", "--config-dir", str(self.cfg), "--codex-dir", str(self.codex), *x).stdout
        self.tw("prefs", "--set", "quota_burn_at=50", "quota_lead_only_at=60")
        self.assertIn("tier lead-only", q())
        st_file = self.dir / ".teamwork" / "state.json"
        st_file.write_text("{broken")
        out = json.loads(q("--json"))
        self.assertEqual((out["changed"], json.loads((self.dir / ".teamwork" / "quota-state.json").read_text())["tier"]),
                         ("no", "lead-only"))  # defaults are not recorded as a change
        self.assertIn("prefs unreadable", q().split("notes: ", 1)[1])
        (self.dir / ".teamwork" / "quota-state.json").write_text('{"tier": "\\ud800", "flags": [NaN]}')
        json.loads(q("--json"))  # still valid JSON, no crash

    def test_tw_without_quota_module_still_runs(self):
        alone = Path(tempfile.mkdtemp(dir=self.dir))
        shutil.copy2(TW, alone / "tw.py")
        r = subprocess.run([sys.executable, str(alone / "tw.py"), "version"], cwd=self.dir, env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([sys.executable, str(alone / "tw.py"), "quota"], cwd=self.dir, env=self.env,
                           capture_output=True, text=True)
        self.assertEqual((r.returncode, "quota.py is missing" in r.stdout), (0, True))

    def test_quota_threshold_prefs_validate_numeric_range(self):
        self.tw("prefs", "--set", "quota_burn_at=68")
        self.assertEqual(self.state()["prefs"]["quota_burn_at"], 68)
        self.tw("prefs", "--set", "quota_gpt_tight=abc", ok=False)
        self.tw("prefs", "--set", "quota_gpt_tight=101", ok=False)


class ModelLog(TwCase):
    def setUp(self):
        super().setUp()
        self.tw("task", "add", "T7", "--title", "test task")

    def log_one(self, result="redo", model="fable", typ="code"):
        return self.tw("model-log", "--task", "T7", "--type", typ, "--model", model,
                       "--result", result, "--why", "fixture")

    def test_record_validation_and_proposed_deny(self):
        self.log_one("ok")
        self.assertEqual(len((self.dir / ".teamwork" / "model-log.jsonl").read_text().splitlines()), 1)
        self.tw("model-log", "--task", "T7", "--type", "code", "--model", "fable", "--result", "redo", "--why", "x", ok=False)
        self.tw("model-log", "--task", "T404", "--type", "code", "--model", "fable", "--result", "redo", "--why", "x", ok=False)
        self.log_one(); self.log_one()
        summary = json.loads(self.tw("model-log", "--summary", "--json").stdout)
        self.assertTrue(summary["combinations"][0]["proposed"])
        self.assertEqual(self.state()["model_deny"][0]["type"], "code")

    def test_gpt_code_needs_user_deny_and_allow_removes(self):
        for _ in range(3):
            self.log_one(model="gpt")
        self.tw("model-log", "--summary", "--json")
        self.assertFalse(self.state().get("model_deny"))
        self.tw("model-log", "--deny", "code", "--model", "gpt", "--user-said", "please avoid")
        self.assertTrue(self.state()["model_deny"])
        self.tw("model-log", "--allow", "code", "--model", "gpt", "--user-said", "try again")
        self.assertNotIn("Unsuitable", self.tw("board").stdout)
        self.assertEqual(json.loads(self.tw("model-log", "--summary", "--json").stdout)["model_deny"], [])

    def test_summary_keeps_unicode_line_separators(self):
        for why in ("a\u2028b", "c\u0085d", "plain"):
            self.tw("model-log", "--task", "T7", "--type", "docs-zh", "--model", "fable", "--result", "redo",
                    "--why", why)
        out = json.loads(self.tw("model-log", "--summary", "--json").stdout)
        self.assertEqual(out["combinations"][0]["count"], 3)
        self.assertNotIn("skipped", out)

    def test_allow_survives_a_cut_or_emptied_log(self):
        path = self.dir / ".teamwork" / "model-log.jsonl"
        deny = lambda: [(x["type"], x["model"]) for x in
                        json.loads(self.tw("model-log", "--summary", "--json").stdout)["model_deny"]]
        for _ in range(4):
            self.log_one()
        self.tw("model-log", "--summary")
        self.tw("model-log", "--allow", "code", "--model", "fable", "--user-said", "again")
        self.log_one("ok")
        path.write_text("\n".join(path.read_text().split("\n")[-3:]))  # head cut: the marker stays
        self.assertEqual(deny(), [])
        path.write_text("")  # emptied: new tries count, by time
        for _ in range(3):
            self.log_one()
        self.assertEqual(deny(), [("code", "fable")])
        path.write_text(path.read_text().rstrip("\n"))  # no final newline: the next record still parses
        self.log_one()
        self.assertNotIn("skipped", json.loads(self.tw("model-log", "--summary", "--json").stdout))

    def test_summary_skips_bad_lines(self):
        self.log_one(); self.log_one()
        path = self.dir / ".teamwork" / "model-log.jsonl"
        path.write_text(path.read_text() + "\n{half\n" + json.dumps({"model": "fable"}) + "\n")
        self.log_one()
        out = json.loads(self.tw("model-log", "--summary", "--json").stdout)
        self.assertEqual((out["combinations"][0]["count"], out["skipped"]), (3, 2))
        self.assertTrue(out["combinations"][0]["proposed"])
        self.assertNotEqual(self.tw("model-log", "--deny", "code", "--allow", "test", "--model", "gpt",
                                    "--user-said", "x", ok=False).returncode, 0)

    def test_allowed_again_needs_new_redos(self):
        for _ in range(3):
            self.log_one()
        self.tw("model-log", "--summary")
        self.tw("model-log", "--allow", "code", "--model", "fable", "--user-said", "give it another go")
        self.assertEqual(json.loads(self.tw("model-log", "--summary", "--json").stdout)["model_deny"], [])
        self.log_one("ok"); self.log_one(); self.log_one()
        deny = json.loads(self.tw("model-log", "--summary", "--json").stdout)["model_deny"]
        self.assertEqual([(x["type"], x["model"]) for x in deny], [("code", "fable")])
        self.assertIn("Unsuitable", self.tw("board").stdout)

    def test_deny_rebuild_snapshot_and_route_display(self):
        self.tw("model-log", "--deny", "test", "--model", "opus", "--user-said", "avoid")
        self.tw("task", "set", "T7", "--route", "gpt")
        self.assertIn("(gpt)", (self.dir / ".teamwork" / "BOARD.md").read_text())
        self.tw("task", "set", "T7", "--route", "")
        self.assertNotIn("(gpt)", (self.dir / ".teamwork" / "BOARD.md").read_text())
        self.tw("rebuild")
        self.assertEqual(self.state()["model_deny"][0]["model"], "opus")



class LedgerStates(TwCase):
    def setUp(self):
        super().setUp()
        self.tw("prefs", "--set", "language=zh")

    def test_request_transitions_evidence_reason_and_close_landed(self):
        self.tw("req", "add", "decision")
        self.tw("req", "set", "R1", "--status", "waiting_user")
        self.tw("req", "set", "R1", "--status", "open")
        self.assertNotEqual(self.tw("req", "set", "R1", "--status", "landed", ok=False).returncode, 0)
        self.tw("req", "set", "R1", "--status", "wont", "--reason", "scope")
        self.tw("req", "set", "R1", "--status", "open")
        self.assertNotEqual(self.tw("req", "set", "R1", "--status", "wont", ok=False).returncode, 0)
        self.tw("req", "set", "R1", "--status", "reported")
        self.assertNotEqual(self.tw("req", "set", "R1", "--status", "landed", ok=False).returncode, 0)
        self.tw("req", "set", "R1", "--status", "landed", "--evidence", "abc")
        self.tw("req", "close", "R1", "--user-said", "accepted")
        self.tw("rebuild")
        self.assertEqual(self.state()["requests"]["R1"]["status"], "closed")

    def test_remark_receipt_board_and_promotion(self):
        out = self.tw("req", "add", "note", "--remark").stderr
        self.assertIn("备注 R1", out)
        self.assertIn("备注（不派）", (self.dir / ".teamwork" / "BOARD.md").read_text())
        self.assertNotIn("R1", self.tw("req", "list").stdout)
        self.tw("req", "set", "R1", "--status", "open")
        self.assertIn("R1", self.tw("req", "list").stdout)

    def test_migrate_preview_apply_and_repeat(self):
        self.tw("req", "add", "已合并 feature", "--where", "commit")
        self.tw("req", "set", "R1", "--status", "reported")
        preview = json.loads(self.tw("req", "migrate").stdout.split("migrate:")[0])
        self.assertEqual(preview["R1"]["suggested"], "landed")
        self.tw("req", "migrate", "--apply", "--user-said", "old ledger")
        self.assertEqual(self.state()["requests"]["R1"]["status"], "landed")
        self.assertEqual(json.loads(self.tw("req", "migrate").stdout.split("migrate:")[0]), {})

    def test_default_requires_undoable_and_board(self):
        self.tw("ask", "choose")
        self.assertNotEqual(self.tw("ask", "--default", "1", "choose A", ok=False).returncode, 0)
        self.tw("ask", "--default", "1", "choose A", "--undoable")
        board = (self.dir / ".teamwork" / "BOARD.md").read_text()
        self.assertIn("按默认做：choose A（可改）", board)
        self.assertFalse(self.state()["questions"][0]["answered"])
        self.tw("ask", "--answer", "1", "yes")
        self.assertTrue(self.state()["decisions"][0]["answered"])
        self.tw("rebuild")
        self.assertTrue(self.state()["decisions"][0]["answered"])

    def test_promise_evidence_and_rebuild(self):
        self.tw("promise", "add", "deliver", "--req", "R1") if "R1" in self.state()["requests"] else self.tw("promise", "add", "deliver")
        self.assertNotEqual(self.tw("promise", "done", "P1", ok=False).returncode, 0)
        self.tw("promise", "done", "P1", "--evidence", "commit")
        self.tw("rebuild")
        self.assertEqual(self.state()["promises"][0]["status"], "done")

    def test_dispatch_version_and_task_message_and_deliverable_dedupe(self):
        self.tw("thread", "add", "worker")
        self.tw("plan", "approve", "--user-said", "yes")
        self.tw("task", "add", "T1", "--title", "goal", "--owner", "worker", "--accept", "done")
        first = self.tw("task-msg", "T1", "--dispatch")
        self.assertIn("Dispatch: v1", first.stdout)
        self.assertEqual(self.state()["tasks"]["T1"]["dispatch_v"], 1)
        self.tw("deliverable", "add", "d", "--task", "T1", "--link", "one")
        out = self.tw("deliverable", "add", "d2", "--task", "T1", "--link", "two")
        self.assertIn("updated D1 (T1 v1)", out.stderr)
        self.assertEqual(len(self.state()["deliverables"]), 1)
        self.assertEqual(self.state()["deliverables"][0]["link"], "two")


class FeatureLedger(TwCase):
    def test_feature_add_set_list_and_rebuild(self):
        self.tw("req", "add", "need feature")
        self.assertNotEqual(self.tw("feature", "add", ok=False).returncode, 0)
        self.tw("feature", "add", "Feature one", "--phase", "P1", "--req", "R1")
        self.assertNotEqual(self.tw("feature", "set", "F1", "--status", "landed", ok=False).returncode, 0)
        self.tw("feature", "set", "F1", "--status", "landed", "--evidence", "commit abc")
        self.tw("rebuild")
        self.assertEqual(self.state()["features"]["F1"]["status"], "landed")
        self.tw("prefs", "--set", "language=zh")
        self.assertIn("功能清单", self.tw("board").stdout)
        self.assertIn("功能清单", self.tw("board", "--share").stdout)
        self.assertIn("F1", self.tw("feature", "list").stdout)

    def test_reconcile_wrapup_sprint(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("req", "add", "未分派")
        self.assertNotEqual(self.tw("reconcile", "--strict", ok=False).returncode, 0)
        self.assertEqual(self.tw("reconcile").returncode, 0)
        before = self.state()["reconcile"]
        self.tw("rebuild")
        self.assertEqual(self.state()["reconcile"], before)
        self.assertIn("收工自查", self.tw("wrapup").stdout)
        self.assertIn("|", self.tw("sprint").stdout)
        self.assertNotEqual(self.tw("prefs", "--set", "reconcile_every=invalid", ok=False).returncode, 0)


class ReportCards(TwCase):
    """T56 block 3: report cards, document links, intro, the progress chain, check-msg --user, standing
    requirements, the bash-guard error log and the merged mark."""

    T0, T1, T2 = "2026-03-01T09:00:00", "2026-03-02T09:00:00", "2026-03-03T09:00:00"

    def at(self, when):
        self.env["TW_NOW"] = when

    def fill(self):
        """Before T1: R1 reported, F1 landed. After T1: R2 waiting, R3 dropped, F2 building, T1 dispatched,
        one open question on its default, one GPT entry in the model log."""
        self.at(self.T0)
        self.tw("thread", "add", "worker")
        self.tw("plan", "approve", "--user-said", "yes")
        self.tw("req", "add", "first thing")
        self.tw("req", "set", "R1", "--status", "reported")
        self.tw("feature", "add", "Old feature", "--req", "R1")
        self.tw("feature", "set", "F1", "--status", "landed", "--evidence", "x")
        self.at(self.T2)
        self.tw("req", "add", "second thing")
        self.tw("req", "set", "R2", "--status", "waiting_user")
        self.tw("req", "add", "third thing")
        self.tw("req", "drop", "R3", "--user-said", "never mind")
        self.tw("feature", "add", "New feature", "--req", "R2")
        self.tw("feature", "set", "F2", "--status", "building")
        self.tw("task", "add", "T1", "--title", "build it", "--owner", "worker", "--accept", "works")
        self.tw("task", "set", "T1", "--status", "dispatched")
        self.tw("ask", "which colour")
        self.tw("ask", "--default", "1", "blue", "--undoable")
        self.tw("model-log", "--task", "T1", "--type", "code", "--model", "gpt", "--result", "ok", "--why", "fixture")

    def test_card_english_sections_ledger_line_and_since(self):
        self.fill()
        out = self.tw("card", "--kind", "done", "--head", "Shipped the thing", "--since", self.T1,
                      "--link", "https://example.test/pr").stdout
        self.assertEqual(out.splitlines()[0], "[Delivery] Shipped the thing")
        for head in ("## Done", "## In progress", "## Waiting on you"):
            self.assertIn(head, out)
        done = out.split("## Done")[1].split("## In progress")[0]
        self.assertNotIn("Old feature", done)  # landed before --since
        self.assertNotIn("first thing", done)
        doing = out.split("## In progress")[1].split("## Waiting on you")[0]
        self.assertIn("F2 New feature", doing)
        self.assertIn("T1 build it", doing)
        waiting = out.split("## Waiting on you")[1]
        self.assertIn("R2 second thing", waiting)
        self.assertIn("which colour (going ahead with the default: blue)", waiting)
        self.assertEqual(out.splitlines()[-1],
                         "Ledger +2/−1 · waiting on you: 2 · GPT did 1 · https://example.test/pr")
        everything = json.loads(self.tw("card", "--kind", "status", "--head", "x", "--json").stdout)
        self.assertEqual(sorted(everything), ["doing", "done", "head", "kind", "waiting"])  # English keys, no ledger
        self.assertIn("F1 Old feature", everything["done"])
        self.assertIn("R1 first thing", everything["done"])

    def test_ledger_line_only_when_asked_for(self):
        self.tw("req", "add", "one")
        line = "Ledger +"
        self.assertNotIn(line, self.tw("card", "--kind", "status", "--head", "h").stdout)  # default: wrapup
        self.assertIn(line, self.tw("card", "--kind", "status", "--head", "h", "--ledger-line").stdout)
        self.assertIn(line, self.tw("card", "--kind", "done", "--head", "h").stdout)
        self.tw("prefs", "--set", "ledger_line_every=report")
        for kind in ("compare", "plan", "read", "status"):
            self.assertIn(line, self.tw("card", "--kind", kind, "--head", "h").stdout, kind)
        self.assertNotEqual(self.tw("prefs", "--set", "ledger_line_every=always", ok=False).returncode, 0)
        self.assertEqual(self.state()["prefs"]["ledger_line_every"], "report")

    def test_card_chinese_kinds_and_labels(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("req", "add", "要做功能")
        self.tw("docs", "set", "ledger", "--link", "https://example.test/ledger")
        for kind, name in (("compare", "对照分析"), ("plan", "新方案"), ("read", "素材已读"), ("done", "交付"),
                           ("status", "进展")):
            self.assertEqual(self.tw("card", "--kind", kind, "--head", "结论").stdout.splitlines()[0], f"[{name}] 结论")
        out = self.tw("card", "--kind", "done", "--head", "已完成").stdout
        for word in ("## 做完了", "## 在做", "## 等你", "（无）"):
            self.assertIn(word, out)
        self.assertEqual(out.splitlines()[-1], "台账 +1/−0 · 等你 0 件 · GPT 做了 0 · https://example.test/ledger")

    def test_last_report_at_and_since_last_report(self):
        self.at(self.T0)
        self.tw("req", "add", "early")
        self.assertNotIn("last_report_at", self.state())
        self.at(self.T1)
        self.tw("card", "--kind", "status", "--head", "first card")
        self.assertTrue(self.state()["last_report_at"].startswith("2026-03-02T09:00:00"))
        self.at(self.T2)
        self.tw("req", "add", "late")
        out = self.tw("card", "--kind", "done", "--head", "second", "--since", "last-report").stdout
        self.assertIn("Ledger +1/−0", out)  # only the request added after the first card
        self.assertTrue(self.state()["last_report_at"].startswith("2026-03-03T09:00:00"))
        self.assertIn("--since", self.tw("card", "--kind", "done", "--head", "x", "--since", "yesterday", ok=False).stderr)

    def test_required_args(self):
        self.assertNotEqual(self.tw("card", ok=False).returncode, 0)
        self.assertNotEqual(self.tw("card", "--kind", "done", ok=False).returncode, 0)
        self.assertNotEqual(self.tw("card", "--kind", "other", "--head", "x", ok=False).returncode, 0)
        self.assertNotEqual(self.tw("docs", "set", "board", ok=False).returncode, 0)
        self.assertNotEqual(self.tw("docs", "set", "--link", "https://example.test/x", ok=False).returncode, 0)
        self.assertNotEqual(self.tw("docs", "set", "notes", "--link", "https://example.test/x", ok=False).returncode, 0)
        self.assertNotEqual(self.tw("memory", "--standing", ok=False).returncode, 0)
        self.assertNotEqual(self.tw("memory", ok=False).returncode, 0)

    def test_docs_set_list_and_board_top(self):
        self.assertEqual(self.tw("docs", "list").stdout.strip(), "(none)")
        self.assertNotIn("example.test", self.tw("board").stdout)
        self.tw("docs", "set", "board", "--link", "https://example.test/board", "--note", "where things stand")
        self.tw("docs", "set", "features", "--link", "https://example.test/features")
        self.tw("docs", "set", "board", "--link", "https://example.test/board2", "--note", "where things stand")
        listed = self.tw("docs", "list").stdout
        self.assertIn("Board (board): https://example.test/board2 — where things stand", listed)
        self.assertIn("Features (features): https://example.test/features", listed)
        self.assertNotIn("ledger", listed.lower())
        board = self.tw("board").stdout
        self.assertIn("- [Board](https://example.test/board2) — where things stand", board)
        self.assertLess(board.index("example.test/board2"), board.index("## "))  # at the top
        self.tw("prefs", "--set", "language=zh")
        self.assertIn("- [看板](https://example.test/board2)", self.tw("board").stdout)
        self.assertIn("功能清单 (features)", self.tw("docs", "list").stdout)

    def test_intro(self):
        self.tw("req", "add", "one")
        self.tw("req", "add", "two")
        self.tw("feature", "add", "Feature one", "--req", "R1")
        out = self.tw("intro").stdout
        self.assertIn("Recorded 2 requests and 1 features", out)
        self.assertIn("Your requests (not closed)", out)
        self.assertIn("See: BOARD.md", out)
        self.tw("docs", "set", "board", "--link", "https://example.test/board")
        self.tw("prefs", "--set", "language=zh")
        out = self.tw("intro").stdout
        self.assertIn("已记下 2 件事、1 个功能", out)
        self.assertIn("看板栏目：", out)
        self.assertIn("看板 https://example.test/board", out)
        self.assertNotIn("BOARD.md", out)

    def chain(self, rid="R1"):
        lines = self.tw("board").stdout.splitlines()
        i = next(n for n, x in enumerate(lines) if x.startswith(f"- **{rid}**"))
        return lines[i + 1].strip()

    def test_progress_chain_and_merged_mark(self):
        self.tw("thread", "add", "worker")
        self.tw("plan", "approve", "--user-said", "yes")
        self.tw("task", "add", "T1", "--title", "build", "--owner", "worker", "--accept", "works")
        self.tw("req", "add", "make it", "--tasks", "T1")
        self.assertEqual(self.chain(), "- recorded✓ → dispatched· → accepted· → review· → merged· → reported·")
        self.tw("task", "set", "T1", "--status", "dispatched", walk=False)
        self.assertEqual(self.chain(), "- recorded✓ → dispatched✓ → accepted· → review· → merged· → reported·")
        self.tw("task", "set", "T1", "--status", "working", walk=False)
        self.assertEqual(self.chain(), "- recorded✓ → dispatched✓ → accepted✓ → review· → merged· → reported·")
        self.tw("task", "set", "T1", "--status", "review", walk=False)
        self.assertEqual(self.chain(), "- recorded✓ → dispatched✓ → accepted✓ → review✓ → merged· → reported·")
        self.assertNotEqual(self.tw("task", "set", "T1", "--merged", "not-a-sha", ok=False).returncode, 0)
        self.assertNotIn("merged_at", self.state()["tasks"]["T1"])
        self.tw("task", "set", "T1", "--merged", "ABC1234")
        k = self.state()["tasks"]["T1"]
        self.assertEqual(k["merged_sha"], "abc1234")
        self.assertTrue(k["merged_at"])
        self.assertEqual(self.chain(), "- recorded✓ → dispatched✓ → accepted✓ → review✓ → merged✓ → reported·")
        self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "worker/subagent")
        self.tw("task", "set", "T1", "--status", "done", walk=False)
        self.tw("req", "set", "R1", "--status", "reported")
        self.assertEqual(self.chain(), "- recorded✓ → dispatched✓ → accepted✓ → review✓ → merged✓ → reported✓")
        self.tw("prefs", "--set", "language=zh")
        self.assertEqual(self.chain(), "- 记台账✓ → 派✓ → 接单✓ → 评审✓ → 合并✓ → 回报✓")
        self.tw("req", "close", "R1", "--user-said", "fine")
        self.assertNotIn("记台账", self.tw("board").stdout)  # closed requests leave the board, chain included

    def user_msg(self, text, *extra, ok=True):
        f = self.dir / "user-msg.md"
        f.write_text(text, encoding="utf-8")
        return self.tw("check-msg", "--user", str(f), *extra, ok=ok)

    def test_check_msg_user(self):
        clean = self.user_msg("The login page is done. 2026 plans and 1234567 users are unchanged; see the board.")
        self.assertEqual((clean.stdout.strip(), clean.stderr), ("ok (user)", ""))
        for text, kind in (("Fixed in a1b2c3d, ready to try.", "commit-id"),
                           ("I ran:\n$ python3 run.py\nand it passed", "command-output"),
                           ("It failed with Traceback (most recent call last): ...", "command-output"),
                           ("Here:\n```bash\nls\n```", "command-output"),
                           ("The file is at /home/someone/project/a.txt", "local-path"),
                           ("Logs are in /home/someone/logs", "local-path"),
                           ("Saved to C:\\Users\\someone\\a.txt", "local-path")):
            r = self.user_msg(text)  # a warning: exit 0
            self.assertIn(f"warning {kind}", r.stderr, text)
            self.assertEqual(self.user_msg(text, "--strict", ok=False).returncode, 1, text)
        self.assertEqual(self.user_msg("All good.", "--strict").returncode, 0)
        data = json.loads(self.user_msg("Fixed in a1b2c3d at /home/someone/x", "--json").stdout)
        self.assertEqual((data["ok"], data["mode"]), (False, "user"))
        self.assertEqual({x["kind"] for x in data["findings"]}, {"commit-id", "local-path"})
        self.assertNotEqual(self.user_msg("x", "--prompt", ok=False).returncode, 0)

    def test_standing_in_task_msg_and_check_msg(self):
        self.tw("thread", "add", "worker")
        self.tw("plan", "approve", "--user-said", "yes")
        self.tw("task", "add", "T1", "--title", "goal", "--owner", "worker", "--accept", "works")
        self.assertNotIn("Standing:", self.tw("task-msg", "T1").stdout)
        self.assertEqual(self.tw("memory", "--standing-list").stdout.strip(), "(none)")
        msg = self.dir / "task.md"
        msg.write_text(self.tw("task-msg", "T1").stdout, encoding="utf-8")
        self.assertEqual(self.tw("check-msg", str(msg)).stdout.strip(), "ok (message)")  # nothing standing yet
        self.assertEqual(self.tw("memory", "--standing", "Reports in  plain\nwords").stdout.strip(),
                         "Standing: Reports in plain words")
        self.tw("memory", "--standing", "No new dependencies")
        self.tw("memory", "--standing", "No new dependencies")  # said twice: kept once
        self.assertEqual(self.state()["standing"], ["Reports in plain words", "No new dependencies"])
        self.assertEqual(self.tw("memory", "--standing-list").stdout.splitlines(),
                         ["- Reports in plain words", "- No new dependencies"])
        self.assertNotEqual(self.tw("memory", "x", "--standing", "--never-raise", ok=False).returncode, 0)
        r = self.tw("check-msg", str(msg))  # the old message carries none: a warning, not a failure
        self.assertIn("warning standing:", r.stdout)
        out = self.tw("task-msg", "T1").stdout
        self.assertEqual(out.splitlines()[-2:], ["Standing: Reports in plain words", "Standing: No new dependencies"])
        msg.write_text(out, encoding="utf-8")
        self.assertEqual(self.tw("check-msg", str(msg)).stdout.strip(), "ok (message)")
        self.tw("prefs", "--set", "language=zh")
        out = self.tw("task-msg", "T1").stdout
        self.assertIn("长期要求：Reports in plain words", out)
        msg.write_text(out, encoding="utf-8")
        self.assertNotIn("standing", self.tw("check-msg", str(msg)).stdout)

    def guard(self, raw, **env):
        return subprocess.run([sys.executable, str(TW), "bash-guard"], input=raw, capture_output=True, text=True,
                              cwd=self.dir, env={**self.env, **env})

    def test_bash_guard_error_log_and_selfcheck_count(self):
        log = self.dir / "guard-logs" / "nested" / "guard-errors.jsonl"  # the folder does not exist yet
        self.env["TW_GUARD_LOG"] = str(log)
        call = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        self.assertEqual((self.guard(call).returncode, self.guard(call).stdout), (0, ""))
        self.assertFalse(log.exists())  # ordinary work writes nothing
        self.assertNotIn("guard_errors", self.tw("selfcheck").stdout)
        self.at("2026-03-10T09:00:00")
        r = self.guard(call, TW_TEST_GUARD_FAULT="boom " + "x" * 400)  # a bug inside the guard: still allowed
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        r = self.guard("not json")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([x["type"] for x in rows], ["RuntimeError", "JSONDecodeError"])
        self.assertTrue(rows[0]["at"].startswith("2026-03-10T09:00:00"))
        self.assertTrue(rows[0]["message"].startswith("boom"))
        self.assertLessEqual(len(rows[0]["message"]), 200)
        with log.open("a", encoding="utf-8") as f:  # an old entry and a broken line are not counted
            f.write(json.dumps({"at": "2026-02-01T09:00:00+00:00", "type": "X", "message": "old"}) + "\nnot json\n")
        self.at("2026-03-12T09:00:00")
        out = self.tw("selfcheck").stdout
        self.assertIn("guard_errors: bash-guard had 2 errors in the last 7 days and allowed the command", out)
        item = next(x for x in json.loads(self.tw("selfcheck", "--json").stdout)["items"] if x["item"] == "guard_errors")
        self.assertIs(item["ok"], False)
        self.tw("prefs", "--set", "language=zh")
        self.assertIn("bash-guard 近 7 天出错 2 次，已照常放行", self.tw("selfcheck", "--brief").stdout)
        self.at("2026-03-20T09:00:00")  # more than 7 days later: nothing to report
        self.assertNotIn("guard_errors", self.tw("selfcheck").stdout)
        blocked = self.dir / "a-file"
        blocked.write_text("x")
        r = self.guard(call, TW_TEST_GUARD_FAULT="boom", TW_GUARD_LOG=str(blocked / "sub" / "log.jsonl"))
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))  # the log cannot be written: still silent

    def test_rebuild_keeps_block3_state(self):
        self.fill()
        self.tw("req", "link", "R2", "T1")
        self.tw("task", "set", "T1", "--status", "review")
        self.tw("task", "set", "T1", "--merged", "abc1234def")
        self.tw("docs", "set", "board", "--link", "https://example.test/board", "--note", "the board")
        self.tw("docs", "set", "ledger", "--link", "https://example.test/ledger")
        self.tw("memory", "--standing", "Reports in plain words")
        self.tw("card", "--kind", "done", "--head", "shipped")
        before, board = self.state(), self.tw("board").stdout
        self.tw("rebuild")
        after = self.state()
        for key in ("docs", "standing", "last_report_at"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(after["tasks"]["T1"]["merged_sha"], "abc1234def")
        self.assertEqual(after["tasks"]["T1"]["merged_at"], before["tasks"]["T1"]["merged_at"])
        self.assertEqual(after["prefs"]["ledger_line_every"], "wrapup")
        self.assertEqual(self.tw("board").stdout, board)


if __name__ == "__main__":
    unittest.main()


class ReviewRound1(TwCase):
    """T56 review round 1: crashes and side doors around the user's yes."""
    def setUp(self):
        super().setUp()
        self.tw("prefs", "--set", "language=zh")

    def test_feature_set_links_and_naive_times(self):
        self.tw("req", "add", "a"); self.tw("req", "add", "b")
        self.tw("feature", "add", "x", "--req", "R1")
        self.tw("feature", "set", "F1", "--req", "R2")
        self.assertEqual(self.state()["features"]["F1"]["reqs"], ["R2"])
        self.tw("feature", "set", "F1", "--status", "landed", "--evidence", "abc")
        self.assertIn("F1", self.tw("sprint").stdout)
        self.assertIn("F1", self.tw("sprint", "--since", "2020-01-01").stdout)
        self.assertIn("F1", self.tw("feature", "list", "--done-since", "2020-01-01").stdout)
        self.assertNotEqual(self.tw("sprint", "--since", "garbage", ok=False).returncode, 0)
        self.assertNotEqual(self.tw("feature", "set", "F1", "--status", "dropped", "--reason", " ", ok=False).returncode, 0)

    def test_no_side_door_to_dispatch(self):
        self.tw("task", "add", "T1", "--title", "work")
        self.tw("req", "add", "p")
        self.tw("req", "set", "R1", "--status", "proposed")
        self.tw("req", "set", "R1", "--status", "waiting_user")
        self.tw("req", "link", "R1", "T1", ok=False)
        self.assertNotEqual(self.state()["requests"]["R1"]["status"], "dispatched")
        self.assertNotEqual(self.tw("req", "set", "R1", "--status", "dispatched", ok=False).returncode, 0)
        self.tw("req", "set", "R1", "--status", "dispatched", "--user-said", "go")

    def test_default_answer_is_not_approval(self):
        self.tw("ask", "db?", "--from", "core")
        self.tw("ask", "--default", "1", "sqlite", "--undoable")
        self.tw("ask", "--answer", "1", "use pg")
        dec = self.state()["decisions"][0]
        self.assertTrue(dec["answered"]); self.assertEqual(dec["answer"], "use pg"); self.assertNotIn("confirmed", dec)

    def test_migrate_json_only_records_words(self):
        self.tw("req", "add", "x", "--followup", "等你定")
        self.tw("req", "set", "R1", "--status", "reported")
        self.tw("req", "add", "y", "--followup", "不做了")
        self.tw("req", "set", "R2", "--status", "reported")
        json.loads(self.tw("req", "migrate").stdout)
        self.tw("req", "migrate", "--apply", "--only", "R1", "--user-said", "按建议")
        st = self.state()["requests"]
        self.assertEqual(st["R1"]["status"], "waiting_user"); self.assertEqual(st["R2"]["status"], "reported")
        self.assertEqual(st["R1"]["migrated"]["user_said"], "按建议")

    def test_promise_and_merged_guards(self):
        self.assertNotEqual(self.tw("promise", "add", "", ok=False).returncode, 0)
        self.tw("promise", "add", "report back")
        self.tw("promise", "done", "P1", "--evidence", "sent")
        self.assertNotEqual(self.tw("promise", "done", "P1", "--evidence", "again", ok=False).returncode, 0)
        self.tw("task", "add", "T1", "--title", "work")
        self.assertNotEqual(self.tw("task", "set", "T1", "--merged", "abc1234", ok=False).returncode, 0)


class ReviewRound2(TwCase):
    """T56 review round 2: the user's yes survives detours through waiting_user, wont and open."""
    def test_detours_keep_the_gate(self):
        self.tw("task", "add", "T1", "--title", "work")
        for i, path in enumerate((["proposed", "waiting_user", "open"], ["proposed", "wont", "open"]), 1):
            self.tw("req", "add", f"p{i}")
            for st in path:
                self.tw("req", "set", f"R{i}", "--status", st, *(["--reason", "r"] if st == "wont" else []))
            self.tw("req", "link", f"R{i}", "T1", ok=False)
            self.assertEqual(self.state()["requests"][f"R{i}"]["status"], "open")
            self.tw("req", "link", f"R{i}", "T1", "--user-said", "go")
            self.assertEqual(self.state()["requests"][f"R{i}"]["status"], "dispatched")
        self.tw("req", "add", "q")
        self.tw("req", "set", "R3", "--status", "reported")
        self.tw("req", "set", "R3", "--status", "landed", "--evidence", "e")
        self.tw("req", "set", "R3", "--status", "wont", "--reason", "r")
        self.tw("req", "set", "R3", "--status", "open")
        self.tw("req", "link", "R3", "T1", ok=False)
        self.assertEqual(self.state()["requests"]["R3"]["status"], "open")

    def test_landed_survives_rework_and_imports_carry_markers(self):
        self.tw("thread", "add", "core")
        self.tw("task", "add", "T1", "--title", "w", "--owner", "core", "--accept", "ok")
        self.tw("plan", "approve", "--user-said", "ok")
        self.tw("task", "set", "T1", "--status", "review")
        self.tw("req", "add", "p", "--tasks", "T1")
        self.tw("req", "set", "R1", "--status", "reported")
        self.tw("req", "set", "R1", "--status", "landed", "--evidence", "sha abc")
        self.tw("task", "set", "T1", "--merged", "abc1234")
        self.tw("task", "set", "T1", "--status", "working")
        st = self.state()
        self.assertEqual(st["requests"]["R1"]["status"], "landed")
        self.assertNotIn("merged_sha", st["tasks"]["T1"])
        led = self.dir / "led.md"
        led.write_text("| # | request | where | status |\n|---|---|---|---|\n| R5 | a | - | 已落地 |\n| R6 | b | - | 不做 |\n")
        self.tw("req", "import", str(led))
        st = self.state()["requests"]
        self.assertTrue(st["R5"]["evidence"].startswith("imported")); self.assertTrue(st["R6"]["reason"].startswith("imported"))
        self.assertNotEqual(self.tw("feature", "add", "f", "--status", "landed", "--evidence", " ", ok=False).returncode, 0)


class ReviewRound3(TwCase):
    """T56 review round 3: rework never re-dispatches a gated request; migrate and import set the gate."""
    def setUp(self):
        super().setUp()
        self.tw("thread", "add", "core")
        self.tw("task", "add", "T1", "--title", "w", "--owner", "core", "--accept", "ok")
        self.tw("task", "add", "T2", "--title", "v", "--owner", "core", "--accept", "ok")
        self.tw("plan", "approve", "--user-said", "ok")
        self.tw("task", "set", "T1", "--status", "working")

    def test_detour_to_reported_is_refused_and_rework_keeps_gate(self):
        self.tw("req", "add", "maybe"); self.tw("req", "set", "R1", "--status", "proposed")
        self.tw("req", "link", "R1", "T1")
        self.tw("req", "set", "R1", "--status", "waiting_user"); self.tw("req", "set", "R1", "--status", "open")
        self.assertNotEqual(self.tw("req", "set", "R1", "--status", "reported", ok=False).returncode, 0)
        self.tw("task", "set", "T1", "--status", "review")
        self.tw("req", "set", "R1", "--status", "reported")
        self.tw("task", "set", "T1", "--status", "working")
        self.assertEqual(self.state()["requests"]["R1"]["status"], "reported")

    def test_migrate_and_import_set_gate(self):
        self.tw("task", "set", "T1", "--status", "review")
        self.tw("req", "add", "等你确认", "--tasks", "T1"); self.tw("req", "set", "R1", "--status", "reported")
        self.tw("req", "migrate", "--apply", "--user-said", "迁吧")
        self.assertEqual(self.state()["requests"]["R1"]["gate"], "reported")
        self.tw("req", "set", "R1", "--status", "open")
        self.tw("req", "link", "R1", "T2", ok=False)
        self.assertEqual(self.state()["requests"]["R1"]["status"], "open")
        led = self.dir / "led.md"
        led.write_text("| # | request | where | status |\n|---|---|---|---|\n| R5 | a | - | 等你定 |\n")
        self.tw("req", "import", str(led))
        self.assertEqual(self.state()["requests"]["R5"]["gate"], "imported")

    def test_defaulted_answer_needs_words(self):
        self.tw("ask", "db?", "--from", "core"); self.tw("ask", "--default", "1", "sqlite", "--undoable")
        self.assertNotEqual(self.tw("ask", "--answer", "1", ok=False).returncode, 0)
