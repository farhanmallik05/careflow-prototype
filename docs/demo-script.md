# Three-minute judge demo: one case, one escalation, one documented closure

This script uses only seeded synthetic users and one newly created synthetic case. It demonstrates the API and the dashboard without sending a notification. Start the app once before the timer:

```bash
python -m careflow.seed --db careflow.db
export CAREFLOW_SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export CAREFLOW_MESSAGE_PROVIDER=rule_based
python -m uvicorn careflow.api:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/> in a browser and keep a terminal in the project directory. The browser and the terminal use separate cookies, so click a synthetic staff login link in the browser before opening the case page.

## 0:00–0:25 — show the boundary and open a result

In the browser, point out the “synthetic operational data only” footer, then click **Demo Lab Service**. In the terminal, run this small helper and create a fresh case linked to the seeded synthetic patient:

```bash
BASE_URL=http://127.0.0.1:8000
COOKIE_JAR=careflow-demo.cookies
rm -f "$COOKIE_JAR"
: > "$COOKIE_JAR"
trap 'rm -f "$COOKIE_JAR"' EXIT

login() {
  curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
    -H 'Content-Type: application/json' \
    -d "{\"user_id\":\"$1\"}" \
    "$BASE_URL/auth/login"
  printf '\n'
}

post() {
  curl -sS -b "$COOKIE_JAR" \
    -H 'Content-Type: application/json' \
    -d "$2" -w '\nHTTP %{http_code}\n' \
    "$BASE_URL$1"
}

login demo-lab
post /cases '{"id":"JUDGE-001","patient_user_id":"demo-patient-001"}'
post /cases/JUDGE-001/transition '{"action":"notify"}'
```

Call out the response state `patient_notified`, owner `demo-screening`, and the opening/notify audit entries. In the browser, open `/cases/JUDGE-001` after the case is created; the page exposes the same state, owner, next action, evidence section, timeline, and audit.

## 0:25–1:05 — deliberately escalate, then resolve by a human role

Use the configured pathway's `unreachable` branch, then show that the escalated owner role can resolve it with the required human decision (an authorized approver could also resolve with a matching approval):

```bash
login demo-screening
post /cases/JUDGE-001/transition '{"action":"unreachable"}'
# Expected: HTTP 200 and "state":"escalated", "owner":"demo-on-call"

login demo-on-call
post /cases/JUDGE-001/transition \
  '{"action":"resolve","evidence":["human_decision"]}'
# Expected: HTTP 200 and "state":"patient_notified", with owner demo-screening
```

Pause on the escalation response and explain that no automatic clinical decision was made: the case moved to `escalated`, the worklist will flag it, and the authenticated `clinician_on_call` role supplied the human decision evidence. Refresh the case page to show both audit events.

## 1:05–1:55 — walk the synthetic journey with required evidence

Run the remaining declared transitions. Each command changes the owner to the next pathway role and records the supplied evidence:

```bash
login demo-screening
post /cases/JUDGE-001/transition '{"action":"acknowledge","evidence":["ack_record"]}'

login demo-primary-care
post /cases/JUDGE-001/transition '{"action":"review","evidence":["clinical_review"]}'

login demo-coordinator
post /cases/JUDGE-001/transition '{"action":"accept","evidence":["acceptance"]}'

login demo-specialist
post /cases/JUDGE-001/transition '{"action":"book","evidence":["appointment"]}'
post /cases/JUDGE-001/transition '{"action":"attend","evidence":["attendance"]}'
```

The expected state sequence is `patient_acknowledged` → `referral_created` → `referral_accepted` → `appointment_booked` → `care_received`. The case page's Evidence table and Timeline make the proof visible rather than inferring it from the current state.

## 1:55–2:30 — show closure blocked by the invariant

The coordinator owns `care_received`. Attempt closure without the required `closure_note`:

```bash
login demo-coordinator
post /cases/JUDGE-001/transition '{"action":"close"}'
```

Expected result:

```text
HTTP 422
"error":"transition_error"
"detail":"missing evidence ['closure_note']..."
```

Refresh or fetch the case and point out that it is still `care_received`; the failed request did not append a transition or close the case.

## 2:30–3:00 — document the human closure and verify the dashboard

Submit a matching documented approval from the authenticated coordinator. This is the explicit human-governed closure path:

```bash
post /cases/JUDGE-001/transition \
  '{"action":"close","approval":{"role":"care_coordinator","name":"Demo Care Coordinator","note":"Reviewed closure documentation for JUDGE-001"}}'

curl -sS -b "$COOKIE_JAR" "$BASE_URL/cases/JUDGE-001"
```

Expected final fields are `"state":"closed"`, `"closed":true`, and `"owner":null`. In the browser, refresh `/cases/JUDGE-001` and point to the final Audit row: it contains the approval note. Finish on `/metrics` to show that the closure is counted from the persisted audit, and remind the judge that any message draft would only create a consent-gated `queued` outbox row—**no real notification is sent**.

## What the demo proves

- A screening result follows a declared pathway rather than arbitrary route-side state changes.
- An unreachable patient path becomes an explicit escalation owned by `clinician_on_call`.
- Required evidence blocks closure and leaves the case unchanged.
- A matching human approval can document and authorize closure; the audit remains inspectable.
- The dashboard, API response, and audit-derived metrics read the same SQLite-backed synthetic record.
