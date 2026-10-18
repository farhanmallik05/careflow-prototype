# CareFlow (prototype)

CareFlow is a pathway-driven case-orchestration prototype for a screening-result-to-care journey. It makes the next responsible action, required evidence, owner role, and audit trail visible across one synthetic case pathway.

> **Simulation boundary:** this repository is for local/demo use with synthetic data. It does not diagnose, make clinical decisions, contact real patients, or send real notifications. Read [docs/safety.md](docs/safety.md) before presenting it as anything beyond a prototype.

## Reproducible setup

The supported runtime is Python 3.12, pinned in `.python-version`. `requirements.lock` is the reproducible install input; `requirements.txt` is the short, direct-dependency list.

On Linux or macOS:

```bash
python3.12 --version
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --requirement requirements.lock
```

On Windows PowerShell, use the same pinned interpreter and lockfile:

```powershell
py -3.12 --version
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --requirement requirements.lock
```

The application reads configuration from the process environment; `.env.example` documents the variable names but is not loaded automatically. For a local run, set a fresh session secret and keep it outside the repository:

```bash
export CAREFLOW_SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export CAREFLOW_SESSION_MAX_AGE=3600
export CAREFLOW_MESSAGE_PROVIDER=rule_based
```

`CAREFLOW_SESSION_SECRET` signs the `careflow_session` cookie. The default session lifetime is 3,600 seconds. `rule_based` is the deterministic, offline message provider used by tests and demos.

## Seed, run, and test

Create the deterministic SQLite fixture before opening the dashboard:

```bash
python -m careflow.seed --db careflow.db
# equivalent Makefile target:
make seed-demo
```

The seed command resets the target database, then creates 40 synthetic cases (`DEMO-001` through `DEMO-040`), six staff users, 40 patient users, case-scoped consent records, pathway audit/evidence/approval records, and consent-checked attention rows in `notification_outbox`. Seeded IDs use `demo-` and seeded email addresses use `example.test`; they are not real identities. Do not run the reset command against a database containing data you need to retain.

Start the API and server-rendered dashboard:

```bash
python -m uvicorn careflow.api:app --host 127.0.0.1 --port 8000 --reload
```

Open <http://127.0.0.1:8000/>. The landing page links to the seeded synthetic users. Staff links go to `/worklist`; the patient link goes to that patient's `/patient/{patient_id}` status page. The small console walkthrough is also available with `python -m careflow.demo` (or `make demo`).

Run the single project test command after dependencies are installed:

```bash
make test
```

`make test` runs `python -m pytest -q`. The tests use temporary SQLite databases and deterministic clocks/seeds; they do not call a network service. The direct equivalent is useful on machines without `make`:

```bash
python -m pytest -q
```

## Docker and deployment

The `Dockerfile` uses `python:3.12-slim`, installs exactly `requirements.lock`, copies the application, seeds `careflow.db` during the image build, and starts Uvicorn on `0.0.0.0:8000`. `.dockerignore` excludes any local database so the image always receives a fresh deterministic seed.

Build and run locally:

```bash
docker build -t careflow:local .
docker run --rm -p 8000:8000 \
  -e CAREFLOW_SESSION_SECRET="$CAREFLOW_SESSION_SECRET" \
  -e CAREFLOW_SESSION_MAX_AGE=3600 \
  -e CAREFLOW_MESSAGE_PROVIDER=rule_based \
  careflow:local
```

For a registry-based deployment, build and push the same image, then supply the deployment-only secret from the platform's secret manager:

```bash
docker build -t registry.example/careflow:step-7 .
docker push registry.example/careflow:step-7
docker run -d --name careflow --restart unless-stopped -p 8000:8000 \
  -e CAREFLOW_SESSION_SECRET="$CAREFLOW_SESSION_SECRET" \
  -e CAREFLOW_SESSION_MAX_AGE=3600 \
  -e CAREFLOW_MESSAGE_PROVIDER=rule_based \
  registry.example/careflow:step-7
```

Replace `registry.example/careflow:step-7` with the target image name. Never put the session secret in the image, source files, or committed configuration. Docker build/install needs the base image and locked packages available; after installation, the test suite and application itself do not require an external service.

## HTTP API and dashboard usage

Routes return JSON to the default `*/*` or `application/json` clients. Send `Accept: text/html` to the read routes that have a dashboard representation. The main dashboard pages are:

| Route | Purpose | Access |
| --- | --- | --- |
| `/` or `/login` | Synthetic-user landing page | Public |
| `/worklist` | Overdue, attention, and unassigned operational cases, urgent first | Authenticated staff |
| `/cases/{case_id}` | Case summary, next actions, evidence, timeline, and audit | Authenticated staff; the linked patient may read their own case |
| `/patient/{patient_id}` | Plain-language status for linked cases | Authenticated staff; a patient may read only their own status |
| `/metrics` | Current counts, audit-derived measures, and labelled simulation | Authenticated staff |

Example JSON read:

```bash
curl -sS -H 'Accept: application/json' \
  http://127.0.0.1:8000/cases/DEMO-001
```

### Auth and signed demo sessions

There is no password or external identity provider in this prototype. Login accepts an active seeded user ID and sets an `HttpOnly`, `SameSite=Lax` timed signed cookie named `careflow_session`:

```bash
curl -sS -i -c careflow.cookies \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"demo-lab"}' \
  http://127.0.0.1:8000/auth/login

curl -sS -b careflow.cookies http://127.0.0.1:8000/auth/current-user
curl -sS -X POST -b careflow.cookies http://127.0.0.1:8000/auth/logout
```

The browser's `/demo-login/{user_id}` links create the same signed session and redirect staff to `/worklist` or patients to their status page. The API also exposes `POST /auth/login`, `GET /auth/current-user`, and `POST /auth/logout` (with compatibility aliases documented by the OpenAPI route surface).

Seeded roles are `lab`, `screening_program`, `primary_care`, `specialist`, `care_coordinator`, `clinician_on_call`, and `patient`. The authenticated user ID, not the request body's optional `actor` label, is recorded in case audit events.

### Cases and transitions

Create a case as an authenticated user:

```bash
curl -sS -b careflow.cookies -H 'Content-Type: application/json' \
  -d '{"id":"CASE-API-001","patient_user_id":"demo-patient-001","urgent":true}' \
  http://127.0.0.1:8000/cases
```

Transitions are submitted to `POST /cases/{case_id}/transition` with `action`, optional `evidence`, and optional documented `approval`. For example, the `lab` role can notify a result:

```bash
curl -sS -b careflow.cookies -H 'Content-Type: application/json' \
  -d '{"action":"notify"}' \
  http://127.0.0.1:8000/cases/CASE-API-001/transition
```

The route reloads the case, authorizes the authenticated role, delegates the state change to `Case.transition`, and saves the snapshot plus ordered audit/evidence/approval records. The authenticated user's role must match the current state's owner role, or an authorized approver (`clinician_on_call` or `care_coordinator`) must submit a matching non-blank approval note. The check is role-based; the separately tracked owner ID is not an additional transition gate. Missing required evidence returns a transition error (HTTP 422); other illegal/conflicting transitions return HTTP 409. `POST /cases/{case_id}/assign` accepts `person`, `owner`, or `assignee`, but the target must be active and have the role required by the current state.

### Patient-message drafts and consent-gated outbox

Message drafting is a separate, review-controlled flow; it never calls `Case.transition` and cannot close or escalate a case. Staff can create and list drafts:

```bash
curl -sS -b careflow.cookies -H 'Content-Type: application/json' \
  -d '{"language":"en","template":"care_update"}' \
  http://127.0.0.1:8000/cases/CASE-API-001/message-drafts

curl -sS -b careflow.cookies \
  http://127.0.0.1:8000/cases/CASE-API-001/message-drafts
```

The default `RuleBasedProvider` is deterministic and offline. Supported languages are English (`en`), Hindi (`hi`), and Bengali (`bn`); Hindi and Bengali drafts carry `review_required: true` and the exact flag `needs clinical-language review`. All drafts start as `pending_approval`. `POST /message-drafts/{draft_id}/approve` requires an allowed staff role, a non-blank approval note, and a granted, unexpired consent for the case's patient:

```bash
curl -sS -b careflow.cookies -H 'Content-Type: application/json' \
  -d '{"note":"Reviewed by the authenticated care team member"}' \
  http://127.0.0.1:8000/message-drafts/{draft_id}/approve
```

Approval atomically marks the draft approved and inserts one `patient_message` row in `notification_outbox` with status `queued`. The prototype has no delivery worker or external transport, so no message is sent. Selecting `CAREFLOW_MESSAGE_PROVIDER=llm` only selects an unconfigured boundary and returns a clear 503; it does not make a network request.

### Metrics

`GET /metrics` is staff-only. It returns current totals/state counts and an `observed_metrics` section derived from each persisted case's ordered audit events, evidence, approvals, and pathway SLAs. It exposes sample sizes and `None` for rates/durations with empty denominators. Measures include time to notification, acknowledgement rate, time to referral acceptance, referral completion, overdue count, documented closure rate, and delay/drop-off reasons. The page also renders `careflow.sim.compare()` separately as `modelled from assumptions, not clinical evidence`; it is not an observed or clinical outcome measure.

## Invariants and controls

These are the behavior-level guarantees covered by the current engine/API/persistence tests:

- The YAML pathway is validated before use: every state has an owner and positive SLA, and every transition target exists.
- Only a declared action from the current state can run. A closed case cannot transition again.
- Required evidence is enforced for acknowledgement, clinical review, acceptance, appointment booking, attendance, and closure. A documented approval from an authorized approver may waive a requirement; the approval role must match the authenticated role.
- Every successful case opening, assignment, and transition is retained in the ordered audit; rejected transitions do not mutate the case. Successful case reads, mutations, worklist accesses, and metrics accesses are recorded in `access_logs`.
- Every open case has an owner. A closed case has no owner. Assignment requires an active user whose role owns the current state.
- Patients can read only their own linked case/status. Patients cannot use operational worklists, metrics, message drafting, or message approval. Staff-only operational reads are enforced by the API.
- `unreachable` and `no_show` route to `escalated`; the escalated state is owned by `clinician_on_call` and requires `human_decision` to resolve. The worklist includes overdue/attention cases and orders urgent cases first.
- Case notifications and message approval are consent-gated by a granted, unexpired case/user consent. Approved drafts remain queued in the local outbox; there is no real notification path.

More detail is in [docs/architecture.md](docs/architecture.md), [docs/safety.md](docs/safety.md), and the three-minute [docs/demo-script.md](docs/demo-script.md).
