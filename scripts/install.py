#!/usr/bin/env python3
"""Install the teamwork skill on the computer that runs the Remote Control server.

Run it from an RC session (the user never needs a terminal).

  python3 install.py --link              # show what would change (dry run)
  python3 install.py --link --apply      # install skill + user allow rules
  python3 install.py --link --apply --project <project folder> --served <served folder>
                                         # also add the SessionStart hook to the project and the served folder

--link (recommended) or copy:
  --link  <config dir>/skills/teamwork becomes a symlink to this clone. Every new session runs the
          clone's current checkout: upgrade with `git pull`, and whatever lands on the checked-out
          branch is live at once (so keep that branch working).
  copy    (no --link) a frozen snapshot of the skill files, without .git, .teamwork, .claude and
          other local state. Nothing changes until you upgrade: delete <config dir>/skills/teamwork
          and re-run the installer (it never overwrites an existing skills/teamwork).

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
# Never part of a copy install: version control, per-project teamwork state, the clone's own
# Claude Code settings, editor/OS/build leftovers.
COPY_SKIP = (
    ".git", ".github", ".gitignore", ".gitattributes", ".teamwork", ".claude",
    "__pycache__", "*.pyc", ".pytest_cache", ".venv", "venv", "node_modules",
    ".DS_Store", ".idea", ".vscode", "*.bak", "*.swp",
)


def load(p):
    if p.exists():
        txt = p.read_text(encoding="utf-8").strip()
        return json.loads(txt) if txt else {}
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
        "--no-allow",
        action="store_true",
        help="skip adding allow rules to <config dir>/settings.json",
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

    if not a.no_allow:
        print("2. allow rules in user settings")
        s = load(USER_SETTINGS)
        allow = s.setdefault("permissions", {}).setdefault("allow", [])
        added = [r for r in ALLOW if r not in allow]
        allow.extend(added)
        print(f"  adding: {added or 'nothing (already present)'}")
        write(USER_SETTINGS, s, a.apply)

    targets = [x for x in (a.project, a.served) if x]
    for i, folder in enumerate(dict.fromkeys(targets)):
        print(f"3.{i + 1} SessionStart hook in {folder}")
        ps = Path(folder).expanduser().resolve() / ".claude" / "settings.json"
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
