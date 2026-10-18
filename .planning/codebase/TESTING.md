# Testing Patterns

**Analysis Date:** 2026-10-19

## Test Framework

**Runner:**
- `pytest` 8.3.3 is pinned in `requirements.txt` and `requirements.lock`.
- No pytest configuration file (`pytest.ini`, `pyproject.toml`, or `tox.ini`) is present; discovery uses pytest defaults for `tests/test_*.py` and `test_*` functions.

**Assertion Library:**
- Use pytest's native `assert` statements and `pytest.raises`, as demonstrated in `tests/test_engine.py`, `tests/test_persistence.py`, and `tests/test_api.py`.
- Use FastAPI/HTTPX `TestClient` response assertions for the HTTP surface in `tests/test_api.py`.

**Run Commands:**
```bash
python -m pytest -q       # Run the complete suite
make test                 # Project-level alias for the complete suite
python -m pytest -q tests/test_engine.py       # Run engine and simulation tests
python -m pytest -q tests/test_persistence.py  # Run SQLite and seed tests
python -m pytest -q tests/test_api.py          # Run API tests
```

At analysis time, pytest collects 23 tests: 4 in `tests/test_api.py`, 6 in `tests/test_auth.py`, 9 in `tests/test_engine.py`, and 4 in `tests/test_persistence.py`. The current `python -m pytest -q` run passes all 23 tests.

## Test File Organization

**Location:**
- Tests are kept in a separate top-level `tests/` directory rather than co-located with implementation modules.
- The suite maps to the main runtime boundaries: domain/simulation tests in `tests/test_engine.py`, repository/seed tests in `tests/test_persistence.py`, and HTTP integration tests in `tests/test_api.py`.
- Authentication and authorization behavior is isolated in `tests/test_auth.py`, which reuses the API factories and login helper from `tests/test_api.py`.

**Naming:**
- Name files `test_<subject>.py` and test functions `test_<behavior>`; examples include `tests/test_engine.py::test_every_transition_audited_and_invalid_rejected` and `tests/test_api.py::test_invalid_transition_is_a_clear_four_xx_response`.
- Use helper names that describe setup intent (`walk`, `at_care_received`, `make_client`, and `open_case`) in `tests/test_engine.py` and `tests/test_api.py`.

**Structure:**
```
tests/
├── test_api.py         # FastAPI TestClient request/response coverage
├── test_auth.py        # Signed sessions, role authorization, isolation, and access logs
├── test_engine.py      # Case, Pathway, worklist, and simulation behavior
└── test_persistence.py # SQLite round trips, consent enforcement, and deterministic seed
```

## Test Structure

**Suite Organization:**
```python
def test_invalid_transition_is_a_clear_four_xx_response(tmp_path):
    with make_client(tmp_path) as client:
        open_case(client)
        response = client.post(
            "/cases/CASE-1/transition",
            json={"action": "close", "actor": "coordinator"},
        )
        assert response.status_code == 409
        assert response.json()["error"] == "transition_error"
        assert "not allowed from result_issued" in response.json()["detail"]
```
This pattern is taken from `tests/test_api.py`: construct isolated state, exercise one behavior through its public boundary, then assert status and meaningful response fields.

**Patterns:**
- Use deterministic constants for time and pathway data (`T0`, `PW`, and `PATHWAY`) in `tests/test_engine.py`, `tests/test_persistence.py`, and `tests/test_api.py`.
- Advance time explicitly with `datetime` and `timedelta` rather than sleeping; `tests/test_engine.py` and `tests/test_api.py` use fixed timestamps to test SLAs, overdue ordering, and transitions.
- Assert externally observable invariants, not implementation call counts: state/owner/audit changes in `tests/test_engine.py`, persisted rows and records in `tests/test_persistence.py`, and HTTP status/JSON in `tests/test_api.py`.
- Preserve state after rejected operations. `tests/test_engine.py::test_cannot_close_without_evidence_or_approval` and `tests/test_api.py::test_closure_requires_evidence_or_documented_authorized_approval` assert that a failed closure leaves the case open or in `care_received`.
- Use repeated deterministic runs for generated data. `tests/test_engine.py::test_simulation_deterministic_and_improves_under_assumptions` compares seeded simulation output, while `tests/test_persistence.py::test_seed_is_deterministic_and_covers_pathway_stages` compares database snapshots from two seed runs.
- Exercise identity and authorization through the signed-cookie boundary rather than calling nested API helpers directly. `tests/test_auth.py` logs in users with `login`, checks current-user/logout behavior, verifies role-based transition and assignment decisions, checks patient case isolation, and inspects access-log records.

## Mocking

**Framework:**
- No mocking library, `unittest.mock` usage, or patching fixture is present in `tests/` or `requirements.lock`.

**Patterns:**
```python
current = [T0]

def clock():
    return current[0]

with make_client(tmp_path, clock=clock) as client:
    open_case(client, "CASE-A")
    current[0] = T0 + timedelta(hours=30)
    response = client.get("/worklist")
```
The mutable clock injection in `tests/test_api.py` is the established substitute for mocking time. `create_app` accepts `clock`, `db_path`, and `pathway` explicitly, so tests use dependency injection rather than patching module globals.

**What to Mock:**
- Do not mock the engine, SQLite connection, or FastAPI routing for behavior tests. Use the real `Case`/`Pathway` implementation from `careflow/engine.py`, a real temporary SQLite database through `SQLiteStore` in `careflow/persistence.py`, and `TestClient` in `tests/test_api.py`.
- Replace nondeterministic inputs through explicit parameters: pass a fixed `clock`, `tmp_path`, inline `Pathway`, or seeded `random.Random` path as done in `tests/test_api.py`, `tests/test_persistence.py`, and `careflow/sim.py`.
- Seed authentication state with real `User` dataclasses through `app.state.store.add_user` in `tests/test_api.py`; use `session_secret="test-secret"` where the cookie signer must be deterministic, as in `tests/test_auth.py`.

**What NOT to Mock:**
- Keep transition validation and audit behavior real; `tests/test_engine.py` exercises `Case.transition` and `worklist` directly.
- Keep persistence serialization and database constraints real; `tests/test_persistence.py` uses SQLite files under pytest's `tmp_path` and checks rehydrated cases, audit events, consents, approvals, and outbox rows.
- Keep HTTP error translation real; `tests/test_api.py` sends requests through `TestClient` and checks the actual JSON error handler in `careflow/api.py`.
- Keep signed-session and access-control behavior real; `tests/test_auth.py` uses actual login cookies and asserts 401/403/422 responses and persisted `AccessLog` rows from `careflow/persistence.py`.

## Fixtures and Factories

**Test Data:**
```python
def walk(c, steps):
    for i, (a, ev) in enumerate(steps, 1):
        c.transition(a, "u", T0 + timedelta(hours=i), evidence=ev)
    return c

def at_care_received():
    return walk(
        Case("C", PW, T0),
        [
            ("notify", []),
            ("acknowledge", ["ack_record"]),
            ("review", ["clinical_review"]),
            ("accept", ["acceptance"]),
            ("book", ["appointment"]),
            ("attend", ["attendance"]),
        ],
    )
```
The compact helper versions are defined in `tests/test_engine.py`; API setup helpers `make_client` and `open_case` are in `tests/test_api.py`.

**Location:**
- Small fixtures are defined beside the tests that use them in `tests/test_engine.py`, `tests/test_persistence.py`, and `tests/test_api.py`.
- `tests/test_api.py` owns shared API helpers (`make_client`, `login`, and `open_case`) and the `USERS` seed tuple; `tests/test_auth.py` imports those helpers rather than duplicating client setup.
- `tests/test_persistence.py` builds a minimal inline `Pathway` for repository tests instead of loading the production YAML, while `tests/test_engine.py` and `tests/test_api.py` load `careflow/pathways/screening_to_care.yaml`.
- No `conftest.py`, shared fixture module, factory package, or committed test fixture data file is present.
- Use pytest's built-in `tmp_path` for isolated SQLite files, as in `tests/test_persistence.py` and `tests/test_api.py`; do not write test databases to the repository's `careflow.db`.

## Coverage

**Requirements:**
- No coverage percentage target is enforced. `pytest-cov` is not listed in `requirements.txt` or `requirements.lock`, and no coverage configuration or CI check is present beside `Makefile`.

**View Coverage:**
```bash
# No repository-defined coverage command is available.
# Install/use a project-local coverage plugin only if the project explicitly adds it.
```

## Test Types

**Unit Tests:**
- `tests/test_engine.py` covers `Case`, `Pathway`, `worklist`, and seeded simulation behavior without HTTP or database dependencies; it checks invalid transitions, evidence/approval rules, ownership, ordering, audit entries, validation, and deterministic outcomes.

**Integration Tests:**
- `tests/test_persistence.py` exercises real SQLite schema creation and round trips through `SQLiteStore`, including users, cases, consents, evidence, approvals, audit events, notifications, and deterministic seed output.
- `tests/test_api.py` exercises the FastAPI application through `TestClient`, including create/get/assign/transition/worklist/metrics routes and the HTTP error handler in `careflow/api.py`.
- `tests/test_auth.py` exercises the signed `careflow_session` cookie, current-user and logout endpoints, authenticated role checks, patient isolation, active assignment targets, and `access_logs` persistence.

**E2E Tests:**
- No browser or deployed-service E2E framework is used. The closest full-flow coverage is the in-process API flow in `tests/test_api.py`, backed by a temporary SQLite database.

## Common Patterns

**Async Testing:**
```python
with make_client(tmp_path) as client:
    response = client.get("/metrics")
    assert response.status_code == 200
```
All tested routes are synchronous and are called through the synchronous `TestClient` in `tests/test_api.py`. The application has an async lifespan function in `careflow/api.py`, but tests do not use `pytest.mark.asyncio` or async test functions.

**Error Testing:**
```python
with pytest.raises(TransitionError):
    c.transition("close", "u", T0 + timedelta(hours=9))
assert not c.closed
```
Domain errors use `pytest.raises` in `tests/test_engine.py`; API errors assert the HTTP status, stable `error` field, and a meaningful substring in `detail` in `tests/test_api.py`; consent validation uses `pytest.raises(..., match="consent")` in `tests/test_persistence.py`; authentication and authorization failures assert 401/403/422 responses and unchanged state in `tests/test_auth.py`.

Use this existing test approach for new behavior: inject deterministic dependencies, exercise the narrowest real boundary, verify both success and rejection behavior, and assert persisted/audited side effects where applicable in `careflow/engine.py`, `careflow/persistence.py`, and `careflow/api.py`.

---

*Testing analysis: 2026-10-19*
