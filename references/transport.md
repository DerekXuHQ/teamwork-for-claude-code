# Who can talk to whom

Tested on a Pro account on 2026-11-15 with the `claude-code-remote` tools (`send_message`, `create_session`, `get_session`, `list_events`). "rc" = a Remote Control session (`environment_kind: bridge`): driven from claude.ai, running in the folder its Remote Control server serves. "cloud" = a cloud container session (`anthropic_cloud`).

| From → To | Result | Notes |
|---|---|---|
| rc → rc, send message | ✅ works | An existing RC session received the message and replied within 1–2 minutes (it was busy, so the message queued until its next turn). |
| rc → rc, create session | ✅ works | An RC session created another RC session in the same bridge environment; it connected and finished its first turn in about 10 s. |
| rc → rc, follow-up | ✅ works | A second message to the new session continued the same context. |
| rc → rc, read transcript | ✅ works | `get_session` and `list_events` on another rc session. |
| rc child → rc parent, send message | ⚠️ stalls | The child stopped at "Waiting on permission: Send Message". Fix: pre-allow the tool (setup.md §2) and pass `extra_allowed_tools` when creating the child. |
| rc → new cloud, create + send | ✅ works | An RC session created a cloud session and messaged it. |
| cloud → rc, send message | ⛔ refused by default | Error `elevated_session_untrusted_device`. It works only after the user approves a trust card for that cloud session (`confirm_repository_trust`). |
| rc → a cloud session that was trust-elevated | ⛔ refused | Error: "the session you named needs proof of a trusted device". Once a cloud session is elevated so it can reach RC sessions, RC sessions can no longer message it. |
| cloud (elevated) → create rc session | ✅ works | |
| cloud → cloud | reported working by the user | not re-tested in this test run |
| cloud → project folder | ⛔ | A cloud session cannot read the served folder. `list_computer_folders` returned nothing on this account. |

## What the design takes from this

1. **The lead is an rc session.** It can create, message, read and interrupt both rc and cloud threads.
2. **rc threads push.** They message the lead directly (`send_message` to the lead's session id), once the tool is pre-allowed.
3. **Cloud threads never push to the lead.** Elevating them would cut the lead's line to them. They end every turn with a `<tw-status>` block and push code to git; the lead pulls with `get_session` / `list_events` and `git fetch`. If a cloud thread's turn fails, the platform tells its parent on its own (a child-session event), so failures still arrive without polling.
4. **Peers hand off directly.** rc↔rc works. A cloud thread can message another cloud thread. A cloud thread that needs an rc thread asks through its STATUS block, and the lead forwards the request.
5. **Completion signals.** RC threads send DONE/BLOCKED to the lead, which arrive as new turns. For cloud threads the lead uses a background timer and sweeps.

## Error strings and what to do

- `elevated_session_untrusted_device` when the **lead** messages an rc session: the lead itself is a cloud session. Open a new session in the Remote Control environment and run `/teamwork` there (setup.md §3). Do not elevate.
- "needs proof of a trusted device" when messaging a cloud thread: that thread was elevated. Leave it, create a fresh cloud thread with the same TASK and the old one's summary, mark the old one `archived` in the ledger after the user agrees.
- `Waiting on permission: <tool>` in a thread's `needs_action`: the user has to approve it in that thread, or add the allow rule (setup.md §2) for next time.
