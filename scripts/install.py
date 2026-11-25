#!/usr/bin/env python3
"""Install the teamwork skill on the computer that runs the Remote Control server.

Run it from an RC session (the user never needs a terminal).

  python3 install.py --link              # show what would change (dry run)
  python3 install.py --link --apply      # install skill + user permission rules (allow, scratch folders, ask)
  python3 install.py --link --apply --project <project folder> --served <served folder>
                                         # also add the SessionStart hook to the project and the served folder

--link (recommended) or copy:
  --link  <config dir>/skills/teamwork becomes a symlink to this clone. Every new session runs the
          clone's current checkout: upgrade with `git pull`, and whatever lands on the checked-out
          branch is live at once (so keep that branch working).
  copy    (no --link) a frozen snapshot of the skill files, without .git, .teamwork, .claude and
          other local state. Nothing changes until you upgrade: delete <config dir>/skills/teamwork
          and re-run the installer (it never overwrites an existing skills/teamwork).

Permission rules go in <config dir>/settings.json (user level: every session on this computer reads them, not only
teamwork ones). additionalDirectories lets sessions read and cd in the scratch folders without asking; edits there
still ask in default mode. The ask rules are a safety net matched on command text, not a security boundary.
Cloud sessions never read this file.

With --project/--served, the folders sessions start in (RC threads start in the served folder) get the folder rule
set in .claude/settings.local.json, as do the project's worktrees: every ordinary tool allowed, defaultMode auto, big
decisions asked, only wiping / or ~ denied (references/setup.md section 2). --default-mode also sets the user-level
default mode.

Never removes anything. Existing settings are merged, and a .bak copy is written before any settings file changes.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
HOME = Path.home()
# Claude Code reads CLAUDE_CONFIG_DIR when set (a Remote Control server may run with e.g. ~/.claude-b).
CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (HOME / ".claude")).expanduser()
DEST = CONFIG_DIR / "skills" / "teamwork"
USER_SETTINGS = CONFIG_DIR / "settings.json"
ALLOW = [
    "mcp__claude-code-remote__send_message",
    "mcp__claude-code-remote__get_session",
    "mcp__claude-code-remote__list_events",
    "mcp__claude-code-remote__list_sessions",
]
def hook_python():
    """The interpreter hooks run with, as an absolute path (T66). A bare `python3`, or `/usr/bin/env python3`, is looked
    up on the PATH the hook gets, which can be a short one (a Remote Control server started by launchd) where it finds
    an older system Python that cannot read tw.py; the bash-guard hook then fails silently. Candidates: the stable
    `python3` link when it is the running interpreter, the running one (outside a venv: its base interpreter, so a
    deleted venv cannot break the hooks), then `python3` on the PATH. The first that can run tw.py wins."""
    exe = sys.executable or ""
    if exe and sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        exe = getattr(sys, "_base_executable", "") or exe
    link = shutil.which("python3") or ""
    cands = []
    if link and exe and os.path.realpath(link) == os.path.realpath(exe):
        cands.append(link)
    cands += [c for c in (exe, link) if c and c not in cands]
    tw = SRC / "scripts" / "tw.py"
    for c in cands:
        try:
            if subprocess.run([c, str(tw), "--help"], capture_output=True, timeout=30).returncode == 0:
                return c
        except (OSError, subprocess.SubprocessError):
            pass
    print(f"  warning: no python3 found that can run tw.py (tried {', '.join(cands) or 'none'}); hooks may fail",
          file=sys.stderr)
    return cands[0] if cands else "python3"


PY = hook_python()
HOOK_CMD = f'"{PY}" "{DEST / "scripts" / "tw.py"}" context'


def _tw_hook(cmd, word):
    """Our hook command, written by any install.py version: `python3 ...` (bare, older) or `"<abs python>" ...`."""
    if not isinstance(cmd, str): return False
    tail = f'"{DEST / "scripts" / "tw.py"}" {word}'
    head = cmd.split(tail, 1)[0] if tail in cmd else None
    return head is not None and (head == "python3 " or
                                 (len(head) > 3 and head[0] == '"' and head.endswith('" ') and '"' not in head[1:-2]))


def temp_dirs(notes=None):
    """Folders sessions use for scratch work (reviews, test repos): /tmp and $TMPDIR, also in their resolved form
    (/private/tmp, /private/var/... on macOS). Inside them, reading and cd never prompt (and, in acceptEdits or
    auto, edits do not either)."""
    out = []
    for p in ("/tmp", os.environ.get("TMPDIR") or ""):
        if not p:
            continue
        if not os.path.isabs(p):
            notes is not None and notes.append(f"TMPDIR={p} is not an absolute path: skipped")
            continue
        p = os.path.normpath(p)
        if not Path(p).is_dir():
            notes is not None and notes.append(f"{p} is not a folder here: skipped")
            continue
        r = os.path.normpath(str(Path(p).resolve()))
        if r == "/" or Path(r) == HOME.resolve() or HOME.resolve().is_relative_to(Path(r)):
            notes is not None and notes.append(f"{p} is the root or holds the home folder: skipped")
            continue
        out += [p, r]
    return list(dict.fromkeys(out))


# Always asked, whatever the permission mode (an explicit ask rule beats allow rules, auto and bypassPermissions):
# irreversible or public actions are the user's own call (SKILL Rules). Rules match the command text, so each
# common spelling is listed; this is a safety net against accidents, not a security boundary.
ASK = [
    "Bash(gh repo delete:*)",
    "Bash(gh repo archive:*)",
    "Bash(gh repo rename:*)",
    "Bash(gh repo edit *--visibility*)",
    "Bash(gh api *-X DELETE*)",
    "Bash(gh api *-XDELETE*)",
    "Bash(gh api *--method DELETE*)",
    "Bash(gh api *--method=DELETE*)",
    "Bash(gh api *deleteRepository*)",
    "Bash(gh api *visibility=*)",
    "Bash(gh api *private=*)",
    "Bash(gh api *default_branch=*)",
    "Bash(gh api *-X=DELETE*)",
    "Bash(gh repo edit *--default-branch*)",
]
# No git push rules: threads force-push their own branches after a rebase every day, and a local rule cannot tell
# those apart from the base branch reliably. Deleting or force-pushing the base branch is blocked on the host
# (GitHub: a branch rule or ruleset with "block force pushes" and "restrict deletions"); install.py says so.
HOST_PROTECTION = ("protect the base branch on the host: GitHub > Settings > Rules (or Branches) > a rule for main "
                   "with \"Block force pushes\" and \"Restrict deletions\" turned on")


# Rules for the folders sessions start in (the Remote Control served folder, the project, each worktree): RC threads
# start in the served folder, so its .claude/settings.local.json is what decides their prompts. Every session runs in
# auto (R45): ordinary tools, folders and connectors never ask; only big decisions do (removals, history rewrites,
# discards, reinstalls, shutdowns, deleting repos, archiving sessions, changing environments); only wiping the root or
# home folder is denied outright. Some shapes are spelled in parts so this file never trips other plugins' guards.
_RF, _FR, _F, _HARD, _FORCE = "-r" + "f", "-f" + "r", "-" + "f", "--" + "hard", "--for" + "ce"


# Big decisions in Bash (removals, history rewrites, discards, shutdowns, uninstalls, dropping data) are judged by a
# PreToolUse hook, `tw.py bash-guard`, that reads each command's argv: permission rules match raw text with `*`
# spanning spaces, so a rule cannot tell a forced push from a commit message that mentions it.
FOLDER_ALLOW = ["Bash", "Edit", "Write", "Read", "Glob", "Grep", "NotebookEdit", "WebFetch", "WebSearch", "Agent",
                "Skill", "ToolSearch", "TodoWrite", "Monitor", "TaskStop", "SendUserFile", "CronCreate", "CronDelete",
                "CronList", "ScheduleWakeup", "EnterWorktree", "ExitWorktree", "mcp__claude_ai_Claude_Docs__*",
                "mcp__claude-code-remote__*"] + ALLOW
FOLDER_ASK = [  # tools; Bash big decisions go through the hook
    # sessions, schedules, documents, environments
    "mcp__claude-code-remote__archive_session", "mcp__claude-code-remote__delete_trigger",
    "mcp__claude-code-remote__update_environment", "mcp__claude-code-remote__create_environment",
    "mcp__claude_ai_Claude_Docs__delete", "mcp__claude_ai_Google_Drive__trash_file",
] + ASK
# Only wiping the root or the home folder is denied (the hook denies it too, in any spelling; these rules hold even
# where the hook is not installed). A rule for a recursive delete of `/*` cannot exist: `*` in a rule matches anything, so
# it would deny every absolute path; the hook denies those spellings instead.
FOLDER_DENY = [f"Bash(rm {x} {t})" for x in (_RF, _FR, "-R" + "f", "-f" + "R", "-r " + _F, "-R " + _F)
               for t in ("/", "/ *", "~", "~/", "$HOME", "$HOME/", '"$HOME"', '"$HOME"/', "${HOME}", "${HOME}/")]


def guard_hook():
    """`|| true`: exit code 2 from a PreToolUse hook blocks the call, and python exits 2 when the file is missing or
    the clone is too old to know `bash-guard`; the guard must never block ordinary work. Its decision is on stdout."""
    return f'"{PY}" "{DEST / "scripts" / "tw.py"}" bash-guard || true'


def _is_guard(cmd):
    return _tw_hook(cmd, "bash-guard")


def superseded(asks):
    """Ask rules for Bash that the hook already asks about (forced pushes, `Bash(rm -r *)`, ...): as text
    rules they also fire on words in commit messages, so with the hook in place they only add false prompts."""
    sys.path.insert(0, str(SRC / "scripts"))
    import tw  # noqa: E402 - the same parser the hook runs
    out = []
    for r in asks:
        if not (isinstance(r, str) and r.startswith("Bash(") and r.endswith(")")) or r in FOLDER_ASK:
            continue
        inner = r[5:-1]
        inner = inner[:-2] if inner.endswith(":*") else inner
        text = inner.replace("*", " x ").strip()
        if text.startswith("x "):
            continue  # a leading wildcard matches anything; noted separately
        if tw.big_decision(text):
            out.append(r)
    return out


def folder_dirs(project, served):
    """additionalDirectories for the folder rules: where sessions read and write without asking."""
    out = [str(Path(x).expanduser().resolve()) for x in (served, project) if x]
    if project:
        pj = Path(project).expanduser().resolve()
        out.append(str(pj.parent / f"{pj.name}-worktrees"))
    out.append(str(DEST.parent))
    return list(dict.fromkeys(out + temp_dirs()))


def folder_rules(pl, sl, a, dirs):
    """Merge the folder rule set into one settings.local.json: the user's own rules stay, nothing is replaced."""
    if not isinstance(sl, dict) or not isinstance(sl.setdefault("permissions", {}), dict):
        raise SystemExit(f"  {pl}: 'permissions' is not an object; fix the file by hand, then re-run")
    perms = sl["permissions"]
    for key, want in ((("allow", FOLDER_ALLOW), ("additionalDirectories", dirs)) if not a.no_allow else ()) + \
            (("ask", FOLDER_ASK), ("deny", FOLDER_DENY)):
        added = merge_rules(perms, key, want, pl)
        print(f"  {pl.name} {key}: " + (f"adding {len(added)}" if added else "nothing to add"))
    old = superseded(perms.get("ask", []))
    if old and a.drop_superseded:
        perms["ask"] = [r for r in perms["ask"] if r not in old]
        print(f"  {pl.name} ask: removing {len(old)} rules the hook now judges (a .bak copy is kept): "
              + ", ".join(old))
    elif old:
        print(f"  {pl.name} ask: {len(old)} older text rules the hook now judges also fire on words in commit "
              f"messages: {', '.join(old)}. Remove them with --drop-superseded (the user's decision)")
    wild = [r for r in perms.get("ask", []) if isinstance(r, str) and r.startswith("Bash(*")]
    if wild:
        print(f"  {pl.name} ask: note: {', '.join(wild)} start with a wildcard and match any command that mentions "
              "the word, commit messages included")
    pre = sl.setdefault("hooks", {}).setdefault("PreToolUse", [])
    mine = [h for g in pre for h in g.get("hooks", []) if _is_guard(h.get("command"))]
    if not mine:
        pre.append({"matcher": "Bash,mcp__claude-code-remote__archive_session", "hooks": [{"type": "command", "command": guard_hook()}]})
        print(f"  {pl.name} PreToolUse hook: adding tw.py bash-guard for Bash and archive_session")
    else:
        stale = [h for h in mine if h.get("command") != guard_hook()]
        for h in stale:
            h["command"] = guard_hook()
        for group in pre:
            if any(h in mine for h in group.get("hooks", [])):
                group["matcher"] = "Bash,mcp__claude-code-remote__archive_session"
        print(f"  {pl.name} PreToolUse hook: " + ("updating to an absolute python path" if stale else "already present"))
    pre = sl.setdefault("hooks", {}).setdefault("PreToolUse", [])
    for group in pre:
        if group.get("matcher") == "mcp__claude-code-remote__archive_session":
            group["hooks"] = [h for h in group.get("hooks", [])
                              if not (isinstance(h.get("command"), str) and "tw.py\" archive-guard" in h["command"])]
    pre[:] = [g for g in pre if g.get("matcher") != "mcp__claude-code-remote__archive_session" or g.get("hooks")]
    if not a.no_allow:
        if perms.get("defaultMode") in (None, "", "auto"):
            perms["defaultMode"] = "auto"  # R45: every session there, the lead included, runs in auto
        else:
            print(f"  {pl.name} defaultMode stays {perms['defaultMode']} (the user's own setting); auto is what "
                  "teamwork needs")
    write(pl, sl, a.apply)


def merge_rules(perms, key, want, where=""):
    have = perms.setdefault(key, [])
    if not isinstance(have, list):
        raise SystemExit(f"  {where}: permissions.{key} is not a list; fix the file by hand, then re-run")
    added = [r for r in dict.fromkeys(want) if r not in have]
    have.extend(added)
    return added


def edit_allow(project):
    """Edits inside the project folder and the rc threads' worktrees (`//` = absolute path in a permission rule)."""
    pj = Path(project).expanduser().resolve()
    return [f"{tool}(/{root}/**)" for root in (str(pj), str(pj.parent / f"{pj.name}-worktrees"))
            for tool in ("Edit", "Write")]


def tw_allow():
    """The guard command worker-prompt prints for rc threads (and the lead's TW calls), in both spellings of the
    skill folder: without it a thread in default mode is asked before every push."""
    out = []
    for root in dict.fromkeys([str(SRC), str(DEST), str(DEST.resolve())]):
        out += [f'Bash(python3 "{root}/scripts/tw.py" *)', f"Bash(python3 {root}/scripts/tw.py *)"]
    return out


# Never part of a copy install: version control, per-project teamwork state, the clone's own
# Claude Code settings, editor/OS/build leftovers.
COPY_SKIP = (
    ".git", ".github", ".gitignore", ".gitattributes", ".teamwork", ".claude",
    "__pycache__", "*.pyc", ".pytest_cache", ".venv", "venv", "node_modules",
    ".DS_Store", ".idea", ".vscode", "*.bak", "*.swp",
)


def _git(cwd, *x):
    import subprocess
    return subprocess.run(["git", "-C", str(cwd), *x], capture_output=True, text=True)


def git_exposed(p):
    """(repo top, common git dir) if p lies inside a git work tree and is not ignored: anything written there
    (absolute home paths, user names) could be committed by the next `git add -A`. None otherwise."""
    d = Path(p).parent
    while not d.exists() and d != d.parent:
        d = d.parent
    top = _git(d, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or not top.stdout.strip():
        return None
    top = Path(top.stdout.strip()).resolve()
    rel = os.path.relpath(Path(p).resolve(), top)
    if rel.startswith(".."):
        return None
    if _git(top, "check-ignore", "-q", "--no-index", rel).returncode == 0:
        return None
    common = _git(top, "rev-parse", "--git-common-dir").stdout.strip()
    return top, (top / common).resolve()


def exclude_locally(top, common, rels, apply):
    """Add paths to <git dir>/info/exclude: ignored in this clone only, nothing committed."""
    ex = common / "info" / "exclude"
    have = ex.read_text(encoding="utf-8").splitlines() if ex.exists() else []
    new = [r for r in rels if r not in have]
    if new:
        print(f"  {'adding' if apply else 'would add'} to {ex} (local ignore, never committed): {', '.join(new)}")
        if apply:
            ex.parent.mkdir(parents=True, exist_ok=True)
            with open(ex, "a", encoding="utf-8") as f:
                f.write(("" if not have or have[-1] == "" else "\n") + "\n".join(new) + "\n")


def load(p):
    if p.exists():
        txt = p.read_text(encoding="utf-8").strip()
        try:
            return json.loads(txt) if txt else {}
        except ValueError as e:
            raise SystemExit(f"  {p} is not valid JSON ({e}); fix the file by hand, then re-run")
    return {}


def write(p, data, apply):
    new = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    old = p.read_text(encoding="utf-8") if p.exists() else ""
    if new == old:
        print(f"  unchanged: {p}")
        return
    print(f"  {'writing' if apply else 'would write'}: {p}")
    if apply:
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            shutil.copy2(p, p.with_suffix(p.suffix + ".bak"))
        p.write_text(new, encoding="utf-8")


def main():
    global CONFIG_DIR, DEST, USER_SETTINGS, HOOK_CMD
    detected = (
        f"from $CLAUDE_CONFIG_DIR={os.environ['CLAUDE_CONFIG_DIR']}"
        if os.environ.get("CLAUDE_CONFIG_DIR")
        else "default ~/.claude, because $CLAUDE_CONFIG_DIR is not set"
    )
    ap = argparse.ArgumentParser(
        description=__doc__,
        epilog=f"Detected config dir: {CONFIG_DIR} ({detected}). Override with --config-dir.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--apply", action="store_true", help="actually write (default is a dry run)"
    )
    ap.add_argument("--config-dir", help="Claude Code config dir (default: $CLAUDE_CONFIG_DIR or ~/.claude)")
    ap.add_argument(
        "--link",
        action="store_true",
        help="symlink the skill to this clone (recommended): live on every pull. Without it: a frozen copy",
    )
    ap.add_argument("--project", help="project folder to receive the SessionStart hook")
    ap.add_argument("--served", help="folder the Remote Control server serves (RC sessions start here); also gets the hook")
    ap.add_argument(
        "--pre-push",
        action="store_true",
        help="also install a git pre-push hook in --project's repo that runs `tw.py guard` (blocks pushes that "
        "contain words or paths from .teamwork/PRIVATE.md); never replaces an existing hook",
    )
    ap.add_argument(
        "--no-allow",
        action="store_true",
        help="skip the allow rules and additionalDirectories in <config dir>/settings.json (the ask rules are "
             "always written)",
    )
    ap.add_argument(
        "--drop-superseded",
        action="store_true",
        help="in the folder settings, remove older Bash ask rules that the bash-guard hook now judges (they also "
             "fire on words in commit messages); the dry run lists them first; a .bak copy is kept",
    )
    ap.add_argument(
        "--default-mode",
        choices=["auto", "acceptEdits", "default"],
        help="also set permissions.defaultMode in <config dir>/settings.json, the mode every new session there starts "
             "in (Remote Control threads too); auto is what teamwork needs so ordinary calls never ask. The user's "
             "decision: it changes every session on this computer",
    )
    ap.add_argument(
        "--no-auto-memory",
        action="store_true",
        help="also set autoMemoryEnabled=false in the project",
    )
    a = ap.parse_args()
    if a.config_dir:
        CONFIG_DIR = Path(os.path.abspath(Path(a.config_dir).expanduser()))  # hooks run from any folder
        DEST = CONFIG_DIR / "skills" / "teamwork"
        USER_SETTINGS = CONFIG_DIR / "settings.json"
        HOOK_CMD = f'"{PY}" "{DEST / "scripts" / "tw.py"}" context'
    print(f"Claude Code config dir: {CONFIG_DIR}"
          + ("" if os.environ.get("CLAUDE_CONFIG_DIR") or a.config_dir else "  (CLAUDE_CONFIG_DIR not set; check it matches the one your Remote Control server uses)"))

    # Check every settings file this run will touch before writing any: a broken one stops it here, not half-way.
    targets = [x for x in (a.project, a.served) if x]
    checks = [USER_SETTINGS] + [Path(f).expanduser().resolve() / ".claude" / n for f in targets
                                for n in ("settings.json", "settings.local.json")]
    if a.project:
        pj = Path(a.project).expanduser().resolve()
        wroot = pj.parent / f"{pj.name}-worktrees"
        checks += [w / ".claude" / "settings.local.json" for w in (sorted(wroot.iterdir()) if wroot.is_dir() else [])
                   if (w / ".git").exists()]
    for c in checks:
        data = load(c)
        perms = data.get("permissions", {}) if isinstance(data, dict) else None
        hooks = data.get("hooks", {}) if isinstance(data, dict) else None
        groups = [g for k in ("SessionStart", "PreToolUse") for g in (hooks.get(k, []) if isinstance(hooks, dict)
                                                                       and isinstance(hooks.get(k, []), list) else [])]
        if not isinstance(data, dict) or not isinstance(perms, dict) or any(
                not isinstance(perms.get(k, []), list) for k in ("allow", "ask", "deny", "additionalDirectories")) \
                or not isinstance(hooks, dict) or any(not isinstance(hooks.get(k, []), list)
                                                      for k in ("SessionStart", "PreToolUse")) \
                or any(not isinstance(g, dict) or not isinstance(g.get("hooks", []), list)
                       or any(not isinstance(h, dict) for h in g.get("hooks", [])) for g in groups):
            raise SystemExit(f"{c}: not a settings object with list rules; fix the file by hand, then re-run "
                             "(nothing was written)")
    print("1. skill files")
    if DEST.exists() and DEST.resolve() == SRC:
        print(f"  already in place: {DEST}")
    elif DEST.exists() or DEST.is_symlink():
        print(f"  {DEST} already exists: left untouched (remove it yourself to reinstall)")
    elif a.link:
        print(f"  {'linking' if a.apply else 'would link'} {DEST} -> {SRC}")
        if a.apply:
            DEST.parent.mkdir(parents=True, exist_ok=True)
            DEST.symlink_to(SRC, target_is_directory=True)
    elif DEST.resolve() == SRC or SRC in DEST.resolve().parents:
        print(f"  {DEST} is inside the clone {SRC}: refusing to copy into itself; pick another --config-dir")
    else:
        print(f"  {'copying' if a.apply else 'would copy'} {SRC} -> {DEST}")
        print(f"  (snapshot, skipping {', '.join(COPY_SKIP)}; to upgrade later, delete {DEST} and re-run."
              " --link is recommended instead)")
        if a.apply:
            shutil.copytree(SRC, DEST, ignore=shutil.ignore_patterns(*COPY_SKIP))

    print("2. permissions in user settings" + (" (--no-allow: only the ask rules)" if a.no_allow else ""))
    s = load(USER_SETTINGS)
    if not isinstance(s, dict) or not isinstance(s.setdefault("permissions", {}), dict):
        raise SystemExit(f"  {USER_SETTINGS}: 'permissions' is not an object; fix the file by hand, then re-run")
    perms = s["permissions"]
    notes = []
    dirs = temp_dirs(notes)
    if a.project:  # rc threads' worktrees sit next to the project folder, maybe outside the served folder
        pj = Path(a.project).expanduser().resolve()
        dirs.append(str(pj.parent / f"{pj.name}-worktrees"))
    wanted = [("ask", ASK, "always asked: deleting, archiving or renaming repos, visibility, default branch")]
    if not a.no_allow:
        wanted = [
            ("allow", ALLOW + tw_allow() + (edit_allow(a.project) if a.project else []),
             "reporting calls, tw.py" + (", edits in the project and its worktrees" if a.project else "")
             + ": ordinary work never asks"),
            ("additionalDirectories", dirs, "scratch folders and worktrees: reads and cd there never ask"),
        ] + wanted
    for n in notes:
        print(f"  note: {n}")
    for key, want, why in wanted:
        have = perms.setdefault(key, [])
        if not isinstance(have, list):
            raise SystemExit(f"  {USER_SETTINGS}: permissions.{key} is not a list; fix the file by hand, then re-run")
        same = (lambda x: os.path.normpath(x) if isinstance(x, str) else x) if key == "additionalDirectories" \
            else (lambda x: x)
        known = {same(h) for h in have if isinstance(h, str)}
        added = [r for r in dict.fromkeys(want) if same(r) not in known]
        have.extend(added)
        print(f"  {key}: {'adding ' + ', '.join(added) if added else 'nothing to add (already present)'}  ({why})")
    if a.default_mode:
        was = perms.get("defaultMode")
        perms["defaultMode"] = a.default_mode
        print(f"  defaultMode: {a.default_mode}" + (f" (was {was})" if was and was != a.default_mode else
                                                     " (already set)" if was == a.default_mode else ""))
    elif perms.get("defaultMode") not in ("auto", "bypassPermissions"):
        print(f"  note: defaultMode is {perms.get('defaultMode') or 'not set (default)'}: new sessions, the lead "
              "included, start there and ask more; --default-mode auto changes it (the user's decision)")
    write(USER_SETTINGS, s, a.apply)
    print(f"  also (the user, once per repo): {HOST_PROTECTION}")

    targets = [x for x in (a.project, a.served) if x]
    for i, folder in enumerate(dict.fromkeys(targets)):
        print(f"3.{i + 1} SessionStart hook in {folder}")
        ps = Path(folder).expanduser().resolve() / ".claude" / "settings.json"
        exposed = git_exposed(ps)
        if exposed:
            # The hook command holds absolute paths (home folder, user name): keep it out of anything committable.
            top, common = exposed
            if any(_tw_hook(h.get("command"), "context") for g in load(ps).get("hooks", {}).get("SessionStart", [])
                   for h in g.get("hooks", [])):
                print(f"  ! {ps} is in a git repo, not ignored, and already holds the hook (absolute paths): move "
                      "that hook to .claude/settings.local.json before committing")
            ps = ps.with_name("settings.local.json")
            rel = os.path.relpath(ps, top)
            exclude_locally(top, common, [rel, rel + ".bak"], a.apply)
            print(f"  {folder} is in a git repo: using {ps.name} (kept out of commits)")
        s = load(ps)
        groups = s.setdefault("hooks", {}).setdefault("SessionStart", [])
        ours = [h for g in groups for h in g.get("hooks", []) if _tw_hook(h.get("command"), "context")]
        present = any(h.get("command") == HOOK_CMD for h in ours)
        for h in ours:
            h["command"] = HOOK_CMD  # an older bare `python3` hook is updated in place, never added twice
        if not ours:
            groups.append({"hooks": [{"type": "command", "command": HOOK_CMD}]})
        print(f"  SessionStart hook: {'already present' if present else 'updating to an absolute python path' if ours else 'adding'}")
        if a.no_auto_memory and folder == a.project:
            s["autoMemoryEnabled"] = False
            print("  autoMemoryEnabled: false")
        # Permissions for sessions that start here (RC threads start in the served folder): always the local file,
        # so the absolute paths and the user's choices never reach a commit. Same file as the hook's: one write.
        pl = ps.with_name("settings.local.json")
        if pl == ps:
            folder_rules(pl, s, a, folder_dirs(a.project, a.served))
            continue
        write(ps, s, a.apply)
        if git_exposed(pl):
            exposed2 = git_exposed(pl)
            exclude_locally(exposed2[0], exposed2[1], [os.path.relpath(pl, exposed2[0])], a.apply)
        folder_rules(pl, load(pl), a, folder_dirs(a.project, a.served))
    if a.project:  # the project's existing worktrees get the same rules (tw.py worktree copies them into new ones)
        pj = Path(a.project).expanduser().resolve()
        wroot = pj.parent / f"{pj.name}-worktrees"
        for wt in sorted(x for x in wroot.iterdir() if (x / ".git").exists()) if wroot.is_dir() else []:
            pl = wt / ".claude" / "settings.local.json"
            print(f"3.w worktree {wt.name}")
            if git_exposed(pl):
                ex = git_exposed(pl)
                exclude_locally(ex[0], ex[1], [os.path.relpath(pl, ex[0])], a.apply)
            folder_rules(pl, load(pl), a, folder_dirs(a.project, a.served))

    if a.pre_push:
        print("4. pre-push privacy guard")
        if not a.project:
            print("  needs --project (the teamwork project whose .teamwork/PRIVATE.md is used)")
        else:
            proj = Path(a.project).expanduser().resolve()
            import subprocess
            r = subprocess.run(["git", "-C", str(proj), "rev-parse", "--git-common-dir"], capture_output=True, text=True)
            hp = subprocess.run(["git", "-C", str(proj), "config", "core.hooksPath"], capture_output=True, text=True)
            if r.returncode != 0:
                print(f"  {proj} is not a git repository: skipped")
            else:
                common = (proj / r.stdout.strip()).resolve()
                hooks = (proj / hp.stdout.strip()).resolve() if hp.stdout.strip() else common / "hooks"
                hook = hooks / "pre-push"
                if git_exposed(hook):  # inside .git it is never committable (git_exposed says None there)
                    print(f"  refused: core.hooksPath {hooks} is inside the work tree and not ignored, so the hook "
                          "(absolute paths to your home folder) could be committed. Ignore that folder, or unset "
                          "core.hooksPath, then re-run")
                    hook = None
            if r.returncode != 0 or hook is None:
                pass
            else:
                marker = "# teamwork privacy guard (installed by install.py --pre-push)"
                body = (f"#!/bin/sh\n{marker}\n"
                        f'exec "{PY}" "{DEST / "scripts" / "tw.py"}" -C "{proj}" guard --repo "$PWD" --pre-push '
                        '--remote "$1" --stdin-owned\n')
                old = hook.read_text(encoding="utf-8", errors="replace") if hook.exists() else None
                ours = old is not None and old.splitlines()[:2] == ["#!/bin/sh", marker] and len(old.splitlines()) == 3
                if old == body:
                    print(f"  already installed: {hook}")
                elif old is not None and not ours:
                    guard = body.splitlines()[-1][len("exec "):]
                    print(f"  a different pre-push hook exists: left untouched: {hook}\n"
                          "  git gives a pre-push hook the refs on stdin only once, so a command that reads it first "
                          "(git lfs pre-push does) leaves the guard nothing to check. Put these lines at the top of "
                          "that hook and feed the saved input to its other commands the same way:\n"
                          '    input=$(cat)\n'
                          f'    printf "%s\\n" "$input" | {guard} || exit 1\n'
                          '  e.g. the git lfs line becomes: printf "%s\\n" "$input" | git lfs pre-push "$@"')
                else:
                    if ours:
                        print(f"  {'updating' if a.apply else 'would update'} an older teamwork hook: {hook}")
                    print(f"  {'writing' if a.apply else 'would write'}: {hook} (covers every worktree of this repo)")
                    if a.apply:
                        hooks.mkdir(parents=True, exist_ok=True)
                        hook.write_text(body, encoding="utf-8")
                        hook.chmod(0o755)

    if not a.apply:
        print("\nDry run only. Re-run with --apply to make these changes.")
    else:
        print(
            "\nThreads pick their permission mode from the lead (setup.md section 2): to stop rc threads asking for "
            "every tool call, set `tw.py prefs --set thread_permission_mode=auto` or add allow rules."
        )
        print(
            "\nDone. In claude.ai, start a NEW session in this Remote Control environment and type /teamwork "
            "(skills load at session start)."
        )


if __name__ == "__main__":
    main()
