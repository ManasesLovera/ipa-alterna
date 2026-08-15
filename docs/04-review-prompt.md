# Code Review Prompt Template

Reusable prompt for the reviewer agent on a task PR. Substitute the four
placeholders and paste it as the agent's instruction.

| Placeholder | Example |
| --- | --- |
| `<PR>` | `3` |
| `<BRANCH>` | `feat/t02-database` |
| `<SPEC>` | `docs/tasks/T02-database.md` |
| `<DOWNSTREAM>` | `docs/tasks/T06-...`, `T07-...`, `T08-...` |

## Template

> Review pull request **#`<PR>`** on `ManasesLovera/ipa-alterna` and post a decisive
> review. Do not stop until the review is posted with `gh pr review`.
>
> **Context.** Repo: `/home/mlovera/dev/ipa` (on `main`). PR branch: ``<BRANCH>``.
> Spec it implements: ``<SPEC>``. Binding rules: `docs/01-conventions.md`
> (especially the file-ownership map). System design: `docs/00-architecture.md`.
>
> **How to work.**
>
> 1. `cd /home/mlovera/dev/ipa && git fetch origin`
> 2. `git diff origin/main...origin/<BRANCH>` for the full diff; `gh pr view <PR>`
>    for the description.
> 3. Read ``<SPEC>`` **before** the diff, so you review against the actual
>    requirement rather than generic taste.
> 4. To run anything, use a scratch worktree:
>    `git worktree add /tmp/review-<PR> origin/<BRANCH>`. Never modify or push to
>    the PR branch. Remove the worktree when done.
>
> **Weight these heavily.**
>
> - **Spec conformance.** Every deliverable in the spec's Deliverables section
>   present? Every acceptance criterion actually satisfiable? Name any that are not.
> - **Downstream sufficiency.** Read `<DOWNSTREAM>` and check that what this PR
>   exposes is enough for them. A missing method or mistyped field here forces a
>   refactor across several tasks — this is the most expensive class of defect.
> - **Ownership boundaries.** Does the PR modify files owned by another task per
>   `docs/01-conventions.md`? That causes merge conflicts between parallel agents.
> - **Idempotency and retry safety.** Anything on the pipeline path must be safe to
>   run twice. Re-running must not duplicate rows, leak blobs, or double-charge a
>   provider.
> - **Correctness traps** specific to this task — reproduce them, do not assume.
> - **Security.** No committed secrets, no credentials in logs, no SSRF, no path
>   traversal, no SQL built by string interpolation.
> - **Conventions.** Docstrings on every module and public function, type hints,
>   `os.environ` only in `core/config.py`, structlog never `print`.
>
> **Do not.** Nitpick formatting, import order, or line length — ruff enforces those
> and CI is green. Propose architectural redesigns; the architecture is settled and
> documented. Demand more test coverage for its own sake: this project deliberately
> excludes model-accuracy evaluation and tests plumbing only.
>
> **Verify claims by reproduction.** If you assert something is broken, run it and
> paste the evidence. A finding without a repro is a suggestion, not a defect.
>
> **Output.** Post with `gh pr review <PR>` using `--approve` or `--request-changes`
> (fall back to `--comment` with an explicit verdict line if GitHub blocks reviewing
> your own PR). The body must:
>
> - open with a one-line verdict plus the single most important finding
> - list findings worst-first, each with `file:line`, what breaks, and the concrete fix
> - separate blocking issues from optional suggestions
> - state explicitly whether downstream tasks can safely build on this
>
> Then report back: the verdict, the blocking findings, and whether the tasks that
> depend on this one can start.

## Notes

- Reviewing your own PR is blocked by GitHub. When the PR author and reviewer are
  the same account, `--approve` / `--request-changes` fail; use `--comment` with the
  verdict stated in the first line.
- Run one reviewer per PR. A single agent reviewing two branches conflates the two
  diffs and produces vague findings.
