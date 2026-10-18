# Technology Stack

**Analysis Date:** 2026-10-19

## Languages

**Primary:**
- Python 3.12 - the supported runtime for the application package, tests, seed/demo commands, simulation, API, engine, persistence, AI-provider boundary, and metrics code under `careflow/` and `tests/`; the repository pin is `.python-version`.

**Secondary:**
- YAML - the declarative screening-to-care pathway in `careflow/pathways/screening_to_care.yaml`, parsed with `yaml.safe_load` by `careflow/engine.py`.
- SQL (SQLite dialect) - schema DDL and parameterized repository queries embedded in `careflow/persistence.py`.
- HTML/CSS - server-rendered Jinja2 templates in `careflow/templates/` and the stylesheet in `careflow/static/style.css`.
- Dockerfile/Make/Markdown - reproducible container instructions, project commands, and operator documentation in `Dockerfile`, `Makefile`, `README.md`, and `docs/`.

## Runtime

**Environment:**
- Python 3.12 - supported interpreter selected by `.python-version`; the container currently uses the mutable `python:3.12-slim` tag in `Dockerfile`.
- ASGI - `careflow.api:app` is served by Uvicorn; `careflow/app.py` provides a compatibility re-export.

**Package Manager:**
- `pip` - the documented install command is `python -m pip install --requirement requirements.lock` in `README.md` and `Dockerfile`.
- Lockfile: present - `requirements.lock` contains the resolved direct and transitive versions; `requirements.txt` contains the seven direct dependencies.

## Frameworks

**Core:**
- FastAPI 0.136.1 - application factory, JSON routes, exception handling, static files, and ASGI lifecycle in `careflow/api.py`.
- Jinja2 3.1.6 - explicit `Environment`/`FileSystemLoader` setup and server-rendered dashboard pages in `careflow/api.py` and `careflow/templates/*.html`.
- Pydantic 2.13.4 with `pydantic-core==2.46.4` - locked transitive validation for request models in `careflow/api.py`.
- Uvicorn 0.47.0 - local and container ASGI process launched by `README.md` and `Dockerfile`.
- PyYAML 6.0.2 - pathway specification loading in `careflow/engine.py`.

**Testing:**
- pytest 8.3.3 - test runner for the separate `tests/` suite.
- FastAPI `TestClient` with httpx 0.28.1 - synchronous in-process API/HTML tests in `tests/test_api.py`, `tests/test_auth.py`, `tests/test_messages.py`, and `tests/test_html.py`; the application has no outbound HTTP client.

**Build/Dev:**
- Docker - runtime image definition and build-time deterministic seeding in `Dockerfile`.
- Make - the only project command aliases in `Makefile`: `make test`, `make demo`, and `make seed-demo`.
- No `pyproject.toml`, `setup.py`, package build metadata, formatter, linter, or console-script entry point is present; run modules from the repository root with `python -m ...`.

## Key Dependencies

**Critical:**
- `fastapi==0.136.1` - HTTP boundary and application assembly in `careflow/api.py`.
- `Jinja2==3.1.6` - dashboard, patient status, landing, metrics, and error rendering in `careflow/templates/`.
- `itsdangerous==2.2.0` - timed signing/verification of the `careflow_session` cookie in `careflow/api.py`.
- `PyYAML==6.0.2` - pathway graph and policy parsing in `careflow/engine.py`.
- `uvicorn==0.47.0` - ASGI server process for local and Docker runs.

**Infrastructure:**
- Python standard-library `sqlite3` - the synchronous `SQLiteStore`, schema, foreign keys, transactions, and row mapping in `careflow/persistence.py`; there is no ORM or external database driver.
- `httpx==0.28.1` - FastAPI `TestClient` support only; no production module performs an outbound httpx request.
- `pydantic`, `pydantic-core`, `starlette`, `anyio`, `h11`, `click`, and related packages - transitive ASGI/validation dependencies pinned in `requirements.lock`.
- `pytest==8.3.3` - offline verification; tests use temporary SQLite databases, injected clocks, deterministic pathways, and seeded random values rather than network services.

## Configuration

**Environment:**
- Session signing reads `CAREFLOW_SESSION_SECRET` first, then `SESSION_SECRET`; lifetime reads `CAREFLOW_SESSION_MAX_AGE` or `SESSION_MAX_AGE`. These are resolved in `create_app` in `careflow/api.py`.
- Message drafting reads `CAREFLOW_MESSAGE_PROVIDER`; `rule_based` is the deterministic offline default and `llm` selects an intentionally unconfigured boundary unless a provider is injected in code (`careflow/ai.py`).
- The API defaults to `careflow.db` and `careflow/pathways/screening_to_care.yaml`, while `create_app` accepts explicit `db_path`, `clock`, `pathway`, `session_secret`, `session_max_age`, and `message_provider` values for isolation and tests.
- `.env.example` exists as a configuration template and `.env` is ignored by `.gitignore`; no secret file is part of the source scan.

**Build:**
- `requirements.txt` - direct dependency pins.
- `requirements.lock` - exact resolved versions installed by the container and clean-machine setup.
- `.python-version` - Python 3.12 runtime pin.
- `Dockerfile` - base image, locked dependency install, source copy, deterministic seed, and Uvicorn command.
- `.dockerignore` - excludes local databases, virtual environments, caches, and local secrets from the Docker context.
- `Makefile` and `README.md` - the canonical local test, seed, demo, run, and Docker usage instructions.

## Platform Requirements

**Development:**
- Python 3.12, pip, and a project-local virtual environment are required by `README.md`; SQLite is supplied by the Python standard library.
- Run from the repository root because the package is not installed as a wheel. The offline test command is `make test` (equivalent to `python -m pytest -q`).

**Production:**
- A Linux-compatible Docker runtime can build and run the image from `Dockerfile`; Uvicorn listens on `0.0.0.0:8000`.
- The process needs a writable filesystem for the SQLite path. No managed database, reverse proxy, process supervisor, cloud deployment manifest, or external service is configured.

## Current Gaps and Milestone Assumptions

- The runtime is pinned only to Python 3.12 at the major/minor level, the Docker base image uses a floating `python:3.12-slim` tag, and `requirements.lock` has version pins but no hashes. These are current reproducibility limitations, not implemented guarantees.
- The Dockerfile currently runs as root, copies the build context, seeds a database during image build, and does not define a health check or data volume. `.dockerignore` excludes `careflow.db` but does not exclude all development/planning content.
- The `.planning/PROJECT.md` active requirements for managed OIDC/SSO, fail-closed production secrets, migrations/backup, health diagnostics, and deployment runbooks are milestone targets; no provider, migration framework, or deployment integration is present in the current stack.

---

*Stack analysis: 2026-10-19*
