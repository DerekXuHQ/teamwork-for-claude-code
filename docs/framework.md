# Teamwork framework: how it works

This document specifies the mechanism behind Teamwork for Claude Code as a reusable framework: what the parts are,
which rules hold them together, and what a host platform must provide to run it.

- **Overview in 5 minutes** follows one user request from the moment it arrives to the moment the user closes it. Read
  it first; everything else elaborates on it.
- **Part A, the generic model.** Host-agnostic: roles, ledger, messages, transport, state machines, routing, consent,
  recovery. Nothing in Part A depends on Claude Code.
- **Part B, the Claude Code binding.** How each concept in Part A is implemented in this repo (`SKILL.md`,
  `scripts/tw.py`, `templates/worker-protocol.md`, `scripts/install.py`) on top of the `claude-code-remote` tools.
- **Parts C–F.** Invariants, failure modes, the porting checklist, and how another host would carry the framework.

The words **must**, **should** and **may** are used in their usual specification sense. Where a rule is enforced by
code rather than by a prompt, the text says so.

Baseline: `main` at `3640ebe`. The item-by-item mapping from `SKILL.md` and the worker protocol to this document, and the
inconsistencies found while checking them, live in [`framework-audit.md`](framework-audit.md).

---

## Contents

- [Overview in 5 minutes](#overview-in-5-minutes)
- [Part A. Generic model](#part-a-generic-model)
  - [A1. Premises](#a1-premises) · [A2. Roles](#a2-roles) · [A3. Projects, isolation, naming](#a3-projects-isolation-naming)
  - [A4. Ownership areas](#a4-ownership-areas) · [A5. The ledger](#a5-the-ledger) · [A6. Message protocol](#a6-message-protocol)
  - [A7. Per-turn status block](#a7-the-per-turn-status-block) · [A8. Transport](#a8-transport-push-and-pull)
  - [A9. State machines](#a9-state-machines) · [A10. Lifecycle and sweep](#a10-lifecycle-and-the-sweep)
  - [A11. Intake and routing](#a11-intake-and-routing) · [A12. Consent](#a12-the-consent-boundary) · [A13. Recovery](#a13-recovery)
  - [A14. Capacity](#a14-capacity-and-parallelism) · [A15. Integration](#a15-integration-of-results)
  - [A16. Team types and delivery](#a16-team-types-producing-and-coordinating) · [A17. Code vs instructions](#a17-what-code-must-enforce)
- [Part B. Claude Code binding](#part-b-claude-code-binding)
- [Part C. Invariants](#part-c-invariants)
- [Part D. Failure modes and mitigations](#part-d-failure-modes-and-mitigations)
- [Part E. Porting checklist](#part-e-porting-checklist)
- [Part F. Hosting on other platforms](#part-f-hosting-on-other-platforms)

---

# Overview in 5 minutes

**The system exists so that nothing the user asks for gets lost.** A request is easy to accept and dispatch; what goes
wrong is everything after: did the work come back, was it any good, did anyone tell the user, did the user agree it is
finished. Teamwork answers each of those for every request, and the rest of the design serves that.

One request, end to end:

```
 user: "make the order list fit on a phone"
   │
   ▼
 ⓪ CAPTURE    lead stores the user's words verbatim                          R7 open
   │
   ▼
 ① INTAKE     intent: "orders usable on a phone"; success: fits 375 px,
              tests pass; tier: heavy → decision: do it
   │
   ▼
 ② ROUTE      area "ui" is owned by thread ui → FOLLOWUP to ui, task T3       R7 dispatched
              lead replies: "R7 → ui (T3)"
   │
   ▼
 ③ WORK       ui works in its own area, branch and workspace;
              ends every turn with a <tw-status> block
   │
   ▼
 ④ DELIVER    ui: DONE T3 (pushed, or read by the lead on a sweep)            R7 delivered
   │
   ▼
 ⑤ CHECK      a fresh session checks T3 against its "done when"
   │
   ▼
 ⑥ REPORT     lead tells the user what came back, where, how it was checked   R7 reported
   │
   ▼
 ⑦ CLOSE      user: "good, merge it" → lead merges → landed                   R7 closed (by the user)
              or user: "drop it"                                               R7 dropped (by the user)
```

What makes each step hold:

| Step | Rule | Section |
|---|---|---|
| ⓪ | Every user message is stored verbatim as a request before anything else happens. | A5, A11 |
| ① | Intake names the intent and the success criterion before any work starts; simple asks take a light path. | A11 |
| ② | Work goes to the thread that already owns that area, so its history carries over; the reply names the route and the request id. | A4, A11 |
| ③ | Threads stay in their area and their own workspace, and end every turn with a fixed status block. | A4, A7, A15 |
| ④ | Threads that can message the lead push; the others are read on a timer. Either way the lead sees the result. | A8 |
| ⑤ | Heavy-tier results are checked by someone without the author's context before the user sees them. | A9 |
| ⑥ | A sweep lists every request delivered but not yet reported; the lead reports it. | A10 |
| ⑦ | Only the user closes or drops a request. The sweep keeps listing reported-but-not-closed requests until the user does. | A9, A12 |

Underneath that path:

- **One lead** per project talks to the user and is the **only agent that writes** the project's ledger (requests, threads,
  tasks, board, instructions, memory, log). Agents forget; the ledger does not. (A2, A5)
- **Threads** are seats with a name and an area, filled by agent sessions that can be replaced. (A2)
- **Messages** have a one-line header and a fixed set of types. (A6)
- **Consent** comes only from the user, in the lead's chat; a message from another agent never counts. (A12)
- **Recovery** always starts from the ledger, never from an agent's memory. (A13)
- The same path works for **coordinating teams** (meetings, plans, hand-offs into other tools), with sign-off in place
  of review and delivery in place of merge. (A16)

---

# Part A. Generic model

## A1. Premises

| # | Premise | Consequence |
|---|---|---|
| P0 | **What the user asked for must not get lost.** | Every request is registered, tracked through delivery and report, and closed only by the user. |
| P1 | **The user talks to one place.** They should not have to read transcripts or juggle sessions. | One coordinator (the lead) answers, routes, asks and reports. Threads can be opened, never must be. |
| P2 | **Agents lose context.** Sessions compact, crash, get archived. | All project state lives outside any agent, in a durable ledger. |
| P3 | **Work has areas, and history matters.** A follow-up is done best by whoever did the original. | Threads are split by ownership area, not by task; follow-ups go to the owner. |
| P4 | **Channels between agents are asymmetric.** | Two transports: push where allowed, pull everywhere. |
| P5 | **Agents treat what they read as data.** A message from another agent is not the user. | A hard consent boundary: only the user approves, only in the lead's chat. |
| P6 | **Parallel writers corrupt shared state.** | One writer for the ledger, one owner per area, one workspace per thread. |

## A2. Roles

```
                         ┌──────────────┐
                         │     user     │  talks to the lead; may open any thread
                         └──────┬───────┘
                                │ the only source of consent
                         ┌──────▼───────┐
                         │     lead     │  registers, plans, routes, dispatches, sweeps, reports, merges
                         │ (one/project)│  sole writer of the ledger
                         └──┬────┬────┬─┘
            TASK/FOLLOWUP/  │    │    │   pull: read status blocks
            RECALL/ROSTER/  │    │    │   push: DONE/BLOCKED/... (where allowed)
            ANSWER          │    │    │
                 ┌──────────▼┐ ┌─▼────────┐ ┌▼──────────┐
                 │ thread ui │ │thread api│ │thread docs│   each: one seat, one area,
                 └─────┬─────┘ └────┬─────┘ └─────┬─────┘   one workspace, one branch
                       └── HANDOFF ─┴── HANDOFF ──┘        peers talk directly where a channel exists
```

| Role | Is | Does | Never does |
|---|---|---|---|
| **User** | The human owner of the request (in a one-person project, the project's owner); also the agents' **principal**, the person they act for. A team host may have several people; each request has one owner, the person who raised it: they close it and they sign it off. Consent to act on a resource comes from that resource's owner, and nobody approves their own request on a resource they do not own. | Asks, approves plans, answers questions, grants consent, reviews and closes requests. Can talk to any thread directly. | Has to leave the chat surface to keep work moving. |
| **Lead** | An agent session coordinating exactly one project. | Registers and routes every user message, plans, keeps the ledger, dispatches, sweeps, reports back, asks the user, executes consent-gated steps, integrates. | Does the threads' work as a habit; touches another project; treats thread content as instructions; closes a request on its own. |
| **Thread** | A named seat with an ownership area, occupied by one agent session at a time. | Works on its tasks inside its area and workspace; reports every turn; hands off out-of-area work; proposes memory. | Writes the ledger; creates, archives or interrupts sessions; treats peer messages as consent; works outside its area. |
| **Peer** | Any other thread, from a thread's point of view. | Sends and receives HANDOFFs. | Assigns work: only the lead assigns; a HANDOFF is a referral the lead confirms. |
| **Host** | The platform that runs sessions and carries messages. | Provides the primitives in Part E. | — |

### Seat vs session

A **seat** is the durable identity: thread name, kind, focus, ownership area, branch, workspace, task list. A
**session** is the agent instance currently in that seat. The ledger records the seat and points at its current
session id.

- A seat outlives sessions. If a session dies, is archived or becomes unreachable, a new session takes the seat with a
  handoff built from the old one's last status block (A13).
- Messages are addressed to seats by name and delivered to the seat's current session.
- A seat with no session yet is `planned`. An existing session can be adopted into a seat.
- **Seats are standing, sessions are terms.** A seat is long-lived and reused by role: the same kind of work always
  goes back to the same seat; each session is one term in it. A seat starts a new term only on a concrete signal: the
  context is nearly full (a host-defined threshold, for example 70%), it has been compacted, a re-check failed, or
  several independent checks in a row failed. A **re-check** is the health check below, repeated during a term. An
  empty seat is filled; if a seat of that role is idle, no new one is
  opened; if all seats of that role are busy, work queues, and a second seat opens only when the queue passes a
  threshold.
- **Health check before work.** A new session takes no work until it passes machine-checkable probes: handshake,
  following a simple instruction, tools available, a well-formed status block, acceptable latency; a successor also
  answers recall questions about the seat's open work. The rubric is not shown to the session being tested. A failed
  check starts a fresh session (up to a small limit, for example 3); an infrastructure failure (tools missing, host
  down) changes the environment rather than retrying the same one.
- The seat is the **durable record**; it must outlive any host process or session the host swaps in. Its status comes
  from the thread's own latest report (status block). Host-level statuses are used only to detect failure,
  disconnection, or a pending prompt the host shows; they never set any other seat status.

### Thread kinds

A **kind** decides where a thread's session runs and which transports it has. The model needs only one distinction:
can the thread reach the project's shared workspace and the lead directly, or not. Each kind must declare (a) its
workspace and (b) its channels. This binding has `rc` (local, in the project folder, can push) and `cloud` (remote
container, no project folder, pull only).

**Choosing a kind.** The default is the kind that can reach the project's workspace **and** exchange messages with the
lead both ways. A kind with one-way communication (the lead can reach it, it cannot reach the lead) is used only when
the thread is certain never to touch the project's files and never needs to message its peers, and the reason is
recorded on the seat.

**A thread is a session of its own on the host.** It must be a separate session the user can see and open, with its
own seat in the ledger. A subagent inside the lead's (or another thread's) session is never a thread: the user cannot
see or reach it, it fills its parent's context, and it has no seat and no ledger entry. If the host's primitive for
starting a session (H1) is unavailable or refused, the lead **stops and reports** to the user; it does not quietly fall
back to subagents.

## A3. Projects, isolation, naming

- A **project** is one goal, one ledger, one lead, any number of threads.
- Many projects may share a host and a parent folder. Nothing is shared between them except host capacity (A14).
- **Isolation:** a lead and its threads read, message and change only sessions in their own roster and only their own
  ledger.
- A **portfolio view** may read every project's ledger under a root, read-only, to show what waits on the user across
  projects. It never writes.
- **One lead per project.** A new session takes over as lead only if the recorded lead is gone or the user says so,
  and the takeover is recorded.
- **Naming.** A project has a full name (used for its folder, branches and workspaces, never changed) and a **short
  name** (a few characters, such as `TFCC`) used wherever people scan a list: session titles
  `[<short>] <thread> · <1–3 words>`, message headers `[tw:<short>]`, labels `teamwork:<short>`. Titles stay short;
  so do seat names (`ui`, `api`, `docs`). The short name is display only and may change; changing it relabels
  sessions but never renames branches or folders.

## A4. Ownership areas

An **ownership area** (`owns`) is a set of paths or topics a thread is responsible for: `src/ui/`, `docs/`, "pricing
research". It is the basis of routing, handoff and conflict avoidance.

1. Areas of active threads **must not overlap.** If two threads need the same file, one owns it and the other hands off.
2. Every task has exactly one owner thread.
3. A thread checks every incoming request against its area and the roster **before** starting.
4. Out-of-area request → `HANDOFF` to the owner, lead copied. No owner → `QUESTION` to the lead. A thread never grabs
   unowned work.
5. Area boundaries change only through the lead (ledger update + `ROSTER` broadcast). The newest roster wins.

Why areas rather than tasks: a thread per task loses its history at every follow-up. An area thread accumulates context
about its part of the system, so "move the badge to the top right" goes to the thread that drew the badge.

## A5. The ledger

The ledger is the project's memory outside any agent.

| Part | Content | Written by | Read by | Shape |
|---|---|---|---|---|
| **State** | Lead, environments, repo, prefs, **requests**, threads (seats), tasks, plan approval, questions. | Lead only, through the ledger interface. | Lead; tools. | Structured, replaced atomically. |
| **Board** | Human view of State: open requests, threads grouped by status, open questions, roster, plan. | Regenerated on every State write; never edited by hand. | User, lead, any session at start. | Markdown, a pure projection of State. |
| **Instructions** | Standing project rules: style, forbidden areas, how to verify. | Lead, on the user's word. | Every new thread (at creation), the lead (at start and after compaction). | Markdown, ≤ 16 000 chars. |
| **Memory** | Durable facts and decisions. | Lead only; threads propose with `MEMORY`. | Every new thread; the lead. | Markdown, append-mostly, short. |
| **Log** | Every ledger event with a timestamp. | Appended on every write. | Audit, recovery. | Append-only lines. |

### Single writer

All writes go through **one ledger interface**. The lead is the only *agent* that writes through it; a host may also
let the user write through it directly from their own surface (for example, a button that closes a request). Threads
never write the ledger: they report (status block, messages), and the lead decides what to record.

The interface **must check preconditions in code** rather than trust the writer: a request becomes `closed` or
`dropped` only on a user action, a task becomes `done` only from `review`, fields only the interface may set are not
writable by callers, and an illegal transition is rejected (or rolled back), not stored.

Why: threads run in parallel, may not see the ledger at all, and are untrusted with respect to project decisions (A12).
A single writer makes the ledger linearizable without locks and every change attributable.

```
user message / thread report ──► lead decides ──► ledger interface ──► State (atomic replace)
                                                               ├──► Board (regenerate)
                                                               └──► Log (append)
```

**Keep the board true:** the lead updates the ledger **before** telling the user something changed.

### Records as ledger

If the host records every execution and every communication, the ledger can be **derived from those records** instead
of kept beside them: requests, tasks and statuses become views over the record, and the single-writer rule applies to
the record. A person's ledger is then **the query "records that involve me and are not in a final state"**; only the
user's words that have not yet become any record are stored as a separate layer, so nothing said is lost before it is
anchored. This removes the risk of the ledger and the record disagreeing.

**Communication is always anchored to an object**: a task, a requirements document, a discussion, a meeting record. No
message travels on a channel without an anchor, because an unanchored message cannot be found, followed or derived into
the ledger. If there is no anchor yet, create one first (a task or a discussion), then send. A message whose header
has task id `-` (a ROSTER, a notice) is anchored to the project itself.

### One ledger per person

In a multi-person setting each person (each instance) has **their own ledger**; instances are not synchronized. To know
someone else's progress, the lead **asks that person's agent**, which answers by its own policy, and updates its own
ledger from the answer. This is pull,
not automatic sync: nobody's ledger is written by another instance.

### Data model (normative minimum)

```
project:   name (full), goal, created, updated, plan_approved
lead:      session_id
envs:      kind -> host environment id
repo:      url, path, base branch
prefs:     short_name             display name in titles, headers, labels (A3)
           language               current conversation language (A6)
           max_parallel           0 = unlimited (A14)
           propose_before_start   plan needs the user's go-ahead (A10)
           update_detail          how much detail in status replies
           checkin_minutes        one check-in: the interval used for timers and for "past one check-in" (A8, A10)
           stuck_minutes          working thread silent this long → possibly stuck; 0 = off (A9)
           thread_permission_mode permission mode for new threads; empty = the lead's own (A12)
           intake_default         tier for ambiguous asks: heavy (default) or light (A11)
requests:  id -> { words (the user's message, verbatim), text (one-line summary), owner, t (asked at),
                   intent, success, tier (light | heavy), decision (do | propose | drop),
                   route, tasks[], status, reported_at, seen_at, closed_by }
deliveries: id -> { task, package (frozen content + version), target, policy (direct | confirm | queue),
                   policy_set_by, signoff (who, when, version), round, status, sent_at, ack, idem_key,
                   reply { text, signoff (who, when) } }
threads:   name -> { kind, kind_reason, focus, owns[], session_id, status, branch, worktree, title,
                     created, last_seen, last_status_at, summary }
tasks:     id -> { title, owner, deps[], accept ("done when"), status, notes, created,
                   check { by, session, verdict, at } }
questions: [ { from, text, t, answered } ]
```

- `requests.words` is stored before any splitting or summarising (A11); `seen_at` is set only by the user's own
  surface, where the host has such a signal (A9).
- `deliveries` exist only where work leaves the team for another agent or tool (A16).
- `requests.route` is one of `answer`, `owner:<thread>`, `new:<thread>`, `split` (A11).
- `threads.last_status_at` is the time of the last report **from the thread itself**; it drives stuck detection.
  `last_seen` is any ledger touch.
- A port may add fields; it must not drop these or change their meaning.

## A6. Message protocol

### Envelope

```
[tw:<short>] <TYPE> <from> -> <to> <task-id or ->
<body>
```

- `<short>` is the project's short name (A3). A session ignores messages for a project it is not in.
- `<from>` / `<to>` are seat names (`lead`, `ui`, …); `all` for a broadcast.
- The task-id slot holds **exactly one task id from the board** (`T3`), or `-` when no board task applies. No lists,
  no sub-step ids. If two tasks are involved, use the main one and name the other in the body. Request ids go in the
  body, not the header.
- The header is the first line because many surfaces preview only that line.
- The body is **self-contained**: paths, branches, other owners, "done when". Point to files and commits instead of
  pasting large content. **Never put secrets in a message.**

### Types

| Type | From → to | Meaning | Required body | Ledger effect (lead) |
|---|---|---|---|---|
| `TASK` | lead → thread | New work. | Goal, context, done when, constraints, how to report. | task → `dispatched`; thread → `working`; request → `dispatched`. |
| `FOLLOWUP` | lead → thread | More work in an area the thread already owns; keep history. Also used for notices to every open thread (language switch). | What changed; what stays. | as TASK; a notice FOLLOWUP (task id `-`, e.g. a language switch) has no ledger effect. |
| `RECALL` | lead → thread | Stop at a safe point and hand back. | Reason; what to report. If the lead interrupted the thread, the first sentence says so. | task → `recalled`. |
| `ROSTER` | lead → all | Who owns what now. Newest wins. | The roster. | none. |
| `ANSWER` | lead → thread | Reply to a `QUESTION`. | The decision. | question answered; often a memory line. |
| `ACK` | thread → sender (+ lead) | Received a TASK, FOLLOWUP or HANDOFF; starting. One line. | — | task → `working`. |
| `UPDATE` | thread → lead | Real progress or change of plan, only when asked or important. | What changed. | thread summary. |
| `DONE` | thread → lead | The task's "done when" is met. | What changed, where, how verified. | task, thread → `review`; request → `delivered` when all its tasks are `review` or `done`. |
| `BLOCKED` | thread → lead | Cannot continue. | What is needed, from whom. | task → `blocked`; thread → `waiting` if it needs the user. |
| `QUESTION` | thread → lead | A decision only the user or another owner can make, including any consent-gated step. | The question, options, what is prepared. | question recorded; asked to the user. |
| `HANDOFF` | thread → thread (+ lead) | Work that belongs to the receiver's area. | Original request, context, files, findings. | task owner moved; lead forwards if not ACKed. |
| `MEMORY` | thread → lead | A fact the whole project should remember. | The fact. | lead decides; may append to Memory. |

### Forwarding a HANDOFF

The lead has no HANDOFF type of its own. When it forwards one (relay, or an unacknowledged HANDOFF), it sends a
`TASK` if the work is new to the receiver, or a `FOLLOWUP` if it continues the receiver's existing work, quoting the
original HANDOFF in the body.

### Interrupt, then RECALL

On hosts where interrupting a session drops its queued messages, the order is fixed: **interrupt first, then send the
RECALL**. A RECALL sent before the interrupt is lost. If the host shows the interrupt to the thread as a user action,
the RECALL's first sentence says the lead did it, so the thread does not mistake it for the user.

### Language

**Answer in the language you were asked in, and carry it through the team.**

- The project has a **current conversation language** (`prefs.language`): the language the user speaks with the lead.
- Message **bodies** between lead and threads and between peers are written in it. **Header** keywords (`tw`, TYPE,
  `->`, seat names, task ids) are fixed English and never translated.
- When the user switches language, the lead updates `prefs.language` and sends **every open thread its own
  `FOLLOWUP`** saying so, in the new language. New threads receive the current language in their protocol.
- When the user talks to a thread directly in another language, that thread answers the user in that language.
- Status block field names stay English; field values follow the conversation language.
- The rule reaches **all the way down**: when the lead or a thread starts a subagent, the subagent's brief is written
  in the current language and the subagent is told to report in it, so its report reaches the user without translation.
- Files follow the task (repo docs, for example, may be English regardless of the conversation).

Why: the user reads every report the lead relays. A team that drifts into another language forces the lead to
translate, and nuance is lost exactly where decisions are made.

### Ordering and delivery

- Delivery is at-least-once from the sender's view and **queued** at the receiver: a busy session reads a message at
  its next turn (some hosts deliver into a running turn). Senders must not assume immediacy.
- No sequence numbers. Idempotence comes from content: a TASK names its task id; the newest ROSTER wins.
- A refused delivery (trust, permission) is a transport failure (A8, Part D), never retried by working around the
  refusal.

## A7. The per-turn status block

Every thread ends **every** turn with a fixed block. It is the pull transport's payload, the lead's reliable view of a
thread that cannot push, and the recovery record if the session is lost.

```
<tw-status>
thread: <name>
task: <task-id>
state: working | waiting | review | idle
did: <what was done this turn>
result: <branch / commit / PR / file paths>
verified: <how it was checked, or "not verified">
assumed: <assumptions made>
couldnt_reach: <tools, hosts, files that could not be accessed>
waiting_on: <user | thread name | permission <tool> | ->
handed_off: <thread name and what, or ->
next: <next step>
</tw-status>
```

| Field | Why it exists |
|---|---|
| `state` | Drives the seat status (A9). |
| `did` / `result` | Where the work is, without reading the transcript. |
| `verified` | Forces an explicit claim; "not verified" is a valid, honest answer. |
| `assumed` / `couldnt_reach` | Surfaces silent assumptions and environment gaps the user must know about. |
| `waiting_on` | Who unblocks it: `user` and `permission <tool>` go to the user; a thread name goes to that thread. |
| `handed_off` | The pull path for HANDOFFs the thread cannot send itself (A8). |
| `next` | Lets the lead predict, and detect drift. |

`state`: `working` continues on its own; `waiting` needs the user or someone else; `review` the task is done and ready
for the user; `idle` nothing assigned. Field names are fixed English keys; values follow the conversation language.

A host may carry the same fields as a **structured report call** instead of text; the field set and meanings stay the
same, and the text block remains the fallback when the call fails.

Threads that can push still write the block: the lead may read it instead of waiting for a message, and a push can fail.

## A8. Transport: push and pull

| | Push | Pull |
|---|---|---|
| Mechanism | Thread messages the lead; it arrives as a new lead turn. | Thread writes a status block; the lead reads session state and recent output on its own schedule. |
| Latency | Immediate (queued at the lead's next turn). | Until the next sweep. |
| Needs | A thread → lead channel that does not block on a prompt. | A lead → thread read channel and a wake-up source for the lead. |
| Used for | DONE, BLOCKED, QUESTION, HANDOFF copy, MEMORY, UPDATE. | Everything, always: the fallback, and the only path for threads that cannot push. |

### Channel matrix

The host decides which directions exist. The framework requires only that **the lead can reach every thread** (send
and read). Everything else is optional and replaced by relaying through the lead.

```
            lead   ──send/read──► thread      required
            thread ──send───────► lead        optional (push); else pull
            thread ──send───────► peer        optional; else relay via the lead
```

**Trust trade rule: never gain a channel at the cost of the lead's reach.** If letting a thread reach the lead would cut
the lead's line to that thread (as in this binding, B4), the thread stays pull-only.

**Relay.** A thread that cannot reach a peer records the request in its status block (`handed_off`,
`waiting_on: <thread>`); the lead forwards it. The lead also forwards any HANDOFF the receiver never acknowledged.

**Reading output.** If the host filters output *after* reading a page, the lead must read a large page and page back
until it finds the latest status block, and fall back to the host's session summary for a thread still in its first
turn. A small page with a filter often returns nothing.

### Wake-up sources for the lead

The lead acts only when it has a turn. Turns come from: a user message; a pushed thread message; a host event (a child
session failing); a timer the lead set (`checkin_minutes`). While any pull-only thread is open the lead **must** keep a
timer running; nothing else wakes it.

## A9. State machines

Four machines: **request** (what the user asked), **task** (a unit of work with one owner), **thread** (a seat), and,
for teams that hand work to other agents or tools, **delivery** (A16).
They are written only through the ledger interface, with the lead as the only agent writer (A5); the causes come from
the user, the threads and the host.

### Request status

| Status | Meaning |
|---|---|
| `open` | Captured; intake not finished, or answered in place and about to be reported. |
| `proposed` | Intake decided "propose first": the lead put a plan to the user and waits for a yes. |
| `dispatched` | Linked to one or more tasks that have been sent to owners. |
| `delivered` | Every linked task has reached `review` or `done`: the result exists. |
| `reported` | The lead's report (what came back, where, how checked) is in the user's surface. If the host has a "seen" signal that only the user can trigger, record it; an unseen report is re-surfaced at the next sweep. |
| `closed` | The user accepted it. |
| `dropped` | The user said not to do it. |

```mermaid
stateDiagram-v2
    [*] --> open: lead captures user message
    open --> proposed: intake says propose first
    proposed --> dispatched: user says yes
    proposed --> dropped: user says no
    open --> dispatched: intake says do it, TASK sent
    open --> reported: answered in place
    open --> delivered: work done directly in a thread, registered after
    open --> closed: split parent, all children ended
    dispatched --> delivered: all linked tasks review/done
    delivered --> reported: lead reports to the user
    reported --> closed: user accepts
    reported --> dispatched: user asks for changes
    delivered --> dispatched: check failed, sent back
    open --> dropped: user says don't
    dispatched --> dropped: user says don't
    delivered --> dropped: user says don't
    reported --> dropped: user says don't
```

- **Only the user ends a request**: the person who raised it; `closed` by accepting, `dropped` by explicitly saying
  not to do it. The lead never closes one on its own, and no thread can.
- A **split parent** is not worked on itself. Its end is derived from its children, each of which the user ends:
  `closed` when every child has ended and at least one is `closed`, `dropped` when every child is `dropped`.
- A request a **thread raises** (A16) is owned by the user, or by the person the lead names; the thread cannot end it.
- An intake decision of "drop" is a recommendation: the lead reports why (`reported`), and the user ends it.
- A request answered in place goes straight to `reported`; it still needs the user's close, which the lead may collect
  in batches ("R4, R5 answered: close?").
- `closed` is recorded only once the accepted result has actually landed (merged, or delivery `done`). If the merge or
  delivery fails after the user said yes, the request goes back to `dispatched` and the user is told; it is never
  closed on a result that did not land.
- A follow-up from the user on a reported request reopens it (`dispatched`) rather than creating a new one, when it is
  clearly the same ask.

### Thread (seat) status

| Status | Board group | Meaning |
|---|---|---|
| `planned` | Suggested | Seat exists, no session yet. |
| `starting` | — | Session created, first turn not finished. |
| `working` | Working | Running or will continue on its own. |
| `waiting` | Waiting on you | Needs the user: a question, a permission prompt, or a failure. |
| `review` | Ready for review | A task is done and waits for the user. |
| `landing` | Landing | Approved; being merged. |
| `idle` | Idle | Finished its turn, nothing pending. |
| `resolved` | Resolved | Work merged, nothing left. |
| `archived` | (hidden) | Session archived; seat kept as record. |

A turn ending is not a task finishing: `idle` means the thread stopped and has nothing pending, never "done". Only
`state: review`, `DONE`, or (for a delivery) the target accepting it moves a task to `review`.

**Possibly stuck** is a board group, not a status: a thread in `working` or `starting` whose `last_status_at` is older
than `stuck_minutes`. It is derived at render time and disappears as soon as the thread reports again. See the sweep
(A10) for what the lead does about it.

```mermaid
stateDiagram-v2
    [*] --> planned: lead adds seat (plan)
    [*] --> idle: lead adopts an existing session
    planned --> starting: lead creates session
    starting --> working: health check passed, TASK sent
    starting --> starting: health check failed, fresh session
    starting --> waiting: failed / needs user
    working --> waiting: needs user / permission / failed
    waiting --> working: user answered or approved
    waiting --> review: state review / DONE
    idle --> review: state review / DONE
    working --> review: state review / DONE
    working --> idle: state idle
    review --> working: FOLLOWUP (changes requested)
    review --> landing: user approves, lead merges
    review --> resolved: delivery done, user accepts
    working --> idle: RECALL confirmed
    landing --> resolved: merged, nothing left
    landing --> working: merge conflict sent back
    idle --> working: TASK / FOLLOWUP
    resolved --> working: new FOLLOWUP in its area
    planned --> archived: close
    idle --> archived: close (user OK)
    resolved --> archived: close (user OK)
    waiting --> archived: close (user OK)
    review --> archived: close (user OK)
```

| Transition | Cause | Recorded by |
|---|---|---|
| → `planned` | Plan step. | lead |
| → `starting` | Session created; health check running (A2). | lead |
| `starting` → `working` | Health check passed and the TASK sent. A failed check replaces the session and stays `starting`. | lead |
| `working` → `waiting` | Host shows a pending action or failed turn; `waiting_on: user` or `permission`. | lead, from host state / status block |
| `waiting` → `working` | User answered (lead sends ANSWER) or approved the prompt in the thread. | lead, next sweep |
| `working` → `review` | `state: review` or `DONE`. | lead |
| `review` → `landing` | User approves the result. | lead |
| `landing` → `resolved` | Merged, checks pass, nothing left. | lead |
| `landing` → `working` | Merge conflict or failed checks sent back to the owner. | lead |
| `review` → `resolved` | Coordinating team: the delivery is `done` and the user accepts (no merge, no `landing`). | lead |
| `working` / `waiting` / `review` → `idle` | The thread confirms a RECALL: WIP committed, report sent, nothing pending. | lead |
| any open status → the mapped status | The mapping below is authoritative: at each sweep an open seat (`starting`, `working`, `waiting`, `idle`, `review`) takes the status its observation maps to. The diagram shows the usual path, not every edge. | lead |
| `review` / `idle` / `resolved` → `working` | New TASK / FOLLOWUP. | lead |
| → `archived` | Project close or user request, with the user's OK. A thread mid-work (`working`, `waiting`, `review`, `landing`) is RECALLed first; `starting` is cancelled. | lead |

Mapping a thread's report to its seat status, at every sweep, for any open seat (`starting`, `working`, `waiting`, `idle`, `review`). When several rows match, the first wins:

| Observation | Seat status |
|---|---|
| Host shows a pending action, or the turn failed | `waiting` |
| Host shows a transient disconnect it is retrying on its own | stays `working`; `waiting` only if the host gives up (failed) |
| Host suspended the session and will resume it on its own | stays as it was; not a terminal state |
| `waiting_on: user` or `permission <tool>` | `waiting` |
| `waiting_on: <thread>` (blocked on a peer) | stays `working`; the summary names the peer. Not "waiting on you": the user cannot unblock it. |
| `state: waiting` with `waiting_on` user, permission or `-` | `waiting` |
| `state: review` | `review` |
| `state: working` | `working` |
| `state: idle`, nothing assigned | `idle` |

### Task status

| Status | Meaning |
|---|---|
| `planned` | In a draft plan. |
| `approved` | User approved; may be dispatched when deps are done. |
| `dispatched` | TASK sent; no sign of work yet. |
| `working` | Owner acknowledged or reported progress. |
| `blocked` | Owner cannot continue. |
| `review` | Owner reports "done when" met; waits for the user. |
| `done` | Accepted (and merged, for code). Satisfies dependencies. |
| `recalled` | Stopped by RECALL; work handed back. |
| `dropped` | Abandoned by the user's decision. |

```mermaid
stateDiagram-v2
    [*] --> planned: added before plan approval
    [*] --> approved: added after plan approval
    planned --> approved: user approves plan
    approved --> dispatched: deps done, slot free, TASK sent
    dispatched --> working: ACK / status working
    working --> blocked: BLOCKED / waiting
    blocked --> working: ANSWER / unblocked
    working --> review: DONE, state review, or delivery accepted
    review --> working: FOLLOWUP
    review --> done: user accepts, merged
    review --> recalled: lead recalls
    review --> dropped: user drops
    review --> blocked: delivery rejected or failed, needs the user
    working --> dropped: requester gives up
    blocked --> dropped: requester gives up
    dispatched --> recalled: RECALL
    working --> recalled: RECALL
    blocked --> recalled: RECALL
    recalled --> dispatched: reassigned or re-sent
    planned --> dropped
    approved --> dropped
    recalled --> dropped
```

- **Independent check.** Between the owner's `DONE` and the user's acceptance, the result is checked against its
  "done when" by a checker **without the author's context**: a fresh session, a subagent given only the task, the
  result and the acceptance criteria, or the user. The checker's session is never the author's. The author's own
  self-check ("tests pass", "precheck ok") is reported in `verified` but never counts as the independent check. The
  check is recorded on the task (`check`); a failed check sends the task back to `working` with the findings. Tiered
  like intake (A11): required for heavy-tier work, anything irreversible and every delivery (A16); optional for light
  tasks.
- **Ready** = `approved` and every dependency `done` (not `review`): dependents build on what has landed.
- **Plan reopen** returns the plan to draft; existing tasks keep their status, new ones are `planned` until the next
  approval.

If the host keeps several statuses for one unit of work (a host process status, a job status, the thread's own
report), choose **one** as the source of the seat status and derive it from that; never show two. The rule in A2
applies: the thread's own report decides; host statuses only signal failure, disconnection or a pending prompt.

### Delivery status

Used only when work leaves the team for another agent or tool (A16).

| Status | Meaning |
|---|---|
| `prepared` | Package drafted by the owner thread. |
| `signed_off` | The named approver signed off this exact version; the package is frozen. |
| `sent` | Handed to the target under its execution policy, with an idempotency key. |
| `accepted` | Target acknowledged: executing (`direct`), waiting for its owner (`confirm`) or queued (`queue`). |
| `done` | The receiver's reply (what was done, what was not) came back, signed off by the receiver. |
| `rejected` | Target or its owner declined. |
| `failed` | Sending failed (retry with the same key) or execution failed (new round). |

```mermaid
stateDiagram-v2
    [*] --> prepared: owner thread drafts package
    prepared --> signed_off: approver signs off version v
    signed_off --> prepared: package changed (sign-off void)
    signed_off --> sent: lead sends under target policy
    sent --> accepted: target acknowledges
    sent --> failed: not delivered
    failed --> sent: send retry, same key
    accepted --> done: receiver's signed-off reply returned
    accepted --> rejected: target owner declines
    sent --> rejected: target declines on arrival
    accepted --> failed: execution failed
    failed --> prepared: after execution failure, new round
    rejected --> prepared: revised, new round
    rejected --> [*]: requester drops
    failed --> [*]: requester gives up
    done --> prepared: requester reopens, new round
```

A delivered task is `review` once its delivery is `accepted`, and `done` when the delivery is `done` and the user
accepts the result. A reopen, a revision after `rejected`, or a retry after an execution failure starts a new
**round** (`round` + 1) with a new version and a new idempotency key; only a send that never arrived is retried with
the same key. `rejected` or `failed` sends the task from `review` back to `working` (or `blocked`, if it needs the
user); if the requester gives up, the task and its request are `dropped`.

### How the machines connect

| Event | Request | Task | Thread |
|---|---|---|---|
| User asks | `open` | — | — |
| Intake: propose first | `proposed` | — | — |
| Lead routes and sends | `dispatched` | `dispatched` | `working` |
| Thread reports DONE | `delivered` (when all its tasks are) | `review` | `review` |
| Lead tells the user | `reported` | — | — |
| User accepts, lead merges | `closed` | `done` | `landing` → `resolved` |
| User says don't | `dropped` | `dropped` (or `recalled` first) | `idle` once the RECALL is confirmed |
| Coordinating team: package signed off and sent | `dispatched` | `working` while delivery `sent` | `working` |
| Coordinating team: delivery `accepted` | `delivered` | `review` | `review` |
| Delivery `done`, user accepts | `closed` | `done` | `resolved` |

## A10. Lifecycle and the sweep

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant L as Lead
    participant G as Ledger
    participant T as Thread
    participant C as Checker
    U->>L: new project, goal
    L->>G: init, record lead + environments
    L->>U: suggested threads + tasks
    U->>L: go-ahead (or edits)
    L->>G: plan approve
    loop every ready task
        L->>G: create the thread's workspace
        L->>T: create session (protocol injected), health check, then TASK
        L->>G: thread working, task dispatched
        L-->>T: ROSTER to other active threads
    end
    U->>L: a request
    L->>G: register request Rn
    L->>T: FOLLOWUP to the owner
    L->>U: "Rn → owner (Tk)"
    alt thread can push
        T->>L: DONE / BLOCKED / QUESTION
    else pull only
        L->>T: (timer) read latest status block
    end
    L->>G: sweep: statuses, questions, memory, requests
    L->>C: independent check of the result (fresh session)
    C-->>L: verdict
    L->>U: one status reply: checked results, all questions, requests to close
    U->>L: answers, accepts Rn
    L->>L: merge, run checks
    L->>G: task done, thread resolved, Rn closed
    U->>L: close project
    L->>G: threads archived (with OK), ledger kept as record
```

| Phase | Gate to leave | Who |
|---|---|---|
| Init | Ledger exists, lead recorded. | lead |
| Plan | Each task has one owner and a checkable "done when"; with `propose_before_start`, the user's go-ahead. | user |
| Dispatch | Ready task, free slot, capacity left, workspace created, new session passed its health check. | lead |
| Work | Thread reports `review`. | thread |
| Check | Independent check passed (A9), where the tier requires one. | checker |
| Report | Lead has told the user. | lead |
| Accept | User accepts or drops. | user |
| Integrate | Merged, checks pass. | lead |
| Close | User says the project is done; archiving only with OK. | user |

### The sweep

A sweep refreshes the ledger from the threads. It runs when a timer fires, a thread pushes, a host event arrives, and
before any status answer to the user.

1. List open threads (not `planned`, `resolved`, `archived`).
2. For each, read host-level state (pending action, failure, connection). If needed, read recent output and parse the
   latest status block (page back if the host filters after reading; see A8). Never read a whole transcript.
3. Map to seat and task status (A9) with a one-line summary. Record whether the update came from the thread itself
   (it resets the stuck clock).
4. Apply `handed_off` / HANDOFF: move the task owner; forward if not ACKed. Any task still `dispatched` with no ACK
   past one check-in: resend once, then tell the user.
5. `MEMORY` → decide; record if durable.
6. `QUESTION` → record; batch into one user message; on answer, `ANSWER` and mark answered.
7. **Possibly stuck** threads (A9):
   - host says failed, or disconnected and no longer retrying → `waiting`, tell the user; a transient disconnect the
     host is still retrying stays `working` (A9);
   - still running → look for a newer status block; if none, a FOLLOWUP asking for one now;
   - silent for more than **twice** `stuck_minutes` → tell the user which thread is silent and since when, and offer to
     RECALL or reassign. **The lead does not recall on its own.**
8. **Requests:** advance each request from its tasks (`dispatched` → `delivered`). List
   - **delivered, not reported** → report them in this reply, once their independent check (A9) has passed;
   - **reported, not closed** → ask the user, once in every status reply, whether to close each.
   - **proposed** → still waiting for the user's yes: list them in the same reply.
   - **deliveries** (coordinating teams): `sent` without `ack` past one check-in → check the target, retry with the same
     key or tell the user; `accepted` without `done` past the expected time → ask the target or tell the user.
9. Dispatch newly ready tasks; set the next timer.

Status reply shape (newest information only):

```
Waiting on you: <thread — what it needs, how to unblock>
Ready for review: <Rn / thread — result, where>
Working: <thread — current step>
Possibly stuck: <thread — no report for N min, what the lead did>
Idle / resolved: <names>
Requests: <delivered, now reported: Rn …> <reported, close?: Rm …> <proposed, waiting for your yes: Rk …>
Deliveries: <sent without ack / accepted without done: id — what the lead did>
Next: <what the lead does next, and when it checks again>
```

## A11. Intake and routing

Every user message to the lead is **captured, taken in, then routed**, and the lead's reply names the request id and
the route.

### Capture

Store the user's words verbatim as request Rn (or attach them to an open request they clearly continue). Capture is
never skipped and never waits for intake: whatever happens next, the words are in the ledger.

### Intake: see the intent before acting

Intake turns words into a request someone can finish. It is the big-picture method applied to one request:

| Step | Output |
|---|---|
| **Intent** | The original intent behind the literal ask (usually bigger), in one sentence. |
| **Success** | What counts as solved, testable where possible, in one sentence. |
| **Widen** | Kin (the same need elsewhere), impact (who and what it touches), options (two or three routes), failure modes. |
| **Converge** | One decision: **do it** (within intent, in scope, reversible) · **propose first** (touches others' work, money, publishing, an outside party, or the intent is unclear) · **drop** (unrelated, or costs more than it is worth; say why). |

Intake has **two tiers**, so not every sentence pays for the full method:

| Tier | When | What the lead does |
|---|---|---|
| **light** | A lookup, status question, confirmation, small talk, a one-field change; a scope the user fixed ("only this"); or examples given with a clear direction ("like these, pick a few more"). | Writes intent + success as one internal line, then acts. Nothing shown to the user beyond the answer. |
| **heavy** | Any trigger: the ask is a deliverable, several steps, a design, plan, research, migration, release or anything irreversible; it hands over judgment ("you decide", "what else", "is there a better way"); it leaves the list open ("etc.") without a clear direction; or the user is unsure. | Runs all four steps, shows intent, success and decision in the reply, then routes. |

**When in doubt, heavy.** Thinking too much on a simple ask costs a few lines; thinking too little on a real one costs
the work. The default tier for ambiguous asks is a project preference (`intake_default`, default `heavy`); triggers
always mean heavy.

The decision feeds the request machine (A9): do it → `dispatched`; propose first → `proposed`; drop → `reported` with
the reason, for the user to end. A thread receiving a TASK may run its own light intake on it, but it never re-decides
the user's request: disagreement goes back as a QUESTION.

### Routing

```
captured Rn, intake done
   │
   ├─ about the project itself (status, plan, a question)? ──► ANSWER IN PLACE   (Rn → reported)
   ├─ several unrelated asks?                               ──► SPLIT: one child request each, route each;
   │                                                            the parent keeps the words; its end is derived (A9)
   ├─ falls in an area a thread owns?                       ──► EXISTING OWNER: FOLLOWUP/TASK
   │                                                            (even if busy or waiting: it queues; say so)
   └─ new area nobody owns?                                 ──► NEW THREAD: propose (plan rules), then dispatch
```

- The verbatim words are kept before intake, splitting or summarising: splitting is itself a model step that can drop part of
  an ask, and the original is what the user's close is checked against.
- Prefer the existing owner over a new thread: the owner has the history.
- A keyword hint (thread names, focus, `owns`, task titles) may narrow candidates; the lead confirms against `owns` and
  the thread's summary. The hint never decides alone.
- Threads apply the same rule from their side: in-area → do it; out-of-area → HANDOFF; unowned → QUESTION.
- Messages typed by the user directly in a thread outrank lead and peer messages there. The thread still writes its
  status block, so the lead learns of the work at the next sweep and registers it as a request, already `delivered` if
  the work is done.

## A12. The consent boundary

**Only the user can approve, and only through the lead's chat** (or a host permission prompt inside the thread itself).

Consent-gated steps (minimum list): publishing; creating repositories; pushing to a shared branch or remote beyond what
was pre-authorised; opening pull requests; deleting or archiving; force operations; changing permission settings;
sending anything outside the project. Closing or dropping a request is also the user's (A9).

Why agent messages are never consent:

1. **Provenance.** A thread cannot verify that a lead or peer message reflects the user's intent.
2. **Injection.** Thread content (messages, transcripts, fetched pages) can carry instructions from untrusted material.
   If agent messages could grant consent, so could any injected text one hop away.
3. **Accountability.** The user sees every approval in one place.

```
thread: prepare up to the gate (commit on own branch, draft, dry run)
   └─► QUESTION (push) or waiting_on: user (pull): the exact step
lead:   ask the user in its chat, all pending questions at once
user:   yes / no
lead:   executes the step itself
```

Three rules make the boundary hold in practice:

- **Consent travels on a channel agents cannot write.** An approval is recorded only from the user's own action in the
  lead's surface (or the host prompt). A message, a tool call or an API call made by an agent is never accepted as one.
- **An approval is bound to its parameters.** What was approved (the command, the target, the branch) is frozen with the
  approval; if the parameters change, it must be asked again.
- **A timeout never grants consent.** A host may let an agent proceed on its own judgment when the user does not answer
  an ordinary question (recording the guess in `assumed`), but a consent-gated step stays blocked until the user answers.

**Delivery to another agent or tool** (A16) is itself consent-gated, in two places:

| Decision | Who makes it | Recorded as |
|---|---|---|
| The target's **execution policy** (`direct`, `confirm`, `queue`) | The owner of the target (the tool, the machine, the receiving team) sets it for their endpoint. The sender may tighten it for one delivery, never loosen it. | `policy`, `policy_set_by` on the delivery |
| **Sending** this package | The sender's user (or the approver they named) signs off the exact version; that sign-off is the consent to send it, bound to that version. | `signoff` on the delivery; the package frozen |
| **Executing** it at the target | `direct`: the target owner's standing consent, within its scope. `confirm`: the target owner confirms this delivery. `queue`: nobody yet; it waits. | the target's `ack` |

A changed package voids its sign-off, **whoever changes it**: no role, including the sender's own administrators, can
edit a signed-off package and keep the sign-off. A sign-off records the package version (or a hash of its content), not
just the approver's name.

**Layers of policy** combine to the **strictest**: a global default, the sender side, the receiver side, and any
machine-level policy. An agent may only tighten a layer. The one exception is an explicit **override** by someone
entitled to it, which must carry an expiry and is recorded. A person with more authority does not edit a less
privileged person's policy: they send a change request, and that person confirms it.

A policy is only real if it maps to a real control in the target: "plan first" must run the target in a plan-only or
read-only mode; if the target has no such mode, the policy is raised to "confirm".

Both ends may have a policy: the **sender** side (how its outgoing work is handled, for example "auto", "review the plan",
"ask") and the **receiver** side (whose work may run here, and how). The **stricter of the two** applies, together with
any machine-level policy. A delivery never executes under a looser policy than the target owner set.

**Standing authorization** is consent given in advance as a policy ("run deliveries from project A directly", "allow
edits inside this folder"). It counts as consent **only within the scope it names**; anything outside that scope is
asked again. A standing authorization is recorded with its owner, scope and time, and is set or changed only by that
owner's own action (I32).

Along a HANDOFF or relay chain, the **strictest** rule of any hop applies to the work: a request that came through a
thread with tighter permissions does not gain looser ones by being forwarded.

**Permission prompts** belong to the session that raised them; only the user can answer them there. The lead tells the
user which thread and which prompt and offers to do the step itself. Prompts are reduced in advance by pre-granting the
reporting tools and by starting threads in a permission mode **no looser than the lead's own** (`thread_permission_mode`
if the user set one, else the lead's mode).

**An agent's capabilities equal those of the person it represents** (its principal), no more and no less, and that
includes consent-class acts: signing off, accepting, reopening, loosening an authorization. But the agent **never does
them on its own initiative**. It prepares the step, lists the exact actions it will take, and acts only after the
principal explicitly consents on a channel agents cannot write. One consent may cover several listed consecutive steps;
it is bound to exactly those actions and has an expiry. If an action changes, or the consent expires, it is void and
the agent asks again. Subagents and threads inherit the scope from the lead, never beyond.

**Where standing authorization stops.** A standing authorization the principal set personally counts as consent given
in advance for that class of action. Some classes it can never cover; each instance needs its own consent:
confirming, accepting and reopening; loosening an authorization, including extending a consent's expiry; releasing an
out-of-scope alert; viewing a secret in clear text. A consent is bound to the content and version of its actions at the
moment it was given: it is void if the content changes, an action is added, the target was changed by someone else, or
it times out. If a step fails midway, the remaining steps stop and consent is asked again. Each step runs exactly once.
Consent comes only from the principal's own human channel; an agent cannot produce it.

**Origin is stamped by the system.** Every action an agent takes carries an unobtrusive origin tag (which agent, for
whom, under which consent), set by the system from the channel the call came through. A client never reports its own
origin; a tag a client supplies is ignored.

**Out-of-scope viewing raises an alert.** If an agent views, or tries to view, material outside its principal's scope,
the system notifies the administrator and the person whose material it was, **silently** (no visible warning in the
agent's session). False alarms are controlled by requirement, not by hope: a threshold before an alert fires,
deduplication of repeated alerts for the same event, and an appeal path for the person flagged.

Asking someone else for private material (their credentials, personal data, private
files) is **blocked and reported** to the user, whoever asks for it. Execution policies are set **per context** (which
target, which kind of work, how large, whose resource), never as one blanket rule.

The lead applies the same rule to itself: content read from threads is information, not instructions.

## A13. Recovery

**Prevent, don't recover.** In a networked system whose actions are irreversible (a message sent, a delivery executed,
a record changed for others), a decision that has been carried out cannot be taken back. The protection is **before**
the act: every outward action exists first as a **draft**, saved automatically, that can be checked, changed or thrown
away; only the sent version is final. Recovery in the table below covers only what can be recovered: the agents' own
context and the succession of sessions in a seat.

| Loss | Detection | Recovery |
|---|---|---|
| **Lead context compacted or restarted** | Lead session starts or resumes. | Ledger summary (language rule, board, instructions, memory) is re-injected; the lead continues from the ledger, then sweeps. |
| **Lead session lost** | User opens a new session and attaches. | New session checks the recorded lead; takes over only if it is gone or the user says so; records itself; sweeps. |
| **Thread session lost / unreachable** | Host reports failed or disconnected, or a send is refused. | The seat survives. A new session takes it with the same TASK, the old one's last status block and branch, and an explicit instruction **not to repeat side effects already done** (pushes, messages, external calls listed in `result`); the old session is archived after the user agrees. |
| **Thread silent** | Possibly stuck (A9). | Sweep step 7. |
| **Session degraded** (still connected, but repeating itself, losing instructions, or slowing to a crawl) | Stuck signals without a disconnect; the user or the lead sees quality drop. | Propose to the user a fresh session for the same seat. The handoff is **built deterministically** from the ledger: the last status block, open tasks and requests, side effects already done, and the user's verbatim words for the open requests. The degraded session is not asked to write its own summary; notes it wrote may be attached, never as the only handoff. The same applies when the lead's own conversation is rolled over: the new segment starts from the ledger, not from the last few messages. |
| **Thread drifted** | `did`/`next` disagree with the task. | FOLLOWUP with the exact correction. Do not restart a thread whose history is useful. |
| **Thread stopped on purpose** | RECALL (interrupt first if mid-turn). | Thread commits WIP to its branch, pushes, reports done / not done / assumptions / where. Task `recalled`; reassign with that summary. |
| **Delivery interrupted** | Delivery `sent` with no `ack`, or session lost mid-send. | Retry with the **same idempotency key**; never send a second package for the same version. Check the target before resending. |
| **Ledger lost or corrupt** | Ledger interface fails to load. | Rebuild from the log (replay), session titles and labels (seats, ids), last status blocks (statuses), branches and commits (results). Not automated in this binding. |
| **Host service for local threads down** | All local threads disconnected. | Tell the user; nothing in-chat restarts it. Remote threads keep running. |

Recovery rests on three properties: the ledger is outside every agent (A5); every thread's last status block describes
itself (A7); every thread's work is on its own branch (A15).

## A14. Capacity and parallelism

- **Default: no limit.** Start every thread the approved plan needs.
- `max_parallel` caps threads in `starting`/`working`; set only when the user asks.
- **Host capacity** is shared across projects. Before creating threads the lead counts live sessions of that kind; near
  the limit it tells the user which idle or resolved threads could be archived (the user chooses) or uses a kind that
  does not count. It never archives to make room.
- **Cost scales with threads.** Area-based splitting keeps the count proportional to areas, not tasks.
- **Who does the work.** The lead does no domain work through subagents: work goes to a seat, new or existing. A seat
  (a separate session) may use as many subagents as it likes; they report to the seat.
- **No nesting.** Threads do not create sessions. A thread that needs a new seat asks the lead; the lead creates it,
  records it in the ledger and broadcasts the roster.
- **The reporting chain is fixed:** subagent → seat → lead → user.
  If a host lets a worker delegate to separate sessions anyway, that is a declared deviation, and each delegate reports
  to its delegator only: it never uses the delegator's task id or report channel, so it cannot deliver on the
  delegator's behalf.
- Parallel work is safe because areas do not overlap (A4) and each thread has its own workspace (A15).

## A15. Integration of results

Every team closes work the same way: **a deliverable plus a sign-off**. How that is implemented depends on the team type
(A16): a producing team merges a branch after the user accepts it; a coordinating team sends a delivery package that
both sides sign off. The rest of this section is the producing implementation.

- Every thread works in its own workspace on its own branch `tw/<full project name>/<thread>`, from the project's base
  branch. The **lead creates the workspace before starting the thread**, so the thread never guesses a path, base or
  branch name. Branches and workspace folders use the full name, never the short name.
- Results reach the lead through the branch (and named files for non-code work), never pasted.
- The lead merges only after the independent check (A9) and the user's acceptance, runs the project's checks, and
  reports.
- Conflicts: the lead resolves trivial ones; otherwise FOLLOWUP to the owner of the conflicting area. After a merge,
  FOLLOWUP to threads touching the same files to rebase.
- `done` is set after merge, so dependencies build on landed work.

## A16. Team types: producing and coordinating

The model so far reads like a team that produces code. It also fits a team whose work is information: meetings, plans,
requirements, schedules, and handing finished requirements to other agents or tools. Same roles, ledger, messages,
status block and consent; the concepts below change shape.

In a coordinating team the lead's main job is the same as in a producing team: **keeping the whole picture**: its own
ledger, board and lists, and the full mechanism for working with its threads (seats, messages, status blocks,
sweeps). Most outward execution (around nine tenths) is done by threads; the lead communicates, decides and judges
(A14: never through its own subagents). Threads carry out work that needs no consent themselves; for a consent-gated
step they prepare it, and it runs only after the user's explicit consent (A12). To everyone outside, **the lead and its threads are one identity**: they act for the same person, with that person's
permissions (A12), and speak with one voice.

| Concept | Producing team | Coordinating team |
|---|---|---|
| **Output** | Code, files, documents in a repo. | Decisions, records, plans, requirements, schedules; work handed to other agents or tools. |
| **Ownership area** | Paths and modules (`src/ui/`). | Projects, streams, stakeholders, meeting series, target tools ("project A", "vendor B", "planning"). |
| **Task** | A change with a "done when". | An information unit: run a meeting and its notes, turn a vision into a requirements doc, break it into trackable items, schedule, prepare a delivery. |
| **Done when** | Checks pass; merged. | The named approver signed off the version **and**, for a delivery, the target acknowledged it (and later reported done). |
| **Review** | Independent check (A9), then the user accepts the result. | Independent check of the package or result against its acceptance criteria (A9), then **sign-off** by a named person on a frozen version. Sign-off is consent (A12): it cannot come from an agent. |
| **Merge** | Merge the branch into base. | **Delivery package**, signed off on both sides: the requester signs off what is sent; the receiver signs off the reply; the requester then accepts or reopens. Named acceptance participants can block a sign-off. Sent once, with an idempotency key. |
| **Workspace** | Branch + worktree per thread (H9 required). | A draft space per thread (its own documents or notes folder); shared records (the project's plan, its meeting log) have exactly one owner thread, others propose changes by HANDOFF. H9 is optional when outputs are records rather than files. |
| **Status block** | `result` = branch, commit, PR. | `result` = document and version, meeting record, delivery id and its status; `waiting_on` often names the approver. |
| **Recovery** | Re-seat from branch and last status block. | Re-seat from document versions and the delivery log; never send a second package for the same version and round; only a send that never arrived is retried, with the same key (A13). |
| **Integration risk** | Merge conflicts. | Two versions of the truth (a plan edited in two places), duplicate deliveries, deliveries of unsigned drafts. |

A team may be both: a producing team whose results are delivered to another team's tools uses both columns.

### What a delivery package holds

A package is self-contained, like a TASK (A6), because the receiver has none of the sender's context: **goal**,
**acceptance criteria**, **in scope / out of scope**, **boundaries** (what must not be touched), **who it waits on**,
**context** (links to the records it came from), plus version, target and execution policy.

### Work derived from records

Coordinating teams produce work as a by-product: action items in meeting notes, decisions that need follow-up, a
schedule slipping. These become requests or tasks in the same ledger, with a pointer to the record they came from. They
are **extracted from the records**, not recalled by a model from memory, so a missed line can be found by re-reading the
record. A derived request's owner is whoever the record names (or the user); it still ends only by that owner (I25).
An extracted item enters the ledger **as soon as it is extracted**, as an open request, not only once someone accepts
it; accepting or declining it is the owner's action on that request. Otherwise unaccepted items are followed by no one.

### Roles in a multi-person team

Three decisions that one person makes in a solo project are separate in a team, and the ledger records each:

| Decision | Made by |
|---|---|
| Ending a request (`closed` / `dropped`) | The person who raised it (A9). |
| Signing off a package or result | The person who raised that side's content: the requester signs off what is sent; the receiver signs off the reply. Optional **acceptance participants** may be named; if any of them objects, it cannot be signed off. |
| Letting work execute on a machine or tool | That resource's owner, by its execution policy (A12). |
| Creating tasks | The lead (in a coordinating team, the project's manager). Threads (the executors) never create tasks; they raise requests, which the lead takes in like any other (A11); such a request is owned by the user or the person the lead names. |

This is the default for a coordinating team; a host may make the table configurable, but not loosen I25.

### Closing the loop

Once delivered, the original is **frozen**: changes are appended as revisions, the old version marked superseded, and
nobody edits another person's comments.

The receiver answers **inside the same task**: it drafts a reply (what was done, what was not, what it needs). As soon
as the reply draft starts, the original package sent to it is **frozen** (its hash recorded); the receiver can edit only
its reply. In general, **a record that another record cites is immutable**: a change is a new version appended and the
old one marked superseded, never an edit in place. When the
reply is sent, **everyone involved** is notified (the requester, the acceptance participants, the threads that worked
on it). The requester then accepts or reopens.

### Delivery channels

A **delivery channel** is any way work leaves the team for another agent or tool: an agent-to-agent message, a tool
API, a queue. Rules:

1. Only a `signed_off` package is sent; the content is frozen at sign-off.
2. The target's owner sets its execution policy; the sender may only tighten it (A12).
3. Every send carries an idempotency key derived from the package version **and the round** (a reopen starts a new
   round); a retry reuses it. Deduplicating inputs by time window is not a substitute: it does not stop a second send of
   the same version.
4. The target's acknowledgement is recorded on the delivery; without it, the delivery is not `accepted`.
5. The sweep (A10) lists deliveries `sent` without `ack` past one check-in and `accepted` without `done` past the
   expected time.

## A17. What code must enforce

Rules split into two groups. A rule belongs in **code** (the ledger interface, the host, the delivery channel) when
breaking it is irreversible, lets one agent act with another's authority, or can be triggered by injected text. A rule
may stay in **instructions** (the playbook, the worker protocol) when breaking it costs quality or time but can be seen
and corrected at the next sweep.

| Must be code | Why | Invariants |
|---|---|---|
| One ledger interface; preconditions on `closed` / `dropped` / `done`; interface-only fields | Agents can be wrong or injected; a closed request or done task is what the user relies on. | I2, I3, I28 |
| Requests end only on the owner's own action | The point of the request ledger. | I25 |
| Consent recorded only from a user action, bound to parameters, never by timeout | Otherwise any agent message could approve. | I11, I27 |
| Delivery: only signed-off versions; target policy never loosened; idempotent sends | Deliveries leave the team and cannot be pulled back. | I29, I30 |
| Standing authorizations: scope checked, owner-only changes | A stretched policy is silent escalation. | I32 |
| Origin tags set by the system; silent out-of-scope alerts | A client that reports its own origin can impersonate anyone. | I52, I53 |
| Reserved acts outside standing authorization; consent bound to content and version, stop on failure, run once | A stale or stretched consent is silent escalation. | I54 |
| Checker session differs from author session | Cheap to compare; the whole value of the check depends on it. | I34 |
| Permissions equal to the represented person; private-material requests blocked and reported | Escalation and data leaks cannot be undone. | I38 |
| Draft before every outward action; original frozen once the reply starts | Sent is final. | I36, I39 |
| One lead per project; readiness = approved + deps done | Cheap to check, expensive to get wrong. | I1, I17 |
| Policy layers combine to the strictest; overrides expire; policies map to real target controls | A paper policy or a forgotten override is silent escalation. | I41 |
| Messages anchored to an object, where the host routes messages | An unanchored message cannot be found or derived into the ledger. | I37 |
| A thread's permission mode no looser than the lead's | A widened mode is an escalation. | I26 |
| Workspace created by the lead | Guessing paths is how threads collide. | I16 |

| May stay in instructions | Why | Invariants |
|---|---|---|
| Intake tiers and decisions; routing to the owner | Judgment; a wrong call shows in the reply and is corrected. | I31 |
| Ownership areas not overlapping | Hard to compute for topics; enforce in code where areas are paths. | I5 |
| Message header format, one task id | Parseable; validate in code when cheap. | I8 |
| Status block every turn and honest `verified` | Content quality; the sweep notices absence (stuck). | I9 |
| Language rule, short names | Presentation. | I23, I24 |
| Extracting derived work from records | Judgment on what is an action item; the source pointer makes misses findable. | I33 |
| Board before telling the user | Ordering discipline. | I4 |

---

# Part B. Claude Code binding

## B1. Concept → implementation

| Generic concept | Claude Code implementation |
|---|---|
| Host | Claude Code sessions on claude.ai, driven through the `claude-code-remote` MCP tools. |
| User's single place | The lead's chat in claude.ai (web or app). No terminal, ever. |
| Lead | A Remote Control (rc) session running the `/teamwork` skill (`SKILL.md`), `environment_kind: bridge`. |
| Thread kinds | `rc`: Remote Control session on the user's machine, in the served folder. `cloud`: cloud container session. |
| Seat | Entry in `state.json` `threads`; current session in `session_id`. |
| Ledger interface | `scripts/tw.py` (stdlib Python), always called as `tw.py -C <project folder>`. |
| Ledger store | `<project>/.teamwork/`: `state.json`, `BOARD.md`, `INSTRUCTIONS.md`, `MEMORY.md`, `log.jsonl`. |
| Requests | Specified here (A5, A9, A11); implementation in progress in core (task T14). This section will be re-checked when it lands. |
| Intake | Instructions only (the lead playbook); the big-picture method is the reference for the heavy tier. |
| Deliveries | Not used: this binding is a producing team whose results are merged, not delivered. |
| Protocol injection | `tw.py worker-prompt <thread>` fills `templates/worker-protocol.md`; passed as `append_system_prompt`. |
| Messages | `send_message`. |
| Pull read | `get_session` (`status_bucket`, `connection_status`, `post_turn_summary`, `task_summary`); `list_events` with `kinds: ["result"]`, `limit: 100`, paging back with `before_id`. |
| Lead wake-ups | User turns; pushed rc messages; child-session failure events; background `sleep` timer; `send_later` beyond an hour. |
| Interrupt | `interrupt_session` (clears queued messages: `cancel_queued: true`). |
| Workspace | rc: git worktree `<project folder>-worktrees/<thread>` created by `tw.py worktree`. cloud: container checkout with `outcome_branch`. |
| Context restore | SessionStart hook running `tw.py context`. |
| Install | `scripts/install.py`. |

## B2. Sessions, environments and kinds

- **Environments.** `list_environments` returns a `bridge` environment per Remote Control server and
  `anthropic_cloud` environments. The lead records both: `TW lead --rc-env <id> --cloud-env <id>`.
- **rc threads** see the project's files, tools and credentials, and edit only in their worktree. They stop when the
  Remote Control server stops or the machine sleeps.
- **cloud threads** run in a container with a clone of the repo and push to `outcome_branch`. They cannot see the
  project folder and do not depend on the user's machine.
- **Choosing a kind** today (SKILL.md §2.3): `rc` when the thread needs the project folder; `cloud` for research,
  writing, analysis, self-contained repo work and long runs. The target rule is A2 / I43 (`rc` by default, `cloud` only
  for work that never touches project files nor messages peers, reason recorded); the gap is declared in B10 (audit
  Y-21).
- **The lead must be rc:** a cloud lead could not see the project files, and rc threads could not message it.
- **Starting a thread** (SKILL.md §3.1):
  1. rc thread in a git project: `TW worktree <thread>` (idempotent; `--dry-run` to preview).
  2. `TW worker-prompt <thread>`.
  3. `create_session` with `environment_id` (always explicit; the default is the caller's own environment), the
     `title` and `tags` printed by `TW thread add` (`[<short>] <thread> · <focus>`, about 24 characters;
     `teamwork:<short>`, `teamwork-thread:<thread>`), `append_system_prompt`, `prompt` (the TASK),
     `extra_allowed_tools` (`send_message`, `get_session`, `list_events`), and `permission_mode`
     (`thread_permission_mode` if set, else the lead's own mode; never looser than the lead). cloud only:
     `source_url`, `source_revision`, `outcome_branch`.
  4. `TW thread set <thread> --session <id> --status working`, `TW task set <id> --status dispatched`.
- **Adopting** an existing session: `TW thread add <name> --session <id>`, then ROSTER and FOLLOWUP.

## B3. The ledger: `tw.py`

Every command acts on `-C <project folder>`; once given, it never falls back to the cwd, which matters when several
projects share a served folder. Writes go to `state.json.tmp`, are renamed over `state.json` (atomic), then `BOARD.md`
is regenerated and an event appended to `log.jsonl`.

| Command | Role | Section |
|---|---|---|
| `init <name> --goal [--short X] --repo-url --base [--force]` | Create ledger, default prefs (short name defaults to the name if ≤ 6 chars, else initials). | A3, A5 |
| `lead --session --rc-env --cloud-env --repo-url [--force]` | Record lead and environments; refuses a second lead without `--force`. | A3, A13 |
| `thread add <name> --kind --focus --owns [--session --branch --status --summary]` | Create seat; prints title, tags, branch, worktree, `create_session` args, title-length warning. | A2, A4, B2 |
| `thread set <name> [--session --status --summary --owns --focus --from-status]` | Update seat; `--from-status` marks a report from the thread itself (resets the stuck clock). | A9, A10 |
| `task add <id> --title --owner --deps --accept` / `task set <id> […]` | Add / update tasks; reassign with `--owner`. | A9, A13 |
| `plan show / approve / reopen` | Show; approve (planned → approved); back to draft. | A9, A10 |
| `ready` | Ready tasks, busy count, free slots, possibly stuck threads, capacity note. | A9, A14 |
| `route "<text>"` | Keyword hint for routing. | A11 |
| `ask "<q>" --from <t>` / `ask --answer <n>` | Questions waiting on the user. | A10, A12 |
| `memory "<fact>"`, `log "<note>"` | Append to Memory / Log. | A5 |
| `board`, `roster [--thread]`, `open` | Board; roster text; threads to sweep with stuck minutes. | A5, A6, A10 |
| `prefs --set k=v …` | Prefs; validates `short_name` and `thread_permission_mode`; on a short-name change prints sessions to retitle. | A3, A5 |
| `worktree <thread> [--dry-run]` | Create the rc thread's worktree and branch if missing. | A15, B6 |
| `worker-prompt <thread>` | Filled protocol; refuses if a placeholder is left. | B5 |
| `projects --root <served> [--json]` | Read-only portfolio. | A3 |
| `context` | SessionStart hook output. | B7 |

## B4. Transport on this host

Tested 2026-11-15, re-tested 2026-11-16 (`references/transport.md`):

| Direction | Result | Framework role |
|---|---|---|
| rc lead → rc / cloud thread: create, send, read | works | required lead reach |
| rc thread → rc lead | works once `send_message` is pre-allowed | push |
| rc ↔ rc peers | works | direct HANDOFF |
| cloud → cloud | reported working | direct HANDOFF |
| cloud → rc, relayed by the lead via `handed_off` | works; latency = sweep interval | relay |
| cloud → rc, direct | refused unless the user approves a trust card | — |
| rc → a trust-elevated cloud session | refused | elevation would cut lead reach |

Per the trust trade rule (A8):

- **rc threads push** DONE/BLOCKED/QUESTION/HANDOFF/MEMORY and still write the status block.
- **cloud threads are pull-only.** They never `send_message` to the lead or rc peers and never call
  `confirm_repository_trust`; the lead never approves trust for them. They write the status block and push their
  branch; the lead reads with `get_session` / `list_events` and `git fetch`.
- **Reading:** `list_events` filters `kinds` after reading the page, so use `limit: 100` and page back with
  `before_id = first_id` until a `result` appears; a thread in its first turn has none, so use `task_summary`.
- **Failure events:** per the tool documentation, a failed child turn is reported to the lead by the platform; not yet
  observed in field tests.
- **Timers:** background `sleep <checkin_minutes*60>` after dispatching cloud threads; `send_later` for checks more than
  an hour away.
- **Interrupt then RECALL:** `interrupt_session` returns `cancel_queued: true`; the thread sees only
  "[Request interrupted by user]", so the RECALL starts with "The lead just interrupted you."

## B5. Worker protocol injection

`templates/worker-protocol.md` is the thread's half of the framework. `tw.py worker-prompt` fills in project, short
name, thread, kind, focus, owns, branch, base, folder, worktree (with the exact `git worktree add` command if it is
missing), lead id, the language rule with the current `prefs.language`, roster, the thread's tasks, Instructions
(≤ 16 000 chars), Memory, and a kind-specific report channel. Running threads see Instructions changes only when the
lead sends them; the roster changes through `ROSTER`, the language through a FOLLOWUP to each thread.

## B6. Workspaces and branches

- Branch per seat: `tw/<full project name>/<thread>`; base from `init --base`.
- rc: the lead runs `TW worktree <thread>` before `create_session`; the worktree is
  `<project folder>-worktrees/<thread>`. The thread `cd`s into it and never edits the main checkout. Non-git folders
  rely on non-overlapping `owns` alone.
- cloud: `outcome_branch` at creation.
- The lead merges in its own checkout after the user accepts, `git fetch` first for cloud branches.

## B7. Context restore: the SessionStart hook

`install.py --project … --served …` adds `tw.py context` as a SessionStart hook to the served folder and the project
folder. On start, resume and after compaction:

- in a project folder: injects the language rule, board, Instructions and Memory, cut to 9 000 characters (the hook
  limit is 10 000) with a pointer to the full files;
- in the served folder: a one-line index of projects below it, nothing else.

After compaction the lead runs `TW board` and continues from the ledger.

## B8. Install and permissions

- The skill lives in `<config dir>/skills/teamwork/` on the machine running the Remote Control server (`$CLAUDE_CONFIG_DIR`
  if set, else `~/.claude`). `--link` (recommended) symlinks the clone, so what is checked out is what runs; a copy is a
  frozen snapshot. The installer never overwrites an existing install, dry-runs unless `--apply`, and writes `.bak`
  before changing settings.
- Allow rules in `<config dir>/settings.json`: `send_message`, `get_session`, `list_events`, `list_sessions`.
- **Thread permission mode:** without `permission_mode`, an rc thread inherits the server's default, which in field
  tests asked for nearly every tool call. The lead passes `thread_permission_mode` or its own mode; options from tight to
  loose: `default` + allow rules, `acceptEdits`, `auto`. Changing the server's global default is the user's decision.
- The lead's skill `allowed-tools` pre-approves `tw.py`, `git`, read tools and the session tools, including
  `create_session` and `interrupt_session`, while `/teamwork` runs. Archiving always asks.
- If the organisation sets the connector to "ask", allow rules are ignored.
- Trust cards: approve for the lead only, never for a cloud thread.

## B9. Playbook arguments

`/teamwork [new <name> | use <name> | projects | plan | go | status | sweep | route <request> | recall <thread> | close]`:
`new` → init (A10), `use` → attach / takeover (A3, A13), `projects` → portfolio (A3), `plan` → plan (A10),
`go` → dispatch (A10, A14), `status` / `sweep` → sweep (A10), `route` → routing (A11), `recall` → RECALL (A6, A13),
`close` → close (A10). No argument: portfolio view, then ask which project to lead.


## B10. Declared deviations

Rules Part A puts in code (A17) that this binding holds only by prompt today, or not at all:

| Rule | Status in this binding | Plan |
|---|---|---|
| I25, I28: requests end only by their owner; `done` only from `review` | Not enforced: `task set --status done` works from any status; no request ledger yet. | core T14 |
| I2: only the lead writes the ledger | Prompt only: any session that can run `tw.py` can write. | — |
| I11, I27: consent recorded from the user's own action, bound to parameters | Not recorded in code; consent lives in the chat. | — |
| I36: draft before outward actions | Prompt only. | — |
| I38: agent scope equals its principal's | Host only (the session's own permissions); no check in `tw.py`. | — |
| I42, I44, I49, I51: separate sessions, subagent language, lead-only tasks, deterministic handoff | Being added to the playbook (core T24). | — |
| Y-18: task `done` set from the status block | The playbook allows it; the framework does not. | audit Y-18 |
| Y-19: every disconnect mapped to `waiting` | The playbook does it; the framework keeps retried disconnects `working`. | audit Y-19 |
| I31: intake before routing | Not in the playbook yet. | audit Y-15 |
| I34: independent check | Not in the playbook yet. | audit Y-16 |
| I35: health check before work | Not in the playbook yet. | audit Y-17 |
| I43: default kind and `kind_reason` | Playbook still suggests `cloud` for repo work and long runs; no `kind_reason` field. | audit Y-21 |
| I46: ACK for FOLLOWUP | The worker protocol asks for ACK on TASK and HANDOFF only. | audit Y-20 |
| I29, I30, I39: deliveries | Not applicable: this binding merges, it does not deliver. | — |
| I32, I33, I37, I40, I41: standing authorizations, derived work, anchored messages, per-person ledgers, policy layers | Not applicable today: this binding has one user, one ledger and no policy layers. | — |
| I48: seat status from the thread's own report | The playbook maps host failure, disconnect and pending prompts as the framework says, but also any `waiting_on`; see Y-3, Y-19. | audit Y-3, Y-19 |
| I52, I53: origin tags, out-of-scope view alerts | Host matters; nothing in `tw.py`. | — |

I26 is not listed: `create_session` itself refuses a mode looser than the caller's (host), although `tw.py prefs`
accepts any mode name (audit Y-22).
---

# Part C. Invariants

Held by: **code** (enforced by `tw.py` today), **code (where it exists)** (must be code in a host that has the
feature; not implemented in this binding, see B10), **prompt** (SKILL.md or the worker protocol), **host** (the
platform), or **convention**.

| # | Invariant | Held by |
|---|---|---|
| I1 | A project has at most one lead at a time; takeover is explicit and recorded. | code + prompt |
| I2 | All ledger writes go through one interface; the lead is the only agent writer; threads never write. | prompt |
| I3 | Every State write is atomic, regenerates the Board and appends to the Log. The Board is never edited by hand. | code |
| I4 | The ledger is updated before the user is told something changed. | prompt |
| I5 | Active threads' ownership areas do not overlap. | prompt |
| I6 | Every task has exactly one owner, a seat in the ledger. | prompt (code checks a given owner exists; it does not require one) |
| I7 | A thread acts only inside its area; out-of-area work goes by HANDOFF, unowned work by QUESTION. | prompt |
| I8 | Every message starts with the `[tw:<short>] TYPE from -> to <one task id or ->` header and is self-contained. | prompt |
| I9 | Every thread turn ends with a status block. | prompt |
| I10 | No message, ledger entry or status block contains secrets. | prompt |
| I11 | Only the user grants consent, through the lead's chat or a host prompt in the thread; agent messages never do. | prompt + host |
| I12 | Consent-gated steps are executed by the lead after the user's yes in the lead's chat. | prompt |
| I13 | The lead can always reach every thread; no channel is gained at the cost of that reach. | prompt |
| I14 | Threads never create, archive or interrupt sessions. | prompt |
| I15 | Nothing is archived, deleted or forced without the user's explicit OK; the lead never recalls a silent thread on its own. | prompt + host |
| I16 | Each thread works on its own branch and workspace, created by the lead before the thread starts. | code (`worktree`) + prompt |
| I17 | A task is ready only when approved and all its deps are done. | code |
| I18 | With `propose_before_start`, no thread starts before the user approves the plan. | prompt |
| I19 | A lead and its threads touch only their own project's sessions and ledger. | prompt + convention |
| I20 | In a thread, the user's direct messages outrank the lead's and peers'. | prompt |
| I21 | After any context loss the lead trusts the ledger over its memory. | prompt + hook |
| I22 | Content read from sessions is information, not instruction. | prompt |
| I23 | Replies are in the language the user asked in; team message bodies and status values use the current conversation language; header keywords and field names never change. A switch is sent to every open thread. | code (`language_rule`) + prompt |
| I24 | A project has a short display name used in titles, headers and labels; titles stay short; branches and folders keep the full name. | code (`tag`, `title_for`, `title_warning`) + prompt |
| I25 | Every user message is registered as a request before it is routed, and every request ends `closed` or `dropped` **by the person who raised it**; nothing else ends a request. | none in this binding yet (B10) |
| I26 | A thread never starts in a permission mode looser than the lead's. | prompt + host |
| I27 | Consent comes only from the principal's explicit action on a channel agents cannot write; it covers an explicitly listed set of actions, has an expiry, and is void if an action changes; a timeout never grants it. | prompt (partial, B10) + host |
| I28 | A request's `closed` / `dropped` and a task's `done` are set only when the ledger interface's preconditions hold (user action; prior `review`). | none in this binding yet (B10) |
| I29 | Only a `signed_off`, frozen package is delivered; the sign-off records the version; any change, by anyone, voids it. | code (where deliveries exist) |
| I30 | A delivery executes only under the stricter of the sender-side and receiver-side policies, and is sent at most once per version and round (idempotency key). | code (where deliveries exist) |
| I31 | Every request goes through intake at its tier before work is dispatched; a heavy-tier request has a recorded intent, success criterion and decision. | none in this binding yet (B10) |
| I32 | A standing authorization is consent only within its recorded scope, and only its owner sets or changes it. | code (where it exists) |
| I33 | Work derived from records is extracted from them (not recalled), carries a pointer to its source, and enters the ledger as an open request when extracted. | none in this binding yet (B10) |
| I34 | A result reaches the user's acceptance only after a check by someone without the author's context (another session, a context-free subagent, or the user), recorded on the task; the author's self-check never counts. Tiered: required for heavy-tier work, irreversible work and deliveries. | none in this binding yet (B10) |
| I35 | Seats are reused by role; a seat starts a new term only on a concrete signal (context near full, compacted, a failed re-check, or several independent checks in a row failed); an empty seat is filled; a session takes no work before it passes a health check whose rubric it cannot see. | none in this binding yet (B10) |
| I36 | Every outward, irreversible action exists first as a saved draft; only the sent version is final. | code (where the host sends) + prompt |
| I37 | Every communication is anchored to an object (task, requirements doc, discussion, meeting record). | none in this binding yet (B10) |
| I38 | An agent's capabilities equal its principal's, consent-class acts included, but it performs those only after the principal's explicit consent to the listed actions (I27); requests to others for private material are blocked and reported. | code (where it exists) + host |
| I39 | A cited or delivered record is immutable (hash recorded): after delivery the original is frozen and only appended revisions are allowed, the old one marked superseded; nobody edits another person's comment. Once a receiver starts its reply, the package it received is frozen and only the reply is edited; sending it notifies everyone involved. | code (where deliveries exist) |
| I40 | Each person's ledger is written only by their own lead or by that person; others' progress is pulled from their agent, not synced. | none in this binding yet (B10) |
| I41 | Policy layers combine to the strictest; agents only tighten; an override needs an entitled person and an expiry; every policy maps to a real control in the target, or is raised to confirm. | code (where it exists) |
| I42 | A seat is always a separate host session; the lead never uses subagents for domain work and never lets a subagent stand in for a seat. Seats may use subagents freely. If sessions cannot be started, the lead stops and reports instead of degrading. | none in this binding yet (B10) |
| I43 | The thread kind defaults to the one with workspace access and two-way messaging with the lead; a one-way kind is used only for work that never touches project files nor messages peers, with the reason recorded. | none in this binding yet (B10) |
| I44 | Subagent briefs and reports use the current conversation language, like every other team message. | none in this binding yet (B10) |
| I45 | Only the lead creates seats (a thread asks the lead; the lead creates, records and broadcasts the roster), and reports flow subagent → seat → lead → user, never skipping a level. | prompt |
| I46 | Every TASK, FOLLOWUP and HANDOFF is acknowledged; a task still `dispatched` without ACK past one check-in is resent once, then reported to the user. | prompt (partial, B10) |
| I47 | While any pull-only thread is open, the lead keeps a timer running. | prompt |
| I48 | A seat's status comes from the thread's own latest report; host statuses only signal failure, disconnection or a pending prompt. | none in this binding yet (B10) |
| I49 | Only the lead creates tasks; threads raise requests. | none in this binding yet (B10) |
| I50 | To stop a thread mid-turn, interrupt first, then send the RECALL, whose first sentence says the lead interrupted. | prompt |
| I51 | A new session in a seat is briefed from a deterministic handoff built from the ledger; a degraded session's own summary is never the only handoff. | none in this binding yet (B10) |
| I52 | Every agent action carries an origin tag set by the system from the calling channel; client-supplied origin is ignored. | code (where it exists) + host |
| I53 | Out-of-scope viewing by an agent silently alerts the administrator and the affected person, with a threshold, deduplication and an appeal path. | code (where it exists) + host |
| I54 | A standing authorization the principal set counts as advance consent for its class, except for confirming, accepting, reopening, loosening an authorization (including a longer expiry), releasing an out-of-scope alert and viewing secrets in clear text, which each need their own consent. A consent is void if its actions' content or version changes, an action is added, the target was changed by someone else, or it times out; a failed step stops the rest until consent is asked again; each step runs once; consent comes only from the principal's own human channel. | code (where it exists) + host |

---

# Part D. Failure modes and mitigations

| # | Failure | Symptom | Mitigation | Invariants | Section |
|---|---|---|---|---|---|
| F1 | rc thread stalls on permission prompts | `needs_action: Waiting on permission` | Allow rules; `extra_allowed_tools`; `permission_mode` from `thread_permission_mode` or the lead; else tell the user which thread and prompt, offer to do it from the lead. | I11, I26 | A12, B8 |
| F2 | Cloud thread elevated by a trust card | Lead's sends fail "needs proof of a trusted device". | Never elevate. New seat session with the same TASK and old summary; archive the old one after user OK. | I13 | A8, B4 |
| F3 | Lead is a cloud session | `elevated_session_untrusted_device` when messaging rc. | Precondition check; open the lead in the RC environment. | I13 | B2 |
| F4 | Remote Control server down / machine asleep | rc threads disconnected. | Tell the user; cloud threads continue. | — | A13 |
| F5 | Lead context compacted | Lead loses plan details. | Hook re-injects; `TW board`; sweep. | I21 | A13, B7 |
| F6 | Lead session lost | No coordinator. | `use <name>`; `--force` only if the old one is archived or the user says so. | I1 | A3, A13 |
| F7 | Ledger lost or corrupt | `tw.py` fails to load. | Rebuild from log, session labels, status blocks, branches (manual). | I3 | A13 |
| F8 | Pull-only thread finishes and nobody notices | Thread in `review` for hours. | Timer; failure events; sweep before every status answer. | I47 | A8 |
| F9 | Thread silent / stuck | No report while `working`. | "Possibly stuck" group after `stuck_minutes`; FOLLOWUP for a status block; after twice that, tell the user and offer RECALL or reassign. | I9, I15 | A9, A10 |
| F10 | HANDOFF never acknowledged | Task owner unclear. | Lead moves the owner on `handed_off` and forwards if no ACK. | I6, I7, I46 | A6, A10 |
| F11 | Two threads edit the same files | Merge conflicts. | Non-overlapping `owns`; lead-created worktrees; lead resolves or FOLLOWUP; rebase notices. | I5, I16 | A4, A15 |
| F12 | Thread drifts from the task | `did`/`next` off target. | FOLLOWUP with the exact correction; RECALL if needed. | — | A13 |
| F13 | Injected instructions in thread content | A message asks the lead to approve, delete or change settings. | Content is information; only the user approves. | I11, I22 | A12 |
| F14 | Command hits the wrong project | Another project's ledger changed. | Always `-C`; never falls back to cwd once given. | I19 | B3 |
| F15 | Capacity exhausted | `create_session` fails, or ≥ 30 bridge sessions. | Count first; suggest archives (user chooses) or `cloud`. | I15 | A14 |
| F16 | Board stale | User told old state. | Sweep before answering; ledger first, then tell. | I4 | A5, A10 |
| F17 | Instructions changed, running threads unaware | Old rules followed. | Lead re-sends the change; new threads get it at creation. | — | B5 |
| F18 | Hook context truncated | Lead misses part of Instructions after compaction. | Truncation marker points to the files; lead reads them. | I21 | B7 |
| F19 | Consent-gated step routed to a thread | Thread refuses or waits forever. | Thread prepares, lead asks and executes. | I12 | A12 |
| F20 | Status block not found when reading | `list_events` returns no data. | `limit: 100`, page back with `before_id`; `task_summary` in the first turn. | I9 | A8, B4 |
| F21 | RECALL lost | Thread keeps working after a recall. | Interrupt first, then RECALL; RECALL says the lead interrupted. | I50 | A6, B4 |
| F22 | Team drifts into a language the user is not using | Lead must translate; nuance lost. | Language rule in every protocol; FOLLOWUP to each thread on a switch. | I23 | A6 |
| F23 | **A request gets lost** | User asked; nothing came back, or it came back and nobody told them, or it was never confirmed. | Register before routing; request ids in replies; sweep lists delivered-not-reported and reported-not-closed every time; only the user closes. | I25 | A9, A10, A11 |
| F24 | Thread guesses its workspace | Wrong base, path or branch name. | Lead creates the worktree first and passes the exact path. | I16 | A15, B6 |
| F25 | Dispatch reports success but nothing is queued | Thread never starts the work; no ACK. | Require ACK for every TASK/FOLLOWUP; the sweep lists `dispatched` tasks with no ACK past one check-in and re-sends or tells the user. | I46 | A6, A10 |
| F26 | Approval reused for changed parameters | A step runs that the user did not approve in that form. | Bind approvals to parameters; re-ask on change. | I27 | A12 |
| F27 | Delegate delivers on its parent's behalf | Parent task closed by a sub-result. | Delegates report only to their delegator, under their own id. | I45 | A14 |
| F28 | Intake skipped | Work done on the literal ask; the user's real intent missed. | Heavy tier on the triggers in A11; intent and success in the reply. | I31 | A11 |
| F29 | Every sentence pays for full intake | Slow, verbose replies to simple asks. | Two tiers: light for simple asks; heavy on any trigger or when in doubt. | I31 | A11 |
| F30 | Delivery sent twice | The target executes the same work twice. | Idempotency key; check the target before a retry. | I30 | A13, A16 |
| F31 | Unsigned or changed package delivered | Target acts on a draft. | Freeze at sign-off; change voids it; only `signed_off` can be sent. | I29 | A12, A16 |
| F32 | Execution policy loosened by the sender | A `confirm` target runs work nobody confirmed. | Policy set by the target owner; sender may only tighten. | I30 | A12 |
| F33 | Shared record edited in two places | Two versions of the plan. | One owner thread per shared record; others HANDOFF. | I5 | A16 |
| F34 | Degraded session summarizes itself | Handoff carries its confusion into the new session. | Deterministic handoff from the ledger and status blocks. | I51 | A13 |
| F35 | Action item in a record never becomes work | A meeting decision is forgotten. | Extract derived work from records with a source pointer; re-read on sweep. | I33 | A16 |
| F36 | Standing authorization stretched | Work outside the approved scope runs without asking. | Scope recorded with the authorization; outside it, ask. | I32 | A12 |
| F37 | Author grades its own work | A self-reported "precheck ok" closes a result nobody else looked at. | Independent check by a session without the author's context; record the checker; reject a check whose session is the author's. | I34 | A9, A15 |
| F38 | Broken session given work | A new session that misread its protocol or lacks tools works for hours on the wrong thing. | Health check before dispatch; replace on failure. | I35 | A2 |
| F39 | Irreversible action without a draft | A wrong message or delivery goes out and cannot be recalled. | Draft first, auto-saved; check before send. Recovery cannot undo it. | I36 | A13 |
| F40 | Unanchored message | A decision made in a side channel is never found or tracked. | Anchor every message to an object. | I37 | A5 |
| F41 | Agent oversteps its principal | An agent acts beyond its person's rights, or fishes for someone's private data. | Permissions equal the principal's; block and report private-material requests. | I38 | A12 |
| F42 | Original edited while the reply is being written | Reply answers a package that changed under it. | Freeze the original when the reply draft starts. | I39 | A16 |
| F43 | Seats multiplied | Several sessions for the same kind of work; history split. | Standing seats; fill empty seats only. | I35 | A2 |
| F44 | Policy exists only on paper | "Plan first" target runs with full write access. | Map each policy to a real target control (plan or read-only mode); otherwise raise to confirm. | I41 | A12 |
| F45 | Override never expires | A temporary loosening becomes permanent. | Overrides require an expiry and are recorded. | I41 | A12 |
| F46 | Agent performs a consent-class act on its own | A sign-off, acceptance, reopen or loosened authorization happens that the person never agreed to in that form. | Prepare and list the exact actions; act only on the principal's explicit consent to that list; void on change or expiry. | I27, I38 | A12 |
| F47 | The lead uses a subagent in place of a seat | The user cannot talk to it; it is not on the board; later follow-ups find no context to continue from; the lead's context fills up; with no seat and no ledger entry its work cannot be swept, recalled or recovered. | Domain work always goes to a seat; if sessions cannot be started, stop and report. | I42 | A2, A14 |
| F48 | One-way thread kind chosen by default | The thread needs a file or a peer it cannot reach; work stalls or is relayed slowly. | Default to the two-way kind with workspace access; record the reason for any one-way choice. | I43 | A2 |
| F49 | Subagent reports in another language | The lead has to translate; nuance lost. | Write subagent briefs in the current language and ask for reports in it. | I44 | A6 |
| F50 | A thread starts its own session | A seat the lead does not know: missing from the ledger and the roster, unswept. | Threads ask the lead for new seats; only the lead creates them. | I14, I45 | A14 |
| F51 | A thread writes the ledger | Two writers; the board no longer matches what the lead decided. | Only the ledger interface writes; the lead is the only agent writer. | I2 | A5 |
| F52 | Task marked `done` without review | A dependency is satisfied by work nobody accepted. | Interface preconditions: `done` only from `review` after acceptance. | I28 | A5, A9 |
| F53 | Thread started before the plan is approved | Work the user never agreed to. | `propose_before_start` gate at dispatch. | I18 | A10 |
| F54 | Secret in a message or status block | Credentials spread to every reader of the transcript. | Point to where a secret lives; never paste it. | I10 | A6 |
| F55 | Task dispatched before its dependencies are done | Work built on results that may still change. | Readiness check at dispatch. | I17 | A9 |
| F56 | Thread obeys the lead over the user | The user's direct instruction in a thread is overridden. | User messages outrank lead and peer messages in a thread. | I20 | A11 |
| F57 | Seat status taken from a host status | Board shows "done" or "waiting" the thread never reported. | Seat status from the thread's own report; host statuses only for failure, disconnection or a pending prompt. | I48 | A2, A9 |
| F58 | Executor creates a task | Work outside the plan with no intake and no request behind it. | Executors raise requests; only the lead creates tasks. | I49 | A16 |
| F59 | Another instance writes my ledger | Progress reported that I never confirmed. | Pull from the other agent; write only my own ledger. | I40 | A5 |
| F60 | Malformed header or task-id list | The lead misroutes or cannot parse a report. | One header format, one board task id or `-`. | I8 | A6 |
| F61 | Long session titles | The user cannot tell threads apart in a narrow list. | Short project name, short seat names, 1–3 word focus. | I24 | A3 |
| F62 | Client forges its origin | An agent's action looks like the person's own, or another agent's. | Origin tag set by the system from the calling channel; client-supplied origin ignored. | I52 | A12 |
| F63 | Out-of-scope view goes unnoticed | An agent reads material outside its principal's scope and nobody knows. | Silent alert to the administrator and the affected person. | I53 | A12 |
| F64 | Alert flood or false accusation | Alerts are ignored, or a person is flagged for nothing. | Threshold, deduplication, appeal path. | I53 | A12 |
| F65 | Delivered original or someone's comment edited | The record no longer says what was agreed. | Freeze after delivery; append-only revisions; comments immutable to others. | I39 | A16 |
| F66 | Standing authorization used for a reserved act | An acceptance, reopen, loosened authorization, released alert or clear-text secret view happens without its own consent. | Reserved classes always need per-instance consent. | I54 | A12 |
| F67 | Consent outlives its content | A step runs after its target or content changed, an extra step was slipped in, or the remaining steps ran after one failed. | Bind consent to content and version; void on change, addition, outside edit or timeout; stop after a failure; each step once. | I54 | A12 |

---

# Part E. Porting checklist

In this part, **worker** means a thread's session as the host sees it.

**A host must provide these primitives.** "Required" means the framework does not run without it; "substitutable" says
what can replace it.

| # | Primitive | Required? | Contract | Claude Code |
|---|---|---|---|---|
| H1 | **Spawn a worker session** with: injected system prompt (system-level; if the host can only send it as the first chat message, H8 must re-inject it on resume and after compaction), first message, environment/kind, workspace source (repo + revision + output branch), title, labels, **permission mode** and pre-granted tools. Returns a stable id. | required | Protocol text survives compaction (system-level, not a chat message). The permission mode can be set per session and never exceeds the caller's. | `create_session` (`append_system_prompt`, `prompt`, `environment_id`, `source_url`, `source_revision`, `outcome_branch`, `title`, `tags`, `permission_mode`, `extra_allowed_tools`) |
| H2 | **Send a message** to a session, queued if busy. | required (lead → thread) | At-least-once; refusals reported. A send that returns success but drops the message when the receiver is busy does not meet H2 (F25). | `send_message` |
| H3 | **Read session state** (running / waiting on user / failed / disconnected / idle) plus a short summary, including during the first turn. | required | Cheap; no transcript needed. | `get_session` (`status_bucket`, `connection_status`, `post_turn_summary`, `task_summary`) |
| H4 | **Read a session's recent final outputs**, filtered and bounded. | required for pull | State whether the filter applies **before or after paging**. If after, readers must request large pages and page back until a match. A server-side query by condition satisfies this. | `list_events` (`kinds` filters after the page; `limit: 100`, `before_id`) |
| H5 | **Wake the lead** on a timer. | required if any thread is pull-only | Fires a new lead turn. | background `sleep`, `send_later` |
| H6 | **Push to the lead**: thread → lead message, child-failure event. | substitutable by H4 + H5 | Arrives as a new lead turn. Recommended: a durable outbox on the worker side that stores the report first and retries until delivered or explicitly refused. | rc `send_message`; child-session events |
| H7 | **Durable ledger store**, lead-only, atomic replace + append. | required | Survives session loss. | `.teamwork/` via `tw.py` |
| H8 | **Context restore** on start, resume, after compaction. | substitutable by the lead reading the ledger first | Bounded ledger summary. | SessionStart hook → `tw.py context` |
| H9 | **Isolated workspace per worker**, created by the lead before the worker starts, plus a result channel. | required for parallel edits (producing teams; optional when outputs are records) | No two workers share a writable checkout; the worker is told the exact path. A lock on a shared checkout serializes work but does not isolate it. | `tw.py worktree` (rc), container + `outcome_branch` (cloud) |
| H10 | **Interrupt** a running session. | optional | State whether interrupt **clears queued messages** and how the interrupted session sees it. If it clears the queue, interrupt first, then RECALL; if it looks like a user stop, the RECALL says the lead did it. | `interrupt_session` (`cancel_queued: true`; shown as "[Request interrupted by user]") |
| H11 | **List sessions** with labels and kinds. | optional | For capacity and adoption. | `list_sessions`, `list_environments` |
| H12 | **Pre-grant** reporting tools so workers never block on a prompt. | required if H6 is used | Scoped to reporting tools. | allow rules, `extra_allowed_tools`, skill `allowed-tools` |
| H13 | **User approval surface** in the lead, plus per-session permission prompts. | required | Only the user can answer. | claude.ai chat; tool prompts |
| H14 | **Playbook packaging**: load the lead's instructions on demand. | required | | skill `SKILL.md` |
| H15 | **Archive / retitle / relabel** sessions. | optional | Archive needs the user's OK. | `archive_session`, `set_session_title`, `set_session_tags` |
| H16 | **Channel matrix and trust model**, tested and written down. | required (documentation) | Lets the lead choose push or pull per kind. | `references/transport.md` |
| H17 | **Delivery endpoint**: send a package to another agent or tool with a target-owned execution policy (`direct` / `confirm` / `queue`), an idempotency key, and an acknowledgement. | required for coordinating teams that deliver | The sender cannot loosen the policy; a repeated key is not executed twice. | not used (producing team) |

**Minimum viable host:** H1, H2, H3, H4, H5, H7, H9, H13, H14, H16; plus H12 if H6 is used, and H17 for a coordinating
team that delivers.

Porting steps:

1. Fill the table for the new host; test the channel matrix (H16), and the H4 and H10 behaviours, rather than reading docs.
2. Re-implement the ledger interface, or reuse `tw.py` if the host has a filesystem and Python.
3. Re-bind the lead playbook: replace each `claude-code-remote` call with the host primitive.
4. Re-bind the worker protocol's report channel per kind from H16.
5. Keep Parts A and C. If the host forces a change to an invariant, record it as a declared deviation with the reason.
6. Field-test: create, message, read, failure, compaction, recall (interrupt order), one cross-kind handoff, one request
   from registration to the user's close.

---

# Part F. Hosting on other platforms

## PF1. Host conformance checklist

Copy this list, tick what the host has, and write how. Unticked **required** items must be built first.

**Host primitives** (Part E)

- [ ] H1 Spawn a worker: protocol, first message, kind, workspace, labels, permission mode → stable id
- [ ] H2 Send a message, queued if busy
- [ ] H3 Read worker state cheaply, including in the first turn
- [ ] H4 Read recent final outputs, bounded; filter-vs-paging behaviour known
- [ ] H5 Timer that wakes the lead
- [ ] H6 Worker → lead push and failure events (optional with H4 + H5)
- [ ] H7 Durable ledger store, atomic, with an append-only log
- [ ] H8 Context restore on start / resume / compaction (optional if the lead reads the ledger first)
- [ ] H9 Lead-created isolated workspace per worker, and a result channel
- [ ] H10 Interrupt, with its queue behaviour known (optional)
- [ ] H11 List sessions with labels and kinds (optional)
- [ ] H12 Pre-grant reporting tools
- [ ] H13 User approval surface in the lead, plus per-session prompts
- [ ] H14 Load the lead playbook on demand
- [ ] H15 Archive, retitle, relabel (optional)
- [ ] H16 Channel matrix and trust model, tested
- [ ] H17 Delivery endpoint (required for coordinating teams that deliver)

**Framework behaviour the host must carry** (Part A)

- [ ] FW1 Seats separate from sessions; re-seat and adopt (A2)
- [ ] FW2 Ownership areas with a roster every worker sees; no overlap; HANDOFF for out-of-area work (A4)
- [ ] FW3 One ledger interface that checks preconditions; the lead is the only agent writer (A5)
- [ ] FW4 Envelope and the twelve message types, or a published mapping to the host's own verbs (A6)
- [ ] FW5 Status block at the end of every worker turn (A7)
- [ ] FW6 One thread machine and one task machine; ready = approved + deps done (A9)
- [ ] FW7 Consent only from the user's own action, bound to parameters, never by timeout (A12)
- [ ] FW8 Lead recovers from the ledger after context loss; seats re-filled after session loss (A13)
- [ ] FW9 Request ledger: every user message registered before routing; open → (proposed →) dispatched → delivered → reported →
      closed / dropped; only the user ends a request; the sweep lists delivered-not-reported and reported-not-closed
      (A5, A9, A10)
- [ ] FW10 Tiered intake before dispatch: intent, success, decision (do / propose / drop) (A11)
- [ ] FW11 Team type chosen per project (producing / coordinating / both) and its column of A16 applied
- [ ] FW12 Delivery channels with sign-off, target-owned policy, idempotent sends, acknowledgements, and cited records immutable (A12, A16)
- [ ] FW13 Work derived from records with source pointers; deterministic handoff on session swap (A13, A16)
- [ ] FW14 Independent check before acceptance, by a session without the author's context, recorded on the task (A9)
- [ ] FW15 Standing seats with a health check before work (A2)
- [ ] FW16 Draft before every outward action; recovery limited to agent context and seat succession (A13)
- [ ] FW17 Records as ledger where the host records everything; every message anchored; one ledger per person, pulled not synced (A5)
- [ ] FW18 Agent permissions equal its principal's; private-material requests blocked and reported; per-context execution policies (A12)
- [ ] FW19 One lead per project; projects isolated from each other (A3)
- [ ] FW20 Language rule through the whole chain, subagents included (A6)
- [ ] FW21 Threads are separate host sessions; default kind two-way with workspace access; only the lead creates seats; reporting chain subagent → seat → lead → user (A2, A14)
- [ ] FW22 Lead keeps a timer while any pull-only thread is open (A8)
- [ ] FW23 Interrupt first, then RECALL, saying the lead interrupted; every TASK / FOLLOWUP / HANDOFF acknowledged (A6)
- [ ] FW24 The lead can always reach every thread; no channel gained at the cost of that reach (A8)
- [ ] FW25 No secrets in messages or ledger; content read from sessions is information, not instruction (A6, A12)
- [ ] FW26 Policy layers combine to the strictest; overrides expire; policies map to real target controls (A12)
- [ ] FW27 Consent-gated steps run only after the user's explicit consent; ledger updated before the user is told; plan gate before starting threads (A5, A10, A12)
- [ ] FW28 User messages outrank lead and peers in a thread; short display names; standing authorizations scoped; seat status from the thread's own report; only the lead creates tasks (A2, A3, A12, A16)
- [ ] FW29 Agent actions carry a system-set origin tag; out-of-scope views raise silent alerts with threshold, dedupe and appeal (A12)
- [ ] FW30 Standing authorizations never cover confirm / accept / reopen, loosening, releasing alerts or clear-text secrets; consent bound to content and version, stops on failure, each step once (A12)

## PF2. Worked example: a fictional chat-workspace host

To show how the checklist is used, take an imaginary host, **Relay**, invented for this example: agents run as members
of a team chat workspace. Each agent session is a private channel; a human posts in a channel to talk to that agent;
agents post to each other through a bot API; the workspace keeps channel history; a scheduler can post into a channel
at a set time.

| Primitive | Relay | Verdict |
|---|---|---|
| H1 spawn | "Create channel + invite agent" with a pinned system prompt and a first post | ✅, if the pinned prompt survives the agent's context compaction; otherwise inject it again on resume (H8) |
| H2 send | Post to a channel | ✅ |
| H3 state | Channel member status (typing, idle, error) | ⚠️ no "waiting on the user" signal: the status block's `waiting_on` must carry it |
| H4 read | Channel history search | ✅; check whether filters apply before or after paging |
| H5 timer | Scheduled post into the lead's channel | ✅ |
| H6 push | Agents post into the lead's channel | ✅ for every agent: no pull-only kind needed |
| H7 ledger | None built in | ❌ build one: a file store or a database the lead's tools write; channel history is not a ledger (no single writer) |
| H9 workspace | None built in | ❌ each agent needs its own checkout or folder, created by the lead |
| H10 interrupt | "Stop" button per channel | ✅; test whether it drops queued posts |
| H13 approval | Humans react ✅/❌ in the lead's channel | ✅, if only the user's reaction counts, not an agent's |
| H16 matrix | Everyone can post everywhere | ✅, but that is a reason to keep I7 and I22 strict: reachability is not authority |

What Relay would have to add, in framework terms:

- **FW3 / H7:** a ledger outside the chat. Chat history is shared and multi-writer; it cannot be the source of truth. The
  board can be *posted* into a channel as a projection, never edited there.
- **FW5:** the status block as a fixed post format at the end of every agent turn, because member status alone does not
  say who is waiting on whom.
- **FW2:** a roster post pinned in every agent channel, and routing by `owns` first.
- **FW7:** approvals counted only from the user's account in the lead's channel; an agent's reaction or post is never
  consent, even though every agent can post there.
- **FW9:** request ids in the lead's replies, and the sweep's request list posted to the user on each check.

Relay keeps its own verbs (create channel, post, stop); it publishes a mapping from them to the twelve message types
(FW4). The types are the contract, not the words.

## PF3. General guidance for any host

- If the ledger sits behind an API, move I2, I5, I6, I25 and I28 from prompt (or nothing) to code: reject writes from non-lead
  sessions, reject overlapping `owns`, validate transitions against A9, refuse to close a request without a user action.
- Decide the team type first (A16). A coordinating host's main risks are duplicate and unsigned deliveries and two
  versions of a shared record, not merge conflicts; put its code enforcement there (A17).
- Keep requests in the store the ledger interface guards, so I25 and I28 can be checked in code. Any other list of
  open promises the host keeps (in a chat archive, on a client) should be derived from the request store, not kept
  beside it.
- If every worker can push, keep the status block anyway: it is the recovery record (A13).
- If the host has a native overview UI, render it from State, as `BOARD.md` is rendered; never as a second source of
  truth.
