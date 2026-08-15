# Scrumforge — Task Management Guide

**All task management for this repository goes through the `scrumforge` CLI.** The
board is the single source of truth for *status*; `docs/tasks/` is the single source
of truth for *scope*. Do not track work in ad-hoc TODO lists, scratch files, or
issue trackers, and do not rely on memory of what is left to do.

The board lives in `.scrumforge.db` at the repo root and is found automatically from
anywhere inside the repository. It is **gitignored** — it is local orchestration
state, not a reviewable artifact, and committing it would conflict on every
concurrent task transition.

## Commands

```bash
scrumforge tasks                               # list every task: id, status, assignee
scrumforge show <id>                           # full detail of one task
scrumforge backlog "<title> | <description>"   # add a task
scrumforge request "<text>"                    # ask the scrum master to plan + assign
scrumforge run <id>                            # send the task to its assignee
scrumforge rework <id>                         # developer addresses review feedback
scrumforge repl                                # interactive REPL (humans)
scrumforge                                     # interactive TUI (humans)
```

## Lifecycle

```text
backlog -> assigned -> in-progress -> in-review -> done
                             ^            |
                             `- changes-requested
```

`scrumforge run <id>` is called **twice** per cycle:

1. First call — the developer agent implements the task and opens a PR.
2. Second call — the reviewer agent approves and merges, or requests changes.

On `changes-requested`, run `scrumforge rework <id>` (developer addresses the
feedback), then `scrumforge run <id>` again to re-review.

## Board ↔ spec mapping

Board `#N` corresponds to `T0N` in `docs/tasks/`. Task `#7` is
`docs/tasks/T07-ingestion-api.md`.

**Always read the spec file before implementing.** The board description is a
pointer, not the requirement — it is deliberately short and cannot carry the
acceptance criteria.

## Operational gotchas

These are behaviours observed in practice. Each one has bitten this project at
least once; none is documented in `scrumforge help`.

### `show` mutates state

`scrumforge show <id>` transitions the task to `in-progress` and provisions a git
worktree as a side effect. It is **not** a read-only inspection command. Use
`scrumforge tasks` when you only want to see status.

### `run` blocks for a long time

`scrumforge run <id>` occupies the terminal until the agent finishes — often many
minutes. Background it and capture the log:

```bash
nohup scrumforge run 4 > /tmp/sf-task4.log 2>&1 &
```

### Branch names are not conventional

Scrumforge creates branches named `scrumforge/task-N`. This repository requires
[conventional prefixes](#conventional-branch-names) and has a pre-push hook that
enforces them. **Rename the branch before the work is reviewed**, which also keeps
the PR title, branch, and board entry legible:

```bash
gh api -X POST repos/<owner>/<repo>/branches/scrumforge%2Ftask-3/rename \
  -f new_name='feat/t03-storage-adapters'
```

> ⚠️ **Renaming a branch closes its open PR.** Rename *first*, then open the PR — or
> be ready to recreate the PR afterwards and reference the closed one.

### It can switch the main checkout's branch

Scrumforge may leave `/home/mlovera/dev/ipa` checked out on a task branch rather
than `main`. A `git log` run there will then show that task's commits and can easily
be misread as "this landed on main".

**Before drawing any conclusion about what is on `main`, verify explicitly:**

```bash
git branch --show-current
git rev-parse --short main origin/main     # these should match
```

Never rewrite `main` history on the basis of a `git log` you did not first confirm
was actually run against `main`.

### There is no "close task" verb

Nothing in the CLI moves a task to `done` manually. A task delivered outside the
scrumforge flow will sit at `in-progress` forever. Record the real state in the task
description (for example "ALREADY DELIVERED in PR #1 — do not re-implement") so the
board does not mislead the next agent.

### Agent processes can exit silently

A `scrumforge run` process can exit having produced commits on a branch but without
opening a PR. After every run, verify rather than assume:

```bash
pgrep -af "scrumforge run"                 # still working?
git ls-remote --heads origin               # what branches exist?
gh pr list --state all --limit 10          # did a PR actually open?
```

If work exists on a branch with no PR, open one manually — do not re-run the task
and duplicate the work.

## Conventional branch names

Branches must start with one of: `feat`, `fix`, `chore`, `hotfix`, `docs`,
`refactor`, `test`, `style`, `perf`, `build`, `ci`, `revert`.

For task work, use `feat/t<NN>-<slug>` — for example `feat/t07-ingestion-api`. A
pre-push hook rejects anything else.

## Rules

1. **Check the board first.** Run `scrumforge tasks` at the start of any work
   session, before deciding what to do next.
2. **One task, one PR.** Never bundle two board tasks into a single pull request —
   it destroys the review signal and makes rollback all-or-nothing.
3. **Never mark work done that you have not verified.** A task is done only when its
   PR is merged and CI is green on `main`.
4. **New work goes on the board before it goes in the code.** If you discover
   necessary work that is not on the board, add it with `scrumforge backlog` rather
   than quietly widening the scope of the task in flight.
5. **Respect the ownership map** in `docs/01-conventions.md`. Each task owns a
   disjoint set of paths so parallel agents merge cleanly. If you need a change in
   another task's files, leave a `TODO(T##)` and work around it via the documented
   contract.
6. **`main` is protected.** Pull requests are required and the `CI gate` check must
   pass. This is enforced by the platform, not by convention — direct pushes are
   rejected.
