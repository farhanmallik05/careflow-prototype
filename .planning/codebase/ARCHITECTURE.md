# Architecture

**Analysis Date:** 2026-10-19

## Pattern Overview

**Overall:** Modular Python monolith with a declarative pathway state machine, synchronous SQLite repository, FastAPI JSON/HTML adapter, read-only message-provider boundary, and pure audit-derived metrics.

**Key Characteristics:**
- `careflow/engine.py` owns legal state transitions, evidence/approval requirements, open/closed ownership invariants, audit events, SLA explanations, and urgent-first worklist ordering.
- `careflow/pathways/screening_to_care.yaml` externalizes the workflow graph, owner roles, SLAs, attention flags, terminal state, approvers, and transition evidence instead of embedding the graph in route handlers.
- `careflow/persistence.py` stores a current case snapshot plus normalized operational records in SQLite; `SQLiteStore` owns one synchronous connection and wraps public writes in transactions.
- `careflow/api.py` is the composition and transport boundary: Pydantic validation, signed demo sessions, patient/staff authorization, JSON serialization, Jinja2 rendering, and delegation to engine/repository methods.
- `careflow/ai.py` accepts a copied read-only case context and returns draft content; the default provider is deterministic/offline and has no store or `Case` access.
- `careflow/metrics.py` derives observed measures from ordered persisted audit events, evidence, approvals, and pathway SLAs; `careflow/sim.py` is a separate seeded what-if model labelled as assumptions.
- `create_app` accepts database path, clock, pathway, session configuration, and an optional message provider, allowing isolated API tests without patching module globals.

## Layers

**Pathway Configuration:**
- Purpose: Define the allowed screening-to-care graph and policy metadata used by the domain engine.
- Location: `careflow/pathways/screening_to_care.yaml`; loaded by `Pathway.load` in `careflow/engine.py`.
- Contains: `screening_to_care`, `result_issued` start, `closed` terminal state, approver roles, owner roles, SLA hours, attention flags, transition targets, and required evidence.
- Depends on: `PyYAML` parsing in `careflow/engine.py`.
- Used by: `Case`, `explain`, and `worklist` in `careflow/engine.py`; app construction in `careflow/api.py`; fixture generation in `careflow/seed.py`; console demo in `careflow/demo.py`; tests in `tests/test_engine.py` and `tests/test_metrics.py`.

**Domain / Pathway Engine:**
- Purpose: Enforce care-case invariants and expose operational decisions without knowing about HTTP or SQL.
- Location: `careflow/engine.py`.
- Contains: `TransitionError`, `Pathway`, mutable `Case`, `hours`, `explain`, and `worklist`.
- Depends on: A validated pathway mapping and Python `datetime` values; it does not import FastAPI, SQLite, Jinja2, or the AI provider.
- Used by: Mutation/query routes in `careflow/api.py`, persistence rehydration types, synthetic fixture generation in `careflow/seed.py`, `careflow/demo.py`, and `tests/test_engine.py`/`tests/test_metrics.py`.

**Persistence / Repository:**
- Purpose: Persist case snapshots and related operational records while supporting deterministic rehydration, consent checks, and local outbox state.
- Location: `careflow/persistence.py`.
- Contains: `SCHEMA`, `SQLiteStore`, frozen record dataclasses (`User`, `Consent`, `EvidenceRecord`, `ApprovalRecord`, `MessageDraftRecord`, `AccessLog`), JSON/time conversion, audit/evidence/approval synchronization, and outbox methods.
- Depends on: Standard-library `sqlite3`, JSON, UUIDs, and `Case`/`Pathway` types from `careflow/engine.py`.
- Used by: The app-scoped store in `careflow/api.py`, deterministic seeding in `careflow/seed.py`, and repository/message/metrics tests.

**AI / Message Content Boundary:**
- Purpose: Generate patient-facing message drafts without granting content generation workflow authority.
- Location: `careflow/ai.py`.
- Contains: `AIProvider`, `DraftContent`, `RuleBasedProvider`, `LLMProvider`, normalization helpers, and provider errors.
- Depends on: Python standard library only; the provider receives a copied mapping, not `Case` or `SQLiteStore`.
- Used by: Message-draft routes in `careflow/api.py` and provider tests in `tests/test_messages.py`; persistence receives the returned content only after API validation.

**Metrics / Simulation:**
- Purpose: Separate audit-based operational reporting from illustrative assumptions.
- Location: `careflow/metrics.py` for observed measures and `careflow/sim.py` for seeded what-if scenarios.
- Contains: `compute_metrics`, timeline/proof helpers, duration/rate summaries, delay/drop-off reasons, `run`, and `compare`.
- Depends on: `Case`/`Pathway` and timestamps for metrics; standard-library `random` for simulation. Neither module reads SQLite directly.
- Used by: `GET /metrics` in `careflow/api.py`, the metrics template, `careflow/demo.py`, and `tests/test_metrics.py`/`tests/test_engine.py`.

**HTTP / Application and UI Adapter:**
- Purpose: Assemble runtime dependencies, authenticate requests, enforce access policy, call domain/repository/provider functions, and return JSON or server-rendered HTML.
- Location: `careflow/api.py`; `careflow/app.py` only re-exports `app` and `create_app`.
- Contains: Pydantic request models, app factory/lifespan, timed cookie signer, route handlers, access logging, API error handlers, JSON conversion, Jinja2 environment, static-file mount, and view-context helpers.
- Depends on: FastAPI, Pydantic, Jinja2, `itsdangerous`, engine, persistence, AI, metrics, simulation, pathway YAML, templates, and static CSS.
- Used by: Uvicorn through `careflow.api:app`, browser/API clients, `README.md`, `Dockerfile`, and `TestClient` tests.

**Fixture / Console Layer:**
- Purpose: Create repeatable synthetic records and demonstrate the workflow outside the HTTP process.
- Location: `careflow/seed.py`, `careflow/demo.py`, and `careflow/sim.py`.
- Contains: `seed_demo`, 40-case deterministic routes, demo users/patients/consents, synthetic audit/evidence/approval/outbox records, a console walkthrough, and seeded drop-off scenarios.
- Depends on: `Pathway`, `Case`, `explain`, `worklist`, `SQLiteStore`, and seeded `random.Random`.
- Used by: `python -m careflow.seed`, `python -m careflow.demo`, `make seed-demo`, `make demo`, Docker build, and persistence/engine tests.

**Verification Layer:**
- Purpose: Check domain invariants, signed-session/API behavior, HTML rendering, persistence round trips, message approval, metrics semantics, deterministic seeding, and documentation markers.
- Location: `tests/test_engine.py`, `tests/test_api.py`, `tests/test_auth.py`, `tests/test_messages.py`, `tests/test_metrics.py`, `tests/test_html.py`, and `tests/test_persistence.py`; `tests/test_docs.py` checks build-step documentation.
- Contains: Pytest unit tests, FastAPI `TestClient` integration-style tests, temporary SQLite files/in-memory stores, fixed clocks, and real provider/repository calls rather than mocks.
- Depends on: Public engine/API/persistence/AI/metrics/seed contracts.
- Used by: `make test` / `python -m pytest -q`.

## Data Flow

**API Application Bootstrap:**

1. Uvicorn imports `app` from `careflow/api.py`; module initialization calls `create_app()`.
2. `create_app` resolves the default pathway with `Pathway.load`, opens an app-scoped `SQLiteStore`, resolves the clock, session signer/settings, and message provider, then stores them in `app.state`.
3. The app mounts `careflow/static/`, uses an explicit Jinja2 environment over `careflow/templates/`, and closes the store in the FastAPI lifespan shutdown.

**Signed Demo Session and Access:**

1. `POST /auth/login` or `/demo-login/{user_id}` looks up an active local `User` by ID; it does not verify a password or external identity claim.
2. `careflow/api.py` signs `{"user_id": ...}` with `URLSafeTimedSerializer` and sets the timed `careflow_session` cookie.
3. Protected routes call `authenticated_user`, then apply `operational_user`, `authorize_case_access`, `authorize_transition`, or `authorize_message_approval` as applicable.
4. Successful case and collection reads/mutations call `SQLiteStore.record_access`; unauthorized requests fail before a success log is written.

**Case Creation:**

1. `POST /cases` validates `CreateCaseInput` and normalizes `id`/`case_id` in `careflow/api.py`.
2. The route validates an optional linked active patient, selects an active owner for the pathway start role, and constructs `Case` with an opening audit event.
3. `SQLiteStore.save_case` checks state/audit/ownership invariants and writes the `cases` snapshot, audit rows, and evidence rows atomically.
4. `_case_payload` combines the aggregate, patient link, and `explain` output into JSON; an explicit `Accept: text/html` renders `case.html` instead.

**Case Transition or Assignment:**

1. A mutation route loads a case through `_load_case`, which rehydrates the snapshot, ordered audit, and evidence rows from SQLite.
2. Authorization uses the signed-in user's role and patient scope; transition route metadata is derived from the authenticated user rather than the optional client `actor` label.
3. `Case.transition` or `Case.assign` validates the current state/action/evidence/approval/owner rules, changes the aggregate, and appends an audit event. The route never assigns `case.state` directly.
4. The route saves the complete snapshot and related provenance in `SQLiteStore`, records access, and returns the updated JSON/HTML view.

**Patient Message Draft and Consent-Gated Outbox:**

1. `POST /cases/{case_id}/message-drafts` loads case context and passes a copied mapping to the configured `AIProvider`.
2. The provider returns `DraftContent`; `SQLiteStore.add_message_draft` persists it as `pending_approval` without changing `Case` or `audit_events`.
3. `POST /message-drafts/{draft_id}/approve` checks staff role, linked active patient, non-blank review note, and a granted unexpired case/user consent.
4. `SQLiteStore.approve_message_draft` atomically inserts one `notification_outbox` row with status `queued` and marks the draft `approved`. No worker or external transport sends it.

**Worklist and Metrics Reads:**

1. `GET /worklist` and `GET /metrics` list all case rows, rehydrate each through `SQLiteStore.load_case`, and use the injected clock.
2. `engine.explain` computes elapsed time, SLA status, attention, and next actions; `engine.worklist` filters overdue/attention/unassigned open cases and sorts urgent cases first.
3. `/metrics` calls `compute_metrics` with the rehydrated cases, pathway, and current time. The module identifies milestones only from ordered audit events plus required evidence/approval proof, then calculates durations, rates, overdue counts, and reason keys.
4. The API adds worklist counts, logs access per case, and either returns JSON or renders `metrics.html`. `careflow.sim.compare()` is displayed separately as assumption-based modelling, not an observed metric.

**Synthetic Seed Flow:**

1. `careflow/seed.py` loads the production pathway and opens the requested SQLite file.
2. With `reset=True`, it clears known tables, inserts six staff users and 40 patient users, and builds `DEMO-001` through `DEMO-040` using deterministic routes and timestamps anchored at `DEMO_NOW`.
3. Cases are transitioned through the real engine, persisted with audit/evidence/approval rows, and given case-scoped granted consents.
4. Overdue/attention cases receive consent-checked `case_attention` outbox rows; the command returns table counts and repeated runs are compared in `tests/test_persistence.py`.

**State Management:**
- `Case` is the mutable in-memory aggregate. Its state, entered timestamp, owner, evidence set, and audit list are changed by `Case.transition`/`Case.assign` in `careflow/engine.py`.
- `SQLiteStore` stores a denormalized current snapshot in `cases` and ordered history in `audit_events`; `load_case` reconstructs the aggregate without replaying transitions.
- `Pathway` is validated at construction and then shared through app closures; its workflow policy remains in YAML.
- There is no cache, broker, external state manager, or separate metrics store. Notifications are local rows in `notification_outbox`.

## Key Abstractions

**`Pathway`:**
- Purpose: Validate and expose the declarative graph/policy.
- Examples: `Pathway.load`/`Pathway.validate` in `careflow/engine.py`, source policy in `careflow/pathways/screening_to_care.yaml`.
- Pattern: Mapping-loaded configuration object passed explicitly into cases, stores, and metrics.

**`Case`:**
- Purpose: Represent one care journey and enforce legal mutations.
- Examples: `Case.transition`, `Case.assign`, `Case.closed`, and `_log` in `careflow/engine.py`.
- Pattern: Mutable aggregate with current state, owner, evidence set, and ordered audit; terminal cases have no owner.

**`SQLiteStore`:**
- Purpose: Provide repository operations for cases, identity, consent, evidence, approvals, access logs, drafts, and outbox rows.
- Examples: `save_case`/`load_case`, `add_user`, `add_consent`, `record_evidence`, `add_approval`, `record_access`, `add_message_draft`, `approve_message_draft`, and notification methods in `careflow/persistence.py`.
- Pattern: One synchronous `sqlite3.Connection`, explicit schema, parameterized SQL, frozen dataclass records, and context-managed writes.

**`AIProvider` / `DraftContent`:**
- Purpose: Keep message generation content-only and outside workflow mutation.
- Examples: `RuleBasedProvider` and `LLMProvider` in `careflow/ai.py`.
- Pattern: Provider receives a plain mapping and returns immutable content metadata; approval, consent, persistence, and delivery remain in the API/repository boundary.

**`compute_metrics`:**
- Purpose: Recompute operational measures from persisted audit proof.
- Location: `careflow/metrics.py`.
- Pattern: Pure function over `Case`/`Pathway`/`datetime`; no database or HTTP dependency, with visible denominators/sample sizes and `None` for empty rates/durations.

**`create_app`:**
- Purpose: Assemble an isolated HTTP application with replaceable infrastructure and time.
- Examples: `create_app` in `careflow/api.py` and `make_client` in `tests/test_api.py`.
- Pattern: Explicit `db_path`, `clock`, `pathway`, session, and provider inputs; route closures capture resolved dependencies.

## Entry Points

**FastAPI module application:**
- Location: `careflow/api.py` (`app = create_app()`).
- Triggers: `python -m uvicorn careflow.api:app --host 127.0.0.1 --port 8000` in `README.md` or the container command in `Dockerfile`.
- Responsibilities: Resolve defaults, expose JSON/HTML routes, translate expected failures, serve templates/static files, and close SQLite on lifespan shutdown.

**Compatibility application module:**
- Location: `careflow/app.py`.
- Triggers: Servers/clients importing `careflow.app:app`.
- Responsibilities: Re-export `app` and `create_app` without adding behavior.

**Synthetic seed command:**
- Location: `careflow/seed.py` (`main`, `seed_demo`).
- Triggers: `python -m careflow.seed --db careflow.db` or `make seed-demo`; the Dockerfile invokes the same seed command only at first start when `/data/careflow.db` is absent.
- Responsibilities: Create/reset deterministic synthetic users, cases, consents, audit/evidence/approval records, and consent-gated attention rows.

**Console demonstration:**
- Location: `careflow/demo.py` (`main`).
- Triggers: `python -m careflow.demo` or `make demo`.
- Responsibilities: Run an in-memory case journey, show worklist behavior, demonstrate closure blocking/approval, and print `careflow.sim.compare()` assumptions.

**Container process:**
- Location: `Dockerfile`.
- Triggers: `docker build -t careflow:local .` followed by `docker run --rm -p 8000:8000 ...` as documented in `README.md`.
- Responsibilities: Install `requirements.lock`, copy the build context, seed a local `careflow.db` in the image, and launch `careflow.api:app` on port 8000.

## Error Handling

**Strategy:** Validate at each boundary, keep business-rule failures as domain `TransitionError`, and translate expected failures at the API boundary into explicit JSON or HTML HTTP responses.

**Patterns:**
- `Pathway.validate` rejects an unknown start/target and missing owner/non-positive SLA during construction; validation is structural rather than a full schema/reachability validator.
- `Case.transition` and `Case.assign` raise `TransitionError` for closed cases, illegal actions, missing evidence, invalid approvals, unauthorized roles, and blank owners.
- The `TransitionError` handler in `careflow/api.py` emits 422 for missing evidence and 409 for other transition conflicts with `error` and `detail` fields.
- `_load_case` maps missing rows to 404 and invalid persisted snapshots to 422; Pydantic models generate framework validation responses for malformed bodies.
- `SQLiteStore.save_case` rejects invalid state/audit/ownership snapshots before writing; selected SQLite reference errors are translated to 422 by case creation.
- `HTTPException` failures render `error.html` when HTML is explicitly requested and JSON otherwise. There is no global structured exception/logging policy for unexpected database errors.

## Cross-Cutting Concerns

**Logging:** No application logger is configured. CLI paths use `print`; workflow audit (`audit_events`) and successful access (`access_logs`) are persisted records, not an external logging integration.

**Validation:** Pydantic validates transport fields in `careflow/api.py`; `Pathway`/`Case` validate policy and transitions in `careflow/engine.py`; `SQLiteStore` validates persisted invariants, consent status, and SQL constraints in `careflow/persistence.py`; AI helpers validate language/template selection in `careflow/ai.py`.

**Authentication:** Active local users receive timed `careflow_session` cookies signed with `itsdangerous`. Login is ID selection for a seeded demo user, not password or external identity authentication.

**Authorization:** Patients are limited to linked cases/patient status; staff-only worklist/metrics/draft operations are enforced by route helpers; transition/message approval roles come from pathway owners/approvers. Transition authorization is role-based and does not require the authenticated user ID to equal the assigned owner ID.

**Auditability:** `Case._log` records open/assign/transition events; `SQLiteStore.save_case` mirrors them into `audit_events` and persists evidence/approval provenance. `compute_metrics` treats ordered audit proof as the source for observed milestones.

**Consent and notifications:** Case-scoped `Consent` records are checked immediately before notification enqueue or message-draft approval. Approved messages remain `queued` in the local outbox; no delivery worker is present.

**Time:** `create_app` accepts a callable or fixed `datetime` for tests. The default API clock is naive local time, while persistence/metrics normalize aware and naive values for some comparisons; callers still need a consistent timestamp convention.

**Reproducibility:** `.python-version`, `requirements.lock`, `Dockerfile`, `Makefile`, deterministic `seed_demo`, seeded simulation, fixed test clocks, and temporary SQLite fixtures define the current rebuild/test path.

## Current Gaps and Milestone Assumptions

- `.planning/PROJECT.md` active requirements for managed OIDC/SSO, fail-closed secret configuration, consistent production authorization, migrations/backup, health/readiness, structured logs, retention, and deployment runbooks are not implemented in this prototype.
- If no session secret environment variable is supplied, `create_app` generates a random process-local secret. This is convenient for local tests but makes sessions non-persistent across restarts and is not a deployment policy; cookie `Secure` and CSRF protections are also not configured.
- `SQLiteStore` uses one `check_same_thread=False` connection without an application lock, WAL/busy policy, version column, or compare-and-swap update. Case writes load/mutate/save snapshots and can lose concurrent updates; `initialize()` has no schema version or migration path.
- `load_case` reconstructs from stored rows without replaying the pathway or fully comparing denormalized snapshot metadata. The seed route definitions in `careflow/seed.py` also duplicate pathway behavior and must be kept synchronized manually.
- Worklist/metrics/patient collection reads hydrate every case and write per-case access logs. No pagination, query projection, retention, or external observability exists.
- The optional `LLMProvider` is a seam only; no external model integration is current. The notification outbox is a consent-gated local queue only; delivery, retry/claim semantics, and webhooks are milestone assumptions rather than implemented behavior.

---

*Architecture analysis: 2026-10-19*
