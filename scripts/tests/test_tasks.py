try:
    from ._base import *
except ImportError:
    from _base import *


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

    def test_m12_failed_latest_review_warns_when_review_gate_is_off(self):
        self.tw("prefs", "--set", "require_review=false", "--user-said", "disable review for this demo")
        self.tw("task", "set", "T1", "--status", "review")
        self.tw("task", "set", "T1", "--review", "fail", "--reviewer", "rev", "--review-note", "red")
        st = self.state(); st["tasks"]["T1"]["primary_check"] = {"result": "seen", "how": "checked", "evidence": "proof"}
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        result = self.tw("task", "set", "T1", "--status", "done")
        self.assertIn("latest review failed", result.stderr)

    def test_m19_done_warns_when_remaining_finding_has_no_followup(self):
        self.tw("task", "set", "T1", "--status", "review")
        self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "rev", "--rounds", "2",
                "--checks", "pass", "--remaining", "document edge case", "--no-blocking")
        result = self.tw("task", "set", "T1", "--status", "done")
        self.assertIn("no follow-up task", result.stderr)

    def test_m19_followups_apply_clears_done_warning(self):
        self.tw("task", "set", "T1", "--status", "review")
        self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "rev", "--rounds", "2",
                "--checks", "pass", "--remaining", "document edge case", "--no-blocking")
        self.tw("task", "followups", "T1", "--apply")
        result = self.tw("task", "set", "T1", "--status", "done")
        self.assertNotIn("no follow-up task", result.stderr)

    def test_m20_legacy_review_without_cap_renders_question_mark(self):
        st = self.state()
        st["tasks"]["T1"].update(status="done", review={"result": "skipped", "reviewer": "lead"}, review_rounds=1)
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.assertIn("1/?", self.tw("task", "reviews").stdout)

    def test_m25_skip_review_voids_prior_verdict_and_marks_followup(self):
        self.tw("task", "set", "T1", "--status", "review")
        self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "rev", "--rounds", "2",
                "--checks", "pass", "--remaining", "document edge case", "--no-blocking")
        self.tw("task", "followups", "T1", "--apply")
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "waived", "--user-said", "done")
        task = self.task()
        self.assertEqual(task["past_reviews"][-1].get("voided"), "review skipped")
        self.assertEqual(self.state()["tasks"]["T1-f1"].get("parent_verdict_voided"), "review skipped")

    def test_m14_dispatch_checks_file_lease_and_force_reason(self):
        self.tw("prefs", "--set", "root_mode=off")
        self.tw("thread", "add", "api")
        self.tw("task", "add", "T2", "--owner", "ui", "--touches", "shared.py")
        self.tw("task", "set", "T1", "--owner", "api", "--touches", "shared.py")
        self.tw("plan", "approve", "--user-said", "approve the plan")
        self.tw("task", "set", "T2", "--status", "dispatched")
        refused = self.tw("task", "set", "T1", "--status", "dispatched", ok=False)
        self.assertNotEqual(refused.returncode, 0)
        self.tw("task", "set", "T1", "--status", "dispatched", "--force-lease", "--lease-reason", "user wants overlap")

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

    def test_t76_reviewer_suffix_rounds_and_fail_status(self):
        base = ("task", "set", "T1", "--review", "pass", "--reviewer")
        tail = ("--tests", "ran", "--base", "abcdef0")
        for reviewer in ("ui/..", "ui/self", "ui/other"):
            r = self.raw(*(base + (reviewer,) + tail))
            self.assertNotEqual(r.returncode, 0, reviewer)
        r = self.raw(*(base + ("ui/subagent",) + tail + ("--rounds", "3")))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("advanced by 3", r.stderr)

    def test_t76_fail_cannot_set_review_status_and_cap_change_names_active_thread(self):
        r = self.raw("task", "set", "T1", "--review", "fail", "--reviewer", "ui/subagent",
                     "--tests", "ran", "--base", "abcdef0", "--status", "review")
        self.assertNotEqual(r.returncode, 0)
        self.tw("task", "set", "T1", "--status", "working")
        r = self.raw("prefs", "--set", "review_max_rounds=2", "--user-said", "use two rounds")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ui", r.stderr)

    def test_t76_format_controls_do_not_count_as_user_words(self):
        r = self.raw("prefs", "--set", "require_review=false", "--user-said", "\u200b\ufeff")
        self.assertNotEqual(r.returncode, 0)

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

class PrimaryOutcome(TwCase):
    """R71/G8 primary outcome gates, reporting and real subprocess behavior."""

    def test_suspect_words_ignore_temp_dir_names(self):
        proof = self.dir / "primary-proof.txt"
        proof.write_text("real output", encoding="utf-8")
        project = self.dir / "demo" / ".teamwork"
        self.assertFalse(tw._primary_suspect(project, "Open the real artifact", str(proof)))
        mocked = self.dir / "demo" / "tests" / "mocks" / "out.json"
        mocked.parent.mkdir(parents=True)
        mocked.write_text("{}", encoding="utf-8")
        self.assertTrue(tw._primary_suspect(project, "Open the artifact", str(mocked)))

    def test_suspect_paths_check_all_components_and_ignore_mkdtemp_names(self):
        random_project = self.dir / "tmpab1mockz" / "project" / ".teamwork"
        proof = random_project.parent / "out.json"
        proof.parent.mkdir(parents=True, exist_ok=True)
        proof.write_text("{}", encoding="utf-8")
        self.assertFalse(tw._primary_suspect(random_project, "Open the artifact", str(proof)))

        external = Path("/tmp/mocks/out.json")
        original_is_file = Path.is_file
        with mock.patch.object(Path, "is_file", autospec=True,
                               side_effect=lambda path: path == external or original_is_file(path)):
            self.assertTrue(tw._primary_suspect(random_project, "Open the artifact", str(external)))

        in_project = random_project.parent / "tests" / "mocks" / "out.json"
        in_project.parent.mkdir(parents=True, exist_ok=True)
        in_project.write_text("{}", encoding="utf-8")
        self.assertTrue(tw._primary_suspect(random_project, "Open the artifact", str(in_project)))

    def fixture_done(self, source, test_id="scripts/tests/primary_fixture_probe.py::Probe.test_reads"):
        self.add()
        self.tw("prefs", "--set", "fixtures_dir=scripts/fixtures")
        self.working()
        test_file = self.dir / "scripts/tests/primary_fixture_probe.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("class Probe:\n" + source, encoding="utf-8")
        return self.tw("task", "verify", "T1", "--seen", "--how", "Open the real artifact",
                       "--evidence", "output shows 12 records", "--test", test_id)

    def done_after_verify(self):
        return self.tw("task", "set", "T1", "--status", "done", "--skip-review", "focused gate test",
                       "--user-said", "skip review for this focused gate test", ok=False)

    def test_fixtures_gate_blocks_done_when_test_reads_no_fixture(self):
        self.fixture_done("    def test_reads(self):\n        assert True\n")
        result = self.done_after_verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("done needs", result.stderr)

    def test_fixtures_gate_passes_when_test_reads_fixture_dir_or_sample(self):
        self.fixture_done("    def test_reads(self):\n        from fixtures.regenerate import sample\n        sample('t80_compaction.jsonl')\n")
        result = self.done_after_verify()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fixtures_gate_counts_hand_built_fixture_paths(self):
        for body in ('        Path(__file__).parents[1] / "fixtures" / "state.json"\n',
                     '        os.path.join(root, "scripts", "fixtures", "state.json")\n',
                     '        Path("scripts/fixtures/state.json").read_text()\n'):
            with self.subTest(body=body):
                self.tearDown(); self.setUp()
                self.fixture_done("    def test_reads(self):\n" + body)
                result = self.done_after_verify()
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_fixtures_gate_ignores_fixture_word_outside_a_path(self):
        self.fixture_done('    def test_reads(self):\n        message = "read the fixtures first"\n        Path("other") / "data.json"\n')
        result = self.done_after_verify()
        self.assertNotEqual(result.returncode, 0)

    def test_fixtures_gate_ignores_comments_and_strings(self):
        self.fixture_done("    def test_reads(self):\n        # sample('fake.jsonl')\n        note = 'place(\\\"fake\\\") fixtures_dir'\n        assert note\n")
        result = self.done_after_verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reads", result.stderr)

    def test_fixtures_gate_rejects_empty_selector(self):
        result = self.fixture_done("    def test_reads(self):\n        assert True\n",
                                   "scripts/tests/primary_fixture_probe.py::")
        result = self.done_after_verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("selector after :: must not be empty", result.stderr)

    def test_fixtures_gate_off_when_pref_empty(self):
        self.fixture_done("    def test_reads(self):\n        assert True\n")
        self.tw("prefs", "--set", "fixtures_dir=")
        self.assertEqual(self.done_after_verify().returncode, 0)

    def test_fixtures_gate_skips_exempt_and_legacy(self):
        from unittest.mock import Mock
        st = self.state()
        st["prefs"]["fixtures_dir"] = "scripts/fixtures"
        task = {"primary": "visible result", "primary_exempt": True}
        self.assertIsNone(tw.fixtures_gate(st, "T1", {}, task, {"status_to": "done"}, Mock(), ""))
        task = {"primary": "visible result", "primary_legacy": True}
        self.assertIsNone(tw.fixtures_gate(st, "T1", {}, task, {"status_to": "done"}, Mock(), ""))

    def test_evidence_under_fixtures_dir_is_not_suspect(self):
        self.tw("prefs", "--set", "fixtures_dir=scripts/fixtures")
        fixture = self.dir / "scripts/fixtures/fixture-output.json"
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_text("{}", encoding="utf-8")
        self.assertFalse(tw._primary_suspect(self.dir / ".teamwork", "Open the output", str(fixture)))

    def setUp(self):
        super().setUp()
        self.tw("prefs", "--set", "require_primary=true")

    def tw(self, *args, ok=True, walk=True):
        if walk:
            walk_args(lambda *x: self.tw(*x, walk=False), args, self.dir / ".teamwork" / "state.json")
        r = subprocess.run([sys.executable, str(TW), *args], cwd=self.dir, env=self.env,
                           capture_output=True, text=True)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def add(self, tid="T1", primary="The user sees all 12 restored records"):
        if "worker" not in self.state()["threads"]:
            self.tw("thread", "add", "worker")
        args = ["task", "add", tid, "--title", "restore records", "--owner", "worker", "--accept", "records match"]
        if primary:
            args += ["--primary", primary]
        self.tw(*args)

    def verify(self, tid="T1", *extra):
        proof = self.dir / f"{tid}-primary.txt"
        proof.write_text("real artifact: 12 records", encoding="utf-8")
        return self.tw("task", "verify", tid, "--seen", "--how", "Open the real project artifact",
                       "--evidence", str(proof), *extra)

    def working(self, tid="T1"):
        self.tw("task", "set", tid, "--status", "working")

    def reviewed_task(self, tid="T1", primary="The user sees all 12 restored records"):
        self.add(tid, primary)
        self.working(tid)
        self.tw("task", "verify", tid, "--seen", "--how", "open the real artifact", "--evidence", "output shows 12 records")
        self.tw("task", "set", tid, "--status", "review")

    def passing_review(self, tid="T1", **extra):
        args = ["task", "set", tid, "--review", "pass", "--reviewer", "reviewer", "--tests", "ran",
                "--base", "abcdef0", "--recheck-how", "reviewer independently opened the artifact",
                "--recheck-evidence", "reviewer confirms 12 records"]
        if extra.get("suspect_cleared"):
            args.append("--suspect-cleared")
        self.tw(*args)

    def test_review_gate_evidence_and_recheck(self):
        self.add()
        self.working()
        denied = self.tw("task", "set", "T1", "--status", "review", ok=False)
        self.assertIn("primary outcome", denied.stderr.lower())
        empty = self.tw("task", "verify", "T1", "--seen", "--how", "Open artifact", "--evidence", "OK", ok=False)
        self.assertIn("concrete", empty.stderr.lower())
        absent = self.tw("task", "verify", "T1", "--seen", "--how", "Open artifact", "--evidence",
                         "./missing-primary.png", ok=False)
        self.assertIn("concrete", absent.stderr.lower())
        self.tw("task", "verify", "T1", "--seen", "--how", "Open artifact", "--evidence", "shows 12 records")
        self.tw("task", "set", "T1", "--status", "review")
        no_recheck = self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "reviewer",
                             "--tests", "ran", "--base", "abcdef0", ok=False)
        self.assertIn("recheck", no_recheck.stderr.lower())
        self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "reviewer", "--tests", "ran",
                "--base", "abcdef0", "--recheck-how", "independently opened artifact", "--recheck-evidence",
                "confirmed 12 records")

    def test_skip_review_done_still_requires_owner(self):
        self.add()
        self.working()
        self.verify()
        self.tw("task", "set", "T1", "--status", "review")
        self.tw("task", "set", "T1", "--owner", "")
        denied = self.tw("task", "set", "T1", "--status", "done", "--skip-review", "user waived",
                         "--user-said", "skip review", ok=False)
        self.assertIn("no owner", denied.stderr)

    def test_relative_evidence_paths_from_project_folder(self):
        # Found on the real CLI: a bare screenshot name in the project folder was refused.
        self.add()
        self.working()
        (self.dir / "shot.png").write_bytes(b"png")
        (self.dir / "docs" / "shots").mkdir(parents=True)
        (self.dir / "docs" / "shots" / "chat.png").write_bytes(b"png")
        self.tw("task", "verify", "T1", "--seen", "--how", "Open the real app", "--evidence", "shot.png")
        self.tw("task", "verify", "T1", "--seen", "--how", "Open the real app", "--evidence",
                "screenshot docs/shots/chat.png")
        self.tw("task", "set", "T1", "--status", "review")
        self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "reviewer", "--tests", "ran",
                "--base", "abcdef0", "--recheck-how", "independently reopened the app",
                "--recheck-evidence", "shot.png")
        nothing = self.tw("task", "verify", "T1", "--seen", "--how", "Open the real app", "--evidence",
                          "missing.png", ok=False)
        self.assertIn("concrete", nothing.stderr.lower())
        self.tw("task", "verify", "T1", "--seen", "--how", "真跑 CLI", "--evidence", "输出 15 条")
        bare = self.tw("task", "verify", "T1", "--seen", "--how", "真跑 CLI", "--evidence", "15", ok=False)
        self.assertIn("concrete", bare.stderr.lower())

    def test_zh_project_shows_chinese_everywhere(self):
        # Lead's check: a zh project must never show the new strings in English.
        self.tw("prefs", "--set", "language=zh")
        self.add()
        self.working()
        self.tw("task", "verify", "T1", "--seen", "--how", "真启动 App", "--evidence", "显示 132 条，原库 132 条")
        self.tw("task", "set", "T1", "--status", "review")
        (self.dir / "shot.png").write_bytes(b"png")
        self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "reviewer", "--tests", "not-run",
                "--tests-why", "没装浏览器", "--checks", "pass", "--base", "abcdef0",
                "--recheck-how", "独立重开 App", "--recheck-evidence", "shot.png")
        self.tw("task", "set", "T1", "--status", "done")
        card = self.tw("card", "--kind", "done", "--head", "T1 完成").stdout
        self.assertTrue(card.startswith("主产出：看到了"), card)
        self.assertIn("测试：没跑（没装浏览器） · 基于 abcdef0", card)
        self.assertIn("测试：没跑（没装浏览器）", self.tw("task", "reviews", "T1").stdout)
        self.add("T2", "打开页面能看到 15 条记录")
        self.working("T2")
        self.tw("task", "verify", "T2", "--seen", "--how", "真跑 CLI", "--evidence", "输出 15 条")
        self.tw("task", "set", "T2", "--status", "review")
        self.tw("task", "set", "T2", "--review", "fail", "--env-down", "浏览器起不来", "--reviewer", "reviewer",
                "--tests", "not-run", "--tests-why", "浏览器缺失", "--base", "abcdef0")
        done = self.tw("task", "set", "T2", "--status", "done", ok=False)
        self.assertIn("环境起不来，待重跑：done 要等重跑", done.stderr)
        board = self.tw("board").stdout
        self.assertIn("看到了", board)
        self.assertNotIn("blocked-env (", board)
        self.assertNotIn("environment unavailable", board)
        noreason = self.tw("task", "set", "T2", "--review", "fail", "--reviewer", "reviewer", "--tests", "not-run",
                           "--base", "abcdef0", ok=False)
        self.assertIn("--tests-why", noreason.stderr)
        self.assertIn("原因", noreason.stderr)
        self.assertIn("主产出：", self.tw("task-msg", "T2").stdout)

    def test_suspect_cleared_with_chinese_negation(self):
        # Found on the real CLI: "确认不是 mock" in --recheck-how kept the review refused.
        self.add()
        self.working()
        self.tw("task", "verify", "T1", "--seen", "--how", "用 mock 数据跑", "--evidence", "显示 132 条，原库 132 条")
        self.assertTrue(self.state()["tasks"]["T1"]["primary_suspect"])
        self.tw("task", "verify", "T1", "--seen", "--how", "真启动，没用 mock", "--evidence", "显示 132 条，原库 132 条")
        self.tw("task", "set", "T1", "--status", "review")
        (self.dir / "shot.png").write_bytes(b"png")
        self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "reviewer", "--tests", "ran",
                "--base", "abcdef0", "--recheck-how", "独立重开 App，确认不是 mock",
                "--recheck-evidence", "shot.png", "--suspect-cleared")
        self.assertEqual(self.state()["tasks"]["T1"]["review"]["result"], "pass")

    def test_suspect_warning_clear_and_negation(self):
        self.add()
        self.working()
        suspected = self.tw("task", "verify", "T1", "--seen", "--how", "Run mocked browser", "--evidence",
                            "shows 12 records")
        self.assertIn("Warning", suspected.stderr)
        self.assertTrue(self.state()["tasks"]["T1"]["primary_suspect"])
        self.tw("task", "set", "T1", "--status", "review")
        withheld = self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "reviewer", "--tests", "ran",
                           "--base", "abcdef0", "--recheck-how", "independently opened artifact",
                           "--recheck-evidence", "confirmed 12 records", ok=False)
        self.assertIn("suspect", withheld.stderr.lower())
        self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "reviewer", "--tests", "ran",
                "--base", "abcdef0", "--recheck-how", "independent real run, no mock used",
                "--recheck-evidence", "confirmed 12 records", "--suspect-cleared")
        self.add("T2")
        self.working("T2")
        self.tw("task", "verify", "T2", "--seen", "--how", "no mock; open the real artifact", "--evidence",
                "shows 12 records")
        self.assertNotIn("primary_suspect", self.state()["tasks"]["T2"])
        self.tw("task", "set", "T2", "--status", "review")
        self.tw("task", "set", "T2", "--status", "working")
        task = self.state()["tasks"]["T2"]
        self.assertNotIn("primary_check", task)
        self.assertEqual(task["past_primary_checks"][-1]["result"], "seen")

    def test_pending_real_done_requires_user_words(self):
        self.add()
        self.working()
        self.tw("task", "verify", "T1", "--pending-real", "--how", "Needs physical device", "--evidence",
                "device run pending: ticket 12", "--why", "No device available")
        self.tw("task", "set", "T1", "--status", "review")
        self.tw("prefs", "--set", "require_review=false", "--user-said", "skip independent review for this fixture")
        denied = self.tw("task", "set", "T1", "--status", "done", ok=False)
        self.assertIn("user-said", denied.stderr)
        self.tw("task", "set", "T1", "--status", "done", "--user-said", "accept device check later")
        self.assertIn("pending-real", self.tw("board").stdout)
        self.assertTrue(self.tw("card", "--kind", "done", "--head", "done").stdout.startswith("Primary outcome: pending-real"))

    def test_blocked_environment_done_message_and_no_round(self):
        self.tw("prefs", "--set", "language=zh")
        self.add()
        self.working()
        self.verify()
        self.tw("task", "set", "T1", "--status", "review")
        self.tw("task", "set", "T1", "--review", "fail", "--reviewer", "reviewer", "--tests", "not-run",
                "--tests-why", "browser runtime unavailable", "--base", "abcdef0", "--env-down",
                "browser runtime cannot start")
        task = self.state()["tasks"]["T1"]
        self.assertEqual(task["review"]["result"], "blocked-env")
        self.assertNotIn("review_rounds", task)
        denied = self.tw("task", "set", "T1", "--status", "done", ok=False)
        self.assertIn("环境起不来，待重跑", denied.stderr)

    def test_review_options_waiver_board_reviews_card_and_dispatch(self):
        self.add()
        self.working()
        self.verify()
        self.tw("task", "set", "T1", "--status", "review")
        missing_tests = self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "reviewer", "--base",
                                "abcdef0", "--recheck-how", "opened artifact", "--recheck-evidence", "12 records",
                                ok=False)
        self.assertIn("--tests", missing_tests.stderr)
        missing_base = self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "reviewer", "--tests", "ran",
                               "--recheck-how", "opened artifact", "--recheck-evidence", "12 records", ok=False)
        self.assertIn("--base", missing_base.stderr)
        missing_why = self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "reviewer", "--tests",
                              "not-run", "--base", "abcdef0", "--recheck-how", "opened artifact",
                              "--recheck-evidence", "12 records", ok=False)
        self.assertIn("--tests-why", missing_why.stderr)
        self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "reviewer", "--tests", "not-run",
                "--tests-why", "runtime unavailable", "--base", "abcdef0", "--recheck-how", "opened artifact",
                "--recheck-evidence", "12 records")
        reviews = self.tw("task", "reviews", "T1").stdout
        self.assertIn("tests", reviews.lower())
        board = self.tw("board").stdout
        self.assertIn("primary", board.lower())
        self.tw("task", "set", "T1", "--status", "done")
        card = self.tw("card", "--kind", "done", "--head", "Task done").stdout
        self.assertTrue(card.splitlines()[0].startswith("Primary outcome:"), card)
        msg = self.tw("task-msg", "T1").stdout
        self.assertIn("Primary outcome:", msg)
        self.assertIn("Test order:", msg)
        self.tw("task", "add", "T2", "--title", "unscoped code task", "--owner", "worker", "--work-kind", "code")
        missing_primary = self.tw("task-msg", "T2", "--dispatch", ok=False)
        self.assertIn("primary outcome", missing_primary.stderr.lower())
        self.tw("task-msg", "T2", "--dispatch", "--no-primary", "user waived", "--user-said", "ship without a primary")

    def test_merge_rejects_missing_primary_and_records_waiver(self):
        def git(*args):
            return subprocess.run(["git", "-C", str(self.dir), *args], capture_output=True, text=True)
        git("init", "-q", "-b", "main")
        git("config", "user.name", "test")
        git("config", "user.email", "test@example.invalid")
        (self.dir / "base.txt").write_text("base\n", encoding="utf-8")
        git("add", "base.txt"); git("commit", "-qm", "initial")
        self.add(primary=None)
        git("checkout", "-q", "-b", "tw/demo/worker")
        (self.dir / "change.txt").write_text("change\n", encoding="utf-8")
        git("add", "change.txt"); git("commit", "-qm", "T1: add change")
        sha = git("rev-parse", "HEAD").stdout.strip()
        blocked = self.tw("merge", "tw/demo/worker", "--sha", sha, "--no-test", "docs only", "--message",
                          "T1: add change", "--apply", ok=False)
        self.assertIn("primary", blocked.stderr.lower())
        state_path = self.dir / ".teamwork" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["tasks"]["T1"]["primary_exempt"] = True
        state["tasks"]["T1"].setdefault("primary_waivers", []).append(
            {"why": "legacy waiver fixture", "at": "2026-01-01T00:00:00", "kind": "merge-primary"})
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        self.tw("merge", "tw/demo/worker", "--sha", sha, "--no-test", "docs only", "--message",
                "T1: add change", "--user-said", "merge without primary proof", "--apply")
        task = self.state()["tasks"]["T1"]
        self.assertTrue(task["primary_exempt"])
        self.assertEqual(task["primary_waivers"][-1]["kind"], "merge-primary")

    def _merge_repo(self):
        def git(*args):
            return subprocess.run(["git", "-C", str(self.dir), *args], capture_output=True, text=True)
        git("init", "-q", "-b", "main"); git("config", "user.name", "test")
        git("config", "user.email", "test@example.invalid")
        (self.dir / "base.txt").write_text("base\n", encoding="utf-8")
        git("add", "base.txt"); git("commit", "-qm", "initial")
        git("checkout", "-q", "-b", "tw/demo/worker")
        (self.dir / "change.txt").write_text("change\n", encoding="utf-8")
        git("add", "change.txt"); git("commit", "-qm", "T1: add change")
        sha = git("rev-parse", "HEAD").stdout.strip()
        git("checkout", "-q", "main")
        return sha

    def _seen_in_review(self, tid="T1"):
        self.add(tid)
        self.working(tid)
        self.tw("task", "verify", tid, "--seen", "--how", "real CLI", "--evidence", "output shows 12 records")
        self.tw("task", "set", tid, "--status", "review")

    def test_merge_blocked_env_needs_user_words(self):
        # Round-3 review I2: a blocked-env task must not merge silently.
        self._seen_in_review()
        self.tw("task", "set", "T1", "--review", "fail", "--env-down", "browser cannot start", "--reviewer",
                "reviewer", "--tests", "not-run", "--tests-why", "no browser", "--base", "abcdef0")
        sha = self._merge_repo()
        denied = self.tw("merge", "tw/demo/worker", "--sha", sha, "--no-test", "docs", "--apply", ok=False)
        self.assertIn("rerun", denied.stderr)
        self.tw("merge", "tw/demo/worker", "--sha", sha, "--no-test", "docs", "--apply", "--user-said", "merge it")

    def test_merge_ignores_queued_tasks_of_the_same_owner(self):
        # Round-3 review I3: a pre-queued next task (IL-5) has no commits and must not block the merge.
        self._seen_in_review()
        self.passing_review()
        self.tw("task", "add", "T5", "--title", "next", "--owner", "worker", "--accept", "ok", "--deps", "T1")
        sha = self._merge_repo()
        self.tw("merge", "tw/demo/worker", "--sha", sha, "--no-test", "docs", "--apply")

    def test_unrecorded_tests_are_not_shown_as_ran(self):
        # Round-3 review I4: a review without a tests record must not read "ran".
        self.tw("prefs", "--set", "language=zh")
        self._seen_in_review()
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "trivial", "--user-said", "跳过")
        shown = self.tw("task", "reviews", "T1").stdout + self.tw("card", "--kind", "done", "--head", "x").stdout
        self.assertNotIn("测试：跑了", shown)
        self.assertIn("未记录", shown)

    def test_disabling_primary_gate_needs_user_said(self):
        denied = self.tw("prefs", "--set", "require_primary=false", ok=False)
        self.assertIn("user's decision", denied.stderr)
        self.tw("prefs", "--set", "require_primary=false", "--user-said", "disable for this project")
        self.assertFalse(self.state()["prefs"]["require_primary"])

    def test_b1_combined_status_and_passing_review_flags_are_gated(self):
        self.reviewed_task()
        for status in ("review", "done"):
            denied = self.tw("task", "set", "T1", "--status", status, "--review", "pass", "--reviewer", "reviewer",
                             "--tests", "ran", "--base", "abcdef0", ok=False)
            self.assertIn("recheck", denied.stderr.lower())

    def test_b1_suspect_cannot_combine_done_and_pass(self):
        self.add(); self.working()
        self.tw("task", "verify", "T1", "--seen", "--how", "run mocked artifact", "--evidence", "output shows 12 records")
        self.tw("task", "set", "T1", "--status", "review")
        denied = self.tw("task", "set", "T1", "--status", "done", "--review", "pass", "--reviewer", "reviewer",
                         "--tests", "ran", "--base", "abcdef0", "--recheck-how",
                         "independent real run", "--recheck-evidence", "reviewer confirms 12 records", ok=False)
        self.assertIn("suspect", denied.stderr.lower())

    def test_b2_failed_review_invalidates_primary_before_rework(self):
        self.reviewed_task()
        self.tw("task", "set", "T1", "--review", "fail", "--reviewer", "reviewer", "--tests", "ran", "--base", "abcdef0")
        self.assertEqual(self.state()["tasks"]["T1"]["status"], "working")
        denied = self.tw("task", "set", "T1", "--status", "review", ok=False)
        self.assertIn("primary outcome", denied.stderr.lower())
        self.assertNotIn("primary_check", self.state()["tasks"]["T1"])

    def test_i1_verify_primary_and_owner_changes_void_reviews(self):
        self.reviewed_task()
        self.passing_review()
        self.tw("task", "verify", "T1", "--seen", "--how", "reviewer reopens real artifact", "--evidence", "new output shows 12 records")
        task = self.state()["tasks"]["T1"]
        self.assertNotIn("review", task)
        self.assertEqual(task["past_reviews"][-1]["result"], "pass")
        self.assertEqual(task["primary_check"]["evidence"], "new output shows 12 records")

        self.passing_review()
        # A new owner changes neither the artifact nor the reviewed diff: the check and an
        # independent pass stay (T23 rule); a reviewer who becomes the owner voids the pass.
        self.tw("thread", "add", "replacement-owner")
        self.tw("task", "set", "T1", "--owner", "replacement-owner")
        task = self.state()["tasks"]["T1"]
        self.assertEqual(task["status"], "review")
        self.assertEqual(task["review"]["result"], "pass")
        self.assertEqual(task["primary_check"]["result"], "seen")
        self.tw("thread", "add", "reviewer")
        self.tw("task", "set", "T1", "--owner", "reviewer")
        task = self.state()["tasks"]["T1"]
        self.assertNotIn("review", task)
        self.assertEqual(task["primary_check"]["result"], "seen")
        self.tw("task", "set", "T1", "--status", "done", ok=False)

    def test_i1_primary_change_voids_review_and_evidence(self):
        self.reviewed_task()
        self.passing_review()
        self.tw("task", "set", "T1", "--primary", "The user sees exactly 13 restored records")
        task = self.state()["tasks"]["T1"]
        self.assertEqual(task["status"], "working")
        self.assertNotIn("review", task)
        self.assertNotIn("primary_check", task)

    def test_i1_base_change_voids_review(self):
        self.reviewed_task()
        self.passing_review()
        self.tw("task", "set", "T1", "--base", "1234567")
        task = self.state()["tasks"]["T1"]
        self.assertEqual(task["status"], "review")
        self.assertNotIn("review", task)
        self.assertEqual(task["past_reviews"][-1]["voided"], "review base changed")

    def test_i2_dispatch_exemption_bypasses_primary_gates(self):
        self.add(primary=None)
        self.tw("plan", "approve", "--user-said", "approve exemption fixture")
        self.tw("task-msg", "T1", "--dispatch", "--no-primary", "user waived", "--user-said", "ship without primary")
        self.working()
        self.tw("task", "set", "T1", "--status", "review")
        self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "reviewer", "--tests", "ran", "--base", "abcdef0")
        self.tw("task", "set", "T1", "--status", "done")
        self.assertIn("豁免" if self.state().get("prefs", {}).get("language") == "zh" else "waived", self.tw("board").stdout)
        self.assertIn("waived", self.tw("card", "--kind", "done", "--head", "done").stdout)

    def test_minor_primary_text_and_suspect_clause_rules(self):
        self.assertTrue(tw._primary_text_valid("列表显示三条"))
        self.assertFalse(tw._primary_text_valid("Open 12"))
        self.assertFalse(tw._primary_evidence_specific(self.dir, "passed 100%"))
        self.assertFalse(tw._primary_evidence_specific(self.dir, "/etc/hosts"))
        self.assertFalse(tw._primary_suspect(self.dir, "simulator screenshot", "output shows 12 rows"))
        self.assertTrue(tw._primary_suspect(self.dir, "no mock, but used sinon", "shows 12 rows"))
        self.assertFalse(tw._primary_suspect(self.dir, "no mock", "output shows 12 rows"))
        self.assertTrue(tw._primary_evidence_specific(self.dir, "https://example.invalid/report"))

    def test_recheck_evidence_must_be_independent(self):
        self.reviewed_task()
        denied = self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "reviewer", "--tests", "ran",
                         "--base", "abcdef0", "--recheck-how", "independently opened artifact",
                         "--recheck-evidence", "output shows 12 records", ok=False)
        self.assertIn("own", denied.stderr.lower())

    def test_recheck_suspect_terms_require_explicit_clearance(self):
        self.reviewed_task()
        denied = self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "reviewer", "--tests", "ran",
                         "--base", "abcdef0", "--recheck-how", "open the browser", "--recheck-evidence",
                         "sinon output shows 13 records", ok=False)
        self.assertIn("suspect", denied.stderr.lower())
        self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "reviewer", "--tests", "ran",
                "--base", "abcdef0", "--recheck-how", "open the browser", "--recheck-evidence",
                "sinon output shows 13 records", "--suspect-cleared")
        self.assertTrue(self.state()["tasks"]["T1"]["review"]["suspect_cleared"])

    def test_invariant_flag_combinations_keep_final_state_valid(self):
        self.reviewed_task()
        self.passing_review()
        self.tw("thread", "add", "alternate")
        seed = self.state()
        names = ("status", "review", "primary", "owner")
        combinations = [combo for size in (1, 2, 3) for combo in itertools.combinations(names, size)]
        scenario_statuses = ("working", "review", "done-ready")
        serial = 0
        for initial in scenario_statuses:
            for combo in combinations:
                serial += 1
                tid = f"Tcombo{serial}"
                fixture = json.loads(json.dumps(seed))
                task = json.loads(json.dumps(seed["tasks"]["T1"]))
                task["status"] = "review" if initial != "working" else "working"
                if initial == "working":
                    task.pop("review", None); task.pop("review_rounds", None)
                if initial == "review":
                    task.pop("review", None); task.pop("review_rounds", None)
                task["id"] = tid
                fixture["tasks"][tid] = task
                state_file = self.dir / ".teamwork" / "state.json"
                state_file.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
                args = ["task", "set", tid]
                if "status" in combo:
                    args += ["--status", {"working": "review", "review": "working", "done-ready": "done"}[initial]]
                if "primary" in combo:
                    args += ["--primary", f"The user sees updated result {serial}"]
                if "owner" in combo:
                    args += ["--owner", "alternate"]
                if "review" in combo:
                    args += ["--review", "pass", "--reviewer", "fresh-reviewer", "--tests", "ran", "--base", "abcdef0",
                             "--recheck-how", "fresh reviewer opens real output", "--recheck-evidence",
                             f"reviewer confirms {serial + 20} records"]
                result = subprocess.run([sys.executable, str(TW), *args], cwd=self.dir, env=self.env,
                                        capture_output=True, text=True)
                after = json.loads(state_file.read_text(encoding="utf-8"))["tasks"][tid]
                if result.returncode == 0 and after["status"] in ("review", "done"):
                    self.assertIn((after.get("primary_check") or {}).get("result"), ("seen", "pending-real"),
                                  (initial, combo, result.stderr))
                if (after.get("review") or {}).get("result") in ("pass", "capped"):
                    self.assertTrue(after["review"].get("recheck_evidence"), (initial, combo))
                    self.assertNotEqual(after["review"].get("recheck_evidence"),
                                        (after.get("primary_check") or {}).get("evidence"), (initial, combo))

    def test_i3_merge_message_matches_complete_task_ids(self):
        def scenario(name, message, squash, blocked_tid=None, valid_tid=None):
            root = self.dir / name
            root.mkdir()
            def git(*args):
                return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
            git("init", "-q", "-b", "main")
            git("config", "user.name", "test")
            git("config", "user.email", "test@example.invalid")
            (root / "base.txt").write_text("base\n", encoding="utf-8")
            git("add", "base.txt"); git("commit", "-qm", "initial")
            init = subprocess.run([sys.executable, str(TW), "init", "demo", "--goal", "merge fixture"], cwd=root,
                                  env=self.env, capture_output=True, text=True)
            self.assertEqual(init.returncode, 0, init.stderr)
            if blocked_tid:
                thread = subprocess.run([sys.executable, str(TW), "thread", "add", "worker"], cwd=root,
                                        env=self.env, capture_output=True, text=True)
                self.assertEqual(thread.returncode, 0, thread.stderr)
            for tid in ("T1", "T12"):
                task_args = ["task", "add", tid, "--title", tid, "--accept", "merged"]
                if blocked_tid: task_args += ["--owner", "worker"]
                add = subprocess.run([sys.executable, str(TW), *task_args],
                                     cwd=root, env=self.env, capture_output=True, text=True)
                self.assertEqual(add.returncode, 0, add.stderr)
            state_path = root / ".teamwork" / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            proof = root / "proof.txt"; proof.write_text("proof", encoding="utf-8")
            for tid in ("T1", "T12"):
                if tid == valid_tid:
                    state["tasks"][tid]["primary_check"] = {"result": "seen", "evidence": str(proof)}
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            git("checkout", "-q", "-b", "tw/demo/worker")
            (root / "change.txt").write_text("change\n", encoding="utf-8")
            git("add", "change.txt"); git("commit", "-qm", f"{valid_tid or blocked_tid or 'T1'}-fix: change")
            sha = git("rev-parse", "HEAD").stdout.strip()
            args = ["merge", "tw/demo/worker", "--sha", sha, "--no-test", "docs only"]
            if message is not None: args += ["--message", message]
            if squash: args += ["--squash"]
            args += ["--apply"]
            result = subprocess.run([sys.executable, str(TW), *args], cwd=root, env=self.env,
                                    capture_output=True, text=True)
            if blocked_tid:
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn(blocked_tid, result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stderr)
                if message is None or message == "chore: squash":
                    self.assertIn("no tasks are associated", result.stderr.lower())

        scenario("merge_t1", "fix: change (T1)", False, valid_tid="T1")
        scenario("merge_t12", "fix: change (T12)", False, valid_tid="T12")
        scenario("merge_no_message", None, False, blocked_tid="T1")
        scenario("merge_squash", "chore: squash", True, blocked_tid="T1")

    def test_legacy_review_done_tasks_allow_unrelated_updates(self):
        self.add("Tlegacy-review")
        self.add("Tlegacy-done")
        path = self.dir / ".teamwork" / "state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        legacy_fields = {key: state["tasks"]["Tlegacy-review"][key]
                         for key in ("title", "owner", "deps", "accept", "touches", "topics", "notes", "created")}
        state["tasks"]["Tlegacy-review"] = {**legacy_fields, "status": "review",
                                               "review": {"result": "pass", "reviewer": "reviewer"}}
        state["tasks"]["Tlegacy-done"] = {**legacy_fields, "title": "legacy done fixture", "status": "done"}
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        self.tw("task", "set", "Tlegacy-review", "--notes", "legacy review note")
        self.tw("task", "set", "Tlegacy-done", "--notes", "legacy done note")
        self.tw("task", "set", "Tlegacy-done", "--merged", "abcdef0")
        board = self.tw("board").stdout
        self.assertIn("Tlegacy-review", board)
        self.assertIn("legacy", board)
        self.assertIn("Tlegacy-done", board)

    def test_return_to_working_paths_share_invalidation_and_keep_not_seen(self):
        def ready(tid):
            self.add(tid)
            self.working(tid)
            self.tw("task", "verify", tid, "--seen", "--how", "open real artifact", "--evidence", "output shows 12 records")
            self.tw("task", "set", tid, "--status", "review")
            self.tw("task", "set", tid, "--checks", "pass")
            self.tw("task", "set", tid, "--merged", "abcdef0")

        ready("T1")
        self.tw("task", "set", "T1", "--status", "working")
        explicit = self.state()["tasks"]["T1"]
        self.assertEqual((explicit["status"], explicit["checks"], explicit["rework"]), ("working", "none", 1))
        self.assertNotIn("merged_sha", explicit)
        self.assertNotIn("primary_check", explicit)

        ready("T2")
        self.tw("task", "set", "T2", "--review", "fail", "--reviewer", "reviewer", "--tests", "ran", "--base", "abcdef0")
        failed = self.state()["tasks"]["T2"]
        self.assertEqual((failed["status"], failed["checks"], failed["rework"]), ("working", "none", 1))
        self.assertNotIn("merged_sha", failed)
        self.assertNotIn("primary_check", failed)

        ready("T3")
        self.tw("task", "set", "T3", "--primary", "The user sees exactly 14 restored records")
        changed = self.state()["tasks"]["T3"]
        self.assertEqual((changed["status"], changed["checks"], changed["rework"]), ("working", "none", 1))
        self.assertNotIn("merged_sha", changed)
        self.assertNotIn("primary_check", changed)

        ready("T4")
        self.tw("task", "verify", "T4", "--not-seen", "--how", "open real artifact", "--evidence",
                "output shows 0 matching records")
        not_seen = self.state()["tasks"]["T4"]
        self.assertEqual((not_seen["status"], not_seen["checks"], not_seen["rework"]), ("working", "none", 1))
        self.assertEqual(not_seen["primary_check"]["result"], "not-seen")
        self.assertNotIn("merged_sha", not_seen)

    def test_merge_refs_history_and_one_time_waiver(self):
        def fixture(name, alias="refs/heads/x", subject="T1-fix: change", verified=True, conflict=False, owner_switch=False):
            root = self.dir / name
            root.mkdir()
            def git(*args):
                return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
            git("init", "-q", "-b", "main")
            git("config", "user.name", "test")
            git("config", "user.email", "test@example.invalid")
            (root / "base.txt").write_text("base\n", encoding="utf-8")
            git("add", "base.txt"); git("commit", "-qm", "initial")
            init = subprocess.run([sys.executable, str(TW), "init", "demo", "--goal", "merge fixture"], cwd=root,
                                  env=self.env, capture_output=True, text=True)
            self.assertEqual(init.returncode, 0, init.stderr)
            for args in (("thread", "add", "worker"), ("thread", "add", "replacement")):
                result = subprocess.run([sys.executable, str(TW), *args], cwd=root, env=self.env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            add = subprocess.run([sys.executable, str(TW), "task", "add", "T1", "--title", "work", "--owner",
                                  "replacement" if owner_switch else "worker", "--accept", "merged",
                                  "--primary", "The user sees 12 restored records"], cwd=root, env=self.env,
                                 capture_output=True, text=True)
            self.assertEqual(add.returncode, 0, add.stderr)
            state_path = root / ".teamwork" / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["threads"]["worker"]["branch"] = "refs/heads/x"
            task = state["tasks"]["T1"]
            task["status"] = "working"  # dispatched work has commits on the branch; queued work does not
            if owner_switch:
                task["past_owners"] = ["worker"]
            if verified:
                task["primary_check"] = {"result": "seen", "evidence": "output shows 12 records", "sha": None}
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            git("checkout", "-q", "-b", "x")
            if conflict:
                (root / "base.txt").write_text("branch\n", encoding="utf-8")
                git("add", "base.txt"); git("commit", "-qm", subject)
                git("checkout", "-q", "main")
                (root / "base.txt").write_text("main\n", encoding="utf-8")
                git("add", "base.txt"); git("commit", "-qm", "main change")
                git("checkout", "-q", "x")
            else:
                (root / "change.txt").write_text("change\n", encoding="utf-8")
                git("add", "change.txt"); git("commit", "-qm", subject)
            sha = git("rev-parse", "HEAD").stdout.strip()
            return root, git, state_path, sha

        for index, alias in enumerate(("heads/x", "refs/heads/x", "x", "sha")):
            root, git, state_path, sha = fixture(f"merge-ref-{index}", subject="T1-fix: change")
            branch_ref = sha if alias == "sha" else alias
            result = subprocess.run([sys.executable, str(TW), "merge", branch_ref, "--sha", sha, "--no-test",
                                     "docs only", "--apply"], cwd=root, env=self.env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

        root, git, state_path, sha = fixture("merge-owner-switch", subject="ordinary change", verified=False,
                                             owner_switch=True)
        blocked = subprocess.run([sys.executable, str(TW), "merge", "refs/heads/x", "--sha", sha, "--no-test",
                                  "docs only", "--apply"], cwd=root, env=self.env, capture_output=True, text=True)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("T1", blocked.stderr)

        root, git, state_path, sha = fixture("merge-failed-waiver", subject="ordinary change", verified=False,
                                             conflict=True)
        before_log = (root / ".teamwork" / "log.jsonl").read_text(encoding="utf-8")
        failed = subprocess.run([sys.executable, str(TW), "merge", "x", "--sha", sha, "--no-test", "docs only",
                                 "--user-said", "allow this merge once", "--apply"], cwd=root, env=self.env,
                                capture_output=True, text=True)
        self.assertNotEqual(failed.returncode, 0)
        after = json.loads(state_path.read_text(encoding="utf-8"))["tasks"]["T1"]
        self.assertNotIn("primary_exempt", after)
        self.assertNotIn("primary_waivers", after)
        self.assertEqual((root / ".teamwork" / "log.jsonl").read_text(encoding="utf-8"), before_log)

        root, git, state_path, sha = fixture("merge-no-association", subject="ordinary change", verified=False)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["threads"]["worker"]["branch"] = "other"
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        denied = subprocess.run([sys.executable, str(TW), "merge", "x", "--sha", sha, "--no-test", "docs only",
                                 "--apply"], cwd=root, env=self.env, capture_output=True, text=True)
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("no tasks are associated", denied.stderr.lower())
        self.assertEqual(denied.stderr.lower().count("no tasks are associated"), 1)  # printed once, not twice
        waived = subprocess.run([sys.executable, str(TW), "merge", "x", "--sha", sha, "--no-test", "docs only",
                                 "--user-said", "allow unassociated merge once", "--apply"], cwd=root, env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(waived.returncode, 0, waived.stderr)
        task = json.loads(state_path.read_text(encoding="utf-8"))["tasks"]["T1"]
        self.assertNotIn("primary_exempt", task)
        events = [json.loads(line) for line in (root / ".teamwork" / "log.jsonl").read_text(encoding="utf-8").splitlines()]
        merge_event = next(event for event in reversed(events) if event.get("ev") == "merge")
        self.assertEqual(merge_event["primary_waiver"]["kind"], "unassociated-merge")

    def test_minor_suspect_normalization_sha_env_skip_and_labels(self):
        self.assertTrue(tw._primary_suspect(self.dir, "fixture run", "output shows 12 records"))
        self.assertTrue(tw._primary_suspect(self.dir, "dummy data", "output shows 12 records"))
        self.assertTrue(tw._primary_suspect(self.dir, "simulated browser", "output shows 12 records"))
        self.assertTrue(tw._primary_suspect(self.dir, "placeholder screen", "output shows 12 records"))
        self.assertTrue(tw._primary_suspect(self.dir, "样例数据", "显示 12 条记录"))
        self.assertTrue(tw._primary_suspect(self.dir, "假的运行环境", "显示 12 条记录"))
        self.assertFalse(tw._primary_evidence_specific(self.dir, "looks fine 1"))

        self.add("Tno-primary", primary=None)
        self.working("Tno-primary")
        no_primary = self.tw("task", "verify", "Tno-primary", "--seen", "--how", "open artifact",
                             "--evidence", "output shows 12 records", ok=False)
        self.assertIn("--primary", no_primary.stderr)

        self.add()
        self.working()
        self.tw("task", "verify", "T1", "--seen", "--how", "open real artifact", "--evidence",
                "worker output shows 12 records", "--sha", "abcdef0")
        self.tw("task", "set", "T1", "--status", "review")
        denied = self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "reviewer", "--tests", "ran",
                         "--base", "abcdef0", "--recheck-how", "reviewer opened real artifact",
                         "--recheck-evidence", "WORKER-output_shows 12 records", ok=False)
        self.assertIn("own", denied.stderr.lower())
        passed = self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "reviewer", "--tests", "ran",
                         "--base", "1234567", "--reviewed", "abcdef0", "--recheck-how",
                         "reviewer opened independent real artifact", "--recheck-evidence",
                         "reviewer independently sees 12 restored records")
        self.assertIn("verification used abcdef0", passed.stderr.lower())

        self.tw("task", "set", "T1", "--status", "working")
        self.tw("task", "verify", "T1", "--seen", "--how", "open real artifact again", "--evidence",
                "fresh output shows 12 records")
        self.tw("task", "set", "T1", "--status", "review")
        self.tw("task", "set", "T1", "--review", "fail", "--reviewer", "reviewer", "--tests", "not-run",
                "--tests-why", "browser unavailable", "--base", "abcdef0", "--env-down", "browser cannot start")
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "environment is unavailable",
                "--user-said", "accept this environment limitation")
        self.assertEqual(self.state()["tasks"]["T1"]["status"], "done")
        done_verify = self.tw("task", "verify", "T1", "--seen", "--how", "open the real artifact",
                              "--evidence", "output still shows 12 records", ok=False)
        self.assertIn("done task cannot be re-verified", done_verify.stderr)

        self.tw("prefs", "--set", "language=zh")
        labels = self.tw("board").stdout
        self.assertIn("主产出", labels)
        reviews = self.tw("task", "reviews", "-").stdout
        self.assertIn("测试/基线", reviews)

    def test_review_status_and_pass_can_be_recorded_together(self):
        self.add()
        self.working()
        self.tw("task", "verify", "T1", "--seen", "--how", "open real artifact", "--evidence", "output shows 12 records")
        self.tw("task", "set", "T1", "--status", "review", "--review", "pass", "--reviewer", "reviewer",
                "--tests", "ran", "--base", "abcdef0", "--recheck-how", "reviewer opened real artifact",
                "--recheck-evidence", "reviewer confirms 12 restored records")
        self.assertEqual(self.state()["tasks"]["T1"]["status"], "review")
        self.assertEqual(self.state()["tasks"]["T1"]["review"]["result"], "pass")

    def test_recalled_task_redispatch_invalidates_old_verify(self):
        self.add()
        self.working()
        self.verify()
        self.tw("task", "set", "T1", "--status", "review")
        self.tw("task", "set", "T1", "--review", "fail", "--reviewer", "reviewer", "--tests", "ran", "--base", "abcdef0")
        self.tw("plan", "approve", "--user-said", "approve redispatch fixture")
        path = self.dir / ".teamwork" / "state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["tasks"]["T1"]["status"] = "recalled"
        state["tasks"]["T1"]["primary_check"] = {"result": "seen", "evidence": "old run shows 12 records"}
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        self.tw("task", "set", "T1", "--status", "dispatched")
        task = self.state()["tasks"]["T1"]
        self.assertNotIn("primary_check", task)
        self.assertEqual(task["past_primary_checks"][-1]["evidence"], "old run shows 12 records")

class T70PrimaryOutcome(unittest.TestCase):
    def test_work_kind_routing_and_quota_slowdown_on_real_cli(self):
        with tempfile.TemporaryDirectory(prefix="tw-t70-outcome-") as tmp:
            root = Path(tmp); subprocess.run(["git", "init", "-q", str(root)], check=True)
            env = {k: v for k, v in os.environ.items() if k not in ("TEAMWORK_DIR", "CLAUDE_CONFIG_DIR")}
            def run(*args):
                p = subprocess.run([sys.executable, str(TW), "-C", str(root), *args], cwd=root, env=env,
                                   capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stderr)
                return p
            run("init", "demo", "--short", "DM")
            run("lead", "--session", "s1")
            run("task", "add", "T1", "--title", "Chinese documents", "--work-kind", "docs-zh")
            run("task", "add", "T2", "--title", "Code work", "--work-kind", "code")
            run("task", "add", "T3", "--title", "Manual precise code", "--work-kind", "code", "--no-scripts")
            run("plan", "approve", "--user-said", "approved for this CLI demonstration")
            ready = run("ready")
            hints = json.loads(ready.stdout)["route_hints"]
            self.assertEqual([hints[x]["route"] for x in ("T1", "T2", "T3")], ["fable", "gpt", "claude"])
            self.assertEqual(hints["T3"]["why"], "no-scripts")
            quota = run("quota", "--simulate-pct", "70")
            pace_run = run("ready")
            pace = json.loads(pace_run.stdout)["pace"]
            self.assertEqual(pace, {"tier": "burn-fable", "checkin_minutes": 20,
                                    "quota_every_minutes": 120, "slowed": True})
            self.assertTrue(any("额度到 70%" in s or "Quota at 70%" in s for s in
                                (pace_run.stderr, quota.stdout, quota.stderr)))
            self.assertEqual(json.loads(pace_run.stdout)["stuck"], {})
            self.assertEqual(json.loads(pace_run.stdout)["urgent_unacked"], [])
            state = json.loads((root / ".teamwork" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["notices"], [])

    def test_real_cli_primary_outcome_transcript(self):
        with tempfile.TemporaryDirectory(prefix="tw-t70-primary-") as tmp:
            root = Path(tmp)
            env = {k: v for k, v in os.environ.items() if k not in ("TEAMWORK_DIR", "CLAUDE_CONFIG_DIR")}
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            transcript = []
            def run(*args):
                r = subprocess.run([sys.executable, str(TW), "-C", str(root), *args], cwd=root, env=env,
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                transcript.append("$ tw.py " + " ".join(args) + "\n" + r.stdout.rstrip())
                return r.stdout
            def run_result(*args):
                r = subprocess.run([sys.executable, str(TW), "-C", str(root), *args], cwd=root, env=env,
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                transcript.append("$ tw.py " + " ".join(args) + "\n" + r.stdout.rstrip())
                return r
            run("init", "demo", "--short", "DM", "--goal", "demo app")
            run("lead", "--session", "s1")
            run("prefs", "--set", "language=zh", "standing_grants=schedule", "--user-said", "schedule this work")
            self.assertIn("实测通过", run("selfcheck"))
            run("board")
            for tid, title, kind, extra in (("T1", "Chinese documents", "docs-zh", []),
                                            ("T2", "Code work", "code", []),
                                            ("T3", "Manual code", "code", ["--no-scripts"])):
                run("task", "add", tid, "--title", title, "--work-kind", kind, *extra)
            run("plan", "approve", "--user-said", "approved for the CLI demonstration")
            ready = json.loads(run("ready"))
            self.assertEqual([ready["route_hints"][tid]["route"] for tid in ("T1", "T2", "T3")],
                             ["fable", "gpt", "claude"])
            run("quota", "--simulate-pct", "72")
            pace = json.loads(run("ready"))["pace"]
            self.assertEqual(pace["tier"], "burn-fable")
            run("thread", "add", "docs", "--session", "session-docs")
            sessions = root / "sessions.json"
            sessions.write_text(json.dumps({"data": [{"id": "session-docs", "title": "[DM] docs · result",
                                                       "status": "completed", "status_bucket": "review_ready",
                                                       "updated_at": "2026-11-24T19:00:00Z",
                                                       "tags": ["teamwork:DM", "teamwork-thread:docs"]}],
                                            "has_more": False}), encoding="utf-8")
            events = root / "events.json"
            events.write_text(json.dumps({"data": [
                {"id": "assistant-1", "type": "assistant", "created_at": "2026-11-24T18:59:00Z",
                 "data": {"assistant": {"text": "DONE: docs delivered"}}},
                {"id": "result-1", "type": "result", "created_at": "2026-11-24T19:00:00Z",
                 "data": {"result": {"subtype": "success"}}}]}), encoding="utf-8")
            run("notice", "sweep", "--sessions", str(sessions), "--events", str(events), "--session", "session-docs")
            run("sprint", "start", "--until", "23:59", "--goal", "ship the demo")
            run("sprint", "beta", "--version", "v1", "--try", "sign in")
            run("sprint", "stop-all")
            run("prefs", "--grant-use", "schedule", "--what", "schedule the next review")
            card = run("card", "--kind", "done", "--head", "交付结果")
            self.assertIn("长期授权：排期 1 次", card.splitlines()[-1])
            (root / "PRIMARY-TRANSCRIPT.txt").write_text("\n\n".join(transcript) + "\n", encoding="utf-8")

class T70Acceptance(TwCase):
    def setUp(self):
        super().setUp()
        fixture=self.dir/"sweep-health.json"
        fixture.write_text(json.dumps({"load":[0,0,0],"cpu_count":8,"process_count":0,"pipe_bytes":65536,
                                       "simulator_count":0,"disk_free":50*1024**3,"disk_total":100*1024**3,
                                       "disk_sizes":{},"processes":[]}),encoding="utf-8")
        self.env["TEAMWORK_HEALTH_FIXTURE"]=str(fixture)

    def _changed_result(self):
        self.tw("thread", "add", "ui")
        self.tw("plan", "approve", "--user-said", "approved")
        self.tw("task", "add", "T1", "--title", "fix result", "--owner", "ui", "--accept", "fix is reviewed")
        self.tw("task", "set", "T1", "--status", "review")
        self.tw("req", "add", "fix the result", "--id", "R1", "--tasks", "T1")
        for status in ("reported", "waiting_user", "open", "reported"):
            self.tw("req", "set", "R1", "--status", status)
        self.tw("task", "set", "T1", "--status", "working")

    def test_changed_result_is_not_confirmable_nor_done_on_the_card(self):
        self._changed_result()
        bad = self.tw("req", "confirm", "R1", "--user-said", "confirmed", ok=False)
        self.assertIn("R1", bad.stderr)
        self.assertEqual(self.state()["requests"]["R1"]["status"], "reported")
        self.assertNotIn("R1", self.tw("req", "confirm", "--all", "--user-said", "confirmed", ok=False).stdout)
        card = self.tw("card", "--kind", "done", "--head", "review", "--all").stdout
        done = card.split("## Done", 1)[1].split("##", 1)[0] if "## Done" in card else ""
        self.assertNotIn("R1", done)

    def test_pace_change_under_handover_lock_notices_without_failing(self):
        self.tw("quota", "--simulate-pct", "75")
        st = self.state(); st.pop("pace_notice_tier", None); st["write_lock"] = {"ho": "HO1"}
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        before = (self.dir / ".teamwork" / "state.json").read_text(encoding="utf-8")
        r = self.tw("ready")
        self.assertIn("75%", r.stderr)
        json.loads(r.stdout)
        self.assertEqual((self.dir / ".teamwork" / "state.json").read_text(encoding="utf-8"), before)

    def test_release_compare_reads_real_ledger_times_and_skips_simulations(self):
        st=self.state(); st["prefs"]["language"]="zh"
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st),encoding="utf-8")
        rows=[
            {"t":"2026-11-09T10:00:00-05:00","ev":"quota-usage","used_pct":25},
            {"t":"2026-11-09T11:00:00-05:00","ev":"quota-usage","used_pct":40},
            {"t":"2026-11-09T12:00:00-05:00","ev":"quota-usage","used_pct":99,"simulated":True},
            {"t":"2026-11-09T13:00:00-05:00","ev":"notice-add","kind":"needs-user"},
            {"t":"2026-11-09T14:00:00-05:00","ev":"notice-add","kind":"turn-end"},
            {"t":"2026-11-10T10:00:00-05:00","ev":"quota-usage","used_pct":80},
            {"t":"2026-11-10T11:00:00-05:00","ev":"sweep-notice","kind":"approval"},
            {"t":"2026-11-10T12:00:00-05:00","ev":"other","snap_waiting_for":"permission"},
            {"t":"2026-11-10T13:00:00-05:00","ev":"quota-usage","used_pct":100,"simulated":True},
        ]
        (self.dir/".teamwork"/"log.jsonl").write_text("".join(json.dumps(x)+"\n" for x in rows),encoding="utf-8")
        result=subprocess.run([sys.executable,str(RELEASE),"compare","--since","2026-11-10","--days","1","--projects",str(self.dir)],cwd=self.dir,env=self.env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("之前 40.0；之后 80.0",result.stdout)
        self.assertIn("每日权限提示：之前 1.00；之后 1.00",result.stdout)

    def test_model_log_accepts_token_and_minute_usage(self):
        self.tw("task","add","T1","--title","Write a paragraph")
        self.tw("model-log","--task","T1","--type","docs-zh","--model","fable","--result","ok","--tokens","345","--minutes","3")
        row=json.loads((self.dir/".teamwork"/"model-log.jsonl").read_text(encoding="utf-8"))
        self.assertEqual((row["tokens"],row["minutes"]),(345,3.0))

    def test_weekly_model_comparison_falls_back_to_same_kind_rework_rate(self):
        st=self.state(); st["prefs"]["language"]="zh"
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st),encoding="utf-8")
        day=dt.date.today().isoformat(); rows=[]
        for kind in ("docs-zh", "docs-en", "code"):
            for i in range(3):
                rows.extend([{"model":"fable","work_kind":kind,"result":"redo" if i==0 else "ok","at":day+"T10:00:00+00:00"},
                             {"model":"opus","work_kind":kind,"result":"redo" if i<2 else "ok","at":day+"T10:01:00+00:00"}])
        (self.dir/".teamwork"/"model-log.jsonl").write_text("".join(json.dumps(x)+"\n" for x in rows),encoding="utf-8")
        out=self.tw("quota","--weekly").stdout
        self.assertIn("返工率",out); self.assertIn("Fable 更省",out)

    def test_session_start_preserves_ledger_save_time(self):
        st=self.state(); st["updated"]="2026-11-01T12:34:00+00:00"
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st),encoding="utf-8")
        output=self.context()
        after=self.state()
        self.assertEqual(after["updated"],"2026-11-01T12:34:00+00:00")
        self.assertIn("As of 12:34 (2026-11-01)",output)

    def test_sprint_deadline_and_ready_json_streams(self):
        self.env["TW_NOW"]="2026-11-24T20:00:00-05:00"
        self.tw("sprint","start","--until","19:00")
        self.assertTrue(self.state()["sprint"]["until"].startswith("2026-11-25T19:00"))
        bad=self.tw("sprint","start",ok=False)
        self.assertIn("--until needs HH:MM",bad.stderr)
        st=self.state(); st["sprint"]["until"]="2026-11-24T19:00:00-05:00"
        st["sprint"]["base"]="HEAD"
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st),encoding="utf-8")
        ready=self.tw("ready")
        self.assertIn("Sprint ended",ready.stderr)
        self.assertIsInstance(json.loads(ready.stdout),dict)

    def test_beta_tracks_landed_features_added_at_same_timestamp(self):
        self.env["TW_NOW"]="2026-11-24T12:00:00-05:00"
        empty=self.tw("sprint","beta","--version","v1")
        self.assertIn("No new landed features",empty.stdout)
        st=self.state(); st["features"]["F1"]={"name":"Same-second feature","phase":"MVP","status":"landed","landed_at":"2026-11-24T12:00:00-05:00"}
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st),encoding="utf-8")
        next_beta=self.tw("sprint","beta","--version","v2")
        self.assertIn("Same-second feature",next_beta.stdout)

    def test_sweep_real_list_sessions_and_list_events_shapes(self):
        isolated_home=self.dir/"isolated-home"; isolated_home.mkdir(); self.env["HOME"]=str(isolated_home)
        self.tw("lead", "--session", "lead-demo")
        self.tw("thread", "add", "docs", "--session", "session-docs")
        row={"id":"session-docs","title":"[DM] docs · report","status":"idle","status_bucket":"blocked",
             "updated_at":"2026-11-24T19:00:00Z","tags":["teamwork:DM","teamwork-thread:docs"],
             "post_turn_summary":{"needs_action":"approval required"},"connection_status":"connected",
             "configured_model":"fable","session_context":{"model":"fable"},
             "external_metadata":{"last_served_model":"gpt"}}
        sessions=self.dir/"real-list-sessions.json"
        sessions.write_text(json.dumps({"data":[row],"has_more":False,"last_id":"session-docs"}),encoding="utf-8")
        events=self.dir/"real-list-events.json"
        events.write_text(json.dumps({"data":[{"id":"evt-1","type":"control_request","data":{"control_request":{"request":{"subtype":"can_use_tool","tool_name":"mcp__claude-code-remote__send_message"}}},"created_at":"2026-11-24T19:00:01Z"}]}),encoding="utf-8")
        self.tw("notice","sweep","--sessions",str(sessions),"--events",str(events),"--session","session-docs")
        state=self.state(); kinds={x["kind"] for x in state["notices"]}
        self.assertTrue({"needs-user","model-fallback"}.issubset(kinds))
        self.assertNotIn("turn-end",kinds)  # a blocked row cannot also claim the turn ended
        self.assertEqual(state["threads"]["docs"]["waiting_for"],"permission")
        # The real API also returns bare lists and one get_session object.
        sessions.write_text(json.dumps([row]),encoding="utf-8"); self.tw("notice","sweep","--sessions",str(sessions))
        sessions.write_text(json.dumps(row),encoding="utf-8"); self.tw("notice","sweep","--sessions",str(sessions))
        state=self.state(); last=state.get("last_sweep_at")
        sessions.write_text(json.dumps({"data":[dict(row,id="unmatched-session",thread="docs")],"has_more":False}),encoding="utf-8")
        out=self.tw("notice","sweep","--sessions",str(sessions)).stdout
        self.assertIn("sweep matched 0 of 1 sessions",out)
        self.assertEqual(self.state().get("last_sweep_at"),last)

    def test_lead_probe_and_opened_by_alone_persist(self):
        self.tw("lead","--spawn-probe","ok")
        self.tw("lead","--opened-by","desktop")
        self.tw("lead","--desktop")
        self.assertTrue(self.state()["lead"]["desktop"])
        check=json.loads(self.tw("selfcheck","--json").stdout)
        items={x["item"]:x for x in check["items"]}
        self.assertEqual(items["spawn"]["how"],"tested")
        self.assertEqual(items["spawn"]["ok"],True)
        self.assertEqual(items["opened_by"]["detail"],"this lead was opened by another session: the user must still pick Auto on a trusted device")
        self.assertIn("on the desktop",check["first_line"])

    def test_sweep_detects_permission_fallback_interruption_and_deliverable(self):
        self.tw("lead", "--session", "lead-session")
        st=self.state()
        st["threads"]["worker"]={"kind":"rc","status":"working","session_id":"worker-session","title":"worker"}
        st["tasks"]["T1"]={"title":"Build output","owner":"worker","status":"working","dispatch_v":2}
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st),encoding="utf-8")
        sessions=self.dir/"sessions.json"
        sessions.write_text(json.dumps({"data":[{"thread":"worker","session_id":"worker-session","status":"completed","status_bucket":"review_ready","updated_at":"2026-11-24T12:00:00-05:00","configured_model":"fable","external_metadata":{"last_served_model":"gpt"}}],"has_more":False}),encoding="utf-8")
        events=self.dir/"events.json"
        events.write_text(json.dumps({"data":[{"id":"evt-assistant","type":"assistant","created_at":"2026-11-24T11:59:00-05:00","data":{"assistant":{"text":"DONE deliverable: https://example.invalid/output"}}},{"id":"evt-result","type":"result","created_at":"2026-11-24T12:00:00-05:00","data":{"result":{"subtype":"success"}}}]}),encoding="utf-8")
        self.tw("notice","sweep","--sessions",str(sessions),"--events",str(events),"--session","worker-session")
        st=self.state()
        self.assertEqual(st["threads"]["worker"]["last_seen_at"],"2026-11-24T12:00:00-05:00")
        kinds={x["kind"] for x in st["notices"]}
        self.assertIn("model-fallback",kinds); self.assertIn("turn-end",kinds)
        self.assertEqual(st["deliverables"][0]["source"],"auto (message not delivered)")
        events.write_text(json.dumps({"data":[{"id":"evt-error","type":"result","created_at":"2026-11-24T12:01:00-05:00","data":{"result":{"subtype":"error_interrupted"}}}]}),encoding="utf-8")
        sessions.write_text(json.dumps({"data":[{"thread":"worker","session_id":"worker-session","status":"review_ready","status_bucket":"review_ready","updated_at":"2026-11-24T12:01:00-05:00"}],"has_more":False}),encoding="utf-8")
        self.tw("notice","sweep","--sessions",str(sessions),"--events",str(events),"--session","worker-session")
        self.assertIn("interrupted",{x["kind"] for x in self.state()["notices"]})

    def test_sweep_scans_all_events_by_turn_and_finds_text_before_result(self):
        self.tw("thread", "add", "worker", "--session", "sid-old")
        self.tw("thread", "add", "titleworker", "--session", "title-old")
        st=self.state(); st["prefs"]["short_name"]="DM"; st["tasks"]["T1"]={"title":"Build output","owner":"worker","status":"working","dispatch_v":2}
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st),encoding="utf-8")
        session_file=self.dir/"sessions.json"
        session_file.write_text(json.dumps({"data":[{"id":"sid-new","title":"Worker report","tags":["teamwork:DM","teamwork-thread:worker"],"status":"idle","status_bucket":"review_ready","updated_at":"2026-11-24T12:00:00Z"},
            {"id":"title-new","title":"[DM] titleworker · preserve this phrase","tags":[],"status":"running"}]}),encoding="utf-8")
        event_file=self.dir/"events.json"
        transcript={"data":[
            {"id":"u1","type":"user","created_at":"2026-11-24T11:59:00Z","message":"continue"},
            {"id":"a1","type":"assistant","created_at":"2026-11-24T11:59:10Z","data":{"assistant":{"text":"The deliverable is https://example.invalid/from-turn-one"}}},
            {"id":"r1","type":"result","created_at":"2026-11-24T11:59:20Z","data":{"result":{"subtype":"success"}}},
            {"id":"u2","type":"user","created_at":"2026-11-24T11:59:30Z","message":"continue"},
            {"id":"a2","type":"assistant","created_at":"2026-11-24T11:59:40Z","data":{"assistant":{"text":"DONE T1"}}},
            {"id":"r2","type":"result","created_at":"2026-11-24T11:59:50Z","data":{"result":{"subtype":"error_tool"}}},
        ]}
        event_file.write_text(json.dumps(transcript),encoding="utf-8")
        out=self.tw("notice","sweep","--sessions",str(session_file),"--events",str(event_file),"--session","sid-new").stdout
        state=self.state(); kinds=[x["kind"] for x in state["notices"]]
        self.assertGreaterEqual(kinds.count("turn-end"),1,(state["notices"],out))
        self.assertIn("interrupted",kinds); self.assertIn("deliverable",kinds)
        self.assertEqual(state["deliverables"][0]["link"],"https://example.invalid/from-turn-one")
        self.assertEqual(state["threads"]["worker"]["sessions"][-1]["session_id"],"sid-new")
        update=json.loads(out.splitlines()[-1])["sessions_to_update"][0]
        self.assertEqual(update["title"],"[DM] worker · Worker report")
        title_update=next(x for x in json.loads(out.splitlines()[-1])["sessions_to_update"] if x["session_id"]=="title-new")
        self.assertEqual(title_update["title"],"[DM] titleworker · preserve this phrase")
        self.assertEqual(self.state()["threads"]["titleworker"]["sessions"][-1]["session_id"],"title-new")

    def test_sweep_events_only_records_approval_then_unblocks_thread(self):
        self.tw("thread", "add", "worker", "--session", "sid-worker")
        event_file=self.dir/"events.json"
        approval={"id":"ask1","type":"control_request","created_at":"2026-11-24T12:00:00Z",
                  "data":{"control_request":{"request":{"subtype":"can_use_tool","tool_name":"Bash"}}}}
        event_file.write_text(json.dumps({"data":[approval]}),encoding="utf-8")
        first=self.tw("notice","sweep","--events",str(event_file),"--session","sid-worker").stdout
        state=self.state(); self.assertEqual(state["threads"]["worker"]["waiting_for"],"permission")
        self.assertNotIn("turn-end",[x["kind"] for x in state["notices"]])
        logged=[json.loads(line) for line in (self.dir/".teamwork"/"log.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(x.get("ev")=="sweep-notice" and x.get("kind")=="approval" for x in logged))
        event_file.write_text(json.dumps({"data":[approval,
            {"id":"ans1","type":"control_response","created_at":"2026-11-24T12:01:00Z"},
            {"id":"result1","type":"result","created_at":"2026-11-24T12:02:00Z","data":{"result":{"subtype":"success"}}}]}),encoding="utf-8")
        self.tw("notice","sweep","--events",str(event_file),"--session","sid-worker")
        state=self.state(); self.assertEqual(state["threads"]["worker"]["status"],"working")
        self.assertNotIn("waiting_for",state["threads"]["worker"])
        self.assertTrue(next(x for x in state["notices"] if x["kind"]=="needs-user")["done"])

    def test_sweep_unmatched_events_only_does_not_advance_poll_time(self):
        before=self.state().get("last_sweep_at")
        event_file=self.dir/"events.json"; event_file.write_text(json.dumps({"data":[]}),encoding="utf-8")
        self.tw("notice","sweep","--events",str(event_file),"--session","stranger")
        self.assertEqual(self.state().get("last_sweep_at"),before)

    def test_cli_edge_cases_zh_filter_leads_and_docs(self):
        self.env["TW_NOW"]="2026-11-24T20:00:00-05:00"
        bad=self.tw("sprint","start","--until","25:99",ok=False)
        self.assertNotIn("Traceback",bad.stderr); self.assertIn("--until",bad.stderr)
        st=self.state(); st["requests"]={"R1":{"status":"reported","text":"reported"},"R2":{"status":"open","text":"open"}}
        st["prefs"]["language"]="zh"
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st),encoding="utf-8")
        listed=json.loads(self.tw("req","list","--status","reported").stdout)
        self.assertEqual(list(listed),["R1"])
        quota=self.tw("quota","--simulate-pct","50").stdout
        self.assertIn("额度模拟",quota)
        self.assertTrue(self.state()["quota"]["simulated"])
        quota_log=[json.loads(line) for line in (self.dir/".teamwork"/"log.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(next(x for x in quota_log if x.get("ev")=="quota-usage")["simulated"])
        stop=self.tw("sprint","stop-all").stdout
        self.assertIn("未记录",stop); self.assertIn("请手动停止本项目的 Codex 运行",stop)
        self.tw("selfcheck","--mode","default")
        check=self.tw("selfcheck","--mode","default").stdout
        self.assertIn("未通过 模式",check); self.assertIn("；怎么弄：",check)
        self.assertIn("实测通过 台账: state.json 和 BOARD.md",check)
        prompt=(self.dir/".teamwork"/"log.jsonl").read_text(encoding="utf-8")
        self.assertIn('"simulated": true',prompt)
        doc=REPO/"templates"/"worker-protocol.md"
        protocol=doc.read_text(encoding="utf-8")
        self.assertIn("Do not run `TW ask`",protocol)
        self.assertIn("TW ask --from <thread> --default",protocol)
        skill=(REPO/"SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("**Lead command map.**",skill)
        self.assertIn("--ledger-line` forces it",skill)
        self.assertIn("TW deliverable add/set",skill)

    def test_version_leads_skips_nonobject_and_unreadable_state(self):
        root=self.dir/"projects"; root.mkdir()
        for name, content in (("array", "[]"), ("bad", "{")):
            project=root/name/".teamwork"; project.mkdir(parents=True)
            (project/"state.json").write_text(content,encoding="utf-8")
        result=subprocess.run([sys.executable,str(TW),"-C",str(self.dir),"version","--leads","--root",str(root)],
                              cwd=self.dir,env=self.env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn("Traceback",result.stderr)

    def test_thread_defaults_sprint_beta_confirmation_and_model_mismatch(self):
        st=self.state(); old="2020-01-01T00:00:00+00:00"
        st["questions"]=[{"from":"worker","text":"Which internal format?","t":old,"answered":False,"default_action":"prepare the reversible draft","default_after_minutes":5}]
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st),encoding="utf-8")
        self.tw("ready")
        self.assertEqual(self.state()["questions"][0]["defaulted"]["by"],"thread")
        self.tw("sprint","start","--until","23:59","--goal","ship a stable build")
        self.assertIn("Sprint in progress",self.tw("board").stdout)
        self.tw("sprint","beta","--version","v1","--try","sign in")
        self.tw("sprint","feedback","--text","Button label is unclear")
        self.assertEqual(self.state()["betas"][0]["status"],"waiting-feedback")
        self.tw("sprint","stop-all"); self.assertTrue(self.state()["stopped_at"])
        self.tw("req","add","Confirm this report")
        self.tw("req","set","R1","--status","reported")
        self.assertNotEqual(self.tw("req","confirm","R1",ok=False).returncode,0)
        self.tw("req","confirm","R1","--user-said","I confirm this report")
        self.assertEqual(self.state()["requests"]["R1"]["confirmed_by"],"I confirm this report")
        check=json.loads(self.tw("selfcheck","--configured-model","opus","--last-served-model","fable","--json").stdout)
        self.assertFalse(next(x for x in check["items"] if x["item"]=="model")["ok"])

    def test_quota_weekly_only_compares_matching_work_kind_pairs(self):
        self.tw("prefs","--set","language=zh")
        monday=(dt.date.today()-dt.timedelta(days=dt.date.today().weekday())).isoformat()
        rows=[]
        for i in range(3):
            rows.extend([{"model":"fable","work_kind":"docs-zh","tokens":100+i,"result":"ok","at":monday+"T10:00:00+00:00"},
                         {"model":"opus","type":"docs-zh","tokens":200+i,"result":"ok","at":monday+"T10:01:00+00:00"}])
        rows.extend([{"model":"fable","type":"code","tokens":1,"result":"ok","at":monday+"T10:02:00+00:00"}])
        (self.dir/".teamwork"/"model-log.jsonl").write_text("\n".join(json.dumps(x) for x in rows)+"\n")
        out=self.tw("quota","--weekly").stdout
        self.assertIn("数据不足",out)

class RoutingAndChecks(TwCase):
    """T57 block B: card detail, route/quota hints, decision lookup, package checks and sensitive guard."""

    def test_card_gpt_detail_and_chinese_empty_line(self):
        self.tw("task", "add", "T1", "--title", "review package")
        for typ in ("code", "test", "read", "review1", "review2", "research"):
            self.tw("model-log", "--task", "T1", "--type", typ, "--model", "gpt", "--result", "ok")
        self.tw("prefs", "--set", "language=zh")
        out = self.tw("card", "--kind", "done", "--head", "完成").stdout
        self.assertIn("GPT 做了 6（T1 code、T1 test、T1 read、T1 review1、T1 review2等 1 项）", out)
        self.tw("model-log", "--task", "T1", "--type", "code", "--model", "opus", "--result", "ok")
        self.assertIn("GPT 做了 0", self.tw("card", "--kind", "done", "--head", "完成", "--since",
                                            "2030-01-01T00:00:00+00:00").stdout)

    def test_quota_due_route_hints_and_rebuild(self):
        self.env["TW_NOW"] = "2026-06-01T10:00:00-04:00"
        self.tw("task", "add", "T1", "--title", "package build")
        self.tw("plan", "approve", "--user-said", "approved")
        self.tw("thread", "add", "busy", "--session", "s-busy", "--status", "working")  # R65: an idle team is never due
        self.tw("quota", "--config-dir", str(self.dir / "empty-config"), "--codex-dir", str(self.dir / "empty-codex"), "--json")
        due = json.loads(self.tw("quota", "--due", "--json").stdout)
        self.assertTrue(due["due"])
        self.env["TW_NOW"] = "2026-06-01T11:01:00-04:00"
        self.assertTrue(json.loads(self.tw("quota", "--due", "--json").stdout)["due"])
        st = self.state(); st["quota"] = {"tier": "save", "at": self.env["TW_NOW"]}
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        hints = json.loads(self.tw("ready").stdout)["route_hints"]
        self.assertEqual(hints["T1"], {"route": "gpt", "why": "tier"})
        self.tw("rebuild")
        self.assertIsNone(self.state().get("last_quota_at"))

    def test_merge_grant_counts_apply_only_and_rebuilds(self):
        def git(*args):
            return subprocess.run(["git", "-C", str(self.dir), *args], check=True, capture_output=True, text=True)
        git("init", "-q", "-b", "main")
        git("config", "user.name", "test")
        git("config", "user.email", "test@example.invalid")
        (self.dir / "base.txt").write_text("base\n")
        git("add", "base.txt"); git("commit", "-q", "-m", "base")
        self.tw("task", "add", "T1", "--title", "result fixture")
        branch = "tw/demo/ui"
        git("checkout", "-q", "-b", branch)
        (self.dir / "result.txt").write_text("result\n")
        git("add", "result.txt"); git("commit", "-q", "-m", "T1: result")
        sha = git("rev-parse", "HEAD").stdout.strip()
        git("checkout", "-q", "main")
        self.tw("prefs", "--set", "standing_grants=merge", "--user-said", "允许合并")
        rehearsal = json.loads(self.tw("merge", branch, "--sha", sha, "--no-test", "docs only").stdout)
        self.assertFalse(rehearsal["applied"])
        self.assertEqual(self.state().get("grant_uses", []), [])
        applied = json.loads(self.tw("merge", branch, "--sha", sha, "--no-test", "docs only", "--apply").stdout)
        self.assertTrue(applied["applied"])
        self.assertEqual(self.state()["grant_uses"][0]["branch"], branch)
        self.tw("prefs", "--set", "language=zh")
        self.tw("rebuild")
        self.assertEqual(len(self.state()["grant_uses"]), 1)
        self.assertIn("长期授权：合并 1 次", self.tw("card", "--kind", "done", "--head", "完成").stdout)

    def test_decided_warns_and_returns_chinese_hits(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("ask", "选择蓝色按钮")
        self.tw("ask", "--answer", "1", "蓝色按钮采用")
        result = json.loads(self.tw("decided", "蓝色按钮", "--json").stdout)
        self.assertTrue(any("answered question" in h["source"] for h in result["hits"]))
        warned = self.tw("ask", "蓝色按钮颜色选择", ok=True)
        self.assertIn("这件可能已经定过", warned.stderr)
        self.assertIn("先看 decided", warned.stderr)

    def test_deliverable_and_package_check(self):
        self.tw("prefs", "--set", "language=zh")
        missing_link = self.tw("deliverable", "add", "安装包", ok=False)
        self.assertIn("交付物要给能打开的链接", missing_link.stderr)
        self.tw("deliverable", "add", "安装包", "--link", "https://example.invalid/app")
        package = self.dir / "package"; package.mkdir()
        (package / "app.bin").write_text("app")
        (package / "README.txt").write_text("docs")
        absent = self.tw("package-check", str(package), "--expect", "app.bin", "--expect", "missing.txt", ok=False)
        self.assertEqual(absent.returncode, 1)
        self.assertIn("missing.txt", absent.stdout)
        self.tw("package-check", str(package), "--expect", "app.bin", "--expect", "README.txt", "--deliverable", "D1")
        self.tw("rebuild")
        self.assertEqual(self.state()["deliverables"][0]["checked_manifest"], ["app.bin", "README.txt"])
        opaque = package / "release.dmg"; opaque.write_bytes(b"dmg")
        unsupported = self.tw("package-check", str(opaque), "--expect", "app.bin", ok=False)
        self.assertEqual(unsupported.returncode, 2)
        self.assertIn("这种包读不了内容，要人工打开核对", unsupported.stderr)

    def test_sensitive_tier_only_adds_review_prompts(self):
        def judge(command):
            event = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
            return subprocess.run([sys.executable, str(TW), "bash-guard"], cwd=self.dir, env=self.env,
                                  input=event, capture_output=True, text=True)
        self.assertEqual(judge("git push origin feature").stdout, "")
        self.tw("prefs", "--set", "language=zh", "perm_policy=managed-sensitive", "--user-said", "请审查敏感命令")
        answer = json.loads(judge("git push origin feature").stdout)
        self.assertEqual(answer["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertIn("敏感档：这类命令你说过要看一眼",
                      answer["hookSpecificOutput"]["permissionDecisionReason"])
        commands = ("npm publish", "pnpm publish", "yarn publish", "pip upload", "twine upload", "docker push image",
                    "curl -X POST https://example.invalid", "wget --post-data=x https://example.invalid",
                    "scp file host:/tmp/file", "rsync file host:/tmp/file", "chmod -R 755 dir",
                    "chown -R user dir", "kubectl apply -f x.yaml", "kubectl delete pod x",
                    "terraform apply", "terraform destroy")
        for command in commands:
            with self.subTest(command=command):
                result = judge(command)
                self.assertTrue(result.stdout, f"{command}: {result.stderr}")
                self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "ask")

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


class T82SpaceManagement(TwCase):
    def setUp(self):
        super().setUp()
        self.home = self.dir / "fake-home"
        self.home.mkdir()
        self.env["HOME"] = str(self.home)
        self.env["CLAUDE_CONFIG_DIR"] = str(self.home / ".claude")
        self.env.pop("TEAMWORK_DIR", None)
        env_patcher = mock.patch.dict(os.environ, {"HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.home / ".claude")})
        env_patcher.start(); self.addCleanup(env_patcher.stop)
        self.project = self.home / "work" / "sample-project"
        (self.project / ".teamwork" / "private" / "thread-sky" / "tmp").mkdir(parents=True)
        (self.project / ".teamwork" / "state.json").write_text(json.dumps({"project":"sample-project","threads":{},"tasks":{}}))
        state = self.state()
        state["prefs"].update(disk_private_tmp_gb=5, disk_growth_gb_per_hour=2, disk_scratch_days=7,
                               disk_keep_snapshots=3, disk_low_pct=10, disk_low_gb=30)
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(state), encoding="utf-8")

    def test_private_tmp_size_and_growth_alarm_names_thread(self):
        import tw
        tmp = self.project / ".teamwork" / "private" / "thread-sky" / "tmp"
        huge = tmp / "nested-copy"
        huge.mkdir()
        (huge / "sparse.bin").open("wb").truncate(5 * 1024**3)
        state = self.state()
        stamp = (dt.datetime.fromisoformat(tw.now()) - dt.timedelta(hours=1)).isoformat()
        state["disk_space"] = {"at": stamp, "sizes": {str(tmp): 0}, "samples":[{"at":stamp,"sizes":{str(tmp):0}}]}
        state["prefs"]["language"] = "zh"
        state["prefs"].update(disk_private_tmp_gb=4, disk_growth_gb_per_hour=2)
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        def completed(paths, cache, budget_s, seed_paths=()):
            cache[str(tmp)]={"bytes":5*1024**3,"complete":True,"measured_at":tw.now(),"updated_at":tw.now(),"device":tmp.stat().st_dev,"stack":[]}
        state["disk_space"]["measure_cache"]={}
        with mock.patch.object(tw,"_disk_refresh",side_effect=completed), \
             mock.patch.object(tw,"_disk_rank",return_value=([],False)):
            report = tw._disk_scan(self.dir / ".teamwork", state)
        self.assertTrue(any("thread-sky" in x and "紧急" in x for x in report["alarms"]))
        self.assertTrue(any("thread-sky" in p and n >= 5 * 1024**3 for n, p in report["growth"]))

    def test_auto_cleanup_preserves_current_snapshot_and_evidence(self):
        import tw
        cache = self.home / ".npm" / "_cacache"
        cache.mkdir(parents=True); (cache / "cache-data").write_bytes(b"cache")
        (self.home / "tmp").mkdir()
        proof = self.project / ".teamwork" / "private" / "thread-sky" / "tmp" / "evidence.txt"
        proof.write_text("ledger evidence"); os.utime(proof,(time.time()-10*86400,time.time()-10*86400))
        state = self.state(); state["evidence"] = str(proof)
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        config = self.home / ".claude"; base = config / "teamwork-releases"; base.mkdir(parents=True)
        names = [f"r2026010{i}-abcdef{i}" for i in range(1, 6)]
        for name in names:
            (base / name).mkdir(); (base / name / "SKILL.md").write_text("x")
        (base / "index.json").write_text(json.dumps({"releases":[{"name":n,"at":n} for n in names],
            "current":names[0],"previous":names[1],"history":[names[0],names[1]]}), encoding="utf-8")
        report = {"pct":0,"free":0,"projects":[str(self.project)]}
        with mock.patch.object(tw,"_disk_in_use",return_value=False), mock.patch.object(tw,"_disk_open_paths",return_value=set()), mock.patch.object(tw.tempfile,"tempdir",str(self.home / "tmp")):
            rows = tw._disk_candidates(self.dir / ".teamwork", report)
            self.assertIn(str(cache), [r["path"] for r in rows])
            self.assertNotIn(str(proof), [r["path"] for r in rows])
            self.assertNotIn(str(base / names[0]), [r["path"] for r in rows])
            before = {r["path"] for r in rows}
            event = tw._disk_apply(self.dir / ".teamwork", state, rows, [self.home, self.dir, self.home / "tmp"])
        self.assertFalse(cache.exists())
        self.assertTrue(proof.exists())
        self.assertTrue((base / names[0]).exists())
        self.assertGreaterEqual(sum(1 for n in names if (base / n).exists()), 3)
        logged = json.loads((self.dir / ".teamwork" / "log.jsonl").read_text().splitlines()[-1])
        self.assertEqual((event["before_free"], event["after_free"]), (logged["before_free"], logged["after_free"]))
        self.assertEqual(set(event["items"]), before)

    def test_pending_preview_fingerprint_rejects_changed_plan_and_zh_sweep(self):
        import tw
        backup = self.home / "Backups"; backup.mkdir(); (backup / "old.zip").write_bytes(b"backup")
        state = self.state(); state["prefs"]["language"] = "zh"
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        with mock.patch.object(tw,"_disk_in_use",return_value=False):
            pending = tw._disk_candidates(self.dir / ".teamwork", {}, pending=True)
        self.assertIn(str(backup), [x["path"] for x in pending])
        self.assertTrue(backup.exists())
        state["disk_space"] = {"pending_plan":{"fingerprint":tw._disk_fingerprint(pending),"items":pending}}
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        from types import SimpleNamespace
        args = SimpleNamespace(space_apply=True, space_fingerprint=state["disk_space"]["pending_plan"]["fingerprint"], apply=True)
        (backup / "old.zip").write_bytes(b"backup changed")
        with mock.patch.object(tw,"_disk_in_use",return_value=False):
            with self.assertRaisesRegex(ValueError,"清单内容已变化"):
                tw._disk_command(args,self.dir / ".teamwork",state)
        self.assertTrue(backup.exists())
        out = self.tw("notice","sweep").stdout
        self.assertIn("磁盘和内存巡检", out)
        self.assertIn("待批清理清单", out)

    def test_pending_preview_skips_projects_with_unmerged_worktrees(self):
        import tw
        with mock.patch.object(tw,"_disk_projects",return_value=[self.project]), \
             mock.patch.object(tw,"_disk_has_unmerged_worktree",return_value=True) as has_unmerged:
            rows=tw._disk_candidates(self.dir/".teamwork",{"projects":[str(self.project)],"health":{"simulator_devices":[]},"measure_cache":{}},pending=True,deadline=time.monotonic()+3,measure=False)
        self.assertNotIn(str(self.project/".teamwork/private/thread-sky/tmp"),[row["path"] for row in rows])
        has_unmerged.assert_called_once_with(self.project,mock.ANY)

    def test_apply_skips_open_descendant_and_rejects_lsof_failure(self):
        import tw
        target=self.home/"Backups"; target.mkdir(); child=target/"held.bin"; child.write_text("held")
        rows=[{"path":str(target),"category":"backup","bytes":10,"eligible":True}]
        fixture=self.dir/"open-paths.json"; fixture.write_text(json.dumps({"in_use":[str(child)]}),encoding="utf-8")
        state=self.state(); state["prefs"]["language"]="zh"
        usage=type("Usage",(),{"free":100,"total":200})()
        with mock.patch.dict(os.environ,{"TEAMWORK_HEALTH_FIXTURE":str(fixture)}),mock.patch.object(tw.shutil,"disk_usage",return_value=usage):
            event=tw._disk_apply(self.dir/".teamwork",state,rows,[self.home])
        self.assertTrue(child.exists()); self.assertEqual(event["failed"],[{"path":str(target),"error":"在用"}])
        with mock.patch.object(tw,"_disk_open_paths",side_effect=RuntimeError("lsof unavailable")),mock.patch.object(tw.shutil,"disk_usage",return_value=usage):
            with self.assertRaisesRegex(ValueError,"lsof unavailable"):
                tw._disk_apply(self.dir/".teamwork",state,rows,[self.home])
        self.assertTrue(child.exists())

    def test_apply_does_not_follow_top_level_symlink_replacement(self):
        import tw
        target=self.home/"Backups"; target.mkdir(); victim=self.home/"keep"; victim.mkdir(); (victim/"data").write_text("keep")
        moved=self.home/"Backups-original"; target.rename(moved); target.symlink_to(victim,target_is_directory=True)
        rows=[{"path":str(target),"category":"backup","bytes":1,"eligible":True}]
        usage=type("Usage",(),{"free":100,"total":200})()
        with mock.patch.object(tw,"_disk_open_paths",return_value=set()),mock.patch.object(tw.shutil,"disk_usage",return_value=usage):
            with self.assertRaises(ValueError): tw._disk_apply(self.dir/".teamwork",self.state(),rows,[self.home])
        self.assertEqual((victim/"data").read_text(),"keep"); self.assertTrue(moved.exists())

    def test_disk_threshold_preferences_are_tunable(self):
        self.tw("prefs", "--set", "disk_low_pct=8", "disk_low_gb=25", "disk_private_tmp_gb=4",
                "disk_growth_gb_per_hour=1", "disk_scratch_days=9", "disk_keep_snapshots=4", "disk_top_n=6",
                "disk_private_budget_s=4", "disk_scan_budget_s=45", "disk_scan_interval_hours=18",
                "health_load1_cpu_multiple=7", "health_pipe_min_bytes=8192", "health_process_max=2800",
                "health_disconnect_window=12")
        prefs = self.state()["prefs"]
        self.assertEqual([prefs[k] for k in ("disk_low_pct", "disk_low_gb", "disk_private_tmp_gb",
            "disk_growth_gb_per_hour", "disk_scratch_days", "disk_keep_snapshots", "disk_top_n", "disk_private_budget_s", "disk_scan_budget_s", "disk_scan_interval_hours")], [8,25,4,1,9,4,6,4,45,18])
        self.assertEqual([prefs[k] for k in ("health_load1_cpu_multiple","health_pipe_min_bytes","health_process_max","health_disconnect_window")], [7,8192,2800,12])
        self.assertNotEqual(self.tw("prefs", "--set", "disk_low_gb=wide", ok=False).returncode, 0)

    def test_private_tmp_incomplete_measurement_keeps_cached_size_without_alarm(self):
        import tw
        from types import SimpleNamespace
        tmp=self.project/".teamwork"/"private"/"thread-sky"/"tmp"
        state=self.state(); state["prefs"].update(language="zh",disk_private_tmp_gb=99,disk_low_gb=1)
        state["disk_space"]={"sizes":{str(tmp):123456},"top":[],"samples":[],"ranked_at":tw.now(),
            "measure_cache":{str(tmp):{"bytes":2048,"cached_bytes":123456,"cached_at":tw.now(),"complete":False,"updated_at":tw.now(),"device":tmp.stat().st_dev,"stack":[]}}}
        health={"alarms":[],"simulators":[],"simulator_devices":[],"simulator_count":0,"thresholds":{"load1":1,"pipe_bytes":1}}
        with mock.patch.object(tw,"_disk_projects",return_value=[self.project]), \
             mock.patch.object(tw.shutil,"disk_usage",return_value=SimpleNamespace(free=50*1024**3,total=100*1024**3)), \
             mock.patch.object(tw,"_disk_refresh"), \
             mock.patch.object(tw,"_disk_rank",return_value=([],False)), \
             mock.patch.object(tw.subprocess,"run",return_value=SimpleNamespace(stdout="",returncode=0)):
            report=tw._disk_scan(self.dir/".teamwork",state,health)
        self.assertEqual(report["sizes"][str(tmp)],123456)
        self.assertFalse(report["measurements"][str(tmp)])
        self.assertFalse(any("thread-sky" in x for x in report["alarms"]))

    def test_full_disk_ranking_runs_each_budgeted_inspection(self):
        import tw
        from types import SimpleNamespace
        health={"alarms":[],"simulators":[],"simulator_devices":[],"simulator_count":0,"thresholds":{"load1":1,"pipe_bytes":1}}
        recent=tw.now()
        def scan(free, ranked_at):
            state=self.state(); state["prefs"].update(disk_low_gb=30,disk_low_pct=10)
            state["disk_space"]={"sizes":{},"top":[[10,"/cached"]],"rank_sizes":{"/cached":10},"ranked_at":ranked_at,"samples":[]}
            with mock.patch.object(tw,"_disk_projects",return_value=[]), \
                 mock.patch.object(tw.shutil,"disk_usage",return_value=SimpleNamespace(free=free,total=100*1024**3)), \
                 mock.patch.object(tw,"_disk_rank",return_value=([(20,"/ranked")],False)) as rank, \
                 mock.patch.object(tw.subprocess,"run",return_value=SimpleNamespace(stdout="",returncode=0)):
                result=tw._disk_scan(self.dir/".teamwork",state,health)
            return rank,result
        rank,result=scan(50*1024**3,recent)
        rank.assert_called_once(); self.assertEqual(result["top"],[(20,"/ranked")])
        stale=(dt.datetime.fromisoformat(recent)-dt.timedelta(hours=25)).isoformat()
        rank,result=scan(50*1024**3,stale)
        rank.assert_called_once(); self.assertEqual(result["top"],[(20,"/ranked")])
        rank,result=scan(5*1024**3,recent)
        rank.assert_called_once()

    def test_full_disk_ranking_seeds_known_large_directory(self):
        import tw
        home=self.home/"rank-home"; backup=home/".dev-scratch"/"backups"; backup.mkdir(parents=True)
        rows,partial=tw._disk_rank(home,{},10,1)
        self.assertIn(str(backup),[path for _size,path in rows])
        self.assertFalse(partial)

    def test_rank_timeout_keeps_cached_largest_and_respects_allocated_budget(self):
        import tw
        home=self.home/"rank-priority"; large=home/".dev-scratch"/"backups"
        large.mkdir(parents=True); (home/".a-small").mkdir(); (home/".b-small").mkdir()
        calls=[]
        cache={}
        for _ in range(20): tw._disk_rank(home,cache,10,.002)
        self.assertTrue(any(cache.get(str(path),{}).get("complete") for path in (large,home/".a-small",home/".b-small")))

    def test_pending_preview_covers_large_categories_with_fingerprints(self):
        import tw
        scratch=self.home/".dev-scratch"; backup=scratch/"backups"; backup.mkdir(parents=True)
        caches=self.home/"Library"/"Caches"/"AppCache"; caches.mkdir(parents=True)
        support=self.home/"Library/Developer/Xcode/iOS DeviceSupport"; support.mkdir(parents=True)
        simulator=self.home/"Library/Developer/CoreSimulator/Devices"/"SIM-1"; simulator.mkdir(parents=True)
        other=self.project/".teamwork/private/thread-sky/tmp/old-copy"; other.mkdir()
        health={"simulator_devices":[{"udid":"SIM-1","name":"iPhone","state":"Shutdown"}]}
        report={"health":health}
        with mock.patch.object(tw,"_disk_in_use",return_value=True), \
             mock.patch.object(tw,"_disk_projects",return_value=[self.project]), \
             mock.patch.object(tw,"_disk_refresh",side_effect=lambda paths,cache,budget_s,seed_paths=():[cache.update({str(path):{"bytes":4096,"complete":True,"measured_at":tw.now(),"updated_at":tw.now(),"device":Path(path).stat().st_dev,"stack":[]}}) for path in paths]):
            rows=tw._disk_candidates(self.dir/".teamwork",report,pending=True)
        by_category={}
        for row in rows: by_category.setdefault(row["category"],[]).append(row)
        for category in ("backup","application cache","other project tmp","iOS DeviceSupport","simulator data"):
            self.assertTrue(by_category.get(category),category)
        self.assertTrue(all(row.get("path") and row.get("bytes",0)>0 and row.get("signature") for row in rows))
        self.assertTrue(all(row["in_use"] is None for row in rows))

    def test_disk_summary_sections_are_printed_once(self):
        import tw
        from types import SimpleNamespace
        fixture=self.dir/"disk-summary.json"
        fixture.write_text(json.dumps({"load":[0,0,0],"cpu_count":2,"process_count":1,"pipe_bytes":65536,
            "simulator_count":1,"disk_free":1,"disk_total":100,"disk_sizes":{},
            "simulator_devices":[{"udid":"SIM-1","name":"iPhone","state":"Booted"}],"processes":[],
            "pending_candidates":[],"auto_candidates":[]}),encoding="utf-8")
        state=self.state(); state["prefs"].update(language="en",disk_scan_budget_s=1)
        args=SimpleNamespace(space_auto=False,space_apply=False,space_fingerprint=None,apply=False,_health_report=None)
        output=io.StringIO()
        with mock.patch.dict(os.environ,{"HOME":str(self.home),"TEAMWORK_HEALTH_FIXTURE":str(fixture)}), \
             mock.patch.object(tw,"_disk_rank",return_value=([],False)), \
             mock.patch.object(tw,"_save_locked"), mock.patch("sys.stdout",output):
            tw._disk_command(args,self.dir/".teamwork",state)
        rendered=output.getvalue()
        for label in ("Largest directories:","Booted simulators:","Simulator options (shutdown / erase / delete):"):
            self.assertEqual(rendered.count(label),1,label)

    def test_health_thresholds_and_orphans_preview_apply_pid_reuse(self):
        import tw
        from types import SimpleNamespace
        fixture=self.dir/"health.json"
        uid=os.getuid()
        hours_ago=lambda h:(dt.datetime.now().astimezone()-dt.timedelta(hours=h)).isoformat()
        rows=[{"pid":701,"ppid":1,"uid":uid,"cmdline":"python test_tw.py T82","start_time":hours_ago(5)},
              {"pid":702,"ppid":1,"uid":uid,"cmdline":"tw-merge-abc","start_time":hours_ago(4)}]
        fixture.write_text(json.dumps({"load":[33,20,10],"cpu_count":4,"process_count":3001,"pipe_bytes":1024,"pipe_holders":[],"simulator_count":2,"processes":rows}),encoding="utf-8")
        self.env["TEAMWORK_HEALTH_FIXTURE"]=str(fixture)
        with mock.patch.dict(os.environ,{"TEAMWORK_HEALTH_FIXTURE":str(fixture),"HOME":str(self.home)}):
            state=self.state(); state["prefs"]["language"]="zh"
            health=tw._disk_health(state)
            self.assertEqual(health["load"],[33,20,10]); self.assertEqual(health["simulator_count"],2)
            self.assertEqual(len(health["orphans"]),2)
            self.assertEqual(len(health["alarms"]),4)
            with mock.patch.object(tw.os,"kill") as kill, mock.patch.object(tw,"_save_locked"):
                args=SimpleNamespace(space_auto=False,space_apply=False,space_fingerprint=None,apply=False)
                tw._disk_command(args,self.dir/".teamwork",state)
                kill.assert_not_called()
                args.apply=True
                tw._disk_command(args,self.dir/".teamwork",state)
                self.assertEqual([c.args[0] for c in kill.call_args_list],[701,702])
                kill.reset_mock()
                fixture.write_text(json.dumps({"processes":[dict(rows[0],cmdline="different command"),rows[1]]}),encoding="utf-8")
                killed=tw._disk_kill_orphans(rows)
                self.assertEqual(killed,[702])
                kill.assert_called_once_with(702,tw.signal.SIGTERM)

    def test_app_server_process_metrics_pipe_alarm_and_idle_preview(self):
        import tw
        broker_dir=self.dir/"broker"; broker_dir.mkdir()
        log=broker_dir/"broker.log"; log.write_text("old")
        old=time.time()-3*3600; os.utime(log,(old,old))
        now=dt.datetime.now().astimezone()
        def stamp(hours): return (now-dt.timedelta(hours=hours)).isoformat()
        rows=[{"pid":500,"ppid":1,"uid":os.getuid(),"cmdline":f"node {broker_dir}/app-server-broker.mjs --cwd /work/tree --pid-file {broker_dir}/broker.pid","start_time":stamp(8)},
              {"pid":501,"ppid":500,"uid":os.getuid(),"cmdline":"codex app-server","start_time":stamp(5)},
              {"pid":502,"ppid":501,"uid":os.getuid(),"cmdline":"node_repl","start_time":stamp(4)},
              {"pid":503,"ppid":501,"uid":os.getuid(),"cmdline":"node_repl","start_time":stamp(2)}]
        (broker_dir/"jobs.json").write_text('{"jobs":[{"status":"idle"}]}')
        fixture=self.dir/"app-health.json"
        fixture.write_text(json.dumps({"load":[1,0,0],"cpu_count":4,"process_count":4,"pipe_bytes":512,
            "processes":rows,"pipe_holders":[{"pid":501,"name":"codex","pipes":20}]}))
        with mock.patch.dict(os.environ,{"TEAMWORK_HEALTH_FIXTURE":str(fixture)}):
            state=self.state(); health=tw._disk_health(state)
        self.assertEqual(len(health["app_servers"]),1)
        server=health["app_servers"][0]
        self.assertEqual((server["node_repl_count"],server["worktree"],server["broker_pid"]),(2,"/work/tree",500))
        self.assertGreater(server["node_repl_longest_s"],server["node_repl_median_s"])
        self.assertTrue(server["idle"])
        self.assertIn("codex pid=501 pipes=20",health["alarms"][0])

    def test_idle_broker_sigterm_rechecks_command_and_start_time(self):
        import tw
        candidate={"broker_pid":901,"broker_cmdline":"node /x/app-server-broker.mjs --cwd /work",
                   "broker_start_time":"start-a","idle":True}
        reused={"pid":901,"cmdline":"node unrelated replacement","start_time":"start-b"}
        with mock.patch.object(tw,"_disk_processes",return_value=[reused]),mock.patch.object(tw.os,"kill") as kill:
            self.assertEqual(tw._disk_kill_idle_brokers([candidate]),[])
            kill.assert_not_called()
        live={"pid":901,"cmdline":candidate["broker_cmdline"],"start_time":"start-a"}
        with mock.patch.object(tw,"_disk_processes",return_value=[live]),mock.patch.object(tw.os,"kill") as kill:
            self.assertEqual(tw._disk_kill_idle_brokers([candidate]),[901])
            kill.assert_called_once_with(901,tw.signal.SIGTERM)

    def test_pending_inventory_keeps_unmeasured_candidate_with_explicit_size(self):
        import tw
        backup=self.home/".dev-scratch"/"backups"; backup.mkdir(parents=True)
        report={"health":{"simulator_devices":[]},"projects":[],"measure_cache":{}}
        with mock.patch.object(tw,"_disk_in_use",return_value=False),mock.patch.object(tw,"_disk_refresh"):
            rows=tw._disk_candidates(self.dir/".teamwork",report,pending=True,deadline=time.monotonic()+2)
        row=next(item for item in rows if item["path"]==str(backup))
        self.assertIsNone(row["bytes"])
        self.assertEqual(row["size_display"],"未测")
        self.assertFalse(row["eligible"])

    def test_disconnect_events_deduplicate_newer_time_and_render_zh(self):
        import tw
        self.tw("lead","--session","lead-demo")
        self.tw("thread","add","worker","--session","session-worker")
        state=self.state(); state["prefs"]["language"]="zh"
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(state),encoding="utf-8")
        fixture=self.dir/"health.json"; fixture.write_text(json.dumps({"load":[2,1,1],"cpu_count":8,"process_count":50,"pipe_bytes":65536,"simulator_count":1,"disk_free":50*1024**3,"disk_total":100*1024**3,"disk_sizes":{},"processes":[]}),encoding="utf-8")
        self.env["TEAMWORK_HEALTH_FIXTURE"]=str(fixture)
        sessions=self.dir/"sessions.json"
        row={"id":"session-worker","thread":"worker","status":"running","external_metadata":{"last_init_error":{"error_kind":"computer_unreachable","at":"2026-11-27T10:00:00Z"}}}
        sessions.write_text(json.dumps({"data":[row]}),encoding="utf-8")
        out=self.tw("notice","sweep","--sessions",str(sessions)).stdout
        self.assertIn("最近 1 次断连",out)
        state=self.state(); self.assertEqual(len(state["computer_unreachable_recent"]),1)
        events=[json.loads(x) for x in (self.dir/".teamwork"/"log.jsonl").read_text().splitlines()]
        self.assertEqual(sum(x.get("ev")=="computer-unreachable" for x in events),1)
        self.tw("notice","sweep","--sessions",str(sessions))
        self.assertEqual(len(self.state()["computer_unreachable_recent"]),1)
        row["external_metadata"]["last_init_error"]["at"]="2026-11-27T10:01:00Z"
        sessions.write_text(json.dumps({"data":[row]}),encoding="utf-8")
        self.tw("notice","sweep","--sessions",str(sessions))
        self.assertEqual(len(self.state()["computer_unreachable_recent"]),2)

    def test_simulator_per_device_action_is_fingerprinted_and_last_used_marked(self):
        import tw
        from types import SimpleNamespace
        device={"udid":"UDID-1","name":"iPhone","runtime":"runtime.iOS","state":"Booted","bytes":1024,
                "last_used":"2026-11-27T10:00+00:00","last_used_estimated":True,"actions":["shutdown","erase","delete"]}
        fingerprint=tw._disk_sim_fingerprint(device,"shutdown")
        args=SimpleNamespace(sim_udid="UDID-1",sim_action="shutdown",sim_fingerprint=fingerprint,apply=True)
        with mock.patch.object(tw,"_disk_scan",return_value={"simulator_inventory":[device]}), \
             mock.patch.object(tw.subprocess,"run",return_value=SimpleNamespace(returncode=0,stdout="",stderr="")) as run:
            self.assertTrue(tw._disk_sim_action(args,self.dir/".teamwork",self.state()))
            run.assert_called_once_with(["xcrun","simctl","shutdown","UDID-1"],capture_output=True,text=True,timeout=120)
        with mock.patch.object(tw,"_disk_scan",return_value={"simulator_inventory":[dict(device,state="Shutdown")]}):
            with self.assertRaisesRegex(ValueError,"Pending cleanup list changed"):
                tw._disk_sim_action(args,self.dir/".teamwork",self.state())
        fixture=self.dir/"sim-health.json"
        exact=dict(device,lastBootedAt="2026-11-01T12:30:00Z")
        fixture.write_text(json.dumps({"load":[0,0,0],"cpu_count":8,"process_count":0,"pipe_bytes":65536,
            "simulator_count":1,"disk_free":50*1024**3,"disk_total":100*1024**3,"disk_sizes":{},
            "simulator_devices":[exact],"processes":[]}),encoding="utf-8")
        with mock.patch.dict(os.environ,{"HOME":str(self.home),"TEAMWORK_HEALTH_FIXTURE":str(fixture)}):
            report=tw._disk_scan(self.dir/".teamwork",self.state())
            self.assertFalse(report["simulator_inventory"][0]["last_used_estimated"])
            parsed=dt.datetime.fromisoformat(report["simulator_inventory"][0]["last_used"])
            self.assertEqual(parsed.astimezone(dt.timezone.utc).strftime("%H:%M"),"12:30")
            folder=self.home/"Library/Developer/CoreSimulator/Devices"/"UDID-1"; folder.mkdir(parents=True)
            (folder/"device-data.bin").write_bytes(b"simulator data"*64)
            os.utime(folder,(time.time()-3600,time.time()-3600))
            fixture.write_text(json.dumps({"load":[0,0,0],"cpu_count":8,"process_count":0,"pipe_bytes":65536,
                "simulator_count":0,"disk_free":50*1024**3,"disk_total":100*1024**3,"disk_sizes":{},
                "simulator_devices":[dict(device,udid="UDID-1")],"processes":[]}),encoding="utf-8")
            report=tw._disk_scan(self.dir/".teamwork",self.state())
            self.assertTrue(report["simulator_inventory"][0]["last_used_estimated"])
            self.assertGreater(report["simulator_inventory"][0]["bytes"],0)
            report=tw._disk_scan(self.dir/".teamwork",self.state(),include_sim_details=True)
            self.assertGreater(report["simulator_inventory"][0]["bytes"],0)


class T82DiskMeasurement(TwCase):
    def setUp(self):
        super().setUp()
        self.home = self.dir / "measured home"
        self.home.mkdir()
        self.env["HOME"] = str(self.home)
        self.env.pop("TEAMWORK_HEALTH_FIXTURE", None)
        self._home_patch = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        self._home_patch.start(); self.addCleanup(self._home_patch.stop)

    def test_r7_cached_measurements_render_ranking_and_both_previews(self):
        import tw
        from types import SimpleNamespace
        fixture=json.loads((Path(__file__).parents[1]/"fixtures"/"t82-r7-state.json").read_text(encoding="utf-8"))
        fake_prefix="/tmp/t82-r7-home"
        cache={path.replace(fake_prefix,str(self.home),1):row for path,row in fixture["disk_space"]["measure_cache"].items()}
        for path in cache: Path(path).mkdir(parents=True,exist_ok=True)
        (self.home/".npm"/"_cacache").mkdir(parents=True,exist_ok=True)
        state=self.state(); state["prefs"].update(language="en",disk_private_budget_s=0,disk_scan_budget_s=0,disk_top_n=20)
        state["disk_space"]={"measure_cache":cache,"simulator_inventory":fixture["disk_space"].get("simulator_inventory",[])}
        devices=[{"udid":Path(path).name,"name":"fixture simulator","state":"Shutdown"}
                 for path in cache if "/CoreSimulator/Devices/" in path and Path(path).parent.name=="Devices"]
        health_fixture=self.dir/"r7-health.json"
        health_fixture.write_text(json.dumps({"load":[0,0,0],"cpu_count":2,"process_count":1,"pipe_bytes":65536,
            "disk_free":1,"disk_total":100,"simulator_devices":devices,"pipe_holders":[]}),encoding="utf-8")
        args=SimpleNamespace(space_auto=False,space_apply=False,space_fingerprint=None,apply=False,_health_report=None)
        output=io.StringIO()
        with mock.patch.dict(os.environ,{"HOME":str(self.home),"TEAMWORK_HEALTH_FIXTURE":str(health_fixture)}), \
             mock.patch.object(tw,"_disk_projects",return_value=[]), mock.patch.object(tw,"_save_locked"), \
             mock.patch("sys.stdout",output):
            tw._disk_command(args,self.dir/".teamwork",state)
        rendered=output.getvalue()
        largest=rendered.split("Largest directories:",1)[1].split("\n",1)[0]
        for name in ("backups","Caches","Devices",".codex"):
            self.assertIn(name,largest)
        self.assertIn("DerivedData",rendered.split("Auto cleanup preview",1)[1].split("\n",1)[0])
        self.assertIn(".npm",rendered.split("Auto cleanup preview",1)[1].split("\n",1)[0])
        self.assertIn("≥ ",largest)
        pending=rendered.split("Pending approval list",1)[1].split("\n",1)[0]
        for name in ("backups","5926FAE6","Caches","iOS DeviceSupport"):
            self.assertIn(name,pending)
        self.assertNotIn("Devices/5926FAE6",largest)

    def test_lsof_has_independent_budget_and_reports_timeout_or_parse_failure(self):
        import tw
        from types import SimpleNamespace
        with mock.patch.object(tw.shutil,"which",return_value="/usr/sbin/lsof"), \
             mock.patch.object(tw.subprocess,"run",side_effect=subprocess.TimeoutExpired("lsof",3)) as run:
            holders,reason=tw._disk_pipe_holders(deadline=time.monotonic()-10)
        self.assertEqual(holders,[])
        self.assertIn("timed out",reason)
        self.assertEqual(run.call_args.kwargs["timeout"],3.0)
        with mock.patch.object(tw.shutil,"which",return_value="/usr/sbin/lsof"), \
             mock.patch.object(tw.subprocess,"run",return_value=SimpleNamespace(returncode=0,stdout="garbage\n",stderr="")):
            holders,reason=tw._disk_pipe_holders()
        self.assertEqual(holders,[])
        self.assertIn("parse failed",reason)
        fixture=self.dir/"low-pipe-health.json"
        fixture.write_text(json.dumps({"load":[0,0,0],"cpu_count":2,"process_count":1,"pipe_bytes":1,
            "disk_free":1,"disk_total":100,"processes":[],"simulator_devices":[]}),encoding="utf-8")
        state=self.state(); state["prefs"]["language"]="en"
        with mock.patch.dict(os.environ,{"TEAMWORK_HEALTH_FIXTURE":str(fixture)}), \
             mock.patch.object(tw,"_disk_pipe_holders",return_value=([],"timed out after 3.0 seconds")):
            health=tw._disk_health(state)
        alarm=next(line for line in health["alarms"] if "holders:" in line)
        self.assertIn("timed out",alarm)
        self.assertNotIn("holders: unknown",alarm)

    def test_internal_blocks_match_du_sk(self):
        import tw
        root = self.home / "tree with spaces"; root.mkdir()
        for index in range(8):
            folder = root / f"dir-{index}"; folder.mkdir()
            (folder / "payload").write_bytes(os.urandom(13000 + index * 1700))
        cache = {}
        tw._disk_refresh([root], cache, 5)
        output = subprocess.run(["du", "-sk", str(root)], capture_output=True, text=True, check=True)
        expected = int(output.stdout.split(None, 1)[0]) * 1024
        actual = cache[str(root)]["bytes"]
        self.assertTrue(cache[str(root)]["complete"])
        self.assertLessEqual(abs(actual - expected) / max(1, expected), .01)

    def test_signature_chain_is_stable_across_fresh_scans(self):
        import tw
        root=self.home/"stable-signature"; root.mkdir()
        for name in ("z-last","a-first","m-middle"):
            folder=root/name; folder.mkdir(); (folder/"data").write_bytes(name.encode())
        signatures=[]
        for _ in range(3):
            cache={}; tw._disk_refresh([root],cache,3)
            self.assertTrue(cache[str(root)]["complete"])
            signatures.append(cache[str(root)]["signature_chain"])
        self.assertEqual(signatures,[signatures[0]]*3)

    def test_tiny_budget_rounds_eventually_complete_every_path(self):
        import tw
        roots = []
        for index in range(5):
            root = self.home / f"large-{index}"; root.mkdir()
            for branch in range(3):
                child = root / f"branch-{branch}"; child.mkdir()
                for leaf in range(5): (child / f"file-{leaf}").write_bytes(b"x" * 2048)
            roots.append(root)
        cache = {}
        started = time.monotonic()
        for _ in range(1000):
            tw._disk_refresh(roots, cache, .0002)
            if all(cache.get(str(root), {}).get("complete") for root in roots): break
        self.assertTrue(all(cache.get(str(root), {}).get("complete") for root in roots))
        self.assertLess(time.monotonic() - started, 5)

    def test_unmeasured_private_tmp_does_not_alarm(self):
        import tw
        from types import SimpleNamespace
        project = self.home / "project"; tmp = project / ".teamwork/private/sky/tmp"; tmp.mkdir(parents=True)
        state = self.state(); state["prefs"].update(disk_private_budget_s=1, disk_scan_budget_s=1,
            disk_private_tmp_gb=0, disk_low_gb=1)
        health = {"alarms": [], "simulators": [], "simulator_devices": [], "simulator_count": 0,
                  "thresholds": {"load1": 1, "pipe_bytes": 1}}
        usage = SimpleNamespace(free=50 * 1024**3, total=100 * 1024**3)
        with mock.patch.object(tw, "_disk_projects", return_value=[project]), \
             mock.patch.object(tw, "_disk_refresh"), \
             mock.patch.object(tw, "_disk_rank", return_value=([], False)), \
             mock.patch.object(tw.shutil, "disk_usage", return_value=usage):
            report = tw._disk_scan(self.dir / ".teamwork", state, health)
        self.assertIsNone(report["sizes"][str(tmp)])
        self.assertFalse(report["measurements"][str(tmp)])
        self.assertFalse(any("sky" in alarm for alarm in report["alarms"]))

    def test_partial_scan_resumes_from_saved_stack(self):
        import tw
        root = self.home / "resumable"; root.mkdir()
        for branch in range(12):
            child = root / f"branch-{branch}"; child.mkdir()
            (child / "payload").write_bytes(os.urandom(8192))
        cache = {}
        tw._disk_refresh([root], cache, .0001)
        self.assertFalse(cache[str(root)]["complete"])
        initial_bytes=cache[str(root)]["bytes"]
        cache=json.loads(json.dumps(cache))
        for _ in range(100):
            if cache[str(root)]["complete"]: break
            tw._disk_refresh([root], cache, .001)
        row = cache[str(root)]
        self.assertTrue(row["complete"])
        self.assertGreater(row["bytes"], initial_bytes)
        self.assertIsNotNone(row["measured_at"])

    def test_symlinks_and_other_devices_are_excluded(self):
        import tw
        from types import SimpleNamespace
        root = self.home / "device-boundary"; root.mkdir()
        regular = root / "regular"; regular.write_bytes(b"r" * 20000)
        linked = root / "linked"; linked.write_bytes(b"s" * 20000)
        link = root / "symbolic"; link.symlink_to(linked)
        foreign = root / "foreign"; foreign.mkdir(); (foreign / "payload").write_bytes(b"f" * 20000)
        original_lstat = Path.lstat
        def adjusted(path, *args, **kwargs):
            info = original_lstat(path, *args, **kwargs)
            if Path(path) == foreign:
                return SimpleNamespace(**{name: getattr(info, name) for name in dir(info) if name.startswith("st_")} | {"st_dev": info.st_dev + 1})
            return info
        with mock.patch.object(Path, "lstat", adjusted):
            cache = {}; tw._disk_refresh([root], cache, 5)
        expected = tw._disk_blocks(original_lstat(root)) + tw._disk_blocks(original_lstat(regular)) + tw._disk_blocks(original_lstat(linked))
        self.assertTrue(cache[str(root)]["complete"])
        self.assertEqual(cache[str(root)]["bytes"], expected)

    def test_candidate_rows_and_rendered_sections_are_not_duplicated(self):
        import tw
        backup = self.home / "Backups"; backup.mkdir(); (backup / "payload").write_bytes(b"x" * 4096)
        report = {"health": {"simulator_devices": []}, "projects": [], "measure_cache": {}}
        with mock.patch.object(tw, "_disk_in_use", return_value=False):
            rows = tw._disk_candidates(self.dir / ".teamwork", report, pending=True,
                                       deadline=time.monotonic() + 2)
        paths = [row["path"] for row in rows]
        self.assertEqual(len(paths), len(set(paths)))

    def test_first_layer_seed_immediately_supplies_a_ranking_value(self):
        import tw
        home=self.home; backup=home/".dev-scratch/backups"; backup.mkdir(parents=True)
        payload=backup/"first-layer.bin"; payload.write_bytes(b"seed data"*1024)
        cache={}
        rows,_partial=tw._disk_rank(home,cache,10,.02)
        self.assertIn(str(backup),[path for _size,path in rows])
        self.assertGreaterEqual(cache[str(backup)]["bytes"],tw._disk_blocks(backup.lstat()))

    def test_scan_deadline_bounds_a_many_file_tree_and_repeated_rounds_finish(self):
        import tw
        from types import SimpleNamespace
        project=self.home/"project"; root=project/"large-tree"; root.mkdir(parents=True)
        for branch in range(30):
            child=root/f"branch-{branch}"; child.mkdir()
            for leaf in range(200): (child/f"file-{leaf}").write_bytes(b"x")
        state=self.state(); state["prefs"].update(disk_private_budget_s=0,disk_scan_budget_s=.5)
        health={"alarms":[],"simulators":[],"simulator_devices":[],"simulator_count":0,
                "thresholds":{"load1":1,"pipe_bytes":1}}
        cache={}
        for round_index in range(100):
            state["disk_space"]={"measure_cache":cache}
            started=time.monotonic()
            with mock.patch.object(tw,"_disk_projects",return_value=[project]):
                report=tw._disk_scan(self.dir/".teamwork",state,health)
            elapsed=time.monotonic()-started
            self.assertLess(elapsed,1.5)
            cache=report["measure_cache"]
            if cache.get(str(project),{}).get("complete"): break
        self.assertTrue(cache.get(str(project),{}).get("complete"))
        self.assertTrue(cache.get(str(project),{}).get("bytes",0)>0)

    def test_root_is_never_added_to_rank_candidates(self):
        import tw
        home=self.home; cache={}; budget={"rank_measured":[],"rank_unmeasured":[]}
        rows,_partial=tw._disk_rank(home,cache,20,.01,known_paths=[Path("/")],du_budget=budget)
        self.assertNotIn("/",[path for _size,path in rows])
        self.assertNotIn("/",budget["rank_unmeasured"])
        projects=tw._disk_projects(home,Path("/.teamwork"))
        self.assertNotIn(Path("/"),projects)

    def test_auto_preview_contains_only_regenerable_categories(self):
        import tw
        npm=self.home/".npm"/"_cacache"; npm.mkdir(parents=True)
        derived=self.home/"Library/Developer/Xcode/DerivedData"; derived.mkdir(parents=True)
        backup=self.home/".dev-scratch/backups"; backup.mkdir(parents=True)
        report={"pct":1,"free":0,"projects":[],"health":{"simulator_devices":[]},"measure_cache":{}}
        with mock.patch.object(tw,"_disk_in_use",return_value=False), mock.patch.object(tw,"_disk_refresh"):
            auto=tw._disk_candidates(self.dir/".teamwork",report,deadline=time.monotonic()+2)
            pending=tw._disk_candidates(self.dir/".teamwork",report,pending=True,deadline=time.monotonic()+2)
        auto_paths={row["path"] for row in auto}; pending_paths={row["path"] for row in pending}
        self.assertIn(str(self.home/".npm"/"_cacache"),auto_paths); self.assertIn(str(derived),auto_paths)
        self.assertIn(str(backup),pending_paths)
        self.assertNotIn(str(backup),auto_paths)
        self.assertNotIn(str(derived),pending_paths)
        self.assertTrue(all(row["size_display"]=="未测" for row in auto if row["path"] in (str(self.home/".npm"/"_cacache"),str(derived))))


if __name__ == "__main__":
    unittest.main()

class T82R9CandidatePaths(TwCase):
    def setUp(self):
        super().setUp()
        import tw
        self.twmod = tw
        self.home = self.dir / "fake-home"
        self.home.mkdir()
        self.project = self.home / "project"
        self.project.mkdir()
        (self.project / ".teamwork" / "private" / "thread" / "tmp").mkdir(parents=True)
        self.state_path = self.dir / ".teamwork" / "state.json"
        patch = mock.patch.dict(os.environ, {"HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.home / ".claude")})
        patch.start(); self.addCleanup(patch.stop)

    def save(self, state):
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

    def test_apply_notes_local_snapshots_when_free_space_does_not_rise(self):
        tw = self.twmod
        from types import SimpleNamespace
        cache = self.home / "Library" / "Caches" / "Homebrew"; cache.mkdir(parents=True)
        (cache / "bottle.tar").write_bytes(b"x" * 10)
        rows = [{"path": str(cache), "category": "Homebrew cache", "bytes": 512 * 1024**2, "eligible": True},
                {"path": str(self.home / "unmeasured"), "category": "npm cache", "bytes": None, "eligible": False}]
        usage = SimpleNamespace(total=100, used=50, free=10 * 1024**3)
        snaps = SimpleNamespace(returncode=0, stdout="Snapshots for disk /:\ncom.apple.TimeMachine.2026-11-27-053029.local\n", stderr="")
        state = self.state(); state["prefs"]["language"] = "zh"
        out = io.StringIO()
        with mock.patch.object(tw, "_disk_in_use", return_value=False), mock.patch.object(tw, "_disk_open_paths",return_value=set()), mock.patch.object(tw.shutil, "disk_usage", return_value=usage), \
             mock.patch.object(tw.shutil, "which", return_value="/usr/bin/tmutil"), \
             mock.patch.object(tw.subprocess, "run", return_value=snaps) as run, mock.patch("sys.stdout", out):
            event = tw._disk_apply(self.dir / ".teamwork", state, rows, [self.home])
        self.assertFalse(cache.exists())
        self.assertEqual(event["items"], [str(cache)])
        self.assertEqual(event["held_by_local_snapshots"], ["com.apple.TimeMachine.2026-11-27-053029.local"])
        run.assert_called_once_with(["tmutil", "listlocalsnapshots", "/"], capture_output=True, text=True, timeout=5)
        self.assertIn("Time Machine 本地快照", out.getvalue())

    def test_pending_lists_only_stale_scratch_skips_harness_tmp_and_caps_length(self):
        tw = self.twmod
        scratch = self.home / ".dev-scratch" / "tmp"; scratch.mkdir(parents=True)
        old = time.time() - 30 * 86400
        for name in ["claude-501", "fresh-copy", *[f"old-{i:02d}" for i in range(40)]]:
            (scratch / name).mkdir()
            if name != "fresh-copy": os.utime(scratch / name, (old, old))
        cache = {str(scratch / f"old-{i:02d}"): {"bytes": 1000 + i, "complete": True, "measured_at": tw.now()} for i in range(40)}
        report = {"pct": 1, "free": 0, "projects": [], "health": {"simulator_devices": []}, "measure_cache": cache}
        pending = tw._disk_candidates(self.dir / ".teamwork", report, True, deadline=time.monotonic() + 3, measure=False)
        paths = [row["path"] for row in pending]
        self.assertNotIn(str(scratch / "claude-501"), paths)
        self.assertNotIn(str(scratch / "fresh-copy"), paths)
        self.assertNotIn(str(scratch), paths)
        self.assertEqual(len(pending), 30)
        self.assertEqual(paths[0], str(scratch / "old-39"))
        self.assertEqual(report["pending_omitted"], 10)

    def test_scratch_age_is_the_newest_file_in_the_tree_and_checkouts_evidence_fallback_stay(self):
        tw = self.twmod
        scratch = self.home / ".dev-scratch" / "tmp"; old = time.time() - 30 * 86400
        def aged(root, keep_fresh=()):
            for p in sorted(root.rglob("*"), key=lambda x: -len(x.parts)) + [root]:
                if p not in keep_fresh: os.utime(p, (old, old), follow_symlinks=False)
        (scratch / "deep-fresh" / "a" / "b").mkdir(parents=True); fresh = scratch / "deep-fresh" / "a" / "b" / "work.log"; fresh.write_text("x")
        (scratch / "checkout").mkdir(); (scratch / "checkout" / ".git").write_text("gitdir: elsewhere")
        (scratch / "evid" / "run").mkdir(parents=True); proof = scratch / "evid" / "run" / "proof.txt"; proof.write_text("x")
        (scratch / "plain" / "x").mkdir(parents=True); (scratch / "plain" / "x" / "f").write_text("x")
        private = self.project / ".teamwork/private/thread/tmp/copy"; (private / "inner").mkdir(parents=True); (private / "inner" / "now.txt").write_text("x")
        for name in ("deep-fresh", "checkout", "evid", "plain"): aged(scratch / name, keep_fresh=(fresh,))
        aged(private, keep_fresh=(private / "inner" / "now.txt",))
        # Evidence named by ANOTHER project's ledger on this machine.
        (self.project / ".teamwork" / "state.json").write_text(json.dumps({"tasks": {"T1": {"evidence": str(proof)}}}), encoding="utf-8")
        report = {"pct": 1, "free": 0, "projects": [str(self.project)], "health": {"simulator_devices": []}, "measure_cache": {}}
        with mock.patch.object(tw, "_disk_has_unmerged_worktree", return_value=False):
            pending = {row["path"] for row in tw._disk_candidates(self.dir / ".teamwork", report, True, deadline=time.monotonic() + 3, measure=False)}
            auto = {row["path"] for row in tw._disk_candidates(self.dir / ".teamwork", report, deadline=time.monotonic() + 3, measure=False)}
            self.assertIn(str(scratch / "plain"), pending)
            for kept in ("deep-fresh", "checkout", "evid"): self.assertNotIn(str(scratch / kept), pending)
            self.assertNotIn(str(private), auto)
            (self.home / ".dev-scratch" / ".LOCAL_FALLBACK").write_text("local")
            again = {row["path"] for row in tw._disk_candidates(self.dir / ".teamwork", report, True, deadline=time.monotonic() + 3, measure=False)}
        self.assertFalse([p for p in again if p.startswith(str(scratch))])

    def test_unreadable_line_in_another_ledger_marks_the_protection_scan_incomplete(self):
        tw = self.twmod
        (self.project / ".teamwork" / "state.json").write_text("{}", encoding="utf-8")
        log = self.project / ".teamwork" / "log.jsonl"; log.write_text('{"ev": "x"}\n\n', encoding="utf-8")
        status = {}; tw._disk_guards(self.dir / ".teamwork", status=status); self.assertTrue(status["complete"])
        log.write_text('{"ev": "x", "evidence": "/some/pro\n', encoding="utf-8")
        status = {}; tw._disk_guards(self.dir / ".teamwork", status=status); self.assertFalse(status["complete"])

    def test_expired_ranking_deadline_still_lists_cached_auto_and_pending(self):
        tw = self.twmod
        npm = self.home / ".npm" / "_cacache"; npm.mkdir(parents=True)
        backup = self.home / "Backups"; backup.mkdir()
        cache = {str(npm): {"bytes": 2048, "complete": True, "measured_at": tw.now()},
                 str(backup): {"bytes": 4096, "complete": True, "measured_at": tw.now()}}
        report = {"pct": 1, "free": 0, "projects": [], "health": {"simulator_devices": []}, "measure_cache": cache, "_scan_deadline": time.monotonic() - 10}
        auto = tw._disk_candidates(self.dir / ".teamwork", report, deadline=time.monotonic() + 3, measure=False)
        pending = tw._disk_candidates(self.dir / ".teamwork", report, True, deadline=time.monotonic() + 3, measure=False)
        self.assertEqual(next(x for x in auto if x["path"] == str(npm))["bytes"], 2048)
        self.assertEqual(next(x for x in pending if x["path"] == str(backup))["bytes"], 4096)

    def test_measure_false_covers_merge_private_tmp_old_snapshot_and_space_gate(self):
        tw = self.twmod
        merge_root = self.home / "tw-merge-case"; merge_root.mkdir()
        private = self.project / ".teamwork/private/thread/tmp/expired-copy"; private.mkdir()
        old = time.time() - 10 * 86400; os.utime(private, (old, old))
        config = self.home / ".claude"; base = config / "teamwork-releases"; base.mkdir(parents=True)
        for name in ("r1", "r2", "r3", "r4", "r5", "r6"):
            (base / name).mkdir()
        (base / "index.json").write_text(json.dumps({"current": "r1", "previous": "r2", "history": ["r1", "r2"], "releases": [{"name": n, "at": n} for n in ("r1", "r2", "r3", "r4", "r5", "r6")]}))
        report = {"pct": 1, "free": 0, "projects": [str(self.project)], "health": {"simulator_devices": []}, "measure_cache": {}}
        with mock.patch.object(tw.tempfile, "gettempdir", return_value=str(self.home)), mock.patch.object(tw, "_disk_has_unmerged_worktree", return_value=False):
            rows = tw._disk_candidates(self.dir / ".teamwork", report, deadline=time.monotonic() + 3, measure=False)
            paths = {x["path"] for x in rows}
            self.assertIn(str(merge_root), paths)
            self.assertIn(str(private), paths)
            self.assertIn(str(base / "r3"), paths)
            self.assertNotIn(str(base / "r1"), paths)
            enough = tw._disk_candidates(self.dir / ".teamwork", {**report, "pct": 50, "free": 50 * 1024**3}, deadline=time.monotonic() + 3, measure=False)
            self.assertEqual(enough, [])

    def test_private_tmp_copies_are_removed_through_clear_thread_tmp(self):
        tw = self.twmod
        tmp = self.project / ".teamwork/private/thread/tmp"
        expired = tmp / "expired-copy"; expired.mkdir(); (expired / "f").write_text("x")
        keep = tmp / "fresh-copy"; keep.mkdir()
        other = self.project / ".teamwork/private/sky/tmp"; other.mkdir(parents=True); (other / "a").write_text("x")
        rows = [{"path": str(expired), "category": "private tmp old copy", "bytes": 1, "eligible": True},
                {"path": str(other), "category": "other project tmp", "bytes": 1, "eligible": True}]
        real = tw.clear_thread_tmp
        with mock.patch.object(tw, "_disk_open_paths", return_value=set()), \
             mock.patch.object(tw, "clear_thread_tmp", side_effect=real) as clear:
            event = tw._disk_apply(self.dir / ".teamwork", self.state(), rows, [self.home])
        self.assertEqual(sorted(c.args[1:] for c in clear.call_args_list), [("sky", ()), ("thread", ("expired-copy",))])
        self.assertFalse(expired.exists()); self.assertTrue(keep.exists())
        self.assertTrue(other.is_dir()); self.assertEqual(list(other.iterdir()), [])
        self.assertEqual(sorted(event["items"]), sorted([str(expired), str(other)]))

    def test_pending_fingerprint_stays_stable_then_apply_removes_only_planned_item(self):
        tw = self.twmod
        backup = self.home / "Backups"; backup.mkdir(); (backup / "payload").write_bytes(b"backup")
        other = self.home / "other"; other.mkdir(); (other / "keep").write_text("keep")
        cache = {}; tw._disk_refresh([backup],cache,3)
        self.assertTrue(cache[str(backup)]["complete"])
        report = {"measure_cache": cache, "health": {"simulator_devices": []}, "projects": []}
        pending = tw._disk_candidates(self.dir / ".teamwork", report, True, deadline=time.monotonic() + 3, measure=False)
        fp = tw._disk_fingerprint(pending)
        time.sleep(2)
        again = tw._disk_candidates(self.dir / ".teamwork", report, True, deadline=time.monotonic() + 3, measure=False)
        self.assertEqual(tw._disk_fingerprint(again), fp)
        state = self.state(); state["disk_space"] = {**report, "pending_plan": {"fingerprint": fp, "items": pending}}
        self.save(state)
        from types import SimpleNamespace
        args = SimpleNamespace(space_apply=True, space_fingerprint=fp, apply=True)
        with mock.patch.object(tw, "_disk_in_use", return_value=False), mock.patch.object(tw,"_disk_open_paths",return_value=set()), mock.patch.object(tw, "_save_locked"), mock.patch.object(tw, "log"):
            tw._disk_command(args, self.dir / ".teamwork", state)
        self.assertFalse(backup.exists()); self.assertTrue(other.exists())

    def test_pending_fingerprint_rejects_new_entry_or_changed_directory_mtime(self):
        tw = self.twmod
        backup = self.home / "Backups"; backup.mkdir()
        report = {"measure_cache": {str(backup): {"bytes": 0, "complete": True}}, "health": {"simulator_devices": []}, "projects": []}
        pending = tw._disk_candidates(self.dir / ".teamwork", report, True, deadline=time.monotonic() + 3, measure=False)
        fp = tw._disk_fingerprint(pending)
        (backup / "new-entry").write_text("changed")
        changed = tw._disk_candidates(self.dir / ".teamwork", report, True, deadline=time.monotonic() + 3, measure=False)
        self.assertNotEqual(tw._disk_fingerprint(changed), fp)
        state = self.state(); state["disk_space"] = {**report, "pending_plan": {"fingerprint": fp, "items": pending}}; self.save(state)
        from types import SimpleNamespace
        args = SimpleNamespace(space_apply=True, space_fingerprint=fp, apply=True)
        with self.assertRaisesRegex(ValueError, "Pending cleanup list changed"):
            tw._disk_command(args, self.dir / ".teamwork", state)

    def test_deep_content_change_after_listing_rejects_apply(self):
        tw=self.twmod
        backup=self.home/"Backups"; backup.mkdir(); nested=backup/"deep"; nested.mkdir(); leaf=nested/"payload"; leaf.write_bytes(b"before")
        cache={}; tw._disk_refresh([backup],cache,3)
        self.assertTrue(cache[str(backup)]["complete"])
        report={"measure_cache":cache,"health":{"simulator_devices":[]},"projects":[]}
        pending=tw._disk_candidates(self.state_path.parent,report,True,deadline=time.monotonic()+3,measure=False)
        fp=tw._disk_fingerprint(pending)
        state=self.state(); state["prefs"]["disk_scan_budget_s"]=3
        state["disk_space"]={**report,"pending_plan":{"fingerprint":fp,"items":pending}}
        self.save(state)
        root_mtime=backup.stat().st_mtime_ns
        leaf.write_bytes(b"changed deeply")
        os.utime(backup,ns=(root_mtime,root_mtime))
        from types import SimpleNamespace
        args=SimpleNamespace(space_apply=True,space_fingerprint=fp,apply=True)
        with mock.patch.object(tw,"_disk_open_paths",return_value=set()),mock.patch.object(tw,"_save_locked"):
            with self.assertRaisesRegex(ValueError,"Pending cleanup list changed"):
                tw._disk_command(args,self.state_path.parent,state)
        self.assertTrue(leaf.exists())

    def test_git_worktree_query_failure_is_unknown_but_non_git_is_clear(self):
        tw=self.twmod
        repo=self.home/"repo"; repo.mkdir(); (repo/".git").mkdir()
        failed=type("Result",(),{"returncode":2,"stdout":"","stderr":"error"})()
        with mock.patch.object(tw.subprocess,"run",return_value=failed):
            self.assertTrue(tw._disk_has_unmerged_worktree(repo))
        plain=self.home/"plain"; plain.mkdir()
        with mock.patch.object(tw.subprocess,"run") as run:
            self.assertFalse(tw._disk_has_unmerged_worktree(plain))
        run.assert_not_called()

    def test_orphan_requires_threshold_age_and_no_live_child(self):
        tw = self.twmod
        old = (dt.datetime.now().astimezone() - dt.timedelta(minutes=130)).strftime("%a %b %d %H:%M:%S %Y")
        recent = (dt.datetime.now().astimezone() - dt.timedelta(minutes=5)).strftime("%a %b %d %H:%M:%S %Y")
        rows = [{"pid": 810, "ppid": 1, "uid": os.getuid(), "cmdline": "python tw.py notice sweep --apply", "start_time": recent},
                {"pid": 811, "ppid": 1, "uid": os.getuid(), "cmdline": "python tw.py merge --apply", "start_time": old},
                {"pid": 812, "ppid": 811, "uid": os.getuid(), "cmdline": "python test_tw.py", "start_time": recent},
                {"pid": 813, "ppid": 1, "uid": os.getuid(), "cmdline": "python test_tw.py", "start_time": old}]
        fixture = self.dir / "health.json"
        fixture.write_text(json.dumps({"load": [0, 0, 0], "cpu_count": 2, "process_count": 4, "pipe_bytes": 65536, "processes": rows, "simulator_devices": []}))
        state = self.state(); state["prefs"]["check_timeout_minutes"] = 60
        with mock.patch.dict(os.environ, {"TEAMWORK_HEALTH_FIXTURE": str(fixture)}):
            health = tw._disk_health(state)
        self.assertEqual([x["pid"] for x in health["orphans"]], [813])

    def test_zh_guard_incomplete_reason_is_chinese(self):
        tw = self.twmod
        backup = self.home / "Backups"; backup.mkdir()
        state = self.state(); state["prefs"]["language"] = "zh"; self.save(state)
        def partial_guard(d, deadline=None, status=None):
            status["complete"] = False
            return set(), []
        with mock.patch.object(tw, "_disk_guards", side_effect=partial_guard):
            rows = tw._disk_candidates(self.dir / ".teamwork", {"measure_cache": {}, "health": {"simulator_devices": []}, "projects": []}, True, deadline=time.monotonic() + 3, measure=False)
        item = next(x for x in rows if x["path"] == str(backup))
        self.assertFalse(item["eligible"])
        self.assertIn("保护路径", item["ineligible_reason"])

    def test_new_preview_and_orphan_preferences_are_tunable(self):
        self.tw("prefs", "--set", "disk_preview_budget_s=4", "health_orphan_min_minutes=90")
        prefs = self.state()["prefs"]
        self.assertEqual(prefs["disk_preview_budget_s"], 4)
        self.assertEqual(prefs["health_orphan_min_minutes"], 90)
