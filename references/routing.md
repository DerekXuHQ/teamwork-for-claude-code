# Routing work by model and quota

Task routing uses explicit `--route` first, then `--no-scripts` (Claude by hand), then the current quota tier, then work kind: `docs-zh`, `review2`, and `research` suggest Fable; `plan` and `debug-hard` suggest Opus; `review1`, `code`, `test`, `read`, `docs-en`, and `merge` suggest GPT. Unknown kinds default to GPT. At `burn-fable`, Chinese docs, second reviews, and research continue to Fable; code-like work stays GPT-first. At `save` and `lead-only`, execution stays GPT.

Who does what when a project has Claude seats (the lead and its threads) and a GPT executor (Codex, through the
codex plugin), and how the split changes as the Claude weekly quota fills. The lead decides; threads follow the
lead's latest `INFO` tier. Only the lead reads the quota (`TW quota`).

## 1. Who does what

GPT first for anything it can do; Claude for what needs judgment, the user, or a second model family.

| Work | Goes to | Fallback / note |
|---|---|---|
| Talking with the user, plans, splitting and dispatching, merge decisions, final review verdicts | Opus (the lead) | kept in every tier |
| Hard debugging: GPT tried once and failed, root cause across modules | Opus | GPT tries first; then straight to Opus |
| Writing and changing code, refactors, bulk mechanical edits | GPT | Fable when GPT is tight |
| Writing and running tests, regression rounds | GPT | |
| Reading many files, mapping the current state, search | GPT (read-only) | Fable |
| First-round and adversarial code review | GPT | |
| The fresh-eyes review before DONE | Fable | another model family sees other blind spots; releases, permissions and privacy changes: Opus |
| English repo docs, README drafts | GPT | Fable; outward-facing final copy: Opus looks over it |
| User-facing plans and reports in the user's language when that is not English | Fable drafts | important decision papers: Opus finalises |
| Web research, collecting sources | Fable | GPT can too |
| Translation, tables, formatting | GPT | Fable |
| The threads themselves (coordination, talking with the lead) | existing seats stay as they are | new execution-only threads may get a Fable seat (section 5) |

A TASK may name the route (`Route: gpt | fable | opus`, `TW task set T<n> --route ...`); without one, the table
applies, and the current tier can override it.

## 2. Tiers

`TW quota` reports the total weekly window, the model-scoped weekly window (shown under its display name, e.g.
Fable), the 5-hour window and the GPT weekly window, and maps them to one tier plus flags. Thresholds are prefs
(`quota_burn_at`, `quota_lead_only_at`, `quota_pre_reset_hours`, `quota_pre_reset_fable_max`, `quota_fable_done_at`,
`quota_5h_tight`, `quota_gpt_tight`).

| Tier | When (defaults) | What changes |
|---|---|---|
| `normal` | total < 70% | the table above; if the scoped model is used up, its rows go to GPT or Opus |
| `pre-reset-burn` | total < 70%, reset within 36 h and the scoped model under 80% (unread counts as 0%: nobody used it this week) | pull queued work the scoped model can do forward: extra reviews, docs, audits, tidying; optional Opus work moves to it |
| `burn-fable` | total ≥ 70% | everything on the Claude side except Opus-only work goes to the scoped model; GPT stays first |
| `save` | total ≥ 70% and the scoped model ≥ 95% | execution all to GPT; Claude does Opus-only work; the rest waits for the reset |
| `lead-only` | total ≥ 90% | only the lead uses Claude (talking with the user, merging, verdicts); threads open no Claude subagents at all, hand execution to GPT and keep their own turns minimal; no new seats |
| `unknown` | the total cannot be read (the newest session reading is over a day old, or only a usage cache older than 6 h; a newer, higher cache reading of the same window wins) | keep the last tier; nothing is broadcast |
| flag `5h-tight` | 5-hour window ≥ 80% | no tier change and not broadcast (it passes within hours): the lead queues new work or gives it to GPT until the window drops |
| flag `gpt-tight` | GPT weekly ≥ 90% | GPT rows take their fallback; if that is tight too, queue |
| flag `fable-done` | the scoped model ≥ 95% | no more work routed to it this window |

At 100% total every Claude session stops, the lead included: `lead-only` is the floor kept for the lead.

**Cadence.** The lead runs `TW quota` when it wakes and the last run is an hour old or more, and whenever the user
asks. When nothing is running (no task dispatched, working or in review, no thread starting, working or landing) there is no check: `quota --due` and `ready`'s `quota_due` say not due (`reason: idle`) and record nothing; the usual interval since the last check applies once work starts again, so after an idle spell of `quota_every_minutes` or more the first check is due at once. Idle means no wake-ups either (R66): when the team goes idle, the lead sets its hourly quota timer to `enabled=false` and switches it back on when it first dispatches work again; woken while idle, it does not reply to the user. Record the timer's id with `prefs --set quota_trigger_id=<trig_...>` (empty if there is none) and each switch with `prefs --set quota_trigger_on=true|false`; `ready` and `wrapup` then say "turn the quota timer off" (idle, timer on) or "turn it on" (work running or ready to send out, timer off), as the field `quota_trigger: off|on`. An id with no recorded switch counts as on. After a lead handover, record the new lead's timer id. tw.py cannot switch the timer itself, so it only says when. Quota is not part of every report. Several `--config-dir` folders are read one by one and the most severe tier decides. Notes (a stale cache, an inferred 0%, a state that could not be saved) end the line. When the output says `changed: yes`, the lead tells the user in one line
(new tier, what changes) and sends every thread one `INFO` with the tier and the routing change (`TW info`).
Threads never read the quota themselves.

At `quota_burn_at` (70% by default), `ready` reports the effective check-in and quota/document refresh intervals. `slow_factor` multiplies both (2 by default; 3 at `lead-only`). Stuck and waiting-on-you notices keep their own timing. The first `quota` or `ready` run after a tier transition prints one notice. `quota --weekly` compares Fable and Opus using tokens, then minutes, then same-kind rework rate; it reports insufficient data only when every measure has fewer than three pairs.

## 3. Switching up when a model does badly

Move the same piece of work one step up (GPT → Fable → Opus) when any of these holds, and say so in the report
("this X did not work on Y, moved to Z, because ..."):

1. a review found blocking problems and they are still there after one fix round;
2. tests or acceptance fail, or the DONE misses an item of Done when;
3. it is plainly stuck: the same step again and again, the task misread, no progress for a long time;
4. this kind of work is on the model's deny list (below);
5. the scoped model reports its limit: no more work to it this window, until the next `TW quota` confirms.

In `save` and `lead-only`, a thread does not move up to Claude by itself: when GPT fails it stops and reports to
the lead, which decides between Opus now and waiting for the reset. When Opus fails too, the lead asks the user.

## 3a. Fixed rules for work given to GPT (D7)

Every brief to GPT carries them, and the Claude side that hands the work out checks the result against them before it is committed or reported:

- do not change tests to fit the code: fix the code, or report why a test is wrong;
- do not touch changes you did not make (other threads', the user's, uncommitted work);
- do not delete or weaken existing features outside the task;
- finish the task or say exactly what is left: no half-done work reported as done;
- precision work the user named (`by hand, no scripts`) is never given to GPT.

The aim is more work on GPT without more redo. The lead looks at the redo count once a week (`TW model-log --summary`, the `redo` share per type for GPT) in its weekly note; a rising share for a type is a reason to brief that type better or move it up a step (section 3).

## 4. The learning table

Threads add one line to DONE: `models: <type>=<model>:ok|redo ...` (types: `code, test, read, review1, review2,
docs-en, docs-zh, research, debug-hard, plan, merge`). The lead records each with
`TW model-log --task T<n> --type <type> --model gpt|fable|opus --result ok|redo --why "<one line>" [--tokens N|--minutes N]`; include tokens or minutes when known.

`TW model-log --summary` adds a (type, model) to the deny list after at least 3 tries with 2 redos among the
last 3 (`proposed` in its JSON); from then on that work goes straight to the next model. Putting GPT's big categories (`code`, `test`) on
the list needs the user's yes first (`--deny ... --user-said`), because it spends more Claude quota. A pair last
tried 30 days ago or more is suggested for one retry; when it works, `--allow ... --user-said` takes it off. The
lead's weekly note (before the reset) gives one line on the week: total used, the scoped model, GPT, and changes
to the deny list.

## 5. Model limits that shape this

- A running session's model cannot be changed by a tool; `create_session(model=...)` applies to new seats only,
  and the user can switch a session by hand in claude.ai. `get_session` shows the configured and the served model.
- A new seat has a fixed start-up cost (protocol, context, handover), so a seat is not swapped only to change its
  model; existing threads route through subagents and GPT instead, which costs nothing to switch.
- Every subagent has a fixed overhead too: batch small jobs into one.
- Use GPT from a thread through the codex plugin (the `codex:codex-rescue` agent, or its companion script with
  `task` and, for background jobs, `status` / `result`), in the thread's own worktree, with a self-contained
  brief: GPT does not see the thread's context.
- When Codex returns `model_not_found`, that model cannot be used under the authentication this computer's Codex already has. Switch to a model this computer's Codex actually runs (see the model of the latest successful run in `~/.codex/sessions`) and retry; do not mark GPT unavailable for that alone, and do not change any login or authentication. The pref `gpt_model` records the model in use (`TW prefs --set gpt_model=<model>`); a brief for GPT names no other model.
- Claude subagents default to `model: "fable"`; use `opus` only for Opus-only work or after a switch up.
