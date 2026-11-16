#!/usr/bin/env python3
"""Tests for tw.py. Stdlib only; every test works in a temp project folder.

  python3 scripts/test_tw.py
"""

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TW = Path(__file__).resolve().parent / "tw.py"


class TwCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.env = {k: v for k, v in os.environ.items() if k != "TEAMWORK_DIR"}
        self.tw("init", "demo", "--goal", "test project")

    def tearDown(self):
        self._tmp.cleanup()

    def tw(self, *args, ok=True):
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
        self.assertNotIn("permission_mode", out["create_session"])
        self.assertTrue(out["worktree"].endswith("proj-worktrees/ui"))
        self.tw("prefs", "--set", "thread_permission_mode=auto")
        out = json.loads(self.tw("thread", "add", "api").stdout)
        self.assertEqual(out["create_session"]["permission_mode"], "auto")
        self.assertNotEqual(self.tw("prefs", "--set", "thread_permission_mode=yolo", ok=False).returncode, 0)

    def test_cloud_thread_has_no_worktree(self):
        out = json.loads(self.tw("thread", "add", "docs", "--kind", "cloud").stdout)
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
        self.assertNotEqual(self.tw("req", "set", "R1", "--status", "reported", ok=False).returncode, 0)
        self.assertNotEqual(self.tw("req", "set", "R1", "--status", "closed", ok=False).returncode, 0)
        self.tw("req", "link", "R1", "T1", "T2")
        self.assertEqual(self.req("R1")["status"], "dispatched")
        self.assertNotEqual(self.tw("req", "link", "R1", "T9", ok=False).returncode, 0)
        self.tw("task", "set", "T1", "--status", "done", "--review", "pass", "--reviewer", "rev")
        self.assertEqual(self.req("R1")["status"], "dispatched")  # T2 still open
        r = self.tw("task", "set", "T2", "--status", "done", "--review", "pass", "--reviewer", "rev")
        self.assertIn("request R1 delivered", r.stderr)
        self.assertEqual(self.req("R1")["status"], "delivered")
        self.assertNotEqual(self.tw("req", "close", "R1", "--confirmed", ok=False).returncode, 0)  # not reported yet
        self.assertNotEqual(self.tw("req", "close", "R1", ok=False).returncode, 0)  # needs a reason
        self.tw("req", "set", "R1", "--status", "reported", "--followup", "ask if blue is right")
        self.assertIn("reported_at", self.req("R1"))
        self.tw("req", "close", "R1", "--confirmed")
        self.assertEqual(self.req("R1")["closed_reason"], "confirmed")

    def test_declined_closes_from_any_state(self):
        self.tw("req", "add", "maybe a dark mode")
        self.tw("req", "close", "R1", "--declined", "--note", "user: not now")
        self.assertEqual(self.req("R1")["status"], "closed")
        self.assertEqual(json.loads(self.tw("req", "list").stdout), {})
        self.assertIn("R1", json.loads(self.tw("req", "list", "--all").stdout))

    def test_board_hook_and_ready_show_unreported(self):
        self.tw("req", "add", "fix login", "--tasks", "T1")
        self.tw("req", "add", "done thing")
        self.tw("req", "add", "dropped")
        self.tw("req", "close", "R3", "--declined")
        self.tw("task", "set", "T1", "--status", "done", "--review", "pass", "--reviewer", "rev")
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
        self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "rev", "--checks", "fail")
        self.assertNotEqual(self.tw("task", "set", "T1", "--status", "done", ok=False).returncode, 0)
        self.tw("task", "set", "T1", "--status", "done", "--checks", "pass")
        self.assertEqual(self.task()["checks"], "pass")

    def test_skip_review_is_recorded(self):
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "docs typo")
        self.assertEqual(self.task()["review"]["result"], "skipped")
        self.assertEqual(self.task()["review"]["note"], "docs typo")

    def test_bool_pref_on_missing_key(self):
        st = self.state()
        del st["prefs"]["require_review"]
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.tw("prefs", "--set", "require_review=false")
        self.assertIs(self.state()["prefs"]["require_review"], False)
        self.tw("prefs", "--set", "require_review=true")
        self.assertIs(self.state()["prefs"]["require_review"], True)

    def test_old_project_without_pref_not_gated(self):
        st = self.state()
        del st["prefs"]["require_review"]
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.tw("task", "set", "T1", "--status", "done")


if __name__ == "__main__":
    unittest.main()
