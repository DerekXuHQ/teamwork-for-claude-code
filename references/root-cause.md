# Root cause workflow reference (T67)

## Review files

`root start AREA` creates `.teamwork/roots/AREA-RCn.md`. A light review contains symptoms, the shared root, invariants, a scenario/test table and a plan. A full review also records related code locations, mature references, additional unreported failure scenarios, per-scenario current behavior, acceptance checks and compatibility with old data and versions. Fill every `<填>` placeholder. Every symptom S number must appear in the scenario table, and every scenario needs a test name. `root check AREA` enforces those checks.

## Default to root fix

At normal cost, use the user's standing rule verified by `TW memory --standing-list` as `root answer AREA A --user-said "能根治的一律根治"`, then proceed to spec and handoff. Ask once with `root ask` only for extreme cost (a full rewrite, multiple threads or clear scope expansion); A is recommended by default. Use B/C only if the user chooses.

Ask wording: “AREA has had N related failures; the shared root is ROOT. A: fix the cause (COST); B: patch now and record debt; C: patch first, then fix the cause. Recommended: A, because REASON. Reply A, B or C.” 中文同义，推荐默认 A。

## Specification

The generated specification copies the review conclusion, turns invariant bullets into numbered `L1`, `L2` requirements and lists every post-baseline request and user correction as a `C<n>` row. Map each C row to an existing L number. If a correction is rejected, the reason must contain `用户:` and the original words. `root spec AREA --check` reports any unmapped row.

## Threshold preferences

| Preference | Default | Meaning |
|---|---:|---|
| `root_mode` | on | Enable the dispatch gate and radar |
| `root_symptoms_at` | 2 | Bug/correction symptoms before pending |
| `root_rework_at` | 2 | Rework events before pending |
| `root_full_at` | 4 | Symptoms before a full review |
| `root_debt_at` | 2 | New debts before asking again |
| `root_fresh_at` | 3 | Thread corrections suggesting a fresh session |

## Failure modes and responses

| Failure | Response |
|---|---|
| False positive | Start with the light review; dismiss one request or reset the area's baseline |
| Wrong area | Register explicit paths and aliases; inspect unassigned items in `wrapup`; use `root move` or `area merge` |
| Too many questions | Ask once with all corrections in the specification; a default authorizes only the patch half |
| A correction is missing | `root spec --check` requires every C row to map to an L row or a reason containing the user's words |
| New thread loses details | Include the checked specification and original-words appendix; ask a screen question about each L line; do not archive the old session before ACK |
| Urgent dispatch is blocked | Use `--hotfix`; the one-time dispatch records debt. User wording that says to fix urgently passes without the reminder |
| Old state has no area fields | Missing fields mean empty; counters begin at the area baseline; `root scan --backfill` only previews |
| Trigger phrase false positive | The trigger list is deliberately narrow; dismiss the false positive |

## Commands

`area add backend --paths 'api/**' --aka service`

`root start backend`

`root check backend`

`root ask backend --why "shared cause" --cost "two days" --root "retries lose ownership"`

`root answer backend A --user-said "fix the retry owner"`

`root spec backend --check`

`root handoff backend --to api --task T12`
