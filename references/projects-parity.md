# Projects beta feature → teamwork

Projects facts come from https://code.claude.com/docs/en/claude-projects.md and the 17 Sep 2026 launch post. Where teamwork does something different, the last column says so.

| Projects beta | teamwork | Notes |
|---|---|---|
| One ongoing project conversation; Claude is the coordinator | The lead: an rc session running `/teamwork` | The lead works in the project folder through Remote Control; the Projects coordinator "has no connectors". |
| A thread per task, each with its own context, reports back when done | A thread per **ownership area**, created with `create_session`, protocol in `append_system_prompt` | Area threads keep history across follow-ups. |
| Cloud threads by default; local threads via Remote Control, optional worktree | `rc` (default for anything touching files) or `cloud` per thread; each rc thread makes its own git worktree | Same split, chosen per thread by need, all driven from claude.ai. |
| Routing: answer in place / new thread / thread already working in that area, and say which | SKILL.md §4 plus `TW route` | Same rule, explicitly stated in every reply. |
| Coordinator sees what threads report, not every step | `<tw-status>` block each turn plus DONE/BLOCKED messages | Lead can still read any transcript when needed. |
| Thread ↔ thread: not documented | Roster in every thread, `HANDOFF` protocol, lead copied | Better than Projects here. Cloud→rc goes through the lead (transport.md). |
| User talks to any thread directly | Same: every thread is a normal session in the sidebar | |
| Permission prompts live in the thread | Same; the lead tells the user which thread to open | Allow rules (setup.md §2) remove most prompts. |
| Overview groups: Ready for review, Waiting on you, Working, Landing, Idle, Resolved | Thread statuses `review`, `waiting`, `working`, `landing`, `idle`, `resolved` on BOARD.md | |
| Suggested threads + "propose threads and wait for my go-ahead" | Plan step: Suggested threads list, `propose_before_start`, `TW plan approve` | Real task list with owners, dependencies and "done when". Projects has no formal plan. |
| Style settings: check-in frequency, how often to start threads, update detail; "at most two threads at a time" | `TW prefs`: `checkin_minutes`, `propose_before_start`, `update_detail`, `max_parallel` | Projects keeps them as instructions; teamwork checks `max_parallel` in `TW ready` (default 0 = no limit). |
| Project instructions sent to every new thread and the coordinator (16k chars) | `.teamwork/INSTRUCTIONS.md` in every worker prompt and in the SessionStart hook | Same 16k cap. |
| Project memory `MEMORY.md`, read by every cloud thread at start | `.teamwork/MEMORY.md`, pasted into every new thread, lead-curated | Also reaches rc threads (Projects: Remote Control threads don't get memory). |
| Coordinator works from recent messages, threads and memory | Lead recovers from `.teamwork/state.json` via the hook after compaction | Durable on disk. |
| "Status update on every thread" / list what each was asked, assumed, couldn't reach, waits on | `/teamwork status`; STATUS fields `assumed`, `couldnt_reach`, `waiting_on` | Same questions, asked every turn. |
| Branch per thread, PRs, auto-fix on PR | Branch `tw/<project>/<thread>`; the lead merges after user review | No PR auto-fix by default; a cloud thread can be asked to open and watch a PR. |
| Threads resolved by the user, after merge, or after a week idle | `resolved` set by the lead on merge or user request | No automatic one-week resolve. |
| Continue in project: pull an existing cloud session in | `TW thread add <name> --session <existing id>` then send it a ROSTER and a FOLLOWUP | Works for rc and cloud sessions. |
| Isolation: project-owned threads, hidden from the normal session list, own memory | Own folder, own Remote Control server/environment, own ledger, titles `[<project>] ...`, tags | Threads still show in the normal session list; `~/.claude/CLAUDE.md` still loads in rc threads. |
| Notifications when Claude posts, a thread errors or needs input | App notifications per session; child-session failure events are documented but not seen in field tests (I9), so the lead's sweep is the backstop | No single project-level notification switch. |
| 500 new threads/day; uses limits faster | No thread limit by default. Hard limit: a Remote Control server holds 32 sessions by default, across all projects on it; cloud threads don't count | Every thread is a full session: cost scales with thread count. |

## Known gaps

- No single UI: the board is a file (`.teamwork/BOARD.md`) and a status reply, not a sidebar pane.
- Cloud threads cannot push to the lead; the lead sweeps them on a timer.
- The ledger is only as fresh as the last sweep.
- Isolation is by convention (folder, titles, tags), not enforced by the platform.
