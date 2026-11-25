# Test brief

Use this template for test work. The primary outcome is the first acceptance check.

## 1. Primary outcome check (real run)

- **Entry point:** exact command, URL, app launch path, or device steps used by a user.
- **Data:** real or representative input and where it came from.
- **Expected result:** the user-visible output and expected count or content.
- **Evidence:** attach a real screenshot or output file that exists, or paste numeric command output. State whether the outcome was seen, not seen, or pending real access. Include the command and raw output where applicable.
- If this check fails, stop. Fix the primary outcome before checking details.

## 2. Details

Only after section 1 passes, check sorting, edge cases, styling, accessibility, and other detailed acceptance criteria. List the exact commands and results.

## 3. Report

Start with the primary outcome result and its evidence. If it did not pass, report the failure and stop; do not report detail checks as a pass. Then report the detail checks, test command and result, review base SHA, and anything not tested.

## Primary outcome examples

- **App feature:** launch the real app, open the feature with representative data, and attach a real device or browser screenshot; state the visible item count.
- **Script or CLI:** run the installed or built command with representative input; include the exact command and raw output, including counts when relevant.
- **Document or research:** provide links that open and show that each user question has an answer; quote the relevant short passage and identify its source.
- **Deployment:** open the real URL, record its response, and attach a screenshot of the key page.
- **Data migration:** query through the real application entry point after migration and compare the record count with the source.
- **When the user's requested outcome is a detail**, such as alignment, that detail is the primary outcome; verify it in a real screenshot.
- **Visual work:** accept only a real screenshot from a real device or browser. DOM assertions do not establish visual appearance.
