#!/usr/bin/env python3
"""teamwork ledger: the single source of truth for one teamwork project.

State lives in <project folder>/.teamwork/ :
  state.json       machine state: lead, environments, threads, tasks, prefs, questions
                   (written ONLY by the lead, always through this script)
  BOARD.md         human view of state.json, regenerated on every write
  INSTRUCTIONS.md  project instructions: sent to every new thread and to the lead (<=16k chars)
  MEMORY.md        project memory: facts, decisions, conventions (lead edits; threads propose)
  log.jsonl        append-only event log

Stdlib only. `tw.py <command> -h` for details.
"""

import argparse
import datetime as dt
import functools
import json
import os
import re
import subprocess
import sys
from pathlib import Path

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
        "req_open": "open", "req_dispatched": "dispatched", "req_delivered": "delivered (report it!)",
        "req_reported": "reported", "req_closed": "closed", "req_proposed": "proposed (waiting for a yes)",
        "req_dropped": "dropped", "followup": "follow-up",
        "no_threads": "(no threads yet)",
        "t_created": "created", "t_updated": "updated", "t_reported": "reported", "t_closed": "closed",
        "t_where": "where", "t_thread": "thread", "t_session": "session", "t_branch": "branch", "t_log": "log",
        "t_other": "other unmerged work on", "t_no_tasks": "no tasks linked",
    },
    "zh": {
        "planned": "建议中（还没有会话）", "starting": "已创建，第一轮还没结束",
        "working": "进行中", "waiting": "等你处理（问题、权限弹窗或失败）",
        "review": "待审", "landing": "合并中（已批准）",
        "idle": "空闲（本轮结束，没有待办）", "resolved": "已完结",
        "stuck": "可能卡住（进行中，超过 {n} 分钟没有汇报）", "no_report": "{m} 分钟没有汇报",
        "overview": "总览", "questions": "等你回答", "roster": "成员", "plan": "计划",
        "requests": "你提的事（未关闭）", "unreported": "已交付、还没回报给用户",
        "req_open": "待办", "req_dispatched": "已派", "req_delivered": "已交付（待回报！）",
        "req_reported": "已回报", "req_closed": "已关闭", "req_proposed": "已提议（等你同意）",
        "req_dropped": "已放弃", "followup": "回访",
        "no_threads": "（还没有线程）",
        "t_created": "创建", "t_updated": "更新", "t_reported": "回报", "t_closed": "关闭",
        "t_where": "落到哪里", "t_thread": "线程", "t_session": "会话", "t_branch": "分支", "t_log": "日志",
        "t_other": "分支上其他未合并的工作", "t_no_tasks": "没有关联任务",
    },
}


def lab(st, key, **kw):
    code = (st.get("prefs", {}).get("language") or "en").strip().lower().split("-")[0]
    return LABELS.get(code, LABELS["en"]).get(key, LABELS["en"][key]).format(**kw)


REQ_STATUSES = ["open", "proposed", "dispatched", "delivered", "reported", "closed", "dropped"]
REQ_END = ("closed", "dropped")
# Request moves the lead may make with `req set` (framework A9). closed/dropped only via close/drop (user action).
REQ_MOVES = {"open": {"proposed", "dispatched", "reported"}, "proposed": {"dispatched"},
             "dispatched": {"delivered"}, "delivered": {"reported"}, "reported": {"dispatched"}}
REQ_NEEDS_USER = {("proposed", "dispatched"), ("reported", "dispatched")}  # user said yes / asked for changes
# Task moves (framework A9). done needs review first and passes the review gate.
TASK_MOVES = {"planned": {"approved", "dropped"}, "approved": {"dispatched", "dropped"},
              "dispatched": {"working", "recalled"}, "working": {"blocked", "review", "recalled"},
              "blocked": {"working", "recalled"}, "review": {"working", "done"},
              "recalled": {"dispatched", "dropped"}, "done": set(), "dropped": set()}
# Accepted when importing a markdown ledger (status column in either language).
REQ_STATUS_ALIASES = {"待办": "open", "已派": "dispatched", "已交付": "delivered", "已回报": "reported",
                      "已关闭": "closed", "已提议": "proposed", "已放弃": "dropped", **{x: x for x in REQ_STATUSES}}


def unreported(st):
    return {r: q for r, q in st.get("requests", {}).items() if q["status"] == "delivered"}


def render_requests(st):
    reqs = [(r, q) for r, q in st.get("requests", {}).items() if q["status"] not in REQ_END]
    if not reqs:
        return None
    order = {"delivered": 0, "proposed": 1, "open": 2, "dispatched": 3, "reported": 4}
    reqs.sort(key=lambda x: (order.get(x[1]["status"], 9), int(x[0][1:]) if x[0][1:].isdigit() else 0))
    out = []
    for r, q in reqs:
        line = f"- **{r}** [{lab(st, 'req_' + q['status'])}] {q['text']}"
        extra = [", ".join(q.get("tasks") or []), q.get("where") or ""]
        extra = " · ".join(x for x in extra if x)
        if extra:
            line += f" — {extra}"
        if q.get("followup"):
            line += f" — {lab(st, 'followup')}: {q['followup']}"
        out.append(line)
    return "\n".join(out)


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
    st["updated"] = now()
    tmp = d / "state.json.tmp"
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(d / "state.json")
    (d / "BOARD.md").write_text(render_board(st), encoding="utf-8")
    if event:
        more = {n: st["threads"][n] for n in touched if n in st["threads"] and n != event.get("thread")}
        log(d, {**event, **snapshots(st, event), **({"snap_more_threads": more} if more else {})})


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
    if ev.get("ev") == "req-import":
        snap["snap_reqs"] = {r: st["requests"][r] for r in ev.get("ids", []) if r in st.get("requests", {})}
    if ev.get("ev") in ("init", "lead", "prefs"):
        snap["snap_meta"] = {k: st.get(k) for k in ("project", "goal", "created", "repo", "envs", "lead", "prefs")}
    if ev.get("ev") in ("question", "answered"):
        snap["snap_questions"] = st.get("questions", [])
    if ev.get("ev", "").startswith("plan-"):
        snap["snap_plan_approved"] = st.get("plan_approved")
        snap["snap_tasks"] = st.get("tasks", {})  # approve/reopen changes every task's status
    if "roster_version" in st:
        snap["snap_roster_version"] = st["roster_version"]
    return snap


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
    return f'git -C "{repo}" worktree add -b {t["branch"]} "{path}" {base}'


def session_args(st, name):
    """Extra create_session arguments for a thread: permission mode and pre-approved tools."""
    out = {"extra_allowed_tools": THREAD_TOOLS}
    mode = (st.get("prefs", {}).get("thread_permission_mode") or "").strip()
    if mode:
        out["permission_mode"] = mode
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
            f"| {tid} | {k['title']} | {k.get('owner') or '-'} | {deps} | {k['status']} | {k.get('accept') or '-'} "
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
    reqs = render_requests(st)
    if reqs:
        out += [f"## {lab(st, 'requests')}", "", reqs, ""]
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
            f"- [{i + 1}] ({q['from']}) {q['text']}" for i, q in open_q
        ]
    out += ["", f"## {lab(st, 'roster')}", "", render_roster(st), "", f"## {lab(st, 'plan')}", "", render_tasks(st)]
    return "\n".join(out) + "\n"


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
            "thread_permission_mode": "",  # create_session permission_mode; empty = the lead's own mode
            "check_cmd": "",  # the project's checks, run by `merge` on the rehearsed merge
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
            "threads propose additions with a MEMORY message. Keep it short: it is pasted into every new thread.\n\n",
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
    if a.rc_env:
        st["envs"]["rc"] = a.rc_env
    if a.cloud_env:
        st["envs"]["cloud"] = a.cloud_env
    if a.repo_url:
        st["repo"]["url"] = a.repo_url
    if a.base:
        st["repo"]["base"] = a.base
    save(d, st, {"ev": "lead", "session": a.session, "envs": st["envs"]})
    print(
        json.dumps(
            {"lead": st["lead"], "envs": st["envs"], "repo": st["repo"]},
            ensure_ascii=False,
        )
    )


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
    if b.lower() == base.lower() or parts[0] in ("origin", "refs", "remotes"):
        return f"--branch {b!r}: a thread works on its own branch, never the base or a remote one"
    lb = b.lower()
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
            sys.exit(f"--branch: {name}'s worktree {t0['worktree']} already exists on {t0['branch']}; the branch is "
                     "fixed once its worktree is made")
        if a.action != "set" or not t0 or t0.get("session_id") or t0.get("sessions") or t0.get("status") != "planned":
            sys.exit("--branch changes only while the thread is planned and has never had a session (the user editing "
                     "the plan); after that the branch is fixed: leases and trace follow it, so work there would stop "
                     "counting. Add a new thread for a new branch")
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
            "branch": t["branch"],
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
    if a.from_status:
        t["last_status_at"] = now()
    if a.owns is not None:
        t["owns"] = csv(a.owns)
    if a.focus is not None:
        t["focus"] = a.focus
        t["title"] = title_for(st, name)
    if a.branch is not None and a.branch != t["branch"]:
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
            "status": "approved" if st["plan_approved"] else "planned",
            "notes": "",
            "created": now(),
        }
        save(d, st, {"ev": "task-add", "task": a.id, "owner": a.owner})
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
        if a.topics is not None:
            k["topics"] = norm_topics(a.topics)
        if a.notes is not None:
            k["notes"] = a.notes
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
            k["status"] = a.status
            if a.status in ("review", "done"):
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
    seats = [st["threads"][n] for n in dict.fromkeys([k.get("owner") or ""] + list(k.get("past_owners") or []))
             if n in st["threads"]]
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
                "unreported_requests": {r: q["text"] for r, q in unreported(st).items()},
                "rc_capacity_note": "a Remote Control server holds 32 sessions by default, shared by every project and lead on it; cloud threads do not count",
            }
        )
    )


def _tokens(s):
    return set(re.findall(r"[a-z0-9]+|[一-鿿]{2,}", s.lower()))


def cmd_route(a):
    d = need(find_dir())
    st = load(d)
    text = a.text.lower()
    words = _tokens(text)
    scores = []
    for name, t in st["threads"].items():
        if t["status"] == "archived":
            continue
        keys = [name, t.get("focus", "")] + t.get("owns", [])
        keys += [k["title"] for k in st["tasks"].values() if k.get("owner") == name]
        s = 0
        for key in keys:
            key = key.lower().strip()
            if not key:
                continue
            if key in text:
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
        q["answered"] = True
        if a.text:
            q["answer"] = a.text
        save(d, st, {"ev": "answered", "n": n})
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


def cmd_memory(a):
    d = need(find_dir())
    with open(d / "MEMORY.md", "a", encoding="utf-8") as f:
        f.write(f"- {a.text}  _(added {now()[:10]})_\n")
    log(d, {"ev": "memory", "text": a.text})
    print(read_file(d, "MEMORY.md"))


def cmd_log(a):
    d = need(find_dir())
    log(d, {"ev": "note", "text": a.text})
    print("logged")


def cmd_board(a):
    d = need(find_dir())
    st = load(d)
    (d / "BOARD.md").write_text(render_board(st), encoding="utf-8")
    print(render_board(st))


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
        if k == "require_review":
            if v.lower() not in ("1", "0", "true", "false", "yes", "no", "on", "off"):
                sys.exit("require_review must be true or false")
            if v.lower() in ("0", "false", "no", "off") and not (a.user_said or "").strip():
                sys.exit("turning the review gate off is the user's decision: add --user-said \"<their words>\"")
        cur = st["prefs"].get(k)
        if isinstance(cur, bool) or v.lower() in ("true", "false", "yes", "no", "on", "off"):
            st["prefs"][k] = v.lower() in ("1", "true", "yes", "on")
        elif v.isdigit():
            st["prefs"][k] = int(v)
        else:
            st["prefs"][k] = v
        if k == "thread_permission_mode" and v and v not in PERMISSION_MODES:
            sys.exit(f"thread_permission_mode: one of {', '.join(PERMISSION_MODES)}, or empty for the lead's own mode")
        if k == "thread_permission_mode" and v in ("auto", "bypassPermissions"):
            # tw.py cannot see the lead's own mode: say the rule out loud instead of recording a mode that fails
            print(f"note: threads get {v} only if the lead runs at least as loose a mode; create_session refuses a "
                  "thread more permissive than its creator, and bypassPermissions lets a thread run everything without "
                  "asking except the ask rules", file=sys.stderr)
        if k == "review_max_rounds":
            if not re.fullmatch(r"[0-9]{1,2}", v):
                sys.exit("review_max_rounds: a whole number of review rounds, 0-99 (default 3; 0 = no limit)")
            if 0 < int(v) < old_cap or (old_cap == 0 and int(v) > 0):
                if not (a.user_said or "").strip():
                    sys.exit("a lower review round limit lets work merge with fewer reviews: that is the user's "
                             "decision, add --user-said \"<their words>\"")
        if k == "short_name" and v and not re.fullmatch(SHORT_RE, v):
            sys.exit("short_name: letters/digits/hyphen, max 6 chars (e.g. TFCC)")
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
        "BRANCH": t["branch"],
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
    res = {"thread": a.thread, "worktree": str(path), "branch": t["branch"]}
    if path.exists():
        res["created"] = False
        res["note"] = "already exists"
        r = git("-C", str(path), "symbolic-ref", "-q", "--short", "HEAD")
        if r.returncode == 0 and r.stdout.strip() != t["branch"]:
            sys.exit(f"{path} is on {r.stdout.strip()}, but {a.thread}'s branch is {t['branch']}: leases and trace "
                     "follow the ledger's branch; switch the worktree or fix the ledger before the thread works")
    else:
        has_branch = git("show-ref", "--verify", "--quiet", f"refs/heads/{t['branch']}").returncode == 0
        cmd = (["worktree", "add", str(path), t["branch"]] if has_branch
               else ["worktree", "add", "-b", t["branch"], str(path), base])
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
    if t.get("worktree") != str(path):
        t["worktree"] = str(path)
        save(d, st, {"ev": "worktree", "thread": a.thread, "path": str(path)})
    elif res.get("created"):
        log(d, {"ev": "worktree", "thread": a.thread, "path": str(path)})
    print(json.dumps(res, ensure_ascii=False))


def deliver_ready(st):
    """Requests whose linked tasks are all done move open/dispatched -> delivered. Returns the ids moved."""
    moved = []
    for r, q in st.get("requests", {}).items():
        ts = q.get("tasks") or []
        if q["status"] == "dispatched" and ts and all(
                st["tasks"].get(t, {}).get("status") in ("review", "done") for t in ts):
            q["status"] = "delivered"
            q["updated"] = now()
            moved.append(r)
    return moved


def undeliver(st):
    """A delivered or reported request whose task went back to work (review fail, rework) is dispatched again:
    what was delivered or told to the user is no longer the result (report the new one when it comes back)."""
    moved = []
    for r, q in st.get("requests", {}).items():
        ts = q.get("tasks") or []
        if q["status"] in ("delivered", "reported") and not all(
                st["tasks"].get(t, {}).get("status") in ("review", "done") for t in ts):
            q["status"] = "dispatched"
            q["updated"] = now()
            moved.append(r)
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
    if new not in REQ_MOVES.get(cur, set()):
        allowed = ", ".join(sorted(REQ_MOVES.get(cur, set()))) or "nothing (it has ended)"
        sys.exit(f"{r} is {cur}: it can move to {allowed}")
    user_said = (user_said or "").strip() or None
    if (cur, new) in REQ_NEEDS_USER and not user_said:
        sys.exit(f"{r} {cur} -> {new} is the user's decision: add --user-said \"<their words>\"")
    if new == "delivered":
        ts = q.get("tasks") or []
        not_ready = [t for t in ts if st["tasks"].get(t, {}).get("status") not in ("review", "done")]
        if not ts or not_ready:
            sys.exit(f"{r} is delivered only when every linked task is in review or done"
                     + (f" (not yet: {', '.join(not_ready)})" if not_ready else " (no tasks linked)"))
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
        rows = {r: q for r, q in reqs.items() if a.all or q["status"] not in REQ_END}
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if a.action == "import":
        cmd_req_import(a, d, st)
        return
    if a.action == "add":
        if not a.args:
            sys.exit('usage: req add "<what the user asked, close to their words>" [--tasks T1,T2]')
        r = a.id or next_req_id(st)
        if r in reqs:
            sys.exit(f"request {r} exists")
        tasks = csv(a.tasks)
        check_tasks(st, tasks)
        q = {"text": " ".join(a.args), "status": "open", "tasks": tasks, "where": a.where or "",
             "followup": a.followup or "", "created": now(), "updated": now()}
        reqs[r] = q
        if tasks:
            q["status"] = "dispatched"
            deliver_ready(st)  # linked to work that is already in review or done
        save(d, st, {"ev": "req-add", "req": r, "text": q["text"], "tasks": tasks})
        print(json.dumps({r: q}, ensure_ascii=False))
        return
    if not a.args:
        sys.exit(f"usage: req {a.action} R<n> ...")
    r = a.args[0]
    if r not in reqs:
        sys.exit(f"unknown request {r}; known: {', '.join(reqs) or '-'}")
    q = reqs[r]
    if a.action == "link":
        tasks = csv(",".join(a.args[1:])) + csv(a.tasks)
        if not tasks:
            sys.exit("usage: req link R<n> T1 [T2 ...]")
        check_tasks(st, tasks)
        q["tasks"] = list(dict.fromkeys(q.get("tasks", []) + tasks))
        if q["status"] in REQ_END:
            sys.exit(f"{r} has ended ({q['status']}); register a new request instead")
        if q["status"] == "reported":
            # more work on something already reported: the user reopens it (reported -> dispatched)
            set_req_status(st, r, q, "dispatched", user_said=a.user_said)
        if q["status"] == "open":
            q["status"] = "dispatched"  # proposed stays proposed until the user says yes
        undeliver(st)  # a new task still in progress
        deliver_ready(st)
    elif a.action == "set":
        if q["status"] in REQ_END:
            sys.exit(f"{r} has ended ({q['status']}); register a new request instead of editing it")
        if a.status:
            set_req_status(st, r, q, a.status, user_said=a.user_said)
        if a.status == "dispatched" and not q.get("tasks"):
            sys.exit(f"{r} has no tasks: link the work first (`req link {r} T<n>`), which dispatches it")
        for k in ("where", "followup", "text"):
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
        if not drop and q["status"] != "reported":
            sys.exit(f"{r} is {q['status']}: report it to the user first (`req set {r} --status reported`), "
                     "then close it when they accept")
        if not drop:
            for t in q.get("tasks") or []:
                why = accept_gate(st, t) if st["tasks"].get(t, {}).get("status") == "review" else None
                if why:
                    sys.exit(f"{r}: {t} {why}; the user accepts only checked work")
        back = [t for t in q.get("tasks") or [] if st["tasks"].get(t, {}).get("status") not in ("review", "done")]
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
        if q["status"] == "delivered" and tasks and not all(
                st["tasks"].get(t, {}).get("status") in ("review", "done") for t in tasks):
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
            for br in (t["branch"], f"origin/{t['branch']}"):
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
                commit_note = (f" (local and origin differ: commits tagged only [{t['branch']}] are not pushed, "
                               f"only [origin/{t['branch']}] not fetched)")
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
        f"- Owns: {', '.join(t.get('owns', [])) or '-'}",
        f"- Branch: {t['branch']} (base {base_name})" + (f"; worktree {t['worktree']}" if t.get("worktree") else ""),
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


def _blank_state(project, folder):
    return {"project": project, "goal": "", "created": None, "lead": {"session_id": None},
            "envs": {"rc": None, "cloud": None}, "repo": {"url": None, "path": str(folder), "base": "main"},
            "threads": {}, "tasks": {}, "plan_approved": False, "questions": [], "requests": {},
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
                                                             "repo", "goal") if old.get(k) != st.get(k))
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


def cmd_context(a):
    """SessionStart hook. Prints nothing outside a teamwork folder."""
    d = find_dir()
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
    text = "This folder is a teamwork project (skill: /teamwork). Board below; source of truth is .teamwork/state.json, " "changed only by the lead through tw.py.\n\n" + language_rule(st) + "\n\n" + unreported_note(st) + render_board(
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


def cmd_merge(a):
    """Rehearse a merge into the base in a throwaway worktree, run the checks there, and only with --apply
    fast-forward the base. The live checkout is never left half-merged: a conflict or a red check changes nothing."""
    import shutil
    import tempfile
    d = need(find_dir())
    st = load(d)
    proj = Path(st.get("repo", {}).get("path") or ".")
    base = st.get("repo", {}).get("base") or "main"
    git = lambda *x, cwd=proj: subprocess.run(["git", "-C", str(cwd), *x], capture_output=True, text=True)
    top = git("rev-parse", "--show-toplevel")
    if top.returncode != 0:
        sys.exit(f"{proj} is not a git repository")
    sub = git("rev-parse", "--show-prefix").stdout.strip()  # the project may be a subfolder of the repo
    test = a.test if a.test is not None else st.get("prefs", {}).get("check_cmd") or ""
    if not test.strip() and not a.no_test:
        sys.exit("no check to run: pass --test \"<command>\", set prefs check_cmd=\"<command>\", or --no-test "
                 "\"<why>\" for a change with nothing to test")
    if a.squash and not (a.message or "").strip():
        sys.exit("--squash needs --message \"T3: <summary> (squash of <branch>)\": keep the task id first so trace "
                 "and leases find it")
    old = git("rev-parse", "--verify", "--quiet", f"refs/heads/{base}^{{commit}}").stdout.strip()
    tip = git("rev-parse", "--verify", "--quiet", f"{a.branch}^{{commit}}").stdout.strip()
    if not old:
        sys.exit(f"base branch {base} not found")
    if not tip:
        sys.exit(f"branch {a.branch} not found (git fetch first for a cloud branch)")
    res = {"branch": a.branch, "base": base, "base_was": old, "tip": tip, "applied": False}
    if git("merge-base", "--is-ancestor", tip, old).returncode == 0:
        res["note"] = f"{a.branch} is already in {base}: nothing to merge"
        print(json.dumps(res, ensure_ascii=False))
        return
    holder = None  # the checkout (this one or another worktree) that has the base checked out, if any
    for blk in git("worktree", "list", "--porcelain").stdout.split("\n\n"):
        lines = dict((ln.split(" ", 1) + [""])[:2] for ln in blk.splitlines())
        if lines.get("branch") == f"refs/heads/{base}":
            holder = Path(lines["worktree"])
    if a.apply and holder and git("status", "--porcelain", "--untracked-files=no", cwd=holder).stdout.strip():
        sys.exit(f"{holder} has uncommitted changes on {base}: commit or move them first")
    pre = git("merge-tree", "--write-tree", "--name-only", "--no-messages", old, tip)
    if pre.returncode == 1:  # conflict found without touching any checkout (git >= 2.38; older git: skip)
        res["conflicts"] = [f for f in pre.stdout.splitlines()[1:] if f.strip()]
        print(json.dumps(res, ensure_ascii=False))
        sys.exit(f"merge of {a.branch} into {base} conflicts: nothing changed; send the owner a FOLLOWUP to rebase")
    tmp = Path(tempfile.mkdtemp(prefix="tw-merge-"))
    wt = tmp / "wt"
    try:
        r = git("worktree", "add", "--detach", str(wt), old)
        if r.returncode != 0:
            sys.exit(f"git worktree add failed: {r.stderr.strip()}")
        if a.squash:
            r = git("merge", "--squash", tip, cwd=wt)
            if r.returncode == 0:
                r = git("commit", "-q", "-m", a.message.strip(), cwd=wt)
        else:
            r = git("merge", "--no-ff", "-q", "-m", (a.message or "").strip() or f"Merge {a.branch}", tip, cwd=wt)
        if r.returncode != 0:
            res["conflicts"] = [f for f in git("diff", "--name-only", "--diff-filter=U", cwd=wt).stdout.splitlines()]
            print(json.dumps(res, ensure_ascii=False))
            sys.exit(f"merge of {a.branch} into {base} failed in the rehearsal: {(r.stderr or r.stdout).strip()[:300]}"
                     "; nothing changed")
        new = git("rev-parse", "HEAD", cwd=wt).stdout.strip()
        res["merged"] = new
        if test.strip():
            t = subprocess.run(test, shell=True, cwd=str(wt / sub), capture_output=True, text=True)
            res["checks"] = "pass" if t.returncode == 0 else "fail"
            if t.returncode != 0:
                res["check_output"] = (t.stdout + t.stderr)[-2000:]
                print(json.dumps(res, ensure_ascii=False))
                sys.exit(f"checks failed on the rehearsed merge ({test}): nothing changed")
        else:
            res["checks"] = f"none ({a.no_test})"
        if not a.apply:
            res["note"] = f"rehearsal only: rerun with --apply to fast-forward {base} to the merged commit"
            print(json.dumps(res, ensure_ascii=False))
            return
        if holder:  # a checkout has the base: fast-forward it there, files and all (refused if base moved)
            r = git("merge", "--ff-only", "-q", new, cwd=holder)
        else:  # base not checked out here: move the ref, only if nobody moved it meanwhile
            r = git("update-ref", f"refs/heads/{base}", new, old)
        if r.returncode != 0:
            sys.exit(f"fast-forward of {base} failed ({r.stderr.strip()[:200]}); {base} is unchanged: was it moved "
                     "meanwhile? run merge again")
        res["applied"] = True
        log(d, {"ev": "merge", "branch": a.branch, "base": base, "from": old, "to": new, "checks": res["checks"]})
        print(json.dumps(res, ensure_ascii=False))
    finally:
        git("worktree", "remove", "--force", str(wt))
        shutil.rmtree(tmp, ignore_errors=True)
        git("worktree", "prune")


def main():
    p = argparse.ArgumentParser(
        prog="tw.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-C", metavar="PROJECT_FOLDER",
                   help="project folder to act on (always pass it: several projects can share one Remote Control folder)")
    s = p.add_subparsers(dest="cmd", required=True)

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
    x.add_argument("--status")
    x.add_argument("--notes")
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

    x = s.add_parser("plan", help="show, approve or reopen the plan")
    x.add_argument("action", choices=["show", "approve", "reopen"])
    x.add_argument("--user-said", metavar="WORDS", help="approve: the user's words agreeing to the plan (recorded)")
    x.set_defaults(f=cmd_plan)
    s.add_parser(
        "ready", help="approved tasks whose deps are done, and free slots"
    ).set_defaults(f=cmd_ready)
    x = s.add_parser("route", help="keyword hint: which thread owns this request?")
    x.add_argument("text")
    x.set_defaults(f=cmd_route)
    x = s.add_parser("ask", help="record a question waiting on the user, or --answer N")
    x.add_argument("text", nargs="?", default="")
    x.add_argument("--from", dest="frm", default="lead")
    x.add_argument("--need-thread", metavar="NAME", help="a thread's NEED-THREAD (message or STATUS need_thread line): "
                   "recorded once per (thread, name); a repeat is refused so it is not handled twice")
    x.add_argument("--answer", type=int, metavar="N", help="mark question N (as numbered on the board, from 1) "
                   "answered; the answer text is optional")
    x.set_defaults(f=cmd_ask)
    x = s.add_parser("memory", help="append a project memory line")
    x.add_argument("text")
    x.set_defaults(f=cmd_memory)
    x = s.add_parser("log", help="append a note to log.jsonl")
    x.add_argument("text")
    x.set_defaults(f=cmd_log)
    s.add_parser("board", help="render and print BOARD.md").set_defaults(f=cmd_board)
    x = s.add_parser("roster", help="print the roster block")
    x.add_argument("--thread")
    x.set_defaults(f=cmd_roster)
    s.add_parser("open", help="threads to sweep (not resolved/archived)").set_defaults(
        f=cmd_open
    )
    x = s.add_parser(
        "prefs", help="show or set coordination prefs: --set key=value ..."
    )
    x.add_argument("--set", nargs="*")
    x.add_argument("--user-said", metavar="WORDS", help="the user's words, needed to turn require_review off")
    x.set_defaults(f=cmd_prefs)
    x = s.add_parser(
        "worker-prompt",
        help="filled worker protocol -> create_session append_system_prompt",
    )
    x.add_argument("thread")
    x.set_defaults(f=cmd_worker_prompt)
    x = s.add_parser("req", help="user requests ledger: add / link / set / close / list / import")
    x.add_argument("action", choices=["add", "link", "set", "close", "drop", "list", "import"])
    x.add_argument("args", nargs="*", help='add: "<text>"; link: R<n> T1 [T2]; set/close: R<n>; import: <file.md>')
    x.add_argument("--id", help="add: explicit id (default next R<n>)")
    x.add_argument("--tasks", help="comma list of task ids to link")
    x.add_argument("--status", help="set: proposed | dispatched | delivered | reported (ending only via close/drop)")
    x.add_argument("--where"); x.add_argument("--followup"); x.add_argument("--text")
    x.add_argument("--user-said", metavar="WORDS", help="close/drop, and moves that are the user's decision: "
                   "the user's own words (recorded)")
    x.add_argument("--confirmed", action="store_true", help="close: kept for compatibility (needs --user-said)")
    x.add_argument("--declined", action="store_true", help="close --declined = drop (needs --user-said)")
    x.add_argument("--note")
    x.add_argument("--all", action="store_true", help="list: include closed")
    x.add_argument("--dry-run", action="store_true", help="import: show what would be imported")
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
    x.add_argument("--squash", action="store_true", help="one squash commit (needs --message starting with the task id)")
    x.add_argument("--message", help="merge or squash commit subject; keep the task id first (T3: ...)")
    x.add_argument("--test", help="check command, run in the rehearsal (default: prefs check_cmd)")
    x.add_argument("--no-test", metavar="WHY", help="nothing to check (docs only): say why")
    x.add_argument("--apply", action="store_true", help="fast-forward the base after a clean merge and green checks")
    x.set_defaults(f=cmd_merge)

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
