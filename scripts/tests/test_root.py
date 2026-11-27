try:
    from ._base import *
except ImportError:
    from _base import *


class RootCauseTest(TwCase):
    def test_zh_project_root_flow_is_chinese(self):
        evidence = os.environ.get("TW_SMOKE_EVIDENCE")
        zh_outputs = []
        forbidden = ("has had", "related failures", "Reply A, B or C", "root review still has",
                     "unmapped", "Root specification", "Every specification line", "symptoms=",
                     "rework=", "next=", "already asked", "requires", "Source",
                     "Original words", "root_ok")

        def run_flow(project, language, save_evidence=False):
            project.mkdir()
            env = {k: v for k, v in os.environ.items() if k != "TEAMWORK_DIR"}
            flow_outputs = []

            def call(*args, ok=True):
                result = subprocess.run([sys.executable, str(TW), *args], cwd=project, env=env,
                                        capture_output=True, text=True)
                record = f"$ tw {' '.join(args)}\nexit={result.returncode}\nstdout:\n{result.stdout}stderr:\n{result.stderr}\n\n"
                if save_evidence and evidence:
                    with open(evidence, "a", encoding="utf-8") as f:
                        f.write(record)
                if ok:
                    self.assertEqual(result.returncode, 0, result.stderr)
                flow_outputs.append(record)
                if save_evidence:
                    zh_outputs.append(record)
                return result

            call("init", "demo", "--goal", "root flow")
            call("prefs", "--set", f"language={language}")
            call("area", "add", "backend", "--paths", "src/**")
            call("area", "add", "other", "--paths", "x/**")
            call("root", "list", "--all")
            call("root", "show", "other")
            call("req", "add", "bug one", "--category", "bug", "--area", "backend")
            call("req", "add", "bug two", "--category", "bug", "--area", "backend")
            call("thread", "add", "api")
            call("task", "add", "T1", "--title", "fix root", "--topics", "backend", "--owner", "api")
            call("root", "list")
            call("plan", "approve", "--user-said", "go")
            call("task", "set", "T1", "--status", "dispatched", ok=False)
            call("root", "start", "backend")
            rc = project / ".teamwork" / "roots" / "backend-RC1.md"
            call("root", "check", "backend", ok=False)
            filled_words = "已核实根因" if language == "zh" else "Verified cause"
            body = rc.read_text(encoding="utf-8").replace("<填>", filled_words).replace("<fill>", filled_words)
            body = re.sub(r"(?m)^(\| [^\n]* \| S\d+ \| [^\n]* \|)[^\n]*$", r"\1 test_zh_project_root_flow_is_chinese |", body)
            rc.write_text(body, encoding="utf-8")
            call("root", "check", "backend")
            call("root", "ask", "backend", "--recommend", "C", "--why", "避免重复故障" if language == "zh" else "avoid repeated failure",
                 "--cost", "低" if language == "zh" else "low", "--root", "共享状态错误" if language == "zh" else "shared state error")
            call("ask", "--answer", "1", "C", ok=False)
            call("root", "answer", "backend", "C", "--user-said", "先补再根治" if language == "zh" else "patch first, then fix the cause")
            call("root", "spec", "backend")
            spec = project / ".teamwork" / "roots" / "backend-RC1-spec.md"
            call("root", "spec", "backend", "--check", ok=False)
            spec_body = spec.read_text(encoding="utf-8").replace("L1. <填>", "L1. 保证共享状态一致").replace("L1. <fill>", "L1. Ensure shared state stays consistent")
            spec_body = spec_body.replace("<填> | <填> |", "L1 | 用户：按此方案处理 |").replace("<fill> | <fill> |", "L1 | User: covered by this plan |")
            spec.write_text(spec_body, encoding="utf-8")
            if language == "zh":
                self.assertIn("编号 | 来源 | 原话", rc.read_text(encoding="utf-8"))
                self.assertIn("故障场景与测试", rc.read_text(encoding="utf-8"))
                self.assertIn("用户纠正映射", spec.read_text(encoding="utf-8"))
                self.assertNotIn("Source", rc.read_text(encoding="utf-8"))
                self.assertNotIn("Original words", rc.read_text(encoding="utf-8"))
                self.assertNotIn("Failure scenarios and tests", rc.read_text(encoding="utf-8"))
                self.assertNotIn("Specification", spec.read_text(encoding="utf-8"))
                self.assertNotIn("User correction mapping", spec.read_text(encoding="utf-8"))
            else:
                for content in (rc.read_text(encoding="utf-8"), spec.read_text(encoding="utf-8"), "".join(flow_outputs)):
                    self.assertNotIn("<填>", content)
                    self.assertNotIn("补", content)
            call("root", "spec", "backend", "--check")
            call("root", "handoff", "backend", "--to", "api", "--task", "T1")
            call("board")
            call("card", "--kind", "status", "--head", "root flow")
            return "".join(flow_outputs)

        run_flow(self.dir / "zh", "zh", save_evidence=True)
        outputs = "".join(zh_outputs)
        for phrase in forbidden:
            self.assertNotIn(phrase, outputs, phrase)
        self.assertIn("症状", outputs)
        self.assertIn("根因", outputs)
        self.assertIn("正常", outputs)

        en_dir = self.dir / "en"
        en_outputs = run_flow(en_dir, "en")
        en_state = json.loads((en_dir / ".teamwork" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(en_state["prefs"]["language"], "en")
        self.assertIn("has had", en_outputs)
        self.assertIn("Symptoms", en_outputs)
        self.assertIn("ok", en_outputs)

    def test_done_starts_clean_second_root_round(self):
        path=self._checked_root()
        self.tw("root","ask","backend","--recommend","A","--why","cause","--cost","one","--root","cause")
        self.tw("root","answer","backend","A","--user-said","fix cause")
        self.tw("task","add","T1","--title","root","--topics","backend")
        st=self.state(); st["tasks"]["T1"].update(status="done",merged_sha="abc1234",fix_kind="root",area_of_task="backend")
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
        self.tw("root","done","backend","--task","T1")
        self.tw("req","add","new one","--category","bug","--area","backend")
        self.tw("req","add","new two","--category","bug","--area","backend")
        self.tw("root","start","backend")
        path=self.dir/".teamwork"/"roots"/"backend-RC2.md"; path.write_text(path.read_text().replace("<填>","filled").replace("<fill>","filled"))
        self.tw("root","check","backend")
        self.tw("root","ask","backend","--recommend","A","--why","new cause","--cost","one","--root","cause")
        self.tw("root","dismiss","backend","--why","second round done")
        self.tw("req","add","third cycle one","--category","bug","--area","backend")
        self.tw("req","add","third cycle two","--category","bug","--area","backend")
        self.tw("root","start","backend")
        path=self.dir/".teamwork"/"roots"/"backend-RC3.md"; path.write_text(path.read_text().replace("<填>","filled").replace("<fill>","filled"))
        self.tw("root","check","backend"); self.tw("root","ask","backend","--recommend","B","--why","cause","--cost","one","--root","cause")

    def test_task_msg_keeps_root_hint_in_task_body(self):
        self.tw("prefs","--set","language=zh")
        self.tw("area","add","backend")
        self.tw("req","add","bug one","--category","bug","--area","backend")
        self.tw("req","add","bug two","--category","bug","--area","backend")
        self.tw("thread","add","api")
        self.tw("task","add","T1","--title","fix","--topics","backend","--owner","api")
        out=self.tw("task-msg","T1").stdout
        self.assertTrue(out.startswith(f"[tw:{self.state()['prefs']['short_name']}] TASK"),out)
        self.assertIn("根因复盘",out)

    def test_root_fresh_handover_namespace_and_spec_gate(self):
        self.tw("thread","add","api")
        self.tw("task","add","T1","--title","fix","--topics","backend","--owner","api")
        self.tw("area","add","backend")
        st=self.state(); st["areas"]["backend"].update(state="rooting",rc=1,spec_checked_at="now")
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
        self.assertIn("run root spec backend",self.tw("root","fresh","api",ok=False).stderr)
        spec=self.dir/".teamwork"/"roots"/"backend-RC1-spec.md"; spec.parent.mkdir(exist_ok=True); spec.write_text("## 规格\nL1. fix\n")
        out=self.tw("root","fresh","api")
        self.assertIn("HANDOVER",out.stdout); self.assertIn(str(spec),out.stdout); self.assertIn("L1. fix",out.stdout)

    def test_urgent_user_words_are_hotfix_authorization_but_negative_is_not(self):
        self.tw("area","add","backend")
        self.tw("req","add","bug one","--category","bug","--area","backend")
        self.tw("req","add","bug two","--category","bug","--area","backend")
        self.tw("thread","add","api"); self.tw("task","add","T1","--title","fix","--topics","backend","--owner","api")
        self.tw("task","set","T1","--status","dispatched","--user-said","先修一下，急")
        debt=self.state()["areas"]["backend"]["debt"][-1]
        self.assertEqual((debt["kind"],debt["why"]),("hotfix","先修一下，急"))
        self.tw("task","add","T2","--title","fix","--topics","backend","--owner","api")
        self.assertNotEqual(self.tw("task","set","T2","--status","dispatched","--user-said","不急",ok=False).returncode,0)

    def test_root_check_requires_started_rc_and_real_scenario_tests(self):
        self.tw("area","add","backend")
        self.tw("req","add","trigger only","--where","backend")
        self.tw("task","add","T1","--title","redo","--topics","backend")
        st=self.state(); st["tasks"]["T1"]["rework_at"]=["2099-01-01T00:00:00"]*2
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
        self.assertNotEqual(self.tw("root","check","backend",ok=False).returncode,0)

    def test_root_ok_label_and_language_normalization(self):
        for index, (language, expected, rejected) in enumerate((("en-US", "ok", "root fix in progress"), ("zh-CN", "正常", "根治中"), (None, "ok", "root fix in progress"))):
            with self.subTest(language=language):
                if language:
                    self.tw("prefs", "--set", f"language={language}")
                else:
                    st=self.state(); st["prefs"].pop("language", None)
                    (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
                area=f"other{index}"
                self.tw("area", "add", area, "--paths", "x/**")
                listing = self.tw("root", "list", "--all").stdout
                showing = self.tw("root", "show", area).stdout
                self.assertIn(expected, listing + showing)
                st = self.state(); st["areas"][area]["state"] = "rooting"
                (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
                refused = self.tw("root", "start", area, ok=False).stderr
                self.assertIn(rejected, refused)

    def test_root_templates_share_language_specific_fill_and_extra_labels(self):
        for index, (language, placeholder, extra) in enumerate((("en", "<fill>", "extra"), ("zh", "<填>", "补"))):
            with self.subTest(language=language):
                self.tw("prefs", "--set", f"language={language}")
                area=f"empty{index}"
                self.tw("area", "add", area)
                self.tw("root", "start", area)
                body = (self.dir/".teamwork"/"roots"/f"{area}-RC1.md").read_text(encoding="utf-8")
                self.assertIn(placeholder, body)
                self.assertIn(f"| {placeholder} | {extra} | {placeholder} | {placeholder} |", body)
                populated=f"sample{index}"
                self.tw("area", "add", populated)
                self.tw("req", "add", "bug", "--category", "bug", "--area", populated)
                self.tw("root", "start", populated)
                populated_body=(self.dir/".teamwork"/"roots"/f"{populated}-RC1.md").read_text(encoding="utf-8")
                self.assertIn(f"| {placeholder} | S1 | {placeholder} | {placeholder} |", populated_body)

    def test_root_start_refused_uses_normalized_language_labels(self):
        self.tw("area", "add", "backend")
        for language, expected in (("en-US", "root fix in progress"), ("zh-CN", "根治中")):
            with self.subTest(language=language):
                self.tw("prefs", "--set", f"language={language}")
                st=self.state(); st["areas"]["backend"]["state"]="rooting"
                (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
                self.assertIn(expected, self.tw("root", "start", "backend", ok=False).stderr)

    def test_root_check_reports_placeholder_found_in_file(self):
        self.tw("prefs", "--set", "language=en")
        path=self._checked_root()
        original=path.read_text()
        path.write_text(original.replace("filled", "<填>"))
        self.assertIn("<填>", self.tw("root", "check", "backend", ok=False).stderr)
        path=self._checked_root("other", symptoms=1)
        original=path.read_text(); lines=original.splitlines()
        for line_no,line in enumerate(lines):
            cols=[part.strip() for part in line.strip().strip("|").split("|")]
            if len(cols)==4 and cols[1]=="S1": lines[line_no]="| issue | S1 | tw.py:1 | - |"
        path.write_text("\n".join(lines)+"\n")
        self.assertNotEqual(self.tw("root","check","other",ok=False).returncode,0)

    def test_root_and_spec_checks_inspect_every_matching_section(self):
        rc=self._checked_root()
        original=rc.read_text()
        rc.write_text(original+"\n## Failure scenarios and tests\n\n| Scenario | Symptom | Current | Test |\n|---|---|---|---|\n| bad | S1 | file:1 | todo |\n| bad | S2 | file:1 | todo |\n")
        self.assertNotEqual(self.tw("root", "check", "backend", ok=False).returncode, 0)
        rc.write_text(original)
        self.tw("root", "check", "backend")
        self.tw("root", "ask", "backend", "--recommend", "C", "--why", "why", "--cost", "low", "--root", "cause")
        self.tw("root", "answer", "backend", "A", "--user-said", "fix")
        self._fill_root_file(rc)
        self.tw("root", "check", "backend")
        self.tw("root", "spec", "backend")
        spec=self.dir/".teamwork"/"roots"/"backend-RC1-spec.md"
        spec.write_text(spec.read_text()+"\n## Specification\n\nL2. <fill>\n")
        self.assertNotEqual(self.tw("root", "spec", "backend", "--check", ok=False).returncode, 0)

    def test_root_and_spec_reject_extended_placeholder_values(self):
        placeholders=("NONE", "N/A", "NA", "TBD", "待定", "TODO", "-", "＜填＞")
        for index, placeholder in enumerate(placeholders):
            with self.subTest(placeholder=placeholder):
                area=f"case{index}"
                self.tw("area", "add", area)
                self.tw("req", "add", "correction", "--category", "correction", "--area", area)
                rc=self._checked_root(area, symptoms=1)
                original=rc.read_text(); lines=original.splitlines()
                for line_no, line in enumerate(lines):
                    cols=[part.strip() for part in line.strip().strip("|").split("|")]
                    if len(cols) == 4 and cols[1] == "S1":
                        lines[line_no]=f"| case | S1 | file:1 | {placeholder} |"
                scenario="\n".join(lines)+"\n"
                rc.write_text(scenario)
                self.assertNotEqual(self.tw("root", "check", area, ok=False).returncode, 0)
                rc.write_text(original)
                self.tw("root", "check", area)
                self.tw("root", "ask", area, "--recommend", "C", "--why", "why", "--cost", "low", "--root", "cause")
                self.tw("root", "answer", area, "A", "--user-said", "fix")
                self._fill_root_file(rc)
                self.tw("root", "check", area)
                self.tw("root", "spec", area)
                spec=self.dir/".teamwork"/"roots"/f"{area}-RC1-spec.md"
                text=spec.read_text().replace("L1. <填>", f"L1. {placeholder}").replace("L1. <fill>", f"L1. {placeholder}")
                spec.write_text(text)
                self.assertNotEqual(self.tw("root", "spec", area, "--check", ok=False).returncode, 0)
                text=spec.read_text().replace(f"L1. {placeholder}", "L1. valid specification")
                updated=[]
                for line in text.splitlines():
                    cols=[part.strip() for part in line.strip().strip("|").split("|")]
                    if len(cols)==5 and cols[0]=="C1":
                        line=f"| C1 | {cols[1]} | {cols[2]} | L1 | User: {placeholder} |"
                    updated.append(line)
                spec.write_text("\n".join(updated)+"\n")
                self.assertNotEqual(self.tw("root", "spec", area, "--check", ok=False).returncode, 0)

    def test_spec_rejects_placeholder_c_reason_and_empty_l_line(self):
        self.tw("area", "add", "backend")
        self.tw("req", "add", "correction", "--category", "correction", "--area", "backend")
        rc=self._checked_root("backend", symptoms=1)
        self.tw("root", "ask", "backend", "--recommend", "C", "--why", "why", "--cost", "low", "--root", "cause")
        self.tw("root", "answer", "backend", "A", "--user-said", "fix")
        self._fill_root_file(rc)
        self.tw("root", "check", "backend")
        self.tw("root", "spec", "backend")
        spec=self.dir/".teamwork"/"roots"/"backend-RC1-spec.md"
        spec.write_text(spec.read_text().replace("L1. <fill>", "L1. <填>"))
        self.assertIn("<填>", self.tw("root", "spec", "backend", "--check", ok=False).stderr)
        text=spec.read_text().replace("L1. <填>", "L1. -").replace("L1. <fill>", "L1. -").replace("<填> | <填> |", "L1 | <填> | 用户：＜填＞ |").replace("<fill> | <fill> |", "L1 | <填> | 用户：＜填＞ |")
        spec.write_text(text)
        self.assertNotEqual(self.tw("root", "spec", "backend", "--check", ok=False).returncode, 0)

    def test_spec_check_requires_all_c_rows_and_nonempty_spec_and_accepts_cn_colon(self):
        self.tw("area","add","backend"); self.tw("req","add","原话 | 带\n换行","--category","correction","--area","backend")
        rc=self._checked_root(); self.tw("root","ask","backend","--recommend","A","--why","cause","--cost","one","--root","cause"); self.tw("root","answer","backend","A","--user-said","fix")
        rc.write_text(rc.read_text().replace("<填>","filled").replace("<fill>","filled")); self.tw("root","check","backend")
        self.tw("root","spec","backend"); spec=self.dir/".teamwork"/"roots"/"backend-RC1-spec.md"
        text=spec.read_text(); self.assertIn(r"原话 \| 带 换行",text)
        text=text.replace("L1. <填>","L1. valid").replace("L1. <fill>","L1. valid").replace("| C1 |", "| C9 |")
        spec.write_text(text)
        self.assertNotEqual(self.tw("root","spec","backend","--check",ok=False).returncode,0)
        text=spec.read_text().replace("| C9 |","| C1 |").replace("L1. valid","L1. <fill>")
        spec.write_text(text)
        self.assertNotEqual(self.tw("root","spec","backend","--check",ok=False).returncode,0)
        text=spec.read_text().replace("L1. <填>","L1. valid").replace("L1. <fill>","L1. valid").replace("<填> | <填> |", "<填> | 用户：暂不采纳 |").replace("<fill> | <fill> |", "<fill> | User: not adopted |")
        spec.write_text(text); self.tw("root","spec","backend","--check")

    def test_implicit_area_baseline_excludes_old_and_ended_requests(self):
        st=self.state(); st["requests"]={"R1":{"text":"old1","status":"open","kind":"request","category":"bug","created":"2020-01-01T00:00:00","area":"ghost"},"R2":{"text":"old2","status":"open","kind":"request","category":"bug","created":"2020-01-02T00:00:00","area":"ghost"}}
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
        self.tw("task","add","T1","--title","work","--topics","ghost")
        self.assertEqual(self.state()["areas"]["ghost"]["state"],"ok")
        self.tw("thread","add","api"); self.tw("task","add","T2","--title","later","--topics","ghost","--owner","api")
        self.tw("req","add","new1","--category","bug","--area","ghost"); self.tw("req","add","new2","--category","bug","--area","ghost")
        self.assertEqual(self.state()["areas"]["ghost"]["state"],"pending")
        self.assertNotEqual(self.tw("task","set","T2","--status","dispatched",ok=False).returncode,0)

    def test_ask_all_answers_each_area_and_defaults_apply_to_all(self):
        for area in ("one","two"):
            self._checked_root(area)
        self.tw("root","ask","--all","--recommend","A","--why","cause","--cost","one","--root","cause")
        self.tw("ask","--default","1","default for all","--undoable")
        self.tw("root","answer","one","A","--user-said","yes"); self.tw("root","answer","two","A","--user-said","yes")
        self.assertTrue(all(q["answered"] for q in self.state()["questions"] if q.get("kind")=="root"))

    def test_default_reraise_accepts_user_answer_and_supersedes_old_question(self):
        self._checked_root(); self.tw("root","ask","backend","--recommend","A","--why","cause","--cost","one","--root","cause")
        self.tw("ask","--default","1","default","--undoable")
        self.tw("plan","approve","--user-said","go"); self.tw("thread","add","api"); self.tw("task","add","T1","--title","patch","--topics","backend","--owner","api")
        for _ in range(2):
            self.tw("task","set","T1","--status","dispatched"); self.tw("task","set","T1","--status","working"); self.tw("task","set","T1","--status","recalled")
        self.tw("root","ask","backend","--recommend","A","--why","again","--cost","one","--root","cause")
        old=self.state()["questions"][0]
        self.assertEqual((old.get("answer"),old.get("by_default")),("superseded",True))
        self.tw("root","answer","backend","A","--user-said","real answer")
        self.assertEqual(self.state()["areas"]["backend"]["user_said"],"real answer")

    def test_patchy_is_category_aware_and_narrow(self):
        self.tw("req","add","这个按钮还是不要加了","--category","feature")
        self.assertNotIn("patchy",self.state()["requests"]["R1"])
        self.tw("req","add","登录又坏了","--category","bug","--area","login")
        self.assertTrue(self.state()["requests"]["R2"]["patchy"])

    def test_answer_a_expands_light_rc_and_requires_recheck(self):
        path=self._checked_root(); self.tw("root","ask","backend","--recommend","A","--why","cause","--cost","one","--root","cause")
        self.tw("root","answer","backend","A","--user-said","full review")
        self.assertIn("Backward compatibility",path.read_text())
        self.assertNotEqual(self.tw("root","spec","backend",ok=False).returncode,0)
        body=path.read_text().replace("<填>","filled").replace("<fill>","filled"); path.write_text(body); self.tw("root","check","backend"); self.tw("root","spec","backend")

    def test_done_and_handoff_enforce_area_and_state(self):
        self.tw("area","add","backend"); self.tw("task","add","T1","--title","fix","--topics","other")
        st=self.state(); st["tasks"]["T1"].update(status="done",merged_sha="abc1234",fix_kind="root",area_of_task="other"); st["areas"]["backend"]={"state":"pending","debt":[]}
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
        self.assertNotEqual(self.tw("root","done","backend","--task","T1",ok=False).returncode,0)
        st=self.state(); st["areas"]["backend"].update(state="rooting",spec_checked_at="now"); st["tasks"]["T1"].update(status="planned",fix_kind="root",area_of_task="other"); st["threads"]["api"]={"kind":"rc","branch":"b","sessions":[]}
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
        self.assertNotEqual(self.tw("root","handoff","backend","--to","api","--task","T1",ok=False).returncode,0)

    def test_wrapup_unassigned_only_new_open_requests(self):
        st=self.state(); st["root_since"]="2026-11-24T00:00:00"; st["requests"]={"R1":{"text":"old ended","category":"bug","status":"wont","created":"2020-01-01"},"R2":{"text":"new open","category":"bug","status":"open","created":"2026-11-25T00:00:00"}}
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st)); out=self.tw("wrapup").stdout
        unassigned=next(line for line in out.splitlines() if "Unassigned area:" in line)
        self.assertNotIn("R1: old ended",unassigned); self.assertIn("R2: new open",unassigned)

    def test_save_legacy_state_does_not_invent_empty_areas(self):
        st=self.state(); st.pop("areas",None); (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
        self.tw("prefs","--set","language=en")
        self.assertNotIn("areas",self.state())

    def test_root_gate_next_step_is_state_specific(self):
        self.tw("area","add","backend"); self.tw("req","add","bug1","--category","bug","--area","backend"); self.tw("req","add","bug2","--category","bug","--area","backend")
        self.tw("thread","add","api"); self.tw("task","add","T1","--title","fix","--topics","backend","--owner","api")
        out=self.tw("task","set","T1","--status","dispatched",ok=False)
        self.assertIn("root start backend",out.stderr)

    def test_area_alias_normalization_and_merge_paths_aliases_requests(self):
        self.tw("area","add","backend","--paths","api/**","--aka","service")
        self.tw("area","add","server","--paths","web/**","--aka","webapp")
        self.tw("req","add","case","--where","service","--area","BACKEND")
        self.tw("req","add","move","--where","backend")
        self.tw("req","add","root move source","--where","backend")
        st=self.state(); st["requests"]["R3"].pop("area",None); (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
        self.tw("req","add","root move","--where","service")
        self.tw("root","move","R4","--area","BACKEND")
        self.assertEqual(self.state()["requests"]["R4"]["area"],"backend")
        self.tw("area","merge","backend","server")
        areas=self.state()["areas"]
        self.assertEqual(set(areas["server"]["paths"]),{"api/**","web/**"})
        self.assertEqual(set(areas["server"]["aka"]),{"service","webapp","backend"})
        self.assertEqual(self.state()["requests"]["R1"]["area"],"server")
        self.assertEqual(self.state()["requests"]["R3"]["area"],"server")
        self.assertEqual(self.state()["requests"]["R4"]["area"],"server")

    def test_reraised_patch_debt_survives_save_and_new_answer_starts_fresh_debt(self):
        self._checked_root()
        self.tw("root","ask","backend","--recommend","B","--why","cause","--cost","one","--root","cause")
        self.tw("root","answer","backend","B","--user-said","patch first")
        self.tw("req","drop","R1","--user-said","no longer needed")
        self.tw("req","drop","R2","--user-said","no longer needed")
        self.tw("plan","approve","--user-said","go"); self.tw("thread","add","api")
        for tid in ("T1","T2"):
            self.tw("task","add",tid,"--title","patch","--topics","backend","--owner","api")
            self.tw("task","set",tid,"--status","dispatched")
        self.assertEqual(self.state()["areas"]["backend"]["state"],"pending")
        self.tw("prefs","--set","signals_red_at=3")
        self.assertEqual(self.state()["areas"]["backend"]["state"],"pending")
        self.tw("root","start","backend")
        rc=self.dir/".teamwork"/"roots"/"backend-RC2.md"
        rc.write_text(rc.read_text().replace("| <fill> | extra | <fill> | <fill> |","| scenario | S1 | tw.py:1 | test_root |").replace("<填>","filled").replace("<fill>","filled"))
        self.tw("root","check","backend")
        self.tw("root","ask","backend","--recommend","B","--why","again","--cost","one","--root","cause")
        self.tw("root","answer","backend","B","--user-said","patch again")
        answered=self.state()["areas"]["backend"]["answered_at"]
        self.tw("task","add","T3","--title","patch","--topics","backend","--owner","api")
        self.tw("task","set","T3","--status","dispatched")
        debt=self.state()["areas"]["backend"]["debt"][-1]
        self.assertGreater(debt["at"],answered)
        self.assertEqual(self.state()["areas"]["backend"]["state"],"patched")

    def test_ask_all_default_only_changes_unanswered_area(self):
        for area in ("one","two"): self._checked_root(area)
        self.tw("root","ask","--all","--recommend","A","--why","cause","--cost","one","--root","cause")
        self.tw("root","answer","one","A","--user-said","root it")
        self.tw("ask","--default","1","patch only","--undoable")
        areas=self.state()["areas"]
        self.assertEqual(areas["one"]["state"],"rooting")
        self.assertEqual(areas["two"]["state"],"patched")
        self.assertTrue(areas["two"].get("root_pending"))

    def test_task_set_cannot_change_topic_to_bypass_root_gate(self):
        self.tw("area","add","backend")
        self.tw("req","add","bug one","--category","bug","--area","backend")
        self.tw("req","add","bug two","--category","bug","--area","backend")
        self.tw("task","add","T1","--title","patch","--topics","misc")
        self.tw("plan","approve","--user-said","go")
        result=self.tw("task","set","T1","--status","dispatched","--topics","backend",ok=False)
        self.assertIn("root start backend",result.stderr)
        self.assertEqual(self.state()["tasks"]["T1"]["topics"],["misc"])

    def test_root_start_rejects_answered_and_asked_rounds(self):
        self._checked_root()
        self.tw("root","ask","backend","--recommend","A","--why","cause","--cost","one","--root","cause")
        rejected=self.tw("root","start","backend",ok=False)
        self.assertIn("current state is asked",rejected.stderr)
        self.tw("root","answer","backend","A","--user-said","root cause")
        rejected=self.tw("root","start","backend",ok=False)
        self.assertIn("current state is root fix in progress",rejected.stderr)
        self.assertEqual(self.state()["areas"]["backend"]["user_said"],"root cause")

    def test_area_assignment_wins_over_where_and_merge_keeps_source_alias(self):
        self.tw("area","add","backend"); self.tw("area","add","server")
        self.tw("req","add","bug one","--category","bug","--area","backend","--where","server")
        self.tw("req","add","bug two","--category","bug","--area","backend","--where","server")
        self.tw("root","move","R1","--area","server"); self.tw("root","move","R2","--area","server")
        self.assertEqual(self.state()["areas"]["backend"]["state"],"ok")
        self.tw("area","merge","backend","server")
        self.assertIn("backend",self.state()["areas"]["server"]["aka"])

    def test_dismiss_withdraws_unanswered_root_question_and_default_skips_ok_area(self):
        self._checked_root()
        self.tw("root","ask","backend","--recommend","A","--why","cause","--cost","one","--root","cause")
        self.tw("root","dismiss","backend","--why","false positive")
        q=self.state()["questions"][0]
        self.assertEqual((q["withdrawn"],q["answered"],q["answer"]),(True,True,"withdrawn"))

    def test_root_mode_off_suppresses_all_root_surfaces(self):
        self.tw("area","add","backend"); self.tw("req","add","bug one","--category","bug","--area","backend")
        self.tw("req","add","bug two","--category","bug","--area","backend")
        self.tw("thread","add","api"); self.tw("task","add","T1","--title","repair","--topics","backend","--owner","api")
        self.tw("prefs","--set","root_mode=off")
        logs_before_off=len((self.dir/".teamwork"/"log.jsonl").read_text().splitlines())
        self.assertNotIn("根因",self.tw("board").stdout)
        self.assertNotIn("root",self.tw("task-msg","T1").stdout.lower())
        msg=self.dir/"m.txt"; msg.write_text(clean_task_fixture(f"[tw:{self.state()['prefs']['short_name']}] TASK lead -> api T1\nGoal: repair\nDone when: pass\n"))
        self.assertNotIn("root backend",self.tw("check-msg",str(msg)).stderr)
        self.assertFalse(any('"ev": "root-pending"' in ln for ln in (self.dir/".teamwork"/"log.jsonl").read_text().splitlines()[logs_before_off:]))

    def test_urgent_phrases_are_narrow_and_negatives_do_not_authorize(self):
        for text in ("先修改一下按钮颜色","不太急","我马上要出门，回头再说"):
            self.assertFalse(__import__("tw").urgent_words(text),text)
        self.assertTrue(__import__("tw").urgent_words("先修一下，很急"))

    def test_request_category_is_normalized_on_add_and_set(self):
        self.tw("req","add","issue","--category","缺陷")
        self.assertEqual(self.state()["requests"]["R1"]["category"],"bug")
        self.tw("req","set","R1","--category","修正")
        self.assertEqual(self.state()["requests"]["R1"]["category"],"correction")

    def test_area_and_root_snapshots_restore_rooting_on_rebuild(self):
        self._checked_root()
        self.tw("root","ask","backend","--recommend","A","--why","cause","--cost","one","--root","cause")
        self.tw("root","answer","backend","A","--user-said","root it")
        (self.dir/".teamwork"/"state.json").unlink()
        self.tw("rebuild")
        rebuilt=json.loads((self.dir/".teamwork"/"state.json.rebuilt").read_text())
        self.assertEqual(rebuilt["areas"]["backend"]["state"],"rooting")

    def test_spec_check_detects_new_correction_and_append_adds_new_c_row(self):
        self._checked_root()
        self.tw("root","ask","backend","--recommend","A","--why","cause","--cost","one","--root","cause")
        self.tw("root","answer","backend","A","--user-said","cause")
        rc=self.dir/".teamwork"/"roots"/"backend-RC1.md"; rc.write_text(rc.read_text().replace("<填>","filled").replace("<fill>","filled"))
        self.tw("root","check","backend"); self.tw("root","spec","backend")
        spec=self.dir/".teamwork"/"roots"/"backend-RC1-spec.md"
        spec.write_text(spec.read_text().replace("L1. <填>","L1. maintain behavior").replace("L1. <fill>","L1. maintain behavior"))
        self.tw("req","add","new correction","--category","correction","--area","backend")
        result=self.tw("root","spec","backend","--check",ok=False)
        self.assertIn("R3: new correction",result.stderr)
        self.tw("root","spec","backend","--append")
        self.assertIn("C2",spec.read_text())
        self.assertIn("R3",spec.read_text())

    def test_root_fresh_only_blocks_rooting_tasks_missing_checked_spec(self):
        self.tw("thread","add","api"); self.tw("area","add","backend")
        self.tw("task","add","T1","--title","done","--topics","backend","--owner","api")
        self.tw("task","add","T2","--title","open","--topics","backend","--owner","api")
        st=self.state(); st["tasks"]["T1"]["status"]="done"; st["areas"]["backend"].update(state="pending",rc=1)
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
        self.assertEqual(self.tw("root","fresh","api").returncode,0)
        st=self.state(); st["areas"]["backend"].update(state="rooting",spec_checked_at=None)
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
        self.assertIn("spec --check",self.tw("root","fresh","api",ok=False).stderr)

    def test_area_matching_and_implicit_topics(self):
        self.tw("area","add","backend","--paths","api/**","--aka","service")
        self.tw("task","add","T1","--title","one","--topics","service")
        self.tw("task","add","T2","--title","two","--touches","api/a.py")
        unregistered=self.tw("task","add","T3","--title","three","--topics","ghost")
        self.assertNotIn("area add ghost",unregistered.stderr)
        self.assertNotIn("ghost",self.tw("root","list").stdout)
        self.assertEqual(self.tw("root","list","--all").stdout.count("ghost"),1)
        self.tw("area","add","server")
        self.tw("area","merge","backend","server")
        self.assertNotIn("backend",self.state()["areas"])

    def test_where_autofills_area_and_two_bugs_pending(self):
        self.tw("area","add","backend")
        self.tw("req","add","broken one","--where","backend","--category","bug")
        self.tw("req","add","broken two","--category","bug","--area","backend")
        self.assertEqual(self.state()["requests"]["R2"]["area"],"backend")
        self.assertEqual(self.state()["areas"]["backend"]["state"],"pending")

    def test_patchy_phrase_and_narrow_negative(self):
        self.tw("area","add","backend")
        self.tw("req","add","still broken again","--area","backend")
        self.assertTrue(self.state()["requests"]["R1"]["patchy"])
        self.assertEqual(self.state()["areas"]["backend"]["state"],"pending")
        self.tw("area","add","other")
        self.tw("req","add","还是 A 还是 B","--area","other")
        self.assertNotIn("patchy",self.state()["requests"]["R2"])

    def test_pending_dispatch_gate_and_hotfix_record(self):
        self.tw("area","add","backend")
        self.tw("req","add","bug one","--category","bug","--area","backend")
        self.tw("req","add","bug two","--category","bug","--area","backend")
        self.tw("thread","add","api")
        self.tw("task","add","T1","--title","fix","--topics","backend","--owner","api")
        self.assertNotEqual(self.tw("task","set","T1","--status","dispatched",ok=False).returncode,0)
        self.tw("task","set","T1","--status","dispatched","--hotfix","restore","--user-said","先修一下")
        debt=self.state()["areas"]["backend"]["debt"]
        self.assertEqual(debt[0]["kind"],"hotfix")

    def test_root_review_start_check_question_and_answer(self):
        self.tw("area","add","backend")
        self.tw("req","add","bug one","--category","bug","--area","backend")
        self.tw("req","add","bug two","--category","bug","--area","backend")
        self.tw("root","start","backend")
        rootdir=self.dir/".teamwork"/"roots"
        review=rootdir/"backend-RC1.md"
        self.assertIn("R1",review.read_text())
        self.assertNotEqual(self.tw("root","ask","backend","--recommend","A","--why","cause","--cost","one day","--root","cause",ok=False).returncode,0)
        review.write_text(review.read_text().replace("<填>","filled").replace("<fill>","filled"))
        self.tw("root","check","backend")
        self.tw("root","ask","backend","--recommend","A","--why","cause","--cost","one day","--root","cause")
        self.assertEqual(self.state()["questions"][0]["kind"],"root")
        self.assertNotEqual(self.tw("root","ask","backend","--recommend","A","--why","again","--cost","one","--root","cause",ok=False).returncode,0)
        self.tw("root","answer","backend","A","--user-said","fix the cause")
        self.assertEqual(self.state()["areas"]["backend"]["state"],"rooting")

    def test_spec_maps_corrections_and_check(self):
        self.tw("area","add","backend")
        self.tw("req","add","user correction","--category","correction","--area","backend")
        self.tw("root","start","backend")
        rc=self.dir/".teamwork"/"roots"/"backend-RC1.md"
        rc.write_text(rc.read_text().replace("<填>","filled").replace("<fill>","filled"))
        self.tw("root","check","backend")
        self.tw("root","spec","backend")
        spec=self.dir/".teamwork"/"roots"/"backend-RC1-spec.md"
        self.assertIn("C1",spec.read_text())
        self.assertNotEqual(self.tw("root","spec","backend","--check",ok=False).returncode,0)
        spec.write_text(spec.read_text().replace("L1. <填>","L1. implementation").replace("L1. <fill>","L1. implementation").replace("| <填> | <填> |","| L1 |  |").replace("| <fill> | <fill> |","| L1 |  |"))
        self.tw("root","spec","backend","--check")

    def test_dismiss_done_and_legacy_state(self):
        self.tw("area","add","backend")
        self.tw("req","add","bug","--category","bug","--area","backend")
        self.tw("root","dismiss","backend","--why","false positive")
        self.assertEqual(self.state()["areas"]["backend"]["state"],"ok")
        st=self.state(); st.pop("areas",None); st["prefs"]={}; (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
        self.assertEqual(self.tw("board").returncode,0)
        self.assertEqual(self.tw("task","add","T1","--title","legacy").returncode,0)

    def test_mode_off_scan_preview_and_task_tag(self):
        self.tw("area","add","backend")
        self.tw("req","add","bug one","--category","bug","--area","backend")
        self.tw("req","add","bug two","--category","bug","--area","backend")
        self.tw("prefs","--set","root_mode=off")
        self.tw("thread","add","api")
        self.tw("task","add","T1","--title","repair","--topics","backend","--owner","api")
        self.tw("plan","approve","--user-said","approved")
        self.tw("task","set","T1","--status","dispatched")
        before=self.state()
        self.tw("root","scan","--backfill")
        self.assertEqual(self.state()["rev"],before["rev"])
        st=self.state(); st["tasks"]["T1"]["fix_kind"]="patch"; (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
        self.assertIn("[patch]",self.tw("board").stdout)

    def _checked_root(self, area="backend", symptoms=2):
        self.tw("area","add",area)
        for i in range(symptoms): self.tw("req","add",f"bug {i}","--category","bug","--area",area)
        self.tw("root","start",area)
        path=self.dir/".teamwork"/"roots"/f"{area}-RC1.md"
        body=path.read_text().replace("<填>","filled").replace("<fill>","filled")
        path.write_text(body)
        self.tw("root","check",area)
        return path

    def test_root_ask_defaults_recommendation_to_a_for_one_and_all(self):
        self._checked_root("single")
        single = self.tw("root", "ask", "single", "--why", "shared cause", "--cost", "normal", "--root", "cause")
        self.assertIn("Recommended: A", single.stdout)
        self._checked_root("group-one")
        self._checked_root("group-two")
        all_areas = self.tw("root", "ask", "--all", "--why", "shared cause", "--cost", "normal", "--root", "cause")
        self.assertEqual(all_areas.stdout.count("Recommended: A"), 2)

    def _fill_root_file(self, path):
        body=path.read_text(encoding="utf-8").replace("<填>","filled").replace("<fill>","filled")
        path.write_text(body,encoding="utf-8")

    def test_task_add_status_is_ignored_and_root_list_next_is_interpolated(self):
        self.tw("area","add","backend")
        self.tw("task","add","T1","--title","fix","--topics","backend","--status","dispatched")
        self.assertEqual(self.state()["tasks"]["T1"]["status"],"planned")
        self.tw("plan","approve","--user-said","go")
        self.tw("task","add","T2","--title","fix","--topics","backend","--status","dispatched")
        self.assertEqual(self.state()["tasks"]["T2"]["status"],"approved")
        self.tw("req","add","bug 1","--category","bug","--area","backend")
        self.tw("req","add","bug 2","--category","bug","--area","backend")
        self.assertIn("root start backend",self.tw("root","list").stdout)

    def test_root_answer_validates_choice_question_and_persists_full_level(self):
        self._checked_root()
        self.tw("root","ask","backend","--recommend","A","--why","cause","--cost","one","--root","cause")
        self.assertNotEqual(self.tw("root","answer","backend","D","--user-said","x",ok=False).returncode,0)
        st=self.state(); st["areas"]["backend"]["question"]=None; (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
        self.assertNotEqual(self.tw("root","answer","backend","A","--user-said","cause",ok=False).returncode,0)
        st=self.state(); st["areas"]["backend"]["question"]=1; (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
        self.tw("root","answer","backend","A","--user-said","fix the cause")
        self.assertEqual(self.state()["areas"]["backend"]["level"],"full")
        self.assertEqual(__import__("tw").radar(self.state())["backend"]["level"],"full")

    def test_ask_answer_refuses_root_questions_and_root_answer_is_direct(self):
        self._checked_root()
        self.tw("root","ask","backend","--recommend","A","--why","cause","--cost","one","--root","cause")
        rejected=self.tw("ask","--answer","1","approved",ok=False)
        self.assertIn("root answer backend A|B|C",rejected.stderr)
        self.tw("root","answer","backend","A","--user-said","yes")
        self.assertTrue(self.state()["questions"][0]["answered"])

    def test_root_reraise_after_two_patch_dispatches_and_reask(self):
        self._checked_root()
        self.tw("root","ask","backend","--recommend","B","--why","cause","--cost","one","--root","cause")
        self.tw("root","answer","backend","B","--user-said","patch now")
        self.tw("plan","approve","--user-said","go")
        self.tw("thread","add","api")
        self.tw("task","add","T1","--title","patch","--topics","backend","--owner","api")
        self.tw("task","set","T1","--status","dispatched")
        self.tw("task","set","T1","--status","working")
        self.tw("task","set","T1","--status","recalled")
        self.tw("task","set","T1","--status","dispatched")
        self.assertEqual(self.state()["areas"]["backend"]["state"],"pending")
        self.assertTrue(any('"ev": "root-reraise"' in x for x in (self.dir/".teamwork"/"log.jsonl").read_text().splitlines()))
        self.tw("root","ask","backend","--recommend","A","--why","again","--cost","one","--root","cause")

    def test_pending_area_returns_to_ok_after_symptom_dismissal(self):
        self.tw("area","add","backend")
        self.tw("req","add","bug one","--category","bug","--area","backend")
        self.tw("req","add","bug two","--category","bug","--area","backend")
        self.assertEqual(self.state()["areas"]["backend"]["state"],"pending")
        self.tw("root","dismiss","backend","--req","R2","--why","not repeated")
        self.assertEqual(self.state()["areas"]["backend"]["state"],"ok")

    def test_root_fix_kind_survives_task_dispatch(self):
        self._checked_root()
        self.tw("root","ask","backend","--recommend","A","--why","cause","--cost","one","--root","cause")
        self.tw("root","answer","backend","A","--user-said","fix it")
        self.assertNotEqual(self.tw("root","spec","backend",ok=False).returncode,0)
        rc=self.dir/".teamwork"/"roots"/"backend-RC1.md"; rc.write_text(rc.read_text().replace("<填>","filled").replace("<fill>","filled")); self.tw("root","check","backend")
        self.tw("root","spec","backend")
        spec=self.dir/".teamwork"/"roots"/"backend-RC1-spec.md"; spec.write_text(spec.read_text().replace("<填>","L1").replace("<fill>","L1"))
        self.tw("root","spec","backend","--check")
        self.tw("plan","approve","--user-said","go")
        self.tw("thread","add","api")
        self.tw("task","add","T1","--title","root","--topics","backend","--owner","api")
        self.tw("root","handoff","backend","--to","api","--task","T1")
        self.tw("task","set","T1","--status","dispatched")
        self.assertEqual(self.state()["tasks"]["T1"]["fix_kind"],"root")

    def test_radar_implicit_area_can_start_and_unseen_area_fails(self):
        self.tw("req","add","bug one","--category","bug","--area","ghost")
        self.tw("req","add","bug two","--category","bug","--area","ghost")
        self.tw("root","start","ghost")
        self.assertTrue(self.state()["areas"]["ghost"]["implicit"])
        self.assertNotEqual(self.tw("root","start","unknown",ok=False).returncode,0)

    def test_check_msg_warns_when_task_area_has_root_review(self):
        self.tw("area","add","backend")
        self.tw("thread","add","api")
        self.tw("req","add","bug one","--category","bug","--area","backend")
        self.tw("req","add","bug two","--category","bug","--area","backend")
        self.tw("task","add","T1","--title","repair","--topics","backend","--owner","api")
        p=self.dir/"msg.txt"; p.write_text(clean_task_fixture(f"[tw:{self.state()['prefs']['short_name']}] TASK lead -> api T1\nGoal: repair\nDone when: pass\n"))
        self.assertIn("root backend",self.tw("check-msg",str(p)).stderr)

    def test_card_task_rows_have_fix_kind_prefix(self):
        self.tw("plan","approve","--user-said","go")
        self.tw("task","add","T1","--title","repair")
        st=self.state(); st["tasks"]["T1"]["fix_kind"]="patch"; st["tasks"]["T1"]["status"]="working"
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
        self.assertIn("[patch] T1 repair",self.tw("card","--kind","status","--head","status").stdout)

    def test_root_board_and_share_labels_follow_chinese_language(self):
        self.tw("prefs","--set","language=zh")
        self.tw("area","add","backend")
        self.tw("req","add","bug one","--category","bug","--area","backend")
        self.tw("req","add","bug two","--category","bug","--area","backend")
        board=self.tw("board").stdout
        self.assertIn("待根因",board)
        self.assertIn("| 区域 | 状态 | 症状 | 返工 | 债 | 复盘 | 下一步 |",board)
        self.assertIn("症状",self.tw("board","--share").stdout)

    def test_root_ask_state_gate(self):
        self.tw("area","add","backend")
        self.assertNotEqual(self.tw("root","ask","backend","--recommend","A","--why","cause","--cost","one","--root","cause",ok=False).returncode,0)

    def test_root_start_no_overwrite_and_check_requires_each_symptom(self):
        path=self._checked_root(symptoms=2)
        st=self.state(); st["areas"]["backend"]["rc"]=1; (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
        (self.dir/".teamwork"/"roots"/"backend-RC2.md").write_text("existing")
        self.assertNotEqual(self.tw("root","start","backend",ok=False).returncode,0)
        body=path.read_text(); heading="## Failure scenarios and tests" if "## Failure scenarios and tests" in body else "## 故障场景与测试"
        intro, scenarios=body.split(heading,1)
        path.write_text(intro+heading+scenarios.replace("S2 |", "S9 |"))
        self.assertNotEqual(self.tw("root","check","backend",ok=False).returncode,0)

    def test_default_root_keeps_question_unanswered_until_direct_answer(self):
        self._checked_root()
        self.tw("root","ask","backend","--recommend","A","--why","cause","--cost","one","--root","cause")
        self.tw("ask","--default","1","只先补","--undoable")
        st=self.state(); self.assertEqual(st["areas"]["backend"]["state"],"patched"); self.assertTrue(st["areas"]["backend"]["root_pending"])
        self.assertFalse(st["questions"][0]["answered"]); self.assertNotIn("user_said",st["areas"]["backend"])
        self.tw("plan","approve","--user-said","go"); self.tw("thread","add","api"); self.tw("task","add","T1","--title","root","--topics","backend")
        self.assertNotEqual(self.tw("root","handoff","backend","--to","api","--task","T1",ok=False).returncode,0)
        self.tw("root","answer","backend","A","--user-said","root fix")
        self.assertEqual(self.state()["areas"]["backend"]["state"],"rooting")
        self.assertNotEqual(self.tw("root","spec","backend",ok=False).returncode,0)
        rc=self.dir/".teamwork"/"roots"/"backend-RC1.md"; rc.write_text(rc.read_text().replace("<填>","filled").replace("<fill>","filled")); self.tw("root","check","backend")
        self.tw("root","spec","backend")
        spec=self.dir/".teamwork"/"roots"/"backend-RC1-spec.md"; spec.write_text(spec.read_text().replace("<填>","L1").replace("<fill>","L1"))
        self.tw("root","spec","backend","--check")
        self.tw("root","handoff","backend","--to","api","--task","T1")

    def test_root_done_requires_merge_then_clears_debt(self):
        self.tw("area","add","backend")
        self.tw("task","add","T1","--title","fix","--topics","backend")
        st=self.state(); st["areas"]["backend"]["state"]="rooting"; st["areas"]["backend"]["debt"]=[{"kind":"patch"}]
        st["tasks"]["T1"].update(status="done",fix_kind="root"); (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
        self.assertNotEqual(self.tw("root","done","backend","--task","T1",ok=False).returncode,0)
        self.tw("task","set","T1","--merged","abc1234")
        self.tw("root","done","backend","--task","T1")
        self.assertEqual(self.state()["areas"]["backend"]["state"],"ok"); self.assertEqual(self.state()["areas"]["backend"]["debt"],[])

    def test_wrapup_lists_unassigned_bug_requests_and_zero_threshold_rejected(self):
        self.tw("req","add","unassigned bug","--category","bug")
        wrapup=self.tw("wrapup").stdout
        self.assertIn("R1",wrapup)
        self.assertIn("no task or feature carries it",wrapup)
        self.assertNotEqual(self.tw("prefs","--set","root_symptoms_at=0",ok=False).returncode,0)

    def test_full_template_at_root_full_threshold_and_thread_fresh_hint(self):
        self.tw("prefs","--set","root_full_at=4")
        self.tw("area","add","backend")
        for i in range(4): self.tw("req","add",f"bug {i}","--category","bug","--area","backend")
        self.tw("root","start","backend")
        template=(self.dir/".teamwork"/"roots"/"backend-RC1.md").read_text()
        self.assertIn("Backward compatibility",template)
        self.tw("thread","add","api")
        self.tw("task","add","T1","--title","fix","--owner","api")
        for i in range(3): self.tw("req","add",f"correction {i}","--category","correction","--tasks","T1")
        st=self.state(); st["threads"]["api"]["status"]="working"; (self.dir/".teamwork"/"state.json").write_text(json.dumps(st))
        self.assertIn("root fresh api",self.tw("threads").stdout)

class RootCauseSmokeTest(TwCase):
    smoke_evidence = True

    def test_main_chain_end_to_end(self):
        self.tw("thread", "add", "api")
        self.tw("task", "add", "T1", "--title", "login root fix", "--topics", "login", "--owner", "api")
        self.tw("task", "add", "T2", "--title", "login root implementation", "--topics", "login", "--owner", "api", "--accept", "login specification passes")
        self.tw("area", "add", "login", "--paths", "web/login/**")
        self.tw("req", "add", "登录点了没反应", "--category", "bug", "--area", "login")
        self.tw("req", "add", "登录报错 500", "--category", "bug", "--where", "login")
        self.assertIn("login", self.tw("root", "list").stdout)
        self.assertIn("pending", self.tw("root", "list").stdout)
        rejected = self.tw("task", "set", "T1", "--status", "dispatched", ok=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("root", rejected.stderr.lower())

        self.tw("root", "start", "login")
        rc = self.dir / ".teamwork" / "roots" / "login-RC1.md"
        rejected = self.tw("root", "check", "login", ok=False)
        self.assertNotEqual(rejected.returncode, 0)
        body = rc.read_text(encoding="utf-8").replace("<填>", "已核实登录失败根因及验收条件").replace("<fill>", "已核实登录失败根因及验收条件")
        body = re.sub(r"(?m)^(\| [^\n]* \| S\d+ \| [^\n]* \|)[^\n]*$",
                      lambda m: m.group(1) + " test_main_chain_end_to_end |", body)
        rc.write_text(body, encoding="utf-8")
        self.tw("root", "check", "login")
        self.tw("root", "ask", "login", "--recommend", "C", "--why", "先止血避免重复故障", "--cost", "低", "--root", "修复共享认证状态")
        self.assertEqual(self.state()["questions"][-1]["kind"], "root")
        self.tw("root", "answer", "login", "C", "--user-said", "先补再根治")
        self.assertEqual(self.state()["areas"]["login"]["state"], "rooting")
        self.assertTrue(self.state()["areas"]["login"]["patch_first"])
        self.tw("plan", "approve", "--user-said", "批准根因修复计划")
        self.tw("task", "set", "T1", "--status", "dispatched")

        self.tw("area", "add", "login_a", "--paths", "web/login-a/**")
        self.tw("req", "add", "备用登录故障一", "--category", "bug", "--area", "login_a")
        self.tw("req", "add", "备用登录故障二", "--category", "bug", "--area", "login_a")
        self.tw("root", "start", "login_a")
        self.tw("root", "check", "login_a", ok=False)
        rc_a = self.dir / ".teamwork" / "roots" / "login-a-RC1.md"
        body = rc_a.read_text(encoding="utf-8").replace("<填>", "已完成初步症状和场景分析").replace("<fill>", "已完成初步症状和场景分析")
        body = re.sub(r"(?m)^(\| [^\n]* \| S\d+ \| [^\n]* \|)[^\n]*$",
                      lambda m: m.group(1) + " test_main_chain_end_to_end |", body)
        rc_a.write_text(body, encoding="utf-8")
        self.tw("root", "check", "login_a")
        self.tw("root", "ask", "login_a", "--recommend", "A", "--why", "需要全面复核", "--cost", "中", "--root", "检查兼容性与整体设计")
        self.tw("root", "answer", "login_a", "A", "--user-said", "请完整分析")
        body = rc_a.read_text(encoding="utf-8").replace("<填>", "已检查兼容性、现有实现与验收测试").replace("<fill>", "已检查兼容性、现有实现与验收测试")
        body = re.sub(r"(?m)^(\| [^\n]* \| S\d+ \| [^\n]* \|)[^\n]*$",
                      lambda m: m.group(1) + " test_main_chain_end_to_end |", body)
        rc_a.write_text(body, encoding="utf-8")
        self.tw("root", "check", "login_a")

        self.tw("root", "spec", "login")
        spec = self.dir / ".teamwork" / "roots" / "login-RC1-spec.md"
        spec_body = spec.read_text(encoding="utf-8")
        spec.write_text(spec_body.replace("L1. <填>", "L1. 登录失败时显示明确错误并允许重试").replace("L1. <fill>", "L1. 登录失败时显示明确错误并允许重试"), encoding="utf-8")
        self.tw("root", "spec", "login", "--check", ok=False)
        spec_body = spec.read_text(encoding="utf-8").replace("| C1 |", "| C9 |", 1)
        spec.write_text(spec_body, encoding="utf-8")
        self.tw("root", "spec", "login", "--check", ok=False)
        spec_body = spec.read_text(encoding="utf-8").replace("| C9 |", "| C1 |", 1)
        spec_body = re.sub(r"(?m)^(\| C\d+ \|[^\n]*\|[^\n]*\|) <填> \| <填> \|$", r"\1 L1 | 已由规格覆盖 |", spec_body)
        spec_body = re.sub(r"(?m)^(\| C\d+ \|[^\n]*\|[^\n]*\|) <fill> \| <fill> \|$", r"\1 L1 | User: covered by spec |", spec_body)
        spec.write_text(spec_body, encoding="utf-8")
        self.tw("root", "spec", "login", "--check")

        handoff = self.tw("root", "handoff", "login", "--to", "api", "--task", "T2").stdout
        self.assertIn("HANDOVER", handoff)
        self.assertIn("login-RC1-spec.md", handoff)
        fresh = self.tw("root", "fresh", "api").stdout
        self.assertIn("HANDOVER", fresh)
        self.assertIn("login-RC1-spec.md", fresh)
        self.assertEqual(self.state()["tasks"]["T2"]["fix_kind"], "root")
        self.assertIn("login", self.tw("board").stdout)
        self.assertIn("root", self.tw("board").stdout.lower())
        self.tw("task", "set", "T2", "--status", "dispatched")
        self.tw("task", "set", "T2", "--status", "working")
        self.tw("task", "set", "T2", "--status", "review")
        self.tw("task", "set", "T2", "--merged", "abc1234")
        self.tw("task", "set", "T2", "--review", "pass", "--reviewer", "reviewer")
        self.tw("task", "set", "T2", "--status", "done")
        self.tw("root", "done", "login", "--task", "T2")
        self.tw("req", "add", "再次登录无响应", "--category", "bug", "--area", "login")
        self.tw("req", "add", "再次登录异常", "--category", "bug", "--area", "login")
        self.assertIn("pending", self.tw("root", "list").stdout)
        self.tw("root", "start", "login")
        rc2 = self.dir / ".teamwork" / "roots" / "login-RC2.md"
        body = rc2.read_text(encoding="utf-8").replace("<填>", "已复核第二轮登录问题及修复方案").replace("<fill>", "已复核第二轮登录问题及修复方案")
        body = re.sub(r"(?m)^(\| [^\n]* \| S\d+ \| [^\n]* \|)[^\n]*$",
                      lambda m: m.group(1) + " test_main_chain_end_to_end |", body)
        rc2.write_text(body, encoding="utf-8")
        self.tw("root", "check", "login")
        self.tw("root", "ask", "login", "--recommend", "A", "--why", "确认第二轮共同根因", "--cost", "低", "--root", "修复会话初始化")
        self.assertEqual(self.state()["questions"][-1]["kind"], "root")


if __name__ == "__main__":
    unittest.main()
