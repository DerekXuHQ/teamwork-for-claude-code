# Open issues from field tests

Collected from `2026-11-15.md`. Each item has a repro and the thread that owns the fix.
Status: `open` = not yet assigned, `assigned: <thread> <task>` = on that thread's board, `fixed` = merged to main.
Assignments per the lead, 2026-11-16.

| # | Severity | Issue | Owner | Status |
|---|---|---|---|---|
| I1 | high | Every tool call in an rc thread needs a manual "allow" | core | assigned: core T9 |
| I2 | medium | Interrupt wipes queued messages, and RECALL looks like a user stop | core | fixed (main c95f9b8 / fa13016) |
| I3 | medium | `list_events` with `kinds` and a small `limit` returns nothing | core | assigned: core T3 |
| I4 | medium | Worktree for a new rc thread is not created by the lead | core | assigned: core T9 |
| I5 | low | Project rename does not cover branches, worktree paths, `.teamwork/` or thread prompts | core | assigned: core T8 (decision: short name is display-only) |
| I6 | low | Header task ids drift to probe ids or ids not on the board | core | assigned: core T9 |
| I8 | high | User requests have no ledger; nobody reports back after delivery | core | assigned: core T14 |
| I7 | low | `install.py --help` does not say which config dir it found | core | fixed (main c95f9b8 / fa13016) |

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

**Decision (lead, 2026-11-16).** The short name is display-only: titles, tags and message headers.
Branches, worktree paths and `.teamwork/` keep the long name. Core documents this in T8.

## I6. Header task ids drift (P2, P2a)

**Repro.** Core's ACKs used `P2` and `P2,T7` as the task id; `P2` is a probe id inside field's
task, and neither is on field's board.

**Fix (core).** messages.md: the task-id slot holds exactly one id from the board, or `-`. If the
work was merged into another task, say so in the body.

## I7. `install.py --help` does not say which config dir it found (P2)

**Fix (core).** One line in `--help`: the detected dir and why (CLAUDE_CONFIG_DIR or default).
Handed off as the P2 probe; core is doing it with T2.

## I8. User requests have no ledger; nobody reports back after delivery

**Repro.** The user asks the lead for something; the lead routes it to a thread; the thread
finishes and reports to the lead. Nothing records the original request, so nothing makes sure
the result gets back to the user, and nobody can list which requests are still open.

**Fix (core, T14, user's top priority).** Every user request gets a ledger entry when it arrives
(who asked, what, which task it became). It is closed only when the result has been reported back to
the user. The lead's sweep lists entries that were delivered but never reported back.

## Not observed in this run

- Child-session failure events (L6c): no thread failed. Re-test by forcing a turn error in a
  throwaway thread.
- Cloud → cloud messaging: not exercised.

## Minor issues from code review (non-blocking)

Handed off by core on 2026-11-16, from the independent reviews of core's last rounds on its
branch. These were found by review, not seen in a field run. They do not block merging (lead's
rule). Owner for all: **core**. Status: `open`. Each line names the case that goes wrong; the
review notes on core's branch have the details.

### T20: status checks

- R20-1 Owner names are not normalised (e.g. `ui/..`, `ui/self`). Independence of a review is judged by name only, not by session.
- R20-2 Zero-width spaces are accepted in the user's verbatim request.
- R20-3 With skip-review, the done criterion can be empty.
- R20-4 When a task goes to done, the user's verbatim request is not recorded with it.
- R20-5 `req import` imports rows that are already closed.
- R20-6 After the user moves a request from `reported` back to `dispatched`, the old result is delivered again.
- R20-7 `req set` does not call `deliver_ready` afterwards.
- R20-8 `req set --text` can rewrite a request that is in progress.
- R20-9 `req drop` does not remind the lead to recall the work.
- R20-10 There is no `unlink` command.
- R20-11 `--review fail --status review` is accepted as a combination.
- R20-12 With `require_review` off, a review `fail` is ignored.
- R20-13 A task that is already done can still be set to `--checks fail`.
- R20-14 `task set dispatched` does not check file leases.
- R20-15 `init --force` does not require the user's verbatim request.
- R20-16 Fields that only appear in the UI have no parsing tests.
- R20-17 Thread status transitions are not validated.
- R20-18 The `close` prompt text needs work.

### T11: file leases

- R11-1 A squash commit message without a task id.
- R11-2 Task ids written as `[T1, T2]`, or in lower case.
- R11-3 A revert after a squash.
- R11-4 A rebase merge whose last commit has no task id.
- R11-5 `/` inside a glob character class.
- R11-6 Unicode case folding.
- R11-7 Nested braces such as `{a,{b}}`.
- R11-8 A real directory whose name contains braces, sitting between two declared leases.
- R11-9 Lease-vs-lease overlap checks are too conservative.
- R11-10 `--touches` given an absolute path.
- R11-11 Git submodules.
- R11-12 No warning when git is not available.
- R11-13 A cherry-picked file releases its lease too early.
- R11-14 A shared branch lists every completed task.
- R11-15 A squash merge where conflicts were resolved by hand only releases once the branch is deleted.

### T12: seats and session history

- R12-1 Session ordering.
- R12-2 Leftover tagged sessions from before.
- R12-3 End times are only approximate.
- R12-4 `rebuild --apply` does not take a snapshot.
- R12-5 SKILL.md 6.4 and 6.5 both describe the ROSTER broadcast (duplicate).
- R12-6 Until reseat, worker-prompt still carries the old session id.
- R12-7 An archived seat cannot get its own session back.
- R12-8 An archived seat still accepts `set --session`.

### T13: rebuild

- R13-1 A snapshot with wrong value types crashes the program.
- R13-2 Thread names in the log are not checked against `NAME_RE`.
- R13-3 Maliciously crafted session files.
- R13-4 The user's verbatim requests in plan and prefs are not replayed.
- R13-5 The lead is not recovered from `parent_session_id`.
- R13-6 `--apply` can overwrite newer state with older state.
- R13-7 `--short` accepts only one value.
- R13-8 With no log, an old short name can re-seat a retired session.
- R13-9 The MCP outer wrapper is not unwrapped.
- R13-10 A missing page in the middle is not detected.
- R13-11 Archived seats get sessions assigned.

### T16: privacy guard

- R16-1 Brackets in path rules can reveal one letter.
- R16-2 Grafts and shallow clones.
- R16-3 Does not recurse into submodules.
- R16-4 The git settings `log.showRoot` and `diffMerges` are not pinned.
- R16-5 Phrases only match exactly (space, hyphen and underscore are not treated as equivalent).
- R16-6 Cloud threads push without going through the guard.
- R16-7 `--private` files other than PRIVATE.md fail silently when missing.
- R16-8 Archives nested deeper than 2 levels, with more than 2000 entries, or with a single entry over 64 MB are skipped.
- R16-9 A corrupt zip is treated as clean.
- R16-10 In docx, XML-escaped words and words split across runs are missed.

### T24: thread kinds and asks

- R24-1 `ask --need-thread` dedup does not normalise `--from` (case, surrounding whitespace) and does not check it is a known thread, so `ghost` or `lead` can be recorded; the name is not checked against `NAME_RE` either.
- R24-2 Once a thread's request for a given name has been answered, the same thread can never record it again. Needs an explicit `--again`.
- R24-3 An rc thread changed to cloud after `worktree` leaves its worktree and branch on disk with no owner; changing cloud to rc does not prompt to run `tw.py worktree`.
- R24-4 One `thread set` call accepts both `--session` and `--kind`.
- R24-5 `lead --session` checks "a lead already exists" before checking for a subagent id; `strip` is applied twice.
- R24-6 The subagent-id check misses forms such as `subagent.1`, `agent 7`, `general_purpose`, `toolu01ABC`.
- R24-7 `thread set ghost --kind cloud` on a thread that does not exist reports "kind is fixed" instead of unknown thread.
- R24-8 For a cloud thread that already has a reason, `--kind cloud` without a reason is refused, but `--cloud-reason ""` keeps the old reason: the two are handled inconsistently.
- R24-9 The rc capacity limit is only in the docs; nothing checks it (e.g. warn when `ready --sessions` exceeds 30).
- R24-10 Nothing in code stops a thread from calling `create_session` (it works even in bypassPermissions mode); only the protocol forbids it. SKILL.md already says so.

### T23: review rounds and caps

- R23-1 When a task goes to done as `capped` or `pass` with remaining findings, nothing warns if some of them were never opened as follow-up tasks.
- R23-2 Old records (a `pass` with no stored cap, `skipped`, rows with only a round count) show the current cap in `task reviews`; with no stored cap they should show `?`.
- R23-3 The help text for `--remaining` and `followups` says "capped" and does not mention that `pass` can use them too.
- R23-4 SKILL.md: the user-decision row of the Rules table does not mention lowering `review_max_rounds`; §8 Close does not require listing unfinished follow-up tasks; "step 7" in the rules table is ambiguous and should read "§7 Integrate results".
- R23-5 After `rebuild`, `prefs_user_said` and `plan_user_said` are lost (older issue; the verbatim text is still in the log). See also R13-4.
- R23-6 When `--rounds` jumps by more than one from the last record, there is no warning and no note is required.
- R23-7 The cap in a worker prompt is fixed at dispatch; changing `review_max_rounds` mid-run does not remind the lead to send a FOLLOWUP to working threads.

### T27: installer permission rules

Reviewed at core branch commit 4aabb32, `scripts/install.py` (rules in `ASK` and `_push_rules`).

**Standing decision (lead, 2026-11-17).** The local `git push` ask rules were removed: the user does
not want to confirm routine force pushes to their own branch. `main` is protected by a GitHub
ruleset on `~DEFAULT_BRANCH` instead, which covers any base branch name. Every item about the local
`git push` ask rules is marked **No longer applies** for this reason, now and in future reviews.
Scope: this covers only the `git push` rules; other ask rules (e.g. `gh api`, R27-4) still apply.

- R27-1 `git *push*` matches the whole command text, so a commit message containing `push`, `-f`, `:main` or `+main` is caught and prompts once more. Suggestion: narrow the rule to commands starting with `git push` or `git -C * push`.
  **No longer applies (2026-11-17):** the `git push` ask rules were removed; see the standing decision above.
- R27-2 Combined short options are not caught: `git push -fu origin main`, `git push -uf origin main`.
  **No longer applies (2026-11-17):** the `git push` ask rules were removed; `main` is protected by the GitHub ruleset.
- R27-3 The ask rules only know `main` and `master`; a project whose base branch has another name is not protected. Suggestion: generate the rules from the project's base branch.
  **No longer applies (2026-11-17):** the `git push` ask rules were removed; the ruleset targets `~DEFAULT_BRANCH`, so any base branch name is covered.
- R27-4 `gh api -X delete` (lower case), and changing visibility through `--input body.json`, are not caught. This is a limit of text matching; setup.md could say so.
- R27-5 A `TMPDIR` that points into a subfolder of the home directory (e.g. `~/Documents/x`) is added to `additionalDirectories` as is.
- R27-6 A malformed settings.json is only detected after step 1 has copied the skill, leaving a half-installed state. Re-running works, but the check could move first.
- R27-7 `install.py` and `tw.py` compute the worktree folder differently (`--project` vs `repo.path`). When the project folder is not the repo root, the two disagree.

### T25: review gate and privacy guard

Reviewed at core branch commit 6692599.

- R25-1 `task set --status done --skip-review` overwrites an existing `pass` + remaining verdict without going through void, so follow-up tasks already opened are not flagged (`tw.py`, around line 1052).
- R25-2 `rebuild`: in a project with a short name and no lead events in the log, a session from another project that matches only on the initials tag is still accepted (with a warning).
- R25-3 A PRIVATE.md line written as `"X Y" - note` also yields the extra word `X Y" - note`. No false positives, just untidy.
- R25-4 The cloud kit does not check ref names other than tags and the current branch; in XML with line breaks, words split by tags are not joined back.
- R25-5 The number of hashes roughly reveals the number of words; with a large word list (1000 words) scanning costs about 1.85 s per MB.
- R25-6 `--rounds` can be reported as 2 on the first round; "at least 2 rounds" relies on the thread reporting honestly.
- R25-7 PDF decompression has no total size cap; `~/x` is not expanded to the absolute home path; short paths such as `/a` cause false positives.
- R25-8 Encodings suggested in review round 1 and not handled: Python `\x` escapes, base64, quoted-printable (.eml), double percent-encoding. Note: the local `tw.py guard` already decodes HTML entities, `\u` Unicode escapes and UTF-16 text (T25 batch 1, with tests). Only the cloud kit (`tw-guard.py`) does not decode them, because it reads raw text only (see R25-4).

### T26: framework audit and follow-ups

Source: T26 review rounds 2 and 3, at core branch commit ce3599a. Owner: core for all.
R26-1 and R26-5 are in `docs/framework.md`, which moved from the framework thread (finished) to
core.

- R26-1 `framework.md` B10 lists I31 and I46 as deviations, but A17 allows I31 to live in the notes only, and I46 is not in the "must be code" set. I46's "maintained by" column says "prompt (partly)" while B10 says "prompt only": the two disagree.
- R26-2 I25: the code only checks the `--user-said` verbatim text the lead enters; the entry should say "partly; see I11/I27".
- R26-3 `test_tw.py` AuditFollowups only checks text fragments; the Y-12 test only asserts the old list appears 0 times.
- R26-4 I31's decision (do / propose / drop) has no place in the ledger.
- R26-5 `framework.md:299` names the data-model field `kind_reason`; the code calls it `cloud_reason`.
- R26-6 SKILL.md step 3.1 sets a new thread straight to `working`, which contradicts the definition of `starting` ("created, first turn not finished").
