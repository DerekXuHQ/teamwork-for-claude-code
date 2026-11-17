# One-time setup (claude.ai only, no terminal)

The user works only in claude.ai. Everything below is done by an RC session on the user's behalf, or by the user clicking in claude.ai. Never ask the user to open a terminal.

## §0 What has to exist

- **A Remote Control environment** serving a folder on the user's computer. It shows up in `list_environments` as `kind: bridge`, named `<computer>:<folder>:<id>`. Every RC session in it starts in that folder. Example: `my-laptop:workspace:ab12` serving `~/workspace`.
- **A cloud environment** (`kind: anthropic_cloud`, e.g. `Default`) for cloud threads.

Projects live as subfolders of the served folder (e.g. `~/workspace/shop/`), so one Remote Control environment can host several projects. RC threads `cd` into the project folder given in their protocol before working.

## §1 Install the skill

The skill must be in `<config dir>/skills/teamwork/` on the computer that runs the Remote Control server: RC sessions are Claude Code running there and load personal skills from that config dir. The config dir is `$CLAUDE_CONFIG_DIR` when the server was started with it (for example `~/.claude-b` for a second account), otherwise `~/.claude`. A skill in the wrong one shows up as "unknown command /teamwork". To find it from an RC session: `echo $CLAUDE_CONFIG_DIR`. Cloud sessions never load it; they get the protocol through `append_system_prompt`.

Have any RC session run (after the files are on the machine):

```
python3 <path>/teamwork/scripts/install.py --link --project <project folder> --served <served folder>          # dry run
python3 <path>/teamwork/scripts/install.py --apply --link --project <project folder> --served <served folder>  # writes
```

Use `--link` (recommended): the skill becomes a symlink to the clone, so every new session runs the clone's current checkout; upgrade with `git pull`, and whatever lands on the checked-out branch is live at once. Without `--link` the installer copies a frozen snapshot (without `.git`, `.teamwork`, `.claude` and other local state); to upgrade a copy, delete `<config dir>/skills/teamwork` and re-run, since the installer never overwrites an existing one.

Then start a **new** RC session (skills load at session start) and check `/teamwork` is listed.

## §2 Let RC threads report without stalling

In the test, an RC child stopped at "Waiting on permission: Send Message". In a later field test, even in `auto` mode, the user had to click "allow" 22 times. Nearly all were for three things: a temp folder made with `mktemp`, a `cd` outside the working folder, and files edited with heredoc scripts. The protocol's habits (Edit tool, `git -C`, `&&`) remove most of them; the settings below remove the rest that can be removed, but in `default` mode a Bash command that creates or writes files (`mktemp -d`, `git init`, `tar -x`) still asks, even in `/tmp`. `install.py` adds these to `<config dir>/settings.json` on that computer, which every RC session there reads:

```json
{ "permissions": {
  "allow": [
    "mcp__claude-code-remote__send_message",
    "mcp__claude-code-remote__get_session",
    "mcp__claude-code-remote__list_events",
    "mcp__claude-code-remote__list_sessions",
    "Bash(python3 \"<skill folder>/scripts/tw.py\" *)"
  ],
  "additionalDirectories": ["/tmp", "/private/tmp", "<$TMPDIR>", "<$TMPDIR resolved>", "<project>-worktrees"],
  "ask": [
    "Bash(gh repo delete:*)", "Bash(gh repo archive:*)", "Bash(gh repo rename:*)",
    "Bash(gh repo edit *--visibility*)",
    "Bash(gh api *-X DELETE*)", "Bash(gh api *--method DELETE*)", "Bash(gh api *visibility=*)", "..."
  ]
} }
```

- `allow`: the reporting calls and `tw.py` (the lead's ledger calls and the threads' privacy check before each push), so threads never stall on them.
- `additionalDirectories`: adds the scratch folders, and with `--project` the folder that holds the rc threads' worktrees, to every session's workspace. Reading and `cd` there no longer ask; editing there goes through in `acceptEdits` or `auto` but still asks in `default` mode. `/tmp` is shared by every session on the computer, so put nothing private there. The protocol tells every session to keep scratch work there, edit files with Edit instead of heredocs, and chain related steps into one command.
- `ask`: these prompt even in `auto` or `bypassPermissions`: `gh repo delete`, `archive` and `rename`; `gh repo edit` with `--visibility` or `--default-branch`; `gh api` with a DELETE method or a `visibility`, `private` or `default_branch` field. These are the user's own decisions (SKILL.md Rules). The rules match the command text in these spellings; other spellings (an alias, a script, lowercase `delete`, a request body from `--input`) get through, so they catch accidents, they are not a security boundary.
- No `git push` rules: threads force-push their own branches after a rebase as a matter of course, and that must not prompt. Deleting or force-pushing the base branch is blocked on the host instead. The user turns it on once per repository: GitHub > Settings > Rules (or Branches) > a rule for `main` with "Block force pushes" and "Restrict deletions". `install.py` prints this reminder.

`install.py` writes the full lists above; with `--no-allow` it writes only the ask rules.

These are user-level settings: every session on that computer reads them, teamwork or not. Cloud threads never read them; their prompts depend on the `permission_mode` the lead passes (below). The dry run (`install.py` without `--apply`) lists exactly what each key gains. Rules already there are kept; a settings file whose `permissions` is not an object stops the installer with a message instead of being overwritten.

### Permission mode for threads (optional)

Without a `permission_mode`, an rc thread gets the Remote Control server's default mode, and in field tests that meant a manual "allow" in claude.ai for nearly every Bash and Edit call. The lead therefore passes `permission_mode` to `create_session`: the user's choice from `tw.py prefs --set thread_permission_mode=<mode>` if set, otherwise the lead's own mode (a thread can never be more permissive than the lead). Options, from tightest to loosest:

- keep `default` and add allow rules for what threads routinely run (for example `Bash(git *)`, `Bash(python3 *)`, `Edit`) to `<config dir>/settings.json`;
- `acceptEdits`: file edits go through, shell commands still ask;
- `auto`: Claude Code decides which calls are safe and asks only for risky ones. Recommended for threads that do real work.

Changing the global default mode of the Remote Control server is the user's decision; the skill does not change it.

`create_session`, `interrupt_session`, `send_later` and background `sleep` (the lead's check-in timer) are pre-approved only for the lead while `/teamwork` runs (skill `allowed-tools`); archiving always asks. If the organisation sets the connector to "ask", allow rules are ignored and the user approves prompts in each thread.

## §2b Privacy guard (optional, recommended for public repos)

`.teamwork/PRIVATE.md` lists the user's private words and path globs; it and `.teamwork/private/` are gitignored by `tw.py init`. `tw.py guard` fails when any of them appears in the commits about to be pushed (messages, author and committer names and emails, diffs including binary files and merges, file names), in annotated tag messages, in the tree of the ref, in branch and tag names, or in the working tree (names and raw bytes). Text is matched case-insensitively in UTF-8, UTF-16, GB18030, Big5, Shift-JIS and Latin-1/CP1252, in both Unicode forms (NFC and NFD), and inside zip-based files (docx, xlsx, pptx, odt, jar, zip) and gzip. Tags are peeled to the end (nested tags, and tags that name a blob or tree directly); symlinks are checked by their target text; git replace objects are ignored, so what is scanned is what is sent. It also decodes common escapes and containers before matching: JSON/Python `\uXXXX`, RTF `\uN` (TextEdit), HTML/XML entities (`&#N;`, `&amp;`), URL `%XX`, XML with its tags removed (a word split across docx runs), PDF streams (FlateDecode, including text split by kerning), bz2 and xz. PDFs with custom font encodings and other image or binary formats are not read. This machine's home folder path (for example `/Users/<name>`) is always private, so an absolute path in a committed file fails the guard. Hits are printed fully masked. A missing `PRIVATE.md` fails closed; an existing but empty one passes.

List format: one entry per line; bullets, numbering, `- [ ]` boxes, `**bold**` and a trailing `# comment` are fine. A line `X, Y` (or `X/Y`, `X; Y`) adds both; `Name: X` adds both sides; quotes (`> X`), `~~X~~` and `<!-- X -->` count; `X - note`, `X: note` and `X (note)` add `X` (the whole line counts too). In a markdown table the first cell of each row counts (the header row is skipped). To keep an entry whole, quote it (`"Quixbar, Ltd"` or `` `a/b` ``): it is never split. A filesystem path (`/Users/x`, `~/x`, `C:\Users\x`) is always kept whole, so `/Users/x` does not make `x` private on its own; a line of paths (`/srv/x, /opt/y`) adds each path whole. `path: <glob>` lines are matched like gitignore and case-insensitively (hits print the path with the rule's own words masked): `notes.md` matches in any folder, `/secrets/` only at the root, `*.pem` and `**/*.pem` anywhere, `notes_*` any name starting with `notes_`. The glob is taken as written: only quotes or backticks around it (and `**bold**` around the whole rule) are removed.

`install.py --apply --project <project folder> --pre-push` installs a git `pre-push` hook (in the repo's common git dir, so every worktree uses it) that runs the guard on each push with the remote's name: a branch that is new on that remote is scanned for every commit the server does not advertise right now (`git ls-remote`; local remote-tracking refs are not trusted, since they go stale when a remote's URL changes), so a first push to a second or moved remote cannot carry old history unchecked. If the server cannot be asked, the whole pushed history is scanned. It never replaces someone else's pre-push hook. Git gives the hook the pushed refs on stdin only once, so when another tool's hook is there (Git LFS installs one), install.py prints lines that save stdin with `input=$(cat)` and pipe it to each command; a guard that gets no refs at all fails closed, except in the hook install.py writes (and the lines it prints), which pass `--stdin-owned`: there the guard is the only reader, so empty input means git had nothing to push ("Everything up-to-date"). An older teamwork hook is updated in place. If `core.hooksPath` points into the work tree and that folder is not ignored, install.py refuses: the hook holds absolute paths and the next `git add -A` would commit them.

Cloud threads push from their own container, where neither `PRIVATE.md` nor this hook exists. The lead gives each cloud TASK a check script (`tw.py guard --hash-list --kit`) holding the list as salted hashes (no home path; raw text only; it fails closed on git errors; it stops accidents, not someone guessing), and re-checks the branch with the real guard when it arrives (SKILL.md step 7).

## §3 Project hook and the lead

`install.py --project` adds a SessionStart hook to `<served folder>/.claude/settings.json` (RC sessions start in the served folder, so that is where the hook must live) and to `<project>/.claude/settings.json`. The hook command holds absolute paths, so when the folder is inside a git work tree and `.claude/settings.json` is not ignored, install.py writes `.claude/settings.local.json` instead and lists it in that clone's `.git/info/exclude`. Nothing it writes can then be committed. It runs `tw.py context`:

- inside a project folder: injects that project's board, instructions and memory (≤ 9 000 characters), at start, resume and after compaction;
- in the served folder: injects only a one-line index of the teamwork projects below it, nothing else.

To start the lead: in claude.ai, open a new session in the Remote Control environment and type `/teamwork new <project>`. The lead creates the project subfolder and `.teamwork/` there.

## §4 Isolation between threads editing the same repo

RC threads all start in the same served folder. Each RC thread creates its own git worktree for its branch before editing (the worker protocol says so). For non-git folders, give threads non-overlapping `owns`.

## §5 Cloud threads

They need a cloud environment and, to edit code, a GitHub repo the account can reach. The lead passes `source_url`, `source_revision` and `outcome_branch`. They cannot read the project folder: anything they need goes into the repo or the TASK message.

## §6 If something is refused

- A trust card appears in claude.ai the first time the lead messages or creates sessions: approve it for the lead only.
- Never approve a trust card for a cloud thread (transport.md explains why).
- RC threads stop if the Remote Control server on the computer stops or the computer sleeps; `get_session` then shows `connection_status: disconnected`. Tell the user; nothing else can restart it from claude.ai.
