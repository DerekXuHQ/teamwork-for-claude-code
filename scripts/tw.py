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
        "req_reported": "reported", "req_closed": "closed", "followup": "follow-up",
        "no_threads": "(no threads yet)",
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
        "req_reported": "已回报", "req_closed": "已关闭", "followup": "回访",
        "no_threads": "（还没有线程）",
    },
}


def lab(st, key, **kw):
    code = (st.get("prefs", {}).get("language") or "en").strip().lower().split("-")[0]
    return LABELS.get(code, LABELS["en"]).get(key, LABELS["en"][key]).format(**kw)


REQ_STATUSES = ["open", "dispatched", "delivered", "reported", "closed"]
# Accepted when importing a markdown ledger (status column in either language).
REQ_STATUS_ALIASES = {"待办": "open", "已派": "dispatched", "已交付": "delivered", "已回报": "reported",
                      "已关闭": "closed", **{x: x for x in REQ_STATUSES}}


def unreported(st):
    return {r: q for r, q in st.get("requests", {}).items() if q["status"] == "delivered"}


def render_requests(st):
    reqs = [(r, q) for r, q in st.get("requests", {}).items() if q["status"] != "closed"]
    if not reqs:
        return None
    order = {"delivered": 0, "open": 1, "dispatched": 2, "reported": 3}
    reqs.sort(key=lambda x: (order.get(x[1]["status"], 9), int(x[0][1:]) if x[0][1:].isdigit() else 0))
    out = []
    for r, q in reqs:
        line = f"- **{r}** [{lab(st, 'req_' + q['status'])}] {q['text']}"
        extra = [", ".join(q.get("tasks", [])), q.get("where") or ""]
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
    st["updated"] = now()
    tmp = d / "state.json.tmp"
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(d / "state.json")
    (d / "BOARD.md").write_text(render_board(st), encoding="utf-8")
    if event:
        log(d, event)


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
            f"- {name} | {t['kind']} | session_id={t.get('session_id') or '(pending)'} | owns: {owns} | status={t['status']}{me}"
        )
    return "\n".join(lines)


def render_tasks(st, only_owner=None):
    rows = []
    for tid, k in st["tasks"].items():
        if only_owner and k.get("owner") != only_owner:
            continue
        deps = ",".join(k.get("deps", [])) or "-"
        rows.append(
            f"| {tid} | {k['title']} | {k.get('owner') or '-'} | {deps} | {k['status']} | {k.get('accept') or '-'} |"
        )
    if not rows:
        return "(no tasks)"
    return (
        "| id | task | owner | deps | status | done when |\n|---|---|---|---|---|---|\n"
        + "\n".join(rows)
    )


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
            f"- [{i}] ({q['from']}) {q['text']}" for i, q in open_q
        ]
    out += ["", f"## {lab(st, 'roster')}", "", render_roster(st), "", f"## {lab(st, 'plan')}", "", render_tasks(st)]
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ commands


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
            "base": a.base,
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
            "stuck_minutes": STUCK_DEFAULT,  # working thread silent this long -> "possibly stuck"; 0 = off
            "language": "",
            "thread_permission_mode": "",  # create_session permission_mode; empty = the lead's own mode  # conversation language code (zh, en, ...); empty = follow the user's latest message
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
    if not gi.exists():
        gi.write_text("state.json.tmp\n", encoding="utf-8")
    save(d, st, {"ev": "init", "project": a.project})
    print(f"initialised {d}")


def cmd_lead(a):
    d = need(find_dir())
    st = load(d)
    old = st["lead"].get("session_id")
    if a.session and old and old != a.session and not a.force:
        sys.exit(f"project {st['project']} already has lead {old}. One lead per project: check that session with "
                 "get_session; if it is archived or the user says to take over, re-run with --force.")
    if a.session:
        st["lead"]["session_id"] = a.session
    if a.rc_env:
        st["envs"]["rc"] = a.rc_env
    if a.cloud_env:
        st["envs"]["cloud"] = a.cloud_env
    if a.repo_url:
        st["repo"]["url"] = a.repo_url
    save(d, st, {"ev": "lead", "session": a.session, "envs": st["envs"]})
    print(
        json.dumps(
            {"lead": st["lead"], "envs": st["envs"], "repo": st["repo"]},
            ensure_ascii=False,
        )
    )


def cmd_thread(a):
    d = need(find_dir())
    st = load(d)
    name = a.name
    if a.action == "add":
        if not re.fullmatch(NAME_RE, name):
            sys.exit(
                "thread name: lowercase letters/digits/hyphen, max 24 chars (e.g. ui, api, research-2)"
            )
        if name == "lead" or name in st["threads"]:
            sys.exit(f"thread name {name} is taken")
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
        }
        if a.kind == "rc":
            st["threads"][name]["worktree"] = str(worktree_path(st, name))
        st["threads"][name]["title"] = title_for(st, name)
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
    if name not in st["threads"]:
        sys.exit(f"unknown thread {name}; known: {', '.join(st['threads']) or '-'}")
    t = st["threads"][name]
    if a.session:
        t["session_id"] = a.session
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
    t["last_seen"] = now()
    save(
        d,
        st,
        {
            "ev": "thread-set",
            "thread": name,
            "status": t["status"],
            "summary": t["summary"],
        },
    )
    print(json.dumps({name: t}, ensure_ascii=False, indent=2))


def cmd_task(a):
    d = need(find_dir())
    st = load(d)
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
            "status": "approved" if st["plan_approved"] else "planned",
            "notes": "",
            "created": now(),
        }
        save(d, st, {"ev": "task-add", "task": a.id, "owner": a.owner})
    else:
        if a.id not in st["tasks"]:
            sys.exit(f"unknown task {a.id}")
        k = st["tasks"][a.id]
        if a.status:
            if a.status not in TASK_STATUSES:
                sys.exit(f"status must be one of {TASK_STATUSES}")
            k["status"] = a.status
            if a.status == "done":
                for r in deliver_ready(st):
                    print(f"request {r} delivered (all its tasks are done): report it to the user, then "
                          f"`tw.py req set {r} --status reported`", file=sys.stderr)
        if a.owner is not None:
            if a.owner and a.owner not in st["threads"]:
                sys.exit(f"unknown owner {a.owner}")
            k["owner"] = a.owner or None
        if a.title:
            k["title"] = a.title
        if a.accept is not None:
            k["accept"] = a.accept
        if a.deps is not None:
            k["deps"] = csv(a.deps)
        if a.notes is not None:
            k["notes"] = a.notes
        save(
            d,
            st,
            {
                "ev": "task-set",
                "task": a.id,
                "status": k["status"],
                "owner": k["owner"],
            },
        )
    print(render_tasks(st))


def cmd_plan(a):
    d = need(find_dir())
    st = load(d)
    if a.action == "approve":
        st["plan_approved"] = True
        for k in st["tasks"].values():
            if k["status"] == "planned":
                k["status"] = "approved"
        save(d, st, {"ev": "plan-approved"})
    elif a.action == "reopen":
        st["plan_approved"] = False
        save(d, st, {"ev": "plan-reopened"})
    print(render_board(st))


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
    print(
        json.dumps(
            {
                "ready": ready,
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
        st["questions"][a.answer]["answered"] = True
        save(d, st, {"ev": "answered", "q": a.answer})
    else:
        st["questions"].append(
            {"from": a.frm, "text": a.text, "t": now(), "answered": False}
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
    for kv in a.set or []:
        k, _, v = kv.partition("=")
        cur = st["prefs"].get(k)
        if isinstance(cur, bool):
            st["prefs"][k] = v.lower() in ("1", "true", "yes", "on")
        elif v.isdigit():
            st["prefs"][k] = int(v)
        else:
            st["prefs"][k] = v
        if k == "thread_permission_mode" and v and v not in PERMISSION_MODES:
            sys.exit(f"thread_permission_mode: one of {', '.join(PERMISSION_MODES)}, or empty for the lead's own mode")
        if k == "short_name" and v and not re.fullmatch(SHORT_RE, v):
            sys.exit("short_name: letters/digits/hyphen, max 6 chars (e.g. TFCC)")
    retitled = {}
    if any(kv.partition("=")[0] == "short_name" for kv in a.set or []):
        for n, t in st["threads"].items():
            new = title_for(st, n)
            if t.get("title") != new:
                t["title"] = new
                retitled[n] = new
    if a.set:
        save(d, st, {"ev": "prefs", "set": a.set})
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
        if q["status"] in ("open", "dispatched") and ts and all(
                st["tasks"].get(t, {}).get("status") == "done" for t in ts):
            q["status"] = "delivered"
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


def set_req_status(st, r, q, new, by_import=False):
    if new not in REQ_STATUSES:
        sys.exit(f"status must be one of {REQ_STATUSES}")
    if by_import:
        q["status"] = new
        return
    if new == "closed":
        sys.exit("use `req close R --confirmed` (user confirmed) or `--declined` (user said not to do it)")
    if new == "reported" and q["status"] not in ("delivered", "reported"):
        sys.exit(f"{r} is {q['status']}: only a delivered request can be reported (deliver first)")
    q["status"] = new
    if new == "reported":
        q["reported_at"] = now()


def cmd_req(a):
    d = need(find_dir())
    st = load(d)
    reqs = st.setdefault("requests", {})
    if a.action == "list":
        rows = {r: q for r, q in reqs.items() if a.all or q["status"] != "closed"}
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
        if q["status"] == "open":
            q["status"] = "dispatched"
        deliver_ready(st)
    elif a.action == "set":
        if a.status:
            set_req_status(st, r, q, a.status)
        for k in ("where", "followup", "text"):
            v = getattr(a, k)
            if v is not None:
                q[k] = v
    elif a.action == "close":
        if a.confirmed == a.declined:
            sys.exit("say why: --confirmed (the user confirmed it is done) or --declined (the user said not to do it)")
        if a.confirmed and q["status"] != "reported":
            sys.exit(f"{r} is {q['status']}: report it to the user first (`req set {r} --status reported`), "
                     "then close it when they confirm")
        q["status"] = "closed"
        q["closed_reason"] = "confirmed" if a.confirmed else "declined"
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
        if status == "closed":
            q["closed_reason"] = "imported"
        out[r] = q
    if a.dry_run:
        print(json.dumps({"would_import": out, "skipped_existing": skipped}, ensure_ascii=False, indent=2))
        return
    reqs.update(out)
    save(d, st, {"ev": "req-import", "file": a.args[0], "ids": list(out), "skipped": skipped})
    print(json.dumps({"imported": list(out), "skipped_existing": skipped}, ensure_ascii=False))


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
    x.add_argument("--base", default="main")
    x.add_argument("--force", action="store_true")
    x.add_argument("--short", help="short name, max 6 chars, used in titles/headers/tags (default: initials, e.g. TFCC)")
    x.set_defaults(f=cmd_init)

    x = s.add_parser("lead", help="record lead session id, environment ids, repo url")
    x.add_argument("--session")
    x.add_argument("--rc-env")
    x.add_argument("--cloud-env")
    x.add_argument("--repo-url")
    x.add_argument("--force", action="store_true", help="take over from a previous lead session")
    x.set_defaults(f=cmd_lead)

    x = s.add_parser("thread", help="add or update a thread")
    x.add_argument("action", choices=["add", "set"])
    x.add_argument("name")
    x.add_argument("--kind", choices=["rc", "cloud"], default="rc")
    x.add_argument("--focus")
    x.add_argument("--owns")
    x.add_argument("--session")
    x.add_argument("--branch")
    x.add_argument("--status")
    x.add_argument("--summary")
    x.add_argument("--from-status", action="store_true",
                   help="this update comes from a report the thread sent (tw-status block or message): resets stuck timer")
    x.set_defaults(f=cmd_thread)

    x = s.add_parser("task", help="add or update a plan task")
    x.add_argument("action", choices=["add", "set"])
    x.add_argument("id")
    x.add_argument("--title")
    x.add_argument("--owner")
    x.add_argument("--deps")
    x.add_argument("--accept")
    x.add_argument("--status")
    x.add_argument("--notes")
    x.set_defaults(f=cmd_task)

    x = s.add_parser("plan", help="show, approve or reopen the plan")
    x.add_argument("action", choices=["show", "approve", "reopen"])
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
    x.add_argument("--answer", type=int)
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
    x.set_defaults(f=cmd_prefs)
    x = s.add_parser(
        "worker-prompt",
        help="filled worker protocol -> create_session append_system_prompt",
    )
    x.add_argument("thread")
    x.set_defaults(f=cmd_worker_prompt)
    x = s.add_parser("req", help="user requests ledger: add / link / set / close / list / import")
    x.add_argument("action", choices=["add", "link", "set", "close", "list", "import"])
    x.add_argument("args", nargs="*", help='add: "<text>"; link: R<n> T1 [T2]; set/close: R<n>; import: <file.md>')
    x.add_argument("--id", help="add: explicit id (default next R<n>)")
    x.add_argument("--tasks", help="comma list of task ids to link")
    x.add_argument("--status", help="set: open | dispatched | delivered | reported (closed only via close)")
    x.add_argument("--where"); x.add_argument("--followup"); x.add_argument("--text")
    x.add_argument("--confirmed", action="store_true", help="close: the user confirmed it is done")
    x.add_argument("--declined", action="store_true", help="close: the user said not to do it")
    x.add_argument("--note")
    x.add_argument("--all", action="store_true", help="list: include closed")
    x.add_argument("--dry-run", action="store_true", help="import: show what would be imported")
    x.set_defaults(f=cmd_req)
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
