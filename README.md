# CareFlow (prototype)
Pathway-driven case orchestration for multi-party care journeys. First pathway: screening result to documented closure (LoopCare).

## Setup and checks

Use Python 3.12 (the pinned version is in `.python-version`) and install the locked dependencies into a project virtual environment:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pytest -q
```

Run the existing console demo with `python -m careflow.demo` (or `make demo`). Create or reset a local SQLite demo database with `python -m careflow.seed --db careflow.db` (or `make seed-demo`); the command writes 40 clearly synthetic cases, deterministic signed-session users for every role, case-scoped consents, pathway audit/evidence/approval records, and consent-checked notification outbox entries to `careflow.db`.

Start the API and server-rendered dashboard with `python -m uvicorn careflow.api:app --reload`. Open `/` for the click-through demo login page; its synthetic-user links create the same signed session used by `POST /auth/login`, without a password or external auth provider. The dashboard pages are `/worklist`, `/cases/{id}`, `/patient/{id}`, and `/metrics`. A browser's `Accept: text/html` receives Jinja2 HTML; requests with the default `*/*` or `application/json` continue to receive the existing JSON API payloads. Set `CAREFLOW_SESSION_SECRET` to a long random value and optionally `CAREFLOW_SESSION_MAX_AGE` (seconds); `.env.example` documents both settings. The app factory also accepts `session_secret` and `session_max_age` for isolated tests and deterministic runs.

The supported roles are `lab`, `screening_program`, `primary_care`, `specialist`, `care_coordinator`, `clinician_on_call`, and `patient`. Every transition uses the authenticated user id in the engine audit. A transition is allowed only for the current state's role owner or for a pathway approver (`clinician_on_call` or `care_coordinator`) who submits a matching documented approval. Assignments require an active user whose role owns the current state, and open cases always retain exactly one owner. Patients can read and mutate only their own case; operational worklists and metrics are staff-only. Successful case reads, mutations, worklist accesses, and metrics accesses are recorded in `access_logs`. Case notifications require a granted, unexpired case consent.

For a clean container build and run:

```bash
docker build -t careflow .
docker run --rm -p 8000:8000 -e CAREFLOW_SESSION_SECRET="$CAREFLOW_SESSION_SECRET" careflow
```

The image seeds the deterministic demo database during the build, so the signed-session users are available when the container starts. The container runs the FastAPI API and dashboard. Server-rendered pages use the existing signed-session authorization and case access logs; no Node build is required.

## Deploy

Build the image in the target container registry, then run it with a deployment-only session secret supplied by the platform's secret manager:

```bash
docker build -t registry.example/careflow:step-4 .
docker push registry.example/careflow:step-4
docker run -d --name careflow --restart unless-stopped -p 8000:8000 \
  -e CAREFLOW_SESSION_SECRET="$CAREFLOW_SESSION_SECRET" \
  registry.example/careflow:step-4
```

Replace `registry.example/careflow:step-4` with the target registry/image name; do not commit the session secret.

Guarantees (each tested): no closure without required evidence or a documented authorized human approval; every open case has an owner; overdue, rejected and escalated cases surface in the worklist (urgent first); every transition is audited; invalid transitions are rejected.

Limits: synthetic data only; patient message drafting is not implemented. The metrics page keeps audit-derived operational counts as-is and includes the existing illustrative simulation labelled “modelled from assumptions, not clinical evidence”; the step-6 metrics redesign is not implemented.
