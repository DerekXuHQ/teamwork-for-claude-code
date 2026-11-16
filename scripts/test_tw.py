#!/usr/bin/env python3
"""Tests for tw.py. Stdlib only; every test works in a temp project folder.

  python3 scripts/test_tw.py
"""

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


if __name__ == "__main__":
    unittest.main()
