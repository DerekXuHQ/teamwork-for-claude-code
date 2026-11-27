#!/usr/bin/env python3
"""Source transcript selection and redaction used by regenerate.py."""
import getpass, hashlib, json, os, re, socket
from pathlib import Path

OUT = Path(__file__).resolve().parent
LOCAL_SESSION = "00000000-0000-4000-8000-000000000001"

def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def compact_rows(roots):
    """Select a real compaction summary and the transcript rows through its next assistant replies."""
    for root in roots:
        for path in sorted(Path(root).glob("**/*.jsonl")):
            try: rows = read_rows(path)
            except (OSError, ValueError, UnicodeError, RecursionError): continue
            for index, row in enumerate(rows):
                message = row.get("message") or {}
                if not (row.get("isCompactSummary") or (isinstance(message, dict) and message.get("isCompactSummary"))):
                    continue
                tail = rows[index + 1:]
                for offset, following in enumerate(tail):
                    content = ((following.get("message") or {}).get("content")
                               if isinstance(following.get("message"), dict) else None)
                    has_tool_use = isinstance(content, list) and any(
                        isinstance(block, dict) and block.get("type") in ("tool_use", "tool_call")
                        and str(block.get("name") or "").lower() == "read"
                        and str((block.get("input") or {}).get("file_path") or
                                (block.get("arguments") or {}).get("file_path") or "").endswith("SKILL.md")
                        for block in content)
                    if following.get("type") == "assistant" and has_tool_use:
                        ids = {str(block.get("id")) for block in content if isinstance(block, dict)
                               and block.get("type") in ("tool_use", "tool_call") and block.get("id")}
                        result_index = next((j for j, item in enumerate(tail[offset + 1:], offset + 1)
                                             if any(isinstance(block, dict) and block.get("type") == "tool_result"
                                                    and str(block.get("tool_use_id")) in ids
                                                    for block in ((item.get("message") or {}).get("content") or []))), None)
                        end = next((j for j, item in enumerate(tail[(result_index + 1 if result_index is not None else offset + 1):],
                                                               result_index + 1 if result_index is not None else offset + 1)
                                    if item.get("type") == "assistant"), result_index)
                        selected = [row, *tail[:end + 1 if end is not None else offset + 1]]
                        return selected
    raise ValueError("no real compaction summary followed by an assistant row found")


def _redact_compaction(value, key=None):
    value = scrub(value, key)
    if isinstance(value, dict):
        result = {}
        for name, item in value.items():
            if name == "file_path" and isinstance(item, str):
                result[name] = "fixture/SKILL.md"
            elif name in {"content", "lastPrompt", "addedBlocks", "addedLines", "addedNames"}:
                if isinstance(item, str):
                    result[name] = "fixture private content"
                elif name in {"addedLines", "addedNames"} and isinstance(item, list):
                    result[name] = ["fixture private content" for _ in item]
                else:
                    result[name] = _redact_compaction(item, name)
            else:
                result[name] = _redact_compaction(item, name)
        return result
    if isinstance(value, list):
        return [_redact_compaction(item, key) for item in value]
    return value


def blocks(row):
    message = row.get("message") or {}
    content = message.get("content", []) if isinstance(message, dict) else []
    return content if isinstance(content, list) else []


def scrub(value, key=None):
    """Replace local identity and private values while retaining real record shape."""
    if isinstance(value, list):
        return [scrub(item, key) for item in value]
    if isinstance(value, dict):
        result = {}
        skill_listing = "skillCount" in value and "names" in value
        for name, item in value.items():
            if name.startswith(("env_","cse_","toolu_")):
                name = "fixture-tool-id" if name.startswith("toolu_") else "fixture-id"
            if name in {"sessionId", "session_id"}:
                result[name] = LOCAL_SESSION
            elif name in {"uuid", "parentUuid", "promptId", "turnPromptId", "messageId", "requestId",
                          "deliveryId", "commandUuid", "id", "tool_use_id", "toolUseID", "sourceToolAssistantUUID"}:
                result[name] = "fixture-id"
            elif name in {"cwd", "path", "file_path", "command"}:
                result[name] = "/fixture/project" if name == "cwd" else "fixture operation"
            elif name in {"title", "project", "project_name"}:
                result[name] = "fixture project"
            elif name in {"signature", "thinking"}:
                result[name] = "fixture private content"
            elif skill_listing and name == "names":
                result[name] = ["fixture-skill"]
            elif skill_listing and name == "digests":
                result[name] = ["fixture-digest"]
            elif skill_listing and name == "skillCount":
                result[name] = 1
            elif skill_listing and name == "content":
                result[name] = "fixture-skill"
            elif name == "text":
                try:
                    payload = json.loads(item) if isinstance(item, str) else None
                except ValueError:
                    payload = None
                result[name] = (json.dumps(_mcp_payload(payload), ensure_ascii=False)
                                if payload is not None else "fixture private content")
            elif value.get("type") == "tool_result" and name == "content" and isinstance(item, str):
                result[name] = "fixture private content"
            else:
                result[name] = scrub(item, name)
        return result
    if isinstance(value, str):
        if value.startswith("/fixture/"):
            return value
        value = re.sub(r"(?:anthropic-skills|plugin):[A-Za-z0-9_.-]+", "fixture-skill", value, flags=re.I)
        private_names={getpass.getuser(),Path.home().name}-{"", "."}; hostname=socket.gethostname()
        if private_names: value=re.sub(r"(?<![A-Za-z0-9_.-])(?:"+"|".join(map(re.escape,private_names))+r")(?![A-Za-z0-9_.-])","fixture-user",value)
        if hostname: value=value.replace(hostname,"fixture-host")
        def scrub_home_path(match):
            suffix = Path(match[0].rstrip(".,;:)]}")).suffix
            return "/fixture/home/file" + suffix
        value = re.sub(r"(?<!/fixture)~/(?:[^\s\"'<>()[\]{}|,;]+)", scrub_home_path, value)
        value = re.sub(r"(?<!/fixture)/(?:Users|home)/[^\s\"'<>()[\]{}|,;]+", scrub_home_path, value)
        value = re.sub(r"-(?:Users|home)-[A-Za-z0-9._-]+", "-fixture-project", value)
        value = re.sub(r"/(?:private/)?tmp/claude-\d+", "/fixture/tmp", value)
        value = re.sub(r"memory/[^/\s\"']+\.md", "memory/fixture-note.md", value)
        value = re.sub(r"(?<![A-Za-z0-9])[0-9a-fA-F]{12,}(?![A-Za-z0-9])", "fixture-id", value)
        value = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "fixture-email", value)
        value = re.sub(r"\b(?:env|cse|toolu)_[A-Za-z0-9_-]+\b", lambda m:"fixture-tool-id" if m[0].startswith("toolu_") else "fixture-id", value)
        value = re.sub(r"\b[A-Za-z0-9-]+/[A-Za-z0-9_.-]+\b", "fixture-owner/fixture-repo", value) if key=="remote" else value
        value = re.sub(r"github\.com[:/]([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+)", "github.com/fixture-owner/fixture-repo", value)
        value = re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "fixture-id", value)
        value = re.sub(r"session_[A-Za-z0-9_-]+", "session_fixture-lead", value)
        value = re.sub(r"(?i)work" + "less", "fixture-project", value)
        value = value.replace("teamwork-for-claude-code", "fixture-project").replace("tw/t71demo/demo", "fixture-branch")
        # Scrub path values by shape, independent of whether the source schema
        # calls the field file_path, filePath, displayPath, cwd, or something else.
        absolute_path = re.compile(r"(?<![A-Za-z0-9:+<])/(?:[^\s\"'<>()[\]{}|,;]+/)*[^\s\"'<>()[\]{}|,;]+")
        value = absolute_path.sub(lambda match: match[0] if match[0].startswith("/fixture/") else "/fixture/path", value)
        return value
    return value


def _private_content(value):
    """Scrub result payload values while preserving the observed JSON value shape."""
    if isinstance(value, dict):
        return {key: ("fixture private content" if key == "text" else _private_content(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_private_content(item) for item in value]
    if isinstance(value, str):
        return "fixture private content"
    return value


def _mcp_payload(value, key=None):
    """Sanitize parsed MCP JSON while retaining the result schema and useful status fields."""
    if isinstance(value, dict):
        result = {}
        for name, item in value.items():
            if name.startswith(("env_","cse_","toolu_")):
                name = "fixture-tool-id" if name.startswith("toolu_") else "fixture-id"
            if name in {"session_id", "sessionId", "uuid", "id", "tool_use_id"}:
                result[name] = "fixture-id"
            elif name in {"cwd", "path", "file_path"}:
                result[name] = "/fixture/project"
            elif name in {"title", "project", "project_name"}:
                result[name] = "fixture project"
            elif name in {"message", "prompt", "text"}:
                result[name] = "fixture private content"
            else:
                result[name] = _mcp_payload(item, name)
        return result
    if isinstance(value, list):
        return [_mcp_payload(item, key) for item in value]
    if isinstance(value, str):
        return scrub(value, key)
    return value


def _sanitize_mcp_result(row):
    for block in blocks(row):
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        content = block.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text" or not isinstance(item.get("text"), str):
                continue
            try:
                payload = json.loads(item["text"])
            except ValueError:
                item["text"] = "fixture private content"
            else:
                item["text"] = json.dumps(_mcp_payload(payload), ensure_ascii=False)

def dump(name, rows):
    path = OUT / name
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _shape(value):
    if isinstance(value, dict):
        return {"type": "object", "keys": {key: _shape(value[key]) for key in sorted(value)}}
    if isinstance(value, list):
        return {"type": "array", "items": [_shape(item) for item in value]}
    if value is None:
        kind = "null"
    elif isinstance(value, bool):
        kind = "boolean"
    elif isinstance(value, int):
        kind = "integer"
    elif isinstance(value, float):
        kind = "number"
    elif isinstance(value, str):
        kind = "string"
    else:
        kind = type(value).__name__
    return {"type": kind}


def _has_dialogue_or_tool_content(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"message", "content", "text"} and child is not None:
                return True
            if key in {"tool_use", "tool_result"}:
                return True
            if _has_dialogue_or_tool_content(child):
                return True
    elif isinstance(value, list):
        return any(_has_dialogue_or_tool_content(item) for item in value)
    return False


def _quiz_rows(path):
    rows = read_rows(path)
    quiz_prompt = next((i for i, row in enumerate(rows) if row.get("type") == "user"
                        and "What is the current seat's branch name?" in json.dumps(row, ensure_ascii=False)), None)
    if quiz_prompt is None:
        raise ValueError("candidate source has no user QUIZ prompt")
    prompt = rows[quiz_prompt]
    question_text = json.dumps(prompt, ensure_ascii=False)
    answer_call = next((i for i in range(quiz_prompt + 1, len(rows))
                        if any("send_message" in str(block.get("name", "")) for block in blocks(rows[i])
                               if isinstance(block, dict) and block.get("type") == "tool_use")
                        and "QUIZ" in json.dumps(rows[i], ensure_ascii=False)), None)
    if answer_call is None:
        raise ValueError("candidate source has no send_message answer to its QUIZ")
    call_id = next(block.get("id") for block in blocks(rows[answer_call])
                   if isinstance(block, dict) and block.get("type") == "tool_use"
                   and "send_message" in str(block.get("name", "")))
    answer_result = next((i for i in range(answer_call + 1, len(rows))
                          if any(block.get("type") == "tool_result" and block.get("tool_use_id") == call_id
                                 for block in blocks(rows[i]) if isinstance(block, dict))), None)
    selected = [(quiz_prompt, prompt), (answer_call, rows[answer_call])]
    if answer_result is not None:
        selected.append((answer_result, rows[answer_result]))
    metadata = next((i for i, row in enumerate(rows)
                     if row.get("type") in {"file-history-snapshot", "file-history-delta"}
                     and not _has_dialogue_or_tool_content(row)), None)
    if metadata is None:
        metadata = next((i for i, row in enumerate(rows) if not _has_dialogue_or_tool_content(row)), None)
    if metadata is not None:
        selected.append((metadata, rows[metadata]))
    # Keep the source order and only records required to retain the observed quiz exchange.
    return [scrub(row) for _, row in sorted(selected)]


def _old_thread_rows(peer_path):
    peer_rows = read_rows(peer_path)
    indexed = {}
    peer_count = 0
    for index, row in enumerate(peer_rows):
        message = row.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        if (peer_count < 2 and row.get("type") == "user" and isinstance(content, str)
                and "<cross-session-message" in content and "[tw:" in content):
            indexed[index] = row
            peer_count += 1
    bash_pair_added = False
    for index, row in enumerate(peer_rows):
        for block in blocks(row):
            if (not bash_pair_added and isinstance(block, dict) and block.get("type") == "tool_use"
                    and block.get("name") == "Bash"):
                tool_id = block.get("id")
                indexed[index] = row
                result_index = next((i for i in range(index + 1, len(peer_rows)) if any(
                    isinstance(candidate, dict) and candidate.get("type") == "tool_result"
                    and candidate.get("tool_use_id") == tool_id for candidate in blocks(peer_rows[i]))), None)
                if result_index is not None:
                    indexed[result_index] = peer_rows[result_index]
                bash_pair_added = result_index is not None
                break
    if not indexed:
        raise ValueError("record source has no cross-session rows or Bash tool result pair")
    return [scrub(indexed[i]) for i in sorted(indexed)]


def _tool_rows(path):
    rows = read_rows(path)
    calls = {}
    selected = []
    for row in rows:
        for block in blocks(row):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and any(kind in str(block.get("name", ""))
                    for kind in ("get_session", "list_events", "list_triggers")):
                calls[block.get("id")] = block.get("name")
                selected.append(row)
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                selected.append(row)
                del calls[block["tool_use_id"]]
    found = {kind for row in selected for block in blocks(row) if isinstance(block, dict)
             for kind in ("get_session", "list_events", "list_triggers") if kind in str(block.get("name", ""))}
    if not {"get_session", "list_events", "list_triggers"}.issubset(found):
        raise ValueError("tool source must contain get_session, list_events, and list_triggers")
    sanitized = [scrub(row) for row in selected]
    for row in sanitized:
        _sanitize_mcp_result(row)
    return sanitized

def _bridge_rows(path):
    rows = read_rows(path)
    hit = next((row for row in rows if re.fullmatch(r"[0-9a-fA-F-]{36}", str(row.get("session_id", "")))), None)
    if hit is None:
        raise ValueError("bridge source has no local UUID in session_id")
    first = scrub(rows[0]) if rows else {"type": "control_request"}
    first.pop("session_id", None)
    first.pop("sessionId", None)
    local = scrub(hit)
    local["session_id"] = LOCAL_SESSION
    return [first] + ([] if local == first else [local])


def _redact_t79(value):
    """Allow-list the hook event shape and replace every free-text value."""
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if key == "tool_name" and isinstance(child, str):
                result[key] = child
            elif key == "tool_input":
                result[key] = _fixture_tool_input(child)
            elif key == "cwd" and isinstance(child, str):
                result[key] = "/fixture/project"
            elif key == "session_id" and isinstance(child, str):
                result[key] = "session_fixture_hook"
            elif key == "hook_event_name" and isinstance(child, str):
                result[key] = child
            elif key == "transcript_path" and isinstance(child, str):
                result[key] = "[fixture path]"
            else:
                # Keep key/type shape; all other strings are free text.
                result[key] = _placeholder_shape(child)
        return result
    return _placeholder_shape(value)


def _placeholder_shape(value):
    if isinstance(value, dict):
        return {key: _placeholder_shape(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_placeholder_shape(child) for child in value]
    if isinstance(value, str):
        return "[fixture text]"
    return value


def _fixture_tool_input(value, key=None):
    """Keep key/type shape, bounded enums and booleans; redact every free-text value."""
    if isinstance(value, dict):
        return {name: _fixture_tool_input(child, name) for name, child in value.items()}
    if isinstance(value, list):
        return [_fixture_tool_input(child, key) for child in value]
    if not isinstance(value, str):
        return value
    if key == "initiation" and value in {"own_initiative", "user_requested", "delegated"}:
        return value
    return "[fixture text]"


def _find_tool_use(name, roots):
    for root in roots:
        if not root.exists(): continue
        for path in root.rglob("*.jsonl"):
            try:
                with path.open(encoding="utf-8", errors="replace") as source:
                    for line in source:
                        if name not in line: continue
                        row = json.loads(line)
                        for block in blocks(row):
                            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == name:
                                cwd = row.get("cwd")
                                sid = row.get("session_id") or row.get("sessionId")
                                if not isinstance(cwd, str) or not sid: continue
                                return {"session_id": sid, "transcript_path": "[fixture transcript]",
                                        "cwd": cwd, "hook_event_name": "PreToolUse", "tool_name": name,
                                        "tool_input": block.get("input")}
            except (OSError, ValueError, UnicodeError, RecursionError):
                continue
    raise ValueError("no real tool_use sample found for " + name)


def _normal_id(value):
    value = str(value or "").strip()
    for prefix in ("session_", "cse_"):
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def _lead_ids_for_cwd(cwd):
    if not isinstance(cwd, str) or not cwd:
        return set()
    current = Path(cwd).expanduser()
    for directory in (current, *current.parents):
        state_path = directory / ".teamwork" / "state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError, RecursionError):
            continue
        lead = state.get("lead") if isinstance(state, dict) else None
        if not isinstance(lead, dict):
            continue
        ids = {_normal_id(lead.get("session_id"))}
        history = lead.get("sessions")
        if isinstance(history, list):
            for item in history:
                ids.add(_normal_id(item.get("session_id") or item.get("id")) if isinstance(item, dict)
                        else _normal_id(item))
        return {value for value in ids if value}
    return set()


def _desktop_metadata_pair(root):
    records = {}
    for path in root.glob("*/*/local_*.json"):
        try: value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError, RecursionError): continue
        if isinstance(value, dict) and isinstance(value.get("sessionId"), str): records[value["sessionId"]] = value
    pair = next(((item, records.get(item.get("spawnedFrom", {}).get("sessionId")))
                 for item in records.values() if isinstance(item.get("spawnedFrom"), dict)
                 and isinstance(item.get("cwd"), str)
                 and records.get(item["spawnedFrom"].get("sessionId"), {}).get("bridgeSessionIds")
                 and _lead_ids_for_cwd(item.get("cwd")) & {
                     _normal_id(value) for value in records[item["spawnedFrom"]["sessionId"]].get("bridgeSessionIds", [])
                     if isinstance(value, str)}), None)
    if pair is None: raise ValueError("no real child/parent metadata pair linked to a current or historical project lead")
    child, parent = pair
    lead_ids = _lead_ids_for_cwd(child.get("cwd"))
    linked_id = next(value for value in parent.get("bridgeSessionIds", [])
                     if isinstance(value, str) and _normal_id(value) in lead_ids)
    result = []
    child_id = "local_00000000-0000-4000-8000-000000000001"
    parent_id = "local_00000000-0000-4000-8000-000000000002"
    for index, item in enumerate((child, parent)):
        selected = {key: item[key] for key in ("sessionId", "cwd", "isArchived", "spawnedFrom", "bridgeSessionIds") if key in item}
        selected = _metadata_allowlist(selected, "/fixture/project")
        selected["sessionId"] = child_id if index == 0 else parent_id
        if index == 0:
            selected["cwd"] = "/fixture/project/nested"
            selected["spawnedFrom"] = {"sessionId": parent_id}
        if index == 1:
            selected["cwd"] = "/fixture/project"
            selected["bridgeSessionIds"] = ["cse_fixture_lead"]
        result.append(selected)
    return result


def _metadata_allowlist(value, project_root):
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            if key == "cwd" and isinstance(child, str):
                try:
                    rel = Path(child).relative_to(Path(project_root))
                    out[key] = "/fixture/project" + ("/" + str(rel) if str(rel) != "." else "")
                except ValueError:
                    out[key] = "/fixture/project"
            elif key in {"isArchived", "sideSession"} and isinstance(child, bool):
                out[key] = child
            elif key == "sessionId" and isinstance(child, str):
                out[key] = "[fixture session id]"
            elif key == "bridgeSessionIds" and isinstance(child, list):
                out[key] = ["cse_fixture_lead" for _ in child]
            else:
                out[key] = _placeholder_shape(child)
        return out
    return _placeholder_shape(value)


def _generate_t79():
    roots = [Path.home() / ".claude/projects", Path.home() / ".claude-b/projects"]
    start = _find_tool_use("mcp__ccd_session__start_session", roots)
    create = _find_tool_use("mcp__claude-code-remote__create_session", [roots[1]])
    dump("t79_hook_start_session.jsonl", [_redact_t79(start)])
    dump("t79_hook_create_session.jsonl", [_redact_t79(create)])
    metadata_root = Path(os.environ.get("TEAMWORK_CLAUDE_SESSIONS_DIR") or
                         (Path.home() / "Library/Application Support/Claude/claude-code-sessions"))
    dump("t79_nested_desktop_sessions.jsonl", _desktop_metadata_pair(metadata_root))


def _generate_t80():
    roots = sorted(Path.home().glob(".claude*/projects"))
    dump("t80_compaction.jsonl", [_redact_compaction(row) for row in compact_rows(roots)])


def build_sources(candidate_source, bridge_source, tool_source, records_source):
    dump("t71_real_candidate.jsonl", _quiz_rows(candidate_source))
    dump("bridge-transcript-cse_fixture-remote-session.jsonl", _bridge_rows(bridge_source))
    dump("t71_real_tool_results.jsonl", _tool_rows(tool_source))
    dump("t71_old_thread.jsonl", _old_thread_rows(records_source))
    _generate_t79()
