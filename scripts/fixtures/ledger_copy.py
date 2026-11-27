#!/usr/bin/env python3
"""Copy only a ledger's own files for smoke tests: skip private/ and scratch paths.

usage: ledger_copy.py SRC_TEAMWORK_DIR DST_TEAMWORK_DIR [--max-mb N]
"""
import shutil
import sys
from pathlib import Path

LEDGER_DIRS = ("tasks", "prompts", "snapshots")  # small, ledger-owned subfolders
MAX_MB = 1024


def copy_ledger(src, dst, max_mb=MAX_MB):
    """Copy top-level ledger files plus LEDGER_DIRS; refuse past max_mb. Returns bytes copied."""
    src, dst = Path(src), Path(dst)
    if not (src / "state.json").is_file():
        raise SystemExit(f"not a ledger (no state.json): {src}")
    plan = [p for p in src.iterdir() if p.is_file() and not p.is_symlink()]
    for name in LEDGER_DIRS:
        if (src / name).is_dir() and not (src / name).is_symlink():
            plan += [p for p in (src / name).rglob("*") if p.is_file() and not p.is_symlink()
                     and not {"private", "tmp"}.intersection(p.relative_to(src).parts)]
    total = sum(p.stat().st_size for p in plan)
    if total > max_mb * 1024 * 1024:
        raise SystemExit(f"ledger copy would be {total // (1024 * 1024)} MB > {max_mb} MB; refusing")
    for p in plan:
        target = dst / p.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
    return total


if __name__ == "__main__":
    args = sys.argv[1:]
    cap = MAX_MB
    if "--max-mb" in args:
        i = args.index("--max-mb")
        cap = int(args[i + 1])
        del args[i:i + 2]
    if len(args) != 2:
        raise SystemExit(__doc__)
    print(copy_ledger(args[0], args[1], cap))
