try:
    from ._base import *
except ImportError:
    from _base import *


class BoardIdleIsStandby(TwCase):
    """Seated idle threads are on standby, not finished: the overview must not list them as completed."""
    def test_idle_threads_are_standby_not_completed(self):
        self.tw("prefs", "--set", "language=zh")
        for n in ("ui", "api", "old"):
            self.tw("thread", "add", n, "--session", "session_" + n)
        self.tw("thread", "set", "ui", "--status", "idle")
        self.tw("thread", "set", "api", "--status", "idle")
        self.tw("thread", "set", "old", "--status", "resolved")
        board = self.tw("board").stdout
        overview = board.split("## 总览", 1)[1].split("\n## ", 1)[0]
        self.assertNotRegex(overview, r"已完成[^\n]*ui")
        self.assertRegex(overview, r"待命 2 个[^\n]*ui, api")
        expanded = self.tw("board", "--all").stdout.split("## 总览", 1)[1].split("\n## ", 1)[0]
        self.assertRegex(expanded, r"待命 2 个[^\n]*ui, api")
        self.assertRegex(expanded, r"已完成 1 个：old")
        threads = self.tw("threads").stdout
        self.assertNotRegex(threads, r"已完成[^\n]*ui")
        self.assertIn("待命 2 个", threads)

class BoardBatch5(TwCase):
    def test_ready_json_stays_on_stdout_and_notice_is_stderr(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("thread", "add", "ui")
        self.tw("thread", "add", "api")
        for tid, owner in (("T1", "ui"), ("T2", "api")):
            self.tw("task", "add", tid, "--title", tid, "--owner", owner)
        self.tw("plan", "approve", "--user-said", "approved")
        result = self.tw("ready")
        self.assertIsInstance(json.loads(result.stdout), dict)
        self.assertIn("接下来要开 2 个线程", result.stderr)

    def test_board_collisions_reviews_and_folded_seats(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("thread", "add", "ui")
        self.tw("thread", "add", "api")
        self.tw("task", "add", "T1", "--title", "one", "--owner", "ui", "--touches", "src/x")
        self.tw("task", "add", "T2", "--title", "two", "--owner", "api", "--touches", "src/x/y")
        board = self.tw("board").stdout
        self.assertIn("撞车 / 等待", board)
        self.assertIn("都要改", board)
        self.tw("thread", "add", "old")
        self.tw("thread", "set", "old", "--status", "resolved")
        self.assertIn("已折叠：3 个（ui, api, old）", self.tw("board").stdout)
        self.assertIn("old", self.tw("roster").stdout)
        expanded = self.tw("board", "--all").stdout
        self.assertIn("old", expanded)
        self.assertNotIn("已折叠：", expanded)

    def test_merge_waiting_rows_and_ready_collision_hint(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("thread", "add", "ui")
        self.tw("thread", "add", "api")
        self.tw("task", "add", "T1", "--title", "one", "--owner", "ui", "--touches", "src/a")
        self.tw("task", "add", "T2", "--title", "two", "--owner", "api", "--touches", "src/a/b")
        state = self.state()
        state["tasks"]["T1"].update(status="review", checks="pass", review={"result": "pass", "at": "2026-11-22T00:00:00+00:00"}, review_rounds=2)
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertIn("T1", self.tw("board").stdout)
        ready = json.loads(self.tw("ready").stdout)
        self.assertTrue(ready["waiting_to_merge"])
        self.assertEqual(ready["collision_hint"], "先排顺序")

    def test_latest_document_link_only(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("docs", "set", "board", "--link", "https://example.invalid/old")
        self.tw("docs", "set", "board", "--link", "https://example.invalid/new")
        current = self.tw("docs", "list").stdout
        self.assertIn("new", current)
        self.assertNotIn("old", current)
        history = self.tw("docs", "list", "--all").stdout
        self.assertIn("旧版，别用", history)
        self.tw("thread", "add", "api")
        self.tw("task", "add", "T1", "--title", "delivery", "--owner", "api")
        self.tw("deliverable", "add", "artifact", "--task", "T1", "--link", "https://example.invalid/v1")
        self.tw("deliverable", "add", "artifact v2", "--task", "T1", "--link", "https://example.invalid/v2")
        self.assertEqual(self.state()["deliverables"][0]["history"][0]["link"], "https://example.invalid/v1")

    def test_receipt_big_picture_warning(self):
        self.tw("thread", "add", "api")
        message = self.dir / "receipt.txt"
        message.write_text("[tw:demo] ACK lead -> api -\n[Big picture: context]\n", encoding="utf-8")
        out = self.tw("check-msg", str(message), "--json").stdout
        self.assertIn("big-picture-receipt", out)

    def test_missing_feature_requires_complete_test_word(self):
        self.tw("prefs", "--set", "language=zh")
        message = self.dir / "test-note.txt"
        message.write_text("Not supported; this measurement is not a test.", encoding="utf-8")
        result = self.tw("check-msg", str(message), ok=False)
        self.assertIn("先说清怎么测的", result.stderr)
        message.write_text("Not supported; reproduced on the current build.", encoding="utf-8")
        self.assertNotIn("unsupported-without-test", self.tw("check-msg", str(message), ok=False).stderr)

class Batch5Round2(TwCase):
    def test_never_raise_refuses_cleanly_everywhere(self):
        self.tw("prefs", "--set", "language=en")
        self.tw("memory", "pricing", "--never-raise")
        self.tw("thread", "add", "ui", "--session", "s-ui")
        self.tw("task", "add", "T1", "--title", "page", "--owner", "ui")
        for args in (("task", "add", "T2", "--title", "x", "--brief", "about pricing"), ("task", "set", "T1", "--title", "pricing again"),
                     ("req", "add", "change pricing"), ("promise", "add", "pricing promise"), ("task", "add", "T3", "--title", "pricing task")):
            r = self.tw(*args, ok=False)
            self.assertNotEqual(r.returncode, 0, args)
            self.assertNotIn("Traceback", r.stderr, args)
            self.assertIn("never-raise", r.stderr, args)

    def test_beyond_task_cannot_be_dispatched_by_status_and_examples_stay_out(self):
        self.tw("thread", "add", "ui", "--session", "s-ui")
        self.tw("task", "add", "T1", "--title", "page", "--owner", "ui", "--beyond", "also redo the header")
        self.assertNotEqual(self.tw("task", "set", "T1", "--status", "dispatched", ok=False).returncode, 0)
        self.tw("req", "add", "like the old site", "--kind", "example")
        self.assertNotEqual(self.tw("req", "set", "R1", "--status", "open", ok=False).returncode, 0)

    def test_default_refused_for_outward_steps_and_due_hint(self):
        self.tw("prefs", "--set", "language=en"); self.tw("prefs", "--set", "default_after_minutes=0")
        self.tw("ask", "merge it into main and tell everyone?")
        r = self.tw("ask", "--default", "1", "merged", "--undoable", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.tw("ask", "use layout A or B?")
        self.assertIn("default time reached", self.tw("board").stdout)
        self.tw("ask", "--default", "2", "layout A", "--undoable")

    def test_parked_items_show_on_board_and_replaced_archive_has_no_broadcast(self):
        cfg = str(self.dir.parent / (self.dir.name + "-cfg"))
        self.tw("prefs", "--set", "language=en")
        self.tw("thread", "add", "api", "--session", "sid", "--status", "idle"); self.tw("thread", "set", "api", "--disconnected")
        self.tw("task", "add", "T1", "--title", "finish API", "--owner", "api")
        self.tw("archive", "verify", "sid", "--stopped", "stopped", "--park", "user decides", "--config-dir", cfg)
        self.assertIn("parked at archive", self.tw("board").stdout)
        self.tw("thread", "add", "ui", "--session", "old1")
        self.tw("thread", "reseat", "ui", "--session", "new1")
        self.tw("thread", "set", "ui", "--status", "working", "--from-status")
        self.tw("archive", "verify", "old1", "--stopped", "old1 idle", "--config-dir", cfg)
        done = self.tw("archive", "done", "old1")
        self.assertNotIn("now owned by", done.stdout + done.stderr)
        json.loads(done.stdout)

    def test_unsuitable_model_moves_up_via_cli(self):
        self.tw("thread", "add", "ui", "--session", "s-ui")
        self.tw("task", "add", "T1", "--title", "page", "--owner", "ui", "--route", "fable", "--work-kind", "code")
        for r in ("redo", "ok", "redo"):
            self.tw("model-log", "--model", "fable", "--type", "code", "--result", r, "--task", "T1")
        msg = self.tw("task-msg", "T1").stdout
        self.assertIn("Route/Model: opus", msg)
        self.tw("task", "add", "T2", "--title", "x", "--owner", "ui", "--route", "opus", "--work-kind", "code")
        for r in ("redo", "ok", "redo"):
            self.tw("model-log", "--model", "opus", "--type", "code", "--result", r, "--task", "T2")
        self.assertIn("Route/Model: opus", self.tw("task-msg", "T2").stdout)  # nothing higher: kept, not refused

class Batch5Round3(TwCase):
    def test_en_promise_heading_and_safe_task_ids(self):
        self.tw("prefs", "--set", "language=en"); self.tw("promise", "add", "send a summary")
        self.assertIn("## Open promises", self.tw("board").stdout)
        self.tw("thread", "add", "ui", "--session", "s-ui")
        memory = (self.dir / ".teamwork" / "MEMORY.md").read_text(encoding="utf-8")
        for bad in ("../MEMORY", "a/b"):
            r = self.tw("task", "add", bad, "--title", "x", "--owner", "ui", ok=False)
            self.assertNotEqual(r.returncode, 0); self.assertNotIn("Traceback", r.stderr)
        self.assertEqual((self.dir / ".teamwork" / "MEMORY.md").read_text(encoding="utf-8"), memory)

    def test_outward_word_forms_refused_inside_words_allowed(self):
        outward = ("pushing to main now?", "deploying to prod?", "部署到生产？", "把分支合到 main？", "提PR吗", "tell the client?", "deleting old rows?")
        inside = ("pushing layout B further?", "customerId as the field name?")
        for q in outward + inside: self.tw("ask", q)
        for n in range(1, len(outward) + 1):
            self.assertNotEqual(self.tw("ask", "--default", str(n), "yes", "--undoable", ok=False).returncode, 0, outward[n - 1])
        for n in range(len(outward) + 1, len(outward) + len(inside) + 1):
            self.tw("ask", "--default", str(n), "yes", "--undoable")

    def test_external_question_ignores_internal_and_gets_no_due_hint(self):
        self.tw("prefs", "--set", "language=en"); self.tw("prefs", "--set", "default_after_minutes=0")
        self.tw("ask", "send the invoice?", "--external")
        self.assertNotIn("default time reached", self.tw("board").stdout)
        self.assertNotEqual(self.tw("ask", "--default", "1", "go", "--undoable", "--internal", "trust me", ok=False).returncode, 0)

    def test_takeover_waiting_matches_board_and_wrapup_skips_remarks(self):
        cfg = str(self.dir.parent / (self.dir.name + "-cfg"))
        self.tw("prefs", "--set", "language=en")
        self.tw("thread", "add", "api", "--session", "sid", "--status", "idle"); self.tw("thread", "set", "api", "--disconnected")
        self.tw("task", "add", "T1", "--title", "finish API", "--owner", "api")
        self.tw("archive", "verify", "sid", "--stopped", "stopped", "--park", "user decides", "--config-dir", cfg)
        self.assertNotIn("Waiting for you: none", self.tw("opening", "--takeover").stdout)
        self.tw("req", "add", "nice weather", "--remark")
        self.assertNotIn("nice weather", self.tw("wrapup").stdout)

    def test_example_requests_cannot_be_linked_elsewhere(self):
        self.tw("thread", "add", "ui", "--session", "s-ui"); self.tw("task", "add", "T1", "--title", "page", "--owner", "ui")
        self.tw("req", "add", "like the old site", "--kind", "example")
        self.assertNotEqual(self.tw("task", "set", "T1", "--req", "R1", ok=False).returncode, 0)
        self.assertNotEqual(self.tw("feature", "add", "home", "--req", "R1", ok=False).returncode, 0)

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
        self.assertNotIn("## Done", out)  # empty sections are omitted
        self.assertIn("F1 Old feature", out.split("## Features done")[1])  # current phase feature inventory is not time-filtered
        self.assertNotIn("first thing", out)
        for head in ("## In progress", "## Waiting on you", "## Features done", "## Features not done yet"):
            self.assertIn(head, out)
        doing = out.split("## In progress")[1].split("## Waiting on you")[0]
        self.assertIn("F2 New feature", doing)
        self.assertIn("T1 build it", doing)
        waiting = out.split("## Waiting on you")[1]
        self.assertIn("R2 second thing", waiting)
        self.assertIn("which colour (going ahead with the default: blue)", waiting)
        self.assertEqual(out.splitlines()[-1],
                         "Ledger +2/−1 · waiting on you: 2 · GPT did 1 (T1 code) · https://example.test/pr")  # D1: names the work
        everything = json.loads(self.tw("card", "--kind", "status", "--head", "x", "--json").stdout)
        self.assertEqual(sorted(everything), ["doing", "done", "features_done", "features_not_done", "head", "kind", "ledger", "waiting"])
        self.assertIn("F1 Old feature", everything["done"])
        self.assertIn("R1 first thing", everything["done"])

    def test_ledger_line_only_when_asked_for(self):
        self.tw("req", "add", "one")
        line = "Ledger +"
        self.assertIn(line, self.tw("card", "--kind", "status", "--head", "h").stdout)  # always on every card
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
        self.assertIn("自 ", out)
        self.assertNotIn("## 做完了", out)
        self.assertNotIn("（无）", out)
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
        i = next(n for n, x in enumerate(lines) if x.startswith(f"- **{rid}**") or x.startswith(f"- {rid}:"))
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
        self.assertEqual(after["prefs"]["ledger_line_every"], "report")
        self.assertEqual(self.tw("board").stdout, board)

class ThreadView(TwCase):
    def test_waiting_stuck_folded_overview_and_notice_dedupe(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("thread", "add", "core", "--session", "sid", "--status", "waiting")
        self.tw("thread", "set", "core", "--waiting-for", "permission", "--summary", "批准命令")
        self.assertIn("等你点批准", self.tw("threads").stdout)
        self.tw("thread", "set", "core", "--status", "working", "--stuck-on", "cleanup")
        self.assertNotIn("waiting_for", self.state()["threads"]["core"])
        self.assertIn("清理临时文件", self.tw("threads").stdout)
        self.tw("notice", "add", "--thread", "core", "--session", "sid", "--ref", "r1", "--text", "一轮结束")
        self.tw("notice", "add", "--thread", "core", "--session", "sid", "--ref", "r1", "--text", "duplicate")
        self.assertEqual(len(self.state()["notices"]), 1)
        self.assertIn("一轮结束", self.tw("notice", "list").stdout)
        self.tw("notice", "done", "--thread", "core")
        self.assertEqual(json.loads(self.tw("notice", "list", "--json").stdout), [])

    def test_notice_rejects_other_session_and_nudge_rate_limit(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("thread", "add", "ui", "--session", "local", "--status", "working")
        self.assertIn("不是本项目的会话", self.tw("notice", "add", "--thread", "ui", "--session", "foreign", "--ref", "x", ok=False).stderr)
        self.tw("nudge", "ui")
        self.assertIn("已经催过一次", self.tw("nudge", "ui", ok=False).stderr)

    def test_urgent_defaults_and_rebuild_persists_notices(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("thread", "add", "ui", "--session", "s1")
        self.tw("task", "add", "T1", "--owner", "ui", "--urgent-minutes", "2")
        self.assertEqual(self.state()["tasks"]["T1"]["urgent_minutes"], 2)
        self.tw("task", "set", "T1", "--status", "dispatched")
        self.env["TW_NOW"] = (dt.datetime.now().astimezone() + dt.timedelta(minutes=3)).isoformat()
        self.assertEqual(json.loads(self.tw("ready").stdout)["urgent_unacked"][0]["task"], "T1")
        self.assertIn("收回再改派", self.tw("open").stderr)
        self.tw("notice", "add", "--thread", "ui", "--session", "s1", "--ref", "z")
        self.tw("rebuild")
        self.assertEqual(len(self.state()["notices"]), 1)

class TestNav(TwCase):
    def setUp(self):
        super().setUp()
        self.tw("lead", "--session", "session_test_lead")
        self.tw("thread", "add", "ui", "--session", "session_test_ui", "--status", "working", "--summary", "build screen")

    def sessions(self, rows, wrapped=False):
        path = self.dir / "sessions.json"
        value = {"ccr": {"data": rows}} if wrapped else rows
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def nav_json(self, rows):
        return json.loads(self.tw("nav", "--sessions", str(self.sessions(rows)), "--json").stdout)

    def test_primary_markdown_lead_first_and_other_project_excluded(self):
        rows = [
            {"id": "session_test_lead", "title": "old lead title", "tags": ["teamwork:DEMO"]},
            {"id": "session_test_ui", "title": "[DEMO] ui · screen", "tags": ["teamwork:DEMO", "teamwork-thread:ui"],
             "status_bucket": "WORKING", "updated_at": "2026-11-24T12:34:00Z", "task_summary": "build page"},
            {"id": "session_other", "title": "[OTHER] api", "tags": ["teamwork:OTHER"]},
        ]
        result = self.tw("nav", "--sessions", str(self.sessions(rows)))
        out = result.stdout
        self.assertIn("docs set nav", result.stderr)  # no Doc yet: told to create and record one
        first = next(line for line in out.splitlines() if line.startswith("- "))
        self.assertIn("[old lead title](https://claude.ai/code/session_test_lead)", first)
        self.assertIn("[ui · screen](https://claude.ai/code/session_test_ui) — build page", out)
        self.assertNotIn("要你", out); self.assertNotIn("You:", out)
        self.assertNotIn("session_other", out)
        self.assertLess(out.index("session_test_lead"), out.index("session_test_ui"))

    def test_parent_chain_test_group_live_bucket_and_archive_accounting(self):
        rows = [
            {"id": "session_test_lead", "title": "[DEMO] ★lead · lead", "tags": ["teamwork:DEMO"]},
            {"id": "session_test_ui", "title": "[DEMO] ui", "tags": ["teamwork:DEMO", "teamwork-thread:ui"],
             "status_bucket": "BLOCKED", "post_turn_summary": {"needs_action": "approve build"}},
            {"id": "session_test_child", "title": "helper", "parent_session_id": "session_test_ui"},
            {"id": "session_test_test", "title": "[DEMO] （可删）test", "parent_session_id": "session_test_child",
             "tags": ["teamwork-test:can-delete"], "status_bucket": "COMPLETED"},
            {"id": "session_test_archived", "title": "[demo] old", "tags": ["teamwork:demo"],
             "session_status": "SESSION_STATUS_ARCHIVED"},
        ]
        data = self.nav_json(rows)
        self.assertEqual(data["groups"]["waiting"][0]["id"], "session_test_ui")
        self.assertEqual(data["groups"]["test"][0]["id"], "session_test_test")
        self.assertTrue(data["groups"]["test"][0]["name"].startswith("↳ ui:"))
        self.assertEqual(data["archived_count"], 1)
        self.assertNotIn("session_test_archived", json.dumps(data["groups"]))
        self.assertEqual(data["groups"]["working"], [])

    def test_old_seat_candidate_title_fix_docs_and_json_shape(self):
        rows = [
            {"id": "session_test_lead", "title": "incorrect", "tags": ["teamwork:demo"]},
            {"id": "session_test_ui", "title": "[demo] ui", "tags": ["teamwork:demo", "teamwork-thread:ui"], "status_bucket": "WORKING"},
            {"id": "session_test_old", "title": "[demo] ui old", "tags": ["teamwork:demo", "teamwork-thread:ui"], "status_bucket": "COMPLETED"},
        ]
        data = self.nav_json(rows)
        self.assertEqual(set(data), {"lead", "groups", "archived_count", "archive_candidates", "title_fixes"})
        self.assertEqual(data["archive_candidates"][0]["id"], "session_test_old")
        self.assertEqual(data["title_fixes"][0]["suggested"], "[demo] ★lead · lead")
        self.tw("docs", "set", "nav", "--link", "https://claude.ai/doc/nav")
        self.assertEqual(self.state()["docs"]["nav"]["link"], "https://claude.ai/doc/nav")
        self.assertIn("Session directory", self.tw("docs", "list").stdout)
        rerun = self.tw("nav", "--sessions", str(self.sessions(rows)))
        self.assertIn("https://claude.ai/doc/nav", rerun.stderr)  # rerun points at the same Doc

    def test_real_list_shape_old_seat_hidden_parent_from_state_resolved_only_tick(self):
        self.tw("thread", "add", "field", "--session", "session_test_field", "--status", "idle")
        self.tw("thread", "add", "done1", "--session", "session_test_done", "--status", "resolved")
        rows = [
            {"id": "session_test_ui", "title": "[demo] ui · new", "tags": ["teamwork:demo", "teamwork-thread:ui"], "status_bucket": "SESSION_STATUS_BUCKET_WORKING", "updated_at": "2026-11-24T10:00:00Z"},
            {"id": "session_test_ui_old", "title": "[demo] ui · old", "tags": ["teamwork:demo", "teamwork-thread:ui"], "status_bucket": "SESSION_STATUS_BUCKET_BLOCKED", "post_turn_summary": {"needs_action": "go"}},
            {"id": "session_test_t1", "title": "[demo] t1 · 实测（可删）", "tags": ["teamwork:demo", "teamwork-thread:field-test"], "parent_session_id": "session_test_field", "status_bucket": "SESSION_STATUS_BUCKET_COMPLETED"},
        ]
        data = self.nav_json(rows)
        self.assertEqual(data["groups"]["waiting"], [])  # an old seat is only an archive candidate
        self.assertIn("session_test_ui_old", [x["id"] for x in data["archive_candidates"]])
        self.assertEqual(data["groups"]["test"][0]["name"], "↳ field: t1 · 实测（可删）")
        ticks = [x.get("thread") for x in data["title_fixes"] if x.get("thread")]
        self.assertEqual(ticks, ["done1"])  # idle = standby: no ✓
        rows.append({"id": "session_test_lead", "title": "[demo] ★lead · lead", "status_bucket": "SESSION_STATUS_BUCKET_WORKING"})
        out = self.tw("nav", "--sessions", str(self.sessions(rows))).stdout
        self.assertNotIn("SESSION_STATUS", out)
        self.assertIn("— working", next(line for line in out.splitlines() if line.startswith("- ★")))

    def test_review_fixes_zh_none_url_other_project_child_seatless_thread_bad_file(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("thread", "add", "api", "--status", "working")  # no session id yet
        self.tw("thread", "add", "gone", "--status", "resolved")  # resolved, never seated
        rows = [
            {"id": "session_test_api", "title": "[demo] api", "tags": ["teamwork:demo", "teamwork-thread:api"], "status_bucket": "SESSION_STATUS_BUCKET_FAILED"},
            {"id": "session_test_other", "title": "[OTHER] x", "tags": ["teamwork:OTHER"], "parent_session_id": "session_test_ui"},
        ]
        result = self.tw("nav", "--sessions", str(self.sessions(rows)))
        out = result.stdout
        self.assertIn("会话目录", out); self.assertIn("等你处理", out)
        self.assertIn("(https://claude.ai/code/session_test_api)", out.split("**等你处理**")[1])  # only seat is not an "old seat"
        self.assertNotIn("session_test_other", out)
        self.assertNotIn("(None)", out)
        self.assertIn("更新已有" if "docs" in self.state() else "docs set nav", result.stderr)
        bad = self.tw("nav", "--sessions", str(self.dir / "missing.json"), ok=False)
        self.assertNotEqual(bad.returncode, 0); self.assertNotIn("Traceback", bad.stderr)

    def test_all_projects_row_uses_state_and_directory_link(self):
        self.tw("docs", "set", "nav", "--link", "https://claude.ai/doc/demo")
        out = self.tw("nav", "--all-projects", "--root", str(self.dir)).stdout
        self.assertIn("[demo ★lead](https://claude.ai/code/session_test_lead)", out)
        self.assertIn("waiting on you 0 · working 1", out)
        self.assertIn("https://claude.ai/doc/demo", out)

class SiblingProjects(TwCase):
    def test_broken_sibling_state_does_not_break_opening(self):
        sibling = Path(tempfile.mkdtemp(dir=self.dir.parent)) / ".teamwork"
        sibling.mkdir()
        for broken in ("[]", '{"prefs": []}', '{"prefs": {"policy_said": []}}'):
            (sibling / "state.json").write_text(broken, encoding="utf-8")
            self.assertEqual(self.tw("opening", "--json").returncode, 0)

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

class SweepBlockedWhileRunning(TwCase):
    """A real list_sessions row can say status running and bucket blocked: the approval wait must stay on the board."""
    def test_blocked_running_row_keeps_wait(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("thread", "add", "api", "--session", "session_api")
        rows = {"data": [{"id": "session_api", "title": "[TP] api · x", "status": "running", "status_bucket": "blocked",
                          "updated_at": "2026-11-24T20:01:00Z", "tags": ["teamwork-thread:api"],
                          "post_turn_summary": {"needs_action": "approve a permission prompt"}}], "has_more": False}
        f = self.dir / "s.json"; f.write_text(json.dumps(rows), encoding="utf-8")
        self.assertIn("api 在等你批准", self.tw("notice", "sweep", "--sessions", str(f)).stdout)
        self.assertEqual(self.state()["threads"]["api"].get("waiting_for"), "permission")
        self.assertIn("api", self.tw("board").stdout.split("## 总览", 1)[1].split("**进行中**")[0])
        self.assertIn("未记录", self.tw("sprint", "stop-all").stdout)
        self.assertNotIn("none recorded", self.tw("sprint", "stop-all").stdout)

class ThreadsAreSessions(TwCase):
    """T24: a thread is a create_session session, rc by default; cloud needs a reason; one language all the way down."""

    def raw(self, *args):
        return self.tw(*args, ok=False, walk=False)

    def test_subagent_ids_never_hold_a_seat(self):
        for sid in ("a0aeaf22707df6481", "agent-zorblax", "subagent:quixbar", "toolu_01Quixbar", "task_7",
                    "subagent.1", "agent 7", "general_purpose", "toolu01ABC"):
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
        self.assertNotEqual(self.raw("ask", "again", "--from", "docs", "--need-thread", "search").returncode, 0)
        self.tw("ask", "again", "--from", "docs", "--need-thread", "search", "--again")
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


if __name__ == "__main__":
    unittest.main()
