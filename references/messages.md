# Messages the lead sends

Every message starts with one header line. Threads use the same header for their replies (see the worker protocol).

```
[tw:<project>] <TYPE> <from> -> <to> <task-id or ->
```

The recipient's app shows only the first line as a preview, so the header must say what this is.

## TASK: new work for a thread

```
[tw:shop] TASK lead -> ui T3
Goal: redesign the order list page so status, total and the next action fit above the fold.
Context: spec at docs/spec.md §3; design notes in .teamwork/MEMORY.md; the api thread owns /api/orders.
Done when: the page renders with seeded data at phone and desktop widths, screenshots saved to docs/shots/, tests pass.
Constraints: do not change API contracts (api owns them; hand off if you need a field). Work on tw/shop/ui.
Report: ACK now, DONE or BLOCKED when finished. End every turn with the tw-status block.
```

## FOLLOWUP: more work in an area the thread already owns

Keeps the thread's history. Say what changed since last time.

```
[tw:shop] FOLLOWUP lead -> ui T3
User feedback on your screenshots: move the status badge to the top-right and make the total tappable.
Everything else stays. Same branch. Report DONE when updated screenshots are saved.
```

## RECALL: stop and hand back

```
[tw:shop] RECALL lead -> data T5
Stop at a safe point. Commit WIP to tw/shop/data, push, and reply with: done, not done, assumptions, where the code is.
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

## Rules for writing tasks

- One task, one owner, one "done when" the thread can check itself.
- Put file paths, branch names and the other owners it depends on in the message; do not assume the thread remembers the board.
- Cloud threads: say which repo and branch; they cannot see the project folder, so paste or commit anything they need.
- Never put secrets in a message.
