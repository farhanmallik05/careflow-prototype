# External Integrations

**Analysis Date:** 2026-10-19

## APIs & External Services

**Inbound HTTP:**
- FastAPI exposes the local application from `careflow/api.py`: signed-session login/logout/current-user routes, case creation/read/transition/assignment routes, patient status, message-draft routes, worklist, and metrics. These are application endpoints, not third-party integrations.
- `Accept: text/html` selects Jinja2 rendering through `careflow/templates/*.html`; JSON remains the default for API clients. Static CSS is served from `careflow/static/` by FastAPI.
- No outbound API client, remote base URL, SDK for a cloud service, SMTP client, webhook client, or message-broker client is present in `careflow/`.
- `httpx==0.28.1` is installed for FastAPI's `TestClient` in `tests/`; it is not an application integration.

**Clinical/care systems:**
- No laboratory, screening-program, primary-care, specialist, EHR, scheduling, identity, or patient-notification provider is connected. Those parties are modeled as roles, users, pathway states, and synthetic IDs in `careflow/pathways/screening_to_care.yaml`, `careflow/persistence.py`, and `careflow/seed.py`.

**AI/message provider boundary:**
- `careflow/ai.py` defines `AIProvider`, deterministic `RuleBasedProvider`, and an optional `LLMProvider` seam. The default provider renders reviewed templates offline for `en`, `hi`, and `bn`; non-English drafts are marked `needs clinical-language review`.
- `CAREFLOW_MESSAGE_PROVIDER=llm` selects `LLMProvider`, but the normal constructor has no generator and raises `ProviderUnavailableError`; it does not call a network service or consume an API key. Tests can inject a callable through `LLMProvider`.

## Data Storage

**Databases:**
- SQLite - the only persistence backend, accessed with Python's standard-library `sqlite3` through `SQLiteStore` in `careflow/persistence.py`.
  - Connection: a filesystem path passed to `SQLiteStore`/`create_app`; defaults to `careflow.db`, while tests use temporary files or `:memory:`.
  - Client: synchronous `sqlite3`; there is no ORM, migration framework, external database service, or connection pool.
  - Schema: `users`, `cases`, `consents`, `evidence`, `approvals`, `audit_events`, `notification_outbox`, `message_drafts`, and `access_logs` are created by `SCHEMA` in `careflow/persistence.py`.
  - Integrity: foreign keys, uniqueness/check constraints, indexes, and transaction contexts are enabled by `SQLiteStore`.

**File Storage:**
- Local filesystem only - SQLite files such as `careflow.db`, pathway YAML, Jinja2 templates, and static CSS are read locally. No object store, document store, or upload service is configured.

**Caching:**
- None detected. Each `create_app` instance owns one in-process `SQLiteStore` connection and no Redis, Memcached, HTTP cache, or message broker is used.

## Authentication & Identity

**Auth Provider:**
- Custom/local signed demo sessions - `careflow/api.py` creates an `itsdangerous.URLSafeTimedSerializer` using `CAREFLOW_SESSION_SECRET` or `SESSION_SECRET`, then exposes JSON and click-through login routes.
  - Implementation: `POST /auth/login` and `/demo-login/{user_id}` accept an active seeded user ID, issue a timed `careflow_session` cookie with `HttpOnly` and `SameSite=Lax`, and reject unknown/inactive users. Protected routes load the active `User` from SQLite, apply patient/staff and pathway-role checks, and record successful access in `access_logs`.
  - Identity source: users are local records created by `careflow/seed.py` or `SQLiteStore.add_user`; there is no password proof, OAuth/OIDC/SAML provider, token exchange, or external directory.
  - Dependency: `itsdangerous==2.2.0` is a direct dependency in `requirements.txt` and `requirements.lock`.

## Monitoring & Observability

**Error Tracking:**
- None detected. `careflow/api.py` translates expected domain/persistence errors to HTTP responses, but no Sentry, OpenTelemetry, metrics exporter, or hosted error service is configured.

**Logs:**
- No application logging framework or structured logger is configured. `careflow/demo.py` and `careflow/seed.py` use `print`; Uvicorn supplies only its own server logs.
- Workflow audit history is persisted in `audit_events`, successful authenticated access in `access_logs`, and observed metrics are recomputed by `careflow/metrics.py`. These are local records, not log shipping or telemetry integrations.

## CI/CD & Deployment

**Hosting:**
- Local process or container only - `README.md` starts Uvicorn directly, and `Dockerfile` builds `python:3.12-slim`, installs `requirements.lock`, seeds a demo database during build, and listens on port 8000. No provider-specific hosting or infrastructure manifest is present.

**CI Pipeline:**
- None detected. `Makefile` provides `make test`, `make demo`, and `make seed-demo`; no GitHub Actions, registry workflow, release pipeline, or deployment automation is present.

## Environment Configuration

**Required/recognized env vars:**
- `CAREFLOW_SESSION_SECRET` or `SESSION_SECRET` - signing secret for the timed session cookie; if neither is set, `create_app` generates a process-local random secret, so a deployment must provide its own stable secret.
- `CAREFLOW_SESSION_MAX_AGE` or `SESSION_MAX_AGE` - optional positive session lifetime in seconds, defaulting to `3600` in `careflow/api.py`.
- `CAREFLOW_MESSAGE_PROVIDER` - optional provider selector, defaulting to offline `rule_based`; `llm` is an unconfigured boundary.
- No database URL, cloud credential, SMTP setting, webhook URL, or external service token is referenced by application code.

**Secrets location:**
- Environment variables are the intended location for session settings; `.env.example` exists as a template and `.env` is ignored by `.gitignore`. No deployment secret-manager integration is implemented, and no secret contents were read or added by this scan.

## Webhooks & Callbacks

**Incoming:**
- None. `careflow/api.py` has ordinary case-management and message-draft endpoints but no provider callback or webhook-signature route.

**Outgoing:**
- None. `notification_outbox` in `careflow/persistence.py` represents consent-gated queued records. `pending_notifications` and `mark_notification_sent` are repository helpers only; there is no worker or transport for email, SMS, push, webhook, or a care-system API.
- `careflow/seed.py` creates synthetic `case_attention` rows only after case-scoped consent exists. Message approval atomically changes `message_drafts` and inserts a queued `patient_message`; it does not send a message.

## Current Gaps and Milestone Assumptions

- Managed OIDC/SSO, password-free production identity, session revocation, CSRF protection, and deployment security policy are active requirements in `.planning/PROJECT.md`, not current integrations.
- Outbox statuses include `queued`, `processing`, `sent`, and `failed`, but no claim/retry worker or external delivery adapter is implemented. Approved drafts therefore remain local queued records.
- SQLite has no schema version/migrations, backup integration, retention service, or multi-process topology. The current deployment assumption is one local writable file and one process-local store.

---

*Integration audit: 2026-10-19*
