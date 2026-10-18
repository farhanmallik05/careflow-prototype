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

For a clean container build and run:

```bash
docker build -t careflow .
docker run --rm careflow
```

The container runs the console demo. This step does not include FastAPI or a UI.

Guarantees (each tested): no closure without required evidence or a documented authorized human approval; every open case has an owner; overdue, rejected and escalated cases surface in the worklist (urgent first); every transition is audited; invalid transitions are rejected.

Limits: synthetic data only; no auth, FastAPI, UI or AI drafting yet. SQLite persistence and a notification outbox are storage primitives for the next API step. `careflow/sim.py` uses illustrative assumptions: modelled results, not clinical evidence.
