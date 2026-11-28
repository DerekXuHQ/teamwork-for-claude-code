# Teamwork for Claude Code

[English](README.md) · [中文](README.zh.md)

**Run a team of Claude sessions from claude.ai. One lead plans the work, named threads do it, and every request you
make stays on the ledger until you close it.**

**Used it? [File a field report](https://github.com/DerekXuHQ/teamwork-for-claude-code/issues/new?template=field-report.yml)** — which parts worked and which didn't.

One session is the **lead**. It plans the work, starts named **threads** (other sessions), sends each request to
the thread that already owns that area, and merges the results. Each project keeps its board, instructions and memory
in a `.teamwork/` folder, and a new session starts from that ledger rather than from what the last session remembered.

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

## Features

What is in place today, by what you see as the user.

<!-- features:start -->
### Opening <!-- group:A -->
- **Says who it is.** The lead's first reply names the project it leads and whether permission hosting took effect; a lead that takes over also says what it inherited and what each thread was doing. <!-- f:A1 A6 -->
- **Proposes, then asks once.** It proposes a starting set of threads from your goal and puts the questions only you can answer on one sheet, each with a suggested answer, so "as suggested" is a full reply. <!-- f:A3 A4 -->
- **No silent downgrades.** When permissions, model, scope or recovery don't go as planned, you are told at that point, with options. <!-- f:A7 -->

### What you can see <!-- group:B -->
- **First-run tour.** After setup the lead tells you how many of your requests are on the ledger, what is on the board and where to look. <!-- f:B2 -->
- **A receipt for every request.** You say something; the lead answers with its ledger number and the thread it went to, then reports each step: dispatched, accepted, reviewed, merged. <!-- f:B3 B4 -->
- **Merges and collisions on the board.** You can see what is waiting to merge and its review verdict, and where two threads touch the same area or one waits on another. <!-- f:B8 B13 -->
- **Which deliverable, which version.** The first line of a reply says what kind of deliverable it is (an analysis, a new proposal, raw material); progress links point to the latest version, and old ones are marked "don't use". <!-- f:B9 B10 -->
- **One shared issue list.** A problem is reported once, and the whole team sees when it is fixed. <!-- f:B12 -->

### Requests and features <!-- group:C -->
- **Traceable both ways.** Each request maps to a feature or task and each feature traces back to your words; a task keeps your original wording next to the lead's summary. <!-- f:C2 C7 -->
- **End-of-turn self-check.** The lead checks four gaps: heard but not recorded, recorded but not dispatched, dispatched but not answered, answered but not reported. Requests made during a sprint and the lead's own proposals waiting on you are on the ledger too. <!-- f:C5 C9 C10 -->
- **Examples are not orders.** An example, an analogy or a passing remark is not dispatched as a task, and a preference is recorded with its limits so it is not over-applied. <!-- f:C8 C11 -->
- **Upgrades keep the old ledger.** Old records are sorted into the newer statuses; you see the preview before it is applied. <!-- f:C12 -->
- **Root-cause flow.** When the same area breaks or is corrected a second time, the lead stops patching, lists the symptoms, looks for the shared cause and asks you once: fix the root, or patch for now.
- **Tasks start from what exists.** Every task lists the material that already exists on its topic, and the thread reports what it used.

### Permissions, models and quota <!-- group:D -->
- **GPT outages explained.** When GPT can't be used you are told why and when it comes back, with a default that applies if you don't answer. <!-- f:D2 -->
- **Permission and seat levels.** Sessions the lead opens follow the permission level you picked. With "open freely" the lead opens threads by topic without asking; with "ask me first" it asks before each one. <!-- f:D3 D4 -->
- **Approved versus blocked.** An outward action you already approved is carried out for you; one stopped by the platform's safety check is brought to you, not worked around. <!-- f:D6 -->
- **Rules for work given to GPT.** It follows fixed rules (for example, no editing tests to fit the code), Claude reviews the result, and private material stays out. <!-- f:D7 D8 -->
- **Quota pacing and model routing.** The lead checks quota about once an hour and advises which model should take new work as limits get close. Work a model does poorly moves up a level and the report says so; threads keep their seats and hand work to subagents on another model. <!-- f:D10 D12 D13 D14 -->

### Watching threads <!-- group:E -->
- **Session directory.** One list of every session in the lead's chat: lead first, grouped, each a clickable link. <!-- f:E1 -->
- **Told before the clicks.** Before opening a batch of threads the lead says how many confirmations may come, and what can still ask you once hosting is on. <!-- f:E6 E11 -->
- **Stuck threads recognised.** The lead can tell when a thread is stuck, for example on temp-file cleanup or at an approval box. <!-- f:E8 -->
- **Threads are their own sessions.** The lead opens each thread as a separate session; the known way to start one nested under the lead is refused.
- **Disk space and machine health in the sweep.** The sweep reports both, cleans up what is safe to clean, and lists the rest for you to decide.

### Decisions and defaults <!-- group:F -->
- **Standing requirements travel with tasks.** A long-term requirement of yours goes into the task template and comes with every task, and threads work to the same short rule: think the request through, stay inside it. <!-- f:F4 F9 -->
- **Defaults and promises on the record.** A decision made for you by default is logged, so you can look it up and change it. A promise is either done with evidence or has a status: waiting on you, dropped (with the reason), landed (with evidence). <!-- f:F5 F6 F7 -->

### Deliverables <!-- group:G -->
- **Openable links.** A deliverable is a link, and its first line says draft or final and what is still missing. <!-- f:G1 -->
- **Fresh lead without a gap.** After a long run the lead suggests a new session; ledger, board and feature list carry over as they are. <!-- f:G3 -->
- **Checked before it reaches you.** A third-party review is done by a session or model that took no part in the work, outgoing material is shown to you first, and the lead checks a package or recording for its promised contents before saying it is ready. <!-- f:G4 G5 -->
- **Reversible steps don't wait.** With a deadline, a step in a direction you already chose that can be undone is done and logged as "done by default". <!-- f:G7 -->
- **Primary outcome first.** A task names the result you will see, and that result is checked on the real product before the details.

### Clean reports <!-- group:I -->
- **Reports without the noise.** No commit hashes or command output in what you read (the evidence stays behind links), and finished threads and empty seats fold away on the board. <!-- f:I2 I3 -->

### Seats and handover <!-- group:J -->
- **Settled things are not asked again.** Before asking you, the lead checks the code, the current docs and the decision record. <!-- f:J1 -->
- **Handover before archive.** Unsent messages, unmerged work and open questions go on the ledger first, and handover notes have a section on what changed in the environment and the method. <!-- f:J2 J6 -->
- **Rules and roster in every session.** Each thread carries the full rules and the current roster, resumed or restarted sessions included; a session not on the roster is not messaged without asking the lead. <!-- f:J3 J8 -->
- **Words said in a thread reach the ledger.** What you tell a thread directly is answered there and passed to the lead to record. <!-- f:J5 -->
- **Swap a seat in one sentence.** "Replace X" for a thread, "hand over to a successor" for the lead. <!-- f:J9 J14 -->
- **Checked before taking over.** A new session takes a health check and compares old and new itself: tasks, branches, commits, your rules and requests. <!-- f:J10 J12 -->
- **A change of lead is confirmed, with a way back.** Threads confirm the new lead and reminders move to it. If a swap fails midway, another candidate is tried or the handover is redone; if that fails the swap is cancelled and the original keeps the seat. <!-- f:J15 J16 -->

### Session health checks <!-- group:L -->
- **Environment check at the start.** Each new session (lead, thread or successor) spends a few seconds checking its environment, such as whether auto mode was accepted. <!-- f:L1 -->
- **Opening health check, run for you.** A short quiz is set and graded outside the session and the score goes on the board. For now a failing session is reported to you, not closed or replaced automatically; afterwards the test content is voided and the session goes on with your real request. <!-- f:L2 L3 L4 -->
- **Handover quiz.** On takeover, change of lead or relay, the session is first tested on its own, then answers questions after reading the handover pack. <!-- f:L5 -->
- **Self-check after compaction.** After its context is compacted a session re-reads its rules and checks itself, and its brief card is put back in front of it.
<!-- features:end -->

## Installer, upgrade and roll back

The installer never removes anything: it merges into existing settings and writes a `.bak` copy before it changes a
settings file. It links the skill into your config dir (with `--link`, `git pull` upgrades it), adds permission rules
so threads can report without stopping at a prompt, and adds a session-start hook so every session in those folders
sees the board. `python3 scripts/install.py --help` lists every option, among them a pre-push guard for private words.

With `--link`, whatever lands on the checked-out branch is live in every new session at once. To decide yourself when
changes reach your sessions, use release snapshots: `python3 scripts/release.py status`, then `publish`, `rollback` or
`edge` (each a dry run until `--apply`). What every rule and command does: [`references/setup.md`](references/setup.md).

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

The board is `.teamwork/BOARD.md`, regenerated on every change. Messages carry one-line headers such as
`[tw:shop] TASK lead -> ui T3`, and every thread ends every turn with a fixed status block.

**The mechanism in full**: [`docs/framework.md`](docs/framework.md) ([中文](docs/framework-zh.md)) specifies the roles,
the ledger, the message protocol, the state machines, routing, consent and recovery, and what another host needs to
run the same framework. Start with its "Overview in 5 minutes".

Which session can message which was tested on a real account: Remote Control sessions message each other in both
directions; the lead can create and message cloud threads, but reads their transcripts to hear back. Details and what
is not proven: [`references/transport.md`](references/transport.md).

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
- Not yet tested by hand: a thread after you type `/clear` in it yourself (the lead cannot reset a thread remotely).

## License

MIT. See [LICENSE](LICENSE).
