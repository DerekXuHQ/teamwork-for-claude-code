# Messages the lead sends

Every message starts with one header line. Threads use the same header for their replies (see the worker protocol).

```
[tw:<short>] <TYPE> <from> -> <to> <task-id or ->
```

The task-id slot holds exactly one id from the board (`T3`), or `-` when no board task applies. No lists (`T2,T7`), no sub-step or probe ids (`P2`); if the work was merged into another task, use that task's id and say so in the body.

`<short>` is the project's short name (`prefs.short_name`, e.g. `TFCC`; projects without one use the full name). It is display only: branches keep the full project name.

The recipient's app shows only the first line as a preview, so the header must say what this is.

The header's keywords (`tw`, the TYPE, `->`) are always English, so they can be parsed and scanned. The body is written in the conversation language (`prefs.language`, the language the user speaks): a Chinese-speaking user means Chinese bodies between the lead and threads and between threads. When the user switches language, the lead updates `prefs.language` and sends every open thread a FOLLOWUP:

```
[tw:shop] FOLLOWUP lead -> ui -
从现在起用中文：给 lead 和其他线程的消息正文都用中文，消息头关键字不变。用户直接用别的语言跟你说话时，用那种语言回他。
```

## TASK: new work for a thread

```
[tw:shop] TASK lead -> ui T3
Goal: redesign the order list page so status, total and the next action fit above the fold.
Context: spec at docs/spec.md §3; design notes in .teamwork/MEMORY.md; the api thread owns /api/orders.
Done when: the page renders with seeded data at phone and desktop widths, screenshots saved to docs/shots/, tests pass.
Constraints: do not change API contracts (api owns them; hand off if you need a field). Work on tw/shop/ui.
Report: ACK now, DONE or BLOCKED when finished (DONE with the review verdict, `rounds: N`, remaining and deferred findings). End every turn with the tw-status block.
```

## FOLLOWUP: more work in an area the thread already owns

Keeps the thread's history. Say what changed since last time.

```
[tw:shop] FOLLOWUP lead -> ui T3
User feedback on your screenshots: move the status badge to the top-right and make the total tappable.
Everything else stays. Same branch. Report DONE when updated screenshots are saved.
```

## RECALL: stop and hand back

If the thread is mid-turn and must stop now, call `interrupt_session` **first** and send the RECALL **after** it: an interrupt clears the thread's queued messages (`cancel_queued: true`), so a RECALL sent before it is lost. The interrupted thread only sees "[Request interrupted by user]", which looks like the user stopped it, so the first sentence of the RECALL is always "The lead just interrupted you." (in the conversation language, e.g. 「lead 刚打断了你。」).

```
[tw:shop] RECALL lead -> data T5
The lead just interrupted you. Stop at a safe point. Commit WIP to tw/shop/data (`T5: WIP ...`), push, and reply with: done, not done, assumptions, where the code is.
Reason: the user wants to rethink the schema first. Do not start anything new until a new TASK.
```

## ROSTER: who owns what now

Send to every active thread after adding, removing or re-scoping a thread. Body is `TW roster` output.

```
[tw:shop] ROSTER lead -> all -
<TW roster output>
```

## ANSWER: reply to a QUESTION

```
[tw:shop] ANSWER lead -> api T4
The user decided: keep refunds in the orders table with a `kind` column. Recorded in MEMORY.md.
```

## NEED-THREAD: a thread asks for a new thread

Threads never create sessions. When a thread finds work that needs its own seat (a new area, or more than it can carry), it sends this to the lead. Fixed body fields, so the lead can turn it into `TW thread add` and `TW task add`:

```
[tw:shop] NEED-THREAD ui -> lead T3
name: search
kind: rc
why: the order list needs full-text search; it is its own area with its own index and tests.
takes over: the search half of T3
owns: api/search/**, web/search/**
```

Cloud threads cannot message the lead: they write the same fields, separated by `;`, on the `need_thread:` line of their STATUS block, and the lead picks it up when it sweeps (SKILL.md step 5); the line goes back to `-` once they see the answer. rc threads send the message and leave that line `-`. The lead records each request once with `TW ask ... --need-thread <name>`, which refuses repeats.

`kind: cloud` only with a reason on the same line: the thread never touches the project folder and never needs to message rc threads. The lead decides. It may give the work to an existing thread instead, or propose the new thread to the user first if `propose_before_start` is true. Either way it answers with `ANSWER` and, if it creates the thread, a `ROSTER`.

## Rules for writing tasks

- One task, one owner, one "done when" the thread can check itself.
- Put file paths, branch names and the other owners it depends on in the message; do not assume the thread remembers the board.
- Cloud threads: say which repo and branch; they cannot see the project folder, so paste or commit anything they need.
- Write the brief in the conversation language; the thread writes its subagents' briefs in it too.
- Never put secrets in a message.
