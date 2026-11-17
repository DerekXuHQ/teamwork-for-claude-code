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

Never removes anything. Existing settings are merged, and a .bak copy is written before any settings file changes.
"""

import argparse
import json
import os
import shutil
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
HOOK_CMD = f'python3 "{DEST / "scripts" / "tw.py"}" context'


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
        "--no-auto-memory",
        action="store_true",
        help="also set autoMemoryEnabled=false in the project",
    )
    a = ap.parse_args()
    if a.config_dir:
        CONFIG_DIR = Path(a.config_dir).expanduser()
        DEST = CONFIG_DIR / "skills" / "teamwork"
        USER_SETTINGS = CONFIG_DIR / "settings.json"
        HOOK_CMD = f'python3 "{DEST / "scripts" / "tw.py"}" context'
    print(f"Claude Code config dir: {CONFIG_DIR}"
          + ("" if os.environ.get("CLAUDE_CONFIG_DIR") or a.config_dir else "  (CLAUDE_CONFIG_DIR not set; check it matches the one your Remote Control server uses)"))

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
            ("allow", ALLOW + tw_allow(), "reporting calls and tw.py, so threads never stall on them"),
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
            if any(h.get("command") == HOOK_CMD for g in load(ps).get("hooks", {}).get("SessionStart", [])
                   for h in g.get("hooks", [])):
                print(f"  ! {ps} is in a git repo, not ignored, and already holds the hook (absolute paths): move "
                      "that hook to .claude/settings.local.json before committing")
            ps = ps.with_name("settings.local.json")
            rel = os.path.relpath(ps, top)
            exclude_locally(top, common, [rel, rel + ".bak"], a.apply)
            print(f"  {folder} is in a git repo: using {ps.name} (kept out of commits)")
        s = load(ps)
        groups = s.setdefault("hooks", {}).setdefault("SessionStart", [])
        present = any(
            h.get("command") == HOOK_CMD for g in groups for h in g.get("hooks", [])
        )
        if not present:
            groups.append({"hooks": [{"type": "command", "command": HOOK_CMD}]})
        print(f"  SessionStart hook: {'already present' if present else 'adding'}")
        if a.no_auto_memory and folder == a.project:
            s["autoMemoryEnabled"] = False
            print("  autoMemoryEnabled: false")
        write(ps, s, a.apply)

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
                        f'exec python3 "{DEST / "scripts" / "tw.py"}" -C "{proj}" guard --repo "$PWD" --pre-push '
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
