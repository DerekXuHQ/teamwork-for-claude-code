try:
    from ._base import *
except ImportError:
    from _base import *


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

    def test_m4_req_link_copies_original_words_to_task(self):
        self.tw("req", "add", "keep the button blue")
        self.tw("req", "link", "R1", "T1")
        self.assertEqual(self.state()["tasks"]["T1"]["user_said"], "keep the button blue")

    def test_m6_reopened_request_waits_for_task_change_and_redelivers(self):
        self.tw("req", "add", "make button blue", "--tasks", "T1")
        self.tw("task", "set", "T1", "--status", "working")
        self.tw("task", "set", "T1", "--status", "review")
        self.assertEqual(self.req("R1")["status"], "delivered")
        self.tw("req", "set", "R1", "--status", "reported")
        self.tw("task", "set", "T1", "--status", "working")
        self.assertEqual(self.req("R1")["status"], "dispatched")
        self.assertTrue(self.req("R1").get("reopened_at"))
        self.tw("task", "set", "T1", "--status", "review")
        self.assertEqual(self.req("R1")["status"], "delivered")

    def test_m7_req_set_dispatched_delivers_completed_linked_task(self):
        self.tw("req", "add", "blue")
        self.tw("req", "link", "R1", "T1")
        self.tw("task", "set", "T1", "--status", "working")
        self.tw("task", "set", "T1", "--status", "review")
        st = self.state(); st["requests"]["R1"]["status"] = "open"
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        result = self.tw("req", "set", "R1", "--status", "dispatched")
        self.assertEqual(self.req("R1")["status"], "delivered")
        self.assertIn("delivered", result.stdout + result.stderr)

    def test_m8_dispatched_text_change_needs_user_words_and_keeps_history(self):
        self.tw("req", "add", "blue", "--tasks", "T1")
        denied = self.tw("req", "set", "R1", "--text", "green", ok=False)
        self.assertNotEqual(denied.returncode, 0)
        changed = self.tw("req", "set", "R1", "--text", "green", "--user-said", "make it green")
        self.assertEqual(self.req("R1")["text_history"][0]["text"], "blue")
        self.assertEqual(self.req("R1")["text"], "green")

    def test_m8_dispatched_text_cannot_be_cleared(self):
        self.tw("req", "add", "blue", "--tasks", "T1")
        before = self.req("R1").copy()
        for text in ("", "   "):
            denied = self.tw("req", "set", "R1", "--text", text, ok=False)
            self.assertIn("text cannot be empty", denied.stderr)
            self.assertEqual(self.req("R1"), before)

    def test_m9_drop_warns_about_active_linked_task(self):
        self.tw("req", "add", "stop work", "--tasks", "T1")
        self.tw("task", "set", "T1", "--status", "dispatched")
        self.tw("task", "set", "T1", "--status", "working")
        result = self.tw("req", "drop", "R1", "--user-said", "stop")
        self.assertIn("T1", result.stderr)

    def test_m10_req_link_remove_unlinks_task(self):
        self.tw("req", "add", "blue", "--tasks", "T1,T2")
        self.tw("req", "link", "R1", "--remove", "T2")
        self.assertEqual(self.req("R1")["tasks"], ["T1"])

    def test_m10_req_link_remove_rejects_ended_request_with_set_message(self):
        self.tw("req", "add", "blue", "--tasks", "T1")
        self.tw("req", "drop", "R1", "--user-said", "stop")
        remove = self.tw("req", "link", "R1", "--remove", "T1", ok=False)
        set_text = self.tw("req", "set", "R1", "--text", "green", ok=False)
        self.assertEqual(remove.stderr, set_text.stderr)

    def test_m15_init_force_requires_words_and_saves_fixed_backup(self):
        denied = self.tw("init", "demo", "--path", str(self.dir), "--force", ok=False)
        self.assertNotEqual(denied.returncode, 0)
        self.tw("init", "demo", "--path", str(self.dir), "--force", "--user-said", "replace the demo ledger")
        self.assertTrue((self.dir / ".teamwork" / "state.json.bak").is_file())

    def test_m17_resolved_thread_reactivation_prints_hint(self):
        self.tw("thread", "set", "ui", "--status", "resolved")
        result = self.tw("thread", "set", "ui", "--status", "working")
        self.assertIn("changed from resolved", result.stderr)

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
        self.assertEqual(self.state()["threads"]["ui"]["status"], "working")
        self.assertEqual(self.state()["threads"]["ui"]["session_id"], "s1")
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
        self.assertIn("按默认做了：choose A，不满意随时换回", board)
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

class LedgerBatch5(TwCase):
    def test_temporary_rule_travels_only_in_its_scope(self):
        self.tw("thread", "add", "ui", "--session", "s-ui")
        self.tw("task", "add", "T2", "--title", "x", "--owner", "ui"); self.tw("task", "add", "T3", "--title", "y", "--owner", "ui")
        self.tw("memory", "skip tests this round", "--temporary", "--scope", "T2")
        self.assertIn("skip tests this round", self.tw("task-msg", "T2").stdout)
        self.assertNotIn("skip tests this round", self.tw("task-msg", "T3").stdout)

    def test_issue_similarity_uses_longer_text_denominator(self):
        self.tw("issue", "add", "the build is red", "--owner", "")
        separate = self.tw("issue", "add", "the page is red in dark mode", "--owner", "")
        self.assertNotIn("已有 I1", separate.stdout)
        self.tw("issue", "add", "login button broken on safari", "--owner", "")
        duplicate = self.tw("issue", "add", "login button broken on safari 17", "--owner", "")
        self.assertIn("I3", duplicate.stdout)

    def test_issue_dedupe_request_kind_and_task_original_words(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("thread", "add", "api")
        self.tw("issue", "add", "登录按钮完全无法工作", "--owner", "api")
        duplicate = self.tw("issue", "add", "登录按钮无法工作", "--owner", "api")
        self.assertIn("已有 I1", duplicate.stdout)
        self.assertEqual(len(self.state()["issues"]), 1)
        self.tw("req", "add", "只是说明例子", "--kind", "example")
        self.assertNotEqual(self.tw("task", "add", "T1", "--title", "例子任务", "--req", "R1", ok=False).returncode, 0)
        self.tw("task", "add", "T2", "--title", "整理", "--user-said", "按原话保存")
        message = self.tw("task-msg", "T2").stdout
        self.assertIn("用户原话", message)
        self.assertTrue((self.dir / ".teamwork" / "tasks" / "T2.md").exists())
        self.tw("prefs", "--set", "language=zh")
        fixed = self.tw("issue", "set", "I1", "--status", "fixed-on-branch", "--ref", "tw/demo@abc123")
        self.assertIn("I1 修好了", fixed.stdout)

    def test_never_raise_preference_boundary_and_original_coverage(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("memory", "pricing", "--never-raise")
        self.assertNotEqual(self.tw("task", "add", "T1", "--title", "pricing task", ok=False).returncode, 0)
        self.tw("task", "add", "T1", "--title", "ordinary", "--user-said", "user words")
        task_msg = self.tw("task-msg", "T1").stdout
        self.assertIn("user words", task_msg)
        self.tw("memory", "偏好长板", "--pref", "--boundary", "不是不要短板")
        self.assertIn("边界：不等于不是不要短板", self.tw("memory", "--standing-list").stdout +
                      (self.dir / ".teamwork" / "MEMORY.md").read_text(encoding="utf-8"))
        state = self.state()
        state["tasks"]["T1"]["status"] = "done"
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        result = json.loads(self.tw("wrapup", "--json").stdout)
        self.assertTrue(any("user words" in x for x in result["sections"]["原话覆盖"]))

    def test_proposal_default_and_temporary_scope(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("ask", "--proposal", "改进可以撤回的显示")
        self.assertIn("提议：", self.tw("board").stdout)
        self.tw("ask", "--default", "1", "采用新显示", "--undoable")
        self.assertIn("待你确认", self.tw("board").stdout)
        self.tw("memory", "短期规则", "--temporary", "--scope", "T1")
        self.assertNotIn("短期规则", self.tw("memory", "--standing-list").stdout)
        self.tw("ask", "--external", "publish it")
        self.assertNotEqual(self.tw("ask", "--default", "2", "publish now", "--undoable", ok=False).returncode, 0)
        self.tw("ask", "prepare a release", "--undoable")
        rejected = self.tw("ask", "--default", "3", "release it", "--undoable", ok=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("--internal", rejected.stderr)

class FeatureLedger(TwCase):
    def test_feature_add_set_list_and_rebuild(self):
        self.tw("req", "add", "need feature")
        self.assertNotEqual(self.tw("feature", "add", ok=False).returncode, 0)
        self.tw("feature", "add", "Feature one", "--phase", "P1", "--req", "R1")
        self.assertNotEqual(self.tw("feature", "set", "F1", "--status", "landed", ok=False).returncode, 0)
        self.tw("feature", "set", "F1", "--status", "landed", "--evidence", "commit abc", "--where", "src/feature.py")
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


class T83ExistingMaterials(TwCase):
    def test_feature_backfill_preview_and_apply_use_task_commit(self):
        self.tw("thread", "add", "worker")
        self.tw("task", "add", "T80", "--title", "compression reminder")
        subprocess.run(["git", "-C", str(self.dir), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(self.dir), "config", "user.name", "t"], check=True)
        subprocess.run(["git", "-C", str(self.dir), "config", "user.email", "t@example.invalid"], check=True)
        artifact = self.dir / "compression.py"
        artifact.write_text("def remind_after_compression(): return True\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.dir), "add", "compression.py"], check=True)
        subprocess.run(["git", "-C", str(self.dir), "commit", "-qm", "T80: add compression reminder"], check=True)
        self.tw("feature", "add", "Compression reminder", "--task", "T80")
        ledger = self.state()
        ledger["features"]["F1"]["status"] = "landed"
        ledger["features"]["F1"]["evidence"] = "T80"
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(ledger), encoding="utf-8")
        preview = json.loads(self.tw("feature", "backfill", "--json").stdout)
        self.assertTrue(preview["preview"])
        self.assertEqual(preview["missing"], 1)
        self.assertNotIn("where", self.state()["features"]["F1"])
        self.tw("feature", "backfill", "--apply", "--json")
        self.assertIn("compression.py", self.state()["features"]["F1"]["where"])

    def test_feature_import_infers_landed_where_and_rejects_only_unmatched_rows(self):
        self.tw("task", "add", "T80", "--title", "compression reminder")
        subprocess.run(["git", "-C", str(self.dir), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(self.dir), "config", "user.name", "t"], check=True)
        subprocess.run(["git", "-C", str(self.dir), "config", "user.email", "t@example.invalid"], check=True)
        (self.dir / "compression.py").write_text("def remind_after_compression(): return True\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.dir), "add", "compression.py"], check=True)
        subprocess.run(["git", "-C", str(self.dir), "commit", "-qm", "T80: add compression reminder"], check=True)
        source = self.dir / "features.json"
        source.write_text(json.dumps([
            {"id": "F80", "name": "Compression reminder", "status": "landed", "tasks": ["T80"]},
            {"id": "F81", "name": "Unmatched feature", "status": "landed"},
            {"id": "F82", "name": "Planned feature", "status": "planned"},
        ]), encoding="utf-8")
        result = self.tw("feature", "import", "--file", str(source))
        self.assertIn("compression.py", self.state()["features"]["F80"]["where"])
        self.assertNotIn("F81", self.state()["features"])
        self.assertEqual(self.state()["features"]["F82"]["status"], "planned")
        self.assertIn("rejected row 2", result.stdout)

    def test_task_msg_paths_are_relative_or_home_tilde(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("thread", "add", "worker")
        self.tw("task", "add", "T80", "--title", "压缩后提醒", "--owner", "worker", "--accept", "压缩后提示一次")
        state = self.state()
        state["features"]["F1"] = {"name": "压缩后提醒", "status": "landed",
                                    "where": [str(self.dir / "src" / "compress.py"), "/Users/alice/private/提醒.md"]}
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        message = self.tw("task-msg", "T80").stdout
        self.assertIn("src/compress.py", message)
        self.assertIn("~/private/提醒.md", message)
        self.assertNotIn("/Users/alice", message)

    def test_git_grep_requires_multiple_terms_and_prefers_code_over_overview_docs(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("thread", "add", "worker")
        self.tw("task", "add", "T80", "--title", "压缩后提醒", "--owner", "worker", "--accept", "压缩后提示一次")
        subprocess.run(["git", "-C", str(self.dir), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(self.dir), "config", "user.name", "t"], check=True)
        subprocess.run(["git", "-C", str(self.dir), "config", "user.email", "t@example.invalid"], check=True)
        files = {
            "src/compress.py": "def 压缩后提醒(): return '压缩提醒'\n",
            "tests/test_compress.py": "def test_压缩后提醒(): assert '压缩提醒'\n",
            "README.zh.md": "压缩提醒功能说明\n",
            "docs/framework-zh.md": "压缩提醒框架概览\n",
            "docs/issues.md": "压缩提醒相关问题\n",
            "docs/single.md": "压缩\n",
        }
        for name, content in files.items():
            target = self.dir / name; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.dir), "add", *files], check=True)
        subprocess.run(["git", "-C", str(self.dir), "commit", "-qm", "add compression reference material"], check=True)
        message = self.tw("task-msg", "T80").stdout
        material = message.split("已有资料:\n", 1)[1].split("\nBrief:", 1)[0]
        self.assertLess(material.index("src/compress.py"), material.index("README.zh.md"))
        self.assertNotIn("docs/single.md", material)
        self.assertLessEqual(sum(path in material for path in ("README.zh.md", "framework-zh.md", "issues.md")), 1)

    def test_task_msg_material_and_check_msg_requires_it(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("thread", "add", "worker")
        self.tw("task", "add", "T80", "--title", "压缩后提醒", "--owner", "worker", "--accept", "压缩后提示一次")
        feature = self.state()["features"]
        feature["F1"] = {"name": "压缩后提醒", "status": "landed", "where": ["scripts/compress.py"], "tasks": ["T80"], "evidence": "docs/T80.md"}
        state = self.state()
        state["features"] = feature
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        message = self.tw("task-msg", "T80").stdout
        self.assertIn("已有资料:", message)
        self.assertIn("scripts/compress.py", message)
        self.assertIn("docs/T80.md", message)
        path = self.dir / "task.txt"
        path.write_text(message, encoding="utf-8")
        self.assertEqual(self.tw("check-msg", str(path)).returncode, 0)
        path.write_text(re.sub(r"(?ms)^已有资料:\n.*?(?=^Done when:)", "", message), encoding="utf-8")
        rejected = self.tw("check-msg", str(path), ok=False)
        self.assertIn("existing-material", rejected.stdout)
        path.write_text(re.sub(r"(?ms)^已有资料:\n.*?(?=^Done when:)", "已有资料:\n无\n", message), encoding="utf-8")
        rejected = self.tw("check-msg", str(path), ok=False)
        self.assertIn("查过哪些来源", rejected.stdout)

    def test_none_material_names_searched_sources(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("thread", "add", "worker")
        self.tw("task", "add", "T1", "--title", "qzxv unmatchable artifact", "--owner", "worker",
                "--accept", "the artifact is listed")
        message = self.tw("task-msg", "T1").stdout
        self.assertIn("已有资料:\n无（查过", message)
        path = self.dir / "task.txt"
        path.write_text(message, encoding="utf-8")
        self.assertEqual(self.tw("check-msg", str(path)).returncode, 0)


class T83ReadyAck(TwCase):
    def test_ready_reports_worker_without_existing_ack(self):
        self.tw("thread", "add", "worker", "--session", "worker-session", "--status", "working")
        result = json.loads(self.tw("ready").stdout)
        self.assertIn("worker", result["ack_missing_existing"])

    def test_sweep_ack_line_clears_ready_warning(self):
        self.tw("thread", "add", "worker", "--session", "worker-session", "--status", "working")
        sessions = self.dir / "sessions.json"
        sessions.write_text(json.dumps({"data": [{"thread": "worker", "session_id": "worker-session",
                                                   "status": "completed", "status_bucket": "review_ready"}]}))
        events = self.dir / "events.json"
        events.write_text(json.dumps({"data": [
            {"id": "a1", "type": "assistant", "data": {"assistant": {"text":
                "[tw:demo] ACK lead -> worker T1\n已有：src/feature.py；打算复用，理由：已经满足需求。"}}},
            {"id": "r1", "type": "result", "data": {"result": {"subtype": "success"}}}]}))
        self.tw("notice", "sweep", "--sessions", str(sessions), "--events", str(events), "--session", "worker-session")
        result = json.loads(self.tw("ready").stdout)
        self.assertEqual(result["ack_missing_existing"], [])

class T70FeatureVisibility(TwCase):
    def test_feature_import_move_reconcile_and_wrapup(self):
        source = self.dir / "features.json"
        source.write_text(json.dumps([{"id": "F7", "name": "Sign in", "phase": "MVP", "status": "planned"}]))
        self.tw("feature", "import", "--file", str(source))
        self.assertIn("unchanged 1", self.tw("feature", "import", "--file", str(source)).stdout)
        self.tw("feature", "move", "F7", "--to", "V2", "--why", "user reprioritized")
        self.assertIn("moved from MVP", self.tw("feature", "list").stdout)
        source.write_text(json.dumps([{"id": "F7", "name": "Sign in", "phase": "MVP", "status": "building"}]))
        diff = self.tw("feature", "reconcile", "--file", str(source), ok=False)
        self.assertEqual(diff.returncode, 1)
        self.assertIn("status differs", diff.stdout)
        self.assertIn("Features done", self.tw("wrapup").stdout)

    def test_feature_import_source_is_not_a_move_and_reimport_keeps_links(self):
        self.tw("req", "add", "Board shows time")
        source = self.dir / "features.json"
        source.write_text(json.dumps([{"id": "B7", "name": "Board time", "phase": "一期", "status": "building",
                                       "from": "R1、IL-13", "note": "doc B7"}], ensure_ascii=False), encoding="utf-8")
        self.tw("feature", "import", "--file", str(source))
        f = self.state()["features"]["B7"]
        self.assertEqual((f["source"], f["reqs"]), ("R1、IL-13", ["R1"]))
        self.assertNotIn("moved_from", f)
        board = self.tw("board").stdout
        self.assertNotIn("moved from R1", board)
        self.tw("task", "add", "T9", "--title", "board time")
        self.tw("feature", "set", "B7", "--task", "T9")
        self.tw("feature", "import", "--file", str(source))
        f = self.state()["features"]["B7"]
        self.assertEqual((f["tasks"], f["reqs"]), (["T9"], ["R1"]))
        for i in (2, 10):
            self.tw("feature", "add", f"x{i}", "--phase", "一期")
        self.assertRegex(self.tw("board").stdout, r"(?s)\*\*B7\*\*.*\*\*F1\*\*")

    def test_feature_reconcile_matches_id_and_card_all_is_untruncated(self):
        self.tw("feature", "add", "Original name", "--phase", "MVP")
        source = self.dir / "features.json"
        source.write_text(json.dumps([{"id": "F1", "name": "Renamed in file", "phase": "MVP", "status": "planned"}]))
        self.assertEqual(self.tw("feature", "reconcile", "--file", str(source)).returncode, 0)
        for n in range(30):
            self.tw("feature", "add", f"Feature {n}", "--phase", "MVP", "--status", "building")
        short = self.tw("card", "--kind", "status", "--head", "x").stdout
        full = self.tw("card", "--kind", "status", "--head", "x", "--all").stdout
        self.assertLessEqual(len(short.splitlines()), 25)
        self.assertIn("+24 more (TW card --all)", short)
        self.assertGreater(len(full.splitlines()), 25)
        self.assertIn("Feature 29", full)

    def test_selfcheck_records_probe_and_versions_without_promoting_saved_mode(self):
        self.tw("lead", "--session", "s1", "--mode", "auto", "--spawn-probe", "refused",
                "--opened-by", "another-session", "--note", "permission prompt")
        result = json.loads(self.tw("selfcheck", "--json").stdout)
        items = {row["item"]: row for row in result["items"]}
        self.assertEqual((items["mode"]["how"], items["mode"]["ok"]), ("not-tested", None))
        self.assertEqual((items["spawn"]["how"], items["spawn"]["ok"]), ("tested", False))
        self.assertEqual((items["opened_by"]["how"], items["opened_by"]["ok"]), ("recorded", None))
        versions = self.state()["versions"]
        self.assertEqual(len(versions), 1)
        self.assertRegex(next(iter(versions)), r"^[a-f0-9]{8}$")
        self.assertNotIn("hostname", json.dumps(versions))
        leads = self.tw("version", "--leads", "--root", str(self.dir)).stdout
        self.assertIn("These 1 leads need you to pick Auto once", leads)

    def test_selfcheck_reports_saved_mode_as_recorded(self):
        self.tw("lead", "--session", "s1", "--mode", "auto")
        result = json.loads(self.tw("selfcheck", "--json").stdout)
        mode = next(row for row in result["items"] if row["item"] == "mode")
        self.assertEqual((mode["how"], mode["ok"]), ("recorded", None))
        self.assertIn("not tested", mode["detail"])

    def test_standing_archive_grant_is_counted_automatically(self):
        self.tw("prefs", "--set", "standing_grants=archive", "--user-said", "archive completed seats")
        self.tw("thread", "add", "old", "--session", "old-session", "--status", "idle")
        self.tw("thread", "reseat", "old", "--session", "new-session", "--reason", "context full")
        self.tw("thread", "set", "old", "--status", "idle", "--from-status")
        self.tw("archive", "verify", "old-session", "--stopped", "get_session confirmed idle",
                "--config-dir", str(self.dir / "claude-config"))
        self.tw("archive", "done", "old-session")
        uses = self.state()["grant_uses"]
        self.assertEqual(sum(row["grant"] == "archive" for row in uses), 1)
        self.assertIn("standing grants: archive 1", self.tw("card", "--kind", "status", "--head", "archived").stdout)

    def test_card_always_ends_with_ledger_and_omits_empty_sections(self):
        out = self.tw("card", "--kind", "status", "--head", "x").stdout.rstrip().splitlines()
        self.assertIn("Ledger", out[-1])
        self.assertFalse(any(line.startswith("## ") for line in out[1:]))

    def test_done_card_has_phase_feature_sections_and_grant_counts(self):
        self.tw("feature", "add", "One", "--phase", "MVP")
        self.tw("feature", "add", "Two", "--phase", "MVP")
        self.tw("feature", "set", "F1", "--status", "landed", "--evidence", "merged", "--where", "src/one.py")
        self.tw("prefs", "--set", "standing_grants=schedule", "--user-said", "schedule low-priority work")
        self.tw("prefs", "--grant-use", "schedule", "--what", "run weekly reports")
        out = self.tw("card", "--kind", "done", "--head", "delivered").stdout
        self.assertIn("Features done", out)
        self.assertIn("Features not done yet", out)
        self.assertIn("standing grants: schedule 1", out)
        self.assertIn("Ledger", out.splitlines()[-1])

    def test_docs_refreshed_and_version_notice_surface(self):
        self.tw("docs", "set", "board", "--link", "https://example.invalid/board")
        self.assertIn("refreshed", self.tw("docs", "list").stdout)
        old = self.state()
        self.tw("thread", "add", "old")
        old = self.state()
        old["threads"]["old"].update(status="working", session_id="old-session")
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(old))
        (self.dir / ".teamwork" / "session-versions.jsonl").write_text(json.dumps({
            "session": "old-session", "version": "release r20261120", "at": "2026-11-20T00:00:00+00:00"}) + "\n")
        notice = self.tw("version", "--notify").stdout
        self.assertIn("one side is a live clone", notice)
        compared=json.loads(self.tw("version","--sessions").stdout)
        self.assertEqual(compared["incomparable_sessions"][0]["thread"],"old")

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
        self.assertEqual((out["title"], out["tags"]), (f"[{short}] ★lead · lead", [f"teamwork:{short}", "teamwork-thread:lead"]))

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
        skill = (REPO / "SKILL.md").read_text()
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
        skill = (REPO / "SKILL.md").read_text()
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
            text = clean_task_fixture(text)
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
        doc = (REPO / "references" / "messages.md").read_text()
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
        self.tw("task", "add", "T1", "--title", "merge fixture")
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


if __name__ == "__main__":
    unittest.main()
