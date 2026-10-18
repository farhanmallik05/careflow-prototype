# Codebase Concerns

**Analysis Date:** 2026-10-19

## Tech Debt

**Authentication, documentation, and test contract drift (High):**
- Issue: The protected routes in `careflow/api.py` require an authenticated cookie and a seeded active user, while `README.md` still states that there is no auth. The API tests in `tests/test_api.py` create cases without logging in or seeding users. `python -m pytest -q` currently reports 5 failures and 11 passes: four API requests return `401 authentication required`, and the notification test in `tests/test_persistence.py` is rejected for missing consent.
- Files: `careflow/api.py`, `README.md`, `tests/test_api.py`, `tests/test_persistence.py`, `careflow/seed.py`
- Impact: The documented startup flow cannot exercise the API, the test suite is not a reliable release gate, and consumers cannot tell whether login and consent are required in development.
- Fix approach: Choose an explicit development/authentication contract, add fixtures that seed users and perform `/auth/login`, update the consent expectation, and make `README.md` describe the exact seeded login flow.

**Dependency manifest is incomplete (High):**
- Issue: `careflow/api.py` imports `itsdangerous`, but neither `requirements.txt` nor `requirements.lock` declares it. `Dockerfile` installs only `requirements.lock`, so a clean image cannot rely on the package that happens to be installed in the development interpreter.
- Files: `careflow/api.py`, `requirements.txt`, `requirements.lock`, `Dockerfile`
- Impact: A clean rebuild can fail at application import with `ModuleNotFoundError`, making the advertised Docker deployment non-reproducible.
- Fix approach: Add `itsdangerous` as a direct pinned dependency, regenerate the lock file from a clean Python 3.12 environment, and run the container startup check without relying on globally installed packages.

**Schema evolution has no migration mechanism (Medium):**
- Issue: `SQLiteStore.initialize()` in `careflow/persistence.py` executes `CREATE TABLE IF NOT EXISTS` statements but has no schema version, migration table, `ALTER TABLE` path, or compatibility checks.
- Files: `careflow/persistence.py`, `careflow.db`
- Impact: Existing databases can retain incompatible columns or constraints after code changes; a deployment cannot safely upgrade or report which schema revision it uses.
- Fix approach: Add an explicit schema version and ordered migrations, validate the database before serving requests, and document backup/rollback behavior.

**Permissive compatibility inputs obscure the API contract (Low):**
- Issue: `careflow/api.py` accepts aliases (`id`/`case_id`, `person`/`owner`/`assignee`) and keeps `actor` fields in `TransitionInput` and `AssignInput`, but routes record the authenticated user ID instead of the supplied actor. Hidden route aliases also duplicate `/login`, `/logout`, `/transitions`, and current-user endpoints.
- Files: `careflow/api.py`, `README.md`, `tests/test_api.py`
- Impact: Clients can send fields that appear authoritative but are ignored, and duplicated routes increase the surface that must be documented, tested, and secured.
- Fix approach: Publish one canonical request shape, deprecate aliases with explicit compatibility tests, and either remove ignored actor fields or rename them to non-authoritative client metadata.

**Container and lock reproducibility is incomplete (Medium):**
- Issue: `Dockerfile` uses the floating `python:3.12-slim` tag, installs version pins without hashes, has no `.dockerignore`, runs as root, and copies the entire build context. `.gitignore` does not control Docker build context, so `.git`, `.planning`, caches, virtual environments, and `careflow.db` can enter the image.
- Files: `Dockerfile`, `requirements.lock`, `.gitignore`, `careflow.db`
- Impact: Builds can change when the base image changes, image contents can include local data or development artifacts, and the service has unnecessary root privileges.
- Fix approach: Pin the base image digest, use hash-checked dependencies, add `.dockerignore`, create a non-root runtime user, and mount a deliberate data volume rather than copying a local database.

## Known Bugs

**Predictable default session secret enables cookie forgery (High):**
- Symptoms: If `CAREFLOW_SESSION_SECRET` and `SESSION_SECRET` are absent, `create_app()` signs cookies with the public constant `careflow-development-secret-change-me`.
- Files: `careflow/api.py`, `README.md`, `Dockerfile`
- Trigger: Start the default application without a session-secret environment variable and submit a cookie signed with the source-controlled default.
- Workaround: Provide a high-entropy secret through deployment configuration; the application does not currently fail closed when the default is used.

**Notification state transitions are not guarded (Medium):**
- Symptoms: `SQLiteStore.mark_notification_sent()` updates any matching notification ID regardless of its current status and does not check whether a row was updated. It increments attempts even when a notification is already sent and offers no failure or retry operation despite the schema having `processing` and `failed` states.
- Files: `careflow/persistence.py`
- Trigger: Call `mark_notification_sent()` twice or call it with an unknown ID.
- Workaround: Inspect `list_outbox()` after each call; there is no atomic claim/acknowledgement workflow.

**Persisted case metadata is not fully validated on rehydration (Medium):**
- Symptoms: `SQLiteStore.load_case()` derives `closed` from the state and never compares it with the persisted `cases.closed` value. It also accepts a caller-supplied pathway without checking `cases.pathway` against `pathway.name`, does not require a non-empty audit event list, and does not replay or validate audit transitions.
- Files: `careflow/persistence.py`, `careflow/api.py`
- Trigger: Modify a SQLite row directly, use a database created for a different pathway, or leave a case without audit rows and request `GET /cases/{case_id}`.
- Workaround: Treat the database file as trusted and immutable; production code needs integrity validation before exposing persisted cases.

**Naive and timezone-aware timestamps can fail in the engine (Medium):**
- Symptoms: `careflow.engine.hours()` subtracts timestamps directly. The API default clock returns a naive local `datetime`, while several persistence defaults use timezone-aware UTC timestamps. Passing an aware clock for a case created with a naive timestamp raises a `TypeError` during `explain()`.
- Files: `careflow/engine.py`, `careflow/api.py`, `careflow/persistence.py`, `careflow/seed.py`
- Trigger: Mix the default API clock or seeded naive case timestamps with an aware `clock` or a transition timestamp.
- Workaround: Use one timestamp convention throughout a run; normalize all ingress and storage values to UTC before comparison.

**Case mutation is vulnerable to lost updates (High):**
- Symptoms: Each transition loads a snapshot, mutates it in memory, then rewrites the case and audit history. There is no version column, compare-and-swap condition, or transaction spanning the read and mutation. Two concurrent requests can both transition the same state and the last save can overwrite the other audit event.
- Files: `careflow/api.py`, `careflow/engine.py`, `careflow/persistence.py`
- Trigger: Send concurrent transition or assignment requests for one case.
- Workaround: Serialize callers externally; the application has no optimistic-locking or per-case lock.

## Security Considerations

**Login is identity selection, not authentication (Critical):**
- Risk: `LoginInput` accepts only a user ID (or alias ID), and `/auth/login` issues a signed session for any active user whose ID is supplied. There is no password, one-time proof, OIDC/SAML assertion, API key, or trusted upstream identity.
- Files: `careflow/api.py`, `careflow/persistence.py`, `careflow/seed.py`
- Current mitigation: The cookie is `HttpOnly`, signed, and time-limited, and inactive users are rejected after lookup.
- Recommendations: Integrate a real identity provider or require a server-validated credential, separate demo login behind an explicit development flag, and prevent predictable seeded identities from being usable in a deployed environment.

**Cookie transport and request-forgery protections are incomplete (High):**
- Risk: `login()` sets `HttpOnly` and `SameSite=Lax` but omits `Secure`; state-changing endpoints rely on the session cookie without a CSRF token or explicit origin check.
- Files: `careflow/api.py`, `Dockerfile`, `README.md`
- Current mitigation: `SameSite=Lax` reduces some cross-site requests, and the session has a maximum age.
- Recommendations: Require HTTPS in deployment, set `Secure` outside a local flag, add CSRF protection for cookie-authenticated mutations, and document the trusted-origin policy.

**Role checks do not enforce case ownership or tenant boundaries (High):**
- Risk: `authorize_case_access()` limits only patient users to their own case. Any authenticated non-patient can read all cases, transition cases when their role matches the current state owner role, and assign cases; `authorize_transition()` checks `user.role` but not `case.owner == user.id`.
- Files: `careflow/api.py`, `careflow/engine.py`, `careflow/persistence.py`, `careflow/pathways/screening_to_care.yaml`
- Current mitigation: Patient access is restricted by `patient_user_id`, and patients cannot use operational views.
- Recommendations: Define organization/tenant membership, enforce least-privilege case scopes, distinguish role authorization from assignment ownership, and add authorization tests for every route.

**Evidence and approval claims are caller-provided (High):**
- Risk: `TransitionInput.evidence` is a list of arbitrary strings and `ApprovalInput` accepts a role, note, and free-text name. The API does not require a persisted evidence record, bind evidence to a source document, verify the approver name against the authenticated user, or bind an approval to a separately recorded approval identity. A caller can self-assert names such as `clinical_review` or `closure_note`.
- Files: `careflow/api.py`, `careflow/engine.py`, `careflow/persistence.py`, `careflow/pathways/screening_to_care.yaml`
- Current mitigation: The engine checks required string names and authorized role names when `actor_role` is supplied; approved transitions are copied into audit and approval tables.
- Recommendations: Create evidence and approval resources with server-side provenance, validate action/state applicability, bind records to authenticated principals, and make waiver policy explicit for each transition.

**Sensitive data handling is not production-ready (High):**
- Risk: `SQLiteStore` persists patient identifiers, emails, case state, evidence, approval notes, and access logs in a local unencrypted SQLite file. `Dockerfile` copies the build context and does not provide a protected data volume or backup policy.
- Files: `careflow/persistence.py`, `careflow.db`, `Dockerfile`, `README.md`
- Current mitigation: `README.md` describes the seed and simulation as synthetic data, and `careflow.db` is ignored by Git through `.gitignore`.
- Recommendations: Keep synthetic fixtures separate from deployment data, use an access-controlled managed database or encrypted storage for real records, exclude database files with `.dockerignore`, and define retention, backup, and deletion controls.

**Failed authentication and abuse controls are absent (Medium):**
- Risk: There is no rate limiting, login-attempt audit, lockout, session revocation list, or security event sink. `record_access()` records only successful case accesses after authentication.
- Files: `careflow/api.py`, `careflow/persistence.py`
- Current mitigation: Signed sessions expire after `DEFAULT_SESSION_MAX_AGE` unless configured otherwise; active status is checked on each request.
- Recommendations: Add bounded login attempts, structured security events, revocation/secret-rotation procedures, and deployment-level rate limiting.

## Performance Bottlenecks

**Worklist and metrics hydrate every case in Python (Medium):**
- Problem: `GET /worklist` and `GET /metrics` call `list_case_records()` and then `load_case()` for every case. Each load performs multiple SQLite queries, and metrics invokes `explain()` repeatedly for the same cases.
- Files: `careflow/api.py`, `careflow/persistence.py`, `careflow/engine.py`
- Cause: Filtering, SLA calculation, and ordering occur after full snapshot hydration rather than through a query-oriented projection.
- Improvement path: Add indexed case/SLA projections or a repository query that returns only eligible fields, paginate worklists, and compute each case's details once.

**Collection reads generate one write per case (Medium):**
- Problem: `/worklist` and `/metrics` call `record_access()` once for every returned database record, including cases that are not present in the worklist. A large read therefore creates a large write transaction and unbounded access-log growth.
- Files: `careflow/api.py`, `careflow/persistence.py`
- Cause: Collection access is represented by the case-level `access_logs.case_id NOT NULL` schema and is logged synchronously in the request.
- Improvement path: Record one collection-level event, batch inserts asynchronously, or use a bounded audit/retention strategy while retaining per-case reads for sensitive detail routes.

**Audit snapshots are rewritten on every mutation (Medium):**
- Problem: `SQLiteStore.save_case()` deletes audit rows at and after the current sequence and then inserts the complete in-memory audit list. Transition cost and transaction size grow with case history.
- Files: `careflow/persistence.py`, `careflow/engine.py`
- Cause: Persistence stores a full snapshot rather than appending an immutable event and updating a current-state row.
- Improvement path: Append validated audit events, update the current case with a version condition, and reserve snapshot rebuilds for migrations or repair jobs.

## Fragile Areas

**Single SQLite connection is shared across request threads without an application lock (High):**
- Files: `careflow/persistence.py`, `careflow/api.py`, `Dockerfile`
- Why fragile: `SQLiteStore` sets `check_same_thread=False` and reuses one connection, but no `threading.Lock`, connection pool, WAL mode, busy timeout, or request-level transaction boundary protects concurrent reads and writes. FastAPI synchronous handlers can run concurrently.
- Safe modification: Use one connection per request or a properly synchronized repository, enable a deliberate SQLite concurrency policy for the prototype, and add concurrent transition tests before changing persistence behavior.
- Test coverage: `tests/test_persistence.py` and `tests/test_api.py` cover sequential operations only; no concurrent access or lost-update test exists.

**Pathway validation accepts structurally weak configurations (Medium):**
- Files: `careflow/engine.py`, `careflow/pathways/screening_to_care.yaml`, `tests/test_engine.py`
- Why fragile: `Pathway.validate()` checks only the start target, positive SLA, owner presence, and transition target names. It does not validate transition shapes, required evidence types, owner/approver roles, terminal reachability, duplicate semantics, or that every configured state is reachable.
- Safe modification: Add a schema for the YAML, validate all fields and role names against `USER_ROLES`, reject unreachable/dead-end configurations, and test malformed pathway fixtures.
- Test coverage: `tests/test_engine.py` checks one bad target but not malformed state/action dictionaries or authorization configuration.

**Seed data duplicates pathway behavior as hard-coded routes (Medium):**
- Files: `careflow/seed.py`, `careflow/pathways/screening_to_care.yaml`, `tests/test_persistence.py`
- Why fragile: `ROUTES`, `STAGES`, `STEP_HOURS`, staff-role mappings, and closure behavior in `careflow/seed.py` must remain manually synchronized with the YAML. Changing a state or action can cause `KeyError`, invalid synthetic journeys, or misleading counts.
- Safe modification: Derive route validation from the pathway graph, keep fixture-only scenario definitions separate, and fail with a clear validation error when a scenario no longer matches the configured pathway.
- Test coverage: The seed test checks counts and stage coverage for one pathway only.

**Database integrity is trusted instead of verified (Medium):**
- Files: `careflow/persistence.py`, `careflow/api.py`, `careflow.db`
- Why fragile: `load_case()` rehydrates state, owner, evidence, and audit rows without replaying the transition graph or checking audit sequence continuity, first-event semantics, evidence consistency, or persisted `closed`/`pathway` fields. A malformed record can become a 500 through `_case_payload()` when `case.audit[0]` is missing.
- Safe modification: Validate and repair/ quarantine corrupt records, compare all denormalized columns with derived state, and return a controlled integrity error.
- Test coverage: There are no corruption, partial-write, or incompatible-pathway fixtures.

**Simulation input validation is missing (Low):**
- Files: `careflow/sim.py`, `careflow/demo.py`, `tests/test_engine.py`
- Why fragile: `run()` accepts zero or negative `n`, allows effects outside the intended range, and divides by zero when `n == 0`. The model is used in a demo but has no domain validation around its illustrative assumptions.
- Safe modification: Validate integer case counts and an effect range, define zero-case output, and expose that simulation results are assumptions in every consumer.
- Test coverage: `tests/test_engine.py` covers deterministic output and relative rates only.

## Scaling Limits

**Local SQLite is the only persistence topology (Medium):**
- Current capacity: A single file-backed SQLite database and one process-local store connection are assumed by `careflow/persistence.py` and the default `careflow/api.py` application.
- Limit: Concurrent writers, multiple API workers, large audit histories, and shared container deployments have no tested locking, pooling, replication, or backup strategy.
- Scaling path: Introduce a service database with transactions and optimistic locking, or explicitly constrain the prototype to one worker with documented volume and backup requirements.

**Case and audit history are unbounded (Medium):**
- Current capacity: `cases`, `audit_events`, `access_logs`, and `notification_outbox` grow for every case and request; no retention or archival job exists.
- Limit: Worklist/metrics latency, SQLite file size, and backup duration increase with total historical records; collection reads also add access rows per case.
- Scaling path: Add retention classes, archival storage, indexes tied to query plans, and paginated APIs before loading the full dataset in memory.

**Session revocation is not modeled (Medium):**
- Current capacity: Sessions are stateless signed cookies checked against `users.is_active` in `careflow/api.py`.
- Limit: Individual sessions cannot be revoked, inspected, or invalidated without deactivating the entire user or rotating the global secret, which invalidates all sessions.
- Scaling path: Add server-side session IDs/revocation metadata or use an identity provider with token introspection and key rotation.

## Dependencies at Risk

**Undeclared `itsdangerous` runtime dependency (Critical):**
- Risk: The import in `careflow/api.py` is absent from both dependency manifests, so a fresh install from `requirements.lock` can fail before the app serves requests.
- Impact: `Dockerfile` startup and any clean-machine deployment are unreliable.
- Migration plan: Declare and pin the direct dependency, regenerate `requirements.lock`, and verify the exact `python -m uvicorn careflow.api:app` command in a clean image.

**Floating Python base image and unhashed wheels (Medium):**
- Risk: `Dockerfile` selects `python:3.12-slim` by mutable tag, while `requirements.lock` pins versions without artifact hashes.
- Impact: Rebuilds can receive different OS or wheel artifacts, and supply-chain verification is weaker than the project’s reproducibility requirement.
- Migration plan: Pin a base-image digest and generate a hash-checked lock file from a clean Python 3.12 environment.

## Missing Critical Features

**Real identity and user lifecycle (Critical):**
- Problem: The only public identity flow is ID-only login; there are no user registration, credential verification, organization membership, password reset, external identity-provider, or session-revocation routes.
- Blocks: Safe deployment with real staff or patients, meaningful audit attribution, and least-privilege access.
- Files: `careflow/api.py`, `careflow/persistence.py`, `README.md`

**Evidence, consent, approval, and notification APIs are incomplete (High):**
- Problem: `careflow/persistence.py` contains repository methods for consents, evidence, approvals, and an outbox, but `careflow/api.py` exposes no authenticated endpoints to create, revoke, inspect, claim, retry, or fail those records. Case transitions can therefore accept strings without a user-visible provenance workflow.
- Blocks: A complete operational care journey, enforceable consent updates, and reliable notification delivery.
- Files: `careflow/api.py`, `careflow/persistence.py`, `careflow/seed.py`

**Operational deployment controls are absent (High):**
- Problem: There is no health/readiness endpoint, metrics exporter, structured logging, error tracking, TLS configuration, backup procedure, migration command, or worker process for the notification outbox.
- Blocks: Reliable production operations and diagnosis of failed or delayed care workflows.
- Files: `careflow/api.py`, `careflow/persistence.py`, `Dockerfile`, `README.md`, `Makefile`

**Pagination, filtering, and privacy scopes are absent (High):**
- Problem: `/worklist` and `/metrics` operate on all cases, and staff access is global rather than organization- or assignment-scoped.
- Blocks: Safe multi-tenant use and predictable response time as case volume grows.
- Files: `careflow/api.py`, `careflow/persistence.py`, `careflow/pathways/screening_to_care.yaml`

## Test Coverage Gaps

**Authentication and authorization:**
- What's not tested: Login identity proof, invalid/expired/tampered cookies, default-secret rejection, cookie flags, inactive users, patient isolation, owner-vs-role authorization, approval identity binding, and CSRF behavior.
- Files: `careflow/api.py`, `tests/test_api.py`, `tests/test_persistence.py`
- Risk: A caller can impersonate any seeded role or access a case outside the intended scope without a regression being caught.
- Priority: High

**Current API contract and seeded fixtures:**
- What's not tested: Seeding users before API calls, logging in through `/auth/login`, patient-linked case creation, and consent-aware notification setup. Existing tests exercise the pre-authenticated route contract and currently fail.
- Files: `tests/test_api.py`, `tests/test_persistence.py`, `careflow/seed.py`, `README.md`
- Risk: CI remains red and documentation examples do not represent a runnable flow.
- Priority: High

**Concurrency and transaction integrity:**
- What's not tested: Simultaneous transitions, assignments, duplicate case creation, thread access to one connection, audit sequence conflicts, and retry behavior after SQLite locking errors.
- Files: `careflow/api.py`, `careflow/persistence.py`, `tests/test_api.py`, `tests/test_persistence.py`
- Risk: Lost updates or corrupted audit history can occur under normal multi-request operation.
- Priority: High

**Persistence corruption and migrations:**
- What's not tested: Missing audit rows, inconsistent `closed`/`pathway` columns, unknown roles/states, non-contiguous audit sequences, old schema upgrades, and failed transactions.
- Files: `careflow/persistence.py`, `careflow.db`, `tests/test_persistence.py`
- Risk: Invalid records can produce 500 responses or silently bypass the intended state history.
- Priority: High

**Time and domain edge cases:**
- What's not tested: Aware/naive datetime combinations, clock values before case entry, consent boundaries at grant/expiry instants, zero-case simulations, negative counts, and invalid pathway schemas.
- Files: `careflow/engine.py`, `careflow/persistence.py`, `careflow/sim.py`, `careflow/pathways/screening_to_care.yaml`, `tests/test_engine.py`
- Risk: Date-dependent SLA and consent decisions can fail only in particular deployments or at boundary times.
- Priority: Medium

**Notification outbox lifecycle:**
- What's not tested: Atomic claiming, duplicate workers, failed delivery, retry/backoff, unknown IDs, already-sent rows, dedupe conflicts, and consent revocation after queueing.
- Files: `careflow/persistence.py`, `tests/test_persistence.py`
- Risk: Notifications can be duplicated, marked sent incorrectly, or remain permanently queued.
- Priority: Medium

**Clean build and runtime verification:**
- What's not tested: Installing exactly `requirements.lock` in a fresh Python 3.12 environment, importing the app without globally installed packages, Docker build context contents, non-root execution, and persistence across container replacement.
- Files: `Dockerfile`, `requirements.txt`, `requirements.lock`, `README.md`, `Makefile`
- Risk: The advertised rebuild-from-scratch workflow can fail before functional tests run.
- Priority: High

---

*Concerns audit: 2026-10-19*
