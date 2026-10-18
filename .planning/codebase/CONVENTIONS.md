# Coding Conventions

**Analysis Date:** 2026-10-19

## Naming Patterns

**Files:**
- Use lowercase `snake_case` for Python modules, as shown by `careflow/engine.py`, `careflow/persistence.py`, `careflow/api.py`, `careflow/seed.py`, and `careflow/sim.py`.
- Keep executable/demo modules in the same style, such as `careflow/demo.py`, and name pytest modules `test_<area>.py` as in `tests/test_engine.py`, `tests/test_persistence.py`, and `tests/test_api.py`.
- Keep pathway specifications in descriptive lowercase filenames such as `careflow/pathways/screening_to_care.yaml`.

**Functions:**
- Use `snake_case` for public functions, methods, and private helpers: examples include `create_app`, `load_case`, `save_case`, `_resolve_pathway`, `_case_payload`, and `_datetime` in `careflow/api.py` and `careflow/persistence.py`.
- Prefix implementation-only helpers with one underscore, following `_clock_factory`, `_required_text`, `_load_case`, `_jsonable`, and `_outbox_row` in `careflow/api.py` and `careflow/persistence.py`.
- Use verb-oriented names for mutations (`assign`, `transition`, `record_evidence`, `enqueue_notification`) and query-oriented names for reads (`get_case_record`, `list_users`, `pending_notifications`, `explain`) in `careflow/engine.py` and `careflow/persistence.py`.

**Variables:**
- Use descriptive `snake_case` locals and parameters such as `case_id`, `patient_user_id`, `resolved_pathway`, `recorded_at`, and `available_at` in `careflow/api.py` and `careflow/persistence.py`.
- Use short names only for tightly scoped iteration or mathematical values, such as `s`, `d`, `t`, `el`, and `e` in `careflow/engine.py`; use descriptive names when a value crosses a statement boundary.
- Use uppercase names for module-level configuration and fixture constants: `PATHWAY_FILE`, `SESSION_COOKIE`, `DEFAULT_SESSION_MAX_AGE`, `USER_ROLES`, `STAGES`, `EFFECT`, `PW`, and `T0` in `careflow/api.py`, `careflow/persistence.py`, `careflow/sim.py`, and the test modules.

**Types:**
- Use `PascalCase` for classes and exception types, including `Case`, `Pathway`, `TransitionError`, `SQLiteStore`, `User`, and `AccessLog` in `careflow/engine.py` and `careflow/persistence.py`.
- Use Pydantic model classes with `PascalCase` names (`ApprovalInput`, `LoginInput`, `CreateCaseInput`, `TransitionInput`, and `AssignInput`) in `careflow/api.py`.
- Use modern Python type syntax (`list[str]`, `dict[str, Any]`, and `str | None`) as established in `careflow/api.py`, `careflow/persistence.py`, and `careflow/seed.py`.
- Keep type aliases in `PascalCase`, following `Clock = Callable[[], datetime]` in `careflow/api.py`; keep domain values such as states, roles, and action names as strings matching `careflow/pathways/screening_to_care.yaml`.
- Model persisted records as frozen dataclasses (`User`, `Consent`, `EvidenceRecord`, `ApprovalRecord`, and `AccessLog`) in `careflow/persistence.py`; use Pydantic `BaseModel` classes only for HTTP input in `careflow/api.py`.

## Code Style

**Formatting:**
- Use four-space indentation and standard Python whitespace. Existing implementation files such as `careflow/persistence.py` and `careflow/api.py` use one statement per line and parenthesized multiline calls for long argument lists.
- There is no formatter configuration in the project files represented by `Makefile`, `requirements.txt`, and the repository root; preserve readable manual formatting when adding code rather than assuming Black or Ruff will rewrite it.
- Prefer trailing commas in multiline calls, literals, and model declarations, as used in `careflow/api.py`, `careflow/persistence.py`, and `careflow/seed.py`.
- Use f-strings for interpolated errors and identifiers, for example the validation messages in `careflow/engine.py` and notification IDs in `careflow/persistence.py`.
- Use comprehensions for compact transformations (`_jsonable`, `worklist`, and record conversion) as in `careflow/api.py`, `careflow/engine.py`, and `careflow/persistence.py`; use an explicit loop when validation or side effects are part of the operation.

**Linting:**
- No linter configuration or lint command is present beside `Makefile`, `requirements.txt`, and `requirements.lock`; the only standardized project check exposed by `Makefile` is `python -m pytest -q`.
- Keep validation at the boundary: Pydantic validators in `careflow/api.py`, pathway validation in `careflow/engine.py`, and repository value checks in `careflow/persistence.py` are the established locations for rejecting invalid input.
- Avoid using `assert` as user-input validation. The existing `assert` statements in `careflow/api.py` and `careflow/persistence.py` narrow values that have already been validated or enforce an internal SQLite invariant.

## Import Organization

**Order:**
1. Standard-library imports (`datetime`, `pathlib`, `sqlite3`, `typing`, and similar) appear first in `careflow/api.py`, `careflow/persistence.py`, and `careflow/seed.py`.
2. Third-party imports (`fastapi`, `pydantic`, `yaml`, and `pytest`) follow the standard library in `careflow/api.py`, `careflow/engine.py`, and `tests/test_engine.py`.
3. Local package imports (`careflow.engine`, `careflow.persistence`, and `careflow.api`) appear after third-party imports in `careflow/api.py`, `careflow/seed.py`, and the test modules.

**Path Aliases:**
- No path-alias configuration is present in the repository files; import project code by its package path, such as `from careflow.engine import Case` in `careflow/persistence.py` and `tests/test_engine.py`.
- Use relative filesystem paths only to locate package-owned data files, following `Path(__file__).parent / "pathways" / "screening_to_care.yaml"` in `careflow/api.py`, `careflow/seed.py`, and `careflow/demo.py`.

## Error Handling

**Patterns:**
- Raise the domain-specific `TransitionError` for invalid case actions, closed-case mutations, missing evidence, owner violations, and unauthorized approvals in `careflow/engine.py`; preserve the case state until validation completes.
- Raise `ValueError` for invalid pathway definitions, invalid consent or access-log values, malformed persisted snapshots, missing approval notes, and invalid constructor inputs in `careflow/engine.py`, `careflow/persistence.py`, and `careflow/api.py`.
- Raise `KeyError` for a missing persisted case in `SQLiteStore.load_case` in `careflow/persistence.py`; translate it to a 404 in `_load_case` in `careflow/api.py`.
- Translate domain failures at the HTTP boundary. The `TransitionError` handler in `careflow/api.py` returns JSON with `error` and `detail`, using 409 for an invalid action and 422 for missing evidence; route code converts missing records and SQLite integrity failures to `HTTPException` responses.
- Use Pydantic `field_validator` and `model_validator` methods in `careflow/api.py` for blank strings, repeated identifier aliases, and required request fields so malformed HTTP payloads receive framework validation responses.
- Use SQLite context managers (`with self.connection`) for atomic writes in `careflow/persistence.py`; use parameterized SQL rather than interpolating values into queries. The dynamic table name in `SQLiteStore.count` is protected by the `TABLES` allowlist in the same file.
- Authenticate requests by reading and verifying the signed `careflow_session` cookie in `careflow/api.py`; route handlers call `authenticated_user`, then apply `operational_user`, `authorize_case_access`, `authorize_transition`, and active-owner checks before mutation or operational reads.
- Return 401 for missing/invalid/expired sessions, 403 for role or patient-isolation failures, and 422 for invalid assignment targets or missing evidence as implemented in `careflow/api.py`; preserve these status distinctions when adding endpoints.

## Logging

**Framework:**
- No logging framework or logger configuration is present in `careflow/engine.py`, `careflow/persistence.py`, `careflow/api.py`, or `Makefile`.

**Patterns:**
- The CLI surfaces human-readable output with `print` in `careflow/demo.py` and JSON summary output in `careflow/seed.py`.
- The engine records operational history as structured `Case.audit` entries through `_log` in `careflow/engine.py`, and persistence stores those records in `audit_events` through `SQLiteStore.save_case` in `careflow/persistence.py`.
- When adding diagnostics to request handling, preserve the existing audit model in `careflow/engine.py` and persistence model in `careflow/persistence.py`; do not treat console output from `careflow/demo.py` as application observability.

## Comments

**When to Comment:**
- Add module docstrings for modules with public runtime responsibilities, following `careflow/engine.py`, `careflow/persistence.py`, `careflow/api.py`, `careflow/app.py`, `careflow/sim.py`, and `careflow/seed.py`.
- Add class and public-method docstrings when the invariant or contract is not obvious, as in `Pathway`, `Case.transition`, `SQLiteStore`, `User`, `AccessLog`, and `create_app` in `careflow/engine.py`, `careflow/persistence.py`, and `careflow/api.py`.
- Use inline comments only for non-obvious compatibility or invariant explanations, such as the TestClient thread-safety rationale in `careflow/persistence.py` and the engine-only state mutation rule in `careflow/api.py`.
- Keep security-sensitive behavior explicit in comments/docstrings when useful: `careflow/api.py` documents signed-session login and engine-only state mutation, while `careflow/persistence.py` documents authenticated access records and consent-gated notifications.

**JSDoc/TSDoc:**
- Not applicable: this is a Python codebase. Use Python docstrings and type annotations as shown in `careflow/api.py`, `careflow/persistence.py`, and `careflow/engine.py`.

## Function Design

**Size:**
- Keep route functions in `careflow/api.py` thin: load the case, obtain the clock, call a domain method, persist the snapshot, and serialize the response. State transitions belong in `Case.transition` in `careflow/engine.py`, not in route handlers.
- Keep persistence operations inside `SQLiteStore` in `careflow/persistence.py`, with serialization helpers (`_iso`, `_json`, `_from_json`) reused instead of duplicating conversion logic.
- Extract repeated API concerns into helpers such as `_resolve_pathway`, `_load_case`, `_case_payload`, and `_jsonable` in `careflow/api.py`; extract repeated test setup into helpers such as `walk`, `at_care_received`, `make_client`, and `open_case` in `tests/test_engine.py` and `tests/test_api.py`.

**Parameters:**
- Prefer explicit dependency injection for clocks, database paths, and pathway definitions, following `create_app(db_path, clock, pathway, ...)` in `careflow/api.py` and `make_client` in `tests/test_api.py`.
- Use keyword-only arguments for optional operational metadata and persistence controls, as in `Case.transition`, `SQLiteStore.record_access`, `SQLiteStore.enqueue_notification`, and `seed_demo` in `careflow/engine.py`, `careflow/persistence.py`, and `careflow/seed.py`.
- Normalize aliases once at the boundary (`identifier`, `target`, and `approval`) in `careflow/api.py`; pass canonical values to the engine and repository.
- Keep identity as a request dependency rather than trusting client actor fields: `careflow/api.py` derives transition actors from the authenticated `User.id` and records access using the authenticated user.

**Return Values:**
- Return domain objects for domain operations (`Case`, `Pathway`, and dataclass records) and plain dictionaries/lists for API and repository collection payloads, matching `careflow/engine.py`, `careflow/persistence.py`, and `careflow/api.py`.
- Use `None` for optional lookups (`get_user`, `get_case_record`, and `explain` for closed cases) and raise a typed exception when absence is an invalid operation (`load_case`), as implemented in `careflow/engine.py` and `careflow/persistence.py`.
- Keep JSON conversion at the API boundary with `_jsonable` in `careflow/api.py`; do not make domain timestamps, sets, or audit records depend on HTTP serialization.

## Module Design

**Exports:**
- Expose the FastAPI application and factory explicitly with `__all__` in `careflow/api.py` and the compatibility module `careflow/app.py`.
- Keep `careflow/__init__.py` empty and import concrete modules directly; no package-wide barrel export is used.
- Keep CLI entry points under `if __name__ == "__main__":` in `careflow/demo.py` and `careflow/seed.py`, with reusable `main` functions for invocation and tests.

**Barrel Files:**
- No barrel files are used. The only compatibility re-export is `careflow/app.py`, which imports `app` and `create_app` from `careflow/api.py`.
- Preserve the source-of-truth boundaries: pathway rules stay in `careflow/engine.py`, SQLite mapping stays in `careflow/persistence.py`, and HTTP validation/serialization stays in `careflow/api.py`.

---

*Convention analysis: 2026-10-19*
