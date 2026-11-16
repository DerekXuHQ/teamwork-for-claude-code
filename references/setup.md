# One-time setup (claude.ai only, no terminal)

The user works only in claude.ai. Everything below is done by an RC session on the user's behalf, or by the user clicking in claude.ai. Never ask the user to open a terminal.

## §0 What has to exist

- **A Remote Control environment** serving a folder on the user's computer. It shows up in `list_environments` as `kind: bridge`, named `<computer>:<folder>:<id>`. Every RC session in it starts in that folder. Example: `my-laptop:workspace:ab12` serving `~/workspace`.
- **A cloud environment** (`kind: anthropic_cloud`, e.g. `Default`) for cloud threads.

Projects live as subfolders of the served folder (e.g. `~/workspace/shop/`), so one Remote Control environment can host several projects. RC threads `cd` into the project folder given in their protocol before working.

## §1 Install the skill

The skill must be in `~/.claude/skills/teamwork/` on the computer that runs the Remote Control server: RC sessions are Claude Code running there and load personal skills from that home folder. Cloud sessions never load it; they get the protocol through `append_system_prompt`.

Have any RC session run (after the files are on the machine):

```
python3 <path>/teamwork/scripts/install.py --project <project folder>            # dry run, shows changes
python3 <path>/teamwork/scripts/install.py --apply --project <project folder>    # writes them
```

Then start a **new** RC session (skills load at session start) and check `/teamwork` is listed.

## §2 Let RC threads report without stalling

In the test, an RC child stopped at "Waiting on permission: Send Message". `install.py` adds these to `~/.claude/settings.json` on that computer, which every RC session there reads:

```json
{ "permissions": { "allow": [
  "mcp__claude-code-remote__send_message",
  "mcp__claude-code-remote__get_session",
  "mcp__claude-code-remote__list_events",
  "mcp__claude-code-remote__list_sessions"
] } }
```

`create_session` and `interrupt_session` are pre-approved only for the lead while `/teamwork` runs (skill `allowed-tools`); archiving always asks. If the organisation sets the connector to "ask", allow rules are ignored and the user approves prompts in each thread.

## §3 Project hook and the lead

`install.py --project` adds a SessionStart hook to `<served folder>/.claude/settings.json` (RC sessions start in the served folder, so that is where the hook must live) and to `<project>/.claude/settings.json`. It runs `tw.py context`:

- inside a project folder: injects that project's board, instructions and memory (≤ 9 000 characters), at start, resume and after compaction;
- in the served folder: injects only a one-line index of the teamwork projects below it, nothing else.

To start the lead: in claude.ai, open a new session in the Remote Control environment and type `/teamwork new <project>`. The lead creates the project subfolder and `.teamwork/` there.

## §4 Isolation between threads editing the same repo

RC threads all start in the same served folder. Each RC thread creates its own git worktree for its branch before editing (the worker protocol says so). For non-git folders, give threads non-overlapping `owns`.

## §5 Cloud threads

They need a cloud environment and, to edit code, a GitHub repo the account can reach. The lead passes `source_url`, `source_revision` and `outcome_branch`. They cannot read the project folder: anything they need goes into the repo or the TASK message.

## §6 If something is refused

- A trust card appears in claude.ai the first time the lead messages or creates sessions: approve it for the lead only.
- Never approve a trust card for a cloud thread (transport.md explains why).
- RC threads stop if the Remote Control server on the computer stops or the computer sleeps; `get_session` then shows `connection_status: disconnected`. Tell the user; nothing else can restart it from claude.ai.
