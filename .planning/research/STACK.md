# Technology Stack

**Project:** CareFlow production-readiness pilot  
**Researched:** 2026-10-19  
**Overall confidence:** MEDIUM-HIGH

## Executive decision

Keep the existing Python 3.12, FastAPI, Pydantic, Uvicorn, Jinja2, PyYAML,
Docker, and standard-library `sqlite3` stack. Do not replace the modular monolith
or introduce a cloud-specific SDK, ORM, hosted database, telemetry backend, or
frontend framework for this milestone.

Add two direct runtime dependencies: **Authlib 1.9.1** for provider-neutral
OAuth/OIDC client plumbing and **pydantic-settings 2.16.0** for typed, validated
environment configuration. Keep `itsdangerous==2.2.0` as a direct dependency
because the current code and Starlette's transient OAuth-state pattern need signed
state, but do not use a client-readable signed cookie as the primary application
authentication session. Use an opaque, high-entropy, server-managed session ID
stored in SQLite and protect cookie-authenticated mutations with CSRF and trusted
origin checks.

Use the Python standard library for JSON logging, request correlation, session
token generation, CSRF token comparison, health checks, and SQLite migrations and
backups. A small pilot gains more from a narrow, testable contract than from
adding a logging, metrics, migration, or observability platform that is not yet
selected. Add structured-log or Prometheus/OpenTelemetry packages only behind a
later operational decision.

The resulting topology must be explicit: one Uvicorn worker/process, one
deployment-local writable SQLite volume, a versioned migration command before the
web process, tested online backups, and no claim that this topology is highly
available or suitable for real PHI.

## Current repository baseline

The codebase is already a coherent Python 3.12 modular monolith:

| Area | Current implementation | Decision |
|---|---|---|
| Runtime | Python `3.12` in `.python-version`; `python:3.12-slim` in `Dockerfile` | Keep Python 3.12 for this milestone; pin the container image by digest during implementation. |
| HTTP | FastAPI `0.136.1`, Pydantic `2.13.4`, Starlette `0.46.2`, Uvicorn `0.47.0` | Keep the tested pins for the first hardening pass; upgrade the framework set only as one compatibility-tested change. |
| Templates/config data | Jinja2 `3.1.6`, PyYAML `6.0.2` with `yaml.safe_load` | Keep; pathway YAML remains policy input, not a second domain implementation. |
| HTTP test client | HTTPX `0.28.1` and FastAPI `TestClient` | Keep; use the same boundary for OIDC callback, health, and authorization tests. |
| Sessions | `itsdangerous==2.2.0` and `URLSafeTimedSerializer`; `careflow_session` contains a user ID | Keep only for compatibility/transient state; replace the demo identity-selection path and add a revocable application session. |
| Persistence | Synchronous standard-library `sqlite3`, one connection owned by `SQLiteStore`, embedded DDL in `SCHEMA` | Keep SQLite for the single-host pilot; add migrations, locking/version checks, diagnostics, and backup commands rather than an ORM. |
| Web lifecycle | FastAPI lifespan closes the store; no health/readiness routes | Use lifespan for startup validation and cleanup; add separate liveness and readiness endpoints. |
| Observability | Domain `/metrics` route, access logs in SQLite, no structured application log contract | Preserve the staff-only domain metrics route; add privacy-safe JSON stdout logs and health diagnostics without exposing case data. |
| Container | Installs `requirements.lock`, seeds `careflow.db` at image build, runs Uvicorn as the default user | Stop baking a mutable data file into the image; run as non-root, mount `/data`, migrate before serving, and reserve seeding for explicit demo mode. |
| Reproducibility | `.python-version`, `requirements.txt`, `requirements.lock`, `Dockerfile`, `.dockerignore`, `Makefile`, deterministic fixtures | Keep these interfaces; add hash-checked lock generation and a Docker smoke test. |

The current manifests already declare `itsdangerous` and the repository already
has a `.dockerignore`; the older codebase concern map says otherwise. The maps
also report an older test count. A fresh local run during this research pass
reported **39 passed** with `python -m pytest -q`; `make test` could not be
invoked in this Windows shell because `make` is not installed. The clean Linux
release gate must still run the documented `make test` command.

## Recommended stack

### Core framework and runtime

| Technology | Version / pin | Purpose | Why this choice |
|---|---:|---|---|
| Python | `3.12` | Runtime for application, tests, seed, and migration commands | Already pinned and supported by the existing code. Avoid a runtime migration while changing security boundaries. |
| FastAPI | Keep `0.136.1` initially; evaluate `0.146.0` separately | HTTP routing, dependency injection, OpenAPI, lifespan | The existing app and tests use FastAPI directly. A framework refresh should not be mixed with OIDC or SQLite behavior changes. PyPI reported `0.146.0` as current on the research date, but compatibility still needs a clean-environment check. |
| Pydantic | Keep `2.13.4` with the current FastAPI pin | Request and response validation | Existing models and validators are already Pydantic 2 code. |
| pydantic-settings | **Add `2.16.0`** | Typed settings from environment/secrets | Fits Pydantic 2, validates URLs, booleans, durations, and deployment modes, and makes tests able to inject settings without patching `os.environ` globally. Do not auto-load `.env` in a pilot container. |
| Starlette | Keep `0.46.2` transitively | ASGI, middleware, session-state support | Use the version selected by FastAPI; do not independently pin or upgrade Starlette. |
| Uvicorn | Keep `0.47.0` initially; evaluate `0.54.0` separately | ASGI server | Existing Docker and README commands are Uvicorn-based. Run one worker because SQLite is process-local. |
| Jinja2 | `3.1.6` | Existing server-rendered dashboard | Preserve the current HTML surface and harden its authorization projections. |
| PyYAML | `6.0.2` | Safe loading of declarative pathway policy | Preserve the existing `safe_load` boundary and validate the complete graph before serving. |
| HTTPX | `0.28.1` | FastAPI `TestClient`; optional Authlib HTTP transport | Keep as a direct dependency while the test client and OIDC contract tests use it. |
| itsdangerous | `2.2.0` | Signed transient state and existing compatibility path | Keep explicitly pinned; do not treat its signed, readable cookie as a revocable identity store. |
| Authlib | **Add `1.9.1`** | Standards-based OAuth 2.0/OIDC client integration | Authlib provides FastAPI/Starlette integrations, discovery metadata, OAuth state handling, and OIDC support without selecting an identity vendor. Its package metadata marks the release stable and compatible with Python 3.10+. |

The version numbers above are recommendations for a controlled hardening branch,
not permission to upgrade every transitive dependency at once. The first
implementation should preserve the current lock, add the two direct dependencies,
regenerate the lock in Python 3.12, and run the full suite and container smoke
test. A later dependency-refresh phase can evaluate the newer FastAPI/Uvicorn
pins as a coherent set.

### Database and schema evolution

| Technology | Version / pin | Purpose | Why this choice |
|---|---:|---|---|
| Python `sqlite3` | Python 3.12 standard library | Transactions, repository access, local pilot persistence | The domain repository already uses parameterized SQL and does not need ORM behavior. Keeping it avoids SQLAlchemy, driver, pool, and deployment coupling. |
| SQLite migrations | Small in-repository migration runner using `PRAGMA user_version` and ordered SQL/Python steps | Upgrade an existing database safely | SQLite-specific versioning is appropriate because SQLite is an explicit pilot boundary. It is easier to test than asking Alembic to manage a non-SQLAlchemy repository. |
| SQLite backup | `sqlite3.Connection.backup()` | Consistent live snapshot before migration and for operator backup | The Python API maps directly to SQLite's Online Backup API and avoids copying a live file while its journal is changing. |
| Integrity checks | `PRAGMA integrity_check` and `PRAGMA foreign_key_check` | Readiness, restore verification, and support diagnostics | They are available without a new package and detect different classes of corruption. |

Do not add SQLAlchemy/Alembic merely to obtain migrations. Alembic `1.20.0` is
current according to PyPI and is a good choice if the project later moves to a
SQLAlchemy-backed service database, but it introduces a second persistence
abstraction for this pilot. Keep a migration interface so that a future database
adapter can replace the SQLite runner without changing `careflow.engine`.

The migration contract should be:

1. A new database is created at schema revision 0 and migrated to the current
   revision by one explicit command.
2. Each migration has an integer revision, a deterministic name, and an
   idempotence/rollback statement in its documentation. The runner applies one
   transaction at a time and updates `PRAGMA user_version` only after the step
   succeeds.
3. A migration runs under `BEGIN IMMEDIATE`, so a second process cannot mutate the
   file during schema change. The service refuses to serve an unsupported,
   partially migrated, or newer-than-code database.
4. The upgrade command makes an online backup first, verifies the backup, and
   records the source revision, target revision, application version, and UTC
   timestamp without storing secrets or patient-message text in logs.
5. Existing denormalized case fields gain a version/compare-and-swap value. A
   concurrent transition must produce one winner and a safe conflict, not silently
   overwrite a case audit history.

### Infrastructure and reproducibility

| Technology | Version / pin | Purpose | Why this choice |
|---|---:|---|---|
| Docker | Engine-compatible Dockerfile | Portable runtime packaging | It is already the project boundary and does not force a hosting vendor. |
| Python base image | `python:3.12-slim@sha256:<approved digest>` | Minimal runtime image | A digest removes mutable-tag drift; record the selected digest and refresh date in the implementation commit. Do not invent or leave a floating tag in the hardened image. |
| pip-tools | **`7.6.2` as a project-local development tool** | Generate the resolved lockfile | `pip-compile --generate-hashes` gives a direct-dependency input plus a hash-checked lock while retaining the existing pip/Docker workflow. Install it only in the project virtual environment, never globally. |
| `make` | Existing project command | Single offline test interface | Keep `make test` as the Linux contract and keep `python -m pytest -q` as its documented no-`make` equivalent. |
| Writable data volume | `/data/careflow.db` plus SQLite sidecars | Runtime persistence | A container image is immutable; the database must not be seeded or copied into the image for a pilot deployment. |

Target container behavior:

- Build from a clean checkout with a `.dockerignore` that excludes `.git`,
  `.planning`, caches, virtual environments, `.env`, `careflow.db`, and SQLite
  sidecars. Keep only runtime source/assets and the locked requirements.
- Install with a hash-checked lock. A lockfile without artifact hashes is useful
  for version repeatability but does not provide the strongest supply-chain
  check.
- Create and use a non-root runtime user. Do not add `curl` solely for a health
  check; Python's standard library can call a local health endpoint in a Docker
  `HEALTHCHECK`.
- Run a one-shot migration command before the web process. Do not run migrations
  concurrently in every worker or hide destructive reset behavior in import-time
  initialization.
- Run Uvicorn with one worker for the SQLite topology. Multiple workers or
  replicas require a different database/concurrency decision and must not be
  enabled by changing only a command-line flag.
- Keep `careflow.seed` as an explicit, clearly labelled synthetic-demo command;
  do not run it during production image build or against a mounted pilot file.

## Identity integration

### Recommended OIDC boundary

Use Authlib's Starlette/FastAPI client with the provider's OIDC discovery
document. Configuration should contain an issuer URL, client ID, client secret
when the deployment is a confidential web client, an exact redirect URI, an
explicit scope set (`openid profile email` only when those claims are needed),
and an administrator-controlled role/group mapping.

The flow should be authorization code plus PKCE (`S256`), with:

- exact registered redirect URI and a single allowed issuer;
- Authlib/issuer JWKS validation of the ID token signature and algorithm;
- `iss`, `aud`/`azp`, `exp`, `iat`, and `nonce` validation;
- OAuth state validation for the callback and bounded callback-session lifetime;
- a timeout and cache policy for discovery/JWKS retrieval;
- no password grant, no user-supplied identity selection, and no provider SDK;
- rejection of unknown, inactive, or unmapped identities rather than automatic
  broad staff access.

Persist an internal user ID and a unique `(issuer, subject)` pair. Treat `sub` in
combination with `iss` as the stable external identity; email is a profile/contact
attribute and must not be the primary key. Map trusted provider groups/roles to
the finite CareFlow role set through configuration, then apply a separate
relationship/assignment policy to cases. Never use a request-body `actor`, email,
display name, or UI visibility as proof of authorization.

Authlib's documented web integration relies on Starlette `SessionMiddleware` to
hold temporary OAuth code/state. That middleware signs a client cookie, and the
contents are readable by the client even though they are not modifiable. Use it
only for short-lived OIDC transaction state with a random secret, `HttpOnly`,
`SameSite=Lax`, a bounded `max_age`, and `https_only=True` in the pilot. Do not
put the internal user ID, role map, claims, access token, or refresh token in that
cookie.

### Explicit demo mode

The current `/auth/login` accepts an active seeded user ID and is therefore
identity selection, not authentication. Keep it only behind an explicit
`CAREFLOW_AUTH_MODE=demo` setting that is visibly labelled and unavailable when
pilot/OIDC mode is active. In pilot mode:

- missing OIDC settings or an unsafe cookie configuration fails startup/readiness;
- `/demo-login/{user_id}` and ID-only login are disabled;
- seeded `demo-*` users cannot be silently promoted to real identities;
- the seed command refuses a declared pilot data path unless an explicit demo
  reset flag is supplied.

The current random fallback generated by `create_app()` is acceptable for an
isolated test only. A deployed pilot must fail closed when the session secret,
OIDC configuration, secure-cookie requirement, or database path is missing or
unsafe; a random secret on each restart unexpectedly logs everyone out and can
mask a configuration error.

## Application session and CSRF policy

Use a server-managed session table in SQLite rather than a stateless signed
identity cookie. Generate at least 256 bits with `secrets.token_urlsafe(32)`,
store only a one-way hash of the token, and include:

- internal user ID;
- external issuer/subject reference;
- created time, last-seen time, idle expiry, and absolute expiry;
- revoked time/reason and a session-policy version;
- optional authentication time and request/audit correlation reference.

Set the application cookie to a `__Host-` name when possible, for example
`__Host-careflow_session`, with `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`,
and no `Domain`. Set `Secure` unconditionally in pilot mode; permit an insecure
local-only cookie only in explicit demo mode on loopback. Rotate the session after
OIDC login and privilege/identity changes. Logout must revoke/delete the server
record and clear the browser cookie; deactivating a user must invalidate active
sessions on the next request.

Because the dashboard uses cookies, `SameSite` is defense in depth, not the whole
CSRF design. Use a synchronizer token in the server session for HTML forms and an
`X-CSRF-Token` header for JSON mutations, plus an exact trusted-origin check. Do
not accept a caller-provided actor label as a CSRF or authorization substitute.
Test missing, wrong, replayed, expired, revoked, and rotated tokens, including
the OIDC callback and every state-changing route.

## Configuration

Use one `Settings(BaseSettings)` object at the application boundary, pass it into
`create_app`, and keep domain code independent of `os.environ`. Validate security
settings before constructing the app. Suggested names are:

| Setting | Required in pilot? | Notes |
|---|---|---|
| `CAREFLOW_ENV` | Yes | `demo` or `pilot`; no ambiguous default in a deployment. |
| `CAREFLOW_DB_PATH` | Yes | Default `/data/careflow.db` in the container; tests inject a temporary path. |
| `CAREFLOW_SESSION_SECRET` | Yes | High-entropy deployment secret; never log or commit. |
| `CAREFLOW_SESSION_MAX_AGE` | Yes | Absolute lifetime; add a separate idle timeout. |
| `CAREFLOW_SESSION_COOKIE_SECURE` | Yes in pilot | Must be true outside explicit loopback demo mode. |
| `CAREFLOW_OIDC_ISSUER` | Yes in pilot | HTTPS issuer URL; discovery is derived and issuer must match exactly. |
| `CAREFLOW_OIDC_CLIENT_ID` | Yes in pilot | Deployment-registered client ID. |
| `CAREFLOW_OIDC_CLIENT_SECRET` | Depends on client type | Secret-manager value for a confidential web client; never in `.env.example`. |
| `CAREFLOW_OIDC_REDIRECT_URI` | Yes in pilot | Exact HTTPS callback URL; reject ambiguous host/scheme construction. |
| `CAREFLOW_OIDC_ROLE_CLAIM` | Yes in pilot | Claim name selected by deployment policy, with an allowlisted mapping. |
| `CAREFLOW_TRUSTED_ORIGINS` | Yes for cookie mutations | Exact origins used by CSRF/origin checks; no wildcard with credentials. |
| `CAREFLOW_SQLITE_JOURNAL_MODE` | Documented | `WAL` only for a local same-host volume; use rollback journaling if that assumption is not true. |
| `CAREFLOW_SQLITE_BUSY_TIMEOUT_MS` | Documented | Bounded wait for transient locks; surface persistent contention as an actionable error. |
| `CAREFLOW_MESSAGE_PROVIDER` | Yes | Keep `rule_based` for offline synthetic operation; `llm` remains an explicit unconfigured boundary. |

Do not load a repository `.env` automatically in a container. Pydantic Settings
can load dotenv files, but deployment secrets should come from the environment or
secret manager, while `.env.example` remains placeholders and documentation.
Unknown environment names are not a reliable typo detector in Pydantic Settings,
so validate all critical fields explicitly and document the accepted variable set.

## Health, logging, and metrics

### Health endpoints

Add two unauthenticated, non-sensitive endpoints:

- `GET /health/live`: process/event-loop liveness only; it must remain useful when
  the database is unavailable.
- `GET /health/ready`: configuration, migration revision, pathway validation,
  database connectivity, integrity/foreign-key checks, and writable storage
  checks. Return `503` when not ready, with only check names and a correlation ID,
  never patient or secret values.

Use FastAPI lifespan for startup validation, migration orchestration, and clean
connection shutdown. A container health check should call readiness, while an
orchestrator liveness probe should call liveness. Keep the existing staff-only
`/metrics` domain report separate from an eventual infrastructure metrics
endpoint; its current case counts and audit-derived measures are not a Prometheus
contract.

### Structured application logs

Start with the standard-library `logging` module, `json`, and `contextvars`:

- emit one JSON object per event to stdout/stderr;
- include timestamp in UTC, severity, service/version, event name, request ID,
  route template, method/status, duration, bounded error class, and safe actor
  role/pseudonym when available;
- log startup/shutdown, config validation, migration, readiness changes,
  authentication result, authorization denial, validation failure, database
  lock/error, and high-risk workflow mutation events;
- redact cookies, authorization headers, OIDC tokens, raw claims, session IDs,
  secrets, emails, patient identifiers, message bodies, approval notes, and SQL;
- keep domain audit records (what happened to a case) separate from security and
  operational logs (what the service observed), with access and retention rules.

This is sufficient for a portable pilot and keeps Uvicorn/standard-library
logging compatible. `structlog==26.1.0` is a stable alternative with JSON and
stdlib integration, but it is not needed until the event vocabulary or processor
chain becomes complex enough to justify another runtime dependency.

Do not adopt the currently observed `opentelemetry-instrumentation-fastapi`
`0.66b1` pre-release as a production baseline. Starlette's native OpenTelemetry
middleware is documented as experimental, and no collector/exporter has been
selected. Define request IDs, log fields, and health contracts first; add
OpenTelemetry later when a deployment can own sampling, exporter failure, and
privacy policy.

### Infrastructure metrics

Do not expose the existing staff-only domain `/metrics` as an unauthenticated
scrape endpoint. If an operator later chooses Prometheus, add a separate
`/internal/metrics` contract with bounded, non-patient labels and a deployment
network boundary. `prometheus-fastapi-instrumentator==8.1.0` is a reasonable
future candidate, but it should not be added in the same phase as auth, schema,
and health changes because it would collide with the current route semantics and
expand the privacy review.

## SQLite operational policy

SQLite remains appropriate for a small, single-host synthetic pilot, not for
multiple replicas, a shared network filesystem, or high write concurrency. Make
these constraints visible in the runbook:

- use one Uvicorn worker and one process-local store topology;
- keep the database and any `-wal`/`-shm` sidecars on the same local writable
  volume;
- enable `PRAGMA foreign_keys = ON` on every connection, as the code already does;
- configure a bounded busy timeout and explicit transaction boundaries;
- choose WAL only on a local same-host filesystem after a concurrency test; SQLite
  documents that WAL is a same-host mode and changes backup/sidecar handling;
- leave `synchronous=FULL` for the safety-first pilot unless a measured workload
  and an explicit durability decision justify another setting;
- do not leave `check_same_thread=False` as the only concurrency control. Either
  use one connection per request or protect a shared connection/store with an
  application lock while using optimistic case-version checks;
- normalize timestamps to timezone-aware UTC at the boundary and persist ISO UTC
  values; do not mix the current naive local default clock with aware timestamps;
- return a controlled conflict/503 response for lock contention rather than a
  stack trace or a silently lost transition.

For backups, call `Connection.backup()` to a new destination file, then run
`integrity_check`, `foreign_key_check`, schema-version validation, and an
application startup check against the destination. Do not copy a live database
file with a generic file copy and assume the result is consistent. Retain backups
outside the container's writable layer, protect their access, define an honest
single-host RPO/RTO, and rehearse restore before calling recovery complete.

## Alternatives considered

| Category | Recommended | Alternative | Why not recommended now |
|---|---|---|---|
| Identity | Authlib 1.9.1 + generic OIDC discovery | Provider SDKs or hand-written OIDC/JWT code | SDKs create vendor lock-in; hand-written token, nonce, JWKS, and redirect validation is an avoidable security risk. |
| Application session | SQLite-backed opaque session with stdlib `secrets` | Starlette `SessionMiddleware`/`itsdangerous` as the identity session | Signed cookie contents are readable and stateless; individual revocation, inspection, and immediate logout are missing. Keep it only for short-lived OAuth state if needed. |
| Configuration | pydantic-settings 2.16.0 | Direct `os.environ` parsing | Direct parsing duplicates type/error handling and makes fail-closed configuration harder to test. Pydantic Settings aligns with the existing Pydantic 2 stack. |
| Logging | stdlib `logging` + JSON formatter | structlog 26.1.0 | structlog is sound, but the pilot needs a stable JSON contract rather than another dependency. Revisit when processors/context propagation grow. |
| Migrations | Small `sqlite3` runner + `PRAGMA user_version` | Alembic 1.20.0 + SQLAlchemy | Alembic is useful with SQLAlchemy, but adding both to a raw-`sqlite3` repository increases abstractions and lock surface without a current deployment benefit. |
| Backup | `sqlite3.Connection.backup()` | Raw file copy or `VACUUM INTO` only | Online Backup API provides a consistent snapshot of a running database and explicit lock/error handling. `VACUUM INTO` can remain a maintenance alternative. |
| Metrics | Health endpoints + JSON logs + existing domain metrics | Prometheus instrumentator 8.1.0 | Existing `/metrics` has a different meaning and contains domain data; a separate scrape contract needs a privacy/network decision. |
| Tracing | Request IDs and provider-neutral contracts first | OpenTelemetry FastAPI/Starlette instrumentation now | The observed FastAPI instrumentation is pre-release and Starlette calls its middleware experimental; no exporter or retention policy is selected. |
| Database | SQLite with one-worker boundary | SQLAlchemy/PostgreSQL or another managed database | The first deployment is intentionally undecided and synthetic-only. Select a service database only after volume, tenancy, HA, and recovery requirements are known. |
| Locking | pip-tools-generated hash lock | Poetry, uv, or a package-manager migration | Existing Docker, README, and offline test contract use pip and requirements files; changing the toolchain is unrelated to this hardening work. |

## Installation and lockfile workflow

The direct dependency input should eventually include the following additions
alongside the existing pins:

```text
Authlib==1.9.1
pydantic-settings==2.16.0
```

Keep a small direct-dependency input such as `requirements.in`, then compile the
runtime lock in a project-local Python 3.12 environment:

```bash
python -m pip install pip-tools==7.6.2
python -m piptools compile --generate-hashes --output-file requirements.lock requirements.in
python -m pip install --require-hashes --requirement requirements.lock
python -m pytest -q
```

The production image should install only the committed lockfile, not resolve
dependencies during startup. If the team keeps `requirements.txt` as the direct
input rather than adding `requirements.in`, document the exact compile command
and verify that the generated lock contains every direct import. Rebuild and test
the lock from clean Python 3.12, not from a machine with unrelated global
packages.

## Adoption order

The stack decisions imply this implementation order:

1. **Configuration and identity boundary:** add typed settings, explicit demo
   mode, Authlib OIDC discovery/callback tests, verified issuer/subject mapping,
   and fail-closed security configuration.
2. **Session and authorization:** add server-managed sessions, cookie/CSRF
   policy, route/relationship authorization, safe projections, and logout/
   revocation tests.
3. **Persistence correctness:** add migrations, schema revision checks, UTC
   normalization, case-version conflict handling, SQLite lock policy, and backup/
   restore commands.
4. **Operations:** add liveness/readiness, JSON stdout logs, redaction tests,
   safe error/correlation responses, container non-root behavior, volume policy,
   and a one-worker health check.
5. **Release gate:** regenerate the hash-checked lock, build from a clean Linux
   checkout, run `make test` offline, run the Docker smoke test, and rehearse
   migration/backup/restore/rollback with synthetic data.

Do not add real identity-provider credentials, real patient data, a delivery
transport, a cloud-specific database, or a telemetry backend as a shortcut to
proving any of these boundaries.

## Sources and confidence

### Repository evidence — HIGH confidence for current state

- `.planning/PROJECT.md` — active production-readiness requirements and explicit
  synthetic-only/deployment-agnostic constraints.
- `.planning/codebase/CONCERNS.md`, `.planning/codebase/ARCHITECTURE.md`, and
  `.planning/codebase/TESTING.md` — known risk inventory, boundaries, and test
  contract. Some map entries are stale; actual manifests and the 39-test run take
  precedence where they disagree.
- `careflow/api.py`, `careflow/persistence.py`, `careflow/engine.py`, and
  `careflow/pathways/screening_to_care.yaml` — current auth, session, lifecycle,
  repository, and pathway behavior.
- `requirements.txt`, `requirements.lock`, `.python-version`, `Dockerfile`,
  `.dockerignore`, `.env.example`, `Makefile`, and `README.md` — current
  reproducibility, container, configuration, and runbook contract.

### Standards and official documentation

- [OpenID Connect Core 1.0](https://openid.net/specs/openid-connect-core-1_0.html)
  — **HIGH**: identity claims, authorization-code flow, ID-token validation, and
  nonce requirements.
- [OpenID Connect Discovery 1.0](https://openid.net/specs/openid-connect-discovery-1_0.html)
  — **HIGH**: issuer metadata and endpoint/JWKS discovery.
- [RFC 9700: OAuth 2.0 Security Best Current Practice](https://www.rfc-editor.org/rfc/rfc9700.html)
  — **HIGH**: authorization code/PKCE, redirect, CSRF, token, and password-grant
  guidance.
- [Authlib FastAPI Integration](https://docs.authlib.org/en/stable/oauth2/client/web/fastapi.html)
  — **HIGH**: current FastAPI/Starlette client integration and temporary session
  state pattern.
- [Authlib on PyPI](https://pypi.org/project/Authlib/) — **HIGH**: version `1.9.1`,
  Python support, stable status, OIDC feature list, and the warning that the
  `authlib.jose` module is being deprecated. Do not use that deprecated module for
  the application session.
- [Starlette middleware documentation](https://www.starlette.dev/middleware/)
  — **HIGH**: signed/readable `SessionMiddleware` semantics, cookie flags,
  `https_only`, trusted-host/HTTPS middleware, and experimental OpenTelemetry
  middleware status.
- [Pydantic Settings documentation](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
  — **HIGH**: environment/secrets loading and typed validation; also documents
  that unmatched environment variable names are ignored.
- [pydantic-settings on PyPI](https://pypi.org/project/pydantic-settings/) —
  **HIGH**: version `2.16.0`, Python 3.11+ support, and stable release metadata.
- [Python `secrets` documentation](https://docs.python.org/3.12/library/secrets.html)
  — **HIGH**: secure token generation for session and CSRF secrets.
- [Python `logging` documentation](https://docs.python.org/3.12/library/logging.html)
  — **HIGH**: standard-library logging foundation.
- [Python `sqlite3.Connection.backup`](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.backup)
  — **HIGH**: Python binding used for online SQLite snapshots.
- [SQLite Online Backup API](https://www.sqlite.org/backup.html) — **HIGH**:
  consistent snapshots, incremental backup, and lock/error behavior.
- [SQLite PRAGMA documentation](https://www.sqlite.org/pragma.html) — **HIGH**:
  `user_version`, foreign-key checks, integrity checks, busy timeout, and journal
  mode semantics.
- [SQLite Write-Ahead Logging](https://www.sqlite.org/wal.html) — **HIGH**:
  same-host concurrency, WAL sidecars, checkpointing, and backup implications.
- [pip-tools on PyPI](https://pypi.org/project/pip-tools/) — **HIGH**: version
  `7.6.2`, `pip-compile`, hash generation, and project-local lock workflow.
- [structlog on PyPI](https://pypi.org/project/structlog/) — **MEDIUM-HIGH**:
  stable version `26.1.0` and stdlib/JSON integration as an optional alternative.
- [Dockerfile `HEALTHCHECK` reference](https://docs.docker.com/reference/dockerfile/#healthcheck)
  — **HIGH**: container health signaling contract.

Package versions and integration behavior should be rechecked when the
implementation phase starts. The recommendation is intentionally conservative:
add only the two libraries that directly reduce identity/configuration risk, use
the standard library for the rest, and make every operational assumption
observable and testable.
