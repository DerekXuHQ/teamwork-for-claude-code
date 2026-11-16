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
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


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


def title_for(st, name):
    t = st["threads"][name]
    focus = t.get("focus") or ""
    return (
        f"[{st['project']}] {name} · {focus}" if focus else f"[{st['project']}] {name}"
    )


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
    order = ["waiting", "review", "working", "starting", "landing", "planned", "idle", "resolved"]
    out = []
    for g in order:
        names = [n for n, t in st["threads"].items() if t["status"] == g]
        if not names:
            continue
        out.append(f"**{THREAD_STATUSES[g]}**")
        for n in names:
            t = st["threads"][n]
            out.append(
                f"- {t.get('title') or n} ({t['kind']}) — {t.get('summary') or '-'}"
            )
    return "\n".join(out) or "(no threads yet)"


def render_board(st):
    approved = (
        "APPROVED"
        if st.get("plan_approved")
        else "DRAFT — waiting for the user's go-ahead"
    )
    r = st.get("repo", {})
    out = [
        f"# teamwork: {st['project']}",
        "",
        f"Goal: {st.get('goal') or '-'}",
        f"Plan: {approved}    Updated: {st.get('updated', '-')}",
        f"Folder: {r.get('path') or '-'}    Repo: {r.get('url') or '-'}    Base branch: {r.get('base') or 'main'}",
        f"Prefs: {json.dumps(st.get('prefs', {}), ensure_ascii=False)}",
        "",
        "## Overview",
        "",
        render_groups(st),
    ]
    open_q = [
        (i, q) for i, q in enumerate(st.get("questions", [])) if not q.get("answered")
    ]
    if open_q:
        out += ["", "## Waiting on the user", ""] + [
            f"- [{i}] ({q['from']}) {q['text']}" for i, q in open_q
        ]
    out += ["", "## Roster", "", render_roster(st), "", "## Plan", "", render_tasks(st)]
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ commands


def cmd_init(a):
    d = find_dir(create_at=a.path)
    if (d / "state.json").exists() and not a.force:
        sys.exit(f"{d}/state.json already exists (use --force to overwrite)")
    if not re.fullmatch(NAME_RE, a.project):
        sys.exit("project name: lowercase letters/digits/hyphen, max 24 chars")
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
        "prefs": {
            "max_parallel": 0,  # 0 = no limit; set only when the user asks for one
            "propose_before_start": True,
            "update_detail": "brief",
            "checkin_minutes": 10,
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
        st["threads"][name]["title"] = title_for(st, name)
        save(
            d,
            st,
            {"ev": "thread-add", "thread": name, "kind": a.kind, "session": a.session},
        )
        t = st["threads"][name]
        print(
            json.dumps(
                {
                    "name": name,
                    "title": t["title"],
                    "branch": t["branch"],
                    "tags": [f"teamwork:{st['project']}", f"teamwork-thread:{name}"],
                    "environment_id": st["envs"].get(a.kind),
                },
                ensure_ascii=False,
            )
        )
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
    print(
        json.dumps(
            [
                {
                    "thread": n,
                    "kind": t["kind"],
                    "session_id": t.get("session_id"),
                    "status": t["status"],
                    "last_seen": t.get("last_seen"),
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
    if a.set:
        save(d, st, {"ev": "prefs", "set": a.set})
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
    }
    out = tpl
    for k, v in fill.items():
        out = out.replace("{{" + k + "}}", v)
    left = re.findall(r"\{\{[A-Z_]+\}\}", out)
    if left:
        sys.exit(f"template placeholders not filled: {left}")
    print(out)


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
            "project": st["project"], "folder": str(f), "lead": st.get("lead", {}).get("session_id"),
            "waiting": count("waiting"), "review": count("review"), "working": count("starting", "working"),
            "threads": sum(1 for t in th if t["status"] != "archived"),
            "tasks_done": f"{sum(1 for k in tasks if k['status'] == 'done')}/{len(tasks)}",
            "questions": sum(1 for q in st.get("questions", []) if not q.get("answered")),
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
    text = "This folder is a teamwork project (skill: /teamwork). Board below; source of truth is .teamwork/state.json, " "changed only by the lead through tw.py.\n\n" + render_board(
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
