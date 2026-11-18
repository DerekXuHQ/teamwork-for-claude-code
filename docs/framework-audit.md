# Framework audit: implementation vs specification

Companion to [`framework.md`](framework.md). Two parts:

- **Mapping**: every item in `SKILL.md`, `templates/worker-protocol.md` and `scripts/tw.py` → the section of
  `framework.md` that specifies it.
- **Findings**: places where the implementation and the specification disagree, or the implementation is silent.

Checked against `main` at `3640ebe`; the status of each finding was updated in T26 (end of Findings).

---

## Mapping

### TW commands

| Item | Source | Section |
|---|---|---|
| `TW init` (incl. `--short`) | SKILL §1.2 | A3, A5, B3 |
| `TW lead --session / --rc-env / --cloud-env / --force` | SKILL §0.2–0.3, Attach | A3, A13, B2, B3 |
| `TW thread add` | SKILL §2.4, §3.1 | A2, A4, B2, B3 |
| `TW thread add --session` (adopt) | projects-parity | A2, B2 |
| `TW thread set` | SKILL §3.1–3.2, §5.3, §8 | A9, B3 |
| `TW thread set --from-status` | SKILL §5.3, §5.7 | A5, A9, A10, B3 |
| `TW task add` / `task set` | SKILL §2.4, §3, §5.3, §6 | A9, A13, B3 |
| `TW plan approve` | SKILL §2.5 | A9, A10 |
| `TW plan show` / `reopen` | tw.py only | A9, B3 (finding Y-4) |
| `TW ready` (incl. `stuck`) | SKILL §3, §5.7 | A9, A10, A14 |
| `TW route` | SKILL §4 | A11 |
| `TW ask` / `--answer` | SKILL §5.6 | A10, A12 |
| `TW memory` | SKILL §5.5 | A5 |
| `TW log` | tw.py only | A5 (finding Y-4) |
| `TW board` | SKILL Attach, Rules | A5, A13 |
| `TW roster` (`--thread`) | SKILL §3.3 | A4, A6 |
| `TW open` | SKILL §5.1 | A10 |
| `TW prefs` (`short_name`, `language`, `stuck_minutes`, `thread_permission_mode`, …) | SKILL §1.2, §1.4, §3.1, §4, §5.7 | A3, A5, A6, A9, A12, A14 |
| `TW worktree [--dry-run]` | SKILL §3.1 | A15, B2, B6 |
| `TW worker-prompt` | SKILL §3.1 | B5 |
| `tw.py projects` | SKILL Portfolio, Attach | A3 |
| `tw.py context` | setup.md §3 | B7 |
| `install.py` | SKILL §1.5, setup.md | B8 |
| Request ledger commands | core T14 (not merged) | A5, A9, A10, A11 (re-check when T14 lands) |

### Playbook steps and arguments

| Item | Section |
|---|---|
| Operating model, one lead per project | Overview, A1–A3 |
| §0 Preconditions | B2, B4 |
| §1 New project (`new`), `use`, `projects` | A3, A10, B9 |
| §2 Plan (`plan`) | A4, A10, A14 |
| §3 Dispatch (`go`), capacity, worktree, permission mode | A10, A14, A15, B2 |
| §4 Route (`route`), language first; intake (not yet in SKILL) | A6, A11 |
| §5 Sweep and status (`status`, `sweep`), possibly stuck | A9, A10 |
| §6 Recall (interrupt first), reassign, stop (`recall`) | A6, A13 |
| §7 Integrate | A15, B6 |
| §8 Close (`close`) | A10 |
| Keep the user in one chat | A1, A12 |
| Rules | Part C |

### Message types

| Type | Section |
|---|---|
| `TASK`, `FOLLOWUP`, `RECALL`, `ROSTER`, `ANSWER` | A6 (examples in references/messages.md) |
| `ACK`, `UPDATE`, `DONE`, `BLOCKED`, `QUESTION`, `HANDOFF`, `MEMORY` | A6 |
| Header format, one task id per header | A6 |

### Statuses

| Item | Section |
|---|---|
| Request: `open`, `proposed`, `dispatched`, `delivered`, `reported`, `closed`, `dropped` | A9 |
| Delivery: `prepared`, `signed_off`, `sent`, `accepted`, `done`, `rejected`, `failed` (not used in this binding) | A9, A16 |
| Intake tiers `light`, `heavy`; decisions `do`, `propose`, `drop` | A11 |
| Thread: `planned`, `starting`, `working`, `waiting`, `review`, `landing`, `idle`, `resolved`, `archived` | A9 |
| Board group "Possibly stuck" (derived, not a status) | A9, A10 |
| Task: `planned`, `approved`, `dispatched`, `working`, `blocked`, `review`, `done`, `recalled`, `dropped` | A9 |
| Worker `state`: `working`, `waiting`, `review`, `idle` | A7, A9 |

### Worker protocol sections

| Item | Section |
|---|---|
| Identity block (thread, short name, kind, focus, owns, branch, folder, worktree, lead) | A2, A3, B5 |
| Language | A6 |
| Team roster | A4, A6 |
| Your tasks | A9 |
| Project instructions / memory | A5 |
| How you report to the lead | A8, B4 |
| Messages, header task-id rule | A6 |
| Peer messages are not approval | A12 |
| Ownership and handoffs | A4, A11 |
| RECALL (interrupted by the lead) | A6, A13 |
| Working rules | A12, A14, A15, B6, Part C |
| STATUS block | A7 |

---

## Findings

Deviations the framework declares for this binding are listed in `framework.md` B10; the rows below are the
implementation-side findings behind them.

Severity: **H** can break a run, **M** wrong or misleading behaviour, **L** cosmetic or missing docs.

| # | Sev | Finding | Where | Suggested fix |
|---|---|---|---|---|
| Y-1 | H | The handoff section tells every thread to send a HANDOFF to the owner and to "reply ACK to the sender". For a cloud thread whose peer is an rc thread there is no channel (cloud → rc is refused); the transport rule is in the report-channel paragraph but not in the handoff steps. | worker-protocol "Ownership and handoffs" 2, 4 | Add: "cloud threads message only cloud peers; for an rc peer, record it in `handed_off` and the lead forwards it." Could be rendered only for cloud threads by `worker-prompt`. |
| Y-2 | M | The lead's timer uses background `sleep`, checks beyond an hour use `send_later`, but neither `Bash(sleep *)` nor `mcp__claude-code-remote__send_later` is in the skill's `allowed-tools`, so each prompts. | SKILL.md front matter vs §3.5 | Add both; keep `archive_session` out (it should keep asking) and say so. |
| Y-3 | M | The protocol defines `state: waiting` as "you need the user or someone else", and SKILL §5.3 maps only `waiting_on` user/permission to `waiting`. What a seat shows when `state: waiting` and `waiting_on: <thread>` is undefined. | worker-protocol STATUS; SKILL §5.3 | Define it: keep the seat `working` with the peer named in the summary (framework A9), or add a "waiting on a peer" board group. Never "Waiting on you". |
| Y-4 | L | `plan show`, `plan reopen`, `log`, `roster --thread`, `worktree --dry-run` exist in tw.py but are not mentioned in SKILL.md. | tw.py vs SKILL.md | Mention them, or drop the unused ones. |
| Y-5 | L | Statuses the playbook never sets: thread `starting`; task `working`, `review`, `dropped`. SKILL §7 says "when a task reaches `review`" without saying who sets it. | SKILL §3, §5.3, §7 | State the transitions (framework A9) or prune. |
| Y-6 | L | Instructions may be 16 000 chars but `context` cuts at 9 000 with the language rule and board first, so after compaction the lead may see only part of the Instructions. | tw.py `cmd_context` | Put Instructions earlier, or tell the lead to read INSTRUCTIONS.md when the truncation marker appears. |
| Y-7 | L | (Specified in framework A6, "Forwarding a HANDOFF"; SKILL still silent.) SKILL §5.4 has the lead "forward" an unacknowledged HANDOFF, but the lead's message types do not include HANDOFF; which type the forward uses is unspecified. | SKILL §5.4 vs messages.md | Forward as `TASK` (new to the receiver) or `FOLLOWUP`, quoting the original. |
| Y-8 | L | `tw.py ask` accepts an empty question; `--answer N` does not check the index. | tw.py `cmd_ask` | Validate. |
| Y-9 | L | Tasks added after `plan approve` are `approved` at once, so a new task for an existing thread skips the go-ahead even with `propose_before_start=true`. | tw.py `cmd_task` vs SKILL §2.5, §4 | Decide and document. With the request ledger, a routed user request can count as the user's go-ahead for its tasks. |
| Y-10 | L | The status block has one `task:` field; a thread with several open tasks cannot report each. | worker-protocol STATUS | Allow one line per task, or a list. |
| Y-11 | L | RECALL sets the task `recalled`, but the thread status after a RECALL is not specified. | SKILL §6 | Sweep right after the thread's RECALL reply; set `idle` when it confirms it stopped. |
| Y-12 | L | `extra_allowed_tools` is listed twice in SKILL §3.1 (from `thread add` output, and again as "rc only"). | SKILL §3.1 | Keep one; note that `thread add` prints it for every kind. |
| Y-13 | L | In `cmd_init`, the comment for `language` sits on the `thread_permission_mode` line. | tw.py `cmd_init` prefs | Move the comment. |
| Y-14 | M | The request ledger (framework A5, A9, A11, I25) is not implemented yet; until it is, nothing records what the user asked or whether it was reported back. | core T14 | Implement per the framework spec; then re-check this audit. |
| Y-15 | M | SKILL §4 routes every user message directly; there is no intake step (intent, success criterion, tiered do / propose / drop) and no `proposed` request state. | SKILL §4; core T14 | Add intake before routing per framework A11, tiered so simple asks stay light; add `proposed` to the request states in T14. |
| Y-16 | M | No independent check: a thread's DONE goes to the user's review with only the author's own `verified`. | SKILL §5, §7 | Before reporting a heavy-tier result, the lead has it checked by a fresh session or a context-free subagent against the task's "done when" and records the checker (framework I34). |
| Y-17 | L | New thread sessions get work immediately; there is no health check (protocol read back, tools and worktree confirmed) before the first TASK. | SKILL §3.1 | Make the first prompt a short health check; send the TASK only after it passes (framework I35). |
| Y-18 | M | SKILL §5.3 says to update task status from the STATUS block including `done`; `done` belongs only after acceptance and merge (framework A9, I28), and the status block has no `done` state. | SKILL §5.3 | Map `state: review` to task `review`; set `done` only after the user accepts and the merge lands. |
| Y-19 | M | SKILL §5.3 and §5.7 map any `failed` or `disconnected` to `waiting`; the framework keeps a transient disconnect the host is retrying as `working` (A9, A10 step 7). | SKILL §5.3, §5.7 | Distinguish a retried disconnect from a host that gave up. |
| Y-20 | L | The worker protocol asks for ACK on TASK and HANDOFF only; the framework requires it for FOLLOWUP too, and the sweep chases unacknowledged tasks (I46). | worker-protocol Messages; SKILL §5 | Add FOLLOWUP to the ACK row; add the chase to the sweep. |
| Y-21 | M | SKILL §2.3 recommends `cloud` for self-contained repo work and long runs, and tw.py has no `kind_reason`; the framework defaults to `rc` and allows `cloud` only for work that never touches project files nor messages peers, with the reason recorded (I43). | SKILL §2.3; tw.py `thread add` | Align the rule; add `--kind-reason`. |
| Y-22 | L | `tw.py prefs --set thread_permission_mode=` accepts any known mode name, including `bypassPermissions`, without comparing it to the lead's mode; the host refuses a looser mode at `create_session`, but the ledger can record one. | tw.py `cmd_prefs` | Warn when the chosen mode is looser than the lead's, or drop the loosest modes from the allowed list. |
| Y-23 | M | `tw.py task set --status done` works from any status; the framework allows `done` only from `review` after acceptance (I28). | tw.py `cmd_task` | Check the transition in `tw.py` (core T14). |

### Resolved since `160cfec`

- Header task ids drifting to lists or probe ids: one id per header, now in messages.md and the protocol.
- Language rule: `prefs.language`, `language_rule()`, protocol section, FOLLOWUP to each thread on a switch.
- Project short name: `--short`, `prefs.short_name`, `tag()`, title-length warning; branches keep the full name.
- RECALL order: interrupt first, then RECALL, which says the lead interrupted.
- `list_events` reading: `limit: 100`, page back, `task_summary` in the first turn.
- Worktree created by the lead (`tw.py worktree`) before `create_session`.
- Thread permission mode passed at creation (`thread_permission_mode` or the lead's).
- Stuck detection: `last_status_at`, `--from-status`, `stuck_minutes`, board group.

### Status of the findings (T26)

Every finding above has been handled. "Fixed" points to where it now lives; the ones changed in T26 have a
test in `scripts/test_tw.py` (`AuditFollowups`) that checks the wording or the behaviour; "earlier" or a task id means it was done before T26.

| # | Status | Where it is handled |
|---|---|---|
| Y-1 | fixed | worker-protocol "Ownership and handoffs" 2 and 4: a cloud thread messages only cloud peers; for an rc peer the whole HANDOFF goes in `handed_off` and the lead forwards it (SKILL §5.4). |
| Y-2 | fixed (T25) | `Bash(sleep *)` and `send_later` in the skill's `allowed-tools`; archiving still asks. |
| Y-3 | fixed | SKILL §5.3: `state: waiting` with a peer in `waiting_on` keeps the seat `working`, peer named in the summary; `waiting` means the user has to act. |
| Y-4 | fixed | SKILL "Ledger": `plan show`, `plan reopen`, `log`, `roster --thread`, `worktree --dry-run` described. |
| Y-5 | fixed | SKILL "Ledger" (statuses that follow from events) and §5.3: `starting` after a `reseat` without `--status`; task `working` on the ACK of a TASK or HANDOFF, `review` on DONE, a still-dispatched task through `working` first; `dropped` is the user's. |
| Y-6 | fixed (earlier) | `tw.py context` ends a cut text with a marker that names INSTRUCTIONS.md, BOARD.md and MEMORY.md to read in full. |
| Y-7 | fixed | SKILL §5.4: forward as `TASK` (new to the receiver) or `FOLLOWUP` (it had the task), quoting the original. |
| Y-8 | fixed | `tw.py ask` refuses a blank question (it was still accepted; fixed now). The `--answer` index check was already there. |
| Y-9 | decided | SKILL "Ledger": a task added after approval for a recorded user request counts as approved (the request is the go-ahead); one the lead thought of, with `propose_before_start=true`, is proposed first. This rests on the prompt only: `tw.py` still records such a task as `approved` and has no state for "proposed, waiting". |
| Y-10 | fixed | worker-protocol STATUS: `task:` takes one entry per open task with its state word (`T3 working; T5 waiting`). |
| Y-11 | fixed | SKILL §6 Recall: the seat goes `idle` once the thread confirms it stopped, or gets the next TASK. |
| Y-12 | fixed | SKILL §3.1: one `extra_allowed_tools` entry, noting `thread add` prints it for every kind. |
| Y-13 | fixed (earlier) | `cmd_init` prefs: each comment on its own key. |
| Y-14 | fixed (T14) | Request ledger: `req add/link/set/close/drop`, `trace`, unreported requests on the board and in the context. |
| Y-15 | fixed | SKILL §4 "Intake, sized to the ask": intent and success criterion, do / propose / drop, before routing; `proposed` request state (T14). |
| Y-16 | fixed (T23) | SKILL §7 and the protocol: a fresh-subagent review before DONE, rounds, `pass` / `capped`, recorded reviewer. |
| Y-17 | partial (decided) | worker-protocol ACK row: the first ACK doubles as a health check (task id, worktree or branch, anything unreachable), and SKILL §5.3 has the lead fix a reported gap soon while the thread works on what it can, sending BLOCKED if the gap stops it. This is lighter than I35: the TASK is already sent and the thread can see its own "done when", so it is a self-report, not a check the session cannot see the answer to. Chosen to save a round trip per thread; a separate check before the first TASK stays open. |
| Y-18 | fixed | SKILL §5.3: never `done` from a report; done only from `review` after a pass or capped verdict (also enforced in `tw.py`, T20/T23). |
| Y-19 | fixed | SKILL §5.3 and §5.7: one disconnected sweep keeps the seat `working`; a second one, or `failed`, makes it `waiting`. |
| Y-20 | fixed | worker-protocol: ACK covers FOLLOWUP too; SKILL §5.3: a TASK or FOLLOWUP with no ACK by the next sweep is sent once more, and the user is told when the next sweep still has none. |
| Y-21 | fixed (T24) | rc by default; `--kind cloud` needs `--cloud-reason`; SKILL §2.3. |
| Y-22 | partial | `prefs --set thread_permission_mode=auto` or `bypassPermissions` prints a note that the host refuses a thread looser than the lead. Gap: the ledger still records the mode, because tw.py cannot see the lead's mode; `create_session` is what refuses it. |
| Y-23 | fixed (T20) | `task set --status done` only from `review`, with a passing or capped review (TASK_MOVES, accept gate). |
