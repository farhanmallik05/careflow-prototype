# Codebase Structure

**Analysis Date:** 2026-10-19

## Directory Layout

```
careflow-prototype/
├── careflow/                         # Importable Python application package
│   ├── __init__.py                   # Package marker; no public logic
│   ├── api.py                        # FastAPI app factory, models, routes, serialization
│   ├── app.py                        # Compatibility re-export of the API app
│   ├── engine.py                     # Pathway state machine and worklist logic
│   ├── persistence.py                # SQLite schema, repository, and operational records
│   ├── seed.py                       # Deterministic synthetic database fixture command
│   ├── sim.py                        # Seeded illustrative what-if simulation
│   ├── demo.py                       # In-memory console walkthrough
│   └── pathways/
│       └── screening_to_care.yaml    # Declarative screening-to-care workflow
├── tests/                            # Pytest tests for domain, API, and persistence
│   ├── test_engine.py                # State-machine and simulation behavior
│   ├── test_api.py                   # FastAPI endpoint behavior
│   └── test_persistence.py           # SQLite round trips and deterministic seeding
├── .planning/
│   └── codebase/                     # Generated architecture/structure mapping documents
├── .python-version                   # Python runtime pin (3.12)
├── requirements.txt                  # Direct dependency pins
├── requirements.lock                 # Fully pinned install set
├── Dockerfile                        # Reproducible API container build/run
├── Makefile                          # Test, demo, and seed command aliases
├── README.md                         # Setup, routes, guarantees, and limitations
├── .gitignore                        # Local database, virtualenv, cache, and secret exclusions
└── careflow.db                       # Local SQLite demo artifact; ignored by `.gitignore`
```

## Directory Purposes

**`careflow/`:**
- Purpose: Holds the complete production/runtime Python package for the prototype.
- Contains: Domain logic, HTTP adapter, persistence repository, command-line modules, simulation code, and pathway specifications in `careflow/` and `careflow/pathways/`.
- Key files: `careflow/engine.py`, `careflow/persistence.py`, `careflow/api.py`, and `careflow/pathways/screening_to_care.yaml`.

**`careflow/pathways/`:**
- Purpose: Stores declarative workflow specifications consumed by `Pathway.load` in `careflow/engine.py`.
- Contains: YAML pathway graphs; the current file is `careflow/pathways/screening_to_care.yaml`.
- Key files: `careflow/pathways/screening_to_care.yaml` defines `screening_to_care`, its states, owners, SLAs, actions, evidence requirements, terminal state, and approver roles.

**`tests/`:**
- Purpose: Groups all automated verification outside the application package.
- Contains: Pytest modules with shared pathway/time setup and temporary database fixtures.
- Key files: `tests/test_engine.py`, `tests/test_api.py`, and `tests/test_persistence.py`.

**`.planning/codebase/`:**
- Purpose: Stores GSD codebase mapping artifacts used by planning and execution agents.
- Contains: Architecture and structure documents such as `.planning/codebase/ARCHITECTURE.md` and `.planning/codebase/STRUCTURE.md`.
- Key files: `.planning/codebase/ARCHITECTURE.md` explains runtime layers and flows; `.planning/codebase/STRUCTURE.md` explains placement conventions.

**Project root:**
- Purpose: Defines runtime, installation, container, test, and contributor-facing commands.
- Contains: `README.md`, `Makefile`, `Dockerfile`, `.python-version`, `requirements.txt`, `requirements.lock`, `.gitignore`, and the ignored local database `careflow.db`.
- Key files: `README.md` is the operational contract; `Dockerfile` launches `careflow.api:app`; `Makefile` provides `test`, `demo`, and `seed-demo` targets.

## Key File Locations

**Entry Points:**
- `careflow/api.py`: Defines `app = create_app()` for Uvicorn and all HTTP routes.
- `careflow/app.py`: Re-exports `app` and `create_app` for deployment/import compatibility.
- `careflow/seed.py`: Defines `main` for `python -m careflow.seed` and `seed_demo` for fixture generation.
- `careflow/demo.py`: Defines `main` for `python -m careflow.demo`.
- `Dockerfile`: Starts the API with `python -m uvicorn careflow.api:app`.

**Configuration:**
- `careflow/pathways/screening_to_care.yaml`: Runtime workflow policy and state graph.
- `careflow/api.py`: Default pathway path, database default, clock resolution, and session environment variable names.
- `careflow/persistence.py`: SQLite schema, table names, indexes, foreign keys, and record constraints.
- `.python-version`: Python version pin.
- `requirements.txt`: Direct package pins for FastAPI, Uvicorn, Pydantic transitively, PyYAML, HTTPX, and Pytest.
- `requirements.lock`: Reproducible resolved package set.

**Core Logic:**
- `careflow/engine.py`: `Pathway`, `Case`, transition validation, evidence/approval checks, audit events, `explain`, and `worklist`.
- `careflow/persistence.py`: `SQLiteStore`, aggregate snapshot rehydration, operational record persistence, consent checks, access logs, and notification outbox.
- `careflow/api.py`: HTTP boundary models and route orchestration; it should not contain independent state-machine rules.
- `careflow/seed.py`: Synthetic route construction that exercises engine transitions and persistence records.
- `careflow/sim.py`: Independent seeded model calculations for the console demo.

**Testing:**
- `tests/test_engine.py`: Test pathway validation, legal/illegal transitions, closure controls, owners, worklist ordering, and deterministic simulation.
- `tests/test_api.py`: Test app-factory isolation, case CRUD/mutations, JSON error contracts, closure API behavior, worklist ordering, and metrics.
- `tests/test_persistence.py`: Test SQLite round trips, audit/evidence/approval/outbox persistence, consent-backed notifications, and repeatable seeding.
- `Makefile`: The canonical local test alias is `make test`, which runs `python -m pytest -q`.

## Naming Conventions

**Files:**
- Use lowercase `snake_case.py` for Python modules, as in `careflow/engine.py`, `careflow/persistence.py`, and the test modules under `tests/`.
- Use `test_<area>.py` for test modules, as in `tests/test_engine.py`, `tests/test_api.py`, and `tests/test_persistence.py`.
- Use lowercase `snake_case.yaml` for pathway specifications, as in `careflow/pathways/screening_to_care.yaml`.
- Use uppercase names for project-level documentation/configuration conventions where already established, such as `README.md`, `Dockerfile`, and `.planning/codebase/ARCHITECTURE.md`.

**Directories:**
- Use lowercase package and feature directories, as in `careflow/`, `careflow/pathways/`, `tests/`, and `.planning/codebase/`.
- Keep pathway files beneath `careflow/pathways/` rather than beside Python modules or in the project root.

**Functions and Variables:**
- Use `snake_case` for functions, methods, local variables, and endpoint helper names, as in `create_app`, `load_case`, `save_case`, `seed_demo`, and `case_id` in `careflow/`.
- Use uppercase `SCREAMING_SNAKE_CASE` for module constants and static configuration, as in `PATHWAY_FILE`, `SESSION_COOKIE`, `USER_ROLES`, `SCHEMA`, `DEMO_NOW`, and `ROUTES`.

**Types and Classes:**
- Use PascalCase for classes and exception types, as in `Pathway`, `Case`, `TransitionError`, `SQLiteStore`, `User`, and `ApprovalRecord`.
- Use frozen dataclasses for persisted operational record shapes in `careflow/persistence.py`; keep the mutable case aggregate as the `Case` class in `careflow/engine.py`.

**Workflow Identifiers:**
- Use lowercase `snake_case` for pathway names, state names, actions, evidence kinds, and role names in `careflow/pathways/screening_to_care.yaml` and `careflow/seed.py`.
- Use explicit string prefixes and zero-padded numbers for synthetic IDs in `careflow/seed.py`, such as `DEMO-001`, `demo-patient-001`, and `consent-001`.

## Where to Add New Code

**New Feature:**
- Primary domain behavior: Add or update domain rules in `careflow/engine.py`; keep route handlers in `careflow/api.py` as orchestration that loads, delegates, saves, and serializes.
- HTTP surface: Add request models and route handlers in `careflow/api.py`, using `create_app` dependencies rather than module-level database access.
- Persistence: Extend `SCHEMA`, records, and repository methods in `careflow/persistence.py`; preserve parameterized SQL, foreign keys, explicit transactions, and `load_case`/`save_case` round-trip behavior.
- Tests: Add domain assertions to `tests/test_engine.py`, endpoint assertions to `tests/test_api.py`, and repository/schema assertions to `tests/test_persistence.py` according to the feature boundary.
- Documentation/commands: Update `README.md`, `Makefile`, or `Dockerfile` when the feature changes setup, run, test, or deploy behavior.

**New Pathway:**
- Implementation: Add a YAML specification under `careflow/pathways/`, following the keys validated by `Pathway` in `careflow/engine.py`: `name`, `start`, `terminal`, `approvers`, and `states`.
- Loading: Pass the new path explicitly to `create_app` in `careflow/api.py` or to `Pathway.load` in a new command; do not duplicate transition rules in API routes.
- Verification: Add a focused pathway fixture or mapping to `tests/test_engine.py`, then cover persistence rehydration in `tests/test_persistence.py` if the pathway is used with SQLite.

**New Component/Module:**
- Implementation: Add a focused lowercase `snake_case.py` module under `careflow/` only when the responsibility does not fit the domain engine, repository, API adapter, or existing CLI modules.
- Imports: Keep dependencies directed toward `careflow/engine.py` for domain policy and `careflow/persistence.py` for storage; avoid making `careflow/engine.py` depend on FastAPI or SQLite.
- Public compatibility: Re-export only deliberate deployment-facing names through `careflow/app.py`; `careflow/__init__.py` currently contains no public exports.

**Utilities:**
- Shared helpers: Keep domain calculations in `careflow/engine.py`, persistence serialization/time helpers in `careflow/persistence.py`, and API JSON/request helpers in `careflow/api.py` until a genuinely cross-layer utility is identified.
- Avoid an unscoped utility module: There is no existing `careflow/utils.py`, so place a new helper beside the owning abstraction rather than introducing a catch-all module.

## Special Directories

**`careflow/pathways/`:**
- Purpose: Versioned declarative workflow policy consumed at runtime by `careflow/engine.py`.
- Generated: No; authored configuration.
- Committed: Yes; the source YAML is required for the default API, seed command, demo, and tests.

**`.planning/codebase/`:**
- Purpose: GSD planning and mapping artifacts for codebase-aware implementation planning.
- Generated: Yes, by mapping/planning workflows.
- Committed: The directory is part of project planning state; keep generated documents separate from runtime package code.

**`__pycache__/` and `.pytest_cache/`:**
- Purpose: Python bytecode and Pytest cache data created locally.
- Generated: Yes, by Python and Pytest.
- Committed: No; `.gitignore` excludes these directories and related compiled files.

**`careflow.db`:**
- Purpose: Local SQLite database produced by the demo seed command.
- Generated: Yes, by `make seed-demo` or `python -m careflow.seed`.
- Committed: No; `.gitignore` excludes `careflow.db` and SQLite variants.

---

*Structure analysis: 2026-10-19*
