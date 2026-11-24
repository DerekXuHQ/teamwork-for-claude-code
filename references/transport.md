# Who can talk to whom

Tested on a Pro account on 2026-11-15 with the `claude-code-remote` tools (`send_message`, `create_session`, `get_session`, `list_events`). "rc" = a Remote Control session (`environment_kind: bridge`): driven from claude.ai, running in the folder its Remote Control server serves. "cloud" = a cloud container session (`anthropic_cloud`).

| From → To | Result | Notes | Re-tested 2026-11-16 (field log) |
|---|---|---|---|
| rc → rc, send message | ✅ works | An existing RC session received the message and replied within 1–2 minutes (it was busy, so the message queued until its next turn). | ✅ 2026-11-16: field→core HANDOFF, ACK back in ~60 s; delivered into core's running turn (log P2) |
| rc → rc, create session | ✅ works | An RC session created another RC session in the same bridge environment; it connected and finished its first turn in about 10 s. | ✅ 2026-11-16: lead→field, first turn ~11 s after `created_at` (log P1) |
| rc → rc, follow-up | ✅ works | A second message to the new session continued the same context. | ✅ 2026-11-16: lead FOLLOWUP and ROSTER landed inside field's running turn (log P7, P8) |
| rc → rc, read transcript | ✅ works | `get_session` and `list_events` on another rc session. | ⚠️ 2026-11-16: works, but `kinds:["result"]` with a small `limit` returns no data; a thread in its first turn has no `result` yet (log P3) |
| rc child → rc parent, send message | ⚠️ stalls | The child stopped at "Waiting on permission: Send Message". Fix: pre-allow the tool (setup.md §2) and pass `extra_allowed_tools` when creating the child. | ✅ 2026-11-16: field→lead ACK delivered with no prompt (tool pre-allowed); ✅ 2026-11-24 (T73): rc child (auto) → parent delivered, permission `accept (hook)`, no card |
| rc → cloud, read transcript | ✅ works | `get_session` and `list_events` on a cloud thread. | ✅ 2026-11-16: field read framework's last `result`; its `<tw-status>` block was complete and parseable (log P4) |
| rename project tag/title mid-run | - | - | ✅ 2026-11-16: running rc thread kept working after the lead changed its title and tag; branch and folder names are not renamed (log P8) |
| cloud → rc, handoff relayed by the lead | ✅ works | Cloud thread writes `handed_off` in its STATUS; the lead reads it on a sweep and dispatches. | ✅ 2026-11-16 (lead, L6b): framework → lead → core T15; latency = sweep interval, ~10 min |
| platform → lead, child-session failure event | - | - | not observed 2026-11-16: no thread failed (L6c). ⚠️ 2026-11-24 (T73): still not observed. An invalid model does not fail (silent `model_fallback`); an `interrupt_session` ends the turn `error_during_execution` but `status_bucket` is REVIEW_READY (not FAILED), and no event had reached the parent by 18:05; a real crash was not provoked (child refused `kill`). See `docs/field-tests/2026-11-24-t73-field-tests.md` |
| rc → new cloud, create + send | ✅ works | An RC session created a cloud session and messaged it. | not re-tested |
| cloud → rc, send message | ⛔ refused by default | Error `elevated_session_untrusted_device`. It works only after the user approves a trust card for that cloud session (`confirm_repository_trust`). | not re-tested |
| rc → a cloud session that was trust-elevated | ⛔ refused | Error: "the session you named needs proof of a trusted device". Once a cloud session is elevated so it can reach RC sessions, RC sessions can no longer message it. | ⛔ 2026-11-16 (lead, L2): `send_message` "needs proof of a trusted device", `list_events` `elevated_session_untrusted_device`; `get_session` still works |
| cloud (elevated) → rc, send message | ✅ works | | ✅ 2026-11-16 (lead, L1): the elevated setup session's message reached the lead |
| cloud (elevated) → create rc session | ✅ works | | not re-tested |
| cloud → cloud | reported working by the user | not re-tested in this test run | ⚠️ 2026-11-24 (T73): not delivered. The sending cloud session (auto) held `send_message` for the user's go-ahead (`status_bucket` BLOCKED) because the ask came in its first prompt, not from the user. Whether cloud sessions have `send_message` at all is unverified (neither session's tool list shows it). Delivery after the go-ahead is a user walkthrough step |
| cloud → project folder | ⛔ | A cloud session cannot read the served folder. `list_computer_folders` returned nothing on this account. | not re-tested |

## What the design takes from this

1. **The lead is an rc session.** It can create, message, read and interrupt both rc and cloud threads.
2. **rc threads push.** They message the lead directly (`send_message` to the lead's session id), once the tool is pre-allowed.
3. **Cloud threads never push to the lead.** Elevating them would cut the lead's line to them. They end every turn with a `<tw-status>` block and push code to git; the lead pulls with `get_session` / `list_events` and `git fetch`. The platform says it tells the parent when a child's turn fails (a child-session event), but this has not been seen in a field test (2026-11-24: an interrupt does not count as a failure), so do not rely on it: the sweep is the backstop.
4. **Peers hand off directly.** rc↔rc works. cloud → cloud is unproven (2026-11-24: held for the user, tool presence unverified), so until it is proven, cloud-to-cloud handoffs also go through the STATUS block and the lead. A cloud thread that needs an rc thread asks through its STATUS block, and the lead forwards the request.
5. **Completion signals.** RC threads send DONE/BLOCKED to the lead, which arrive as new turns. For cloud threads the lead uses a background timer and sweeps.

## Reading another thread's STATUS

`list_events` applies `kinds` after it reads the page. On a busy thread, `kinds: ["result"]` with
`limit: 3` returns no `data`, only `has_more: true`. Ask for `limit: 100` and page back with
`before_id = first_id` until a `result` turns up. A thread still in its first turn has no `result`
at all; use `get_session` → `task_summary` instead. (Re-tested 2026-11-16, see
`docs/field-tests/2026-11-15.md`.)

## Interrupt, then RECALL

`interrupt_session` returns `cancel_queued: true`: messages still queued for the thread are dropped.
Interrupt first, then send the RECALL; a RECALL sent before the interrupt is wiped. The interrupted
thread only sees `[Request interrupted by user]`, so the RECALL's first sentence should say the lead
did it. (Re-tested 2026-11-16, log P5 and L3.)

## Error strings and what to do

- `elevated_session_untrusted_device` when the **lead** messages an rc session: the lead itself is a cloud session. Open a new session in the Remote Control environment and run `/teamwork` there (setup.md §3). Do not elevate.
- "needs proof of a trusted device" when messaging a cloud thread: that thread was elevated. Leave it, create a fresh cloud thread with the same TASK and the old one's summary, mark the old one `archived` in the ledger after the user agrees.
- `Waiting on permission: <tool>` in a thread's `needs_action`: the user has to approve it in that thread, or add the allow rule (setup.md §2) for next time.
