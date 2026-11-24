# Teamwork for Claude Code

[English](README.md) · [中文](README.zh.md)

**Run a team of Claude sessions from claude.ai. One lead plans the work, named threads do it, and every request you
make comes back to you.**

One session is the **lead**. It plans the work and starts named **threads** (other sessions). It sends each request to
the thread that already owns that area, watches the threads, recalls or reassigns work, and merges the results.
Threads know about each other, pass work to the right owner, and report back in a fixed format. Each project keeps its
board, instructions and memory in a `.teamwork/` folder, so a long session or a restart loses nothing.

This is a community skill, not an Anthropic product. It is for people who want to work the way the
[Claude Code Projects beta](https://code.claude.com/docs/en/claude-projects.md) works but don't have access yet, or
who need threads that work in folders on their own computer.

> **Screenshot placeholder:** the lead's status reply next to `.teamwork/BOARD.md` (threads grouped as Waiting on
> you / Ready for review / Working).

## 30-second start

You need Claude Code with Remote Control running on the computer whose folders the threads should use, signed in to
the account you use on claude.ai. Open a session in that Remote Control environment and ask it to run these three
commands. Replace `<served folder>` with the folder your Remote Control server serves and `<project>` with your
project folder inside it.

**1. Get the skill**

```bash
git clone https://github.com/DerekXuHQ/teamwork-for-claude-code <served folder>/teamwork-for-claude-code
```

**2. See what the installer would change (a dry run, nothing is written)**

```bash
python3 <served folder>/teamwork-for-claude-code/scripts/install.py --link --project <served folder>/<project> --served <served folder>
```

**3. Install**

```bash
python3 <served folder>/teamwork-for-claude-code/scripts/install.py --link --project <served folder>/<project> --served <served folder> --apply
```

Then start a **new** session in the same environment (skills load when a session starts) and type:

```
/teamwork new <project>
```

The lead asks for the goal, proposes threads and tasks, and starts them after you say go.

**Check the config dir first.** The installer writes to `$CLAUDE_CONFIG_DIR` if it is set, otherwise `~/.claude`, and
`--help` and every dry run print the one they found. If your Remote Control server runs with a
different one (for example `CLAUDE_CONFIG_DIR=~/.claude-work`), add `--config-dir ~/.claude-work`. Otherwise `/teamwork`
will not be found.

## What the installer does

It never removes anything. It merges into existing settings and writes a `.bak` copy before it changes a settings
file.

1. Links the skill into `<config dir>/skills/teamwork`. With `--link` (recommended) that is a symlink to your clone,
   so `git pull` upgrades it. Without `--link` it is a frozen copy. An existing `skills/teamwork` is never overwritten.
2. Adds permission rules to `<config dir>/settings.json`: allow rules so threads can report without stopping at a
   permission prompt, the scratch folders as `additionalDirectories`, and ask rules for big decisions (a safety net
matched on command text, not a security boundary). `--no-allow` skips the allow rules.
3. With `--project` and `--served`: adds a SessionStart hook to both folders, so every session there sees the
   board, and writes the folder rule set to their `.claude/settings.local.json` (and the project's worktrees').

Other options: `--pre-push` adds a git pre-push hook that blocks pushes containing words or paths listed in
`.teamwork/PRIVATE.md`. `--default-mode {auto,acceptEdits,default}` sets the mode every new session on this computer
starts in. Teamwork works best in `auto`, but this affects every session on the computer, so the choice is yours.
`python3 scripts/install.py --help` lists every option.

## Upgrade and roll back

With `--link`, every merge into the checked-out branch is live in every new session at once. To let changes reach your sessions only when
you decide, use release snapshots:

```bash
python3 <served folder>/teamwork-for-claude-code/scripts/release.py status
python3 <served folder>/teamwork-for-claude-code/scripts/release.py publish --ref main
python3 <served folder>/teamwork-for-claude-code/scripts/release.py publish --ref main --apply
python3 <served folder>/teamwork-for-claude-code/scripts/release.py rollback --apply
python3 <served folder>/teamwork-for-claude-code/scripts/release.py edge --apply
```

`status` shows where each config dir points. `publish` snapshots a ref and switches every config dir that links the skill to it (a dry
run until `--apply`). `rollback` goes back to the previous release; after only one publish it refuses and changes nothing. `edge` runs
the clone live again. Old snapshots are never deleted. Details: [`references/setup.md`](references/setup.md).

## Teamwork and Projects beta

| | Claude Code Projects beta | Teamwork |
|---|---|---|
| Coordinator | A project conversation | A lead session running `/teamwork` |
| A thread is for | One task | One ownership area; follow-ups go to it, so its history carries over |
| Where threads run | Cloud by default, local via Remote Control | Remote Control in your folders (own git worktree each) or cloud, chosen per thread |
| Threads talking to each other | Not documented | Every thread has the roster; out-of-scope work is handed to the owner, with the lead copied |
| Plan before work | Suggested threads | A task list with owners, dependencies and "done when", started only after you approve |
| Memory | `MEMORY.md` for cloud threads | `.teamwork/MEMORY.md` pasted into every thread, Remote Control threads included |
| Recovery | From recent messages and memory | From the ledger on disk, re-injected at session start and after compaction |
| UI | A sidebar overview | A board file and the lead's status reply; no sidebar |
| Isolation | Project threads hidden from the normal list | By convention: folder, titles `[project] thread · focus`, tags |
| Access | Beta | Any account with Claude Code and Remote Control |

Feature-by-feature comparison: [`references/projects-parity.md`](references/projects-parity.md).

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

- **Every request is tracked until you close it.** The lead stores your words, routes them, checks what comes back,
  reports it, and keeps listing it until you say it is done or dropped.
- **Board**: `.teamwork/BOARD.md`, regenerated from `state.json` on every change. Threads are grouped like the Projects
  overview (Waiting on you, Ready for review, Working, Landing, Idle, Resolved), with open questions, the roster and
  the plan.
- **Messages**: one-line headers such as `[tw:shop] TASK lead -> ui T3`. The lead sends TASK, FOLLOWUP, RECALL, ROSTER
  and ANSWER; threads send ACK, DONE, BLOCKED, QUESTION, HANDOFF and MEMORY.
- **Status**: every thread ends every turn with a `<tw-status>` block (did, result, verified, assumed, couldn't reach,
  waiting on, handed off, next).

**The mechanism in full**: [`docs/framework.md`](docs/framework.md) ([中文](docs/framework-zh.md)) specifies the roles,
the ledger, the message protocol, the state machines, routing, consent and recovery, and what another host needs to
run the same framework. Start with its "Overview in 5 minutes".

Which session can message which was tested on a real account; see [`references/transport.md`](references/transport.md).
In short: Remote Control sessions can message each other in both directions; the lead can create and message
cloud threads; cloud threads cannot message a Remote Control lead, so the lead reads their transcripts instead.

> **Screenshot placeholder:** a thread's `DONE` message and the lead's report to the user.

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

Or just talk to the lead: "give this to the UI thread", "status of all threads", "run at most two at a time". The lead
also checks results before reporting them, hands itself over to a new session when needed, and keeps private words out
of pushes; [`SKILL.md`](SKILL.md) has the full playbook.

## Requirements

- Claude Code with Remote Control, signed in to the same account you use on claude.ai.
- A Remote Control server running on the computer whose folders the threads should use. Everything else is done from
  claude.ai.
- Python 3 and git on that computer.
- For cloud threads that edit code: a GitHub repo your account can reach.

## Limits

- A Remote Control server holds 32 sessions by default, shared by every project and lead on it. Cloud threads don't
  count. There is no other limit on threads, but your plan's usage limits apply and every thread is a full session.
- There is no sidebar pane: the board is a file plus a status reply from the lead.
- Isolation is by convention (folder, titles, tags), not enforced by the platform. Threads appear in your normal
  session list.
- Cloud threads are pulled, not pushed: their status is as fresh as the lead's last sweep.

## Layout

```
SKILL.md                     lead playbook (what /teamwork does)
templates/worker-protocol.md protocol given to every thread via append_system_prompt
scripts/tw.py                the ledger: board, roster, plan, routing hints, hook output
scripts/install.py           installer (dry run by default)
scripts/release.py           release snapshots: publish, roll back, status
docs/framework.md            the mechanism as a reusable framework (中文: docs/framework-zh.md)
references/                  transport matrix, setup, message formats, Projects comparison
```

## License

MIT. See [LICENSE](LICENSE).
