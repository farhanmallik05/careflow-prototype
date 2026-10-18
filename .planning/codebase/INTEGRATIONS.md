# External Integrations

**Analysis Date:** 2026-10-19

## APIs & External Services

**HTTP API:**
- FastAPI exposes the local CareFlow service from `careflow/api.py` with `POST /auth/login`, `POST /auth/logout`, `GET /auth/current-user`, `POST /cases`, transition and assignment routes, `GET /cases/{case_id}`, `GET /worklist`, and `GET /metrics`; these are inbound application routes, not integrations with a third-party API.
- No outbound API client, remote base URL, webhook client, SDK for a cloud service, SMTP client, or message-broker client is detected in `careflow/`.
- `httpx==0.28.1` appears in `requirements.txt` and `requirements.lock` to support FastAPI's test client in `tests/test_api.py`; it is not used for production outbound calls.

**Clinical/care systems:**
- No laboratory, screening-program, primary-care, specialist, EHR, scheduling, or patient-notification provider is connected. Those parties are represented as pathway roles and synthetic identifiers in `careflow/pathways/screening_to_care.yaml`, `careflow/engine.py`, and `careflow/seed.py`.

## Data Storage

**Databases:**
- SQLite - the only persistence backend, accessed directly through Python's `sqlite3` module and the synchronous `SQLiteStore` repository in `careflow/persistence.py`.
  - Connection: a filesystem path passed to `SQLiteStore`/`create_app`; defaults to `careflow.db`, while tests use temporary `careflow.sqlite` files and can use `:memory:` through `careflow/persistence.py`.
  - Client: standard-library `sqlite3`; no ORM, migration framework, or external database service is configured.
  - Schema: `users`, `cases`, `consents`, `evidence`, `approvals`, `audit_events`, `notification_outbox`, and `access_logs` are created by the `SCHEMA` constant in `careflow/persistence.py`.
  - Integrity: foreign keys, uniqueness constraints, indexes, and per-operation transactions are enabled in `careflow/persistence.py`.

**File Storage:**
- Local filesystem only - SQLite files such as `careflow.db` and pathway configuration at `careflow/pathways/screening_to_care.yaml` are read/written locally; no object storage or document service is integrated.

**Caching:**
- None detected. The API creates one in-process `SQLiteStore` connection per `create_app` instance in `careflow/api.py`; there is no Redis, Memcached, or HTTP cache layer.

## Authentication & Identity

**Auth Provider:**
- Custom/local signed-session authentication - `careflow/api.py` constructs an `itsdangerous.URLSafeTimedSerializer` using `CAREFLOW_SESSION_SECRET`/`SESSION_SECRET`, stores session settings on `app.state`, and exposes login, logout, and current-user routes.
  - Implementation: `POST /auth/login` accepts a seeded user ID, signs a timed `careflow_session` HttpOnly/Lax cookie, and rejects unknown/inactive users. Protected case and operational routes verify the cookie, load the active `User` from SQLite, enforce patient ownership/operational-role rules, and record successful access in `access_logs` in `careflow/api.py` and `careflow/persistence.py`.
  - Identity source: there is no OAuth/OIDC/SAML provider or password/credential verification. Users are local records created by `careflow/seed.py` or inserted through `SQLiteStore.add_user` in `careflow/persistence.py`; role names are constrained by `USER_ROLES` in `careflow/persistence.py`.
  - Dependency: `itsdangerous==2.2.0` is declared in `requirements.txt` and pinned in `requirements.lock`.

## Monitoring & Observability

**Error Tracking:**
- None detected. `careflow/api.py` maps `TransitionError` and selected persistence failures to JSON HTTP responses, but no Sentry, OpenTelemetry, metrics exporter, or hosted error service is configured.

**Logs:**
- No application logging framework or structured logger is configured. Demo and seed commands print human-readable/JSON output in `careflow/demo.py` and `careflow/seed.py`; Uvicorn provides only its own server logging when started by `Dockerfile` or `README.md`.
- Domain audit history is persisted in `audit_events`, and successful authenticated-access records can be persisted in `access_logs`, both defined in `careflow/persistence.py`; these are database records rather than a log shipping integration.

## CI/CD & Deployment

**Hosting:**
- Local process or container only - `README.md` starts Uvicorn directly, and `Dockerfile` builds a `python:3.12-slim` image listening on port 8000. No cloud hosting, managed deployment manifest, or infrastructure provider configuration is present.

**CI Pipeline:**
- None detected. `Makefile` provides the single local test command (`make test`), while `Dockerfile` provides the build/run path; no GitHub Actions, other CI workflow, registry push, or release pipeline is present.

## Environment Configuration

**Required env vars:**
- `CAREFLOW_SESSION_SECRET` or `SESSION_SECRET` - signing secret for the session serializer and `careflow_session` cookie in `careflow/api.py`; a development fallback exists, but deployments should set an explicit secret.
- `CAREFLOW_SESSION_MAX_AGE` or `SESSION_MAX_AGE` - optional positive session lifetime in seconds; defaults to 3600 in `careflow/api.py`.
- No database URL, API key, cloud credentials, SMTP settings, webhook URL, or external service token is referenced by application code in `careflow/`.

**Secrets location:**
- Environment variables are the intended location for session configuration in `careflow/api.py`; `.env.example` documents the two supported settings, and `.env` is ignored by `.gitignore`. No deployment secret manager integration is provided.
- The fallback value `DEFAULT_SESSION_SECRET` is committed in `careflow/api.py` for development and must not be treated as a production secret.

## Webhooks & Callbacks

**Incoming:**
- None. The routes in `careflow/api.py` accept ordinary case-management requests; no provider callback or webhook signature endpoint exists.

**Outgoing:**
- None. `notification_outbox` in `careflow/persistence.py` queues consent-gated internal notification records and exposes `pending_notifications`/`mark_notification_sent`, but no worker or transport sends them to email, SMS, push, webhook, or a third-party care system.
- The deterministic seed in `careflow/seed.py` creates synthetic `case_attention` outbox rows for local demos only; it does not call an external notification service.

---

*Integration audit: 2026-10-19*
