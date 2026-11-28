#!/usr/bin/env python3
"""Staged rollout of the teamwork skill (H8): every `~/.claude*/skills/teamwork` points at a release snapshot,
not at the clone, so a merge into main reaches nobody until it is published, and one command rolls back.

  release.py status                    where each config dir points, and how far the release is behind main
  release.py publish --ref main        snapshot the ref (git archive) and switch every config dir to it (dry run)
  release.py publish --ref main --apply
  release.py rollback --apply          back to the previous release (or --to <name>; `edge` = the clone)
  release.py edge --apply              run the clone live again (the old way)
  release.py features                  check README feature sections against the ledger

Dry run by default. Never deletes: old snapshots stay under <config dir>/teamwork-releases/."""

import argparse
import datetime
import difflib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import re

CLONE = Path(__file__).resolve().parents[1]
FEATURE_MARKERS = ("<!-- features:start -->", "<!-- features:end -->")

LABELS = {
    "en": {
        "heading": "{skill} release comparison ({since}, {days} days each side)",
        "quota": "quota used per day: before {before}; after {after}",
        "prompts": "permission prompts per day: before {before}; after {after}",
        "missing": "data missing: {items}", "missing_value": "data missing",
    },
    "zh": {
        "heading": "{skill} 版本对比（{since}，前后各 {days} 天）",
        "quota": "每日额度用量：之前 {before}；之后 {after}",
        "prompts": "每日权限提示：之前 {before}；之后 {after}",
        "missing": "数据缺失：{items}", "missing_value": "数据缺失",
    },
}


def configs(args):
    skill = getattr(args, "skill", "teamwork")
    if args.config_dir:
        return [Path(p).expanduser() for p in args.config_dir]
    home = Path(os.environ.get("HOME", str(Path.home())))
    found = [p for p in home.glob(".claude*") if ((p / "skills" / skill).exists() or
             (p / "skills" / skill).is_symlink() or skill != "teamwork" and (p / "skills").is_dir())]
    extra = os.environ.get("CLAUDE_CONFIG_DIR")
    if extra and ((Path(extra).expanduser() / "skills" / skill).exists() or (Path(extra).expanduser() / "skills" / skill).is_symlink() or
                  skill != "teamwork" and (Path(extra).expanduser() / "skills").is_dir()):
        found.append(Path(extra).expanduser())
    return list(dict.fromkeys(found))


def announce(args, message):
    print(message if args.apply else "would " + message)


def index_file(config, skill="teamwork"):
    return config / f"{skill}-releases" / "index.json"


def read_index(config, skill="teamwork"):
    f = index_file(config, skill)
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"releases": [], "current": None, "previous": None}


def write_index(args, config, data):
    f = index_file(config, args.skill)
    announce(args, f"write {f}")
    if args.apply:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _link(config, skill="teamwork"):
    return config / "skills" / skill


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
        announce(args, f"switch {_link(config, args.skill)} -> {target}")
    if not args.apply:
        for config, _, data in plan:
            write_index(args, config, data)
        return
    done, olds = [], {c: (index_file(c, args.skill).read_text(encoding="utf-8") if index_file(c, args.skill).exists() else None)
                      for c, _, _ in plan}
    try:
        for config, target, _ in plan:
            link = _link(config, args.skill)
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
                    index_file(c, args.skill).write_text(text, encoding="utf-8")
                elif index_file(c, args.skill).exists():
                    index_file(c, args.skill).unlink()  # an index this run created goes again
            except OSError:
                bad.append(str(index_file(c, args.skill)))
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


def _feature_rows(repo, ledger):
    tw = repo / "scripts" / "tw.py"
    if not tw.exists() or not (ledger / ".teamwork").exists():
        raise ValueError(f"missing feature CLI: {tw}" if not tw.exists() else f"ledger has no .teamwork directory: {ledger}")
    result = subprocess.run([sys.executable, str(tw), "-C", str(ledger), "feature", "list", "--json"], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f"cannot read ledger features: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    rows = data.get("features", data) if isinstance(data, dict) else data
    return {fid: row for fid, row in rows.items() if row.get("status") == "landed"} if isinstance(rows, dict) else {
        row["id"]: row for row in rows if row.get("status") == "landed"}


def _region(text):
    start, end = FEATURE_MARKERS
    if start not in text or end not in text or text.index(start) >= text.index(end):
        return None
    a, b = text.index(start), text.index(end) + len(end)
    return a, b, text[a:b]


def _privacy_terms(repo, ledger):
    spec = importlib.util.spec_from_file_location("release_tw", repo / "scripts" / "tw.py")
    if not spec or not spec.loader: raise ValueError("cannot load scripts/tw.py privacy helpers")
    tw = importlib.util.module_from_spec(spec); spec.loader.exec_module(tw)
    private, _, _ = tw.load_private(ledger / ".teamwork"); terms = private + tw.home_terms()
    checks = [(r"[^\s<>]+@[^\s<>]+\.[^\s<>]+", "email", re.I), ("/" + "Users/|/home/", "home path", 0), (r"session_\w{10,}", "session id", 0), ("work" + "less", "private term", re.I)]
    return terms, checks


def _rewrite_features(text, landed, filename, terms, checks):
    found = _region(text)
    a, b, region = found if found is not None else (None, None, None)
    if found is None:
        missing = [(fid, row.get("name", "")) for fid, row in sorted(landed.items())]
        for fid, name in missing: print(f"missing in {filename}: {fid} {name}")
        rewritten, dropped, new_region = text, [], None
    else:
        lines = region.splitlines()
        groups, current, heading, preamble = {}, None, {}, []
        all_ids, dropped = set(), []
        for line in lines[1:-1]:
            gh = re.search(r"<!--\s*group:([A-Za-z]+)\s*-->", line)
            if gh:
                current = gh.group(1)
                groups.setdefault(current, [])
                heading[current] = line
                continue
            if current is None:
                preamble.append(line)
                continue
            ids_match = re.search(r"<!--\s*f:([^>]*)-->", line)
            ids = re.findall(r"[A-Za-z]+\d+", ids_match.group(1)) if ids_match else []
            if not ids:
                groups[current].append((None, line))
                continue
            valid = [fid for fid in ids if fid in landed]
            all_ids.update(ids)
            if not valid:
                dropped.append((" ".join(ids), line.strip()[:40]))
                continue
            line = re.sub(r"<!--\s*f:[^>]*-->", "<!-- f:" + " ".join(valid) + " -->", line)
            groups[current].append((min(int(re.search(r"\d+$", fid).group()) for fid in valid), line))
        output = [lines[0], *preamble]
        for key in sorted(groups, key=lambda k: (k.upper(), len(k))):
            if key in heading: output.append(heading[key])
            rows = groups[key]
            output.extend(line for _, line in sorted((x for x in rows if x[0] is not None), key=lambda x: x[0]))
            output.extend(line for _, line in rows if _ is None)
        output.append(lines[-1])
        new_region = "\n".join(output)
        rewritten = text[:a] + new_region + text[b:]
        for ids, snippet in dropped: print(f"dropped from {filename}: {ids} ({snippet})")
        missing = [(fid, row.get("name", "")) for fid, row in sorted(landed.items()) if fid not in all_ids]
        for fid, name in missing: print(f"missing in {filename}: {fid} {name}")
        missing.extend((fid, snippet) for ids, snippet in dropped for fid in ids.split())
    marker = re.compile(r"<!--\s*(?:features:(?:start|end)|group:[A-Za-z]+|f:[^>]*)\s*-->"); hits = []
    for number, line in enumerate(rewritten.splitlines(), 1):
        clean = marker.sub("", line)
        for pattern, label, flags in checks:
            if re.search(pattern, clean, flags): hits.append((number, label))
        if any(term and term.casefold() in clean.casefold() for term in terms): hits.append((number, "private term"))
    if found is not None:
        start, end = FEATURE_MARKERS
        region_text = rewritten[rewritten.index(start):rewritten.index(end) + len(end)]
        for number, line in enumerate(region_text.splitlines(), 1):
            if re.search(r"(?<![A-Za-z0-9])(?:R|T|IL-)\d+(?![A-Za-z0-9])", marker.sub("", line)):
                actual = rewritten[:rewritten.index(start)].count("\n") + number
                hits.append((actual, "ledger ref"))
    hits = list(dict.fromkeys(hits))
    for number, label in hits: print(f"private in {filename}:{number} ({label})")
    return rewritten, missing, hits, (region, new_region) if found is not None else None, dropped


def check_features(repo, ledger, sources=None, apply=False):
    if not (ledger / ".teamwork").exists(): return True, [], []
    landed = _feature_rows(repo, ledger)
    terms, checks = _privacy_terms(repo, ledger)
    missing, hits, failures = [], [], False
    writes, privacy_any = [], False
    for filename in ("README.md", "README.zh.md"):
        before = sources[filename] if sources is not None else (repo / filename).read_text(encoding="utf-8")
        after, absent, found_hits, regions, dropped = _rewrite_features(before, landed, filename, terms, checks)
        print(f"{filename}: {len(landed)} landed, {len(landed) - len(absent)} on the page, {len(absent)} missing, "
              f"{len(found_hits)} privacy hits")
        missing.extend((filename, fid, name) for fid, name in absent)
        hits.extend((filename, number, label) for number, label in found_hits)
        failures = failures or bool(absent or found_hits or dropped)
        privacy_any = privacy_any or bool(found_hits)
        if regions and regions[0] != regions[1]:
            if apply:
                writes.append((repo / filename, after))
            else:
                print("".join(difflib.unified_diff(regions[0].splitlines(True), regions[1].splitlines(True),
                                                   fromfile=filename, tofile=filename + " (updated)")), end="")
                print("run features --apply")
                failures = True
    if apply and not privacy_any:
        for path, content in writes: path.write_text(content, encoding="utf-8")
    return not failures, missing, hits


def publish(args, dirs, repo):
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", f"{args.ref}^{{commit}}"],
                         capture_output=True, text=True)
    if sha.returncode:
        raise ValueError(f"cannot resolve ref: {args.ref}")
    sha = sha.stdout.strip()
    ledger = args.ledger.resolve() if args.ledger else repo
    if args.ledger and not (ledger / ".teamwork").exists(): raise ValueError(f"ledger has no .teamwork directory: {ledger}")
    sources = {}
    for filename in ("README.md", "README.zh.md"):
        shown = subprocess.run(["git", "-C", str(repo), "show", f"{sha}:{filename}"], capture_output=True, text=True)
        if shown.returncode:
            print("note: published ref has no README feature documents; skipping feature check")
            sources = None
            break
        sources[filename] = shown.stdout
    if sources is not None:
        _, missing, hits = check_features(repo, ledger, sources=sources)
        if hits: raise ValueError("README feature privacy hit; refusing to publish")
        if missing:
            print("README features: missing or dropped: " + ", ".join(f"{f}:{fid}" for f, fid, _ in missing))
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
            data = read_index(config, args.skill)
            if data.get("current") == name or (data.get("current") and any(
                    r["name"] == data["current"] and r["sha"] == sha for r in data["releases"])):
                print(f"{config}: {data['current']} is already {sha[:7]}: nothing to publish")
                continue
            if any(r["name"] == name for r in data["releases"]):
                raise ValueError(f"{config}: a release named {name} exists already (another commit in the same "
                                 "second?); wait a second and publish again")
            dest = config / f"{args.skill}-releases" / name
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
        data = read_index(config, args.skill)
        h = history(data)
        if args.to:
            name, h = args.to, h + [args.to]
        elif len(h) >= 2:
            name, h = h[-2], h[:-1]  # each rollback goes one further back
        else:
            raise ValueError(f"{config}: no previous release; nothing changed")
        if name != "edge" and not any(r["name"] == name for r in data["releases"]):
            raise ValueError(f"{config}: release {name} is not in the index; nothing changed")
        target = repo if name == "edge" else config / f"{args.skill}-releases" / name
        data["history"], data["previous"], data["current"] = h, (h[-2] if len(h) >= 2 else None), name
        plan.append((config, target, data))
    switch_all(args, plan)


def edge(args, dirs, repo):
    plan = []
    for config in dirs:
        data = read_index(config, args.skill)
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
        link = _link(config, args.skill)
        target = str(link.resolve()) if link.exists() else None
        data = read_index(config, args.skill)
        if target == str(repo.resolve()):
            point = f"edge: {target}"
        else:
            rel = next((r for r in data["releases"] if target == str((config / f"{args.skill}-releases" / r["name"]).resolve())), None)
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


def compare(args):
    since = None
    for config in configs(args):
        try:
            data=json.loads(index_file(config,args.skill).read_text(encoding="utf-8"))
            rel=next((x for x in data.get("releases",[]) if x.get("name")==args.since),None)
            if rel: since=rel.get("at"); break
        except (OSError,ValueError): pass
    if since is None:
        try: since=datetime.datetime.fromisoformat(args.since).isoformat()
        except ValueError:
            try: since=datetime.datetime.strptime(args.since,"%Y%m%d-%H%M%S").isoformat()
            except ValueError:
                print(f"release time missing for {args.since}; quota and permission-prompt data unavailable")
                return
    try: start=datetime.datetime.fromisoformat(str(since).replace("Z","+00:00"))
    except ValueError: start=datetime.datetime.strptime(str(since),"%Y%m%d-%H%M%S")
    cutoff=(start-datetime.timedelta(days=args.days)).date().isoformat()
    end=(start+datetime.timedelta(days=args.days)).date().isoformat()
    daily={"before":{},"after":{}}; missing=[]; prompt_daily={"before":{},"after":{}}
    projects=Path(args.projects).expanduser()
    candidates=[projects,*projects.glob("*")]
    ledgers=[p/".teamwork" for p in candidates if (p/".teamwork"/"log.jsonl").exists()]
    language="en"
    if len(ledgers)==1:
        try: language=json.loads((ledgers[0]/"state.json").read_text(encoding="utf-8")).get("prefs",{}).get("language","en")
        except (OSError,ValueError,AttributeError): pass
    zh=str(language).lower().replace("_","-").startswith("zh")
    if not ledgers: missing.append("teamwork ledgers")
    quota_rows=[]; prompt_rows=[]
    for ledger in ledgers:
        try: rows=[json.loads(x) for x in (ledger/"log.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        except (OSError,ValueError): continue
        for row in rows:
            if row.get("simulated") is True:
                continue
            date=str(row.get("t") or row.get("at", ""))[:10]
            side="before" if cutoff <= date < start.date().isoformat() else "after" if start.date().isoformat() <= date <= end else None
            if not side: continue
            ev=str(row.get("ev", "")).lower()
            if "quota" in ev:
                used=row.get("used_pct", row.get("used", row.get("tokens", row.get("pct"))))
                if isinstance(used,(int,float)): daily[side][date]=max(daily[side].get(date,float("-inf")),used)
            kind=str(row.get("kind", "")).lower()
            prompted=(ev == "notice-add" and kind == "needs-user" or
                      ev == "sweep-notice" and kind in ("approval", "needs-user") or
                      ev in ("thread-set", "thread-status") and row.get("waiting_for") == "permission")
            if prompted:
                prompt_rows.append((side,date)); prompt_daily[side][date]=prompt_daily[side].get(date,0)+1
        for name in ("quota-log.jsonl","quota.jsonl","quota-log.ndjson"):
            try: quota_rows += [json.loads(x) for x in (ledger/name).read_text(encoding="utf-8").splitlines() if x.strip()]
            except (OSError,ValueError): pass
    for row in quota_rows:
        date=str(row.get("t") or row.get("at") or row.get("date", ""))[:10]
        side="before" if cutoff <= date < start.date().isoformat() else "after" if start.date().isoformat() <= date <= end else None
        used=row.get("used_pct",row.get("used",row.get("tokens",row.get("pct"))))
        if side and isinstance(used,(int,float)) and row.get("simulated") is not True: daily[side][date]=max(daily[side].get(date,float("-inf")),used)
    if not daily["before"] and not daily["after"]: missing.append("quota usage log")
    if not prompt_rows: missing.append("permission-prompt events")
    def avg(vals): return sum(vals.values())/len(vals) if vals else None
    qbefore=avg(daily["before"]); qafter=avg(daily["after"])
    pbefore=sum(prompt_daily["before"].values())/args.days if prompt_rows else None
    pafter=sum(prompt_daily["after"].values())/args.days if prompt_rows else None
    lang=LABELS["zh" if zh else "en"]
    print(lang["heading"].format(skill=args.skill,since=args.since,days=args.days))
    q_before=qbefore if qbefore is not None else lang["missing_value"]
    q_after=qafter if qafter is not None else lang["missing_value"]
    p_before=f"{pbefore:.2f}" if pbefore is not None else lang["missing_value"]
    p_after=f"{pafter:.2f}" if pafter is not None else lang["missing_value"]
    print(lang["quota"].format(before=q_before,after=q_after))
    print(lang["prompts"].format(before=p_before,after=p_after))
    if missing: print(lang["missing"].format(items=("、" if zh else ", ").join(dict.fromkeys(missing))))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", type=Path, default=CLONE)
    p.add_argument("--config-dir", action="append")
    p.add_argument("--skill", default="teamwork")
    sub = p.add_subparsers(dest="command", required=True)
    def skill_arg(x): x.add_argument("--skill", default=argparse.SUPPRESS)
    x = sub.add_parser("publish"); skill_arg(x); x.add_argument("--ref", default="main"); x.add_argument("--note", default=""); x.add_argument("--apply", action="store_true")
    x = sub.add_parser("rollback"); skill_arg(x); x.add_argument("--to"); x.add_argument("--apply", action="store_true")
    x = sub.add_parser("edge"); skill_arg(x); x.add_argument("--apply", action="store_true")
    x = sub.add_parser("status"); skill_arg(x); x.add_argument("--json", action="store_true"); x.set_defaults(apply=False)
    x = sub.add_parser("compare"); skill_arg(x); x.add_argument("--since", required=True); x.add_argument("--days", type=int, default=7); x.add_argument("--projects", default=".")
    x = sub.add_parser("features"); x.add_argument("--ledger", type=Path); x.add_argument("--apply", action="store_true")
    x = sub.choices["publish"]; x.add_argument("--ledger", type=Path)
    a = p.parse_args()
    if a.command == "compare" and a.days < 1:
        p.error("compare --days must be a positive whole number")
    repo = a.repo.resolve()
    dirs = configs(a)
    if a.command == "compare":
        compare(a); return
    if a.command == "features":
        try:
            ledger = (a.ledger or repo).resolve()
            if a.ledger and not (ledger / ".teamwork").exists(): raise ValueError(f"ledger has no .teamwork directory: {ledger}")
            ok, _, _ = check_features(repo, ledger, apply=a.apply)
        except (ValueError, OSError, json.JSONDecodeError) as e: sys.exit(str(e))
        sys.exit(0 if ok else 1)
    if not dirs:
        sys.exit("no config dir with skills/teamwork found under ~/.claude*; pass --config-dir")
    bad = [str(_link(c, a.skill)) for c in dirs if a.command != "status" and
           _link(c, a.skill).exists() and not _link(c, a.skill).is_symlink()]
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
