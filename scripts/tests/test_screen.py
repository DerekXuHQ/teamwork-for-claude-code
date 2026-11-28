try:
    from ._base import *
except ImportError:
    from _base import *


class T87QuietQuiz(T80Helpers, unittest.TestCase):
    def setUp(self):
        self.tmp=Path(tempfile.mkdtemp(prefix="tw-t87-")); self.oldcwd=Path.cwd()
        self.project=self.tmp/"project"; self.project.mkdir(); self.keys=self.tmp/"keys"; self.keys.mkdir()
        self.config=self.tmp/"claude"; self.config.mkdir()
        self.env={k:v for k,v in os.environ.items() if k != "TEAMWORK_DIR"}
        self.env.update(TW_KEYS_DIR=str(self.keys),CLAUDE_CONFIG_DIR=str(self.config),TW_NOW=dt.datetime.now().astimezone().isoformat(timespec="seconds"))
        os.chdir(self.project); self.cli("init","demo","--goal","test project"); self.cli("prefs","--set","language=zh")
    def tearDown(self): os.chdir(self.oldcwd); shutil.rmtree(self.tmp)
    def setup_compaction(self, seat="lead"):
        session=self.sid()
        if seat=="lead": self.cli("lead","--session",session)
        else: self.cli("thread","add",seat,"--session",session)
        return session
    def answer_values(self,seat="lead"):
        items=json.loads((self.keys/"demo"/f"{seat}-HQ1.json").read_text(encoding="utf-8"))["questions"]
        return ["中文" if i==0 else " ".join(x["keywords"]) for i,x in enumerate(items)]
    def record_read(self,session,seat="lead"):
        base=dt.datetime.fromisoformat(self.env["TW_NOW"])
        path="/tmp/SKILL.md" if seat=="lead" else "/tmp/worker-protocol.md"
        self.record(session,[
            {"type":"user","isCompactSummary":True,"timestamp":(base-dt.timedelta(seconds=1)).isoformat(),"message":{"content":"summary"}},
            {"type":"assistant","timestamp":base.isoformat(),"message":{"content":[{"type":"tool_use","id":"read-ok","name":"Read","input":{"file_path":path}}]}},
            {"type":"user","timestamp":(base+dt.timedelta(seconds=1)).isoformat(),"message":{"content":[{"type":"tool_result","tool_use_id":"read-ok","content":"rules"}]}}
        ])

    def test_00_quiet_quiz_command_grades_and_records_without_user_notice(self):
        st=self.state(); st["standing"]=["能根治就根治：提交前跑检查并报告结果。","能根治的先根治：提交前跑检查并报告结果。","变更要有证据：记录验证命令。","消息简短明确：先给结论。"]
        (self.project/".teamwork"/"state.json").write_text(json.dumps(st,ensure_ascii=False),encoding="utf-8")
        session=self.setup_compaction(); first=self.hook(session); second=self.hook(session)
        for context in (first,second):
            questions=[line.split(". ",1)[1] for line in context.splitlines() if re.match(r"\s*\d+\. ",line)]
            self.assertEqual(len(questions),len(set(questions)))
            self.assertEqual(questions[0],"你该用什么语言回复用户？")
            self.assertNotIn("[quiz",context)
            self.assertIn("必须先做完",context); self.assertIn("不要写进给用户的回复",context)
            command=next(line for line in context.splitlines() if "signals" in line and "--answers" in line)
            self.assertIn("-C "+str(self.project),command)
        self.assertIn("（第 1 次）",first); self.assertIn("（第 2 次）",second)
        self.record_read(session); answers="；".join(f"{i+1}. {x}" for i,x in enumerate(self.answer_values()))
        command=[sys.executable,str(TW.resolve()),"-C",str(self.project),"signals","quiz","HQ1","lead","--answers",answers]
        result=subprocess.run(command,cwd=self.project,env=self.env,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr); self.assertEqual(result.stdout.strip(),"半份测验 HQ1：通过")
        self.assertNotIn("给用户",result.stdout)
        check=self.state()["signals"]["lead"]["compact_check"]
        self.assertEqual({k:check[k] for k in ("answered","passed","reread")},{"answered":True,"passed":True,"reread":True})
        self.assertIn("过",tw._compact_label(self.state(),"lead")); self.assertIn("重读规则：是",tw._compact_label(self.state(),"lead"))
        self.assertFalse(self.hook(session,"resume").startswith("回复用户一律用中文。"))

    def test_deduplicates_matching_standing_questions(self):
        from unittest.mock import patch
        st=self.state(); st["standing"]=["能根治就根治：提交前跑全量检查。","能根治的先根治：提交前跑全量检查。"]
        (self.project/".teamwork"/"state.json").write_text(json.dumps(st,ensure_ascii=False),encoding="utf-8")
        with patch.dict(os.environ,self.env): tw._signals_half_quiz(self.project/".teamwork",st,"lead")
        questions=json.loads((self.keys/"demo"/"lead-HQ1.json").read_text())["questions"]
        self.assertEqual(len([x for x in questions if x["fact"] in st["standing"]]),1)

    def test_colliding_material_leaves_only_language_and_task_question(self):
        from unittest.mock import patch
        st=self.state(); st["standing"]=["能根治就根治：提交前跑全量检查。","能根治的先根治：提交前跑全量检查。"]
        st["tasks"]={"T1":{"owner":"lead","status":"active","accept":"测试结果记录在报告中"}}
        (self.project/".teamwork"/"state.json").write_text(json.dumps(st,ensure_ascii=False),encoding="utf-8")
        with patch.dict(os.environ,self.env): tw._signals_half_quiz(self.project/".teamwork",st,"lead")
        items=json.loads((self.keys/"demo"/"lead-HQ1.json").read_text())["questions"]
        self.assertEqual(items[0]["question"],"你该用什么语言回复用户？")
        self.assertEqual(items[1]["question"],tw.lab(st,"succ_question_task",task="T1"))

    def test_task_and_standing_with_same_fact_ask_once(self):
        from unittest.mock import patch
        st=self.state(); st["standing"]=["测试结果记录在报告中","消息简短明确：先给结论。"]
        st["tasks"]={"T1":{"owner":"lead","status":"active","accept":"测试结果记录在报告中"}}
        (self.project/".teamwork"/"state.json").write_text(json.dumps(st,ensure_ascii=False),encoding="utf-8")
        with patch.dict(os.environ,self.env): tw._signals_half_quiz(self.project/".teamwork",st,"lead")
        facts=[x["fact"] for x in json.loads((self.keys/"demo"/"lead-HQ1.json").read_text())["questions"]]
        self.assertEqual(facts.count("测试结果记录在报告中"),1)

    def test_decimal_inside_answer_does_not_open_next_answer(self):
        from unittest.mock import patch
        keys=self.keys/"demo"; keys.mkdir(parents=True,exist_ok=True)
        (keys/"lead-HQ9.json").write_text(json.dumps({"questions":[{"question":"a","keywords":["兼容"]},{"question":"b","keywords":["中文"]}]},ensure_ascii=False),encoding="utf-8")
        st=self.state(); d=self.project/".teamwork"
        with patch.dict(os.environ,self.env): passed,wrong=tw._signals_quiz_grade_text(d,st,"HQ9","lead","1. 用 2.5 版；2.5 版保留兼容；2. 中文")
        self.assertTrue(passed,wrong)

    def test_shell_read_without_rules_heading_or_writing_is_not_reread(self):
        rows=[{"type":"user","isCompactSummary":True,"message":{"content":"s"}},
              {"type":"assistant","message":{"content":[{"type":"tool_use","id":"a","name":"Bash","input":{"command":"grep -c x SKILL.md"}}]}},
              {"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"a","content":"3"}]}},
              {"type":"assistant","message":{"content":[{"type":"tool_use","id":"b","name":"Bash","input":{"command":"sed -i '' s/a/b/ SKILL.md; cat SKILL.md"}}]}},
              {"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"b","content":"## Rules"}]}}]
        self.assertFalse(tw._compact_reread(rows,"lead",0,len(rows)))

    def test_answers_from_file_are_accepted(self):
        session=self.setup_compaction(); self.hook(session); self.record_read(session)
        values=self.answer_values(); path=self.project/"answer.txt"; path.write_text("\n".join(f"{i+1}. {v}" for i,v in enumerate(values)),encoding="utf-8")
        result=subprocess.run([sys.executable,str(TW.resolve()),"-C",str(self.project),"signals","quiz","HQ1","lead","--answers",str(path)],cwd=self.project,env=self.env,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr); self.assertIn("通过",result.stdout)
    def test_answers_from_stdin_are_accepted(self):
        # '-' reads stdin; the fixture runner uses this separate invocation to supply the payload.
        session=self.setup_compaction(); self.hook(session); self.record_read(session)
        answers="\n".join(f"{i+1}. {v}" for i,v in enumerate(self.answer_values()))
        result=subprocess.run([sys.executable,str(TW.resolve()),"-C",str(self.project),"signals","quiz","HQ1","lead","--answers","-"],input=answers,cwd=self.project,env=self.env,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr); self.assertIn("通过",result.stdout)
    def test_wrong_answer_without_reread_gives_one_sentence(self):
        session=self.setup_compaction(); self.hook(session)
        base=dt.datetime.fromisoformat(self.env["TW_NOW"])
        self.record(session,[{"type":"user","isCompactSummary":True,"timestamp":(base-dt.timedelta(seconds=1)).isoformat(),"message":{"content":"summary"}},
            {"type":"assistant","timestamp":base.isoformat(),"message":{"content":[{"type":"tool_use","id":"e1","name":"Bash","input":{"command":"echo SKILL.md; echo '## Rules'"}}]}},
            {"type":"user","timestamp":(base+dt.timedelta(seconds=1)).isoformat(),"message":{"content":[{"type":"tool_result","tool_use_id":"e1","content":"SKILL.md\n## Rules"}]}}])
        result=subprocess.run([sys.executable,str(TW.resolve()),"-C",str(self.project),"signals","quiz","HQ1","lead","--answers","1. 错误"],cwd=self.project,env=self.env,text=True,capture_output=True)
        self.assertEqual(result.stdout.count("只加这一句"),1,result.stdout); self.assertIn("Read 打开",result.stdout)
        self.assertFalse(self.state()["signals"]["lead"]["compact_check"]["reread"])  # echo of the heading is not a reread
    def test_wrong_answer_emits_user_sentence_and_marks_failed(self):
        session=self.setup_compaction(); self.hook(session); self.record_read(session)
        result=subprocess.run([sys.executable,str(TW.resolve()),"-C",str(self.project),"signals","quiz","HQ1","lead","--answers","1. 错误"],cwd=self.project,env=self.env,text=True,capture_output=True)
        self.assertIn("压缩后自检有答错，已补读规则。",result.stdout); self.assertFalse(self.state()["signals"]["lead"]["compact_check"]["passed"])
    def test_shell_read_of_rules_counts_as_reread(self):
        # real Opus session reread the Rules with `awk ... SKILL.md` instead of the Read tool
        session=self.setup_compaction(); self.hook(session)
        base=dt.datetime.fromisoformat(self.env["TW_NOW"])
        self.record(session,[{"type":"user","isCompactSummary":True,"timestamp":(base-dt.timedelta(seconds=1)).isoformat(),"message":{"content":"summary"}},
            {"type":"assistant","timestamp":base.isoformat(),"message":{"content":[{"type":"tool_use","id":"sh-1","name":"Bash","input":{"command":"awk '/^## Rules/{f=1} f' /x/SKILL.md | head -80"}}]}},
            {"type":"user","timestamp":(base+dt.timedelta(seconds=1)).isoformat(),"message":{"content":[{"type":"tool_result","tool_use_id":"sh-1","content":"## Rules"}]}}])
        answer="\n".join(f"{i+1}. {v}" for i,v in enumerate(self.answer_values()))
        result=subprocess.run([sys.executable,str(TW.resolve()),"-C",str(self.project),"signals","quiz","HQ1","lead","--answers",answer],cwd=self.project,env=self.env,text=True,capture_output=True)
        self.assertNotIn("已补读",result.stdout); self.assertTrue(self.state()["signals"]["lead"]["compact_check"]["reread"])
    def test_missing_read_emits_reread_sentence(self):
        session=self.setup_compaction(); self.hook(session)
        base=dt.datetime.fromisoformat(self.env["TW_NOW"])
        self.record(session,[{"type":"user","isCompactSummary":True,"timestamp":(base-dt.timedelta(seconds=1)).isoformat(),"message":{"content":"summary"}}])
        answer="\n".join(f"{i+1}. {v}" for i,v in enumerate(self.answer_values()))
        result=subprocess.run([sys.executable,str(TW.resolve()),"-C",str(self.project),"signals","quiz","HQ1","lead","--answers",answer],cwd=self.project,env=self.env,text=True,capture_output=True)
        self.assertIn("压缩后没先重读规则，已补读。",result.stdout); self.assertFalse(self.state()["signals"]["lead"]["compact_check"]["reread"])
    def test_no_local_record_gives_no_reread_notice(self):
        session=self.setup_compaction(); self.hook(session)
        answer="\n".join(f"{i+1}. {v}" for i,v in enumerate(self.answer_values()))
        result=subprocess.run([sys.executable,str(TW.resolve()),"-C",str(self.project),"signals","quiz","HQ1","lead","--answers",answer],cwd=self.project,env=self.env,text=True,capture_output=True)
        self.assertIn("通过",result.stdout); self.assertNotIn("已补读",result.stdout)
    def test_thread_output_directs_notice_to_lead(self):
        from unittest.mock import patch
        session=self.setup_compaction("api")
        with patch.dict(os.environ,self.env): text=tw._after_compact(self.project/".teamwork",self.state(),"api",session)
        self.assertIn("线程 api",text); self.assertIn("signals quiz HQ1 api --answers",text)
        self.assertEqual(text.count("用 Read 打开"),1); self.assertTrue(text.split("用 Read 打开",1)[1].splitlines()[0].rstrip().endswith("worker-protocol.md，重读线程协议的 Language、Messages 和 STATUS 三节。"))
        self.record_read(session,"api"); result=subprocess.run([sys.executable,str(TW.resolve()),"-C",str(self.project),"signals","quiz","HQ1","api","--answers","1. 错"],cwd=self.project,env=self.env,text=True,capture_output=True)
        self.assertIn("在下一条发给 lead 的消息里",result.stdout)
    def test_alert_and_swap_have_only_user_reminder_and_no_drift_text(self):
        session=self.setup_compaction()
        for _ in range(2): text=self.hook(session)
        self.assertIn("提醒：这个会话已压缩 2 次。",text); self.assertNotIn("跑偏",text); self.assertNotIn("未算",text)
        for _ in range(2): text=self.hook(session)
        self.assertIn("这个会话已压缩 4 次，建议换一个新会话接手。",text); self.assertIn("TW succ start lead",text)
    def test_successful_normal_compaction_never_mentions_user_sentence(self):
        session=self.setup_compaction(); text=self.hook(session); self.record_read(session)
        answer="\n".join(f"{i+1}. {v}" for i,v in enumerate(self.answer_values()))
        result=subprocess.run([sys.executable,str(TW.resolve()),"-C",str(self.project),"signals","quiz","HQ1","lead","--answers",answer],cwd=self.project,env=self.env,text=True,capture_output=True)
        self.assertNotIn("只加这一句",text+result.stdout)
    def test_legacy_quiz_marker_remains_sweep_compatible(self):
        session=self.setup_compaction(); self.hook(session); self.record_read(session)
        answer="[quiz HQ1]\n"+"\n".join(f"{i+1}. {v}" for i,v in enumerate(self.answer_values()))
        base=dt.datetime.fromisoformat(self.env["TW_NOW"])
        rows=[{"type":"user","isCompactSummary":True,"timestamp":(base-dt.timedelta(seconds=1)).isoformat(),"message":{"content":"summary"}},
              {"type":"assistant","timestamp":base.isoformat(),"message":{"content":[{"type":"tool_use","id":"read-ok","name":"Read","input":{"file_path":"/tmp/SKILL.md"}}]}},
              {"type":"user","timestamp":(base+dt.timedelta(seconds=1)).isoformat(),"message":{"content":[{"type":"tool_result","tool_use_id":"read-ok","content":"rules"}]}},
              {"type":"assistant","timestamp":(base+dt.timedelta(seconds=2)).isoformat(),"message":{"content":answer}}]
        self.record(session,rows); result=json.loads(self.cli("screen","sweep","--json").stdout)
        self.assertTrue(result["compact_checks"][0]["passed"]); self.assertTrue(result["compact_checks"][0]["reread"])


class T80Compact(T80Helpers, unittest.TestCase):
    def setUp(self):
        self.tmp=Path(tempfile.mkdtemp(prefix="tw-t80-")); self.oldcwd=Path.cwd()
        self.project=self.tmp/"project"; self.project.mkdir(); self.keys=self.tmp/"keys"; self.keys.mkdir()
        self.config=self.tmp/"claude"; self.config.mkdir()
        self.env={k:v for k,v in os.environ.items() if k != "TEAMWORK_DIR"}
        self.env.update(TW_KEYS_DIR=str(self.keys),CLAUDE_CONFIG_DIR=str(self.config),TW_NOW=dt.datetime.now().astimezone().isoformat(timespec="seconds"))
        os.chdir(self.project); self.cli("init","demo","--goal","test project"); self.cli("prefs","--set","language=zh")
    def tearDown(self): os.chdir(self.oldcwd); shutil.rmtree(self.tmp)
    def test_find_transcripts_reads_uuid_named_bridge(self):
        session=self.sid(); bridged=self.sid(); project=self.config/"projects"/"fixture"; project.mkdir(parents=True)
        transcript=project/f"{bridged}.jsonl"; transcript.write_text("{}\n",encoding="utf-8")
        bridge=self.config/f"bridge-transcript-cse_{session}.jsonl"; bridge.write_text(json.dumps({"session_id":bridged})+"\n",encoding="utf-8")
        self.assertIn(transcript,tw._find_transcripts(session,[self.config]))
    def test_00_compact_hook_quiz_sweep_and_board(self):
        session=self.sid(); self.cli("lead","--session",session)
        context=self.hook(session)
        self.assertTrue(context.startswith("回复用户一律用中文。")); self.assertIn("刚被压缩（第 1 次）",context); self.assertNotIn("[quiz",context); self.assertIn("--answers",context); self.assertIn("1.",context)
        question,answer=tw.SCREEN_BANK["zh"]["P7"][0]; base=dt.datetime.fromisoformat(self.env["TW_NOW"])
        questions=json.loads((self.keys/"demo"/"lead-HQ1.json").read_text(encoding="utf-8"))["questions"]
        quiz_answer="[quiz HQ1]\n"+"\n".join(f"{i+1}. "+" ".join(q["keywords"]) for i,q in enumerate(questions))
        transcript=Path(__import__("fixtures.regenerate",fromlist=["place"]).place("t80_compaction.jsonl",self.config,session=session))
        rows=[json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines() if line.strip()]
        for index,row in enumerate(rows): row["timestamp"]=(base+dt.timedelta(seconds=index-3)).isoformat()
        rows.append({"type":"assistant","timestamp":(base+dt.timedelta(seconds=len(rows)+1)).isoformat(),
                     "message":{"role":"assistant","content":[{"type":"text","text":quiz_answer}]}})
        transcript.write_text("".join(json.dumps(row,ensure_ascii=False)+"\n" for row in rows),encoding="utf-8")
        self.assertTrue(self.hook(session,"resume").startswith("回复用户一律用中文。"))
        result=json.loads(self.cli("screen","sweep","--json").stdout)
        self.assertTrue(result["compact_checks"][0]["passed"],result); self.assertTrue(result["compact_checks"][0]["reread"],result)
        board=self.cli("board").stdout
        self.assertIn("压缩 1 次",board); self.assertIn("过",board); self.assertIn("重读规则：是",board)
        self.assertFalse(self.hook(session,"resume").startswith("回复用户一律用中文。"))
    def test_compact_sweep_extracts_numbered_answers_after_quiz_marker(self):
        from unittest.mock import patch
        session=self.sid(); self.cli("lead","--session",session); self.hook(session)
        key=self.keys/"demo"/"lead-HQ1.json"
        key.write_text(json.dumps({"seat":"lead","questions":[
            {"question":"规则一","keywords":["每次提交前","全量测试"]},
            {"question":"规则二","keywords":["对外消息","中文"]}
        ]},ensure_ascii=False),encoding="utf-8")
        state=self.state(); state["signals"]["lead"]["half_quiz"]={"id":"HQ1","at":self.env["TW_NOW"]}
        (self.project/".teamwork"/"state.json").write_text(json.dumps(state,ensure_ascii=False),encoding="utf-8")
        base=dt.datetime.fromisoformat(self.env["TW_NOW"]); stamps=[(base+dt.timedelta(seconds=i)).isoformat() for i in (-2,1,2,3)]
        answer="**状态汇报**\n- 项目：测试项目\n- 进度：正常\n\n**小测 HQ1**\n\n[quiz HQ1]\n1. 每次提交前必须跑全量测试。\n2. 所有对外消息都用中文。\n\n（这两条答案来自规则文档。）"
        with patch.dict(os.environ,self.env): grade=tw._signals_quiz_grade_text(self.project/".teamwork",self.state(),"HQ1","lead",answer)
        self.assertTrue(grade[0],grade)
        self.record(session,[
            {"type":"user","isCompactSummary":True,"timestamp":stamps[0],"message":{"content":"summary"}},
            {"type":"assistant","timestamp":stamps[1],"message":{"content":[{"type":"tool_use","id":"read-1","name":"Read","input":{"file_path":"/tmp/SKILL.md"}}]}},
            {"type":"user","timestamp":stamps[2],"message":{"content":[{"type":"tool_result","tool_use_id":"read-1","content":"rules"}]}},
            {"type":"assistant","timestamp":stamps[3],"message":{"content":[{"type":"text","text":answer}]}}
        ])
        result=json.loads(self.cli("screen","sweep","--json").stdout)
        self.assertTrue(result["compact_checks"][0]["passed"],result)
        self.assertTrue(result["compact_checks"][0]["reread"],result)
    def test_compact_sweep_read_error_does_not_count_as_reread(self):
        session=self.sid(); self.cli("lead","--session",session); self.hook(session)
        base=dt.datetime.fromisoformat(self.env["TW_NOW"]); stamps=[(base+dt.timedelta(seconds=i)).isoformat() for i in (-2,1,2,3)]
        self.record(session,[
            {"type":"user","isCompactSummary":True,"timestamp":stamps[0],"message":{"content":"summary"}},
            {"type":"assistant","timestamp":stamps[1],"message":{"content":[{"type":"tool_use","id":"read-bad","name":"Read","input":{"file_path":"/tmp/SKILL.md"}}]}},
            {"type":"user","timestamp":stamps[2],"message":{"content":[{"type":"tool_result","tool_use_id":"read-bad","is_error":True,"content":"<tool_use_error>missing"}]}},
            {"type":"assistant","timestamp":stamps[3],"message":{"content":"[quiz HQ1]\n1. answer"}}
        ])
        result=json.loads(self.cli("screen","sweep","--json").stdout)
        self.assertFalse(result["compact_checks"][0]["reread"],result)
    def test_second_compaction_alerts_user(self):
        session=self.sid(); self.cli("lead","--session",session)
        self.cli("thread","add","api","--session",self.sid())
        state=self.state(); state["threads"]["api"]["status"]="working"; (self.project/".teamwork"/"state.json").write_text(json.dumps(state),encoding="utf-8")
        base=dt.datetime.fromisoformat(self.env["TW_NOW"])
        self.record(session,[{"type":"assistant","timestamp":(base-dt.timedelta(seconds=1)).isoformat(),"message":{"role":"assistant","content":[{"type":"tool_use","name":"Agent","input":{}} for _ in range(3)]}}])
        self.hook(session); text=self.hook(session)
        self.assertIn("提醒：这个会话已压缩 2 次。",text); self.assertNotIn("跑偏",text); self.assertNotIn("未算",text)
        self.assertEqual(self.state()["compactions"]["lead"]["count"],2)
    def test_fourth_compaction_prints_successor_command(self):
        session=self.sid(); self.cli("lead","--session",session)
        for _ in range(4): text=self.hook(session)
        self.assertIn('TW succ start lead --reason "多次压缩"',text)
    def test_thread_compaction_uses_worker_protocol_path(self):
        session=self.sid(); self.cli("thread","add","api","--session",session)
        from unittest.mock import patch
        with patch.dict(os.environ,self.env): text=tw._after_compact(self.project/".teamwork",self.state(),"api",session)
        self.assertIn(str(Path(tw.__file__).resolve().parents[1]/"templates"/"worker-protocol.md"),text)
        self.assertNotIn("references/messages.md",text)
    def test_new_session_resets_compaction_count(self):
        first=self.sid(); second=self.sid()
        self.cli("lead","--session",first); self.hook(first); self.hook(first)
        self.cli("lead","--session",second,"--force"); text=self.hook(second)
        self.assertIn("第 1 次",text); self.assertEqual(self.state()["compactions"]["lead"]["count"],1)
    def test_signals_reuses_hook_quiz_for_compaction_row(self):
        session=self.sid(); self.cli("lead","--session",session)
        self.hook(session); qid=self.state()["signals"]["lead"]["half_quiz"]["id"]
        stamp=self.env["TW_NOW"]; path=self.project/"compact.jsonl"
        path.write_text(json.dumps({"type":"user","isCompactSummary":True,"timestamp":stamp,"message":{"role":"user","content":"summary"}}),encoding="utf-8")
        self.cli("signals","lead","--transcript",path)
        self.assertEqual(self.state()["signals"]["lead"]["half_quiz"]["id"],qid)
    def test_drift_self_work_agent_threshold_and_send_balance(self):
        st=self.state(); st["threads"]["api"]={"status":"working","session_id":"thread"}; stamp=self.env["TW_NOW"]
        def rows(a,s): return [{"type":"assistant","timestamp":stamp,"message":{"role":"assistant","content":[{"type":"tool_use","name":"Agent","input":{}} for _ in range(a)]+[{"type":"tool_use","name":"mcp__send_message","input":{}} for _ in range(s)]}}]
        self.assertEqual(tw._signals_analyze(self.project/".teamwork",st,"lead",rows(3,1),persist=False)[0]["counts"]["drift_self_work"],1)
        self.assertEqual(tw._signals_analyze(self.project/".teamwork",st,"lead",rows(3,5),persist=False)[0]["counts"]["drift_self_work"],0)
    def test_idle_threads_are_not_on_duty_for_drift(self):
        st=self.state(); st["threads"]["api"]={"status":"idle","session_id":"thread","last_status_at":(dt.datetime.now().astimezone()-dt.timedelta(hours=2)).isoformat()}
        stamp=self.env["TW_NOW"]
        result,_=tw._signals_analyze(self.project/".teamwork",st,"lead",[{"type":"assistant","timestamp":stamp,"message":{"content":"still working"}}],persist=False)
        self.assertEqual(result["counts"]["drift_silent_threads"],0)
        def rows(): return [{"type":"assistant","timestamp":stamp,"message":{"content":[{"type":"tool_use","name":"Agent","input":{}} for _ in range(3)]}}]
        self.assertEqual(tw._signals_analyze(self.project/".teamwork",st,"lead",rows(),persist=False)[0]["counts"]["drift_self_work"],0)
    def test_big_picture_requires_all_three_sections(self):
        st=self.state(); stamp=self.env["TW_NOW"]
        def analyze(txt): return tw._signals_analyze(self.project/".teamwork",st,"lead",[{"type":"assistant","timestamp":stamp,"message":{"role":"assistant","content":txt}}],persist=False)[0]["counts"]["drift_dajuguan"]
        self.assertEqual(analyze("【大局观】直接做 / 先提议 / 不做；原始意图：x；成功标准：y"),0)
        self.assertEqual(analyze("[Big picture] Do now / Propose first / Drop; intent: x; success criteria: y"),0)
        self.assertEqual(analyze("【大局观】"),1)
    def test_silent_thread_count_respects_threshold_and_disabled_pref(self):
        st=self.state(); old=(dt.datetime.now().astimezone()-dt.timedelta(hours=2)).isoformat()
        st["threads"]["api"]={"status":"working","session_id":"local","last_status_at":old}
        stamp=self.env["TW_NOW"]
        result,_=tw._signals_analyze(self.project/".teamwork",st,"lead",[{"type":"assistant","timestamp":stamp,"message":{"role":"assistant","content":"still working"}}],persist=False)
        self.assertEqual(result["counts"]["drift_silent_threads"],1); self.assertIn("api",result["drift_silent_threads_names"])
        st["prefs"]["stuck_minutes"]=0
        self.assertEqual(tw._signals_analyze(self.project/".teamwork",st,"lead",[],persist=False)[0]["counts"]["drift_silent_threads"],0)
    def test_compaction_prefs_require_positive_integers_and_cap_hours(self):
        self.cli("prefs","--set","lead_compact_alert=3","lead_compact_swap=6","drift_window_hours=168")
        self.assertEqual(self.state()["prefs"]["drift_window_hours"],168)
        self.cli("prefs","--set","drift_window_hours=169",ok=False)
        self.cli("prefs","--set","lead_compact_swap=0",ok=False)
    def test_compact_block_survives_context_truncation(self):
        session=self.sid(); self.cli("lead","--session",session)
        from unittest.mock import patch
        with patch.object(tw,"CONTEXT_LIMIT",150): text=self.hook(session)
        self.assertTrue(text.startswith("回复用户一律用中文。"))
        notice="\n...(truncated: read .teamwork/BOARD.md, INSTRUCTIONS.md, MEMORY.md)"
        self.assertLessEqual(len(text),150+len(notice))
    def test_compact_drift_failure_keeps_hook_output(self):
        session=self.sid(); self.cli("lead","--session",session)
        state=self.state(); state["prefs"]["lead_compact_alert"]=1; (self.project/".teamwork"/"state.json").write_text(json.dumps(state),encoding="utf-8")
        import contextlib,io
        from unittest.mock import patch
        old=os.environ.copy(); os.environ.update(self.env)
        oldstdin=sys.stdin; output=io.StringIO(); readfd,writefd=os.pipe(); os.write(writefd,json.dumps({"session_id":session,"source":"compact","hook_event_name":"SessionStart"}).encode()); os.close(writefd); sys.stdin=os.fdopen(readfd,"r")
        try:
            with patch.object(tw,"_signals_analyze",side_effect=RuntimeError("drift failed")),contextlib.redirect_stdout(output): tw.cmd_context(None)
        finally: sys.stdin.close(); sys.stdin=oldstdin; os.environ.clear(); os.environ.update(old)
        emitted=json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
        self.assertIn("下一条消息前必须先做完",emitted)
        self.assertNotIn("跑偏",emitted); self.assertNotIn("未算",emitted)
    def test_write_lock_still_emits_without_ledger_mutation(self):
        session=self.sid(); self.cli("lead","--session",session)
        st=self.state(); st["write_lock"]={"ho":"HO1"}; (self.project/".teamwork"/"state.json").write_text(json.dumps(st),encoding="utf-8")
        text=self.hook(session); self.assertTrue(text.startswith("回复用户一律用中文。")); self.assertNotIn("lead",self.state().get("compactions",{}))
        self.assertIn("重读其中的 Rules 一节",text); self.assertNotIn("[quiz HQ",text); self.assertEqual(text.count("用 Read 打开"),1); self.assertNotIn("原始意图",text)


class ScreenShadow(TwCase):
    def setUp(self):
        super().setUp()
        self.keys = self.dir.parent / (self.dir.name + "-keys") / "demo"
        self.keys.mkdir(parents=True)
        self.env["TW_KEYS_DIR"] = str(self.keys.parent)
        self.tw("prefs", "--set", "language=zh")

    def test_ask_score_clear_and_rebuild(self):
        self.tw("thread", "add", "api", "--session", "s-api")
        prompt = self.tw("screen", "ask", "api", "--session", "s-api").stdout
        self.assertIn("这一回只答题", prompt)
        self.assertEqual(len(self.state()["screens"]["SC1"]["items"]), 6)
        self.assertNotIn("questions", self.state()["screens"]["SC1"])
        self.assertTrue((self.keys / "SC1.json").exists())
        answer_file = self.dir / "answers.txt"
        answer_file.write_text("P1 to P6 answers", encoding="utf-8")
        grade_path = self.tw("screen", "brief", "SC1", "--answers", str(answer_file)).stdout.splitlines()[0]
        self.assertTrue(Path(grade_path).is_relative_to(self.keys))
        scores = ",".join(f"P{i}=2" for i in range(1, 7))
        self.tw("screen", "record", "SC1", "--scores", scores)
        self.assertEqual(self.state()["screens"]["SC1"]["verdict"], "pass")
        self.assertIn("作废", self.tw("screen", "clear-msg", "SC1").stdout)
        board = self.tw("board").stdout
        self.assertIn("体检：过（12/12）", board)
        self.tw("rebuild")
        self.assertEqual(self.state()["screens"]["SC1"]["verdict"], "pass")

    def test_strict_key_is_external_and_invalid_scores_fail(self):
        self.tw("thread", "add", "merge")
        self.tw("thread", "set", "merge", "--critical")
        self.tw("screen", "ask", "merge", "--session", "s-bad")
        self.assertEqual(len(self.state()["screens"]["SC1"]["items"]), 7)
        scores = ",".join(f"P{i}={0 if i == 5 else 1}" for i in range(1, 8))
        self.tw("screen", "record", "SC1", "--scores", scores)
        self.assertEqual(self.state()["screens"]["SC1"]["verdict"], "fail")
        self.assertNotEqual(self.tw("thread", "reseat", "merge", "--session", "s-bad", ok=False).returncode, 0)
        self.tw("screen", "ask", "merge", "--session", "s-bad")
        self.tw("screen", "unavailable", "SC2", "--why", "grader unavailable")
        self.assertIn("未体检", self.tw("screen", "list", "--pending").stdout)

    def test_signal_json_and_off_mode(self):
        transcript = self.dir / "transcript.jsonl"
        transcript.write_text("\n".join(json.dumps(x) for x in [
            {"type": "user", "message": {"content": "不对"}},
            {"type": "user", "message": {"content": "又错了"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "已修好"}]}}
        ]) + "\n", encoding="utf-8")
        result = json.loads(self.tw("signals", "lead", "--transcript", str(transcript), "--json").stdout)
        self.assertEqual(result["color"], "red")
        self.assertEqual(result["color_label"], "红")
        self.tw("prefs", "--set", "screen_mode=off")
        self.assertEqual(self.tw("screen", "ask", "lead").stdout.strip(), "体检已关")

class ScreenShadow2(TwCase):
    def setUp(self):
        super().setUp()
        self.keys = self.dir.parent / (self.dir.name + "-keys") / "demo"
        self.keys.mkdir(parents=True)
        self.env["TW_KEYS_DIR"] = str(self.keys.parent)
        self.tw("prefs", "--set", "language=zh")

    def test_screen_and_handover_errors_use_language_labels(self):
        screen = self.tw("screen", "ask", "lead", ok=False)
        self.assertIn("需要 --session", screen.stderr)
        pack_dir = self.dir / ".teamwork" / "private" / "handover" / "HO1-api"
        pack_dir.mkdir(parents=True)
        st = self.state()
        st["handovers"] = {"HO1": {"id":"HO1", "seat":"api", "kind":"thread", "phase":"packed", "lead_session":"lead-session",
                                    "steps":{"start":"now"}, "pack_dir":str(pack_dir),
                                    "candidates":[{"session_id":"candidate-next"}]}}
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        receipt = self.tw("succ", "verify", "HO1", "--candidate", "candidate-next",
                          "--receipt", str(self.dir / "missing-receipt.json"), ok=False)
        self.assertIn("找不到验收回执", receipt.stderr)
        self.tw("succ", "quiz", "HO1", "--candidate", "candidate-next", "--caller", "lead-session")
        quiz = self.tw("succ", "quiz", "HO1", "--candidate", "candidate-next", "--caller", "lead-session", ok=False)
        self.assertNotEqual(quiz.returncode, 0)
        self.assertIn("没有未使用的测验材料", quiz.stderr)

    def test_scoring_is_immutable(self):
        self.tw("thread", "add", "api", "--session", "session_api")
        self.tw("screen", "ask", "api", "--session", "session_api")
        scores = ",".join(f"P{i}=2" for i in range(1, 7))
        self.tw("screen", "record", "SC1", "--scores", scores)
        self.assertNotEqual(self.tw("screen", "record", "SC1", "--scores", scores, ok=False).returncode, 0)
        self.assertNotEqual(self.tw("screen", "unavailable", "SC1", "--why", "later", ok=False).returncode, 0)

    def test_signals_skips_bad_lines_and_does_not_save_while_locked(self):
        st = self.state()
        st["write_lock"] = {"ho": "HO9", "since": "now", "old_session": "lead-old"}
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        transcript = self.dir / "signals.jsonl"
        transcript.write_text('{"type":"user","message":{"content":"不对"}}\nnot-json\n', encoding="utf-8")
        result = json.loads(self.tw("signals", "lead", "--transcript", str(transcript), "--json").stdout)
        self.assertEqual(result["color"], "yellow")
        self.assertEqual(result["bad_lines"], 1)
        self.assertEqual(self.state()["signals"], {})

    def test_session_ids_are_trimmed_and_signal_labels_follow_language(self):
        self.tw("thread", "add", "api", "--session", " session_api ")
        self.assertEqual(self.state()["threads"]["api"]["session_id"], "session_api")
        self.tw("thread", "set", "api", "--session", " session_next ")
        self.assertEqual(self.state()["threads"]["api"]["session_id"], "session_next")
        self.tw("screen", "ask", "api", "--session", " session_next ")
        self.assertEqual(self.state()["screens"]["SC1"]["session_id"], "session_next")
        self.tw("succ", "start", "api", "--reason", "swap")
        self.tw("succ", "candidate", "HO1", " candidate_next ")
        self.assertEqual(self.state()["handovers"]["HO1"]["candidates"][0]["session_id"], "candidate_next")
        import importlib.util
        spec = importlib.util.spec_from_file_location("tw_screen_test", TW)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        screens = self.state()
        screens["screens"]["SC9"] = {"session_id":" session_next ", "verdict":"fail"}
        self.assertTrue(mod._screen_failed(screens, "session_next"))
        self.tw("succ", "abort", "HO1", "--reason", "end")
        transcript=self.dir/"signal-labels.jsonl"
        transcript.write_text(json.dumps({"type":"user","timestamp":dt.datetime.now().astimezone().isoformat(),"message":{"content":"hello"}}),encoding="utf-8")
        self.tw("prefs", "--set", "language=en")
        english = json.loads(self.tw("signals", "lead", "--transcript", str(transcript), "--json").stdout)
        self.assertEqual(english["color_label"], "green")
        self.tw("prefs", "--set", "language=zh")
        chinese = json.loads(self.tw("signals", "lead", "--transcript", str(transcript), "--json").stdout)
        self.assertEqual(chinese["color_label"], "绿")

class T72ScreenAutomationSignalsStats(TwCase):
    def setUp(self):
        super().setUp()
        self.keys = self.dir.parent / (self.dir.name + "-keys")
        self.keys.mkdir()
        self.env["TW_KEYS_DIR"] = str(self.keys)
        subprocess.run(["git", "init", "-q"], cwd=self.dir, check=True)
        self.tw("prefs", "--set", "language=zh")
        self.tw("prefs", "--set", "screen_autograde=false")

    def test_p5_veto_needs_a_second_run_of_the_grader(self):
        """T72 field run: the same reply graded P5=1 then P5=0 (pass, then fail by veto). A veto is final for the
        critical seats, so it holds only when a second run agrees; both runs are kept for the stats card."""
        self.tw("thread","add","ui","--session","session_test_veto")
        sid=json.loads(self.tw("screen","ask","ui","--session","session_test_veto","--json").stdout)["screen"]
        answers=self.dir/"veto-answer.txt"; answers.write_text("逐题作答：先停下来说明报错。",encoding="utf-8")
        flip=self.dir/"flip"; line0="P1=2 P2=2 P3=2 P4=2 P5=0 P6=2"; line1="P1=2 P2=2 P3=2 P4=2 P5=1 P6=2"
        self.env["TW_GRADER_CMD"]=f'if [ -f "{flip}" ]; then echo "{line1}"; else touch "{flip}"; echo "{line0}"; fi'
        self.tw("screen","grade",sid,"--answers",str(answers))
        rec=self.state()["screens"][sid]
        self.assertEqual((rec["scores"]["P5"],rec["veto"],rec["verdict"]),(1,False,"pass"))
        self.assertEqual([r["P5"] for r in rec["grader_runs"]],[0,1])
        sid2=json.loads(self.tw("screen","ask","ui","--session","session_test_veto","--retake","--json").stdout)["screen"]
        self.env["TW_GRADER_CMD"]=f'echo "{line0}"'
        self.tw("screen","grade",sid2,"--answers",str(answers))
        rec=self.state()["screens"][sid2]
        self.assertEqual((rec["veto"],rec["verdict"]),(True,"fail"))  # both runs say 0: the veto holds

    def test_00_dispatch_ready_auto_grades_and_board_shows_language(self):
        self.tw("thread", "add", "ui", "--session", "session_test1")
        self.tw("plan", "approve", "--user-said", "approved")
        self.tw("task", "add", "T1", "--title", "做页面", "--owner", "ui", "--accept", "页面完成")
        dispatched = self.tw("task-msg", "T1", "--dispatch", "--json")
        payload = json.loads(dispatched.stdout)
        self.assertEqual(payload["screen"]["screen"], "SC1")
        self.assertTrue(payload["message"].startswith("[screen SC1]"))
        self.assertIn("先答体检，答完再看下面的任务", payload["message"])
        self.assertEqual(self.state()["screens"]["SC1"]["status"], "asked")
        self.env["TW_GRADER_CMD"] = "cat >/dev/null; printf 'P1=2 P2=2 P3=2 P4=2 P5=2 P6=2\\n'"
        config = self.dir / "grader-config"
        project = config / "projects" / "test-project"
        project.mkdir(parents=True)
        self.env["CLAUDE_CONFIG_DIR"] = str(config)
        (config / "bridge-transcript-cse_test1.jsonl").write_text(json.dumps({"session_id":"local1"})+"\n",encoding="utf-8")
        transcript = project / "local1.jsonl"
        transcript.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [
            {"type":"user","timestamp":dt.datetime.now().astimezone().isoformat(),"message":{"content":"[screen SC1]"}},
            {"type":"assistant","timestamp":dt.datetime.now().astimezone().isoformat(),"message":{"content":[{"type":"text","text":"我会先查任务清单，再逐项回答中文问题。"},{"type":"tool_use","name":"git status","input":{"short":True}},{"type":"tool_result","content":"clean"}]}}
        ]) + "\n", encoding="utf-8")
        self.tw("prefs", "--set", "screen_autograde=true")
        ready=self.tw("ready")
        self.assertIn("已在后台给 1 个体检打分",ready.stderr)
        deadline=time.time()+8
        while time.time()<deadline and self.state()["screens"]["SC1"].get("status")!="graded": time.sleep(.05)
        graded=self.state()["screens"]["SC1"]
        self.assertEqual(graded["grader"], "cmd")
        self.assertEqual(graded["answer_source"], "session")
        self.assertEqual(graded["lang_penalty"], 0)
        self.assertIn("体检：过（12/12）", self.tw("board").stdout)

    def test_events_rows_and_tool_evidence_extraction(self):
        data = {"data": [{"kind": "assistant", "data": {"assistant": {"message": {
            "role": "assistant", "content": [{"type": "text", "text": "检查结果"},
                {"type": "tool_use", "name": "read", "input": {"path": "x"}},
                {"type": "tool_result", "content": "ok"}]}}}}]}
        rows = __import__("tw")._rows_from_events(data)
        self.assertEqual(len(rows), 1)
        answer = __import__("tw")._extract_screen_answer([{"type":"user","message":{"content":"[screen SC1]"}},*rows],"SC1")
        self.assertIn("tool_use read", answer)
        self.assertIn("tool_result", answer)

    def test_origin_controls_user_row_role(self):
        tw=__import__("tw")
        def row(text, origin_marker):
            return {"type":"user", **origin_marker, "message":{"role":"user","content":text}}
        self.assertEqual(tw._row_role(row("human words", {"origin":{"kind":"human"}})),"user")
        self.assertEqual(tw._row_role(row("<agent-message>peer", {"origin":{"kind":"human"}})),"peer")
        self.assertEqual(tw._row_role(row("[tw:T] TASK", {"origin":{"kind":"task-notification"}})),"peer")
        self.assertEqual(tw._row_role(row("report", {"origin":{"body":"report"}})),"other")
        self.assertEqual(tw._row_role(row("[tw:T] TASK", {})),"peer")
        self.assertEqual(tw._row_role(row("[Request interrupted by user]", {"origin":{"kind":"human"}})),"other")
        self.assertEqual(tw._row_role(row("<task-notification>done", {"origin":{"kind":"task-notification"}})),"other")

    def test_tool_rows_unwraps_platform_tool_shapes(self):
        tw=__import__("tw"); rows=[{"id":"s1"}]
        shapes=[
            ({"ccr":{"data":rows}},("data",)),
            ({"data":rows},("data",)),
            ([{"type":"text","text":json.dumps({"data":rows})}],("data",)),
            (rows,("data",)),
            ({"sessions":rows},("sessions",)),
            ({"a":{"b":{"c":{"data":rows}}}},("data",)),
        ]
        for raw, keys in shapes: self.assertEqual(tw._tool_rows(raw,keys),rows)
        self.assertIsNone(tw._tool_rows({"unknown":{"items":rows}}))

    def test_save_and_patch_state_share_save_lock_helper(self):
        from unittest.mock import patch
        tw=__import__("tw"); ledger=self.dir/".teamwork"
        real=tw.ledger_lock
        with patch.object(tw,"ledger_lock",wraps=real) as lock:
            tw.save(ledger,tw.load(ledger),{"ev":"lock-test"})
            self.assertTrue(tw._patch_state(ledger,"lock_test",lambda current,latest:True,{"ev":"lock-test"}))
        self.assertEqual(lock.call_count,2)

    def test_grade_and_signals_accept_events_json(self):
        self.tw("thread", "add", "api", "--session", "session_test_events")
        self.tw("screen", "ask", "api", "--session", "session_test_events")
        events={"data":[
            {"kind":"user","data":{"user":{"message":{"role":"user","content":"[screen SC1]"}}}},
            {"kind":"assistant","data":{"assistant":{"message":{"role":"assistant","content":[{"type":"text","text":"我先核对项目资料，再逐题回答。"},{"type":"tool_use","name":"read","input":{"file":"README"}},{"type":"tool_result","content":"checked"}]}}}}
        ]}
        event_file=self.dir/"events.json"; event_file.write_text(json.dumps(events,ensure_ascii=False),encoding="utf-8")
        self.env["TW_GRADER_CMD"]="cat >/dev/null; printf 'P1=2 P2=2 P3=2 P4=2 P5=2 P6=2\\n'"
        graded=json.loads(self.tw("screen","grade","SC1","--events",str(event_file),"--json").stdout)
        self.assertEqual(graded["answer_source"],"events")
        signals=json.loads(self.tw("signals","api","--events",str(event_file),"--json").stdout)
        self.assertEqual(signals["transcript"],True)

    def test_missing_answer_does_not_mutate_and_sweep_lists_pending(self):
        self.tw("thread", "add", "api", "--session", "session_test2")
        self.tw("screen", "ask", "api", "--session", "session_test2")
        before = self.state()["screens"]["SC1"].copy()
        self.assertNotEqual(self.tw("screen", "grade", "SC1", ok=False).returncode, 0)
        self.assertEqual(self.state()["screens"]["SC1"], before)
        result = json.loads(self.tw("screen", "sweep", "--json").stdout)
        self.assertEqual(result["cloud"], [{"screen":"SC1","seat":"api"}])
        self.assertEqual(self.tw("ready").stderr,"")

    def test_language_penalty_and_stats_read_only(self):
        self.tw("thread", "add", "api", "--session", "session_test3")
        self.tw("screen", "ask", "api", "--session", "session_test3")
        answer_file = self.dir / "answers.txt"
        answer_file.write_text("I checked the files and the current branch before answering every question.",encoding="utf-8")
        scores=",".join(f"P{i}=2" for i in range(1,7))
        self.tw("screen","record","SC1","--scores",scores,"--answers",str(answer_file))
        rec=self.state()["screens"]["SC1"]
        self.assertEqual(rec["lang_penalty"],2)
        self.assertIn("语言 -2",self.tw("board").stdout)
        before=(self.dir/".teamwork"/"state.json").read_text()
        replay=self.dir/"replay"; replay.mkdir(); (replay/"sample.jsonl").write_text(json.dumps({"type":"user","message":{"content":"all good"}}),encoding="utf-8")
        out=self.dir/"stats-card.txt"
        card=self.tw("screen","stats","--replay",str(replay),"--out",str(out)).stdout
        self.assertIn("样本不足",card)
        self.assertIn("sample.jsonl",out.read_text(encoding="utf-8"))
        self.assertEqual((self.dir/".teamwork"/"state.json").read_text(),before)

    def test_compaction_creates_half_quiz_and_yellow_retake(self):
        self.tw("thread","add","api","--session","session_test_compact")
        events=self.dir/"compact.jsonl"
        events.write_text(json.dumps({"type":"summary","isCompactSummary":True,"timestamp":dt.datetime.now().astimezone().isoformat(),"message":{"content":"summary"}}),encoding="utf-8")
        empty=self.tw("signals","api","--transcript",str(events)).stdout
        self.assertIn("半份测验",empty)
        initial_quiz=self.state()["signals"]["api"]["half_quiz"]["id"]
        self.tw("memory","--standing","汇报一律用中文写给用户")
        first=self.tw("signals","api","--transcript",str(events)).stdout
        state=self.state(); quiz=state["signals"]["api"]["half_quiz"]
        self.assertIn("半份测验",first)
        self.assertIn("分析",first)  # the hook and signals reuse the P7 fallback from the same compaction
        self.assertEqual(quiz["id"],initial_quiz)
        key=json.loads(next(self.keys.rglob(f"api-{quiz['id']}.json")).read_text(encoding="utf-8"))
        self.assertTrue(key["questions"][0]["keywords"])
        self.assertNotIn(str(self.keys),json.dumps(self.state(),ensure_ascii=False))  # no key path in the ledger
        first_p7=state["signals"]["api"]["last_p7"]
        self.tw("signals","api","--transcript",str(events))
        self.assertNotEqual(self.state()["signals"]["api"]["last_p7"],first_p7)

    def test_signals_window_observation_red_and_relay_limit(self):
        self.tw("thread","add","api","--session","session_test4")
        transcript=self.dir/"signals.jsonl"
        old=(dt.datetime.now().astimezone()-dt.timedelta(hours=2)).isoformat()
        recent=(dt.datetime.now().astimezone()-dt.timedelta(minutes=1)).isoformat()
        self.tw("prefs","--set","observe_minutes=180")
        transcript.write_text("\n".join(json.dumps(x,ensure_ascii=False) for x in [
            {"type":"user","timestamp":old,"message":{"content":"wrong again"}},
            {"type":"assistant","timestamp":recent,"message":{"content":[{"type":"tool_result","is_error":True,"content":"error"}]}},
            {"type":"assistant","timestamp":recent,"message":{"content":[{"type":"tool_use","name":"retry","input":{}}]}}
        ]),encoding="utf-8")
        result=json.loads(self.tw("signals","api","--transcript",str(transcript),"--json").stdout)
        self.assertEqual(result["color"],"yellow")  # one silent retry is a candidate, not a red
        self.assertEqual(result["counts"]["bypass_error"],1)
        self.assertEqual(result["counts"]["dissatisfaction"],0)
        self.assertGreater(self.state()["observations"]["api"]["bypass"],0)
        st=self.state(); st["handovers"]={"HO1":{"seat":"api","phase":"seated","steps":{"seat":dt.datetime.now().astimezone().isoformat()}},"HO2":{"seat":"api","phase":"retired","steps":{"seat":dt.datetime.now().astimezone().isoformat()}}}
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st),encoding="utf-8")
        self.assertNotEqual(self.tw("succ","start","api","--reason","signals red",ok=False).returncode,0)
        self.assertEqual(self.tw("succ","start","api","--reason","signals red","--user-said","replace api").returncode,0)
        log_rows=[json.loads(x) for x in (self.dir/".teamwork"/"log.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(log_rows[-1]["user_said"],"replace api")
        all_result=self.tw("signals","--all","--json")
        self.assertEqual(json.loads(all_result.stdout)[0]["seat"],"api")

    def test_real_record_shape_answer_signals_and_events(self):
        """Rows as Claude Code writes them: attachments and reminders around the prompt, one row per content block,
        tool results stored as user rows. Found by the T72 field run, where all of this was read as the answer."""
        tw=__import__("tw"); t=dt.datetime.now().astimezone().isoformat()
        rows=[{"type":"attachment","timestamp":t,"attachment":{"text":"<system-reminder>English instructions here</system-reminder>"}},
              {"type":"user","timestamp":t,"message":{"role":"user","content":"[screen SC1]\nP1: 练习"}},
              {"type":"user","timestamp":t,"isMeta":True,"message":{"role":"user","content":"<system-reminder>Some long English reminder text that is not the answer at all</system-reminder>"}},
              {"type":"assistant","timestamp":t,"message":{"role":"assistant","content":[{"type":"thinking","thinking":"let me check git"}]}},
              {"type":"assistant","timestamp":t,"message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"git branch"}}]}},
              {"type":"user","timestamp":t,"message":{"role":"user","content":[{"type":"tool_result","content":"* main"}]}},
              {"type":"assistant","timestamp":t,"message":{"role":"assistant","content":[{"type":"text","text":"不是 develop，查了 git branch，只有 main。已完成核对。"}]}},
              {"type":"user","timestamp":t,"message":{"role":"user","content":"下一件事"}}]
        answer=tw._extract_screen_answer(rows,"SC1")
        self.assertIn("只有 main",answer); self.assertIn("[tool_use Bash",answer); self.assertIn("* main",answer)
        self.assertNotIn("reminder",answer); self.assertNotIn("下一件事",answer)
        words=tw._extract_screen_answer(rows,"SC1",text_only=True)
        self.assertEqual(tw._language_penalty({"prefs":{"language":"zh"}},words),(0,[]))
        self.assertEqual([tw._row_role(r) for r in rows],["other","peer","other","assistant","assistant","tool_result","assistant","user"])
        self.tw("thread","add","api","--session","session_test_shape")
        path=self.dir/"shape.jsonl"; path.write_text("\n".join(json.dumps(r,ensure_ascii=False) for r in rows),encoding="utf-8")
        result=json.loads(self.tw("signals","api","--transcript",str(path),"--json").stdout)
        self.assertEqual(result["counts"]["unsupported_done"],0)  # "已完成" right after a tool run in the same turn
        self.assertEqual(result["color"],"green")
        events={"ccr":{"data":[{"created_at":t,"user":{"internal_anthropic_catchall":{"message":r["message"]}}} if r["type"]=="user" else
                               {"created_at":t,"assistant":{"internal_anthropic_catchall":{"message":r["message"]}}}
                               for r in rows if r["type"] in ("user","assistant") and not r.get("isMeta")],"has_more":False}}
        self.assertEqual(len(tw._rows_from_events(events)),6)  # the page wrapper of list_events is unwrapped
        self.assertIn("只有 main",tw._extract_screen_answer(tw._rows_from_events(events),"SC1"))
        self.assertEqual(tw._screen_score_line("tokens used 12\nP2=1, P1=2\n", ["P1","P2"]),{"P1":2,"P2":1})
        self.assertIsNone(tw._screen_score_line("P1=2", ["P1","P2"]))

    def test_lead_task_arrives_as_meta_cross_session_row(self):
        """A real rc thread gets the lead's TASK (and so the screen) as a user row with isMeta and a
        cross-session-message wrapper (seen in the T72 rc probe): it is where the answer starts, never a complaint."""
        tw=__import__("tw"); t=dt.datetime.now().astimezone().isoformat()
        wrap='<cross-session-message from-session="session_x">\n[tw:T] TASK lead -> ui T1\n[screen SC1]\nP1: 不对，又错了\n</cross-session-message>'
        rows=[{"type":"queue-operation","timestamp":t,"content":wrap},
              {"type":"user","isMeta":True,"timestamp":t,"message":{"role":"user","content":wrap}},
              {"type":"assistant","timestamp":t,"message":{"role":"assistant","content":[{"type":"text","text":"P1：他要的是分析。"}]}}]
        self.assertEqual(tw._row_role(rows[1]),"peer")
        self.assertEqual(tw._extract_screen_answer(rows,"SC1"),"P1：他要的是分析。")
        self.tw("thread","add","ui","--session","session_test_meta")
        path=self.dir/"meta.jsonl"; path.write_text("\n".join(json.dumps(r,ensure_ascii=False) for r in rows),encoding="utf-8")
        result=json.loads(self.tw("signals","ui","--transcript",str(path),"--json").stdout)
        self.assertEqual(result["counts"]["dissatisfaction"],0)

    def test_local_cli_session_record_found_by_its_id(self):
        tw=__import__("tw"); root=Path(tempfile.mkdtemp()); sid="0b8f2c1e-1111-4222-8333-944455556666"
        (root/"projects"/"-tmp-x").mkdir(parents=True); rec=root/"projects"/"-tmp-x"/f"{sid}.jsonl"; rec.write_text("{}\n",encoding="utf-8")
        self.assertEqual(tw._find_transcripts(f"session_{sid}",[str(root)]),[rec])
        self.assertEqual(tw._find_transcripts("session_01NotAUuid",[str(root)]),[])

    def test_manual_language_english_and_no_session_dispatch(self):
        self.tw("thread","add","ui")
        self.tw("plan","approve","--user-said","approved")
        self.tw("task","add","T2","--title","plain task","--owner","ui")
        result=self.tw("task-msg","T2","--dispatch","--json")
        self.assertIsNone(json.loads(result.stdout)["screen"])
        self.tw("prefs","--set","language=en")
        self.assertEqual(__import__("tw")._screen_label(self.state(),"ui"),"")

    def test_compact_rows_language_unset_events_wrappers_and_numeric_times(self):
        tw=__import__("tw")
        summary={"type":"user","isCompactSummary":True,"message":{"role":"user","content":"compact"}}
        self.assertEqual(tw._row_role(summary),"other")
        self.tw("thread","add","api","--session","session_flags")
        events=[{"type":"user","timestamp":1700000000,"isCompactSummary":True,"internal_anthropic_catchall":{"message":{"role":"user","content":"summary"}}}]
        rows=tw._rows_from_events([{"type":"text","text":json.dumps(events)}])
        self.assertEqual(len(rows),1); self.assertTrue(rows[0]["isCompactSummary"])
        self.assertEqual(tw._row_role(rows[0]),"other")
        st=self.state(); result,_=tw._signals_analyze(self.dir/".teamwork",st,"api",rows,persist=False)
        self.assertEqual(result["counts"]["compact"],1)
        self.assertIsNotNone(tw._when(1700000000000)); self.assertIsNone(tw._when("not-a-time"))
        self.assertEqual(tw._language_penalty({"prefs":{"language":""}},"English text"),(0,["language_unset"]))
        self.assertEqual(tw._screen_score_line("P1=<0-2> P2=<0-2>\nP2=1, P1=2",["P1","P2"]),{"P1":2,"P2":1})
        empty=self.dir/"empty-events.json"; empty.write_text("[]",encoding="utf-8")
        self.assertIn("事件里没有可读的消息",self.tw("signals","api","--events",str(empty),ok=False).stderr)

    def test_failed_grader_keeps_provisional_and_sweep_can_retry(self):
        self.tw("thread","add","api","--session","session_retry")
        self.tw("screen","ask","api","--session","session_retry")
        answers=self.dir/"retry-answer.txt"; answers.write_text("逐题核对后回答。",encoding="utf-8")
        self.env["TW_GRADER_CMD"]="exit 2"
        failed=self.tw("screen","grade","SC1","--answers",str(answers),ok=False)
        rec=self.state()["screens"]["SC1"]
        self.assertEqual(rec["status"],"unscored"); self.assertEqual(rec["attempts"],2)
        self.assertNotIn("graded_at",rec); self.assertEqual(rec["provisional"],"pass"); self.assertIn("why",rec)
        self.assertIn("打分暂不可用，已排队补考",self.tw("board").stdout)
        self.env["TW_GRADER_CMD"]="printf 'P1=2 P2=2 P3=2 P4=2 P5=2 P6=2\\n'"
        graded=json.loads(self.tw("screen","grade","SC1","--answers",str(answers),"--json").stdout)
        self.assertEqual(graded["status"],"graded"); self.assertEqual(graded["attempts"],3)
        self.assertNotIn("provisional",graded); self.assertNotIn("why",graded)
        self.assertNotEqual(failed.returncode,0)

    def test_compaction_quiz_dedup_grading_and_red_on_second_failure(self):
        self.tw("thread","add","api","--session","session_quiz")
        self.tw("memory","--standing","汇报一律用中文写给用户")
        stamp=dt.datetime.now().astimezone().isoformat()
        path=self.dir/"quiz.jsonl"
        event={"type":"summary","isCompactSummary":True,"uuid":"compact-1","timestamp":stamp,"message":{"content":"summary"}}
        path.write_text(json.dumps(event,ensure_ascii=False),encoding="utf-8")
        first=self.tw("signals","api","--transcript",str(path)).stdout
        quiz=self.state()["signals"]["api"]["half_quiz"]["id"]
        second=self.tw("signals","api","--transcript",str(path)).stdout
        self.assertIn(quiz,second); self.assertEqual(self.state()["signals"]["api"]["half_quiz"]["id"],quiz)
        key=json.loads(next(self.keys.rglob(f"api-{quiz}.json")).read_text(encoding="utf-8"))
        answers=self.dir/"wrong-answers.txt"; answers.write_text("错答\n"*len(key["questions"]),encoding="utf-8")
        one=self.tw("signals","quiz",quiz,"api","--answers",str(answers)).stdout
        self.assertIn("汇报一律用中文写给用户",one)
        two=self.tw("signals","quiz",quiz,"api","--answers",str(answers)).stdout
        self.assertIn("标红",two); self.assertEqual(self.state()["signals"]["api"]["red_reason"],"压缩后测验两次不过")
        self.assertIn("半份测验",first)

    def test_observation_starts_after_screen_and_keeps_scanning_after_first_done(self):
        self.tw("thread","add","api","--session","session_observe")
        self.tw("prefs","--set","observe_minutes=20")
        t=dt.datetime.now().astimezone(); stamps=[(t+dt.timedelta(seconds=i)).isoformat() for i in range(9)]
        path=self.dir/"observation.jsonl"
        rows=[
            {"type":"user","timestamp":stamps[0],"message":{"content":"[screen SC1] please answer"}},
            {"type":"user","timestamp":stamps[1],"message":{"content":"Now do the task"}},
            {"type":"assistant","timestamp":stamps[2],"message":{"content":[{"type":"tool_result","is_error":True,"content":"error"}]}},
            {"type":"assistant","timestamp":stamps[3],"message":{"content":[{"type":"tool_use","name":"retry","input":{}}]}},
            {"type":"user","timestamp":stamps[4],"message":{"content":"Check completion"}},
            {"type":"assistant","timestamp":stamps[5],"message":{"content":"已完成"}},
            {"type":"assistant","timestamp":stamps[6],"message":{"content":[{"type":"tool_use","name":"verify","input":{}}]}},
            {"type":"assistant","timestamp":stamps[7],"message":{"content":"又已完成"}},
        ]
        path.write_text("\n".join(json.dumps(x,ensure_ascii=False) for x in rows),encoding="utf-8")
        self.tw("signals","api","--transcript",str(path))
        obs=self.state()["observations"]["api"]
        self.assertEqual(obs["bypass"],1); self.assertFalse(obs["first_done_evidence"])

    def test_observation_starts_when_task_arrives_as_cross_session_peer(self):
        tw=__import__("tw"); self.tw("thread","add","api","--session","session_peer_observe")
        t=dt.datetime.now().astimezone(); stamps=[(t+dt.timedelta(seconds=i)).isoformat() for i in range(5)]
        rows=[
            {"type":"user","isMeta":True,"timestamp":stamps[0],"message":{"content":"<cross-session-message>[tw:T9] TASK lead -> api T9</cross-session-message>"}},
            {"type":"user","timestamp":stamps[1],"message":{"content":"开始处理任务"}},
            {"type":"assistant","timestamp":stamps[2],"message":{"content":[{"type":"tool_result","is_error":True,"content":"error"}]}},
            {"type":"assistant","timestamp":stamps[3],"message":{"content":[{"type":"tool_use","name":"retry","input":{}}]}},
        ]
        result,_=tw._signals_analyze(self.dir/".teamwork",self.state(),"api",rows,persist=False)
        self.assertEqual(tw._row_role(rows[0]),"peer")
        self.assertEqual(result["observation"]["bypass"],1)

    def test_completion_evidence_and_error_bypass_stop_at_peer_turn(self):
        tw=__import__("tw"); self.tw("thread","add","api","--session","session_peer_boundary")
        t=dt.datetime.now().astimezone(); stamps=[(t+dt.timedelta(seconds=i)).isoformat() for i in range(7)]
        rows=[
            {"type":"user","timestamp":stamps[0],"message":{"content":"开始处理"}},
            {"type":"assistant","timestamp":stamps[1],"message":{"content":[{"type":"tool_use","name":"read","input":{}}]}},
            {"type":"user","isMeta":True,"timestamp":stamps[2],"message":{"content":"[tw:T9] TASK lead -> api T9"}},
            {"type":"assistant","timestamp":stamps[3],"message":{"content":"已完成"}},
            {"type":"assistant","timestamp":stamps[4],"message":{"content":[{"type":"tool_result","is_error":True,"content":"error"}]}},
            {"type":"user","isMeta":True,"timestamp":stamps[5],"message":{"content":"[tw:T10] TASK lead -> api T10"}},
            {"type":"assistant","timestamp":stamps[6],"message":{"content":[{"type":"tool_use","name":"retry","input":{}}]}},
        ]
        result,_=tw._signals_analyze(self.dir/".teamwork",self.state(),"api",rows,persist=False)
        self.assertFalse(result["observation"]["first_done_evidence"])
        self.assertEqual(result["observation"]["bypass"],0)

    def test_signal_filters_threshold_denied_fix_breaks_and_all_missing(self):
        self.tw("thread","add","api","--session","session_signal")
        self.tw("prefs","--set","signals_red_at=2")
        path=self.dir/"signal-rules.jsonl"
        rows=[
            {"type":"user","message":{"content":"不要用 --force"}},
            {"type":"assistant","message":{"content":[{"type":"text","text":"已修好但回归了；我会用 --force"}]}},
            {"type":"user","message":{"content":"[tw:proj] 不对，这只是 lead 消息"}},
            {"type":"user","message":{"content":"[screen SC2] 傻"}},
            {"type":"user","message":{"content":"<agent-message>wrong again</agent-message>"}},
            {"type":"user","message":{"content":"这不对外"}},
        ]
        path.write_text("\n".join(json.dumps(x,ensure_ascii=False) for x in rows),encoding="utf-8")
        result=json.loads(self.tw("signals","api","--transcript",str(path),"--json").stdout)
        self.assertEqual(result["counts"]["dissatisfaction"],0)
        self.assertEqual(result["counts"]["denied_again"],1)
        self.assertEqual(result["counts"]["fix_breaks"],1)
        self.assertEqual(result["color"],"red")
        all_rows=json.loads(self.tw("signals","--all","--json").stdout)
        self.assertIsNone(all_rows[0]["color"])

    def test_retake_screen_affects_stats_not_current_screen_judgement(self):
        self.tw("thread","add","api","--session","session_retake")
        self.tw("screen","ask","api","--session","session_retake")
        good=",".join(f"P{i}=2" for i in range(1,7)); bad=",".join(f"P{i}=0" for i in range(1,7))
        self.tw("screen","record","SC1","--scores",good)
        self.tw("screen","ask","api","--session","session_retake","--retake")
        self.tw("screen","record","SC2","--scores",bad)
        self.assertTrue(self.state()["screens"]["SC2"]["retake"])
        self.assertNotIn("未体检",self.tw("board").stdout)
        self.assertFalse(__import__("tw")._screen_failed(self.state(),"session_retake"))
        card=self.tw("screen","stats").stdout
        self.assertIn("同一张卡测两次",card); self.assertIn("12",card)

    def test_background_grade_patches_latest_ledger_after_task_add(self):
        self.tw("thread","add","ui","--session","session_parallel")
        self.tw("plan","approve","--user-said","approved")
        self.tw("task","add","T8","--title","existing work","--owner","ui")
        self.tw("task-msg","T8","--dispatch")
        config=self.dir/"grader-config"; project=config/"projects"/"test-project"; project.mkdir(parents=True)
        self.env["CLAUDE_CONFIG_DIR"]=str(config)
        (config/"bridge-transcript-cse_parallel.jsonl").write_text(json.dumps({"session_id":"local_parallel"})+"\n",encoding="utf-8")
        t=dt.datetime.now().astimezone().isoformat()
        (project/"local_parallel.jsonl").write_text("\n".join(json.dumps(x) for x in [
            {"type":"user","timestamp":t,"message":{"content":"[screen SC1]"}},
            {"type":"assistant","timestamp":t,"message":{"content":"先逐项核对后回答全部题目。"}},
        ])+"\n",encoding="utf-8")
        self.env["TW_GRADER_CMD"]="sleep 8; echo P1=2 P2=2 P3=2 P4=2 P5=2 P6=2"
        self.tw("prefs","--set","screen_autograde=true")
        started=time.monotonic(); ready=self.tw("ready")
        self.assertLess(time.monotonic()-started,8.0,ready.stderr)
        self.assertIn("已在后台给 1 个体检打分",ready.stderr)
        self.tw("task","add","T9","--title","during grading","--owner","ui")
        state=self.state()
        self.assertNotEqual(state["screens"]["SC1"].get("status"),"graded")
        deadline=time.time()+12
        while time.time()<deadline:
            state=self.state()
            if state["screens"]["SC1"].get("status")=="graded": break
            time.sleep(.05)
        self.assertEqual(state["screens"]["SC1"]["status"],"graded")
        self.assertIn("T9",state["tasks"])
        self.assertIn("体检：过",self.tw("board").stdout)

    def test_unified_row_roles_boundaries_and_user_only_dissatisfaction(self):
        tw=__import__("tw")
        peer={"type":"user","isMeta":True,"message":{"content":"<cross-session-message>[tw:T] TASK\n[screen SC1]\nwrong"}}
        complaint={"type":"user","message":{"content":"还是不对啊"}}
        self.assertEqual(tw._row_role(peer),"peer")
        self.assertEqual(tw._row_role(complaint),"user")
        self.assertEqual(tw._extract_screen_answer([peer,{"type":"assistant","message":{"content":"answer"}},complaint],"SC1"),"answer")
        self.assertTrue(tw._is_dissatisfied("还是不对")); self.assertTrue(tw._is_dissatisfied("结果不对啊"))
        for text in ("不对外","不对称","不对劲","run the tests again"):
            self.assertFalse(tw._is_dissatisfied(text),text)
        for text in ("wrong","wrong again","not again","how many times"):
            self.assertTrue(tw._is_dissatisfied(text),text)
        rows=[{"type":"user","timestamp":dt.datetime.now().astimezone().isoformat(),"message":{"content":"[tw:project] TASK"}},
              {"type":"assistant","timestamp":dt.datetime.now().astimezone().isoformat(),"message":{"content":[{"type":"tool_use","name":"read"}]}}]
        self.tw("thread","add","api","--session","session_tw_start")
        tw._signals_analyze(self.dir/".teamwork",self.state(),"api",rows,persist=False)
        self.assertEqual(tw._row_role(rows[0]),"peer")

    def test_signal_quiz_sticky_red_survives_recompute_and_correct_answer_clears(self):
        self.tw("thread","add","api","--session","session_sticky")
        self.tw("memory","--standing","汇报一律用中文写给用户")
        path=self.dir/"compact-sticky.jsonl"
        path.write_text(json.dumps({"type":"summary","isCompactSummary":True,"timestamp":dt.datetime.now().astimezone().isoformat(),"message":{"content":"summary"}}),encoding="utf-8")
        self.tw("signals","api","--transcript",str(path))
        quiz=self.state()["signals"]["api"]["half_quiz"]["id"]
        key=json.loads(next(self.keys.rglob(f"api-{quiz}.json")).read_text(encoding="utf-8"))
        wrong=self.dir/"wrong.json"; wrong.write_text("错答\n"*len(key["questions"]),encoding="utf-8")
        self.tw("signals","quiz",quiz,"api","--answers",str(wrong)); self.tw("signals","quiz",quiz,"api","--answers",str(wrong))
        self.assertEqual(self.state()["signals"]["api"]["sticky"][-1]["kind"],"quiz_red")
        recomputed=json.loads(self.tw("signals","api","--transcript",str(path),"--json").stdout)
        self.assertEqual(recomputed["color"],"red")
        correct=self.dir/"correct.json"
        correct.write_text(json.dumps([x["fact"] for x in key["questions"]],ensure_ascii=False),encoding="utf-8")
        self.tw("signals","quiz",quiz,"api","--answers",str(correct))
        self.assertFalse(self.state()["signals"]["api"].get("sticky"))

    def test_signals_missing_and_unknown_seat_do_not_write(self):
        self.tw("thread","add","api","--session","session_missing")
        before=(self.dir/".teamwork"/"state.json").read_bytes()
        result=self.tw("signals","api")
        self.assertIn("只算了台账",result.stdout)
        self.assertEqual((self.dir/".teamwork"/"state.json").read_bytes(),before)
        self.assertNotEqual(self.tw("signals","typo","--json",ok=False).returncode,0)
        self.assertNotIn("typo",self.state().get("signals",{}))

    def test_config_dir_glob_and_multi_transcript_merge(self):
        tw=__import__("tw"); home=Path(tempfile.mkdtemp())
        config=home/".claude-work"; project=config/"projects"/"p"; project.mkdir(parents=True)
        session="session_11111111-1111-4111-8111-111111111111"
        (config/f"bridge-transcript-cse_{session.removeprefix('session_')}.jsonl").write_text(json.dumps({"session_id":"one"})+"\n"+json.dumps({"session_id":"two"})+"\n",encoding="utf-8")
        (project/"one.jsonl").write_text("\n".join(json.dumps(row) for row in [
            {"type":"user","timestamp":"2026-11-24T10:00:00+00:00","message":{"content":"first"}},
            {"type":"user","message":{"content":"untimed first"}},
            {"type":"user","message":{"content":"untimed second"}},
        ])+"\n",encoding="utf-8")
        (project/"two.jsonl").write_text(json.dumps({"type":"user","timestamp":"2026-11-24T10:00:01+00:00","message":{"content":"second"}})+"\n",encoding="utf-8")
        from unittest.mock import patch
        with patch.dict(os.environ,{"HOME":str(home)},clear=False):
            self.assertIn(str(config),tw._config_dirs())
            rows,found=tw._session_rows(session)
        self.assertTrue(found); self.assertEqual([tw._row_text(x) for x in rows],["first","untimed first","untimed second","second"])

    def test_subagent_sidechain_does_not_replace_primary_screen_answer_or_signal(self):
        self.tw("thread","add","api","--session","session_agent_fixture")
        self.tw("screen","ask","api","--session","session_agent_fixture")
        config=self.dir/"agent-config"; project=config/"projects"/"fixture"; project.mkdir(parents=True)
        (config/"bridge-transcript-cse_agent_fixture.jsonl").write_text(json.dumps({"session_id":"local-fixture"})+"\n",encoding="utf-8")
        stamp=dt.datetime.now().astimezone().isoformat()
        (project/"local-fixture.jsonl").write_text(json.dumps({"type":"user","timestamp":stamp,"message":{"content":"[screen SC1]"}})+"\n"+
            json.dumps({"type":"assistant","timestamp":stamp,"message":{"content":"主记录完整作答全部题目，核对后给出最终结果。"}})+"\n",encoding="utf-8")
        sub=project/"local-fixture"/"subagents"; sub.mkdir(parents=True)
        (sub/"agent-fixture.jsonl").write_text(json.dumps({"type":"user","isSidechain":True,"timestamp":stamp,"message":{"content":"上次结果不对"}})+"\n",encoding="utf-8")
        self.env["CLAUDE_CONFIG_DIR"]=str(config)
        prompt=self.dir/"grader-input.txt"
        self.env["TW_GRADER_CMD"]=f'cat > "{prompt}"; echo P1=2 P2=2 P3=2 P4=2 P5=2 P6=2'
        self.tw("screen","grade","SC1")
        self.assertIn("主记录完整作答全部题目",prompt.read_text(encoding="utf-8"))
        signal=json.loads(self.tw("signals","api","--json").stdout)
        self.assertEqual(signal["counts"]["dissatisfaction"],0)

    def test_rebuild_restores_quiz_sticky_red_after_compaction_and_two_failures(self):
        self.tw("thread","add","api","--session","session_rebuild_sticky")
        self.tw("memory","--standing","汇报一律用中文写给用户")
        transcript=self.dir/"compact-rebuild.jsonl"
        transcript.write_text(json.dumps({"type":"summary","isCompactSummary":True,"uuid":"compact-rebuild",
            "timestamp":dt.datetime.now().astimezone().isoformat(),"message":{"content":"summary"}}),encoding="utf-8")
        self.tw("signals","api","--transcript",str(transcript))
        quiz=self.state()["signals"]["api"]["half_quiz"]["id"]
        key=json.loads(next(self.keys.rglob(f"api-{quiz}.json")).read_text(encoding="utf-8"))
        wrong=self.dir/"wrong-rebuild.txt"; wrong.write_text("错答\n"*len(key["questions"]),encoding="utf-8")
        self.tw("signals","quiz",quiz,"api","--answers",str(wrong)); self.tw("signals","quiz",quiz,"api","--answers",str(wrong))
        self.tw("rebuild","--apply","--force")
        rebuilt=self.state()["signals"]["api"]
        self.assertEqual(rebuilt["color"],"red")
        self.assertEqual(rebuilt["sticky"][-1]["kind"],"quiz_red")
        self.assertIn("quizzes",rebuilt)
        self.assertIn("api",self.state()["observations"])

    def test_row_role_reminders_interruptions_tool_results_and_dissatisfaction_edges(self):
        tw=__import__("tw")
        self.assertEqual(tw._row_role({"type":"user","message":{"content":[{"type":"tool_result","content":"ok"},{"type":"text","text":"<system-reminder>note</system-reminder>"}]}}),"tool_result")
        for text in ("<task-notification>done", "[Request interrupted by user: stop"):
            self.assertEqual(tw._row_role({"type":"user","message":{"content":text}}),"other")
        for text in ("傻瓜", "傻瓜式流程", "nothing wrong", "not wrong", "我说过的那个方案很好"):
            self.assertFalse(tw._is_dissatisfied(text),text)
        self.assertTrue(tw._is_dissatisfied("我说过这个"))

    def test_lead_relay_json_is_conditional_and_contains_bilingual_prompt(self):
        self.tw("lead","--session","session_relay")
        transcript=self.dir/"relay.jsonl"; transcript.write_text("",encoding="utf-8")
        result=json.loads(self.tw("signals","lead","--transcript",str(transcript),"--json").stdout)
        self.assertNotIn("lead_relay",result)
        st=self.state(); st["lead"]["sessions"][-1]["from"]=(dt.datetime.now().astimezone()-dt.timedelta(hours=13)).isoformat()
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st),encoding="utf-8")
        result=json.loads(self.tw("signals","lead","--transcript",str(transcript),"--json").stdout)
        self.assertIsInstance(result["lead_relay"],str)
        self.assertTrue(result["lead_relay"])

    def test_quota_uses_one_main_config_directory(self):
        from unittest.mock import patch
        import argparse, contextlib, io
        self.tw("thread","add","api","--session","session_quota_config")
        tw=__import__("tw")
        seen=[]
        class Reader:
            @staticmethod
            def read_quota(dirs,codex,current): seen.extend(dirs); return {"notes":[]}
            @staticmethod
            def tier(result,prefs,current): return "unknown",[]
            @staticmethod
            def num(value): return None
        with patch.object(tw,"quota_reader",Reader),patch.object(tw,"find_dir",return_value=self.dir/".teamwork"),patch.dict(os.environ,{"CLAUDE_CONFIG_DIR":str(self.dir/"only-config")},clear=False):
            (self.dir/"only-config").mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                tw.cmd_quota(argparse.Namespace(due=False,weekly=False,config_dir=None,codex_dir=None,json=True))
        self.assertEqual(seen,[str(self.dir/"only-config")])

    def test_patch_state_none_is_noop_and_conflict_retries_latest_state(self):
        from unittest.mock import patch
        tw=__import__("tw"); ledger=self.dir/".teamwork"
        before=self.state()["rev"]
        self.assertIsNone(tw._patch_state(ledger,"test_value",lambda current,latest:None,{"ev":"test-noop"}))
        self.assertEqual(self.state()["rev"],before)
        real=tw._save_locked; calls={"n":0}
        def conflict_once(d,st,event=None):
            calls["n"]+=1
            if calls["n"]==1:
                disk=json.loads((d/"state.json").read_text(encoding="utf-8")); disk["rev"]+=1
                (d/"state.json").write_text(json.dumps(disk),encoding="utf-8")
                raise SystemExit("ledger changed")
            return real(d,st,event)
        with patch.object(tw,"_save_locked",side_effect=conflict_once):
            result=tw._patch_state(ledger,"test_value",lambda current,latest:{"ok":True},{"ev":"test-retry"})
        self.assertTrue(result); self.assertEqual(self.state()["test_value"],{"ok":True}); self.assertEqual(calls["n"],2)

    def test_screen_show_reprints_questions_without_answer_key(self):
        self.tw("thread","add","api","--session","session_screen_show")
        self.tw("screen","ask","api","--session","session_screen_show")
        prompt=self.tw("screen","show","SC1").stdout
        self.assertTrue(prompt.startswith("[screen SC1]\n"))  # the record lookup starts at this marker
        key=json.loads(next(self.keys.rglob("SC1.json")).read_text(encoding="utf-8"))
        for item,question in key["questions"].items(): self.assertIn(f"{item}: {question}",prompt)
        self.assertNotIn(next(iter(key["answers"].values())),prompt)

    def test_grade_race_returns_already_graded_without_score_output(self):
        from unittest.mock import patch
        self.tw("thread","add","api","--session","session_grade_race")
        self.tw("screen","ask","api","--session","session_grade_race")
        answers=self.dir/"race-answer.txt"; answers.write_text("逐题完整回答。",encoding="utf-8")
        self.env["TW_GRADER_CMD"]="echo P1=2 P2=2 P3=2 P4=2 P5=2 P6=2"
        tw=__import__("tw")
        with patch.dict(os.environ,{"TW_GRADER_CMD":self.env["TW_GRADER_CMD"],"TW_KEYS_DIR":str(self.keys)}),patch.object(tw,"_patch_state",return_value=None):
            ok,result=tw._screen_grade(self.dir/".teamwork",self.state(),"SC1",answers.read_text(),"answers")
        self.assertFalse(ok); self.assertEqual(result,"already graded")

    def test_automatic_grade_applies_lead_and_nonlead_notices_and_sweep_ready_hint(self):
        self.tw("thread","add","api","--session","session_auto_notice")
        self.tw("screen","ask","api","--session","session_auto_notice")
        answers=self.dir/"notice-answer.txt"; answers.write_text("逐题作答。",encoding="utf-8")
        self.env["TW_GRADER_CMD"]="echo P1=0 P2=0 P3=0 P4=0 P5=0 P6=0"
        failed=self.tw("screen","grade","SC1","--answers",str(answers),ok=True)
        self.assertIn("api",failed.stderr)
        tw=__import__("tw")
        tw._screen_grade_notice(self.dir/".teamwork",self.state(),"SC1",self.state()["screens"]["SC1"],background=True)
        ready=self.tw("ready")
        self.assertIn("api",ready.stderr)
        self.assertNotIn("api",self.tw("ready").stderr)
        self.tw("lead","--session","session_auto_lead")
        self.tw("opening","--json")
        st=self.state(); st["lead_trials"]=[{"screen":"SC0"}]; st["lead_first_message"]="retained first request"
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st),encoding="utf-8")
        self.env["TW_GRADER_CMD"]="echo P1=2 P2=2 P3=2 P4=2 P5=2 P6=2 P7=2"
        grade=self.tw("screen","grade","SC2","--answers",str(answers))
        self.assertEqual(self.state()["lead_trials"],[])
        self.assertNotIn("lead_first_message",self.state())
        self.assertEqual(grade.stderr,"")

    def test_screen_cli_grader_text_via_and_stats_asked_bucket(self):
        self.tw("thread","add","api","--session","session_cli")
        self.tw("screen","ask","api","--session","session_cli")
        answers=self.dir/"answers-cli"; answers.write_text("逐题核对后回答。",encoding="utf-8")
        self.env["TW_GRADER_CMD"]="printf 'P1=2 P2=2 P3=2 P4=2 P5=2 P6=2\\n'"
        self.tw("screen","grade","SC1","--answers",str(answers),"--via","auto")
        self.assertEqual(self.state()["screens"]["SC1"]["grader"],"cmd")
        self.assertIn("未打分 0",self.tw("screen","stats").stdout)
        self.tw("screen","ask","api","--session","session_cli","--retake")
        self.tw("screen","record","SC2","--scores",",".join(f"P{i}=2" for i in range(1,7)),"--grader","gpt-6-luna")
        self.assertEqual(self.state()["screens"]["SC2"]["grader"],"gpt-6-luna")
        self.tw("screen","ask","api","--session","session_cli","--retake")
        self.assertIn("未打分 1",self.tw("screen","stats").stdout)

    def test_ready_reports_cloud_screen_without_starting_sweep(self):
        self.tw("thread","add","api","--session","session_cloud")
        self.tw("screen","ask","api","--session","session_cloud")
        self.tw("prefs","--set","screen_autograde=true")
        ready=self.tw("ready")
        self.assertIn("本机找不到 api 的对话记录",ready.stderr)
        self.assertIn("TW screen grade SC1 --events <list_events 结果文件>",ready.stderr)
        self.assertNotIn("已在后台给",ready.stderr)

    def test_sweep_reports_scored_failed_unanswered_and_no_record_separately(self):
        self.tw("thread","add","api","--session","session_sweep_unanswered")
        self.tw("screen","ask","api","--session","session_sweep_unanswered")
        self.tw("thread","add","ui","--session","session_sweep_failed")
        self.tw("screen","ask","ui","--session","session_sweep_failed")
        self.tw("thread","add","test","--session","session_sweep_cloud")
        self.tw("screen","ask","test","--session","session_sweep_cloud")
        config=self.dir/"sweep-config"; project=config/"projects"/"test-project"; project.mkdir(parents=True)
        self.env["CLAUDE_CONFIG_DIR"]=str(config)
        for session,local in (("session_sweep_unanswered","local_unanswered"),("session_sweep_failed","local_failed")):
            (config/f"bridge-transcript-cse_{session.removeprefix('session_')}.jsonl").write_text(json.dumps({"session_id":local})+"\n",encoding="utf-8")
        stamp=dt.datetime.now().astimezone().isoformat()
        (project/"local_unanswered.jsonl").write_text(json.dumps({"type":"user","timestamp":stamp,"message":{"content":"no screen answer"}})+"\n",encoding="utf-8")
        (project/"local_failed.jsonl").write_text("\n".join(json.dumps(row,ensure_ascii=False) for row in [
            {"type":"user","timestamp":stamp,"message":{"content":"[screen SC2]"}},
            {"type":"assistant","timestamp":stamp,"message":{"content":"逐题仔细作答。"}},
        ])+"\n",encoding="utf-8")
        self.env["TW_GRADER_CMD"]="exit 2"
        result=json.loads(self.tw("screen","sweep","--json").stdout)
        self.assertEqual(result["scored"],0)
        self.assertEqual([item["screen"] for item in result["failed"]],["SC2"])
        self.assertEqual(result["unanswered"],["SC1"])
        self.assertEqual([item["screen"] for item in result["no_record"]],["SC3"])

    def test_signal_text_bad_lines_lead_key_and_session_helper(self):
        tw=__import__("tw"); self.tw("thread","add","api","--session","session_signal_text")
        st=self.state(); st["lead"]["session_id"]="session_signal_lead"
        (self.dir/".teamwork"/"state.json").write_text(json.dumps(st),encoding="utf-8")
        self.assertEqual(tw._seat_session(st,"api"),"session_signal_text")
        self.assertEqual(tw._seat_session(st,"lead"),"session_signal_lead")
        path=self.dir/"signal-text.jsonl"
        path.write_text(json.dumps({"type":"user","timestamp":dt.datetime.now().astimezone().isoformat(),"message":{"content":"傻"}},ensure_ascii=False)+"\nnot-json\n",encoding="utf-8")
        result=json.loads(self.tw("signals","lead","--transcript",str(path),"--json").stdout)
        self.assertNotIn("lead_relay",result)
        self.assertEqual(result["bad_lines"],1)
        text=self.tw("signals","lead","--transcript",str(path)).stdout
        self.assertIn("今天这个席位已接力 0 次，上限 2",text)
        self.assertIn("跳过 1 行坏记录",text)

    def test_failed_grade_why_is_redacted_home_shortened_and_bounded(self):
        self.tw("thread","add","api","--session","session_why")
        self.tw("screen","ask","api","--session","session_why")
        answers=self.dir/"why-answer.txt"; answers.write_text("逐题作答。",encoding="utf-8")
        self.env["TW_GRADER_CMD"]='python3 -c "import pathlib; print(\'failed at \'+str(pathlib.Path.home())+\' \'+\'x\'*260); raise SystemExit(2)"'
        self.tw("screen","grade","SC1","--answers",str(answers),ok=False)
        why=self.state()["screens"]["SC1"]["why"]
        self.assertIn("~",why)
        self.assertNotIn(str(Path.home()),why)
        self.assertLessEqual(len(why),200)

class T81ServedFolderCompact(unittest.TestCase):
    def setUp(self):
        self.tmp=Path(tempfile.mkdtemp(prefix="tw-t81-")); self.oldcwd=Path.cwd()
        self.served=self.tmp/"served"; self.served.mkdir(); self.project=self.served/"proj"; self.project.mkdir()
        self.config=self.tmp/"cfg"; self.config.mkdir(); self.keys=self.tmp/"keys"; self.keys.mkdir()
        self.env={k:v for k,v in os.environ.items() if k not in ("TEAMWORK_DIR","CLAUDE_CONFIG_DIR")}
        self.env.update(TW_KEYS_DIR=str(self.keys),CLAUDE_CONFIG_DIR=str(self.config),TW_NOW=dt.datetime.now().astimezone().isoformat(timespec="seconds"))
        self.cli("init","demo")
        # Global -C precedes the subcommand.
        self.cli("prefs","--set","language=zh")
        os.chdir(self.served)
    def tearDown(self): os.chdir(self.oldcwd); shutil.rmtree(self.tmp)
    def cli(self,*args):
        cmd=[sys.executable,str(TW)]
        if args[0] not in ("-C",): cmd += ["-C",str(self.project)]
        p=subprocess.run(cmd+list(map(str,args)),cwd=self.served,env=self.env,text=True,capture_output=True)
        if p.returncode: self.fail(p.stderr or p.stdout)
        return p
    def state(self): return json.loads((self.project/".teamwork"/"state.json").read_text(encoding="utf-8"))
    def save(self,st): (self.project/".teamwork"/"state.json").write_text(json.dumps(st,ensure_ascii=False),encoding="utf-8")
    def sid(self): return str(uuid.uuid4())
    def hook(self,session,source="compact"):
        old=os.environ.copy(); os.environ.update(self.env)
        oldstdin=sys.stdin; out=io.StringIO(); r,w=os.pipe(); os.write(w,json.dumps({"session_id":session,"source":source,"cwd":str(self.served)}).encode()); os.close(w); sys.stdin=os.fdopen(r,"r")
        try:
            with contextlib.redirect_stdout(out): tw.cmd_context(None)
        finally: sys.stdin.close(); sys.stdin=oldstdin; os.environ.clear(); os.environ.update(old)
        return json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]
    def bridge(self,remote,local):
        path=self.config/f"bridge-transcript-cse_{remote.removeprefix('session_')}.jsonl"
        path.write_text(json.dumps({"type":"system","session_id":local,"uuid":"fixture"})+"\n"+
                        json.dumps({"type":"control_request","uuid":"fixture-2"})+"\n"+
                        json.dumps({"type":"assistant","session_id":local,"uuid":"fixture-3"})+"\n",encoding="utf-8")

    def test_00_served_folder_compact_emits_card_quiz_and_records_local_session(self):
        local=self.sid(); remote="session_rc-fixture"; self.cli("lead","--session",remote); self.bridge(remote,local)
        text=self.hook(local)
        self.assertTrue(text.startswith("回复用户一律用中文。\n你是 demo（demo）的 lead，会话 session_rc-fixture"),text[:300])
        self.assertIn("1. 你该用什么语言回复用户？",text)
        self.assertIn("teamwork projects below this folder:",text)
        self.assertEqual(self.state()["compactions"]["lead"]["session"],local)

    def test_unmatched_served_folder_output_is_unchanged(self):
        text=self.hook(self.sid())
        self.assertEqual(text,"teamwork projects below this folder: demo ("+str(self.project)+"). Only use one if the user asks; cd into its folder first (its own board then applies).")

    def test_thread_bridge_match_emits_thread_card(self):
        local=self.sid(); remote="session_thread-fixture"
        self.cli("thread","add","api","--session",remote); self.bridge(remote,local)
        text=self.hook(local)
        self.assertTrue(text.startswith("回复用户一律用中文。\n你是 demo 的线程 api，lead 会话"),text[:300])
        self.assertIn("只向 lead 汇报",text)

    def test_resume_pending_reprints_without_bridge_but_answered_does_not(self):
        local=self.sid(); self.cli("lead","--session",local); self.hook(local)
        from unittest.mock import patch
        with patch.object(tw,"_bridge_local_ids",side_effect=AssertionError("bridge read")) as reader:
            self.assertTrue(self.hook(local,"resume").startswith("回复用户一律用中文")); reader.assert_not_called()
        st=self.state(); st["signals"]["lead"]["compact_check"]["answered"]=True; self.save(st)
        with patch.object(tw,"_bridge_local_ids",side_effect=AssertionError("bridge read")) as reader:
            text=self.hook(local,"resume"); reader.assert_not_called()
        self.assertFalse(text.startswith("回复用户一律用中文"))

    def test_standing_card_caps_at_fifteen_lines(self):
        st=self.state(); st["standing"]=["rule-"+str(i) for i in range(30)]
        card=tw._brief_card(st,"lead")
        self.assertLessEqual(len(card.splitlines()),15); self.assertIn("还有 21 条：tw.py memory --standing-list",card)

    def test_english_project_card_and_quiz_keyword(self):
        self.cli("prefs","--set","language=en"); local=self.sid(); self.cli("lead","--session",local)
        text=self.hook(local)
        self.assertTrue(text.startswith("Reply to the user in English."))
        key=json.loads((self.keys/"demo"/"lead-HQ1.json").read_text(encoding="utf-8"))
        self.assertEqual(key["questions"][0]["keywords"],["English"])

    def test_numbered_chinese_answer_passes_language_question(self):
        local=self.sid(); self.cli("lead","--session",local); self.hook(local)
        from unittest.mock import patch
        questions=json.loads((self.keys/"demo"/"lead-HQ1.json").read_text(encoding="utf-8"))["questions"]
        response="[quiz HQ1]\n1. 中文\n"+"\n".join(f"{i+1}. "+" ".join(q["keywords"]) for i,q in enumerate(questions[1:],start=1))
        with patch.dict(os.environ,self.env):
            passed,wrong=tw._signals_quiz_grade_text(self.project/".teamwork",self.state(),"HQ1","lead",response)
        self.assertTrue(passed,wrong)

    def test_bridge_collision_is_ambiguous_and_direct_match_wins(self):
        local=self.sid(); remote="session_shared-rc"; self.cli("lead","--session",remote); self.bridge(remote,local)
        other=self.served/"other"; other.mkdir()
        p=subprocess.run([sys.executable,str(TW),"-C",str(other),"init","other"],cwd=self.served,env=self.env,text=True,capture_output=True)
        self.assertEqual(p.returncode,0,p.stderr)
        p=subprocess.run([sys.executable,str(TW),"-C",str(other),"lead","--session",remote],cwd=self.served,env=self.env,text=True,capture_output=True)
        self.assertEqual(p.returncode,0,p.stderr)
        bridge=self.config/f"bridge-transcript-cse_{remote.removeprefix('session_')}.jsonl"
        bridge.write_text(json.dumps({"session_id":local})+"\n",encoding="utf-8")
        text=self.hook(local)
        self.assertIn("compact_ambiguous",text); self.assertNotIn("刚被压缩",text)
        self.assertNotIn("compactions",self.state()); self.assertNotIn("compactions",json.loads((other/".teamwork"/"state.json").read_text()))
        # Direct local-id match takes precedence over another project's bridge match.
        self.cli("lead","--session",local,"--force")
        text=self.hook(local)
        self.assertIn("你是 demo",text); self.assertNotIn("compact_ambiguous",text)

    def test_bridge_lookup_cached_once_and_skipped_for_direct_match(self):
        from unittest.mock import patch
        local=self.sid(); remote="session_cached-rc"; self.cli("lead","--session",remote); self.cli("thread","add","api","--session","session_api-rc")
        st=self.state(); st["threads"]["api"]["session_id"]=remote; self.save(st); self.bridge(remote,local)
        with patch.object(tw,"_bridge_local_ids",wraps=tw._bridge_local_ids) as reader:
            self.hook(local); self.assertEqual(reader.call_count,1)
        self.cli("lead","--session",local,"--force")
        with patch.object(tw,"_bridge_local_ids",side_effect=AssertionError("bridge read")) as reader:
            self.hook(local); reader.assert_not_called()

    def test_resume_uses_compaction_local_session_even_when_ledger_is_rc(self):
        local=self.sid(); remote="session_resume-rc"; self.cli("lead","--session",remote)
        st=self.state(); st.setdefault("compactions",{})["lead"]={"session":local,"count":1}
        st.setdefault("signals",{}).setdefault("lead",{})["compact_check"]={"answered":False,"quiz":"HQ1"}
        self.save(st)
        self.assertTrue(self.hook(local,"resume").startswith("回复用户一律用中文"))
        st=self.state(); st["signals"]["lead"]["compact_check"]["answered"]=True; self.save(st)
        self.assertFalse(self.hook(local,"resume").startswith("回复用户一律用中文"))

    def test_bad_project_state_is_skipped_and_quiz_failure_keeps_card(self):
        bad=self.served/"bad"; (bad/".teamwork").mkdir(parents=True); (bad/".teamwork"/"state.json").write_text("{")
        local=self.sid(); self.cli("lead","--session",local)
        with mock.patch.object(tw,"_after_compact",side_effect=OSError("keys unavailable")):
            text=self.hook(local)
        self.assertIn("compact_quiz_unavailable",text); self.assertIn("回复用户一律用中文",text); self.assertIn("bad (",text)

    def test_standing_newlines_are_flattened_before_card_line_limit(self):
        st=self.state(); st["standing"]=["first\nsecond\rthird"]+["rule-"+str(i) for i in range(30)]
        card=tw._brief_card(st,"lead")
        self.assertLessEqual(len(card.splitlines()),15); self.assertIn("first second third",card)


if __name__ == "__main__":
    unittest.main()
