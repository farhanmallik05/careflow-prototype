# CareFlow (prototype)
Pathway-driven case orchestration for multi-party care journeys. First pathway: screening result to documented closure (LoopCare).

Run: `pip install -r requirements.txt && make test && make demo` (or `docker build -t careflow . && docker run careflow`).

Guarantees (each tested): no closure without required evidence or a documented authorized human approval; every open case has an owner; overdue, rejected and escalated cases surface in the worklist (urgent first); every transition is audited; invalid transitions are rejected.

Limits: synthetic data only; no auth, UI, notifications or AI drafting yet. `careflow/sim.py` uses illustrative assumptions: modelled results, not clinical evidence.
