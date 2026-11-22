#!/usr/bin/env python3
"""Staged rollout of the teamwork skill (H8): every `~/.claude*/skills/teamwork` points at a release snapshot,
not at the clone, so a merge into main reaches nobody until it is published, and one command rolls back.

  release.py status                    where each config dir points, and how far the release is behind main
  release.py publish --ref main        snapshot the ref (git archive) and switch every config dir to it (dry run)
  release.py publish --ref main --apply
  release.py rollback --apply          back to the previous release (or --to <name>; `edge` = the clone)
  release.py edge --apply              run the clone live again (the old way)

Dry run by default. Never deletes: old snapshots stay under <config dir>/teamwork-releases/."""

import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

CLONE = Path(__file__).resolve().parents[1]


def configs(args):
    if args.config_dir:
        return [Path(p).expanduser() for p in args.config_dir]
    home = Path(os.environ.get("HOME", str(Path.home())))
    found = [p for p in home.glob(".claude*") if (p / "skills" / "teamwork").exists() or
             (p / "skills" / "teamwork").is_symlink()]
    extra = os.environ.get("CLAUDE_CONFIG_DIR")
    if extra and (Path(extra).expanduser() / "skills" / "teamwork").exists():
        found.append(Path(extra).expanduser())
    return list(dict.fromkeys(found))


def announce(args, message):
    print(message if args.apply else "would " + message)


def index_file(config):
    return config / "teamwork-releases" / "index.json"


def read_index(config):
    f = index_file(config)
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"releases": [], "current": None, "previous": None}


def write_index(args, config, data):
    f = index_file(config)
    announce(args, f"write {f}")
    if args.apply:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _link(config):
    return config / "skills" / "teamwork"


def _point(link, target):
    """Point link at target in one step (a temp link renamed over it)."""
    link.parent.mkdir(parents=True, exist_ok=True)
    tmp = link.parent / f".teamwork.tmp-{os.getpid()}"
    if tmp.is_symlink():
        tmp.unlink()
    tmp.symlink_to(target)
    os.replace(tmp, link)


def switch_all(args, plan, check=None):
    """plan: [(config, target, new_index)]. All links move or none does: a failure puts the switched ones back.
    The indexes are written only after every link points where they say. check: where to look for the files in a
    dry run (the staged snapshot), when target does not exist yet."""
    for config, target, _ in plan:
        if not (Path(target) / "SKILL.md").exists() and not (check and not args.apply and (Path(check) / "SKILL.md").exists()):
            raise ValueError(f"{config}: {target} is missing or incomplete; nothing changed")
        announce(args, f"switch {_link(config)} -> {target}")
    if not args.apply:
        for config, _, data in plan:
            write_index(args, config, data)
        return
    done, olds = [], {c: (index_file(c).read_text(encoding="utf-8") if index_file(c).exists() else None)
                      for c, _, _ in plan}
    try:
        for config, target, _ in plan:
            link = _link(config)
            old = os.readlink(link) if link.is_symlink() else None
            _point(link, target)
            done.append((link, old))
        for config, _, data in plan:
            write_index(args, config, data)
    except OSError as e:
        bad = []
        for link, old in reversed(done):
            try:
                _point(link, old) if old else link.unlink()  # a link we created is taken away again
            except OSError:
                bad.append(str(link))
        for c, text in olds.items():
            try:
                if text is not None:
                    index_file(c).write_text(text, encoding="utf-8")
                elif index_file(c).exists():
                    index_file(c).unlink()  # an index this run created goes again
            except OSError:
                bad.append(str(index_file(c)))
        raise ValueError(f"switch failed ({e}); " + (f"could not put back: {', '.join(bad)}" if bad else
                                                    "every config dir is back where it was")) from e


def timestamp():
    return os.environ.get("TW_NOW") or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def _extract(tar_path, stage):
    """Committed files only; a member that is absolute, climbs out with `..`, or links outside the snapshot is
    refused, and links are kept as links (never followed into the rest of the disk)."""
    with tarfile.open(tar_path) as tf:
        for m in tf.getmembers():
            p = Path(m.name)
            if p.is_absolute() or ".." in p.parts:
                raise ValueError(f"unsafe archive member: {m.name}")
            if m.issym() or m.islnk():
                t = Path(m.linkname)
                if t.is_absolute() or ".." in (p.parent / t).parts or m.islnk() and ".." in t.parts:
                    raise ValueError(f"archive member {m.name} links outside the snapshot ({m.linkname})")
        if "filter" in tarfile.TarFile.extractall.__code__.co_varnames:
            tf.extractall(stage, filter="data")
        else:
            tf.extractall(stage)


def history(data):
    return data.get("history") or [x for x in (data.get("previous"), data.get("current")) if x]


def publish(args, dirs, repo):
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", f"{args.ref}^{{commit}}"],
                         capture_output=True, text=True)
    if sha.returncode:
        raise ValueError(f"cannot resolve ref: {args.ref}")
    sha = sha.stdout.strip()
    at = timestamp()
    name = f"r{at}-{sha[:7]}"
    with tempfile.TemporaryDirectory(prefix="tw-release-") as td:
        tmp_archive = Path(td) / "src.tar"
        tmp_archive.write_bytes(subprocess.run(["git", "-C", str(repo), "archive", sha], capture_output=True,
                                               check=True).stdout)
        stage = Path(td) / name
        stage.mkdir()
        _extract(tmp_archive, stage)
        (stage / "RELEASE.json").write_text(json.dumps({"name": name, "sha": sha, "ref": args.ref,
            "at": at, "channel": "release"}, indent=2) + "\n", encoding="utf-8")
        plan = []
        for config in dirs:  # stage everywhere first; nothing points at a snapshot until all are in place
            data = read_index(config)
            if data.get("current") == name or (data.get("current") and any(
                    r["name"] == data["current"] and r["sha"] == sha for r in data["releases"])):
                print(f"{config}: {data['current']} is already {sha[:7]}: nothing to publish")
                continue
            if any(r["name"] == name for r in data["releases"]):
                raise ValueError(f"{config}: a release named {name} exists already (another commit in the same "
                                 "second?); wait a second and publish again")
            dest = config / "teamwork-releases" / name
            announce(args, f"install snapshot {dest}")
            if args.apply and dest.exists():  # left by an interrupted run? only a complete copy of this commit counts
                try:
                    ok = json.loads((dest / "RELEASE.json").read_text(encoding="utf-8")).get("sha") == sha
                except (OSError, ValueError):
                    ok = False
                if not ok:
                    raise ValueError(f"{dest} exists but is not a complete snapshot of {sha[:7]}; move it aside and "
                                     "publish again (nothing changed)")
            if args.apply and not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmpdest = dest.with_name(dest.name + f".partial-{os.getpid()}")
                shutil.copytree(stage, tmpdest, symlinks=True)
                os.replace(tmpdest, dest)  # a snapshot appears whole or not at all
            data["releases"].append({"name": name, "sha": sha, "ref": args.ref, "at": at, "note": args.note or ""})
            data["history"] = history(data) + [name]
            data["previous"], data["current"] = data.get("current"), name
            plan.append((config, dest, data))
        switch_all(args, plan, check=stage)


def rollback(args, dirs, repo):
    plan = []
    for config in dirs:  # check every dir before any link moves
        data = read_index(config)
        h = history(data)
        if args.to:
            name, h = args.to, h + [args.to]
        elif len(h) >= 2:
            name, h = h[-2], h[:-1]  # each rollback goes one further back
        else:
            raise ValueError(f"{config}: no previous release; nothing changed")
        if name != "edge" and not any(r["name"] == name for r in data["releases"]):
            raise ValueError(f"{config}: release {name} is not in the index; nothing changed")
        target = repo if name == "edge" else config / "teamwork-releases" / name
        data["history"], data["previous"], data["current"] = h, (h[-2] if len(h) >= 2 else None), name
        plan.append((config, target, data))
    switch_all(args, plan)


def edge(args, dirs, repo):
    plan = []
    for config in dirs:
        data = read_index(config)
        data["history"] = history(data) + ["edge"]
        data["previous"], data["current"] = data.get("current"), "edge"
        plan.append((config, repo, data))
    switch_all(args, plan)


def status(args, dirs, repo):
    main = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "origin/main^{commit}"],
                          capture_output=True, text=True)
    if main.returncode:
        main = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "main^{commit}"],
                              capture_output=True, text=True)
    main_sha = main.stdout.strip() if main.returncode == 0 else None
    rows = []
    for config in dirs:
        link = config / "skills" / "teamwork"
        target = str(link.resolve()) if link.exists() else None
        data = read_index(config)
        if target == str(repo.resolve()):
            point = f"edge: {target}"
        else:
            rel = next((r for r in data["releases"] if target == str((config / "teamwork-releases" / r["name"]).resolve())), None)
            point = f"release: {rel['name']} ({rel['sha'][:7]})" if rel else f"other: {target}"
        current = next((r for r in data["releases"] if r["name"] == data.get("current")), None)
        behind = None
        if current and main_sha:
            count = subprocess.run(["git", "-C", str(repo), "rev-list", "--count", f"{current['sha']}..{main_sha}"],
                                   capture_output=True, text=True)
            behind = int(count.stdout.strip()) if count.returncode == 0 else None
        rows.append({"config_dir": str(config), "link": point, "current": data.get("current"),
                     "previous": data.get("previous"), "releases": data["releases"],
                     "main": main_sha, "freshness": "up to date" if behind == 0 else
                     (f"{behind} commits behind main" if behind is not None else "unknown")})
    if args.json:
        print(json.dumps({"configs": rows}, indent=2))
    else:
        for row in rows:
            print(f"{row['config_dir']}: {row['link']}; current={row['current']} previous={row['previous']}; {row['freshness']}")
            for r in row["releases"]:
                print(f"  {r['name']} {r['sha'][:7]} {r.get('note', '')}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", type=Path, default=CLONE)
    p.add_argument("--config-dir", action="append")
    sub = p.add_subparsers(dest="command", required=True)
    x = sub.add_parser("publish"); x.add_argument("--ref", default="main"); x.add_argument("--note", default=""); x.add_argument("--apply", action="store_true")
    x = sub.add_parser("rollback"); x.add_argument("--to"); x.add_argument("--apply", action="store_true")
    x = sub.add_parser("edge"); x.add_argument("--apply", action="store_true")
    x = sub.add_parser("status"); x.add_argument("--json", action="store_true"); x.set_defaults(apply=False)
    a = p.parse_args()
    repo = a.repo.resolve()
    dirs = configs(a)
    if not dirs:
        sys.exit("no config dir with skills/teamwork found under ~/.claude*; pass --config-dir")
    bad = [str(c / "skills" / "teamwork") for c in dirs if a.command != "status" and
           (c / "skills" / "teamwork").exists() and not (c / "skills" / "teamwork").is_symlink()]
    if bad:  # all config dirs move together or none does: never leave them on different versions
        sys.exit(f"refusing: {', '.join(bad)} is a real folder, not a link (a copy install); nothing changed. "
                 "Move it aside yourself, then run again")
    try:
        if a.command == "publish": publish(a, dirs, repo)
        elif a.command == "rollback": rollback(a, dirs, repo)
        elif a.command == "edge": edge(a, dirs, repo)
        else: status(a, dirs, repo)
    except (ValueError, OSError, subprocess.CalledProcessError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
