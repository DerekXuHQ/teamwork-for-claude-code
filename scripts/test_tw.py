#!/usr/bin/env python3
"""Tests for tw.py. Stdlib only; every test works in a temp project folder.

  python3 scripts/test_tw.py
"""

import datetime as dt
import contextlib
import io
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest import mock
import zipfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts/: quota.py next to tw.py
import quota  # noqa: E402
import tw  # noqa: E402

TW = Path(os.environ.get("TW_TEST_SCRIPT", Path(__file__).resolve().parent / "tw.py"))
RELEASE = Path(__file__).resolve().parent / "release.py"
# bash-guard logs its own errors inside each test's temporary project.
WALK = ["planned", "approved", "dispatched", "working", "review", "done"]


def quiz_transcript(path, item, answer):
    stamp=dt.datetime.now().astimezone().isoformat(timespec="seconds")
    rows=[{"type":"user","timestamp":stamp,"origin":{"kind":"human"},"message":{"role":"user","content":item["question"]}},
          {"type":"user","timestamp":stamp,"origin":{"kind":"human"},"message":{"role":"user","content":answer}}]
    Path(path).write_text("\n".join(json.dumps(x,ensure_ascii=False) for x in rows)+"\n",encoding="utf-8")


def walk_args(run, args, state_file, prepare_primary=False):
    """Tests that only care about the end status: step a task through the legal path first (T20 enforces it).
    run(*args) must run tw.py; the final call is left to the caller."""
    if len(args) < 3 or args[0] != "task" or args[1] != "set" or "--status" not in args:
        return
    target = args[args.index("--status") + 1]
    if target == "blocked":
        target_path = WALK[:WALK.index("working") + 1]
    elif target in WALK:
        target_path = WALK[:WALK.index(target)]
    else:
        return
    st = json.loads(Path(state_file).read_text(encoding="utf-8"))
    cur = st["tasks"].get(args[2], {}).get("status")
    if cur not in WALK:
        return
    task = st["tasks"].get(args[2], {})
    if prepare_primary and target in ("review", "done") and st.get("prefs", {}).get("require_primary", True):
        if not task.get("primary"):
            run("task", "set", args[2], "--primary", "The user can see all 12 restored records")
        if (task.get("primary_check") or {}).get("result") not in ("seen", "pending-real"):
            proof = Path(state_file).parent.parent / "primary-proof.txt"
            proof.write_text("real artifact: 12 records", encoding="utf-8")
            run("task", "verify", args[2], "--seen", "--how", "Open the real project artifact", "--evidence", str(proof))
    for step in target_path[WALK.index(cur) + 1:]:
        if step == "approved":
            run("plan", "approve", "--user-said", "ok")  # tasks are approved with the plan (T20)
        else:
            run("task", "set", args[2], "--status", step)


class T80Compact(unittest.TestCase):
    def setUp(self):
        self.tmp=Path(tempfile.mkdtemp(prefix="tw-t80-")); self.oldcwd=Path.cwd()
        self.project=self.tmp/"project"; self.project.mkdir(); self.keys=self.tmp/"keys"; self.keys.mkdir()
        self.config=self.tmp/"claude"; self.config.mkdir()
        self.env={k:v for k,v in os.environ.items() if k != "TEAMWORK_DIR"}
        self.env.update(TW_KEYS_DIR=str(self.keys),CLAUDE_CONFIG_DIR=str(self.config),TW_NOW=dt.datetime.now().astimezone().isoformat(timespec="seconds"))
        os.chdir(self.project); self.cli("init","demo","--goal","test project"); self.cli("prefs","--set","language=zh")
    def tearDown(self): os.chdir(self.oldcwd); shutil.rmtree(self.tmp)
    def cli(self,*args,ok=True):
        p=subprocess.run([sys.executable,str(TW),*map(str,args)],cwd=self.project,env=self.env,text=True,capture_output=True)
        if ok and p.returncode: self.fail(p.stderr or p.stdout)
        if not ok and p.returncode==0: self.fail("expected failure")
        return p
    def state(self): return json.loads((self.project/".teamwork"/"state.json").read_text())
    def sid(self): return str(uuid.uuid4())
    def hook(self,session,source="compact"):
        import contextlib,io
        old=os.environ.copy(); os.environ.update(self.env)
        oldstdin=sys.stdin; output=io.StringIO(); readfd,writefd=os.pipe(); os.write(writefd,json.dumps({"session_id":session,"source":source,"hook_event_name":"SessionStart"}).encode()); os.close(writefd); sys.stdin=os.fdopen(readfd,"r")
        try:
            with contextlib.redirect_stdout(output): tw.cmd_context(None)
        finally: sys.stdin.close(); sys.stdin=oldstdin; os.environ.clear(); os.environ.update(old)
        return json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    def record(self,session,rows):
        folder=self.config/"projects"/"-tmp-project"; folder.mkdir(parents=True,exist_ok=True)
        path=folder/f"{session}.jsonl"; path.write_text("\n".join(json.dumps(x,ensure_ascii=False) for x in rows)+"\n",encoding="utf-8"); return path

    def test_00_compact_hook_quiz_sweep_and_board(self):
        session=self.sid(); self.cli("lead","--session",session)
        context=self.hook(session)
        self.assertTrue(context.startswith("刚被压缩（第 1 次）")); self.assertIn("[quiz HQ1]",context); self.assertIn("1.",context)
        question,answer=tw.SCREEN_BANK["zh"]["P7"][0]; base=dt.datetime.fromisoformat(self.env["TW_NOW"])
        stamps=[(base+dt.timedelta(seconds=i)).isoformat() for i in (-3,1,2)]
        self.record(session,[
            {"type":"user","isCompactSummary":True,"timestamp":stamps[0],"message":{"role":"user","content":"summary"}},
            {"type":"assistant","timestamp":stamps[1],"message":{"role":"assistant","content":[{"type":"tool_use","id":"read-ok","name":"Read","input":{"file_path":str(Path(tw.__file__).resolve().parents[1]/"SKILL.md")}}]}},
            {"type":"user","timestamp":stamps[1],"message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"read-ok","content":"rules"}]}},
            {"type":"assistant","timestamp":stamps[2],"message":{"role":"assistant","content":[{"type":"text","text":"[quiz HQ1]\n"+answer}]}}
        ])
        self.assertTrue(self.hook(session,"resume").startswith("刚被压缩（第 1 次）"))
        result=json.loads(self.cli("screen","sweep","--json").stdout)
        self.assertTrue(result["compact_checks"][0]["passed"],result); self.assertTrue(result["compact_checks"][0]["reread"],result)
        board=self.cli("board").stdout
        self.assertIn("压缩 1 次",board); self.assertIn("过",board); self.assertIn("重读规则：是",board)
        self.assertFalse(self.hook(session,"resume").startswith("刚被压缩"))

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
        self.assertIn("已压缩 2 次",text); self.assertIn("自己开 agent 1 次",text)
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
        self.assertTrue(text.startswith("刚被压缩"))
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
        self.assertIn("跑偏信号：记录不在本机，跑偏信号未算",emitted)
    def test_write_lock_still_emits_without_ledger_mutation(self):
        session=self.sid(); self.cli("lead","--session",session)
        st=self.state(); st["write_lock"]={"ho":"HO1"}; (self.project/".teamwork"/"state.json").write_text(json.dumps(st),encoding="utf-8")
        text=self.hook(session); self.assertTrue(text.startswith("刚被压缩")); self.assertNotIn("lead",self.state().get("compactions",{}))
        self.assertIn("重读 Rules 一节",text); self.assertNotIn("[quiz HQ",text); self.assertEqual(sum(line.startswith("1.") for line in text.splitlines()),1); self.assertNotIn("原始意图",text)


class TwCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.env = {k: v for k, v in os.environ.items() if k != "TEAMWORK_DIR"}
        guard_dir = self.dir / "guard-log"
        guard_dir.mkdir()
        self.env["TW_GUARD_LOG"] = str(guard_dir / "guard-errors.jsonl")
        self.tw("init", "demo", "--goal", "test project")
        # Legacy suites here cover non-R71 behavior; PrimaryOutcome owns the enabled-gate coverage.
        self.tw("prefs", "--set", "require_primary=false", "--user-said", "legacy fixture covers other task behavior")

    def tearDown(self):
        self._tmp.cleanup()

    def tw(self, *args, ok=True, walk=True):
        # Existing suites predate R71. Prepare the primary artifact before moving
        # a legacy task into review, and supply the required independent recheck
        # metadata when those suites record a passing verdict.
        if len(args) >= 3 and args[0:2] == ("task", "set"):
            tid = args[2]
            wants_review = ("--status" in args and args[args.index("--status") + 1] in ("review", "done"))
            passing_review = ("--review" in args and args[args.index("--review") + 1] in ("pass", "capped"))
            if (wants_review or passing_review) and (self.dir / ".teamwork" / "state.json").exists():
                self.verify_primary(tid)
            if "--review" in args:
                defaults = [("--tests", "ran"), ("--base", "0000000")]
                if passing_review:
                    defaults += [("--recheck-how", "independent test run"),
                                 ("--recheck-evidence", "Primary output shows 12 records")]
                if "--tests" in args and args[args.index("--tests") + 1] == "not-run":
                    defaults.append(("--tests-why", "legacy fixture did not run tests"))
                for flag, value in defaults:
                    if flag not in args:
                        args = (*args, flag, value)
        if len(args) >= 2 and args[0:1] == ("task-msg",) and "--dispatch" in args:
            tid = args[1]
            if (self.dir / ".teamwork" / "state.json").exists() and not self.state()["tasks"].get(tid, {}).get("primary"):
                self.tw("task", "set", tid, "--primary", "The user can see all 12 restored records")
        if len(args) >= 3 and args[:2] == ("succ","pack") and "--transcript" in args or len(args) >= 3 and args[:2] == ("succ","pack") and "--from-transcript" in args:
            handover=self.state().get("handovers",{}).get(args[2],{})
            target=handover.get("old_session")
            transcript_flag="--transcript" if "--transcript" in args else "--from-transcript"
            transcript_paths=[args[i+1] for i,x in enumerate(args[:-1]) if x==transcript_flag]
            action="pack"
        elif len(args) >= 3 and args[:2] == ("succ","grade") and ("--from-transcript" in args or "--transcript" in args):
            handover=self.state().get("handovers",{}).get(args[2],{})
            target=handover.get("quiz",{}).get("candidate") or next((x.get("session_id") for x in handover.get("candidates",[]) if not x.get("void")),None)
            transcript_paths=[args[i+1] for i,x in enumerate(args[:-1]) if x in ("--from-transcript","--transcript")]
            action="grade"
        elif len(args) >= 3 and args[:2] == ("succ","retire-check"):
            handover=self.state().get("handovers",{}).get(args[2],{})
            target=handover.get("old_session")
            transcript_paths=[args[i+1] for i,x in enumerate(args[:-1]) if x=="--transcript"]
            caller=args[args.index("--caller")+1] if "--caller" in args else None
            caller_paths=[(args[i+1],caller) for i,x in enumerate(args[:-1]) if x=="--from-transcript"]
            action="retire-check"
        else:
            target=None; transcript_paths=[]; caller_paths=[]; action=None
        if action!="retire-check": caller_paths=[]
        if target and transcript_paths:
            config=Path(args[args.index("--config-dir")+1]) if "--config-dir" in args else self.dir.parent/(self.dir.name+"-claude-test-config")  # outside the repo: a test helper must not dirty the frozen worktree
            config.mkdir(exist_ok=True)
            bridge_rows=[]
            fixture_old=Path(__file__).parent/"fixtures"/"t71_old_thread.jsonl"
            fixture_candidate=Path(__file__).parent/"fixtures"/"t71_real_candidate.jsonl"
            for path in transcript_paths:
                p=Path(path)
                permitted=p.is_relative_to(self.dir) or p==fixture_old or (action=="grade" and p==fixture_candidate)
                if not permitted or p.suffix.lower()==".json" or not p.is_file(): continue
                try:
                    parsed=[json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
                    for row in parsed:
                        owner=row.get("sessionId") or row.get("session_id")
                        if not owner and p.is_relative_to(self.dir):
                            owner="00000000-0000-4000-8000-000000000001"
                            row["sessionId"]=owner
                        if isinstance(owner,str) and re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}",owner.strip()):
                            bridge_rows.append({"session_id":owner.strip()})
                    if p.is_relative_to(self.dir) and parsed:
                        p.write_text("\n".join(json.dumps(row,ensure_ascii=False) for row in parsed)+"\n",encoding="utf-8")
                except (OSError,ValueError): pass
            for path,owner_target in caller_paths:
                p=Path(path)
                if not owner_target or not p.is_relative_to(self.dir) or p.suffix.lower()==".json" or not p.is_file(): continue
                try:
                    parsed=[json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
                    for row in parsed:
                        owner=row.get("sessionId") or row.get("session_id")
                        if not owner:
                            owner="00000000-0000-4000-8000-000000000002"
                            row["sessionId"]=owner
                        if isinstance(owner,str) and re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}",owner.strip()):
                            caller_bridge=config/f"bridge-transcript-cse_{str(owner_target).removeprefix('session_')}.jsonl"
                            with caller_bridge.open("a",encoding="utf-8") as stream: stream.write(json.dumps({"session_id":owner.strip()})+"\n")
                    p.write_text("\n".join(json.dumps(row,ensure_ascii=False) for row in parsed)+"\n",encoding="utf-8")
                except (OSError,ValueError): pass
            if bridge_rows:
                suffix=str(target).removeprefix("session_")
                bridge=config/f"bridge-transcript-cse_{suffix}.jsonl"
                bridge.write_text("\n".join(json.dumps(row) for row in bridge_rows)+"\n",encoding="utf-8")
                if "--config-dir" not in args:
                    args=(*args,"--config-dir",str(config))
        if walk:
            walk_args(lambda *x: self.tw(*x, walk=False), args, self.dir / ".teamwork" / "state.json", prepare_primary=True)
        r = subprocess.run([sys.executable, str(TW), *args], cwd=self.dir, env=self.env,
                           capture_output=True, text=True)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
        return r

    def state(self):
        return json.loads((self.dir / ".teamwork" / "state.json").read_text(encoding="utf-8"))

    def verify_primary(self, tid):
        task = self.state()["tasks"].get(tid)
        if not task:
            return
        if not task.get("primary"):
            self.tw("task", "set", tid, "--primary", "The user can see all 12 restored records")
        if (task.get("primary_check") or {}).get("result") not in ("seen", "pending-real"):
            proof = self.dir / "primary-proof.txt"
            proof.write_text("A real artifact containing 12 records", encoding="utf-8")
            self.tw("task", "verify", tid, "--seen", "--how", "Run the project artifact", "--evidence",
                    str(proof))

    def context(self):
        out = self.tw("context").stdout
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]


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
        doc=Path(__file__).resolve().parents[1]/"templates"/"worker-protocol.md"
        protocol=doc.read_text(encoding="utf-8")
        self.assertIn("Do not run `TW ask`",protocol)
        self.assertIn("TW ask --from <thread> --default",protocol)
        skill=(Path(__file__).resolve().parents[1]/"SKILL.md").read_text(encoding="utf-8")
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
        self.tw("feature", "set", "F1", "--status", "landed", "--evidence", "merged")
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
        msg=self.dir/"m.txt"; msg.write_text(f"[tw:{self.state()['prefs']['short_name']}] TASK lead -> api T1\nGoal: repair\nDone when: pass\n")
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
        p=self.dir/"msg.txt"; p.write_text(f"[tw:{self.state()['prefs']['short_name']}] TASK lead -> api T1\nGoal: repair\nDone when: pass\n")
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
    def tw(self, *args, ok=True, walk=True):
        if len(args) >= 3 and args[0:2] == ("task", "set"):
            tid = args[2]
            wants_primary = "--status" in args and args[args.index("--status") + 1] in ("review", "done")
            passing_review = "--review" in args and args[args.index("--review") + 1] in ("pass", "capped")
            if wants_primary or passing_review:
                self.verify_primary(tid)
            if "--review" in args:
                for flag, value in (("--tests", "ran"), ("--base", "0000000"),
                                    ("--recheck-how", "independent test run"),
                                    ("--recheck-evidence", "Primary output shows 12 records")):
                    if flag not in args:
                        args = (*args, flag, value)
        if walk:
            walk_args(lambda *x: self.tw(*x, walk=False), args, self.dir / ".teamwork" / "state.json", prepare_primary=True)
        r = subprocess.run([sys.executable, str(TW), *args], cwd=self.dir, env=self.env,
                           capture_output=True, text=True)
        evidence = os.environ.get("TW_SMOKE_EVIDENCE")
        if evidence:
            with open(evidence, "a", encoding="utf-8") as f:
                f.write(f"$ tw {' '.join(args)}\nexit={r.returncode}\nstdout:\n{r.stdout}stderr:\n{r.stderr}\n\n")
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

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
        try:
            pr.wait(timeout=5)  # the output is small: it fits the pipe without being read
        finally:
            pr.stdin.close()
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


class Guard(unittest.TestCase):
    """T16: private words/paths never leave the machine in commits."""

    SECRET = "Zorblax"  # stands in for a private product name; deliberately made up

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.remote = root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        self.proj = root / "proj"
        self.proj.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.proj / ".gitignore").write_text(".teamwork/\n")
        (self.proj / "a.txt").write_text("hello\n")
        self.commit("init")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-q", "origin", "main")
        self.tw("init", "proj")
        (self.proj / ".teamwork" / "PRIVATE.md").write_text(f"# private\n- {self.SECRET.lower()}\npath: secrets/*\n")

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *x):
        return subprocess.run(["git", "-C", str(self.proj), *x], check=True, capture_output=True, text=True)

    def commit(self, msg):
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", msg)

    def tw(self, *args):
        return subprocess.run([sys.executable, str(TW), "-C", str(self.proj), *args], cwd=self.proj,
                              capture_output=True, text=True)

    def test_init_ignores_private_files(self):
        ig = (self.proj / ".teamwork" / ".gitignore").read_text()
        self.assertIn("PRIVATE.md", ig)
        self.assertIn("private/", ig)
        self.assertTrue((self.proj / ".teamwork" / "private").is_dir())

    def test_clean_passes(self):
        (self.proj / "b.txt").write_text("nothing to see\n")
        self.commit("b")
        r = self.tw("guard")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("guard: ok", r.stdout)

    def test_hit_in_worktree_and_history_masked(self):
        (self.proj / "b.txt").write_text(f"look at {self.SECRET}\n")
        r = self.tw("guard")
        self.assertEqual(r.returncode, 1)
        self.assertIn("worktree b.txt", r.stdout)
        self.assertNotIn(self.SECRET.lower(), r.stdout.lower())  # the word itself is never printed
        self.commit("add b")
        self.assertIn("history", self.tw("guard").stdout)

    def test_hit_only_in_history(self):
        (self.proj / "b.txt").write_text(f"{self.SECRET} notes\n")
        self.commit("oops")
        (self.proj / "b.txt").write_text("cleaned\n")
        self.commit("clean up")
        r = self.tw("guard")
        self.assertEqual(r.returncode, 1)
        self.assertIn("history", r.stdout)
        self.assertNotIn("worktree b.txt", r.stdout)

    def test_private_path(self):
        (self.proj / "secrets").mkdir()
        (self.proj / "secrets" / "k.txt").write_text("x\n")
        r = self.tw("guard")
        self.assertEqual(r.returncode, 1)
        self.assertIn("(name hidden)", r.stdout)  # the whole matching path stays private
        self.assertNotIn("secrets", r.stdout)

    def guard(self, *a):
        return self.tw("guard", *a)

    def tw_mod(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        return tw

    def test_t25_escaped_and_packed_words(self):
        import bz2, io, lzma, zipfile, zlib
        w = "蓝鲸星"
        docx = io.BytesIO()
        with zipfile.ZipFile(docx, "w") as z:
            z.writestr("word/document.xml", "<w:r><w:t>Zor</w:t></w:r><w:r><w:t>blax</w:t></w:r>")
        stream = zlib.compress(b"BT /F1 12 Tf [(Zor) -20 (blax)] TJ ET")
        files = {
            "fixture.json": json.dumps({"name": w}).encode(),  # ensure_ascii: \u84dd...
            "notes.rtf": b"{\\rtf1 \\uc1\\u34013 ?\\u40120 ?\\u26143 ?}",
            "page.html": "&#34013;&#x9cb8;&#26143;".encode(),
            "link.txt": b"https://x.invalid/?q=%5A%6F%72%62%6C%61%78",
            "report.docx": docx.getvalue(),
            "report.pdf": b"%PDF-1.4\n1 0 obj<</Filter/FlateDecode>>stream\n" + stream + b"\nendstream endobj",
            "dump.bz2": bz2.compress(b"abc" * 50 + b" Zorblax" * 20),
            "dump.xz": lzma.compress(b"abc" * 50 + b" Zorblax" * 20),
        }
        (self.proj / ".teamwork" / "PRIVATE.md").write_text(f"- Zorblax\n- {w}\n")
        for name, data in files.items():
            (self.proj / name).write_bytes(data)
            self.commit("add " + name)
            r = self.guard("--base", "origin/main")
            self.assertEqual(r.returncode, 1, name + ": " + r.stdout + r.stderr)
            self.git("reset", "-q", "--hard", "origin/main")
            self.git("clean", "-qfd")
        (self.proj / "plain.json").write_text(json.dumps({"a": "caf\u00e9 \u00e5"}) + " &amp; %41 <b>ok</b>\n")
        self.commit("clean escapes")
        self.assertEqual(self.guard("--base", "origin/main").returncode, 0)

    def test_t25_empty_pre_push_input_fails_closed(self):
        r = self.pre_push("")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("input=$(cat)", r.stdout + r.stderr)
        # the hook install.py writes owns stdin: empty means git had nothing to push ("Everything up-to-date")
        for stdin in ("", "\n"):
            r = subprocess.run([sys.executable, str(TW), "-C", str(self.proj), "guard", "--pre-push", "--remote",
                                "origin", "--stdin-owned"], cwd=self.proj, input=stdin, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("nothing to push", r.stdout)

    def test_t25_paths_and_quoted_entries_stay_whole(self):
        tw = self.tw_mod()
        pm = self.proj / ".teamwork" / "PRIVATE.md"
        pm.write_text('- /home/zorbuser\n- /Users/zorbuser\n- ~/quixbar-notes\n- C:\\Users\\zorb\n- "Quixbar, Ltd"\n- `a/b`\n- Zorblax/Quixbar\n')
        terms, _, _ = tw.load_private(self.proj / ".teamwork")
        for t in ("/home/zorbuser", "/Users/zorbuser", "~/quixbar-notes", "C:\\Users\\zorb", "Quixbar, Ltd", "a/b"):
            self.assertIn(t, terms)
        for t in ("Users", "zorbuser", "quixbar-notes", "Ltd", "a", "b"):
            self.assertNotIn(t, terms)
        self.assertIn("Zorblax", terms)  # a plain 'X/Y' line still adds both
        pm.write_text("1. /srv/zorblax, /opt/quixbar\n- /\n- ~\n- /a/zorb,/b/quix\n")  # a list of paths: each one whole
        terms, _, _ = tw.load_private(self.proj / ".teamwork")
        self.assertEqual(terms, ["/srv/zorblax", "/opt/quixbar", "/a/zorb", "/b/quix"])  # a bare / or ~ never counts
        # a note after a path is dropped, as for any entry; a quoted entry keeps its own '#'; one character never counts
        pm.write_text('- /home/zorbuser - my home\n- /srv/quixbar (old box)\n- ~/lanjing: notes\n- "Quixbar #1"\n'
                      '- "x"\n')
        terms, _, _ = tw.load_private(self.proj / ".teamwork")
        self.assertEqual(terms, ["/home/zorbuser", "/srv/quixbar", "~/lanjing", "Quixbar #1"])

    def test_t25_home_path_is_built_in(self):
        home = Path(self._tmp.name) / "home" / "quixuser"
        home.mkdir(parents=True)
        env = {**os.environ, "HOME": str(home)}
        (self.proj / "cfg.json").write_text(json.dumps({"hook": f"python3 {home}/x/tw.py context"}))
        self.commit("absolute path")
        r = subprocess.run([sys.executable, str(TW), "-C", str(self.proj), "guard", "--base", "origin/main"],
                           cwd=self.proj, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("quixuser", r.stdout)

    def test_t25_hashed_list_and_cloud_kit(self):
        (self.proj / ".teamwork" / "PRIVATE.md").write_text("- Zorblax\n- 蓝鲸星\n")
        j1 = json.loads(self.guard("--hash-list").stdout)
        j2 = json.loads(self.guard("--hash-list").stdout)
        self.assertNotEqual(j1["salt"], j2["salt"])  # a new salt every time
        blob = json.dumps(j1)
        for w in ("zorblax", "蓝鲸星", "\\u84dd"):
            self.assertNotIn(w, blob.lower())
        self.assertGreater(len(j1["hashes"]), 2)  # decoys pad the list
        self.assertNotIn("len", "".join(j1))  # no lengths stored
        kit = self.guard("--hash-list", "--kit").stdout
        self.assertNotIn("Zorblax", kit)
        root = Path(self._tmp.name)
        (root / "tw-guard.py").write_text(kit)
        (root / "h.json").write_text(json.dumps(j1))
        cloud = root / "cloud"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(cloud)], check=True)
        g = lambda *x: subprocess.run(["git", "-C", str(cloud), "-c", "user.name=t", "-c", "user.email=t@e.invalid",
                                       *x], capture_output=True, text=True)
        g("checkout", "-qb", "work")
        (cloud / "n.md").write_text("the 蓝鲸星 plan\n")
        g("add", "-A")
        g("commit", "-qm", "n")
        kitrun = lambda: subprocess.run([sys.executable, "-I", str(root / "tw-guard.py"), "origin/main"], cwd=cloud,
                                        capture_output=True, text=True)
        hashed = lambda: subprocess.run([sys.executable, str(TW), "guard", "--hashed", str(root / "h.json"),
                                         "--repo", str(cloud), "--base", "origin/main"], cwd=cloud,
                                        capture_output=True, text=True)
        self.assertEqual(kitrun().returncode, 1)
        r = hashed()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)  # works without any .teamwork folder
        self.assertNotIn("蓝鲸星", r.stdout)
        g("reset", "-q", "--hard", "origin/main")
        (cloud / "ok.md").write_text("Quixbar is fine\n")
        g("add", "-A")
        g("commit", "-qm", "ok")
        self.assertEqual(kitrun().returncode, 0, kitrun().stdout)
        self.assertEqual(hashed().returncode, 0)
        # run from a subfolder: an untracked file at the top is still read
        (cloud / "sub").mkdir()
        (cloud / "top.md").write_text("蓝鲸星\n")
        r = subprocess.run([sys.executable, "-I", str(root / "tw-guard.py"), "origin/main"], cwd=cloud / "sub",
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        (cloud / "top.md").unlink()
        # any git error fails closed: a base that does not exist, or no repo at all
        for args, cwd in ((["origin/nope"], cloud), (["origin/main"], root)):
            r = subprocess.run([sys.executable, "-I", str(root / "tw-guard.py"), *args], cwd=cwd,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 1, (args, r.stdout, r.stderr))
            self.assertNotIn("guard: ok", r.stdout)
        # decoys are random values, never hashes of a string: hex-heavy files (lock files, SHAs) do not trip them
        tw = self.tw_mod()
        import secrets
        noise = "\n".join(secrets.token_hex(20) for _ in range(3000)).encode()
        for _ in range(10):
            j = json.loads(self.guard("--hash-list").stdout)
            self.assertFalse(tw.HashedList(j["salt"], j["hashes"], j["pre"])(noise.lower()))
            self.assertTrue(all(len(p) == 3 for p in j["pre"]))  # 4096 buckets: the prefix is not named
        # no home path in the list: it would give the cloud thread a guessable user name
        home = root / "home" / "quixuser"
        home.mkdir(parents=True)
        r = subprocess.run([sys.executable, str(TW), "-C", str(self.proj), "guard", "--hash-list"], cwd=self.proj,
                           env={**os.environ, "HOME": str(home)}, capture_output=True, text=True)
        j = json.loads(r.stdout)
        self.assertFalse(tw.HashedList(j["salt"], j["hashes"], j["pre"])(str(home).lower().encode()))

    def test_t25_install_keeps_absolute_paths_out_of_commits(self):
        inst = Path(__file__).resolve().parent / "install.py"
        cfg = Path(self._tmp.name) / "cfg"
        run = lambda: subprocess.run([sys.executable, str(inst), "--apply", "--link", "--no-allow", "--config-dir",
                                      str(cfg), "--project", str(self.proj), "--pre-push"],
                                     capture_output=True, text=True)
        r = run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.proj / ".claude" / "settings.json").exists())
        self.assertTrue((self.proj / ".claude" / "settings.local.json").exists())
        self.assertIn(".claude/settings.local.json", (self.proj / ".git" / "info" / "exclude").read_text())
        self.assertNotIn("settings", self.git("status", "--porcelain").stdout)
        # a tracked hooks folder would carry the hook (absolute paths) into a commit: refused
        (self.proj / ".git" / "hooks" / "pre-push").unlink()
        self.git("config", "core.hooksPath", ".githooks")
        r = run()
        self.assertIn("refused", r.stdout)
        self.assertFalse((self.proj / ".githooks" / "pre-push").exists())
        # another tool's hook: the printed lines save stdin once
        self.git("config", "--unset", "core.hooksPath")
        (self.proj / ".git" / "hooks" / "pre-push").write_text("#!/bin/sh\ngit lfs pre-push \"$@\"\n")
        out = run().stdout
        self.assertIn("input=$(cat)", out)
        self.assertIn("--stdin-owned || exit 1", out)  # the saved input is the guard's alone: up-to-date pushes pass

    def test_round5_star_globs_keep_their_stars(self):
        pm = self.proj / ".teamwork" / "PRIVATE.md"
        pm.write_text("- quixbar\npath: *.pem\n- path: notes_*\n- path: `*.key`\n**path: dump_*.md**\n")
        import importlib.util
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        _, paths, _ = tw.load_private(self.proj / ".teamwork")
        self.assertEqual(paths, ["*.pem", "notes_*", "*.key", "dump_*.md"])
        for name in ["server.pem", "notes_client.md", "deep/id.key", "dump_x.md"]:
            f = self.proj / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x\n")
            self.commit("add " + name)
            r = self.guard("--base", "origin/main")
            self.assertEqual(r.returncode, 1, name + ": " + r.stdout + r.stderr)
            self.git("reset", "-q", "--hard", "origin/main")
            self.git("clean", "-qfd")
        self.assertEqual(self.guard("--base", "origin/main").returncode, 0)

    def test_review_binary_and_encodings(self):
        (self.proj / "l1.txt").write_bytes(("caf\xe9 " + self.SECRET).encode("latin-1"))
        self.assertEqual(self.guard().returncode, 1)
        (self.proj / "l1.txt").unlink()
        (self.proj / "u16.txt").write_bytes(("x " + self.SECRET).encode("utf-16"))
        self.assertEqual(self.guard().returncode, 1)
        (self.proj / "u16.txt").unlink()
        (self.proj / "img.png").write_bytes(b"\x89PNG\x00\x01" + self.SECRET.encode() + b"\x00")
        self.commit("bin")
        (self.proj / "img.png").unlink()
        self.commit("rm bin")
        r = self.guard()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("history", r.stdout)

    def test_review_gitattributes_nodiff(self):
        (self.proj / ".gitattributes").write_text("*.dat -diff\n")
        (self.proj / "x.dat").write_text(self.SECRET + "\n")
        self.commit("dat")
        (self.proj / "x.dat").unlink()
        self.commit("rm dat")
        self.assertEqual(self.guard().returncode, 1)

    def test_review_names_and_chinese(self):
        (self.proj / ".teamwork" / "PRIVATE.md").write_text("- 蓝鲸星\n1. Quixbar\n**Snorf**  # product\n")
        for name, body in (("文件.txt", "Quixbar inside"), ("蓝鲸星.txt", "ok"), ("notes-snorf.md", "ok")):
            (self.proj / name).write_text(body)
            r = self.guard()
            self.assertEqual(r.returncode, 1, name)
            for w in ("蓝鲸星", "quixbar", "snorf"):
                self.assertNotIn(w, r.stdout.lower())  # nothing printed in clear, file names included
            (self.proj / name).unlink()
        self.assertEqual(self.guard().returncode, 0)

    def test_review_ref_not_checked_out(self):
        self.git("checkout", "-q", "-b", "feat")
        (self.proj / "f.txt").write_text(self.SECRET)
        self.commit("feat")
        self.git("checkout", "-q", "main")
        r = self.guard("--base", "main", "--ref", "feat")
        self.assertEqual(r.returncode, 1)
        self.assertIn("tree feat", r.stdout)

    def test_review_missing_list_fails_closed(self):
        (self.proj / ".teamwork" / "PRIVATE.md").unlink()
        r = self.guard()
        self.assertEqual(r.returncode, 1)
        self.assertIn("no private list", r.stdout + r.stderr)
        (self.proj / ".teamwork" / "PRIVATE.md").write_text("# nothing private\n")
        self.assertEqual(self.guard().returncode, 0)

    def test_review_branch_name(self):
        r = subprocess.run([sys.executable, str(TW), "-C", str(self.proj), "guard", "--pre-push"], cwd=self.proj,
                           input=f"refs/heads/x {'a' * 40} refs/heads/{self.SECRET.lower()}-wip {'0' * 40}\n",
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("ref name", r.stdout)

    def test_pre_push_hook_blocks(self):
        inst = Path(__file__).resolve().parent / "install.py"
        cfg = Path(self._tmp.name) / "cfg"
        r = subprocess.run([sys.executable, str(inst), "--apply", "--link", "--no-allow", "--config-dir", str(cfg),
                            "--project", str(self.proj), "--pre-push"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        hook = self.proj / ".git" / "hooks" / "pre-push"
        self.assertTrue(hook.exists())
        self.assertIn('--remote "$1"', hook.read_text())
        lines = hook.read_text().splitlines()  # an older teamwork hook (no --remote) is upgraded in place
        hook.write_text("\n".join(lines[:2] + [lines[2].replace(' --remote "$1"', "")]) + "\n")
        subprocess.run([sys.executable, str(inst), "--apply", "--link", "--no-allow", "--config-dir", str(cfg),
                        "--project", str(self.proj), "--pre-push"], capture_output=True, text=True, check=True)
        self.assertIn('--remote "$1"', hook.read_text())
        (self.proj / "c.txt").write_text(f"{self.SECRET}\n")
        self.commit("c")
        push = subprocess.run(["git", "-C", str(self.proj), "push", "origin", "main"], capture_output=True, text=True)
        self.assertNotEqual(push.returncode, 0)
        self.assertIn("guard: FAILED", push.stdout + push.stderr)
        self.git("reset", "-q", "--hard", "HEAD~1")
        (self.proj / "d.txt").write_text("fine\n")
        self.commit("d")
        push = subprocess.run(["git", "-C", str(self.proj), "push", "origin", "main"], capture_output=True, text=True)
        self.assertEqual(push.returncode, 0, push.stdout + push.stderr)


    def pre_push(self, stdin, remote="origin"):
        return subprocess.run([sys.executable, str(TW), "-C", str(self.proj), "guard", "--pre-push",
                               *(["--remote", remote] if remote else [])],
                              cwd=self.proj, input=stdin, capture_output=True, text=True)

    def test_round2_first_push_to_another_remote(self):
        (self.proj / "b.txt").write_text(f"{self.SECRET}\n")
        self.commit("old leak, already on origin")
        self.git("push", "-q", "origin", "main")
        (self.proj / "b.txt").write_text("clean\n")
        self.commit("clean")
        self.git("push", "-q", "origin", "main")
        other = Path(self._tmp.name) / "mirror.git"
        subprocess.run(["git", "init", "-q", "--bare", str(other)], check=True)
        self.git("remote", "add", "mirror", str(other))
        head = self.git("rev-parse", "HEAD").stdout.strip()
        line = f"refs/heads/main {head} refs/heads/main {'0' * 40}\n"
        self.assertEqual(self.pre_push(line, "mirror").returncode, 1)  # mirror never had the old commit
        self.assertEqual(self.pre_push(line, None).returncode, 1)  # remote unknown: whole history
        self.assertEqual(self.pre_push(line, "origin").returncode, 0)  # origin already has all of it

    def test_round2_encodings_everywhere(self):
        (self.proj / ".teamwork" / "PRIVATE.md").write_text("蓝鲸星\nQuixbär\n")
        for name, data in (("gb.txt", "注意 蓝鲸星".encode("gb18030")), ("l1.txt", "x Quixbär".encode("latin-1")),
                           ("u16.txt", "x 蓝鲸星".encode("utf-16-le")), ("up.txt", "QUIXBÄR".encode("utf-8"))):
            (self.proj / name).write_bytes(data)
            self.commit(name)
            self.assertEqual(self.guard().returncode, 1, f"worktree/tree {name}")
            (self.proj / name).unlink()
            self.commit("rm " + name)
            r = self.guard()
            self.assertEqual(r.returncode, 1, f"history {name}")
            self.assertIn("history", r.stdout)
            self.git("reset", "-q", "--hard", "origin/main")

    def test_round2_list_formats(self):
        (self.proj / ".teamwork" / "PRIVATE.md").write_text(
            "| word | why |\n|---|---|\n| Zeta9 | client |\n"
            "- [ ] Snorf\n- [x] Blorpo\nQuixbar, Flimwax\nFlurbo - our client\nGronky: internal\n")
        terms = None
        import importlib.util
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        terms, _, _ = tw.load_private(self.proj / ".teamwork")
        for w in ("Zeta9", "Snorf", "Blorpo", "Quixbar", "Flimwax", "Flurbo", "Gronky"):
            self.assertIn(w, terms)
        self.assertNotIn("word", terms)  # table header skipped
        self.assertTrue(tw._path_rule_hit("secrets/k.txt", "/Secrets/"))
        self.assertTrue(tw._path_rule_hit("docs/Notes.md", "notes.md"))
        self.assertTrue(tw._path_rule_hit("a/b/key.pem", "**/*.pem"))
        self.assertFalse(tw._path_rule_hit("docs/secrets/x", "/secrets/"))

    def test_round2_author_tag_and_branch_names(self):
        (self.proj / "e.txt").write_text("e\n")
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", f"user.email={self.SECRET.lower()}@example.invalid",
                 "commit", "-qm", "clean message")
        r = self.guard()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.git("reset", "-q", "--hard", "origin/main")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "tag", "-a", "v1", "-m",
                 f"release for {self.SECRET}")
        head = self.git("rev-parse", "HEAD").stdout.strip()
        tag = self.git("rev-parse", "v1").stdout.strip()
        self.assertEqual(self.pre_push(f"refs/tags/v1 {tag} refs/tags/v1 {'0' * 40}\n").returncode, 1)
        self.git("checkout", "-q", "-b", f"{self.SECRET.lower()}-wip")
        r = self.guard()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("ref name", r.stdout)
        self.assertNotIn(self.SECRET.lower(), r.stdout.lower())

    def test_round2_private_folder_name_masked(self):
        (self.proj / ".teamwork" / "private" / "Quixbar-notes.txt").write_text("x")
        self.git("add", "-f", ".teamwork/private/Quixbar-notes.txt")  # someone force-added it
        r = self.guard()
        self.assertEqual(r.returncode, 1)
        self.assertIn("private/*****", r.stdout)
        self.assertNotIn("quixbar", r.stdout.lower())


    def test_round3_stale_remote_url(self):
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty",
                 "-m", f"fix for {self.SECRET} customer")
        self.git("push", "-q", "origin", "main")  # on the old (private) remote
        public = Path(self._tmp.name) / "public.git"
        subprocess.run(["git", "init", "-q", "--bare", str(public)], check=True)
        self.git("remote", "set-url", "origin", str(public))  # refs/remotes/origin/* are now stale
        head = self.git("rev-parse", "HEAD").stdout.strip()
        r = self.pre_push(f"refs/heads/main {head} refs/heads/main {'0' * 40}\n", "origin")
        self.assertEqual(r.returncode, 1, r.stdout)

    def test_round4_history_zip_pushurl_list_encoding(self):
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", f"<w>hello {self.SECRET} quixbar</w>" * 50)
        (self.proj / "d.docx").write_bytes(buf.getvalue())
        self.commit("add doc")
        self.git("rm", "-q", "d.docx")
        self.commit("drop doc")
        r = self.tw("guard")
        self.assertEqual(r.returncode, 1, r.stdout)  # only in history, compressed
        self.git("reset", "-q", "--hard", "origin/main")
        # pushurl differs from the fetch URL: compare against what the push server has
        (self.proj / "b.txt").write_text(f"{self.SECRET}\n")
        self.commit("leak")
        (self.proj / "b.txt").write_text("clean\n")
        self.commit("clean")
        self.git("push", "-q", "origin", "main")
        public = Path(self._tmp.name) / "public2.git"
        subprocess.run(["git", "init", "-q", "--bare", str(public)], check=True)
        self.git("config", "remote.origin.pushurl", str(public))
        head = self.git("rev-parse", "HEAD").stdout.strip()
        self.assertEqual(self.pre_push(f"refs/heads/main {head} refs/heads/main {'0' * 40}\n").returncode, 1)
        self.git("config", "--unset", "remote.origin.pushurl")
        # a list saved with a BOM or as UTF-16 still works; undecodable fails closed
        self.git("reset", "-q", "--hard", "HEAD~2")
        (self.proj / "c.txt").write_text(f"{self.SECRET}\n")
        priv = self.proj / ".teamwork" / "PRIVATE.md"
        for data in (b"\xef\xbb\xbf" + self.SECRET.encode(), self.SECRET.encode("utf-16"), b"\xff\xfa\x80"):
            priv.write_bytes(data)
            r = self.tw("guard")
            self.assertEqual(r.returncode, 1, data[:4])
            self.assertIn("UTF-8" if data[:2] == b"\xff\xfa" else "FAILED", r.stdout + r.stderr)

    def test_round3_tags_on_blobs_and_nested(self):
        blob = subprocess.run(["git", "-C", str(self.proj), "hash-object", "-w", "--stdin"],
                              input=f"{self.SECRET} plan", capture_output=True, text=True).stdout.strip()
        self.git("tag", "lw", blob)
        r = self.pre_push(f"refs/tags/lw {blob} refs/tags/lw {'0' * 40}\n")
        self.assertEqual(r.returncode, 1, r.stdout)
        g = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        self.git(*g, "tag", "-a", "inner", "-m", f"{self.SECRET} inner")
        self.git(*g, "tag", "-a", "outer", "inner", "-m", "clean")
        outer = self.git("rev-parse", "outer").stdout.strip()
        self.git("tag", "-d", "inner")
        self.assertEqual(self.pre_push(f"refs/tags/outer {outer} refs/tags/outer {'0' * 40}\n").returncode, 1)

    def test_round3_parser_nfd_replace_zip_symlink(self):
        import importlib.util, unicodedata, zipfile, io, os as _os
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        (self.proj / ".teamwork" / "PRIVATE.md").write_text(
            "Name: Zorblax\n> Quixbar\n* [x] ~~Snorfle~~\n<!-- Blorpo -->\nFlimwax/Gronky\nCrème\n")
        terms, _, _ = tw.load_private(self.proj / ".teamwork")
        for w in ("Zorblax", "Quixbar", "Snorfle", "Blorpo", "Flimwax", "Gronky"):
            self.assertIn(w, terms)
        (self.proj / "n.txt").write_text(unicodedata.normalize("NFD", "la Crème"))
        self.assertEqual(self.guard().returncode, 1)
        (self.proj / "n.txt").unlink()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", "<w>Zorblax roadmap</w>" * 50)
        (self.proj / "r.docx").write_bytes(buf.getvalue())
        self.assertEqual(self.guard().returncode, 1)
        (self.proj / "r.docx").unlink()
        _os.symlink("/nonexistent/Quixbar-dir", self.proj / "ln")
        self.assertEqual(self.guard().returncode, 1)
        (self.proj / "ln").unlink()
        self.assertEqual(self.guard().returncode, 0)
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty",
                 "-m", "Zorblax secret msg")
        bad = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--amend",
                 "--allow-empty", "-m", "clean copy")
        good = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("reset", "-q", "--hard", bad)
        self.git("replace", bad, good)  # local view says clean; the server would get the original
        self.assertEqual(self.guard().returncode, 1)


class Leases(TwCase):
    """T11: a ready task that overlaps an active task's paths is held back."""

    def setUp(self):
        super().setUp()
        for t in ("ui", "api"):
            self.tw("thread", "add", t)
        self.tw("plan", "approve", "--user-said", "ok")

    def ready(self):
        return json.loads(self.tw("ready").stdout)

    def test_declared_overlap_blocks(self):
        self.tw("task", "add", "T1", "--owner", "ui", "--touches", "web/**")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "web/login.js,api/x.py")
        self.tw("task", "add", "T3", "--owner", "api", "--touches", "api/**")
        self.tw("task", "set", "T1", "--status", "working")
        r = self.ready()
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["with"], "T1")
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["paths"], ["web/**"])
        self.assertIn("T3", r["ready"])
        self.assertNotIn("T2", r["ready"])
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.assertIn("T2", self.ready()["ready"])

    def test_same_owner_not_blocked_and_no_touches_listed(self):
        self.tw("task", "add", "T1", "--owner", "ui", "--touches", "web/**")
        self.tw("task", "add", "T2", "--owner", "ui", "--touches", "web/a.js")
        self.tw("task", "add", "T3", "--owner", "api")
        self.tw("task", "set", "T1", "--status", "working")
        r = self.ready()
        self.assertEqual(r["blocked_by_lease"], {})
        self.assertEqual(r["no_touches"], ["T3"])

    def test_overlap_rules(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        self.assertTrue(tw.paths_overlap("web/", "web/sub/a.js"))
        self.assertTrue(tw.paths_overlap("web/*.js", "web/**"))
        self.assertTrue(tw.paths_overlap("./api/x.py", "api/x.py"))
        self.assertFalse(tw.paths_overlap("web/**", "api/x.py"))
        self.assertFalse(tw.paths_overlap("docs/a.md", "docs/b.md"))
        # review findings
        self.assertTrue(tw.paths_overlap("web", "web/app.js"))
        self.assertTrue(tw.paths_overlap("web", "web/"))
        self.assertTrue(tw.paths_overlap("web/**", "web"))
        self.assertFalse(tw.paths_overlap(".github/x", "github/x"))
        self.assertFalse(tw.paths_overlap("webapp/x", "web"))
        # round 2
        self.assertTrue(tw.paths_overlap("**/config.py", "config.py"))
        self.assertTrue(tw.paths_overlap("web/**/a.js", "web/a.js"))
        self.assertTrue(tw.paths_overlap(".", "api/x.py"))
        self.assertTrue(tw.paths_overlap("web/./a/../b.js", "web/b.js"))
        self.assertTrue(tw.paths_overlap("/web/", "web/b.js"))
        self.assertFalse(tw.paths_overlap("Web/a.js", "web/b.js"))
        # round 3: glob against glob
        for a, b in (("**/*.py", "api/"), ("**/*.py", "api"), ("api/**/*.py", "**/x.py"), ("*.py", "web/*"),
                     ("*/config.py", "api/*.py"), ("docs/*.md", "*/README.md"), ("web/{a,b}.py", "web/a.py"),
                     ("Web/App.py", "web/app.py"), ("app/[id]/page.js", "app/[id]/")):
            self.assertTrue(tw.paths_overlap(a, b), (a, b))
        self.assertTrue(tw.paths_overlap("web/*", "web/sub/a.js"))  # 'web/*' may be a folder
        self.assertTrue(tw.paths_overlap("{a,b}" * 20 + "/x", "b" * 20 + "/x"))  # too many braces: widened
        self.assertFalse(tw.paths_overlap("web/*.css", "web/a.py"))
        self.assertNotEqual(self.tw("task", "add", "T9", "--touches", "../x", ok=False).returncode, 0)

    def test_round8_brace_widening(self):
        import importlib.util
        import time
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        many = "src/{" + ",".join(f"a{i}" for i in range(70)) + ",deep/q}.py"
        self.assertTrue(tw.paths_overlap(many, "src/deep/q.py"))  # widened across folders
        self.assertTrue(tw.paths_overlap("x/{z,{" + ",".join(f"a{i}" for i in range(70)) + "}}", "x/a5"))
        t0 = time.time()
        self.assertTrue(tw.paths_overlap("{a,b}" * 24 + "/x", "a" * 24 + "/x"))
        self.assertLess(time.time() - t0, 2)

    def test_two_ready_tasks_do_not_collide(self):
        self.tw("task", "add", "T1", "--owner", "ui", "--touches", "web/app.js")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "web/**")
        r = self.ready()
        self.assertEqual(r["ready"], ["T1"])
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["with"], "T1")


class LeasesGit(unittest.TestCase):
    """T11: the real diff of an active branch counts, even when undeclared."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name) / "proj"
        self.proj.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.proj / "README").write_text("r\n")
        (self.proj / ".gitignore").write_text(".teamwork/\n")
        self.commit("init")
        self.tw("init", "proj")
        self.tw("thread", "add", "ui")
        self.tw("thread", "add", "api")
        self.tw("plan", "approve", "--user-said", "ok")

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *x):
        return subprocess.run(["git", "-C", str(self.proj), *x], check=True, capture_output=True, text=True)

    def commit(self, msg):
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", msg)

    def tw(self, *args, walk=True, ok=True):
        if len(args) >= 3 and args[0:2] == ("task", "set") and "--review" in args:
            passing = args[args.index("--review") + 1] in ("pass", "capped")
            defaults = [("--tests", "ran"), ("--base", "0000000")]
            if passing:
                defaults += [("--recheck-how", "independent test run"),
                             ("--recheck-evidence", "Primary output shows 12 records")]
            for flag, value in defaults:
                if flag not in args:
                    args = (*args, flag, value)
        if walk and ok:
            walk_args(lambda *x: self.tw(*x, walk=False), args, self.proj / ".teamwork" / "state.json", prepare_primary=True)
        r = subprocess.run([sys.executable, str(TW), "-C", str(self.proj), *args], cwd=self.proj,
                           capture_output=True, text=True)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def test_branch_diff_blocks(self):
        self.tw("task", "add", "T1", "--owner", "ui")  # declared nothing
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.tw("task", "set", "T1", "--status", "working")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "shared").mkdir()
        (self.proj / "shared" / "config.py").write_text("x = 1\n")
        self.commit("ui touches shared")
        self.git("checkout", "-q", "main")
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["paths"], ["shared/config.py"])


class LeasesGitReview(LeasesGit):
    """T11 review findings: base only on origin / wrong base, done-but-unmerged keeps its lease."""

    test_branch_diff_blocks = None

    def ui_branch_changes_shared(self):
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "shared").mkdir()
        (self.proj / "shared" / "config.py").write_text("x = 1\n")
        self.commit("T1: ui touches shared")
        self.git("checkout", "-q", "main")

    def test_wrong_base_warns(self):
        st = json.loads((self.proj / ".teamwork" / "state.json").read_text())
        st["repo"]["base"] = "master"
        (self.proj / ".teamwork" / "state.json").write_text(json.dumps(st))
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared")
        self.tw("task", "set", "T1", "--status", "working")
        self.ui_branch_changes_shared()
        r = json.loads(self.tw("ready").stdout)
        self.assertTrue(any("base branch master not found" in w for w in r["lease_warnings"]))

    def test_done_but_unmerged_still_holds(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared")  # bare folder
        self.ui_branch_changes_shared()
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["status"], "done")
        self.git("merge", "-q", "tw/proj/ui")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["ready"])


    def test_round8_quoted_file_names(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "dir/")
        self.tw("task", "set", "T1", "--status", "working")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "dir").mkdir()
        for n in ('a"b.py', "c\td.py", "e\\f.py"):
            (self.proj / "dir" / n).write_text("x\n")
        self.commit("T1: odd names")
        self.git("checkout", "-q", "main")
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(sorted(r["blocked_by_lease"]["T2"][0]["paths"]),
                         sorted(["dir/a\"b.py", "dir/c\td.py", "dir/e\\f.py"]))

    def test_round10_brace_cli_and_subfolder_project(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "web/{zorblax,quixbar}.js, api/x.py")
        st = json.loads((self.proj / ".teamwork" / "state.json").read_text())
        self.assertEqual(st["tasks"]["T2"]["touches"], ["web/{zorblax,quixbar}.js", "api/x.py"])
        self.assertNotEqual(self.tw("task", "add", "T9", "--touches", "{a,../x}", ok=False).returncode, 0)
        self.tw("task", "set", "T1", "--status", "working")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "web").mkdir()
        (self.proj / "web" / "quixbar.js").write_text("x\n")
        self.commit("T1: quixbar")
        self.git("checkout", "-q", "main")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])
        # a project in a subfolder of the repo: --touches are relative to the project folder
        sub = self.proj / "pkg"
        sub.mkdir()
        run = lambda *x: subprocess.run([sys.executable, str(TW), "-C", str(sub), *x], cwd=sub,
                                        capture_output=True, text=True)
        self.assertEqual(run("init", "pkg").returncode, 0)
        for x in (("thread", "add", "ui"), ("thread", "add", "api"), ("plan", "approve", "--user-said", "ok"),
                  ("task", "add", "T1", "--owner", "ui"), ("task", "add", "T2", "--owner", "api",
                                                            "--touches", "src/zorblax.ts")):
            self.assertEqual(run(*x).returncode, 0, x)
        for x in ("approved", "dispatched"):
            run("task", "set", "T1", "--status", x)
        self.git("checkout", "-q", "-b", "tw/pkg/ui")
        (sub / "src").mkdir()
        (sub / "src" / "zorblax.ts").write_text("x\n")
        self.commit("T1: zorblax")
        self.git("checkout", "-q", "main")
        r = json.loads(run("ready").stdout)
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["paths"], ["src/zorblax.ts"])

    def test_round11_earlier_task_squash_not_held_by_later_ones(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.tw("task", "add", "T4", "--owner", "ui", "--touches", "zz/q.js")
        self.ui_branch_changes_shared()
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.git("merge", "-q", "--squash", "tw/proj/ui")
        self.commit("T1: squash")
        (self.proj / "shared" / "config.py").write_text("x = 9\n")
        self.commit("T5: later edit on main")
        self.tw("task", "set", "T4", "--status", "working")  # the same branch goes on with T4
        r = json.loads(self.tw("ready").stdout)
        self.assertNotIn("T2", r["blocked_by_lease"], r)
        self.assertNotEqual(self.tw("task", "add", "T9", "--touches", "web/{a,b.js, api/x.py", ok=False).returncode, 0)

    def test_round12_stale_origin_copy_after_squash(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "shared").mkdir()
        (self.proj / "shared" / "config.py").write_text("x = 0\n")
        self.commit("T1: wip")
        self.git("update-ref", "refs/remotes/origin/tw/proj/ui", "HEAD")  # pushed WIP, now behind
        (self.proj / "shared" / "config.py").write_text("x = 1\n")
        self.commit("T1: final")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.git("merge", "-q", "--squash", "tw/proj/ui")
        self.commit("T1: squash")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["ready"])
        self.assertNotEqual(self.tw("task", "add", "T9", "--touches", "web/}a,b{.js, api/x.py", ok=False).returncode, 0)

    def test_round13_diverged_stale_copy_and_deleted_branch(self):
        def commit_at(msg, when):
            self.git("add", "-A")
            subprocess.run(["git", "-C", str(self.proj), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                            "commit", "-qm", msg], check=True, capture_output=True,
                           env={**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when})
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "shared").mkdir()
        (self.proj / "shared" / "config.py").write_text("x = 0\n")
        commit_at("T1: wip", "2030-01-01T10:00:00Z")
        self.git("update-ref", "refs/remotes/origin/tw/proj/ui", "HEAD")  # pushed WIP
        (self.proj / "shared" / "config.py").write_text("x = 1\n")
        self.git("add", "-A")
        subprocess.run(["git", "-C", str(self.proj), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-q", "--amend", "-m", "T1: final"], check=True, capture_output=True,
                       env={**os.environ, "GIT_COMMITTER_DATE": "2030-01-01T11:00:00Z"})  # diverged from origin
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.git("merge", "-q", "--squash", "tw/proj/ui")
        commit_at("T1: zorblax (#12)", "2030-01-01T12:00:00Z")
        # T25 / T11-b: diverged copies both hold (work on either side is never dropped), so the stale pushed WIP
        # still holds after the amended copy landed, until it is pushed over, deleted or pruned
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])
        self.git("branch", "-q", "-D", "tw/proj/ui")  # the host deleted the branch: only the stale origin copy left
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])  # unsure: held ...
        self.git("update-ref", "-d", "refs/remotes/origin/tw/proj/ui")  # ... until `git fetch --prune`
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["ready"])

    def test_t25_past_owners_branch_still_holds(self):
        """T11-a: a task handed to another thread keeps the lease of what the earlier owner left unmerged."""
        self.tw("thread", "add", "db")
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.tw("task", "set", "T1", "--status", "working")
        self.ui_branch_changes_shared()
        self.tw("task", "set", "T1", "--owner", "db")  # db has no branch yet; ui's work is still unmerged
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["paths"], ["shared/config.py"])

    def test_t29_past_owner_holds_only_this_tasks_commits(self):
        """T29: after a handover, the earlier owner's branch holds only what its commits for this task changed;
        that thread's other work on the same branch is not counted against its own next task."""
        self.tw("thread", "add", "db")
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T5", "--owner", "ui")
        self.tw("task", "add", "T6", "--owner", "ui", "--touches", "zorb/quixbar.py")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.tw("task", "set", "T1", "--status", "working")
        self.ui_branch_changes_shared()  # T1: shared/config.py
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "zorb").mkdir()
        (self.proj / "zorb" / "quixbar.py").write_text("q = 1\n")
        self.commit("T5: zorblax on the same branch")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T1", "--owner", "db")
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["paths"], ["shared/config.py"])  # T1's own work still holds
        self.assertIn("T6", r["ready"], r)  # ui's other work is not held by db's T1
        self.assertNotIn("T6", r["blocked_by_lease"])

    def test_t29_untagged_and_merge_commits_still_hold(self):
        """T29 review: a `WIP:` commit (RECALL), a fixup! and a merge on the earlier owner's branch keep holding;
        a T2 commit is not T29's (id prefix)."""
        self.tw("thread", "add", "db")
        for t in ("T29", "T2"):
            self.tw("task", "add", t, "--owner", "ui")
        self.tw("task", "add", "T7", "--owner", "api", "--touches", "w/zorblax.py, w/fix.py, m/side.py")
        self.tw("task", "add", "T8", "--owner", "ui", "--touches", "w/two.py")
        self.tw("task", "set", "T29", "--status", "working")
        self.git("checkout", "-q", "-b", "side")
        (self.proj / "m").mkdir()
        (self.proj / "m" / "side.py").write_text("s = 1\n")
        self.commit("side work")
        self.git("checkout", "-q", "main")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "w").mkdir()
        (self.proj / "w" / "zorblax.py").write_text("z = 1\n")
        self.commit("WIP: recall, half of the Zorblax parser")
        (self.proj / "w" / "fix.py").write_text("f = 1\n")
        self.commit("fixup! T29: quixbar")
        (self.proj / "w" / "two.py").write_text("t = 1\n")
        self.commit("T2: other work")
        self.git("merge", "-q", "--no-ff", "-m", "T29: merge side", "side")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T29", "--owner", "db")
        r = json.loads(self.tw("ready").stdout)
        held = sorted(p for b in r["blocked_by_lease"]["T7"] for p in b["paths"])
        self.assertEqual(held, ["m/side.py", "w/fix.py", "w/zorblax.py"], r)
        self.assertIn("T8", r["ready"], r)  # T2's file is not held by T29

    def test_t29_other_lead_naming_this_task_and_renames_hold(self):
        """T29 review r2: `T5 and T29: ...` still names T29; a file T29 made that a T5 commit renamed still holds."""
        self.tw("thread", "add", "db")
        for t in ("T29", "T5"):
            self.tw("task", "add", t, "--owner", "ui")
        self.tw("task", "add", "T7", "--owner", "api", "--touches", "q/both.py, q/new.py")
        self.tw("task", "set", "T29", "--status", "working")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "q").mkdir()
        (self.proj / "q" / "both.py").write_text("b = 1\n")
        self.commit("T5 and T29: shared Quixbar")
        (self.proj / "q" / "old.py").write_text("o = 1\n" * 20)
        self.commit("T29: zorblax module")
        self.git("mv", "q/old.py", "q/new.py")
        self.commit("T5: rename")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T29", "--owner", "db")
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(sorted(p for b in r["blocked_by_lease"]["T7"] for p in b["paths"]), ["q/both.py", "q/new.py"])

    def test_t29_tagged_merge_and_near_names_hold(self):
        """T29 review r3: a merge titled `T5: ...` (evil edits included) and `T5: T29-related` still hold."""
        self.tw("thread", "add", "db")
        for t in ("T29", "T5"):
            self.tw("task", "add", t, "--owner", "ui")
        self.tw("task", "add", "T7", "--owner", "api", "--touches", "e/evil.py, e/side.py, e/near.py")
        self.tw("task", "set", "T29", "--status", "working")
        self.git("checkout", "-q", "-b", "side")
        (self.proj / "e").mkdir()
        (self.proj / "e" / "side.py").write_text("s = 1\n")
        self.commit("T5: side")
        self.git("checkout", "-q", "main")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        self.git("merge", "-q", "--no-ff", "--no-commit", "side")
        (self.proj / "e" / "evil.py").write_text("e = 1\n")
        self.commit("T5: merge side")
        (self.proj / "e" / "near.py").write_text("n = 1\n")
        self.commit("T5: T29-related Zorblax")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T29", "--owner", "db")
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(sorted(p for b in r["blocked_by_lease"]["T7"] for p in b["paths"]),
                         ["e/evil.py", "e/near.py", "e/side.py"])

    def test_t29_many_tasks_stay_fast(self):
        """T29 review r2: the branch log is parsed once, not once per task with a regex per task id."""
        import time
        self.tw("thread", "add", "db")
        self.tw("task", "add", "T1", "--owner", "ui")
        st = json.loads((self.proj / ".teamwork" / "state.json").read_text())
        base = st["tasks"]["T1"]
        for i in range(600):
            st["tasks"][f"T{100 + i}"] = {**base, "owner": "db", "past_owners": ["ui"], "status": "working"}
        (self.proj / ".teamwork" / "state.json").write_text(json.dumps(st))
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "z").mkdir()
        for i in range(40):
            (self.proj / "z" / f"f{i}.py").write_text(f"x = {i}\n")
            self.commit(f"T{100 + i}: zorblax {i}")
        self.git("checkout", "-q", "main")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import tw
        t0 = time.time()
        held = tw._held_paths(json.loads((self.proj / ".teamwork" / "state.json").read_text()))
        self.assertLess(time.time() - t0, 5)
        self.assertEqual(held["T105"], ["z/f5.py"])

    def test_t25_diverged_copies_both_hold(self):
        """T11-b: local and origin diverged with different work on each side: both sides count, not only the newer."""
        def commit_at(msg, when):
            self.git("add", "-A")
            subprocess.run(["git", "-C", str(self.proj), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                            "commit", "-qm", msg], check=True, capture_output=True,
                           env={**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when})
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "zorb/quixbar.py")
        self.tw("task", "set", "T1", "--status", "working")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "zorb").mkdir()
        (self.proj / "zorb" / "quixbar.py").write_text("q = 1\n")
        commit_at("T1: pushed from the other seat", "2030-01-01T10:00:00Z")
        self.git("update-ref", "refs/remotes/origin/tw/proj/ui", "HEAD")
        self.git("reset", "-q", "--hard", "main")
        (self.proj / "zorb").mkdir(exist_ok=True)
        (self.proj / "zorb" / "lanjing.py").write_text("l = 1\n")
        commit_at("T1: newer local work", "2030-01-01T11:00:00Z")
        self.git("checkout", "-q", "main")
        r = json.loads(self.tw("ready").stdout)
        self.assertIn("T2", r["blocked_by_lease"], r)  # the older origin copy's file still holds

    def test_round14_unpushed_work_after_older_copy_squashed(self):
        def commit_at(msg, when):
            self.git("add", "-A")
            subprocess.run(["git", "-C", str(self.proj), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                            "commit", "-qm", msg], check=True, capture_output=True,
                           env={**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when})
        for fix_id in ("T4", "T1"):  # a later task's work, or the done task's own review fix, not pushed
            self.tearDown()
            self.setUp()
            self.tw("task", "add", "T1", "--owner", "ui")
            self.tw("task", "add", "T4", "--owner", "ui", "--touches", "zz/q.js")
            self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/zorb.py")
            self.git("checkout", "-q", "-b", "tw/proj/ui")
            (self.proj / "shared").mkdir()
            (self.proj / "shared" / "zorb.py").write_text("a = 1\n")
            commit_at("T1: zorblax", "2030-01-01T10:00:00Z")
            self.git("update-ref", "refs/remotes/origin/tw/proj/ui", "HEAD")  # pushed
            pushed = self.git("rev-parse", "HEAD").stdout.strip()
            (self.proj / "shared" / "zorb.py").write_text("a = 2\n")
            commit_at(f"{fix_id}: more on zorb", "2030-01-01T11:30:00Z")  # local only
            self.git("checkout", "-q", "main")
            self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
            if fix_id == "T4":
                self.tw("task", "set", "T4", "--status", "working")
            self.git("merge", "-q", "--squash", pushed)
            commit_at("T1: Zorblax (#3)", "2030-01-01T12:00:00Z")
            self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"], fix_id)

    def test_round15_combined_squash_and_amended_fix(self):
        def commit_at(msg, when, *extra):
            self.git("add", "-A")
            subprocess.run(["git", "-C", str(self.proj), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                            "commit", "-q", *extra, "-m", msg], check=True, capture_output=True,
                           env={**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when})
        lines = lambda *x: "".join(f"{v}\n" for v in x)
        for case in ("plain", "later-task", "amended"):
            self.tearDown()
            self.setUp()
            (self.proj / "shared").mkdir()
            (self.proj / "shared" / "zorb.py").write_text(lines(1, 2, 3, 4, 5, 6, 7))
            commit_at("base file", "2030-01-01T09:00:00Z")
            self.tw("task", "add", "T1", "--owner", "ui")
            self.tw("task", "add", "T4", "--owner", "ui", "--touches", "zz/q.js")
            self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/zorb.py")
            self.git("checkout", "-q", "-b", "tw/proj/ui")
            (self.proj / "shared" / "zorb.py").write_text(lines("one", 2, 3, 4, 5, 6, 7))
            commit_at("T1: zorblax", "2030-01-01T10:00:00Z")
            pushed = self.git("rev-parse", "HEAD").stdout.strip()
            self.git("update-ref", "refs/remotes/origin/tw/proj/ui", pushed)
            if case == "later-task":
                (self.proj / "shared" / "zorb.py").write_text(lines("one", 2, 3, "four", 5, 6, 7))
                commit_at("T4: quixbar", "2030-01-01T11:00:00Z")
            if case == "amended":
                (self.proj / "shared" / "zorb.py").write_text(lines("ONE", 2, 3, 4, 5, 6, 7))
                commit_at("T1: zorblax", "2030-01-01T11:00:00Z", "--amend")
            self.git("checkout", "-q", "main")
            (self.proj / "shared" / "zorb.py").write_text(lines(1, 2, 3, 4, 5, 6, "seven"))
            commit_at("lead: line 7", "2030-01-01T10:30:00Z")
            self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
            if case == "later-task":
                self.tw("task", "set", "T4", "--status", "working")
            (self.proj / "shared" / "zorb.py").write_text(lines("one", 2, 3, 4, 5, 6, "seven"))  # host squash
            commit_at("T1: Zorblax (#3)", "2030-01-01T12:00:00Z")
            r = json.loads(self.tw("ready").stdout)
            if case == "plain":
                self.assertIn("T2", r["ready"], case)  # the squash folded in the base's own edit
            else:
                self.assertIn("T2", r["blocked_by_lease"], case)  # later work / an unpushed fix is not on the base

    def test_round2_stale_origin_rename_recalled(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared")
        self.ui_branch_changes_shared()
        self.git("update-ref", "refs/remotes/origin/main", "main")  # origin/main from before the merge
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.git("merge", "-q", "tw/proj/ui")  # merged locally, not pushed: local main is newer
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["ready"])
        # a rename moves files out of a leased folder: both names count
        self.git("checkout", "-q", "-b", "tw/proj/api2", "main")
        self.tw("thread", "add", "api2")
        self.tw("task", "add", "T3", "--owner", "api2")
        self.tw("task", "add", "T4", "--owner", "ui", "--touches", "shared/**")
        (self.proj / "moved.py").write_text((self.proj / "shared" / "config.py").read_text())
        (self.proj / "shared" / "config.py").unlink()
        self.commit("T3: move config")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T3", "--status", "working")
        r = json.loads(self.tw("ready").stdout)
        self.assertIn("shared/config.py", r["blocked_by_lease"]["T4"][0]["paths"])
        self.tw("task", "set", "T3", "--status", "recalled")  # WIP stays on the branch
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(r["blocked_by_lease"]["T4"][0]["status"], "recalled")

    def test_round3_squash_merge_releases(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared")
        self.ui_branch_changes_shared()
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.git("merge", "-q", "--squash", "tw/proj/ui")
        self.commit("T1: squash of tw/proj/ui")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["ready"])

    def test_round8_real_files_and_later_fix(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        F = tw.RealFile
        for g, f in (("*.css", "zorb/app.py"), ("**/*.test.js", "quix/main.go"), ("src/**/*.ts", "src/app.py"),
                     ("*.md", "README"), ("zorb/*.py", "zorb/back\\sl.txt")):
            self.assertFalse(tw.paths_overlap(g, F(f)), (g, f))
        for g, f in (("**/*.py", "a/b.py"), ("zorb/", "zorb/x/y.py"), ("zorb", "zorb/a.py"), ("*.md", "d/x.md"),
                     ("zorb/*.py", "zorb/back\\sl.py"), ("a/[*]b", "a/*b")):
            self.assertTrue(tw.paths_overlap(g, F(f)), (g, f))
        self.assertTrue(tw.paths_overlap(F("Zorb/A.py"), F("zorb/a.py")))
        for g, f in (("app/[id]/page.tsx", "app/[id]/page.tsx"), ("app/[id]/", "app/[id]/x.ts"),
                     ("src/routes/[slug]/**", "src/routes/[slug]/+page.svelte"), ("{a,b}.py", "{a,b}.py"),
                     ("*blax", "src/zorblax/gen.py")):
            self.assertTrue(tw.paths_overlap(g, F(f)), (g, f))
            self.assertTrue(tw.paths_overlap(g, f), (g, f))  # declared and real agree
        # squash-merged, then a review fix on the branch that is not merged: still held
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared")
        self.ui_branch_changes_shared()
        self.git("merge", "-q", "--squash", "tw/proj/ui")
        self.commit("T1: squash of tw/proj/ui")
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "shared" / "config.py").write_text("x = 2\n")
        self.commit("T1: review fix")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])

    def test_round4_squash_then_base_edits_and_topic_blocked(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared")
        self.ui_branch_changes_shared()
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.git("merge", "-q", "--squash", "tw/proj/ui")
        self.commit("T1: squash of tw/proj/ui")
        (self.proj / "shared" / "config.py").write_text("x = 2\n")  # main edits the file afterwards
        self.commit("later edit")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["ready"])
        self.tw("task", "add", "T3", "--owner", "ui", "--topics", "t", "--touches", "z.py")
        self.tw("task", "add", "T4", "--owner", "api", "--topics", "t")
        self.tw("thread", "add", "db")
        self.tw("task", "add", "T5", "--owner", "db", "--touches", "z.py")
        self.tw("task", "set", "T4", "--status", "working")
        r = json.loads(self.tw("ready").stdout)
        self.assertIn("T3", r["blocked_by_topic"])
        self.assertIn("T5", r["ready"])  # T3 will not start, so it holds no lease

    def test_round5_mention_is_not_a_merge(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.ui_branch_changes_shared()
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        (self.proj / "NOTES").write_text("n\n")
        self.commit("plan: T1 review pending, do not merge yet")  # names T1, touches nothing of it
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])
        (self.proj / "shared").mkdir(exist_ok=True)
        (self.proj / "shared" / "config.py").write_text("other\n")
        self.commit("Revert T1 partially")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])

    def test_round5_one_pass_no_collisions(self):
        for n in ("web", "db"):
            self.tw("thread", "add", n)
        self.tw("task", "add", "X", "--owner", "api", "--touches", "a/")
        self.tw("task", "add", "A", "--owner", "ui", "--topics", "t", "--touches", "a/")
        self.tw("task", "add", "B", "--owner", "web", "--topics", "t", "--touches", "b/")
        self.tw("task", "add", "C", "--owner", "db", "--touches", "b/x.py")
        self.tw("task", "set", "X", "--status", "working")
        r = json.loads(self.tw("ready").stdout)
        self.assertIn("A", r["blocked_by_lease"])
        self.assertIn("B", r["ready"])
        self.assertIn("C", r["blocked_by_lease"])  # B is offered, so it holds b/

    def test_round6_bracket_names_and_mention_edits(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "docs/*.md")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "docs").mkdir()
        (self.proj / "docs" / "notes[draft.md").write_text("d\n")
        self.commit("T1: odd name")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T1", "--status", "working")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])  # no crash, still a clash
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "t", "--user-said", "ok")
        (self.proj / "docs").mkdir(exist_ok=True)
        (self.proj / "docs" / "notes[draft.md").write_text("prep\n")
        self.commit("Prepare docs for T1 (not merged yet)")  # names T1 and edits the file, but is not T1
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])

class HandoverGit(LeasesGit):
    test_branch_diff_blocks = None

    def test_origin_commits_not_hidden_by_local_branch(self):
        self.git("checkout", "-q", "-b", "tmp")
        (self.proj / "w.txt").write_text("w\n")
        self.commit("T1: pushed work")
        sha = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("checkout", "-q", "main")
        self.git("update-ref", "refs/remotes/origin/tw/proj/ui", sha)
        self.git("branch", "tw/proj/ui", "main")  # local branch has nothing
        out = self.tw("handover", "ui").stdout
        self.assertIn("T1: pushed work", out)
        self.assertIn("local and origin differ", out)
        self.assertIn("[origin/tw/proj/ui]", out)


    def test_round2_missing_base_keeps_configured_name(self):
        st = json.loads((self.proj / ".teamwork" / "state.json").read_text())
        st["repo"]["base"] = "nope"
        (self.proj / ".teamwork" / "state.json").write_text(json.dumps(st))
        out = self.tw("handover", "ui").stdout
        self.assertIn("(base nope)", out)
        self.assertIn("base branch nope not found", out)


class Trace(LeasesGit):
    """T17: follow a request to tasks, threads, sessions, commits and report time."""

    test_branch_diff_blocks = None  # inherited fixture only

    def test_trace_request_end_to_end(self):
        self.tw("thread", "set", "ui", "--session", "sess-ui", "--status", "working")
        self.tw("task", "add", "T1", "--owner", "ui", "--title", "blue button")
        self.tw("task", "add", "T2", "--owner", "ui", "--title", "other work")
        self.tw("req", "add", "make the button blue", "--tasks", "T1")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "b.css").write_text("blue\n")
        self.commit("T1: blue button")
        (self.proj / "c.css").write_text("x\n")
        self.commit("T2: unrelated")
        self.git("checkout", "-q", "main")
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        k = t["tasks"][0]
        self.assertEqual((k["task"], k["thread"], k["session_id"], k["branch"]), ("T1", "ui", "sess-ui", "tw/proj/ui"))
        self.assertEqual(len(k["commits"]), 1)  # only its own commits
        self.assertTrue(any("T2: unrelated" in c for c in t["other_branch_work"]["tw/proj/ui"]))
        self.git("merge", "-q", "--no-ff", "tw/proj/ui", "-m", "Merge ui (T1)")
        self.tw("task", "set", "T1", "--accept", "x", "--status", "done", "--review", "pass", "--reviewer", "rev")
        self.tw("req", "set", "R1", "--status", "reported")
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        self.assertTrue(t["reported_at"])
        commits = t["tasks"][0]["commits"]
        self.assertTrue(any("T1: blue button" in c for c in commits))
        self.assertFalse(any("T2: unrelated" in c for c in commits))  # finished task: only its own commits
        evs = [e["ev"] for e in t["events"]]
        self.assertIn("req-add", evs)
        self.assertIn("task-set", evs)
        text = self.tw("trace", "R1").stdout
        self.assertIn("R1 [reported] make the button blue", text)
        self.assertIn("session sess-ui", text)

    def test_board_cross_references(self):
        self.tw("task", "add", "T1", "--owner", "ui", "--title", "blue button")
        self.tw("req", "add", "make the button blue", "--tasks", "T1")
        board = self.tw("board").stdout
        self.assertIn("**R1** [dispatched] make the button blue — T1", board)
        row = [ln for ln in board.splitlines() if ln.startswith("| T1 |")][0]
        self.assertTrue(row.rstrip().endswith("| R1 |"))

    def test_review_reassigned_task_keeps_commits(self):
        self.tw("task", "add", "T1", "--owner", "ui", "--title", "x")
        self.tw("req", "add", "x", "--tasks", "T1")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "early.txt").write_text("e\n")
        self.commit("T1: early work by ui")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T1", "--owner", "api")
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        self.assertEqual(t["tasks"][0]["owners"], ["ui", "api"])
        self.assertTrue(any("T1: early work by ui" in c for c in t["tasks"][0]["commits"]))

    def test_review_bad_log_lines_wrong_base_zh_closed(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("req", "add", "x", "--tasks", "T1")
        with open(self.proj / ".teamwork" / "log.jsonl", "ab") as f:
            f.write(b'[1,2]\n"str"\n42\n{bad\n\xff\xfe\n')
        st = json.loads((self.proj / ".teamwork" / "state.json").read_text())
        st["repo"]["base"] = "master"
        (self.proj / ".teamwork" / "state.json").write_text(json.dumps(st))
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        self.assertTrue(t["warnings"])
        self.tw("prefs", "--set", "language=zh")
        self.tw("req", "drop", "R1", "--user-said", "不做了")
        text = self.tw("trace", "R1").stdout
        self.assertIn("[已放弃]", text)
        self.assertIn("线程 ui", text)
        self.assertIn("关闭 20", text)
        self.assertIn("| R1✗ |", self.tw("board").stdout)

    def test_trace_without_git(self):
        subprocess.run(["rm", "-rf", str(self.proj / ".git")], check=True)
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("req", "add", "x", "--tasks", "T1")
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        self.assertEqual(t["tasks"][0]["commits"], [])


    def test_round2_ids_dedupe_base_text(self):
        self.tw("thread", "set", "ui", "--session", "s1")
        self.tw("thread", "reseat", "ui", "--session", "s2", "--reason", "failed")
        for t in ("T1", "T2", "T3"):
            self.tw("task", "add", t, "--owner", "ui", "--title", t)
        self.tw("req", "add", "two tasks", "--tasks", "T1,T2")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        for msg in ("T1: one", "T2: two", "T1.5: half step", "T3: other"):
            (self.proj / (msg[:4].replace(":", "").replace(".", "_") + ".txt")).write_text(msg)
            self.commit(msg)
        self.git("checkout", "-q", "main")
        self.git("update-ref", "refs/remotes/origin/tw/proj/ui", "tw/proj/ui")
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        t1 = t["tasks"][0]["commits"]
        self.assertEqual(len(t1), 1, t1)  # T1.5 is not T1
        other = [c for cs in t["other_branch_work"].values() for c in cs]
        self.assertEqual(sorted(c.split(" ", 2)[2] for c in other), ["T1.5: half step", "T3: other"])  # once each
        with open(self.proj / ".teamwork" / "log.jsonl", "a") as f:
            f.write(json.dumps({"ev": "custom", "ids": "R1,R12"}) + "\n" + json.dumps({"ev": "x", "ids": "R12"}) + "\n")
        evs = [e["ev"] for e in json.loads(self.tw("trace", "R1", "--json").stdout)["events"]]
        self.assertIn("custom", evs)
        self.assertNotIn("x", evs)
        self.tw("req", "drop", "R1", "--user-said", "stop", "--note", "superseded by R2")
        text = self.tw("trace", "R1").stdout
        self.assertIn("superseded by R2", text)
        self.assertIn("sessions: s1 (failed) -> s2", text)

    def test_round3_sub_ids_and_bad_log_fields(self):
        for t in ("T1", "T1a"):
            self.tw("task", "add", t, "--owner", "ui")
        self.tw("req", "add", "one", "--tasks", "T1")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        for msg in ("T1: main", "T1a: sub", "T1-2: part", "T1_x: other"):
            (self.proj / (str(abs(hash(msg))) + ".txt")).write_text(msg)
            self.commit(msg)
        self.git("checkout", "-q", "main")
        with open(self.proj / ".teamwork" / "log.jsonl", "a") as f:
            for e in ({"ids": 42}, {"task": "T1", "snap_task": "str"}, {"task": "T1", "owner": ["ui"]}):
                f.write(json.dumps(e) + "\n")
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        self.assertEqual([c.split(" ", 4)[4] for c in t["tasks"][0]["commits"]], ["T1: main"])

    def test_round2_init_detects_base_and_lead_base(self):
        self.git("branch", "-m", "main", "trunk")
        other = self.proj.parent / "p2"
        subprocess.run(["git", "clone", "-q", str(self.proj), str(other)], check=True, capture_output=True)
        subprocess.run([sys.executable, str(TW), "-C", str(other), "init", "p2", "--path", str(other)],
                       check=True, capture_output=True, cwd=other)
        self.assertEqual(json.loads((other / ".teamwork" / "state.json").read_text())["repo"]["base"], "trunk")
        self.tw("lead", "--base", "trunk")
        self.assertEqual(json.loads((self.proj / ".teamwork" / "state.json").read_text())["repo"]["base"], "trunk")


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


class Rebuild(LeasesGit):
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


class FewerPrompts(TwCase):
    """T27: install.py pre-approves scratch folders and always asks for irreversible repo actions; the protocol
    tells every session how not to cause prompts."""

    def install(self, *extra):
        inst = Path(__file__).resolve().parent / "install.py"
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
        sys.path.insert(0, str(Path(__file__).resolve().parent))
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
        r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "install.py"), "--link", "--apply",
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
        r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "install.py"), "--link", "--apply",
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
        r2 = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "install.py"), "--link", "--apply",
                             "--config-dir", str(cfg), "--project", str(proj), "--served", str(served)], capture_output=True,
                            text=True, cwd=self.dir, env={**os.environ, "TMPDIR": str(self.dir / "tmpd")})
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("PreToolUse hook: already present", r2.stdout)
        self.assertNotIn("updating to an absolute python path", r2.stdout)

    def test_hook_match_needs_exactly_one_quoted_interpreter(self):
        """T66 review: a user's wrapper around our hook is theirs; only `python3 ` or one quoted word counts as ours."""
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import install
        tw_py = f'"{install.DEST / "scripts" / "tw.py"}"'
        self.assertTrue(install._tw_hook(f"python3 {tw_py} context", "context"))
        self.assertTrue(install._tw_hook(f'"/opt/py/bin/python3" {tw_py} context', "context"))
        self.assertFalse(install._tw_hook(f'"/x/wrap" --flag "y" {tw_py} context', "context"))
        self.assertFalse(install._tw_hook(f'"" {tw_py} context', "context"))
        self.assertFalse(install._tw_hook(f"python3 {tw_py} bash-guard", "context"))

    def test_hook_python_can_run_tw(self):
        """T66 review: the interpreter written into hooks must be able to run tw.py, and not a venv's."""
        sys.path.insert(0, str(Path(__file__).resolve().parent))
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
        sys.path.insert(0, str(Path(__file__).resolve().parent))
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
        sys.path.insert(0, str(Path(__file__).resolve().parent))
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
        skill = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text()
        for want in ("Keep prompts rare", "The user's own decisions", "force-pushing the base branch",
                     "visibility", "Bash(sleep *)", "send_later"):
            self.assertIn(want, skill)


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
        skill = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text()
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
        skill = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text()
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


class LabelsBothLanguages(unittest.TestCase):
    def test_every_label_has_english_and_chinese(self):
        sys.path.insert(0, str(TW.parent)); import tw as twmod
        en, zh = set(twmod.LABELS["en"]), set(twmod.LABELS["zh"])
        self.assertEqual(sorted(zh - en), []); self.assertEqual(sorted(en - zh), [])


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


class GuardSafeShapes(unittest.TestCase):
    """T36: nothing the skill tells a session to run, and nothing its scripts run, trips other plugins' guards."""

    # The shapes common safety hooks ask about (recursive force delete, hard reset, forced clean / branch delete /
    # push, checkout over the work tree). Written as regexes, so this file does not match itself.
    G = r"git\s+(?:-C\s+\S+\s+)?"  # `git -C <path> ...` is the protocol's own spelling
    SHAPES = [r"\brm\s+-[a-zA-Z]*[rR][a-zA-Z]*f", r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*[rR]", r"\brm\s+.*-[rR]\b.*\s-f\b",
              r"\brm\s+.*-f\b.*\s-[rR]\b", r"\brm\s+.*--recursive\b.*--force\b", r"\brm\s+.*--force\b.*--recursive\b",
              G + r"reset\s+(.*\s)?--hard", G + r"clean\s+(.*\s)?-[a-zA-Z]*f", G + r"push\s+.*--force([^-]|$)",
              G + r"push(.*\s)?-f[a-zA-Z]*\b", G + r"push\s+(.*\s)?\+\S", G + r"checkout\s+(.*\s)?--\s+\.",
              G + r"branch\s+(.*\s)?-D"]
    SKIP = (".git", ".teamwork", ".claude", "docs/field-tests")  # local settings are rules, not commands; field tests quote what went wrong, word for word

    def test_no_guard_tripping_shapes(self):
        root = Path(__file__).resolve().parent.parent
        hits = []
        paths = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in (".py", ".md", ".txt", ".sh", ".json",
                                                                                  ".yml", ".yaml")
                       and not any(str(p.relative_to(root)).startswith(x) for x in self.SKIP))
        self.assertIn(root / "SKILL.md", paths)
        for path in paths:
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for rx in self.SHAPES:
                    if re.search(rx, line, 0 if "branch" in rx else re.I):
                        hits.append(f"{path.relative_to(root)}:{i}: {line.strip()[:100]}")
        self.assertEqual(hits, [])

    def test_shapes_still_match_what_they_should(self):
        bad = ["rm " + "-rf /tmp/x", "rm -r " + "-f x", "rm " + "-Rf x", "rm " + "-vrf x", "git reset " + "--hard HEAD",
               "git clean " + "-fd", "git clean -xd" + "f", "git branch " + "-D x", "git push origin " + "--for" + "ce",
               "git push " + "-" + "f origin x", "git push -" + "fu origin x", "git push origin " + "+main",
               "git -C /tmp/q push " + "-" + "f origin x", "git -C /tmp/q reset " + "--hard", "git reset HEAD~1 " + "--hard"]
        ok = ["rm -r /tmp/x", "git push --force-with-lease origin x", "git branch -d x", "git switch -C x main",
              "git worktree remove --force /tmp/wt"]
        hit = lambda c: any(re.search(rx, c, 0 if "branch" in rx else re.I) for rx in self.SHAPES)
        self.assertTrue(all(hit(c) for c in bad))
        self.assertFalse([c for c in ok if hit(c)])

    def test_protocol_lists_the_shapes_and_alternatives(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        env = {k: v for k, v in os.environ.items() if k != "TEAMWORK_DIR"}
        subprocess.run([sys.executable, str(TW), "init", "demo", "--goal", "g"], cwd=d, env=env, check=True,
                       capture_output=True)
        subprocess.run([sys.executable, str(TW), "thread", "add", "ui"], cwd=d, env=env, check=True, capture_output=True)
        wp = subprocess.run([sys.executable, str(TW), "worker-prompt", "ui"], cwd=d, env=env, capture_output=True,
                            text=True).stdout
        for want in ("safety hooks", "`git push --force-with-lease`", "`git switch -C", "Never disguise",
                     "this list included"):
            self.assertIn(want, wp)
        skill = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Command shapes that trip guards", skill)
        self.assertIn("never edit the user's plugins or hooks", skill)


class BashGuard(unittest.TestCase):
    """T36: the PreToolUse hook judges each command's argv: big decisions ask, wiping / or ~ is denied, everything
    else (words in messages, heredocs, grep arguments included) passes silently."""

    F, FORCE, HARD, RF = "-" + "f", "--for" + "ce", "--" + "hard", "-r" + "f"

    def judge(self, cmd):
        r = subprocess.run([sys.executable, str(TW), "bash-guard"], input=json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": cmd}}), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] if r.stdout.strip() else None

    def test_ordinary_work_passes(self):
        F, FORCE, HARD = self.F, self.FORCE, self.HARD
        for cmd in ("git status", "git -C /tmp/q commit -m x", "git push -u origin tw/q/ui",
                    "git push --force-with-lease origin tw/q/ui", "git push --force-with-lease=tw/q/ui:abc origin x",
                    "git -C /tmp/q push origin x", "git branch -d tw/q/ui", "git switch -C tw/q/ui origin/main",
                    "git restore --staged quix.py", "git clean -n", "rm zorb.txt", f"rm {F} zorb.txt", "ls /tmp",
                    "python3 scripts/tw.py ready", "launchctl list", "brew install quixbar", "git checkout main",
                    "git stash list", "git worktree list", "mkdir -p /tmp/z && cd /tmp/z && git init -q"):
            self.assertIsNone(self.judge(cmd), cmd)

    def test_t42_no_heredoc_inside_arithmetic_or_expansions(self):
        """`<<` in (( )), $(( )), $[ ], ${ } is a shift or text: the next lines are commands; a quoted `)` does not
        end $( )."""
        HARD = self.HARD
        for cmd in (f"(( x = 1 << 2 ))\ngit reset {HARD}\n2", f"for (( i=1<<2; i<9; i++ )); do :; done\ngit reset {HARD}\n2",
                    f"echo ${{x:-<<EOF}}\ngit reset {HARD}\nEOF}}", f"echo $[ 1 <<2 ]\ngit reset {HARD}\n2",
                    f'echo "$((1<<2))"\ngit reset {HARD}\n2', f"echo $(echo ')' ; git reset {HARD})",
                    f'echo $(echo ")" ; git reset {HARD})', f"echo ${{x:-$(git reset {HARD})}}"):
            self.assertEqual(self.judge(cmd), "ask", cmd)
        self.assertIsNone(self.judge(f"echo $(cat <<EOF\ngit reset {HARD}\nEOF\n)\nls"))  # a real heredoc in $( )

    def test_t42_heredoc_delimiters_read_as_the_shell_does(self):
        HARD = self.HARD
        for cmd in (f'cat <<E"O"F\nbody\nEOF\ngit reset {HARD}', f'cat <<"EO F"\nx\nEO F\ngit reset {HARD}',
                    f"cat << EOF\ngit reset {HARD}", f'cat <<A <<B\na\nA\nb\nB\ngit reset {HARD}',
                    f"cat <<$'EOF'\nbody\nEOF\ngit reset {HARD}\n$EOF", f'cat <<$"EOF"\nb\nEOF\ngit reset {HARD}',
                    f"cat <<$'E\\x4fF'\nhi\nEOF\ngit reset {HARD}\nE\\x4fF", f'cat <<"E\\$F"\nhi\nE$F\ngit reset {HARD}\nE\\$F'):
            self.assertEqual(self.judge(cmd), "ask", cmd)  # after the body, or an unclosed one: judged
        for cmd in (f"cat <<'EOF'\ngit reset {HARD}\nEOF\nls", f"cat <<-EOF\n\tgit reset {HARD}\n\tEOF\nls"):
            self.assertIsNone(self.judge(cmd), cmd)  # inside a closed body: data

    def test_words_in_messages_heredocs_and_greps_pass(self):
        F, FORCE, HARD, RF = self.F, self.FORCE, self.HARD, self.RF
        words = (f"launchctl and DROP TABLE, sudo, shutdown, reboot, pkill, rm {RF} /, git push {FORCE}, push {F}u, "
                 f"+main, :old, reset {HARD}, clean {F}, worktree remove, stash drop, branch -" + "D, checkout -- ., "
                 "restore ., brew uninstall")
        for cmd in (f'git commit -m "T36: {words}"', f'git -C /tmp/zorblax commit -m "fix checkout flow -- handle cart"',
                    f'git -C /tmp/zorblax commit -m "docs: worktree remove step; stash drop; restore .env; push {F}ail"',
                    f'git -C /tmp/zorblax commit -m "retry push with + backoff; reset with {HARD}; clean {F}x"',
                    f'git -C /tmp/zorblax log --grep "use branch -" + "D sparingly"', f"grep -n '{words}' notes.md",
                    f"cat > /tmp/zorblax.md <<'EOF'\n{words}\nrm {RF} ~\nEOF\necho done",
                    f'python3 scripts/tw.py ask "{words}"', f"echo 'git push {FORCE}' | wc -c"):
            self.assertIsNone(self.judge(cmd), cmd)

    def test_big_decisions_ask_in_every_spelling(self):
        F, FORCE, HARD, RF = self.F, self.FORCE, self.HARD, self.RF
        for cmd in (f"git push {FORCE} origin main", f"git push origin main {FORCE}", f"git push {F} origin x",
                    f"git push {F}u origin x", "git push origin " + "+main", "git push origin :old",
                    "git push origin --delete old", "git push --mirror", f"git -C /tmp/q push {F} origin x",
                    f"git -c core.x=y -C /tmp/q push origin x {FORCE}", f"git reset {HARD} HEAD~1",
                    f"git reset HEAD~1 {HARD}", f"git -c a=b reset {HARD}", "git clean -xd" + "f",
                    f"git clean -d {F}", "git branch -" + "D x", "git branch --delete " + FORCE + " x",
                    "git branch " + F + " main HEAD~3", "git checkout .", "git checkout -- quix.py",
                    f"git checkout {F} main", "git switch --discard-changes main", "git restore quix.py",
                    "git restore .", "git stash drop", "git -C /tmp/q stash clear", "git worktree remove /tmp/w",
                    "rm -r /tmp/z", f"rm {RF} /tmp/z", "rm -R /tmp/z", "rm -vr" + "f /tmp/z", "/bin/rm -r /tmp/z",
                    "rm --recursive " + FORCE + " /tmp/z", "find /tmp/z -delete", "sudo ls", "shutdown -h now",
                    "launchctl unload x.plist", "launchctl kickstart -k system/x", "killall quixbar", "pkill zorb",
                    "diskutil eraseDisk x", "brew uninstall quixbar", "brew services restart quixbar",
                    "npm uninstall -g quixbar", "psql -c 'DROP TABLE zorb'", "gh repo delete quixbar/zorblax",
                    "gh repo edit q/z --visibility public", "gh api -X DELETE repos/q/z",
                    "cd /tmp/q && git push origin x " + FORCE, "git status; git reset " + HARD,
                    "X=1 git push " + FORCE):
            self.assertEqual(self.judge(cmd), "ask", cmd)

    def test_wiping_root_or_home_is_denied(self):
        RF, F = self.RF, self.F
        for cmd in (f"rm {RF} /", f"rm {RF} /*", f"rm {RF} ~", f"rm {RF} ~/", f"rm {RF} ~/*", f'rm {RF} "$HOME"',
                    f"rm {RF} $HOME", "rm -R" + F[1] + " ${HOME}", f"rm -r {F} /", f"sudo rm {RF} /",
                    f"rm {RF} {Path.home()}"):
            self.assertEqual(self.judge(cmd), "deny", cmd)
        self.assertEqual(self.judge(f"rm {RF} ~/proj/tmp"), "ask")

    def test_r3_spellings(self):
        F, FORCE, HARD, RF = self.F, self.FORCE, self.HARD, self.RF
        for cmd in (f"if [ -d q ]; then rm {RF} q; fi", f"for f in a b; do rm {RF} $f; done", f'bash -c "rm {RF} q"',
                    "sh -lc 'git clean -xd" + "f'", f'eval "git reset {HARD}"', "x=`git clean -d" + F + "`",
                    f"echo $(git reset {HARD})", f"timeout 5 git reset {HARD}", f"nice -n 5 git reset {HARD}",
                    f"env -i FOO=1 git reset {HARD}", f"git push \\\n{FORCE} origin q", f"rm \\\n{RF} q",
                    "find . -execdir rm {} \\;", f"xargs -n 1 rm {RF}", f"sudo -u root git reset {HARD}",
                    f"{{ git reset {HARD}; }}", "git push origin " + "+HEAD:main", f"ls 2>&1 | head; git reset {HARD}"):
            self.assertEqual(self.judge(cmd), "ask", cmd)
        self.assertEqual(self.judge(f'bash -c "rm {RF} ~"'), "deny")
        for cmd in (f'git commit -m "T9: tidy guard\nrm {RF} build was slow"', "git commit -m 'subject\nsudo is not it'",
                    f'python3 tw.py ask "two lines\ngit clean -d{F} here?" --from ui',
                    'gh pr create --body "notes:\ngit branch -' + 'D old"', "find . -name '*.py' -exec grep -l rm {} +",
                    "ls &> /tmp/q", f"cat <<-EOF\n\tgit reset {HARD}\n\tEOF", f'echo "a <<EOF b"', "ls # rm -r q",
                    f"git commit -m \"$(cat <<'EOF'\nreset {HARD} and rm {RF} notes\nEOF\n)\""):
            self.assertIsNone(self.judge(cmd), cmd)
        self.assertEqual(self.judge(f'echo "a <<EOF b"\ngit reset {HARD}'), "ask")
        sys.path.insert(0, str(TW.parent))
        import tw
        t = time.time()
        tw.big_decision("echo " + "a" * 2_000_000)
        self.assertLess(time.time() - t, 3)

    def run_corpus(self, **env):
        here = Path(__file__).resolve().parent
        r = subprocess.run([sys.executable, str(here / "guard_corpus_run.py"), "--tw", str(TW), "--json"],
                           capture_output=True, text=True, env={**os.environ, **env})
        return r.returncode, json.loads(r.stdout)

    def test_adversarial_corpus(self):
        """T51: every bypass spelling found in review rounds, fed to the real hook; guard changes regress against it."""
        corpus = json.loads((Path(__file__).resolve().parent / "guard_corpus.json").read_text(encoding="utf-8"))
        cases = corpus["cases"]
        ids = [case["id"] for case in cases]
        self.assertEqual(len(ids), len(set(ids)))
        pinned = {f"bg-{i:03d}" for i in range(1, 143)} | {f"sens-{i:03d}" for i in range(1, 20)}
        self.assertLessEqual(pinned, set(ids))  # ids only grow; none is dropped or reused
        self.assertTrue(all(case["expect"] in {"ask", "deny", "allow"} for case in cases))
        self.assertTrue(all(case.get("policy") in {None, *corpus["policies"]} for case in cases))
        self.assertEqual(len({c["cmd"] for c in cases if not c.get("policy")}), len([c for c in cases if not c.get("policy")]))
        rc, out = self.run_corpus()
        self.assertEqual(out["cases"], len(cases))
        for item in out["results"]:
            with self.subTest(id=item["id"]):
                self.assertTrue(item["pass"], item)
        self.assertEqual((rc, out["failed"]), (0, 0))

    def test_corpus_runner_fails_when_the_guard_crashes(self):
        """A crash prints nothing, like "allow": the runner reads the guard's error log so allow cases fail too."""
        rc, out = self.run_corpus(TW_TEST_GUARD_FAULT="boom", TW_CORPUS_KEEP_FAULT="1")
        self.assertEqual((rc, out["failed"]), (1, out["cases"]))
        rc, out = self.run_corpus(TW_TEST_GUARD_FAULT="boom")  # a stray fault in the caller's env is scrubbed
        self.assertEqual((rc, out["failed"]), (0, 0))

    def test_other_tools_and_bad_input_pass(self):
        for raw in ("[]", '"x"', '{"tool_input": "x"}', '{"tool_input": {"command": ["a"]}}', "", "null",
                    '{"tool_input": {"command": null}}'):
            r = subprocess.run([sys.executable, str(TW), "bash-guard"], input=raw, capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout), (0, ""), raw)
        r = subprocess.run([sys.executable, str(TW), "bash-guard"], input=b"\xff\xfe{", capture_output=True)
        self.assertEqual((r.returncode, r.stdout), (0, b""))
        r = subprocess.run([sys.executable, str(TW), "bash-guard"], input="not json", capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        r = subprocess.run([sys.executable, str(TW), "bash-guard"], input=json.dumps(
            {"tool_name": "Edit", "tool_input": {"command": "sudo x"}}), capture_output=True, text=True)
        self.assertEqual(r.stdout, "")
        self.assertIsNone(self.judge('git commit -m "unbalanced'))


class WorktreeSettings(LeasesGit):
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


class ThreadBranchAndPrivateScratch(TwCase):
    """T32: `thread set --branch` no longer exits 0 while changing nothing; private scratch has a home."""

    def branch(self, t="db"):
        return self.state()["threads"][t]["branch"]

    def test_set_branch_while_planned(self):
        self.tw("thread", "add", "db")
        v = self.state().get("roster_version", 0)
        self.tw("thread", "set", "db", "--branch", "quix/zorblax")
        self.assertEqual(self.branch(), "quix/zorblax")
        self.assertGreater(self.state().get("roster_version", 0), v)

    def test_set_branch_refused_once_seated(self):
        self.tw("thread", "add", "db", "--session", "session_01Zorblax")
        r = self.tw("thread", "set", "db", "--branch", "quix/zorblax", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("branch is fixed", r.stderr)
        self.assertEqual(self.branch(), "tw/demo/db")
        self.assertNotEqual(self.tw("thread", "reseat", "db", "--session", "session_01Quixbar", "--branch", "x/y",
                                    ok=False).returncode, 0)

    def test_branch_names_checked_and_not_shared(self):
        self.tw("thread", "add", "db")
        for bad in ("-x", "a..b", "a b", "x/", "x.lock", ""):
            self.assertNotEqual(self.tw("thread", "set", "db", "--branch", bad, ok=False).returncode, 0, bad)
        self.assertNotEqual(self.tw("thread", "add", "ui", "--branch", "tw/demo/db", ok=False).returncode, 0)
        self.tw("thread", "add", "ui")
        self.assertNotEqual(self.tw("thread", "set", "ui", "--branch", "tw/demo/db", ok=False).returncode, 0)
        self.assertEqual(self.branch(), "tw/demo/db")

    def test_branch_clashes_and_bad_names(self):
        self.tw("thread", "add", "db")
        self.tw("thread", "add", "web")
        for bad in ("tw/demo/db", "TW/DEMO/DB", "tw/demo/db/x", "tw", "main", "origin/main", "HEAD", "a.lock/b",
                    "a;b", "x/.y"):
            r = self.tw("thread", "set", "web", "--branch", bad, ok=False)
            self.assertNotEqual(r.returncode, 0, bad)
        # ui's default name, before ui exists: refused (T38 M1), so ui can still be added later
        self.assertNotEqual(self.tw("thread", "set", "web", "--branch", "tw/demo/ui", ok=False).returncode, 0)
        self.tw("thread", "add", "ui")
        self.assertNotEqual(self.tw("thread", "add", "q3", "--branch", "main", ok=False).returncode, 0)
        self.tw("thread", "add", "s1", "--session", "session_01Zorblax")
        self.tw("thread", "set", "s1", "--branch", "tw/demo/s1")  # its own branch again: a no-op, allowed

    def test_branch_fixed_once_worktree_exists(self):
        subprocess.run(["git", "-C", str(self.dir), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(self.dir), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-q", "--allow-empty", "-m", "init"], check=True)
        self.tw("thread", "add", "db")
        self.addCleanup(shutil.rmtree, str(Path(self.state()["threads"]["db"]["worktree"]).parent), True)
        self.tw("worktree", "db")
        r = self.tw("thread", "set", "db", "--branch", "quix/zorblax", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("worktree", r.stderr)
        st = self.state()
        st["threads"]["db"]["branch"] = "quix/zorblax"  # a ledger edited by hand
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st))
        r = self.tw("worktree", "db", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("is on tw/demo/db", r.stderr)

    def test_t32_leftovers_namespace_base_remotes_missing_field(self):
        """T38: M1 tw/<project>/<other>, M3 tw/<project> itself, M2 inside the base and other remotes, M6 no field."""
        self.tw("thread", "add", "db")
        for bad in ("tw/demo/zorb", "tw/demo/web/x", "tw/demo", "TW/Demo", "main/x"):
            r = self.tw("thread", "set", "db", "--branch", bad, ok=False)
            self.assertNotEqual(r.returncode, 0, bad)
        self.tw("thread", "set", "db", "--branch", "tw/demo/db")  # its own default name is fine
        self.tw("thread", "set", "db", "--branch", "quix/db")
        subprocess.run(["git", "-C", str(self.dir), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(self.dir), "remote", "add", "upstream", "https://example.invalid/x.git"],
                       check=True)
        self.assertNotEqual(self.tw("thread", "set", "db", "--branch", "upstream/main", ok=False).returncode, 0)
        self.assertNotEqual(self.tw("thread", "set", "db", "--branch", "Upstream/x", ok=False).returncode, 0)
        subprocess.run(["git", "-C", str(self.dir), "remote", "add", "tw", "https://example.invalid/y.git"], check=True)
        self.tw("thread", "add", "ui")  # a remote named tw does not block the default branches
        stf = self.dir / ".teamwork" / "state.json"
        st = json.loads(stf.read_text())
        del st["threads"]["db"]["branch"]  # a ledger edited by hand
        stf.write_text(json.dumps(st))
        self.assertIn("tw/demo/db", self.tw("worker-prompt", "db").stdout)  # falls back to the default name
        self.tw("thread", "set", "db", "--branch", "quix/db2")
        self.assertEqual(self.branch(), "quix/db2")

    def test_t32_leftovers_detached_head_and_orphaned_branch(self):
        """T38: M4 a detached worktree is refused; M5 a removed worktree's branch is not orphaned by a rename."""
        subprocess.run(["git", "-C", str(self.dir), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(self.dir), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-q", "--allow-empty", "-m", "init"], check=True)
        self.tw("thread", "add", "web")
        wt = self.state()["threads"]["web"]["worktree"]
        self.addCleanup(shutil.rmtree, str(Path(wt).parent), True)
        self.tw("worktree", "web")
        subprocess.run(["git", "-C", wt, "checkout", "-q", "--detach"], check=True)
        r = self.tw("worktree", "web", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("detached", r.stderr)
        subprocess.run(["git", "-C", str(self.dir), "worktree", "remove", wt], check=True)
        r = self.tw("thread", "set", "web", "--branch", "quix/web", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("orphan", r.stderr)
        self.assertEqual(self.branch("web"), "tw/demo/web")
        self.tw("thread", "set", "web", "--session", "session_01Zorblax")
        r = self.tw("thread", "set", "web", "--branch", "quix/web", ok=False)
        self.assertIn("planned", r.stderr)  # a seated thread hears the real reason

    def test_private_scratch_is_ignored_and_documented(self):
        self.tw("thread", "add", "ui")
        p = self.tw("worker-prompt", "ui").stdout
        self.assertIn("**Private scratch never goes to `/tmp`**", p)
        self.assertIn(f"{self.state()['repo']['path']}/.teamwork/private/ui/tmp/", p)
        self.assertIn("never a `.teamwork/` inside your worktree", p)
        subprocess.run(["git", "-C", str(self.dir), "init", "-q"], check=True)
        f = self.dir / ".teamwork" / "private" / "ui" / "tmp" / "zorblax.txt"
        f.parent.mkdir(parents=True)
        f.write_text("x")
        r = subprocess.run(["git", "-C", str(self.dir), "check-ignore", "-q", str(f)])
        self.assertEqual(r.returncode, 0)  # ignored by .teamwork/.gitignore from init


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


class MergeRehearsal(LeasesGit):
    """T33: `merge` rehearses in a temp worktree; the live checkout only ever moves by fast-forward."""

    test_branch_diff_blocks = None

    def setUp(self):
        super().setUp()
        self.git("config", "user.name", "t")
        self.git("config", "user.email", "t@example.invalid")
        (self.proj / "zorb.py").write_text("a = 1\n")
        (self.proj / "check.sh").write_text("grep -q 'a = ' zorb.py\n")
        self.commit("base")
        self.tw("task", "add", "T1", "--owner", "ui", "--title", "quixbar",
                "--primary", "The user sees one quixbar result")
        proof = self.proj / "merge-primary-proof.txt"
        proof.write_text("the real artifact displays 1 quixbar result", encoding="utf-8")
        self.tw("task", "verify", "T1", "--seen", "--how", "open the real quixbar artifact",
                "--evidence", str(proof))
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "quix.py").write_text("q = 1\n")
        self.commit("T1: quixbar")
        self.git("checkout", "-q", "main")
        self.main0 = self.head()

    def head(self, ref="main"):
        return self.git("rev-parse", ref).stdout.strip()

    def m(self, *x, ok=True):
        return self.tw("merge", "tw/proj/ui", "--sha", self.head("tw/proj/ui"), *x, ok=ok)

    def merge(self, *x, ok=True):
        return self.m("--test", "sh check.sh", *x, ok=ok)

    def no_temp_worktrees(self):
        self.assertNotIn("tw-merge-", self.git("worktree", "list").stdout)

    def test_rehearsal_changes_nothing(self):
        r = json.loads(self.merge().stdout)
        self.assertEqual((r["checks"], r["applied"]), ("pass", False))
        self.assertEqual(self.head(), self.main0)
        self.assertFalse((self.proj / "quix.py").exists())
        self.no_temp_worktrees()

    def test_apply_fast_forwards_live_checkout(self):
        r = json.loads(self.merge("--apply").stdout)
        self.assertTrue(r["applied"])
        self.assertEqual(self.head(), r["merged"])
        self.assertTrue((self.proj / "quix.py").exists())  # the live files moved with the ref
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=no").stdout, "")
        self.assertIn('"ev": "merge"', (self.proj / ".teamwork" / "log.jsonl").read_text())
        self.no_temp_worktrees()

    def test_conflict_leaves_live_untouched(self):
        (self.proj / "quix.py").write_text("q = 2\n")
        self.commit("T2: other quix")
        before = self.head()
        r = self.merge("--apply", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("quix.py", r.stdout)
        self.assertEqual(self.head(), before)
        self.assertNotIn("<<<<<<<", (self.proj / "quix.py").read_text())
        self.no_temp_worktrees()

    def test_red_checks_leave_live_untouched(self):
        r = self.m("--test", "exit 3", "--apply", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("checks failed", r.stderr)
        self.assertEqual(self.head(), self.main0)
        self.no_temp_worktrees()

    def test_needs_a_check_and_a_squash_subject(self):
        self.assertIn("no check to run", self.m(ok=False).stderr)
        self.assertIn("--squash needs --message", self.merge("--squash", ok=False).stderr)
        self.tw("prefs", "--set", "check_cmd=sh check.sh")
        r = json.loads(self.m("--squash", "--message", "T1: quixbar (squash)", "--apply").stdout)
        self.assertEqual(self.git("log", "-1", "--format=%s %P").stdout.split()[0:2], ["T1:", "quixbar"])
        self.assertEqual(len(self.git("log", "-1", "--format=%P").stdout.split()), 1)  # one parent: a squash
        self.assertTrue(r["applied"])
        r = json.loads(self.m("--squash", "--message", "T1: again", "--no-test", "docs only").stdout)
        self.assertIn("nothing to merge", r["note"])  # an empty squash is not a failure

    def test_dirty_live_base_refused(self):
        (self.proj / "zorb.py").write_text("a = 9\n")
        r = self.merge("--apply", ok=False)
        self.assertIn("uncommitted changes", r.stderr)
        self.assertEqual(self.head(), self.main0)

    def test_base_not_checked_out_moves_ref(self):
        self.git("checkout", "-q", "-b", "elsewhere")
        r = json.loads(self.merge("--apply").stdout)
        self.assertEqual(self.head("main"), r["merged"])
        self.assertEqual(self.head("HEAD"), self.main0)  # the checkout on another branch did not move

    def test_only_the_reviewed_sha_merges(self):
        reviewed = self.head("tw/proj/ui")
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "late.py").write_text("z = 1\n")
        self.commit("T1: pushed after the review")
        self.git("checkout", "-q", "main")
        r = self.tw("merge", "tw/proj/ui", "--sha", reviewed[:9], "--test", "sh check.sh", "--apply", ok=False)
        self.assertIn("not the reviewed", r.stderr)
        self.assertIn("pushed after the review", json.loads(r.stdout)["extra_commits"][0])
        self.assertEqual(self.head(), self.main0)
        self.git("checkout", "-q", "-b", "stray")
        (self.proj / "stray.py").write_text("s = 1\n")
        self.commit("stray")
        self.git("checkout", "-q", "main")
        r = self.tw("merge", "tw/proj/ui", "--sha", self.head("stray"), "--test", "sh check.sh", ok=False)
        self.assertIn("not on tw/proj/ui", r.stderr)
        self.assertIn("no such commit", self.tw("merge", "tw/proj/ui", "--sha", "abcdef0", "--no-test", "x",
                                                ok=False).stderr)
        for name in ("tw/proj/ui", "stray", "HEAD", f"{reviewed}~0", "main", " "):  # names never stand for a review
            r = self.tw("merge", "tw/proj/ui", "--sha", name, "--no-test", "x", ok=False)
            self.assertIn("commit id", r.stderr, name)
        self.git("branch", "deadbeef", "stray")  # a branch named like hex is still not that commit
        self.assertIn("no such commit", self.tw("merge", "tw/proj/ui", "--sha", "deadbeef", "--no-test", "x",
                                                ok=False).stderr)
        self.assertTrue(json.loads(self.tw("merge", "tw/proj/ui", "--sha", f" {self.head('tw/proj/ui').upper()} ",
                                           "--no-test", "x").stdout)["merged"])
        self.assertNotEqual(self.tw("merge", "tw/proj/ui", "--no-test", "x", ok=False).returncode, 0)  # --sha required

    def test_holder_switching_branch_during_checks_refused(self):
        r = self.merge_with_check(f"git -C {self.proj} checkout -q -b zorblax", ok=False)
        self.assertRegex(r.stderr, "changed meanwhile|no longer on main")
        self.assertEqual(self.head(), self.main0)
        self.assertEqual(self.head("zorblax"), self.main0)  # the other branch was not fast-forwarded
        self.assertNotIn('"ev": "merge"', (self.proj / ".teamwork" / "log.jsonl").read_text())
        self.no_temp_worktrees()

    def test_base_checked_out_during_checks_refused(self):
        self.git("checkout", "-q", "-b", "side")
        r = self.merge_with_check(f"git -C {self.proj} checkout -q main", ok=False)
        self.assertIn("changed meanwhile", r.stderr)
        self.assertEqual(self.head(), self.main0)
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=no").stdout, "")

    def test_holder_changing_during_checks_refused(self):
        for cmd, why in ((f"echo junk > {self.proj / 'zorb.py'}", "uncommitted changes"),
                         (f"git -C {self.proj} commit -q --allow-empty -m moved", "moved to")):
            r = self.merge_with_check(cmd, ok=False)
            self.assertIn(why, r.stderr)
            self.assertFalse(json.loads(r.stdout)["applied"])
            self.git("checkout", "-q", "--", "zorb.py")
            self.no_temp_worktrees()
        self.assertNotIn('"ev": "merge"', (self.proj / ".teamwork" / "log.jsonl").read_text())

    def test_rebase_started_during_checks_refused(self):
        r = self.merge_with_check(f"git -C {self.proj} rebase -q --exec false HEAD~1 >/dev/null 2>&1; true", ok=False)
        self.assertRegex(r.stderr, "no longer on main|changed meanwhile")
        self.git("rebase", "--abort")
        self.assertEqual(self.head(), self.main0)

    def test_ignored_local_file_in_the_way_refused(self):
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "quixbar.env").write_text("from the branch\n")
        self.git("add", "-f", "quixbar.env")
        self.commit("T1: env")
        self.git("checkout", "-q", "main")
        (self.proj / ".git" / "info" / "exclude").write_text("*.env\n")
        (self.proj / "quixbar.env").write_text("MY LOCAL\n")
        r = self.merge("--apply", ok=False)
        self.assertIn("quixbar.env", json.loads(r.stdout)["untracked_in_the_way"])
        self.assertEqual((self.proj / "quixbar.env").read_text(), "MY LOCAL\n")

    def test_sigterm_during_checks_cleans_up(self):
        flag = self.proj.parent / "started"
        p = subprocess.Popen([sys.executable, str(TW), "-C", str(self.proj), "merge", "tw/proj/ui", "--sha",
                              self.head("tw/proj/ui"), "--test", f"touch {flag}; sleep 30", "--apply"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(300):
            if flag.exists():
                break
            time.sleep(0.05)
        p.terminate()
        out, err = p.communicate(timeout=20)
        self.assertIn("nothing changed", err)
        self.assertEqual(self.head(), self.main0)
        self.no_temp_worktrees()
        self.assertEqual([x for x in Path(tempfile.gettempdir()).glob("tw-merge-*/pid")
                          if x.read_text() == str(p.pid)], [])  # its temp folder is gone too

    def test_sigterm_during_fast_forward_waits_for_it(self):
        flag = self.proj.parent / "ff-started"
        hook = self.proj / ".git" / "hooks" / "post-merge"
        hook.write_text(f'#!/bin/sh\ncase "$(pwd -P)" in "{self.proj.resolve()}") touch "{flag}"; sleep 2;; esac\n')
        hook.chmod(0o755)
        p = subprocess.Popen([sys.executable, str(TW), "-C", str(self.proj), "merge", "tw/proj/ui", "--sha",
                              self.head("tw/proj/ui"), "--test", "sh check.sh", "--apply"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(400):
            if flag.exists():
                break
            time.sleep(0.05)
        self.assertTrue(flag.exists())
        p.terminate()
        out, err = p.communicate(timeout=20)
        self.assertNotIn("nothing changed", err)
        self.assertTrue(json.loads(out)["applied"])  # the report is printed before the waiting signal acts
        self.assertNotEqual(self.head(), self.main0)
        self.assertTrue((self.proj / "quix.py").exists())
        self.assertIn('"ev": "merge"', (self.proj / ".teamwork" / "log.jsonl").read_text())
        self.no_temp_worktrees()

    def test_t42_folder_and_file_prefix_clashes_refused(self):
        """T42: a local ignored folder where the merge adds a file, and a local ignored file where it adds a folder."""
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "bld").write_text("tracked file\n")
        (self.proj / "cfg").mkdir()
        (self.proj / "cfg" / "a").write_text("tracked\n")
        self.git("add", "-f", "bld", "cfg/a")
        self.commit("T1: bld and cfg")
        self.git("checkout", "-q", "main")
        (self.proj / ".git" / "info" / "exclude").write_text("bld\ncfg\n")
        (self.proj / "bld").mkdir()
        (self.proj / "bld" / "x").write_text("LOCAL\n")
        (self.proj / "cfg").write_text("LOCAL\n")
        r = self.merge("--apply", ok=False)
        self.assertEqual(sorted(json.loads(r.stdout)["untracked_in_the_way"]), ["bld/x", "cfg"])
        self.assertEqual(((self.proj / "bld" / "x").read_text(), (self.proj / "cfg").read_text()), ("LOCAL\n",) * 2)

    def test_t42_non_ascii_paths_in_the_way(self):
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "中文").write_text("tracked\n")
        self.commit("T1: zh")
        self.git("checkout", "-q", "main")
        (self.proj / "中文").mkdir()
        (self.proj / "中文" / "k").write_text("LOCAL\n")
        r = self.merge("--apply", ok=False)
        self.assertEqual(json.loads(r.stdout)["untracked_in_the_way"], ["中文/k"])
        self.assertEqual(self.head(), self.main0)

    def test_t42_ignored_file_appearing_during_checks_refused(self):
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "k.secret").write_text("from the branch\n")
        self.git("add", "-f", "k.secret")
        self.commit("T1: secret")
        self.git("checkout", "-q", "main")
        (self.proj / ".git" / "info" / "exclude").write_text("*.secret\nrace.sh\n")
        r = self.merge_with_check(f"echo LOCAL > {self.proj / 'k.secret'}", ok=False)
        self.assertIn("appeared meanwhile", r.stderr)
        self.assertEqual((self.proj / "k.secret").read_text(), "LOCAL\n")
        self.assertEqual(self.head(), self.main0)

    def test_t42_check_timeout_and_no_stdin(self):
        stf = self.proj / ".teamwork" / "state.json"
        st = json.loads(stf.read_text())
        st["prefs"]["check_timeout_minutes"] = 0.02  # about a second
        stf.write_text(json.dumps(st))
        r = self.m("--test", "sleep 30", ok=False)
        self.assertIn("were stopped", r.stderr)
        self.no_temp_worktrees()
        self.assertEqual(json.loads(self.m("--test", "cat").stdout)["checks"], "pass")  # stdin is empty, not ours
        self.assertNotEqual(self.tw("prefs", "--set", "check_timeout_minutes=x", ok=False).returncode, 0)

    def test_t42_prunable_base_worktree_is_nobody(self):
        self.git("checkout", "-q", "-b", "zorb-side")
        side = Path(tempfile.mkdtemp()) / "m"
        self.git("worktree", "add", "-q", str(side), "main")
        shutil.rmtree(side)  # its folder is gone: the worktree is prunable
        r = json.loads(self.merge("--apply").stdout)
        self.assertTrue(r["applied"])  # the first run, not after a misleading "changed meanwhile"
        self.assertEqual(self.head(), r["merged"])

    def test_t42_live_or_young_leftovers_are_kept(self):
        tmp = Path(tempfile.mkdtemp(prefix="tw-merge-"))
        self.git("worktree", "add", "-q", "--detach", str(tmp / "wt"), "main")
        (tmp / "pid").write_text("1")  # alive, not ours to signal: PermissionError means running
        young = Path(tempfile.mkdtemp(prefix="tw-merge-"))
        self.git("worktree", "add", "-q", "--detach", str(young / "wt"), "main")  # no pid file, just made
        self.merge()
        self.assertTrue((tmp / "wt").exists() and (young / "wt").exists())
        for w in (tmp, young):
            self.git("worktree", "remove", "--force", str(w / "wt"))

    def test_killed_run_leftover_is_cleaned_next_time(self):
        tmp = Path(tempfile.mkdtemp(prefix="tw-merge-"))
        self.git("worktree", "add", "-q", "--detach", str(tmp / "wt"), "main")
        (tmp / "pid").write_text("999999999")  # no such process
        self.merge()
        self.assertFalse(tmp.exists())
        self.no_temp_worktrees()

    def merge_with_check(self, cmd, ok=True):
        (self.proj / "race.sh").write_text(cmd + "\n")  # untracked: not in the rehearsal, so call it by path
        return self.m("--test", f"sh {self.proj / 'race.sh'}", "--apply", ok=ok)

    def test_check_cmd_is_text_and_no_test_wins(self):
        self.tw("prefs", "--set", "check_cmd=true")
        self.assertEqual(json.loads((self.proj / ".teamwork" / "state.json").read_text())["prefs"]["check_cmd"], "true")
        self.tw("prefs", "--set", "check_cmd=exit 5")
        r = json.loads(self.m("--no-test", "docs only").stdout)
        self.assertEqual(r["checks"], "none (docs only)")

    def test_untracked_file_in_the_way_refused_before_checks(self):
        (self.proj / "quix.py").write_text("mine\n")
        r = self.m("--test", "exit 7", "--apply", ok=False)
        self.assertIn("untracked files", r.stderr)
        self.assertEqual(json.loads(r.stdout)["untracked_in_the_way"], ["quix.py"])
        self.assertEqual((self.proj / "quix.py").read_text(), "mine\n")
        self.assertEqual(self.head(), self.main0)


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
        doc = (Path(__file__).resolve().parents[1] / "references" / "messages.md").read_text()
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


class Quota(TwCase):
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


class FeatureLedger(TwCase):
    def test_feature_add_set_list_and_rebuild(self):
        self.tw("req", "add", "need feature")
        self.assertNotEqual(self.tw("feature", "add", ok=False).returncode, 0)
        self.tw("feature", "add", "Feature one", "--phase", "P1", "--req", "R1")
        self.assertNotEqual(self.tw("feature", "set", "F1", "--status", "landed", ok=False).returncode, 0)
        self.tw("feature", "set", "F1", "--status", "landed", "--evidence", "commit abc")
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


class T71GuardAndSuccession(TwCase):
    def _load_tw(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location("tw_t71_shape", TW); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        return mod

    def test_generated_fixture_signatures_and_byte_tamper(self):
        import importlib.util
        fixture_dir=Path(__file__).parent/"fixtures"
        generator=fixture_dir/"regenerate_t71.py"
        spec=importlib.util.spec_from_file_location("t71_fixture_signatures",generator)
        mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        self.assertEqual(set(mod.SIGNATURE_MANIFEST),{
            "bridge-transcript-cse_fixture-remote-session.jsonl","t71_real_candidate.jsonl",
            "t71_real_tool_results.jsonl","t71_old_thread.jsonl","t79_hook_start_session.jsonl",
            "t79_hook_create_session.jsonl","t79_nested_desktop_sessions.jsonl"})
        for name in mod.SIGNATURE_MANIFEST:
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
        fixtures=Path(__file__).parent/"fixtures"
        names={"bridge-transcript-cse_fixture-remote-session.jsonl","t71_real_candidate.jsonl",
               "t71_real_tool_results.jsonl","t71_old_thread.jsonl","t79_hook_start_session.jsonl",
               "t79_hook_create_session.jsonl","t79_nested_desktop_sessions.jsonl"}
        texts=[(fixtures/name).read_text(encoding="utf-8") for name in names]
        home=str(Path.home()).casefold()
        username=getpass.getuser().casefold()
        host=socket.gethostname().casefold()
        private_dir=Path.home()/"Claude-B"/"teamwork-for-claude-code"/".teamwork"
        if not (private_dir/"PRIVATE.md").is_file():
            private_dir=Path(__file__).resolve().parents[1]/".teamwork"
        private_terms,_,_=tw.load_private(private_dir)
        private_terms.extend(x for x in (home,username) if x)
        patterns=[re.compile(re.escape(term),re.I) for term in private_terms if term]
        patterns.extend((re.compile(re.escape(host),re.I), re.compile(r"https?://\S+",re.I),
                         re.compile(r"~/\S+",re.I), re.compile(r"(?i)\.pem\b"),
                         re.compile(r"/Users/[^/\s]+",re.I), re.compile(r"session_01",re.I),
                         re.compile(r"env_01",re.I), re.compile(r"cse_01",re.I)))
        for text in texts:
            for pattern in patterns:
                self.assertIsNone(pattern.search(text), "fixture privacy marker detected")

    def test_nested_metadata_fixture_preserves_real_lead_id_relationship(self):
        rows=[json.loads(line) for line in (Path(__file__).parent/"fixtures"/"t79_nested_desktop_sessions.jsonl").read_text(encoding="utf-8").splitlines()]
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
        fixtures=Path(__file__).parent/"fixtures"
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
        fixture=Path(__file__).parent/"fixtures"/"t71_real_candidate.jsonl"
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
        fixture=Path(__file__).parent/"fixtures"/"t71_real_candidate.jsonl"
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
        old_rows=[json.loads(line) for line in (Path(__file__).parent/"fixtures"/"t71_old_thread.jsonl").read_text(encoding="utf-8").splitlines()]
        for row in old_rows: row["timestamp"]=(freeze-dt.timedelta(seconds=1)).isoformat()
        old=self.dir/"old.jsonl"; old.write_text("\n".join(json.dumps(row) for row in old_rows)+"\n",encoding="utf-8")
        config=self.dir/"claude-config"; project=config/"projects"/"fixture"; local="00000000-0000-4000-8000-000000000001"
        child=project/local/"subagents"; child.mkdir(parents=True)
        bridge=Path(__file__).parent/"fixtures"/"bridge-transcript-cse_fixture-remote-session.jsonl"
        shutil.copy2(bridge,config/"bridge-transcript-cse_fixture-lead.jsonl")
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
        self.tw("succ","pack","HO1","--from-transcript",str(Path(__file__).parent/"fixtures"/"t71_old_thread.jsonl"))
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
        transcript=Path(__file__).parent/"fixtures"/"t71_old_thread.jsonl"
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
        rows=[json.loads(x) for x in (Path(__file__).parent/"fixtures"/"t71_old_thread.jsonl").read_text().splitlines()]
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
        fixture=Path(__file__).parent/"fixtures"/"t71_old_thread.jsonl"
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
        rows=[json.loads(x) for x in (Path(__file__).parent/"fixtures"/"t71_old_thread.jsonl").read_text().splitlines()]
        rows[0].pop("timestamp",None)
        transcript=self.dir/"old-without-time.jsonl"
        transcript.write_text("\n".join(json.dumps(x) for x in rows)+"\n",encoding="utf-8")
        failed=self.tw("succ","retire-check","HO1","--caller","session_fixture-lead","--transcript",str(transcript),ok=False)
        self.assertEqual(failed.returncode,1)
        self.assertIn("rows lack timestamps; unverified",failed.stderr)
        self.assertNotIn("retire_ticket",self.state()["handovers"]["HO1"])

    def test_real_shape_fixture_handover_through_archive_guard(self):
        fixtures=Path(__file__).parent/"fixtures"
        candidate=fixtures/"t71_real_candidate.jsonl"; old=fixtures/"t71_old_thread.jsonl"
        self.tw("thread","add","demo","--session","old-seat")
        self.fixture_old_session_start()
        self.tw("succ","start","demo","--reason","fixture replacement")
        self.tw("succ","candidate","HO1","candidate-seat")
        self.tw("succ","freeze","HO1","--stopped","old-seat stopped")
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
        old_rows=Path(__file__).parent/"fixtures"/"t71_old_thread.jsonl"
        config=self.dir/"claude-config"; config.mkdir()
        stale_result=self.tw("succ","retire-check","HO1","--caller","session_fixture-lead","--transcript",str(old_rows),"--from-transcript",str(connected),ok=False)
        self.assertIn("not newer than seat step",stale_result.stderr)
        wrong_owner=self.tw("succ","retire-check","HO1","--caller","session_fixture-lead","--transcript",str(candidate),"--from-transcript",str(connected),"--config-dir",str(config),ok=False)
        self.assertNotEqual(wrong_owner.returncode,0)
        self.assertIn("belongs to another session; unverified",wrong_owner.stderr)
        self.assertNotIn("retire_ticket",self.state()["handovers"]["HO1"])
        connected.write_text("\n".join(json.dumps(x) for x in results)+"\n",encoding="utf-8")
        self.tw("succ","retire-check","HO1","--caller","session_fixture-lead","--transcript",str(old),"--from-transcript",str(connected))
        cfg=config
        local="00000000-0000-4000-8000-000000000001"
        bridge=Path(__file__).parent/"fixtures"/"bridge-transcript-cse_fixture-remote-session.jsonl"
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
        self.tw("succ","pack","HO1","--from-transcript",str(Path(__file__).parent/"fixtures"/"t71_old_thread.jsonl"))
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
        failed_grade=self.tw("succ","grade","HO1","--answers",str(wrong),"--caller","session_fixture-lead","--from-transcript",str(Path(__file__).parent/"fixtures"/"t71_real_candidate.jsonl"),ok=False)
        self.assertNotEqual(failed_grade.returncode,0)
        self.assertEqual(self.state()["handovers"]["HO1"]["phase"],"quiz")
        self.assertEqual(json.loads(self.tw("succ","status","HO1","--json").stdout)[0]["next"],"grade")

    def test_verify_self_lists_items_and_todo_pack_is_rejected(self):
        self.tw("thread","add","demo","--session","old-seat")
        self.fixture_old_session_start()
        self.tw("succ","start","demo","--reason","fixture replacement")
        self.tw("succ","candidate","HO1","candidate-seat")
        self.tw("succ","freeze","HO1","--stopped","old-seat stopped")
        self.tw("succ","pack","HO1","--from-transcript",str(Path(__file__).parent/"fixtures"/"t71_old_thread.jsonl"))
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
        self.tw("succ","pack","HO1","--from-transcript",str(Path(__file__).parent/"fixtures"/"t71_old_thread.jsonl"))
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
        bridge=Path(__file__).parent/"fixtures"/"bridge-transcript-cse_fixture-remote-session.jsonl"
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
        tr=Path(__file__).parent/"fixtures"/"t71_real_tool_results.jsonl"
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


class SiblingProjects(TwCase):
    def test_broken_sibling_state_does_not_break_opening(self):
        sibling = Path(tempfile.mkdtemp(dir=self.dir.parent)) / ".teamwork"
        sibling.mkdir()
        for broken in ("[]", '{"prefs": []}', '{"prefs": {"policy_said": []}}'):
            (sibling / "state.json").write_text(broken, encoding="utf-8")
            self.assertEqual(self.tw("opening", "--json").returncode, 0)


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


class QuotaIdle(Quota):
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

    def test_zh_hint(self):
        self.tw("prefs", "--set", "language=zh"); self.tw("prefs", "--set", "quota_trigger_id=trig_1")
        self.assertIn("关掉额度定时", self.tw("ready").stderr)


class T57Round3Core(Quota):
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
        config = self.dir / "claude-config"; config.mkdir()
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
        transcript = self.dir / ".teamwork" / "tr.jsonl"
        transcript.write_text(json.dumps({"type": "user", "timestamp": self.env["TW_NOW"], "origin": {"kind": "human"},
                                          "message": {"content": "导出一定要用 CSV 格式，不要用 xlsx 表格"}}) + "\n", encoding="utf-8")
        self.env["TW_NOW"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=2)).isoformat(timespec="seconds")
        self.tw("succ", "start", "ui", "--reason", "把 ui 换掉")
        self.tw("succ", "candidate", "HO1", " new1 ")
        self.tw("screen","ask","ui","--session","new1")
        self.tw("screen","record","SC1","--scores",",".join(f"P{i}=2" for i in range(1,7)))
        self.tw("screen","clear-msg","SC1")
        self.tw("succ","screened","HO1","new1","--screen","SC1")
        self.tw("succ","candidate","HO1","new1","--cleared")
        self.tw("succ", "freeze", "HO1", "--stopped", "get_session old1: idle")
        self.tw("succ", "pack", "HO1", "--transcript", str(transcript))
        missing = self.tw("succ", "seat", "HO1", "new1", ok=False)
        self.assertIn("verify", missing.stderr)
        head = subprocess.run(["git", "-C", str(self.dir), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        receipt = self.dir / ".teamwork" / "rc.json"
        fact="导出一定要用 CSV 格式，不要用 xlsx 表格"
        receipt.write_text(json.dumps({"tasks": [], "branch": "tw/demo/ui", "head": head, "rules": [1], "requests": [],
            "only_read_fact":fact,"unresolved":[],"sections":"## Rules\nNo additional rule changes.\n## Only-known-after-reading fact / 只有读过才知道的一条\n"+fact+"\n## Unresolved / 悬而未决\nnone"}))
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
        candidate_transcript=self.dir/".teamwork"/"candidate.jsonl"
        quiz_transcript(candidate_transcript,items[0]," ".join(answers.read_text(encoding="utf-8").splitlines()))
        self.tw("succ","grade","HO1","--answers",str(answers),"--caller","session_lead","--from-transcript",str(candidate_transcript))
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
        packed = self.tw("succ", "pack", "HO1", "--transcript", str(transcript)).stdout
        package = Path(packed.split(" · ")[0]).parent
        for path in package.rglob("*"):
            if path.is_file(): self.assertNotIn("SECRETANSWER", path.read_text(encoding="utf-8", errors="replace"), str(path))

    def test_init_force_uses_current_revision(self):
        self.tw("prefs", "--set", "language=en")
        self.tw("init", "demo", "--force", "--goal", "g2")
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
        real=tw._with_save_lock
        with patch.object(tw,"_with_save_lock",wraps=real) as lock:
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
        self.env["TW_GRADER_CMD"]="sleep 3; echo P1=2 P2=2 P3=2 P4=2 P5=2 P6=2"
        self.tw("prefs","--set","screen_autograde=true")
        started=time.monotonic(); ready=self.tw("ready")
        self.assertLess(time.monotonic()-started,1.0,ready.stderr)
        self.assertIn("已在后台给 1 个体检打分",ready.stderr)
        self.tw("task","add","T9","--title","during grading","--owner","ui")
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




class PrimaryOutcome(TwCase):
    """R71/G8 primary outcome gates, reporting and real subprocess behavior."""

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
        self.assertFalse(tw._primary_suspect("simulator screenshot", "output shows 12 rows"))
        self.assertTrue(tw._primary_suspect("no mock, but used sinon", "shows 12 rows"))
        self.assertFalse(tw._primary_suspect("no mock", "output shows 12 rows"))
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
        self.assertTrue(tw._primary_suspect("fixture run", "output shows 12 records"))
        self.assertTrue(tw._primary_suspect("dummy data", "output shows 12 records"))
        self.assertTrue(tw._primary_suspect("simulated browser", "output shows 12 records"))
        self.assertTrue(tw._primary_suspect("placeholder screen", "output shows 12 records"))
        self.assertTrue(tw._primary_suspect("样例数据", "显示 12 条记录"))
        self.assertTrue(tw._primary_suspect("假的运行环境", "显示 12 条记录"))
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


class T79NoNestedDesktopThreads(TwCase):
    def _fixture(self, name):
        return json.loads((Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8").splitlines()[0])

    def _guard(self, script, event):
        return subprocess.run([sys.executable, str(script), "bash-guard"], input=json.dumps(event),
                              capture_output=True, text=True, cwd=self.dir,
                              env={**os.environ, "TW_GUARD_LOG": str(self.dir / "guard.log")})

    def test_tool_name_is_normalized_for_start_and_archive_routes(self):
        start = self._fixture("t79_hook_start_session.jsonl")
        start["cwd"] = str(self.dir)
        state = self.state()
        state["lead"]["session_id"] = "session_live_lead"
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        variants = (" mcp__ccd_session__start_session", "MCP__CCD_SESSION__START_SESSION")
        for tool_name in variants:
            with self.subTest(tool_name=tool_name):
                start["tool_name"] = tool_name
                decision = json.loads(self._guard(TW, start).stdout)["hookSpecificOutput"]
                self.assertEqual(decision["permissionDecision"], "deny")
        archive = {"cwd": str(self.dir), "session_id": "caller",
                   "tool_input": {"session_id": "session_live_lead"}}
        variants = (" mcp__claude-code-remote__archive_session",
                    "MCP__CLAUDE-CODE-REMOTE__ARCHIVE_SESSION")
        for tool_name in variants:
            with self.subTest(tool_name=tool_name):
                archive["tool_name"] = tool_name
                decision = json.loads(self._guard(TW, archive).stdout)["hookSpecificOutput"]
                self.assertEqual(decision["permissionDecision"], "deny")

    def test_real_start_session_shape_denied_only_inside_teamwork_project(self):
        event = self._fixture("t79_hook_start_session.jsonl")
        event["cwd"] = str(self.dir)
        decision = json.loads(self._guard(TW, event).stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("create_session", decision["permissionDecisionReason"])
        self.assertIn("Remote Control", decision["permissionDecisionReason"])
        outside = self.dir.parent / (self.dir.name + "-outside")
        outside.mkdir()
        event["cwd"] = str(outside)
        self.assertEqual(self._guard(TW, event).stdout, "")

    def test_real_create_session_shape_is_unaffected(self):
        event = self._fixture("t79_hook_create_session.jsonl")
        event["cwd"] = str(self.dir)
        self.assertEqual(self._guard(TW, event).stdout, "")

    def test_nested_metadata_report_matches_lead_and_skips_archived(self):
        import copy
        rows = [json.loads(line) for line in (Path(__file__).parent / "fixtures" / "t79_nested_desktop_sessions.jsonl").read_text().splitlines()]
        child, parent = rows
        child["cwd"] = str(self.dir / "nested")
        parent["cwd"] = str(self.dir)
        parent["bridgeSessionIds"] = ["lead-remote"]
        state = self.state()
        state["lead"]["session_id"] = "session_lead-remote"
        metadata = self.dir / "metadata" / "account" / "org"
        metadata.mkdir(parents=True)
        (self.dir / "nested").mkdir()
        (metadata / "local_child.json").write_text(json.dumps(child), encoding="utf-8")
        (metadata / "local_parent.json").write_text(json.dumps(parent), encoding="utf-8")
        os.environ["TEAMWORK_CLAUDE_SESSIONS_DIR"] = str(self.dir / "metadata")
        try:
            found, gaps = tw._nested_session_report(self.dir / ".teamwork", state)
            self.assertEqual((gaps, [row["parent_is_lead"] for row in found]), (0, [True]))
            (self.dir / ".teamwork" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            ready_env = {**os.environ, "TEAMWORK_DIR": str(self.dir / ".teamwork"),
                         "TEAMWORK_CLAUDE_SESSIONS_DIR": str(self.dir / "metadata")}
            ready = subprocess.run([sys.executable, str(TW), "ready"], capture_output=True, text=True,
                                   cwd=self.dir, env=ready_env)
            self.assertIn("local_00000000-0000-4000-8000-000000000001", ready.stderr)
            self.assertIn("别归档 lead", ready.stderr)
            other_parent = dict(parent, sessionId="local_other", bridgeSessionIds=["other-remote"])
            other_child = dict(child, sessionId="local_other_child", spawnedFrom={"sessionId": "local_other"})
            archived = dict(child, sessionId="local_archived", isArchived=True)
            for name, value in (("local_other.json", other_parent), ("local_other_child.json", other_child),
                                ("local_archived.json", archived)):
                (metadata / name).write_text(json.dumps(value), encoding="utf-8")
            found, _ = tw._nested_session_report(self.dir / ".teamwork", state)
            self.assertEqual({row["session_id"] for row in found}, {child["sessionId"], "local_other_child"})
            self.assertEqual({row["parent_is_lead"] for row in found}, {True, False})
            state["lead"]["session_id"] = "session_unrelated"
            (self.dir / ".teamwork" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            ready = subprocess.run([sys.executable, str(TW), "ready"], capture_output=True, text=True,
                                   cwd=self.dir, env=ready_env)
            self.assertIn("local_00000000-0000-4000-8000-000000000001", ready.stderr)
            self.assertNotIn("别归档 lead", ready.stderr)
            (metadata / "local_broken.json").write_text("{", encoding="utf-8")
            self.assertEqual(tw._nested_session_report(self.dir / ".teamwork", state)[1], 1)
        finally:
            os.environ.pop("TEAMWORK_CLAUDE_SESSIONS_DIR", None)

    def test_install_updates_legacy_matcher(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import install
        (self.dir / "tmpd").mkdir()
        project = self.dir / "project"
        cfg = self.dir / "cfg"
        settings = project / ".claude" / "settings.local.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"hooks": {"PreToolUse": [{
            "matcher": "Bash,mcp__claude-code-remote__archive_session",
            "hooks": [{"type": "command", "command": f'"{sys.executable}" "{cfg / "skills/teamwork/scripts/tw.py"}" bash-guard || true'}]}]}}), encoding="utf-8")
        result = subprocess.run([sys.executable, str(Path(__file__).parent / "install.py"), "--link", "--apply",
                                 "--config-dir", str(cfg), "--project", str(project)],
                                capture_output=True, text=True, env={**os.environ, "TMPDIR": str(self.dir / "tmpd"),
                                                                       "CLAUDE_CONFIG_DIR": str(cfg)})
        self.assertEqual(result.returncode, 0, result.stderr)
        hooks = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
        matchers = [group["matcher"] for group in hooks]
        self.assertIn(install.BASH_GUARD_MATCHER, matchers)
        self.assertNotIn("Bash,mcp__claude-code-remote__archive_session", matchers)


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
        r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "install.py"), "--config-dir", str(cfg)],
                           capture_output=True, text=True, env={**os.environ, "HOME": str(home), "TMPDIR": str(scratch)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("inside the home folder", r.stdout)
        self.assertIn("would be added to additionalDirectories", r.stdout)
        self.assertIn("additionalDirectories: adding /tmp", r.stdout)
        home_root = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "install.py"), "--config-dir", str(cfg / "home-root")],
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
        r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "install.py"), "--config-dir", str(cfg),
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
        skill = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text()
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
