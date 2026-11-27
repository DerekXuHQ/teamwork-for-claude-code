#!/usr/bin/env python3
"""Generate sanitized transcript samples and expose deterministic test fixtures."""
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import _regenerate_sources as source

OUT = HERE
LOCAL_SESSION = source.LOCAL_SESSION

# Each row identifies source inputs, selection, and redaction. Generated-only
# samples have no raw source argument and are emitted by source._generate_t79().
SAMPLES = {
    "t71_real_candidate.jsonl": {"source": "candidate_source", "select": source._quiz_rows, "redact": source.scrub,
                                  "bridge": "bridge-transcript-cse_fixture-remote-session.jsonl"},
    "bridge-transcript-cse_fixture-remote-session.jsonl": {"source": "bridge_source", "select": source._bridge_rows,
                                                            "redact": source.scrub},
    "t71_real_tool_results.jsonl": {"source": "tool_source", "select": source._tool_rows, "redact": source.scrub,
                                     "bridge": "bridge-transcript-cse_fixture-remote-session.jsonl"},
    "t71_old_thread.jsonl": {"source": "records_source", "select": source._old_thread_rows, "redact": source.scrub,
                              "bridge": "bridge-transcript-cse_fixture-remote-session.jsonl"},
    "t79_hook_start_session.jsonl": {"source": None, "select": None, "redact": source._redact_t79},
    "t79_hook_create_session.jsonl": {"source": None, "select": None, "redact": source._redact_t79},
    "t79_nested_desktop_sessions.jsonl": {"source": None, "select": None, "redact": source._metadata_allowlist},
    "t80_compaction.jsonl": {"source": None, "select": None, "redact": source.scrub,
                              "bridge": "bridge-transcript-cse_fixture-remote-session.jsonl"},
}


def _shape(value):
    if isinstance(value, dict):
        return {"type": "object", "keys": {key: _shape(value[key]) for key in sorted(value)}}
    if isinstance(value, list):
        return {"type": "array", "items": [_shape(item) for item in value]}
    return {"type": "null" if value is None else "boolean" if isinstance(value, bool) else
            "integer" if isinstance(value, int) else "number" if isinstance(value, float) else
            "string" if isinstance(value, str) else type(value).__name__}


def signatures(path):
    lines = [line for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [json.loads(line) for line in lines]
    return {"line_count": len(lines), "rows": [
        {"sha256": hashlib.sha256(line.encode()).hexdigest(),
         "structure_sha256": hashlib.sha256(json.dumps(_shape(row), sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
        for line, row in zip(lines, rows)]}


def _manifest():
    return json.loads((HERE / "MANIFEST.json").read_text(encoding="utf-8"))


def manifest_matches(name, path=None):
    return signatures(path or OUT / name) == _manifest().get(name)


def sample(name):
    """Read one checked-in sample as JSON rows."""
    if name not in SAMPLES:
        raise KeyError(f"unknown fixture sample: {name}")
    return source.read_rows(OUT / name)


def place(name, config_dir, session=None):
    """Place a sample transcript and a matching bridge into a temporary config tree."""
    if name not in SAMPLES:
        raise KeyError(f"unknown fixture sample: {name}")
    session = str(session or LOCAL_SESSION)
    rows = sample(name)
    for row in rows:
        row["sessionId"] = session
    root = Path(config_dir)
    folder = root / "projects" / "fixture-project"
    folder.mkdir(parents=True, exist_ok=True)
    transcript = folder / f"{session}.jsonl"
    transcript.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    bridge = root / f"bridge-transcript-cse_{session}.jsonl"
    bridge.write_text(json.dumps({"session_id": session}, ensure_ascii=False) + "\n", encoding="utf-8")
    return transcript


def _write_manifest():
    data = {name: signatures(OUT / name) for name in sorted(SAMPLES)}
    (HERE / "MANIFEST.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                         encoding="utf-8")


def main(candidate_source, bridge_source, tool_source, records_source):
    inputs = {"candidate_source": candidate_source, "bridge_source": bridge_source,
              "tool_source": tool_source, "records_source": records_source}
    for name, spec in SAMPLES.items():
        if spec["source"]:
            rows = spec["select"](inputs[spec["source"]])
            source.dump(name, [spec["redact"](row) for row in rows])
    source._generate_t79()
    source._generate_t80()
    _write_manifest()


if __name__ == "__main__":
    if sys.argv[1:] == ["--t79-only"]:
        source._generate_t79()
        _write_manifest()
    elif len(sys.argv) == 5:
        main(*sys.argv[1:])
    else:
        raise SystemExit("usage: regenerate.py REAL_CANDIDATE_JSONL REAL_BRIDGE_JSONL REAL_TOOL_RESULTS_JSONL REAL_RECORDS_JSONL")
