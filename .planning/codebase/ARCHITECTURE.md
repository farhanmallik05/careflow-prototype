# Architecture

**Analysis Date:** 2026-10-19

## Pattern Overview

**Overall:** Modular Python monolith with a pathway-driven state-machine domain core, a synchronous SQLite repository, and a thin FastAPI HTTP adapter.

**Key Characteristics:**
- `careflow/engine.py` is the domain authority for state transitions, evidence and approval rules, ownership invariants, audit events, elapsed-time explanations, and worklist ordering.
- `careflow/pathways/screening_to_care.yaml` externalizes the workflow graph, state owners, SLAs, attention flags, terminal states, and authorized approver roles rather than hard-coding the graph in HTTP handlers.
- `careflow/persistence.py` stores a validated case snapshot plus normalized operational records in SQLite; `SQLiteStore` owns one connection and makes each public write transactional.
- `careflow/api.py` provides request validation, error-to-HTTP mapping, serialization, and application wiring while deliberately delegating state changes to `Case.transition` and `Case.assign`.
- `careflow/seed.py` and `careflow/demo.py` are deterministic command-line/application entry points that exercise the same engine and persistence abstractions used by the API.
- Dependency injection through `create_app` in `careflow/api.py` accepts a database path, clock, and pathway, making API behavior reproducible in `tests/test_api.py` and isolated from module globals.

## Layers

**Pathway Configuration:**
- Purpose: Define the allowed care journey graph and the policy metadata used by the domain engine.
- Location: `careflow/pathways/screening_to_care.yaml` and the `Pathway` loader in `careflow/engine.py`.
- Contains: Pathway name, start state, terminal states, approver roles, state owners, SLA hours, attention flags, transition targets, and required evidence.
- Depends on: YAML parsing through `PyYAML` in `careflow/engine.py`.
- Used by: `Case`, `explain`, and `worklist` in `careflow/engine.py`; application construction in `careflow/api.py`; demo generation in `careflow/seed.py`; console examples in `careflow/demo.py`; tests in `tests/test_engine.py` and `tests/test_persistence.py`.

**Domain / Orchestration:**
- Purpose: Enforce the care-case state machine and expose operational decisions without knowing about HTTP or SQL.
- Location: `careflow/engine.py`.
- Contains: `TransitionError`, `Pathway`, mutable `Case`, `hours`, `explain`, and `worklist`.
- Depends on: A validated pathway mapping and Python datetime values; it has no persistence or FastAPI dependency.
- Used by: HTTP mutation and query routes in `careflow/api.py`, synthetic fixture generation in `careflow/seed.py`, the console scenario in `careflow/demo.py`, and unit tests in `tests/test_engine.py`.

**Persistence / Repository:**
- Purpose: Persist the case aggregate and related operational records while preserving rehydration and audit history.
- Location: `careflow/persistence.py`.
- Contains: SQLite `SCHEMA`, immutable records (`User`, `Consent`, `EvidenceRecord`, `ApprovalRecord`, `AccessLog`), and `SQLiteStore` CRUD/query methods.
- Depends on: Python `sqlite3`, JSON serialization, and the `Case`/`Pathway` types from `careflow/engine.py`.
- Used by: The app-scoped store created in `careflow/api.py`, the deterministic fixture command in `careflow/seed.py`, and persistence tests in `tests/test_persistence.py`.

**HTTP / Application Adapter:**
- Purpose: Expose case creation, transition, assignment, retrieval, worklist, and metrics operations as JSON HTTP endpoints.
- Location: `careflow/api.py`; `careflow/app.py` provides a compatibility import.
- Contains: Pydantic request models, pathway/clock resolution, app factory, lifecycle cleanup, JSON conversion, route handlers, and the `TransitionError` exception handler.
- Depends on: FastAPI, Pydantic, `URLSafeTimedSerializer` configuration, `SQLiteStore`, and domain functions/types from `careflow/engine.py`.
- Used by: Uvicorn via `careflow.api:app` in `README.md` and `Dockerfile`; the FastAPI `TestClient` in `tests/test_api.py`.

**Fixture, Simulation, and Console Layer:**
- Purpose: Create repeatable synthetic data, demonstrate the state machine, and calculate illustrative what-if outcomes outside the HTTP process.
- Location: `careflow/seed.py`, `careflow/demo.py`, and `careflow/sim.py`.
- Contains: `seed_demo`, deterministic route definitions, a 40-case SQLite fixture, a console walkthrough, and seeded drop-off simulations.
- Depends on: `Pathway`, `Case`, `explain`, `worklist`, `SQLiteStore`, and Python's seeded `random.Random`.
- Used by: `python -m careflow.seed`, `python -m careflow.demo`, `make seed-demo`, `make demo`, and tests in `tests/test_persistence.py` and `tests/test_engine.py`.

**Verification Layer:**
- Purpose: Check domain invariants, HTTP behavior, persistence round trips, deterministic seeding, and simulation behavior.
- Location: `tests/test_engine.py`, `tests/test_api.py`, and `tests/test_persistence.py`.
- Contains: Pytest unit tests, FastAPI `TestClient` integration-style tests, and temporary SQLite database fixtures.
- Depends on: The public engine, API factory, persistence repository, and seed command rather than private implementation-only test doubles.
- Used by: `Makefile` target `test` and the README's `python -m pytest -q` command.

## Data Flow

**API Application Bootstrap:**

1. Uvicorn imports `app` from `careflow/api.py`, which calls `create_app()` at module load time.
2. `create_app` resolves `careflow/pathways/screening_to_care.yaml` through `Pathway.load`, creates an app-scoped `SQLiteStore`, resolves the clock, and places these objects in FastAPI application state.
3. The FastAPI lifespan in `careflow/api.py` closes the store connection when the app shuts down.

**Case Creation:**

1. `POST /cases` validates `CreateCaseInput` in `careflow/api.py` and normalizes `id`/`case_id` to one identifier.
2. The route obtains a timestamp from the injected clock and constructs `Case` in `careflow/engine.py`; construction sets the pathway start state, its owner, and an `open` audit event.
3. `SQLiteStore.save_case` in `careflow/persistence.py` validates state/ownership invariants, writes the case snapshot, and persists the complete audit snapshot atomically.
4. `_case_payload` in `careflow/api.py` combines the case aggregate, the case row's patient reference, and `explain` output into a JSON-safe response.

**Case Transition or Assignment:**

1. A route in `careflow/api.py` calls `_load_case`, which asks `SQLiteStore.load_case` to rehydrate the case and its ordered audit/evidence collections.
2. `Case.transition` or `Case.assign` in `careflow/engine.py` validates the current state, action, evidence, approval, and owner rules, mutates the aggregate, and appends an audit event.
3. The route calls `SQLiteStore.save_case`, which updates the snapshot and synchronizes `audit_events`, evidence provenance, and approval records in one SQLite transaction.
4. The route returns `_case_payload`, so API clients receive the resulting state, owner, evidence, audit, timing, and next-action information.

**Worklist and Metrics Reads:**

1. `GET /worklist` and `GET /metrics` enumerate rows from `SQLiteStore.list_case_records` in `careflow/api.py`.
2. Each record is rehydrated through `SQLiteStore.load_case` using the resolved pathway; persisted state is validated during rehydration.
3. `explain` computes elapsed time, SLA status, and attention state, while `worklist` filters overdue, attention, or unassigned cases and sorts urgent cases first.
4. `/metrics` derives counts from the same rehydrated cases and engine explanations, avoiding a separate metrics state store.

**Synthetic Seed Flow:**

1. `careflow/seed.py` loads the pathway and opens `SQLiteStore` for the requested database path.
2. `seed_demo` clears tables when `reset=True`, inserts deterministic staff and patient `User` records, and creates 40 `Case` aggregates using deterministic routes and timestamps.
3. Each case is transitioned through the engine, then persisted with patient-scoped consent, evidence/audit/approval rows, and consent-gated attention notifications.
4. The command returns table counts as JSON; `tests/test_persistence.py` verifies that repeated seeding produces identical case and outbox rows.

**State Management:**
- `Case` is the mutable in-memory aggregate; its `state`, `entered`, `owner`, `evidence`, and `audit` values are changed only by domain methods in `careflow/engine.py` during a request or seed operation.
- `SQLiteStore` persists a denormalized current snapshot in `cases` and the ordered audit history in `audit_events`, then reconstructs the aggregate in `load_case` without replaying transitions.
- `Pathway` is immutable in practice after validation and is shared by the app's route closures; workflow policy lives in `careflow/pathways/screening_to_care.yaml`.
- There is no separate cache, message broker, or external state manager; notification delivery is represented by the SQLite `notification_outbox` table in `careflow/persistence.py`.

## Key Abstractions

**`Pathway`:**
- Purpose: Validate and expose a declarative state graph and its policy metadata.
- Examples: `Pathway.load` and `Pathway.validate` in `careflow/engine.py`; the source specification in `careflow/pathways/screening_to_care.yaml`.
- Pattern: Configuration object loaded from YAML or a mapping, validated at construction, and passed explicitly into cases and stores.

**`Case`:**
- Purpose: Represent one care journey and enforce legal mutations.
- Examples: `Case.transition`, `Case.assign`, `Case.closed`, and `_log` in `careflow/engine.py`.
- Pattern: Mutable aggregate with a pathway reference, current state, current owner, accumulated evidence, and append-only audit entries; closed cases have no owner.

**`SQLiteStore`:**
- Purpose: Provide repository operations for cases and surrounding operational records.
- Examples: `save_case`/`load_case`, `add_user`, `add_consent`, `record_evidence`, `add_approval`, `record_access`, and outbox methods in `careflow/persistence.py`.
- Pattern: Synchronous repository around one `sqlite3.Connection`, explicit SQL schema, parameterized queries, dataclass read models, and context-managed transactions.

**`create_app`:**
- Purpose: Assemble an isolated HTTP application with replaceable infrastructure and time.
- Examples: `create_app` in `careflow/api.py` and `make_client` in `tests/test_api.py`.
- Pattern: Application factory with `db_path`, `clock`, and `pathway` inputs; route closures capture the resolved store, pathway, clock, and session settings.

**Pydantic Request Models:**
- Purpose: Normalize and reject malformed JSON before domain calls.
- Examples: `CreateCaseInput`, `TransitionInput`, `AssignInput`, `ApprovalInput`, and `LoginInput` in `careflow/api.py`.
- Pattern: Field validators trim required text, model validators reconcile accepted aliases, and route handlers consume normalized properties such as `identifier`, `target`, and `approval`.

**Operational Record Dataclasses:**
- Purpose: Give typed read/write shapes to users, consent, evidence, approval, and access-log data outside the core `Case` aggregate.
- Examples: `User`, `Consent`, `EvidenceRecord`, `ApprovalRecord`, and `AccessLog` in `careflow/persistence.py`.
- Pattern: Frozen dataclasses with explicit datetime and mapping fields, converted to/from SQLite rows by `SQLiteStore` methods.

## Entry Points

**FastAPI module application:**
- Location: `careflow/api.py` (`app = create_app()`).
- Triggers: Uvicorn command `python -m uvicorn careflow.api:app --reload` in `README.md` or the container command in `Dockerfile`.
- Responsibilities: Resolve default configuration, expose HTTP routes, translate domain/persistence failures to responses, and close the SQLite connection on lifespan shutdown.

**Compatibility application module:**
- Location: `careflow/app.py`.
- Triggers: Deployment servers or clients importing `careflow.app:app`.
- Responsibilities: Re-export `app` and `create_app` from `careflow.api` without adding behavior.

**Synthetic seed command:**
- Location: `careflow/seed.py` (`main` and `seed_demo`).
- Triggers: `python -m careflow.seed --db careflow.db` or `make seed-demo`.
- Responsibilities: Create/reset deterministic synthetic users, cases, consents, audit/evidence/approval records, and notification outbox rows.

**Console demonstration:**
- Location: `careflow/demo.py` (`main`).
- Triggers: `python -m careflow.demo` or `make demo`.
- Responsibilities: Run a small in-memory case journey, show worklist output, demonstrate closure blocking/approval, and print illustrative simulation results from `careflow/sim.py`.

**Container process:**
- Location: `Dockerfile`.
- Triggers: `docker run --rm -p 8000:8000 careflow`.
- Responsibilities: Install `requirements.lock` and launch `careflow.api:app` through Uvicorn on port 8000.

## Error Handling

**Strategy:** Validate at each boundary, keep business-rule failures in the domain as `TransitionError`, and translate expected failures at the API boundary into explicit JSON 4xx responses.

**Patterns:**
- `Pathway.validate` in `careflow/engine.py` rejects unknown start/target states and missing owners or non-positive SLAs during pathway construction.
- `Case.transition` and `Case.assign` raise `TransitionError` for closed cases, illegal actions, missing evidence, invalid approvals, unauthorized roles, and owner violations in `careflow/engine.py`.
- The `TransitionError` handler in `careflow/api.py` emits 422 for missing evidence and 409 for other transition conflicts with `error` and `detail` fields.
- `_load_case` in `careflow/api.py` maps missing cases to 404 and invalid persisted snapshots to 422.
- Pydantic models in `careflow/api.py` produce FastAPI validation responses for blank fields, inconsistent aliases, and malformed request bodies.
- `SQLiteStore.save_case` in `careflow/persistence.py` rejects invalid state/ownership snapshots before writing; SQLite integrity failures from case references are translated by the create route to 422.
- Invalid or unexpected database errors are not wrapped by a global application exception policy; callers outside the API receive the underlying Python/SQLite exception.

## Cross-Cutting Concerns

**Logging:** No logging framework or application logger is configured in `careflow/`; the demo and seed commands use `print`, while API failures are returned as JSON.

**Validation:** `careflow/api.py` validates transport input with Pydantic, `careflow/engine.py` validates workflow policy and transition invariants, and `careflow/persistence.py` validates persisted aggregate invariants and SQLite constraints.

**Authentication:** The API currently has no login route, authentication dependency, or authorization middleware. `careflow/api.py` resolves a session secret and creates a `URLSafeTimedSerializer`, but the serializer is only stored in `app.state`; the README and route surface describe the service as having no auth.

**Authorization Policy:** Workflow approver roles are configured in `careflow/pathways/screening_to_care.yaml` and enforced only when `Case.transition` receives an `actor_role`; the current HTTP `TransitionInput` carries an actor string but does not populate `actor_role` from an authenticated identity.

**Auditability:** `Case._log` in `careflow/engine.py` records opening, assignment, and transition events; `SQLiteStore.save_case` mirrors these events into `audit_events` and stores approval/evidence provenance in related tables.

**Time:** `careflow/api.py` supports an injected callable or fixed `datetime` for deterministic tests; the default clock is naive local time, while `careflow/persistence.py` normalizes values for serialization and safe timezone comparisons.

**Configuration:** The default pathway is resolved relative to `careflow/api.py`, the database defaults to `careflow.db`, and session settings can come from `CAREFLOW_SESSION_SECRET`, `SESSION_SECRET`, `CAREFLOW_SESSION_MAX_AGE`, and `SESSION_MAX_AGE` as defined in `careflow/api.py`.

**Reproducibility:** `.python-version`, `requirements.lock`, `Dockerfile`, `Makefile`, and deterministic clocks/seeds in `careflow/seed.py`, `careflow/sim.py`, and `tests/` define the supported runtime and repeatable checks.

---

*Architecture analysis: 2026-10-19*
