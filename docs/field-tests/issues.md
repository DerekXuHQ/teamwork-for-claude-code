# Open issues from field tests

Collected from `2026-11-15.md`. Each item has a repro and the thread that owns the fix.
Status: `open` = not yet handed off, `handed off` = owner ACKed, `fixed` = merged to main.

| # | Severity | Issue | Owner | Status |
|---|---|---|---|---|
| I1 | high | Every tool call in an rc thread needs a manual "allow" | core | open |
| I2 | medium | Interrupt wipes queued messages, and RECALL looks like a user stop | core | handed off (ACK 2026-11-16) |
| I3 | medium | `list_events` with `kinds` and a small `limit` returns nothing | core | open |
| I4 | medium | Worktree for a new rc thread is not created by the lead | core | open |
| I5 | low | Project rename does not cover branches, worktree paths, `.teamwork/` or thread prompts | core | open |
| I6 | low | Header task ids drift to probe ids or ids not on the board | core | open |
| I7 | low | `install.py --help` does not say which config dir it found | core | handed off (ACK 2026-11-16, merged into core T2) |

## I1. Every tool call in an rc thread needs a manual "allow" (L5)

**Repro.** From the lead, `create_session` an rc thread without `permission_mode`. Give it a TASK.
Each Bash / Edit / send_message call stops at "Waiting on permission" until the user clicks allow
in claude.ai. In this run the user clicked about a hundred times across threads.

**Cause.** The thread inherits the Remote Control server's default permission mode.

**Fix (core).** `tw.py` start / the SKILL.md start step passes `permission_mode` (and
`extra_allowed_tools` for `send_message`, `get_session`, `list_events`) to `create_session`.
`install.py` prints, or offers to add, the recommended allow rules and default mode for the
Remote Control config dir. setup.md explains the trade-off.

## I2. Interrupt wipes queued messages; RECALL looks like a user stop (P5, L3)

**Repro.** Lead sends RECALL, then calls `interrupt_session` → the RECALL is dropped
(`cancel_queued: true`). In the other order, the thread sees only `[Request interrupted by user]`
and cannot tell it came from the lead until the RECALL arrives.

**Fix (core).** messages.md and SKILL.md: always interrupt first, then RECALL; the RECALL body
starts with "The lead just interrupted you." Handed off; core ACKed.

## I3. `list_events` with `kinds` and a small `limit` returns nothing (P3, P4)

**Repro.** `list_events(session_id=<busy thread>, kinds=["result"], limit=3)` → no `data`,
`has_more: true`. Same call with `limit: 100` → the `result` event with the `<tw-status>` block.
A thread still in its first turn has no `result` event at all.

**Fix (core).** SKILL.md sweep step and any `tw.py` helper: use `limit: 100`, page back with
`before_id` until a `result` appears; fall back to `get_session.task_summary` when there is none.
transport.md already documents it (field).

## I4. Worktree for a new rc thread is not created by the lead (P1a)

**Repro.** Start an rc thread whose brief says "work in `<worktrees>/<thread>`". The path does
not exist; the thread has to create it itself and could pick a different base or branch name.

**Fix (core).** The start step creates the worktree and branch before `create_session`, or the
brief includes the exact `git worktree add -b <branch> <path> <base>` with "if missing".

## I5. Rename does not cover everything (P8, L4)

**Repro.** Rename the project short name mid-run (title and tag to `TFCC`). Running threads keep
working, but the branch `tw/<old name>/<thread>`, the worktree path, the `.teamwork/` folder and
each thread's system prompt still carry the old name.

**Fix (core).** Decide whether the short name is display-only (then document that branches and
folders keep the long name forever), or make rename a `tw.py` command that updates all of them.

## I6. Header task ids drift (P2, P2a)

**Repro.** Core's ACKs used `P2` and `P2,T7` as the task id; `P2` is a probe id inside field's
task, and neither is on field's board.

**Fix (core).** messages.md: the task-id slot holds exactly one id from the board, or `-`. If the
work was merged into another task, say so in the body.

## I7. `install.py --help` does not say which config dir it found (P2)

**Fix (core).** One line in `--help`: the detected dir and why (CLAUDE_CONFIG_DIR or default).
Handed off as the P2 probe; core is doing it with T2.

## Pending

- L6: cloud thread sweep timing and cloud → lead handoff through the STATUS block. Waiting for the
  lead's FOLLOWUP; new issues from it go here.
