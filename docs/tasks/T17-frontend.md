# T17 — Next.js frontend

- **Wave:** 5 (parallel with T18); may be **started** after wave 2 once the OpenAPI
  schema is stable
- **Depends on:** T06, T07, T08, T13, T14, T16
- **Owns:** `apps/web/`

## Goal

The operator UI: upload documents, configure tags and their extraction schemas,
monitor the pipeline, and review extractions side by side with the source page.

## Stack

- Next.js 15, App Router, TypeScript strict.
- Tailwind CSS + shadcn/ui.
- TanStack Query for server state; no Redux.
- **Generate the API client from the OpenAPI schema** (`openapi-typescript` +
  `openapi-fetch`). Wire `make api-types` to regenerate. Do not hand-write types that
  duplicate the backend — they will drift.
- `react-hook-form` + `zod` for forms.
- `next-auth`-style session handling against T16's JWT cookies, or a thin custom
  provider — either is fine, but the refresh-rotation flow must be handled centrally
  in a fetch interceptor.

## Pages

### `/login`

Email + password. Redirect to `/documents` on success.

### `/documents`

The main list. Server-paginated table: thumbnail, filename, tag, status badge,
confidence bar, created date. Filters for status, tag, needs-review, date range, and
a text search. Bulk selection with bulk approve for admins.

Poll the list every 5 s while any visible document is in a non-terminal status; stop
polling when everything is terminal. Do not poll blindly forever.

### `/documents/upload`

Drag-and-drop multi-file upload with per-file progress, optional tag pre-selection,
and inline results (including "already existed — deduplicated", which must be shown
as a neutral outcome, not an error). Generate an `Idempotency-Key` per file client-side.

### `/documents/[id]`

Split view:

- **Left:** page viewer with thumbnail strip, zoom, and page navigation, backed by the
  presigned image URLs.
- **Right:** tabs — *Extraction* (field table with value, confidence, evidence
  snippet, validation errors), *Text* (per-page OCR output), *Pipeline* (step timeline
  with status, attempts, duration, error detail, and a per-step **Reprocess from
  here** button), *Events* (audit log), *Versions* (extraction history with a diff
  between any two versions).
- Clicking a field's evidence jumps the viewer to that field's page. This is the
  single most useful interaction in the product — make sure the `page` field from the
  extraction actually drives it.

### `/review`

The review queue, defaulted to worst-confidence-first. Keyboard-driven:
`j`/`k` to move between fields, `Enter` to edit, `a` to approve, `r` to reject,
`n` for the next document. Reviewers process hundreds of documents; every extra click
costs real time.

Per-field confidence is colour-coded, and fields below the tag threshold are
highlighted and focused first. Submitting sends the extraction version the reviewer
loaded; on `409 stale_extraction`, show a clear "this document changed" banner with a
reload action rather than silently discarding their edits.

### `/tags`

Tag list with document counts.

### `/tags/[id]`

The schema builder — the heart of the configuration UX:

- Drag-to-reorder field list.
- Per-field editor: key, label, type, required, description, and type-specific
  constraints (enum values, regex, min/max, array item type).
- **Auto-approval control:** a clearly-labelled toggle that is **off by default**,
  with helper text reading "All documents with this tag require human validation."
  Turning it on reveals a threshold slider (0–1) and a plain-language preview:
  "Documents scoring above 0.85 will be approved automatically; everything else goes
  to the review queue." Make the default posture obvious — a user must not enable
  auto-approval by accident.
- Live JSON Schema preview via `POST /v1/tags/schema/preview`.
- Save sends the whole field list via `PUT /fields` so a multi-field edit produces one
  version bump.
- Version history with snapshot viewing.

### `/search`

Search box with mode toggle (hybrid/dense/sparse), filters, and grouped results
showing snippet, page thumbnail, and a link into the document viewer. A second tab
for the RAG question box, rendering the answer with clickable citation chips that
open the cited page.

### `/pipeline`

Operational dashboard: counts by status and step, queue depths, oldest pending item,
recent failures, dead-letter list with replay buttons.

### `/settings`

Users, API keys (raw key shown once in a copy-to-clipboard modal with an explicit
"you will not see this again" warning), and webhooks with delivery history and a test
button.

## Cross-cutting requirements

- **Accessible:** semantic HTML, keyboard reachable, visible focus rings, WCAG AA
  contrast in both themes. The review screen must be fully operable without a mouse.
- **Dark mode** via `prefers-color-scheme` plus a manual toggle.
- **Error states everywhere:** loading skeletons, empty states with a next action, and
  RFC 7807 errors surfaced with their `detail` — never a bare "Something went wrong".
- **Never store the access token in `localStorage`.** Cookies only.
- Dockerfile for `apps/web` and a `web` compose service.

## Acceptance criteria

- `make up` then `localhost:3000` → login → upload a PDF → watch it move through the
  pipeline → review it → approve it, with no console errors.
- Generated API types compile; `make api-types` reproduces them.
- Tag creation with three fields produces exactly one version bump.
- Auto-approval toggle defaults to off for a new tag and the helper text states the
  default policy.
- Review screen is fully keyboard operable.
- Stale-version submit shows the reload banner instead of losing edits.
- Deduplicated upload is presented as a neutral, explained outcome.
- Lighthouse accessibility ≥ 90 on `/documents` and `/review`.
