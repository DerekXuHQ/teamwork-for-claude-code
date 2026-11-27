try:
    from ._base import *
except ImportError:
    from _base import *


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

class ArchiveAndSeats(TwCase):
    def setUp(self):
        super().setUp()
        self.tw("prefs", "--set", "language=zh")

    def test_replaced_session_archive_verification_and_rebuild(self):
        self.tw("thread", "add", "ui", "--session", "old", "--status", "idle")
        result = self.tw("thread", "reseat", "ui", "--session", "new", "--reason", "context full")
        self.assertIn("ROSTER", result.stderr)
        self.tw("thread", "set", "ui", "--status", "idle", "--from-status")
        candidates = json.loads(self.tw("archive", "candidates", "--json").stdout)
        self.assertEqual((candidates[0]["session_id"], candidates[0]["category"]), ("old", "replaced"))
        self.assertNotEqual(self.tw("archive", "done", "old", ok=False).returncode, 0)
        self.tw("archive", "verify", "old", "--stopped", "get_session old: idle; final event 12:00", "--config-dir", str(self.dir / "claude-config"))
        self.tw("archive", "done", "old")
        failed_check = self.tw("archive", "check", "old", "--not-archived")
        self.assertIn("已知问题", failed_check.stderr)
        self.tw("rebuild")
        rec = self.state()["archive"]["old"]
        self.assertEqual(rec["check"], "failed")
        self.assertEqual(rec["history"][-1]["evidence"], "get_session old: idle; final event 12:00")


    def test_disconnected_requires_user_decision_and_clears_on_report(self):
        self.tw("thread", "add", "api", "--session", "sid", "--status", "idle")
        self.tw("thread", "set", "api", "--disconnected")
        rows = json.loads(self.tw("archive", "candidates", "--json").stdout)
        self.assertEqual(rows[0]["category"], "disconnected")
        self.assertIn("等你定", rows[0]["reason"])
        self.tw("archive", "verify", "sid", "--stopped", "idle in get_session", "--config-dir", str(self.dir / "claude-config"))
        self.assertIn("--user-said", self.tw("archive", "done", "sid", ok=False).stderr)
        self.tw("archive", "done", "sid", "--user-said", "归档这条断线会话")
        self.tw("thread", "set", "api", "--from-status")
        self.assertFalse(self.state()["threads"]["api"].get("disconnected"))

    def test_idle_old_is_shown_first_and_requires_user_decision(self):
        self.tw("thread", "add", "docs", "--session", "sid", "--status", "idle")
        self.tw("thread", "set", "docs", "--from-status")
        self.env["TW_NOW"] = (dt.datetime.now().astimezone() + dt.timedelta(days=4)).isoformat()
        rows = json.loads(self.tw("archive", "candidates", "--json").stdout)
        self.assertEqual(rows[0]["category"], "idle-old")
        self.assertIn("先列给用户看", rows[0]["reason"])
        self.tw("archive", "verify", "sid", "--stopped", "idle; final event timestamp checked", "--config-dir", str(self.dir / "claude-config"))
        self.assertIn("--user-said", self.tw("archive", "done", "sid", ok=False).stderr)
        self.tw("archive", "done", "sid", "--user-said", "先给我看过，确认归档")

    def test_stale_deliverable_review_guard_and_spawn_refused(self):
        self.tw("thread", "add", "api", "--session", "s1")
        self.tw("task", "add", "T1", "--owner", "api", "--accept", "works")
        self.tw("plan", "approve", "--user-said", "approved")
        self.tw("task", "set", "T1", "--status", "dispatched")
        self.tw("task", "set", "T1", "--status", "working")
        stale_review = self.tw("task", "set", "T1", "--status", "review", "--dispatch-v", "0", ok=False)
        self.assertIn("迟到的旧结果", stale_review.stderr)
        stale = json.loads(self.tw("deliverable", "add", "old result", "--task", "T1", "--dispatch-v", "0").stdout)
        self.assertEqual((stale["status"], stale["superseded_by"]), ("stale", "v1"))
        self.assertIn("旧版本的结果，已标过期", self.tw("deliverable", "add", "late", "--task", "T1", "--dispatch-v", "0").stderr)
        self.assertIn("请在 lead 会话里", self.tw("spawn-refused", "api").stdout)
        self.assertEqual(len(self.state()["spawn_refusals"]), 1)
        self.tw("rebuild")
        self.assertEqual(self.state()["deliverables"][0]["status"], "stale")
        self.assertEqual(len(self.state()["spawn_refusals"]), 1)

    def test_handover_prompt_and_selfcheck_chinese(self):
        self.tw("thread", "add", "ui", "--session", "s1")
        prompt = self.tw("handover", "ui", "--prompt").stdout
        self.assertIn("--- HANDOVER ---", prompt)
        self.assertIn("# teamwork thread protocol", prompt)
        self.assertIn("完整输出，不要只给文件位置", self.tw("handover", "ui").stdout)
        check = json.loads(self.tw("selfcheck", "--json").stdout)
        self.assertIn("开会话的实测情况", check["session_opening"])

class ArchiveBatch5(TwCase):
    def test_repeated_parked_archive_verify_reuses_pack_and_notice(self):
        self.tw("thread", "add", "api", "--session", "sid", "--status", "idle")
        self.tw("thread", "set", "api", "--disconnected")
        self.tw("task", "add", "T1", "--title", "finish API", "--owner", "api")
        for _ in range(2):
            self.tw("archive", "verify", "sid", "--stopped", "confirmed stopped", "--park",
                    "user will decide later", "--config-dir", str(self.dir / "claude-config"))
        state = self.state()
        self.assertEqual(len(state["archive_packs"]), 1)
        self.assertEqual(sum(n.get("kind") == "parked" for n in state["notices"]), 1)

    def test_current_session_archive_requires_handoff_of_open_task(self):
        self.tw("thread", "add", "api", "--session", "sid", "--status", "idle")
        self.tw("thread", "set", "api", "--disconnected")
        self.tw("task", "add", "T1", "--title", "finish API", "--owner", "api")
        cfg = str(self.dir.parent / (self.dir.name + "-cfg"))
        blocked = self.tw("archive", "verify", "sid", "--stopped", "confirmed stopped", "--config-dir", cfg, ok=False)
        self.assertIn("--handed-to", blocked.stderr)
        self.tw("archive", "verify", "sid", "--stopped", "confirmed stopped", "--handed-to", "lead", "--config-dir", cfg)
        self.assertEqual(self.state()["tasks"]["T1"]["owner"], "lead")

    def test_park_and_pack_reuse_the_handover_extraction(self):
        self.tw("thread", "add", "api", "--session", "sid", "--status", "idle")
        self.tw("thread", "set", "api", "--disconnected")
        self.tw("task", "add", "T1", "--title", "finish API", "--owner", "api")
        tr = self.dir / ".teamwork" / "tr.jsonl"
        config=self.dir/"claude-config"; config.mkdir()
        (config/"bridge-transcript-cse_sid.jsonl").write_text(
            json.dumps({"session_id":"00000000-0000-4000-8000-000000000001"})+"\n",encoding="utf-8")
        tr.write_text(json.dumps({"type": "user", "sessionId": "00000000-0000-4000-8000-000000000001", "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "origin": {"kind": "human"},
                                  "message": {"content": "keep the export in CSV, never xlsx"}}) + "\n", encoding="utf-8")
        self.tw("archive", "verify", "sid", "--stopped", "confirmed stopped", "--park", "user will decide later", "--transcript", str(tr), "--config-dir", str(config))
        st = self.state()
        self.assertTrue(any(n.get("kind") == "parked" and "T1" in n.get("text", "") for n in st.get("notices", [])))
        pack = st["archive_packs"]["AR1"]
        words = (Path(pack["dir"]) / "words.md").read_text(encoding="utf-8")
        self.assertIn("keep the export in CSV", words)
        self.assertTrue((Path(pack["dir"]) / "MANIFEST.json").exists())  # the full T58 pack, not a summary
        self.tw("rebuild", "--apply")
        self.assertIn("AR1", self.state()["archive_packs"])

    def test_unmerged_branch_blocks_archive(self):
        def git(*args):
            subprocess.run(["git", "-C", str(self.dir), *args], check=True, capture_output=True)
        git("init", "-q", "-b", "main"); git("config", "user.name", "t"); git("config", "user.email", "t@example.invalid")
        (self.dir / "f.txt").write_text("a\n"); git("add", "f.txt"); git("commit", "-q", "-m", "init")
        self.tw("thread", "add", "api", "--session", "sid", "--status", "idle")
        self.tw("thread", "set", "api", "--disconnected")
        branch = self.state()["threads"]["api"]["branch"]
        git("switch", "-q", "-c", branch); (self.dir / "g.txt").write_text("b\n"); git("add", "g.txt"); git("commit", "-q", "-m", "work"); git("switch", "-q", "main")
        cfg = str(self.dir.parent / (self.dir.name + "-cfg"))
        blocked = self.tw("archive", "verify", "sid", "--stopped", "confirmed stopped", "--config-dir", cfg, ok=False)
        self.assertIn(branch, blocked.stderr)

    def test_outside_reviewer_cannot_be_a_past_owner(self):
        self.tw("thread", "add", "ui", "--session", "s-ui"); self.tw("thread", "add", "api", "--session", "s-api")
        self.tw("task", "add", "T1", "--title", "x", "--owner", "ui")
        self.tw("task", "set", "T1", "--owner", "api")  # ui handed it over
        r = self.tw("task", "set", "T1", "--review", "pass", "--reviewer", "ui", "--outside", "--rounds", "1", ok=False)
        self.assertNotEqual(r.returncode, 0)

class SeatSwap(TwCase):
    def setUp(self):
        super().setUp()
        self.keys = self.dir.parent / (self.dir.name + "-keys") / "demo"
        self.keys.mkdir(parents=True)
        self.env["TW_KEYS_DIR"] = str(self.keys.parent)
        self.tw("prefs", "--set", "language=zh")
        self.tw("thread", "add", "api", "--session", "s-old")

    def test_start_freeze_pack_abort_and_rebuild(self):
        self.assertIn("记下了，开始换 api", self.tw("succ", "start", "api", "--reason", "把 api 换掉").stdout)
        self.tw("succ", "candidate", "HO1", "session_candidate")
        self.assertNotEqual(self.tw("succ", "freeze", "HO1", "--stopped", "idle confirmed", ok=False).returncode, 0)
        self.tw("succ", "freeze", "HO1", "--stopped", "s-old idle; last event 12:00")
        empty_transcript = self.dir / "empty.jsonl"
        empty_transcript.write_text("")
        packed = self.tw("succ", "pack", "HO1", "--transcript", str(empty_transcript), ok=False)
        self.assertEqual(packed.returncode, 1)
        self.assertIn("zero readable records", packed.stderr)
        self.assertEqual(self.state()["handovers"]["HO1"]["phase"],"frozen")
        self.tw("succ", "abort", "HO1", "--reason", "cancel")
        self.tw("rebuild")
        self.assertEqual(self.state()["handovers"]["HO1"]["phase"], "aborted")

    def test_bad_candidate_and_chinese_status(self):
        self.tw("lead", "--session", "s-lead")
        self.tw("succ", "start", "api", "--reason", "换掉")
        self.assertNotEqual(self.tw("succ", "candidate", "HO1", "s-lead", ok=False).returncode, 0)
        self.assertIn("HO1", self.tw("succ", "status").stdout)

    def test_pack_extracts_tools_facts_and_excludes_screen_window(self):
        self.tw("succ", "start", "api", "--reason", "换掉")
        self.tw("succ", "candidate", "HO1", "session-next")
        st = self.state()
        st["threads"]["api"]["sessions"][0]["from"] = "2025-12-01T00:00:00-05:00"
        st["screens"]["SC9"] = {"seat": "api", "session_id": "s-old", "created_at": "2025-12-03T00:00:00-05:00",
                                  "cleared_at": "2025-12-04T00:00:00-05:00"}
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        stamp = "2025-12-02T12:00:00-05:00"
        rows = [
            {"type": "user", "timestamp": stamp, "origin": {"kind": "human"}, "message": {"content": "不要修改保留内容"}},
            {"type": "assistant", "timestamp": stamp, "message": {"content": [
                {"type": "tool_use", "id": "read1", "name": "Read", "input": {"file_path": "README.md"}},
                {"type": "tool_use", "id": "bash1", "name": "Bash", "input": {"command": "false"}}]}},
            {"type": "user", "timestamp": stamp, "message": {"content": [{"type": "tool_result", "tool_use_id": "bash1", "content": "Process exited with code 1"},
                                                                                 {"type": "text", "text": "https://example.com/path"}]}},
            {"type": "user", "timestamp": "2025-12-03T12:00:00-05:00", "origin": {"kind": "human"}, "message": {"content": "体检里的练习要求"}},
            {"type": "assistant", "timestamp": stamp, "isCompactSummary": True, "message": {"content": "压缩摘要"}},
        ]
        transcript = self.dir / "transcript.jsonl"
        transcript.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows), encoding="utf-8")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], transcript, self.dir / "claude")
        self.tw("succ", "freeze", "HO1", "--stopped", "s-old idle evidence")
        out = self.tw("succ", "pack", "HO1", "--transcript", str(transcript), "--config-dir", str(self.dir / "claude")).stdout
        brief = Path(out.split(" · ")[0]).read_text(encoding="utf-8")
        facts = json.loads((Path(out.split(" · ")[0]).parent / "facts.json").read_text(encoding="utf-8"))
        words = (Path(out.split(" · ")[0]).parent / "words.md").read_text(encoding="utf-8")
        manifest = json.loads((Path(out.split(" · ")[0]).parent / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertIn("未推送提交", brief)
        self.assertEqual(facts["files"][0]["exists"], False)
        self.assertEqual(facts["failed_commands"][0]["command"], "false")
        self.assertIn("https://example.com/path", facts["urls"])
        self.assertIn("不要修改保留内容", words)
        self.assertNotIn("体检里的练习要求", words)
        self.assertEqual(manifest["compactions"], 1)

    def test_t76_pack_session_filters_foreign_and_post_freeze_rows(self):
        old_session = self.state()["threads"]["api"]["session_id"]
        self.tw("succ", "start", "api", "--reason", "replace")
        self.tw("succ", "candidate", "HO1", "session-next")
        st = self.state()
        st["threads"]["api"]["sessions"][-1]["from"] = "2026-11-20T00:00:00Z"
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.env["TW_NOW"] = "2026-11-24T12:00:00Z"
        self.tw("succ", "freeze", "HO1", "--stopped", f"{old_session} is idle")
        transcript = self.dir / "explicit-transcript.jsonl"
        rows = [
            {"type": "user", "session_id": old_session, "timestamp": "2026-11-24T11:59:00Z",
             "message": {"content": "owned before freeze"}},
            {"type": "user", "session_id": "session_foreign", "timestamp": "2026-11-24T11:59:00Z",
             "message": {"content": "foreign session row"}},
            {"type": "user", "session_id": old_session, "timestamp": "2026-11-24T12:01:00Z",
             "message": {"content": "after freeze row"}},
        ]
        transcript.write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
        st = self.state()
        rec = st["handovers"]["HO1"]
        config = self.dir.parent / (self.dir.name + "-claude-test-config"); config.mkdir()
        args = type("PackArgs", (), {"transcript": [str(transcript)], "config_dir": [str(config)]})()
        with mock.patch.object(tw, "_succ_transcript_rows",
                               side_effect=lambda path, expected, _cfg: [r for r in rows if r.get("session_id") == expected]) as parser:
            pack, _, _ = tw._pack_session(self.dir / ".teamwork", st, "HO1", rec, args)
        parser.assert_called_once_with(transcript, old_session, [str(config)])
        raw = pack / "raw" / "old.jsonl"
        packed_rows = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(packed_rows), 1)
        self.assertEqual(packed_rows[0]["message"]["content"], "owned before freeze")

    def test_quiz_uses_rule_and_fact_material_and_grades_keywords(self):
        st=self.state(); st["lead"]["session_id"]="session_lead"
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st),encoding="utf-8")
        self.tw("succ", "start", "api", "--reason", "换掉")
        self.tw("succ", "candidate", "HO1", "session-next")
        stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        transcript = self.dir / "quiz.jsonl"
        rows = [{"type": "user", "timestamp": stamp, "origin": {"kind": "human"},
                 "message": {"content": f"必须落实 Rule{i} 检查 Item{i}"}} for i in range(1, 11)]
        transcript.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows), encoding="utf-8")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], transcript, self.dir / "claude")
        self.tw("succ", "freeze", "HO1", "--stopped", "s-old idle evidence")
        self.tw("succ", "pack", "HO1", "--transcript", str(transcript), "--config-dir", str(self.dir / "claude"))
        prompt = self.tw("succ", "quiz", "HO1","--caller","session_lead").stdout
        self.assertEqual(len(prompt.splitlines()), 10)  # The ten distinct rule answers fill this quiz.
        keyfile = self.keys / "HO1-quiz-1.json"
        key = json.loads(keyfile.read_text(encoding="utf-8"))["items"]
        self.assertEqual({f"rule{i}" for i in range(1,11)},
                         {word for item in key for word in item["keywords"] if word.startswith("rule")})
        answers = self.keys / "answers.txt"
        answers.write_text("\n".join(" ".join(x["keywords"]) for x in key), encoding="utf-8")
        graded = self.tw("succ", "grade", "HO1", "--answers", str(answers),"--caller","session_lead",ok=False)
        self.assertEqual(graded.returncode,1)
        self.assertIn("Zero tool calls: unverified",graded.stdout)
        quiz_transcript(self.dir/"candidate.jsonl",key[0]," ".join(answers.read_text(encoding="utf-8").splitlines()))
        self.synthetic_bridge("session-next", self.dir/"candidate.jsonl")
        import importlib.util
        spec=importlib.util.spec_from_file_location("tw_quiz_probe",TW); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        transcript_rows=mod._succ_transcript_rows(self.dir/"candidate.jsonl")
        answer_lines=answers.read_text(encoding="utf-8").splitlines()
        self.assertTrue(mod._succ_zero_tool_window(transcript_rows,key[0]["question"],answer_lines),
                        f"question={key[0]['question']!r}; answers={answer_lines!r}; rows={transcript_rows!r}")
        self.tw("succ","grade","HO1","--answers",str(answers),"--caller","session_lead","--from-transcript",str(self.dir/"candidate.jsonl"))
        second = self.tw("succ", "quiz", "HO1","--caller","session_lead",ok=False)
        self.assertEqual(len(second.stdout.splitlines()),2)  # branch and unpushed count remain after all ten rules.
        self.tw("succ","grade","HO1","--answers",str(answers),"--caller","session_lead","--from-transcript",str(self.dir/"candidate.jsonl"),ok=False)
        final=self.tw("succ","quiz","HO1","--caller","session_lead",ok=False)
        self.assertEqual(final.returncode,1)
        self.assertIn("没有未使用的测验材料",final.stderr)

    def test_signals_relay_count_and_replaced_archive_reason(self):
        today = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        st = self.state()
        st["handovers"] = {f"HO{i}": {"id": f"HO{i}", "seat": "api", "phase": "seated", "old_session": f"s{i}",
                                                "steps": {"seat": today}} for i in (1, 2)}
        t = st["threads"]["api"]
        t.update(session_id="s-new", status="working", last_report_session="s-new", last_status_at=today)
        t["sessions"] = [{"session_id": "s1", "from": "2025-01-01T00:00:00-05:00", "to": today, "reason": "HO1: change"},
                          {"session_id": "s2", "from": "2025-02-01T00:00:00-05:00", "to": today, "reason": "HO2: change"},
                          {"session_id": "s-new", "from": today}]
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        transcript=self.dir/"relay-signals.jsonl"
        transcript.write_text(json.dumps({"type":"user","timestamp":today,"message":{"content":"hello"}}),encoding="utf-8")
        sig = json.loads(self.tw("signals", "api", "--transcript", str(transcript), "--json").stdout)
        self.assertEqual(sig["relay_count_today"], 2)
        bare = json.loads(self.tw("signals", "api", "--json").stdout)  # no record: still JSON, ledger facts, no colour
        self.assertEqual((bare["relay_count_today"], bare["color"]), (2, None))
        rows = json.loads(self.tw("archive", "candidates", "--json").stdout)
        self.assertEqual(next(x for x in rows if x["session_id"] == "s1")["reason"], "HO1 换下的会话，新会话已汇报")

    def test_verify_rejects_post_freeze_changes(self):
        self.tw("succ", "start", "api", "--reason", "换掉")
        self.tw("succ", "candidate", "HO1", "session-next")
        st = self.state(); st["tasks"]["T1"] = {"title": "finish task", "owner": "api", "status": "working", "accept": "包含测试和报告", "touches": ["src"]}
        st["requests"]["R1"] = {"status": "open", "text": "finish T1", "tasks": ["T1"]}
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        transcript = self.dir / "verify.jsonl"
        transcript.write_text(json.dumps({"type": "user", "timestamp": stamp, "origin": {"kind": "human"},
                                          "message": {"content": "永远保留工作文件"}}, ensure_ascii=False), encoding="utf-8")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], transcript, self.dir / "claude")
        self.tw("succ", "freeze", "HO1", "--stopped", "s-old idle evidence")
        self.tw("succ", "pack", "HO1", "--transcript", str(transcript), "--config-dir", str(self.dir / "claude"))
        st = self.state(); st["handovers"]["HO1"]["freeze"]["dirty_fingerprint"] = "changed-after-freeze"
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        head = "-"
        receipt = self.keys / "receipt.json"
        receipt.write_text(json.dumps({"tasks": {"T1": {"accept": "包含测试和报告"}}, "branch": "tw/demo/api", "head": head,
                                       "rules": [1], "requests": {"R1": "open"}}, ensure_ascii=False), encoding="utf-8")
        result = self.tw("succ", "verify", "HO1", "--receipt", str(receipt), ok=False)
        self.assertIn("冻结后还有改动", result.stdout)
        self.assertFalse(self.state()["handovers"]["HO1"]["verify"]["passed"])

    def test_critical_seat_rejects_failed_screen(self):
        self.tw("thread", "set", "api", "--critical")
        self.tw("succ", "start", "api", "--reason", "换掉")
        self.tw("succ", "candidate", "HO1", "session-next")
        st = self.state()
        rec = st["handovers"]["HO1"]
        rec.update(freeze={"at": "now"}, pack_dir=str(self.dir),
                   verify={"passed": True}, quiz={"passed": True})
        rec["steps"].update(freeze="now", pack="now", verify="now", quiz="now")
        st["screens"]["SC1"] = {"seat": "api", "session_id": "session-next", "verdict": "fail"}
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        result = self.tw("succ", "seat", "HO1", "session-next", ok=False)
        self.assertIn("关键席位不让体检没过", result.stderr)

class SeatSwapFlow(TwCase):
    """End to end in a real git repo: the pack inside .teamwork must not count as a change after freeze."""

    def test_full_swap_in_git_repo(self):
        import importlib.util
        fixture_dir = FIXTURES
        fixture_spec = importlib.util.spec_from_file_location("s12_fixture_place", fixture_dir / "regenerate.py")
        fixture_mod = importlib.util.module_from_spec(fixture_spec); fixture_spec.loader.exec_module(fixture_mod)
        def git(*args):
            subprocess.run(["git", "-C", str(self.dir), *args], check=True, capture_output=True)
        git("init", "-q", "-b", "main"); git("config", "user.name", "t"); git("config", "user.email", "t@example.invalid")
        (self.dir / "f.txt").write_text("a\n"); git("add", "f.txt"); git("commit", "-q", "-m", "init")
        self.tw("lead", "--session", "session_lead")
        keys = self.dir.parent / (self.dir.name + "-keys"); keys.mkdir()
        self.env["TW_KEYS_DIR"] = str(keys)
        self.tw("prefs", "--set", "language=zh")
        self.tw("thread", "add", "ui", "--session", "old1")
        self.tw("thread", "set", "ui", "--status", "working", "--from-status")
        self.env["TW_NOW"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=1)).isoformat(timespec="seconds")
        config = self.dir.parent / (self.dir.name + "-claude-test-config"); config.mkdir()
        transcript = fixture_mod.place("t71_old_thread.jsonl", config, session="old1")
        self.env["TW_NOW"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=2)).isoformat(timespec="seconds")
        self.tw("succ", "start", "ui", "--reason", "把 ui 换掉")
        self.tw("succ", "candidate", "HO1", " new1 ")
        self.tw("screen","ask","ui","--session","new1")
        self.tw("screen","record","SC1","--scores",",".join(f"P{i}=2" for i in range(1,7)))
        self.tw("screen","clear-msg","SC1")
        self.tw("succ","screened","HO1","new1","--screen","SC1")
        self.tw("succ","candidate","HO1","new1","--cleared")
        self.tw("succ", "freeze", "HO1", "--stopped", "get_session old1: idle")
        self.tw("succ", "pack", "HO1", "--transcript", str(transcript), "--config-dir", str(config))
        missing = self.tw("succ", "seat", "HO1", "new1", ok=False)
        self.assertIn("verify", missing.stderr)
        head = subprocess.run(["git", "-C", str(self.dir), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        receipt = self.dir / ".teamwork" / "rc.json"
        receipt.write_text(json.dumps({"tasks": [], "branch": "tw/demo/ui", "head": head, "rules": [1], "requests": [],
            "only_read_fact":"none — 原因：样本没有用户原话可引用",
            "unresolved":[],"sections":"## Rules\nNo additional rule changes.\n## Only-known-after-reading fact / 只有读过才知道的一条\nnone — 原因：样本没有用户原话可引用\n## Unresolved / 悬而未决\nnone"}))
        verified = self.tw("succ", "verify", "HO1", "--receipt", str(receipt)).stdout
        self.assertNotIn("不过", verified)
        quiz = self.tw("succ", "quiz", "HO1", "--caller", "session_lead").stdout
        self.assertNotIn("xlsx", quiz.split("\n", 1)[-1].split("\n")[0] if "xlsx" in quiz else "")
        answers = self.dir / ".teamwork" / "ans.txt"
        items = json.loads((keys / "demo" / "HO1-quiz-1.json").read_text(encoding="utf-8"))["items"]
        answers.write_text("\n".join(" ".join(item["keywords"]) for item in items), encoding="utf-8")
        unverified = self.tw("succ", "grade", "HO1", "--answers", str(answers), "--caller", "session_lead", ok=False)
        self.assertIn("Zero tool calls: unverified", unverified.stdout)
        items=json.loads((keys/"demo"/"HO1-quiz-1.json").read_text(encoding="utf-8"))["items"]
        candidate_transcript=fixture_mod.place("t71_real_candidate.jsonl",config,session="new1")
        quiz_transcript(candidate_transcript,items[0]," ".join(answers.read_text(encoding="utf-8").splitlines()))
        candidate_rows=[json.loads(line) for line in candidate_transcript.read_text(encoding="utf-8").splitlines() if line.strip()]
        for row in candidate_rows: row["sessionId"]="new1"
        candidate_transcript.write_text("".join(json.dumps(row,ensure_ascii=False)+"\n" for row in candidate_rows),encoding="utf-8")
        self.tw("succ","grade","HO1","--answers",str(answers),"--caller","session_lead","--from-transcript",str(candidate_transcript),"--config-dir",str(config))
        seat_card=self.tw("succ", "seat", "HO1", " new1 ").stdout
        self.assertRegex(seat_card,r"体检分：\d+/\d+；测验 得分/总数：\d+/\d+；验收 通过/总项：\d+/\d+")
        self.assertEqual(self.state()["threads"]["ui"]["session_id"], "new1")
        (self.dir / "f.txt").write_text("changed after seat\n")
        self.tw("thread", "set", "ui", "--status", "working", "--from-status")
        self.assertIn("HO1", self.tw("archive", "candidates").stdout)

class LeadSwap(TwCase):
    def setUp(self):
        super().setUp()
        self.keys = self.dir.parent / (self.dir.name + "-keys") / "demo"
        self.keys.mkdir(parents=True)
        self.env["TW_KEYS_DIR"] = str(self.keys.parent)
        self.tw("prefs", "--set", "language=zh")
        self.tw("lead", "--session", "lead-old")

    def test_freeze_lock_abort_and_rebuild(self):
        self.tw("succ", "start", "lead", "--reason", "我原话要求换 lead")
        self.tw("succ", "candidate", "HO1", "lead-next")
        self.tw("succ", "freeze", "HO1", "--stopped", "lead-old idle at last event")
        self.assertEqual(self.state()["write_lock"]["ho"], "HO1")
        self.assertNotEqual(self.tw("prefs", "--set", "language=en", ok=False).returncode, 0)
        self.assertIn("teamwork: demo", self.tw("board").stdout)
        self.tw("rebuild")
        self.assertEqual(self.state()["write_lock"]["old_session"], "lead-old")
        self.tw("succ", "abort", "HO1", "--reason", "候选无响应")
        self.assertNotIn("write_lock", self.state())
        self.tw("rebuild")
        self.assertEqual(self.state()["handovers"]["HO1"]["phase"], "aborted")

    def test_relead_lock_blocks_succ_and_abort_preserves_other_lock(self):
        self.tw("thread", "add", "api")
        self.tw("opening", "--json")
        self.tw("screen", "record", "SC1", "--scores", ",".join(f"P{i}=0" for i in range(1, 8)))
        self.tw("screen", "relead", "SC1", "--said", "原始请求")
        locked = self.tw("succ", "start", "lead", "--reason", "attempt bypass", ok=False)
        self.assertIn("先跑", locked.stderr)
        self.assertTrue(self.state()["write_lock"]["relead"])
        self.assertNotEqual(self.tw("task", "add", "T-lock", "--title", "must stay locked", ok=False).returncode, 0)
        self.tw("succ", "start", "api", "--reason", "unrelated seat")
        self.tw("succ", "abort", "HO1", "--reason", "not its lock")
        self.assertTrue(self.state()["write_lock"]["relead"])
        self.tw("lead", "--session", "lead-2", "--relead")
        self.assertFalse(self.state().get("write_lock"))

    def test_lead_handover_rejects_thread_swap_while_open(self):
        self.tw("succ", "start", "lead", "--reason", "交给继任")
        self.tw("thread", "add", "api", "--session", "api-old")
        result = self.tw("succ", "start", "api", "--reason", "换 api", ok=False)
        self.assertIn("lead handover", result.stderr)

    def test_lead_seat_updates_history_and_clears_lock(self):
        self.tw("lead", "--session", "lead-old")
        self.tw("succ", "start", "lead", "--reason", "换 lead")
        self.tw("succ", "candidate", "HO1", "lead-next")
        self.tw("screen", "ask", "lead", "--session", "lead-next")
        self.tw("screen", "record", "SC1", "--scores", ",".join(f"P{i}=2" for i in range(1, 8)))
        self.tw("screen", "clear-msg", "SC1")
        self.tw("succ", "screened", "HO1", "lead-next", "SC1")
        transcript = self.dir / "lead.jsonl"
        stamp=(dt.datetime.now().astimezone()+dt.timedelta(minutes=1)).isoformat()
        transcript.write_text("\n".join(json.dumps({"type":"user","timestamp":stamp,
            "origin":{"kind":"human"},"message":{"content":"Always "+x}},ensure_ascii=False) for x in ("validate alpha endpoint", "preserve beta records", "verify gamma status", "update delta summary", "record epsilon outcome", "compare zeta branch", "check eta request", "store theta facts", "track iota changes", "confirm kappa result"))+"\n",encoding="utf-8")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], transcript, self.dir / "claude")
        self.env["TW_NOW"]=(dt.datetime.fromisoformat(stamp)+dt.timedelta(minutes=1)).isoformat()
        self.tw("succ", "freeze", "HO1", "--stopped", "lead-old idle event")
        self.tw("succ", "pack", "HO1", "--transcript", str(transcript), "--config-dir", str(self.dir / "claude"))
        receipt = self.dir / "receipt.json"
        fact="Always validate alpha endpoint"
        receipt.write_text(json.dumps({"tasks":[],"branch":None,"head":"-","rules":list(range(1,11)),"requests":[],
            "only_read_fact":fact,"unresolved":[],"sections":"## Rules\nAll listed rules reviewed.\n## Only-known-after-reading fact\n"+fact+"\n## Unresolved\nnone"}),encoding="utf-8")
        self.tw("succ", "verify", "HO1", "--receipt", str(receipt))
        self.tw("succ", "quiz", "HO1", "--caller", "lead-old")
        key = json.loads((self.keys / "HO1-quiz-1.json").read_text(encoding="utf-8"))["items"]
        answers = self.dir / "answers.txt"
        answers.write_text("\n".join(" ".join(item["keywords"]) for item in key),encoding="utf-8")
        unverified = self.tw("succ", "grade", "HO1", "--answers", str(answers), "--caller", "lead-old", ok=False)
        self.assertIn("Zero tool calls: unverified", unverified.stdout)
        quiz_transcript(self.dir/"candidate.jsonl",key[0]," ".join(answers.read_text(encoding="utf-8").splitlines()))
        self.synthetic_bridge("lead-next", self.dir/"candidate.jsonl")
        self.tw("succ","grade","HO1","--answers",str(answers),"--caller","lead-old","--from-transcript",str(self.dir/"candidate.jsonl"))
        result = self.tw("succ", "seat", "HO1", "lead-next")
        self.assertIn("https://claude.ai/code/lead-next", result.stdout)
        self.assertIn("验收通过项：13/13",result.stdout)
        st = self.state()
        self.assertEqual(st["lead"]["session_id"], "lead-next")
        self.assertEqual([x["session_id"] for x in st["lead"]["sessions"]], ["lead-old", "lead-next"])
        self.assertNotIn("write_lock", st)
        self.tw("rebuild")
        self.assertEqual(self.state()["lead"]["session_id"], "lead-next")

    def _seat_lead(self, triggers=None, subscriptions=None):
        self.tw("succ", "start", "lead", "--reason", "Please hand over the lead")
        self.tw("succ", "candidate", "HO1", "lead-next")
        self.tw("screen", "ask", "lead", "--session", "lead-next")
        self.tw("screen", "record", "SC1", "--scores", ",".join(f"P{i}=2" for i in range(1, 8)))
        self.tw("screen", "clear-msg", "SC1")
        self.tw("succ", "screened", "HO1", "lead-next", "SC1")
        transcript = self.dir / "lead.jsonl"
        stamp=(dt.datetime.now().astimezone()+dt.timedelta(minutes=1)).isoformat()
        transcript.write_text("\n".join(json.dumps({"type":"user","timestamp":stamp,
            "origin":{"kind":"human"},"message":{"content":"Always "+x}},ensure_ascii=False) for x in ("validate alpha endpoint", "preserve beta records", "verify gamma status", "update delta summary", "record epsilon outcome", "compare zeta branch", "check eta request", "store theta facts", "track iota changes", "confirm kappa result"))+"\n",encoding="utf-8")
        self.env["TW_NOW"]=(dt.datetime.fromisoformat(stamp)+dt.timedelta(minutes=1)).isoformat()
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], transcript, self.dir / "claude")
        self.tw("succ", "freeze", "HO1", "--stopped", "lead-old idle event")
        args = ["succ", "pack", "HO1", "--transcript", str(transcript), "--config-dir", str(self.dir / "claude")]
        if triggers: args += ["--triggers", str(triggers)]
        if subscriptions: args += ["--subscriptions", str(subscriptions)]
        self.tw(*args)
        receipt = self.dir / "receipt.json"
        fact="Always validate alpha endpoint"
        receipt.write_text(json.dumps({"tasks":[],"branch":None,"head":"-","rules":list(range(1,11)),"requests":[],
            "only_read_fact":fact,"unresolved":[],"sections":"## Rules\nAll listed rules reviewed.\n## Only-known-after-reading fact\n"+fact+"\n## Unresolved\nnone"}),encoding="utf-8")
        self.tw("succ", "verify", "HO1", "--receipt", str(receipt))
        self.tw("succ", "quiz", "HO1", "--caller", "lead-old")
        items = json.loads((self.keys / "HO1-quiz-1.json").read_text(encoding="utf-8"))["items"]
        answers = self.dir / "answers.txt"
        answers.write_text("\n".join(" ".join(item["keywords"]) for item in items),encoding="utf-8")
        unverified = self.tw("succ", "grade", "HO1", "--answers", str(answers), "--caller", "lead-old", ok=False)
        self.assertIn("Zero tool calls: unverified", unverified.stdout)
        quiz_transcript(self.dir/"candidate.jsonl",items[0]," ".join(answers.read_text(encoding="utf-8").splitlines()))
        self.synthetic_bridge("lead-next", self.dir/"candidate.jsonl")
        self.tw("succ","grade","HO1","--answers",str(answers),"--caller","lead-old","--from-transcript",str(self.dir/"candidate.jsonl"))
        return self.tw("succ", "seat", "HO1", "lead-next")

    def test_ack_blocks_archive_until_every_thread_acknowledges(self):
        self.tw("thread", "add", "api", "--session", "api-old")
        self.tw("thread", "add", "ui", "--session", "ui-old")
        self.tw("thread", "add", "planned")
        self.tw("thread", "add", "retired", "--session", "retired-old")
        self.tw("thread", "set", "retired", "--status", "archived")
        triggers=self.dir/"triggers_ack.json"; triggers.write_text(json.dumps([{"id":"daily"}]),encoding="utf-8")
        subscriptions=self.dir/"subscriptions_ack.json"; subscriptions.write_text(json.dumps([]),encoding="utf-8")
        self._seat_lead(triggers,subscriptions)
        self.assertEqual(self.state()["handovers"]["HO1"]["ack_needed"], ["api", "ui"])
        self.tw("succ", "ack", "HO1", "--thread", "api")
        rows = json.loads(self.tw("archive", "candidates", "--json").stdout)
        old = next(x for x in rows if x["session_id"] == "lead-old")
        self.assertEqual(old["category"], "blocked-replaced")
        self.assertIn("还有 1 个线程", old["reason"])
        self.tw("succ", "ack", "HO1", "--thread", "ui")
        rows = json.loads(self.tw("archive", "candidates", "--json").stdout)
        self.assertIn("还有 1 项提醒",next(x for x in rows if x["session_id"] == "lead-old")["reason"])
        self.tw("succ", "moved", "HO1", "--trigger", "daily")
        self.tw("succ", "reconciled", "HO1", "--count", "0")
        self.tw("succ", "window", "HO1")
        rows = json.loads(self.tw("archive", "candidates", "--json").stdout)
        self.assertEqual(next(x for x in rows if x["session_id"] == "lead-old")["category"], "replaced")

    def test_moved_lists_and_missing_list_gaps(self):
        triggers = self.dir / "triggers.json"
        triggers.write_text(json.dumps([{"id":"daily"}]),encoding="utf-8")
        subscriptions = self.dir / "subscriptions.json"
        subscriptions.write_text(json.dumps([{"owner":"acme","repo":"app","number":9}]),encoding="utf-8")
        self._seat_lead(triggers, subscriptions)
        self.assertIn("trigger:daily", self.tw("succ", "status", "HO1").stdout)
        self.tw("succ", "moved", "HO1", "--trigger", "daily")
        self.tw("succ", "moved", "HO1", "--subscription", "acme/app#9")
        self.assertNotIn("trigger:daily", self.tw("succ", "status", "HO1").stdout)
        self.tw("rebuild")
        self.assertEqual(len(self.state()["handovers"]["HO1"]["moved"]), 2)

    def test_window_reconciliation_and_lead_seat_json(self):
        self.tw("succ", "start", "lead", "--reason", "Please hand over")
        early = self.tw("succ", "window", "HO1", ok=False)
        self.assertIn("上任后", early.stderr)
        self.tw("succ", "abort", "HO1", "--reason", "stop")
        # A separate complete handover exercises the atomic seat response and reconciliation record.
        self.tw("succ", "start", "lead", "--reason", "Please hand over")
        self.tw("succ", "candidate", "HO2", "lead-next")
        self.tw("screen", "ask", "lead", "--session", "lead-next")
        self.tw("screen", "record", "SC1", "--scores", ",".join(f"P{i}=2" for i in range(1, 8)))
        self.tw("screen", "clear-msg", "SC1")
        self.tw("succ", "screened", "HO2", "lead-next", "SC1")
        transcript = self.dir / "lead2.jsonl"
        stamp=(dt.datetime.now().astimezone()+dt.timedelta(minutes=1)).isoformat()
        transcript.write_text("\n".join(json.dumps({"type":"user","timestamp":stamp,"origin":{"kind":"human"},"message":{"content":"Always "+x}}) for x in ("validate alpha endpoint", "preserve beta records", "verify gamma status", "update delta summary", "record epsilon outcome", "compare zeta branch", "check eta request", "store theta facts", "track iota changes", "confirm kappa result"))+"\n",encoding="utf-8")
        self.synthetic_bridge(self.state()["handovers"]["HO2"]["old_session"], transcript)
        self.env["TW_NOW"]=(dt.datetime.fromisoformat(stamp)+dt.timedelta(minutes=1)).isoformat()
        self.tw("succ", "freeze", "HO2", "--stopped", "lead-old idle event")
        self.tw("succ", "pack", "HO2", "--transcript", str(transcript))
        receipt = self.dir / "receipt.json"
        fact="Always validate alpha endpoint"
        receipt.write_text(json.dumps({"tasks":[],"branch":None,"head":"-","rules":list(range(1,11)),"requests":[],
            "only_read_fact":fact,"unresolved":[],"sections":"## Rules\nAll listed rules reviewed.\n## Only-known-after-reading fact\n"+fact+"\n## Unresolved\nnone"}),encoding="utf-8")
        self.tw("succ", "verify", "HO2", "--receipt", str(receipt)); self.tw("succ", "quiz", "HO2", "--caller", "lead-old")
        items=json.loads((self.keys / "HO2-quiz-1.json").read_text())["items"]
        answers=self.dir / "answers2.txt"; answers.write_text("\n".join(" ".join(x["keywords"]) for x in items))
        unverified = self.tw("succ", "grade", "HO2", "--answers", str(answers), "--caller", "lead-old", ok=False)
        self.assertIn("Zero tool calls: unverified", unverified.stdout)
        quiz_transcript(self.dir/"candidate.jsonl",items[0]," ".join(answers.read_text(encoding="utf-8").splitlines()))
        self.synthetic_bridge("lead-next", self.dir/"candidate.jsonl")
        self.tw("succ","grade","HO2","--answers",str(answers),"--caller","lead-old","--from-transcript",str(self.dir/"candidate.jsonl"))
        seated=self.tw("succ", "seat", "HO2", "lead-next", "--json")
        obj=json.loads(seated.stdout)
        self.assertIsNone(obj["thread"]); self.assertEqual(obj["lead"]["session_id"],"lead-next")
        self.tw("succ", "reconciled", "HO2", "--count", "2", "--missing", "one reply")
        self.tw("succ", "window", "HO2")
        self.assertEqual(self.state()["handovers"]["HO2"]["reconciled"]["missing"],"one reply")

    def test_watchdog_threshold_does_not_abort(self):
        self.tw("succ", "start", "lead", "--reason", "Please hand over")
        self.tw("succ", "ping", "HO1", "--candidate-seen", "2026-11-22T00:00:00-05:00")
        stale=self.tw("succ", "status", "HO1").stdout
        self.assertIn("新 lead 卡住了",stale)
        self.assertEqual(self.state()["handovers"]["HO1"]["phase"],"started")
        self.tw("succ", "ping", "HO1", "--candidate-seen", dt.datetime.now().astimezone().isoformat())
        self.assertNotIn("新 lead 卡住了",self.tw("succ", "status", "HO1").stdout)

    def test_quiz_threshold_and_chinese_keyword_matching(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location("tw_module",TW); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        self.assertTrue(mod._succ_hit("CSV 格式",mod._succ_normalize("CSV 格式，不要 xlsx")))
        self.assertFalse(mod._succ_hit("CSV 格式",mod._succ_normalize("随便")))
        self.tw("succ","start","lead","--reason","test grading")
        st=self.state(); keyfile=self.keys/"HO1-quiz-1.json"
        keyfile.write_text(json.dumps({"items":[{"question":"one","keywords":["apple"]},{"question":"two","keywords":["banana"]}]}))
        st["handovers"]["HO1"].update(candidates=[{"session_id":"lead-next"}],quiz={"key_file":str(keyfile),"candidate":"lead-next"},phase="quiz",steps={"quiz":"now"})
        (self.dir/".teamwork/state.json").write_text(json.dumps(st))
        answers=self.dir/"threshold-2.txt"; answers.write_text("apple\nwrong\n")
        candidate=self.dir/"lead-candidate-low.jsonl"; quiz_transcript(candidate,{"question":"one"},"apple wrong")
        self.synthetic_bridge("lead-next", candidate)
        result=self.tw("succ","grade","HO1","--answers",str(answers),"--caller","lead-old","--from-transcript",str(candidate),ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("1/2 不过",result.stdout)
        self.assertIn("本轮可用题目少于 3 题",result.stdout)
        self.assertFalse(self.state()["handovers"]["HO1"]["quiz"]["passed"])
        st=self.state(); keyfile=self.keys/"HO1-quiz-2.json"
        keyfile.write_text(json.dumps({"items":[{"question":str(i),"keywords":[f"word{i}"]} for i in range(10)]}))
        st["handovers"]["HO1"].update(candidates=[{"session_id":"lead-next"}],quiz={"key_file":str(keyfile),"candidate":"lead-next"},phase="quiz",steps={"quiz":"now"})
        (self.dir/".teamwork/state.json").write_text(json.dumps(st))
        answers=self.dir/"threshold-10.txt"; answers.write_text("\n".join(["wrong",*[f"word{i}" for i in range(1,10)]])+"\n")
        candidate=self.dir/"lead-candidate.jsonl"
        quiz_transcript(candidate,{"question":"0"},"\n".join(["wrong",*[f"word{i}" for i in range(1,10)]]))
        self.synthetic_bridge("lead-next", candidate)
        result=self.tw("succ","grade","HO1","--answers",str(answers),"--caller","lead-old","--from-transcript",str(candidate))
        self.assertIn("9/10 过",result.stdout)
        self.assertIn("Zero tool calls: passed",result.stdout)
        self.assertTrue(self.state()["handovers"]["HO1"]["quiz"]["passed"])

    def test_english_brief_and_locked_readonly_lead(self):
        self.tw("prefs","--set","language=en")
        self.tw("lead","--session","lead-old")
        self.tw("succ","start","lead","--reason","Please hand over the lead")
        transcript=self.dir/"english.jsonl"
        stamp=(dt.datetime.now().astimezone()+dt.timedelta(minutes=1)).isoformat(timespec="seconds")
        transcript.write_text(json.dumps({"type":"user","timestamp":stamp,"origin":{"kind":"human"},"message":{"content":"Keep every task status accurate"}})+"\n")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], transcript)
        self.tw("succ","candidate","HO1","lead-next")
        self.env["TW_NOW"]=(dt.datetime.fromisoformat(stamp)+dt.timedelta(minutes=1)).isoformat()
        self.tw("succ","freeze","HO1","--stopped","lead-old idle event")
        self.tw("succ","pack","HO1","--transcript",str(transcript))
        brief=(self.dir/".teamwork/private/handover/HO1-lead/BRIEF.md").read_text()
        self.assertNotRegex(brief,r"[\u3400-\u9fff]")
        self.assertIn("Missing triggers list",brief)
        self.assertIn("Missing subscriptions list",brief)
        lead=self.tw("lead"); self.assertEqual(lead.returncode,0); json.loads(lead.stdout)
        add=self.tw("task","add","Blocked task","--accept","done",ok=False)
        self.assertNotEqual(add.returncode,0)

class SeatSwap2(TwCase):
    def setUp(self):
        super().setUp()
        self.keys = self.dir.parent / (self.dir.name + "-keys") / "demo"
        self.keys.mkdir(parents=True)
        self.env["TW_KEYS_DIR"] = str(self.keys.parent)
        self.tw("prefs", "--set", "language=zh")
        self.tw("thread", "add", "api", "--session", "old-api")

    def test_aborted_handover_is_terminal(self):
        self.tw("succ", "start", "api", "--reason", "换 api")
        self.tw("succ", "candidate", "HO1", "candidate-api")
        self.tw("succ", "abort", "HO1", "--reason", "取消")
        result = self.tw("succ", "candidate", "HO1", "candidate-two", ok=False)
        self.assertIn("不能再 candidate", result.stderr)
        self.tw("succ", "start", "api", "--reason", "重新换 api")
        self.tw("succ", "candidate", "HO2", "candidate-api")

    def test_pack_filters_answer_files_and_redacts_main_rows(self):
        self.tw("succ", "start", "api", "--reason", "换 api")
        self.tw("succ", "candidate", "HO1", "candidate-api")
        self.tw("succ", "freeze", "HO1", "--stopped", "old-api is idle")
        main = self.dir / "main.jsonl"
        main.write_text(json.dumps({"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":str(self.keys / "SC1-grade.md")}}]}}), encoding="utf-8")
        child = self.dir / "agent-run.jsonl"
        child.write_text(json.dumps({"type":"user","message":{"content":f"read {self.keys / 'SC1.json'}"}}), encoding="utf-8")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], main,
                              second_local_session=f"synthetic-child-{self.state()['handovers']['HO1']['old_session']}")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], child,
                              second_local_session=f"synthetic-child-{self.state()['handovers']['HO1']['old_session']}")
        packed = self.tw("succ", "pack", "HO1", "--transcript", str(main), "--transcript", str(child)).stdout
        brief = Path(packed.split(" · ")[0])
        facts = json.loads((brief.parent / "facts.json").read_text(encoding="utf-8"))
        raw = (brief.parent / "raw" / "old.jsonl").read_text(encoding="utf-8")
        self.assertIn("跳过 1 份读过答案", brief.read_text(encoding="utf-8"))
        self.assertNotIn("SC1-grade.md", json.dumps(facts))
        self.assertNotIn("SC1-grade.md", raw)
        self.assertNotIn("SC1-quiz-1.json", raw)

    def test_pack_redacts_tool_result_from_custom_key_directory(self):
        keyroot = self.dir.parent / (self.dir.name + "-vault")
        self.env["TW_KEYS_DIR"] = str(keyroot)
        actual = keyroot / "demo" / "SC1.json"
        self.tw("succ", "start", "api", "--reason", "swap")
        self.tw("succ", "candidate", "HO1", "candidate-api")
        self.tw("succ", "freeze", "HO1", "--stopped", "old-api idle")
        transcript = self.dir / "answer-transcript.jsonl"
        rows = [
            {"type":"assistant","message":{"content":[
                {"type":"tool_use","id":"read-secret","name":"Bash","input":{"command":f"cat {actual}"}}]}},
            {"type":"user","message":{"content":[
                {"type":"tool_result","tool_use_id":"read-secret","content":"SECRETANSWER"}]},
             "toolUseResult":{"stdout":"SECRETANSWER"}},
            {"type":"assistant","message":{"content":[
                {"type":"tool_use","id":"cd-cat-star","name":"Bash","input":{"command":f"cd {actual.parent} && cat *"}}]}},
            {"type":"user","message":{"content":[
                {"type":"tool_result","tool_use_id":"cd-cat-star","content":"SECRETANSWER"}]},
             "toolUseResult":{"stdout":"SECRETANSWER"}},
            {"type":"assistant","message":{"content":[
                {"type":"tool_use","id":"cd-keys","name":"Bash","input":{"command":f"cd {actual.parent}"}}]}},
            {"type":"assistant","message":{"content":[
                {"type":"tool_use","id":"cat-sc","name":"Bash","input":{"command":"cat SC1.json"}}]}},
            {"type":"user","message":{"content":[
                {"type":"tool_result","tool_use_id":"cat-sc","content":"SECRETANSWER"}]},
             "toolUseResult":{"stdout":"SECRETANSWER"}},
        ]
        transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], transcript)
        packed = self.tw("succ", "pack", "HO1", "--transcript", str(transcript)).stdout
        package = Path(packed.split(" · ")[0]).parent
        for path in package.rglob("*"):
            if path.is_file(): self.assertNotIn("SECRETANSWER", path.read_text(encoding="utf-8", errors="replace"), str(path))

    def test_init_force_uses_current_revision(self):
        self.tw("prefs", "--set", "language=en")
        self.tw("init", "demo", "--force", "--goal", "g2", "--user-said", "start over")
        self.assertEqual(self.state()["goal"], "g2")

    def test_succ_status_omits_terminal_next_step_and_shows_seated_followup(self):
        st = self.state()
        st["handovers"] = {
            "HO1":{"id":"HO1","seat":"api","phase":"aborted","steps":{"start":"t","abort":"t"}},
            "HO2":{"id":"HO2","seat":"lead","phase":"seated","steps":{"start":"t","candidate":"t","freeze":"t","pack":"t","verify":"t","quiz":"t","grade":"t","seat":"t"}},
        }
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        rows = json.loads(self.tw("succ", "status", "--json").stdout)
        self.assertIsNone(rows[0]["next"])
        self.assertEqual(rows[1]["next"], "ack")
        self.assertNotIn("→", self.tw("succ", "status", "HO1").stdout)

    def test_grade_rejects_candidate_different_from_quiz_candidate(self):
        st=self.state(); st["lead"]["session_id"]="session_lead"
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st),encoding="utf-8")
        self.tw("succ", "start", "api", "--reason", "swap")
        st = self.state()
        rec = st["handovers"]["HO1"]
        rec["candidates"] = [{"session_id":"candidate-a"}, {"session_id":"candidate-b"}]
        keyfile = self.keys / "HO1-quiz.json"
        keyfile.write_text(json.dumps({"items":[{"question":"q","keywords":["apple"]}]}), encoding="utf-8")
        rec["quiz"] = {"key_file":str(keyfile), "candidate":"candidate-a"}
        rec["phase"] = "quiz"
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        answers = self.dir / "answers.txt"
        answers.write_text("apple\n", encoding="utf-8")
        result = self.tw("succ", "grade", "HO1", "--candidate", "candidate-b", "--answers", str(answers),"--caller","session_lead", ok=False)
        self.assertIn("candidate", result.stderr)

    def test_thread_set_session_is_blocked_during_open_handover(self):
        self.tw("succ", "start", "api", "--reason", "swap")
        before=self.state()
        result = self.tw("thread", "set", "api", "--session", "new-api",ok=False)
        self.assertEqual(result.returncode,1)
        self.assertIn("succ seat 或 succ abort",result.stderr)
        self.assertEqual(self.state()["threads"],before["threads"])

class LeadSwap2(TwCase):
    def setUp(self):
        super().setUp()
        self.tw("lead", "--session", "lead-old")
        self.tw("prefs", "--set", "language=zh")

    def test_disk_write_lock_is_rechecked_by_save(self):
        st = self.state()
        st["write_lock"] = {"ho": "HO9", "since": "now", "old_session": "lead-old"}
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        result = self.tw("prefs", "--set", "language=en", ok=False)
        self.assertNotEqual(result.returncode, 0)

    def test_write_lock_gate_and_rev_conflict(self):
        self.tw("succ", "start", "lead", "--reason", "swap")
        self.tw("succ", "candidate", "HO1", "lead-next")
        self.tw("succ", "freeze", "HO1", "--stopped", "lead-old idle")
        self.assertEqual(self.tw("task", "reviews").returncode, 0)
        rejected = self.tw("memory", "should be blocked", ok=False)
        self.assertIn("换届中", rejected.stderr)
        import importlib.util
        spec = importlib.util.spec_from_file_location("tw_rev_test", TW)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ledger = self.dir / ".teamwork"
        loaded = mod.load(ledger)
        disk = self.state()
        disk["rev"] += 1
        (ledger / "state.json").write_text(json.dumps(disk), encoding="utf-8")
        with self.assertRaises(SystemExit) as caught:
            mod.save(ledger, loaded, {"ev":"succ-ping"})
        self.assertIn("台账刚被别的命令改过，重跑一次", str(caught.exception))
        self.assertEqual(self.state()["rev"], disk["rev"])

    def test_lock_allows_hooks_reads_and_denies_writers(self):
        self.tw("thread", "add", "api", "--session", "old-api")
        self.tw("task", "add", "T1", "--title", "test writer lock")
        self.tw("succ", "start", "lead", "--reason", "swap")
        self.tw("succ", "candidate", "HO1", "lead-next")
        self.tw("succ", "freeze", "HO1", "--stopped", "lead-old idle")
        event = json.dumps({"tool_name":"Bash","tool_input":{"command":"rm " + "-rf /"}})
        before = subprocess.run([sys.executable, str(TW), "bash-guard"], cwd=self.dir, env=self.env,
                                input=event, capture_output=True, text=True)
        self.assertEqual(before.returncode, 0)
        fake_claude = self.dir / "fake-claude"
        fake_codex = self.dir / "fake-codex"
        for cmd in (("context",), ("req", "list"), ("decided", "x"), ("handover", "api"),
                    ("quota", "--config-dir", str(fake_claude), "--codex-dir", str(fake_codex))):
            result = self.tw(*cmd)
            self.assertEqual(result.returncode, 0, (cmd, result.stderr))
        after = subprocess.run([sys.executable, str(TW), "bash-guard"], cwd=self.dir, env=self.env,
                               input=event, capture_output=True, text=True)
        self.assertEqual((after.returncode, after.stdout), (before.returncode, before.stdout))
        for cmd in (("memory", "blocked"), ("log", "blocked"), ("task", "add", "T99", "--title", "blocked"),
                    ("model-log", "--task", "T1", "--type", "code", "--model", "gpt", "--result", "ok"),
                    ("worktree", "api")):
            result = self.tw(*cmd, ok=False)
            self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(self.tw("rebuild", "--apply", ok=False).returncode, 0)

    def test_first_lead_session_with_failed_screen_is_rejected(self):
        st = self.state()
        st["lead"]["session_id"] = None
        st["screens"]["SC9"] = {"seat":"lead","session_id":"first-lead","verdict":"fail"}
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        result = self.tw("lead", "--session", "first-lead", ok=False)
        self.assertIn("关键席位", result.stderr)

    def test_concurrent_request_adds_are_serialized_without_tracebacks(self):
        commands = [[sys.executable, str(TW), "req", "add", f"request {i}"] for i in range(8)]
        procs = [subprocess.Popen(cmd, cwd=self.dir, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True) for cmd in commands]
        results = [p.communicate() + (p.returncode,) for p in procs]
        successes = sum(code == 0 for _, _, code in results)
        for stdout, stderr, code in results:
            self.assertNotIn("Traceback", stderr)
            if code:
                self.assertTrue("台账刚被别的命令改过" in stderr or "ledger was just changed" in stderr, stderr)
        self.assertEqual(len(self.state()["requests"]), successes)

    def test_lead_session_cannot_bypass_open_handover(self):
        self.tw("succ", "start", "lead", "--reason", "swap")
        self.tw("succ", "candidate", "HO1", "lead-next")
        self.tw("succ", "freeze", "HO1", "--stopped", "lead-old idle")
        for args in (("--session", "lead-next"), ("--session", "lead-next", "--force")):
            result = self.tw("lead", *args, ok=False)
            self.assertEqual(result.returncode,1)
            self.assertIn("succ seat or succ abort", result.stderr)
        removed = self.tw("lead", "--session", "lead-next", "--handover", "HO1", ok=False)
        self.assertEqual(removed.returncode,2)

class T71GuardAndSuccession(TwCase):
    def _load_tw(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location("tw_t71_shape", TW); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        return mod

    def test_clear_tmp_empties_only_the_threads_own_tmp_without_following_links(self):
        # R84: threads clear scratch with `worktree <thread> --clear-tmp`, a Python delete, not rm -r through the guard.
        for name in ("api", "ui"):
            if name not in self.state()["threads"]: self.tw("thread", "add", name)
        own = self.dir / ".teamwork" / "private" / "api" / "tmp"; other = self.dir / ".teamwork" / "private" / "ui" / "tmp"
        (own / "smoke-A" / "deep").mkdir(parents=True); (own / "smoke-A" / "deep" / "f").write_text("12345", encoding="utf-8")
        (own / "keep").mkdir(); other.mkdir(parents=True); (other / "x").write_text("x", encoding="utf-8")
        outside = self.dir.parent / f"{self.dir.name}-outside"; outside.mkdir(); (outside / "victim").write_text("v", encoding="utf-8")
        (own / "smoke-A" / "link").symlink_to(outside, target_is_directory=True); (own / "toplink").symlink_to(outside)
        out = json.loads(self.tw("worktree", "api", "--clear-tmp", "smoke-A", "toplink").stdout)
        self.assertEqual((out["removed"], out["freed_bytes"] >= 5), (["smoke-A", "toplink"], True))
        self.assertTrue((outside / "victim").is_file())  # links were removed as links
        self.assertEqual(sorted(p.name for p in own.iterdir()), ["keep"])
        self.assertEqual(json.loads(self.tw("worktree", "api", "--clear-tmp").stdout)["removed"], ["keep"])
        self.assertTrue(own.is_dir() and (other / "x").is_file())
        for bad in ("..", "../ui", "a/b"):
            self.assertNotEqual(self.tw("worktree", "api", "--clear-tmp", bad, ok=False).returncode, 0)
        self.assertTrue((other / "x").is_file())
        moved = self.dir / ".teamwork" / "private" / "ui"; real = self.dir.parent / f"{self.dir.name}-realui"
        moved.rename(real); moved.symlink_to(real, target_is_directory=True)
        self.assertNotEqual(self.tw("worktree", "ui", "--clear-tmp", ok=False).returncode, 0)  # tmp reached via a link
        self.assertTrue((real / "tmp" / "x").is_file())
        linkedproj = self.dir.parent / f"{self.dir.name}-linkedproj"; linkedproj.mkdir()
        elsewhere = self.dir.parent / f"{self.dir.name}-elsewhere"; (elsewhere / "private" / "api" / "tmp" / "victim").mkdir(parents=True)
        (linkedproj / ".teamwork").symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(SystemExit): tw.clear_thread_tmp(linkedproj, "api")  # .teamwork itself is a link
        self.assertTrue((elsewhere / "private" / "api" / "tmp" / "victim").is_dir())

    def test_bash_guard_still_asks_for_rm_r_in_own_tmp(self):
        # the guard no longer parses shell to allow deletes: rm -r / find -delete always ask (use --clear-tmp instead)
        own = self.dir / ".teamwork" / "private" / "api" / "tmp"; own.mkdir(parents=True)
        for command in (f"rm -r {own}/smoke-A", f"S=$(mktemp -d {own}/rb-XXXX) && find $S -mindepth 1 -delete"):
            event = {"tool_name": "Bash", "cwd": str(self.dir), "session_id": "local-api", "tool_input": {"command": command}}
            result = subprocess.run([sys.executable, str(TW), "bash-guard"], cwd=self.dir, env=self.env,
                                    input=json.dumps(event), capture_output=True, text=True)
            self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_generated_fixture_signatures_and_byte_tamper(self):
        import importlib.util
        fixture_dir=FIXTURES
        generator=fixture_dir/"regenerate.py"
        spec=importlib.util.spec_from_file_location("t71_fixture_signatures",generator)
        mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        self.assertEqual(set(mod.SAMPLES),{
            "bridge-transcript-cse_fixture-remote-session.jsonl","t71_real_candidate.jsonl",
            "t71_real_tool_results.jsonl","t71_old_thread.jsonl","t79_hook_start_session.jsonl",
            "t79_hook_create_session.jsonl","t79_nested_desktop_sessions.jsonl","t80_compaction.jsonl"})
        for name in mod.SAMPLES:
            self.assertTrue(mod.manifest_matches(name),name)
        original=fixture_dir/"t71_real_candidate.jsonl"
        tampered=self.dir/"tampered-candidate.jsonl"
        content=original.read_bytes()
        self.assertIn(b"What is the current seat",content)
        tampered.write_bytes(content.replace(b"What is the current seat",b"Xhat is the current seat",1))
        self.assertFalse(mod.manifest_matches("t71_real_candidate.jsonl",tampered))

    def test_generated_fixtures_contain_no_private_markers(self):
        import getpass, importlib.util, re, socket
        tw = importlib.util.module_from_spec(importlib.util.spec_from_file_location("tw_fixture_privacy", TW))
        tw.__spec__.loader.exec_module(tw)
        fixtures=FIXTURES
        texts=[path.read_text(encoding="utf-8") for path in sorted(fixtures.glob("*.jsonl"))]
        home=str(Path.home()).casefold()
        username=getpass.getuser().casefold()
        host=socket.gethostname().casefold()
        private_dir=Path.home()/"Claude-B"/"teamwork-for-claude-code"/".teamwork"
        if not (private_dir/"PRIVATE.md").is_file():
            private_dir=REPO/".teamwork"
        private_terms,_,_=tw.load_private(private_dir)
        private_terms.extend(x for x in (home,username) if x)
        patterns=[re.compile(re.escape(term),re.I) for term in private_terms if term]
        patterns.extend((re.compile(re.escape(host),re.I),
                         re.compile(r"~/\S+",re.I), re.compile(r"(?i)\.pem\b"),
                         re.compile(r"/Users/",re.I), re.compile(r"(?<!fixture)/home/",re.I),
                         re.compile(r"/fixture/fixture/",re.I),
                         re.compile(r"(?:anthropic-skills|plugin):(?!fixture-skill\b)[A-Za-z0-9_.-]+",re.I),
                         re.compile(r"-Users-",re.I), re.compile(r"-home-",re.I),
                         re.compile(r"/private/tmp/claude-",re.I), re.compile(r"/tmp/claude-",re.I),
                         re.compile(r"memory/(?!fixture-note\.md(?:\b|[\"'])?)[^/\s\"']+\.md",re.I),
                         re.compile(re.escape(username),re.I) if username else re.compile(r"(?!)"),
                         re.compile(r"(?<![A-Za-z0-9-])(?=[0-9a-f]*[a-f])[0-9a-f]{12,}(?![A-Za-z0-9-])",re.I),
                         re.compile(r"session_01",re.I),
                         re.compile(r"env_01",re.I), re.compile(r"cse_01",re.I)))
        for text in texts:
            for pattern in patterns:
                self.assertIsNone(pattern.search(text), "fixture privacy marker detected")
            for absolute in re.findall(r"(?<![A-Za-z0-9:+<])/(?:[^\\\s\"'<>()[\]{}|,;]+/)+[^\\\s\"'<>()[\]{}|,;]+", text):
                self.assertTrue(absolute.startswith("/fixture/"), f"non-fixture absolute path in generated fixture: {absolute}")
                self.assertNotIn("/fixture/fixture/",absolute, f"repeated fixture root in generated fixture: {absolute}")

    def test_regenerate_table_covers_every_fixture_file(self):
        import importlib.util
        fixture_dir = FIXTURES
        spec = importlib.util.spec_from_file_location("fixture_table", fixture_dir / "regenerate.py")
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        names = {path.name for path in fixture_dir.glob("*.jsonl")}
        self.assertEqual(names, set(mod.SAMPLES))
        self.assertEqual(names, set(json.loads((fixture_dir / "MANIFEST.json").read_text(encoding="utf-8"))))
        placed = mod.place("t71_old_thread.jsonl", self.dir / "placed-config", session="session_fixture")
        self.assertTrue(placed.is_file())
        self.assertTrue((placed.parent.parent.parent / "bridge-transcript-cse_session_fixture.jsonl").is_file())
        self.assertEqual(mod.sample("t71_old_thread.jsonl")[0]["type"], "user")

    def test_nested_metadata_fixture_preserves_real_lead_id_relationship(self):
        rows=[json.loads(line) for line in (FIXTURES/"t79_nested_desktop_sessions.jsonl").read_text(encoding="utf-8").splitlines()]
        child,parent=rows
        self.assertTrue(child["spawnedFrom"]["sessionId"].startswith("local_"))
        self.assertEqual(parent["bridgeSessionIds"],["cse_fixture_lead"])
        state=self.state()
        state["lead"]["session_id"]="session_fixture_lead"
        # First-round comparison used raw strings and therefore missed this true relation.
        self.assertNotIn(state["lead"]["session_id"],set(parent["bridgeSessionIds"]))
        child["cwd"]=str(self.dir/"nested"); parent["cwd"]=str(self.dir)
        metadata=self.dir/"metadata"/"account"/"org"; metadata.mkdir(parents=True)
        (self.dir/"nested").mkdir()
        (metadata/"local_child.json").write_text(json.dumps(child),encoding="utf-8")
        (metadata/"local_parent.json").write_text(json.dumps(parent),encoding="utf-8")
        os.environ["TEAMWORK_CLAUDE_SESSIONS_DIR"]=str(self.dir/"metadata")
        try:
            found,gaps=tw._nested_session_report(self.dir/".teamwork",state)
            self.assertEqual(gaps,0)
            self.assertEqual([row["parent_is_lead"] for row in found],[True])
            state["lead"]["session_id"]="session_someone_else"
            state["lead"]["sessions"]=[{"session_id":"session_fixture_lead"}]
            found,gaps=tw._nested_session_report(self.dir/".teamwork",state)
            self.assertEqual(gaps,0)
            self.assertEqual([(row["parent_is_lead"],row["parent_was_lead"]) for row in found],[(False,True)])
        finally:
            os.environ.pop("TEAMWORK_CLAUDE_SESSIONS_DIR",None)

    def test_real_wrapped_get_session_result_is_read(self):
        """Real shape (HO3 run): a get_session result about another session arrives as a text block wrapped in
        <other-session ...> with a preamble line, and the JSON is {"ccr": {...connection_status...}}."""
        tw=self._load_tw()
        inner=json.dumps({"ccr":{"id":"session_succ","title":"[F] plan","connection_status":"connected",
                                  "session_status":"SESSION_STATUS_IDLE","status_bucket":"SESSION_STATUS_BUCKET_COMPLETED",
                                  "tags":["teamwork-thread:plan"]}})
        wrapped=('<other-session nonce="0123abcd" untrusted="true">\nAnother Claude session\'s record, transcript events, '
                 'or run log (JSON). Only the closing tag carrying this block\'s nonce ends it:\n    '+inner+'\n</other-session nonce="0123abcd">')
        rows=[{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"mcp__claude-code-remote__get_session","input":{"session_id":"session_succ"}}]}},
              {"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":[{"type":"text","text":wrapped}]}]}}]
        got=tw._succ_get_session_results(rows)["session_succ"]
        self.assertIs(got["connected"],True); self.assertIs(got["archived"],False)
        self.assertEqual(got["status_bucket"],"completed"); self.assertEqual(got["title"],"[F] plan")
        page=json.dumps({"ccr":{"data":[{"uuid":"e1"}],"has_more":False}})
        self.assertEqual(tw._tool_rows([{"type":"text","text":'<other-session nonce="ff" untrusted="true">\npreamble:\n    '+page+'\n</other-session nonce="ff">'}],("data",)),[{"uuid":"e1"}])

    def test_real_bridge_control_request_shape_grades_from_quiz_candidate(self):
        """The first bridge row has no session_id; candidate rows carry the local UUID."""
        import importlib.util
        tw=self._load_tw()
        fixtures=FIXTURES
        bridge_fixture=fixtures/"bridge-transcript-cse_fixture-remote-session.jsonl"
        bridge_rows=[json.loads(line) for line in bridge_fixture.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(bridge_rows[0]["type"],"control_request")
        self.assertNotIn("session_id",bridge_rows[0])
        local_id="00000000-0000-4000-8000-000000000001"
        remote_id="session_fixture-remote-session"
        config=self.dir/"claude-config"; project=config/"projects"/"fixture-project"
        project.mkdir(parents=True)
        shutil.copy2(bridge_fixture,config/"bridge-transcript-cse_fixture-remote-session.jsonl")
        candidate=project/f"{local_id}.jsonl"
        shutil.copy2(fixtures/"t71_real_candidate.jsonl",candidate)
        rows,bad=tw._transcript_rows(candidate)
        self.assertEqual(bad,0)
        self.assertTrue(rows)
        self.assertTrue(all(row.get("sessionId")==local_id for row in rows if row.get("sessionId")))
        quiz_prompt=next(row for row in rows if "What is the current seat's branch name?" in json.dumps(row))
        answer_lines=["fixture-branch","0"]
        # 7154a0e's first-line-only lookup sees no bridge session_id and therefore
        # feeds no rows to the zero-tool check.
        self.assertFalse(quiz_prompt.get("session_id"))
        self.assertFalse(tw._succ_zero_tool_window([],"What is the current seat's branch name?",answer_lines))

        self.tw("thread","add","api","--session","old")
        self.tw("succ","start","api","--reason","real bridge shape")
        st=self.state(); st["lead"]["session_id"]="lead"
        key=self.dir/"HO1-real-shape.json"
        key.write_text(json.dumps({"items":[
            {"question":"What is the current seat's branch name?","keywords":["fixture-branch"]},
            {"question":"How many unpushed commits are there?","keywords":["0"]}] }),encoding="utf-8")
        rec=st["handovers"]["HO1"]
        rec.update(phase="quiz",candidates=[{"session_id":remote_id},{"session_id":"other-candidate"}],
                   quiz={"key_file":str(key),"candidate":remote_id})
        (self.dir/".teamwork/state.json").write_text(json.dumps(st),encoding="utf-8")
        answers=self.dir/"real-shape-answers.txt"; answers.write_text("fixture-branch\n0\n",encoding="utf-8")
        result=self.tw("succ","grade","HO1","--answers",str(answers),"--caller","lead","--config-dir",str(config))
        self.assertIn("Zero tool calls: passed",result.stdout)
        self.assertTrue(self.state()["handovers"]["HO1"]["quiz"]["passed"])
        self.assertEqual(self.state()["handovers"]["HO1"]["quiz"]["candidate"],remote_id)

        wrong_project=config/"projects"/"wrong-project"; wrong_project.mkdir()
        mismatched=wrong_project/f"{local_id}.jsonl"
        wrong=[dict(row) for row in rows]
        dialogue_index=next(i for i,row in enumerate(wrong) if row.get("message"))
        wrong[dialogue_index]={**wrong[dialogue_index],"sessionId":"00000000-0000-4000-8000-000000000002"}
        mismatched.write_text("\n".join(json.dumps(row,ensure_ascii=False) for row in wrong)+"\n",encoding="utf-8")
        self.assertEqual(tw._succ_transcript_rows(mismatched,remote_id,[str(config)]),[])

    def setUp(self):
        super().setUp()
        self._keys_tmp=tempfile.TemporaryDirectory(prefix="tw-t71-keys-")
        self.addCleanup(self._keys_tmp.cleanup)
        self.env["TW_KEYS_DIR"]=self._keys_tmp.name

    def fixture_old_session_start(self, seat="demo"):
        st=self.state()
        st["lead"]["session_id"]="session_fixture-lead"
        sessions=st["threads"][seat].setdefault("sessions",[])
        if sessions:
            sessions[-1]["from"]="2025-01-01T00:00:00+00:00"
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st),encoding="utf-8")

    def test_succ_shared_parsers_and_empty_pack_fail_closed(self):
        tw=self._t71_module()
        self.assertIsNone(tw._tool_json("see items [1] and [2]"))
        self.assertIsNone(tw._tool_json('{"ok":1}\n{"ok":2}'))
        event={"uuid":"e1","timestamp":"2026-11-24T00:00:00Z","message":{"role":"user","content":"check export"}}
        wrapped='<other-session nonce="x">preamble\n'+json.dumps({"ccr":{"data":[event],"has_more":False}})+'\n</other-session nonce="x">'
        self.assertEqual(tw._tool_rows(wrapped),[event])
        wrapped_file=self.dir/"wrapped-events.json"
        wrapped_file.write_text(wrapped,encoding="utf-8")
        self.assertEqual([row["type"] for row in tw._succ_transcript_rows(wrapped_file)], ["user"])
        self.tw("thread","add","demo","--session","old-seat")
        self.tw("succ","start","demo","--reason","replace")
        self.tw("succ","candidate","HO1","candidate-seat")
        self.tw("succ","freeze","HO1","--stopped","old-seat stopped")
        empty=self.dir/"empty.jsonl"; empty.write_text("\n",encoding="utf-8")
        failed=self.tw("succ","pack","HO1","--transcript",str(empty),ok=False)
        self.assertIn("zero readable records",failed.stderr)
        self.assertEqual(self.state()["handovers"]["HO1"]["phase"],"frozen")

    def test_succ_transcript_owner_required_for_explicit_exports(self):
        tw=self._t71_module()
        expected="session_expected"
        event=lambda sid:{"session_id":sid,"message":{"role":"user","content":"owned event"}}
        shapes=[
            lambda sid:('<other-session nonce="x">preamble\n'+json.dumps({"ccr":{"id":sid,"data":[event(sid)]}})+'\n</other-session nonce="x">'),
            lambda sid:json.dumps({"ccr":{"id":sid,"data":[event(sid)]}}),
            lambda sid:json.dumps({"data":[event(sid)]}),
            lambda sid:json.dumps({"data":[
                {"message":{"role":"assistant","content":[{"type":"tool_use","id":"events-call","name":"list_events","input":{"session_id":sid}}]}},
                {"message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"events-call","content":"event export"}]}}
            ]}),
        ]
        for index,make in enumerate(shapes):
            for sid,accepted in (("session_other",False),(expected,True)):
                path=self.dir/f"export-{index}-{sid}.json"
                path.write_text(make(sid),encoding="utf-8")
                rows=tw._succ_transcript_rows(path,expected,[])
                self.assertEqual(bool(rows),accepted,(index,sid,rows))

    def test_succ_transcript_rejects_non_uuid_other_session_fixture(self):
        tw=self._t71_module()
        fixture=FIXTURES/"t71_real_candidate.jsonl"
        self.assertEqual(tw._succ_transcript_rows(fixture,"session_expected",[]),[])
        config=self.dir/"owner-config"; config.mkdir()
        local="00000000-0000-4000-8000-000000000021"
        (config/"bridge-transcript-cse_expected.jsonl").write_text(json.dumps({"session_id":local})+"\n",encoding="utf-8")
        unrelated_filename=self.dir/"00000000-0000-4000-8000-000000000099.jsonl"
        unrelated_filename.write_text(json.dumps({"type":"user","sessionId":local,"message":{"content":"owned"}})+"\n",encoding="utf-8")
        self.assertEqual(len(tw._succ_transcript_rows(unrelated_filename,"session_expected",[config])),1)

    def test_succ_transcript_ignores_platform_metadata_but_requires_owner_for_content(self):
        tw=self._t71_module()
        config=self.dir/"metadata-owner-config"; config.mkdir()
        local="00000000-0000-4000-8000-000000000001"
        (config/"bridge-transcript-cse_expected.jsonl").write_text(
            json.dumps({"session_id":local})+"\n",encoding="utf-8")
        fixture=FIXTURES/"t71_real_candidate.jsonl"
        source=[json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines() if line.strip()]
        metadata=source[0]
        self.assertIn(metadata.get("type"),{"file-history-snapshot","file-history-delta"})
        self.assertFalse(any(key in metadata for key in ("message","content","text","tool_use","tool_result")))
        path=self.dir/"metadata-and-dialogue.jsonl"
        rows=source
        path.write_text("\n".join(json.dumps(row,ensure_ascii=False) for row in rows)+"\n",encoding="utf-8")
        accepted=tw._succ_transcript_rows(path,"session_expected",[config])
        self.assertTrue(accepted)
        self.assertTrue(all(row.get("sessionId")==local for row in accepted))
        rows.append({"type":"user","message":{"role":"user","content":"unattributed dialogue"}})
        path.write_text("\n".join(json.dumps(row,ensure_ascii=False) for row in rows)+"\n",encoding="utf-8")
        self.assertEqual(tw._succ_transcript_rows(path,"session_expected",[config]),[])
        path.write_text(json.dumps(metadata)+"\n",encoding="utf-8")
        self.assertEqual(tw._succ_transcript_rows(path,"session_expected",[config]),[])

    def test_succ_transcript_rejects_content_outside_message(self):
        tw=self._t71_module()
        config=self.dir/"outside-content-config"; config.mkdir()
        local="00000000-0000-4000-8000-000000000031"
        (config/"bridge-transcript-cse_expected.jsonl").write_text(json.dumps({"session_id":local})+"\n",encoding="utf-8")
        path=self.dir/"outside-content.jsonl"
        path.write_text(json.dumps({"type":"assistant","sessionId":local,"timestamp":"2026-11-24T00:00:01Z",
                                   "text":"post-freeze dialogue","tool_use":[{"type":"tool_use","name":"Bash","input":{"command":"git push origin HEAD"}}]})+"\n",encoding="utf-8")
        self.assertIsNone(tw._row_blocks(json.loads(path.read_text(encoding="utf-8"))))
        self.assertEqual(tw._succ_transcript_rows(path,"session_expected",[config]),[])

    def test_thread_set_handover_uses_the_shared_gate(self):
        self.tw("thread","add","api","--session","old")
        self.tw("succ","start","api","--reason","swap")
        before=self.state()
        for hid in ("HO1","HO99"):
            result=self.tw("thread","set","api","--session","stranger","--handover",hid,ok=False)
            self.assertEqual(result.returncode,1)
            self.assertIn("--handover blocked",result.stderr)
            self.assertEqual(self.state()["threads"],before["threads"])
            self.assertEqual(self.state()["handovers"],before["handovers"])

    def test_retire_check_rejects_bash_writes_after_freeze(self):
        self.tw("thread","add","demo","--session","old-seat")
        self.fixture_old_session_start()
        self.tw("succ","start","demo","--reason","replace")
        self.tw("succ","candidate","HO1","candidate-seat")
        self.tw("succ","freeze","HO1","--stopped","old-seat stopped")
        st=self.state(); st["threads"]["demo"].update(session_id="candidate-seat",status="starting")
        st["handovers"]["HO1"].update(phase="seated",successor="candidate-seat")
        (self.dir/".teamwork/state.json").write_text(json.dumps(st),encoding="utf-8")
        stamp=(dt.datetime.fromisoformat(st["handovers"]["HO1"]["freeze"]["at"])+dt.timedelta(seconds=1)).isoformat()
        transcript=self.dir/"bash-write.jsonl"
        transcript.write_text(json.dumps({"type":"assistant","timestamp":stamp,"message":{"content":[
            {"type":"tool_use","name":"Bash","input":{"command":"git push origin HEAD > /tmp/log"}}]}})+"\n",encoding="utf-8")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], transcript)
        failed=self.tw("succ","retire-check","HO1","--caller","session_fixture-lead","--transcript",str(transcript),ok=False)
        self.assertIn("Bash writes=git push origin HEAD > /tmp/log",failed.stderr)

    def test_retire_check_uses_primary_get_session_and_ignores_subagent(self):
        self.tw("thread","add","demo","--session","old-seat")
        self.fixture_old_session_start()
        self.tw("succ","start","demo","--reason","replace")
        self.tw("succ","candidate","HO1","candidate-seat")
        self.tw("succ","freeze","HO1","--stopped","old-seat stopped")
        state=self.state(); seat_at=dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        freeze=seat_at-dt.timedelta(seconds=1)
        state["threads"]["demo"].update(session_id="candidate-seat",status="starting")
        state["handovers"]["HO1"].update(phase="seated",successor="candidate-seat",steps={"seat":seat_at.isoformat()})
        (self.dir/".teamwork/state.json").write_text(json.dumps(state),encoding="utf-8")
        old_rows=[json.loads(line) for line in (FIXTURES/"t71_old_thread.jsonl").read_text(encoding="utf-8").splitlines()]
        for row in old_rows: row["timestamp"]=(freeze-dt.timedelta(seconds=1)).isoformat()
        old=self.dir/"old.jsonl"; old.write_text("\n".join(json.dumps(row) for row in old_rows)+"\n",encoding="utf-8")
        config=self.dir/"claude-config"; project=config/"projects"/"fixture"; local="00000000-0000-4000-8000-000000000001"
        child=project/local/"subagents"; child.mkdir(parents=True)
        bridge=FIXTURES/"bridge-transcript-cse_fixture-remote-session.jsonl"
        shutil.copy2(bridge,config/"bridge-transcript-cse_fixture-lead.jsonl")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], old, config)
        def records(stamp):
            return [{"type":"assistant","sessionId":local,"timestamp":stamp,"message":{"content":[{"type":"tool_use","id":"g","name":"get_session","input":{"session_id":"candidate-seat"}}]}},
                    {"type":"user","sessionId":local,"timestamp":stamp,"message":{"content":[{"type":"tool_result","tool_use_id":"g","content":json.dumps({"connected":True,"status":"connected","title":state["threads"]["demo"]["title"],"tags":["teamwork-thread:demo"]})}]}}]
        newer=records((seat_at+dt.timedelta(seconds=6)).isoformat())
        older=records((seat_at-dt.timedelta(hours=2)).isoformat())
        primary=project/f"{local}.jsonl"; primary.parent.mkdir(parents=True,exist_ok=True)
        primary.write_text("\n".join(json.dumps(row) for row in newer)+"\n",encoding="utf-8")
        (child/"agent-old.jsonl").write_text("\n".join(json.dumps(row) for row in older)+"\n",encoding="utf-8")
        success=self.tw("succ","retire-check","HO1","--caller","session_fixture-lead","--transcript",str(old),"--config-dir",str(config))
        self.assertIn('"used": false',success.stdout)
        state=self.state(); state["handovers"]["HO1"].pop("retire_ticket",None)
        (self.dir/".teamwork/state.json").write_text(json.dumps(state),encoding="utf-8")
        primary.unlink()
        failed=self.tw("succ","retire-check","HO1","--caller","session_fixture-lead","--transcript",str(old),"--config-dir",str(config),ok=False)
        self.assertIn("unverified",failed.stderr)

    def test_retire_check_rejects_second_bridge_session_post_freeze_message(self):
        self.tw("thread","add","demo","--session","old-seat")
        self.fixture_old_session_start()
        self.tw("succ","start","demo","--reason","replace")
        self.tw("succ","candidate","HO1","candidate-seat")
        self.tw("succ","freeze","HO1","--stopped","old-seat stopped")
        st=self.state(); freeze=dt.datetime.fromisoformat(st["handovers"]["HO1"]["freeze"]["at"])
        st["threads"]["demo"].update(session_id="candidate-seat",status="starting")
        st["handovers"]["HO1"].update(phase="seated",successor="candidate-seat")
        (self.dir/".teamwork/state.json").write_text(json.dumps(st),encoding="utf-8")
        config=self.dir/"claude-config"; (config/"projects"/"fixture").mkdir(parents=True)
        remote="session_remote-old"
        bridge=config/f"bridge-transcript-cse_{remote.removeprefix('session_')}.jsonl"
        bridge.write_text(json.dumps({"session_id":"local-first"})+"\n"+
                          json.dumps({"session_id":"local-second"})+"\n",encoding="utf-8")
        post_freeze=(freeze+dt.timedelta(seconds=1)).isoformat()
        old=config/"projects"/"fixture"/"local-second.jsonl"
        old.write_text(json.dumps({"type":"user","timestamp":post_freeze,
                                   "message":{"content":"旧会话冻结后仍有用户消息"}},ensure_ascii=False)+"\n",encoding="utf-8")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], old, config, second_local_session="local-first")
        self.env["CLAUDE_CONFIG_DIR"]=str(config)
        import tw
        paths=tw._find_transcripts(remote,[str(config)])
        self.assertIn(old,paths)
        failed=self.tw("succ","retire-check","HO1","--caller","session_fixture-lead","--transcript",str(old),ok=False)
        self.assertEqual(failed.returncode,1)
        self.assertIn("failed A7: post-freeze messages remain",failed.stderr)
        self.assertIn("human=",failed.stderr)

    def test_archive_guard_allows_verified_pending_and_void_candidate(self):
        self.tw("thread","add","api","--session","session_old")
        st=self.state(); st["archive"]["session_old"]={"verified_at":dt.datetime.now().astimezone().isoformat()}
        st["handovers"]={"HO1":{"id":"HO1","phase":"candidate","seat":"api","old_session":"session_old",
            "candidates":[{"session_id":"discarded","void":True}]}}
        (self.dir/".teamwork/state.json").write_text(json.dumps(st),encoding="utf-8")
        event={"cwd":str(self.dir),"tool_name":"mcp__claude-code-remote__archive_session","tool_input":{"session_id":" session_old "}}
        result=subprocess.run([sys.executable,str(TW),"bash-guard"],cwd=self.dir,env=self.env,input=json.dumps(event),capture_output=True,text=True)
        self.assertEqual(result.stdout.strip(),"")
        event["tool_input"]["session_id"]="discarded"
        result=subprocess.run([sys.executable,str(TW),"bash-guard"],cwd=self.dir,env=self.env,input=json.dumps(event),capture_output=True,text=True)
        self.assertEqual(result.stdout.strip(),"")

    def test_accept_receipt_caller_must_be_a_lead(self):
        self.tw("thread","add","demo","--session","old-seat")
        self.fixture_old_session_start()
        self.tw("succ","start","demo","--reason","replace")
        self.tw("succ","candidate","HO1","candidate-seat")
        self.tw("succ","freeze","HO1","--stopped","old-seat stopped")
        sample = self.place_fixture("t71_old_thread.jsonl","session_fixture-lead")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], sample)
        self.tw("succ","pack","HO1","--from-transcript",str(sample))
        receipt=self.dir/"accept.json"; receipt.write_text("{}",encoding="utf-8")
        failed=self.tw("succ","verify","HO1","--accept",str(receipt),"--caller","candidate-seat",ok=False)
        self.assertIn("candidate cannot run verify",failed.stderr)

    def _t71_module(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location("tw_t71_test_module",TW)
        mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        return mod

    def test_t71_peer_message_unwraps_real_wrapper_and_platform_preamble(self):
        mod=self._t71_module()
        wrapped=('The following message was sent from another session:\n\n'
                 '<cross-session-message from-session="fixture-lead">\n'
                 '[tw:fixture] TASK: preserve the fixture.\n&gt; report status\n'
                 '</cross-session-message>')
        self.assertEqual(mod._peer_message(wrapped),
                         ("fixture-lead","[tw:fixture] TASK: preserve the fixture.\n> report status"))
        self.assertEqual(mod._peer_message("  [tw:fixture] plain message  "),
                         (None,"[tw:fixture] plain message"))

    def test_t71_quiz_uses_full_entries_without_tw_header_and_deduplicates(self):
        mod=self._t71_module(); pack=self.dir/"pack"; pack.mkdir()
        (pack/"words.md").write_text(
            "[L#1] [tw:fixture] TASK: preserve status\nThe branch is tw/demo/demo and checksum is 4f92.\n"
            "[P#2] [tw:fixture] TASK: preserve status\nThe branch is tw/demo/demo and checksum is 4f92.\n",
            encoding="utf-8")
        material=mod._succ_quiz_material({"pack_dir":str(pack)},self.state())
        lead_items=[x for x in material if "lead instruct" in x["question"]]
        questions=[x["question"] for x in lead_items]
        self.assertEqual(len(questions),len(set(questions)))
        self.assertEqual(len(questions),1)
        self.assertNotIn("TASK",questions[0])
        self.assertTrue(any("demo" in key for key in lead_items[0]["keywords"]))
        self.assertTrue(any("checksum" in key or "4f92" in key for key in lead_items[0]["keywords"]))

    def test_t71_yes_no_matching_is_bilingual_and_boundary_safe(self):
        mod=self._t71_module()
        for answer in ("yes","是","对"):
            self.assertTrue(mod._succ_hit("yes",answer))
        for answer in ("no","否","不"):
            self.assertTrue(mod._succ_hit("no",answer))
        self.assertFalse(mod._succ_hit("yes","不是"))
        self.assertFalse(mod._succ_hit("no","none not nobody"))

    def test_t71_repack_replaces_fixed_set_and_keeps_old_set_out_of_manifest(self):
        self.tw("thread","add","demo","--session","old-seat")
        self.fixture_old_session_start()
        self.tw("succ","start","demo","--reason","fixture replacement")
        self.tw("succ","candidate","HO1","candidate-seat")
        self.tw("succ","freeze","HO1","--stopped","old-seat stopped")
        transcript=self.place_fixture("t71_old_thread.jsonl", "session_fixture-lead")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], transcript)
        packed=self.tw("succ","pack","HO1","--from-transcript",str(transcript))
        self.assertIn('"only_read_fact": "none — 原因：填写没有用户原话可引用的原因"',packed.stdout)
        self.assertIn("## Rules\\n",packed.stdout)
        self.assertIn("## Only-known-after-reading fact\\n",packed.stdout)
        self.assertIn("## Unresolved\\n",packed.stdout)
        self.tw("succ","pack","HO1","--from-transcript",str(transcript))
        pack=Path(self.state()["handovers"]["HO1"]["pack_dir"])
        for name in ("BRIEF.md","HANDOFF.md","words.md","MANIFEST.json"):
            self.assertTrue((pack/name).is_file(),name)
        archived=list((pack/"superseded").glob("*/"))
        self.assertTrue(archived)
        self.assertTrue(all((p/"BRIEF.md").is_file() for p in archived))
        manifest=json.loads((pack/"MANIFEST.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["files"])
        self.assertFalse(any("superseded" in name for name in manifest["files"]))
        self.assertEqual(self._t71_module()._succ_pack_gate(pack),[])

    def test_t71_pack_classifies_lead_peer_candidate_and_trims_url_punctuation(self):
        self.tw("thread","add","demo","--session","old-seat")
        self.fixture_old_session_start()
        st=self.state(); st["lead"]={"session_id":"fixture-lead","sessions":[{"session_id":"fixture-old-lead"}]}
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st),encoding="utf-8")
        self.tw("succ","start","demo","--reason","fixture replacement")
        self.tw("succ","candidate","HO1","candidate-seat")
        self.tw("succ","freeze","HO1","--stopped","old-seat stopped")
        rows=[json.loads(x) for x in (FIXTURES/"t71_old_thread.jsonl").read_text().splitlines()]
        for row in rows:
            if row.get("type")=="user" and "<cross-session-message" in str(row.get("message",{}).get("content","")):
                row["message"]["content"]=("platform note\n\n<cross-session-message from-session=\"fixture-lead\">\n"
                    "[tw:fixture] TASK: preserve lead data.\n&gt; exact marker\n</cross-session-message>")
                break
        rows.extend([
            {"type":"user","timestamp":"2026-01-01T00:01:00Z","_handover_subagent":True,
             "message":{"content":"<cross-session-message from-session=\"fixture-peer\">\n[tw:fixture] TASK: verify peer data.\n</cross-session-message>"}},
            {"type":"user","timestamp":"2026-01-01T00:02:00Z","_handover_subagent":True,
             "message":{"content":"<cross-session-message from-session=\"candidate-seat\">\n[tw:fixture] TASK: candidate test traffic.\n</cross-session-message>"}},
            {"type":"user","timestamp":"2026-01-01T00:02:30Z","_handover_subagent":True,
             "message":{"content":"<cross-session-message from-session=\"fixture-old-lead\">\n[tw:fixture] TASK: preserve historical lead decision.\n</cross-session-message>"}},
            {"type":"assistant","timestamp":"2026-01-01T00:03:00Z","message":{"content":[
                {"type":"tool_use","id":"url-fixture","name":"Bash","input":{"command":"python -c 'print(1)'"}},
                {"type":"tool_result","tool_use_id":"url-fixture","content":"https://example.invalid/path).,;]"}]}}])
        source=self.dir/"ho2-shaped.jsonl"; source.write_text("\n".join(json.dumps(x) for x in rows)+"\n",encoding="utf-8")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], source)
        self.tw("succ","pack","HO1","--from-transcript",str(source))
        pack=Path(self.state()["handovers"]["HO1"]["pack_dir"])
        words=(pack/"words.md").read_text(encoding="utf-8")
        self.assertIn("[L#1]",words); self.assertIn("exact marker",words)
        self.assertIn("[P#3]",words); self.assertIn("verify peer data",words)
        self.assertIn("[L#4]",words); self.assertIn("historical lead decision",words)
        self.assertNotIn("candidate test traffic",words)
        facts=json.loads((pack/"facts.json").read_text(encoding="utf-8"))
        self.assertEqual(facts["urls"],["https://example.invalid/path"])

    def test_t71_pack_strips_all_requested_url_trailing_punctuation(self):
        self.tw("thread","add","demo","--session","old-seat")
        self.fixture_old_session_start()
        self.tw("succ","start","demo","--reason","fixture replacement")
        self.tw("succ","candidate","HO1","candidate-seat")
        self.tw("succ","freeze","HO1","--stopped","old-seat stopped")
        rows=[{"type":"assistant","timestamp":"2026-01-01T00:00:00Z","message":{"content":[
            {"type":"tool_use","id":"url-fixture","name":"Bash","input":{"command":"python -c 'print(1)'"}},
            {"type":"tool_result","tool_use_id":"url-fixture","content":"https://example.invalid/path).,;]"}]}}]
        source=self.dir/"url-shaped.jsonl"; source.write_text(json.dumps(rows[0])+"\n",encoding="utf-8")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], source)
        self.tw("succ","pack","HO1","--from-transcript",str(source))
        pack=Path(self.state()["handovers"]["HO1"]["pack_dir"])
        facts=json.loads((pack/"facts.json").read_text(encoding="utf-8"))
        self.assertEqual(facts["urls"],["https://example.invalid/path"])

    def test_reseat_reclaims_archived_session_after_other_seat_moved_on(self):
        self.tw("thread", "add", "api", "--session", "s_a")
        self.tw("thread", "set", "api", "--status", "archived")
        self.tw("thread", "add", "other", "--session", "s_x")
        self.tw("thread", "reseat", "other", "--session", "s_a")
        self.tw("thread", "reseat", "other", "--session", "s_b")
        result=self.tw("thread", "reseat", "api", "--session", "s_a")
        self.assertIn("ROSTER", result.stderr)
        state=self.state(); api=state["threads"]["api"]
        self.assertEqual((api["session_id"],api["status"]),("s_a","starting"))
        self.assertEqual(api["sessions"][-1]["session_id"],"s_a")
        self.assertIsNone(api["sessions"][-1].get("to"))
        self.assertTrue(api["sessions"][-2].get("to"))
        self.assertEqual(api["sessions"][-2]["session_id"],"s_a")
        self.assertEqual(json.loads((self.dir/".teamwork"/"log.jsonl").read_text().splitlines()[-2])["ev"],"thread-reseat")
        board=self.tw("board").stdout
        self.assertIn("s_a",board)
        self.assertIn("api | rc | session_id=s_a",board)

    def test_reseat_rejects_live_occupied_and_self_session_without_state_change(self):
        self.tw("thread","add","api","--session","s_api")
        self.tw("thread","add","other","--session","s_other")
        before=self.state()
        occupied=self.tw("thread","reseat","api","--session","s_other",ok=False)
        self.assertIn("already holds the other seat",occupied.stderr)
        self.assertEqual(self.state()["threads"],before["threads"])
        self_session=self.tw("thread","reseat","api","--session","s_api",ok=False)
        self.assertIn("already holds the api seat",self_session.stderr)
        self.assertEqual(self.state()["threads"],before["threads"])

    def test_set_session_restores_archived_seat_through_shared_path(self):
        self.tw("thread","add","api","--session","old-api")
        self.tw("thread","set","api","--status","archived")
        result=self.tw("thread","set","api","--session","new-api","--status","working")
        self.assertIn("ROSTER",result.stderr)
        seat=self.state()["threads"]["api"]
        self.assertEqual((seat["session_id"],seat["status"]),("new-api","working"))
        self.assertEqual(json.loads((self.dir/".teamwork"/"log.jsonl").read_text().splitlines()[-1])["ev"],"thread-reseat")
        self.tw("thread","set","api","--status","working")
        self.assertTrue(seat["sessions"][-2].get("to"))
        self.assertIsNone(seat["sessions"][-1].get("to"))

    def test_handover_reseat_gate_requires_verified_and_graded_successor(self):
        self.tw("thread","add","api","--session","old")
        self.tw("succ","start","api","--reason","swap")
        self.tw("succ","candidate","HO1","candidate")
        before=self.state()
        result=self.tw("thread","reseat","api","--session","candidate","--handover","HO1",ok=False)
        self.assertIn("verify",result.stderr)
        self.assertEqual(self.state()["threads"],before["threads"])
        self.assertEqual(self.state()["handovers"],before["handovers"])

    def test_handover_reseat_rejects_stranger_without_state_change(self):
        self.tw("thread","add","api","--session","old")
        self.tw("succ","start","api","--reason","swap")
        before=self.state()
        result=self.tw("thread","reseat","api","--session","stranger","--handover","HO1",ok=False)
        self.assertEqual(result.returncode,1)
        self.assertIn("candidate",result.stderr)
        after=self.state()
        self.assertEqual(after["threads"],before["threads"])
        self.assertEqual(after["handovers"],before["handovers"])

    def test_open_handover_blocks_plain_reseat_and_set(self):
        self.tw("thread","add","api","--session","old")
        self.tw("succ","start","api","--reason","swap")
        before=self.state()
        for args in (("thread","reseat","api","--session","new"),("thread","set","api","--session","new")):
            result=self.tw(*args,ok=False)
            self.assertEqual(result.returncode,1)
            self.assertIn("succ seat or succ abort",result.stderr)
            self.assertEqual(self.state()["threads"],before["threads"])

    def test_candidate_in_other_open_handover_is_occupied(self):
        self.tw("thread","add","api","--session","old-api")
        self.tw("thread","add","web","--session","old-web")
        st=self.state()
        st["handovers"]={"HO1":{"id":"HO1","seat":"web","phase":"candidate","candidates":[{"session_id":"reserved"}]}}
        (self.dir/".teamwork/state.json").write_text(json.dumps(st),encoding="utf-8")
        before=self.state()
        result=self.tw("thread","set","api","--session","reserved",ok=False)
        self.assertEqual(result.returncode,1)
        self.assertIn("handover HO1",result.stderr)
        self.assertEqual(self.state()["threads"],before["threads"])

    def test_retire_check_lists_peer_rows_and_ignores_fixture_tool_results(self):
        fixture=FIXTURES/"t71_old_thread.jsonl"
        self.tw("thread","add","demo","--session","old-seat")
        self.fixture_old_session_start()
        self.tw("succ","start","demo","--reason","fixture replacement")
        self.tw("succ","candidate","HO1","candidate-seat")
        self.tw("succ","freeze","HO1","--stopped","old-seat stopped")
        state=self.state(); freeze=dt.datetime.fromisoformat(state["handovers"]["HO1"]["freeze"]["at"])
        rows=[json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines()]
        for i,row in enumerate(rows): row["timestamp"]=(freeze+dt.timedelta(seconds=i+1)).isoformat()
        old=self.dir/"old.jsonl"; old.write_text("\n".join(json.dumps(row) for row in rows)+"\n",encoding="utf-8")
        state["threads"]["demo"].update(session_id="candidate-seat",status="starting")
        state["handovers"]["HO1"].update(phase="seated",successor="candidate-seat")
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(state),encoding="utf-8")
        thread=state["threads"]["demo"]
        connected=self.dir/"connected.jsonl"
        result_rows=[{"type":"assistant","message":{"content":[{"type":"tool_use","id":"g","name":"get_session","input":{"session_id":"candidate-seat"}}]}},
                     {"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"g","content":json.dumps({"connected":True,"status":"connected","title":thread["title"],"tags":["teamwork-thread:demo"]})}]}}]
        connected.write_text("\n".join(json.dumps(x) for x in result_rows)+"\n",encoding="utf-8")
        self.synthetic_bridge("old-seat", old)
        failed=self.tw("succ","retire-check","HO1","--caller","session_fixture-lead","--transcript",str(old),"--from-transcript",str(connected),ok=False)
        self.assertIn("human=none",failed.stderr)
        self.assertIn("peer_pending=2026",failed.stderr)
        self.assertIn("writes=none",failed.stderr)

    def test_retire_check_fails_closed_on_missing_row_timestamp(self):
        self.tw("thread","add","demo","--session","old-seat")
        self.fixture_old_session_start()
        self.tw("succ","start","demo","--reason","replace")
        self.tw("succ","candidate","HO1","candidate-seat")
        self.tw("succ","freeze","HO1","--stopped","old-seat stopped")
        st=self.state(); st["threads"]["demo"].update(session_id="candidate-seat",status="starting")
        st["handovers"]["HO1"].update(phase="seated",successor="candidate-seat")
        (self.dir/".teamwork/state.json").write_text(json.dumps(st),encoding="utf-8")
        rows=[json.loads(x) for x in (FIXTURES/"t71_old_thread.jsonl").read_text().splitlines()]
        rows[0].pop("timestamp",None)
        transcript=self.dir/"old-without-time.jsonl"
        transcript.write_text("\n".join(json.dumps(x) for x in rows)+"\n",encoding="utf-8")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], transcript)
        failed=self.tw("succ","retire-check","HO1","--caller","session_fixture-lead","--transcript",str(transcript),ok=False)
        self.assertEqual(failed.returncode,1)
        self.assertIn("rows lack timestamps; unverified",failed.stderr)
        self.assertNotIn("retire_ticket",self.state()["handovers"]["HO1"])

    def test_real_shape_fixture_handover_through_archive_guard(self):
        fixtures=FIXTURES
        fixture_config=self.dir/"fixture-config"
        candidate=self.place_fixture("t71_real_candidate.jsonl", "synthetic-candidate-source", fixture_config)
        old=self.place_fixture("t71_old_thread.jsonl", "synthetic-old-source", fixture_config)
        self.tw("thread","add","demo","--session","old-seat")
        self.fixture_old_session_start()
        self.tw("succ","start","demo","--reason","fixture replacement")
        self.tw("succ","candidate","HO1","candidate-seat")
        self.tw("succ","freeze","HO1","--stopped","old-seat stopped")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], old)
        self.synthetic_bridge("candidate-seat", candidate)
        self.tw("succ","pack","HO1","--from-transcript",str(old))
        pack=Path(self.state()["handovers"]["HO1"]["pack_dir"])
        words=(pack/"words.md").read_text(encoding="utf-8")
        self.assertIn("[L#1]",words); self.assertIn("[L#2]",words)
        quiz=self.tw("succ","quiz","HO1","--caller","session_fixture-lead")
        self.assertGreaterEqual(self.state()["handovers"]["HO1"]["quiz"]["question_count"],3)
        answers=self.dir/"answers.txt"
        key=json.loads(Path(self.state()["handovers"]["HO1"]["quiz"]["key_file"]).read_text(encoding="utf-8"))
        key["items"]=[{"question":"What is the current seat's branch name?","keywords":["fixture-branch"]},
                      {"question":"How many unpushed commits are there?","keywords":["0"]},
                      {"question":"What would you check next?","subjective":True,"keywords":[]}]
        Path(self.state()["handovers"]["HO1"]["quiz"]["key_file"]).write_text(json.dumps(key),encoding="utf-8")
        answers.write_text("fixture-branch\n0\n\n",encoding="utf-8")
        self.tw("succ","grade","HO1","--answers",str(answers),"--caller","session_fixture-lead","--from-transcript",str(candidate))
        self.assertTrue(self.state()["handovers"]["HO1"]["quiz"]["passed"])
        receipt=self.dir/"accept.json"
        t=self.state()["threads"]["demo"]
        receipt.write_text(json.dumps({"tasks":[],"branch":t["branch"],"head":"-","rules":[],"requests":[],
            "only_read_fact":"none — no user-authored statements were recorded",
            "unresolved":[],"sections":"## Rules / 规则\nnone\n## Only-known-after-reading fact / 只有读过才知道的一条\nnone; no user-authored statements\n## Unresolved / 悬而未决\nnone"}),encoding="utf-8")
        self.tw("succ","verify","HO1","--accept",str(receipt),"--caller","session_fixture-lead")
        self.tw("succ","seat","HO1","candidate-seat")
        connected=self.dir/"connected.jsonl"
        real_tools=[json.loads(line) for line in (fixtures/"t71_real_tool_results.jsonl").read_text(encoding="utf-8").splitlines()]
        calls={}; results=[]
        recorded_after_seat=(dt.datetime.now().astimezone()+dt.timedelta(seconds=30)).isoformat(timespec="seconds")
        def other_session(value):
            return ('<other-session nonce="0123abcd" untrusted="true">\nAnother Claude session record (JSON):\n    '+
                    json.dumps({"ccr":value},ensure_ascii=False)+'\n</other-session nonce="0123abcd">')
        for row in real_tools:
            block=(row.get("message") or {}).get("content",[{}])[0]
            if row.get("type")=="assistant" and "get_session" in str(block.get("name")):
                calls[block["id"]]=block
                if len(calls)==1:
                    block["input"]={"session_id":"candidate-seat"}; results.append(row)
            elif row.get("type")=="user" and block.get("tool_use_id") in calls:
                row["timestamp"]=recorded_after_seat
                block["content"]=[{"type":"text","text":other_session({"id":"candidate-seat","title":t["title"],"connection_status":"connected","session_status":"SESSION_STATUS_IDLE","status_bucket":"SESSION_STATUS_BUCKET_COMPLETED","tags":["teamwork-thread:demo"]})}]
                results.append(row); break
        connected.write_text("\n".join(json.dumps(x) for x in results)+"\n",encoding="utf-8")
        stale=json.loads(json.dumps(results))
        stale[-1]["timestamp"]=(dt.datetime.fromisoformat(self.state()["handovers"]["HO1"]["steps"]["seat"])-dt.timedelta(seconds=1)).isoformat()
        connected.write_text("\n".join(json.dumps(x) for x in stale)+"\n",encoding="utf-8")
        self.synthetic_bridge("session_fixture-lead", connected)
        config=self.dir/"claude-config"; config.mkdir()
        old_rows=self.place_fixture("t71_old_thread.jsonl", "synthetic-retire-source", self.dir/"retire-config")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], old_rows, config)
        stale_result=self.tw("succ","retire-check","HO1","--caller","session_fixture-lead","--transcript",str(old_rows),"--from-transcript",str(connected),ok=False)
        self.assertIn("not newer than seat step",stale_result.stderr)
        wrong_owner=self.tw("succ","retire-check","HO1","--caller","session_fixture-lead","--transcript",str(candidate),"--from-transcript",str(connected),"--config-dir",str(config),ok=False)
        self.assertNotEqual(wrong_owner.returncode,0)
        self.assertIn("belongs to another session; unverified",wrong_owner.stderr)
        self.assertNotIn("retire_ticket",self.state()["handovers"]["HO1"])
        connected.write_text("\n".join(json.dumps(x) for x in results)+"\n",encoding="utf-8")
        self.synthetic_bridge("session_fixture-lead", connected)
        self.tw("succ","retire-check","HO1","--caller","session_fixture-lead","--transcript",str(old),"--from-transcript",str(connected))
        cfg=config
        local="00000000-0000-4000-8000-000000000001"
        bridge=FIXTURES/"bridge-transcript-cse_fixture-remote-session.jsonl"
        shutil.copy2(bridge,cfg/"bridge-transcript-cse_fixture-lead.jsonl")
        scan=self.dir/"hook-cwd"; scan.mkdir()
        event={"cwd":str(scan),"session_id":local,"tool_name":"mcp__claude-code-remote__archive_session","tool_input":{"session_id":"old-seat"}}
        env={**self.env,"CLAUDE_CONFIG_DIR":str(cfg),"TW_GUARD_LOG":str(self.dir/"guard-errors.jsonl")}
        hook=subprocess.run([sys.executable,str(TW),"bash-guard"],cwd=scan,env=env,input=json.dumps(event),capture_output=True,text=True)
        self.assertEqual(json.loads(hook.stdout)["hookSpecificOutput"]["permissionDecision"],"allow",hook.stdout)
        connected.write_text("\n".join(json.dumps(x) for x in results)+"\n",encoding="utf-8")
        archived_call=json.loads(json.dumps(real_tools[2] if len(real_tools)>2 else real_tools[0]))
        archived_result=json.loads(json.dumps(real_tools[3] if len(real_tools)>3 else real_tools[1]))
        call_block=archived_call["message"]["content"][0]; call_block.update(id="fixture-old-get",input={"session_id":"old-seat"})
        archived_result["timestamp"]=recorded_after_seat
        result_block=archived_result["message"]["content"][0]; result_block.update(tool_use_id="fixture-old-get",content=[{"type":"text","text":other_session({"id":"old-seat","session_status":"SESSION_STATUS_ARCHIVED","status_bucket":"SESSION_STATUS_BUCKET_COMPLETED","archived":True})}])
        lead_call=json.loads(json.dumps(archived_call)); lead_result=json.loads(json.dumps(archived_result))
        lead_call["message"]["content"][0].update(id="fixture-lead-get",input={"session_id":"session_fixture-lead"})
        lead_result["timestamp"]=recorded_after_seat
        lead_result["message"]["content"][0].update(tool_use_id="fixture-lead-get",content=[{"type":"text","text":other_session({"id":"session_fixture-lead","connection_status":"connected","session_status":"SESSION_STATUS_IDLE","title":"lead","tags":[]})}])
        with connected.open("a",encoding="utf-8") as f: f.write(json.dumps(archived_call)+"\n"+json.dumps(archived_result)+"\n"+json.dumps(lead_call)+"\n"+json.dumps(lead_result)+"\n")
        self.tw("succ","archived","HO1","--caller","session_fixture-lead","--from-transcript",str(connected))
        self.assertEqual(self.state()["handovers"]["HO1"]["phase"],"retired")

    def test_failed_verify_and_grade_exit_nonzero_without_phase_advance(self):
        self.tw("thread","add","demo","--session","old-seat")
        self.fixture_old_session_start()
        self.tw("succ","start","demo","--reason","fixture replacement")
        self.tw("succ","candidate","HO1","candidate-seat")
        self.tw("succ","freeze","HO1","--stopped","old-seat stopped")
        sample = self.place_fixture("t71_old_thread.jsonl","session_fixture-lead")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], sample)
        self.tw("succ","pack","HO1","--from-transcript",str(sample))
        pack=Path(self.state()["handovers"]["HO1"]["pack_dir"])
        bad=self.dir/"bad.json"
        bad.write_text(json.dumps({"tasks":[],"branch":self.state()["threads"]["demo"]["branch"],"head":"-","rules":[],"requests":[],
            "only_read_fact":"invented quote","unresolved":[],"sections":"## Rules\nnone\n## Only-known-after-reading fact\ninvented quote\n## Unresolved\nnone"}),encoding="utf-8")
        failed=self.tw("succ","verify","HO1","--accept",str(bad),"--caller","session_fixture-lead",ok=False)
        self.assertNotEqual(failed.returncode,0)
        self.assertEqual(self.state()["handovers"]["HO1"]["phase"],"packed")
        self.assertEqual(json.loads(self.tw("succ","status","HO1","--json").stdout)[0]["next"],"verify")
        good=self.dir/"good.json"
        good.write_text(json.dumps({"tasks":[],"branch":self.state()["threads"]["demo"]["branch"],"head":"-","rules":[],"requests":[],
            "only_read_fact":"none — no user-authored statements were recorded","unresolved":[],"sections":"## Rules\nnone\n## Only-known-after-reading fact\nnone; no user-authored statements\n## Unresolved\nnone"}),encoding="utf-8")
        self.tw("succ","verify","HO1","--accept",str(good),"--caller","session_fixture-lead")
        self.tw("succ","quiz","HO1","--caller","session_fixture-lead")
        wrong=self.dir/"wrong.txt"; wrong.write_text("wrong\nwrong\nwrong\nwrong\n",encoding="utf-8")
        failed_grade=self.tw("succ","grade","HO1","--answers",str(wrong),"--caller","session_fixture-lead","--from-transcript",str(self.place_fixture("t71_real_candidate.jsonl","session_fixture-lead")),ok=False)
        self.assertNotEqual(failed_grade.returncode,0)
        self.assertEqual(self.state()["handovers"]["HO1"]["phase"],"quiz")
        self.assertEqual(json.loads(self.tw("succ","status","HO1","--json").stdout)[0]["next"],"grade")

    def test_verify_self_lists_items_and_todo_pack_is_rejected(self):
        self.tw("thread","add","demo","--session","old-seat")
        self.fixture_old_session_start()
        self.tw("succ","start","demo","--reason","fixture replacement")
        self.tw("succ","candidate","HO1","candidate-seat")
        self.tw("succ","freeze","HO1","--stopped","old-seat stopped")
        sample = self.place_fixture("t71_old_thread.jsonl","session_fixture-lead")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], sample)
        self.tw("succ","pack","HO1","--from-transcript",str(sample))
        out=self.tw("succ","verify","HO1","--self").stdout
        for name in ("tasks","branch","head","rules","requests","pack_integrity","only_read_fact"):
            self.assertIn("Verification "+name+":",out)
        import importlib.util
        spec=importlib.util.spec_from_file_location("tw_module",TW); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        pack=self.dir/"bad-pack"; (pack/"raw").mkdir(parents=True)
        (pack/"BRIEF.md").write_text("## Current facts\nstate\n",encoding="utf-8")
        (pack/"words.md").write_text("",encoding="utf-8")
        (pack/"HANDOFF.md").write_text("## Rules\n- TODO\n## Only-known-after-reading fact\n- TODO\n## Unresolved\n- TODO\n",encoding="utf-8")
        (pack/"raw/old.jsonl").write_text("{}\n",encoding="utf-8")
        (pack/"MANIFEST.json").write_text(json.dumps({"files":{},"gaps":[]}),encoding="utf-8")
        missing=mod._succ_pack_gate(pack)
        self.assertTrue(any("TODO" in x for x in missing)); self.assertTrue(any("words.md" in x for x in missing))

    def test_only_read_fact_none_is_not_applicable_only_when_pack_has_no_quotes(self):
        self.tw("thread","add","demo","--session","old-seat")
        self.fixture_old_session_start()
        self.tw("succ","start","demo","--reason","fixture replacement")
        self.tw("succ","candidate","HO1","candidate-seat")
        self.tw("succ","freeze","HO1","--stopped","old-seat stopped")
        sample = self.place_fixture("t71_old_thread.jsonl","session_fixture-lead")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], sample)
        self.tw("succ","pack","HO1","--from-transcript",str(sample))
        pack=Path(self.state()["handovers"]["HO1"]["pack_dir"])
        receipt={"tasks":[],"branch":self.state()["threads"]["demo"]["branch"],"head":"-","rules":[],"requests":[],
                 "only_read_fact":"none — 包里没有用户原话可引用","unresolved":[],
                 "sections":"## Rules\nnone\n## Only-known-after-reading fact\nnone; 包里没有用户原话可引用\n## Unresolved\nnone"}
        path=self.dir/"receipt.json"; path.write_text(json.dumps(receipt),encoding="utf-8")
        self.tw("succ","verify","HO1","--accept",str(path),"--caller","session_fixture-lead")
        self.assertTrue(self.state()["handovers"]["HO1"]["verify"]["checks"]["only_read_fact"])
        words=pack/"words.md"; words.write_text(words.read_text(encoding="utf-8")+"\n[#1] User explicitly requested a retained original statement.\n",encoding="utf-8")
        # Keep the manifest current so only the receipt applicability changes.
        import hashlib
        mf=json.loads((pack/"MANIFEST.json").read_text(encoding="utf-8")); mf["files"]["words.md"]=hashlib.sha256(words.read_bytes()).hexdigest()
        (pack/"MANIFEST.json").write_text(json.dumps(mf,ensure_ascii=False,indent=2),encoding="utf-8")
        self.tw("succ","verify","HO1","--accept",str(path),"--caller","session_fixture-lead",ok=False)
        self.assertFalse(self.state()["handovers"]["HO1"]["verify"]["checks"]["only_read_fact"])

    def test_archive_guard_denies_live_ids_and_consumes_release_once(self):
        self.tw("thread", "add", "api", "--session", "session_live_api")
        self.tw("thread", "add", "next", "--session", "session_candidate")
        st=self.state(); st["lead"]={"session_id":"session_lead"}
        st["threads"]["next"]["status"]="archived"
        st["handovers"]={"HO1":{"id":"HO1","phase":"seated","seat":"api","old_session":"session_old",
            "successor":"session_successor","candidates":[{"session_id":"session_candidate"}],
            "retire_ticket":{"id":"ticket1","session_id":"session_old","issuer":"session_caller",
                "expires_at":(dt.datetime.now().astimezone()+dt.timedelta(minutes=5)).isoformat(),"used":False}}}
        (self.dir/".teamwork/state.json").write_text(json.dumps(st),encoding="utf-8")
        scan=self.dir/"hook-cwd"; scan.mkdir()
        cfg=self.dir/"claude-config"; cfg.mkdir()
        local="00000000-0000-4000-8000-000000000001"
        bridge=FIXTURES/"bridge-transcript-cse_fixture-remote-session.jsonl"
        shutil.copy2(bridge,cfg/"bridge-transcript-cse_caller.jsonl")
        def hook(target):
            event={"cwd":str(scan),"session_id":local,
                   "tool_name":"mcp__claude-code-remote__archive_session","tool_input":{"session_id":target}}
            env={**self.env,"CLAUDE_CONFIG_DIR":str(cfg),"TW_GUARD_LOG":str(self.dir/"guard-errors.jsonl")}
            return subprocess.run([sys.executable,str(TW),"bash-guard"],cwd=scan,env=env,
                input=json.dumps(event),capture_output=True,text=True)
        expected_reasons={"session_live_api":"在座会话（api）/ active seat session (api)",
                          "session_lead":"lead / lead", "session_candidate":"候选会话 / candidate session",
                          "session_successor":"继任会话 / successor session",
                          "session_caller":"调用者自己 / caller session itself"}
        for target,reason in expected_reasons.items():
            result=hook(target); output=json.loads(result.stdout)["hookSpecificOutput"]
            self.assertEqual(output["permissionDecision"],"deny")
            self.assertIn(reason,output["permissionDecisionReason"])
        allowed=hook("session_old")
        self.assertEqual(json.loads(allowed.stdout)["hookSpecificOutput"]["permissionDecision"],"allow",allowed.stdout)
        self.assertTrue(self.state()["handovers"]["HO1"]["retire_ticket"]["used"])
        self.assertEqual(hook("session_old").stdout.strip(),"")

    def test_archive_guard_finds_project_from_worktree_cwd(self):
        project=self.dir/"demo"; (project/".teamwork").mkdir(parents=True)
        state=self.state(); state["lead"]["session_id"]="project-lead"
        (project/".teamwork"/"state.json").write_text(json.dumps(state),encoding="utf-8")
        worktree=self.dir/"demo-worktrees"/"api"; worktree.mkdir(parents=True)
        event={"cwd":str(worktree),"tool_name":"mcp__claude-code-remote__archive_session","tool_input":{"session_id":"project-lead"}}
        result=subprocess.run([sys.executable,str(TW),"bash-guard"],cwd=worktree,env=self.env,
            input=json.dumps(event),capture_output=True,text=True)
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"],"deny",result.stdout)

    def test_candidate_cannot_run_own_quiz_or_grade(self):
        self.tw("thread","add","api","--session","session_api")
        self.tw("succ","start","api","--reason","replacement")
        pack=self.dir/"candidate-pack"; pack.mkdir()
        (pack/"BRIEF.md").write_text("## Current facts\nfixture\n",encoding="utf-8")
        key=Path(self._keys_tmp.name)/"HO1-quiz.json"; key.write_text(json.dumps({"items":[{"question":"fixture question","keywords":["fixture"]}]}),encoding="utf-8")
        answers=self.dir/"answers.txt"; answers.write_text("fixture\n",encoding="utf-8")
        st=self.state(); st["handovers"]["HO1"].update(phase="quiz",candidates=[{"session_id":"candidate"}],
            pack_dir=str(pack),quiz={"candidate":"candidate","key_file":str(key)})
        st["lead"]["session_id"]="lead"; st["handovers"]["HO1"]["lead_session"]="lead"
        (self.dir/".teamwork/state.json").write_text(json.dumps(st),encoding="utf-8")
        quiz=self.tw("succ","quiz","HO1","--caller","candidate",ok=False)
        grade=self.tw("succ","grade","HO1","--caller","candidate","--answers",str(answers),ok=False)
        self.assertEqual(quiz.returncode,1); self.assertEqual(grade.returncode,1)
        self.assertIn("candidate cannot run quiz",quiz.stderr)
        self.assertIn("candidate cannot run grade",grade.stderr)
        missing=self.tw("succ","grade","HO1","--caller","lead","--answers",str(answers),ok=False)
        self.assertIn("Zero tool calls: unverified",missing.stdout)
        self.assertNotEqual(missing.returncode,0)

    def test_pack_gate_checks_summary_checksum(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location("tw_module",TW); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        pack=self.dir/"pack"; (pack/"raw").mkdir(parents=True)
        (pack/"BRIEF.md").write_text("# HO1\n\n## Current facts\n",encoding="utf-8")
        (pack/"words.md").write_text("",encoding="utf-8")
        (pack/"HANDOFF.md").write_text("# HANDOFF\n\n## Rules / 规则\n- No user rules were recorded.\n\n## Only-known-after-reading fact\n- No user-authored statement was recorded.\n\n## Unresolved\n- none\n",encoding="utf-8")
        (pack/"raw/old.jsonl").write_text("{}\n",encoding="utf-8")
        summary=pack/"CANDIDATE-SUMMARY.md"; summary.write_text("safe\n",encoding="utf-8")
        files={p.relative_to(pack).as_posix():mod.hashlib.sha256(p.read_bytes()).hexdigest() for p in pack.rglob("*") if p.is_file()}
        (pack/"MANIFEST.json").write_text(json.dumps({"files":files,"gaps":["No user-authored statements in this seat term; transcript extraction completed with zero eligible user messages."]}),encoding="utf-8")
        self.assertEqual(mod._succ_pack_gate(pack),[])
        summary.write_text("changed\n",encoding="utf-8")
        self.assertIn("MANIFEST checksum: CANDIDATE-SUMMARY.md",mod._succ_pack_gate(pack))

    def test_archive_disconnected_direct_pref_is_opt_in(self):
        self.tw("thread","add","api","--session","session_api","--status","idle")
        self.tw("thread","set","api","--disconnected")
        self.tw("archive","verify","session_api","--stopped","idle verified","--config-dir",str(self.dir/"claude"))
        self.assertNotEqual(self.tw("archive","done","session_api",ok=False).returncode,0)
        self.tw("prefs","--set","archive_disconnected_direct=true")
        out=self.tw("archive","done","session_api")
        self.assertIn("direct",out.stdout.lower())

    def test_archive_guard_logs_per_project_state_read_error_and_fails_open(self):
        scan=self.dir/"bad-state-cwd"; state=scan/"broken"/".teamwork"/"state.json"
        state.parent.mkdir(parents=True); state.write_text("{broken",encoding="utf-8")
        log=self.dir/"guard-errors.jsonl"
        env={**self.env,"TW_GUARD_LOG":str(log)}
        event={"cwd":str(scan),"tool_name":"mcp__claude-code-remote__archive_session","tool_input":{"session_id":"target"}}
        result=subprocess.run([sys.executable,str(TW),"bash-guard"],cwd=scan,env=env,input=json.dumps(event),capture_output=True,text=True)
        decision=json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"],"deny")
        self.assertEqual(decision["permissionDecisionReason"],"teamwork 看板读不出来/处理出错，先修好 / board unreadable or guard error")
        rows=[json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[-1]["guard"],"archive-guard")
        self.assertIn(rows[-1]["type"],("ValueError","JSONDecodeError"))

    def test_archive_guard_denies_all_requests_after_project_error(self):
        scan=self.dir/"null-archive-cwd"; team=scan/".teamwork"; team.mkdir(parents=True)
        (team/"state.json").write_text(json.dumps({"lead":{"session_id":"lead-x"},
            "threads":{"demo":{"session_id":"seat-x","status":"working"}},"archive":None}),encoding="utf-8")
        log=self.dir/"null-archive-guard-errors.jsonl"
        env={**self.env,"TW_GUARD_LOG":str(log)}
        for target in ("lead-x","seat-x"):
            event={"cwd":str(scan),"tool_name":"mcp__claude-code-remote__archive_session","tool_input":{"session_id":target}}
            result=subprocess.run([sys.executable,str(TW),"bash-guard"],cwd=scan,env=env,input=json.dumps(event),capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            decision=json.loads(result.stdout)["hookSpecificOutput"]
            self.assertEqual(decision["permissionDecision"],"deny")
            self.assertEqual(decision["permissionDecisionReason"],"teamwork 看板读不出来/处理出错，先修好 / board unreadable or guard error")
        errors=[json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(errors),2)
        self.assertTrue(all(error["guard"]=="archive-guard" for error in errors))
        elsewhere=self.dir/"no-teamwork-project"; elsewhere.mkdir()
        event={"cwd":str(elsewhere),"tool_name":"mcp__claude-code-remote__archive_session","tool_input":{"session_id":"seat-x"}}
        silent=subprocess.run([sys.executable,str(TW),"bash-guard"],cwd=elsewhere,env=env,input=json.dumps(event),capture_output=True,text=True)
        self.assertEqual(silent.stdout,"")

    def test_archive_guard_denies_unreadable_or_incomplete_board(self):
        for label,state in (("corrupt","{broken"),("missing",json.dumps({"prefs":{},"lead":{}}))):
            with self.subTest(label=label):
                scan=self.dir/label/"project"; team=scan/".teamwork"; team.mkdir(parents=True)
                (team/"state.json").write_text(state,encoding="utf-8")
                log=self.dir/f"{label}-guard-errors.jsonl"
                env={**self.env,"TW_GUARD_LOG":str(log)}
                event={"cwd":str(scan),"tool_name":"mcp__claude-code-remote__archive_session","tool_input":{"session_id":"lead-x"}}
                result=subprocess.run([sys.executable,str(TW),"bash-guard"],cwd=scan,env=env,input=json.dumps(event),capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                decision=json.loads(result.stdout)["hookSpecificOutput"]
                self.assertEqual(decision["permissionDecision"],"deny")
                self.assertEqual(decision["permissionDecisionReason"],"teamwork 看板读不出来/处理出错，先修好 / board unreadable or guard error")
                self.assertTrue(log.is_file())

    def test_archive_guard_rejects_unknown_session_when_tasks_missing(self):
        state = self.state()
        state.pop("tasks")
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        event = {"cwd": str(self.dir), "tool_name": "mcp__claude-code-remote__archive_session",
                 "tool_input": {"session_id": "unknown-session"}}
        result = subprocess.run([sys.executable, str(TW), "bash-guard"], cwd=self.dir, env=self.env,
                                input=json.dumps(event), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")

    def test_archive_guard_strips_ids_when_building_deny_table(self):
        st=self.state()
        st["lead"]["session_id"]=" lead-x "
        st["lead"]["sessions"]=[{"session_id":" lead-history "}]
        st["threads"]={"demo":{"session_id":" seat-x ","status":"working","sessions":[]}}
        st["handovers"]={"HO1":{"phase":"candidate","candidates":[{"session_id":" candidate-x "}],"successor":" successor-x "}}
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st),encoding="utf-8")
        for target in ("lead-x","lead-history","seat-x","candidate-x","successor-x"):
            event={"cwd":str(self.dir),"tool_name":"mcp__claude-code-remote__archive_session","tool_input":{"session_id":target}}
            result=subprocess.run([sys.executable,str(TW),"bash-guard"],cwd=self.dir,env=self.env,input=json.dumps(event),capture_output=True,text=True)
            self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"],"deny",target)

    def test_discard_restores_candidate_tracked_change_and_moves_only_new_untracked(self):
        def git(*args):
            return subprocess.run(["git","-C",str(self.dir),*args],capture_output=True,text=True)
        git("init","-q","-b","main"); git("config","user.name","fixture"); git("config","user.email","fixture@example.invalid")
        (self.dir/"candidate.txt").write_text("base\n"); (self.dir/"user.txt").write_text("user base\n")
        git("add","candidate.txt","user.txt"); git("commit","-q","-m","base")
        self.tw("thread","add","api","--session","old")
        st=self.state(); st["repo"]["path"]=str(self.dir)
        (self.dir/".teamwork/state.json").write_text(json.dumps(st),encoding="utf-8")
        (self.dir/"user.txt").write_text("pre-existing user edit\n")
        (self.dir/"user-untracked.txt").write_text("pre-existing untracked\n")
        (self.dir/"other-thread-output.txt").write_text("other thread work\n")
        (self.dir/"snapshot-untracked.txt").write_text("already present at snapshot\n")
        self.tw("succ","start","api","--reason","replace")
        self.tw("succ","candidate","HO1","--plan")
        self.tw("succ","candidate","HO1","candidate")
        (self.dir/"candidate.txt").write_text("candidate edit\n")
        (self.dir/"candidate-new.txt").write_text("candidate new file\n")
        out=self.tw("succ","candidate","HO1","candidate","--discard").stdout
        self.assertEqual((self.dir/"candidate.txt").read_text(),"base\n")
        self.assertEqual((self.dir/"user.txt").read_text(),"pre-existing user edit\n")
        self.assertEqual((self.dir/"user-untracked.txt").read_text(),"pre-existing untracked\n")
        self.assertEqual((self.dir/"other-thread-output.txt").read_text(),"other thread work\n")
        self.assertEqual((self.dir/"snapshot-untracked.txt").read_text(),"already present at snapshot\n")
        saved=self.dir/".teamwork/private/api/tmp/HO1/discarded/candidate-new.txt"
        self.assertFalse((self.dir/"candidate-new.txt").exists())
        self.assertEqual(saved.read_text(),"candidate new file\n")
        self.assertIn("candidate-new.txt",out)

    def test_grade_without_caller_is_rejected_and_cannot_pass_reseat_gate(self):
        self.tw("thread","add","api","--session","old")
        st=self.state(); st["lead"]["session_id"]="lead"
        (self.dir/".teamwork/state.json").write_text(json.dumps(st),encoding="utf-8")
        self.tw("succ","start","api","--reason","replace")
        key=self.dir/"quiz.json"; key.write_text(json.dumps({"items":[{"question":"q1","keywords":["one"]},{"question":"q2","keywords":["two"]},{"question":"q3","keywords":["three"]}]}),encoding="utf-8")
        answers=self.dir/"answers.txt"; answers.write_text("one\ntwo\nthree\n",encoding="utf-8")
        st=self.state(); rec=st["handovers"]["HO1"]; rec.update(phase="quiz",candidates=[{"session_id":"candidate"}],quiz={"key_file":str(key),"candidate":"candidate"})
        (self.dir/".teamwork/state.json").write_text(json.dumps(st),encoding="utf-8")
        out=self.tw("succ","grade","HO1","--answers",str(answers),ok=False)
        self.assertEqual(out.returncode,1)
        self.assertIn("rerun with --caller",out.stderr)
        self.assertEqual(self.state()["handovers"]["HO1"]["phase"],"quiz")
        self.assertNotIn("graded_at",self.state()["handovers"]["HO1"]["quiz"])
        blocked=self.tw("succ","seat","HO1","candidate",ok=False)
        self.assertNotEqual(blocked.returncode,0)
        item=json.loads(key.read_text(encoding="utf-8"))["items"][0]
        transcript=self.dir/"candidate.jsonl"
        transcript.write_text("\n".join(json.dumps({"message":{"content":text}}) for text in (item["question"],"one","two","three"))+"\n",encoding="utf-8")
        self.synthetic_bridge("candidate", transcript)
        verified=self.tw("succ","grade","HO1","--answers",str(answers),"--caller","lead","--from-transcript",str(transcript)).stdout
        self.assertIn("Zero tool calls: passed",verified)
        self.assertTrue(self.state()["handovers"]["HO1"]["quiz"]["passed"])

    def test_grade_missing_answers_file_has_clear_error(self):
        self.tw("thread","add","api","--session","old")
        st=self.state(); st["lead"]["session_id"]="lead"
        (self.dir/".teamwork/state.json").write_text(json.dumps(st),encoding="utf-8")
        self.tw("succ","start","api","--reason","replace")
        key=self.dir/"quiz.json"; key.write_text(json.dumps({"items":[{"question":"q1","keywords":["one"]}]}),encoding="utf-8")
        st=self.state(); rec=st["handovers"]["HO1"]; rec.update(phase="quiz",candidates=[{"session_id":"candidate"}],quiz={"key_file":str(key),"candidate":"candidate"})
        (self.dir/".teamwork/state.json").write_text(json.dumps(st),encoding="utf-8")
        missing=self.dir/"missing-answers.txt"
        result=self.tw("succ","grade","HO1","--answers",str(missing),"--caller","lead",ok=False)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("cannot read answers file",result.stderr)
        self.assertNotIn("Traceback",result.stderr)

    def test_ack_parser_and_latest_list_triggers_result(self):
        self.tw("thread","add","api","--session","old")
        self.tw("succ","start","lead","--reason","replace lead")
        st=self.state(); rec=st["handovers"]["HO1"]; rec.update(phase="seated",ack_needed=["api"])
        (self.dir/".teamwork/state.json").write_text(json.dumps(st),encoding="utf-8")
        ack=self.dir/"acks.jsonl"
        ack.write_text(json.dumps({"type":"user","message":{"role":"user","content":[{"type":"text","text":"<cross-session-message from-session=\"session_api\">\n[tw:demo] ACK api -> lead\n</cross-session-message>"}]},"origin":{"kind":"human"}})+"\n",encoding="utf-8")  # real shape: a peer message arrives wrapped
        self.tw("succ","ack","HO1","--caller","lead-next","--from-transcript",str(ack))
        self.assertEqual(self.state()["handovers"]["HO1"]["acks"],["api"])
        tr=FIXTURES/"t71_real_tool_results.jsonl"
        self.tw("succ","ack","HO1","--triggers","--from-transcript",str(tr))
        self.assertTrue(self.state()["handovers"]["HO1"]["trigger_snapshot"]["result"])

    def test_freeze_unreachable_env_gone_and_status_next_step(self):
        self.tw("thread","add","api","--session","old")
        self.tw("succ","start","api","--reason","replace")
        self.tw("succ","freeze","HO1","--unreachable","--reason","env-gone")
        row=json.loads(self.tw("succ","status","HO1","--json").stdout)[0]
        self.assertTrue(row["unreachable"]); self.assertEqual(row["unreachable_reason"],"env-gone")
        self.assertIn("continue from recorded transcript",self.tw("succ","status","HO1").stdout)

    def test_pack_extracts_promises_in_both_languages_and_ignores_non_promises(self):
        # Exercise the shared promise extractor through a lead transcript pack.
        self.tw("lead","--session","lead-old")
        self.tw("succ","start","lead","--reason","replace")
        self.tw("succ","candidate","HO1","candidate")
        self.tw("succ","freeze","HO1","--stopped","lead-old idle")
        tr=self.dir/"lead.jsonl"
        tr.write_text("\n".join(json.dumps({"type":"assistant","message":{"content":s}}) for s in ("我会核对接口。","I'll send the result.","普通说明。"))+"\n",encoding="utf-8")
        self.synthetic_bridge(self.state()["handovers"]["HO1"]["old_session"], tr)
        self.tw("succ","pack","HO1","--transcript",str(tr))
        promises=self.state()["promises"]
        self.assertEqual([x["text"] for x in promises],["我会核对接口。","I'll send the result."])
        self.assertTrue(all(x["confidence"]=="low" for x in promises))

    def test_accept_receipt_requires_two_sections_and_fact_from_original_words(self):
        self.tw("thread","add","api","--session","old")
        self.fixture_old_session_start("api")
        self.tw("succ","start","api","--reason","replace")
        st=self.state(); rec=st["handovers"]["HO1"]
        rec.update(phase="packed",candidates=[{"session_id":"candidate"}],pack_dir=str(self.dir/"pack"),
                   freeze={"dirty_fingerprint":"-"},steps={"freeze":"2026-01-01T00:00:00+00:00","pack":"2026-01-01T00:01:00+00:00"})
        (self.dir/".teamwork/state.json").write_text(json.dumps(st),encoding="utf-8")
        pack=self.dir/"pack"; (pack/"raw").mkdir(parents=True)
        fact="Never overwrite user work; preserve the original notes."
        (pack/"BRIEF.md").write_text("## Current facts\n",encoding="utf-8")
        (pack/"HANDOFF.md").write_text("## Rules\n- keep files\n",encoding="utf-8")
        (pack/"words.md").write_text("[#1] "+fact+"\n",encoding="utf-8")
        (pack/"raw/old.jsonl").write_text("{}\n",encoding="utf-8")
        files={p.relative_to(pack).as_posix():__import__("hashlib").sha256(p.read_bytes()).hexdigest() for p in pack.rglob("*") if p.is_file()}
        (pack/"MANIFEST.json").write_text(json.dumps({"files":files,"gaps":[]}),encoding="utf-8")
        receipt=self.dir/"receipt.json"
        base={"tasks":[],"branch":self.state()["threads"]["api"]["branch"],"head":"-","rules":[],"requests":[],"only_read_fact":fact,"unresolved":[]}
        receipt.write_text(json.dumps({**base,"sections":"## Only-known-after-reading fact\n"+fact+"\n## Unresolved\nnone\n"}),encoding="utf-8")
        self.tw("succ","verify","HO1","--accept",str(receipt),"--caller","session_fixture-lead",ok=False)
        checks=self.state()["handovers"]["HO1"]["verify"]["checks"]
        self.assertFalse(checks["handoff_rules"])
        receipt.write_text(json.dumps({**base,"sections":"## Rules\nkeep files\n## Only-known-after-reading fact\n"+fact+"\n## Unresolved\nnone"}),encoding="utf-8")
        self.tw("succ","verify","HO1","--accept",str(receipt),"--caller","session_fixture-lead",ok=False)
        checks=self.state()["handovers"]["HO1"]["verify"]["checks"]
        self.assertTrue(checks["unresolved_section"])
        receipt.write_text(json.dumps({**base,"only_read_fact":"invented statement","sections":"## Rules\nkeep files\n## Only-known-after-reading fact\ninvented\n## Unresolved\nnone"}),encoding="utf-8")
        self.tw("succ","verify","HO1","--accept",str(receipt),"--caller","session_fixture-lead",ok=False)
        checks=self.state()["handovers"]["HO1"]["verify"]["checks"]
        self.assertFalse(checks["only_read_fact"])
        self.assertFalse(self.state()["handovers"]["HO1"]["verify"]["passed"])


if __name__ == "__main__":
    unittest.main()
