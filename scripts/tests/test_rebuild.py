try:
    from ._base import *
except ImportError:
    from _base import *


class Rebuild(GitCase):
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

    def test_rebuild_leads_contains_relead_seat_event_session(self):
        keys = self.proj.parent / "keys"
        keys.mkdir()
        env_before = os.environ.get("TW_KEYS_DIR")
        os.environ["TW_KEYS_DIR"] = str(keys)
        try:
            self.tw("lead", "--session", "old-lead")
            self.tw("opening", "--json")
            self.tw("screen", "record", "SC1", "--scores", ",".join(f"P{i}=0" for i in range(1, 8)))
            self.tw("screen", "relead", "SC1", "--said", "source request")
            self.tw("lead", "--session", "new-lead", "--relead")
            self.tw("lead", "--session", "later-lead", "--force")
            sessions = self.proj / "sessions.json"
            sessions.write_text(json.dumps([{"id": "api-session", "parent_session_id": "new-lead",
            "tags": ["teamwork:proj", "teamwork-thread:api"], "created_at": "2026-01-01T00:00:00Z"}]))
            self.tw("rebuild", "--sessions", str(sessions))
            rebuilt = json.loads((self.proj / ".teamwork" / "state.json.rebuilt").read_text())
            self.assertEqual(rebuilt["threads"]["api"]["session_id"], "api-session")
        finally:
            if env_before is None: os.environ.pop("TW_KEYS_DIR", None)
            else: os.environ["TW_KEYS_DIR"] = env_before

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

    def test_rebuild_preview_and_apply_increment_rev(self):
        self.populate()
        tw_dir = self.proj / ".teamwork"
        next_rev = json.loads((tw_dir / "state.json").read_text())["rev"] + 1
        self.tw("rebuild")
        self.assertEqual(json.loads((tw_dir / "state.json.rebuilt").read_text())["rev"], next_rev)
        self.tw("rebuild", "--apply")
        self.assertEqual(json.loads((tw_dir / "state.json").read_text())["rev"], next_rev)

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
        # A status-only change cannot reopen a retired stint; explicit session reassignment opens a fresh stint.
        self.tw("thread", "add", "quix", "--session", "S3")
        self.tw("thread", "set", "quix", "--status", "archived")
        self.tw("thread", "add", "blip", "--session", "S3")
        self.tw("thread", "set", "blip", "--status", "archived")
        self.assertNotEqual(self.tw("thread", "set", "quix", "--status", "idle", ok=False).returncode, 0)
        self.tw("thread", "set", "quix", "--session", "S3", "--status", "idle")
        quix=json.loads((self.proj/".teamwork"/"state.json").read_text())["threads"]["quix"]
        self.assertEqual((quix["session_id"],quix["status"]),("S3","idle"))
        self.assertEqual(quix["sessions"][-1]["session_id"],"S3")
        self.assertFalse(quix["sessions"][-1].get("to"))

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


if __name__ == "__main__":
    unittest.main()
