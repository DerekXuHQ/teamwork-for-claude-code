try:
    from ._base import *
except ImportError:
    from _base import *


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
        versions = got["old_sessions"] + got["incomparable_sessions"]
        self.assertEqual(next(x["thread"] for x in versions if x["session"] == "s-old"), "api")
        self.tw("prefs", "--set", "skill_edge=/tmp/x")
        self.assertIn("tests unreleased teamwork", self.context())
        self.assertEqual(self.run_context("garbage").returncode, 0)

class OpeningAndSelfcheck(TwCase):
    def test_title_with_goal(self):
        self.tw("lead", "--session", "lead-session")
        out = json.loads(self.tw("lead").stdout)
        self.assertEqual(out["title"], f"[{self.state()['prefs']['short_name']}] ★lead · lead")

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

class OpeningZh(TwCase):
    """T65: in Chinese the opening questions and their steps read in Chinese; keys, values and English stay as they were."""

    def setUp(self):
        super().setUp()
        home = self.dir / "isolated-home"
        project = home / "project"
        project.mkdir(parents=True)
        ledger = self.dir / ".teamwork"
        target = project / ".teamwork"
        shutil.move(str(ledger), str(target))
        self.env.update({"HOME": str(home), "TEAMWORK_DIR": str(target)})

    def test_zh_questions_steps_and_status_lines(self):
        self.tw("prefs", "--set", "language=zh")
        out = self.tw("opening").stdout
        for text in ("我开新线程，要先问你吗？", "权限交给我来管吗？", "能交给 GPT 的活，都交给 GPT 吗？",
                     "全交给我管（perm_policy=managed）", "现在：没设", "还没回答："):
            self.assertIn(text, out)
        self.assertNotIn("May the lead open", out); self.assertNotIn("last elsewhere", out); self.assertNotIn("unanswered_now", out)
        self.assertIn("按话题直接开，不用问（spawn_policy=free） — 不打扰你，开了哪些在看板上都看得到（推荐）", out)
        rows = json.loads(self.tw("opening", "--json").stdout)["questions"]
        self.assertEqual([r["key"] for r in rows], ["spawn_policy", "perm_policy", "gpt_policy"])
        self.assertEqual(rows[1]["question"], "权限交给我来管吗？（我用 auto 模式跑，弹出来的确认我来处理）")

    def test_zh_variants_and_all_answered(self):
        for code in ("zh_TW", "ZH-cn"):
            self.tw("prefs", "--set", f"language={code}")
            out = self.tw("opening").stdout
            self.assertIn("还没回答：", out); self.assertIn("（推荐）", out)
            self.assertNotIn("opening_current", out); self.assertNotIn("opening_unanswered", out)
        for kv in ("spawn_policy=free", "perm_policy=managed", "gpt_policy=yes"):
            self.tw("prefs", "--set", kv, "--user-said", "好")
        self.assertIn("三问都答过了", self.tw("opening").stdout)
        self.assertIn("只用在你点名的地方", self.tw("opening").stdout)

    def test_en_unchanged(self):
        self.tw("prefs", "--set", "language=en")
        out = self.tw("opening").stdout
        self.assertIn("May the lead open sessions (threads) freely?", out)
        self.assertIn("  current: None; last elsewhere: None", out)
        self.assertIn("unanswered_now: spawn_policy, perm_policy, gpt_policy", out)

class LeadOpening(TwCase):
    def setUp(self):
        super().setUp()
        self.keys = self.dir.parent / (self.dir.name + "-keys")
        self.keys.mkdir()
        self.env["TW_KEYS_DIR"] = str(self.keys)

    def test_opening_creates_one_strict_lead_screen_and_respects_missing_off(self):
        missing = json.loads(self.tw("opening", "--json").stdout)
        self.assertEqual(missing["lead_screen"]["status"], "no_session")
        self.assertEqual(self.state()["screens"], {})
        self.tw("lead", "--session", "lead-1")
        first = json.loads(self.tw("opening", "--json").stdout)
        self.assertEqual(first["lead_screen"]["screen"], "SC1")
        self.assertEqual(first["lead_screen"]["status"], "asked")
        self.assertEqual(first["lead_screen"]["attempt"], 1)
        self.assertEqual(self.state()["screens"]["SC1"]["level"], "strict")
        self.assertEqual(len(self.state()["screens"]["SC1"]["items"]), 7)
        again = json.loads(self.tw("opening", "--json").stdout)
        self.assertEqual(again["lead_screen"]["screen"], "SC1")
        self.assertEqual(len(self.state()["screens"]), 1)
        self.tw("prefs", "--set", "screen_mode=off")
        self.tw("lead", "--session", "lead-2", "--force")
        off = json.loads(self.tw("opening", "--json").stdout)
        self.assertEqual(off["lead_screen"]["status"], "off")
        self.assertEqual(len(self.state()["screens"]), 1)

    def test_opening_defaults_and_selfcheck(self):
        self.tw("prefs", "--set", "language=zh")
        result = json.loads(self.tw("opening", "--json").stdout)
        rows = {r["key"]: r for r in result["questions"]}
        self.assertEqual(rows["perm_policy"]["default"], "managed")
        self.assertEqual(rows["gpt_policy"]["default"], "yes")
        for key in ("perm_policy", "gpt_policy"):
            self.assertEqual(rows[key]["recommended"], 2)
            self.assertIn("prefs --set " + key, rows[key]["change"])
        self.assertEqual(result["defaults_in_effect"], {"perm_policy": "managed", "gpt_policy": "yes"})
        self.assertFalse(rows["perm_policy"]["never_default"])
        self.assertFalse(rows["gpt_policy"]["never_default"])
        check = json.loads(self.tw("selfcheck", "--json").stdout)
        opening = next(x for x in check["items"] if x["item"] == "opening")
        self.assertTrue(opening["ok"])
        self.assertIn("按默认", opening["detail"])
        self.assertEqual(opening["fix"], "")
        text = self.tw("opening").stdout
        self.assertIn("（推荐）", text)
        self.assertIn("以后想改", text)
        for key, value in (("perm_policy", "ask"), ("gpt_policy", "no")):
            self.assertNotEqual(self.tw("prefs", "--set", f"{key}={value}", ok=False).returncode, 0)
            self.tw("prefs", "--set", f"{key}={value}", "--user-said", "请按这个设置")
            self.assertEqual(self.state()["prefs"]["policy_said"][key]["text"], "请按这个设置")

    def test_opening_after_dispatched_task_is_not_new_and_does_not_write(self):
        self.tw("lead", "--session", "lead-1")
        self.tw("thread", "add", "ui")
        self.tw("task", "add", "T1", "--owner", "ui", "--title", "already started")
        self.tw("task", "set", "T1", "--status", "dispatched")
        self.tw("lead", "--session", "lead-2", "--force")
        before = self.state()
        out = json.loads(self.tw("opening", "--json").stdout)
        self.assertEqual(out["lead_screen"], {"status": "not_new"})
        self.assertEqual([q["key"] for q in out["questions"]], ["spawn_policy", "perm_policy", "gpt_policy"])  # the questions still show
        self.assertEqual(self.state(), before)
        self.tw("screen", "ask", "lead", "--session", "lead-2")
        before = self.state()
        existing = json.loads(self.tw("opening", "--json").stdout)
        self.assertEqual(existing["lead_screen"], {"screen": "SC1", "status": "asked"})
        self.assertEqual(self.state(), before)

    def test_round2_relead_chain_lock_escape_and_force(self):
        fail = ",".join(f"P{i}={0 if i == 5 else 1}" for i in range(1, 8))
        self.tw("lead", "--session", "A")
        self.tw("opening", "--json")
        self.tw("screen", "record", "SC1", "--scores", fail)
        self.tw("screen", "relead", "SC1", "--said", "x")
        self.assertIn("--relead", self.tw("req", "add", "y", ok=False).stderr)  # every write names the way out
        self.assertNotEqual(self.tw("screen", "relead", "SC1", "--cancel", ok=False).returncode, 0)  # the user's call
        self.tw("screen", "relead", "SC1", "--cancel", "--user-said", "no seat left, keep this one")
        self.assertIsNone(self.state().get("write_lock"))
        self.tw("req", "add", "y")
        self.tw("thread", "add", "ui"); self.tw("task", "add", "T1", "--owner", "ui", "--title", "t")
        self.tw("task", "set", "T1", "--status", "dispatched")
        self.tw("lead", "--session", "B", "--force")
        out = json.loads(self.tw("opening", "--json").stdout)  # started project, but a relead chain is open: B is screened
        self.assertEqual(out["lead_screen"]["status"], "asked")
        self.assertNotEqual(self.tw("lead", "--session", "A", "--force", ok=False).returncode, 0)  # A already tried

    def test_round2_no_relead_during_lead_handover_and_no_freeze_over_relead(self):
        fail = ",".join(f"P{i}={0 if i == 5 else 1}" for i in range(1, 8))
        self.tw("lead", "--session", "A"); self.tw("opening", "--json")
        self.tw("screen", "record", "SC1", "--scores", fail)
        self.tw("succ", "start", "lead", "--reason", "x")
        self.assertNotEqual(self.tw("screen", "relead", "SC1", "--said", "x", ok=False).returncode, 0)

    def test_candidate_screen_does_not_relead_or_clear_current_lead_trials(self):
        self.tw("lead", "--session", "lead-1")
        self.tw("opening", "--json")
        self.tw("screen", "record", "SC1", "--scores", ",".join(f"P{i}={0 if i == 5 else 1}" for i in range(1, 8)))
        self.tw("screen", "relead", "SC1", "--said", "原始请求")
        self.tw("lead", "--session", "lead-2", "--relead")
        self.tw("opening", "--json")
        self.tw("screen", "ask", "lead", "--session", "candidate")
        candidate_pass = self.tw("screen", "record", "SC3", "--scores", ",".join(f"P{i}=2" for i in range(1, 8)))
        self.assertEqual(len(self.state()["lead_trials"]), 1)
        self.assertNotIn("screen relead", candidate_pass.stderr)
        self.tw("screen", "record", "SC2", "--scores", ",".join(f"P{i}=2" for i in range(1, 8)))
        self.assertEqual(self.state()["lead_trials"], [])
        self.assertIsNone(self.state().get("lead_first_message"))
        self.tw("screen", "ask", "lead", "--session", "candidate-bad")
        failed = self.tw("screen", "record", "SC4", "--scores", ",".join(f"P{i}={0 if i == 5 else 1}" for i in range(1, 8)))
        self.assertIn("whether to replace it is your call", failed.stderr)
        denied = self.tw("screen", "relead", "SC4", "--said", "原始请求", ok=False)
        self.assertIn("current lead's opening screen", denied.stderr)
        self.tw("task", "add", "T2", "--title", "current lead still writes")

    def test_relead_prompt_permission_and_trial_reuse_are_guarded(self):
        self.tw("lead", "--session", "L1", "--mode", "default")
        self.tw("opening", "--json")
        self.tw("screen", "record", "SC1", "--scores", ",".join(f"P{i}={0 if i == 5 else 2}" for i in range(1, 8)))
        prompt = json.loads(self.tw("screen", "relead", "SC1", "--said", "原始请求", "--json").stdout)
        self.assertIn("--relead", prompt["create_session"]["prompt"])
        self.assertEqual(prompt["create_session"]["permission_mode"], "default")
        self.assertIn("P5=0 veto", prompt["user_line"])
        seated = self.tw("lead", "--session", "L2", "--relead")
        self.assertIn("previous lead's permission mode", seated.stderr)
        self.tw("opening", "--json")
        self.tw("screen", "record", "SC2", "--scores", ",".join(f"P{i}=1" for i in range(1, 8)))
        self.tw("screen", "relead", "SC2", "--said", "原始请求")
        self.assertIn("--relead", self.tw("lead", "--session", "Lx", ok=False).stderr)
        self.assertIn("--relead", self.tw("lead", "--session", "Lx", "--force", ok=False).stderr)
        self.assertIn("--relead", self.tw("succ", "start", "lead", "--reason", "handoff", ok=False).stderr)
        reused = self.tw("lead", "--session", "L1", "--relead", ok=False)
        self.assertIn("not the one that did not pass", reused.stderr)
        self.tw("lead", "--session", "L3", "--relead")
        self.tw("opening", "--json")
        self.tw("screen", "record", "SC3", "--scores", ",".join(f"P{i}=0" if i == 5 else f"P{i}=2" for i in range(1, 8)))
        stop = self.tw("screen", "relead", "SC3", "--said", "原始请求")
        self.assertIn("P5=0 veto (an error worked around without saying so)", stop.stdout)

    def test_screen_policy_and_selfcheck_use_policy_function_defaults(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("prefs", "--set", "perm_policy=ask", "--user-said", "ask me")
        self.tw("prefs", "--set", "gpt_policy=no", "--user-said", "no gpt")
        result = json.loads(self.tw("opening", "--json").stdout)
        self.assertEqual(result["defaults_in_effect"], {})
        check = json.loads(self.tw("selfcheck", "--json").stdout)
        opening = next(x for x in check["items"] if x["item"] == "opening")
        self.assertEqual(opening["detail"], "answered")

    def test_screen_ask_keeps_task_identifier_in_record(self):
        self.tw("lead", "--session", "lead-1")
        self.tw("task", "add", "T1", "--title", "one; two")
        self.tw("screen", "ask", "lead", "--session", "lead-1", "--task", "T1")
        self.assertEqual(self.state()["screens"]["SC1"]["task"], "T1")

    def test_failed_lead_screen_relead_locks_old_lead_and_seats_new_one(self):
        self.tw("lead", "--session", "lead-old")
        self.tw("opening", "--json")
        self.tw("screen", "record", "SC1", "--scores", ",".join(f"P{i}={0 if i == 5 else 1}" for i in range(1, 8)))
        result = json.loads(self.tw("screen", "relead", "SC1", "--said", "做个网站", "--json").stdout)
        self.assertEqual(result["attempt"], 2)
        self.assertIn("做个网站", result["create_session"]["prompt"])
        self.assertTrue(self.state()["write_lock"]["relead"])
        self.assertNotEqual(self.tw("task", "add", "T1", "--title", "blocked", ok=False).returncode, 0)
        seated = json.loads(self.tw("lead", "--session", "lead-new", "--relead").stdout)
        self.assertEqual(seated["lead"]["session_id"], "lead-new")
        self.assertFalse(self.state().get("write_lock"))
        history = seated["lead"]["sessions"]
        self.assertEqual(history[-1]["session_id"], "lead-new")
        self.assertEqual(history[-1]["reason"], "relead after failed opening screen")
        self.assertNotEqual(self.tw("lead", "--relead", ok=False).returncode, 0)

    def test_three_failed_screens_stop_without_lock_and_show_each_score(self):
        self.tw("lead", "--session", "lead-1")
        self.tw("opening", "--json")
        self.tw("screen", "record", "SC1", "--scores", ",".join(f"P{i}=0" for i in range(1, 8)))
        first_message = self.dir / "first-message.txt"
        first_message.write_text("原始请求来自文件", encoding="utf-8")
        first = json.loads(self.tw("screen", "relead", "SC1", "--first-message", str(first_message), "--json").stdout)
        self.assertEqual(first["attempt"], 2)
        self.assertIn("原始请求来自文件", first["create_session"]["prompt"])
        self.assertIn("P5=0 veto", first["user_line"])
        self.tw("lead", "--session", "lead-2", "--relead")
        self.tw("opening", "--json")
        self.tw("screen", "record", "SC2", "--scores", ",".join(f"P{i}=0" for i in range(1, 8)))
        second = json.loads(self.tw("screen", "relead", "SC2", "--json").stdout)
        self.assertEqual(second["attempt"], 3)
        self.assertIn("原始请求来自文件", second["create_session"]["prompt"])
        self.tw("lead", "--session", "lead-3", "--relead")
        self.tw("opening", "--json")
        self.tw("screen", "record", "SC3", "--scores", ",".join(f"P{i}=0" for i in range(1, 8)))
        third = self.tw("screen", "relead", "SC3")
        self.assertIn("SC1", third.stdout)
        self.assertIn("SC2", third.stdout)
        self.assertIn("SC3", third.stdout)
        self.assertIn("0/14", third.stdout)
        self.assertIn("user decides", third.stdout)
        self.assertIn("P5=0 veto", third.stdout)
        self.assertNotIn("write_lock", self.state())

    def test_pass_clears_trials_unavailable_and_unscored_relead_is_rejected(self):
        self.tw("lead", "--session", "lead-1")
        self.tw("opening", "--json")
        self.tw("screen", "record", "SC1", "--scores", ",".join(f"P{i}=0" for i in range(1, 8)))
        self.tw("screen", "relead", "SC1", "--said", "原始请求")
        self.tw("lead", "--session", "lead-2", "--relead")
        self.tw("opening", "--json")
        self.tw("screen", "record", "SC2", "--scores", ",".join(f"P{i}=2" for i in range(1, 8)))
        self.assertEqual(self.state()["lead_trials"], [])
        self.assertIsNone(self.state().get("lead_first_message"))
        self.tw("rebuild")
        rebuilt = json.loads((self.dir / ".teamwork" / "state.json.rebuilt").read_text(encoding="utf-8"))
        self.assertEqual(rebuilt["lead_trials"], [])
        self.assertNotIn("lead_first_message", rebuilt)
        self.tw("lead", "--session", "lead-3", "--force")
        self.tw("opening", "--json")
        self.tw("prefs", "--set", "language=zh")
        self.tw("screen", "unavailable", "SC3", "--why", "grader unavailable")
        denied = self.tw("screen", "relead", "SC3", ok=False)
        self.assertIn("未体检", denied.stderr)

    def test_rebuild_restores_relead_trial_and_lock(self):
        self.tw("lead", "--session", "lead-1")
        self.tw("opening", "--json")
        self.tw("screen", "record", "SC1", "--scores", ",".join(f"P{i}=0" for i in range(1, 8)))
        self.tw("screen", "relead", "SC1", "--said", "恢复信息")
        self.tw("rebuild")
        rebuilt = json.loads((self.dir / ".teamwork" / "state.json.rebuilt").read_text(encoding="utf-8"))
        self.assertEqual(len(rebuilt["lead_trials"]), 1)
        self.assertTrue(rebuilt["write_lock"]["relead"])

    def test_new_user_text_follows_language(self):
        self.tw("prefs", "--set", "language=en")
        self.tw("lead", "--session", "lead-en")
        opening = json.loads(self.tw("opening", "--json").stdout)
        outputs = [opening["lead_screen"]["message"], opening["rule"]]
        outputs.extend(row["change"] for row in opening["questions"])
        self.tw("screen", "record", "SC1", "--scores", ",".join(f"P{i}=0" for i in range(1, 8)))
        en_relead = json.loads(self.tw("screen", "relead", "SC1", "--said", "original request", "--json").stdout)
        outputs.append(en_relead["create_session"]["prompt"])
        self.assertIn("--relead", en_relead["create_session"]["prompt"])
        for output in outputs:
            self.assertIsNone(re.search(r"[\u4e00-\u9fff]", output), output)
        self.tw("lead", "--session", "lead-mid", "--relead")
        self.tw("prefs", "--set", "language=zh")
        self.tw("opening", "--json")
        self.tw("screen", "record", "SC2", "--scores", ",".join(f"P{i}=0" for i in range(1, 8)))
        zh_relead = json.loads(self.tw("screen", "relead", "SC2", "--said", "用户原话", "--json").stdout)
        self.assertIn("--relead", zh_relead["create_session"]["prompt"])
        self.assertIn("第一步跑", zh_relead["create_session"]["prompt"])
        self.tw("lead", "--session", "lead-zh", "--relead")
        output = self.tw("opening").stdout
        self.assertIn("这一轮就答", output)
        self.assertIn("推荐", output)

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

class FewerPrompts(TwCase):
    """T27: install.py pre-approves scratch folders and always asks for irreversible repo actions; the protocol
    tells every session how not to cause prompts."""

    def install(self, *extra):
        inst = SCRIPTS / "install.py"
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
        self.assertNotIn("was added to additionalDirectories", dry.stdout)
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
        sys.path.insert(0, str(SCRIPTS))
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
            hooks = [h["command"] for g in sl["hooks"]["PreToolUse"] if g.get("matcher") == install.BASH_GUARD_MATCHER for h in g["hooks"]]
            self.assertTrue(any(c.endswith("bash-guard || true") for c in hooks), hooks)  # r3: never exits 2
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
        r = subprocess.run([sys.executable, str(SCRIPTS / "install.py"), "--link", "--apply",
                            "--config-dir", "cfg2", "--served", str(served)], capture_output=True, text=True,
                           cwd=self.dir, env={**os.environ, "TMPDIR": str(self.dir / "tmpd")})
        self.assertEqual(r.returncode, 0, r.stderr)
        sl = json.loads((served / ".claude" / "settings.local.json").read_text())
        cmd = sl["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertTrue(Path(cmd.split('"')[1]).is_absolute() and "cfg2" in cmd, cmd)  # works from any folder
        broken = cmd.replace("tw.py", "zorblax-missing.py")
        r = subprocess.run(["sh", "-c", broken], input="{}", capture_output=True, text=True, cwd="/")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_hooks_use_an_absolute_python_and_old_bare_hooks_are_updated_in_place(self):
        """T66: a bare `python3` is looked up on the hook's PATH, which may find a Python too old for tw.py."""
        (self.dir / "tmpd").mkdir()
        served, proj = self.dir / "served", self.dir / "proj"
        served.mkdir(); proj.mkdir()
        cfg = (self.dir / "cfg3").resolve()
        tw_py = cfg / "skills" / "teamwork" / "scripts" / "tw.py"
        (proj / ".claude").mkdir()
        (proj / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": f'python3 "{tw_py}" context'}]}]}}))
        (served / ".claude").mkdir()
        (served / ".claude" / "settings.local.json").write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": f'python3 "{tw_py}" bash-guard || true'}]}]}}))
        r = subprocess.run([sys.executable, str(SCRIPTS / "install.py"), "--link", "--apply",
                            "--config-dir", str(cfg), "--project", str(proj), "--served", str(served)], capture_output=True, text=True,
                           cwd=self.dir, env={**os.environ, "TMPDIR": str(self.dir / "tmpd")})
        self.assertEqual(r.returncode, 0, r.stderr)
        starts = [h["command"] for f in (proj / ".claude" / "settings.json", proj / ".claude" / "settings.local.json") if f.exists()
                  for g in json.loads(f.read_text()).get("hooks", {}).get("SessionStart", []) for h in g["hooks"]]
        guards = [h["command"] for g in json.loads((served / ".claude" / "settings.local.json").read_text())["hooks"]["PreToolUse"]
                  for h in g["hooks"]]
        self.assertEqual(len([c for c in starts if "tw.py" in c]), 1, starts)  # updated, not added twice
        self.assertEqual(len([c for c in guards if "bash-guard" in c]), 1, guards)
        for c in [x for x in starts if "tw.py" in x] + guards:
            self.assertFalse(c.startswith("python3 "), c)
            self.assertTrue(Path(c.split('"')[1]).is_absolute(), c)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.dir)}  # a short PATH like a launchd-started server
        g = subprocess.run(["sh", "-c", guards[0]], input=json.dumps({"tool_input": {"command": "ls"}}),
                           capture_output=True, text=True, cwd=str(proj), env=env)
        self.assertEqual(g.returncode, 0); self.assertNotIn("SyntaxError", g.stderr)
        self.assertIn("PreToolUse hook: updating to an absolute python path", r.stdout)
        r2 = subprocess.run([sys.executable, str(SCRIPTS / "install.py"), "--link", "--apply",
                             "--config-dir", str(cfg), "--project", str(proj), "--served", str(served)], capture_output=True,
                            text=True, cwd=self.dir, env={**os.environ, "TMPDIR": str(self.dir / "tmpd")})
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("PreToolUse hook: already present", r2.stdout)
        self.assertNotIn("updating to an absolute python path", r2.stdout)

    def test_hook_match_needs_exactly_one_quoted_interpreter(self):
        """T66 review: a user's wrapper around our hook is theirs; only `python3 ` or one quoted word counts as ours."""
        sys.path.insert(0, str(SCRIPTS))
        import install
        tw_py = f'"{install.DEST / "scripts" / "tw.py"}"'
        self.assertTrue(install._tw_hook(f"python3 {tw_py} context", "context"))
        self.assertTrue(install._tw_hook(f'"/opt/py/bin/python3" {tw_py} context', "context"))
        self.assertFalse(install._tw_hook(f'"/x/wrap" --flag "y" {tw_py} context', "context"))
        self.assertFalse(install._tw_hook(f'"" {tw_py} context', "context"))
        self.assertFalse(install._tw_hook(f"python3 {tw_py} bash-guard", "context"))

    def test_hook_python_can_run_tw(self):
        """T66 review: the interpreter written into hooks must be able to run tw.py, and not a venv's."""
        sys.path.insert(0, str(SCRIPTS))
        import install
        py = install.hook_python()
        self.assertTrue(Path(py).is_absolute(), py)
        r = subprocess.run([py, str(install.SRC / "scripts" / "tw.py"), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

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
        sys.path.insert(0, str(SCRIPTS))
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
        sys.path.insert(0, str(SCRIPTS))
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
        skill = (REPO / "SKILL.md").read_text()
        for want in ("Keep prompts rare", "The user's own decisions", "force-pushing the base branch",
                     "visibility", "Bash(sleep *)", "send_later"):
            self.assertIn(want, skill)

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

    def test_skill_is_published_to_its_own_link_and_index(self):
        result=self.release("publish","--skill","docs","--apply")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((self.configs[0]/"skills"/"docs").is_symlink())
        self.assertTrue((self.configs[0]/"docs-releases"/"index.json").exists())
        self.assertFalse((self.configs[0]/"teamwork-releases"/"index.json").exists())
        status=self.release("status","--skill","docs","--json")
        self.assertEqual(status.returncode,0,status.stderr)
        self.assertIn("release:",json.loads(status.stdout)["configs"][0]["link"])
        self.assertEqual(self.release("edge","--skill","docs","--apply").returncode,0)
        self.assertEqual(self.release("rollback","--skill","docs","--apply").returncode,0)
        self.assertTrue((self.configs[0]/"skills"/"docs").resolve().is_relative_to(self.configs[0]/"docs-releases"))

    def test_compare_reports_quota_and_permission_prompt_daily_rates(self):
        project=self.root/"projects"/"demo"/".teamwork"; project.mkdir(parents=True)
        rows=[{"ev":"quota-usage","used_pct":42,"at":"2026-11-20T10:00:00Z"},
              {"ev":"notice-add","kind":"needs-user","at":"2026-11-20T10:01:00Z"},
              {"ev":"quota-usage","used_pct":68,"at":"2026-11-22T10:00:00Z"},
              {"ev":"permission-wait","at":"2026-11-22T10:01:00Z"}]
        (project/"log.jsonl").write_text("\n".join(json.dumps(x) for x in rows)+"\n")
        result=self.release("compare","--skill","docs","--since","2026-11-21","--days","7","--projects",str(self.root/"projects"))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("quota used per day",result.stdout)
        self.assertIn("permission prompts per day",result.stdout)

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
        drained = [threading.Thread(target=f.read, daemon=True) for f in (pr.stdout, pr.stderr)]
        for t in drained: t.start()  # read the output: pipe capacity can be as small as 512 bytes
        try:
            pr.wait(timeout=5)  # exits without waiting for end of input
        finally:
            pr.stdin.close()
            for t in drained: t.join(timeout=5)
            pr.stdout.close(); pr.stderr.close()
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

class WorktreeSettings(GitCase):
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

class LeadBatch5(TwCase):
    def test_new_user_facing_text_has_no_chinese_in_english_projects(self):
        self.tw("prefs", "--set", "language=en")
        self.tw("thread", "add", "ui", "--session", "s-ui")
        self.tw("task", "add", "T1", "--title", "build page", "--owner", "ui")
        outs = [self.tw("opening", "--takeover"), self.tw("notice", "add", "--kind", "needs-user", "--note", "approve deploy"),
                self.tw("quota", "--weekly"), self.tw("model-status", "gpt", "--down", "--why", "quota"),
                self.tw("model-status", "gpt", "--up"), self.tw("memory", "push the strengths", "--pref"), self.tw("board")]
        for r in outs:
            self.assertIsNone(re.search(r"[\u4e00-\u9fff]", r.stdout + r.stderr), r.stdout + r.stderr)

    def test_takeover_opening_contains_unfinished_ledgers(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("thread", "add", "api", "--session", "api-session", "--status", "working", "--summary", "修复接口")
        self.tw("task", "add", "T1", "--title", "修复用户接口", "--owner", "api")
        self.tw("promise", "add", "补文档")
        self.tw("ask", "--proposal", "调整可撤回默认")
        self.tw("issue", "add", "启动报错")
        out = json.loads(self.tw("opening", "--takeover", "--json").stdout)
        self.assertEqual(out["threads"][0]["seat"], "api")
        self.assertIn("T1 修复用户接口", out["threads"][0]["task"])
        self.assertEqual(out["promises"][0]["text"], "补文档")
        self.assertEqual(out["asks"][0]["text"], "调整可撤回默认")
        self.assertEqual(out["issues"][0]["text"], "启动报错")
        self.assertTrue(out["lines"][-1].startswith("我先接着做能撤回的事"))

    def test_takeover_caps_each_category_independently(self):
        self.tw("prefs", "--set", "language=zh")
        st = self.state()
        st["threads"] = {f"t{i}": {"session_id": f"s{i}", "status": "working", "summary": f"task{i}"} for i in range(15)}
        st["promises"] = [{"text": f"p{i}", "status": "open"} for i in range(7)]
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        lines = json.loads(self.tw("opening", "--takeover", "--json").stdout)["lines"]
        self.assertIn("另有 10 个线程，见 board", lines)
        self.assertIn("另有 2 个承诺，见 board", lines)
        self.assertEqual(sum(f"task{i}" in "\n".join(lines) for i in range(15)), 5)


    def test_notice_needs_user_is_rendered_on_board(self):
        self.tw("prefs", "--set", "language=zh")
        self.tw("notice", "add", "--kind", "needs-user", "--note", "确认登录设备")
        self.assertIn("这一步要你本人来点：确认登录设备", self.tw("board").stdout)

class ModelsBatch5(TwCase):
    def test_gpt_private_accepts_cli_comma_and_json_lists(self):
        self.tw("prefs", "--set", "gpt_private=alpha,beta")
        self.assertEqual(self.state()["prefs"]["gpt_private"], ["alpha", "beta"])
        self.tw("prefs", "--set", 'gpt_private=["secret path","customer"]')
        self.assertEqual(self.state()["prefs"]["gpt_private"], ["secret path", "customer"])

    def test_unrouted_task_msg_keeps_route_line_absent(self):
        self.tw("thread", "add", "api")
        self.tw("task", "add", "T1", "--title", "review", "--owner", "api")
        message = self.tw("task-msg", "T1").stdout
        self.assertIn("Goal: review", message)
        self.assertNotIn("Brief:", message)  # same TASK as before batch 5 when no brief was given
        self.assertNotIn("Route/Model:", message)

    def test_gpt_down_records_undoable_fallback_and_ready_blocks_nonurgent(self):
        self.tw("prefs", "--set", "language=zh")
        out = self.tw("model-status", "gpt", "--down", "--why", "quota", "--until", "20:00")
        self.assertIn("GPT 暂不可用", out.stdout)
        self.assertTrue(self.state()["models"]["gpt"]["down"])
        self.assertTrue(any("P0" in q["text"] for q in self.state()["questions"]))
        self.tw("thread", "add", "api")
        self.tw("task", "add", "T1", "--title", "review", "--owner", "api")
        task = self.state(); task["tasks"]["T1"].update(status="approved", model="gpt")
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(task), encoding="utf-8")
        self.assertIn("等 GPT 恢复", self.tw("task-msg", "T1", ok=False).stderr)
        self.tw("model-status", "gpt", "--up")
        self.assertNotIn("gpt", self.state()["models"])

    def test_gpt_task_private_text_is_blocked_and_masked(self):
        self.tw("prefs", "--set", "language=zh")
        st = self.state(); st["prefs"]["gpt_private"] = ["secretphrase"]
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.tw("task", "add", "T1", "--title", "send secretphrase", "--model", "gpt")
        result = self.tw("task-msg", "T1", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("secretphrase", result.stderr)
        self.assertIn("不能给 GPT", result.stderr)

    def test_private_path_and_word_boundaries(self):
        self.tw("prefs", "--set", "language=zh")
        st = self.state(); st["prefs"]["gpt_private"] = ["path: secret/**", "acme"]
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.tw("task", "add", "T1", "--title", "mention secret/a.txt", "--model", "gpt")
        self.assertNotEqual(self.tw("task-msg", "T1", ok=False).returncode, 0)
        st = self.state(); st["prefs"]["gpt_private"] = ["acme"]
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.tw("task", "add", "T2", "--title", "acmeville", "--model", "gpt")
        self.assertEqual(self.tw("task-msg", "T2").returncode, 0)
        self.tw("task", "add", "T3", "--title", "acme", "--model", "gpt")
        self.assertNotEqual(self.tw("task-msg", "T3", ok=False).returncode, 0)

class LabelsBothLanguages(unittest.TestCase):
    def test_every_label_has_english_and_chinese(self):
        sys.path.insert(0, str(TW.parent)); import tw as twmod
        en, zh = set(twmod.LABELS["en"]), set(twmod.LABELS["zh"])
        self.assertEqual(sorted(zh - en), []); self.assertEqual(sorted(en - zh), [])

class SyntheticFixtureBoundary(TwCase):
    def test_primary_outcome_tests_do_not_use_synthetic_bridge(self):
        import inspect
        import importlib
        import sys
        sys.path.insert(0, str(TW.parent))
        fixtures = importlib.import_module("fixtures.regenerate")
        SeatSwapFlow = importlib.import_module("test_seat").SeatSwapFlow
        T80Compact = importlib.import_module("test_screen").T80Compact
        RootCauseSmokeTest = importlib.import_module("test_root").RootCauseSmokeTest
        primary_sources = (
            inspect.getsource(SeatSwapFlow),
            inspect.getsource(T80Compact.test_00_compact_hook_quiz_sweep_and_board),
            inspect.getsource(RootCauseSmokeTest),
        )
        for source in primary_sources:
            self.assertNotIn("synthetic_bridge", source)
            self.assertNotIn("self.record(", source)
            self.assertNotIn('"isCompactSummary"', source)
            self.assertNotIn('"tool_result"', source)
        self.assertTrue(fixtures.sample("t80_compaction.jsonl"))


if __name__ == "__main__":
    unittest.main()
