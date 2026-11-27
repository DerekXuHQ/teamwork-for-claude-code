"""Read local usage windows and classify quota pressure. Standard library only."""
import datetime
import glob
import json
import math
import os

KEYS = ("five_hour", "seven_day", "seven_day_overage_included")
DEFAULTS = {"quota_burn_at": 70, "quota_lead_only_at": 90, "quota_pre_reset_hours": 36,
            "quota_pre_reset_fable_max": 80, "quota_fable_done_at": 95,
            "quota_5h_tight": 80, "quota_gpt_tight": 90}


def num(value):
    try:
        if isinstance(value, bool):
            return None
        result = float(value)
        return result if math.isfinite(result) and 0 <= result <= 1e15 else None
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None


def epoch(value):
    try:
        if isinstance(value, str) and not value.replace(".", "", 1).isdigit():
            return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        result = num(value)
        if result is None or result <= 0:
            return None
        if result > 1e11:
            result /= 1000
        return result if result < 4e9 else None
    except (TypeError, ValueError, OverflowError, OSError, RecursionError):
        return None


def _load(raw):
    try:
        return json.loads(raw)
    except (ValueError, RecursionError, TypeError):
        return None


def _files(pattern, recursive=False, limit=40):
    rows = []
    try:
        for path in glob.glob(pattern, recursive=recursive):
            try:
                if not os.path.islink(path) or os.path.exists(path):
                    rows.append((os.path.getmtime(path), path))
            except OSError:
                continue
    except (OSError, RecursionError, ValueError):
        pass
    return sorted(rows, reverse=True)[:limit]


def _tail(path, size=2_000_000):
    try:
        with open(path, "rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - size))
            return stream.read().decode("utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return []


def _find(obj, key, depth=0):
    if depth > 8:
        return None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _find(value, key, depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find(value, key, depth + 1)
            if found is not None:
                return found
    return None


def _window(obj, reset_key, now, fraction=False, source="events", seen=None):
    if not isinstance(obj, dict):
        return None
    value, reset = num(obj.get("utilization" if fraction else "percent" if source == "cache" else "used_percent")), epoch(obj.get(reset_key))
    if value is None or reset is None or reset <= now:
        return None
    # round: 0.57 * 100 is 56.99...
    return {"pct": round(value * 100, 6) if fraction else value, "resets_at": reset, "seen_at": seen,
            "src": source}


def _account_file(cfg):
    """The config folder's state file (the default folder keeps it in $HOME), parsed, or None."""
    candidates = [os.path.join(cfg, ".claude.json")]
    if os.path.realpath(cfg) == os.path.realpath(os.path.expanduser("~/.claude")):
        candidates.append(os.path.expanduser("~/.claude.json"))
    for path in candidates:
        try:
            with open(path, encoding="utf-8", errors="replace") as stream:
                data = _load(stream.read())
        except OSError:
            continue
        if isinstance(data, dict) and (data.get("cachedUsageUtilization") or data.get("oauthAccount")):
            return data
    return None


DESKTOP_USAGE = os.path.expanduser("~/Library/Application Support/Claude/plan-usage-history.json")


def _desktop(config_dirs, now):
    """T85: the desktop app's weekly-total samples, per organization; covers accounts with no remote-control session
    (local sessions write no usage events). Total only, no reset time; older than 6 hours: shown, not used for the tier."""
    try:
        with open(os.environ.get("TW_DESKTOP_USAGE") or DESKTOP_USAGE, encoding="utf-8", errors="replace") as stream:
            data = _load(stream.read())
    except OSError:
        return {}
    samples = data.get("samples") if isinstance(data, dict) else None
    if not isinstance(samples, list):
        return {}
    for cfg in config_dirs:
        account = (_account_file(cfg) or {}).get("oauthAccount")
        org = account.get("organizationUuid") if isinstance(account, dict) else None
        rows = [(epoch(x.get("t")), num((x.get("u") or {}).get("sd"))) for x in samples
                if isinstance(x, dict) and org and x.get("org") == org and isinstance(x.get("u"), dict)]
        rows = [r for r in rows if r[0] and r[1] is not None and r[1] <= 150 and r[0] <= now + 60]  # sd: whole percents
        if rows:
            seen, pct = max(rows)
            age = (now - seen) / 3600
            return {"total": {"pct": pct, "resets_at": None, "seen_at": seen, "src": "desktop", "age_h": age,
                              "stale": age > 6}}
    return {}


def _cache(config_dirs, now):
    for cfg in config_dirs:
        data = _account_file(cfg)
        cached = data.get("cachedUsageUtilization") if isinstance(data, dict) else None
        utilization = cached.get("utilization") if isinstance(cached, dict) else None
        if not isinstance(utilization, dict):
            continue
        fetched = epoch(cached.get("fetchedAtMs"))
        age = (now - fetched) / 3600 if fetched else None
        meta = {"seen_at": fetched, "src": "cache", "age_h": age, "stale": age is None or age > 6}
        result = {}
        for cache_key, result_key in (("seven_day", "total"), ("five_hour", "five_hour")):
            raw = utilization.get(cache_key)
            if isinstance(raw, dict):
                value = num(raw.get("utilization"))
                reset = epoch(raw.get("resets_at"))
                if value is not None and reset is not None and reset > now:
                    result[result_key] = {"pct": value, "resets_at": reset, "seen_at": fetched, "src": "cache"}
        result = {k: dict(meta, **v) for k, v in result.items()}
        limits = utilization.get("limits") if isinstance(utilization.get("limits"), list) else []
        for limit in limits:
            if isinstance(limit, dict) and limit.get("kind") == "weekly_scoped":
                scope = limit.get("scope")
                name = _find(scope, "display_name")
                row = _window({"percent": limit.get("percent"), "resets_at": limit.get("resets_at")},
                              "resets_at", now, source="cache", seen=fetched)
                if row:
                    result["scoped"] = dict(meta, **row, model=name if isinstance(name, str) else "Fable")
                if isinstance(name, str):
                    result["scoped_model"] = name
                break
        shares = utilization.get("weekly_scoped_shares")
        if isinstance(shares, list):
            for share in shares:
                if isinstance(share, dict) and num(share.get("allowance_percent_of_weekly")) is not None:
                    result["scoped_share_of_total_pct"] = num(share["allowance_percent_of_weekly"])
                    break
        if result:
            return result
    return {}


def _claude_events(config_dirs, now):
    best = {}
    files = []
    for cfg in config_dirs:
        files.extend(_files(os.path.join(cfg, "bridge-transcript-cse_*.jsonl"), limit=40))
    for mtime, path in sorted(files, reverse=True)[:40]:
        here = {}
        for line in reversed(_tail(path)):
            if "unifiedWindows" not in line:
                continue
            data = _load(line)
            windows = _find(data, "unifiedWindows") if data is not None else None
            if not isinstance(windows, dict):
                continue
            seen = epoch(data.get("timestamp")) if isinstance(data, dict) else None
            seen = seen or mtime
            for key in KEYS:
                if key in here or not isinstance(windows.get(key), dict):
                    continue
                row = _window(windows[key], "resetsAt", now, fraction=True, seen=seen)
                if row:
                    here[key] = row
            if len(here) == len(KEYS):
                break
        for key, value in here.items():
            best.setdefault(key, value)
        if all(k in best for k in ("seven_day", "seven_day_overage_included")):
            break
    out = {}
    for key, dest in (("seven_day", "total"), ("five_hour", "five_hour"),
                      ("seven_day_overage_included", "scoped")):
        if key in best:
            out[dest] = dict(best[key], **({"model": "Fable"} if dest == "scoped" else {}))
    cache = _cache(config_dirs, now)
    for row in (out.get("total"), out.get("scoped")):  # a day-old event may be far below today's: not for the tier
        if row and row.get("seen_at") and now - row["seen_at"] > 86400:
            row["stale"] = True
    desk = _desktop(config_dirs, now)  # T85: accounts with only local sessions log no events
    for key in ("total", "scoped"):
        rows = [r for r in (out.get(key), cache.get(key), desk.get(key)) if isinstance(r, dict) and num(r.get("pct")) is not None]
        if not rows:
            continue
        live = [r for r in rows if not r.get("stale")]
        timed = [r for r in live if num(r.get("resets_at")) is not None]
        pick = None
        if timed:  # the latest window (resets within 3 hours count as one), then the highest: usage only grows
            last = max(r["resets_at"] for r in timed)
            pick = max((r for r in timed if last - r["resets_at"] < 10800), key=lambda r: r["pct"])
        for r in live:  # a reading with no reset time (desktop samples) wins when it was taken later
            if num(r.get("resets_at")) is None and (pick is None or (r.get("seen_at") or 0) > (pick.get("seen_at") or 0)):
                pick = r
        if pick is None:  # nothing live: the newest reading, shown but not used for the tier
            pick = max(rows, key=lambda r: r.get("seen_at") or 0)
        model = (out.get(key) or {}).get("model")
        out[key] = dict(pick, **({"model": model or pick.get("model") or "Fable"} if key == "scoped" else {}))
        if pick.get("src") != "events" or pick.get("stale"):
            age = pick.get("age_h") if pick.get("src") != "events" else (now - pick["seen_at"]) / 3600
            where = {"desktop": "the desktop app", "cache": "cache"}.get(pick.get("src"), "events")
            out.setdefault("notes", []).append(f"{key} from {where}" + (f", {age:.0f}h old" if age is not None else ", age unknown")
                                               + (" (stale: not used for tier)" if pick.get("stale") else ""))
    for key in ("five_hour", "scoped_share_of_total_pct"):
        if key not in out and key in cache:
            out[key] = cache[key]
    if out.get("scoped") and cache.get("scoped_model"):
        out["scoped"]["model"] = cache["scoped_model"]
    if "total" in out and "scoped" not in out and out["total"].get("src") != "desktop":  # local sessions log no events
        out["scoped"] = {"pct": 0.0, "resets_at": out["total"]["resets_at"], "seen_at": None,
                         "src": "inferred", "model": cache.get("scoped_model") or "Fable"}
        out.setdefault("notes", []).append("scoped not seen this window: inferred 0%")
    return out


def _codex(codex_dir, now):
    for mtime, path in _files(os.path.join(codex_dir, "sessions", "**", "*.jsonl"), True, 40):
        for line in reversed(_tail(path, 500_000)):
            if "rate_limits" not in line:
                continue
            data = _load(line)
            limits = _find(data, "rate_limits") if data is not None else None
            if not isinstance(limits, dict):
                continue
            good = []
            wins = [w for w in (limits.get("primary"), limits.get("secondary")) if isinstance(w, dict)]
            for row in wins:
                value, reset, mins = num(row.get("used_percent")), epoch(row.get("resets_at")), num(row.get("window_minutes"))
                if value is not None and reset and reset > now:
                    good.append({"pct": value, "resets_at": reset, "window_min": mins,
                                 "seen_at": (epoch(data.get("timestamp")) if isinstance(data, dict) else None) or mtime,
                                 "weekly": (mins or 0) >= 10080, "src": "codex"})
            if good:
                return max([w for w in good if w["weekly"]] or good, key=lambda w: w["pct"])
            if wins:
                return {"expired": True, "src": "codex"}
    return {}


def read_quota(config_dirs, codex_dir, now):
    """Return validated Claude and Codex usage readings; malformed inputs are treated as absent."""
    try:
        dirs = [os.fspath(x) for x in config_dirs]
        claude = _claude_events(dirs, now)
        if "total" not in claude:
            claude.setdefault("notes", []).append("no total window")
        return {"claude": claude, "gpt": _codex(os.fspath(codex_dir), now),
                "notes": list(claude.get("notes", []))}
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return {"claude": {"notes": ["malformed or unavailable usage data"]}, "gpt": {},
                "notes": ["malformed or unavailable usage data"]}


def tier(result, prefs=None, now=None):
    """Return (tier, flags) using unrounded utilization values."""
    prefs = prefs or {}
    def threshold(key):
        value = num(prefs.get(key, DEFAULTS[key]))
        return DEFAULTS[key] if value is None else value
    claude = result.get("claude", result)
    gpt = result.get("gpt", {})
    total = claude.get("total") or {}
    scoped = claude.get("scoped") or {}
    total_pct = total.get("pct") if not total.get("stale") else None
    scoped_pct = scoped.get("pct") if not scoped.get("stale") else None
    flags = []
    five = claude.get("five_hour") or {}
    if num(five.get("pct")) is not None and five["pct"] >= threshold("quota_5h_tight"):
        flags.append("5h-tight")
    if gpt.get("weekly") and num(gpt.get("pct")) is not None and gpt["pct"] >= threshold("quota_gpt_tight"):
        flags.append("gpt-tight")
    done = scoped_pct is not None and scoped_pct >= threshold("quota_fable_done_at")
    if done:
        flags.append("fable-done")
    if num(total_pct) is None:
        return "unknown", flags
    if total_pct >= threshold("quota_lead_only_at"):
        return "lead-only", flags
    if total_pct >= threshold("quota_burn_at"):
        return ("save" if done else "burn-fable"), flags
    if now is not None and num(total.get("resets_at")) is not None and scoped_pct is not None:
        if (total["resets_at"] - now) / 3600 <= threshold("quota_pre_reset_hours") and scoped_pct < threshold("quota_pre_reset_fable_max"):
            return "pre-reset-burn", flags
    return "normal", flags
