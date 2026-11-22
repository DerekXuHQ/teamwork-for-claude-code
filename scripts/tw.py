#!/usr/bin/env python3
"""teamwork ledger: the single source of truth for one teamwork project.

State lives in <project folder>/.teamwork/ :
  state.json       machine state: lead, environments, threads, tasks, prefs, questions
                   (written ONLY by the lead, always through this script)
  BOARD.md         human view of state.json, regenerated on every write
  INSTRUCTIONS.md  project instructions: sent to every new thread and to the lead (<=16k chars)
  MEMORY.md        project memory: facts, decisions, conventions (lead edits; threads propose)
  SHARE.md         the user's phone view, a snapshot written by `board --share` (IL-13)
  log.jsonl        append-only event log

Stdlib only. `tw.py <command> -h` for details.
"""

import argparse
import datetime as dt
import contextlib
import functools
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))  # quota.py sits next to tw.py (also under python -P)
try:  # only `quota` needs it: a copy without quota.py must not break the hooks (context, bash-guard)
    import quota as quota_reader  # noqa: E402
    QUOTA_DEFAULTS = dict(quota_reader.DEFAULTS)
except ImportError:
    quota_reader = None
    QUOTA_DEFAULTS = {"quota_burn_at": 70, "quota_lead_only_at": 90, "quota_pre_reset_hours": 36,
                      "quota_pre_reset_fable_max": 80, "quota_fable_done_at": 95, "quota_5h_tight": 80,
                      "quota_gpt_tight": 90}

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_DIR / "templates"

# Thread statuses mirror the Projects overview groups.
THREAD_STATUSES = {
    "planned": "Suggested (no session yet)",
    "starting": "created, first turn not finished",
    "working": "Working",
    "waiting": "Waiting on you (question, permission prompt, or failure)",
    "review": "Ready for review",
    "landing": "Landing (approved, being merged)",
    "idle": "Idle (finished its turn, nothing pending)",
    "resolved": "Resolved",
    "archived": "archived session",
}
TASK_STATUSES = [
    "planned",
    "approved",
    "dispatched",
    "working",
    "blocked",
    "review",
    "done",
    "recalled",
    "dropped",
]
CONTEXT_LIMIT = 9000  # SessionStart additionalContext is capped at 10k chars
NAME_RE = r"[a-z0-9][a-z0-9-]{0,23}"


def now():
    # TW_NOW (ISO time) fakes the clock for tests.
    fake = os.environ.get("TW_NOW")
    t = dt.datetime.fromisoformat(fake) if fake else dt.datetime.now()
    return t.astimezone().isoformat(timespec="seconds")


# Board / status labels follow prefs.language (en fallback).
LABELS = {
    "en": {
        "planned": "Suggested (no session yet)", "starting": "Created, first turn not finished",
        "working": "Working", "waiting": "Waiting on you (question, permission prompt, or failure)",
        "review": "Ready for review", "landing": "Landing (approved, being merged)",
        "idle": "Idle (finished its turn, nothing pending)", "resolved": "Resolved",
        "stuck": "Possibly stuck (working, no report for over {n} min)", "no_report": "no report for {m} min",
        "overview": "Overview", "questions": "Waiting on the user", "roster": "Roster", "plan": "Plan",
        "requests": "Your requests (not closed)", "unreported": "Delivered, not yet reported to the user",
        "req_open": "open", "req_held": "held: waiting for your material", "req_dispatched": "dispatched", "req_delivered": "delivered (report it!)",
        "req_reported": "reported", "req_closed": "closed", "req_proposed": "proposed (waiting for a yes)",
        "req_dropped": "dropped", "req_waiting_user": "waiting for your decision", "req_wont": "won't do (reason)", "req_landed": "landed (evidence)", "req_remark": "remark", "remark_heading": "Remarks (not dispatched)", "default_heading": "Defaulted decisions", "promise_heading": "Open promises", "default_prefix": "Defaulted: ", "can_change": " (can change)", "rcpt_remark": "recorded as a remark {r}, not dispatched", "rcpt_given": "recorded as {r}, given to {who}", "rcpt_open": "recorded as {r}, not dispatched yet", "default_undoable": "only something that can be undone may be done by default; an irreversible step waits for the user (add --undoable when it can be undone)", "followup": "follow-up",
        "no_threads": "(no threads yet)",
        "t_created": "created", "t_updated": "updated", "t_reported": "reported", "t_closed": "closed",
        "t_where": "where", "t_thread": "thread", "t_session": "session", "t_branch": "branch", "t_log": "log",
        "t_other": "other unmerged work on", "t_no_tasks": "no tasks linked",
        "s_title": "where things stand", "s_updated": "Updated", "s_deliverables": "Deliverables",
        "s_none_yet": "(none yet)", "s_requests": "Your requests", "s_none_open": "(none open)",
        "s_waiting": "Waiting on you", "s_issues": "Known issues", "s_missing": "missing",
        "s_all": "Every requirement (strike out what is stale)", "s_ungrouped": "(no phase or category)",
        "merge_waiting": "Waiting to merge: {n} branch(es), oldest {m} min ({tasks})", "owner": "owner",
        "d_draft": "draft", "d_final": "final", "i_open": "open", "i_fixed-on-branch": "fixed on a branch",
        "i_merged": "merged", "i_verified": "verified", "qa_pending": "Merged, regression not run yet",
        "quota_deny": "Unsuitable model combinations", "features": "Feature ledger", "reconcile": "Reconcile",
        "reconcile_badge": "🔴 Reconcile: {n} mismatches → TW reconcile", "wrapup": "Wrap-up self-check",
        "selfcheck_reminder": "Lead: check that every request the user made this round was recorded with req add.",
        "heard_unrecorded": "Heard but not recorded", "recorded_not_dispatched": "Recorded but not dispatched",
        "dispatched_no_reply": "Dispatched without reply", "replied_not_reported": "Replied but not reported",
        "promises_open": "Unfulfilled promises", "no_items": "(none)", "in_round": "In this version", "rc_unassigned": "{req}: open, but no task or feature carries it", "rc_pair": "{req}: check the record against the user's words — said: {original} / recorded: {record}", "rc_no_req": "{feature}: no request it comes from", "rc_landed_open": "{feature} landed, but {req} is still open", "rc_done_unreported": "{task} is done, but {req} is not reported yet",
        "still_working": "Still in progress", "missed": "Not started", "feature_status": "{status}",
        "original_pair": "Original ↔ record", "feature_no_req": "Feature {feature} has no source request",
        "feature_planned": "planned", "feature_building": "building", "feature_landed": "landed", "feature_dropped": "dropped",
        "card_done": "Done", "card_doing": "In progress", "card_waiting": "Waiting on you", "ledger": "Ledger",
        "docs": "Documents", "intro": "Recorded {requests} requests and {features} features. Board sections: {sections}. See: {where}.",
        "standing": "Standing: {text}", "guard_errors": "bash-guard had {n} errors in the last 7 days and allowed the command",
        "card_compare": "Comparison", "card_plan": "New plan", "card_read": "Material read", "card_delivered": "Delivery",
        "card_status": "Progress", "card_defaulted": "going ahead with the default: {what}", "card_question": "Question {n}",
        "card_ledger": "Ledger +{added}/−{ended} · waiting on you: {waiting} · GPT did {gpt} · {links}",
        "chain_recorded": "recorded", "chain_dispatched": "dispatched", "chain_accepted": "accepted",
        "chain_review": "review", "chain_merged": "merged", "chain_reported": "reported",
        "doc_board": "Board", "doc_ledger": "Ledger", "doc_features": "Features",
    },
    "zh": {
        "planned": "建议中（还没有会话）", "starting": "已创建，第一轮还没结束",
        "working": "进行中", "waiting": "等你处理（问题、权限弹窗或失败）",
        "review": "待审", "landing": "合并中（已批准）",
        "idle": "空闲（本轮结束，没有待办）", "resolved": "已完结",
        "stuck": "可能卡住（进行中，超过 {n} 分钟没有汇报）", "no_report": "{m} 分钟没有汇报",
        "overview": "总览", "questions": "等你回答", "roster": "成员", "plan": "计划",
        "requests": "你提的事（未关闭）", "unreported": "已交付、还没回报给用户",
        "req_open": "待办", "req_held": "等你的材料", "req_dispatched": "已派", "req_delivered": "已交付（待回报！）",
        "req_reported": "已回报", "req_closed": "已关闭", "req_proposed": "已提议（等你同意）",
        "req_dropped": "已放弃", "req_waiting_user": "等你定", "req_wont": "不做（附理由）", "req_landed": "已落地（附证据）", "req_remark": "备注", "remark_heading": "备注（不派）", "default_heading": "按默认做的决定", "promise_heading": "未完成的承诺", "default_prefix": "按默认做：", "can_change": "（可改）", "rcpt_remark": "记为备注 {r}，不派", "rcpt_given": "记为 {r}，交给 {who} 线程", "rcpt_open": "记为 {r}，待派", "default_undoable": "只有能撤回的事才能按默认做；不可撤回的要等用户（能撤回时加 --undoable）", "followup": "回访",
        "no_threads": "（还没有线程）",
        "t_created": "创建", "t_updated": "更新", "t_reported": "回报", "t_closed": "关闭",
        "t_where": "落到哪里", "t_thread": "线程", "t_session": "会话", "t_branch": "分支", "t_log": "日志",
        "t_other": "分支上其他未合并的工作", "t_no_tasks": "没有关联任务",
        "s_title": "进展一览", "s_updated": "更新于", "s_deliverables": "交付物", "s_none_yet": "（还没有）",
        "s_requests": "你提的事", "s_none_open": "（没有未完成的）", "s_waiting": "等你处理", "s_issues": "已知问题",
        "s_missing": "还缺", "s_all": "全部需求（过时的划掉）", "s_ungrouped": "（未分阶段或类别）",
        "merge_waiting": "等待合并：{n} 个分支，最早的已等 {m} 分钟（{tasks}）", "owner": "负责",
        "d_draft": "草稿", "d_final": "定稿", "i_open": "未修", "i_fixed-on-branch": "已在分支上修好",
        "i_merged": "已合并", "i_verified": "已验证", "qa_pending": "已合并、还没跑回归",
        "quota_deny": "不适合名单", "features": "功能清单", "reconcile": "三方对账",
        "reconcile_badge": "🔴 对账：{n} 处对不上 → TW reconcile", "wrapup": "收工自查",
        "selfcheck_reminder": "负责人自查：本轮用户说过的事是否都已用 req add 记入台账。",
        "heard_unrecorded": "听到没记", "recorded_not_dispatched": "记了没派",
        "dispatched_no_reply": "派了没回", "replied_not_reported": "回了没报",
        "promises_open": "没兑现的承诺", "no_items": "（无）", "in_round": "进了这一版", "rc_unassigned": "{req}：还没派，也没有功能或任务接它", "rc_pair": "{req}：请核对原话和记录——原话：{original} / 记录：{record}", "rc_no_req": "{feature}：找不到它来自哪条请求", "rc_landed_open": "{feature} 已落地，但 {req} 还开着", "rc_done_unreported": "{task} 做完了，但 {req} 还没回报",
        "still_working": "还在做", "missed": "没赶上", "feature_status": "{status}",
        "original_pair": "原话 ↔ 记录", "feature_no_req": "功能 {feature} 没有关联来源请求",
        "feature_planned": "计划中", "feature_building": "开发中", "feature_landed": "已落地", "feature_dropped": "已放弃",
        "card_done": "做完了", "card_doing": "在做", "card_waiting": "等你", "ledger": "台账",
        "docs": "文档", "intro": "已记下 {requests} 件事、{features} 个功能。看板栏目：{sections}。查看：{where}。",
        "standing": "长期要求：{text}", "guard_errors": "bash-guard 近 7 天出错 {n} 次，已照常放行",
        "card_compare": "对照分析", "card_plan": "新方案", "card_read": "素材已读", "card_delivered": "交付",
        "card_status": "进展", "card_defaulted": "已按默认做：{what}", "card_question": "问题 {n}",
        "card_ledger": "台账 +{added}/−{ended} · 等你 {waiting} 件 · GPT 做了 {gpt} · {links}",
        "chain_recorded": "记台账", "chain_dispatched": "派", "chain_accepted": "接单",
        "chain_review": "评审", "chain_merged": "合并", "chain_reported": "回报",
        "doc_board": "看板", "doc_ledger": "台账", "doc_features": "功能清单",
    },
}


def lab(st, key, **kw):
    code = (st.get("prefs", {}).get("language") or "en").strip().lower().split("-")[0]
    return LABELS.get(code, LABELS["en"]).get(key, LABELS["en"].get(key, key)).format(**kw)  # unknown: the key


REQ_STATUSES = ["open", "held", "proposed", "dispatched", "delivered", "reported", "waiting_user", "landed", "wont", "remark", "closed", "dropped"]
REQ_END = ("closed", "dropped")
# Request moves the lead may make with `req set` (framework A9). closed/dropped only via close/drop (user action).
REQ_MOVES = {"open": {"held", "proposed", "dispatched", "reported", "waiting_user"}, "held": set(),  # held: back where it paused
             "proposed": {"held", "dispatched", "waiting_user"},
             "dispatched": {"held", "delivered", "waiting_user"}, "delivered": {"reported"}, "reported": {"dispatched", "waiting_user", "landed"},
             "waiting_user": {"open", "proposed", "dispatched"}, "landed": {"dispatched"}, "wont": {"open"}, "remark": {"open"}}
REQ_NEEDS_USER = {("proposed", "dispatched"), ("reported", "dispatched"),  # user said yes / asked for changes
                  ("waiting_user", "dispatched"), ("landed", "dispatched")}  # no side door around the user's yes
# Task moves (framework A9). done needs review first and passes the review gate.
TASK_MOVES = {"planned": {"approved", "dropped"}, "approved": {"dispatched", "dropped"},
              "dispatched": {"working", "recalled"}, "working": {"blocked", "review", "recalled"},
              "blocked": {"working", "recalled"}, "review": {"working", "done"},
              "recalled": {"dispatched", "dropped"}, "done": set(), "dropped": set()}
# Accepted when importing a markdown ledger (status column in either language).
REQ_STATUS_ALIASES = {"待办": "open", "等材料": "held", "等你的材料": "held", "等你定": "waiting_user", "不做": "wont", "已落地": "landed", "已派": "dispatched", "已交付": "delivered", "已回报": "reported",
                      "已关闭": "closed", "已提议": "proposed", "已放弃": "dropped", **{x: x for x in REQ_STATUSES}}


def unreported(st):
    return {r: q for r, q in st.get("requests", {}).items() if q["status"] == "delivered"}


def render_requests(st, chain=False):
    reqs = [(r, q) for r, q in st.get("requests", {}).items() if q["status"] not in REQ_END and q["status"] != "remark"]
    if not reqs:
        return None
    order = {"delivered": 0, "held": 1, "proposed": 2, "open": 3, "waiting_user": 4, "dispatched": 5, "reported": 6, "landed": 7, "wont": 8}
    reqs.sort(key=lambda x: (order.get(x[1]["status"], 9), int(x[0][1:]) if x[0][1:].isdigit() else 0))
    out = []
    for r, q in reqs:
        line = f"- **{r}** [{lab(st, 'req_' + q['status'])}] {_md(q['text'])}"  # user text: never struck or bolded
        extra = [", ".join(q.get("tasks") or []), _where(q.get("where")) if q.get("where") else ""]
        extra = " · ".join(x for x in extra if x)
        if extra:
            line += f" — {extra}"
        if q.get("followup"):
            line += f" — {lab(st, 'followup')}: {_md(q['followup'])}"
        out.append(line)
        if chain:  # B4: the board shows how far each request has come
            out.append(f"  - {progress_chain(st, q)}")
    return "\n".join(out)


def render_features(st):
    rows = st.get("features", {})
    if not rows:
        return None
    groups = {}
    for fid, f in rows.items():
        groups.setdefault(f.get("phase") or "", []).append((fid, f))
    out = []
    for phase, items in sorted(groups.items(), key=lambda x: (x[0] or "~")):
        out.append("### " + _md(phase or "-"))
        for fid, f in sorted(items, key=lambda x: int(x[0][1:])):
            out.append(f"- **{fid}** {_md(f['name'])} [{lab(st, 'feature_' + f['status'])}] — {', '.join(f.get('reqs') or []) or '-'}")
    return "\n".join(out)


def _feature_ids(rows):
    return [f"F{i}" for i in range(1, max([int(x[1:]) for x in rows if re.fullmatch(r"F\d+", x)] or [0]) + 1)]


def _reconcile_rows(st):
    rows = []
    reqs, tasks, features = st.get("requests", {}), st.get("tasks", {}), st.get("features", {})
    for rid, q in reqs.items():
        if q.get("status") in ("open", "dispatched", "held") and not q.get("tasks") and not q.get("features"):
            rows.append({"kind": "unassigned_request", "req": rid})
        for said in q.get("user_said", []) if isinstance(q.get("user_said"), list) else ([{"text": q["user_said"]}] if q.get("user_said") else []):
            rows.append({"kind": "original_pair", "req": rid, "original": said.get("text", ""), "record": q.get("text", "")})
    for fid, f in features.items():
        linked = f.get("reqs") or []
        if not linked:
            rows.append({"kind": "feature_without_request", "feature": fid})
        if f.get("status") == "landed":
            for rid in linked:
                if rid in reqs and reqs[rid].get("status") in ("open", "dispatched"):
                    rows.append({"kind": "landed_request_open", "feature": fid, "req": rid})
    for tid, task in tasks.items():
        if task.get("status") == "done":
            for rid, q in reqs.items():
                if tid in (q.get("tasks") or []) and q.get("status") not in REQ_SETTLED:
                    rows.append({"kind": "done_task_request_unreported", "task": tid, "req": rid})
    return rows


REQ_SETTLED = ("reported", "landed", "closed", "dropped", "wont", "remark")
RC_KEYS = {"unassigned_request": "rc_unassigned", "original_pair": "rc_pair", "feature_without_request": "rc_no_req",
           "landed_request_open": "rc_landed_open", "done_task_request_unreported": "rc_done_unreported"}


def _rc_line(st, x):
    """One reconcile finding in the user's language."""
    return lab(st, RC_KEYS[x["kind"]], **{k: v for k, v in x.items() if k != "kind"})


def _run_reconcile(st):
    rows = _reconcile_rows(st)
    st["reconcile"] = {"at": now(), "count": sum(1 for x in rows if x["kind"] != "original_pair"), "items": rows}
    return rows


STUCK_DEFAULT = 30  # minutes without a report before a working thread counts as possibly stuck


def stuck_threads(st):
    """{thread: minutes since its last report} for working/starting threads silent longer than prefs.stuck_minutes."""
    limit = st.get("prefs", {}).get("stuck_minutes", STUCK_DEFAULT)
    if not limit:
        return {}
    cur = dt.datetime.fromisoformat(now())
    out = {}
    for n, t in st["threads"].items():
        if t["status"] not in ("working", "starting"):
            continue
        since = t.get("last_status_at") or t.get("created")
        if not since:
            continue
        mins = int((cur - dt.datetime.fromisoformat(since)).total_seconds() // 60)
        if mins > limit:
            out[n] = mins
    return out


LANG_NAMES = {"zh": "Chinese (中文)", "en": "English", "ja": "Japanese (日本語)", "ko": "Korean (한국어)",
              "fr": "French", "de": "German", "es": "Spanish"}


def language_rule(st):
    """The conversation-language rule, with the project's current language (prefs.language)."""
    code = (st.get("prefs", {}).get("language") or "").strip()
    cur = (f"{LANG_NAMES.get(code.lower(), code)} (prefs.language={code})" if code
           else "not set: use the language of the user's latest message")
    return (
        f"Language (most important rule). Current conversation language: {cur}.\n"
        "- Reply in the language you were asked in. The lead writes to threads, threads answer the lead and talk to "
        "each other in the current conversation language.\n"
        "- If the user talks to a thread directly in another language, that thread answers the user in that language.\n"
        "- The same language runs all the way down: the task brief a thread gives its subagents and the reports they "
        "return are in the current conversation language too.\n"
        "- When the user switches language, the lead runs `tw.py prefs --set language=<code>` and broadcasts a "
        "FOLLOWUP to every thread; the newest one wins.\n"
        f"- Message headers keep their fixed English keywords (`[tw:{tag(st)}] TASK lead -> <thread> T2`); "
        "only the body follows the language.\n"
        "- Files follow what the task says (repo docs are English unless the task says otherwise); conversation "
        "follows the language."
    )


def find_dir(create_at=None):
    if create_at:
        d = Path(create_at).resolve() / ".teamwork"
        d.mkdir(parents=True, exist_ok=True)
        return d
    env = os.environ.get("TEAMWORK_DIR")
    if env:
        return Path(env).resolve()  # set by -C; never fall back to cwd when it was given
    cur = Path.cwd().resolve()
    for p in [cur, *cur.parents]:
        if (p / ".teamwork" / "state.json").exists():
            return p / ".teamwork"
    return None


def need(d):
    if d is None or not (d / "state.json").exists():
        sys.exit(
            "no .teamwork/state.json found from here upward: run `tw.py init <project>` in the project folder, or set TEAMWORK_DIR"
        )
    return d


def load(d):
    return json.loads((d / "state.json").read_text(encoding="utf-8"))


def log(d, event):
    with open(d / "log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": now(), **event}, ensure_ascii=False) + "\n")


def save(d, st, event=None):
    touched = st.pop("_touched", [])
    moved_reqs = st.pop("_moved_reqs", [])  # requests a task move delivered or re-dispatched (any request, linked or not)
    st["updated"] = now()
    tmp = d / "state.json.tmp"
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(d / "state.json")
    (d / "BOARD.md").write_text(render_board(st), encoding="utf-8")
    if event:
        more = {n: st["threads"][n] for n in touched if n in st["threads"] and n != event.get("thread")}
        snap = snapshots(st, event)
        if moved_reqs:
            snap["snap_reqs"] = {**snap.get("snap_reqs", {}),
                                 **{r: st["requests"][r] for r in moved_reqs if r in st.get("requests", {})}}
        log(d, {**event, **snap, **({"snap_more_threads": more} if more else {})})


def snapshots(st, ev):
    """Copies of what this event changed, so `rebuild` can restore state.json from log.jsonl alone."""
    snap = {}
    if ev.get("task") in st.get("tasks", {}):
        snap["snap_task"] = st["tasks"][ev["task"]]
        linked = {r: q for r, q in st.get("requests", {}).items() if ev["task"] in (q.get("tasks") or [])}
        if linked:  # task done can move requests to delivered
            snap["snap_reqs"] = linked
    if ev.get("thread") in st.get("threads", {}):
        snap["snap_thread"] = st["threads"][ev["thread"]]
    if ev.get("req") in st.get("requests", {}):
        snap["snap_req"] = st["requests"][ev["req"]]
    if ev.get("ev") in ("req-import", "req-migrate"):
        snap["snap_reqs"] = {r: st["requests"][r] for r in ev.get("ids", []) if r in st.get("requests", {})}
    if ev.get("ev") in ("init", "lead", "prefs"):
        snap["snap_meta"] = {k: st.get(k) for k in ("project", "goal", "created", "repo", "envs", "lead", "prefs")}
    if ev.get("ev") in ("question", "answered", "default"):
        snap["snap_questions"] = st.get("questions", [])
    if ev.get("ev", "").startswith("plan-"):
        snap["snap_plan_approved"] = st.get("plan_approved")
        snap["snap_tasks"] = st.get("tasks", {})  # approve/reopen changes every task's status
    if ev.get("ev", "").startswith("issue-"):
        snap["snap_issues"] = st.get("issues", [])
    if ev.get("ev", "").startswith("deliverable-"):
        snap["snap_deliverables"] = st.get("deliverables", [])
    if ev.get("ev") in ("default", "answered"):
        snap["snap_decisions"] = st.get("decisions", [])
    if ev.get("ev", "").startswith("feature-"):
        snap["snap_features"] = st.get("features", {})
        snap["snap_reqs"] = {**snap.get("snap_reqs", {}), **st.get("requests", {})}
    if ev.get("ev") == "reconcile":
        snap["snap_reconcile"] = st.get("reconcile")
    if ev.get("ev") == "docs-set":
        snap["snap_docs"] = st.get("docs", {})
    if ev.get("ev") == "standing":
        snap["snap_standing"] = st.get("standing", [])
    if ev.get("ev") == "report-card":
        snap["snap_last_report_at"] = st.get("last_report_at")
    if ev.get("ev", "").startswith("promise-"):
        snap["snap_promises"] = st.get("promises", [])
    if ev.get("ev") in ("merge", "qa-done"):
        snap["snap_qa_pending"] = st.get("qa_pending", [])
    if ev.get("ev", "").startswith("model-deny-"):
        snap["snap_model_deny"] = st.get("model_deny", [])
    if "roster_version" in st:
        snap["snap_roster_version"] = st["roster_version"]
    return snap


DOC_KINDS = ("board", "ledger", "features")
CHAIN_STEPS = ("recorded", "dispatched", "accepted", "review", "merged", "reported")


def progress_chain(st, q):
    """B4: how far one request has come: recorded -> dispatched -> accepted -> review -> merged -> reported."""
    tasks = [st.get("tasks", {}).get(t, {}) for t in q.get("tasks") or []]
    steps = (True, any(int(t.get("dispatch_v") or 0) >= 1 for t in tasks),
             any(t.get("status") in ("working", "blocked", "review", "done") for t in tasks),
             any(t.get("status") in ("review", "done") for t in tasks),
             any(t.get("merged_at") for t in tasks), q.get("status") in ("reported", "landed", "closed"))
    return " → ".join(lab(st, "chain_" + name) + ("✓" if yes else "·") for name, yes in zip(CHAIN_STEPS, steps))


def read_file(d, name):
    p = d / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


def csv(s):
    return [x.strip() for x in s.split(",") if x.strip()] if s else []


SHORT_RE = r"[A-Za-z0-9][A-Za-z0-9-]{0,5}"
TITLE_TARGET = 24  # session titles are read in a narrow sidebar


def default_short(project):
    """Short name for a new project: the name itself if it fits in 6 chars, else the initials (TFCC)."""
    if len(project) <= 6:
        return project
    initials = "".join(w[0] for w in project.split("-") if w).upper()
    return initials[:6] if len(initials) >= 2 else project[:6]


def tag(st):
    """Project label used in titles, message headers and tags. Old projects without short_name keep the full name."""
    return (st.get("prefs", {}).get("short_name") or "").strip() or st["project"]


def title_for(st, name):
    t = st["threads"][name]
    focus = t.get("focus") or ""
    return f"[{tag(st)}] {name} · {focus}" if focus else f"[{tag(st)}] {name}"


PERMISSION_MODES = ("default", "acceptEdits", "auto", "dontAsk", "bypassPermissions")
# How loose each mode is: create_session refuses a thread looser than its creator (auto needs an auto parent).
PERMISSION_RANK = {"default": 0, "dontAsk": 0, "acceptEdits": 1, "auto": 2, "bypassPermissions": 3}
THREAD_MODE_DEFAULT = "auto"  # threads that do real work; ordinary calls never ask (R44)
STANDING_GRANTS = ("archive", "schedule", "merge")  # what the user may allow once for the lead (IL-30)


def thread_mode(st):
    return (st.get("prefs", {}).get("thread_permission_mode") or "").strip() or THREAD_MODE_DEFAULT


def permission_warning(st):
    """One line for the user when threads cannot get the mode they need, or None. Never a silent downgrade."""
    want = thread_mode(st)
    lead = (st.get("lead", {}).get("permission_mode") or "").strip()
    live = [n for n, t in st["threads"].items() if t.get("status") not in ("archived", "resolved")]
    if not lead:
        return ("the lead's own permission mode is not recorded: check it (get_session, or the mode menu under this "
                f"session's message box) and run `tw.py lead --mode <mode>`; threads are opened with {want}")
    if PERMISSION_RANK.get(lead, 0) >= PERMISSION_RANK.get(want, 0):
        behind = [n for n in live if st["threads"][n].get("session_id") and st["threads"][n].get("permission_mode")
                  and PERMISSION_RANK.get(st["threads"][n]["permission_mode"], 0) < PERMISSION_RANK.get(want, 0)]
        if behind:
            return (f"threads running below {want}: {', '.join(behind)}; they ask for ordinary calls. Tell the user "
                    f"to switch each to {want} in its mode menu, or reseat them with permission_mode={want}")
        return None
    return (f"Tell the user: this lead session runs in {lead} mode, so create_session refuses {want} for threads and "
            f"they would ask before ordinary calls. Switch this session to {want} (claude.ai: the mode menu under the "
            f"message box; or reopen the lead with permission_mode={want}), then run `tw.py lead --mode {want}`. "
            f"Threads affected: {', '.join(live) or 'none yet'}")
# Pre-approved in every thread (create_session extra_allowed_tools) so reporting never stalls on a prompt.
# The lead can only grant what it has pre-approved itself (install.py adds these allow rules).
THREAD_TOOLS = [
    "mcp__claude-code-remote__send_message",
    "mcp__claude-code-remote__get_session",
    "mcp__claude-code-remote__list_events",
]


def worktree_path(st, name):
    """rc thread worktree: <project folder>-worktrees/<thread>, next to the project folder (full name, not short)."""
    repo = Path(st.get("repo", {}).get("path") or ".")
    return repo.parent / f"{repo.name}-worktrees" / name


def worktree_cmd(st, name):
    t = st["threads"][name]
    repo = st.get("repo", {}).get("path") or "."
    base = st.get("repo", {}).get("base") or "main"
    path = t.get("worktree") or str(worktree_path(st, name))
    return f'git -C "{repo}" worktree add -b {branch_of(st, name)} "{path}" {base}'


def session_args(st, name):
    """Extra create_session arguments for a thread: permission mode and pre-approved tools."""
    out = {"extra_allowed_tools": THREAD_TOOLS, "permission_mode": thread_mode(st)}
    if (st["threads"].get(name) or {}).get("model"):
        out["model"] = st["threads"][name]["model"]  # IL-31: the thread's default model
    w = permission_warning(st)
    if w:
        out["permission_warning"] = w
    return out


def title_warning(title):
    if len(title) > TITLE_TARGET + 8:
        return (f"title is {len(title)} chars; aim for about {TITLE_TARGET}: shorten the thread name or --focus")
    return None


# ------------------------------------------------------------------ rendering


def render_roster(st, for_thread=None):
    lead = st["lead"]
    lines = [
        f"- lead | rc | session_id={lead.get('session_id') or '(unset)'} | coordinator: plans, routes, merges, talks to the user"
    ]
    for name, t in st["threads"].items():
        if t["status"] == "archived":
            continue
        me = "   <- YOU" if name == for_thread else ""
        owns = ", ".join(t.get("owns", [])) or "-"
        lines.append(
            f"- {name} | {t['kind']} | session_id={t.get('session_id') or '(pending)'} | owns: {owns}{me}"
        )
    return "\n".join(lines)


def render_tasks(st, only_owner=None):
    rows = []
    for tid, k in st["tasks"].items():
        if only_owner and k.get("owner") != only_owner:
            continue
        deps = ",".join(k.get("deps", [])) or "-"
        rv = k.get("review") or {}
        review = f"{rv['result']} ({rv.get('reviewer') or '-'})" if rv else "-"
        if k.get("checks"):
            review += f", checks {k['checks']}"
        reqs = ",".join([r + {"closed": "✓", "dropped": "✗"}.get(q["status"], "")
                         for r, q in st.get("requests", {}).items() if tid in (q.get("tasks") or [])]
                        + [f"({r})" for r in k.get("from_requests") or []]) or "-"  # (R1): follow-up, not holding it
        rows.append(
            f"| {tid} | {k['title']}" + (f" ({k['route']})" if k.get("route") else "") + f" | {k.get('owner') or '-'} | {deps} | {k['status']} | {k.get('accept') or '-'} "
            f"| {review} | {reqs} |"
        )
    if not rows:
        return "(no tasks)"
    return (
        "| id | task | owner | deps | status | done when | review | req |\n|---|---|---|---|---|---|---|---|\n"
        + "\n".join(rows)
    )


def _cell(x):
    return str(x).replace("|", "\\|").replace("\n", " ")


def render_reviews(st, only=None):
    """Per task: verdict, rounds used of the limit (as it was when recorded), reviewer, findings left, follow-ups."""
    cap = review_max(st)
    rows = []
    for tid, k in st["tasks"].items():
        if only and tid != only:
            continue
        rv = k.get("review") or {}
        n = int(k.get("review_rounds") or 0)
        if not rv and not n and not k.get("past_reviews"):
            continue
        fu = [t + (" (verdict voided)" if x.get("parent_verdict_voided") else "")
              for t, x in st["tasks"].items() if x.get("followup_of") == tid]
        left = "; ".join(rv.get("remaining") or []) or "-"
        rows.append("| " + " | ".join(_cell(x) for x in (
            tid, k.get("owner") or "-", k["status"], rv.get("result") or "-", f"{n}/{rv.get('cap', cap) or '-'}",
            rv.get("reviewer") or "-", k.get("checks") or "-", left, ",".join(fu) or "-")) + " |")
    if not rows:
        return "(no reviews recorded)"
    return ("| task | owner | status | verdict | rounds | reviewer | checks | remaining | follow-ups |\n"
            "|---|---|---|---|---|---|---|---|---|\n" + "\n".join(rows))


def task_followups(d, st, a):
    """One follow-up task per important finding left at a capped review; dry run unless --apply."""
    if a.id not in st["tasks"]:
        sys.exit(f"unknown task {a.id}")
    k = st["tasks"][a.id]
    rv = k.get("review") or {}
    if rv.get("result") not in PASSING or not rv.get("remaining"):
        sys.exit(f"{a.id} has no passing or capped review with remaining findings (`task set {a.id} --review "
                 "pass|capped --remaining ...` first)")
    done = {x.get("followup_item") for t, x in st["tasks"].items() if x.get("followup_of") == a.id}
    reqs = [r for r, q in st.get("requests", {}).items() if a.id in (q.get("tasks") or [])]
    plan, n = [], 0
    for item in rv["remaining"]:
        if item in done:
            continue
        n += 1
        while True:
            tid = f"{a.id}-f{n}"
            if tid not in st["tasks"]:
                break
            n += 1
        plan.append((tid, item))
    out = []
    for tid, item in plan:
        out.append({"id": tid, "owner": k.get("owner"), "accept": f"fixed, with a test that fails before the fix: {item}",
                    "followup_of": a.id, "requests": reqs})
        if a.apply:
            st["tasks"][tid] = {
                "title": f"follow-up of {a.id}: {item}"[:120],
                "owner": k.get("owner"),
                "deps": [a.id],  # starts from the merged parent, not from unmerged code
                "accept": out[-1]["accept"],
                "touches": list(k.get("touches") or []),
                "topics": list(k.get("topics") or []),
                "status": "approved" if st["plan_approved"] else "planned",
                "notes": "",
                "created": now(),
                "followup_of": a.id,
                "followup_item": item,
                "from_requests": reqs,  # for trace; not linked, so the original request is not held back
            }
    if a.apply and plan:
        save(d, st, {"ev": "task-followups", "task": a.id, "added": [t for t, _ in plan],
                     "snap_more_tasks": {t: st["tasks"][t] for t, _ in plan}})
    print(json.dumps({"from": a.id, "apply": bool(a.apply), "tasks": out,
                      "next": ("show them to the user with the plan; dispatch like any task" if a.apply and plan
                               else "re-run with --apply to add them" if plan else "nothing new to add")},
                     ensure_ascii=False, indent=2))


def render_groups(st):
    order = ["waiting", "stuck", "review", "working", "starting", "landing", "planned", "idle", "resolved"]
    stuck = stuck_threads(st)
    out = []
    for g in order:
        if g == "stuck":
            if stuck:
                out.append(f"**{lab(st, 'stuck', n=st['prefs'].get('stuck_minutes', STUCK_DEFAULT))}**")
                for n, m in stuck.items():
                    t = st["threads"][n]
                    out.append(f"- {t.get('title') or n} ({t['kind']}) — {lab(st, 'no_report', m=m)} — "
                               f"{t.get('summary') or '-'}")
            continue
        names = [n for n, t in st["threads"].items() if t["status"] == g and n not in stuck]
        if not names:
            continue
        out.append(f"**{lab(st, g)}**")
        for n in names:
            t = st["threads"][n]
            out.append(
                f"- {t.get('title') or n} ({t['kind']}) — {t.get('summary') or '-'}"
            )
    return "\n".join(out) or lab(st, "no_threads")


def render_board(st):
    approved = (
        "APPROVED"
        if st.get("plan_approved")
        else "DRAFT — waiting for the user's go-ahead"
    )
    r = st.get("repo", {})
    out = [
        f"# teamwork: {st['project']}" + (f" ({tag(st)})" if tag(st) != st["project"] else ""),
        "",
    ]
    recent = st.get("reconcile") or {}
    docs = [f"- [{lab(st, 'doc_' + key)}]({doc['link']})" + (f" — {_md(doc['note'])}" if doc.get("note") else "")
            for key in DOC_KINDS for doc in [st.get("docs", {}).get(key)] if doc and doc.get("link")]
    if docs:
        out += docs + [""]
    if recent.get("count"):
        out += [lab(st, "reconcile_badge", n=recent["count"]), ""]
    reqs = render_requests(st, chain=True)
    if reqs:
        out += [f"## {lab(st, 'requests')}", "", reqs, ""]
    features = render_features(st)
    if features:
        out += [f"## {lab(st, 'features')}", "", features, ""]
    remarks = [(r, q) for r, q in st.get("requests", {}).items() if q.get("status") == "remark"][-10:]
    if remarks: out += ["## " + lab(st, "remark_heading"), ""] + [f"- {r}: {_md(q['text'])}" for r, q in remarks] + [""]
    defaults = [x for x in st.get("decisions", []) if not x.get("answered") and not x.get("confirmed")]
    if defaults: out += ["## " + lab(st, "default_heading"), ""] + [f"- [{x['question']}] {_md(x['what'])}" for x in defaults] + [""]
    promises = [x for x in st.get("promises", []) if x.get("status") == "open"]
    if promises: out += ["## " + lab(st, "promise_heading"), ""] + [f"- {x['id']}: {_md(x['text'])}" for x in promises] + [""]
    pending = st.get("qa_pending", [])
    if pending:
        out += [f"## {lab(st, 'qa_pending')}", ""]
        out += [f"- {x.get('branch', '?')}@{str(x.get('sha', ''))[:7]}" for x in pending]
        out += ["", "`tw.py qa done` after the regression round", ""]
    out += [
        f"Goal: {st.get('goal') or '-'}",
        f"Plan: {approved}    Updated: {st.get('updated', '-')}",
        f"Folder: {r.get('path') or '-'}    Repo: {r.get('url') or '-'}    Base branch: {r.get('base') or 'main'}",
        f"Prefs: {json.dumps(st.get('prefs', {}), ensure_ascii=False)}",
        "",
        f"## {lab(st, 'overview')}",
        "",
        render_groups(st),
    ]
    open_q = [
        (i, q) for i, q in enumerate(st.get("questions", [])) if not q.get("answered")
    ]
    if open_q:
        out += ["", f"## {lab(st, 'questions')}", ""] + [
            f"- [{i + 1}] ({q['from']}) {_md(q['text'])}" + (" — " + lab(st, "default_prefix") + _md(q["defaulted"]["what"])
                                                            + lab(st, "can_change") if q.get("defaulted") else "")
            for i, q in open_q
        ]
    w = merge_waiting(st)
    if w:
        out += ["", lab(st, "merge_waiting", n=len(w), m=w[0]["minutes"] if w[0]["minutes"] is not None else "?",
                        tasks=", ".join(x["task"] for x in w))]
    iss = [i for i in st.get("issues", []) if i.get("status") != "verified"]
    if iss:
        out += ["", f"## {lab(st, 's_issues')}", ""] + [render_issue(i, st) for i in iss]
    deny = [x for x in st.get("model_deny", []) if not x.get("allowed_at")]
    if deny:
        out += ["", f"## {lab(st, 'quota_deny')}", ""] + [f"- {x.get('type')} — {x.get('model')}" for x in deny]
    out += ["", f"## {lab(st, 'roster')}", "", render_roster(st), "", f"## {lab(st, 'plan')}", "", render_tasks(st)]
    return "\n".join(out) + "\n"


ISSUE_STATUSES = ("open", "fixed-on-branch", "merged", "verified")
DELIVERABLE_STATUSES = ("draft", "final")


def render_issue(i, st=None):
    st = st or {}
    return (f"- {i['id']} [{lab(st, 'i_' + i['status'])}] {_md(i['text'])} ({lab(st, 'owner')}: {i.get('owner') or '-'}"
            + (f"; {i['ref']}" if i.get("ref") else "") + ")")


def _where(x):
    """A link or path stays as typed (escaping would break it when the text is pasted); other text is escaped."""
    x = " ".join(str(x or "").split())
    return f"<{x}>" if re.fullmatch(r"https?://\S+", x) else f"`{x}`" if x and " " not in x and "`" not in x else _md(x)


def _md(x):
    """User text inside markdown: one line, and ~ * _ ` escaped, so it never strikes, bolds or breaks a list item."""
    return re.sub(r"([~*_`\\])", r"\\\1", " ".join(str(x or "").split()))


def render_share(st):
    """IL-13, IL-38: what the user opens on a phone, in their language. Deliverables first, then their requests,
    waiting items, issues, then every requirement by phase and category so stale ones can be struck out."""
    L = lambda k, **kw: lab(st, k, **kw)
    out = [f"# {st['project']}: {L('s_title')}", "", f"{L('s_updated')} {st.get('updated', '-')[:16].replace('T', ' ')}",
           ""]
    dl = st.get("deliverables", [])
    out += [f"## {L('s_deliverables')}", ""] + ([f"{i + 1}. **{_md(x['name'])}** — {L('d_' + x['status'])}"
                                                 + (f" — {x['link']}" if x.get("link") else "")
                                                 + (f" ({L('s_missing')}: {_md(x['missing'])})" if x.get("missing")
                                                    else "")
                                                 for i, x in enumerate(dl)] or [L("s_none_yet")]) + [""]
    reqs = st.get("requests", {})
    live = [(r, q) for r, q in reqs.items() if q["status"] not in REQ_END]
    out += [f"## {L('s_requests')}", ""] + ([f"- {r} — {L('req_' + q['status'])}: {_md(q['text'])}"
                                             + (f" → {_where(q['where'])}" if q.get("where") else "")
                                             + (f" ({L('followup')}: {_md(q['followup'])})" if q.get("followup")
                                                else "")
                                             for r, q in live] or [L("s_none_open")]) + [""]
    features = render_features(st)
    if features:
        out += [f"## {L('features')}", "", features, ""]
    qs = [q for q in st.get("questions", []) if not q.get("answered")]
    if qs:
        out += [f"## {L('s_waiting')}", ""] + [f"- {_md(q['text'])} ({q['from']})" for q in qs] + [""]
    iss = [i for i in st.get("issues", []) if i.get("status") != "verified"]
    if iss:
        out += [f"## {L('s_issues')}", ""] + [render_issue(i, st) for i in iss] + [""]
    remarks = [(r, q) for r, q in st.get("requests", {}).items() if q.get("status") == "remark"][-10:]
    if remarks: out += ["## " + lab(st, "remark_heading"), ""] + [f"- {r}: {_md(q['text'])}" for r, q in remarks] + [""]
    defaults = [x for x in st.get("decisions", []) if not x.get("answered") and not x.get("confirmed")]
    if defaults: out += ["## " + lab(st, "default_heading"), ""] + [f"- [{x['question']}] {_md(x['what'])}" for x in defaults] + [""]
    promises = [x for x in st.get("promises", []) if x.get("status") == "open"]
    if promises: out += ["## " + lab(st, "promise_heading"), ""] + [f"- {x['id']}: {_md(x['text'])}" for x in promises] + [""]
    pending = st.get("qa_pending", [])
    if pending:
        out += [f"## {L('qa_pending')}", ""]
        out += [f"- {x.get('branch', '?')}@{str(x.get('sha', ''))[:7]}" for x in pending]
        out += [""]
    if reqs:
        out += [f"## {L('s_all')}", ""]
        groups = {}
        for r, q in reqs.items():  # phases in the order the user first used them
            groups.setdefault((q.get("phase") or "", q.get("category") or ""), []).append((r, q))
        for (ph, cat), items in groups.items():
            out += [f"### {_md(ph or '-')} / {_md(cat or '-')}" if ph or cat else f"### {L('s_ungrouped')}"]
            for r, q in items:
                body = f"{r}: {_md(q['text'])}"
                out.append(f"- {'~~' + body + '~~' if q['status'] in REQ_END else body} — {L('req_' + q['status'])}")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


# ------------------------------------------------------------------ commands


def detect_base(folder):
    """The repo's default branch: origin/HEAD, else the current branch, else main."""
    g = lambda *x: subprocess.run(["git", "-C", str(folder), *x], capture_output=True, text=True)
    r = g("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if r.returncode == 0 and r.stdout.strip().startswith("origin/"):
        return r.stdout.strip()[len("origin/"):]
    r = g("symbolic-ref", "--quiet", "--short", "HEAD")
    if r.returncode == 0 and r.stdout.strip() and not r.stdout.strip().startswith("tw/"):
        return r.stdout.strip()
    return "main"


def cmd_init(a):
    d = find_dir(create_at=a.path)
    if (d / "state.json").exists() and not a.force:
        sys.exit(f"{d}/state.json already exists (use --force to overwrite)")
    if not re.fullmatch(NAME_RE, a.project):
        sys.exit("project name: lowercase letters/digits/hyphen, max 24 chars")
    short = a.short or default_short(a.project)
    if not re.fullmatch(SHORT_RE, short):
        sys.exit("short name: letters/digits/hyphen, max 6 chars (e.g. TFCC, shop)")
    st = {
        "project": a.project,
        "goal": a.goal or "",
        "created": now(),
        "lead": {"session_id": None},
        "envs": {"rc": None, "cloud": None},
        "repo": {
            "url": a.repo_url,
            "path": str(Path(a.path).resolve()),
            "base": a.base or detect_base(Path(a.path)),
        },
        "threads": {},
        "tasks": {},
        "plan_approved": False,
        "questions": [],
        "decisions": [],
        "promises": [],
        "features": {},
        "requests": {},
        "prefs": {
            "max_parallel": 0,  # 0 = no limit; set only when the user asks for one
            "propose_before_start": True,
            "update_detail": "brief",
            "checkin_minutes": 10,
            "short_name": short,  # in titles [X], headers [tw:X], tags teamwork:X
            "require_review": True,  # task done needs an independent review pass (task set --review)
            "review_max_rounds": REVIEW_MAX_DEFAULT,  # at this many review rounds: no blocking + checks pass -> capped
            "stuck_minutes": STUCK_DEFAULT,  # working thread silent this long -> "possibly stuck"; 0 = off
            "language": "",  # conversation language code (zh, en, ...); empty = follow the user's latest message
            "thread_permission_mode": THREAD_MODE_DEFAULT,  # create_session permission_mode for every thread
            "standing_grants": [],  # archive, schedule, merge: the user allowed these once (IL-30), each by --user-said
            "check_cmd": "",  # the project's checks, run by `merge` on the rehearsed merge
            "spawn_policy": "",
            "perm_policy": "",
            "gpt_policy": "",
            "skill_edge": "",  # H8: a clone this project tests before release; empty = the installed release
            "check_timeout_minutes": 60,  # merge stops checks that run longer (the whole process group); 0 = no limit
            "default_after_minutes": 30,  # IL-1: an unanswered non-gated question takes its stated default after this
            "ledger_line_every": "wrapup",
            "reconcile_every": "wrapup", "selfcheck_every": "wrapup",
        },
    }
    if not (d / "INSTRUCTIONS.md").exists():
        (d / "INSTRUCTIONS.md").write_text(
            f"# {a.project}: project instructions\n\n"
            "Standing instructions for the lead and every thread: coding style, what not to touch, how to report.\n"
            "New threads get this file at creation. Running threads only see changes when the lead re-broadcasts.\n\n",
            encoding="utf-8",
        )
    if not (d / "MEMORY.md").exists():
        (d / "MEMORY.md").write_text(
            f"# {a.project}: project memory\n\n"
            "Facts, decisions and conventions every thread must respect. Only the lead edits this file;\n"
            "threads propose additions with a MEMORY message. Keep it short: it is pasted into every new thread.\n\n"
            + "".join(f"{h}\n\n" for h in MEMORY_SECTIONS.values()),
            encoding="utf-8",
        )
    gi = d / ".gitignore"
    need_ig = ["state.json.tmp", "state.json.rebuilt", "state.json.bak-*", "PRIVATE.md", "private/"]
    have = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    if any(x not in have for x in need_ig):
        gi.write_text("\n".join(have + [x for x in need_ig if x not in have]) + "\n", encoding="utf-8")
    if not (d / "PRIVATE.md").exists():
        (d / "PRIVATE.md").write_text(PRIVATE_HELP, encoding="utf-8")
    (d / "private").mkdir(exist_ok=True)
    save(d, st, {"ev": "init", "project": a.project})
    print(f"initialised {d}")


def cmd_lead(a):
    d = need(find_dir())
    st = load(d)
    old = st["lead"].get("session_id")
    if a.session is not None:
        a.session = a.session.strip()
    if a.session and old and old != a.session and not a.force:
        sys.exit(f"project {st['project']} already has lead {old}. One lead per project: check that session with "
                 "get_session; if it is archived or the user says to take over, re-run with --force.")
    if a.session is not None:
        a.session = a.session.strip()
    if a.session and not_a_session(a.session, st):
        sys.exit(not_a_session(a.session, st).split(": a thread is")[0] + ": the lead is the user's own Remote "
                 "Control session; pass its id from get_session")
    if a.session:
        holder = next((n for n, t in st["threads"].items() if t.get("session_id") == a.session
                       and t.get("status") != "archived"), None)
        if holder:
            sys.exit(f"session {a.session} holds the {holder} seat; the lead needs its own session")
        if a.session != old:
            bump_roster(st)  # the roster names the lead: every thread needs the new id
            _close_archived(st, a.session, "the lead", now())
        st["lead"]["session_id"] = a.session
    if a.mode:
        st["lead"]["permission_mode"] = a.mode
    if a.rc_env:
        st["envs"]["rc"] = a.rc_env
    if a.cloud_env:
        st["envs"]["cloud"] = a.cloud_env
    if a.repo_url:
        st["repo"]["url"] = a.repo_url
    if a.base:
        st["repo"]["base"] = a.base
    save(d, st, {"ev": "lead", "session": a.session, "envs": st["envs"],
                 **({"mode": a.mode} if a.mode else {})})
    out = {"lead": st["lead"], "envs": st["envs"], "repo": st["repo"], "thread_permission_mode": thread_mode(st),
           "title": f"[{tag(st)}] lead" + (f" · {st['goal'][:40]}{'…' if len(st['goal']) > 40 else ''}" if st.get("goal") else ""), "tags": [f"teamwork:{tag(st)}", "teamwork-thread:lead"],
           "set_up": "set_session_title and set_session_tags on your own session with these (IL-34)"}
    w = permission_warning(st)
    if w:
        out["permission_warning"] = w
    print(json.dumps(out, ensure_ascii=False))


def _after(a, b):
    """ISO time a is later than b (offsets compared properly; missing a = never)."""
    from datetime import datetime
    try:
        return bool(a) and (not b or datetime.fromisoformat(a) > datetime.fromisoformat(b))
    except (TypeError, ValueError):
        return (a or "") > (b or "")


def _close_archived(st, sid, me, ts):
    """An archived seat that still lists this session: its stint is over (the session moved to `me`)."""
    for n2, t2 in st["threads"].items():
        if n2 != me and t2.get("status") == "archived" and t2.get("session_id") == sid:
            for h in t2.get("sessions", []):
                if h.get("session_id") == sid and not h.get("to"):
                    end = ts if not h.get("from") or _after(ts, h["from"]) else now()  # never before its start
                    h["to"], h["reason"] = end, f"session moved to {me}"
                    st.setdefault("_touched", []).append(n2)  # save() logs its snapshot too (rebuild sees it)


def _session_taken(st, sid, me):
    """Another live seat already holds this session: two seats would share one inbox."""
    return next((n for n, t in st["threads"].items()
                 if n != me and t.get("session_id") == sid and t.get("status") != "archived"), None)


SUBAGENT_ID_RE = r"(?i)a[0-9a-f]{8,}|(agent|subagent|task|toolu|reviewer|helper)([-_:].*|\d*)"


def not_a_session(sid, st=None):
    """Why sid cannot be a thread's session (an Agent-tool subagent id or name, not a create_session id), or None.
    A heuristic: real ids (session_..., cse_...) pass; subagent-shaped ids and thread or role names do not."""
    sid = (sid or "").strip()
    names = {"lead", "all", "user", "explore", "plan", "general-purpose", "worker", "explorer"} | {n.lower() for n in ((st or {}).get("threads") or {})}
    if sid and (re.fullmatch(SUBAGENT_ID_RE, sid) or sid.lower() in names):
        what = "is a thread or role name" if sid.lower() in names else "looks like a subagent id"
        return (f"{sid} {what}, not a session id: a thread is a remote session made with create_session (its id "
                "starts with session_). Subagents are helpers inside one session and never hold a seat. If "
                "create_session is not available, stop and tell the user; do not fall back to a subagent")
    return None


def _remotes(st):
    """The repo's remote names (`upstream/x` is as much a remote branch as `origin/x`); none outside git."""
    repo = st.get("repo", {}).get("path")
    if not repo:
        return ()
    try:
        r = subprocess.run(["git", "-C", repo, "remote"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ()
    return tuple(x for x in r.stdout.split() if x) if r.returncode == 0 else ()


def branch_of(st, name):
    """A thread's branch; a hand-edited ledger without the field falls back to the default name."""
    return st["threads"][name].get("branch") or f"tw/{st['project']}/{name}"


def _branch_problem(st, name, b):
    """Why b cannot be thread `name`'s branch, or None. Shell-safe characters only (the branch goes into printed
    git commands), git's ref rules, never the base, and no clash with another thread's branch: same name in any
    case (macOS refs ignore case) or one inside the other (`x` and `x/y` cannot both exist)."""
    if not b or re.fullmatch(r"[A-Za-z0-9._/+-]+", b) is None:
        return f"--branch {b!r}: use only letters, digits and . _ / + - (it goes into shell commands)"
    parts = b.split("/")
    if b.startswith("-") or b in ("@", "HEAD") or any(not c or c.startswith(".") or c.endswith((".", ".lock"))
                                                       for c in parts) or ".." in b:
        return f"--branch {b!r} is not a valid git branch name"
    base = st.get("repo", {}).get("base") or "main"
    lb, lbase = b.lower(), base.lower()
    ns = f"tw/{st.get('project', '')}".lower()  # the namespace of the default thread branches
    remote = parts[0].lower() in ("origin", "refs", "remotes") or (
        not lb.startswith(ns + "/") and parts[0].lower() in {x.lower() for x in _remotes(st)})
    if lb == lbase or lb.startswith(lbase + "/") or lbase.startswith(lb + "/") or remote:
        return f"--branch {b!r}: a thread works on its own branch, never the base (or one inside it) or a remote one"
    if ns.startswith(lb + "/") or lb == ns:
        return f"--branch {b!r}: holds every thread's default branch ({ns}/<thread>); no new thread could be added"
    if lb.startswith(ns + "/") and lb != f"{ns}/{name}".lower():
        return (f"--branch {b!r}: {ns}/<name> is the default branch of thread <name> (rebuild reads it that way); "
                f"use {ns}/{name} or a name outside {ns}/")
    for n, o in st["threads"].items():
        ob = (o.get("branch") or "").lower()
        if n != name and ob and (ob == lb or ob.startswith(lb + "/") or lb.startswith(ob + "/")):
            return f"--branch {b}: clashes with {n}'s branch {o['branch']}; one branch per thread"
    return None


def cmd_thread(a):
    d = need(find_dir())
    st = load(d)
    name = a.name
    if a.session is not None:
        a.session = a.session.strip()
    if a.session and not_a_session(a.session, {"threads": dict(st["threads"], **{name: {}})}):
        sys.exit(not_a_session(a.session, {"threads": dict(st["threads"], **{name: {}})}))
    if a.action == "add" and a.cloud_reason is not None and (a.kind or "rc") != "cloud":
        sys.exit("--cloud-reason goes with --kind cloud; an rc thread needs no reason")
    if a.action == "add" and (a.kind or "rc") == "cloud" and not (a.cloud_reason or "").strip():
        sys.exit("a cloud thread needs --cloud-reason \"<why>\": rc is the default. Use cloud only when the thread never "
                 "touches the project folder AND never needs to message rc threads (a cloud thread cannot message rc "
                 "sessions, and once its trust card is approved rc sessions, the lead included, can no longer message "
                 "it or read its event log). If rc seats are full, ask "
                 "the user instead of switching to cloud")
    if a.branch is not None:
        a.branch = a.branch.strip()
    if a.action != "add" and a.branch is not None and a.branch == (st["threads"].get(name) or {}).get("branch"):
        a.branch = None  # already its branch: nothing to change
    if a.action == "add" and name in st["threads"]:
        pass  # "taken" below
    elif a.action == "add" or a.branch is not None:
        why = _branch_problem(st, name, a.branch if a.branch is not None else f"tw/{st['project']}/{name}")
        if why:
            sys.exit(why)
    if a.action != "add" and a.branch is not None:
        t0 = st["threads"].get(name) or {}
        if a.action == "set" and t0 and t0.get("worktree") and Path(t0["worktree"]).exists():
            sys.exit(f"--branch: {name}'s worktree {t0['worktree']} already exists on {branch_of(st, name)}; the "
                     "branch is fixed once its worktree is made")
        if a.action != "set" or not t0 or t0.get("session_id") or t0.get("sessions") or t0.get("status") != "planned":
            sys.exit("--branch changes only while the thread is planned and has never had a session (the user editing "
                     "the plan); after that the branch is fixed: leases and trace follow it, so work there would stop "
                     "counting. Add a new thread for a new branch")
        repo, old = st.get("repo", {}).get("path"), branch_of(st, name)
        held = [ref for ref in (f"refs/heads/{old}", f"refs/remotes/origin/{old}") if repo and subprocess.run(
            ["git", "-C", repo, "show-ref", "--verify", "--quiet", ref], capture_output=True).returncode == 0]
        if held:
            sys.exit(f"--branch: {old} already exists in the repo ({', '.join(held)}; its worktree was removed); "
                     f"renaming the thread's branch would orphan that work. Rename the git branch yourself "
                     f"(`git branch -m {old} {a.branch}`, and the pushed copy) and then set it here, or keep it")
    if a.action != "add" and (a.kind is not None or a.cloud_reason is not None):
        t0 = st["threads"].get(name) or {}
        if a.action != "set" or not t0 or t0.get("session_id") or t0.get("sessions") or t0.get("status") != "planned":
            sys.exit("--kind and --cloud-reason change only while the thread is planned and has never had a session "
                     "(the user editing the plan); after that its kind is fixed: add a new thread")
        kind = a.kind or t0["kind"]
        if kind == "cloud" and not (a.cloud_reason or t0.get("cloud_reason") if a.kind is None else a.cloud_reason or "").strip():
            sys.exit("a cloud thread needs --cloud-reason \"<why>\" (see thread add)")
        if kind == "rc" and a.cloud_reason is not None:
            sys.exit("--cloud-reason goes with --kind cloud; an rc thread needs no reason")
    if a.session and a.action in ("add", "set", "reseat"):
        other = _session_taken(st, a.session, name)
        if other:
            sys.exit(f"session {a.session} already holds the {other} seat; one session holds one seat")
        if a.session == (st.get("lead") or {}).get("session_id"):
            sys.exit(f"session {a.session} is the lead; a thread needs its own session")
    if a.action in ("set", "reseat") and name in st["threads"] and a.status \
            and (not a.session or a.session == st["threads"][name].get("session_id")) \
            and st["threads"][name]["status"] == "archived" and a.status != "archived":
        sid0 = st["threads"][name].get("session_id")
        hist0 = st["threads"][name].get("sessions") or []
        closed = sid0 and any(h.get("session_id") == sid0 for h in hist0) and \
            not any(h.get("session_id") == sid0 and not h.get("to") for h in hist0)
        other = _session_taken(st, sid0, name) or \
            ("the lead" if sid0 and sid0 == (st.get("lead") or {}).get("session_id") else None) or \
            ("another seat or the lead (its stint here has ended)" if closed else None)
        if other:  # reviving an archived seat whose session has moved on to another seat or the lead
            sys.exit(f"{name}'s session now belongs to {other}: reseat {name} with a new --session instead")
    if a.action == "add":
        if not re.fullmatch(NAME_RE, name):
            sys.exit(
                "thread name: lowercase letters/digits/hyphen, max 24 chars (e.g. ui, api, research-2)"
            )
        if name in ("lead", "all") or name in st["threads"]:
            sys.exit(f"thread name {name} is taken")
        a.kind = a.kind or "rc"
        st["threads"][name] = {
            "kind": a.kind,
            "focus": a.focus or "",
            "owns": csv(a.owns),
            "session_id": a.session,
            "status": "planned" if not a.session else (a.status or "idle"),
            "branch": a.branch or f"tw/{st['project']}/{name}",
            "created": now(),
            "last_seen": None,
            "summary": a.summary or "",
            "sessions": [{"session_id": a.session, "from": now()}] if a.session else [],
        }
        if a.session:
            _close_archived(st, a.session, name, now())
        if a.kind == "cloud":
            st["threads"][name]["cloud_reason"] = a.cloud_reason.strip()
        if a.mode:
            st["threads"][name]["permission_mode"] = a.mode
        if (a.model or "").strip():
            st["threads"][name]["model"] = a.model.strip()
        if a.kind == "rc":
            st["threads"][name]["worktree"] = str(worktree_path(st, name))
        st["threads"][name]["title"] = title_for(st, name)
        bump_roster(st, name)
        save(
            d,
            st,
            {"ev": "thread-add", "thread": name, "kind": a.kind, "session": a.session},
        )
        t = st["threads"][name]
        out = {
            "name": name,
            "title": t["title"],
            "branch": branch_of(st, name),
            "tags": [f"teamwork:{tag(st)}", f"teamwork-thread:{name}"],
            "environment_id": st["envs"].get(a.kind),
            "create_session": session_args(st, name),
        }
        if t.get("worktree"):
            out["worktree"] = t["worktree"]
            out["next"] = f"tw.py worktree {name}  (before create_session)"
        if title_warning(t["title"]):
            out["warning"] = title_warning(t["title"])
        print(json.dumps(out, ensure_ascii=False))
        return
    if a.action == "set" and a.roster_sent and name == "all":
        if any(getattr(a, x, None) for x in ("status", "summary", "owns", "focus", "session", "from_status")):
            sys.exit("`thread set all --roster-sent` takes no other options; set threads one by one")
        for n in roster_stale(st):
            st["threads"][n]["roster_seen"] = st.get("roster_version", 0)
        save(d, st, {"ev": "roster-sent", "to": "all", "version": st.get("roster_version", 0),
                     "snap_threads": st["threads"]})
        print(json.dumps({"roster_version": st.get("roster_version", 0), "not_sent": roster_stale(st)}))
        return
    if name not in st["threads"]:
        sys.exit(f"unknown thread {name}; known: {', '.join(st['threads']) or '-'}")
    t = st["threads"][name]
    if a.kind is not None or a.cloud_reason is not None:  # planned seat, checked above: the user edited the plan
        t["kind"] = a.kind or t["kind"]
        if t["kind"] == "cloud":
            t["cloud_reason"] = (a.cloud_reason or t.get("cloud_reason") or "").strip()
            t.pop("worktree", None)
        else:
            t.pop("cloud_reason", None)
            t["worktree"] = str(worktree_path(st, name))
    roster_before = (t.get("session_id"), t.get("owns"), t["status"] == "archived")
    if a.action == "reseat":
        if not a.session:
            sys.exit("reseat needs --session <new session id> (create the new session first)")
        if a.session == t.get("session_id"):
            sys.exit(f"{a.session} already holds the {name} seat")
        if a.status and a.status not in ("starting", "working", "waiting", "idle"):
            sys.exit("reseat --status must be starting, working, waiting or idle (a new live session)")
    reason = a.reason or ("reseated" if a.action == "reseat" else "session changed")
    prev_session = t.get("session_id")
    if a.session and a.session != t.get("session_id"):
        hist = t.setdefault("sessions", [])
        ts = now()  # one instant: the old stint ends exactly when the new one starts
        if t.get("session_id") and not any(h.get("session_id") == t["session_id"] for h in hist):
            hist.append({"session_id": t["session_id"], "from": t.get("created")})
        for h in hist:
            if h.get("session_id") == t.get("session_id") and not h.get("to"):
                h["to"] = ts
                h["reason"] = reason
        hist.append({"session_id": a.session, "from": ts})
        t["session_id"] = a.session
        _close_archived(st, a.session, name, ts)
        if a.action == "reseat":
            if not a.status:
                t["status"] = "starting"
            t["last_status_at"] = ts  # the new session gets a fresh stuck timer (same instant: not a report)
    if a.status:
        if a.status not in THREAD_STATUSES:
            sys.exit(f"status must be one of {list(THREAD_STATUSES)}")
        t["status"] = a.status
    if a.summary is not None:
        t["summary"] = a.summary
    if a.mode:
        t["permission_mode"] = a.mode
    if a.model is not None:
        t["model"] = a.model.strip()  # IL-31; empty = the lead's model
    if a.from_status:
        t["last_status_at"] = now()
    if a.owns is not None:
        t["owns"] = csv(a.owns)
    if a.focus is not None:
        t["focus"] = a.focus
        t["title"] = title_for(st, name)
    if a.branch is not None and a.branch != t.get("branch"):
        t["branch"] = a.branch
        bump_roster(st)  # the roster and worker prompt name the branch
    if (t.get("session_id"), t.get("owns"), t["status"] == "archived") != roster_before:
        bump_roster(st)  # peers need a new ROSTER; t itself learns it with the same broadcast
    if a.roster_sent:
        t["roster_seen"] = st.get("roster_version", 0)
    t["last_seen"] = now()
    save(
        d,
        st,
        {
            "ev": "thread-reseat" if a.action == "reseat" else "thread-set",
            "thread": name,
            "status": t["status"],
            "summary": t["summary"],
            **({"session": t["session_id"], "prev_session": prev_session, "reason": reason}
               if t.get("session_id") != prev_session else {}),
        },
    )
    print(json.dumps({name: t}, ensure_ascii=False, indent=2))


def cmd_task(a):
    d = need(find_dir())
    st = load(d)
    if a.action == "reviews":
        if a.id != "-" and a.id not in st["tasks"]:
            sys.exit(f"unknown task {a.id}")
        print(render_reviews(st, None if a.id == "-" else a.id))
        return
    if a.id == "-":
        sys.exit(f"task {a.action} needs a task id")
    if a.action == "followups":
        return task_followups(d, st, a)
    if a.action == "add":
        if a.id in st["tasks"]:
            sys.exit(f"task {a.id} exists")
        if a.owner and a.owner not in st["threads"]:
            sys.exit(f"unknown owner {a.owner} (add the thread first)")
        st["tasks"][a.id] = {
            "title": a.title or a.id,
            "owner": a.owner,
            "deps": csv(a.deps),
            "accept": a.accept or "",
            "touches": check_touches(a.touches),
            "topics": norm_topics(a.topics),
            **({"model": a.model.strip()} if (a.model or "").strip() else {}),
            **({"route": a.route} if a.route else {}),
            **({"no_scripts": True} if a.no_scripts else {}),
            "status": "approved" if st["plan_approved"] else "planned",
            "notes": "",
            "created": now(),
        }
        save(d, st, {"ev": "task-add", "task": a.id, "owner": a.owner})
        warn = topic_mismatch(st, a.owner, a.title)  # no --title: nothing to compare yet
        if warn:
            print(warn, file=sys.stderr)
    else:
        if a.id not in st["tasks"]:
            sys.exit(f"unknown task {a.id}")
        k = st["tasks"][a.id]
        cur = k["status"]
        said = (a.user_said or "").strip()
        stale = {}  # follow-up tasks opened from a verdict that is now void (T23-c)

        def void(why, restart=False):
            if k.get("review"):
                old = k.pop("review")
                k.setdefault("past_reviews", []).append({**old, "voided": why})
                for t, x in st["tasks"].items():
                    if x.get("followup_of") == a.id and x.get("followup_item") in (old.get("remaining") or []) \
                            and x.get("status") not in ("done", "dropped") and not x.get("parent_verdict_voided"):
                        x["parent_verdict_voided"] = why
                        stale[t] = x
            if restart and k.get("review_rounds"):  # a new criterion or a new owner: the round count starts over
                k.setdefault("past_reviews", []).append({"rounds_reset": k.pop("review_rounds"), "why": why,
                                                         "at": now()})
        # 0. the move itself must be legal before anything is changed
        if a.status and a.status != cur:
            if a.status not in TASK_STATUSES:
                sys.exit(f"status must be one of {TASK_STATUSES}")
            allowed = TASK_MOVES.get(cur, set())
            if a.status not in allowed:
                sys.exit(f"{a.id} is {cur}: it can move to {', '.join(sorted(allowed)) or 'nothing (it has ended)'}"
                         + (" (done only from review)" if a.status == "done" else ""))
        if cur in ("done", "dropped") and any(x is not None for x in (a.owner, a.accept, a.deps)):
            sys.exit(f"{a.id} has ended ({cur}): its owner, 'done when' and deps are final")
        # 1. field edits first, so every check below sees the task as it will be stored
        if a.owner is not None:
            if a.owner and a.owner not in st["threads"]:
                sys.exit(f"unknown owner {a.owner}")
            if (a.owner or None) != k.get("owner"):
                rv = k.get("review") or {}
                if rv.get("result") in PASSING and not reviewer_independent(rv.get("reviewer") or "", a.owner or ""):
                    void("its reviewer became the owner")
                if (k.get("review") or {}).get("result") == "capped":  # capped stood on the old owner's rounds
                    void("owner changed: capped stood on the old owner's rounds")
                    print(f"{a.id}: capped verdict voided by the owner change; review again", file=sys.stderr)
                if k.get("review_rounds"):  # the rounds were the old owner's: a new owner starts over
                    k.setdefault("past_reviews", []).append({"rounds_reset": k.pop("review_rounds"),
                                                             "why": "owner changed", "at": now()})
                if k.get("owner") and k["owner"] not in k.setdefault("past_owners", []):
                    k["past_owners"].append(k["owner"])  # wrote part of the work: never its reviewer
                k["owner"] = a.owner or None
        if a.accept is not None and a.accept != k.get("accept"):
            void("'done when' changed", restart=True)  # the verdict was against the old criterion
            k["accept"] = a.accept
        old_deps = list(k.get("deps") or [])
        if a.deps is not None:
            k["deps"] = csv(a.deps)
        if a.title:
            k["title"] = a.title
        if a.touches is not None:
            k["touches"] = check_touches(a.touches)
        if a.model is not None:
            if a.model.strip():
                k["model"] = a.model.strip()
            else:
                k.pop("model", None)
        if a.route is not None:
            if a.route.strip():
                k["route"] = a.route.strip()
            else:
                k.pop("route", None)
        if a.no_scripts:
            k["no_scripts"] = True
        if a.scripts_ok:
            k.pop("no_scripts", None)
        if a.topics is not None:
            k["topics"] = norm_topics(a.topics)
        if a.notes is not None:
            k["notes"] = a.notes
        if getattr(a, "merged", None) is not None:  # B4: the board's progress chain reads this mark
            if (a.status or cur) not in ("review", "done"):
                sys.exit("--merged: only a task in review or done has merged work")
            if not re.fullmatch(r"[0-9a-fA-F]{7,40}", a.merged.strip()):
                sys.exit("--merged: the merge commit (7 to 40 hex characters)")
            k["merged_at"], k["merged_sha"] = now(), a.merged.strip().lower()
        if a.checks:
            if cur == "done" and a.checks != k.get("checks"):  # T23-a: a done task's verdict and checks are final
                sys.exit(f"{a.id} is done: its checks and verdict are final. A regression is new work: add a new task for it"
                         )
            if k.get("checks") == "fail" and a.checks == "none":
                sys.exit(f"{a.id}: checks failed; only `--checks pass` clears that")
            if a.checks != "pass" and (k.get("review") or {}).get("result") == "capped":
                void("checks no longer passing")  # capped stood on green checks
            k["checks"] = a.checks
        owner = k.get("owner") or ""
        # 2. a review verdict, only on work that is in review, by someone independent of the owner
        if a.rounds is not None and not a.review:
            sys.exit("--rounds goes with --review (the number of review rounds the thread reported)")
        if a.rounds is not None and not 1 <= a.rounds <= 99:
            sys.exit("--rounds: 1 to 99")
        if a.review:
            if not (a.reviewer or "").strip():
                sys.exit("--review needs --reviewer (a fresh subagent of the owner, a review thread, ...)")
            if cur != "review":
                sys.exit(f"{a.id} is {cur}: record a review only while it is in review "
                         f"(`task set {a.id} --status review` once the work is there)")
            if not owner:
                sys.exit(f"{a.id} has no owner: an independent review needs to know whose work it is")
            past = next((o for o in k.get("past_owners") or [] if not reviewer_independent(a.reviewer, o)), None)
            if past:
                sys.exit(f"{a.reviewer} owned {a.id} before and wrote part of it: get a reviewer without its context")
            if not reviewer_independent(a.reviewer, owner):
                sys.exit(f"{a.reviewer} owns {a.id}: the reviewer must be someone without its context "
                         f"(e.g. {owner}/subagent, a fresh subagent the owner starts)")
            prev = int(k.get("review_rounds") or 0)
            if a.rounds is not None and a.rounds < max(prev, 1):
                sys.exit(f"{a.id}: {prev} review rounds are already recorded; --rounds only goes up "
                         "(the thread's own count, from its DONE)")
            rounds = a.rounds if a.rounds is not None else prev + 1
            remaining = list(dict.fromkeys(x.strip() for x in re.split(r"\s*;\s*|\n", a.remaining or "") if x.strip()))
            if a.remaining is not None and a.review not in PASSING:
                sys.exit("--remaining goes with --review pass or capped (important findings left for follow-up tasks)")
            if a.no_blocking and not (a.review == "capped" or (a.review == "pass" and remaining)):
                sys.exit("--no-blocking goes with --review capped, or pass with --remaining")
            if a.review == "pass" and remaining:  # T23-b: the protocol's "two rounds without a new important one"
                if rounds < 2:
                    sys.exit(f"{a.id}: pass with --remaining needs at least 2 review rounds (two in a row without a "
                             "new important finding); with one round, fix them or record fail")
                if k.get("checks") != "pass":
                    sys.exit(f"{a.id}: pass with --remaining needs the checks green: record `--checks pass`")
                if not a.no_blocking:
                    sys.exit(f"{a.id}: pass with --remaining needs --no-blocking (no blocking finding left open)")
            if a.review == "capped":
                cap = review_max(st)
                if not cap or rounds < cap:
                    sys.exit(f"{a.id}: review round {rounds} of {cap or 'unlimited'}; `capped` is only for the last "
                             "allowed round (prefs review_max_rounds). Pass the thread's own round count from its DONE "
                             "with --rounds N; otherwise fix blocking and important findings and review again")
                if k.get("checks") != "pass":
                    sys.exit(f"{a.id}: capped needs the checks green: record `--checks pass` (in the same call is fine)")
                if not a.no_blocking:
                    sys.exit(f"{a.id}: capped needs --no-blocking: the last round left no blocking finding "
                             "(a blocking one is a fail)")
                if not remaining:
                    sys.exit(f"{a.id}: capped needs --remaining \"<finding>; <finding>\" (the important findings left "
                             "for follow-up tasks); with none left, record pass")
            if k.get("review"):  # a new verdict replaces the old one: it goes to past_reviews like any voided one
                void(f"replaced by a {a.review} verdict")
            k["review_rounds"] = rounds
            k["review"] = {"result": a.review, "reviewer": a.reviewer.strip(), "note": a.review_note or "",
                           "round": rounds, "cap": review_max(st), "at": now(),
                           **({"remaining": remaining} if remaining else {})}
            if a.review in PASSING:  # the new verdict lists the same finding again: its follow-up stands
                for t, x in st["tasks"].items():
                    if x.get("followup_of") == a.id and x.get("parent_verdict_voided") \
                            and x.get("followup_item") in remaining:
                        x.pop("parent_verdict_voided")
                        stale[t] = x
            if a.review == "capped":
                print(f"{a.id}: capped at round {rounds}; open its follow-ups with `tw.py task followups {a.id} --apply`",
                      file=sys.stderr)
            if a.review == "fail" and not a.status:
                k["status"] = "working"
                if k.get("checks") == "pass":
                    k["checks"] = "none"  # the fix changes the code: run the checks again
                print(f"{a.id} back to working: send its owner a FOLLOWUP with the review findings", file=sys.stderr)
        # 3. preconditions of the status move, against the edited task
        if a.status and a.status != k["status"]:
            if a.status == "approved" and not st.get("plan_approved"):
                sys.exit(f"{a.id}: tasks are approved with the plan: `plan approve --user-said ...` once the user agrees")
            if a.status == "dispatched":
                deps = list(dict.fromkeys(old_deps + (k.get("deps") or [])))  # clearing deps here skips nothing
                open_deps = [x for x in deps if st["tasks"].get(x, {}).get("status") != "done"]
                if open_deps:
                    sys.exit(f"{a.id} waits for {', '.join(open_deps)} (deps not done)")
                if not st.get("plan_approved"):
                    sys.exit(f"{a.id}: the plan is reopened; `plan approve` again before dispatching")
                if not owner:
                    sys.exit(f"{a.id} has no owner: `task set {a.id} --owner <thread>` first")
            if a.status == "dropped" and not said:
                sys.exit(f"dropping {a.id} is the user's decision: add --user-said \"<their words>\"")
            if a.skip_review and (not said or not a.skip_review.strip()):
                sys.exit("--skip-review \"<reason>\" needs --user-said \"<the user's words>\": skipping review is "
                         "their call")
            if a.status == "done":
                if not owner:
                    sys.exit(f"{a.id} has no owner: set it before marking done")
                if st["prefs"].get("require_review", True) and not a.skip_review:
                    rv = k.get("review") or {}
                    if rv.get("result") not in PASSING:
                        sys.exit(f"{a.id}: no passing review yet. Have an independent reviewer check the diff against "
                                 f"'done when', then `task set {a.id} --review pass --reviewer <who>` "
                                 "(or --skip-review \"<reason>\" --user-said \"<their words>\")")
                    if not all(reviewer_independent(rv.get("reviewer") or "", o)
                               for o in [owner] + list(k.get("past_owners") or [])):
                        sys.exit(f"{a.id}: its reviewer {rv.get('reviewer')} owns it; get another review")
                    if rv.get("result") == "capped" and k.get("checks") != "pass":
                        sys.exit(f"{a.id}: capped at the round limit, which needs `--checks pass`")
                if k.get("checks") == "fail":
                    sys.exit(f"{a.id}: checks failed; fix them and record `--checks pass` first (skipping review "
                             "does not skip checks)")
                if a.skip_review:
                    k["review"] = {"result": "skipped", "note": a.skip_review.strip(), "user_said": said, "at": now()}
            if a.status == "done" and not a.skip_review and not str(k.get("accept") or "").strip():
                sys.exit(f"{a.id} has no 'done when': set --accept first (a review needs something to check against)")
            if k["status"] == "review" and a.status == "working":
                void("rework after review")  # the old verdict no longer counts
                if k.get("checks") == "pass" and a.checks is None:
                    k["checks"] = "none"  # green checks were for the old code
            if a.status == "working" and cur in ("review", "done"):  # rework: the earlier merge mark is stale
                k.pop("merged_at", None); k.pop("merged_sha", None)
            k["status"] = a.status
            k["updated"] = now()
            if a.status == "dispatched":
                k["dispatch_v"] = int(k.get("dispatch_v") or 0) + 1
                k["dispatched_at"] = now()
            if a.status in ("review", "done", "dropped"):  # dropping the last open task can deliver the rest
                for r in deliver_ready(st):
                    print(f"request {r} delivered (all its tasks are in review or done): report it to the user, then "
                          f"`tw.py req set {r} --status reported`", file=sys.stderr)
        marked = sorted(t for t, x in stale.items() if x.get("parent_verdict_voided"))
        if marked:
            print(f"{a.id}: follow-ups {', '.join(marked)} came from a voided verdict; after the next review keep them "
                  "(a pass or capped that lists the same finding clears the mark) or drop them with the user's OK",
                  file=sys.stderr)
        if k["status"] not in ("review", "done"):
            for r in undeliver(st):
                print(f"request {r} back to dispatched: {a.id} is {k['status']} again", file=sys.stderr)
        save(
            d,
            st,
            {
                "ev": "task-set",
                "task": a.id,
                "status": k["status"],
                "owner": k["owner"],
                **({"user_said": said} if said else {}),
                **({"snap_more_tasks": stale} if stale else {}),
            },
        )
        if a.owner or a.title:  # after the save: warn only about what was stored
            warn = topic_mismatch(st, k.get("owner"), k.get("title") if k.get("title") != a.id else None)
            if warn:
                print(warn, file=sys.stderr)
    print(render_tasks(st))


def cmd_plan(a):
    d = need(find_dir())
    st = load(d)
    if a.action == "approve":
        if not (a.user_said or "").strip():
            sys.exit("approving the plan is the user's decision: `plan approve --user-said \"<their words>\"`")
        st.setdefault("plan_user_said", []).append({"t": now(), "text": a.user_said.strip()})
        st["plan_approved"] = True
        for k in st["tasks"].values():
            if k["status"] == "planned":
                k["status"] = "approved"
        save(d, st, {"ev": "plan-approved"})
    elif a.action == "reopen":
        st["plan_approved"] = False
        save(d, st, {"ev": "plan-reopened"})
    print(render_board(st))


LEASE_ACTIVE = ("dispatched", "working", "blocked", "review")


def _norm_path(x):
    """'./web//a/../b/' -> 'web/b/' (trailing slash kept: it marks a folder); '.' or '/' -> '**' (everything)."""
    import posixpath
    x = x.strip().replace("\\", "/")
    if not x:
        return ""
    folder = x.endswith("/")
    x = posixpath.normpath(x.lstrip("/") or ".")
    if x in (".", ""):
        return "**"
    return x + "/" if folder and not x.endswith("/") else x


_GLOB = "*?["
_GLOB_TOK = re.compile(r"\*|\?|\[!?\]?[^\]]*\]")  # glob tokens as fnmatch reads them ('[' alone is literal)


def _expand_braces(p, limit=64):
    """'web/{a,b}.py' -> ['web/a.py', 'web/b.py'] (nested groups too). Past `limit` names the pattern is widened:
    the folder part from the first brace on becomes '**' (a superset across folders, so still conservative)."""
    def count(x, depth=0):
        m = re.search(r"\{([^{}]*,[^{}]*)\}", x)
        if not m or depth > 12:
            return 1 if not m else limit + 1
        n = 0
        for alt in m.group(1).split(","):
            n += count(x[:m.start()] + alt + x[m.end():], depth + 1)
            if n > limit:
                return n
        return n
    if count(p) > limit:
        head = p[:p.index("{")]
        return [head[:head.rfind("/") + 1] + "**"]
    m = re.search(r"\{([^{}]*,[^{}]*)\}", p)
    if not m:
        return [p]
    out = []
    for alt in m.group(1).split(","):
        out += _expand_braces(p[:m.start()] + alt + p[m.end():], limit)
    return out


def _seg_meet(x, y):
    """Can one path segment match both glob segments? Exact for literals; for two wildcards a conservative check
    on their literal head and tail (may say yes when unsure, never no when they can meet)."""
    import fnmatch
    if x == y:
        return True
    gx, gy = any(c in x for c in _GLOB), any(c in y for c in _GLOB)
    if not gx and not gy:
        return False
    if not gx:
        return fnmatch.fnmatchcase(x, y)
    if not gy:
        return fnmatch.fnmatchcase(y, x)
    mx, my = list(_GLOB_TOK.finditer(x)), list(_GLOB_TOK.finditer(y))
    if not mx or not my:  # only an unclosed '[': fnmatch reads it literally
        return fnmatch.fnmatchcase(x, y) if not mx else fnmatch.fnmatchcase(y, x) if not my else x == y
    hx, hy = x[:mx[0].start()], y[:my[0].start()]
    tx, ty = x[mx[-1].end():], y[my[-1].end():]
    return (hx.startswith(hy) or hy.startswith(hx)) and (tx.endswith(ty) or ty.endswith(tx))


def _globs_meet(a, b):
    """Is there a path that matches both segment lists? '**' matches zero or more segments."""
    from functools import lru_cache
    A, B = tuple(a), tuple(b)

    @lru_cache(maxsize=None)
    def go(i, j):
        if i == len(A) and j == len(B):
            return True
        if i < len(A) and A[i] == "**":
            return go(i + 1, j) or (j < len(B) and go(i, j + 1)) or (j < len(B) and B[j] == "**" and go(i + 1, j + 1))
        if j < len(B) and B[j] == "**":
            return go(i, j + 1) or (i < len(A) and go(i + 1, j))
        if i == len(A) or j == len(B):
            return False
        return _seg_meet(A[i], B[j]) and go(i + 1, j + 1)

    return go(0, 0)


@functools.lru_cache(maxsize=4096)
def _lease_forms_cached(p):
    return tuple(tuple(x) for x in _lease_forms_raw(p))


def _lease_forms(p):
    return _lease_forms_cached(p)


def _lease_forms_raw(p):
    """Segment lists a lease covers: a folder ('web/', or a literal 'web' that may be one) covers all below it;
    a glob without '/' ('*.py') matches in any folder.
    Case-folded: on a case-insensitive disk Web/App.py and web/app.py are one file."""
    out = []
    for q in _expand_braces(_norm_path(p).lower()):
        folder = q.endswith("/")
        segs = [x for x in q.strip("/").split("/") if x]
        if not segs:
            continue
        out.append(segs + ["**"] if folder else segs)
        if len(segs) == 1 and any(c in segs[0] for c in _GLOB) and segs[0] != "**":
            out.append(["**"] + out[-1])  # '*.py' means .py files in any folder, as in .gitignore
            if not folder:
                out.append(["**"] + segs + ["**"])  # ... or a folder of that name at any depth
        if not folder and segs[-1] != "**":
            out.append(segs + ["**"])  # 'web' or 'web/*' may name a folder: cover what is below it
    return out


def _split_touches(s):
    """'web/{a,b}.js, api/x.py' -> ['web/{a,b}.js', 'api/x.py']: commas inside {...} belong to the brace."""
    out, cur, depth = [], "", 0
    for ch in s or "":
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
            continue
        depth += (ch == "{") - (ch == "}" and depth > 0)
        cur += ch
    out.append(cur)
    return [x.strip() for x in out if x.strip()]


def check_touches(s):
    depth = 0
    for ch in s or "":
        depth += (ch == "{") - (ch == "}")
        if depth < 0:
            break
    if depth != 0:
        sys.exit(f"--touches has unbalanced braces: {s}")
    out = _split_touches(s)
    bad = [x for x in out for y in _expand_braces(x) if _norm_path(y).startswith("../") or _norm_path(y) == ".."]
    if bad:
        sys.exit(f"--touches must stay inside the repo: {', '.join(bad)}")
    return out


class RealFile(str):
    """A file name from git (a real file, never a folder, never a glob: '*', '[', '\\' and braces are literal)."""


def _file_segs(f):
    return tuple(x for x in f.lower().split("/") if x)


@functools.lru_cache(maxsize=65536)
def _lit_meet(f, g):
    """Does the literal path f (segments) match the glob g (segments, '**' spans folders)?"""
    import fnmatch
    if not g:
        return not f
    if g[0] == "**":
        return any(_lit_meet(f[i:], g[1:]) for i in range(len(f) + 1))
    return bool(f) and (f[0] == g[0] or fnmatch.fnmatchcase(f[0], g[0])) and _lit_meet(f[1:], g[1:])  # 'app/[id]/' is
    # also a literal folder name (Next.js, SvelteKit), as git pathspecs read it


def paths_overlap(a, b):
    """True if some file could match both path globs (or a glob and a real path). Conservative when unsure."""
    if isinstance(a, RealFile) or isinstance(b, RealFile):
        f, other = (a, b) if isinstance(a, RealFile) else (b, a)
        if isinstance(other, RealFile):
            return _file_segs(f) == _file_segs(other)
        fs = _file_segs(f)
        raw = _file_segs(_norm_path(other))  # the lease read literally too: a real file named '{a,b}.py'
        return any(_lit_meet(fs, g) for g in {raw, raw + ("**",)} | {tuple(g) for g in _lease_forms(other)})
    return any(_globs_meet(x, y) for x in _lease_forms(a) for y in _lease_forms(b))


def _base_ref(st, git):
    """origin/<base> or <base>, whichever is newer (a stale origin would count merged work as unmerged)."""
    base = st.get("repo", {}).get("base") or "main"
    have = [b for b in (f"origin/{base}", base)
            if git("rev-parse", "--verify", "--quiet", b + "^{commit}").returncode == 0]
    if len(have) == 2 and git("merge-base", "--is-ancestor", have[0], have[1]).returncode == 0:
        return have[1]  # local base has everything origin has, and maybe more (merged but not pushed yet)
    return have[0] if have else None


_GIT_CACHE = {}


def _git_cached(repo):
    """git runner for one repo; identical read-only calls within one tw.py run are answered from memory."""
    def git(*x):
        key = (repo, x)
        if key not in _GIT_CACHE:
            try:
                _GIT_CACHE[key] = subprocess.run(["git", "-c", "core.quotePath=false", "-C", repo, *x],
                                                 capture_output=True, text=True)
            except OSError as e:  # no git program: same as "not a git repo" (declared paths only)
                _GIT_CACHE[key] = subprocess.CompletedProcess(x, 127, "", str(e))
        return _GIT_CACHE[key]
    return git


def _z(out):
    """NUL-separated git name list (-z): names with quotes, tabs, newlines or backslashes stay whole."""
    return [n for n in out.split("\x00") if n]


_ID_RX = lambda tid: re.compile(rf"(^|[^A-Za-z0-9_]){re.escape(tid)}(?![0-9A-Za-z_]|[.-][0-9A-Za-z])")


def _subject_leads_with(subj, tids, rxs=None):
    """True when a commit subject starts with one of the task ids (`T3: ...`, `[T3] ...`, `fix(T3): ...`)."""
    rxs = rxs or [_ID_RX(t) for t in tids]
    head = re.match(r"\s*(?:\[([^\]]+)\]|\w+\(([^)]+)\)|([^\s:]+))", subj)
    lead = head and next((g for g in head.groups() if g), "")
    return bool(lead) and (any(rx.fullmatch(lead.strip()) for rx in rxs) or lead.strip() in tids)


_TOKEN_RX = re.compile(r"[A-Za-z0-9_]+(?:[.-][0-9A-Za-z]+)*")  # one id-like word, as _ID_RX delimits task ids
_TOUCHES_CACHE = {}


def _branch_touches(git, base, br):
    """{file: [(lead id, words in the subject, plain)]} for the commits on br since base, parsed once per run.
    lead: the subject's leading id (`T5: ...`, `[T5] ...`, `fix(T5): ...`); words: lowercased, each also cut
    at its first `-` or `.` (`T29-related` names T29: a near miss holds); plain: an add / modify / delete in a
    one-parent commit. Renames git detects (both names) and every file a merge brought in (first parent) are
    never plain, whatever the subject says. Copies are not looked for (`--find-copies-harder` is too slow), and
    a rename git sees as delete + add counts as plain."""
    args = ("-z", "-M", "--relative", "--name-status", "--format=%x01%P%x02%s", f"{base}..{br}")
    r = git("log", "--diff-merges=first-parent", *args)  # a merge lists what it brought in (git >= 2.31) ...
    log = (r if r.returncode == 0 else git("log", "-m", *args)).stdout  # ... older git: against every parent
    key = log  # git calls are cached per run; the parse is shared by every task that reads this branch
    if key not in _TOUCHES_CACHE:
        touches = {}
        for chunk in log.split("\x01")[1:]:
            subj, _, names = chunk.partition("\x00")
            parents, _, subj = subj.partition("\x02")
            merge = len(parents.split()) > 1
            head = re.match(r"\s*(?:\[([^\]]+)\]|\w+\(([^)]+)\)|([^\s:]+))", subj)
            lead = ((head and next((g for g in head.groups() if g), "")) or "").strip()
            words = {w.lower() for w in _TOKEN_RX.findall(subj)}
            words = frozenset(words | {re.split(r"[.-]", w, 1)[0] for w in words})
            tok, i = _z(names[1:] if names.startswith("\n") else names), 0
            while i < len(tok):
                two = tok[i].strip()[:1] in ("R", "C")
                paths = tok[i + 1:i + 3] if two else tok[i + 1:i + 2]
                for f in paths:
                    touches.setdefault(f, []).append((lead, words, not (two or merge)))
                i += 1 + len(paths)
        _TOUCHES_CACHE[key] = touches
    return _TOUCHES_CACHE[key]


def _other_tasks_only(git, base, br, task, ids, files):
    """The files (of `files`) that only other tasks' commits on br changed: every commit touching them leads with
    another task's id (`T5: ...`), does not name `task` anywhere, and is a plain one-parent change (see
    _branch_touches). Everything else holds: this task's commits, commits naming no task (`WIP: ...`, `fixup! ...`),
    renames and merges, so unclear work is never dropped from the lease."""
    touches = _branch_touches(git, base, br)
    return {f for f in files if touches.get(f) and all(plain and lead != task and lead in ids and task.lower() not in words
                                                         for lead, words, plain in touches[f])}


def _landed_files(git, base, br, files, tids):
    """The subset of a done branch's files that has reached the base, by any kind of merge:
    - merging the branch into the base would change nothing at all (git merge-tree, git >= 2.38): all of them;
    - the base has the same content now (plain squash/rebase);
    - a base commit since the fork changed that file and names the task id in its subject (squash or rebase that the
      base edited again later; SKILL step 7 keeps the id in the subject). Reverts and mere mentions do not count."""
    mt = git("merge-tree", "--write-tree", base, br)
    bt = git("rev-parse", f"{base}^{{tree}}")
    if mt.returncode == 0 and mt.stdout.split() and bt.stdout.strip() == mt.stdout.split()[0]:
        return set(files)
    same = set(files) - set(_z(git("diff", "-z", "--relative", "--name-only", "--no-renames", base, br, "--").stdout))
    tids = [tids] if isinstance(tids, str) else list(tids or [])
    if tids:
        fork = git("merge-base", base, br).stdout.strip()
        if fork:
            rxs = [_ID_RX(t) for t in tids]
            log = git("log", "-z", "--relative", "--no-renames", "--name-only", "--format=%x01%H %s",
                      f"{fork}..{base}").stdout
            for chunk in log.split("\x01")[1:]:
                subj, _, names = chunk.partition("\x00")
                sha, _, subj = subj.partition(" ")
                if _subject_leads_with(subj, tids, rxs):
                    todo = (set(files) - same) & set(_z(names[1:] if names.startswith("\n") else names))
                    if not todo:
                        continue
                    at_c, at_br = _blobs_at(git, sha, todo), _blobs_at(git, br, todo)
                    mt = git("merge-tree", "--write-tree", f"{sha}^", br)  # the branch merged onto the squash's parent
                    at_m = _blobs_at(git, mt.stdout.split()[0], todo) if mt.returncode == 0 and mt.stdout.split() \
                        else {}
                    for f in todo:
                        # landed if the commit has the branch's content, or merging the branch where the squash was
                        # made gives exactly what it committed (the base's own edits to other lines folded in).
                        # Anything the branch changed beyond it (later work, an unpushed or amended fix) stays held.
                        if at_c.get(f) and (at_c[f] == at_br.get(f) or at_c[f] == at_m.get(f)):
                            same.add(f)
    return same


def _blobs_at(git, rev, files):
    """{file: blob id} at rev for the given project-relative files (one git call)."""
    out = {}
    for e in git("ls-tree", "-r", "-z", rev, "--", *[f"./{f}" for f in sorted(files)]).stdout.split("\x00"):
        meta, _, path = e.partition("\t")
        if path and len(meta.split()) == 3:
            out[path] = meta.split()[2]
    return out


def branch_changes(st, k, warnings=None, unmerged_only=False, tid=None, task=None):
    """tid: a task id or a list of them whose squash commits count as landed (see _landed_files).
    task: this task's id. An earlier owner's branch then does not hold files that only commits for other tasks
    changed (subject starts with another task's id): that thread's other work is not this task's (T29).
    Files changed on the owner's branch vs base ([] when not a git repo or no branch). Problems go to warnings.
    unmerged_only: leave out files that have already landed on the base (done or recalled tasks)."""
    # the current owner's branch, and every earlier owner's: work a handed-over task left unmerged there still counts
    seats = [{**st["threads"][n], "branch": branch_of(st, n)}
             for n in dict.fromkeys([k.get("owner") or ""] + list(k.get("past_owners") or [])) if n in st["threads"]]
    repo = st.get("repo", {}).get("path")
    if not seats or not repo:
        return []
    git = _git_cached(repo)
    if git("rev-parse", "--git-dir").returncode != 0:
        return []
    base = _base_ref(st, git)
    if base is None:
        if warnings is not None:
            warnings.append(f"base branch {st.get('repo', {}).get('base') or 'main'} not found: real branch diffs "
                            "are not checked (fix repo.base)")
        return []
    files, refs, past = [], [], set()
    for owner in seats:
        two = [b for b in (owner["branch"], f"origin/{owner['branch']}")
               if git("rev-parse", "--verify", "--quiet", b).returncode == 0]
        if len(two) == 2:  # one is behind the other (pushed WIP, or a stale local copy): only the newer one counts
            if git("merge-base", "--is-ancestor", two[0], two[1]).returncode == 0:
                two = [two[1]]
            elif git("merge-base", "--is-ancestor", two[1], two[0]).returncode == 0:
                two = [two[0]]
            # diverged (rebased, amended, force-pushed, or two sessions each holding work): both copies hold, so
            # work on either side is never dropped; a stale copy stops once it lands, is deleted or pruned
        if task and owner is not st["threads"].get(k.get("owner") or "") and not set(two) & set(refs):
            past |= set(two)  # the current owner comes first, so a branch it shares is never filtered
        refs += [r for r in two if r not in refs]
    for br in refs:
        r = git("diff", "-z", "--relative", "--name-only", "--no-renames", f"{base}...{br}")  # a rename holds old and new
        if r.returncode != 0 and warnings is not None:
            warnings.append(f"git diff {base}...{br} failed: {r.stderr.strip()[:120]}")
        got = [RealFile(f) for f in _z(r.stdout)]
        if br in past:
            theirs = _other_tasks_only(git, base, br, task, st["tasks"], got)
            got = [f for f in got if f not in theirs]
        if unmerged_only and got:
            gone = _landed_files(git, base, br, got, tid)
            got = [f for f in got if f not in gone]
        files += got
    return [RealFile(f) for f in sorted(set(files))]


def _held_paths(st, warnings=None):
    """{task: paths} held by active tasks (declared + real diff) and by done/recalled tasks (unlanded diff)."""
    held = {}
    for t, k in st["tasks"].items():
        # one branch per thread: what the owner's earlier tasks already landed (merge or squash with their id)
        # is not this task's to hold
        seats = {k.get("owner")} | set(k.get("past_owners") or [])
        landed_ids = [x for x, kx in st["tasks"].items() if kx.get("owner") and kx.get("owner") in seats
                      and kx.get("status") == "done"]
        if k["status"] in LEASE_ACTIVE:
            held[t] = (k.get("touches") or []) + branch_changes(st, k, warnings, unmerged_only=True, tid=landed_ids, task=t)
        elif k["status"] in ("done", "recalled"):
            changed = branch_changes(st, k, warnings, unmerged_only=True, tid=landed_ids, task=t)
            if changed:
                held[t] = changed
    if warnings is not None:
        warnings[:] = list(dict.fromkeys(warnings))
    return held


def plan_ready(st, ready, warnings=None):
    """One pass over the ready tasks in order. A task is held back if its paths overlap what is held (active,
    unlanded, or an earlier task offered in this pass) or it shares a topic with an active task or an earlier
    offered one, always of another owner. Only offered tasks hold paths and topics, so the result is consistent:
    no two offered tasks collide, and nothing held back blocks anyone.
    Returns (offered, blocked_by_lease, blocked_by_topic)."""
    held = _held_paths(st, warnings)
    topics = [(t, k) for t, k in st["tasks"].items() if k["status"] in LEASE_ACTIVE]
    offered, by_lease, by_topic = [], {}, {}
    for t in ready:
        k = st["tasks"][t]
        owner = k.get("owner")
        mine = k.get("touches") or []
        for other, paths in held.items():
            if owner and st["tasks"][other].get("owner") == owner:
                continue  # same thread works sequentially on its own branch
            clash = sorted({p for p in paths for m in mine if paths_overlap(m, p)})
            if clash:
                by_lease.setdefault(t, []).append({"with": other, "owner": st["tasks"][other].get("owner"),
                                                   "status": st["tasks"][other]["status"], "paths": clash})
        mt = set(k.get("topics") or [])
        for other, ko in topics:
            if owner and ko.get("owner") == owner:
                continue
            shared = sorted(mt & set(ko.get("topics") or []))
            if shared:
                by_topic.setdefault(t, []).append({"with": other, "owner": ko.get("owner"), "topics": shared})
        if t in by_lease:
            by_topic.pop(t, None)  # report one reason: the lease
        elif t not in by_topic:
            offered.append(t)
            if mine:
                held[t] = mine
            if mt:
                topics.append((t, k))
    return offered, by_lease, by_topic


def lease_conflicts(st, ready, warnings=None):
    """blocked_by_lease part of plan_ready (kept for callers that only need leases)."""
    return plan_ready(st, ready, warnings)[1]


def norm_topics(s):
    """'Request Ledger, request_ledger , mechanism' -> ['request-ledger', 'mechanism']"""
    return list(dict.fromkeys(re.sub(r"[\s_]+", "-", x.strip().lower()) for x in csv(s) if x.strip()))


REVIEW_MAX_DEFAULT = 3
PASSING = ("pass", "capped")  # capped: the round limit was reached with no blocking finding and checks passing


def review_max(st):
    v = st.get("prefs", {}).get("review_max_rounds", REVIEW_MAX_DEFAULT)
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 99 else REVIEW_MAX_DEFAULT


def accept_gate(st, tid):
    """Why a task in review is not yet acceptable (None if it is): the same bar as `task set --status done`."""
    k = st["tasks"][tid]
    if k.get("checks") == "fail":
        return "has failing checks"
    rv = k.get("review") or {}
    if rv.get("result") == "skipped":  # the user's words, recorded with the skip
        return None
    if not str(k.get("accept") or "").strip():
        return "has no 'done when' to check against"
    if not k.get("owner"):
        return "has no owner"
    if not st["prefs"].get("require_review", True):
        return None
    if rv.get("result") not in PASSING:
        return "has no passing independent review yet"
    if rv.get("result") == "capped" and k.get("checks") != "pass":
        return "was capped at the round limit, which needs checks passing"
    if not all(reviewer_independent(rv.get("reviewer") or "", o) for o in [k["owner"]] + list(k.get("past_owners") or [])):
        return f"was reviewed by its own owner ({rv.get('reviewer')})"
    return None


def reviewer_independent(reviewer, owner):
    """The owner itself (any spelling: 'UI', './ui/', 'ui thread') is not independent; 'ui/subagent' is."""
    rv, ow = norm_reviewer(reviewer), (owner or "").strip().lower()
    if not ow:
        return True
    if rv == ow:
        return False
    if "/" in rv:
        return True
    tok = lambda x: [w for w in re.split(r"[^a-z0-9]+", x) if w]
    rt, ot = tok(rv), tok(ow)  # 'ui-thread', 'the ui', 'research-2:self' = the owner (its words as one run)
    return not ot or not any(rt[i:i + len(ot)] == ot for i in range(len(rt) - len(ot) + 1))


def norm_reviewer(x):
    import posixpath
    return posixpath.normpath(x.strip().lower() or ".").strip("/")


def roster_stale(st):
    """Live threads that have not been sent the newest ROSTER."""
    v = st.get("roster_version", 0)
    return [n for n, t in st["threads"].items()
            if t.get("session_id") and t["status"] not in ("planned", "resolved", "archived")
            and t.get("roster_seen", 0) < v]


def bump_roster(st, name=None):
    st["roster_version"] = st.get("roster_version", 0) + 1
    if name:  # a thread created now gets the current roster in its worker prompt
        st["threads"][name]["roster_seen"] = st["roster_version"]


def _minutes_since(ts):
    from datetime import datetime
    try:
        t = datetime.fromisoformat(ts)
        cur = datetime.fromisoformat(now())
        if t.tzinfo is None:  # an old or hand-edited stamp: read it in the clock's zone
            t = t.replace(tzinfo=cur.tzinfo)
        return max(0, int((cur - t).total_seconds() // 60))
    except (TypeError, ValueError):
        return None


def merge_waiting(st):
    """IL-3: tasks in review with a passing or capped verdict, not merged yet, oldest first."""
    out = []
    for t, k in st["tasks"].items():
        rv = k.get("review") or {}
        if k.get("status") == "review" and rv.get("result") in PASSING:
            out.append({"task": t, "owner": k.get("owner"), "branch": (st["threads"].get(k.get("owner") or "") or {})
                        .get("branch"), "minutes": _minutes_since(rv.get("at") or k.get("created"))})
    return sorted(out, key=lambda x: -(x["minutes"] or 0))


def merge_overdue(st):
    limit = st.get("prefs", {}).get("stuck_minutes", STUCK_DEFAULT)
    w = merge_waiting(st)
    return bool(limit and w and (w[0]["minutes"] or 0) > limit)


def task_model(st, t):
    k = st["tasks"][t]
    m = k.get("model") or (st["threads"].get(k.get("owner") or "") or {}).get("model") or ""
    return " ".join(x for x in (m, "(by hand, no scripts)" if k.get("no_scripts") else "") if x)


def cmd_ready(a):
    d = need(find_dir())
    st = load(d)
    done = {t for t, k in st["tasks"].items() if k["status"] == "done"}
    busy = sum(
        1 for t in st["threads"].values() if t["status"] in ("starting", "working")
    )
    ready = [
        t
        for t, k in st["tasks"].items()
        if k["status"] == "approved" and set(k.get("deps", [])) <= done
    ]
    lease_warnings = []
    ready, blocked, by_topic = plan_ready(st, ready, lease_warnings)
    print(
        json.dumps(
            {
                "ready": ready,
                "blocked_by_lease": blocked,
                "lease_warnings": lease_warnings,
                "blocked_by_topic": {t: v + [{"why": "same topic as an active task of another thread: merge into one "
                                              "thread in order, or make it wait (deps)"}] for t, v in by_topic.items()},
                "roster_not_sent": roster_stale(st),
                "no_touches": [t for t in ready if not st["tasks"][t].get("touches")],
                "busy_threads": busy,
                "max_parallel": st["prefs"].get("max_parallel", 0) or "unlimited",
                "slots": (max(0, st["prefs"]["max_parallel"] - busy) if st["prefs"].get("max_parallel") else "unlimited"),
                "stuck": stuck_threads(st),
                "merge_waiting": merge_waiting(st),
                **({"merge_overdue": "the oldest reviewed branch has waited longer than stuck_minutes: merge it, or "
                                     "tell the user why it waits"} if merge_overdue(st) else {}),
                "models": {t: m for t in ready for m in [task_model(st, t)] if m},
                "unreported_requests": {r: q["text"] for r, q in unreported(st).items()},
                "rc_capacity_note": "a Remote Control server holds 32 sessions by default, shared by every project and lead on it; cloud threads do not count",
                **({"permission_warning": permission_warning(st)} if permission_warning(st) else {}),
            }
        )
    )


_STOP = set("a an the and or of to in on at for with from by is are be it this that these those into as via per "
             "new add fix update make do use get set run all any some more one two not no up out our your my "
             "task tasks thread threads work please also then build write create implement draft prepare need want "
             "help improve change start finish check review".split())


# Chinese words that carry no topic: runs are cut at these multi-character function words before pairing, and pairs
# with 的 are dropped, so `我的设计稿` and `用户的设置页面` share nothing through `的设`. Single characters are not cut:
# they live inside real words (申请, 请求, 参与, 了解).
_ZH_STOP = re.compile("我们|你们|他们|这个|那个|这些|那些|一个|一些|需要")


def _stem(w):
    """One form for the singular and the plural, the same on both sides (not always a real word): house/houses ->
    hous, bus/buses -> bus, policy/policies -> policy, status/statuses -> status, analysis/analyses -> analysis."""
    if len(w) > 4 and w.endswith("yses"):  # analyses
        return w[:-3] + "sis"
    if len(w) > 4 and w.endswith("ies") and not w.endswith(("kies", "vies")):  # policies (not cookies, movies)
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is", "vas", "ias", "gas")):  # canvas, alias, gas stay
        cut = w[:-2] if w.endswith("es") else ""  # boxes, buses, statuses, matches; frees, uses keep their e
        w = cut if len(cut) > 2 and cut.endswith(("s", "x", "z", "ch", "sh")) else w[:-1]
    if len(w) > 3 and w.endswith("e") and not w.endswith(("ee", "ie")):
        w = w[:-1]  # house and houses meet at hous; free, tie stay
    return w


def _tokens(s, stop=True):
    """Topic words: English words (stop words and single letters dropped, plural `s` cut) and Chinese character
    pairs within words, so `请求台账` and `台账导出` share `台账`."""
    s = s.lower()
    out = {_stem(w) for w in re.findall(r"[a-z0-9]+", s) if len(w) > 1 and not (stop and w in _STOP)}
    for run in re.findall(r"[一-鿿]+", s):
        for part in _ZH_STOP.split(run):
            out.update(x for x in (part[i:i + 2] for i in range(len(part) - 1)) if "的" not in x)
    return out


def _thread_topic(t, name):
    """A thread's topic words; its own name always counts, even a stop word such as `review`."""
    return _tokens(" ".join([t.get("focus", "")] + t.get("owns", []))) | _tokens(name, stop=False)


def topic_mismatch(st, owner, title):
    """R41: a warning when a task's title shares no topic word with its owner's name, focus and owns (never blocks)."""
    t = st["threads"].get(owner or "")
    if not t or not title:
        return None
    title_words = _tokens(title) | (_tokens(title, stop=False) & _tokens(owner, stop=False))
    if title_words & _thread_topic(t, owner) or not title_words:
        return None
    if not t.get("focus") and not t.get("owns"):
        return (f"topic_mismatch: thread {owner} has no focus or owns, so its topic is unknown: set them with "
                f"`TW thread set {owner} --focus ... --owns ...` (one topic per thread). Kept as given.")
    return (f"topic_mismatch: '{title[:60]}' shares no topic word with thread {owner} (focus: {t.get('focus') or '-'}; "
            f"owns: {', '.join(t.get('owns', [])) or '-'}). One topic per thread: if this is a different topic, open "
            "a new thread for it instead (no thread-count target; max_parallel 0 = no limit). Kept as given.")


def _suggest_name(text):
    words = [w for w in re.findall(r"[a-z][a-z0-9]*", text.lower()) if len(w) > 2 and w not in _STOP]
    return "-".join(words[:2]) or None


def cmd_route(a):
    d = need(find_dir())
    st = load(d)
    if a.path:
        if a.text.strip():
            sys.exit("route takes the request text or --path, not both")
        roots = [d.parent.resolve()] + [Path(t.get("worktree") or worktree_path(st, n)).resolve()
                                        for n, t in st["threads"].items()]
        out = {}
        for f in a.path:
            rel = f
            if os.path.isabs(f):  # QA reports give absolute paths: make them relative to the project or a worktree
                fp = Path(f).resolve()
                for r in roots:
                    if r == fp or r in fp.parents:
                        rel = str(fp.relative_to(r))
                        break
            rf = RealFile(_norm_path(rel))
            hit = [n for n, t in st["threads"].items() if t["status"] != "archived" and (
                any(paths_overlap(rf, o) for o in t.get("owns", []) if o) or
                any(paths_overlap(rf, x) for k in st["tasks"].values() if k.get("owner") == n
                    and k.get("status") in LEASE_ACTIVE for x in k.get("touches") or []))]
            out[f] = hit or None
        print(json.dumps({"owners": out, "note": "from owns and active task leases; none = nobody owns it yet"},
                         ensure_ascii=False))
        return
    if not a.text.strip():
        sys.exit("route needs the request text, or --path <file>")
    text = a.text.lower()
    words = _tokens(text)
    scores = []
    for name, t in st["threads"].items():
        if t["status"] == "archived":
            continue
        keys = [name, t.get("focus", "")] + t.get("owns", [])
        topic = _thread_topic(t, name)
        s = min(2, sum(  # its tasks count a little (2 at most), only on its topic: a task routed there by mistake
            min(1, len(words & _tokens(k.get("title") or "") & topic))  # does not pull more of the same
            for k in st["tasks"].values() if k.get("owner") == name and k["status"] != "dropped"))
        for key in keys:
            key = key.lower().strip()
            if not key:
                continue
            if re.search(r"(?<![a-z0-9])" + re.escape(key) + (r"(?![a-z0-9])" if key[-1:].isalnum() else ""),
                         text):  # `ui` is not in `build`; `docs/` is in `docs/intro.md`
                s += 3
            s += len(words & _tokens(key))
        if s:
            scores.append((s, name))
    scores.sort(reverse=True)
    print(
        json.dumps(
            {
                "best": scores[0][1] if scores else None,
                "candidates": [
                    {"thread": n, "score": s, "status": st["threads"][n]["status"]}
                    for s, n in scores[:4]
                ],
                **({} if scores or not words else {
                    "suggest": "new_thread", "suggest_name": _suggest_name(a.text) or "<short English name>",
                    "why": "no thread owns this topic: open a new thread for it (step 2.5) rather than handing it "
                           "to the closest one; one topic per thread, no thread-count target"}),
                **({"topic_words": "none found: decide from the request itself"} if not words else {}),
                "note": "a keyword hint only: confirm against each candidate's owns/summary before routing",
            },
            ensure_ascii=False,
        )
    )


def cmd_ask(a):
    d = need(find_dir())
    st = load(d)
    if a.answer is not None:
        n = a.answer
        if not 1 <= n <= len(st["questions"]):
            sys.exit(f"no question {n}; questions are numbered 1..{len(st['questions'])} (see the board)")
        q = st["questions"][n - 1]
        if q.get("answered"):
            sys.exit(f"question {n} is already answered" + (f": {q['answer']}" if q.get("answer") else "")
                     + "; ask a new question if the user changed their mind")
        if q.get("defaulted") and not (a.text or "").strip():
            sys.exit(f"question {n} was done by default: record the user's answer (`ask --answer {n} \"<their words>\"`)")
        q["answered"] = True
        for dec in st.get("decisions", []):
            if dec.get("question") == n and not dec.get("answered"):
                # the user's answer settles it, and may overrule the default: never stored as approval of it
                dec.update(answered=True, answer=a.text or "", answered_at=now())
        if a.text:
            q["answer"] = a.text
        save(d, st, {"ev": "answered", "n": n})
    elif a.default is not None:
        n = a.default
        if not a.undoable:
            sys.exit(lab(st, "default_undoable"))
        if not 1 <= n <= len(st["questions"]) or st["questions"][n - 1].get("answered"):
            sys.exit(f"no unanswered question {n}")
        if st["questions"][n - 1].get("defaulted"):
            sys.exit(f"question {n} was already done by default: {st['questions'][n - 1]['defaulted']['what']}")
        q = st["questions"][n - 1]; what = (a.text or "").strip()
        if not what: sys.exit("default needs a description")
        q["defaulted"] = {"what": what, "at": now()}
        st.setdefault("decisions", []).append({"kind": "default", "question": n, "what": what, "at": now()})
        save(d, st, {"ev": "default", "n": n})
    else:
        a.text = (a.text or "").strip()
        if not a.text:  # a blank question would sit on the board as "waiting on the user" with nothing to answer
            sys.exit('usage: ask "<question>" --from <thread>   or   ask --answer N ["<answer>"]')
        nt = (a.need_thread or "").strip().lower() or None
        if nt:
            for i, q in enumerate(st["questions"], 1):
                if q.get("need_thread") == nt and q.get("from") == a.frm:
                    sys.exit(f"{a.frm}'s request for a {nt} thread is already question {i}"
                             + (" (answered)" if q.get("answered") else "") + ": skip it (a STATUS line repeats "
                             "until the thread sees your ANSWER)")
        st["questions"].append(
            {"from": a.frm, "text": a.text, "t": now(), "answered": False, **({"need_thread": nt} if nt else {})}
        )
        save(d, st, {"ev": "question", "from": a.frm, "text": a.text})
    print(render_board(st))


MEMORY_SECTIONS = {
    "never_raise": "## Never raise (topics the user ruled out: never bring them up again, side notes included)",
    "user_owned": "## User-owned (the user provides or decides these: never plan, ask or remind about them)",
}


def _memory_section(text, key):
    """Where a special section's heading starts (matched by its first words, so a hand-edited heading counts)."""
    word = MEMORY_SECTIONS[key].split(" (")[0]  # "## Never raise"
    m = re.search(r"(?mi)^" + re.escape(word) + r"\b.*$", text)
    return m.start() if m else None


def cmd_memory(a):
    d = need(find_dir())
    if sum(bool(x) for x in (a.never_raise, a.user_owned, a.standing)) > 1:
        sys.exit("--never-raise, --user-owned, or --standing: choose one")
    a.text = " ".join((a.text or "").split())  # one line: a newline could start a heading of its own
    which = "never_raise" if a.never_raise else "user_owned" if a.user_owned else "standing" if a.standing else None
    if a.standing_list:
        st = load(d)
        print("\n".join("- " + x for x in st.get("standing", [])) or lab(st, "no_items"))
        return
    if not a.text:
        sys.exit('usage: memory "<text>" [--never-raise | --user-owned | --standing] (or --standing-list)')
    if which == "standing":  # F4: a long-term requirement of the user's; every TASK carries it (task-msg)
        st = load(d)
        if a.text not in st.setdefault("standing", []):
            st["standing"].append(a.text)
            save(d, st, {"ev": "standing"})
        print(lab(st, "standing", text=a.text))
        return
    mark = " _(temporary: confirm with the user before enforcing it later)_" if a.temporary else ""
    line = f"- {a.text}{mark}  _(added {now()[:10]})_\n"
    path = d / "MEMORY.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    starts = {k: _memory_section(text, k) for k in MEMORY_SECTIONS}
    if which:
        if starts[which] is None:
            other = starts["user_owned"] if which == "never_raise" else None  # Never raise comes first
            block = f"{MEMORY_SECTIONS[which]}\n\n"
            text = (text[:other] + block + text[other:]) if other is not None else \
                text.rstrip("\n") + "\n\n" + block.rstrip("\n") + "\n"
            starts = {k: _memory_section(text, k) for k in MEMORY_SECTIONS}
        i = text.index("\n", starts[which]) if "\n" in text[starts[which]:] else len(text)
        nxt = text.find("\n## ", i)
        end = len(text) if nxt < 0 else nxt + 1
        body = text[i:end].rstrip("\n")
        text = text[:i] + body + "\n" + ("\n" if not body.strip() else "") + line + ("\n" if nxt >= 0 else "") \
            + text[end:]
    else:  # facts stay above the special sections
        at = min([x for x in starts.values() if x is not None] or [len(text)])
        before = text[:at].rstrip("\n")
        last = before.rsplit("\n", 1)[-1]
        text = before + ("\n" if last.startswith("- ") else "\n\n") + line + ("\n" + text[at:] if at < len(text) else "")
    path.write_text(text, encoding="utf-8")
    log(d, {"ev": "memory", "text": a.text, **({"section": which} if which else {}),
            **({"temporary": True} if a.temporary else {})})
    print(read_file(d, "MEMORY.md"))


def cmd_log(a):
    d = need(find_dir())
    log(d, {"ev": "note", "text": a.text})
    print("logged")


def _json_or_text(a, data, lines):
    if getattr(a, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print("\n".join(lines))


def cmd_feature(a):
    d = need(find_dir()); st = load(d); rows = st.setdefault("features", {})
    if a.action == "add":
        if not a.name.strip(): sys.exit("feature add requires a name")
        reqs = [x for group in (a.req or []) for item in group for x in csv(item)]
        tasks = [x for group in (a.task or []) for item in group for x in csv(item)]
        if (a.status or "planned") not in ("planned", "building", "landed", "dropped"): sys.exit("invalid feature status")
        a.evidence, a.reason = (a.evidence or "").strip() or None, (a.reason or "").strip() or None
        if a.status == "landed" and not a.evidence: sys.exit("landed requires --evidence")
        if a.status == "dropped" and not a.reason: sys.exit("dropped requires --reason")
        for rid in reqs:
            if rid not in st.get("requests", {}): sys.exit(f"unknown request {rid}")
        for tid in tasks:
            if tid not in st.get("tasks", {}): sys.exit(f"unknown task {tid}")
        fid = f"F{max([int(x[1:]) for x in rows if re.fullmatch(r'F\d+', x)] or [0])+1}"
        rows[fid] = {"name": a.name.strip(), "phase": a.phase or "", "reqs": reqs, "tasks": tasks,
                     "status": a.status or "planned", "evidence": a.evidence or "", "reason": a.reason or "", "created": now()}
        if a.status == "landed": rows[fid]["landed_at"] = now()
        for rid in reqs: st["requests"][rid].setdefault("features", []).append(fid)
        save(d, st, {"ev": "feature-add", "feature": fid}); print(fid); return
    if a.action == "set":
        f = rows.get(a.name)
        if not f: sys.exit(f"unknown feature {a.name}")
        status = a.status or f["status"]
        if status not in ("planned", "building", "landed", "dropped"): sys.exit("invalid feature status")
        a.evidence, a.reason = (a.evidence or "").strip() or None, (a.reason or "").strip() or None
        if status == "landed" and not (a.evidence or f.get("evidence")): sys.exit("landed requires --evidence")
        if status == "dropped" and not (a.reason or f.get("reason")): sys.exit("dropped requires --reason")
        if a.phase is not None: f["phase"] = a.phase
        for attr, key in (("req", "reqs"), ("task", "tasks")):
            value = getattr(a, attr)
            if value is not None:
                flat = value if isinstance(value, list) else [value]  # append + nargs="+": a list of lists
                vals = [x for item in flat for sub in (item if isinstance(item, list) else [item]) for x in csv(sub)]
                valid = st.get("requests", {}) if key == "reqs" else st.get("tasks", {})
                if any(x not in valid for x in vals): sys.exit(f"unknown {attr} id")
                f[key] = vals
                if key == "reqs":
                    for rid, q in st.get("requests", {}).items():
                        q["features"] = [x for x in q.get("features", []) if x != a.name]
                    for rid in vals: st["requests"][rid].setdefault("features", []).append(a.name)
        if a.evidence: f["evidence"] = a.evidence
        if a.reason: f["reason"] = a.reason
        if status == "landed" and f.get("status") != "landed": f["landed_at"] = now()
        f["status"] = status; save(d, st, {"ev": "feature-set", "feature": a.name}); print(a.name); return
    if a.action == "list":
        selected = [(fid, f) for fid, f in rows.items() if not a.phase or f.get("phase") == a.phase]
        if a.done_since:
            cutoff = last_report_time(st) if a.done_since == "last-report" else _when(a.done_since)
            if cutoff is None: sys.exit("--done-since: an ISO time (2026-11-20 or 2026-11-20T09:00) or last-report")
            selected = [(i, f) for i, f in selected if f.get("status") == "landed" and _later(f.get("landed_at"), cutoff)]
        data = [{"id": i, **f} for i, f in selected]
        _json_or_text(a, data, [f"{i} [{lab(st, 'feature_' + f['status'])}] {f['name']} — {', '.join(f.get('reqs') or []) or '-'}" for i, f in selected])
        return


EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)  # "since the start": comparable with every stored time


def _when(text):
    """A stored or typed ISO time as an aware datetime (a time without a zone is local); None when unreadable."""
    try:
        t = dt.datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return t if t.tzinfo else t.astimezone()


def _later(text, since):
    t = _when(text) if text else None
    return bool(t and t > since)


def last_report_time(st):
    if st.get("last_report_at") and _when(st["last_report_at"]):
        return _when(st["last_report_at"])
    times = [_when(q["updated"]) for q in st.get("requests", {}).values() if q.get("status") == "reported" and q.get("updated")]
    return max((x for x in times if x), default=EPOCH)


def cmd_reconcile(a):
    d = need(find_dir()); st = load(d); rows = _run_reconcile(st)
    save(d, st, {"ev": "reconcile"})
    _json_or_text(a, {"count": st["reconcile"]["count"], "items": rows},
                  [_rc_line(st, x) for x in rows] or [lab(st, "no_items")])
    if a.strict and st["reconcile"]["count"]: sys.exit(1)


def cmd_wrapup(a):
    d = need(find_dir()); st = load(d); rows = _run_reconcile(st); save(d, st, {"ev": "reconcile"})
    p = st.get("prefs", {}); limit = p.get("stuck_minutes", STUCK_DEFAULT); cur = dt.datetime.fromisoformat(now())
    sections = {lab(st, "heard_unrecorded"): [lab(st, "selfcheck_reminder")],
        lab(st, "recorded_not_dispatched"): [f"{r}: {q['text']}" for r,q in st.get("requests",{}).items() if q.get("status")=="open"],
        lab(st, "dispatched_no_reply"): [f"{t}: {x.get('title','')}" for t,x in st.get("tasks",{}).items() if x.get("status")=="dispatched" and x.get("dispatched_at") and (cur-dt.datetime.fromisoformat(x['dispatched_at'])).total_seconds()>limit*60],
        lab(st, "replied_not_reported"): [r for r,q in st.get("requests",{}).items() if q.get("status")=="delivered"] + [t for t,x in st.get("tasks",{}).items() if x.get("status")=="done" and any(t in q.get("tasks",[]) and q.get("status") not in REQ_SETTLED + ("delivered",) for q in st.get("requests",{}).values())],
        lab(st, "promises_open"): [f"{x['id']}: {x['text']}" for x in st.get("promises",[]) if x.get("status")=="open"]}
    data = {"sections": sections, "reconcile": st["reconcile"]}
    lines = [f"## {lab(st,'wrapup')}"] + [f"### {k}\n" + ("\n".join("- "+x for x in v) if v else lab(st,"no_items")) for k,v in sections.items()]
    lines.append(f"### {lab(st,'reconcile')}\n" + ("\n".join("- " + _rc_line(st, x) for x in rows) or lab(st, "no_items")))
    _json_or_text(a, data, lines)


def cmd_sprint(a):
    d=need(find_dir()); st=load(d); since=_when(a.since) if a.since else EPOCH
    if since is None: sys.exit("--since: an ISO time (2026-11-20 or 2026-11-20T09:00)")
    ph=st.get("prefs",{}).get("current_phase")
    if not ph:
        phases=sorted(f.get("phase") for f in st.get("features",{}).values() if f.get("status")!="landed" and f.get("phase"))
        ph=phases[0] if phases else ""
    landed=[f"{i} {f['name']}" for i,f in st.get("features",{}).items() if f.get("status")=="landed" and _later(f.get("landed_at"), since)]
    landed += [f"{i} {x.get('title','')}" for i,x in st.get("tasks",{}).items() if x.get("status")=="done" and _later(x.get("updated"), since)]
    doing=[f"{i} {f['name']}" for i,f in st.get("features",{}).items() if f.get("status")=="building"]+[f"{i} {x.get('title','')}" for i,x in st.get("tasks",{}).items() if x.get("status") in ("dispatched","working","blocked","review")]
    missed=[f"{i} {f['name']}" for i,f in st.get("features",{}).items() if f.get("status")=="planned" and f.get("phase")==ph]
    data={"in_round":landed,"still_working":doing,"missed":missed}
    lines=["| "+" | ".join(lab(st,k) for k in data)+" |","| --- | --- | --- |","| "+" | ".join("<br>".join(v) or "-" for v in data.values())+" |"]
    _json_or_text(a,data,lines)


def cmd_board(a):
    d = need(find_dir())
    st = load(d)
    (d / "BOARD.md").write_text(render_board(st), encoding="utf-8")
    if getattr(a, "share", False):
        text = render_share(st)
        (d / "SHARE.md").write_text(text, encoding="utf-8")
        print(text)
        print("SHARE.md written: put this text into a doc visible only in the user's own account and update it on "
              "each report (IL-13, IL-14); sharing it further is the user's decision", file=sys.stderr)
        return
    print(render_board(st))


CARD_KINDS = ("compare", "plan", "read", "done", "status")


def cmd_card(a):
    """B5/B6/B9: one report card for the user: a first line (receipt or conclusion), then done / doing / waiting,
    and (prefs ledger_line_every) the ledger line."""
    d = need(find_dir()); st = load(d)
    if a.since == "last-report":
        since = last_report_time(st)
    elif a.since:
        since = _when(a.since)
        if not since:
            sys.exit("--since: an ISO time (2026-01-31T09:00) or last-report")
    else:
        since = EPOCH
    reqs, feats, tasks = st.get("requests", {}), st.get("features", {}), st.get("tasks", {})
    done = [f"{i} {f.get('name', '')}" for i, f in feats.items() if f.get("status") == "landed" and _later(f.get("landed_at"), since)]
    done += [f"{i} {q.get('text', '')}" for i, q in reqs.items() if q.get("status") in ("reported", "landed") and _later(q.get("updated"), since)]
    doing = [f"{i} {f.get('name', '')}" for i, f in feats.items() if f.get("status") == "building"]
    doing += [f"{i} {t.get('title', '')}" for i, t in tasks.items() if t.get("status") in ("dispatched", "working")]
    waiting = [f"{i} {q.get('text', '')}" for i, q in reqs.items() if q.get("status") == "waiting_user"]
    for n, q in enumerate(st.get("questions", []), 1):
        if not q.get("answered"):  # a question that took its default still waits for the user's word
            mark = f" ({lab(st, 'card_defaulted', what=q['defaulted'].get('what', ''))})" if q.get("defaulted") else ""
            waiting.append(f"{lab(st, 'card_question', n=n)} {q.get('text', '')}{mark}")
    added = sum(1 for q in reqs.values() if _later(q.get("created"), since))
    ended = sum(1 for q in reqs.values() if q.get("status") in REQ_END + ("wont", "landed") and _later(q.get("updated"), since))
    gpt = 0
    try:
        for line in (d / "model-log.jsonl").read_text(encoding="utf-8", errors="replace").split("\n"):
            try:
                x = json.loads(line) if line.strip() else {}
            except ValueError:
                continue
            gpt += isinstance(x, dict) and str(x.get("model") or "").lower() == "gpt" and _later(x.get("at"), since)
    except OSError:
        pass
    links = list(dict.fromkeys(list(a.link or []) + [x["link"] for k in DOC_KINDS for x in [st.get("docs", {}).get(k)] if x and x.get("link")]))
    head = f"[{lab(st, 'card_delivered' if a.kind == 'done' else 'card_' + a.kind)}] {' '.join(a.head.split())}"
    payload = {"kind": a.kind, "head": head, "done": done, "doing": doing, "waiting": waiting}
    if st.get("prefs", {}).get("ledger_line_every", "wrapup") == "report" or a.kind == "done" or a.ledger_line:
        payload["ledger"] = lab(st, "card_ledger", added=added, ended=ended, waiting=len(waiting), gpt=gpt,
                                links=" ".join(links) or "-")
    st["last_report_at"] = now()
    save(d, st, {"ev": "report-card", "kind": a.kind})
    lines = [head]
    for key in ("done", "doing", "waiting"):
        lines += ["", f"## {lab(st, 'card_' + key)}", "\n".join("- " + x for x in payload[key]) or lab(st, "no_items")]
    if "ledger" in payload:
        lines += ["", payload["ledger"]]
    _json_or_text(a, payload, lines)


def cmd_intro(a):
    """B2: what the user is told once the project is set up, straight from the ledger."""
    d = need(find_dir()); st = load(d)
    sections = ", ".join(re.findall(r"(?m)^## (.+)$", render_board(st))) or lab(st, "no_items")
    where = " ".join(f"{lab(st, 'doc_' + k)} {x['link']}" for k in DOC_KINDS for x in [st.get("docs", {}).get(k)]
                     if x and x.get("link")) or "BOARD.md"
    print(lab(st, "intro", requests=len(st.get("requests", {})), features=len(st.get("features", {})),
              sections=sections, where=where))


def cmd_docs(a):
    """B1: links to the documents the user opens (board, ledger, features); the board lists them at the top."""
    d = need(find_dir()); st = load(d); rows = st.setdefault("docs", {})
    if a.action == "set":
        if not a.which or not (a.link or "").strip():
            sys.exit(f"usage: docs set {'|'.join(DOC_KINDS)} --link <url> [--note \"<one line>\"]")
        rows[a.which] = {"link": a.link.strip(), "note": " ".join((a.note or "").split())}
        save(d, st, {"ev": "docs-set", "which": a.which})
        print(a.which)
        return
    print("\n".join(f"{lab(st, 'doc_' + k)} ({k}): {rows[k]['link']}" + (f" — {rows[k]['note']}" if rows[k].get("note") else "")
                    for k in DOC_KINDS if k in rows) or lab(st, "no_items"))


def cmd_issue(a):
    """IL-20: one shared known-issues list. Threads check it before reporting a problem."""
    d = need(find_dir())
    st = load(d)
    iss = st.setdefault("issues", [])
    if a.action == "list":
        rows = [i for i in iss if a.all or i.get("status") != "verified"]
        print("\n".join(render_issue(i, st) for i in rows) or "(no known issues)")
        return
    if a.action == "add":
        if not a.text.strip():
            sys.exit('usage: issue add "<what is wrong>" --owner <thread>')
        if a.owner and a.owner not in st["threads"]:
            sys.exit(f"unknown owner {a.owner}")
        i = {"id": f"I{len(iss) + 1}", "text": " ".join(a.text.split()), "owner": a.owner or None, "status": "open",
             "ref": "", "created": now(), "updated": now()}
        iss.append(i)
    else:
        i = next((x for x in iss if x["id"] == a.text), None)
        if not i:
            sys.exit(f"unknown issue {a.text}; known: {', '.join(x['id'] for x in iss) or '-'}")
        if a.status:
            if a.status not in ISSUE_STATUSES:
                sys.exit(f"status: one of {', '.join(ISSUE_STATUSES)}")
            if a.status == "fixed-on-branch" and not (a.ref or i.get("ref") if a.ref is None else a.ref):
                sys.exit("fixed-on-branch needs --ref <branch>@<commit>: QA tests only what is merged")
            if a.status == "merged" and not (i.get("ref") if a.ref is None else a.ref):
                sys.exit("merged needs --ref <base>@<commit> (or the fix's ref from fixed-on-branch): QA tests that "
                         "commit, not whatever is on the base now")
            if a.status == "verified" and i["status"] != "merged":
                sys.exit(f"{i['id']} is {i['status']}: QA verifies only a merged fix (set it to merged first)")
            if a.status == "open":
                i["ref"] = ""  # a reopened issue has no fix yet
            i["status"] = a.status
        if a.ref is not None:
            i["ref"] = a.ref
        if a.owner is not None:
            if a.owner and a.owner not in st["threads"]:
                sys.exit(f"unknown owner {a.owner}")
            i["owner"] = a.owner or None
        i["updated"] = now()
    save(d, st, {"ev": f"issue-{a.action}", "issue": i["id"], "status": i["status"]})
    print(render_issue(i, st))
    if a.action == "add":
        print(f"broadcast it: tw.py info --issue {i['id']}")


def cmd_info(a):
    d = need(find_dir())
    st = load(d)
    text = a.text.strip()
    if a.issue:
        issue = next((i for i in st.get("issues", []) if i["id"] == a.issue), None)
        if not issue:
            sys.exit(f"unknown issue {a.issue}")
        line = render_issue(issue, st)
        text = f"{text}\n{line}" if text else line
    if not text:
        sys.exit('usage: info "<text>" [--issue I3]')
    message = f"[tw:{tag(st)}] INFO lead -> all -\n{text}"
    to = [{"thread": n, "session_id": t.get("session_id")} for n, t in st.get("threads", {}).items()
          if t.get("session_id") and t.get("status") not in ("archived", "resolved", "planned")]
    log(d, {"ev": "info", "text": text, "to": to})
    print(json.dumps({"message": message, "to": to}, ensure_ascii=False) if a.json else
          message + "\n\nsend to:\n" + ("\n".join(f"{x['thread']} {x['session_id']}" for x in to) or "(no live threads)"))


def _qa_label(x):
    return f"{x.get('branch', '?')}@{str(x.get('sha', ''))[:7]}"


def cmd_qa(a):
    """IL-22: merges wait on the board until a regression round covered them."""
    d = need(find_dir())
    st = load(d)
    pending = st.setdefault("qa_pending", [])
    if a.action == "list":
        print("\n".join(_qa_label(x) for x in pending) or "(none)")
        return
    if not a.items and not a.all:
        sys.exit("qa done <branch or sha> ... (what the regression round covered), or --all; waiting: "
                 + (", ".join(_qa_label(x) for x in pending) or "(none)"))
    hit = lambda x, i: i in (x.get("branch"), _qa_label(x)) or (len(i) >= 7 and any(
        str(x.get(k, "")).startswith(i) for k in ("sha", "to")))  # the reviewed commit or the one on the base
    unknown = [i for i in a.items if not any(hit(x, i) for x in pending)]
    if unknown:
        sys.exit(f"not waiting for regression: {', '.join(unknown)}")
    gone = [x for x in pending if a.all or any(hit(x, i) for i in a.items)]
    st["qa_pending"] = [x for x in pending if x not in gone]
    save(d, st, {"ev": "qa-done", "cleared": [_qa_label(x) for x in gone], "note": a.note or ""})
    print(f"cleared {len(gone)} merge(s): {', '.join(_qa_label(x) for x in gone) or '-'}"
          + (f"; still waiting: {', '.join(_qa_label(x) for x in st['qa_pending'])}" if st["qa_pending"] else ""))


# Every header type in references/messages.md and templates/worker-protocol.md.
MSG_TYPES = ("TASK FOLLOWUP HANDOFF RECALL ROSTER INFO ANSWER ACK UPDATE STATUS DONE BLOCKED QUESTION MEMORY "
             "NEED-THREAD").split()
# Words a hand-written template leaves in angle brackets (`<session id>`, `<thread>`); HTML-like `<button>` is not one.
HOLE_WORDS = {"id", "sid", "session", "thread", "peer", "sha", "commit", "branch", "branchname", "recipient", "words",
              "short", "task", "taskid", "goal", "owner", "fill", "here", "todo", "tbd", "name", "n", "session id",
              "task id", "thread name", "fill in"}  # compared lowercased, - and _ as spaces
HTML_TAGS = {"a", "abbr", "b", "br", "button", "div", "form", "hr", "i", "img", "input", "label", "li", "link", "ol",
             "option", "p", "pre", "script", "select", "span", "style", "table", "td", "textarea", "th", "tr", "ul",
             "video", "audio", "source", "meta", "section", "header", "footer", "nav", "main", "svg", "path"}
SESSION_ID = re.compile(r"(?<![A-Za-z0-9_])(?:session_01|cse_)[A-Za-z0-9]{16,}(?![A-Za-z0-9_])")  # real id shapes


def _memory_topics(memory, key):
    """(topics, unchecked count) of one MEMORY.md special section: `- topic — note` gives `topic`."""
    start = _memory_section(memory, key)
    if start is None:
        return [], 0
    end = memory.find("\n## ", start + 1)
    topics, unchecked = [], 0
    for line in (memory[start:] if end < 0 else memory[start:end]).splitlines()[1:]:
        if not line.startswith("- "):
            continue
        item = re.sub(r"\s*_\((?:temporary|added)\b.*?\)_", "", line[2:])  # tw.py memory's marks
        phrase = re.split(r" — | -- |：|: |（| \(", item, maxsplit=1)[0].strip().strip("*`_ ").strip()
        if len(phrase) < 2:
            continue
        if len(phrase.split()) > 5 or len(re.findall(r"[\u3400-\u9fff]", phrase)) > 16:
            unchecked += 1  # a sentence, not a topic: matching it whole would never hit
            continue
        topics.append(phrase)
    return topics, unchecked


def _mentions(text, phrase):
    if re.fullmatch(r"[\x00-\x7f]+", phrase):  # ASCII: whole words only (`AI` is not in `main`)
        return re.search(r"(?<![A-Za-z0-9])" + re.escape(phrase) + r"(?![A-Za-z0-9])", text, re.I) is not None
    return phrase.casefold() in text.casefold()


# TASK fields (references/messages.md), English and the Chinese labels a zh project may use.
MSG_FIELDS = ("Goal|User's words|Context|Read first|Depends on|Others waiting|You change only|Not doing|Done when|"
              "Constraints|Review depth|Model / method|Model|Report|To|Owner|目标|用户原话|背景|先读|依赖|只改|不做|"
              "完成标准|验收标准|完成条件|验收|任务目标|约束|评审|模型|汇报")
GOAL_FIELDS, DONE_FIELDS = {"goal", "目标", "任务目标"}, {"done when", "完成标准", "验收标准", "完成条件", "验收"}
FIELD_RE = re.compile(rf"\s*(?:[-*]|\d+[.)]|#+)?\s*\**({MSG_FIELDS})\**\s*(?:[:：]\**|$)\s*(.*)$", re.I)
EMPTY_VALUES = {"", "...", "…", "?", "-", "n/a", "tbd", "todo", "fixme", "xxx", "???", "[tbd]", "待定", "待填"}
PATH_RE = (r"(?<![\w/])(?:~/|\./|/)?(?:[\w.-]*[A-Za-z_][\w.-]*/)+[\w.-]*[A-Za-z_][\w.-]*"
           r"|(?<![\w/])[\w.-]+\.(?:md|txt|json|ya?ml|toml)\b")  # a/b/c with a letter in each step; 2026/11/21 is not
READ_RE = r"(?i)\b(?:read|see|follow|open)\b|读|看|见|按|照着|打开"


def _wlen(text):
    """Length with each CJK character counted as three: a short Chinese line says as much as a longer English one."""
    return len(text) + 2 * len(re.findall(r"[\u3400-\u9fff]", text))


USER_MSG_TRACES = (  # I2: what a message to the user should not carry
    ("commit-id", r"(?i)(?<![0-9a-z])(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}(?![0-9a-z])"),
    ("command-output", r"(?m)^\$ \S.*|Traceback \(most recent call last\)|^```(?:shell|bash)\b"),
    ("local-path", r"/(?:Users|home)/\S*|\b[A-Za-z]:\\\S*"),
)


def cmd_check_msg(a):
    """IL-11, IL-16, IL-19: read a hand-written message (or, with --prompt, a session prompt) before sending it.
    Problems fail (exit 1); warnings are for the lead to judge and do not."""
    raw = (sys.stdin.read() if a.file == "-" else Path(a.file).read_text(encoding="utf-8")).lstrip("\ufeff")
    if a.user:  # I2: a message for the user reads like a report, not like a terminal
        if a.prompt:
            sys.exit("--user or --prompt, not both")
        found = [(kind, m.group(0).strip()[:40]) for kind, pat in USER_MSG_TRACES for m in [re.search(pat, raw)] if m]
        for kind, sample in found:
            print(f"warning {kind}: {sample}", file=sys.stderr)
        if a.json:
            print(json.dumps({"ok": not found, "mode": "user", "findings": [
                {"kind": k, "detail": v, "level": "warning"} for k, v in found]}, ensure_ascii=False))
        else:
            print("ok (user)" if not found else f"{len(found)} warning(s): say it in plain words; keep commit ids, "
                  "command output and local paths out of a message to the user")
        if found and a.strict:
            raise SystemExit(1)
        return
    findings = []

    def add(kind, detail, level="problem"):
        findings.append({"kind": kind, "detail": detail, "level": level})
    if a.prompt:  # IL-19: the prompt must carry the protocol itself
        if _wlen(raw.strip()) < 600 and re.search(PATH_RE, raw) and re.search(READ_RE, raw):
            add("pointer", "this prompt only points to a file: pass the protocol text itself (TW worker-prompt)")
        mode = "prompt"
    else:
        mode = "message"
        lines = raw.lstrip().splitlines()
        head = lines[0].strip() if lines else ""
        body = "\n".join(lines[1:])
        prose = re.sub(r"`[^`\n]*`", "", re.sub(r"```.*?(?:```|\Z)", "", body, flags=re.S))  # code is not prose
        d = find_dir()
        st = load(d) if d and (d / "state.json").exists() else None
        m = re.fullmatch(r"(?i)\[tw:([^\]\s]+)\] ([A-Z-]+) (\S+) -> (\S+) (\S+)", head)
        if not m:
            add("header", "first line must be `[tw:<short>] <TYPE> <from> -> <to> <task id or ->` (one task id); "
                          "a session prompt is checked with --prompt")
        else:
            short, typ, frm, to, tid = m.groups()
            names = {"lead", "all", "user"} | set((st or {}).get("threads", {}))
            bad = [f"tag {short} (this project is {tag(st)})" for _ in [0] if st and short != tag(st)]
            bad += [f"type {typ}" for _ in [0] if typ not in MSG_TYPES]
            bad += [f"{w} {n}" for w, n in (("from", frm), ("to", to)) if st and n not in names]
            bad += [f"task id {tid}" for _ in [0] if not re.fullmatch(r"T\d+(?:-f\d+)*|-", tid)
                    or st and tid != "-" and tid not in st.get("tasks", {})]
            if bad:
                add("header", ", ".join(bad) + f"; known types: {', '.join(MSG_TYPES)}")
        fields, blines = {}, [ln for ln in body.splitlines()]
        for i, ln in enumerate(blines):  # the raw lines: a value in backticks is a value
            f = FIELD_RE.match(ln)
            if not f:
                continue
            v = f.group(2).strip().strip("*").strip()
            if not v:  # the value may start on the next line (a list under `Done when:`)
                nxt = next((x for x in blines[i + 1:] if x.strip()), "")
                v = "" if not nxt or FIELD_RE.match(nxt) else nxt.strip()
            fields.setdefault(f.group(1).lower(), v)
        holes = [f"{k}: {v[:30] or '(empty)'}" for k, v in fields.items()
                 if v.lower() in EMPTY_VALUES or re.match(r"(?i)\[?(?:TBD|TODO|FIXME|XXX|待定|待填)\]?(?:$|[:(（.,，])", v)]
        holes += [f"<{h}>" for h in re.findall(r"(?<![A-Za-z0-9<])<([A-Za-z\u3400-\u9fff][^<>\n]{0,199})>",
                                               head + "\n" + prose)
                  if not h[-1].isspace() and not h.endswith("-") and not re.search(r"[=\"]", h)
                  and not re.fullmatch(r"[\w.]+(?:\s*[,|]\s*[\w.]+)+", h)  # a type list: <string, number>
                  and not (re.fullmatch(r"[A-Za-z][\w-]*( [\w-]+)?", h) and (h.split()[0].lower() in HTML_TAGS
                                                                              or h[0].isupper() and " " in h))
                  and (" " in h or re.search(r"[\u3400-\u9fff]", h)
                       or re.sub(r"[-_]", " ", h.lower()) in HOLE_WORDS)]
        if holes:
            add("placeholder", ", ".join(dict.fromkeys(holes)))
        if m and m.group(2) == "TASK":
            missing = [n for n, keys in (("Goal", GOAL_FIELDS), ("Done when", DONE_FIELDS)) if not keys & set(fields)]
            if missing:
                add("fields", "a TASK needs " + " and ".join(missing) + (
                    " (it only points to a file: write the task in the message)" if re.search(PATH_RE, prose) else ""))
            if st and st.get("standing") and not re.search(r"(?im)^\s*Standing:|长期要求", raw):
                add("standing", f"the ledger has {len(st['standing'])} standing requirement(s) and this TASK carries "
                                "none: add a `Standing: ...` line for each (`task-msg` writes them)", "warning")
        if st:
            known = {(st.get("lead") or {}).get("session_id")}
            for t in st.get("threads", {}).values():
                known.add(t.get("session_id"))
                known.update(h.get("session_id") for h in t.get("sessions", []) if isinstance(h, dict))
            for sid in sorted(set(SESSION_ID.findall(raw)) - known):  # code too: a pasted id is checked anywhere
                add("unknown-session", sid)
            memory = (d / "MEMORY.md").read_text(encoding="utf-8") if (d / "MEMORY.md").exists() else ""
            for key, kind in (("never_raise", "never-raise"), ("user_owned", "user-owned")):
                topics, unchecked = _memory_topics(memory, key)
                for t in topics:
                    if _mentions(prose, t):
                        add(kind, t + (" (the user ruled it out: do not raise it)" if kind == "never-raise" else
                                       " (the user decides or provides it: do not decide it here)"), "warning")
                if unchecked:
                    add("unchecked", f"{kind}: {unchecked} entr{'y' if unchecked == 1 else 'ies'} too long to check: "
                                     "write them as `topic — note`", "warning")
            lang = str(st.get("prefs", {}).get("language") or "").lower()  # unset: follows the user, nothing to check
            cjk, latin = len(re.findall(r"[\u3400-\u9fff]", prose)), len(re.findall(r"[A-Za-z]", prose))
            if lang.startswith("zh") and latin >= 20 and not cjk:
                add("language", "the conversation language is Chinese: write the body in Chinese", "warning")
            elif lang.startswith("en") and cjk >= 10 and cjk > latin:
                add("language", "the conversation language is English: write the body in English", "warning")
    bad = [x for x in findings if x["level"] == "problem"]
    if a.json:
        print(json.dumps({"ok": not bad, "mode": mode, "findings": findings}, ensure_ascii=False))
    else:
        print("\n".join(f"{'' if x['level'] == 'problem' else 'warning '}{x['kind']}: {x['detail']}"
                        for x in findings) or f"ok ({mode})")
    if bad:
        raise SystemExit(1)


def cmd_deliverable(a):
    """IL-13, IL-14: the index of what the user can open, in delivery order."""
    d = need(find_dir())
    st = load(d)
    dl = st.setdefault("deliverables", [])
    if a.action == "add":
        if a.task and a.task not in st.get("tasks", {}): sys.exit(f"unknown task {a.task}")
        version = a.dispatch_v if a.dispatch_v is not None else (st["tasks"][a.task].get("dispatch_v", 0) if a.task else None)
        existing = next((y for y in dl if a.task and y.get("task") == a.task and y.get("dispatch_v") == version), None)
        if existing:
            x = existing; x["link"] = a.link or x.get("link", ""); x["status"] = a.status or x.get("status", "draft"); x["at"] = now()
        else:
            if not a.name.strip():
                sys.exit('usage: deliverable add "<name>" --link <url> [--status draft|final] [--missing "<what>"]')
            x = {"id": f"D{len(dl) + 1}", "name": a.name.strip(), "status": a.status or "draft", "link": a.link or "",
                 "missing": a.missing or "", "req": a.req or "", "at": now(), **({"task": a.task, "dispatch_v": version} if a.task else {})}
            dl.append(x)
    else:
        x = next((y for y in dl if y["id"] == a.name), None)
        if not x:
            sys.exit(f"unknown deliverable {a.name}; known: {', '.join(y['id'] for y in dl) or '-'}")
        for k in ("status", "link", "missing", "req"):
            if getattr(a, k) is not None:
                x[k] = getattr(a, k)
        x["at"] = now()
    if x["status"] not in DELIVERABLE_STATUSES:
        sys.exit(f"status: one of {', '.join(DELIVERABLE_STATUSES)}")
    if x.get("req") and x["req"] not in st.get("requests", {}):
        sys.exit(f"unknown request {x['req']}")
    save(d, st, {"ev": f"deliverable-{a.action}", "deliverable": x["id"]})
    if a.action == "add" and x.get("task") and existing:
        print(f"updated {x['id']} ({a.task} v{x.get('dispatch_v')}): same task and dispatch, not added twice", file=sys.stderr)
    print(json.dumps(x, ensure_ascii=False))


def cmd_task_msg(a):
    d = need(find_dir()); st = load(d)
    if a.id not in st["tasks"]: sys.exit(f"unknown task {a.id}")
    k = st["tasks"][a.id]
    if a.dispatch:
        if k.get("status") != "dispatched":
            # Reuse task command's transition checks by invoking the normal CLI path.
            proxy = argparse.Namespace(**vars(a)); proxy.action = "set"; proxy.status = "dispatched"
            for key in ("owner", "accept", "deps", "title", "touches", "model", "route", "no_scripts", "scripts_ok", "topics", "notes", "checks", "rounds", "review", "remaining", "no_blocking", "reviewer", "review_note", "skip_review", "user_said", "apply"):
                if not hasattr(proxy, key): setattr(proxy, key, None)
            with contextlib.redirect_stdout(io.StringIO()): cmd_task(proxy)
            st = load(d); k = st["tasks"][a.id]
    if not a.dispatch and k.get("status") != "dispatched":
        print(f"preview only: {a.id} is {k.get('status')}, not dispatched; send with --dispatch (records it first)", file=sys.stderr)
    owner = k.get("owner") or "?"; v = int(k.get("dispatch_v") or 0)
    lines = [f"[tw:{tag(st)}] TASK lead -> {owner} {a.id}", f"Goal: {k.get('title') or a.id}", f"Done when: {k.get('accept') or '-'}"]
    route = k.get("route") or k.get("model")
    if route: lines.append(f"Route/Model: {route}")
    lines.append(f"Dispatch: v{v}")
    lines.extend(lab(st, "standing", text=x) for x in st.get("standing", []))
    print("\n".join(lines))


def cmd_promise(a):
    d = need(find_dir()); st = load(d); rows = st.setdefault("promises", [])
    if a.action == "list": print(json.dumps(rows, ensure_ascii=False, indent=2)); return
    if a.action == "add":
        if a.req and a.req not in st.get("requests", {}): sys.exit(f"unknown request {a.req}")
        if not (a.text or "").strip(): sys.exit('usage: promise add "<what you told the user you would do>"')
        x = {"id": f"P{max([int(y['id'][1:]) for y in rows if y.get('id','')[1:].isdigit()] or [0])+1}", "text": a.text, "req": a.req, "status": "open", "evidence": "", "created": now()}; rows.append(x)
        save(d, st, {"ev": "promise-add"}); print(json.dumps(x, ensure_ascii=False)); return
    pid = a.id or a.text
    x = next((y for y in rows if y["id"] == pid), None)
    if not x: sys.exit(f"unknown promise {pid}" if pid else "usage: promise done P<n> --evidence \"<where>\"")
    if x.get("status") == "done": sys.exit(f"{pid} is already done: {x.get('evidence')}")
    if not (a.evidence or "").strip(): sys.exit("promise done requires --evidence")
    x.update(status="done", evidence=a.evidence.strip()); save(d, st, {"ev": "promise-done"}); print(json.dumps(x, ensure_ascii=False))


def cmd_roster(a):
    d = need(find_dir())
    print(render_roster(load(d), a.thread))


def cmd_open(a):
    d = need(find_dir())
    st = load(d)
    stuck = stuck_threads(st)
    print(
        json.dumps(
            [
                {
                    "thread": n,
                    "kind": t["kind"],
                    "session_id": t.get("session_id"),
                    "status": t["status"],
                    "last_seen": t.get("last_seen"),
                    "last_status_at": t.get("last_status_at"),
                    "stuck_minutes": stuck.get(n),
                    "roster_sent": t.get("roster_seen", 0) >= st.get("roster_version", 0),
                }
                for n, t in st["threads"].items()
                if t["status"] not in ("planned", "resolved", "archived")
            ],
            ensure_ascii=False,
        )
    )


def cmd_prefs(a):
    d = need(find_dir())
    st = load(d)
    old_cap = review_max(st)
    for kv in a.set or []:
        k, _, v = kv.partition("=")
        if k == "ledger_line_every" and v not in ("wrapup", "report"):
            sys.exit("ledger_line_every: one of wrapup, report")
        if k in ("reconcile_every", "selfcheck_every") and v not in ("wrapup", "round"):
            sys.exit(f"{k}: one of wrapup, round")
        if k == "require_review":
            if v.lower() not in ("1", "0", "true", "false", "yes", "no", "on", "off"):
                sys.exit("require_review must be true or false")
            if v.lower() in ("0", "false", "no", "off") and not (a.user_said or "").strip():
                sys.exit("turning the review gate off is the user's decision: add --user-said \"<their words>\"")
        if k in ("default_after_minutes", "stuck_minutes", "checkin_minutes", "check_timeout_minutes", *QUOTA_DEFAULTS) and not re.fullmatch(r"[0-9]+", v):
            sys.exit(f"{k}: a whole number" + ("" if k in QUOTA_DEFAULTS else " of minutes"))
        cur = st["prefs"].get(k)
        if k in ("check_cmd", "language", "short_name", "thread_permission_mode", "skill_edge", "spawn_policy", "perm_policy", "gpt_policy"):
            st["prefs"][k] = v  # text, never coerced (check_cmd=true is a command)
        elif isinstance(cur, bool) or v.lower() in ("true", "false", "yes", "no", "on", "off"):
            st["prefs"][k] = v.lower() in ("1", "true", "yes", "on")
        elif v.isdigit():
            st["prefs"][k] = int(v)
        else:
            st["prefs"][k] = v
        if k == "thread_permission_mode" and v and v not in PERMISSION_MODES:
            sys.exit(f"thread_permission_mode: one of {', '.join(PERMISSION_MODES)}, or empty for the default "
                     f"({THREAD_MODE_DEFAULT})")
        if k == "spawn_policy" and v not in ("", "free", "ask"):
            sys.exit("spawn_policy: one of free, ask, or empty")
        if k == "perm_policy" and v not in ("", "managed", "ask"):
            sys.exit("perm_policy: one of managed, ask, or empty")
        if k in ("spawn_policy", "perm_policy", "gpt_policy") and v:
            if not (a.user_said or "").strip():
                sys.exit(f"{k} is the user's decision: add --user-said \"<their words>\"")
            st["prefs"].setdefault("policy_said", {})[k] = {"text": a.user_said.strip(), "at": now()}
        if k == "thread_permission_mode" and v in ("auto", "bypassPermissions"):
            print(f"note: threads get {v} only if the lead runs at least as loose a mode (`tw.py lead --mode`); "
                  "create_session refuses a thread more permissive than its creator, and bypassPermissions lets a "
                  "thread run everything without asking except the ask rules", file=sys.stderr)
        if k == "thread_permission_mode" and (v or THREAD_MODE_DEFAULT) != THREAD_MODE_DEFAULT \
                and not (a.user_said or "").strip():
            sys.exit(f"threads run in {THREAD_MODE_DEFAULT} (R45): a tighter mode asks the user for ordinary calls, a "
                     "looser one skips the checks; either is the user's decision, add --user-said \"<their words>\"")
        if k == "standing_grants":
            g = [x for x in csv(v) if x]
            bad = [x for x in g if x not in STANDING_GRANTS]
            if bad:
                sys.exit(f"standing_grants: any of {', '.join(STANDING_GRANTS)} (comma list); not {', '.join(bad)}")
            if set(g) - set(cur or []) and not (a.user_said or "").strip():
                sys.exit("a standing grant lets the lead act without asking each time: only the user gives it, add "
                         "--user-said \"<their words>\"")
            st["prefs"][k] = g
        if k == "review_max_rounds":
            if not re.fullmatch(r"[0-9]{1,2}", v):
                sys.exit("review_max_rounds: a whole number of review rounds, 0-99 (default 3; 0 = no limit)")
            if 0 < int(v) < old_cap or (old_cap == 0 and int(v) > 0):
                if not (a.user_said or "").strip():
                    sys.exit("a lower review round limit lets work merge with fewer reviews: that is the user's "
                             "decision, add --user-said \"<their words>\"")
        if k == "short_name" and v and not re.fullmatch(SHORT_RE, v):
            sys.exit("short_name: letters/digits/hyphen, max 6 chars (e.g. TFCC)")
        if k in ("quota_burn_at", "quota_lead_only_at"):
            other = {**QUOTA_DEFAULTS, **st["prefs"], **dict(x.partition("=")[::2] for x in a.set or [])}
            if re.fullmatch(r"[0-9]+", str(other["quota_burn_at"])) and re.fullmatch(
                    r"[0-9]+", str(other["quota_lead_only_at"])) and int(other["quota_burn_at"]) >= int(
                    other["quota_lead_only_at"]):
                sys.exit("quota_burn_at must be below quota_lead_only_at (or burn-fable and save never happen)")
        if k in QUOTA_DEFAULTS and not 0 <= int(v) <= (168 if k.endswith("_hours") else 100):
            sys.exit(f"{k}: a whole number, " + ("hours from 0 to 168" if k.endswith("_hours") else "percent from 0 to 100"))
    retitled = {}
    if any(kv.partition("=")[0] == "short_name" for kv in a.set or []):
        for n, t in st["threads"].items():
            new = title_for(st, n)
            if t.get("title") != new:
                t["title"] = new
                retitled[n] = new
    if a.set and (a.user_said or "").strip():
        st.setdefault("prefs_user_said", []).append({"t": now(), "set": a.set, "text": a.user_said.strip()})
    if a.set:
        save(d, st, {"ev": "prefs", "set": a.set, **({"user_said": a.user_said.strip()}
                                                     if (a.user_said or "").strip() else {})})
    if retitled:
        print(json.dumps({"retitled": retitled, "tags": f"teamwork:{tag(st)}",
                          "note": "update each session with set_session_title / set_session_tags"},
                         ensure_ascii=False), file=sys.stderr)
    print(json.dumps(st["prefs"], ensure_ascii=False))


QUOTA_RANK = ("unknown", "normal", "pre-reset-burn", "burn-fable", "save", "lead-only")  # least to most severe


def cmd_quota(a):
    """T55: the lead's quota check. Reads local files only; bad data counts as missing; exit code is always 0."""
    if quota_reader is None:
        print("quota: scripts/quota.py is missing next to tw.py (reinstall teamwork) · tier unknown · changed: no")
        return
    try:
        sys.stdout.reconfigure(errors="replace")  # a lone surrogate in a cached name must not crash the print
    except (AttributeError, ValueError):
        pass
    dirs = a.config_dir or [os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")]
    codex_dir = a.codex_dir or str(Path.home() / ".codex")
    current = dt.datetime.now().timestamp()
    notes, prefs, prev, changed, state_path, project_dir, prefs_ok = [], {}, None, "no", None, None, True
    try:
        project_dir = find_dir()
        state_path = Path(project_dir) / "quota-state.json" if project_dir else None
        if state_path:
            try:
                prev = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, RecursionError):
                prev = None
            if isinstance(prev, dict) and prev.get("tier") in QUOTA_RANK and isinstance(prev.get("flags", []), list) \
                    and all(x in ("gpt-tight", "fable-done") for x in prev.get("flags", [])):
                prev = {"tier": prev["tier"], "flags": sorted(prev.get("flags", []))}  # only the checked fields
            else:
                prev = None  # a hand-edited or broken file counts as no earlier run
            try:
                st0 = load(project_dir)
                prefs = st0["prefs"] if isinstance(st0, dict) and isinstance(st0.get("prefs"), dict) else None
            except Exception:
                prefs = None
            if prefs is None:  # thresholds unknown: report, but do not record a change on defaults
                prefs, prefs_ok = {}, False
                notes.append("prefs unreadable: default thresholds, state not updated")
    except Exception as exc:  # anything odd about the project: answer as outside one
        notes.append(f"project files unreadable: {exc.__class__.__name__}")
        state_path = None
    # One reading per config folder (different accounts must not be mixed); the most severe tier decides.
    graded = []
    for i, c in enumerate(dirs):
        r = quota_reader.read_quota([c], codex_dir, current)
        lv, fl = quota_reader.tier(r, prefs, current)
        tot = quota_reader.num(((r.get("claude") or {}).get("total") or {}).get("pct"))
        sc = quota_reader.num(((r.get("claude") or {}).get("scoped") or {}).get("pct"))
        graded.append(((QUOTA_RANK.index(lv), tot or -1, sc or -1), i, r, lv, fl))
    _, pick, result, level, flags = max(graded, key=lambda g: (g[0], -g[1]))
    result.setdefault("notes", [])
    result["notes"] += notes
    if sum(g[3] != "unknown" for g in graded) > 1:
        result["notes"].append(f"most severe of {len(dirs)} config folders: #{pick + 1} ({dirs[pick]})")
    signature = {"tier": level, "flags": sorted(x for x in flags if x != "5h-tight")}  # 5h-tight: the lead's own
    if state_path and level != "unknown" and prefs_ok:
        changed = "yes" if (prev is None and (level != "normal" or signature["flags"])) or (
            prev is not None and (prev["tier"], prev["flags"]) != (level, signature["flags"])) else "no"
        try:
            state_path.write_text(json.dumps(dict(signature, at=int(current))), encoding="utf-8")
        except OSError as exc:
            result["notes"].append(f"state not saved ({exc.__class__.__name__}): the next run reports this "
                                   "change again")
        if changed == "yes":
            try:
                log(Path(project_dir), {"ev": "quota", "tier": level, "flags": signature["flags"], "prev": prev})
            except Exception as exc:
                result["notes"].append(f"log not written: {exc.__class__.__name__}")
    output = {**result, "tier": level, "flags": flags, "prev": prev, "changed": changed}
    if a.json:
        print(json.dumps(output, ensure_ascii=False, indent=1))
        return
    claude = result.get("claude", {})
    total, scoped, five = (claude.get(x) or {} for x in ("total", "scoped", "five_hour"))
    gpt = result.get("gpt", {}) or {}
    def show(row):
        return "?" if quota_reader.num(row.get("pct")) is None else f"{row['pct']:.0f}%"
    # Keep the line compact and avoid exposing local paths.
    gpt_label = show(gpt) + ("" if gpt.get("weekly") else "(5h)") if gpt else "?"
    prev_label = prev.get("tier") if prev else "-"
    line = f"quota: total {show(total)} · 5h {show(five)} · {scoped.get('model') or 'Fable'} {show(scoped)} · GPT {gpt_label} · tier {level}"
    if flags:
        line += " [" + ", ".join(flags) + "]"
    line += f" · changed: {changed} · prev {prev_label}"
    if result.get("notes"):
        line += " · notes: " + "; ".join(str(n) for n in result["notes"])
    print(line)


MODEL_TYPES = ("code", "test", "read", "review1", "review2", "docs-en", "docs-zh", "research",
               "debug-hard", "plan", "merge")
MODELS = ("gpt", "fable", "opus")


def _model_log_append(path, record):
    """One JSON line; a hand-edited file without a final newline does not glue two records together."""
    try:
        ends = path.read_bytes()[-1:] in (b"", b"\n")
    except OSError:
        ends = True
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(("" if ends else "\n") + json.dumps(record, ensure_ascii=False) + "\n")


def _model_deny_change(st, typ, model, allow=False):
    rows = st.setdefault("model_deny", [])
    found = next((x for x in rows if x.get("type") == typ and x.get("model") == model), None)
    if allow:
        if found:
            rows.remove(found)
        # remembered: the old redos must not put it straight back; only new tries after this count
        rows.append({"type": typ, "model": model, "allowed_at": now()})
    elif found and found.get("allowed_at"):
        found.pop("allowed_at")
    elif not found:
        rows.append({"type": typ, "model": model})


def _denied(st, typ, model):
    return any(x.get("type") == typ and x.get("model") == model and not x.get("allowed_at")
               for x in st.get("model_deny", []))


def cmd_model_log(a):
    d = need(find_dir())
    st = load(d)
    path = d / "model-log.jsonl"
    if a.summary:
        rows, skipped = [], 0  # (line number, record): `after` counts lines, so the index is the line
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").split("\n")  # not splitlines: U+2028 in a why
        except OSError:
            lines = []
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except (ValueError, RecursionError):
                item = None
            if isinstance(item, dict) and item.get("type") in MODEL_TYPES and item.get("model") in MODELS:
                rows.append((i, item))  # a record, or an `allow` marker (ev) the summary counts from
            else:
                skipped += 1
        summary = []
        pairs = sorted({(x["type"], x["model"]) for _, x in rows if x.get("ev") != "allow"})
        stamp = dt.datetime.now().timestamp()
        for typ, model in pairs:
            pair = [(i, x) for i, x in rows if (x["type"], x["model"]) == (typ, model)]
            marks = [i for i, x in pair if x.get("ev") == "allow"]
            allowed = next((x["allowed_at"] for x in st.get("model_deny", []) if x.get("allowed_at")
                            and (x.get("type"), x.get("model")) == (typ, model)), None)
            # only tries since the user allowed it again: after the log's marker, or (marker cut away) by time
            matched = [x for i, x in pair if x.get("ev") != "allow" and (
                i > marks[-1] if marks else not allowed or str(x.get("at") or "") >= allowed)]
            if not matched:
                continue
            recent = matched[-3:]
            redo = sum(x.get("result") == "redo" for x in recent)
            latest = matched[-1]
            try:
                last = dt.datetime.fromisoformat(str(latest.get("at"))).timestamp()
            except ValueError:
                last = None
            summary.append({"type": typ, "model": model, "count": len(matched),
                            "recent": [x.get("result") for x in recent],
                            "proposed": len(matched) >= 3 and redo >= 2,
                            "denied": _denied(st, typ, model),
                            "retry": bool(last and stamp - last >= 30 * 86400)})
        deny = lambda: [x for x in st.get("model_deny", []) if not x.get("allowed_at")]
        output = {"combinations": summary, "model_deny": deny(), **({"skipped": skipped} if skipped else {})}
        additions = [(x["type"], x["model"]) for x in summary if x["proposed"] and
                     (x["model"], x["type"]) not in (("gpt", "code"), ("gpt", "test")) and not x["denied"]]
        if additions:
            for typ, model in additions:
                _model_deny_change(st, typ, model)
            save(d, st, {"ev": "model-deny-proposed", "items": additions})
            output["model_deny"] = deny()
            for row in summary:
                row["denied"] = _denied(st, row["type"], row["model"])
        if a.json:
            print(json.dumps(output, ensure_ascii=False, indent=1))
        else:
            print(json.dumps(output, ensure_ascii=False))
        return
    if a.allow and a.deny:
        sys.exit("--allow or --deny, not both")
    if a.allow or a.deny:
        typ, model = a.allow or a.deny, a.model
        if typ not in MODEL_TYPES or model not in MODELS:
            sys.exit("--allow/--deny needs a valid type and model")
        if not (a.user_said or "").strip():
            sys.exit("--allow/--deny requires --user-said")
        _model_deny_change(st, typ, model, allow=bool(a.allow))
        if a.allow:  # the marker the summary counts new tries from (survives a cut or emptied log)
            _model_log_append(path, {"ev": "allow", "type": typ, "model": model, "at": now()})
        save(d, st, {"ev": "model-deny-allow" if a.allow else "model-deny-add",
                     "type": typ, "model": model, "user_said": a.user_said.strip()})
        print(f"{typ} on {model}: " + ("allowed again" if a.allow else "on the deny list"))
        return
    if a.type not in MODEL_TYPES or a.model not in MODELS or a.result not in ("ok", "redo"):
        sys.exit("record needs a valid --type, --model and --result")
    if a.task not in st.get("tasks", {}):
        sys.exit(f"unknown task {a.task}")
    record = {"task": a.task, "type": a.type, "model": a.model, "result": a.result,
              "why": a.why, "at": now()}
    _model_log_append(path, record)
    print(json.dumps(record, ensure_ascii=False))



OPENING = {
    "spawn_policy": ("May the lead open sessions (threads) freely?",
                     ["ask me before each (spawn_policy=ask)", "ask only for a new topic (say it in words)",
                      "open by topic without asking (spawn_policy=free)"],
                     ["one line to read and answer per thread", "a question per new topic",
                      "no questions; you see each one on the board"], ("ask", "free")),
    "perm_policy": ("Hand permissions to the lead (it runs in auto and handles prompts)?",
                    ["ask me for each (perm_policy=ask, no auto)", "managed, but show me the sensitive ones (in words)",
                     "managed (perm_policy=managed)"],
                    ["every message between threads asks for a click: about a hundred a night",
                     "a click only for what you name", "switch this session to auto once if the server has not "
                     "accepted it; then only irreversible steps ask"], ("ask", "managed")),
    "gpt_policy": ("Use GPT wherever it can do the work?",
                   ["no GPT (gpt_policy=no)", "only for what I name (say it in words)", "wherever it can (gpt_policy=yes)"],
                   ["all work on Claude quota", "GPT only inside the bounds you set", "Claude plans and reviews, GPT "
                    "writes; saves Claude quota"], ("no", "yes")),
}


def opening_data(d, st):
    answers = []
    for folder in find_projects(d.parent.parent):  # the other projects under the same served folder
        if folder.resolve() == d.parent.resolve():
            continue
        try:
            other = json.loads((folder / ".teamwork" / "state.json").read_text(encoding="utf-8"))
            for key, record in (other.get("prefs", {}).get("policy_said") or {}).items():
                if key in OPENING and record.get("text"):
                    answers.append((record.get("at", ""), key, other["prefs"].get(key), record["text"]))
        except (OSError, ValueError, TypeError):
            continue
    rows = []
    for key, (question, scale, costs, _) in OPENING.items():
        prior = max((x for x in answers if x[1] == key), default=None)
        rows.append({"key": key, "question": question, "scale": [{"step": s, "cost": c} for s, c in zip(scale, costs)],
                     "current": st.get("prefs", {}).get(key) or None,
                     "last": ({"value": prior[2], "text": prior[3], "at": prior[0]} if prior else None),
                     "never_default": key in ("perm_policy", "gpt_policy")})
    return {"questions": rows, "unanswered_now": [k for k in OPENING if not st.get("prefs", {}).get(k)],
            "rule": "until answered: perm and gpt stay at ask / riskless work only; spawn takes the last answer as a default and only reversible steps are taken"}


def cmd_opening(a):
    d = need(find_dir()); result = opening_data(d, load(d))
    if a.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        for row in result["questions"]:
            print(f"{row['key']}: {row['question']}")
            for n, step in enumerate(row["scale"], 1): print(f"  {n}. {step['step']} — {step['cost']}")
            print(f"  current: {row['current']}; last elsewhere: {row['last']}")
        print("unanswered_now: " + ", ".join(result["unanswered_now"]))
        print(result["rule"])


def newer_release(info=None):
    label, info = running_version() if info is None else (None, info)
    skill = Path(__file__).resolve().parents[1]
    if info.get("type") != "release": return None
    try:
        releases = json.loads((skill.parent / "index.json").read_text(encoding="utf-8"))["releases"]
        return releases[-1]["name"] if releases and releases[-1]["name"] != info["name"] else None
    except (OSError, ValueError, KeyError): return None


def cmd_selfcheck(a):
    d = find_dir(); st = load(d) if d else {}; lead = st.get("lead", {})
    def item(name, ok, detail="", fix=""): return {"item": name, "ok": ok, "detail": detail, "fix": fix}
    mode = a.mode or lead.get("permission_mode")
    items = [item("mode", mode == "auto" if mode else None, mode or "mode unavailable", "switch this session's mode away from Auto and back in the mode menu under the message box, then tell the lead"),
             item("session", bool(lead.get("session_id")), str(lead.get("session_id") or "lead session not set"), "TW lead --session <id>")]
    items.append(item("model", a.model == a.served_model if a.model and a.served_model else None,
                      f"served {a.served_model}, configured {a.model} (fallback)" if a.model and a.served_model and a.model != a.served_model else ""))
    items.append(item("effort", a.effort == a.want_effort if a.want_effort else None,
                      f"effort {a.effort}, wanted {a.want_effort}" if a.want_effort and a.effort != a.want_effort else ""))
    folder = Path(st.get("repo", {}).get("path") or (d.parent if d else "."))
    used = a.seats_used
    if used is None:  # the seat cap counts every project's sessions on this computer, not only this one's
        used = 1 if lead.get("session_id") else 0
        own = d.parent.resolve() if d else None
        for f in find_projects(Path(a.served).expanduser() if a.served else (d.parent.parent if d else Path("."))):
            try:
                o = json.loads((f / ".teamwork" / "state.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            used += sum(1 for t in (o.get("threads") or {}).values()
                        if isinstance(t, dict) and t.get("status") != "archived" and t.get("session_id"))
            used += 1 if f.resolve() != own and (o.get("lead") or {}).get("session_id") else 0
    items.append(item("seats", used < a.seats_max, f"{used} of {a.seats_max} used"))
    found = shutil.which("codex")
    items.append(item("gpt", True if found else None, "codex CLI found" if found else "codex CLI not found; GPT work runs only through the codex plugin if installed"))
    label, info = running_version(); newer = newer_release(info)
    items.append(item("version", not bool(newer), f"newer release {newer} available" if newer else
                      label + ("; unreleased changes: this is the live clone, not a release" if info.get("type") == "edge" else ""),
                      "say which version you run, and that the user may want the newest release" if newer else ""))
    items.append(item("ledger", bool(d and (d / "state.json").exists() and (d / "BOARD.md").exists()), "state.json and BOARD.md"))
    found = {"SessionStart": False, "PreToolUse": False}  # RC sessions start in the served folder: its hooks count too
    for base in dict.fromkeys([folder, Path(a.served).expanduser() if a.served else folder.parent]):
        for name in ("settings.json", "settings.local.json"):
            try:
                hk = json.loads((base / ".claude" / name).read_text(encoding="utf-8")).get("hooks") or {}
            except (OSError, ValueError, AttributeError):
                continue
            for ev, word in (("SessionStart", "context"), ("PreToolUse", "bash-guard")):
                for g in hk.get(ev) or []:
                    for h in (g.get("hooks") or []) if isinstance(g, dict) else []:
                        c = str(h.get("command", "")) if isinstance(h, dict) else ""
                        found[ev] |= "tw.py" in c and word in c
    hookok = all(found.values())
    items.append(item("hooks", hookok, "SessionStart context and PreToolUse bash-guard" if hookok else "required hooks not found", "run install.py --link --project <folder> --served <served folder> (dry run, then --apply)"))
    errs = guard_errors_recent()
    if errs:  # reported only when there is something to report
        items.append(item("guard_errors", False, lab(st, "guard_errors", n=errs), f"read {guard_log_path()} and report the error"))
    prefs = st.get("prefs", {}); missing = [k for k in ("perm_policy", "gpt_policy") if not prefs.get(k)]
    items.append(item("opening", not missing, "unanswered: " + ", ".join(missing) if missing else "answered", "TW opening" if missing else ""))
    modeitem, proj = items[0], st.get("project")
    if not d:
        first = f"No teamwork project here: run /teamwork in the project folder (teamwork {label})"
    elif a.desktop:
        first = f"I am the lead of {proj}, on the desktop: I open threads in the turn you send a message (teamwork {label})"
    elif modeitem["ok"]:
        first = f"I am the lead of {proj}, auto is in effect (teamwork {label})"
    elif modeitem["ok"] is None:
        first = (f"I am the lead of {proj}; I could not read this session's mode: check it says Auto in the mode menu "
                 f"under the message box (teamwork {label})")
    else:
        first = f"I am the lead of {proj}; please switch this session to auto once: {modeitem['fix']} (teamwork {label})"
    failed = [x for x in items if x["ok"] is False]
    if a.json: print(json.dumps({"items": items, "first_line": first}, ensure_ascii=False))
    else:
        print(first)
        for x in (failed if a.brief else items): print(f"{'✓' if x['ok'] else '✗' if x['ok'] is False else '?'} {x['item']}: {x['detail']}" + (f"; fix: {x['fix']}" if x['ok'] is False and x['fix'] else ""))


def cmd_worker_prompt(a):
    d = need(find_dir())
    st = load(d)
    if a.thread not in st["threads"]:
        sys.exit(f"unknown thread {a.thread}")
    t = st["threads"][a.thread]
    tpl = (TEMPLATES / "worker-protocol.md").read_text(encoding="utf-8")
    if t["kind"] == "rc":
        channel = (
            "Push: send_message to the lead's session_id (works rc->rc; allowed without a prompt if setup was done). "
            "Also end every turn with the STATUS block, because the lead may read your transcript instead."
        )
    else:
        channel = (
            "Pull only: you are a cloud thread, and a message from a cloud session to an RC session (the lead) is "
            "blocked by account trust rules, so NEVER send_message to the lead and never call confirm_repository_trust. "
            "End EVERY turn with the STATUS block; the lead reads your transcript (get_session / list_events). You MAY "
            "send_message to other cloud threads. You cannot see the project folder; code reaches the lead through git: "
            "commit and push your branch."
        )
    fill = {
        "PROJECT": st["project"],
        "TAG": tag(st),
        "GOAL": st.get("goal") or "-",
        "THREAD": a.thread,
        "KIND": t["kind"],
        "FOCUS": t.get("focus") or "-",
        "OWNS": ", ".join(t.get("owns", [])) or "-",
        "BRANCH": branch_of(st, a.thread),
        "BASE": st.get("repo", {}).get("base") or "main",
        "LEAD_SESSION": st["lead"].get("session_id") or "(unset)",
        "ROSTER": render_roster(st, a.thread),
        "MY_TASKS": render_tasks(st, only_owner=a.thread),
        "INSTRUCTIONS": read_file(d, "INSTRUCTIONS.md").strip()[:16000] or "(none)",
        "MEMORY": read_file(d, "MEMORY.md").strip() or "(empty)",
        "REPORT_CHANNEL": channel,
        "FOLDER": st.get("repo", {}).get("path") or "-",
        "LANGUAGE": language_rule(st),
        "REVIEW_MAX": (f"**{review_max(st)}** rounds" if review_max(st) else "**none** (no round limit; "
                       "the 'at the limit' rule does not apply)"),
        "GUARD": (f"run `python3 \"{SKILL_DIR / 'scripts' / 'tw.py'}\" -C \"{st.get('repo', {}).get('path') or '.'}\" "
                  "guard --repo .` in your worktree before every push; it must print `guard: ok`"
                  if t["kind"] == "rc" else
                  "you cannot see the private list, so never write names of the user's own products, people, "
                  "accounts or machines into files, commits or branch names. Your TASK carries a check script with "
                  "a salted, hashed list (`tw-guard.py`): save it outside the repo (e.g. /tmp/tw-guard.py, never "
                  "commit it) and run `python3 /tmp/tw-guard.py origin/<base>` before every push; it must print "
                  "`guard: ok`. It only catches accidents; the lead re-checks your branch with the real list before "
                  "merging"),
        "WORKTREE": (f"`{t.get('worktree') or worktree_path(st, a.thread)}` (the lead creates it; if it is missing, "
                     f"create it yourself with exactly: `{worktree_cmd(st, a.thread)}`)")
        if t["kind"] == "rc" else "your container's checkout of the repo (cloud thread)",
    }
    out = tpl
    for k, v in fill.items():
        out = out.replace("{{" + k + "}}", v)
    left = re.findall(r"\{\{[A-Z_]+\}\}", out)
    if left:
        sys.exit(f"template placeholders not filled: {left}")
    print(out)


def cmd_worktree(a):
    """Create the rc thread's git worktree and branch if missing (idempotent)."""
    d = need(find_dir())
    st = load(d)
    if a.thread not in st["threads"]:
        sys.exit(f"unknown thread {a.thread}")
    t = st["threads"][a.thread]
    if t["kind"] != "rc":
        sys.exit("cloud threads have no local worktree; they push their branch")
    repo = st.get("repo", {}).get("path") or "."
    base = st.get("repo", {}).get("base") or "main"
    path = Path(t.get("worktree") or worktree_path(st, a.thread))
    git = lambda *x: subprocess.run(["git", "-C", repo, *x], capture_output=True, text=True)
    if git("rev-parse", "--git-dir").returncode != 0:
        sys.exit(f"{repo} is not a git repository: no worktree needed; the thread works in the project folder")
    br = branch_of(st, a.thread)
    res = {"thread": a.thread, "worktree": str(path), "branch": br}
    if path.exists():
        res["created"] = False
        res["note"] = "already exists"
        top = git("-C", str(path), "rev-parse", "--show-toplevel")
        own = top.returncode == 0 and Path(top.stdout.strip()).resolve() == path.resolve()
        r = git("-C", str(path), "symbolic-ref", "-q", "--short", "HEAD") if own else top  # a plain folder: no checks
        if own and r.returncode == 0 and r.stdout.strip() != br:
            sys.exit(f"{path} is on {r.stdout.strip()}, but {a.thread}'s branch is {br}: leases and trace "
                     "follow the ledger's branch; switch the worktree or fix the ledger before the thread works")
        if own and r.returncode != 0:
            sys.exit(f"{path} is on a detached HEAD, but {a.thread}'s branch is {br}: commits there belong to no "
                     f"branch, so leases and trace miss them. `git -C \"{path}\" switch {br}` before the thread works")
    else:
        has_branch = git("show-ref", "--verify", "--quiet", f"refs/heads/{br}").returncode == 0
        cmd = (["worktree", "add", str(path), br] if has_branch
               else ["worktree", "add", "-b", br, str(path), base])
        res["command"] = "git -C " + repo + " " + " ".join(cmd)
        if a.dry_run:
            res["created"] = False
            res["note"] = "dry run"
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            r = git(*cmd)
            if r.returncode != 0:
                sys.exit(f"git worktree add failed: {r.stderr.strip()}")
            res["created"] = True
            src = Path(repo) / ".claude" / "settings.local.json"
            dst = path / ".claude" / "settings.local.json"
            if src.is_file() and not dst.exists():  # the project's permission rules follow the thread (T36)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src.read_bytes())
                common = git("rev-parse", "--git-common-dir").stdout.strip()
                ex = (Path(repo) / common / "info" / "exclude") if common else None
                if ex is not None:
                    ex.parent.mkdir(parents=True, exist_ok=True)
                    cur = ex.read_text(encoding="utf-8") if ex.exists() else ""
                    if ".claude/settings.local.json" not in cur.splitlines():
                        ex.write_text(cur + ("" if cur.endswith("\n") or not cur else "\n")
                                      + ".claude/settings.local.json\n", encoding="utf-8")
                res["settings"] = str(dst)
    if t.get("worktree") != str(path):
        t["worktree"] = str(path)
        save(d, st, {"ev": "worktree", "thread": a.thread, "path": str(path)})
    elif res.get("created"):
        log(d, {"ev": "worktree", "thread": a.thread, "path": str(path)})
    print(json.dumps(res, ensure_ascii=False))


def live_tasks(st, q):
    """A request's linked tasks that still count: a dropped task neither delivers it nor holds it back."""
    return [t for t in (q.get("tasks") or []) if st["tasks"].get(t, {}).get("status") != "dropped"]


def tasks_ready(st, q):
    """True when the request has live linked work and all of it is in review or done (all dropped: not ready)."""
    ts = live_tasks(st, q)
    return bool(ts) and all(st["tasks"].get(t, {}).get("status") in ("review", "done") for t in ts)


def deliver_ready(st):
    """Requests whose live linked tasks are all in review or done move dispatched -> delivered. Returns the ids."""
    moved = []
    for r, q in st.get("requests", {}).items():
        if q["status"] == "dispatched" and tasks_ready(st, q):
            q["status"] = "delivered"
            q["updated"] = now()
            moved.append(r)
    st.setdefault("_moved_reqs", []).extend(moved)
    return moved


def undeliver(st):
    """A delivered or reported request whose task went back to work (review fail, rework) is dispatched again:
    what was delivered or told to the user is no longer the result (report the new one when it comes back)."""
    moved = []
    for r, q in st.get("requests", {}).items():
        ts = live_tasks(st, q)  # every task dropped: the result already told stays told (not re-dispatched)
        if q["status"] in ("delivered", "reported") and not q.get("gate") and not all(  # landed stays; gated waits for the user
                st["tasks"].get(t, {}).get("status") in ("review", "done") for t in ts):
            q["status"] = "dispatched"
            q["updated"] = now()
            moved.append(r)
    st.setdefault("_moved_reqs", []).extend(moved)
    return moved


def unreported_note(st):
    u = unreported(st)
    if not u:
        return ""
    items = "; ".join(f"{r}: {q['text']}" for r, q in u.items())
    return (f"{lab(st, 'unreported')}: {items}. Tell the user what came back, then "
            "`tw.py req set <R> --status reported`.\n\n")


def next_req_id(st):
    nums = [int(r[1:]) for r in st.get("requests", {}) if r[1:].isdigit()]
    return f"R{max(nums, default=0) + 1}"


def check_tasks(st, ids):
    bad = [t for t in ids if t not in st["tasks"]]
    if bad:
        sys.exit(f"unknown task(s) {', '.join(bad)}; add them with `task add` first")
    gone = [t for t in ids if st["tasks"][t].get("status") == "dropped"]
    if gone:
        sys.exit(f"{', '.join(gone)} dropped: a request waits only on live work, link the task that replaces it")


def set_req_status(st, r, q, new, by_import=False, user_said=None):
    """The only place a request's status changes (besides close/drop): checks framework A9 preconditions."""
    if new not in REQ_STATUSES:
        sys.exit(f"status must be one of {REQ_STATUSES}")
    if by_import:
        q["status"] = new
        return
    cur = q["status"]
    if new == cur:
        return
    if new in REQ_END:
        sys.exit(f"only the user ends a request: `req close {r} --user-said \"<their words>\"` (accepted) or "
                 f"`req drop {r} --user-said \"<their words>\"` (not to do it)")
    frm = q.get("held_from") or "open" if cur == "held" else cur  # IL-33: held pauses a request where it was
    moves = ({frm} | REQ_MOVES.get(frm, set())) - {"held"} if cur == "held" else set(REQ_MOVES.get(cur, set()))
    if cur not in REQ_END:
        moves.add("wont")  # any live request can be set aside with a reason (F7)
    if new not in moves:
        allowed = ", ".join(sorted(moves)) or "nothing (it has ended)"
        sys.exit(f"{r} is {cur}" + (f" (paused while {frm})" if cur == "held" else "") + f": it can move to {allowed}")
    user_said = (user_said or "").strip() or None
    if (frm, new) in REQ_NEEDS_USER and not user_said:
        sys.exit(f"{r} {cur} -> {new} is the user's decision: add --user-said \"<their words>\"")
    if new == "landed" and cur != "reported":
        sys.exit(f"{r}: landed is only allowed from reported")
    if new == "reported" and cur == "open":
        busy = [t for t in live_tasks(st, q) if st["tasks"].get(t, {}).get("status") not in ("review", "done")]
        if busy:
            sys.exit(f"{r} has work still in progress ({', '.join(busy)}): it is reported once that work is delivered")
    # A request the user had to say yes to keeps that gate through waiting_user / wont / open (no side door)
    if new in ("waiting_user", "wont") and frm in ("proposed", "reported", "landed"):
        q["gate"] = frm
    if new == "dispatched" and q.get("gate") and not user_said:
        sys.exit(f"{r} came from {q['gate']}: dispatching it is the user's decision: add --user-said \"<their words>\"")
    if new == "dispatched" and user_said:
        q.pop("gate", None)
    if new == "delivered":
        ts = live_tasks(st, q)
        not_ready = [t for t in ts if st["tasks"].get(t, {}).get("status") not in ("review", "done")]
        if not ts or not_ready:
            sys.exit(f"{r} is delivered only when every linked task that is not dropped is in review or done"
                     + (f" (not yet: {', '.join(not_ready)})" if not_ready else
                        " (all linked tasks are dropped: link the new work, or ask the user to drop the request)"
                        if q.get("tasks") else " (no tasks linked)"))
    if new == "held":
        q["held_from"] = cur
    else:
        q.pop("held_from", None)
    q["status"] = new
    if user_said:
        q.setdefault("user_said", []).append({"t": now(), "to": new, "text": user_said})
    if new == "reported":
        q["reported_at"] = now()


def cmd_req(a):
    d = need(find_dir())
    st = load(d)
    reqs = st.setdefault("requests", {})
    if a.action == "list":
        rows = {r: q for r, q in reqs.items() if a.all or (q["status"] not in REQ_END and q["status"] != "remark")}
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if a.action == "import":
        cmd_req_import(a, d, st); return
    if a.action == "migrate":
        suggestions = {}
        for rid, q in reqs.items():
            if q.get("status") != "reported" or q.get("migrated"): continue
            text = " ".join(str(q.get(k) or "") for k in ("followup", "where", "text")); low = text.lower()
            rule = None; target = None
            if any(w in low for w in ("等你", "待你", "确认", "回访", "你定", "wait for you")): target="waiting_user"; rule=next(w for w in ("等你", "待你", "确认", "回访", "你定", "wait for you") if w in low)
            elif q.get("where") and any(w in low for w in ("已合并", "合入", "merged", "已做")): target="landed"; rule=next(w for w in ("已合并", "合入", "merged", "已做") if w in low)
            elif any(w in low for w in ("不做", "放弃", "won't")): target="wont"; rule=next(w for w in ("不做", "放弃", "won't") if w in low)
            suggestions[rid] = {"suggested": target or "unclear", "rule": rule}
        only = set(csv(a.only)) if a.only else None
        if only and only - set(suggestions): sys.exit(f"--only: not in the preview: {', '.join(sorted(only - set(suggestions)))}")
        if a.apply:
            said = (a.user_said or "").strip()
            if not said: sys.exit("req migrate --apply requires --user-said")
            moved = []
            for rid, rec in suggestions.items():
                if rec["suggested"] == "unclear" or (only is not None and rid not in only): continue
                q=reqs[rid]; q["status"]=rec["suggested"]; q["updated"]=now()
                q["migrated"]={"from":"reported","at":now(),"rule":rec["rule"],"user_said":said}
                q.setdefault("user_said", []).append({"t": now(), "to": rec["suggested"], "text": said})
                if rec["suggested"] in ("waiting_user", "wont"):
                    q["gate"] = "reported"  # agreeing to the migration is not agreeing to more work
                if rec["suggested"] == "wont": q["reason"]=f"migrated (matched: {rec['rule']})"
                if rec["suggested"] == "landed": q["evidence"]=q.get("where")
                moved.append(rid)
            if moved: save(d, st, {"ev":"req-migrate", "ids":moved, "user_said":said})
        print(json.dumps(suggestions, ensure_ascii=False, indent=2))
        print(f"migrate: {sum(x['suggested'] != 'unclear' for x in suggestions.values())} suggested, {sum(x['suggested'] == 'unclear' for x in suggestions.values())} unclear", file=sys.stderr)
        return
    if a.action == "add":
        if not a.args:
            sys.exit('usage: req add "<what the user asked, close to their words>" [--tasks T1,T2]')
        r = a.id or next_req_id(st)
        if r in reqs:
            sys.exit(f"request {r} exists")
        tasks = csv(a.tasks)
        if a.remark and tasks: sys.exit("remark cannot have tasks")
        check_tasks(st, tasks)
        q = {"text": " ".join(a.args), "status": "remark" if a.remark else "open", "tasks": [] if a.remark else tasks, "where": a.where or "",
             "followup": a.followup or "", "created": now(), "updated": now(),
             **{k: getattr(a, k) for k in ("phase", "category") if getattr(a, k)}}
        reqs[r] = q
        if tasks and not a.remark:
            q["status"] = "dispatched"
            deliver_ready(st)  # linked to work that is already in review or done
        save(d, st, {"ev": "req-add", "req": r, "text": q["text"], "tasks": tasks})
        print(json.dumps({r: q}, ensure_ascii=False))
        owners = ", ".join(dict.fromkeys(st["tasks"][t]["owner"] for t in tasks if st["tasks"][t].get("owner")))
        print(lab(st, "rcpt_remark", r=r) if a.remark else lab(st, "rcpt_given", r=r, who=owners) if owners
              else lab(st, "rcpt_open", r=r), file=sys.stderr)  # B3: the receipt the lead repeats; stdout stays JSON
        return
    if not a.args:
        sys.exit(f"usage: req {a.action} R<n> ...")
    r = a.args[0]
    if r not in reqs:
        sys.exit(f"unknown request {r}; known: {', '.join(reqs) or '-'}")
    q = reqs[r]
    if a.action == "link":
        if q.get("status") == "remark": sys.exit("remark cannot have tasks")
        tasks = csv(",".join(a.args[1:])) + csv(a.tasks)
        if not tasks:
            sys.exit("usage: req link R<n> T1 [T2 ...]")
        check_tasks(st, tasks)
        q["tasks"] = list(dict.fromkeys(q.get("tasks", []) + tasks))
        if q["status"] in REQ_END:
            sys.exit(f"{r} has ended ({q['status']}); register a new request instead")
        if q["status"] in ("reported", "landed"):
            # more work on something already reported: the user reopens it (reported -> dispatched)
            set_req_status(st, r, q, "dispatched", user_said=a.user_said)
        if q["status"] == "open" and q.get("gate"):  # was proposed / reported / landed: the user's yes first
            set_req_status(st, r, q, "dispatched", user_said=a.user_said)
        if q["status"] == "open":
            q["status"] = "dispatched"  # proposed stays proposed until the user says yes
        undeliver(st)  # a new task still in progress
        deliver_ready(st)
    elif a.action == "set":
        if q["status"] in REQ_END:
            sys.exit(f"{r} has ended ({q['status']}); register a new request instead of editing it")
        was_held = q["status"] == "held"
        if a.status:
            if a.status == "wont":
                if not (a.reason or "").strip(): sys.exit("wont requires --reason")
                q["reason"] = a.reason.strip()
            if a.status == "landed":
                if not (a.evidence or "").strip(): sys.exit("landed requires --evidence")
                q["evidence"] = a.evidence.strip()
            set_req_status(st, r, q, a.status, user_said=a.user_said)
        if a.status == "dispatched" and not q.get("tasks"):
            sys.exit(f"{r} has no tasks: link the work first (`req link {r} T<n>`), which dispatches it")
        if was_held and a.status == "dispatched":
            deliver_ready(st)  # back from held after the work finished meanwhile (rework stays dispatched)
        for k in ("where", "followup", "text", "phase", "category"):
            v = getattr(a, k)
            if v is not None:
                q[k] = v
    elif a.action in ("close", "drop"):
        drop = a.action == "drop" or a.declined
        a.user_said = (a.user_said or "").strip()
        if not a.user_said:
            sys.exit(f"only the user ends a request: add --user-said \"<the user's words>\" "
                     f"({'not to do it' if drop else 'accepting the result'})")
        if q["status"] in REQ_END:
            sys.exit(f"{r} has already ended ({q['status']})")
        if not drop and q["status"] == "wont":
            sys.exit(f"{r} is set aside (wont): if the user agrees not to do it, `req drop {r} --user-said \"<their words>\"`")
        if not drop and q["status"] not in ("reported", "landed"):
            sys.exit(f"{r} is {q['status']}: report it to the user first (`req set {r} --status reported`), "
                     "then close it when they accept")
        if not drop:
            for t in q.get("tasks") or []:
                why = accept_gate(st, t) if st["tasks"].get(t, {}).get("status") == "review" else None
                if why:
                    sys.exit(f"{r}: {t} {why}; the user accepts only checked work")
        back = [t for t in live_tasks(st, q) if st["tasks"].get(t, {}).get("status") not in ("review", "done")]
        if not drop and back:
            sys.exit(f"{r} was reported, but {', '.join(back)} went back to work since: report the new result "
                     "first (the user accepted an old one)")
        q["status"] = "dropped" if drop else "closed"
        q["closed_reason"] = "dropped" if drop else "accepted"
        q["closed_at"] = now()
        q.setdefault("user_said", []).append({"t": now(), "to": q["status"], "text": a.user_said})
        if a.note:
            q["closed_note"] = a.note
    q["updated"] = now()
    save(d, st, {"ev": f"req-{a.action}", "req": r, "status": q["status"]})
    print(json.dumps({r: q}, ensure_ascii=False))


def cmd_req_import(a, d, st):
    """Import a markdown table ledger: | # | what the user said | where | status | follow-up |."""
    if not a.args:
        sys.exit("usage: req import <file.md> [--dry-run]")
    lines = Path(a.args[0]).read_text(encoding="utf-8").splitlines()
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines if ln.strip().startswith("|")]
    rows = [r for r in rows if not all(set(c) <= set("-: ") for c in r)]
    if not rows:
        sys.exit("no markdown table found")
    head, body = rows[0], rows[1:]
    def col(*names):
        for i, h in enumerate(head):
            if any(n in h.lower() for n in names):
                return i
        return None
    ci = {"id": col("#", "id"), "text": col("用户说的", "request", "asked", "said"),
          "where": col("落到哪里", "where"), "status": col("状态", "status"), "followup": col("回访", "follow")}
    if ci["id"] is None or ci["text"] is None or ci["status"] is None:
        sys.exit(f"table needs id, request and status columns; found header {head}")
    reqs = st.setdefault("requests", {})
    out, skipped = {}, []
    for row in body:
        get = lambda k: (row[ci[k]] if ci[k] is not None and ci[k] < len(row) else "").strip()
        r = get("id")
        if not re.fullmatch(r"R\d+", r):
            continue
        if r in reqs:
            skipped.append(r)
            continue
        status = REQ_STATUS_ALIASES.get(get("status"))
        if status is None:
            sys.exit(f"{r}: unknown status {get('status')!r}; use one of {sorted(REQ_STATUS_ALIASES)}")
        clean = lambda v: "" if v in ("—", "-", "–") else v
        where = clean(get("where"))
        tasks = [t for t in dict.fromkeys(re.findall(r"\bT\d+\b", where)) if t in st["tasks"]]
        span = re.search(r"\bT(\d+)\s*[–-]\s*T(\d+)\b", where)  # T10–T13
        if span:
            tasks += [f"T{i}" for i in range(int(span.group(1)), int(span.group(2)) + 1)
                      if f"T{i}" in st["tasks"] and f"T{i}" not in tasks]
        tasks.sort(key=lambda t: int(t[1:]))
        q = {"text": get("text"), "status": "open", "tasks": tasks, "where": where,
             "followup": clean(get("followup")), "created": now(), "updated": now(), "imported": True}
        set_req_status(st, r, q, status, by_import=True)
        if status in REQ_END:
            q["closed_reason"] = "imported (ended in the old ledger; no user words recorded here)"
        if status == "landed" and not q.get("evidence"):
            q["evidence"] = "imported: " + (where or "no evidence in the old ledger")
        if status == "wont" and not q.get("reason"):
            q["reason"] = "imported (no reason in the old ledger)"
        if status in ("waiting_user", "wont", "landed"):
            q["gate"] = "imported"  # history unknown: dispatching it again takes the user's words
        if q["status"] == "delivered" and tasks and not tasks_ready(st, q):
            q["status"] = "dispatched"  # linked work is not finished (an old row without tasks keeps its status)
        out[r] = q
    if a.dry_run:
        print(json.dumps({"would_import": out, "skipped_existing": skipped}, ensure_ascii=False, indent=2))
        return
    reqs.update(out)
    save(d, st, {"ev": "req-import", "file": a.args[0], "ids": list(out), "skipped": skipped})
    print(json.dumps({"imported": list(out), "skipped_existing": skipped}, ensure_ascii=False))


# ------------------------------------------------------------------ privacy guard

PRIVATE_HELP = """# Private words and paths: never in commits, branches, messages to public places.
# This file lives in .teamwork/ and is never committed. One entry per line, case-insensitive.
#   some-private-product        a word or phrase (substring match)
#   path: secrets/**            a path glob that must never be committed
# Private notes and files go in .teamwork/private/ (ignored, never committed).
"""


def _clean_entry(ln):
    ln = ln.strip()
    m = re.fullmatch(r"<!--\s*(.*?)\s*-->", ln)
    if m:
        ln = m.group(1)  # <!-- X --> still means X
    ln = re.sub(r"^(>\s*)+", "", ln)  # quotes
    ln = re.sub(r"^([-*+]|\d+[.)])\s+", "", ln)  # list bullets and numbering
    ln = re.sub(r"^\[[ xX]?\]\s*", "", ln)  # task-list boxes: - [ ] X, - [x] X
    ln = ln.replace("~~", "")  # strikethrough
    if ln.startswith("#"):
        return ""
    ln = re.sub(r"\s+#.*$", "", ln)  # inline comment
    m = re.fullmatch(r"(\*\*|__)?\s*path:\s*(.*)", ln.strip(), re.I | re.S)
    if m:
        g = m.group(2).strip()  # a path glob keeps its * and _ ; only bold around the whole rule, quotes, backticks go
        if m.group(1) and g.endswith(m.group(1)):
            g = g[: -len(m.group(1))].strip()
        while len(g) > 1 and g[0] == g[-1] and g[0] in "`\"'":
            g = g[1:-1].strip()
        return "path:" + g
    ln = ln.strip().strip("*_`\"'").strip()  # markdown emphasis / quotes
    return ln


def _entry_terms(ln):
    """One cleaned line -> its terms. The whole line always counts; so do its comma/、/；-separated parts and the
    head of 'X - note', 'X — note', 'X: note', 'X（note）' (extra terms only make the guard stricter)."""
    out = [ln]
    parts = [p.strip().strip("*_`\"'").strip() for p in re.split(r"\s*[,，、;；/|]\s*", ln)]
    if len(parts) > 1:
        out += parts
    for p in list(out):
        kv = re.split(r"[:：]\s*", p, maxsplit=1)
        if len(kv) == 2:
            out += [x.strip().strip("*_`\"'").strip() for x in kv]  # 'Client: X' -> both sides
        head = re.split(r"\s+[-–—]\s+|\s*[（(]", p, maxsplit=1)[0].strip().strip("*_`\"'").strip()
        if head and head != p:
            out.append(head)
    return [t for t in dict.fromkeys(out) if len(t) >= 2 or (t and ord(t[0]) > 0x2E7F)]  # 1 char only if CJK


def home_terms():
    """Built-in private words: this machine's home folder path in its usual spellings (a home path in a repo is
    always a leak). The bare user name is not added: it is often a public handle too."""
    out = []
    for h in {str(Path.home()), os.environ.get("HOME") or ""}:
        h = h.rstrip("/\\")
        if len(h) >= 6 and h.count("/") + h.count("\\") >= 2:
            out += [h, h.replace("/", "\\"), h.replace("\\", "/")]
    return list(dict.fromkeys(out))


def load_private(d, extra=None):
    """(terms, path globs, files found). Missing files are reported by the caller (fail closed).
    Accepts plain lines, bullets, numbering, task boxes, **bold**, '# comments', comma lists, 'X - note' and
    markdown tables (first cell of each row; the header row and the --- row are skipped)."""
    terms, paths, found = [], [], []
    for f in [d / "PRIVATE.md"] + [Path(x).expanduser() for x in (extra or [])]:
        if not f.exists():
            continue
        found.append(f)
        raw_b = f.read_bytes()
        try:
            if raw_b[:2] in (b"\xff\xfe", b"\xfe\xff"):
                txt = raw_b.decode("utf-16")
            else:
                txt = raw_b.decode("utf-8-sig")
        except UnicodeDecodeError:
            sys.exit(f"{f.name}: not UTF-8 or UTF-16 text; save it as UTF-8 (the guard fails closed)")
        lines = txt.splitlines()
        for i, raw in enumerate(lines):
            s = raw.strip()
            if s.startswith("|"):
                cells = [c.strip() for c in s.strip("|").split("|")]
                if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    continue  # | --- | --- |
                nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
                if nxt.startswith("|") and re.fullmatch(r"[|\s:\-]+", nxt):
                    continue  # header row
                s = cells[0] if cells else ""
            ln = _clean_entry(s)
            if not ln:
                continue
            body = re.sub(r"^\s*(>\s*)*([-*+]|\d+[.)])?\s*(\[[ xX]?\]\s*)?", "", s)
            lit = re.fullmatch(r"(`|\"|')(.+)\1(\s+#.*)?", body.strip())  # a quoted entry keeps its own '#'
            body = re.sub(r"\s+#.*$", "", body).strip()
            lit = lit or re.fullmatch(r"(`|\"|')(.+)\1", body)
            if lit and not ln.lower().startswith("path:"):
                if len(lit.group(2).strip()) >= 2:  # one character would match every file
                    terms.append(lit.group(2).strip())  # quoted or `code`: the whole text, never split
                continue
            if re.match(r"(~|/|[A-Za-z]:[\\/]|\\\\)", ln) and not ln.lower().startswith("path:"):
                # a filesystem path (/Users/x, ~/x, C:\\x) counts whole: never split at / or :. A list of them
                # (`/a/x, /b/y` or `;`) adds each path whole; a bare `/` or `~` is too short to count.
                # a note after it (`X - note`, `X: note`, `X (note)`) is dropped first, as for any entry
                pl = re.sub(r"\s+-\s+.*$|:\s+.*$|\s+\([^()]*\)\s*$", "", ln).strip()
                terms += [p for p in (x.strip() for x in re.split(r"\s*[,;]\s+|\s*;\s*|,(?=[/~]|[A-Za-z]:[\\/])", pl)) if len(p) >= 3
                          and p not in ("~/", "//")]
                continue
            if ln.lower().startswith("path:"):
                g = ln[5:].strip()
                if g:
                    paths.append(g)
            else:
                terms += _entry_terms(ln)
    return list(dict.fromkeys(terms)), list(dict.fromkeys(paths)), found


def mask(term):
    return "*" * 5  # never reveal the word, its first letter or its length


class _Masker:
    def __init__(self, terms):
        self.res = [re.compile(re.escape(t), re.I) for t in sorted(terms, key=len, reverse=True)]

    def __call__(self, text):
        for r in self.res:
            text = r.sub("*****", text)
        return text


_GUARD_ENCODINGS = ("utf-8", "utf-16-le", "utf-16-be", "gb18030", "big5", "shift_jis", "cp1252", "latin-1")


def _needles(terms):
    """Byte patterns for every term in every encoding a file may use, matched against data.lower()."""
    import unicodedata
    out = set()
    for t in terms:
        forms = {unicodedata.normalize(f, t) for f in ("NFC", "NFD")}
        for v in {x for f in forms for x in (f, f.lower(), f.upper(), f.title())}:
            for enc in _GUARD_ENCODINGS:
                try:
                    out.add(v.encode(enc).lower())
                except (UnicodeEncodeError, LookupError):
                    continue
    return sorted((n for n in out if n), key=len)


def _bytes_has(data, needles, depth=0):
    """True if raw bytes hold any needle (case-insensitive for ASCII; other cases via the variants above).
    Zip-based files (docx, xlsx, pptx, odt, jar, zip) and gzip are opened and their contents checked too."""
    if isinstance(needles, str):
        needles = _needles([needles])
    low = data.lower()
    if _hit(needles, low):
        return True
    for alt in _decoded_views(data):  # \uXXXX, RTF \uN, &#N; / &amp;, %XX, XML split by tags
        if _hit(needles, alt):
            return True
    if depth >= 2:
        return False
    import io
    if data[:5] == b"%PDF-":
        return any(_bytes_has(x, needles, depth + 1) for x in _pdf_streams(data))
    if data[:3] == b"BZh" or data[:6] == b"\xfd7zXZ\x00":
        import bz2
        import lzma
        try:
            dec = bz2.BZ2Decompressor() if data[:3] == b"BZh" else lzma.LZMADecompressor()
            return _bytes_has(dec.decompress(data, max_length=256 * 1024 * 1024), needles, depth + 1)  # no bomb
        except Exception:
            return False
    if data[:4] == b"PK\x03\x04":
        import zipfile
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist()[:2000]:
                    if _bytes_has(info.filename.encode("utf-8", "replace"), needles, depth + 1):
                        return True
                    if info.file_size <= 64 * 1024 * 1024 and _bytes_has(z.read(info), needles, depth + 1):
                        return True
        except Exception:
            return False
    elif data[:2] == b"\x1f\x8b":
        import gzip
        try:
            return _bytes_has(gzip.decompress(data)[:256 * 1024 * 1024], needles, depth + 1)
        except Exception:
            return False
    return False


def _hit(needles, low):
    return needles(low) if callable(needles) else any(n in low for n in needles)


class HashedList:
    """Salted hashes of private words, for a cloud thread that must not see the words (SKILL step 3): a word is
    found anywhere in the text (as with the real guard), after NFC and case folding. No lengths are stored; decoys
    (random values, never hashes of a real string, so they cannot match) pad the list. The prefilter keeps 3 hex
    digits (4096 buckets) of each word's first three characters, so it narrows a guess but does not name the
    prefix; two-character words are checked directly. The salt ships with the list: anyone with the kit can still
    test guesses against it (a dictionary word or a short name falls fast). It stops an accidental push, not
    someone set on guessing. Text over 64 MB is not checked and counts as a hit (fails closed)."""

    MAXLEN = 64
    PRE = 3
    LIMIT = 64 * 1024 * 1024

    def __init__(self, salt, hashes, pre):
        import hashlib
        self.salt, self.hashes, self.pre = salt, set(hashes), set(pre)
        self._base = hashlib.sha256(salt.encode("utf-8"))

    @staticmethod
    def norm(t):
        import unicodedata
        return unicodedata.normalize("NFC", t).casefold()

    def h(self, t, n=20):
        x = self._base.copy()  # = sha256(salt + t), without hashing the salt again for every window
        x.update(t.encode("utf-8"))
        return x.hexdigest()[:n]

    @classmethod
    def make(cls, terms):
        import secrets
        salt = secrets.token_hex(16)
        self = cls(salt, [], [])
        words = [cls.norm(t) for t in terms if len(cls.norm(t)) >= 2]
        words = [w[: cls.MAXLEN] for w in words]
        n_decoys = len(words) + secrets.randbelow(8) + 3
        hashes = [self.h(w) for w in words] + [secrets.token_hex(10) for _ in range(n_decoys)]
        pre = [self.h(w[:3], cls.PRE) for w in words if len(w) >= 3] + \
            [secrets.token_hex(2)[:cls.PRE] for _ in range(secrets.randbelow(4))]
        secrets.SystemRandom().shuffle(hashes)
        secrets.SystemRandom().shuffle(pre)
        return {"teamwork_hashed_private_list": 1, "salt": salt, "hashes": hashes, "pre": sorted(set(pre))}

    @classmethod
    def load(cls, path):
        try:
            j = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls(j["salt"], j["hashes"], j["pre"])
        except (OSError, ValueError, KeyError, TypeError):
            sys.exit(f"guard: FAILED, {path} is not a hashed private list (`tw.py guard --hash-list` makes one)")

    def kit(self):
        """A self-contained check script (stdlib only) for a cloud thread whose repo has no tw.py."""
        return KIT_TEMPLATE.replace("@SALT@", self.salt).replace("@H@", json.dumps(sorted(self.hashes))) \
            .replace("@P@", json.dumps(sorted(self.pre)))

    def __call__(self, low):
        if len(low) > self.LIMIT:
            return True  # too large to check: fail closed
        t = self.norm(low.decode("utf-8", "replace"))
        H, P, h = self.hashes, self.pre, self.h
        for i in range(len(t) - 1):
            if h(t[i:i + 2]) in H:
                return True
            if i + 3 <= len(t) and h(t[i:i + 3], self.PRE) in P:
                for j in range(i + 3, min(len(t), i + self.MAXLEN) + 1):
                    if h(t[i:j]) in H:
                        return True
        return False


KIT_TEMPLATE = r'''#!/usr/bin/env python3
# tw-guard.py: teamwork privacy check for a cloud thread. Keep it OUTSIDE the repo; never commit it.
# Salted hashes only: it stops accidental pushes, not someone guessing a word. Usage: python3 tw-guard.py origin/main
import hashlib, os, subprocess, sys, unicodedata
S, H, P = "@SALT@", set(@H@), set(@P@)
B = hashlib.sha256(S.encode("utf-8"))
def h(t, n):
    x = B.copy(); x.update(t.encode("utf-8")); return x.hexdigest()[:n]
def hit(b):  # same rule as tw.py's HashedList: 2-char words directly, longer ones behind a 3-char prefilter
    if len(b) > 64 * 1024 * 1024: return True  # too large to check: fail closed
    t = unicodedata.normalize("NFC", b.decode("utf-8", "replace")).casefold()
    for i in range(len(t) - 1):
        if h(t[i:i + 2], 20) in H: return True
        if i + 3 <= len(t) and h(t[i:i + 3], 3) in P:
            for j in range(i + 3, min(len(t), i + 64) + 1):
                if h(t[i:j], 20) in H: return True
    return False
base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
def g(*a):  # any git error fails closed: a wrong base or no repo must never print ok
    r = subprocess.run(["git", *a], capture_output=True)
    if r.returncode != 0:
        sys.exit("guard: FAILED, git " + a[0] + " failed (" + r.stderr.decode("utf-8", "replace").strip()[:200]
                 + "); check the base (git fetch first) and run it inside the repo")
    return r.stdout
g("rev-parse", "--verify", "--quiet", base + "^{commit}")
os.chdir(g("rev-parse", "--show-toplevel").decode("utf-8", "replace").strip())  # from a subfolder too: every file
bad = []
if hit(g("log", "-p", "-m", "--text", "--format=%an %ae %cn %ce %B", base + "..HEAD")): bad.append("commits")
if hit(g("log", "--name-only", "--format=", base + "..HEAD")): bad.append("a file name in the commits")
if hit(g("rev-parse", "--abbrev-ref", "HEAD")): bad.append("the branch name")
for n in [x for x in g("ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0") if x]:
    if not os.path.isfile(n):
        continue  # deleted in the work tree, a folder (submodule) or a dangling link: nothing to read
    try:
        data = open(n, "rb").read()
    except OSError:
        bad.append(n.decode("utf-8", "replace") + " (unreadable)"); continue
    if hit(n) or hit(data): bad.append("(a file name)" if hit(n) else n.decode("utf-8", "replace"))
print("guard: FAILED, private words in: " + ", ".join(bad) if bad else "guard: ok")
sys.exit(1 if bad else 0)
'''


def _decoded_views(data):
    """Lower-cased UTF-8 views of data with common text escapes undone, only when the data has such escapes:
    JSON/Python \\uXXXX, RTF \\uN (TextEdit), HTML/XML &#N; &#xH; &amp;, URL %XX, and XML/HTML with its tags
    removed (a docx word split into runs). Each view is checked like the raw bytes."""
    if len(data) > 64 * 1024 * 1024:
        return []
    out = []
    try:
        t = data.decode("utf-8")
    except UnicodeDecodeError:
        t = data.decode("latin-1")
    if "\\u" in t:
        def u4(m):
            return chr(int(m.group(1), 16))
        v = re.sub(r"\\u([0-9a-fA-F]{4})", u4, t)  # JSON / Python: exactly four hex digits
        try:
            v = v.encode("utf-16", "surrogatepass").decode("utf-16")  # join surrogate pairs
        except UnicodeError:
            pass
        out.append(v)
        def rtf(m):  # RTF: signed decimal, then one fallback character (?, \'hh or a space-separated char)
            c = int(m.group(1)) % 65536
            return "" if 0xD800 <= c <= 0xDFFF else chr(c)
        out.append(re.sub(r"\\u(-?\d{1,5}) ?(?:\\'[0-9a-fA-F]{2}|\?)?", rtf, t))
    if "&" in t and ";" in t:
        import html
        out.append(html.unescape(t))
    if re.search(r"%[0-9a-fA-F]{2}", t):
        from urllib.parse import unquote
        out.append(unquote(t, errors="replace"))
    if "</" in t or "/>" in t:
        import html
        out.append(html.unescape(re.sub(r"<[^<>]{0,2000}>", "", t)))
    return [v.lower().encode("utf-8", "replace") for v in out]


def _pdf_streams(data):
    """Decoded content of a PDF's streams (FlateDecode via zlib) plus the text shown with Tj/TJ joined, so
    '(Zor) -20 (blax) TJ' reads as Zorblax. Fonts with custom encodings are not decoded."""
    import zlib
    out = []
    for m in re.finditer(rb"stream\r?\n", data):
        start = m.end()
        end = data.find(b"endstream", start)
        if end < 0:
            break
        raw = data[start:end]
        try:
            raw = zlib.decompressobj().decompress(raw, 256 * 1024 * 1024)
        except zlib.error:
            pass
        out.append(raw)
        strings = re.findall(rb"\(((?:\\.|[^\\)])*)\)", raw)
        hexes = re.findall(rb"<([0-9A-Fa-f\s]{2,})>", raw)
        joined = b"".join(re.sub(rb"\\(.)", rb"\1", x) for x in strings)
        for h in hexes:
            try:
                joined += bytes.fromhex(h.decode().replace(" ", "").replace("\n", ""))
            except ValueError:
                pass
        if joined:
            out.append(joined)
        if len(out) > 4000:
            break
    return out


def _path_rule_hit(name, g):
    """gitignore-like and case-insensitive: 'secrets/*', '/secrets/', 'Notes.md' (any folder), '**/x'."""
    import fnmatch
    n = name.lower().lstrip("/")
    g = g.lower().strip()
    anchored = g.startswith("/")
    g = g.lstrip("/")
    core = g.rstrip("/")
    if not core:
        return False
    if fnmatch.fnmatchcase(n, core) or fnmatch.fnmatchcase(n, core + "/*") or n.startswith(core + "/"):
        return True
    if fnmatch.fnmatchcase(n, core.replace("**/", "")):
        return True
    if not anchored and "/" not in core:
        return any(fnmatch.fnmatchcase(c, core) for c in n.split("/"))  # bare name: any folder level
    if not anchored and core.startswith("**/"):
        return any(fnmatch.fnmatchcase("/".join(n.split("/")[i:]), core[3:]) for i in range(len(n.split("/"))))
    return False


def guard_scan(repo, rng, ref, terms, paths, worktree=True, refnames=(), hashed=None):
    """Hits [(where, what)] for the commits in rng (git rev args; messages, author/committer, diffs incl. binary
    and merges, changed paths), tag objects, the tree of ref, ref names, and (worktree=True) every non-ignored
    file in the checkout (names and bytes). Nothing private is printed: names are masked."""
    rp = str(repo)

    env = {**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"}  # scan what is really sent, not local replacements

    def git(*x, text=True, inp=None):
        return subprocess.run(["git", "-c", "core.quotePath=false", "-C", rp, *x], capture_output=True, text=text,
                              input=inp, env=env)

    if git("rev-parse", "--git-dir").returncode != 0:
        sys.exit(f"{repo} is not a git repository")
    # literal parts of path rules are private too: never print them
    rule_words = [w for g in paths for w in re.split(r"[*?\[\]{}/,]+", g) if len(w) >= 2]
    m = _Masker(list(terms) + rule_words) if hashed is None else \
        (lambda x: "(name hidden)" if hashed(x.lower().encode("utf-8", "replace")) else x)
    needles = _needles(terms) if hashed is None else hashed
    hits = []

    def name_hits(where, name, show=None):
        shown = m(show if show is not None else name)
        if _bytes_has(name.encode("utf-8", errors="replace"), needles):
            hits.append((f"{where} {shown}", "private word in a name"))
        for i, g in enumerate(paths, 1):
            if _path_rule_hit(name, g):
                hits.append((f"{where} {shown}", f"private path rule #{i}"))

    for rn in refnames:
        name_hits("ref name", rn)
    if rng:
        raw = git("log", "--encoding=UTF-8", "-p", "-m", "--text", "--no-textconv", "--no-ext-diff", "--no-renames",
                  "--format=%x00commit %H%n%an <%ae>%n%cn <%ce>%n%B", *rng, text=False).stdout
        for chunk in raw.split(b"\x00commit ")[1:]:
            if _bytes_has(chunk, needles):
                hits.append((f"history {chunk[:12].decode('ascii', 'replace')}", "private word"))
        names = git("log", "-m", "--no-renames", "--name-only", "-z", "--format=", *rng, text=False).stdout
        for n in {x.decode("utf-8", errors="replace").strip("\n") for x in names.split(b"\0")}:
            if n:
                name_hits("history path", n)
    def scan_blobs(blobs, where):
        if not blobs:
            return
        out = git("cat-file", "--batch", inp=b"".join(x.encode() + b"\n" for x, _ in blobs), text=False).stdout
        pos = 0
        for sha, pth in blobs:
            nl = out.index(b"\n", pos)
            head = out[pos:nl].split()
            if len(head) != 3:  # "<sha> missing"
                pos = nl + 1
                continue
            size = int(head[2])
            data = out[nl + 1:nl + 1 + size]
            pos = nl + 1 + size + 1
            if _bytes_has(data, needles):
                hits.append((f"{where}{m(pth)}", "private word"))

    def scan_tree(tree, label):
        ls = git("ls-tree", "-r", "-z", tree, text=False).stdout
        blobs = []
        for e in [x for x in ls.split(b"\0") if x]:
            meta, _, path = e.partition(b"\t")
            pth = path.decode("utf-8", errors="replace")
            name_hits(f"tree {label}:", pth)
            f = meta.split()
            if len(f) == 3 and f[1] == b"blob":  # symlinks are blobs too: their target text is scanned
                blobs.append((f[2].decode(), pth))
        scan_blobs(blobs, f"tree {label}:")

    if rng:  # every blob the pushed commits add, read whole: archives (docx, zip, gz) deep in a diff get opened
        rawl = git("log", "-m", "--no-renames", "--raw", "--no-abbrev", "-z", "--format=", *rng, text=False).stdout
        items, blobs = rawl.split(b"\0"), {}
        for i, e in enumerate(items):
            if e.lstrip(b"\n").startswith(b":") and i + 1 < len(items):
                f = e.lstrip(b"\n").split()
                if len(f) >= 5 and set(f[3]) != {ord("0")}:
                    blobs.setdefault(f[3].decode(), items[i + 1].decode("utf-8", errors="replace"))
        scan_blobs(list(blobs.items()), "history file ")

    # every tag object on the way down (nested tags too), and whatever the chain ends at
    ends = set()
    for t in dict.fromkeys([ref] + [x for x in refnames if x.startswith("refs/tags/")]):
        if not t:
            continue
        obj, seen = t, set()
        while git("cat-file", "-t", obj).stdout.strip() == "tag" and obj not in seen:
            seen.add(obj)
            body = git("cat-file", "-p", obj, text=False).stdout
            if _bytes_has(body, needles):
                hits.append((f"tag {m(t)}", "private word in a tag message"))
            mo = re.match(rb"object ([0-9a-f]{40,64})", body)
            if not mo:
                break
            obj = mo.group(1).decode()
        kind = git("cat-file", "-t", obj).stdout.strip()
        end = git("rev-parse", "--verify", "--quiet", obj).stdout.strip() or obj
        if end in ends:
            continue
        ends.add(end)
        if kind == "blob":  # also a lightweight tag that names a blob directly
            scan_blobs([(end, "(tagged blob)")], f"tag {m(t)} -> ")
        elif kind == "tree":
            scan_tree(end, f"{m(t)}^{{tree}}")
    # the tree that is being pushed / merged, every blob in raw bytes
    if ref and git("rev-parse", "--verify", "--quiet", ref + "^{commit}").returncode == 0:
        scan_tree(ref + "^{commit}", m(ref))
    if worktree:
        out = git("ls-files", "-z", "--cached", "--others", "--exclude-standard", text=False).stdout
        for n in [x.decode("utf-8", errors="replace") for x in out.split(b"\0") if x]:
            name_hits("worktree", n)
            if n.startswith(".teamwork/private/") or n == ".teamwork/PRIVATE.md":
                hits.append(("worktree .teamwork/" + ("private/*****" if "/private/" in n else "PRIVATE.md"),
                             "private folder is tracked or not ignored"))
            fp = Path(rp) / n
            try:
                # a symlink is committed as its target path, so scan that text, never the file it points to
                data = os.readlink(fp).encode("utf-8", "replace") if fp.is_symlink() else fp.read_bytes()
            except OSError:
                continue
            if _bytes_has(data, needles):
                hits.append((f"worktree {m(n)}", "private word"))
    return list(dict.fromkeys(hits))


def cmd_guard(a):
    if a.hash_list:
        d = need(find_dir())
        terms, _, found = load_private(d, a.private)
        if not found:
            sys.exit(f"guard: no private list {d / 'PRIVATE.md'}")
        # no home path here: it would hand the cloud thread a guessable user name, and a cloud container's home
        # differs anyway; the lead's own guard checks the home path when the branch comes back
        j = HashedList.make(terms)
        print(HashedList(j["salt"], j["hashes"], j["pre"]).kit() if a.kit else json.dumps(j))
        return
    hashed = HashedList.load(a.hashed) if a.hashed else None
    if hashed is not None:  # a cloud thread: no .teamwork here, only the hashed list from its TASK
        d, st, terms, paths, found = None, {}, [], [], [a.hashed]
    else:
        d = need(find_dir())
        st = load(d)
        terms, paths, found = load_private(d, a.private)
    terms = terms + [t for t in home_terms() if hashed is None and t.lower() not in {x.lower() for x in terms}]
    if not found:
        sys.exit(f"guard: FAILED, no private list: create {d / 'PRIVATE.md'} (tw.py init does; it may stay empty "
                 "if the user has nothing private)")
    repo = Path(a.repo or os.getcwd()).resolve()
    genv = {**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"}
    git = lambda *x: subprocess.run(["git", "-C", str(repo), *x], capture_output=True, text=True, env=genv)
    ok = lambda r: git("rev-parse", "--verify", "--quiet", r + "^{commit}").returncode == 0
    jobs = []  # (rev args for the commits to scan, ref whose tree is scanned, ref names)
    if a.pre_push:
        # git pre-push: argv remote name; stdin <local ref> <local sha> <remote ref> <remote sha>
        remote, advertised = a.remote, None
        lines = sys.stdin.read().splitlines()
        if not [x for x in lines if x.strip()] and a.stdin_owned:
            print("guard: ok (nothing to push)")  # git runs the hook with empty stdin when everything is up to date
            return
        if not [x for x in lines if x.strip()]:
            sys.exit("guard: FAILED, the pre-push hook got no refs on stdin: another command in the hook read it "
                     "first (git lfs pre-push does). Save it once with input=$(cat) and pipe \"$input\" to each "
                     "command (install.py --pre-push prints the lines); the guard fails closed")
        for ln in lines:
            parts = ln.split()
            if len(parts) != 4 or set(parts[1]) == {"0"}:
                continue  # deleting a remote branch sends nothing
            local_ref, local_sha, remote_ref, remote_sha = parts
            tip = git("rev-parse", "--verify", "--quiet", local_sha + "^{commit}").stdout.strip()
            if not tip:
                rng = None  # a tag on a blob or tree: guard_scan peels and scans it
            elif not set(remote_sha) == {"0"} and ok(remote_sha):
                rng = [f"{remote_sha}..{tip}"]
            else:
                # Branch new on this remote. Local remote-tracking refs can be stale (the remote URL changed),
                # so exclude only commits the server itself advertises now; offline: scan everything.
                if advertised is None:
                    advertised = []
                    if remote:
                        pu = git("remote", "get-url", "--push", remote)  # pushurl / pushInsteadOf: where it goes
                        r = git("ls-remote", pu.stdout.strip() if pu.returncode == 0 and pu.stdout.strip()
                                else remote)
                        advertised = [x.split()[0] for x in r.stdout.splitlines() if x.split()] \
                            if r.returncode == 0 else []
                    advertised = [x for x in dict.fromkeys(advertised) if ok(x)]
                rng = [tip, "--not", *advertised] if advertised else [tip]
            jobs.append((rng, local_sha, [local_ref, remote_ref]))
    else:
        ref = a.ref
        if a.base:
            if not ok(a.base):
                sys.exit(f"base {a.base} not found (git fetch first, or pass --base)")
            base = a.base
        else:
            b = st.get("repo", {}).get("base") or "main"
            base = f"origin/{b}" if ok(f"origin/{b}") else b
            if not ok(base):
                sys.exit(f"base {base} not found (git fetch first, or pass --base)")
        full = git("rev-parse", "--symbolic-full-name", ref).stdout.strip()
        jobs.append(([f"{base}..{ref}"], ref, [x for x in dict.fromkeys((ref, full)) if x and x != "HEAD"]))
    if not terms and not paths and hashed is None:
        print(f"guard: ok (private list {found[0]} is empty)")
        return
    hits = []
    for i, (rng, ref, names) in enumerate(jobs):
        hits += guard_scan(repo, rng, ref, terms, paths, worktree=not a.pre_push and i == 0, refnames=names,
                           hashed=hashed)
    hits = list(dict.fromkeys(hits))
    if hits:
        print(f"guard: FAILED, {len(hits)} private hit(s). Remove them (history hits need the commits rewritten "
              "before any push):")
        for where, what in hits:
            print(f"  {where}: {what}")
        sys.exit(1)
    print("guard: ok (hashed list: words anywhere in the text, no path rules; the lead re-checks with the real list)" if hashed is not None
          else f"guard: ok ({len(terms)} words, {len(paths)} paths checked)")


def _read_log(d):
    out = []
    if (d / "log.jsonl").exists():
        for ln in (d / "log.jsonl").read_bytes().decode("utf-8", errors="replace").split("\n"):
            if not ln.strip():
                continue
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if isinstance(e, dict):
                out.append(e)
    return out


def trace_data(d, st, r):
    q = st["requests"][r]
    repo = st.get("repo", {}).get("path")
    git = (lambda *x: subprocess.run(["git", "-c", "core.quotePath=false", "-C", repo, *x],
                                     capture_output=True, text=True)) if repo else None
    is_git = bool(git) and git("rev-parse", "--git-dir").returncode == 0
    base = _base_ref(st, git) if is_git else None
    warnings = []
    if is_git and base is None:
        warnings.append(f"base branch {st.get('repo', {}).get('base') or 'main'} not found: commits on the base "
                        "cannot be listed (fix repo.base)")
    log_all = _read_log(d)
    linked = q.get("tasks") or []
    def ids_of(e):  # hand-edited logs may hold anything here
        v = e.get("ids")
        return csv(v) if isinstance(v, str) else [str(x) for x in v] if isinstance(v, list) else []
    events = [e for e in log_all
              if e.get("req") == r or e.get("task") in linked or r in ids_of(e)]
    # T1 is not T10, T1.5, T1a, T1-2 or T1_x
    idre_of = lambda tid: re.compile(rf"(^|[^A-Za-z0-9_]){re.escape(tid)}(?![0-9A-Za-z_]|[.-][0-9A-Za-z])")
    any_linked = [idre_of(x) for x in linked]
    since = [f"--since={st['created']}"] if st.get("created") else []
    tasks, other_work, other_seen = [], {}, set()
    for tid in linked:
        k = st["tasks"].get(tid, {})
        owners = []  # every thread that ever owned it, current one last
        for e in log_all:
            if e.get("task") == tid:
                sn = e.get("snap_task") if isinstance(e.get("snap_task"), dict) else {}
                o = sn.get("owner") or e.get("owner")
                if isinstance(o, str) and o and o not in owners:
                    owners.append(o)
        if k.get("owner"):
            owners = [o for o in owners if o != k["owner"]] + [k["owner"]]
        th = st["threads"].get(k.get("owner") or "", {})
        item = {"task": tid, "title": k.get("title"), "status": k.get("status"), "review": k.get("review"),
                "checks": k.get("checks"), "touches": k.get("touches") or [], "owners": owners,
                "thread": k.get("owner"), "thread_status": th.get("status"), "session_id": th.get("session_id"),
                "sessions": [h.get("session_id") for h in th.get("sessions", [])],
                "session_history": th.get("sessions", []),
                "branch": th.get("branch"), "worktree": th.get("worktree"), "commits": []}
        if is_git:
            idre = idre_of(tid)
            seen = set()
            for o in owners:
                br0 = (st["threads"].get(o) or {}).get("branch")
                for br in ([br0, f"origin/{br0}"] if br0 else []):
                    if not base or git("rev-parse", "--verify", "--quiet", br).returncode != 0:
                        continue
                    for x in git("log", "--format=%h %ad %s", "--date=short", f"{base}..{br}").stdout.splitlines():
                        h = x.split(" ", 1)[0]
                        if h in seen:
                            continue
                        seen.add(h)
                        subj = x.split(" ", 2)[-1]
                        if idre.search(subj):
                            item["commits"].append(f"(unmerged {br}) {x}")
                        elif o == k.get("owner") and k.get("status") not in ("done", "dropped", "recalled") \
                                and not any(rx.search(subj) for rx in any_linked) and h not in other_seen:
                            other_seen.add(h)  # listed once, under the first branch that has it
                            other_work.setdefault(br, []).append(x)
            if base:
                for x in git("log", "--format=%h %ad %s", "--date=short", "-E", *since,
                             f"--grep=(^|[^A-Za-z0-9]){re.escape(tid)}([^0-9]|$)", base).stdout.splitlines():
                    if x.split(" ", 1)[0] not in seen and idre.search(x.split(" ", 2)[-1]):
                        item["commits"].append(f"({base}) {x}")
        tasks.append(item)
    return {"request": r, **{k: q.get(k) for k in ("text", "status", "where", "followup", "created", "updated",
                                                   "reported_at", "closed_at", "closed_reason", "closed_note")},
            "tasks": tasks, "other_branch_work": other_work, "events": events, "warnings": warnings,
            "followups": [{"task": t, "title": x.get("title"), "status": x.get("status"), "owner": x.get("owner"),
                           "followup_of": x.get("followup_of"),
                           **({"parent_verdict_voided": x["parent_verdict_voided"]}
                              if x.get("parent_verdict_voided") else {})}
                          for t, x in st["tasks"].items() if r in (x.get("from_requests") or [])]}


def cmd_trace(a):
    d = need(find_dir())
    st = load(d)
    r = a.request
    if r not in st.get("requests", {}):
        sys.exit(f"unknown request {r}; known: {', '.join(st.get('requests', {})) or '-'}")
    t = trace_data(d, st, r)
    if a.json:
        print(json.dumps(t, ensure_ascii=False, indent=2))
        return
    L = lambda k: lab(st, k)
    out = [f"{r} [{L('req_' + t['status'])}] {t['text']}",
           f"  {L('t_created')} {t['created'] or '-'}  {L('t_reported')} {t['reported_at'] or '-'}"
           + (f"  {L('t_closed')} {t['closed_at'] or '-'} ({t['closed_reason']})" if t["status"] in REQ_END else "")]
    if t.get("closed_note"):
        out.append(f"  {L('t_closed')}: {t['closed_note']}")
    if t["where"]:
        out.append(f"  {L('t_where')}: {t['where']}")
    if t["followup"]:
        out.append(f"  {L('followup')}: {t['followup']}")
    for w in t["warnings"]:
        out.append(f"  ! {w}")
    if not t["tasks"]:
        out.append(f"  ({L('t_no_tasks')}: `req link {r} T<n>`)")
    for k in t["tasks"]:
        rv = k["review"] or {}
        out.append(f"  - {k['task']} [{k['status']}] {k['title']}"
                   + (f"  review {rv.get('result')} ({rv.get('reviewer') or '-'})" if rv else ""))
        out.append(f"      {L('t_thread')} {k['thread'] or '-'} [{k['thread_status'] or '-'}]"
                   f"  {L('t_session')} {k['session_id'] or '-'}  {L('t_branch')} {k['branch'] or '-'}"
                   + (f"  (owners: {' -> '.join(k['owners'])})" if len(k["owners"]) > 1 else ""))
        if len(k.get("session_history") or []) > 1:
            out.append("      sessions: " + " -> ".join(
                f"{h.get('session_id')}" + (f" ({h['reason']})" if h.get("reason") else "")
                for h in k["session_history"]))
        for c in k["commits"]:
            out.append(f"      {c}")
    for f in t.get("followups") or []:
        out.append(f"  - {f['task']} [{f['status']}] follow-up of {f['followup_of']} (does not hold {r}): {f['title']}"
                   + (f" (its verdict was voided: {f['parent_verdict_voided']})" if f.get("parent_verdict_voided") else ""))
    for br, cs in t["other_branch_work"].items():
        out.append(f"  {L('t_other')} {br}:")
        out += [f"      {c}" for c in cs]
    if t["events"]:
        out.append(f"  {L('t_log')}:")
        for e in t["events"]:
            out.append(f"    {e.get('t', '')} {e.get('ev')} " + " ".join(
                f"{x}={e[x]}" for x in ("req", "task", "status", "owner") if e.get(x)))
    print("\n".join(out))


def cmd_handover(a):
    """Handover brief for the next session of a seat: what the seat owns, open work, where the code is."""
    d = need(find_dir())
    st = load(d)
    if a.thread not in st["threads"]:
        sys.exit(f"unknown thread {a.thread}")
    t = st["threads"][a.thread]
    rank = {"working": 0, "dispatched": 1, "blocked": 2, "review": 3}
    open_tasks = dict(sorted(((tid, k) for tid, k in st["tasks"].items()
                              if k.get("owner") == a.thread and k["status"] not in ("done", "dropped")),
                             key=lambda x: rank.get(x[1]["status"], 9)))
    repo = st.get("repo", {}).get("path")
    commits, commit_note = [], ""
    base_name = st.get("repo", {}).get("base") or "main"
    git = lambda *x: subprocess.run(["git", "-C", repo, *x], capture_output=True, text=True)
    if not repo or not Path(repo).is_dir() or git("rev-parse", "--git-dir").returncode != 0:
        commit_note = " unknown (no git repository at the project path)" if t["kind"] == "rc" else \
            " see the branch on the remote"
    else:
        base = _base_ref(st, git)
        if base is None:
            commit_note = f" unknown (base branch {base_name} not found)"
        else:
            seen, heads = set(), {}
            for br in (branch_of(st, a.thread), f"origin/{branch_of(st, a.thread)}"):
                h = git("rev-parse", "--verify", "--quiet", br)
                if h.returncode != 0:
                    continue
                heads[br] = h.stdout.strip()
                r = git("log", "--format=%h %s", f"{base}..{br}")
                if r.returncode != 0:
                    commit_note = f" unknown (git log failed on {br})"
                for c in r.stdout.splitlines():
                    if c.split(" ", 1)[0] not in seen:
                        seen.add(c.split(" ", 1)[0])
                        commits.append(f"{c}  [{br}]")
            if len(set(heads.values())) > 1:
                commit_note = (f" (local and origin differ: commits tagged only [{branch_of(st, a.thread)}] are not pushed, "
                               f"only [origin/{branch_of(st, a.thread)}] not fetched)")
    hist = t.get("sessions", [])
    closed = [h for h in hist if h.get("to")]
    cur = next((h for h in reversed(hist) if h.get("session_id") == t.get("session_id") and not h.get("to")), None)
    reported = bool(cur) and _after(t.get("last_status_at"), cur.get("from"))
    # The session leaving is the one holding the seat now: SKILL step 6 runs handover before reseat. No guessing;
    # if the seat was already switched, name the old session with --leaving.
    if a.leaving:
        # prefer a finished stint of that session (A -> B -> A: --leaving A means the stint that ended)
        hit = next((h for h in reversed(hist) if h.get("session_id") == a.leaving and h.get("to")), None) or \
            next((h for h in reversed(hist) if h.get("session_id") == a.leaving), None)
        if not hit:
            sys.exit(f"{a.leaving} never held the {a.thread} seat; its sessions: "
                     f"{', '.join(h.get('session_id') or '-' for h in hist) or '-'}")
        leaving = {**hit, "reason": a.reason or (hit.get("reason") if hit.get("to") else None)}
    else:
        leaving = {"session_id": t.get("session_id"), "reason": a.reason} if t.get("session_id") else {}
        if cur and not reported and closed:
            print(f"note: {t.get('session_id')} has not reported since it took the seat. If you already reseated "
                  f"{a.thread}, re-run with --leaving {closed[-1].get('session_id')}; otherwise this brief is right.",
                  file=sys.stderr)
    lines = [
        f"[tw:{tag(st)}] TASK lead -> {a.thread} " + (next(iter(open_tasks), "-")),
        (f"You take over the {a.thread} seat from an earlier session ({leaving['session_id']}, left: "
         f"{leaving.get('reason') or '-'}). Its history is not in your context; this brief is what it left."
         if leaving.get("session_id") else
         f"You start the {a.thread} seat (no earlier session). This brief is what the board knows."),
        f"Goal: take over the {a.thread} seat and continue its open tasks below.",
        "Done when: you replied ACK with what is done, what is next, and anything you cannot reach.",
        f"- Owns: {', '.join(t.get('owns', [])) or '-'}",
        f"- Branch: {branch_of(st, a.thread)} (base {base_name})" + (f"; worktree {t['worktree']}" if t.get("worktree") else ""),
        f"- Last summary: {t.get('summary') or '-'}",
        "- Unmerged commits on the branch:" + (commit_note or ("" if commits else " none")),
    ] + [f"    {c}" for c in commits] + ["- Open tasks:" + ("" if open_tasks else " none")] + [
        f"    {tid} [{k['status']}] {k['title']} — done when: {k.get('accept') or '-'}" for tid, k in open_tasks.items()
    ] + [
        "- Last STATUS of the previous session (paste the last <tw-status> block from its transcript, "
        "list_events kinds=[\"result\"] limit 100):",
        "    <paste here>",
        ("First: cd into the worktree (check out the branch there), read the commits above, " if t["kind"] == "rc" else
         "First: fetch and check out the branch in your container, read the commits above, ")
        + "then reply ACK with what you think is done and what is next. Do not redo committed work.",
    ]
    print("\n".join(lines))
    args = session_args(st, a.thread)
    print(f"open the new session with create_session(..., permission_mode={args['permission_mode']!r}, "
          f"extra_allowed_tools={args['extra_allowed_tools']!r})"
          + (f"; {args['permission_warning']}" if args.get("permission_warning") else ""), file=sys.stderr)


def _blank_state(project, folder):
    return {"project": project, "goal": "", "created": None, "lead": {"session_id": None},
            "envs": {"rc": None, "cloud": None}, "repo": {"url": None, "path": str(folder), "base": "main"},
            "threads": {}, "tasks": {}, "plan_approved": False, "questions": [], "requests": {}, "features": {},
            "promises": [],
            "prefs": {"max_parallel": 0, "propose_before_start": True, "update_detail": "brief", "checkin_minutes": 10,
                      "require_review": True}}


def _blank_thread(st, name, kind="rc"):
    return {"kind": kind, "focus": "", "owns": [], "session_id": None, "status": "planned",
            "branch": f"tw/{st['project']}/{name}", "created": None, "last_seen": None, "summary": "", "sessions": []}


def _pref_value(v):
    if v.lower() in ("true", "false", "yes", "no", "on", "off"):
        return v.lower() in ("true", "yes", "on")
    return int(v) if v.isdigit() else v


def rebuild_state(d, project=None, sessions=None, short=None):
    """Replay log.jsonl (snapshots win over sparse old fields), then add sessions and tw/<project>/* branches."""
    st = None
    notes = []
    old_short = None
    if (d / "state.json").exists():
        try:
            old = json.loads((d / "state.json").read_text(encoding="utf-8"))
            project = project or old.get("project")
            old_short = ((old.get("prefs") or {}).get("short_name") or "").strip() or None
        except (OSError, ValueError, AttributeError):
            pass
    events = _read_log(d)
    if not project:  # the log lost its head: take the name from any later event, so nothing before it is skipped
        project = next((n for e in events for n in [(e.get("snap_meta") or {}).get("project") if isinstance(
            e.get("snap_meta"), dict) else None, e.get("project")] if isinstance(n, str) and n), None)
    if events and events[0].get("ev") != "init":
        notes.append("log.jsonl does not start with init: its earliest events may be lost; check the result")
    if (d / "log.jsonl").exists():
        total = sum(1 for ln in (d / "log.jsonl").read_bytes().split(b"\n") if ln.strip())
        if total > len(events):
            notes.append(f"skipped {total - len(events)} unreadable log line(s)")
    bad = 0
    for e in events:
        try:
            st = _replay(st, e, project, notes, d)
        except (AttributeError, TypeError, KeyError, ValueError):
            bad += 1
    if bad:
        notes.append(f"skipped {bad} log event(s) with unexpected content")
    if st is not None and not (st.get("prefs") or {}).get("short_name"):
        st.setdefault("prefs", {})["short_name"] = short or old_short or default_short(st["project"])
        if not (short or old_short):
            notes.append(f"short name not in the log: using {st['prefs']['short_name']} "
                         "(pass --short <name> if titles and tags used another)")
    seen = set()  # every short name the project ever had: live sessions may still carry an earlier one
    for e in events:
        sm = e.get("snap_meta")
        if isinstance(sm, dict) and isinstance(sm.get("prefs"), dict) and isinstance(sm["prefs"].get("short_name"), str):
            seen.add(sm["prefs"]["short_name"])
        for kv in (e.get("set") or []) if isinstance(e.get("set"), list) else []:
            if isinstance(kv, str) and kv.startswith("short_name="):
                seen.add(kv.partition("=")[2])
    leads = {e["session"] for e in events if e.get("ev") == "lead" and isinstance(e.get("session"), str)}
    return _finish_rebuild(d, st, project, sessions, notes, short, sorted(x for x in seen if x), leads)


def _replay(st, e, project, notes, d):
        ev = e.get("ev", "")
        if not isinstance(ev, str):
            raise TypeError("ev")
        if st is None:
            name = (e.get("snap_meta") or {}).get("project") or e.get("project") or project
            if not name:
                return st
            st = _blank_state(name, d.parent)
            st["created"] = e.get("t")
        if e.get("snap_meta"):
            for k, v in e["snap_meta"].items():
                if v is not None:
                    st[k] = v
        if ev == "lead":
            if e.get("session"):
                st["lead"]["session_id"] = e["session"]
            st["envs"].update({k: v for k, v in (e.get("envs") or {}).items() if v})
        elif ev == "prefs" and not e.get("snap_meta"):
            for kv in e.get("set") or []:
                k, _, v = kv.partition("=")
                st["prefs"][k] = _pref_value(v)
        elif ev == "plan-approved":
            st["plan_approved"] = True
        elif ev == "plan-reopened":
            st["plan_approved"] = False
        th = e.get("thread")
        if th and (ev.startswith("thread") or ev == "worktree"):
            t = st["threads"].setdefault(th, _blank_thread(st, th, e.get("kind") or "rc"))
            if ev == "thread-add":
                t["created"] = e.get("t")
                if e.get("session"):
                    t["session_id"] = e["session"]
                    t["status"] = "idle"
            if e.get("status"):
                t["status"] = e["status"]
            if e.get("summary") is not None and ev == "thread-set":
                t["summary"] = e["summary"]
            if ev == "worktree":
                t["worktree"] = e.get("path")
        if e.get("snap_thread") and th:
            st["threads"][th] = e["snap_thread"]
        if isinstance(e.get("snap_threads"), dict):
            st["threads"] = e["snap_threads"]
        if isinstance(e.get("snap_more_threads"), dict):  # other seats the event changed (stints closed)
            st["threads"].update(e["snap_more_threads"])
        tk = e.get("task")
        if tk and ev.startswith("task"):
            k = st["tasks"].setdefault(tk, {"title": tk, "owner": None, "deps": [], "accept": "", "status": "planned",
                                            "notes": "", "created": e.get("t")})
            if e.get("owner") is not None:
                k["owner"] = e["owner"]
            if e.get("status"):
                k["status"] = e["status"]
        if e.get("snap_task") and tk:
            st["tasks"][tk] = e["snap_task"]
        if e.get("snap_tasks"):
            st["tasks"] = e["snap_tasks"]
        if isinstance(e.get("snap_more_tasks"), dict):  # tasks an event added besides its own (follow-ups)
            st["tasks"].update(e["snap_more_tasks"])
        if e.get("snap_roster_version") is not None:
            st["roster_version"] = e["snap_roster_version"]
        if e.get("snap_model_deny") is not None:
            st["model_deny"] = e["snap_model_deny"]
        r = e.get("req")
        if r and ev.startswith("req"):
            q = st["requests"].setdefault(r, {"text": e.get("text") or r, "status": "open", "tasks": [],
                                              "where": "", "followup": "", "created": e.get("t")})
            if e.get("tasks"):
                q["tasks"] = e["tasks"]
            if e.get("status"):
                q["status"] = e["status"]
        if e.get("snap_req") and r:
            st["requests"][r] = e["snap_req"]
        if e.get("snap_reqs"):
            st["requests"].update(e["snap_reqs"])
        elif ev == "req-import":
            missing = [x for x in e.get("ids", []) if x not in st["requests"]]
            if missing:
                notes.append(f"req-import of {', '.join(missing)} has no content in the log (old format): "
                             "re-import the source file after rebuild")
        if e.get("snap_questions") is not None:
            st["questions"] = e["snap_questions"]
        elif ev == "question":
            st["questions"].append({"from": e.get("from"), "text": e.get("text"), "answered": False})
        elif ev == "answered":
            i = e["n"] - 1 if isinstance(e.get("n"), int) else e.get("q")  # n: 1-based; q: old 0-based events
            if isinstance(i, int) and 0 <= i < len(st["questions"]):
                st["questions"][i]["answered"] = True
        if e.get("snap_plan_approved") is not None:
            st["plan_approved"] = e["snap_plan_approved"]
        for k in ("issues", "deliverables", "qa_pending", "decisions", "promises", "features", "docs", "standing", "last_report_at"):
            if e.get(f"snap_{k}") is not None:
                st[k] = e[f"snap_{k}"]
        if e.get("snap_reconcile") is not None:
            st["reconcile"] = e["snap_reconcile"]
        return st


def _finish_rebuild(d, st, project, sessions, notes, short=None, earlier_shorts=(), leads=()):
    if st is None:
        if not project:
            sys.exit("log.jsonl has no project name: pass --project <name>")
        st = _blank_state(project, d.parent)
        st.setdefault("prefs", {})["short_name"] = short or default_short(project)
        notes.append("no usable log: rebuilt from sessions and branches only")
    # sessions from list_sessions (lead passes the JSON): tags teamwork:<short|project>, teamwork-thread:<name>.
    # Only names this project used: the default short name (initials) is used only when no short name is known
    # (then it is tag(st)), since another project can have the same initials.
    labels = {f"teamwork:{st['project']}", f"teamwork:{tag(st)}"}
    labels |= {f"teamwork:{x}" for x in ([short] if short else []) + list(earlier_shorts)}
    # a thread is a session this project's lead created: one whose parent is some other session is not ours
    leads = {x for x in set(leads) | {(st.get("lead") or {}).get("session_id")} if x}
    ini = default_short(st["project"])
    initials_only = {f"teamwork:{ini}"} if ini != st["project"] and short != ini else set()
    known_sids = {x for t in st["threads"].values() for x in [t.get("session_id")] +
                  [h.get("session_id") for h in t.get("sessions") or []] if x}
    other = {x for sess in sessions or [] for x in (sess.get("tags") or []) if isinstance(x, str)
             and x.startswith("teamwork:")} - labels
    if other and not any(set(sess.get("tags") or []) & labels for sess in sessions or []):
        notes.append(f"no listed session carries {', '.join(sorted(labels))}; other project tags seen: "
                     f"{', '.join(sorted(other)[:5])} (pass --short <name> if one is this project)")
    found = {}
    roster_changed = False
    for sess in sessions or []:
        tags = set(sess.get("tags") or [])
        if not tags & labels or sess.get("archived") or sess.get("status") == "archived" \
                or "archived" in str(sess.get("session_status") or "").lower():
            continue
        names = sorted({x.split(":", 1)[1] for x in tags if x.startswith("teamwork-thread:")})
        parent = sess.get("parent_session_id")
        sid0 = sess.get("id") or sess.get("session_id")
        if leads and isinstance(parent, str) and parent and parent not in leads:
            notes.append(f"session {sid0} carries this project's tag but was created by a session that never led "
                         "it; ignored")
            continue
        # matched only by the default initials, which another project can share: take it only with a second sign
        # that it is ours (created by one of our leads, or a session id the log already knows)
        if tags & labels <= initials_only and not (parent in leads or sid0 in known_sids):
            if leads:  # we know who led this project, so its threads name a lead of ours as their parent
                notes.append(f"session {sid0} matches only the initials tag {', '.join(sorted(initials_only))}, "
                             "which another project may share, and nothing else ties it here; ignored (pass "
                             "--short <name>, or reseat the thread by hand, if it is ours)")
                continue
            notes.append(f"session {sid0} taken on the initials tag alone: check it belongs to this project")
        if len(names) > 1:
            notes.append(f"session {sess.get('id') or sess.get('session_id')} has several thread tags "
                         f"({', '.join(n[:40] for n in names)}); ignored")
            continue
        name = names[0] if names else None
        if name and not re.fullmatch(NAME_RE, name):
            notes.append(f"ignored session tag with an invalid thread name: {name[:40]!r}")
            continue
        if name:
            found.setdefault(name, []).append(sess)
    for name, ss in found.items():
        ss.sort(key=lambda x: x.get("created_at") or "")
        sid_of = lambda x: x.get("id") or x.get("session_id")
        prior = st["threads"].get(name) or {}
        held, phist = prior.get("session_id"), prior.get("sessions") or []
        lead_sid = (st.get("lead") or {}).get("session_id")
        # never a session the lead or another live seat holds; prefer the current holder, then a session whose
        # stint has not ended, then the newest
        free = [x for x in ss if sid_of(x) != lead_sid and not _session_taken(st, sid_of(x), name)]
        ended = {h.get("session_id") for h in phist if h.get("to")} - \
            {h.get("session_id") for h in phist if not h.get("to")}
        if not free:
            notes.append(f"{name}: every listed session is held by the lead or another seat; seat left as it was")
            continue
        cur = next((x for x in free if sid_of(x) == held), None) or \
            next((x for x in reversed(free) if sid_of(x) not in ended), None)
        if cur is None:  # every listed session was retired from this seat (reseat): never bring one back
            notes.append(f"{name}: every listed session already left this seat; seat left as it was "
                         f"(reseat {name} --session <id> if one should take it again)")
            continue
        kind = "cloud" if cur.get("environment_kind") == "anthropic_cloud" else "rc"
        t = st["threads"].setdefault(name, _blank_thread(st, name, kind))
        hist = t.setdefault("sessions", [])
        holder_from = next((h.get("from") for h in reversed(hist)
                            if h.get("session_id") == sid_of(cur) and not h.get("to")), None) or cur.get("created_at")
        for x in ss:
            sid = sid_of(x)
            if not sid or sid == sid_of(cur) or any(h.get("session_id") == sid for h in hist):
                continue
            if sid == lead_sid or _session_taken(st, sid, name):
                continue  # held by the lead or another seat now: never a past stint of this one
            if holder_from and x.get("created_at") and not _after(holder_from, x.get("created_at")):
                notes.append(f"{name}: session {sid} is newer than the holder and was never seated; not added "
                             f"(reseat {name} --session {sid} if it should take over)")
                continue
            # an older listed session of this seat: an ended stint, never a second open one
            hist.append({"session_id": sid, "from": x.get("created_at"), "to": holder_from or now(),
                         "reason": "earlier session (session list)"})
        sid = cur.get("id") or cur.get("session_id")
        if sid and t.get("session_id") != sid:
            for h in hist:
                if h.get("session_id") != sid and not h.get("to"):
                    end = cur.get("created_at") or now()
                    h["to"] = end if not h.get("from") or _after(end, h["from"]) else now()  # never before from
                    h["reason"] = "replaced (session list)"
            notes.append(f"{name}: session set to {sid} from the session list")
            t["session_id"] = sid
            _close_archived(st, sid, name, cur.get("created_at") or now())
            st.pop("_touched", None)
            if not any(h.get("session_id") == sid and not h.get("to") for h in hist):
                hist.append({"session_id": sid, "from": cur.get("created_at") or now()})  # back to an earlier one
            if t["status"] == "planned":
                t["status"] = "idle"
            roster_changed = True
    # branches tw/<project>/<thread>, local or origin
    repo = st.get("repo", {}).get("path") or str(d.parent)
    r = subprocess.run(["git", "-C", repo, "for-each-ref", "--format=%(refname:short)",
                        f"refs/heads/tw/{st['project']}/", f"refs/remotes/origin/tw/{st['project']}/"],
                       capture_output=True, text=True)
    for ref in (r.stdout.split() if r.returncode == 0 else []):
        rest = ref.split(f"tw/{st['project']}/", 1)[-1]
        if "/" in rest or not re.fullmatch(NAME_RE, rest):
            notes.append(f"ignored branch {ref}: not tw/{st['project']}/<thread>")
            continue
        name = rest
        if name not in st["threads"]:
            st["threads"][name] = _blank_thread(st, name)
            notes.append(f"{name}: thread restored from branch {ref} (kind, owns, focus unknown)")
            roster_changed = True
    for name, t in st["threads"].items():
        for k, v in _blank_thread(st, name, t.get("kind", "rc")).items():
            t.setdefault(k, v)
        t["title"] = title_for(st, name)
    if roster_changed:
        bump_roster(st)  # session ids or threads changed: every thread needs a new ROSTER
        notes.append("the roster changed: broadcast ROSTER to every live thread")
    return st, notes


def _session_page(x, depth=0):
    """The page dict inside a list_sessions result ({"ccr": {"data": [...], "has_more": ...}}, or bare), or None."""
    if not isinstance(x, dict) or depth > 4:
        return None
    if isinstance(x.get("data"), list) or isinstance(x.get("sessions"), list):
        return x
    return next((pg for v in x.values() for pg in [_session_page(v, depth + 1)] if pg), None)


def _load_sessions(path):
    """Rows from a saved list_sessions result: one result, several pages (a JSON list of results, or results
    written one after another), or a plain list of rows. Refused if the last page says more rows exist."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        sys.exit(f"cannot read --sessions {path}: {e}")
    docs, dec, i = [], json.JSONDecoder(), 0
    while i < len(text):
        while i < len(text) and text[i] in " \t\r\n,":
            i += 1
        if i >= len(text):
            break
        try:
            doc, i = dec.raw_decode(text, i)
        except ValueError as e:
            sys.exit(f"cannot read --sessions {path}: {e}")
        docs.append(doc)
    pages, rows = [], []
    for doc in docs:
        for item in (doc if isinstance(doc, list) else [doc]):
            pg = _session_page(item)
            if pg is not None:
                pages.append(pg)
                rows += [x for x in (pg.get("data") if isinstance(pg.get("data"), list) else pg.get("sessions"))
                         if isinstance(x, dict)]
            elif isinstance(item, dict):
                rows.append(item)  # a plain row
    if pages and pages[-1].get("has_more"):
        sys.exit("--sessions: the last page has has_more true; page through list_sessions to the end and save "
                 "every page")
    seen, out = set(), []
    for x in rows:
        k = x.get("id") or x.get("session_id")
        if k and k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


def cmd_rebuild(a):
    d = Path(os.environ.get("TEAMWORK_DIR") or (Path.cwd() / ".teamwork")).resolve()
    if not d.is_dir():
        sys.exit(f"{d} does not exist")
    sessions = _load_sessions(a.sessions) if a.sessions else []
    st, notes = rebuild_state(d, a.project, sessions, a.short)
    if a.sessions and not any(isinstance(x, str) and x.startswith("teamwork:")
                              for sess in sessions for x in (sess.get("tags") or []) if isinstance(sess.get("tags"), list)):
        notes.append(f"--sessions {a.sessions}: {len(sessions)} session(s), none with a teamwork tag; nothing taken "
                     "from it (save the whole list_sessions result)")
    st["updated"] = now()
    preview = d / "state.json.rebuilt"
    preview.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"preview": str(preview), "threads": sorted(st["threads"]), "tasks": sorted(st["tasks"]),
               "requests": sorted(st["requests"]), "notes": notes}
    cur = d / "state.json"
    old = None
    if cur.exists():
        try:
            old = json.loads(cur.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = None
        if not isinstance(old, dict) or not all(isinstance(old.get(k, {}), dict) and all(
                isinstance(v, dict) for v in old.get(k, {}).values()) for k in ("threads", "tasks", "requests")):
            old = None
            notes.append("the current state.json is unreadable: no diff shown; --apply keeps a copy of it")
    if old is not None:
        summary["vs_current"] = {}
        for kind in ("threads", "tasks", "requests"):
            o, n = old.get(kind, {}), st[kind]
            changed = {i: sorted(k for k in set(o[i]) | set(n[i]) if o[i].get(k) != n[i].get(k)
                                 and k not in ("last_seen", "updated"))
                       for i in set(o) & set(n)}
            summary["vs_current"][kind] = {"missing": sorted(set(o) - set(n)), "extra": sorted(set(n) - set(o)),
                                           "changed": {i: f for i, f in sorted(changed.items()) if f}}
        summary["vs_current"]["other"] = sorted(k for k in ("lead", "prefs", "questions", "plan_approved", "envs",
                                                             "repo", "goal", "issues", "deliverables",
                                                             "qa_pending", "features", "promises", "decisions",
                                                             "docs", "standing", "reconcile", "last_report_at")
                                                             if (old.get(k) or None) != (st.get(k) or None))  # [] = missing
        lost = sum(len(old.get(k, {})) for k in ("threads", "tasks", "requests"))
        kept = sum(len(st[k]) for k in ("threads", "tasks", "requests"))
        if a.apply and lost and kept < lost / 2 and not a.force:
            sys.exit(json.dumps({**summary, "refused": "the rebuild has far less than the current state; check the "
                                 "notes, then re-run with --apply --force if you are sure"}, ensure_ascii=False, indent=2))
    if a.apply:
        if cur.exists():
            stamp = now()[:19].replace(':', '')
            bak, i = d / f"state.json.bak-{stamp}", 1
            while bak.exists():  # never overwrite an earlier backup
                i += 1
                bak = d / f"state.json.bak-{stamp}-{i}"
            bak.write_bytes(cur.read_bytes())
            summary["backup"] = str(bak)
        preview.replace(cur)
        (d / "BOARD.md").write_text(render_board(st), encoding="utf-8")
        log(d, {"ev": "rebuild", "notes": notes})
        summary["applied"] = True
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def find_projects(root):
    root = Path(root).expanduser().resolve()
    hits = set()
    for pat in (".teamwork/state.json", "*/.teamwork/state.json", "*/*/.teamwork/state.json"):
        hits |= {q.parent.parent for q in root.glob(pat)}
    return sorted(hits)


def cmd_projects(a):
    """Portfolio view: every teamwork project under a folder, one line each."""
    rows = []
    for f in find_projects(a.root):
        try:
            st = json.loads((f / ".teamwork" / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        th = [t for t in st.get("threads", {}).values()]
        count = lambda *ss: sum(1 for t in th if t["status"] in ss)
        tasks = list(st.get("tasks", {}).values())
        rows.append({
            "project": st["project"], "short_name": tag(st), "folder": str(f), "lead": st.get("lead", {}).get("session_id"),
            "waiting": count("waiting"), "review": count("review"), "working": count("starting", "working"),
            "threads": sum(1 for t in th if t["status"] != "archived"),
            "tasks_done": f"{sum(1 for k in tasks if k['status'] == 'done')}/{len(tasks)}",
            "questions": sum(1 for q in st.get("questions", []) if not q.get("answered")),
            "stuck": len(stuck_threads(st)),
            "updated": st.get("updated"),
        })
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2)); return
    if not rows:
        print("(no teamwork projects under this folder)"); return
    print("| project | lead | threads | waiting on you | ready for review | working | tasks done | questions | updated |")
    print("|---|---|---|---|---|---|---|---|---|")
    for x in rows:
        print(f"| {x['project']} | {x['lead'] or '-'} | {x['threads']} | {x['waiting']} | {x['review']} | {x['working']} | "
              f"{x['tasks_done']} | {x['questions']} | {(x['updated'] or '-')[:16]} |")


def thread_reminder(st, me):
    """IL-18: after a compaction the SessionStart hook runs again; a thread gets its header and rules back first."""
    if not me or me not in st.get("threads", {}) or st["threads"][me].get("status") in ("archived", "resolved"):
        return ""
    lead = (st.get("lead") or {}).get("session_id") or "(see roster)"
    return (f"You are thread {me} of this project (lead session {lead}). If your context was just summarized, re-read "
            "your thread protocol's Language, Messages and STATUS sections before your next message. Every message "
            f"starts with `[tw:{tag(st)}] <TYPE> {me} -> <recipient> <task-id or ->`, and every turn ends with the "
            "tw-status block.\n\n")


def lead_reminder(st):
    sid = (st.get("lead") or {}).get("session_id")
    if not sid:
        return ""
    return (f"If you are this project's lead (session {sid}): if your context was just summarized, re-read SKILL.md's "
            f"Rules (language, messages, approvals) before your next message. Messages to threads start with "
            f"`[tw:{tag(st)}] <TYPE> lead -> <thread> <task-id or ->`.\n\n")


def running_version():
    skill = Path(__file__).resolve().parents[1]
    release = skill / "RELEASE.json"
    if release.exists():
        r = json.loads(release.read_text(encoding="utf-8"))
        return f"release {r['name']} ({r['sha'][:7]})", {"type": "release", "name": r["name"], "sha": r["sha"]}
    if (skill / ".git").exists():
        try:
            sha = subprocess.run(["git", "-C", str(skill), "rev-parse", "HEAD"], capture_output=True,
                                 text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            sha = ""
        return f"edge {sha[:7] or '?'} (live clone)", {"type": "edge", "sha": sha}
    return "copy (unknown version)", {"type": "copy"}


def _vkey(label):
    """What makes two sessions' rules differ: the release name; every live-clone (edge) session counts as one."""
    label = str(label or "")
    return "edge" if label.startswith("edge ") else label.split(" (")[0]


def cmd_version(a):
    label, info = running_version()
    newer = newer_release(info)
    if not a.sessions and not a.json:
        print(label)
        if newer:
            print(f"newer release {newer} available (rollback in effect?)")
        return
    result = {"running": {**info, "version": label}, "newer": newer}
    if a.sessions:
        d = find_dir()
        latest = {}
        if d and (d / "session-versions.jsonl").exists():
            for line in (d / "session-versions.jsonl").read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                    latest[row["session"]] = row
                except (ValueError, KeyError):
                    continue
        st = load(d) if d and (d / "state.json").exists() else {}
        old = []
        for name, thread in (st.get("threads") or {}).items():
            sid = thread.get("session_id") if isinstance(thread, dict) else None
            if sid in latest and _vkey(latest[sid].get("version")) != _vkey(label):
                old.append({"thread": name, "session": sid, "version": latest[sid].get("version")})
        sid = (st.get("lead") or {}).get("session_id")
        if sid in latest and _vkey(latest[sid].get("version")) != _vkey(label):
            old.append({"thread": "lead", "session": sid, "version": latest[sid].get("version")})
        result["old_sessions"] = old
    print(json.dumps(result, ensure_ascii=False) if a.json or a.sessions else label)


def cmd_context(a):
    """SessionStart hook. Prints nothing outside a teamwork folder."""
    d = find_dir()
    me, in_worktree = None, False
    if d is None:  # an rc thread's worktree, <project>-worktrees/<thread>: the board lives in the project folder
        cwd = Path.cwd().resolve()
        for p in [cwd, *cwd.parents]:
            if p.parent.name.endswith("-worktrees"):
                proj = p.parent.parent / p.parent.name[:-len("-worktrees")]
                if (proj / ".teamwork" / "state.json").exists():
                    try:
                        st0 = load(proj / ".teamwork")
                        seats = st0.get("threads") or {}
                    except Exception:
                        return  # a broken board: say nothing rather than fail the thread's hook on every start
                    d, in_worktree = proj / ".teamwork", True
                    by_path = []  # the deepest recorded worktree holding cwd, active seats first
                    for n, t in seats.items():
                        w = t.get("worktree") if isinstance(t, dict) else None
                        if isinstance(w, str) and w:
                            wp = Path(w).resolve()
                            if wp == cwd or wp in cwd.parents:
                                by_path.append((t.get("status") not in ("archived", "resolved"), len(wp.parts), n))
                    me = max(by_path)[2] if by_path else p.name
                    break  # else keep walking: a nested `x-worktrees` folder is not the project's
    if d is None or not (d / "state.json").exists():
        # Served folder of a Remote Control environment: list projects below it, nothing more.
        cwd = Path.cwd()
        found = sorted({p.parent.parent for pat in ("*/.teamwork/state.json", "*/*/.teamwork/state.json")
                        for p in cwd.glob(pat)})
        if found:
            names = ", ".join(f"{json.loads((f / '.teamwork' / 'state.json').read_text(encoding='utf-8'))['project']} ({f})"
                              for f in found)
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext":
                  f"teamwork projects below this folder: {names}. Only use one if the user asks; "
                  "cd into its folder first (its own board then applies)."}}, ensure_ascii=False))
        return
    st = load(d)
    label, info = running_version()
    newer = newer_release(info)
    newer_note = ""
    try:
        rels = json.loads((Path(__file__).resolve().parents[1].parent / "index.json").read_text(encoding="utf-8"))["releases"]
        if newer:
            newer_note = next((r.get("note", "") for r in rels if r["name"] == newer), "")
    except (OSError, ValueError, KeyError):
        pass
    try:  # the hook's JSON on stdin names the session; wait briefly, a stdin left open must not hang the hook
        import select
        import time
        event, buf, end = None, b"", time.monotonic() + 0.5
        fd = sys.stdin.fileno()
        while not sys.stdin.isatty() and time.monotonic() < end and select.select([fd], [], [], max(0, end - time.monotonic()))[0]:
            chunk = os.read(fd, 65536)  # what is there now; never wait for end of input
            if not chunk:
                break
            buf += chunk
            try:
                event = json.loads(buf.decode("utf-8"))
                break
            except ValueError:
                continue
        sid = event.get("session_id") if isinstance(event, dict) else None
        if sid:  # H9: which version each session started on
            with (d / "session-versions.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"session": sid, "version": label, "at": os.environ.get("TW_NOW") or now(),
                                    "cwd": str(Path.cwd())}) + "\n")
    except Exception:
        pass
    pw = permission_warning(st) if not in_worktree else ""
    reminder = thread_reminder(st, me) if in_worktree else lead_reminder(st)
    text = "This folder is a teamwork project (skill: /teamwork). Board below; source of truth is .teamwork/state.json, " "changed only by the lead through tw.py.\n\nteamwork version: " + label + (f"\nnewer release {newer} available (rollback in effect?)" + (f": {newer_note}" if newer_note else "") if newer else "") + (f"\nThis project tests unreleased teamwork: read {st['prefs']['skill_edge']}/SKILL.md and run {st['prefs']['skill_edge']}/scripts/tw.py instead of the installed copy (staged rollout; the rest of the computer stays on the release)." if st.get("prefs", {}).get("skill_edge") else "") + "\n\n" + (f"Lead: permissions: {pw}\n\n" if pw else "") + reminder + language_rule(st) + "\n\n" + (unreported_note(st) if not in_worktree else
    "The board below is the lead's view: its requests, questions and commands are the lead's to act on, not yours. "
    "Read it for the roster, your tasks and the known issues.\n\n") + render_board(
        st
    ) + "\n\nProject instructions (.teamwork/INSTRUCTIONS.md):\n" + read_file(
        d, "INSTRUCTIONS.md"
    ) + "\n\nProject memory (.teamwork/MEMORY.md):\n" + read_file(
        d, "MEMORY.md"
    )
    if len(text) > CONTEXT_LIMIT:
        text = (
            text[:CONTEXT_LIMIT]
            + "\n...(truncated: read .teamwork/BOARD.md, INSTRUCTIONS.md, MEMORY.md)"
        )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": text,
                }
            },
            ensure_ascii=False,
        )
    )


def _base_holder(git, base):
    """The checkout that has `base` checked out (or is rebasing/bisecting it), and that checkout's HEAD; (None, None)
    when nobody holds it."""
    for blk in git("worktree", "list", "--porcelain").stdout.split("\n\n"):
        lines = dict((ln.split(" ", 1) + [""])[:2] for ln in blk.splitlines() if ln.strip())
        if not lines.get("worktree"):
            continue
        if "prunable" in lines:  # its folder is gone: nobody works there (merge prunes it before the checks)
            continue
        w = Path(lines["worktree"])
        if lines.get("branch") == f"refs/heads/{base}":
            return w, lines.get("HEAD", "")
        gd = git("rev-parse", "--absolute-git-dir", cwd=w).stdout.strip()
        if not gd:
            continue  # not readable: never fall back to a path relative to the current folder
        for f in ("rebase-merge/head-name", "rebase-apply/head-name", "BISECT_START"):
            try:
                if Path(gd, f).read_text().strip() in (f"refs/heads/{base}", base):
                    return w, lines.get("HEAD", "")
            except OSError:
                pass
    return None, None


def _in_the_way(git, checkout, old, new):
    """Local untracked or ignored files the fast-forward old -> new would overwrite or delete in checkout: the same
    path, a local file where the merge adds a folder (`cfg` vs `cfg/a`), or local files inside a path the merge
    turns into a file (`bld/x` vs `bld`). Ignored files count: a local .env must not vanish silently."""
    root = git("rev-parse", "--show-toplevel", cwd=checkout).stdout.strip()
    if not root:
        return []
    z = lambda r: {x for x in r.stdout.split("\0") if x}  # -z: paths as they are, not quoted (non-ASCII names)
    changed = z(git("diff", "-z", "--name-only", "--no-renames", old, new))
    dirs = {"/".join(c.split("/")[:i]) for c in changed for i in range(1, c.count("/") + 1)}
    local = z(git("ls-files", "-z", "--others", "--exclude-standard", cwd=root))
    local |= z(git("ls-files", "-z", "--others", "--ignored", "--exclude-standard", cwd=root))
    hit = set()
    for u in local:
        up = u.rstrip("/").split("/")
        if u in changed or u.rstrip("/") in dirs or any("/".join(up[:i]) in changed for i in range(1, len(up))):
            hit.add(u)
    return sorted(hit)


def cmd_merge(a):
    """Rehearse a merge of the reviewed commit into the base in a throwaway worktree, run the checks there, and only
    with --apply fast-forward the base. The live checkout is never left half-merged: a conflict, a red check, or a
    checkout that changed meanwhile changes nothing."""
    import shutil
    import signal
    import tempfile
    import time
    d = need(find_dir())
    st = load(d)
    proj = Path(st.get("repo", {}).get("path") or ".")
    base = st.get("repo", {}).get("base") or "main"
    git = lambda *x, cwd=proj: subprocess.run(["git", "-C", str(cwd), *x], capture_output=True, text=True)
    top = git("rev-parse", "--show-toplevel")
    if top.returncode != 0:
        sys.exit(f"{proj} is not a git repository")
    sub = git("rev-parse", "--show-prefix").stdout.strip()  # the project may be a subfolder of the repo
    if a.no_test is not None:
        test = ""  # said out loud: nothing to check, even if check_cmd is set
        if not a.no_test.strip():
            sys.exit("--no-test needs the reason (\"docs only\")")
    else:
        test = str(a.test if a.test is not None else st.get("prefs", {}).get("check_cmd") or "")
    if not test.strip() and a.no_test is None:
        sys.exit("no check to run: pass --test \"<command>\", set prefs check_cmd=\"<command>\", or --no-test "
                 "\"<why>\" for a change with nothing to test")
    if a.squash and not (a.message or "").strip():
        sys.exit("--squash needs --message \"T3: <summary> (squash of <branch>)\": keep the task id first so trace "
                 "and leases find it")
    old = git("rev-parse", "--verify", "--quiet", f"refs/heads/{base}^{{commit}}").stdout.strip()
    tip = git("rev-parse", "--verify", "--quiet", f"{a.branch}^{{commit}}").stdout.strip()
    given = (a.sha or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{7,40}", given):
        sys.exit(f"--sha {a.sha!r}: give the reviewed commit id (7-40 hex digits from the owner's DONE), not a branch, "
                 "tag or revision name")
    sha = git("rev-parse", "--verify", "--quiet", f"{given}^{{commit}}").stdout.strip()
    if sha and not sha.startswith(given):  # a ref that happens to look like hex is not the reviewed commit
        sha = ""
    if not old:
        sys.exit(f"base branch {base} not found")
    if not tip:
        sys.exit(f"branch {a.branch} not found (git fetch first for a cloud branch)")
    if not sha:
        sys.exit(f"--sha {given}: no such commit, or an ambiguous short id (use the full commit id the owner's DONE "
                 "reported as reviewed)")
    res = {"branch": a.branch, "sha": sha, "base": base, "base_was": old, "tip": tip, "applied": False}
    if sha != tip:
        res["extra_commits"] = git("log", "--oneline", "--no-decorate", f"{sha}..{tip}").stdout.splitlines()
        res["missing_commits"] = git("log", "--oneline", "--no-decorate", f"{tip}..{sha}").stdout.splitlines()
        print(json.dumps(res, ensure_ascii=False))
        what = (f"{len(res['extra_commits'])} commit(s) after the reviewed one" if res["extra_commits"] else "")
        if res["missing_commits"]:
            what = (what + "; " if what else "") + f"the reviewed commit is not on {a.branch}"
        sys.exit(f"{a.branch} is at {tip[:9]}, not the reviewed {sha[:9]} ({what}): nothing changed. Merge only what "
                 "was reviewed: ask the owner for a review of the new commits, or for the right SHA")
    if git("merge-base", "--is-ancestor", sha, old).returncode == 0:
        res["note"] = f"{sha[:9]} is already in {base}: nothing to merge"
        print(json.dumps(res, ensure_ascii=False))
        return
    holder, _ = _base_holder(git, base)
    if a.apply and holder and git("status", "--porcelain", "--untracked-files=no", cwd=holder).stdout.strip():
        sys.exit(f"{holder} has uncommitted changes on {base}: commit or move them first")
    pre = git("merge-tree", "--write-tree", "--name-only", "--no-messages", old, sha)
    if pre.returncode == 1:  # conflict found without touching any checkout (git >= 2.38; older git: skip)
        res["conflicts"] = [f for f in pre.stdout.splitlines()[1:] if f.strip()]
        print(json.dumps(res, ensure_ascii=False))
        sys.exit(f"merge of {a.branch} into {base} conflicts: nothing changed; send the owner a FOLLOWUP to rebase")
    for blk in git("worktree", "list", "--porcelain").stdout.split("\n\n"):  # leftovers of runs killed with -9
        w = dict((ln.split(" ", 1) + [""])[:2] for ln in blk.splitlines() if ln.strip()).get("worktree", "")
        if Path(w).name == "wt" and Path(w).parent.name.startswith("tw-merge-"):
            try:
                os.kill(int((Path(w).parent / "pid").read_text()), 0)
                continue  # another merge is running
            except PermissionError:
                continue  # alive, run by another user: theirs to clean up
            except (OSError, ValueError):
                pidfile = Path(w).parent / "pid"
                try:  # no pid file (an older tw.py, or one killed before writing it): only once it is an hour old
                    if not pidfile.exists() and time.time() - Path(w).parent.stat().st_mtime < 3600:
                        continue
                except OSError:
                    pass
                git("worktree", "remove", "--force", w)
                shutil.rmtree(Path(w).parent, ignore_errors=True)
    git("worktree", "prune")
    tmp = Path(tempfile.mkdtemp(prefix="tw-merge-"))
    (tmp / "pid").write_text(str(os.getpid()))
    wt = tmp / "wt"
    final = {"past": False}  # past the point of no return: a signal must not claim "nothing changed"

    def stop(signum, _frame):  # a killed run still cleans up its worktree (finally below)
        raise SystemExit(f"stopped by signal {signum}: nothing changed")
    sigs = [signal.SIGTERM, signal.SIGINT] + ([signal.SIGHUP] if hasattr(signal, "SIGHUP") else [])
    saved = {s_: signal.signal(s_, stop) for s_ in sigs}
    can_block = hasattr(signal, "pthread_sigmask")
    try:
        r = git("worktree", "add", "--detach", str(wt), old)
        if r.returncode != 0:
            sys.exit(f"git worktree add failed: {r.stderr.strip()}")
        if a.squash:
            r = git("merge", "--squash", sha, cwd=wt)
            if r.returncode == 0 and git("diff", "--cached", "--quiet", cwd=wt).returncode == 0:
                res["note"] = f"the changes of {sha[:9]} are already in {base}: nothing to merge"
                print(json.dumps(res, ensure_ascii=False))
                return
            if r.returncode == 0:
                r = git("commit", "-q", "-m", a.message.strip(), cwd=wt)
        else:
            r = git("merge", "--no-ff", "-q", "-m", (a.message or "").strip() or f"Merge {a.branch} at {sha[:9]}",
                    sha, cwd=wt)
        if r.returncode != 0:
            res["conflicts"] = [f for f in git("diff", "--name-only", "--diff-filter=U", cwd=wt).stdout.splitlines()]
            print(json.dumps(res, ensure_ascii=False))
            sys.exit(f"merge of {a.branch} into {base} failed in the rehearsal: {(r.stderr or r.stdout).strip()[:300]}"
                     "; nothing changed")
        new = git("rev-parse", "HEAD", cwd=wt).stdout.strip()
        res["merged"] = new
        if a.apply and holder:  # untracked files the fast-forward would overwrite: say so before the checks run
            in_way = _in_the_way(git, holder, old, new)
            if in_way:
                res["untracked_in_the_way"] = in_way
                print(json.dumps(res, ensure_ascii=False))
                sys.exit(f"untracked files in {holder} would be overwritten by the merge: move them, then run "
                         "merge again; nothing changed")
        if test.strip():
            env = {k: v for k, v in os.environ.items() if k != "TEAMWORK_DIR"}  # checks never touch the live ledger
            try:
                limit = float(st.get("prefs", {}).get("check_timeout_minutes", 60) or 0)
            except (TypeError, ValueError):
                limit = 60.0  # a hand-edited ledger: the default, never no limit by accident
            if limit != limit or limit in (float("inf"), float("-inf")) or limit < 0:
                limit = 60.0  # nan, inf, negative
            pr = subprocess.Popen(test, shell=True, cwd=str(wt / sub), stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                  stderr=subprocess.STDOUT, text=True, env=env, start_new_session=True)
            try:
                out, _ = pr.communicate(timeout=limit * 60 if limit > 0 else None)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(pr.pid, signal.SIGKILL) if hasattr(os, "killpg") else pr.kill()
                except OSError:
                    pass
                try:
                    out, _ = pr.communicate(timeout=10)
                except subprocess.TimeoutExpired:  # a detached grandchild still holds the output pipe
                    out = "(output not read: a process the check detached still holds it)"
                    pr.kill()
                    pr.wait()
                    if pr.stdout:
                        pr.stdout.close()
                res["checks"] = "fail"
                res["check_output"] = (out or "")[-2000:]
                print(json.dumps(res, ensure_ascii=False))
                sys.exit(f"checks ran over {limit:g} minutes on the rehearsed merge ({test}) and were stopped: nothing "
                         "changed (prefs check_timeout_minutes sets the limit; 0 = none)")
            except BaseException:  # stopped: take the whole check down with us, grandchildren included
                try:
                    os.killpg(pr.pid, signal.SIGKILL) if hasattr(os, "killpg") else pr.kill()
                except OSError:
                    pass
                pr.wait()
                raise
            res["checks"] = "pass" if pr.returncode == 0 else "fail"
            if pr.returncode != 0:
                res["check_output"] = (out or "")[-2000:]
                print(json.dumps(res, ensure_ascii=False))
                sys.exit(f"checks failed on the rehearsed merge ({test}): nothing changed")
        else:
            res["checks"] = f"none ({a.no_test})"
        if not a.apply:
            res["note"] = f"rehearsal only: rerun with --apply to fast-forward {base} to the merged commit"
            print(json.dumps(res, ensure_ascii=False))
            return
        # The checks took a while: the checkouts may have changed. Fast-forward only what is still as we left it.
        # From here to the log line a signal waits: a fast-forward is never cut in half, nor reported as not done.
        if can_block:
            signal.pthread_sigmask(signal.SIG_BLOCK, sigs)
        else:
            for s_ in sigs:
                signal.signal(s_, signal.SIG_IGN)
        final["past"] = True
        holder_now, now_at = _base_holder(git, base)
        cur = git("rev-parse", "--verify", "--quiet", f"refs/heads/{base}^{{commit}}").stdout.strip()
        moved = None
        if cur != old:
            moved = f"{base} moved to {cur[:9]} meanwhile"
        elif (holder_now and str(holder_now) != str(holder)) or (holder and not holder_now):
            moved = f"the checkout that has {base} changed meanwhile ({holder or '-'} -> {holder_now or '-'})"
        elif holder_now:
            head = git("symbolic-ref", "-q", "HEAD", cwd=holder_now).stdout.strip()
            if head != f"refs/heads/{base}" or git("rev-parse", "HEAD", cwd=holder_now).stdout.strip() != old:
                moved = f"{holder_now} is no longer on {base} at {old[:9]} (rebase or checkout in progress?)"
            elif git("status", "--porcelain", "--untracked-files=no", cwd=holder_now).stdout.strip():
                moved = f"{holder_now} got uncommitted changes on {base} meanwhile"
            elif (late := _in_the_way(git, holder_now, old, new)):
                res["untracked_in_the_way"] = late
                moved = f"untracked or ignored files in {holder_now} that the merge would overwrite appeared meanwhile"
        if moved:
            res["refused"] = moved
            print(json.dumps(res, ensure_ascii=False))
            sys.exit(f"{moved}: nothing changed; run merge again")
        if holder_now:  # a checkout has the base: fast-forward it there, files and all (refused if base moved)
            r = git("merge", "--ff-only", "-q", new, cwd=holder_now)
        else:  # base not checked out anywhere: move the ref, only if nobody moved it meanwhile
            r = git("update-ref", f"refs/heads/{base}", new, old)
        if r.returncode != 0:
            res["refused"] = (r.stderr or r.stdout).strip()[:300]
            print(json.dumps(res, ensure_ascii=False))
            sys.exit(f"fast-forward of {base} failed ({res['refused'][:200]}); {base} is unchanged")
        after = git("rev-parse", "--verify", "--quiet", f"refs/heads/{base}^{{commit}}").stdout.strip()
        if after != new and git("merge-base", "--is-ancestor", new, after).returncode == 0:
            res["note"] = f"{base} moved on to {after[:9]} right after the fast-forward (it contains the merge)"
        elif after != new:  # the checkout switched branches in the last instant: the fast-forward went elsewhere
            moved_to = git("symbolic-ref", "-q", "--short", "HEAD", cwd=holder_now).stdout.strip() if holder_now else ""
            res["refused"] = f"{base} is at {after[:9]}, not {new[:9]}, after the fast-forward"
            log(d, {"ev": "merge-misfired", "branch": a.branch, "sha": sha, "base": base, "to": new,
                    "checkout_branch": moved_to})
            print(json.dumps(res, ensure_ascii=False))
            if not holder_now:
                sys.exit(f"{base} is at {after[:9]} right after it was moved to {new[:9]}: something else moved it; "
                         f"check `git log -1 {base}` and run merge again")
            sys.exit(f"{holder_now or 'the repo'} switched to {moved_to or 'another branch'} during the fast-forward: that "
                     f"branch may now be at {new[:9]}, and {base} is unchanged. Check {moved_to or 'it'} "
                     f"(`git -C \"{holder_now}\" log -1`), then run merge again")
        res["applied"] = True
        ev = {"ev": "merge", "branch": a.branch, "sha": sha, "base": base, "from": old, "to": new,
              "checks": res["checks"]}
        fresh = None
        try:  # IL-22: the board lists the merge until a regression round clears it (`qa done`)
            fresh = st = load(d)  # fresh: the checks may have run for minutes
            st.setdefault("qa_pending", []).append({"branch": a.branch, "sha": sha, "base": base, "to": new,
                                                    "at": now()})
            save(d, st, ev)
        except Exception as e:  # the base already moved: report it, never fail the merge on bookkeeping
            res["note"] = (res.get("note", "") + f"; regression list not updated ({e})").lstrip("; ")
            if isinstance(fresh, dict) and isinstance(fresh.get("qa_pending"), list):
                ev["snap_qa_pending"] = fresh["qa_pending"]  # rebuild then agrees with a state.json already written
            log(d, ev)
        print(json.dumps(res, ensure_ascii=False))
    finally:
        git("worktree", "remove", "--force", str(wt))
        shutil.rmtree(tmp, ignore_errors=True)
        git("worktree", "prune")
        sys.stdout.flush()
        if final["past"]:  # a signal that waited acts now, after the report, and says what happened
            def late(signum, _frame):
                raise SystemExit(f"stopped by signal {signum} after the merge step finished: see the report above")
            for s_ in sigs:
                signal.signal(s_, late)
            if can_block:
                signal.pthread_sigmask(signal.SIG_UNBLOCK, sigs)
        for s_, h in saved.items():
            signal.signal(s_, h)


# ------------------------------------------------------------------ bash guard (T36)
# A PreToolUse hook for Bash: asks the user before big decisions, denies wiping / or ~, says nothing otherwise.
# Permission rules match the raw command text with `*` spanning spaces, so a rule can never tell a command from a
# word in a commit message; this reads the argv of each command instead (heredoc bodies and quoted text are data).

_SHELL_OPS = {"&&", "||", ";", "|", "&", "(", ")", "|&", ";;", "{", "}"}
_WRAPPERS = {"command", "exec", "env", "nohup", "time", "nice", "xargs", "builtin", "timeout", "gtimeout", "watch",
             "caffeinate", "stdbuf", "doas"}
_KEYWORDS = {"if", "then", "elif", "else", "fi", "do", "done", "while", "until", "for", "case", "esac", "!", "in"}
_WRAPPER_VALUE_OPTS = {"-n", "-u", "-I", "-L", "-P", "-d", "-s", "-k", "-S", "-o", "-e", "-i", "--signal",
                       "--kill-after", "--interval", "-E", "-a"}
_GUARD_MAX = 1 << 20  # parse at most 1 MB of a command line
_PLAIN = re.compile(r"[^\s'\"\\$`<>;&|(){}#]+")
_REDIR = re.compile(r"[<>]+&?-?\d*|&>>?")
_TARGET = re.compile(r"\s*(\"[^\"]*\"|'[^']*'|[^\s;&|()<>]+)")
_OP = re.compile(r"&&|\|\||;;|\|&|&>>?|[;&|(){}]")


def _heredoc_word(cmd, i):
    """The heredoc delimiter starting at i (after `<<` or `<<-`), as the shell reads it: one word, its quotes
    removed (`E"O"F` and `'EOF'` are both EOF), and the index after it. ('', i) when there is none."""
    n = len(cmd)
    while i < n and cmd[i] in " \t":
        i += 1
    out, start = [], i
    while i < n and cmd[i] not in " \t\n;&|()<>":
        c = cmd[i]
        if c == "$" and i + 1 < n and cmd[i + 1] in "'\"":  # $'EOF' and $"EOF" close at EOF, like 'EOF'
            i += 1
            continue
        if c in "'\"":
            j = cmd.find(c, i + 1)
            if j < 0 or "\n" in cmd[i:j] or "\\" in cmd[i:j]:
                return "", start  # unclosed, multi-line, or escapes the shell decodes ($'E\x4fF', "E\$F"): untrusted
            out.append(cmd[i + 1:j])
            i = j + 1
        elif c == "\\" and i + 1 < n:
            out.append(cmd[i + 1])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out), i


def _segments(cmd, heredoc=True):
    """The commands in a shell line, each as an argv list: quotes kept together across lines, heredoc bodies dropped,
    `\\`+newline joined, operators and newlines split commands, `$( )` and backtick bodies judged as commands too.
    Arithmetic (`(( ))`, `$(( ))`, `$[ ]`) and `${ }` bodies are judged with heredoc=False: `<<` there is a shift or
    plain text, never a heredoc, so no line after it is dropped."""
    cmd, out, cur, word, i, n, heredocs = cmd[:_GUARD_MAX], [], [], None, 0, 0, []
    n = len(cmd)

    def end_word():
        nonlocal word
        if word is not None:
            cur.append(word)
        word = None

    def end_cmd():
        nonlocal cur
        end_word()
        if cur:
            out.append(cur)
        cur = []

    def sub_body(j, closer, opener="("):  # the text of $( ... ), ` ... `, (( ... )), ${ ... } from j; index after it
        depth, k = 1, j
        while k < n:
            c = cmd[k]
            if c == "\\":
                k += 2
                continue
            if c == "'" and closer != "`":  # a quoted `)` does not close it
                q = cmd.find("'", k + 1)
                k = n if q < 0 else q + 1
                continue
            if c == '"' and closer != "`":
                k += 1
                while k < n and cmd[k] != '"':
                    k += 2 if cmd[k] == "\\" else 1
                k += 1
                continue
            if c == opener and closer != "`":
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    return cmd[j:k], k + 1
            k += 1
        return cmd[j:], n

    def no_heredoc(j):  # $(( )), (( )), $[ ], ${ } at j: judge the inside, `<<` there is no heredoc
        if cmd.startswith("$((", j) or cmd.startswith("((", j):
            k = j + (3 if cmd[j] == "$" else 2)
            body, k2 = sub_body(k, ")")
            k2 = k2 + 1 if k2 < n and cmd[k2] == ")" else k2  # the second closing paren
        else:
            opener, closer = ("[", "]") if cmd[j + 1] == "[" else ("{", "}")
            body, k2 = sub_body(j + 2, closer, opener)
        out.extend(_segments(body, heredoc=False))
        return body, k2

    while i < n:
        c = cmd[i]
        if c == "\\" and i + 1 < n:
            if cmd[i + 1] == "\n":
                i += 2
                continue
            word = (word or "") + cmd[i + 1]
            i += 2
        elif c == "'":
            j = cmd.find("'", i + 1)
            j = n if j < 0 else j
            word = (word or "") + cmd[i + 1:j]
            i = j + 1
        elif c == '"':
            j, buf = i + 1, []
            while j < n and cmd[j] != '"':
                if cmd[j] == "\\" and j + 1 < n:
                    buf.append(cmd[j + 1])
                    j += 2
                    continue
                if cmd.startswith(("$((", "$[", "${"), j):
                    body, j2 = no_heredoc(j)
                    buf.append(cmd[j:j2])
                    j = j2
                    continue
                if cmd.startswith("$(", j) or cmd[j] == "`":
                    body, j2 = sub_body(j + 2 if cmd[j] == "$" else j + 1, ")" if cmd[j] == "$" else "`")
                    out.extend(_segments(body))
                    buf.append(cmd[j:j2])
                    j = j2
                    continue
                buf.append(cmd[j])
                j += 1
            word = (word or "") + "".join(buf)
            i = j + 1
        elif cmd.startswith(("$((", "$[", "${"), i):
            j0 = i
            _, i = no_heredoc(i)
            word = (word or "") + cmd[j0:i]  # kept as typed: `${HOME}` is still home to the wipe check
        elif cmd.startswith("((", i) and word is None:  # (( arithmetic )) and for (( ...; ...; ... ))
            _, i = no_heredoc(i)
        elif cmd.startswith("$(", i) or c == "`":
            body, i = sub_body(i + 2 if c == "$" else i + 1, ")" if c == "$" else "`")
            out.extend(_segments(body))
            word = (word or "") + "$(...)"
        elif c == "\n":
            end_cmd()
            i += 1
            for tag, strip in heredocs:  # heredoc bodies are data
                k, closed = i, False
                while k < n:
                    j = cmd.find("\n", k)
                    j = n if j < 0 else j
                    line, k = cmd[k:j], j + 1
                    if (line.lstrip("\t") if strip else line) == tag:
                        closed = True
                        break
                if not closed:  # no closing line: maybe a delimiter read wrong; judge the rest as commands
                    break
                i = k
            heredocs = []
        elif c in " \t\r":
            end_word()
            i += 1
        elif c in "<>":
            if word is not None and word.isdigit():
                word = None
            end_word()
            if heredoc and cmd.startswith("<<", i) and not cmd.startswith("<<<", i):
                tag, j = _heredoc_word(cmd, i + 2 + (cmd.startswith("<<-", i)))
                if tag:
                    heredocs.append((tag, cmd.startswith("<<-", i)))
                    i = j
                    continue
            i = _REDIR.match(cmd, i).end()
            m = _TARGET.match(cmd, i)  # the redirect target is not an argument
            i = m.end() if m else i
        elif c in ";&|()" or (c in "{}" and word is None and (i + 1 >= n or cmd[i + 1] in " \t\n;")):
            m = _OP.match(cmd, i)
            if m.group(0).startswith("&>"):  # &>file redirects, it does not end the command
                end_word()
                m = _TARGET.match(cmd, m.end())
                i = m.end() if m else i + 2
                continue
            end_cmd()
            i = m.end()
        elif c == "#" and word is None:
            j = cmd.find("\n", i)
            i = n if j < 0 else j
        else:
            m = _PLAIN.match(cmd, i)
            word = (word or "") + (m.group(0) if m else c)
            i = m.end() if m else i + 1
    end_cmd()
    return out


def _flags(args):
    """Short flags (combined `-fu` -> f, u) and long flags of an argv tail, up to `--`."""
    short, long_ = set(), set()
    for x in args:
        if x == "--":
            break
        if x.startswith("--"):
            long_.add(x.split("=", 1)[0])
        elif x.startswith("-") and len(x) > 1:
            short.update(x[1:])
    return short, long_


def _wipe_target(x):
    x = x.rstrip("/") or "/"
    return x in ("/", "~", "$HOME", "${HOME}", "/*", "~/*", "$HOME/*", "${HOME}/*") or x == str(Path.home())


def big_decision(cmd, _depth=0):
    """("deny"|"ask", why) for a big decision in a Bash command line, else None."""
    worst = None
    for argv in _segments(cmd):
        hit = _judge(argv, _depth)
        if hit and hit[0] == "deny":
            return hit
        worst = worst or hit
    return worst


def _judge(argv, depth=0):
    """The decision for one command's argv (wrappers, keywords and VAR=x stripped; `sh -c`, `eval`, `find -exec`
    bodies judged as commands)."""
    worst, sudo = None, False
    while True:
        while argv and (argv[0] in _KEYWORDS or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", argv[0])):
            argv = argv[1:]
        if not argv or not (Path(argv[0]).name in _WRAPPERS or Path(argv[0]).name in ("sudo",)):
            break
        w = Path(argv[0]).name
        sudo = sudo or w in ("sudo", "doas")
        argv = argv[1:]
        while argv and argv[0].startswith("-") and argv[0] != "-":  # wrapper options and their values
            opt = argv.pop(0)
            if opt in _WRAPPER_VALUE_OPTS and argv and w != "sudo":
                argv = argv[1:]
            elif w == "sudo" and opt in ("-u", "-g", "-C", "-D", "-h", "-p", "-U", "-r", "-t") and argv:
                argv = argv[1:]
        if w in ("timeout", "gtimeout") and argv:  # the duration
            argv = argv[1:]
    if sudo:
        worst = ("ask", "sudo")
    if not argv:
        return worst
    name, args = Path(argv[0]).name, argv[1:]
    short, long_ = _flags(args)
    why = None
    if name in ("bash", "sh", "zsh", "dash", "ksh") and depth < 4:
        for k, x in enumerate(args):
            if x.startswith("-") and not x.startswith("--") and "c" in x[1:]:
                rest = [y for y in args[k + 1:] if not y.startswith("-")]
                if rest:
                    hit = big_decision(rest[0], depth + 1)
                    if hit:
                        return hit if hit[0] == "deny" or not worst else worst
                break
    elif name == "eval" and depth < 4:
        hit = big_decision(" ".join(args), depth + 1)
        if hit:
            return hit if hit[0] == "deny" or not worst else worst
    if name == "rm":
        rec = bool(short & {"r", "R"}) or "--recursive" in long_
        if rec and any(_wipe_target(x) for x in args if not x.startswith("-")):
            return "deny", "wiping the root or home folder"
        if rec:
            why = "recursive delete"
    elif name == "find":
        if "-delete" in args:
            why = "find deleting files"
        for k, x in enumerate(args):
            if x in ("-exec", "-execdir", "-ok", "-okdir") and depth < 4:
                sub = []
                for y in args[k + 1:]:
                    if y in (";", "+"):
                        break
                    sub.append(y)
                hit = _judge(sub, depth + 1)
                if hit and hit[0] == "deny":
                    return hit
                why = why or (hit and hit[1]) or (sub and Path(sub[0]).name == "rm" and "find deleting files")
    elif name == "git":
        i = 0
        while i < len(args) and args[i].startswith("-"):  # global options: -C <path>, -c k=v, --git-dir=...
            i += 2 if args[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") else 1
        sub, rest = (args[i], args[i + 1:]) if i < len(args) else ("", [])
        s_, l_ = _flags(rest)
        pos = [x for x in rest if not x.startswith("-")]
        if sub == "push":
            if "f" in s_ or "--force" in l_ or "--mirror" in l_ or "--delete" in l_ or "d" in s_ \
                    or "--prune" in l_ or any(x.startswith(("+", ":")) for x in pos[1:]):
                why = "forced or deleting push"
        elif sub == "reset" and l_ & {"--hard", "--merge"}:
            why = "hard reset"
        elif sub == "clean" and not ("n" in s_ or "--dry-run" in l_):
            why = "git clean"
        elif sub == "branch" and ("D" in s_ or "M" in s_ or "f" in s_ or "--force" in l_):
            why = "forced branch delete or move"
        elif sub == "checkout" and ("--" in rest or "." in pos or "f" in s_ or "--force" in l_):
            why = "checkout discarding changes"
        elif sub == "switch" and ("--discard-changes" in l_ or "f" in s_ or "--force" in l_):
            why = "switch discarding changes"
        elif sub == "restore" and not (("S" in s_ or "--staged" in l_) and not ("W" in s_ or "--worktree" in l_)):
            why = "restore discarding changes"
        elif sub == "stash" and pos[:1] in (["drop"], ["clear"]):
            why = "dropping a stash"
        elif sub == "worktree" and pos[:1] == ["remove"]:
            why = "removing a worktree"
        elif sub == "update-ref" and "d" in s_ or sub in ("filter-branch", "filter-repo"):
            why = "rewriting refs or history"
        elif sub == "reflog" and pos[:1] in (["expire"], ["delete"]):
            why = "expiring the reflog"
    elif name in ("shutdown", "reboot", "halt", "diskutil", "killall", "pkill", "dropdb", "mkfs") \
            or name.startswith("mkfs."):
        why = name
    elif name == "launchctl" and args[:1] and args[0] in ("unload", "bootout", "remove", "kill", "stop",
                                                          "kickstart", "disable", "reboot"):
        why = f"launchctl {args[0]}"
    elif name == "brew" and (args[:1] in (["uninstall"], ["reinstall"], ["remove"], ["rm"]) or
                             args[:2] in (["services", "stop"], ["services", "restart"], ["services", "kill"])):
        why = "brew " + " ".join(args[:2])
    elif name in ("npm", "pnpm", "yarn") and args[:1] and args[0] in ("uninstall", "remove", "rm", "un") \
            and ("g" in short or "--global" in long_ or args[:2] == ["global", "remove"]):
        why = "global uninstall"
    elif name in ("psql", "mysql", "sqlite3") and re.search(r"\b(DROP|TRUNCATE)\b", " ".join(args), re.I):
        why = "dropping data"
    elif name == "gh" and (args[:2] in (["repo", "delete"], ["repo", "archive"], ["repo", "rename"]) or
                           (args[:2] == ["repo", "edit"] and long_ & {"--visibility", "--default-branch"}) or
                           (args[:1] == ["api"] and re.search(r"(?i)(-X\s*=?\s*DELETE|--method[ =]DELETE|"
                                                              r"visibility=|private=|default_branch=|"
                                                              r"deleteRepository)", " ".join(args)))):
        why = "gh " + " ".join(args[:2])
    if why and not worst:
        worst = ("ask", why)
    return worst


def guard_log_path():
    return Path(os.environ.get("TW_GUARD_LOG") or "~/.cache/teamwork/guard-errors.jsonl").expanduser()


def guard_errors_recent(days=7):
    """How many times bash-guard hit an internal error (and let the command through) in the last `days` days."""
    try:
        lines = guard_log_path().read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return 0
    cutoff, n = _when(now()) - dt.timedelta(days=days), 0
    for line in lines:
        try:
            x = json.loads(line) if line.strip() else None
        except ValueError:
            continue
        n += isinstance(x, dict) and _later(x.get("at"), cutoff)
    return n


def cmd_bash_guard(a):
    """PreToolUse hook: read the tool call on stdin, print a decision only for big decisions. Never fails: odd input
    or an internal error prints nothing, so ordinary work is never blocked by the guard itself."""
    try:
        if a.command is not None:
            cmd = a.command
        else:
            ev = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
            if not isinstance(ev, dict) or ev.get("tool_name") not in ("Bash", None):
                return
            ti = ev.get("tool_input")
            cmd = ti.get("command") if isinstance(ti, dict) else None
        if not isinstance(cmd, str):
            return
        if os.environ.get("TW_TEST_GUARD_FAULT"):  # tests: stands in for a bug inside the guard
            raise RuntimeError(os.environ["TW_TEST_GUARD_FAULT"])
        hit = big_decision(cmd)
    except Exception as exc:  # noqa: BLE001 - a guard bug must not stop the user's work
        try:  # ... but it leaves a line, so `selfcheck` can say the guard is failing
            path = guard_log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"at": now(), "type": type(exc).__name__, "message": str(exc)[:200]},
                                   ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 - a log that cannot be written changes nothing either
            pass
        return
    if hit:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": hit[0],
                                                 "permissionDecisionReason": f"teamwork: big decision ({hit[1]})"}}))


def main():
    p = argparse.ArgumentParser(
        prog="tw.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-C", metavar="PROJECT_FOLDER",
                   help="project folder to act on (always pass it: several projects can share one Remote Control folder)")
    s = p.add_subparsers(dest="cmd", required=True)

    x = s.add_parser("feature", help="feature ledger")
    x.add_argument("action", choices=["add", "set", "list"]); x.add_argument("name", nargs="?", default="")
    x.add_argument("--phase"); x.add_argument("--req", action="append", nargs="+"); x.add_argument("--task", action="append", nargs="+")
    x.add_argument("--status", choices=["planned", "building", "landed", "dropped"]); x.add_argument("--evidence"); x.add_argument("--reason")
    x.add_argument("--json", action="store_true"); x.add_argument("--done-since"); x.set_defaults(f=cmd_feature)
    x = s.add_parser("reconcile", help="compare requests, features and tasks")
    x.add_argument("--strict", action="store_true"); x.add_argument("--json", action="store_true"); x.set_defaults(f=cmd_reconcile)
    x = s.add_parser("wrapup", help="end-of-round ledger self-check")
    x.add_argument("--json", action="store_true"); x.set_defaults(f=cmd_wrapup)
    x = s.add_parser("sprint", help="summarize sprint progress")
    x.add_argument("--since"); x.add_argument("--json", action="store_true"); x.set_defaults(f=cmd_sprint)

    x = s.add_parser("init", help="create .teamwork/ in a project folder")
    x.add_argument("project")
    x.add_argument("--goal")
    x.add_argument("--path", default=".")
    x.add_argument("--repo-url")
    x.add_argument("--base", help="base branch (default: the repo's default branch, else main)")
    x.add_argument("--force", action="store_true")
    x.add_argument("--short", help="short name, max 6 chars, used in titles/headers/tags (default: initials, e.g. TFCC)")
    x.set_defaults(f=cmd_init)

    x = s.add_parser("lead", help="record lead session id, environment ids, repo url")
    x.add_argument("--session")
    x.add_argument("--rc-env")
    x.add_argument("--cloud-env")
    x.add_argument("--repo-url")
    x.add_argument("--base", help="change the base branch (merges, leases, trace and handover diff against it)")
    x.add_argument("--force", action="store_true", help="take over from a previous lead session")
    x.add_argument("--mode", choices=PERMISSION_MODES, help="the lead session's own permission mode (check it at "
                   "start): threads cannot be looser, so a lead below auto is reported, never silently downgraded")
    x.set_defaults(f=cmd_lead)

    x = s.add_parser("thread", help="add, update or reseat a thread (a seat that outlives its sessions)")
    x.add_argument("action", choices=["add", "set", "reseat"])
    x.add_argument("name")
    x.add_argument("--kind", choices=["rc", "cloud"], help="add only; default rc")
    x.add_argument("--cloud-reason", help="required with --kind cloud: why it never needs the folder or rc threads")
    x.add_argument("--focus")
    x.add_argument("--owns")
    x.add_argument("--session")
    x.add_argument("--branch")
    x.add_argument("--mode", choices=PERMISSION_MODES, help="the mode the thread's session actually runs in")
    x.add_argument("--model", help="the thread's default model (create_session `model`); empty = the lead's (IL-31)")
    x.add_argument("--status")
    x.add_argument("--summary")
    x.add_argument("--roster-sent", action="store_true",
                   help="you sent this thread the newest ROSTER (thread name 'all': every live thread)")
    x.add_argument("--reason", help="reseat: why the previous session left (failed, recalled, context full, ...)")
    x.add_argument("--from-status", action="store_true",
                   help="this update comes from a report the thread sent (tw-status block or message): resets stuck timer")
    x.set_defaults(f=cmd_thread)

    x = s.add_parser("task", help="add or update a plan task")
    x.add_argument("action", choices=["add", "set", "followups", "reviews"],
                   help="followups <id> [--apply]: one task per finding left at a capped review; reviews -: the table")
    x.add_argument("id", nargs="?", default="-")
    x.add_argument("--title")
    x.add_argument("--owner")
    x.add_argument("--deps")
    x.add_argument("--accept")
    x.add_argument("--topics", help="comma list of concepts/deliverables (e.g. mechanism,request-ledger): related "
                   "tasks must not run in parallel")
    x.add_argument("--touches", help="comma list of paths/globs the task will change (file lease), e.g. 'web/**,api/x.py'")
    x.add_argument("--model", help="model for this task, over the thread's default (IL-31); empty clears it")
    x.add_argument("--route", choices=["", "gpt", "fable", "opus"], help="task model route; empty clears it")
    x.add_argument("--no-scripts", action="store_true", help="write this by hand, no generating scripts (IL-31)")
    x.add_argument("--scripts-ok", action="store_true", help="set: clear --no-scripts")
    x.add_argument("--status")
    x.add_argument("--notes")
    x.add_argument("--merged", metavar="SHA", help="set: the commit this task's work was merged as (the board's "
                   "progress chain shows it as merged)")
    x.add_argument("--review", choices=["pass", "fail", "capped"],
                   help="independent review result against 'done when'; capped = last allowed round "
                        "(prefs review_max_rounds), no blocking finding, checks pass, --remaining for follow-ups")
    x.add_argument("--rounds", type=int, metavar="N",
                   help="with --review: review rounds the thread ran (from its DONE); only goes up. Default: +1")
    x.add_argument("--no-blocking", action="store_true", help="with --review capped, or pass with --remaining: nothing blocking is left open")
    x.add_argument("--remaining", metavar="FINDINGS", help="with --review pass or capped: important findings left, ';'-separated")
    x.add_argument("--apply", action="store_true", help="followups: add the tasks (default: dry run)")
    x.add_argument("--reviewer", help="who reviewed (not the owner): lead-subagent, review thread name, user")
    x.add_argument("--review-note")
    x.add_argument("--checks", choices=["pass", "fail", "none"], help="tests / checks result on the branch")
    x.add_argument("--skip-review", metavar="REASON", help="mark done without review (recorded; needs --user-said)")
    x.add_argument("--user-said", metavar="WORDS", help="the user's words: needed for --skip-review and dropping")
    x.set_defaults(f=cmd_task)
    x = s.add_parser("task-msg", help="format a task dispatch message")
    x.add_argument("id"); x.add_argument("--dispatch", action="store_true"); x.set_defaults(f=cmd_task_msg)
    x = s.add_parser("promise", help="record and track commitments")
    x.add_argument("action", choices=["add", "done", "list"]); x.add_argument("text", nargs="?", default=""); x.add_argument("--req"); x.add_argument("--evidence"); x.add_argument("id", nargs="?"); x.set_defaults(f=cmd_promise)

    x = s.add_parser("plan", help="show, approve or reopen the plan")
    x.add_argument("action", choices=["show", "approve", "reopen"])
    x.add_argument("--user-said", metavar="WORDS", help="approve: the user's words agreeing to the plan (recorded)")
    x.set_defaults(f=cmd_plan)
    s.add_parser(
        "ready", help="approved tasks whose deps are done, and free slots"
    ).set_defaults(f=cmd_ready)
    x = s.add_parser("route", help="keyword hint: which thread owns this request?")
    x.add_argument("text", nargs="?", default="")
    x.add_argument("--path", action="append", help="a file: which thread owns it (owns globs and active task "
                   "leases); repeat for several (IL-22: QA reports name the owner)")
    x.set_defaults(f=cmd_route)
    x = s.add_parser("ask", help="record a question waiting on the user, or --answer N")
    x.add_argument("text", nargs="?", default="")
    x.add_argument("--from", dest="frm", default="lead")
    x.add_argument("--need-thread", metavar="NAME", help="a thread's NEED-THREAD (message or STATUS need_thread line): "
                   "recorded once per (thread, name); a repeat is refused so it is not handled twice")
    x.add_argument("--default", type=int, metavar="N", help="apply a reversible default to question N")
    x.add_argument("--undoable", action="store_true")
    x.add_argument("--answer", type=int, metavar="N", help="mark question N (as numbered on the board, from 1) "
                   "answered; the answer text is optional")
    x.set_defaults(f=cmd_ask)
    x = s.add_parser("memory", help="append a project memory line")
    x.add_argument("text", nargs="?", default="")
    x.add_argument("--never-raise", action="store_true", help="a topic the user ruled out (IL-16)")
    x.add_argument("--user-owned", action="store_true", help="something the user provides or decides (IL-16)")
    x.add_argument("--temporary", action="store_true", help="a rule for now: confirm before enforcing it later (IL-36)")
    x.add_argument("--standing", action="store_true", help="a long-term requirement of the user's: every TASK "
                   "from task-msg carries it")
    x.add_argument("--standing-list", action="store_true", help="list the standing requirements")
    x.set_defaults(f=cmd_memory)
    x = s.add_parser("log", help="append a note to log.jsonl")
    x.add_argument("text")
    x.set_defaults(f=cmd_log)
    x = s.add_parser("board", help="render and print BOARD.md; --share: the user's phone view (SHARE.md)")
    x.add_argument("--share", action="store_true", help="deliverables, requests, waiting items, issues and every "
                   "requirement by phase/category, for a doc the user opens (IL-13, IL-38)")
    x.set_defaults(f=cmd_board)
    x = s.add_parser("card", help="a report card for the user: first line, done / doing / waiting, ledger line")
    x.add_argument("--kind", required=True, choices=list(CARD_KINDS),
                   help="compare (analysis), plan (new plan), read (material read), done (delivery), status (progress)")
    x.add_argument("--head", required=True, help="the first line: the receipt or the conclusion")
    x.add_argument("--since", metavar="ISO|last-report", help="count what changed after this time (default: everything)")
    x.add_argument("--link", action="append", metavar="URL", help="a link for the ledger line (repeatable)")
    x.add_argument("--ledger-line", action="store_true", help="print the ledger line on this card whatever "
                   "prefs ledger_line_every says")
    x.add_argument("--json", action="store_true")
    x.set_defaults(f=cmd_card)
    x = s.add_parser("docs", help="links to the documents the user opens: set board|ledger|features --link URL, list")
    x.add_argument("action", choices=["set", "list"])
    x.add_argument("which", nargs="?", choices=list(DOC_KINDS))
    x.add_argument("--link", metavar="URL")
    x.add_argument("--note", help="one line saying what the document is")
    x.set_defaults(f=cmd_docs)
    s.add_parser("intro", help="what to tell the user once the project is set up").set_defaults(f=cmd_intro)
    x = s.add_parser("issue", help="known issues: add / set / list (IL-20)")
    x.add_argument("action", choices=["add", "set", "list"])
    x.add_argument("text", nargs="?", default="", help='add: "<what is wrong>"; set: I<n>')
    x.add_argument("--owner")
    x.add_argument("--status", help=f"set: one of {', '.join(ISSUE_STATUSES)}")
    x.add_argument("--ref", help="branch@commit of the fix")
    x.add_argument("--all", action="store_true", help="list: include verified")
    x.set_defaults(f=cmd_issue)
    x = s.add_parser("info", help="format a lead INFO broadcast for the live threads")
    x.add_argument("text", nargs="?", default="")
    x.add_argument("--issue")
    x.add_argument("--json", action="store_true")
    x.set_defaults(f=cmd_info)
    x = s.add_parser("check-msg", help="check a hand-written message or session prompt before sending (IL-11/16/19)")
    x.add_argument("file", help="message file or - for stdin")
    x.add_argument("--prompt", action="store_true", help="the text is a session prompt (append_system_prompt): "
                   "check only that it is not a file pointer")
    x.add_argument("--user", action="store_true", help="the text is a message for the user: warn about commit ids, "
                   "command output and local paths (exit 0)")
    x.add_argument("--strict", action="store_true", help="with --user: a warning fails (exit 1)")
    x.add_argument("--json", action="store_true")
    x.set_defaults(f=cmd_check_msg)
    x = s.add_parser("qa", help="merges waiting for a regression round: list, or done <branch|sha> ... (IL-22)")
    x.add_argument("action", choices=["list", "done"])
    x.add_argument("items", nargs="*", help="done: the merges the round covered (branch, branch@sha7 or sha)")
    x.add_argument("--all", action="store_true", help="done: every waiting merge")
    x.add_argument("--note", help="done: what ran")
    x.set_defaults(f=cmd_qa)
    x = s.add_parser("deliverable", help="index of what the user can open: add / set (IL-13, IL-14)")
    x.add_argument("action", choices=["add", "set"])
    x.add_argument("name", help='add: "<name>"; set: D<n>')
    x.add_argument("--link")
    x.add_argument("--status", help="draft or final")
    x.add_argument("--missing", help="what is still missing")
    x.add_argument("--req", help="the request it answers")
    x.add_argument("--task"); x.add_argument("--dispatch-v", type=int)
    x.set_defaults(f=cmd_deliverable)
    x = s.add_parser("roster", help="print the roster block")
    x.add_argument("--thread")
    x.set_defaults(f=cmd_roster)
    s.add_parser("open", help="threads to sweep (not resolved/archived)").set_defaults(
        f=cmd_open
    )
    x = s.add_parser(
        "prefs", help="show or set coordination prefs: --set key=value ...; quota thresholds: "
        + ", ".join(f"{k}={v}" for k, v in QUOTA_DEFAULTS.items())
    )
    x.add_argument("--set", nargs="*")
    x.add_argument("--user-said", metavar="WORDS", help="the user's words, needed to turn require_review off")
    x.set_defaults(f=cmd_prefs)
    x = s.add_parser("quota", help="read local usage windows and quota tier (lead)")
    x.add_argument("--config-dir", action="append", metavar="DIR")
    x.add_argument("--codex-dir", metavar="DIR")
    x.add_argument("--json", action="store_true")
    x.set_defaults(f=cmd_quota)
    x = s.add_parser("model-log", help="record model outcomes and review suitability")
    x.add_argument("--task")
    x.add_argument("--type", choices=MODEL_TYPES)
    x.add_argument("--model", choices=MODELS)
    x.add_argument("--result", choices=["ok", "redo"])
    x.add_argument("--why")
    x.add_argument("--summary", action="store_true")
    x.add_argument("--json", action="store_true")
    x.add_argument("--deny", choices=MODEL_TYPES)
    x.add_argument("--allow", choices=MODEL_TYPES)
    x.add_argument("--user-said")
    x.set_defaults(f=cmd_model_log)
    x = s.add_parser("opening", help="show the opening policy questions")
    x.add_argument("--json", action="store_true"); x.set_defaults(f=cmd_opening)
    x = s.add_parser("selfcheck", help="check this lead session and project setup")
    x.add_argument("--mode"); x.add_argument("--model"); x.add_argument("--served-model"); x.add_argument("--served"); x.add_argument("--desktop", action="store_true"); x.add_argument("--effort"); x.add_argument("--want-effort")
    x.add_argument("--seats-used", type=int); x.add_argument("--seats-max", type=int, default=32)
    x.add_argument("--brief", action="store_true"); x.add_argument("--json", action="store_true"); x.set_defaults(f=cmd_selfcheck)
    x = s.add_parser("version", help="show this skill copy and active sessions")
    x.add_argument("--sessions", action="store_true")
    x.add_argument("--json", action="store_true")
    x.set_defaults(f=cmd_version)
    x = s.add_parser(
        "worker-prompt",
        help="filled worker protocol -> create_session append_system_prompt",
    )
    x.add_argument("thread")
    x.set_defaults(f=cmd_worker_prompt)
    x = s.add_parser("req", help="user requests ledger: add / link / set / close / list / import")
    x.add_argument("action", choices=["add", "link", "set", "close", "drop", "list", "import", "migrate"])
    x.add_argument("args", nargs="*", help='add: "<text>"; link: R<n> T1 [T2]; set/close: R<n>; import: <file.md>')
    x.add_argument("--id", help="add: explicit id (default next R<n>)")
    x.add_argument("--tasks", help="comma list of task ids to link")
    x.add_argument("--status", help="set: request status")
    x.add_argument("--reason"); x.add_argument("--evidence")
    x.add_argument("--remark", action="store_true")
    x.add_argument("--where"); x.add_argument("--followup"); x.add_argument("--text")
    x.add_argument("--phase", help="add/set: the phase it belongs to, for the full requirement list (IL-38)")
    x.add_argument("--category", help="add/set: e.g. goal, constraint, feature (IL-38)")
    x.add_argument("--user-said", metavar="WORDS", help="close/drop, and moves that are the user's decision: "
                   "the user's own words (recorded)")
    x.add_argument("--confirmed", action="store_true", help="close: kept for compatibility (needs --user-said)")
    x.add_argument("--declined", action="store_true", help="close --declined = drop (needs --user-said)")
    x.add_argument("--note")
    x.add_argument("--all", action="store_true", help="list: include closed")
    x.add_argument("--dry-run", action="store_true", help="import: show what would be imported")
    x.add_argument("--only", help="migrate --apply: only these ids (comma list), after reading the preview")
    x.add_argument("--apply", action="store_true")
    x.set_defaults(f=cmd_req)
    x = s.add_parser("rebuild", help="rebuild state.json from log.jsonl, sessions JSON and tw/<project>/* branches")
    x.add_argument("--sessions", help="JSON file with the list_sessions result (sessions tagged teamwork:<project>)")
    x.add_argument("--project", help="project name, only needed when the log has none")
    x.add_argument("--short", help="the project's short name in session tags (teamwork:<short>), if the log lost it")
    x.add_argument("--apply", action="store_true", help="replace state.json (a timestamped backup is kept)")
    x.add_argument("--force", action="store_true", help="apply even when the rebuild is much smaller than state.json")
    x.set_defaults(f=cmd_rebuild)
    x = s.add_parser("handover", help="handover brief for the next session taking a thread's seat")
    x.add_argument("thread")
    x.add_argument("--leaving", help="session id that is leaving (default: the one holding the seat now)")
    x.add_argument("--reason", help="why it is leaving (failed, recalled, context full, ...)")
    x.set_defaults(f=cmd_handover)
    x = s.add_parser("trace", help="follow a request: its tasks, threads, sessions, commits, report times")
    x.add_argument("request")
    x.add_argument("--json", action="store_true")
    x.set_defaults(f=cmd_trace)
    x = s.add_parser("merge", help="rehearse merging a branch into the base in a temp worktree, run the checks, "
                     "and with --apply fast-forward the base (the live checkout never holds a half merge)")
    x.add_argument("branch")
    x.add_argument("--sha", required=True, help="the reviewed commit from the owner's DONE; the branch head must be "
                   "exactly this commit, and only it is merged")
    x.add_argument("--squash", action="store_true", help="one squash commit (needs --message starting with the task id)")
    x.add_argument("--message", help="merge or squash commit subject; keep the task id first (T3: ...)")
    x.add_argument("--test", help="check command, run in the rehearsal (default: prefs check_cmd)")
    x.add_argument("--no-test", metavar="WHY", help="nothing to check (docs only): say why")
    x.add_argument("--apply", action="store_true", help="fast-forward the base after a clean merge and green checks")
    x.set_defaults(f=cmd_merge)

    x = s.add_parser("bash-guard", help="PreToolUse hook for Bash (install.py installs it): ask before big "
                     "decisions (removals, history rewrites, discards, shutdowns), deny wiping / or ~, else silent")
    x.add_argument("--command", help="check this command line instead of reading the hook event on stdin")
    x.set_defaults(f=cmd_bash_guard)
    x = s.add_parser("guard", help="fail if private words/paths (.teamwork/PRIVATE.md) are in commits or files")
    x.add_argument("--repo", help="git checkout to scan (default: current folder)")
    x.add_argument("--base", help="scan commits base..ref (default: origin/<base branch>)")
    x.add_argument("--ref", default="HEAD")
    x.add_argument("--private", action="append", help="extra private list file (same format), repeatable")
    x.add_argument("--pre-push", action="store_true", help="read git pre-push stdin for the ranges to scan")
    x.add_argument("--stdin-owned", action="store_true", help="pre-push: the hook gives its stdin to the guard alone "
                   "(exec, or input=$(cat) saved first), so empty stdin means nothing to push; without it empty "
                   "stdin fails closed")
    x.add_argument("--hash-list", action="store_true", help="lead: print a salted, hashed private list for a cloud "
                   "thread's TASK (new salt each time; no words, no lengths)")
    x.add_argument("--kit", action="store_true", help="with --hash-list: print tw-guard.py, a self-contained check "
                   "script holding the hashed list, to paste into a cloud thread's TASK")
    x.add_argument("--hashed", metavar="FILE", help="cloud thread: check against a hashed list (from the TASK) instead "
                   "of .teamwork/PRIVATE.md; words matched anywhere in the text, path rules not included")
    x.add_argument("--remote", help="pre-push: the remote's name (the hook's $1); new branches are scanned for "
                   "every commit that remote does not have yet. Without it the whole pushed history is scanned")
    x.set_defaults(f=cmd_guard)
    x = s.add_parser("worktree", help="create an rc thread's git worktree + branch if missing (run before create_session)")
    x.add_argument("thread")
    x.add_argument("--dry-run", action="store_true")
    x.set_defaults(f=cmd_worktree)
    x = s.add_parser("projects", help="portfolio view of all projects under --root")
    x.add_argument("--root", default="."); x.add_argument("--json", action="store_true"); x.set_defaults(f=cmd_projects)
    s.add_parser(
        "context", help="SessionStart hook output (silent outside teamwork folders)"
    ).set_defaults(f=cmd_context)

    a = p.parse_args()
    if a.C:
        os.environ["TEAMWORK_DIR"] = str(Path(a.C).expanduser().resolve() / ".teamwork")
        if a.cmd == "init" and a.path == ".":
            a.path = a.C
    a.f(a)


if __name__ == "__main__":
    main()
