# Teamwork for Claude Code

**A Projects-style team of Claude sessions, run entirely from claude.ai through Remote Control.**

One session is the **lead**. It plans the work, starts named **threads** (other sessions), routes every request to
the thread that already owns that area, watches them, recalls or reassigns work, and merges the results. Threads
know each other, hand work to the right owner, and report back in a fixed format. Each project keeps its own board,
instructions and memory in a `.teamwork/` folder.

It is a community skill, not an Anthropic product. It was built for people who want the
[Claude Code Projects beta](https://code.claude.com/docs/en/claude-projects.md) way of working but don't have access
yet, or who need threads that work in their own machine's folders.

## Why

| You want | Teamwork gives you |
|---|---|
| One coordinator for many sessions | A lead session that creates, messages, reads, interrupts and recalls threads |
| Follow-ups to land in the right place | Threads are split by ownership area; new requests go to the owner, so its history carries over |
| Threads that talk to each other | Every thread gets the roster; out-of-scope work is handed to the owner, with the lead copied |
| Work on your own machine | Remote Control threads run in your folders, driven from claude.ai |
| Work that needs no files | Cloud threads, for research, writing and self-contained repo work |
| A plan you approve first | Suggested threads + tasks with owners, dependencies and "done when", started only after your go-ahead |
| Several projects at once | One folder and one lead per project; a portfolio view across all of them |
| Recovery after a long session | The board, instructions and memory are re-injected at session start and after compaction |

See [`references/projects-parity.md`](references/projects-parity.md) for a feature-by-feature comparison with Projects beta.

## How it works

```
claude.ai (web / app)
   │  you talk to any session directly
   ▼
lead  ── Remote Control session, runs /teamwork, owns .teamwork/ for one project
   │  create_session / send_message / get_session / list_events / interrupt_session
   ├── rc thread "ui"      ── Remote Control session in the project folder (own git worktree)
   ├── rc thread "api"     ── Remote Control session in the project folder
   └── cloud thread "research" ── cloud session, reports through its transcript and git
```

- **Board**: `.teamwork/BOARD.md`, regenerated from `state.json` on every change: threads grouped like the Projects
  overview (Waiting on you, Ready for review, Working, Landing, Idle, Resolved), open questions, roster, plan.
- **Messages**: one-line headers such as `[tw:shop] TASK lead -> ui T3`; types TASK, FOLLOWUP, RECALL, ROSTER,
  ANSWER from the lead, and ACK, DONE, BLOCKED, QUESTION, HANDOFF, MEMORY from threads.
- **Status**: every thread ends every turn with a `<tw-status>` block (did, result, verified, assumed,
  couldn't reach, waiting on, handed off, next).

Which session can message which was tested on a real account; see [`references/transport.md`](references/transport.md).
The short version: Remote Control ↔ Remote Control works both ways; the lead can create and message cloud threads;
cloud threads cannot message a Remote Control lead, so the lead reads their transcripts instead.

## Requirements

- Claude Code with Remote Control, signed in to the same account you use on claude.ai.
- A Remote Control server running on the computer whose folders the threads should use. Everything else is done from claude.ai.
- Python 3 and git on that computer.
- For cloud threads that edit code: a GitHub repo your account can reach.

## Install (from claude.ai, no terminal)

Open a session in your Remote Control environment and ask it to run:

```bash
git clone https://github.com/<owner>/teamwork-for-claude-code ~/.claude/skills/teamwork
```

```bash
python3 ~/.claude/skills/teamwork/scripts/install.py --project <served folder>/<project> --served <served folder>
```

That is a dry run that prints every change. Re-run with `--apply` to write them. The installer:

1. keeps the skill at `~/.claude/skills/teamwork` (copies it there if you cloned elsewhere);
2. adds four allow rules to `~/.claude/settings.json` so threads can report without stalling on a permission prompt
   (`send_message`, `get_session`, `list_events`, `list_sessions` from the `claude-code-remote` tools), after writing a `.bak` copy;
3. adds a SessionStart hook to the project folder and the served folder so sessions see the board.

Then start a **new** session in the Remote Control environment (skills load at session start) and type:

```
/teamwork new <project>
```

## Commands

| | |
|---|---|
| `/teamwork new <name>` | create a project, set its goal and instructions |
| `/teamwork use <name>` | make this session the lead of an existing project |
| `/teamwork projects` | portfolio: every project, its lead, what waits on you |
| `/teamwork plan` | propose threads and tasks, wait for your go-ahead |
| `/teamwork go` | start approved work |
| `/teamwork status` | sweep all threads and report |
| `/teamwork route <request>` | send a request to the thread that owns it |
| `/teamwork recall <thread>` | stop a thread and get its work back |
| `/teamwork close` | wrap up the project |

Or just talk to the lead: "give this to the UI thread", "status of all threads", "run at most two at a time".

## Limits

- A Remote Control server holds 32 sessions by default, shared by every project and lead on it. Cloud threads don't count.
  There is no limit on threads otherwise; usage limits of your plan apply, and every thread is a full session.
- There is no sidebar pane: the board is a file and a status reply from the lead.
- Isolation is by convention (folder, titles `[project] thread · focus`, tags), not enforced by the platform.
  Threads appear in your normal session list.
- Cloud threads are pulled, not pushed: their status is as fresh as the lead's last sweep.

## Layout

```
SKILL.md                     lead playbook (what /teamwork does)
templates/worker-protocol.md protocol given to every thread via append_system_prompt
scripts/tw.py                the ledger: board, roster, plan, routing hints, hook output
scripts/install.py           installer (dry run by default)
references/                  transport matrix, setup, message formats, Projects comparison
```

## License

MIT. See [LICENSE](LICENSE).
