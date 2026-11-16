#!/usr/bin/env python3
"""Install the teamwork skill on the computer that runs the Remote Control server.

Run it from an RC session (the user never needs a terminal).

  python3 install.py                     # show what would change (dry run)
  python3 install.py --apply             # install skill + user allow rules
  python3 install.py --apply --project <project folder> --served <served folder>
                                         # also add the SessionStart hook to the project and the served folder

Never removes anything. Existing settings are merged, and a .bak copy is written before any settings file changes.
"""

import argparse
import json
import shutil
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
HOME = Path.home()
DEST = HOME / ".claude" / "skills" / "teamwork"
USER_SETTINGS = HOME / ".claude" / "settings.json"
ALLOW = [
    "mcp__claude-code-remote__send_message",
    "mcp__claude-code-remote__get_session",
    "mcp__claude-code-remote__list_events",
    "mcp__claude-code-remote__list_sessions",
]
HOOK_CMD = 'python3 "$HOME/.claude/skills/teamwork/scripts/tw.py" context'


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
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--apply", action="store_true", help="actually write (default is a dry run)"
    )
    ap.add_argument("--project", help="project folder to receive the SessionStart hook")
    ap.add_argument("--served", help="folder the Remote Control server serves (RC sessions start here); also gets the hook")
    ap.add_argument(
        "--no-allow",
        action="store_true",
        help="skip adding allow rules to ~/.claude/settings.json",
    )
    ap.add_argument(
        "--no-auto-memory",
        action="store_true",
        help="also set autoMemoryEnabled=false in the project",
    )
    a = ap.parse_args()

    print("1. skill files")
    if SRC == DEST:
        print(f"  already running from {DEST}")
    else:
        print(f"  {'copying' if a.apply else 'would copy'} {SRC} -> {DEST}")
        if a.apply:
            shutil.copytree(
                SRC,
                DEST,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".teamwork"),
            )

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
            "\nDone. In claude.ai, start a NEW session in this Remote Control environment and type /teamwork "
            "(skills load at session start)."
        )


if __name__ == "__main__":
    main()
