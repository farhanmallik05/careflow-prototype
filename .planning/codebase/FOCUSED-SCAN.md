# Focused Codebase Scan Summary

**Analysis Date:** 2026-10-19
**Focus:** `tech` + `arch`
**Repository:** `careflow-prototype`

## Scope and verification

This focused scan read the current application package, pathway YAML, tests, templates/static assets, `README.md`, `Dockerfile`, `Makefile`, dependency manifests, `.planning/PROJECT.md`, and the tracked `.planning/codebase/` artifacts. It did not read secret files or modify application source/tests.

The repository's supported runtime is Python 3.12 from `.python-version`. The local verification command `python -m pytest -q` completed with **39 passed** during this scan; the host interpreter reported Python 3.13.14, so this result does not replace a clean Python 3.12/Docker verification.

## Current boundary map

- **Pathway policy:** `careflow/pathways/screening_to_care.yaml` declares the screening-to-care graph, owners, SLAs, attention states, approvers, terminal state, and required evidence.
- **Engine:** `careflow/engine.py` owns `Pathway`, `Case`, `TransitionError`, audit entries, evidence/approval checks, ownership invariants, `explain`, and urgent-first `worklist` behavior.
- **Persistence:** `careflow/persistence.py` exposes `SQLiteStore` over one synchronous `sqlite3` connection. It stores cases, users, consents, evidence, approvals, ordered `audit_events`, `message_drafts`, `access_logs`, and a consent-gated `notification_outbox`.
- **HTTP/UI:** `careflow/api.py` assembles the app, verifies timed `itsdangerous` cookies, applies patient/staff and pathway-role checks, delegates mutations to the engine, and returns JSON or Jinja2 HTML from `careflow/templates/`. `careflow/app.py` is only a compatibility re-export.
- **AI boundary:** `careflow/ai.py` provides deterministic offline `RuleBasedProvider` output for `en`, `hi`, and `bn`; `LLMProvider` is an injected, unconfigured seam and makes no network call.
- **Metrics/simulation:** `careflow/metrics.py` recomputes observed measures from ordered audit events, proof, and pathway SLAs. `careflow/sim.py` is separate seeded what-if modelling and is labelled as assumption-based.
- **Fixtures/commands:** `careflow/seed.py` deterministically creates 40 synthetic cases, 46 users, case-scoped consent, evidence/audit/approval records, and attention outbox rows; `careflow/demo.py` runs a console journey.

## Reproducibility surface

- `requirements.txt` directly pins FastAPI, HTTPX, itsdangerous, Jinja2, PyYAML, pytest, and Uvicorn; `requirements.lock` pins the resolved transitive set.
- `Dockerfile` uses `python:3.12-slim`, installs the lock, copies the context, seeds `careflow.db` at build time, and starts Uvicorn on port 8000.
- `Makefile` exposes the single test command `make test` plus `make demo` and `make seed-demo`; `README.md` documents local setup, API/dashboard use, Docker run, safety, and deployment examples.
- `.dockerignore` excludes local DBs, virtual environments, caches, and `.env`; `.gitignore` excludes local DBs, caches, virtual environments, and `.env`. `.env.example` exists as a template; no secret value was read or written.

## Current gaps versus milestone assumptions

- Signed sessions are demo identity selection: `/auth/login` accepts an active local user ID, not a password or OIDC/SSO assertion. If no secret env var is supplied, `create_app` generates a process-local random signing secret. Cookie `Secure`, CSRF protection, external identity, session revocation, and owner-ID enforcement are not implemented.
- SQLite has no schema version/migration path, backup/restore integration, connection lock/concurrency policy, optimistic versioning, or multi-process topology. `load_case` rehydrates stored rows without replaying the entire transition history.
- The outbox is a local, consent-gated queue. There is no delivery worker, external transport, claim/retry workflow, or webhook. Approved patient drafts remain `queued` and are not sent.
- Docker remains a development-grade container contract: floating base tag, unhashed dependency artifacts, root process, build-time database seed, no health check, and no deliberate persistent volume. No CI/CD or external hosting manifest exists.
- `.planning/PROJECT.md` active requirements for managed identity, fail-closed production configuration, consistent authorization, migrations/operations, structured diagnostics, retention, and deployment runbooks are milestone assumptions rather than current implementation.

## Existing artifact note

The focused refresh updates the technology/architecture artifacts: `.planning/codebase/STACK.md`, `.planning/codebase/INTEGRATIONS.md`, `.planning/codebase/ARCHITECTURE.md`, and `.planning/codebase/STRUCTURE.md`. Existing `.planning/codebase/CONCERNS.md`, `.planning/codebase/CONVENTIONS.md`, and `.planning/codebase/TESTING.md` were inspected but are outside this `tech` + `arch` scan; some contain older claims that should be refreshed by a later quality/concerns mapper (for example, the current suite has 39 tests and `itsdangerous` is now declared in both dependency manifests).

---

*Focused scan: 2026-10-19*
