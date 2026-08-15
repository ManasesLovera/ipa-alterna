# T19 — Generated documentation + mermaid diagrams

- **Wave:** 6 (final)
- **Depends on:** everything
- **Owns:** `docs/` (except the task files), `README.md`, module docstring audit

## Goal

Documentation that describes the system as built, not as planned. Run this last, and
verify every claim against the code.

## Deliverables

### `README.md`

Quickstart, architecture summary, the component mermaid diagram, prerequisites, the
`.env` walkthrough (especially the NVIDIA model IDs the operator must fill in),
`make up` → `make migrate` → `make seed` → open `localhost:3000`, and a troubleshooting
section.

### `docs/00-architecture.md`

Update to match reality. Diagrams to include or refresh:

1. **Component diagram** — services, stores, providers, data flow.
2. **Pipeline state machine** — actual states and transitions as implemented.
3. **Sequence diagram** — upload through completion, showing every store touched.
4. **ER diagram** (`erDiagram`) — every PostgreSQL table and relationship.
5. **Reprocessing flow** — what gets reset and deleted per `from_step`.
6. **RAG/MCP flow** — agent query → MCP tool → search service → stores → cited answer.

Every diagram must be valid mermaid that renders on GitHub. Verify by viewing the
pushed file, not by assuming.

### `docs/api.md`

Endpoint reference generated from the OpenAPI schema, plus hand-written narrative for
the flows that need it: idempotent upload, the 200-vs-202 distinction, reprocessing
semantics, webhook signature verification (with a worked example in Python and Node),
and pagination.

### `docs/configuration.md`

Every environment variable: name, purpose, default, whether required, and what breaks
if it is wrong. Generated from the `Settings` model where possible so it cannot drift.

### `docs/operations.md`

Runbook: reading the pipeline dashboard, interpreting a stuck document, replaying dead
letters, rotating API keys, rotating the NVIDIA key, backing up (Postgres dump + Mongo
dump + MinIO mirror), restoring, and the "rebuild everything from blobs" procedure.
Include the observability guide — which spans exist, which metrics matter, and the
three alerts worth setting.

### `docs/tags-and-schemas.md`

The user-facing guide to designing a tag: choosing field types, writing field
descriptions that improve extraction, when to set an auto-approval threshold and when
not to, and how versioning affects historical documents.

### `docs/mcp.md`

Written in T15; verify and expand with worked examples.

### Docstring audit

The project requires documentation on all code sections and modules. Sweep the
codebase and fix gaps:

- Every module has a docstring stating its responsibility.
- Every public function/class documents args, returns, and raises.
- Add a lint gate: `ruff` with `D` rules (pydocstyle) enabled for `src/ipa/`,
  configured to `google` convention, with `D100`–`D107` enforced. Land the config and
  fix the resulting failures.

### `CONTRIBUTING.md`

Repo layout, ownership map, how to add a pipeline step, how to add a tag field type,
migration conventions, and the test strategy.

## Acceptance criteria

- Every mermaid block renders on GitHub (verified visually on the pushed branch).
- A fresh clone can go from zero to a processed document following only `README.md`.
- No documented endpoint, env var, or table is absent from the code, and none is
  missing from the docs — verify by diffing against the OpenAPI schema and the
  `Settings` model programmatically in a test.
- `ruff` with `D` rules passes.
- No TODO or placeholder text remains in `docs/`.
