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

Run the existing console demo with `python -m careflow.demo` (or `make demo`). Create or reset a local SQLite demo database with `python -m careflow.seed --db careflow.db` (or `make seed-demo`); the command writes 40 clearly synthetic cases, demo users and case-scoped consents, pathway audit/evidence/approval records, and queued notification outbox entries to `careflow.db`.

Start the step-2 API with `python -m uvicorn careflow.api:app --reload` and use the routes `POST /cases`, `POST /cases/{case_id}/transition`, `POST /cases/{case_id}/assign`, `GET /cases/{case_id}`, `GET /worklist`, and `GET /metrics`. The app factory is `careflow.api.create_app(db_path, clock, pathway)` for isolated tests and deterministic runs. Invalid engine transitions and missing closure evidence/approval return JSON 4xx responses.

For a clean container build and run:

```bash
docker build -t careflow .
docker run --rm -p 8000:8000 careflow
```

The container runs the FastAPI API. There is no auth or UI yet.

Guarantees (each tested): no closure without required evidence or a documented authorized human approval; every open case has an owner; overdue, rejected and escalated cases surface in the worklist (urgent first); every transition is audited; invalid transitions are rejected.

Limits: synthetic data only; no auth, UI or AI drafting yet. `careflow/sim.py` uses illustrative assumptions: modelled results, not clinical evidence.
