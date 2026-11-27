try:
    from ._base import *
except ImportError:
    from _base import *


class QuotaBase(TwCase):
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


class Quota(QuotaBase):
    NOW = 2_000_000_000
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


class QuotaIdle(QuotaBase):
    """R65: nothing running means no quota check and nothing written; once work restarts the usual interval applies."""

    def due(self):
        return json.loads(self.tw("quota", "--due", "--json").stdout)

    def test_idle_is_not_due_quietly_and_long_idle_restart_is_due(self):
        self.tw("prefs", "--set", "language=en")
        self.event({"seven_day": self.win(0.5), "five_hour": self.win(0.2)})
        self.tw("thread", "add", "ui", "--session", "s-ui", "--status", "working")
        self.tw("quota", "--config-dir", str(self.cfg), "--codex-dir", str(self.codex), "--json")
        self.assertFalse(self.due()["due"])
        self.tw("thread", "set", "ui", "--status", "idle")
        before = (self.dir / ".teamwork" / "state.json").read_text(encoding="utf-8")
        r = self.due()
        self.assertEqual((r["due"], r.get("reason")), (False, "idle"))
        self.assertIn("idle", self.tw("quota", "--due").stdout)
        self.assertFalse(json.loads(self.tw("ready").stdout)["quota_due"])
        self.assertEqual((self.dir / ".teamwork" / "state.json").read_text(encoding="utf-8"), before)  # nothing recorded
        self.tw("task", "add", "T1", "--title", "page", "--owner", "ui")
        self.tw("task", "set", "T1", "--status", "dispatched")
        self.assertFalse(self.due()["due"])  # short pause: no extra check
        self.env["TW_NOW"] = "2099-01-01T00:00:00+00:00"  # long idle spell, then work again
        self.assertTrue(self.due()["due"])
        self.assertTrue(json.loads(self.tw("ready").stdout)["quota_due"])
        self.assertNotIn("reason", self.due())

    def test_idle_check_works_under_the_handover_write_lock(self):
        st = self.state(); st["write_lock"] = {"ho": "H1"}
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.assertEqual(self.due().get("reason"), "idle")
        self.assertFalse(json.loads(self.tw("ready").stdout)["quota_due"])

    def test_ready_with_work_to_send_after_long_idle_is_due(self):
        self.event({"seven_day": self.win(0.5), "five_hour": self.win(0.2)})
        self.tw("thread", "add", "ui", "--session", "s-ui", "--status", "working")
        self.tw("quota", "--config-dir", str(self.cfg), "--codex-dir", str(self.codex), "--json")
        self.tw("thread", "set", "ui", "--status", "idle")
        self.tw("task", "add", "T1", "--title", "page", "--owner", "ui")
        self.tw("plan", "approve", "--user-said", "approved")
        self.env["TW_NOW"] = "2099-01-01T00:00:00+00:00"
        out = json.loads(self.tw("ready").stdout)
        self.assertEqual((out["ready"], out["quota_due"]), (["T1"], True))
        self.assertEqual(self.due().get("reason"), "idle")  # --due alone still says idle

    def test_landing_thread_and_review_task_count_as_running(self):
        self.tw("thread", "add", "ui", "--session", "s-ui", "--status", "landing")
        self.assertNotIn("reason", self.due())
        self.tw("thread", "set", "ui", "--status", "idle")
        self.tw("task", "add", "T1", "--title", "page", "--owner", "ui"); self.tw("task", "set", "T1", "--status", "dispatched")
        self.tw("task", "set", "T1", "--status", "working"); self.tw("task", "set", "T1", "--status", "review")
        self.assertNotIn("reason", self.due())

    def test_planned_tasks_alone_count_as_idle_starting_thread_does_not(self):
        self.tw("task", "add", "T1", "--title", "later")
        self.assertEqual(self.due().get("reason"), "idle")
        self.tw("thread", "add", "ui", "--session", "s-ui", "--status", "starting")
        self.assertTrue(self.due()["due"])

class QuotaTrigger(TwCase):
    """R66: tw.py only says when the lead should switch the hourly quota timer; the lead records what it did."""

    def test_hints_follow_idle_and_sendable_work(self):
        self.tw("prefs", "--set", "language=en")
        r = self.tw("ready")
        self.assertNotIn("quota_trigger", json.loads(r.stdout))  # no timer recorded: no hint
        self.tw("prefs", "--set", "quota_trigger_id=trig_1")
        r = self.tw("ready")
        self.assertEqual(json.loads(r.stdout)["quota_trigger"], "off")
        self.assertIn("Turn the quota timer off", r.stderr)
        self.assertIn("Turn the quota timer off", self.tw("wrapup").stdout)
        self.tw("prefs", "--set", "quota_trigger_on=false")
        self.assertNotIn("quota_trigger", json.loads(self.tw("ready").stdout))
        self.tw("thread", "add", "ui", "--session", "s-ui")
        self.tw("task", "add", "T1", "--title", "page", "--owner", "ui")
        self.tw("plan", "approve", "--user-said", "approved")
        r = self.tw("ready")
        self.assertEqual(json.loads(r.stdout)["quota_trigger"], "on")
        self.assertIn("Turn the quota timer on", r.stderr)
        self.assertEqual(json.loads(self.tw("wrapup", "--json").stdout)["quota_trigger"], "on")
        self.tw("prefs", "--set", "quota_trigger_on=true")
        self.assertNotIn("quota_trigger", json.loads(self.tw("ready").stdout))
        self.assertNotEqual(self.tw("prefs", "--set", "quota_trigger_on=maybe", ok=False).returncode, 0)

    def test_running_work_with_timer_off_keeps_saying_on(self):
        self.tw("prefs", "--set", "quota_trigger_id=trig_1"); self.tw("prefs", "--set", "quota_trigger_on=false")
        self.tw("thread", "add", "ui", "--session", "s-ui", "--status", "working")
        self.tw("task", "add", "T1", "--title", "page", "--owner", "ui"); self.tw("task", "set", "T1", "--status", "dispatched")
        self.assertEqual(json.loads(self.tw("ready").stdout)["quota_trigger"], "on")
        self.assertEqual(json.loads(self.tw("wrapup", "--json").stdout)["quota_trigger"], "on")
        self.tw("prefs", "--set", "quota_trigger_on=true"); self.tw("thread", "set", "ui", "--status", "idle")
        self.tw("task", "set", "T1", "--status", "blocked")
        self.assertEqual(json.loads(self.tw("wrapup", "--json").stdout)["quota_trigger"], "off")

    def test_create_hint_until_a_timer_is_recorded(self):
        """T85: work running and no timer recorded -> one copyable create line in ready, wrapup and SessionStart."""
        cfg = self.dir / "acct-a"; cfg.mkdir(); self.env["CLAUDE_CONFIG_DIR"] = str(cfg)
        self.tw("prefs", "--set", "language=en"); self.tw("lead", "--session", "lead-session")
        self.assertNotIn("quota_trigger", json.loads(self.tw("ready").stdout))  # idle: nothing to create
        self.tw("thread", "add", "ui", "--session", "s-ui", "--status", "working")
        self.tw("task", "add", "T1", "--title", "page", "--owner", "ui"); self.tw("task", "set", "T1", "--status", "dispatched")
        r = self.tw("ready")
        self.assertEqual(json.loads(r.stdout)["quota_trigger"], "create")
        line = next(x for x in r.stderr.splitlines() if x.startswith("Create the hourly quota timer"))
        for part in (f"CLAUDE_CONFIG_DIR={cfg}", f"-C {self.dir} quota --due", f"--config-dir {cfg}",
                     'name="demo hourly quota"', "prefs --set quota_trigger_id=<trig_...> quota_trigger_on=true"):
            self.assertIn(part, line)
        self.assertEqual(json.loads(self.tw("wrapup", "--json").stdout)["quota_trigger"], "create")
        self.assertIn(line, self.context())
        self.tw("prefs", "--set", "quota_trigger_id=trig_1", "quota_trigger_on=true")
        self.assertNotIn("quota_trigger", json.loads(self.tw("ready").stdout))
        self.assertNotIn("Create the hourly quota timer", self.context())

    def test_desktop_samples_read_an_account_without_events(self):
        """T85: local sessions log no usage events; the desktop app's per-organization samples give the total."""
        cfg = self.dir / "acct-a"; cfg.mkdir()
        (cfg / ".claude.json").write_text(json.dumps({"oauthAccount": {"organizationUuid": "org-a"}}))
        now = 2_000_000_000
        samples = self.dir / "plan-usage-history.json"
        samples.write_text(json.dumps({"version": 2, "samples": [
            {"t": (now - 7200) * 1000, "org": "org-a", "u": {"sd": 71}}, {"t": (now - 600) * 1000, "org": "org-a", "u": {"sd": 77}},
            {"t": (now - 60) * 1000, "org": "org-b", "u": {"sd": 99}}, {"t": "bad", "org": "org-a", "u": {"sd": 100}}]}))
        with mock.patch.dict(os.environ, {"TW_DESKTOP_USAGE": str(samples)}):
            r = quota.read_quota([str(cfg)], str(self.dir / "no-codex"), now)
            self.assertEqual((r["claude"]["total"]["pct"], r["claude"]["total"]["src"]), (77, "desktop"))
            self.assertNotIn("scoped", r["claude"])  # no events at all: Fable unknown, not 0%
            self.assertEqual(quota.tier(r, {}, now)[0], "burn-fable")
            stale = quota.read_quota([str(cfg)], str(self.dir / "no-codex"), now + 7 * 3600)
            self.assertEqual(quota.tier(stale, {}, now + 7 * 3600)[0], "unknown")
            (cfg / ".claude.json").write_text(json.dumps({"oauthAccount": {"organizationUuid": "org-a"},
                "cachedUsageUtilization": {"fetchedAtMs": (now - 36000) * 1000,
                                           "utilization": {"seven_day": {"utilization": 20, "resets_at": now + 86400}}}}))
            r = quota.read_quota([str(cfg)], str(self.dir / "no-codex"), now)  # a stale cache must not hide fresh samples
            self.assertEqual((r["claude"]["total"]["pct"], r["claude"]["total"]["src"]), (77, "desktop"))
            samples.write_text(json.dumps({"samples": [{"t": now * 1000, "org": "org-a", "u": {"sd": 5000}}]}))
            self.assertEqual(quota.read_quota([str(cfg)], str(self.dir / "no-codex"), now)["claude"]["total"]["src"], "cache")
            samples.write_text("{bad")
            self.assertEqual(quota.read_quota([str(cfg)], str(self.dir / "no-codex"), now)["claude"]["total"]["src"], "cache")

    def test_older_ledger_missing_default_keys_still_runs_ready(self):
        """T85: a ledger written before some keys existed (here: decisions) must not crash a due default."""
        state = json.loads((self.dir / ".teamwork" / "state.json").read_text())
        for key in ("decisions", "notices", "features"):
            state.pop(key, None)
        state["questions"] = [{"from": "ui", "text": "Which colour?", "t": "2000-01-01T00:00:00+00:00", "default_action": "blue"}]
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(state))
        self.tw("ready")
        self.assertEqual(json.loads((self.dir / ".teamwork" / "state.json").read_text())["decisions"][0]["what"], "blue")

    def test_zh_hint(self):
        self.tw("prefs", "--set", "language=zh"); self.tw("prefs", "--set", "quota_trigger_id=trig_1")
        self.assertIn("关掉额度定时", self.tw("ready").stderr)

class T57Round3Core(QuotaBase):
    """Fixes made by hand after review round 3 (D10 positive path, sensitive-tier normalisation)."""

    def test_known_tier_quota_is_not_due_right_after(self):
        self.event({"seven_day": self.win(0.5), "five_hour": self.win(0.2)})
        self.tw("quota", "--config-dir", str(self.cfg), "--codex-dir", str(self.codex), "--json")
        self.assertIsNotNone(self.state().get("last_quota_at"))
        self.assertFalse(json.loads(self.tw("quota", "--due", "--json").stdout)["due"])

    def test_sensitive_tier_normalises_like_the_big_decision_judge(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        asks = ("pnpm -r publish", "npm --tag beta publish", "yarn npm publish", "curl -sd a https://x.invalid",
                "curl -Fa=b https://x.invalid", "curl -sX POST https://x.invalid", "timeout 30 git push",
                "eval 'git push'", r"find . -exec git push \;", "python -m twine upload d", "bash -c -- 'git push'",
                "FOO=1 git push origin main", "if true; then git push; fi")
        quiet = ("npm run publish", "npm run-script publish", "curl -odata https://x.invalid", "curl -sSL https://x.invalid",
                 "wget -d https://x.invalid", "curl -X GET https://x.invalid")
        for command in asks:
            self.assertEqual(tw.sensitive_decision(command), ("ask", "sensitive"), command)
        for command in quiet:
            self.assertIsNone(tw.sensitive_decision(command), command)

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

class T57Round1(TwCase):
    def setUp(self):
        super().setUp()
        self.tw("prefs", "--set", "language=zh")

    def test_archive_global_live_recheck_retry_and_current_seat(self):
        self.tw("thread", "add", "ui", "--session", "retired", "--status", "idle")
        self.tw("thread", "reseat", "ui", "--session", "current")
        self.tw("thread", "set", "ui", "--status", "idle")
        self.tw("thread", "add", "api", "--session", "other-live", "--status", "working")
        st = self.state(); st["threads"]["api"]["sessions"].append({"session_id": "retired", "from": "2026-01-01T00:00:00+00:00"})
        st["threads"]["ui"]["sessions"].append({"session_id": "other-live", "from": "2026-01-01T00:00:00+00:00", "to": "2026-01-02T00:00:00+00:00", "reason": "replaced"})
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.assertNotIn("retired", [x["session_id"] for x in json.loads(self.tw("archive", "candidates", "--json").stdout)])
        self.assertNotIn("other-live", [x["session_id"] for x in json.loads(self.tw("archive", "candidates", "--json").stdout)])
        self.tw("thread", "add", "review-seat", "--session", "review-sid", "--status", "review")
        st = self.state(); st["threads"]["review-seat"]["sessions"].append({"session_id": "other-live", "from": "2026-01-01T00:00:00+00:00", "to": "2026-01-02T00:00:00+00:00", "reason": "replaced"})
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.assertNotIn("other-live", [x["session_id"] for x in json.loads(self.tw("archive", "candidates", "--json").stdout)])
        self.tw("thread", "set", "api", "--status", "idle")
        self.assertNotIn("other-live", [x["session_id"] for x in json.loads(self.tw("archive", "candidates", "--json").stdout)])
        self.tw("thread", "set", "ui", "--disconnected")
        self.tw("archive", "verify", "current", "--stopped", "会话已停", "--config-dir", str(self.dir / "claude-config"))
        self.tw("thread", "set", "ui", "--status", "working")
        self.assertNotEqual(self.tw("archive", "done", "current", "--user-said", "归档", ok=False).returncode, 0)
        self.tw("thread", "set", "ui", "--status", "idle")
        self.tw("thread", "set", "ui", "--disconnected")
        self.tw("archive", "verify", "current", "--stopped", "再次确认已停", "--config-dir", str(self.dir / "claude-config"))
        self.tw("archive", "done", "current", "--user-said", "归档这条会话")
        self.assertEqual(self.state()["threads"]["ui"]["status"], "archived")
        self.assertNotIn("ui", json.loads(self.tw("threads", "--json").stdout)["other"])
        self.tw("archive", "check", "current", "--not-archived")
        self.assertIn("current", [x["session_id"] for x in json.loads(self.tw("archive", "candidates", "--json").stdout)])

    def test_stuck_thread_list_notice_and_nudge_rules(self):
        self.tw("thread", "add", "planned")
        self.tw("thread", "add", "waiter", "--session", "sid", "--status", "waiting", "--waiting-for", "permission")
        self.tw("thread", "add", "reviewer", "--session", "review-sid", "--status", "review")
        self.assertTrue(any(x["thread"] == "reviewer" for x in json.loads(self.tw("threads", "--json").stdout)["review"]))
        self.assertNotIn("planned", json.loads(self.tw("threads", "--json").stdout)["other"])
        self.tw("thread", "set", "waiter", "--status", "working", "--stuck-on", "cleanup")
        self.tw("thread", "set", "waiter", "--from-status")
        self.assertNotIn("stuck_on", self.state()["threads"]["waiter"])
        self.tw("thread", "set", "waiter", "--stuck-on", "cleanup")
        self.tw("thread", "set", "waiter", "--stuck-on", "none")
        self.tw("prefs", "--set", "stuck_minutes=0")
        self.tw("nudge", "waiter")
        self.assertNotEqual(self.tw("nudge", "waiter", ok=False).returncode, 0)
        self.tw("thread", "set", "waiter", "--status", "idle")
        self.assertNotEqual(self.tw("nudge", "waiter", ok=False).returncode, 0)

    def test_notice_ref_normalization_duplicate_json_and_decision_labels(self):
        self.tw("thread", "add", "ui", "--session", "sid")
        self.tw("notice", "add", "--thread", "ui", "--session", "sid", "--ref", " ref-1 ", "--text", "一轮结束")
        duplicate = self.tw("notice", "add", "--thread", "ui", "--session", "sid", "--ref", "ref-1", "--text", "重复")
        self.assertEqual(json.loads(duplicate.stdout)["text"], "一轮结束")
        self.assertIn("已记过", duplicate.stderr)
        self.tw("ask", "选择绿色按钮")
        self.tw("ask", "--answer", "1", "绿色按钮采用")
        self.assertIn("已回答的问题 1", self.tw("decided", "绿色按钮").stdout)
        self.tw("thread", "set", "ui", "--status", "resolved")
        self.assertIn("ui", self.tw("board").stdout)

    def test_package_path_directory_empty_corrupt_missing_and_dotenv(self):
        package = self.dir / "files"; (package / "sub").mkdir(parents=True)
        (package / ".env").write_text("")
        (package / "sub" / "same").write_text("ok")
        (package / "same").write_text("top")
        wrong = self.tw("package-check", str(package), "--expect", "missing/same", ok=False)
        self.assertEqual(wrong.returncode, 1)
        complete = self.tw("package-check", str(package), "--expect", "./.env", "--expect", "sub/same", "--expect", "sub/")
        self.assertIn("空文件：.env", complete.stdout)
        bad = package / "bad.zip"; bad.write_bytes(b"PK\x03\x04broken")
        self.assertEqual(self.tw("package-check", str(bad), "--expect", "x", ok=False).returncode, 2)
        self.assertIn("包坏了或不完整", self.tw("package-check", str(bad), "--expect", "x", ok=False).stderr)
        missing_path = self.tw("package-check", str(package / "absent"), "--expect", "x", ok=False)
        self.assertEqual(missing_path.returncode, 2)
        self.assertIn("找不到这个路径", missing_path.stderr)

    def test_sensitive_subcommands_and_malformed_state_are_safe(self):
        self.tw("prefs", "--set", "perm_policy=managed-sensitive", "--user-said", "敏感命令需要检查")
        self.env["TW_GUARD_LOG"] = str(self.dir / "own-guard-log.jsonl")
        def judge(command):
            event = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
            return subprocess.run([sys.executable, str(TW), "bash-guard"], cwd=self.dir, env=self.env,
                                  input=event, capture_output=True, text=True)
        for command in ("git push --force-with-lease origin x", "curl --request PATCH https://example.invalid",
                        "curl -X OPTIONS https://example.invalid", "curl --upload-file a https://example.invalid",
                        "gh release create v1", "gh pr create", "gh repo create x"):
            self.assertEqual(json.loads(judge(command).stdout)["hookSpecificOutput"]["permissionDecision"], "ask")
        for command in ("git stash push", "git log --grep push", "git commit -m push"):
            self.assertEqual(judge(command).stdout, "")
        for invalid in ("broken", "[]", '{"prefs":null}', '{"prefs":"invalid"}'):
            (self.dir / ".teamwork" / "state.json").write_text(invalid, encoding="utf-8")
            self.assertEqual(judge("git push origin x").stdout, "")
        self.assertFalse(Path(self.env["TW_GUARD_LOG"]).exists())

    def test_stale_deliverable_dedupe_link_validation_decision_ratio(self):
        self.tw("thread", "add", "api", "--session", "s1")
        self.tw("task", "add", "T1", "--owner", "api", "--title", "package work")
        self.tw("plan", "approve", "--user-said", "approved")
        self.tw("task", "set", "T1", "--status", "dispatched")
        self.tw("task", "set", "T1", "--status", "working")
        self.tw("task", "set", "T1", "--status", "recalled")
        self.tw("task", "set", "T1", "--status", "dispatched")
        first = json.loads(self.tw("deliverable", "add", "old", "--task", "T1", "--dispatch-v", "0").stdout)
        again = self.tw("deliverable", "add", "old revised", "--task", "T1", "--dispatch-v", "0")
        self.assertEqual(json.loads(again.stdout)["id"], first["id"])
        self.assertIn("已记过", again.stderr)
        self.assertEqual(len(self.state()["deliverables"]), 1)
        self.assertNotEqual(self.tw("deliverable", "add", "future", "--task", "T1", "--dispatch-v", "9", ok=False).returncode, 0)
        self.assertNotEqual(self.tw("deliverable", "add", "final", "--status", "final", ok=False).returncode, 0)
        bad = self.tw("deliverable", "add", "bad", "--link", "file:bad")
        self.assertIn("http", bad.stderr)
        self.tw("ask", "选择蓝色按钮")
        self.tw("ask", "--answer", "1", "蓝色按钮采用")
        warning = self.tw("ask", "请问是否可以选蓝色按钮，以及账户安全如何配置数据库缓存", ok=True)
        self.assertNotIn("这件可能已经定过", warning.stderr)

    def test_burn_fable_route_handover_stdout_and_rebuild(self):
        self.tw("thread", "add", "ui", "--session", "s1")
        st = self.state(); st["quota"] = {"tier": "burn-fable"}
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.tw("task", "add", "T1", "--owner", "ui", "--title", "package")
        self.tw("plan", "approve", "--user-said", "approved")
        hint = json.loads(self.tw("ready").stdout)["route_hints"]["T1"]
        self.assertEqual((hint["route"], hint["why"], hint["claude_side"]), ("gpt", "tier", "fable"))
        handover = self.tw("handover", "ui", "--prompt")
        self.assertIn("# teamwork thread protocol", handover.stdout)
        self.assertNotIn("create_session(", handover.stdout)
        self.assertIn("create_session(", handover.stderr)
        self.tw("rebuild")
        self.assertEqual(self.state()["threads"]["ui"]["session_id"], "s1")

class T57Round2(TwCase):
    def setUp(self):
        super().setUp()
        self.tw("prefs", "--set", "language=zh")

    def test_selfcheck_skips_non_object_project_records(self):
        served = self.dir / "served"; broken = served / "bad" / ".teamwork"
        broken.mkdir(parents=True); (broken / "state.json").write_text("[]", encoding="utf-8")
        result = self.tw("selfcheck", "--served", str(served), "--json")
        self.assertIsInstance(json.loads(result.stdout), dict)

    def test_archive_failed_recheck_requires_verify_and_rebuilds_user_said(self):
        self.tw("thread", "add", "ui", "--session", "sid", "--status", "idle")
        self.tw("thread", "set", "ui", "--disconnected")
        st = self.state(); st["threads"]["ui"].update({"stuck_on": "cleanup", "waiting_for": "permission"})
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.tw("archive", "verify", "sid", "--stopped", "确认 sid 已停止", "--config-dir", str(self.dir / "claude-config"))
        self.tw("archive", "done", "sid", "--user-said", "用户原话：可以归档")
        archived_thread = self.state()["threads"]["ui"]
        self.assertNotIn("stuck_on", archived_thread); self.assertNotIn("waiting_for", archived_thread)
        failed = json.loads(self.tw("archive", "check", "sid", "--not-archived").stdout)
        self.assertEqual(failed["history"][-1]["evidence"], "确认 sid 已停止")
        self.assertNotIn("verified_at", failed)
        self.assertNotIn("archived_at", failed)
        self.assertNotEqual(self.tw("archive", "done", "sid", "--user-said", "再次归档", ok=False).returncode, 0)
        self.assertIn("归档复核失败：sid [ui]", self.tw("board").stdout)
        listed = json.loads(self.tw("archive", "list", "--json").stdout)
        self.assertEqual(listed["failed"][0]["session_id"], "sid")
        self.tw("archive", "verify", "sid", "--stopped", "再次确认已停", "--config-dir", str(self.dir / "claude-config"))
        self.tw("archive", "done", "sid", "--user-said", "用户原话：可以归档")
        listed = json.loads(self.tw("archive", "list", "--json").stdout)
        self.assertEqual(listed["archived"][0]["result"], "已核实并归档")
        self.tw("rebuild")
        rec = self.state()["archive"]["sid"]
        self.assertEqual(rec["user_said"], "用户原话：可以归档")
        self.assertIn("用户原话：可以归档", (self.dir / ".teamwork" / "log.jsonl").read_text())

    def test_reseat_clears_stale_state_and_planned_nudge_is_rejected(self):
        self.tw("thread", "add", "ui", "--session", "old", "--status", "waiting", "--waiting-for", "permission", "--stuck-on", "cleanup")
        st = self.state(); st["threads"]["ui"]["nudges"] = [{"at": "2026-01-01T00:00:00+00:00"}]
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        reseat = self.tw("thread", "reseat", "ui", "--session", "new")
        self.assertIn("handover <t> --prompt", reseat.stderr)
        thread = self.state()["threads"]["ui"]
        self.assertNotIn("stuck_on", thread); self.assertNotIn("waiting_for", thread); self.assertNotIn("nudges", thread)
        self.tw("thread", "add", "planned")
        self.assertNotEqual(self.tw("nudge", "planned", ok=False).returncode, 0)

    def test_waiting_stuck_display_and_real_stuck_minutes(self):
        self.tw("thread", "add", "waiter", "--session", "wait", "--status", "waiting",
                "--waiting-for", "permission", "--stuck-on", "cleanup")
        self.tw("thread", "add", "worker", "--session", "work", "--status", "working", "--stuck-on", "cleanup")
        st = self.state()
        st["threads"]["worker"]["last_status_at"] = "2026-06-01T10:00:00-04:00"
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.env["TW_NOW"] = "2026-06-01T11:05:00-04:00"
        board = self.tw("board").stdout
        self.assertIn("waiter (rc) — 在等你点批准 — 清理临时文件", board)
        self.assertIn("可能卡住", board)
        self.assertIn("清理临时文件", board)
        self.assertEqual(json.loads(self.tw("ready").stdout)["stuck"]["worker"], 65)

    def test_merge_grant_follows_thread_owner_not_task_branch(self):
        def git(*args):
            return subprocess.run(["git", "-C", str(self.dir), *args], check=True, capture_output=True, text=True)
        git("init", "-q", "-b", "main")
        git("config", "user.name", "test")
        git("config", "user.email", "test@example.invalid")
        (self.dir / "base").write_text("base\n")
        git("add", "base"); git("commit", "-q", "-m", "base")
        branch = "tw/demo/ui"
        git("checkout", "-q", "-b", branch)
        (self.dir / "change").write_text("change\n")
        git("add", "change"); git("commit", "-q", "-m", "change")
        sha = git("rev-parse", "HEAD").stdout.strip()
        git("checkout", "-q", "main")
        self.tw("thread", "add", "ui", "--branch", branch, "--session", "sid", "--status", "working")
        self.tw("task", "add", "T1", "--owner", "ui", "--title", "work")
        self.tw("plan", "approve", "--user-said", "approve")
        self.tw("task", "set", "T1", "--status", "dispatched")
        st = self.state(); st["tasks"]["T1"]["branch"] = "wrong/task-branch"
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.tw("prefs", "--set", "standing_grants=merge", "--user-said", "允许合并")
        self.tw("merge", branch, "--sha", sha, "--no-test", "docs only", "--apply")
        self.assertEqual(self.state()["grant_uses"][0]["task"], "T1")

    def test_sensitive_commands_are_normalized_and_precise(self):
        self.tw("prefs", "--set", "perm_policy=managed-sensitive", "--user-said", "敏感命令需要复核")
        def judge(command):
            event = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
            result = subprocess.run([sys.executable, str(TW), "bash-guard"], cwd=self.dir, env=self.env,
                                    input=event, capture_output=True, text=True)
            return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] if result.stdout else "allow"
        positive = (
            "X=y env terraform -chdir=infra apply", "env -i terraform apply", "env MODE=prod sh -c 'docker image push x'",
            "if true; then kubectl --context c -n ns apply -f x; fi", "kubectl -n ns delete pod x",
            "python3 -m twine upload dist/*", "chmod --recursive 755 x", "chown --recursive user x",
            "wget --method=PATCH https://example.invalid", "wget --post-file=x https://example.invalid",
            "curl -d x https://example.invalid", "curl --data-raw=x https://example.invalid", "curl -F x https://example.invalid",
        )
        negative = ("wget -d https://example.invalid", "curl -odata https://example.invalid",
                    "npm run publish", "pnpm run publish")
        for command in positive:
            with self.subTest(command=command): self.assertEqual(judge(command), "ask")
        for command in negative:
            with self.subTest(command=command): self.assertEqual(judge(command), "allow")

    def test_package_infers_zip_directories_and_filters_empty_files(self):
        package = self.dir / "bundle.zip"
        with zipfile.ZipFile(package, "w") as zf:
            zf.writestr("app/bin", "payload")
            zf.writestr("unrelated/empty", "")
            zf.writestr("app/empty", "")
        checked = self.tw("package-check", str(package), "--expect", "app/", "--expect", "app/bin", ok=True)
        self.assertIn("空文件：app/empty", checked.stdout)
        self.assertNotIn("unrelated/empty", checked.stdout)
        self.tw("deliverable", "add", "包", "--link", "https://example.invalid/app")
        stale = self.tw("deliverable", "set", "D1", "--status", "stale")
        self.assertEqual(json.loads(stale.stdout)["status"], "stale")
        self.assertIn("过期", self.tw("board", "--share").stdout)
        self.assertNotEqual(self.tw("deliverable", "add", "非法", "--status", "stale", ok=False).returncode, 0)

class T57Round3(TwCase):
    def setUp(self):
        super().setUp()
        self.tw("prefs", "--set", "language=zh")

    def test_stuck_on_forces_board_and_waiting_reason_is_retained(self):
        self.tw("thread", "add", "wait", "--session", "s", "--status", "waiting",
                "--waiting-for", "permission", "--stuck-on", "cleanup")
        board = self.tw("board").stdout
        self.assertIn("可能卡住（进行中，超过 30 分钟没有汇报）", board)
        self.assertIn("在等你点批准", board)
        self.assertIn("清理临时文件", board)

    def test_decision_stop_words_do_not_create_partial_chinese_pairs(self):
        import tw
        self.assertEqual(tw._decision_tokens("为什么选择蓝色怎么样"), tw._decision_tokens("为什么选择蓝色怎么样"))
        self.assertNotIn("为什", tw._decision_tokens("为什么主题不同怎么样"))


if __name__ == "__main__":
    unittest.main()
