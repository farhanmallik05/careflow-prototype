# Technology Stack

**Analysis Date:** 2026-10-19

## Languages

**Primary:**
- Python 3.12 - application code, pathway engine, persistence repository, API, demo/seed commands, simulation, and tests in `careflow/` and `tests/`; the supported interpreter is pinned in `.python-version`.

**Secondary:**
- YAML - declarative pathway configuration in `careflow/pathways/screening_to_care.yaml`, loaded with `yaml.safe_load` by `careflow/engine.py`.
- SQL (SQLite dialect) - schema DDL and parameterized queries embedded in `careflow/persistence.py`.
- Dockerfile/Makefile/Markdown - container build, developer commands, and project documentation in `Dockerfile`, `Makefile`, and `README.md`.

## Runtime

**Environment:**
- Python 3.12 - local runtime selected by `.python-version`; the container uses `python:3.12-slim` in `Dockerfile`.
- ASGI - the HTTP application is exposed as `careflow.api:app` and served by Uvicorn.

**Package Manager:**
- `pip` - dependencies are installed with `python -m pip install -r requirements.lock` as documented in `README.md` and in `Dockerfile`.
- Lockfile: present - `requirements.lock` pins the full resolved set; `requirements.txt` lists the six direct dependencies without transitive pins.

## Frameworks

**Core:**
- FastAPI 0.136.1 - HTTP application and route declarations in `careflow/api.py`.
- Pydantic 2.13.4 - request models, field validators, and model validators in `careflow/api.py`; it is pinned transitively in `requirements.lock`.
- Uvicorn 0.47.0 - ASGI development/container server invoked by `README.md` and `Dockerfile`.
- PyYAML 6.0.2 - safe parsing of pathway YAML in `careflow/engine.py`.

**Testing:**
- pytest 8.3.3 - test runner for `tests/test_engine.py`, `tests/test_api.py`, and `tests/test_persistence.py`.
- FastAPI `TestClient` with httpx 0.28.1 - synchronous API tests in `tests/test_api.py`; httpx is a test/client dependency rather than an application outbound HTTP client.

**Build/Dev:**
- Docker - reproducible runtime image defined by `Dockerfile`.
- Make - short commands in `Makefile` (`make test`, `make demo`, and `make seed-demo`).
- No Python packaging/build metadata such as `pyproject.toml`, `setup.py`, or a console-script entry point is present; run the package from the repository root with `python -m ...` as shown in `README.md`.

## Key Dependencies

**Critical:**
- `fastapi==0.136.1` - framework for the JSON API and application factory in `careflow/api.py`.
- `pydantic==2.13.4` and `pydantic-core==2.46.4` - validation of API input models in `careflow/api.py`.
- `uvicorn==0.47.0` - production/development ASGI process used by `Dockerfile`.
- `PyYAML==6.0.2` - pathway specification parsing in `careflow/engine.py`.
- `itsdangerous==2.2.0` - signs and verifies the `careflow_session` cookie used by the login/current-user flow in `careflow/api.py`.

**Infrastructure:**
- Python standard-library `sqlite3` - synchronous local persistence, transactions, foreign keys, and row mapping in `careflow/persistence.py`; no ORM or database driver is used.
- `httpx==0.28.1` - supports FastAPI's `TestClient` in `tests/test_api.py`; no application module makes an outbound httpx request.
- `starlette==0.46.2`, `anyio==4.13.0`, `h11==0.16.0`, and related packages - ASGI/runtime transitive dependencies pinned in `requirements.lock`.

## Configuration

**Environment:**
- Session signing can be supplied through `CAREFLOW_SESSION_SECRET` or the compatibility name `SESSION_SECRET`; session lifetime can be supplied through `CAREFLOW_SESSION_MAX_AGE` or `SESSION_MAX_AGE` in `careflow/api.py`.
- The API defaults to `careflow.db` for SQLite, loads `careflow/pathways/screening_to_care.yaml` by default, and accepts explicit `db_path`, `clock`, and `pathway` values through `create_app` in `careflow/api.py`.
- The source contains a development fallback session secret in `careflow/api.py`; production deployments must provide a non-default environment secret. Variable names and safe placeholder values are documented in `.env.example`; `.env` is ignored by `.gitignore`.

**Build:**
- `requirements.txt` - direct dependency declarations.
- `requirements.lock` - exact resolved Python dependency versions copied into the image by `Dockerfile`.
- `.python-version` - interpreter pin.
- `Dockerfile` - `python:3.12-slim` image, dependency installation, source copy, and Uvicorn command.
- `Makefile` - local test/demo/seed commands; `README.md` is the authoritative setup and run guide.

## Platform Requirements

**Development:**
- Python 3.12, pip, and a virtual environment are required; SQLite is provided by Python's standard library. The documented workflow is in `README.md`, and tests run with `python -m pytest -q` or `make test`.
- The repository must be run from its root so imports such as `from careflow.engine ...` resolve; the package is not installed as a wheel.

**Production:**
- A Linux-compatible Docker runtime can build and run the application from `Dockerfile`; the container exposes Uvicorn on `0.0.0.0:8000`.
- A writable local filesystem is required for the SQLite database path. No managed database, reverse proxy, process supervisor, or cloud deployment target is configured in the repository.

---

*Stack analysis: 2026-10-19*
