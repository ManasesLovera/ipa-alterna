# T16 — Auth (API keys + JWT) and webhooks

- **Wave:** 4 (parallel with T13, T14, T15)
- **Depends on:** T02 (`users`, `api_keys`, `webhooks` tables), T07, T03
- **Owns:** `src/ipa/api/auth.py`, `src/ipa/domain/users.py`,
  `src/ipa/domain/webhooks.py`, `src/ipa/api/routers/auth.py`,
  `src/ipa/api/routers/webhooks.py`

## Goal

Two authentication paths for two audiences, and outbound webhooks so integrating
applications do not have to poll.

Single tenant, so there is no tenant scoping — but there are still users with roles,
and machine clients with scoped keys.

## Authentication

### Machine clients — `X-API-Key`

- Key format `ipa_<env>_<32 url-safe random chars>`. Shown **once** at creation.
- Store only `sha256(key)` in `api_keys.key_hash`, plus an 8-char `prefix` for
  display. Never store or log the raw key.
- Scopes: `documents:read`, `documents:write`, `tags:read`, `tags:write`,
  `review:read`, `review:write`, `search:read`, `mcp`. Enforced per endpoint.
- Update `last_used_at` at most once per minute per key (write-behind through Redis,
  not on every request).
- Rate limit per key with a Redis sliding window; `429` with `Retry-After` and
  `X-RateLimit-*` headers.

### UI users — JWT

- `POST /v1/auth/login` with email + password → short-lived access token (15 min) and
  a rotating refresh token (7 days) in an `HttpOnly`, `SameSite=Lax`, `Secure` cookie.
- Passwords hashed with Argon2id (`argon2-cffi`). Never bcrypt-with-defaults, never
  a bare SHA.
- Refresh rotation with reuse detection: a refresh token may be used once; presenting
  a used token revokes the whole family and forces re-login.
- Roles: `admin` (everything), `reviewer` (review + read), `viewer` (read only).

### The unified dependency

```python
async def require_auth(scopes: list[str] | None = None) -> Principal
```

`Principal` carries `kind` (`user` | `api_key`), `id`, `role`, `scopes`.
**T01 shipped a permissive stub of this so other tasks could import it — replace the
stub, keep the import path identical.** After this task lands, verify every existing
router declares its scope requirement; add them where missing (this is the one case
where you may touch other tasks' router files, and only to add the dependency).

Bind the principal into the log context and as OTel span attributes so every request
is attributable.

`/healthz`, `/readyz`, `/metrics`, and the OpenAPI schema stay unauthenticated.

## Webhooks

### Events

`document.created`, `document.step_completed`, `document.step_failed`,
`document.pending_review`, `document.validated`, `document.completed`,
`document.failed`, `document.quarantined`.

### Delivery

- `POST` JSON to the registered URL with headers:
  - `X-IPA-Event` — event type
  - `X-IPA-Delivery` — delivery UUID
  - `X-IPA-Timestamp` — unix seconds
  - `X-IPA-Signature` — `sha256=<hmac(secret, timestamp + "." + body)>`
- Signing over timestamp **and** body prevents replay; document that receivers must
  reject deliveries older than five minutes.
- Payload is **thin**: event type, `document_id`, `status`, `occurred_at`, and a
  `self` URL. Receivers fetch the full resource. This keeps payloads small, avoids
  leaking extracted content to a misconfigured endpoint, and means a redelivery is
  never stale.
- Delivery runs as a Celery task on its own queue. Retries: 6 attempts with
  exponential backoff (10 s → 1 h). Every attempt writes a `webhook_deliveries` row.
- Auto-disable a webhook after 20 consecutive failures and emit a
  `webhook.disabled` log + metric.
- **SSRF protection:** reject registration of URLs resolving to private, loopback,
  link-local, or metadata addresses (`169.254.169.254`), and re-check at delivery time
  — DNS can change between registration and delivery. HTTPS required outside `local`.
- 5-second connect / 10-second total timeout. Follow no redirects.

### Emission

Emit from a single place — a `WebhookService.emit(event, document_id)` called by the
orchestrator's status-transition code and by T13. Do not scatter `emit` calls through
step handlers.

## Endpoints

| Method | Path | Auth |
| --- | --- | --- |
| `POST` | `/v1/auth/login` | public |
| `POST` | `/v1/auth/refresh` | cookie |
| `POST` | `/v1/auth/logout` | user |
| `GET` | `/v1/auth/me` | any |
| `POST` | `/v1/auth/change-password` | user |
| `GET` `POST` | `/v1/users` | admin |
| `PATCH` `DELETE` | `/v1/users/{id}` | admin |
| `GET` `POST` | `/v1/api-keys` | admin |
| `DELETE` | `/v1/api-keys/{id}` | admin (revoke, never hard-delete) |
| `GET` `POST` | `/v1/webhooks` | admin |
| `PATCH` `DELETE` | `/v1/webhooks/{id}` | admin |
| `POST` | `/v1/webhooks/{id}/test` | admin — sends a `ping` event |
| `GET` | `/v1/webhooks/{id}/deliveries` | admin |
| `POST` | `/v1/webhooks/deliveries/{id}/redeliver` | admin |

## Bootstrap

`make seed` creates an initial admin from `IPA_BOOTSTRAP_ADMIN_EMAIL` /
`IPA_BOOTSTRAP_ADMIN_PASSWORD`, and refuses to run in a non-`local` env if the
password is the default. Log the created admin's email, never the password.

## Acceptance criteria

- Raw API key is returned exactly once; the DB holds only a hash; the key
  authenticates; a revoked key returns 401.
- Missing scope returns 403 with `code=insufficient_scope`, listing the required scope.
- Rate limit trips at the configured threshold with `Retry-After`.
- Login → access + refresh; refresh rotates; replaying a used refresh token revokes
  the family and returns 401.
- Argon2id verified in use (assert the hash prefix).
- Webhook signature verifies against a hand-computed HMAC over `timestamp.body`.
- Registering `http://169.254.169.254/` is rejected; a public URL that later resolves
  to a private IP is rejected at delivery time.
- Failing endpoint retries 6 times with growing backoff, then the delivery row is
  `failed`; 20 consecutive failures disable the webhook.
- `POST /webhooks/{id}/test` delivers a `ping`.
- `/healthz` remains reachable without credentials.
