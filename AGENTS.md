<!-- GSD:project-start source:PROJECT.md -->
## Project

**CareFlow**

CareFlow is a pathway-driven case-orchestration service for multi-party care journeys. Its current LoopCare prototype guides a screening result through assignment, evidence collection, escalation or rejection, patient communication drafting, and documented closure while keeping ownership, consent, approvals, and audit history explicit. The next milestone hardens this existing prototype for a deployment-agnostic pilot used by patients and care teams.

**Core Value:** Patients and care teams can move a screening case safely from result to documented next action or closure, with every responsibility, permission, decision, and communication traceable.

### Constraints

- **Data safety**: Synthetic data only — no real patient or protected health information may be required, generated, or committed for this milestone.
- **Identity**: Managed OIDC/SSO boundary — CareFlow should consume verified identity claims and map them to application roles rather than own passwords.
- **Messaging**: Auditable queue only — patient communication remains consent-gated and reviewable; delivery is deferred.
- **Portability**: Deployment-agnostic — keep the existing Python/FastAPI/SQLite/Docker stack portable across clean Linux environments.
- **Reproducibility**: Pinned runtime, lockfile, Dockerfile, offline `make test`, and deterministic fixtures must remain working after every change.
- **Compatibility**: Preserve the current pathway engine and existing user-visible workflow unless a security or reliability fix requires a deliberate behavior change.
- **Security**: Secrets must come from environment or deployment secret managers; no credentials, private keys, or absolute machine paths may enter repository artifacts.
- **Scope**: Production-readiness means a safe, observable pilot baseline, not a claim of regulatory certification or clinical production approval.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

## Languages
- Python 3.12 - the supported runtime for the application package, tests, seed/demo commands, simulation, API, engine, persistence, AI-provider boundary, and metrics code under `careflow/` and `tests/`; the repository pin is `.python-version`.
- YAML - the declarative screening-to-care pathway in `careflow/pathways/screening_to_care.yaml`, parsed with `yaml.safe_load` by `careflow/engine.py`.
- SQL (SQLite dialect) - schema DDL and parameterized repository queries embedded in `careflow/persistence.py`.
- HTML/CSS - server-rendered Jinja2 templates in `careflow/templates/` and the stylesheet in `careflow/static/style.css`.
- Dockerfile/Make/Markdown - reproducible container instructions, project commands, and operator documentation in `Dockerfile`, `Makefile`, `README.md`, and `docs/`.
## Runtime
- Python 3.12 - supported interpreter selected by `.python-version`; the container currently uses the mutable `python:3.12-slim` tag in `Dockerfile`.
- ASGI - `careflow.api:app` is served by Uvicorn; `careflow/app.py` provides a compatibility re-export.
- `pip` - the documented install command is `python -m pip install --requirement requirements.lock` in `README.md` and `Dockerfile`.
- Lockfile: present - `requirements.lock` contains the resolved direct and transitive versions; `requirements.txt` contains the seven direct dependencies.
## Frameworks
- FastAPI 0.136.1 - application factory, JSON routes, exception handling, static files, and ASGI lifecycle in `careflow/api.py`.
- Jinja2 3.1.6 - explicit `Environment`/`FileSystemLoader` setup and server-rendered dashboard pages in `careflow/api.py` and `careflow/templates/*.html`.
- Pydantic 2.13.4 with `pydantic-core==2.46.4` - locked transitive validation for request models in `careflow/api.py`.
- Uvicorn 0.47.0 - local and container ASGI process launched by `README.md` and `Dockerfile`.
- PyYAML 6.0.2 - pathway specification loading in `careflow/engine.py`.
- pytest 8.3.3 - test runner for the separate `tests/` suite.
- FastAPI `TestClient` with httpx 0.28.1 - synchronous in-process API/HTML tests in `tests/test_api.py`, `tests/test_auth.py`, `tests/test_messages.py`, and `tests/test_html.py`; the application has no outbound HTTP client.
- Docker - runtime image definition and build-time deterministic seeding in `Dockerfile`.
- Make - the only project command aliases in `Makefile`: `make test`, `make demo`, and `make seed-demo`.
- No `pyproject.toml`, `setup.py`, package build metadata, formatter, linter, or console-script entry point is present; run modules from the repository root with `python -m ...`.
## Key Dependencies
- `fastapi==0.136.1` - HTTP boundary and application assembly in `careflow/api.py`.
- `Jinja2==3.1.6` - dashboard, patient status, landing, metrics, and error rendering in `careflow/templates/`.
- `itsdangerous==2.2.0` - timed signing/verification of the `careflow_session` cookie in `careflow/api.py`.
- `PyYAML==6.0.2` - pathway graph and policy parsing in `careflow/engine.py`.
- `uvicorn==0.47.0` - ASGI server process for local and Docker runs.
- Python standard-library `sqlite3` - the synchronous `SQLiteStore`, schema, foreign keys, transactions, and row mapping in `careflow/persistence.py`; there is no ORM or external database driver.
- `httpx==0.28.1` - FastAPI `TestClient` support only; no production module performs an outbound httpx request.
- `pydantic`, `pydantic-core`, `starlette`, `anyio`, `h11`, `click`, and related packages - transitive ASGI/validation dependencies pinned in `requirements.lock`.
- `pytest==8.3.3` - offline verification; tests use temporary SQLite databases, injected clocks, deterministic pathways, and seeded random values rather than network services.
## Configuration
- Session signing reads `CAREFLOW_SESSION_SECRET` first, then `SESSION_SECRET`; lifetime reads `CAREFLOW_SESSION_MAX_AGE` or `SESSION_MAX_AGE`. These are resolved in `create_app` in `careflow/api.py`.
- Message drafting reads `CAREFLOW_MESSAGE_PROVIDER`; `rule_based` is the deterministic offline default and `llm` selects an intentionally unconfigured boundary unless a provider is injected in code (`careflow/ai.py`).
- The API defaults to `careflow.db` and `careflow/pathways/screening_to_care.yaml`, while `create_app` accepts explicit `db_path`, `clock`, `pathway`, `session_secret`, `session_max_age`, and `message_provider` values for isolation and tests.
- `.env.example` exists as a configuration template and `.env` is ignored by `.gitignore`; no secret file is part of the source scan.
- `requirements.txt` - direct dependency pins.
- `requirements.lock` - exact resolved versions installed by the container and clean-machine setup.
- `.python-version` - Python 3.12 runtime pin.
- `Dockerfile` - base image, locked dependency install, source copy, deterministic seed, and Uvicorn command.
- `.dockerignore` - excludes local databases, virtual environments, caches, and local secrets from the Docker context.
- `Makefile` and `README.md` - the canonical local test, seed, demo, run, and Docker usage instructions.
## Platform Requirements
- Python 3.12, pip, and a project-local virtual environment are required by `README.md`; SQLite is supplied by the Python standard library.
- Run from the repository root because the package is not installed as a wheel. The offline test command is `make test` (equivalent to `python -m pytest -q`).
- A Linux-compatible Docker runtime can build and run the image from `Dockerfile`; Uvicorn listens on `0.0.0.0:8000`.
- The process needs a writable filesystem for the SQLite path. No managed database, reverse proxy, process supervisor, cloud deployment manifest, or external service is configured.
## Current Gaps and Milestone Assumptions
- The runtime is pinned only to Python 3.12 at the major/minor level, the Docker base image uses a floating `python:3.12-slim` tag, and `requirements.lock` has version pins but no hashes. These are current reproducibility limitations, not implemented guarantees.
- The Dockerfile currently runs as root, copies the build context, seeds a database during image build, and does not define a health check or data volume. `.dockerignore` excludes `careflow.db` but does not exclude all development/planning content.
- The `.planning/PROJECT.md` active requirements for managed OIDC/SSO, fail-closed production secrets, migrations/backup, health diagnostics, and deployment runbooks are milestone targets; no provider, migration framework, or deployment integration is present in the current stack.
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

## Naming Patterns
- Use lowercase `snake_case` for Python modules, as shown by `careflow/engine.py`, `careflow/persistence.py`, `careflow/api.py`, `careflow/seed.py`, and `careflow/sim.py`.
- Keep executable/demo modules in the same style, such as `careflow/demo.py`, and name pytest modules `test_<area>.py` as in `tests/test_engine.py`, `tests/test_persistence.py`, and `tests/test_api.py`.
- Keep pathway specifications in descriptive lowercase filenames such as `careflow/pathways/screening_to_care.yaml`.
- Use `snake_case` for public functions, methods, and private helpers: examples include `create_app`, `load_case`, `save_case`, `_resolve_pathway`, `_case_payload`, and `_datetime` in `careflow/api.py` and `careflow/persistence.py`.
- Prefix implementation-only helpers with one underscore, following `_clock_factory`, `_required_text`, `_load_case`, `_jsonable`, and `_outbox_row` in `careflow/api.py` and `careflow/persistence.py`.
- Use verb-oriented names for mutations (`assign`, `transition`, `record_evidence`, `enqueue_notification`) and query-oriented names for reads (`get_case_record`, `list_users`, `pending_notifications`, `explain`) in `careflow/engine.py` and `careflow/persistence.py`.
- Use descriptive `snake_case` locals and parameters such as `case_id`, `patient_user_id`, `resolved_pathway`, `recorded_at`, and `available_at` in `careflow/api.py` and `careflow/persistence.py`.
- Use short names only for tightly scoped iteration or mathematical values, such as `s`, `d`, `t`, `el`, and `e` in `careflow/engine.py`; use descriptive names when a value crosses a statement boundary.
- Use uppercase names for module-level configuration and fixture constants: `PATHWAY_FILE`, `SESSION_COOKIE`, `DEFAULT_SESSION_MAX_AGE`, `USER_ROLES`, `STAGES`, `EFFECT`, `PW`, and `T0` in `careflow/api.py`, `careflow/persistence.py`, `careflow/sim.py`, and the test modules.
- Use `PascalCase` for classes and exception types, including `Case`, `Pathway`, `TransitionError`, `SQLiteStore`, `User`, and `AccessLog` in `careflow/engine.py` and `careflow/persistence.py`.
- Use Pydantic model classes with `PascalCase` names (`ApprovalInput`, `LoginInput`, `CreateCaseInput`, `TransitionInput`, and `AssignInput`) in `careflow/api.py`.
- Use modern Python type syntax (`list[str]`, `dict[str, Any]`, and `str | None`) as established in `careflow/api.py`, `careflow/persistence.py`, and `careflow/seed.py`.
- Keep type aliases in `PascalCase`, following `Clock = Callable[[], datetime]` in `careflow/api.py`; keep domain values such as states, roles, and action names as strings matching `careflow/pathways/screening_to_care.yaml`.
- Model persisted records as frozen dataclasses (`User`, `Consent`, `EvidenceRecord`, `ApprovalRecord`, and `AccessLog`) in `careflow/persistence.py`; use Pydantic `BaseModel` classes only for HTTP input in `careflow/api.py`.
## Code Style
- Use four-space indentation and standard Python whitespace. Existing implementation files such as `careflow/persistence.py` and `careflow/api.py` use one statement per line and parenthesized multiline calls for long argument lists.
- There is no formatter configuration in the project files represented by `Makefile`, `requirements.txt`, and the repository root; preserve readable manual formatting when adding code rather than assuming Black or Ruff will rewrite it.
- Prefer trailing commas in multiline calls, literals, and model declarations, as used in `careflow/api.py`, `careflow/persistence.py`, and `careflow/seed.py`.
- Use f-strings for interpolated errors and identifiers, for example the validation messages in `careflow/engine.py` and notification IDs in `careflow/persistence.py`.
- Use comprehensions for compact transformations (`_jsonable`, `worklist`, and record conversion) as in `careflow/api.py`, `careflow/engine.py`, and `careflow/persistence.py`; use an explicit loop when validation or side effects are part of the operation.
- No linter configuration or lint command is present beside `Makefile`, `requirements.txt`, and `requirements.lock`; the only standardized project check exposed by `Makefile` is `python -m pytest -q`.
- Keep validation at the boundary: Pydantic validators in `careflow/api.py`, pathway validation in `careflow/engine.py`, and repository value checks in `careflow/persistence.py` are the established locations for rejecting invalid input.
- Avoid using `assert` as user-input validation. The existing `assert` statements in `careflow/api.py` and `careflow/persistence.py` narrow values that have already been validated or enforce an internal SQLite invariant.
## Import Organization
- No path-alias configuration is present in the repository files; import project code by its package path, such as `from careflow.engine import Case` in `careflow/persistence.py` and `tests/test_engine.py`.
- Use relative filesystem paths only to locate package-owned data files, following `Path(__file__).parent / "pathways" / "screening_to_care.yaml"` in `careflow/api.py`, `careflow/seed.py`, and `careflow/demo.py`.
## Error Handling
- Raise the domain-specific `TransitionError` for invalid case actions, closed-case mutations, missing evidence, owner violations, and unauthorized approvals in `careflow/engine.py`; preserve the case state until validation completes.
- Raise `ValueError` for invalid pathway definitions, invalid consent or access-log values, malformed persisted snapshots, missing approval notes, and invalid constructor inputs in `careflow/engine.py`, `careflow/persistence.py`, and `careflow/api.py`.
- Raise `KeyError` for a missing persisted case in `SQLiteStore.load_case` in `careflow/persistence.py`; translate it to a 404 in `_load_case` in `careflow/api.py`.
- Translate domain failures at the HTTP boundary. The `TransitionError` handler in `careflow/api.py` returns JSON with `error` and `detail`, using 409 for an invalid action and 422 for missing evidence; route code converts missing records and SQLite integrity failures to `HTTPException` responses.
- Use Pydantic `field_validator` and `model_validator` methods in `careflow/api.py` for blank strings, repeated identifier aliases, and required request fields so malformed HTTP payloads receive framework validation responses.
- Use SQLite context managers (`with self.connection`) for atomic writes in `careflow/persistence.py`; use parameterized SQL rather than interpolating values into queries. The dynamic table name in `SQLiteStore.count` is protected by the `TABLES` allowlist in the same file.
- Authenticate requests by reading and verifying the signed `careflow_session` cookie in `careflow/api.py`; route handlers call `authenticated_user`, then apply `operational_user`, `authorize_case_access`, `authorize_transition`, and active-owner checks before mutation or operational reads.
- Return 401 for missing/invalid/expired sessions, 403 for role or patient-isolation failures, and 422 for invalid assignment targets or missing evidence as implemented in `careflow/api.py`; preserve these status distinctions when adding endpoints.
## Logging
- No logging framework or logger configuration is present in `careflow/engine.py`, `careflow/persistence.py`, `careflow/api.py`, or `Makefile`.
- The CLI surfaces human-readable output with `print` in `careflow/demo.py` and JSON summary output in `careflow/seed.py`.
- The engine records operational history as structured `Case.audit` entries through `_log` in `careflow/engine.py`, and persistence stores those records in `audit_events` through `SQLiteStore.save_case` in `careflow/persistence.py`.
- When adding diagnostics to request handling, preserve the existing audit model in `careflow/engine.py` and persistence model in `careflow/persistence.py`; do not treat console output from `careflow/demo.py` as application observability.
## Comments
- Add module docstrings for modules with public runtime responsibilities, following `careflow/engine.py`, `careflow/persistence.py`, `careflow/api.py`, `careflow/app.py`, `careflow/sim.py`, and `careflow/seed.py`.
- Add class and public-method docstrings when the invariant or contract is not obvious, as in `Pathway`, `Case.transition`, `SQLiteStore`, `User`, `AccessLog`, and `create_app` in `careflow/engine.py`, `careflow/persistence.py`, and `careflow/api.py`.
- Use inline comments only for non-obvious compatibility or invariant explanations, such as the TestClient thread-safety rationale in `careflow/persistence.py` and the engine-only state mutation rule in `careflow/api.py`.
- Keep security-sensitive behavior explicit in comments/docstrings when useful: `careflow/api.py` documents signed-session login and engine-only state mutation, while `careflow/persistence.py` documents authenticated access records and consent-gated notifications.
- Not applicable: this is a Python codebase. Use Python docstrings and type annotations as shown in `careflow/api.py`, `careflow/persistence.py`, and `careflow/engine.py`.
## Function Design
- Keep route functions in `careflow/api.py` thin: load the case, obtain the clock, call a domain method, persist the snapshot, and serialize the response. State transitions belong in `Case.transition` in `careflow/engine.py`, not in route handlers.
- Keep persistence operations inside `SQLiteStore` in `careflow/persistence.py`, with serialization helpers (`_iso`, `_json`, `_from_json`) reused instead of duplicating conversion logic.
- Extract repeated API concerns into helpers such as `_resolve_pathway`, `_load_case`, `_case_payload`, and `_jsonable` in `careflow/api.py`; extract repeated test setup into helpers such as `walk`, `at_care_received`, `make_client`, and `open_case` in `tests/test_engine.py` and `tests/test_api.py`.
- Prefer explicit dependency injection for clocks, database paths, and pathway definitions, following `create_app(db_path, clock, pathway, ...)` in `careflow/api.py` and `make_client` in `tests/test_api.py`.
- Use keyword-only arguments for optional operational metadata and persistence controls, as in `Case.transition`, `SQLiteStore.record_access`, `SQLiteStore.enqueue_notification`, and `seed_demo` in `careflow/engine.py`, `careflow/persistence.py`, and `careflow/seed.py`.
- Normalize aliases once at the boundary (`identifier`, `target`, and `approval`) in `careflow/api.py`; pass canonical values to the engine and repository.
- Keep identity as a request dependency rather than trusting client actor fields: `careflow/api.py` derives transition actors from the authenticated `User.id` and records access using the authenticated user.
- Return domain objects for domain operations (`Case`, `Pathway`, and dataclass records) and plain dictionaries/lists for API and repository collection payloads, matching `careflow/engine.py`, `careflow/persistence.py`, and `careflow/api.py`.
- Use `None` for optional lookups (`get_user`, `get_case_record`, and `explain` for closed cases) and raise a typed exception when absence is an invalid operation (`load_case`), as implemented in `careflow/engine.py` and `careflow/persistence.py`.
- Keep JSON conversion at the API boundary with `_jsonable` in `careflow/api.py`; do not make domain timestamps, sets, or audit records depend on HTTP serialization.
## Module Design
- Expose the FastAPI application and factory explicitly with `__all__` in `careflow/api.py` and the compatibility module `careflow/app.py`.
- Keep `careflow/__init__.py` empty and import concrete modules directly; no package-wide barrel export is used.
- Keep CLI entry points under `if __name__ == "__main__":` in `careflow/demo.py` and `careflow/seed.py`, with reusable `main` functions for invocation and tests.
- No barrel files are used. The only compatibility re-export is `careflow/app.py`, which imports `app` and `create_app` from `careflow/api.py`.
- Preserve the source-of-truth boundaries: pathway rules stay in `careflow/engine.py`, SQLite mapping stays in `careflow/persistence.py`, and HTTP validation/serialization stays in `careflow/api.py`.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## Pattern Overview
- `careflow/engine.py` owns legal state transitions, evidence/approval requirements, open/closed ownership invariants, audit events, SLA explanations, and urgent-first worklist ordering.
- `careflow/pathways/screening_to_care.yaml` externalizes the workflow graph, owner roles, SLAs, attention flags, terminal state, approvers, and transition evidence instead of embedding the graph in route handlers.
- `careflow/persistence.py` stores a current case snapshot plus normalized operational records in SQLite; `SQLiteStore` owns one synchronous connection and wraps public writes in transactions.
- `careflow/api.py` is the composition and transport boundary: Pydantic validation, signed demo sessions, patient/staff authorization, JSON serialization, Jinja2 rendering, and delegation to engine/repository methods.
- `careflow/ai.py` accepts a copied read-only case context and returns draft content; the default provider is deterministic/offline and has no store or `Case` access.
- `careflow/metrics.py` derives observed measures from ordered persisted audit events, evidence, approvals, and pathway SLAs; `careflow/sim.py` is a separate seeded what-if model labelled as assumptions.
- `create_app` accepts database path, clock, pathway, session configuration, and an optional message provider, allowing isolated API tests without patching module globals.
## Layers
- Purpose: Define the allowed screening-to-care graph and policy metadata used by the domain engine.
- Location: `careflow/pathways/screening_to_care.yaml`; loaded by `Pathway.load` in `careflow/engine.py`.
- Contains: `screening_to_care`, `result_issued` start, `closed` terminal state, approver roles, owner roles, SLA hours, attention flags, transition targets, and required evidence.
- Depends on: `PyYAML` parsing in `careflow/engine.py`.
- Used by: `Case`, `explain`, and `worklist` in `careflow/engine.py`; app construction in `careflow/api.py`; fixture generation in `careflow/seed.py`; console demo in `careflow/demo.py`; tests in `tests/test_engine.py` and `tests/test_metrics.py`.
- Purpose: Enforce care-case invariants and expose operational decisions without knowing about HTTP or SQL.
- Location: `careflow/engine.py`.
- Contains: `TransitionError`, `Pathway`, mutable `Case`, `hours`, `explain`, and `worklist`.
- Depends on: A validated pathway mapping and Python `datetime` values; it does not import FastAPI, SQLite, Jinja2, or the AI provider.
- Used by: Mutation/query routes in `careflow/api.py`, persistence rehydration types, synthetic fixture generation in `careflow/seed.py`, `careflow/demo.py`, and `tests/test_engine.py`/`tests/test_metrics.py`.
- Purpose: Persist case snapshots and related operational records while supporting deterministic rehydration, consent checks, and local outbox state.
- Location: `careflow/persistence.py`.
- Contains: `SCHEMA`, `SQLiteStore`, frozen record dataclasses (`User`, `Consent`, `EvidenceRecord`, `ApprovalRecord`, `MessageDraftRecord`, `AccessLog`), JSON/time conversion, audit/evidence/approval synchronization, and outbox methods.
- Depends on: Standard-library `sqlite3`, JSON, UUIDs, and `Case`/`Pathway` types from `careflow/engine.py`.
- Used by: The app-scoped store in `careflow/api.py`, deterministic seeding in `careflow/seed.py`, and repository/message/metrics tests.
- Purpose: Generate patient-facing message drafts without granting content generation workflow authority.
- Location: `careflow/ai.py`.
- Contains: `AIProvider`, `DraftContent`, `RuleBasedProvider`, `LLMProvider`, normalization helpers, and provider errors.
- Depends on: Python standard library only; the provider receives a copied mapping, not `Case` or `SQLiteStore`.
- Used by: Message-draft routes in `careflow/api.py` and provider tests in `tests/test_messages.py`; persistence receives the returned content only after API validation.
- Purpose: Separate audit-based operational reporting from illustrative assumptions.
- Location: `careflow/metrics.py` for observed measures and `careflow/sim.py` for seeded what-if scenarios.
- Contains: `compute_metrics`, timeline/proof helpers, duration/rate summaries, delay/drop-off reasons, `run`, and `compare`.
- Depends on: `Case`/`Pathway` and timestamps for metrics; standard-library `random` for simulation. Neither module reads SQLite directly.
- Used by: `GET /metrics` in `careflow/api.py`, the metrics template, `careflow/demo.py`, and `tests/test_metrics.py`/`tests/test_engine.py`.
- Purpose: Assemble runtime dependencies, authenticate requests, enforce access policy, call domain/repository/provider functions, and return JSON or server-rendered HTML.
- Location: `careflow/api.py`; `careflow/app.py` only re-exports `app` and `create_app`.
- Contains: Pydantic request models, app factory/lifespan, timed cookie signer, route handlers, access logging, API error handlers, JSON conversion, Jinja2 environment, static-file mount, and view-context helpers.
- Depends on: FastAPI, Pydantic, Jinja2, `itsdangerous`, engine, persistence, AI, metrics, simulation, pathway YAML, templates, and static CSS.
- Used by: Uvicorn through `careflow.api:app`, browser/API clients, `README.md`, `Dockerfile`, and `TestClient` tests.
- Purpose: Create repeatable synthetic records and demonstrate the workflow outside the HTTP process.
- Location: `careflow/seed.py`, `careflow/demo.py`, and `careflow/sim.py`.
- Contains: `seed_demo`, 40-case deterministic routes, demo users/patients/consents, synthetic audit/evidence/approval/outbox records, a console walkthrough, and seeded drop-off scenarios.
- Depends on: `Pathway`, `Case`, `explain`, `worklist`, `SQLiteStore`, and seeded `random.Random`.
- Used by: `python -m careflow.seed`, `python -m careflow.demo`, `make seed-demo`, `make demo`, Docker build, and persistence/engine tests.
- Purpose: Check domain invariants, signed-session/API behavior, HTML rendering, persistence round trips, message approval, metrics semantics, deterministic seeding, and documentation markers.
- Location: `tests/test_engine.py`, `tests/test_api.py`, `tests/test_auth.py`, `tests/test_messages.py`, `tests/test_metrics.py`, `tests/test_html.py`, and `tests/test_persistence.py`; `tests/test_docs.py` checks build-step documentation.
- Contains: Pytest unit tests, FastAPI `TestClient` integration-style tests, temporary SQLite files/in-memory stores, fixed clocks, and real provider/repository calls rather than mocks.
- Depends on: Public engine/API/persistence/AI/metrics/seed contracts.
- Used by: `make test` / `python -m pytest -q`.
## Data Flow
- `Case` is the mutable in-memory aggregate. Its state, entered timestamp, owner, evidence set, and audit list are changed by `Case.transition`/`Case.assign` in `careflow/engine.py`.
- `SQLiteStore` stores a denormalized current snapshot in `cases` and ordered history in `audit_events`; `load_case` reconstructs the aggregate without replaying transitions.
- `Pathway` is validated at construction and then shared through app closures; its workflow policy remains in YAML.
- There is no cache, broker, external state manager, or separate metrics store. Notifications are local rows in `notification_outbox`.
## Key Abstractions
- Purpose: Validate and expose the declarative graph/policy.
- Examples: `Pathway.load`/`Pathway.validate` in `careflow/engine.py`, source policy in `careflow/pathways/screening_to_care.yaml`.
- Pattern: Mapping-loaded configuration object passed explicitly into cases, stores, and metrics.
- Purpose: Represent one care journey and enforce legal mutations.
- Examples: `Case.transition`, `Case.assign`, `Case.closed`, and `_log` in `careflow/engine.py`.
- Pattern: Mutable aggregate with current state, owner, evidence set, and ordered audit; terminal cases have no owner.
- Purpose: Provide repository operations for cases, identity, consent, evidence, approvals, access logs, drafts, and outbox rows.
- Examples: `save_case`/`load_case`, `add_user`, `add_consent`, `record_evidence`, `add_approval`, `record_access`, `add_message_draft`, `approve_message_draft`, and notification methods in `careflow/persistence.py`.
- Pattern: One synchronous `sqlite3.Connection`, explicit schema, parameterized SQL, frozen dataclass records, and context-managed writes.
- Purpose: Keep message generation content-only and outside workflow mutation.
- Examples: `RuleBasedProvider` and `LLMProvider` in `careflow/ai.py`.
- Pattern: Provider receives a plain mapping and returns immutable content metadata; approval, consent, persistence, and delivery remain in the API/repository boundary.
- Purpose: Recompute operational measures from persisted audit proof.
- Location: `careflow/metrics.py`.
- Pattern: Pure function over `Case`/`Pathway`/`datetime`; no database or HTTP dependency, with visible denominators/sample sizes and `None` for empty rates/durations.
- Purpose: Assemble an isolated HTTP application with replaceable infrastructure and time.
- Examples: `create_app` in `careflow/api.py` and `make_client` in `tests/test_api.py`.
- Pattern: Explicit `db_path`, `clock`, `pathway`, session, and provider inputs; route closures capture resolved dependencies.
## Entry Points
- Location: `careflow/api.py` (`app = create_app()`).
- Triggers: `python -m uvicorn careflow.api:app --host 127.0.0.1 --port 8000` in `README.md` or the container command in `Dockerfile`.
- Responsibilities: Resolve defaults, expose JSON/HTML routes, translate expected failures, serve templates/static files, and close SQLite on lifespan shutdown.
- Location: `careflow/app.py`.
- Triggers: Servers/clients importing `careflow.app:app`.
- Responsibilities: Re-export `app` and `create_app` without adding behavior.
- Location: `careflow/seed.py` (`main`, `seed_demo`).
- Triggers: `python -m careflow.seed --db careflow.db` or `make seed-demo`; also invoked by `Dockerfile` during image build.
- Responsibilities: Create/reset deterministic synthetic users, cases, consents, audit/evidence/approval records, and consent-gated attention rows.
- Location: `careflow/demo.py` (`main`).
- Triggers: `python -m careflow.demo` or `make demo`.
- Responsibilities: Run an in-memory case journey, show worklist behavior, demonstrate closure blocking/approval, and print `careflow.sim.compare()` assumptions.
- Location: `Dockerfile`.
- Triggers: `docker build -t careflow:local .` followed by `docker run --rm -p 8000:8000 ...` as documented in `README.md`.
- Responsibilities: Install `requirements.lock`, copy the build context, seed a local `careflow.db` in the image, and launch `careflow.api:app` on port 8000.
## Error Handling
- `Pathway.validate` rejects an unknown start/target and missing owner/non-positive SLA during construction; validation is structural rather than a full schema/reachability validator.
- `Case.transition` and `Case.assign` raise `TransitionError` for closed cases, illegal actions, missing evidence, invalid approvals, unauthorized roles, and blank owners.
- The `TransitionError` handler in `careflow/api.py` emits 422 for missing evidence and 409 for other transition conflicts with `error` and `detail` fields.
- `_load_case` maps missing rows to 404 and invalid persisted snapshots to 422; Pydantic models generate framework validation responses for malformed bodies.
- `SQLiteStore.save_case` rejects invalid state/audit/ownership snapshots before writing; selected SQLite reference errors are translated to 422 by case creation.
- `HTTPException` failures render `error.html` when HTML is explicitly requested and JSON otherwise. There is no global structured exception/logging policy for unexpected database errors.
## Cross-Cutting Concerns
## Current Gaps and Milestone Assumptions
- `.planning/PROJECT.md` active requirements for managed OIDC/SSO, fail-closed secret configuration, consistent production authorization, migrations/backup, health/readiness, structured logs, retention, and deployment runbooks are not implemented in this prototype.
- If no session secret environment variable is supplied, `create_app` generates a random process-local secret. This is convenient for local tests but makes sessions non-persistent across restarts and is not a deployment policy; cookie `Secure` and CSRF protections are also not configured.
- `SQLiteStore` uses one `check_same_thread=False` connection without an application lock, WAL/busy policy, version column, or compare-and-swap update. Case writes load/mutate/save snapshots and can lose concurrent updates; `initialize()` has no schema version or migration path.
- `load_case` reconstructs from stored rows without replaying the pathway or fully comparing denormalized snapshot metadata. The seed route definitions in `careflow/seed.py` also duplicate pathway behavior and must be kept synchronized manually.
- Worklist/metrics/patient collection reads hydrate every case and write per-case access logs. No pagination, query projection, retention, or external observability exists.
- The optional `LLMProvider` is a seam only; no external model integration is current. The notification outbox is a consent-gated local queue only; delivery, retry/claim semantics, and webhooks are milestone assumptions rather than implemented behavior.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, or `.github/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
