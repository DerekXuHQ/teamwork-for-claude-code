# Lessons from a real overnight run (2026-11-17/18)

One lead session coordinated about 27 rc threads for roughly 10 hours, from a pile of planning
documents to an app that was close to complete. Looking back afterwards, the user called the result
very good, the way the team communicated most of all. This file collects what can be reused. Product, requirements, names
and session ids are left out on purpose; private notes with quotes stay outside the repo.

Method: every transcript of the run (lead, an earlier lead for the same project, all threads) was
condensed to its messages, then read by parallel subagents: one for every user instruction to the
lead, three for the lead's history by time, four for the threads in groups. Their findings were
compared against `SKILL.md`, `templates/worker-protocol.md` and `references/messages.md` as of
main `b894579` (re-checked against `3deb85c`). Times below are UTC.

## 1. Shape of the run

| Phase | What happened |
|---|---|
| Intake | Lead read the user's document pack, opened 2 planning threads, later a reference thread. |
| Planning | One thread collected 12 unrelated tasks until the user objected; then one thread per topic (ledger, pages, architecture, AI, content, dev plan). |
| Skeleton (~04:40) | One shell thread built the app skeleton with registries and per-thread files, target one hour. |
| Fan-out (05:19–05:27) | About 14 feature threads opened within 8 minutes, each owning explicit file globs and using fake implementations until dependencies landed. |
| Integration (06:00–08:00) | Threads sent DONE, the lead merged and handed out the next task from each thread's deferred list; a QA thread ran a regression round and routed about 90 findings to owners in 80 s. |
| Stall (06:57–13:51) | The lead blocked on its own question to the user; see §4.1. |
| Resume (13:51–) | The lead merged 16 branches in one wave (791 tests green), recovered from context exhaustion from its own merge table. |

## 2. What worked (keep doing it)

**Lead to user**
- Every report opened with a one-line headline, then intent and success criterion in one line
  each, then "done / proposed / dropped", and ended with the one or two things only the user can give.
- Each question carried a recommendation and a one-word answer format, plus "no answer = I take the
  recommendation". The user rarely had to answer.
- Corrections were acknowledged in one line ("you are right, my mistake"), followed by what changed,
  that it was written into the project rules, and which threads were told. The user's strongest
  praise came right after such a round.
- Deliverables were sent as documents that open on a phone, with a status line (draft / final,
  what is missing), never as paths on the computer.

**Lead to threads**
- A stable TASK skeleton: task / read first (exact paths) / dependencies (what exists on main, what
  to fake until then) / approach / done when / report. Threads ACKed in 4–15 s and most asked nothing back.
- The user's own words, quoted at the top when the task came from them. Threads hit the intent first time.
- Explicit file ownership per task ("you change only these paths") plus fakes for unready
  dependencies, plus a skeleton thread that generated shared wiring so nobody edited it by hand.
  This is what made 14 threads start in the same minutes and merge with few conflicts.
- "Deliver the slice others need first, commit it separately, UPDATE me", naming who waits. Worked
  every time with no follow-ups (first slices in 5–14 minutes).
- Review cost stated per message: "mechanical change, no review, reply in one line" came back in
  under a minute; scope was matched to size.
- Close-and-continue messages: "merged at <commit>, main has N tests green, T40 closed; here is what
  I changed while merging; T50 = the first three items of your deferred list". Long-lived threads
  kept moving without re-explaining.
- Decisions sent with their reasons; rule boundaries clarified ("this rule means X, not Y");
  a peer's better answer acknowledged ("this is more correct than what I said; keep it").
- Team-wide rules went into the project instructions plus a short per-thread message with one
  example from that thread's own area.
- Pre-queued sequences ("T61 → T62 → T65, do not stop to wait for me") were the only work that kept
  flowing during the lead's stall.

**Threads**
- ACK in seconds, in a fixed form, stating the approach; the lead corrected course within seconds.
- Contracts published to peers before implementation; handoffs carried a contract and a fallback;
  "default on silence" clauses ("no reply = I go with this version, marked as unconfirmed").
- Peers routed work to each other directly (one thread sent tasks to 9 peers; answers with commits in 1–8 min).
- Threads told the lead the merge order and which commits belong together; did trial merges to check
  a dependency; asked other owners for "add only, don't change" with root cause and tests.
- A DONE schema rich enough to merge without reading the diff: branch@commit, merge-readiness,
  verification commands with counts, review verdict and rounds, deferred items, items to relay.
- Threads proposed mechanisms the lead then made project-wide (wiring auto-collection, a "rough,
  to tune" list, release gating, a shared "AI unavailable" component, process cleanup).
- QA: own build, crawl of every page, parallel subagents for deep flows, one message per owning
  thread with blocking / important / minor and expected behaviour; its scripts became a shared check.

## 3. What the user asked of the lead (generic form)

About 50 instructions about how to lead were found. All but one were eventually followed, but
several had to be repeated before they held: a banned topic three times, a waived security
reminder twice, "take all the material I pointed to" once more after the first time (counts are
how often the user had to say it).
The one that was lost was "do it now, don't make me remind you later". Grouped:

1. **Set up as the skill says** before any real work: the lead renames its own session to "lead",
   then ledger, roles. This was the user's very first correction.
2. **Threads do the work, not the lead's own subagents.** One topic per thread; never pile
   unrelated work on one thread ("its context gets polluted").
3. **Open threads when the phase changes**, and parallelise hard up to the session limit behind a
   standard internal interface; "I am not afraid of mistakes, I am afraid of no output". The user
   said budget was not the constraint.
4. **Standing "you decide" grants are used**: archiving finished threads to free seats, choosing
   when low-priority work runs, merging. Each was given once and should be recorded once.
5. **A coordinator that only forwards the user's words is not doing its job**: digest them into tasks.
6. **Ask vs decide.** In the build phase decide everything that real-world logic, earlier
   decisions, the project's records or a quick public search already answer; check a thread's question against recorded decisions before relaying it; never
   bring the user fine detail before the visible basics exist. When the user's framing is wrong,
   say so with reasons; the user asked for that.
7. **Banned topics stay banned**: once the user rules a topic out, it leaves every later message,
   side notes included, and every thread is told. Things the user said they own or will provide
   leave the risk list and the open questions too. When a cautious lead default conflicts with the
   delivery form the user stated, the user's form wins.
8. **Merging reviewed, non-conflicting branches is the lead's call**; do not ask.
9. **Breadth first, depth on the strengths** ("long-board" principle): do not treat every gap as a
   blocker; limit review rigour in early phases; go deep only where the product must be real.
   When the user states a time budget, plan to it: order by priority, parallelise, and say what
   will still be rough at that time.
10. **Depth by importance.** For non-core topics stop at plan level: one recommended scheme, no
    execution detail. For decisions the user owns, give a complete, filterable list or a
    side-by-side comparison plus a recommendation.
11. **Deliverables open on the phone** (rendered tables), are complete (nothing lost, "dedupe means
    union"), concise and layered (headline items first), and external versions carry no internal
    hedging. The form the user asked for is the acceptance test; settled points are written in
    directly, not asked again. Keep an index of all outputs in delivery order, and keep the target
    deliverables and their status in front of the user.
12. **Measure before claiming**: assess material before praising it; counts and coverage must match
    what the user sees; do not chase things the user declared unnecessary.
13. **Questions in plain words**, self-contained, no internal labels.
14. **Every request in one log the user can open**, with done / not done.
15. **Run unattended**: no permission prompts overnight; find the root cause of repeated prompts.
16. **Explain side effects on the user's machine** and never touch the user's own apps; whoever
    starts test apps or servers closes them.
17. **Use the user's own tools and reference code** when stuck; take everything they pointed to.
18. **Rules found in old history may have been temporary**: confirm before enforcing them.
19. **Hold while input is still arriving.** When the user says more material is coming (asked four
    times): acknowledge in one line, ask nothing, conclude nothing, keep earlier analysis and
    pending questions as they are, and prepare. When the user ranks the material (current vs
    reference), read the current files completely first. When the input is complete, send one
    integrated plan plus the short list of what only the user can give.
20. **Tool and model by task.** Judgment, planning and precision-critical writing stay with the
    strongest model and are written by hand (no generation scripts, no bulk model); bulk execution,
    tests and regression may go to another model.

The one request that was lost arrived in the same message as an interrupt and never became a
ledger entry. Eight user messages were comments typed while viewing a document; they arrive wrapped
in a document-context block and are easy to miss when scanning for user input.

## 4. Failures and friction

1. **The lead blocked for 6 h 54 min on its own question.** At 06:57 it used a blocking question
   tool (the answer it waited for was not even needed: the affected thread had already been told
   "not in this build"). About 50–60 messages queued, including every DONE; the user's own message
   at 13:51 queued too. The whole team idled, because only the lead merges.
2. **Lead-only merging is the team's speed limit.** By 08:03 every thread but one was "waiting for
   the lead to merge". With the lead down there was no fallback.
3. **QA tested a stale main** while about 11 fix branches were unmerged, so a large share of its
   reports were "already fixed on my branch". One defect was reported by 9 threads, another hit by 3
   threads within 2 minutes: there was no shared known-issues list and no broadcast.
4. **"FIX" meant two things**: fixed on the branch, or merged.
5. **Thread granularity was wrong at first**: one planning thread got 26 lead messages and compacted
   5 times; a narrow thread finished in 31 minutes with no follow-ups.
6. **Direction changes streamed into busy threads**: one thread got 12 follow-ups in 50 minutes,
   another 6 in 45 and reviewed a dead draft; one thread relayed a user decision to a peer just
   before the user reversed it.
7. **Defaults the lead chose for product behaviour** were reversed by the user minutes after DONE.
   Technical defaults were fine; product behaviour defaults were not.
8. **Spend and rate limits stalled sessions with no recovery**: the lead sat idle about 1 h 50 min
   after a monthly spend limit until the user typed "try again"; two threads hit it at the same
   time; one thread named the wrong quota as exhausted.
9. **Permission prompts overnight** came from command shape (a plugin's guard on recursive force
   deletes), not from the tool. The lead fixed it by editing that plugin's guard, which is the
   user's tooling and should not be changed without asking.
10. **Remote Control seats**: the 32-session limit held back a wave for about 40 minutes.
11. **Rules announced before their tool was on main**: three threads got "Command not found" within a minute.
12. **Missing context in first TASKs**: peer session ids arrived 3 minutes later, one TASK had a
    placeholder id, a TASK arrived before the ROSTER, one brief contained a name the user later
    asked to keep out.
13. **Protocol handed over as a pointer to a file under `/tmp`**: too long to read in one go, and
    `/tmp` can be cleaned. Status block format then drifted between threads.
14. **Drift after context compaction**: threads switched to English and dropped or garbled the
    header (`[x → lead]`, sender and recipient swapped); the lead also replied in English twice.
15. **Markers and examples leaked into the product**: "rough, to tune" labels landed in the UI;
    an example sentence from a TASK became a button in the release build.
16. **Ownership at seams was unclear** (pages rendered by several modules); one bug took 4 hops to its owner.
17. **Task ids reused after "closed"** (one task closed twice, 12 follow-ups).
18. **Smoke tests ran in the user's real app-data folder**; about a dozen test windows and servers
    were left running.
19. **Reports with nothing for the user** ("nothing for you to do") and open questions repeated in
    almost every report until answered.
20. **Subagent output blocked by the content filter** during a bulk text extraction; a background
    job hit the 2-hour limit. Threads recovered on their own.
21. **A banned topic came back.** About 80 minutes after the user ruled a topic out, it reappeared
    in a side note of a status report; the user had to repeat the ban three times, the last time
    angrily. A waived reminder (rotating a key) was raised again the same way.
22. **The long-running lead got worse over time.** After many hours and several compactions the
    user said, in effect, that the lead was getting slower-witted; the questions it relayed were
    ones earlier decisions already answered.

Not seen in this run: push approvals (no remote), cloud threads (all rc, permission mode `auto`
with the reporting tools pre-allowed), RECALLs, child-session failure events.

## 5. Compared with the skill today

Already covered by TFCC (no change needed beyond wording): one ledger and `--user-said` rules;
ownership areas and file leases; record every request before routing; language rule; ROSTER after
creating a thread; `extra_allowed_tools` and `permission_mode`; independent review with a round
limit; intake with do / propose / drop; capacity check before creating rc threads; "keep prompts
rare" (scratch in `/tmp`, Edit not heredocs); "after compaction run `TW board`" for the lead;
the protocol goes into `append_system_prompt` from `TW worker-prompt` (the run deviated from this,
see IL-19); "who works where" (the lead does not use its own subagents for work, threads are
created with `create_session`); "never wait silently" and "do not repeat settled things" in the
worker protocol; `review_max_rounds` as a standing preference.

Missing or different: everything in §6, except where an item says the rule already exists and
only needs tightening.

## 6. Proposed changes (for core)

Numbered for reference (IL = item from this lessons list); files are relative to the repo root.

**Keep the team moving**

- **IL-1 No blocking questions in the lead.** `SKILL.md` "Keep the user in one chat" + Rules: the
  lead never uses a tool that blocks its turn for an answer (e.g. `AskUserQuestion`); it asks in
  chat with a recommendation and a default, keeps sweeping and merging, and applies the default
  after a stated deadline (`prefs default_after_minutes`, e.g. 30), noting it as revisable. The
  timeout never applies to approval-gated, irreversible or outward steps ("Approvals happen here"),
  the user's own decisions, plan approval, or anything that needs `--user-said`: those wait, and
  the lead keeps working around them. Write
  this as an explicit rule in `SKILL.md` Rules (the frontmatter has no "do not use" field; check
  whether the harness supports `disallowed-tools` before relying on one).
- **IL-2 Merging is the lead's standing job.** `SKILL.md` §7: replace "On approval, merge" with:
  a task whose review passed or was capped, whose checks pass and whose branch merges cleanly is
  merged without asking once the user has given the standing `merge` grant (IL-30; a new
  `--user-said` gate, since `prefs --user-said` today covers only review settings); only conflicts,
  failing checks or approval-gated steps go to the user.
- **IL-3 Merge queue on the board.** `scripts/tw.py` + board: "Waiting to merge: N branches, oldest X min";
  `TW ready` lists `merge_waiting`; the lead reports it when it exceeds `stuck_minutes`.
- **IL-4 Self-wake.** `SKILL.md` §3.5/§5: before ending any turn with work in flight, the lead sets a
  timer (`sleep … run_in_background` or `send_later`) so it resumes even if a turn ends badly; after a
  spend or rate limit resets, sweep and nudge every thread that stalled on it. Note: a timer does not
  cure a turn held by a blocking tool (that is IL-1), and on a quota stop the wake-up must be set
  after the reset time (`send_later` at the reset), or it fails again.
- **IL-5 Pre-queue sequences.** `references/messages.md` TASK + `SKILL.md` §3 + `scripts/tw.py`:
  chain one owner's tasks with the existing `task add --deps` and say in the first TASK "then T62,
  then T65; do not stop to wait for me", with priority and resource rules. Today `task set --status
  dispatched` refuses a task whose deps are open (and the SKILL ledger table says the same), so
  either the later tasks stay `approved` on the board and are only announced in the TASK body, or
  add an explicit exception: a same-owner chain may be dispatched together.
- **IL-6 Unblocking slice first.** `references/messages.md` TASK rules: when others wait on this task,
  name them and ask for the slice they need first, committed separately, with an UPDATE.

**Messages**

- **IL-7 Richer TASK template.** `references/messages.md`: Goal / User's words (verbatim, when from
  the user) / Read first (paths) / Depends on (what is on main, what to fake until then) / You change
  only (globs) / Not doing / Done when (what the DONE must contain) / Review depth (full, one round,
  none) / Report. Keep the old example as the short form.
- **IL-8 Close-and-continue FOLLOWUP.** `references/messages.md`: merged commit, test count on the
  base, what the lead changed while merging, "T<n> closed", next task id from the thread's deferred
  list, who was told. A reopened task gets a new id.
- **IL-9 Decisions with reasons and superseded labels.** `references/messages.md` ANSWER/FOLLOWUP:
  every decision carries its reason; a reversed decision is named ("the earlier '<x>' is replaced").
  Only the lead broadcasts user decisions; threads relay a decision only when the lead says so.
- **IL-10 Batch direction changes.** `SKILL.md` §4: while the user is still deciding, do not stream
  follow-ups to a busy thread; send "pause, wait for a summary" and then one consolidated FOLLOWUP.
- **IL-11 Never send placeholders.** `SKILL.md` §3: `TW worker-prompt` already embeds the current
  roster, but threads created early in a wave cannot see peers created later, and the TASK is the
  `create_session` prompt itself. So: in a wave, create every session first, then send one ROSTER,
  or put peer session ids in the TASK's "Depends on". The placeholder check belongs on the
  hand-written TASK, not on `TW worker-prompt` (which renders from state): a new
  `TW check-msg <file>` the lead runs before sending, or at least a written rule.
- **IL-12 Mark examples and dev markers.** `references/messages.md`: example strings in a TASK are
  labelled "example, not product text"; dev markers ("rough, to tune") go in code comments or the
  DONE, never in user-visible text.

**Ledger and visibility for the user**

- **IL-13 A board the user can open.** `SKILL.md` §5.9 + `scripts/tw.py`: add `TW board --share`
  that prints a phone-readable summary (first the target deliverables: name → status → link; then
  the user's requests → status → where the result is); the lead keeps it as a document or artifact
  visible only in the user's own account, updates it on each report, not only `BOARD.md` on disk.
  The same as for IL-14: sharing it with anyone else or making it public stays an approval step
  ("Approvals happen here").
- **IL-14 Deliverables as openable links.** `SKILL.md` "Report results": every user-facing
  deliverable is reported as a link that renders on a phone, with a status line (draft / final,
  what is missing); keep an index of outputs in delivery order. The link is private to the user's
  account unless the user asks to share it.
- **IL-15 Interrupted and document-view input.** `SKILL.md` §4: after an interrupt, re-read the
  user's last message and record every ask in it; comments the user types while viewing a document
  arrive in a context block and are user messages too.
- **IL-16 Banned topics and recorded decisions.** the initial `MEMORY.md` that `scripts/tw.py init`
  writes gets a "Never raise" section (`TW memory --never-raise "<topic>"` appends to it): topics the user ruled out, plus a "User-owned" sub-list
  (things the user provides or decides themselves). The lead checks reports, risk lists and relayed
  thread questions against it and against recorded decisions before sending, side notes and risk
  lines included (`TW check-msg`, IL-11, can flag never-raise words).
- **IL-17 Ask vs decide by phase.** `SKILL.md` §4 intake: technical defaults are the lead's;
  product-behaviour defaults are proposed or marked provisional; questions are plain, self-contained,
  one recommendation, one-word answer; for decisions the user owns, give the complete list or a
  side-by-side comparison with the recommendation; non-core topics stay at plan level; disagree
  with reasons when the user's framing is wrong; repeat open questions once per report at most, and
  send no report that has nothing for the user.

**Threads**

- **IL-18 Re-read after compaction.** `templates/worker-protocol.md`: after a context summary,
  re-read this protocol's header, language and STATUS sections before the next message. The
  SessionStart hook already runs `TW context` (board, instructions, memory); extend `TW context` to
  repeat the header format and the language line, and confirm the hook also fires after a compact.
  Same for the lead (`SKILL.md` Rules: language and header too).
- **IL-19 Keep the protocol in the session.** `SKILL.md` §3 already says `append_system_prompt` is
  the output of `TW worker-prompt`; the run deviated by passing a pointer to a file under `/tmp`.
  Tighten: "never replace it with a file pointer", and reorder `templates/worker-protocol.md` so
  tasks and done-when come first and the long rules after.
- **IL-20 Known-issues list and broadcast.** `scripts/tw.py issue add/list` + worker protocol: one
  shared list (owner, status: open / fixed-on-branch / merged / verified); threads check it before
  reporting; "main is red" goes there first. Add a new message type `INFO` (`lead -> all`, no reply
  needed) to `references/messages.md` and to "Types you may receive" in `templates/worker-protocol.md`.
- **IL-21 Fix states.** `templates/worker-protocol.md`: `FIXED <branch>@<commit>` vs merged vs
  verified; QA re-tests only merged or the integration of ready branches.
- **IL-22 QA after merge.** `SKILL.md` §7: start a regression round after the pending wave is merged,
  or have QA test an integration branch of all ready branches; QA reports include the owner from the
  path → ownership table (a new `TW route --path <file>` in `scripts/tw.py`).
- **IL-23 Process and data hygiene.** `templates/worker-protocol.md` Working rules: whoever starts an
  app, server or browser closes it; never use the user's own apps or real data folders (temp dirs by
  default); DONE says "processes closed".
- **IL-24 Command shapes that trigger guards.** `templates/worker-protocol.md` + `references/setup.md`:
  list the commands to avoid (recursive force delete, `reset --hard`, `clean -f`, branch force delete)
  and the safe alternative (`mktemp -d`, leave temp dirs); copy the list into subagent briefs. The lead
  never edits the user's plugins or hooks to silence prompts; it proposes an allow rule instead.
- **IL-25 Don't announce what isn't merged.** `SKILL.md` §7: a rule that relies on a script or
  command is broadcast only after that script is on the base.
- **IL-26 Name the limit correctly.** `templates/worker-protocol.md`: on a quota or spend stop, report
  which one (session 5-hour, monthly spend, another tool's quota) and when it resets.

**Planning**

- **IL-27 Skeleton first, then fan out.** `SKILL.md` §2: for a new codebase plan a skeleton task that
  creates registries, per-thread files and a written internal interface, and fakes for every
  cross-thread call; feature threads start when it lands, all at once.
- **IL-28 One owner per seam.** `SKILL.md` §2: pages or flows that several modules render get one
  named owner in `owns`.
- **IL-29 One topic per thread, checked on every route.** `SKILL.md` §4: before giving new work to an
  existing thread, check its topic matches the thread's focus; a new topic gets a new thread.
- **IL-30 Standing grants.** `SKILL.md` §3 capacity, §6 (last rule) and §8 all forbid the lead to
  archive on its own; change all three to allow a standing grant. Generalise it as
  `prefs standing_grants=archive,schedule,merge`, each set once with `--user-said`: archive threads
  whose work is merged, choose when low-priority work runs, merge per IL-2; report each use.
- **IL-31 Model and method per task.** `scripts/tw.py thread add --model` sets a thread default;
  `task add --model <m> --no-scripts` overrides it per task; the TASK template (IL-7) gains a
  "Model / method" line. Judgment and precision-critical writing stay with the strongest model,
  written by hand; bulk execution and regression may go elsewhere. `create_session` passes `model`
  when set.
- **IL-32 Review depth by phase.** `SKILL.md` §7: `review_max_rounds` is already a standing
  preference; add a standing user decision for review depth per phase (deep for documents that go
  to outside parties, one round for scaffolding, none for mechanical edits), recorded once with
  `--user-said`, instead of per-task `--skip-review`.

**Intake and setup**

- **IL-33 Hold while input is arriving.** `SKILL.md` §4 Intake: when the user says more material is
  coming, acknowledge in one line, ask nothing, conclude nothing, keep earlier analysis and pending
  questions, and prepare; when the input is complete, send one integrated plan (do / propose /
  drop) plus what only the user can give. `scripts/tw.py req` gains `--status held` so the board
  shows "waiting for the user's material".
- **IL-34 The lead sets itself up first.** `SKILL.md` §0 after step 2: `set_session_title` to
  `[<short>] lead` and `set_session_tags` to `teamwork:<short>`, `teamwork-thread:lead`; `TW lead`
  prints the suggested title and tags like `thread add` does. §1 ends with a setup checklist (title,
  ledger, prefs, hooks, instructions) that must be complete before real work starts.
- **IL-35 Digest, and lose nothing.** `references/messages.md` TASK: "User's words" is evidence,
  "Goal" is the lead's digested target (never only forwarded words). Merge or dedupe tasks must say
  in done-when "every source checked, 0 items missing; dedupe keeps the union".
- **IL-36 Temporary rules.** `SKILL.md` §5.5 and the memory row of its ledger table, plus
  `scripts/tw.py memory --temporary`: a MEMORY entry can be marked temporary; rules found only in
  old history are confirmed with the user before they are enforced.
- **IL-37 Fresh lead after long runs.** `SKILL.md` §5: after N compactions of the lead (or when the
  user says its quality is dropping), hand over to a new lead session with `TW lead --force`
  through the existing attach flow; the board and ledger carry the state. The old lead proposes it;
  the user opens the new session (only the user starts a lead).
- **IL-38 Complete requirement list.** `SKILL.md` §5.9 + `TW board --share` (IL-13): the user's
  view can list every requirement, goal and constraint by phase and category, so the user can
  strike out what is stale.
