# Field test plan: can a screened session forget its screen? (L7)

Status: not run yet. This page lists the steps and what to record. It promises nothing about the outcome.

The opening screen (`TW screen ask`) puts practice questions into a new session's context. Today the lead removes
their effect with a clear-out message (`TW screen clear-msg`). This test checks whether a real context reset can
remove the questions completely. If it can, the order becomes "screen, reset, real work".

## Setup

- One throwaway Remote Control session, opened by the lead with the normal worker prompt and permission mode
  `auto`. Do not use a seat that holds real work.
- A marker the session cannot guess, for example `zebra-7431`, used only in this test.

## Steps

| # | Lead does | Record |
|---|---|---|
| 1 | `send_message`: "Practice only. Remember the word zebra-7431. Reply with OK." | The reply; the time |
| 2 | `get_session`: note `status` and the number of events (`list_events`, `limit` 100) | Event count before the reset |
| 3 | `send_message` with only `/clear` | Did the session treat it as a command or as text? Paste the reply |
| 4 | If step 3 was text: try `/compact` in the same way, then stop (no other tricks) | The reply |
| 5 | `send_message`: "What word did I ask you to remember? If you do not know, say so." | Does it say zebra-7431? |
| 6 | `send_message`: "Quote the first line of your thread protocol and name your thread." | Does the protocol survive (it lives in the system prompt)? |
| 7 | `list_events` again | Does the transcript still hold step 1 (it should: a reset only changes what the model sees) |

Repeat steps 1 to 7 once from the phone app and once from the web if both are at hand. Write which surface each run used.

## How to read the result

- Step 5 says "I do not know" and step 6 quotes the protocol: the reset works and keeps the protocol. Propose
  "screen, reset, real work" to the user. Keep the clear-out message as the fallback.
- Step 5 still knows the word, or step 3 or 4 came back as plain text: a reset cannot be sent this way. Keep the
  clear-out message.
- Step 6 fails: the reset also dropped the protocol. Do not use it.

Log each run as a row in a dated field-test file and link it from docs/field-tests/issues.md.
