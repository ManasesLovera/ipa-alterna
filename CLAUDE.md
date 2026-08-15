# IPA — Project Instructions

> This file is the agent guide for **every** AI tool used on this repository.
> `AGENTS.md` (read by opencode and other AGENTS.md-aware tools) is a symlink to
> this file, so there is exactly one copy and it cannot drift. Edit `CLAUDE.md`;
> never replace the symlink with a second copy.

Intelligent Process Automation platform for documents. Read
[`docs/00-architecture.md`](docs/00-architecture.md) and
[`docs/01-conventions.md`](docs/01-conventions.md) before writing any code.

## Task management — always use scrumforge

**All task management for this repository goes through the `scrumforge` CLI.**
Do not track work in ad-hoc TODO lists, scratch files, or issue trackers, and do
not rely on memory of what is left to do. The board is the single source of truth
for status; `docs/tasks/` is the single source of truth for scope.

**Read [`docs/03-scrumforge.md`](docs/03-scrumforge.md) before using it.** It
carries the full command reference, the lifecycle, and the operational gotchas —
several of which are surprising and none of which are in `scrumforge help`.

```bash
scrumforge tasks                               # list every task, status, assignee
scrumforge show <id>                           # detail — WARNING: mutates state
scrumforge backlog "<title> | <description>"   # add a task
scrumforge run <id>                            # send to assignee (long-running)
scrumforge rework <id>                         # developer addresses review feedback
```

```text
backlog -> assigned -> in-progress -> in-review -> done
                             ^            |
                             `- changes-requested
```

`scrumforge run <id>` is called **twice** per cycle: once for the developer to
implement and open a PR, and again for the reviewer to approve and merge or
request changes. On `changes-requested`, call `scrumforge rework <id>`, then
`scrumforge run <id>` again.

### The four traps

1. **`show` is not read-only** — it moves the task to `in-progress` and creates a
   worktree. Use `scrumforge tasks` to look without touching.
2. **Branches come out as `scrumforge/task-N`**, which the pre-push hook rejects.
   Rename to `feat/t<NN>-<slug>` *before* opening the PR — renaming afterwards
   closes it.
3. **It can leave the main checkout on a task branch.** Always run
   `git branch --show-current` before concluding anything about `main`.
4. **A run can exit without opening a PR.** Verify with `gh pr list` and
   `git ls-remote --heads origin`; open the PR by hand rather than re-running.

### Rules

1. **Check the board first.** Run `scrumforge tasks` at the start of any work
   session, before deciding what to do next.
2. **One task, one PR.** Never bundle two board tasks into a single pull request —
   it destroys the review signal and makes rollback all-or-nothing.
3. **Never mark work done that you have not verified.** A task moves to `done`
   only when its PR is merged and CI is green on `main`.
4. **New work goes on the board before it goes in the code.** If you discover
   necessary work that is not on the board, add it with `scrumforge backlog`
   rather than quietly widening the scope of the task in flight.
5. **Board IDs match task specs:** board `#N` corresponds to `T0N` in
   `docs/tasks/`. Task `#7` is `docs/tasks/T07-ingestion-api.md`. Always read the
   spec file before implementing — the board description is a pointer, not the
   requirement.
6. **Respect the ownership map** in `docs/01-conventions.md`. Each task owns a
   disjoint set of paths so parallel agents merge cleanly. If you need a change in
   another task's files, leave a `TODO(T##)` and work around it via the documented
   contract.

## Working agreements

- **Worktrees live outside the repo tree**, at
  `~/dev/worktrees/ipa/<branch-name>`. Never create a worktree inside the
  checkout.
- **Conventional Commits**, imperative mood, lowercase after the colon, no
  trailing period, subject under 72 characters.
- **CI must be green before merge.** The pipeline in `.github/workflows/ci.yml`
  runs actionlint, markdownlint, mermaid validation, ruff, mypy, unit tests,
  alembic up/down, integration tests, frontend build, docker build, and gitleaks.
- **Markdown must pass markdownlint.** CI runs markdownlint on all `*.md`
  files. Follow the rules it enforces — notably MD022/MD032 (blank lines
  around headings and lists, including lists inside blockquotes), MD040
  (language on every fenced code block), MD024 (no duplicate headings), and
  MD060 (spaced pipes in table separators). When in doubt, run
  `npx markdownlint-cli2 <file>` before committing.
- **Docstrings are required** on every module and public function — this is a
  stated project requirement, not a style preference.
- **`os.environ` is read only in `src/ipa/core/config.py`.** Everywhere else uses
  `get_settings()`.
- **Never commit secrets.** `NVIDIA_API_KEY` and the model IDs stay blank in
  `.env.example`.

## Testing scope

This project deliberately **excludes model-accuracy evaluation**. Do not build
golden sets or measure OCR, classification, or extraction quality. Tests cover
plumbing: uploads work, state advances, retries behave, adapters persist, APIs
honour their contracts. Providers are faked so results are deterministic.

## Provider

OCR and LLM inference run on **NVIDIA NIM**, which exposes an OpenAI-compatible
API. Use the `openai` SDK pointed at `NVIDIA_BASE_URL`. Model IDs are
configuration, never constants — they come from the environment and are blank by
default.
