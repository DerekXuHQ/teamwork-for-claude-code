try:
    from ._base import *
except ImportError:
    from _base import *


class T76BatchB(TwCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="t76B-project-")
        self.dir = Path(self._tmp.name)
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.env = {k: v for k, v in os.environ.items() if k != "TEAMWORK_DIR"}
        self.tw("init", "demo", "--goal", "test project")

    def test_m31_slash_in_glob_class_rejected_cli(self):
        self.assertNotEqual(self.tw("task", "add", "T31", "--touches", "src/[a/b]", ok=False).returncode, 0)

    def test_m32_unicode_casefold_and_normalization_cli(self):
        self.tw("thread", "add", "ui")
        self.tw("thread", "add", "api")
        self.tw("plan", "approve", "--user-said", "approve")
        self.tw("task", "add", "T1", "--owner", "ui", "--touches", "straße")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "STRASSE")
        self.tw("task", "add", "T5", "--owner", "ui", "--touches", "café.py")
        self.tw("task", "add", "T6", "--owner", "api", "--touches", "café.py")
        self.tw("task", "set", "T1", "--status", "working")
        self.tw("task", "set", "T5", "--status", "working")
        ready = json.loads(self.tw("ready").stdout)
        self.assertIn("T2", ready["blocked_by_lease"])
        self.assertIn("T6", ready["blocked_by_lease"])

    def test_m33_literal_brace_before_alternative_still_detects_lease_cli(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("tw_under_test", TW)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module._expand_braces("src/{x}/{a,b}.py"),
                         ["src/{x}/a.py", "src/{x}/b.py"])
        self.tw("thread", "add", "ui"); self.tw("thread", "add", "api")
        self.tw("plan", "approve", "--user-said", "approve")
        self.tw("task", "add", "T1", "--owner", "ui", "--touches", "src/{x}/{a,b}.py")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "src/{x}/a.py")
        self.tw("task", "set", "T1", "--status", "working")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])

    def test_m36_absolute_touches_project_bounds_cli(self):
        self.assertNotEqual(self.tw("task", "add", "T36x", "--touches", "/outside/path", ok=False).returncode, 0)
        self.tw("task", "add", "T36y", "--touches", str(self.dir / "src" / "a.py"))
        self.assertEqual(self.state()["tasks"]["T36y"]["touches"], ["src/a.py"])
        self.assertNotEqual(self.tw("task", "add", "T36z", "--touches",
                                    "{" + str(self.dir / "q.py") + ",/etc/passwd}", ok=False).returncode, 0)
        self.tw("task", "add", "T36folder", "--touches", "lib/")
        self.assertEqual(self.state()["tasks"]["T36folder"]["touches"], ["lib/"])
        self.assertNotEqual(self.tw("task", "add", "T36root", "--touches", str(self.dir), ok=False).returncode, 0)
        nested = self.dir / "nested"
        nested.mkdir()
        result = subprocess.run([sys.executable, str(TW), "task", "add", "T36cwdroot", "--touches", "."],
                                cwd=nested, capture_output=True, text=True,
                                env={**self.env, "TEAMWORK_DIR": str(self.dir / ".teamwork")})
        self.assertNotEqual(result.returncode, 0)

    def test_m38_missing_git_warns(self):
        self.tw("thread", "add", "ui")
        self.tw("plan", "approve", "--user-said", "approve")
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "set", "T1", "--status", "working")
        env = dict(self.env, PATH="/nonexistent")
        r = subprocess.run([sys.executable, str(TW), "ready"], cwd=self.dir, env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(any("git is not available" in x for x in json.loads(r.stdout)["lease_warnings"]))

    def session_fixture(self, wrapped=True):
        self.tw("thread", "add", "api", "--session", "session_old")
        self.tw("thread", "set", "api", "--status", "archived")
        sessions = self.dir / "sessions.json"
        rows = [
            {"id": "lead_s", "tags": ["teamwork:demo"], "created_at": "2026-01-01T00:00:00Z"},
            {"id": "api_s", "tags": ["teamwork:demo", "teamwork-thread:api"], "parent_session_id": "lead_s",
             "created_at": "2026-01-02T08:00:00+08:00"},
            {"id": "ui_old", "tags": ["teamwork:demo", "teamwork-thread:ui"], "parent_session_id": "lead_s",
             "created_at": "2026-01-01T00:00:00Z"},
            {"id": "ui_new", "tags": ["teamwork:demo", "teamwork-thread:ui"], "parent_session_id": "lead_s",
             "created_at": "2026-01-01T04:00:00+08:00"},
            {"id": "bad", "tags": [1], "created_at": "bad"},
        ]
        payload = {"type": "text", "text": json.dumps(rows)} if wrapped else rows
        sessions.write_text(json.dumps(payload), encoding="utf-8")
        return sessions

    def test_m42_mixed_timezone_session_order_cli(self):
        sessions = self.session_fixture(wrapped=False)
        r = self.tw("rebuild", "--sessions", str(sessions))
        preview = json.loads((self.dir / ".teamwork" / "state.json.rebuilt").read_text())
        self.assertEqual(preview["threads"]["ui"]["session_id"], "ui_old")

    def test_m7_naive_session_times_are_sorted_as_utc_cli(self):
        sessions = self.session_fixture(wrapped=False)
        rows = json.loads(sessions.read_text())
        next(x for x in rows if x["id"] == "ui_old")["created_at"] = "2026-01-01T00:00:00"
        next(x for x in rows if x["id"] == "ui_new")["created_at"] = "2026-01-01T02:00:00Z"
        sessions.write_text(json.dumps(rows), encoding="utf-8")
        self.tw("rebuild", "--sessions", str(sessions))
        preview = json.loads((self.dir / ".teamwork" / "state.json.rebuilt").read_text())
        self.assertEqual(preview["threads"]["ui"]["session_id"], "ui_new")

    def test_m53_malformed_sessions_are_skipped_with_note_cli(self):
        sessions = self.session_fixture(wrapped=False)
        rows = json.loads(sessions.read_text())
        rows.append({"id": ["bad-id"], "tags": ["teamwork:demo"], "parent_session_id": {"bad": True}})
        sessions.write_text(json.dumps(rows), encoding="utf-8")
        r = self.tw("rebuild", "--sessions", str(sessions))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(any("malformed tags" in n for n in json.loads(r.stdout)["notes"]))

    def test_m54_parent_session_restores_lead_cli(self):
        sessions = self.session_fixture(wrapped=False)
        r = self.tw("rebuild", "--sessions", str(sessions))
        preview = json.loads((self.dir / ".teamwork" / "state.json.rebuilt").read_text())
        self.assertEqual(preview["lead"]["session_id"], "lead_s")

    def test_m54_current_lead_ignores_old_lead_tag_cli(self):
        sessions = self.dir / "sessions.json"
        sessions.write_text(json.dumps([
            {"id": "session_lead_old", "tags": ["teamwork:demo", "teamwork-thread:lead"], "created_at": "2026-01-01T00:00:00Z"},
            {"id": "session_lead_now", "tags": ["teamwork:demo", "teamwork-thread:lead"], "created_at": "2026-01-02T00:00:00Z"},
            {"id": "session_ui_stale", "tags": ["teamwork:demo", "teamwork-thread:ui"], "parent_session_id": "session_lead_old",
             "created_at": "2026-01-01T01:00:00Z"},
        ]), encoding="utf-8")
        self.tw("lead", "--session", "session_lead_now")
        r = self.tw("rebuild", "--sessions", str(sessions))
        preview = json.loads((self.dir / ".teamwork" / "state.json.rebuilt").read_text())
        self.assertEqual(preview["lead"]["session_id"], "session_lead_now")
        self.assertNotEqual((preview.get("threads", {}).get("ui") or {}).get("session_id"), "session_ui_stale")

    def test_m61_archived_seat_not_assigned_in_rebuild_cli(self):
        sessions = self.session_fixture(wrapped=False)
        r = self.tw("rebuild", "--sessions", str(sessions))
        preview = json.loads((self.dir / ".teamwork" / "state.json.rebuilt").read_text())
        self.assertEqual(preview["threads"]["api"]["status"], "archived")
        self.assertTrue(any("archived seat skipped" in n for n in json.loads(r.stdout)["notes"]))

    def test_m59_mcp_text_wrapper_unwrapped_cli(self):
        sessions = self.session_fixture()
        r = self.tw("rebuild", "--sessions", str(sessions))
        preview = json.loads((self.dir / ".teamwork" / "state.json.rebuilt").read_text())
        self.assertIn("ui", preview["threads"])
        self.assertEqual(json.loads(r.stdout)["preview"], str(self.dir / ".teamwork" / "state.json.rebuilt"))

    def test_m48_handover_prompt_clears_old_session_id(self):
        self.tw("thread", "add", "api", "--session", "session_old")
        prompt = self.tw("handover", "api", "--prompt").stdout
        self.assertNotIn("session_id=session_old", prompt)
        self.assertIn("session_id=(new session, fill at reseat)", prompt)
        self.assertNotIn("fill at reseat)|", prompt)

    def test_m43_plan_and_prefs_original_words_rebuild(self):
        self.tw("plan", "approve", "--user-said", "approve this plan")
        self.tw("prefs", "--set", "language=zh", "--user-said", "use Chinese")
        st = self.state()
        st["plan_user_said"] = []
        st["prefs_user_said"] = []
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.tw("rebuild", "--apply")
        rebuilt = self.state()
        self.assertEqual(rebuilt["plan_user_said"][0]["text"], "approve this plan")
        self.assertEqual(rebuilt["prefs_user_said"][0]["text"], "use Chinese")

    def test_m46_rebuild_event_snapshots_seats(self):
        self.tw("thread", "add", "api", "--session", "session_api")
        self.tw("rebuild", "--apply")
        log = [json.loads(x) for x in (self.dir / ".teamwork" / "log.jsonl").read_text().splitlines()]
        event = next(x for x in reversed(log) if x.get("ev") == "rebuild")
        self.assertIn("api", event["snap_threads"])
        self.assertIn("snap_roster_version", event)
        state = self.state(); state["threads"] = {}
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        self.tw("rebuild", "--apply")
        self.assertIn("api", self.state()["threads"])

    def test_m51_m52_m53_m59_bad_snapshot_and_names_survive(self):
        log = self.dir / ".teamwork" / "log.jsonl"
        rows = [json.loads(x) for x in log.read_text().splitlines()]
        rows.append({"ev": "thread-set", "thread": "invalid name", "snap_threads": [], "snap_meta": "bad",
                     "snap_screens": [], "snap_handovers": "bad", "snap_task": [], "snap_req": [],
                     "snap_reqs": [], "snap_roster_version": "bad"})
        rows.append({"ev": "task-set", "task": "bad", "snap_tasks": [], "snap_models": []})
        log.write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
        r = self.tw("rebuild")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("invalid name", json.loads((self.dir / ".teamwork" / "state.json.rebuilt").read_text())["threads"])
        self.assertIn("outside NAME_RE", r.stdout)
        self.assertIn("ignored malformed snapshot snap_meta", r.stdout)

    def test_m1_absent_models_snapshot_field_is_not_reported_cli(self):
        log = self.dir / ".teamwork" / "log.jsonl"
        rows = [json.loads(x) for x in log.read_text().splitlines()]
        for row in rows:
            row.pop("snap_models", None)
        log.write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
        result = self.tw("rebuild")
        self.assertNotIn("discarded malformed snapshot field models", result.stdout)

    def test_m51_wrong_snapshot_types_are_discarded_cli(self):
        log = self.dir / ".teamwork" / "log.jsonl"
        rows = [json.loads(x) for x in log.read_text().splitlines()]
        rows.append({"ev": "rebuild", "snap_meta": [], "snap_threads": [], "snap_tasks": [],
                     "snap_screens": "bad", "snap_questions": {"bad": True}, "snap_roster_version": "x"})
        log.write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
        r = self.tw("rebuild")
        self.assertEqual(r.returncode, 0, r.stderr)
        for field in ("snap_meta", "snap_threads", "snap_tasks", "snap_screens", "snap_questions"):
            self.assertIn(f"ignored malformed snapshot {field}", r.stdout)

    def test_m52_invalid_log_thread_name_skipped_cli(self):
        log = self.dir / ".teamwork" / "log.jsonl"
        rows = [json.loads(x) for x in log.read_text().splitlines()]
        rows.append({"ev": "thread-set", "thread": "invalid name", "status": "working"})
        log.write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
        r = self.tw("rebuild")
        rebuilt = json.loads((self.dir / ".teamwork" / "state.json.rebuilt").read_text())
        self.assertNotIn("invalid name", rebuilt["threads"])
        self.assertIn("outside NAME_RE", r.stdout)

    def test_m55_truncated_log_requires_force(self):
        self.tw("thread", "add", "api")
        st = self.state(); st["rev"] += 1
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        r = self.tw("rebuild", "--apply", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("log may be truncated", r.stderr)

    def test_t76_empty_log_with_state_rev_requires_force(self):
        (self.dir / ".teamwork" / "log.jsonl").write_text("", encoding="utf-8")
        r = self.tw("rebuild", "--apply", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("log may be truncated", r.stderr)
        self.tw("rebuild", "--apply", "--force")

    def test_m58_no_log_does_not_reseat_retired_session_cli(self):
        self.tw("thread", "add", "api", "--session", "session_old")
        (self.dir / ".teamwork" / "log.jsonl").write_text("", encoding="utf-8")
        sessions = self.dir / "sessions.json"
        sessions.write_text(json.dumps([
            {"id": "session_old", "tags": ["teamwork:demo", "teamwork-thread:api"],
             "created_at": "2026-01-01T00:00:00Z", "archived": True},
            {"id": "session_new", "tags": ["teamwork:demo", "teamwork-thread:api"],
             "created_at": "2026-01-02T00:00:00Z"},
        ]), encoding="utf-8")
        self.tw("rebuild", "--sessions", str(sessions), "--force")
        preview = json.loads((self.dir / ".teamwork" / "state.json.rebuilt").read_text())
        self.tw("rebuild", "--sessions", str(sessions), "--apply", "--force")
        thread = self.state()["threads"]["api"]
        self.assertEqual(thread["session_id"], "session_new")
        self.assertEqual(thread["sessions"], preview["threads"]["api"]["sessions"])
        self.assertEqual(thread["sessions"][-1]["session_id"], "session_new")
        self.assertTrue(thread["sessions"][0]["to"])

    def git_lease_project(self, subject="[t1, T3] squash"):
        self.git = lambda *args: subprocess.run(["git", "-C", str(self.dir), *args], check=True,
                                                capture_output=True, text=True)
        self.git("init", "-q", "-b", "main")
        (self.dir / ".gitignore").write_text(".teamwork/\n")
        (self.dir / "README").write_text("init\n"); self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "init")
        self.tw("thread", "add", "ui"); self.tw("thread", "add", "api"); self.tw("plan", "approve", "--user-said", "ok")
        self.tw("task", "add", "T1", "--owner", "ui"); self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared.py")
        self.tw("task", "set", "T1", "--status", "working")
        self.git("checkout", "-q", "-b", "tw/demo/ui"); (self.dir / "shared.py").write_text("x\n"); self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "T1: change")
        self.git("checkout", "-q", "main"); self.git("merge", "-q", "--squash", "tw/demo/ui"); self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", subject)
        squash = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("checkout", "-q", "tw/demo/ui"); (self.dir / "later.py").write_text("later\n"); self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "T3: later work")
        self.git("checkout", "-q", "main")
        (self.dir / "shared.py").write_text("x\ny\n"); self.git("add", "shared.py")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "base followup")
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")

        return squash

    def test_m28_squash_list_and_casefold_cli(self):
        self.git_lease_project()
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["ready"])

    def test_m29_reverted_squash_still_holds_lease_cli(self):
        squash = self.git_lease_project("T1: squash")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["ready"])
        (self.dir / "shared.py").write_text("y\n")
        self.git("add", "shared.py")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm",
                 "Revert squash", "-m", f"This reverts commit {squash[:8]}, reversing changes made earlier.")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])

    def test_m29_ready_scans_300_base_commits_once_within_five_seconds(self):
        self.git_lease_project()
        for i in range(300):
            (self.dir / "README").write_text(f"base {i}\n")
            self.git("add", "README")
            self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", f"base {i}")
        start = time.monotonic()
        self.tw("ready")
        self.assertLess(time.monotonic() - start, 5.0)

class T76BatchC(TwCase):
    """T76 batch C: privacy guard, installer path rules, and thread kind/NEED-THREAD edge cases."""

    def setUp(self):
        super().setUp()
        self.env = {**self.env, "HOME": str(self.dir / "test-home")}

    def tw(self, *args, **kwargs):
        return super().tw(*args, **kwargs)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.dir), *args], check=True, capture_output=True, text=True)

    def guard_setup(self, private="Zorblax"):
        self.git("init", "-q", "-b", "main")
        (self.dir / "base.txt").write_text("base\n")
        self.git("add", "base.txt")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "base")
        (self.dir / ".teamwork" / "PRIVATE.md").write_text(private + "\n")

    def guard(self, *args):
        return self.tw("guard", "--repo", str(self.dir), "--base", "main", *args, ok=False)

    def test_m62_path_glob_hit_hides_entire_name_cli(self):
        self.guard_setup("path: secret/[ab].txt")
        (self.dir / "secret").mkdir()
        (self.dir / "secret" / "a.txt").write_text("clean\n")
        r = self.guard()
        self.assertEqual(r.returncode, 1)
        self.assertIn("(name hidden)", r.stdout)
        self.assertNotIn("a.txt", r.stdout)

    def test_m63_grafts_fail_closed_cli(self):
        self.guard_setup()
        (self.dir / ".git" / "info" / "grafts").write_text("deadbeef deadbeef\n")
        r = self.guard()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("grafts", r.stderr)

    def test_m63_grafts_fail_closed_in_linked_worktree_cli(self):
        self.guard_setup()
        linked = self.dir / "linked"
        self.git("worktree", "add", "-q", "-b", "linked", str(linked), "main")
        (self.dir / ".git" / "info" / "grafts").write_text("deadbeef deadbeef\n")
        r = subprocess.run([sys.executable, str(TW), "guard", "--repo", str(linked), "--base", "main"],
                           cwd=linked, capture_output=True, text=True,
                           env={**self.env, "TEAMWORK_DIR": str(self.dir / ".teamwork")})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("grafts", r.stderr)

    def test_m64_submodule_limit_reminder_cli(self):
        self.guard_setup("")
        (self.dir / ".gitmodules").write_text("[submodule \"x\"]\n path = x\n")
        r = self.guard()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("submodule contents are not scanned", r.stderr)

    def test_m65_git_log_settings_do_not_hide_history_cli(self):
        self.guard_setup()
        base = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("config", "log.showRoot", "false")
        self.git("config", "log.diffMerges", "first-parent")
        (self.dir / "private.txt").write_text("Zorblax\n")
        self.git("add", "private.txt")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "leak")
        (self.dir / "private.txt").unlink()
        self.git("add", "-u")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "clean")
        self.git("checkout", "-q", "-b", "side")
        (self.dir / "side.txt").write_text("Zorblax in merge side\n")
        self.git("add", "side.txt")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "side private")
        self.git("checkout", "-q", "main")
        (self.dir / "main.txt").write_text("Zorblax in merge first parent\n")
        self.git("add", "main.txt")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "main private")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "merge", "--no-ff", "side", "-m", "merge private")
        r = self.tw("guard", "--repo", str(self.dir), "--base", base, ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("history", r.stdout)
        self.assertGreaterEqual(r.stdout.count("history "), 2, r.stdout)
        kit = self.tw("guard", "--hash-list", "--kit").stdout
        self.assertIn('"log.showRoot=true"', kit)
        self.assertIn('"--diff-merges=separate"', kit)

    def test_m66_phrase_separator_forms_match_contents_names_and_refs_cli(self):
        self.guard_setup("foo bar")
        (self.dir / "word.txt").write_text("foo_bar\n")
        self.assertEqual(self.guard().returncode, 1)
        (self.dir / "word.txt").unlink()
        (self.dir / "foo_bar.txt").write_text("clean\n")
        self.assertEqual(self.guard().returncode, 1)
        (self.dir / "foo_bar.txt").unlink()
        (self.dir / ".teamwork" / "PRIVATE.md").write_text("acme widgets\n")
        (self.dir / "compact.txt").write_text("acmewidgets\n")
        self.assertEqual(self.guard().returncode, 1)
        (self.dir / "compact.txt").unlink()
        (self.dir / ".teamwork" / "PRIVATE.md").write_text("foo bar\n")
        (self.dir / "double-space.txt").write_text("foo  bar\n")
        self.assertEqual(self.guard().returncode, 1)
        (self.dir / "double-space.txt").unlink()
        self.git("checkout", "-q", "-b", "feature/foo_bar")
        r = self.guard()
        self.assertEqual(r.returncode, 1)
        self.assertIn("ref name", r.stdout)

    def test_m68_missing_extra_private_list_fails_cli(self):
        self.guard_setup()
        r = self.guard("--private", str(self.dir / "absent-private.md"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no private list", r.stderr)
        r = self.tw("guard", "--hash-list", "--private", str(self.dir / "absent-private.md"), ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no private list", r.stderr)

    def test_m69_m70_archive_limits_and_corruption_warn_cli(self):
        import io
        import gzip
        self.guard_setup()
        nested = b"clean archive content"
        for i in range(3):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr(f"level{i}.zip", nested)
            nested = buf.getvalue()
        (self.dir / "nested.zip").write_bytes(nested)
        r = self.guard()
        self.assertIn("not fully checked", r.stderr)
        (self.dir / "nested.zip").unlink()
        (self.dir / "broken.zip").write_bytes(b"PK\x03\x04truncated")
        r = self.guard()
        self.assertIn("corrupt zip", r.stderr)
        (self.dir / "broken.zip").unlink()
        nested_gzip = b"clean data"
        for _ in range(3):
            nested_gzip = gzip.compress(nested_gzip)
        (self.dir / "nested.gz").write_bytes(nested_gzip)
        (self.dir / "broken.gz").write_bytes(b"\x1f\x8bbroken")
        (self.dir / "broken.xz").write_bytes(b"\xfd7zXZ\x00broken")
        r = self.guard()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for name in ("nested.gz", "broken.gz", "broken.xz"):
            self.assertIn(name, r.stderr)
        self.assertIn("not fully checked", r.stderr)

    def test_m74_xml_whitespace_split_word_is_joined_cli(self):
        self.guard_setup("Acme Widgets")
        buf = __import__("io").BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", "<p><b>Acme</b> <i>Widgets</i></p>")
        (self.dir / "split.docx").write_bytes(buf.getvalue())
        self.assertEqual(self.guard().returncode, 1)

    def test_m75_large_private_list_scan_is_bounded_cli(self):
        self.guard_setup("\n".join(f"privatewordtoken{i:04d}" for i in range(1000)))
        (self.dir / "megabyte.txt").write_text("ordinarytext " * 80000)
        start = time.monotonic()
        r = self.guard()
        elapsed = time.monotonic() - start
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertLess(elapsed, 5.0, f"1 MB scan took {elapsed:.2f}s")
        (self.dir / "megabyte.txt").unlink()
        (self.dir / "common-prefix.txt").write_text("privatewordtoken" * 100000)
        start = time.monotonic()
        r = self.guard()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertLess(time.monotonic() - start, 5.0, "common-prefix scan regressed")

    def test_m76_pdf_decompression_limit_warns_cli(self):
        import tw, zlib
        streams = [b"clean" * 10, b"clean" * 10, b"clean" * 10, b"(Zorblax) Tj"]
        pdf = b"%PDF-1.4\n" + b"".join(b"stream\n" + zlib.compress(x) + b"\nendstream\n" for x in streams)
        with mock.patch.object(tw, "GUARD_UNIT_CAP", 32), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertTrue(tw._bytes_has(pdf, tw._needles(["Zorblax"]), label="many.pdf"))
        self.assertIn("many.pdf", err.getvalue())
        first_large = b"%PDF-1.4\nstream\n" + zlib.compress(b"x" * 40) + b"\nendstream\n"
        second_secret = b"stream\n" + zlib.compress(b"(Zorblax) Tj") + b"\nendstream\n"
        with mock.patch.object(tw, "GUARD_UNIT_CAP", 32):
            self.assertTrue(tw._bytes_has(first_large + second_secret, tw._needles(["Zorblax"])))

    def test_m76_gzip_cap_scans_partial_and_names_file(self):
        import bz2, gzip, lzma, tw
        with mock.patch.object(tw, "GUARD_UNIT_CAP", 32), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertTrue(tw._bytes_has(gzip.compress(b"Zorblax" + b"x" * 80),
                                          tw._needles(["Zorblax"]), label="large.gz"))
        self.assertIn("large.gz", err.getvalue())
        for label, data in (("members.gz", gzip.compress(b"x" * 80) + gzip.compress(b"Zorblax")),
                            ("members.bz2", bz2.compress(b"x" * 80) + bz2.compress(b"Zorblax")),
                            ("members.xz", lzma.compress(b"x" * 80) + lzma.compress(b"Zorblax"))):
            with mock.patch.object(tw, "GUARD_UNIT_CAP", 32):
                self.assertTrue(tw._bytes_has(data, tw._needles(["Zorblax"]), label=label), label)

    def test_m76_zip_item_cap_scans_partial_and_next_item(self):
        import tw
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("large.txt", b"x" * 80)
            archive.writestr("secret.txt", b"Zorblax")
        with mock.patch.object(tw, "GUARD_UNIT_CAP", 32), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertTrue(tw._bytes_has(buf.getvalue(), tw._needles(["Zorblax"]), label="archive.zip"))
        self.assertIn("archive.zip/large.txt", err.getvalue())

    def test_m76_short_phrase_boundaries_and_separator_forms(self):
        import tw
        self.assertFalse(tw._bytes_has(b"TODO visit", tw._needles(["to do", "is it"])))
        needles = tw._needles(["foo bar", "acme widgets"])
        for text in (b"foo_bar", b"foo-bar", b"acmewidgets"):
            self.assertTrue(tw._bytes_has(text, needles), text)

    def test_m76_phrase_scan_small_timing_regression(self):
        import tw
        needles = tw._needles([f"private phrase {i:02d}" for i in range(20)])
        content = b"ordinarytext " * 5000
        start = time.monotonic()
        self.assertFalse(tw._bytes_has(content, needles))
        self.assertLess(time.monotonic() - start, 0.4)

    def test_m77_hex_escape_and_double_percent_decode_cli(self):
        self.guard_setup()
        (self.dir / "hex.txt").write_text(r"Zorblax \x5A\x6F\x72\x62\x6C\x61\x78")
        self.assertEqual(self.guard().returncode, 1)
        (self.dir / "hex.txt").unlink()
        (self.dir / "percent.txt").write_text("%255A%256F%2572%2562%256C%2561%2578")
        self.assertEqual(self.guard().returncode, 1)

    def test_m82_tmpdir_inside_home_is_noted_with_config_isolated_cli(self):
        home = self.dir / "fake-home"
        scratch = home / "Documents" / "scratch"
        scratch.mkdir(parents=True)
        cfg = self.dir / "install-config"
        r = subprocess.run([sys.executable, str(SCRIPTS / "install.py"), "--config-dir", str(cfg)],
                           capture_output=True, text=True, env={**os.environ, "HOME": str(home), "TMPDIR": str(scratch)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("inside the home folder", r.stdout)
        self.assertIn("would be added to additionalDirectories", r.stdout)
        self.assertIn("additionalDirectories: adding /tmp", r.stdout)
        home_root = subprocess.run([sys.executable, str(SCRIPTS / "install.py"), "--config-dir", str(cfg / "home-root")],
                                   capture_output=True, text=True, env={**os.environ, "HOME": str(home), "TMPDIR": str(home)})
        self.assertEqual(home_root.returncode, 0, home_root.stderr)
        self.assertIn("holds the home folder: skipped", home_root.stdout)
        self.assertNotIn("was added to additionalDirectories", home_root.stdout)

    def test_m84_installer_uses_init_path_repo_root_cli(self):
        parent = self.dir / "project-parent"
        nested = parent / "nested"
        nested.mkdir(parents=True)
        subprocess.run([sys.executable, str(TW), "-C", str(nested), "init", "demo", "--path", str(nested)],
                       check=True, capture_output=True, text=True)
        other = parent / "other"
        other.mkdir()
        subprocess.run([sys.executable, str(TW), "-C", str(other), "init", "other", "--path", str(other)],
                       check=True, capture_output=True, text=True)
        cfg = self.dir / "config-m84"
        r = subprocess.run([sys.executable, str(SCRIPTS / "install.py"), "--config-dir", str(cfg),
                            "--project", str(parent)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        expected = str(nested.parent / (nested.name + "-worktrees"))
        self.assertIn(expected, r.stdout)
        self.assertIn("multiple teamwork projects found", r.stdout)
        self.assertIn(str(other), r.stdout)
        for tool in ("Edit", "Write"):
            self.assertIn(f"{tool}(/{expected}/**)", r.stdout)
        self.assertIn(expected, r.stdout)
        self.assertNotIn(str(parent.parent / (parent.name + "-worktrees")), r.stdout)

    def test_m85_need_thread_normalizes_validates_from_and_name_cli(self):
        self.tw("thread", "add", "ui")
        self.assertNotEqual(self.tw("ask", "need", "--from", "ghost", "--need-thread", "api", ok=False).returncode, 0)
        self.assertNotEqual(self.tw("ask", "need", "--from", "ui", "--need-thread", "Bad_Name", ok=False).returncode, 0)
        self.tw("ask", "need", "--from", " UI ", "--need-thread", "api")
        r = self.tw("ask", "repeat", "--from", "ui", "--need-thread", "API", ok=False)
        self.assertIn("already question", r.stderr)

    def test_m86_again_allows_new_request_only_after_answer_cli(self):
        self.tw("thread", "add", "ui")
        self.tw("ask", "first", "--from", "ui", "--need-thread", "api")
        self.assertNotEqual(self.tw("ask", "premature", "--from", "ui", "--need-thread", "api", "--again", ok=False).returncode, 0)
        self.tw("ask", "--answer", "1", "done")
        self.tw("ask", "again", "--from", "ui", "--need-thread", "api", "--again")
        self.assertNotEqual(self.tw("ask", "again", "--from", "ui", "--again", ok=False).returncode, 0)

    def test_m87_kind_change_checks_worktree_and_warns_cli(self):
        self.tw("thread", "add", "ui")
        worktree = Path(self.state()["threads"]["ui"]["worktree"])
        worktree.mkdir(parents=True)
        r = self.tw("thread", "set", "ui", "--kind", "cloud", "--cloud-reason", "remote", ok=False)
        self.assertIn("worktree exists", r.stderr)
        worktree.rmdir()
        self.tw("thread", "set", "ui", "--kind", "cloud", "--cloud-reason", "remote")
        r = self.tw("thread", "set", "ui", "--kind", "rc")
        self.assertIn("run `tw.py worktree ui`", r.stderr)

    def test_m88_thread_set_rejects_session_and_kind_without_write_cli(self):
        self.tw("thread", "add", "ui")
        before = self.state()
        r = self.tw("thread", "set", "ui", "--session", "session_ui", "--kind", "cloud", "--cloud-reason", "x", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.state(), before)

    def test_m89_subagent_id_error_precedes_existing_lead_cli(self):
        self.tw("lead", "--session", "session_lead")
        r = self.tw("lead", "--session", "subagent.1", ok=False)
        self.assertIn("looks like a subagent", r.stderr)

    def test_m90_subagent_shapes_rejected_arbitrary_sessions_allowed_cli(self):
        for sid in ("subagent.1", "agent 7", "general_purpose", "toolu01ABC", "subagent 2", "agent.7",
                    "Agent.7", "subagent.x", "subagent_x", "subagent-x"):
            r = self.tw("thread", "add", "ui", "--session", sid, ok=False)
            self.assertNotEqual(r.returncode, 0, sid)
        for i, sid in enumerate(("lead-session", "lead-1", "A", "L1", "s-old", "123e4567-e89b-12d3-a456-426614174000")):
            name = f"known{i}"
            self.tw("thread", "add", name, "--session", sid)

    def test_m91_unknown_thread_precedes_kind_fixed_error_cli(self):
        r = self.tw("thread", "set", "ghost", "--kind", "cloud", "--cloud-reason", "reason", ok=False)
        self.assertIn("unknown thread ghost", r.stderr)
        r = self.tw("thread", "reseat", "ghost", "--session", "session_x1", ok=False)
        self.assertIn("unknown thread ghost; known:", r.stderr)

    def test_m92_empty_cloud_reason_matches_missing_reason_cli(self):
        self.tw("thread", "add", "ui")
        self.tw("thread", "set", "ui", "--kind", "cloud", "--cloud-reason", "reason")
        for extra in (("--kind", "cloud"), ("--cloud-reason", "")):
            r = self.tw("thread", "set", "ui", *extra, ok=False)
            self.assertNotEqual(r.returncode, 0, extra)
        self.assertEqual(self.state()["threads"]["ui"]["cloud_reason"], "reason")

class T76BatchD(TwCase):
    """T76 batch D regressions. Keep tests isolated from the host's config and answer files."""

    def run_cli(self, *args, env=None, ok=True, input_data=None):
        r = subprocess.run([sys.executable, str(TW), *args], cwd=self.dir,
                           env={**self.env, **(env or {})}, input=input_data, capture_output=True, text=True)
        if ok: self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def test_m104_log_precedes_state_and_rebuild_replays_crash_cli(self):
        crash = self.run_cli("prefs", "--set", "short_name=T76D", env={"TW_TEST_SAVE_CRASH": "before-state-replace"}, ok=False)
        self.assertEqual(crash.returncode, 86)
        old_rev = self.state()["rev"]
        events = [json.loads(x) for x in (self.dir / ".teamwork" / "log.jsonl").read_text().splitlines()]
        self.run_cli("rebuild", "--apply")
        self.assertEqual(self.state()["prefs"]["short_name"], "T76D")
        self.assertEqual(events[-1].get("rev"), old_rev + 1)
        self.run_cli("rebuild", "--apply")
        self.assertEqual(self.state()["prefs"]["short_name"], "T76D")

    def test_m100_new_thread_is_dispatched_before_first_ack_cli(self):
        self.tw("thread", "add", "ui")
        self.tw("plan", "approve", "--user-said", "approved")
        self.tw("task", "add", "T1", "--title", "first work", "--owner", "ui", "--accept", "the work is reviewed")
        self.tw("thread", "set", "ui", "--session", "session_ui")
        self.tw("task", "set", "T1", "--status", "dispatched")
        self.assertEqual(self.state()["tasks"]["T1"]["status"], "dispatched")
        self.tw("thread", "set", "ui", "--status", "working")
        self.assertEqual(self.state()["threads"]["ui"]["status"], "working")
        skill = (REPO / "SKILL.md").read_text()
        snippet = skill[skill.index("- `TW thread set <thread> --session <new id>`"):]
        self.assertLess(snippet.index("TW task set <id> --status dispatched"), snippet.index("after the thread's first ACK"))

    def test_m104_crash_retry_deduplicates_user_said_and_warns_on_lag_cli(self):
        crashed = self.run_cli("plan", "approve", "--user-said", "plan approved",
                               env={"TW_TEST_SAVE_CRASH": "before-state-replace"}, ok=False)
        self.assertEqual(crashed.returncode, 86)
        self.run_cli("plan", "approve", "--user-said", "plan approved")
        self.run_cli("rebuild", "--apply")
        self.assertEqual(len(self.state()["plan_user_said"]), 1)

        crashed = self.run_cli("prefs", "--set", "gpt_model=gpt-fixture", "--user-said", "model approved",
                               env={"TW_TEST_SAVE_CRASH": "before-state-replace"}, ok=False)
        self.assertEqual(crashed.returncode, 86)
        self.run_cli("prefs", "--set", "gpt_model=gpt-fixture", "--user-said", "model approved")
        self.run_cli("rebuild", "--apply")
        said = self.state()["prefs_user_said"]
        self.assertEqual(sum(x.get("text") == "model approved" for x in said), 1)

        crashed = self.run_cli("prefs", "--set", "short_name=T76D",
                               env={"TW_TEST_SAVE_CRASH": "before-state-replace"}, ok=False)
        self.assertEqual(crashed.returncode, 86)
        tmp_files = list((self.dir / ".teamwork").glob("state.json.tmp-*"))
        self.assertTrue(tmp_files)
        saved = self.run_cli("prefs", "--set", "language=en")
        self.assertIn("log.jsonl is ahead", saved.stderr)
        self.assertFalse(list((self.dir / ".teamwork").glob("state.json.tmp-*")))

    def test_t76_crash_retry_with_changed_summary_replays_retry_state_cli(self):
        self.tw("thread", "add", "ui")
        crashed = self.run_cli("thread", "set", "ui", "--summary", "first",
                               env={"TW_TEST_SAVE_CRASH": "before-state-replace"}, ok=False)
        self.assertEqual(crashed.returncode, 86)
        self.run_cli("thread", "set", "ui", "--summary", "second")
        retried = self.state()
        self.assertEqual(retried["threads"]["ui"]["summary"], "second")
        self.run_cli("rebuild", "--apply")
        rebuilt = self.state()
        self.assertEqual(rebuilt["threads"], retried["threads"])

    def test_m104_next_save_repairs_board_after_state_replace_cli(self):
        board_path = self.dir / ".teamwork" / "BOARD.md"
        board = board_path.read_text()
        crashed = self.run_cli("prefs", "--set", "short_name=T76D", env={"TW_TEST_SAVE_CRASH": "after-state-replace"}, ok=False)
        self.assertEqual(crashed.returncode, 87)
        self.assertNotIn("T76D", board_path.read_text())
        self.run_cli("prefs", "--set", "language=en")
        self.assertIn("T76D", board_path.read_text())
        self.assertNotEqual(board, board_path.read_text())

    def test_m107_gated_reported_result_shows_redelivery_after_rework_cli(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("thread", "add", "ui")
        self.tw("plan", "approve", "--user-said", "approved")
        self.tw("task", "add", "T1", "--title", "fix result", "--owner", "ui", "--accept", "fix is reviewed")
        self.tw("task", "set", "T1", "--status", "review")
        self.tw("req", "add", "fix the result", "--id", "R1", "--tasks", "T1")
        for status in ("reported", "waiting_user", "open", "reported"):
            self.tw("req", "set", "R1", "--status", status)
        self.tw("task", "set", "T1", "--status", "working")
        state = self.state()
        self.assertEqual(state["requests"]["R1"]["status"], "reported")
        self.assertEqual(state["requests"]["R1"]["gate"], "reported")
        board = (self.dir / ".teamwork" / "BOARD.md").read_text()
        self.assertIn("结果已变，待重新交付", board)
        ready = json.loads(self.tw("ready").stdout)
        self.assertEqual(ready["result_changed_requests"], {"R1": "结果已变，待重新交付"})
        self.tw("task", "set", "T1", "--status", "review")
        self.tw("task", "set", "T1", "--status", "done", "--review", "pass", "--reviewer", "lead")
        self.assertNotIn("结果已变，待重新交付", (self.dir / ".teamwork" / "BOARD.md").read_text())

    def test_m105_quota_folders_union_flags_and_notes_cli(self):
        one, two, codex = self.dir / "cfg-one", self.dir / "cfg-two", self.dir / "codex"
        one.mkdir(); two.mkdir(); (codex / "sessions").mkdir(parents=True)
        cache = {"fetchedAtMs": time.time() * 1000, "utilization": {
            "seven_day": {"utilization": 70.0, "resets_at": time.time() + 86400},
            "limits": [{"kind": "weekly_scoped", "scope": {"model": {"display_name": "Fable"}},
                        "percent": 96, "resets_at": time.time() + 86400}]}}
        (one / ".claude.json").write_text(json.dumps({"cachedUsageUtilization": cache}))
        (two / "bridge-transcript-cse_other.jsonl").write_text(json.dumps({"timestamp": time.time(),
            "rate_limit_info": {"unifiedWindows": {"seven_day": {"utilization": 0.3,
                "resetsAt": time.time() + 86400 * 7}}}}) + "\n")
        codex_file = codex / "sessions" / "session.jsonl"
        codex_file.write_text(json.dumps({"rate_limits": {"secondary": {"used_percent": 90,
            "window_minutes": 10080, "resets_at": time.time() + 86400}}}) + "\n")
        out = json.loads(self.tw("quota", "--config-dir", str(one), "--config-dir", str(two), "--codex-dir",
                                 str(codex), "--json").stdout)
        self.assertIn("gpt-tight", out["flags"])
        self.assertIn("fable-done", out["flags"])
        self.assertTrue(any(x.startswith("config #1:") for x in out["notes"]), out)
        self.assertTrue(any(x.startswith("config #2:") for x in out["notes"]), out)

    def test_m106_bad_single_threshold_adds_note_cli(self):
        state_path = self.dir / ".teamwork" / "state.json"
        state = json.loads(state_path.read_text()); state["prefs"]["quota_gpt_tight"] = "broken"
        state_path.write_text(json.dumps(state))
        codex = self.dir / "codex"; (codex / "sessions").mkdir(parents=True)
        out = json.loads(self.tw("quota", "--config-dir", str(self.dir / "empty-config"), "--codex-dir",
                                 str(codex), "--json").stdout)
        self.assertTrue(any("quota_gpt_tight" in x and "default" in x for x in out["notes"]))

    def test_m108_user_message_reports_tilde_tmp_private_and_var_paths_cli(self):
        msg = self.dir / "message.txt"
        msg.write_text("Please read ~/x/y, /tmp/a, /private/tmp/b, and /var/folders/c/d. Tested.")
        out = json.loads(self.tw("check-msg", "--user", str(msg), "--json").stdout)
        self.assertEqual([x["kind"] for x in out["findings"]], ["local-path"])

    def test_m108_user_message_path_boundaries_cli(self):
        msg = self.dir / "message.txt"
        for value in ("/tmp/a", "read ~/x/y", 'path="/private/tmp/b"', "file=/var/folders/c/d", "(/Users/example/a)"):
            msg.write_text(value)
            out = json.loads(self.tw("check-msg", "--user", str(msg), "--json").stdout)
            self.assertIn("local-path", [x["kind"] for x in out["findings"]], value)
        for value in ("https://example.com/private/report", "docs/private/notes.md", "src/tmp/cache.py", "~/= 5"):
            msg.write_text(value)
            out = json.loads(self.tw("check-msg", "--user", str(msg), "--json").stdout)
            self.assertNotIn("local-path", [x["kind"] for x in out["findings"]], value)

    def test_m109_sprint_escapes_pipe_in_task_title_cli(self):
        self.tw("plan", "approve", "--user-said", "approved")
        self.tw("thread", "add", "ui")
        self.tw("task", "add", "T1", "--title", "pipe | title", "--owner", "ui", "--accept", "visible in sprint")
        self.tw("task", "set", "T1", "--status", "dispatched")
        self.tw("task", "set", "T1", "--status", "working")
        row = next(x for x in self.tw("sprint").stdout.splitlines() if "pipe" in x)
        self.assertEqual(len(re.findall(r"(?<!\\)\|", row)), 4)
        self.assertIn(r"pipe \| title", row)

    def test_m116_gpt_model_pref_selfcheck_and_task_msg_cli(self):
        check = json.loads(self.tw("selfcheck", "--json").stdout)
        self.assertEqual(next(x for x in check["items"] if x["item"] == "gpt_model")["detail"], "not recorded")
        self.tw("task", "add", "T1", "--title", "model task", "--accept", "message has configured model", "--route", "gpt")
        self.assertNotIn("GPT model:", self.tw("task-msg", "T1").stdout)
        self.tw("prefs", "--set", "gpt_model=gpt-fixture")
        check = json.loads(self.tw("selfcheck", "--json").stdout)
        self.assertEqual(next(x for x in check["items"] if x["item"] == "gpt_model")["detail"], "gpt-fixture")
        self.assertIn("GPT model: gpt-fixture", self.tw("task-msg", "T1").stdout)
        self.tw("task", "add", "T2", "--title", "claude task", "--accept", "claude message")
        self.assertNotIn("GPT model:", self.tw("task-msg", "T2").stdout)
        self.tw("thread", "add", "ui")
        self.assertNotIn("GPT model:", self.tw("worker-prompt", "ui").stdout)
        self.tw("prefs", "--set", "language=zh")
        self.assertIn("GPT 模型：gpt-fixture", self.tw("task-msg", "T1").stdout)

    def test_m117_selfcheck_screen_fix_asks_for_screen_cli(self):
        keys = self.dir.parent / (self.dir.name + "-keys")
        self.env["TW_KEYS_DIR"] = str(keys)
        self.tw("lead", "--session", "session_lead")
        self.tw("screen", "ask", "lead", "--session", "session_lead")
        check = json.loads(self.tw("selfcheck", "--json").stdout)
        screen = next(x for x in check["items"] if x["item"] == "screening")
        self.assertIn("screen ask lead --session session_lead", screen["fix"])
        self.assertNotIn("screen list", screen["fix"])

    def test_m129_opening_uses_isolated_temp_home(self):
        home = self.dir / "temporary-home"; home.mkdir()
        shared = home / "served"; shared.mkdir()
        unrelated = shared / "neighbor"; unrelated.mkdir()
        (unrelated / ".teamwork").mkdir(parents=True)
        (unrelated / ".teamwork" / "state.json").write_text(json.dumps({"prefs": {"policy_said": {
            "perm_policy": {"at": "later", "text": "foreign answer"}}}}))
        project = shared / "current"; project.mkdir()
        ledger = self.dir / ".teamwork"
        target = project / ".teamwork"
        shutil.move(str(ledger), str(target))
        env = {"HOME": str(home), "TEAMWORK_DIR": str(target)}
        out = self.run_cli("opening", env=env).stdout
        self.assertIn("foreign answer", out)
        isolated = home / "isolated" / "current"; isolated.mkdir(parents=True)
        isolated_target = isolated / ".teamwork"
        shutil.move(str(target), str(isolated_target))
        out = self.run_cli("opening", env={**env, "TEAMWORK_DIR": str(isolated_target)}).stdout
        self.assertNotIn("foreign answer", out)

    def test_opening_tie_with_different_answer_types_cli(self):
        served = self.dir / "served"; served.mkdir()
        current = served / "current"; current.mkdir()
        target = current / ".teamwork"
        shutil.move(str(self.dir / ".teamwork"), str(target))
        for name, value in (("neighbor-one", "ask"), ("neighbor-two", 2)):
            folder = served / name / ".teamwork"; folder.mkdir(parents=True)
            (folder / "state.json").write_text(json.dumps({"prefs": {
                "perm_policy": value,
                "policy_said": {"perm_policy": {"at": "same-time", "text": "recorded answer"}}}}))
        out = self.run_cli("opening", env={"TEAMWORK_DIR": str(target), "HOME": str(self.dir)} , ok=False)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("recorded answer", out.stdout)

    def test_m111_pack_redacts_command_from_assistant_and_tool_result_cli(self):
        self.env["TW_KEYS_DIR"] = str(self.dir.parent / (self.dir.name + "-keys"))
        self.tw("thread", "add", "api", "--session", "session_old")
        self.tw("succ", "start", "api", "--reason", "replace")
        self.tw("succ", "candidate", "HO1", "session_next")
        self.tw("succ", "freeze", "HO1", "--stopped", "session_old is idle")
        transcript = self.dir / "transcript.jsonl"
        secret = "password=fixture-secret-value"
        rows = [
            {"type": "assistant", "timestamp": "2026-11-24T12:00:00Z", "message": {"content": [
                {"type": "text", "text": f"Run {secret}"},
                {"type": "tool_use", "id": "danger", "name": "Bash", "input": {"command": f"echo {secret}"}}]}},
            {"type": "user", "timestamp": "2026-11-24T12:00:01Z", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "danger", "content": f"output {secret}"}]}}
        ]
        transcript.write_text("\n".join(json.dumps(x) for x in rows) + "\n")
        self.synthetic_bridge("session_old", transcript)
        packed = self.tw("succ", "pack", "HO1", "--transcript", str(transcript)).stdout
        brief = Path(packed.split(" · ")[0])
        package = brief.parent
        combined = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in package.rglob("*") if p.is_file())
        self.assertNotIn(secret, combined)

    def test_m114_archive_default_uses_main_config_dirs_cli(self):
        home = Path(tempfile.mkdtemp(prefix="t76d-home-", dir=self.dir.parent))
        self.addCleanup(shutil.rmtree, home)
        config = home / ".claude"; config.mkdir()
        sibling = home / ".claude-extra"; sibling.mkdir()
        project = sibling / "projects" / "fake-project"; project.mkdir(parents=True)
        bridge = sibling / "bridge-transcript-cse_old.jsonl"
        bridge.write_text(json.dumps({"session_id": "session_old"}) + "\n")
        (project / "session_old.jsonl").write_text(json.dumps({"type": "user", "message": {
            "content": "sibling config transcript marker"}}) + "\n")
        self.env.update({"HOME": str(home), "CLAUDE_CONFIG_DIR": ""})
        self.env["TW_KEYS_DIR"] = str(home / "keys")
        self.tw("thread", "add", "api", "--session", "session_old")
        self.tw("succ", "start", "api", "--reason", "replace")
        self.tw("succ", "candidate", "HO1", "session_next")
        self.tw("succ", "freeze", "HO1", "--stopped", "session_old is idle")
        packed = self.tw("succ", "pack", "HO1").stdout
        brief = Path(packed.split(" · ")[0])
        self.assertIn("sibling config transcript marker", brief.parent.joinpath("raw", "old.jsonl").read_text())

    def test_m114_pack_searches_config_dir_recorded_by_session_start_cli(self):
        home = Path(tempfile.mkdtemp(prefix="t76d-m114-home-", dir=self.dir.parent))
        self.addCleanup(shutil.rmtree, home)
        default_config = home / ".claude"; default_config.mkdir()
        remembered = home / "alternate-config"; remembered.mkdir()
        project = remembered / "projects" / "fake-project"; project.mkdir(parents=True)
        (remembered / "bridge-transcript-cse_old.jsonl").write_text(json.dumps({"session_id": "session_old"}) + "\n")
        (project / "session_old.jsonl").write_text(json.dumps({"type": "user", "message": {
            "content": "remembered config transcript marker"}}) + "\n")
        self.env.update({"HOME": str(home), "CLAUDE_CONFIG_DIR": "", "TW_KEYS_DIR": str(home / "keys")})
        self.tw("thread", "add", "api", "--session", "session_old")
        self.tw("succ", "start", "api", "--reason", "replace")
        self.tw("succ", "candidate", "HO1", "session_next")
        self.tw("succ", "freeze", "HO1", "--stopped", "session_old is idle")
        context = self.run_cli("context", env={"CLAUDE_CONFIG_DIR": str(remembered)},
                               input_data=json.dumps({"session_id": "session_old"}))
        self.assertEqual(context.returncode, 0, context.stderr)
        packed = self.tw("succ", "pack", "HO1").stdout
        brief = Path(packed.split(" · ")[0])
        raw = brief.parent.joinpath("raw", "old.jsonl").read_text()
        self.assertIn("remembered config transcript marker", raw)

    def test_m114_pack_gap_lists_searched_config_dirs_cli(self):
        home = Path(tempfile.mkdtemp(prefix="t76d-m114-home-", dir=self.dir.parent))
        self.addCleanup(shutil.rmtree, home)
        default_config = home / ".claude"; default_config.mkdir()
        self.env.update({"HOME": str(home), "CLAUDE_CONFIG_DIR": "", "TW_KEYS_DIR": str(home / "keys")})
        self.tw("thread", "add", "api", "--session", "session_old")
        self.tw("succ", "start", "api", "--reason", "replace")
        self.tw("succ", "candidate", "HO1", "session_next")
        self.tw("succ", "freeze", "HO1", "--stopped", "session_old is idle")
        packed = self.tw("succ", "pack", "HO1").stdout
        brief = Path(packed.split(" · ")[0]).read_text()
        self.assertIn(str(default_config), brief)
        self.assertIn("--config-dir", brief)


    def test_m127_common_branch_is_not_filtered_for_other_tasks_cli(self):
        # Real git branches exercise the same shared-ref filtering used by ready/lease checks.
        self.tw("thread", "add", "ui")
        self.tw("thread", "add", "api")
        self.tw("thread", "add", "db")
        self.tw("plan", "approve", "--user-said", "approved")
        self.tw("task", "add", "T1", "--title", "old", "--owner", "ui", "--touches", "shared.txt")
        self.tw("task", "add", "T2", "--title", "current", "--owner", "api")
        self.tw("task", "add", "T3", "--title", "ready", "--owner", "db", "--touches", "shared.txt")
        self.tw("task", "set", "T1", "--status", "dropped", "--user-said", "drop the old work")
        self.tw("task", "set", "T2", "--status", "dispatched")
        self.tw("task", "set", "T2", "--status", "working")
        self.git("init", "-q", "-b", "main")
        (self.dir / "shared.txt").write_text("base\n"); self.git("add", "shared.txt"); self.git_commit("base")
        self.git("checkout", "-qb", "tw/demo/ui")
        (self.dir / "shared.txt").write_text("branch\n"); self.git("add", "shared.txt"); self.git_commit("T1: dropped task work")
        self.git("checkout", "main")
        state_path = self.dir / ".teamwork" / "state.json"
        state = json.loads(state_path.read_text())
        state["threads"]["ui"]["branch"] = "tw/demo/ui"
        state["threads"]["api"]["branch"] = "tw/demo/ui"
        state["repo"]["base"] = "main"
        state_path.write_text(json.dumps(state))
        out = json.loads(self.tw("ready").stdout)
        self.assertIn("T3", out["blocked_by_lease"])

    def test_m127_sequential_tasks_keep_only_their_own_branch_lease_cli(self):
        self.tw("thread", "add", "ui")
        self.tw("thread", "add", "api")
        self.tw("plan", "approve", "--user-said", "approved")
        self.tw("task", "add", "T1", "--title", "first", "--owner", "ui", "--touches", "f1.txt", "--accept", "f1 updated")
        self.tw("task", "add", "T2", "--title", "second", "--owner", "ui", "--touches", "f2.txt", "--accept", "f2 updated")
        self.tw("task", "add", "T3", "--title", "third", "--owner", "ui", "--touches", "f3.txt", "--accept", "f3 updated")
        self.tw("task", "add", "T8", "--title", "api uses first", "--owner", "api", "--touches", "f1.txt", "--accept", "api reads f1")
        self.tw("task", "set", "T1", "--status", "done", "--review", "pass", "--reviewer", "lead")
        self.tw("task", "set", "T2", "--status", "done", "--review", "pass", "--reviewer", "lead")
        self.tw("task", "set", "T3", "--status", "working")
        self.git("init", "-q", "-b", "main")
        for name in ("f1.txt", "f2.txt", "f3.txt"):
            (self.dir / name).write_text("base\n")
        self.git("add", "f1.txt", "f2.txt", "f3.txt"); self.git_commit("base")
        self.git("checkout", "-qb", "tw/demo/ui")
        for tid, name in zip(("T1", "T2", "T3"), ("f1.txt", "f2.txt", "f3.txt")):
            (self.dir / name).write_text(tid + "\n")
            self.git("add", name); self.git_commit(f"{tid}: update {name}")
        out = json.loads(self.tw("ready").stdout)
        self.assertEqual([x["with"] for x in out["blocked_by_lease"]["T8"]], ["T1"], out)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.dir), *args], check=True, capture_output=True, text=True)

    def git_commit(self, message):
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", message)


if __name__ == "__main__":
    unittest.main()
