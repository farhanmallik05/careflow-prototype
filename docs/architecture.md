# CareFlow architecture and data flow

## System boundary

CareFlow is a small Python monolith with four explicit boundaries:

1. `careflow/pathways/screening_to_care.yaml` declares the state graph, owner roles, SLAs, attention flags, terminal state, approver roles, and required transition evidence.
2. `careflow/engine.py` is the domain authority. `Pathway` validates the graph; `Case.transition` and `Case.assign` are the only normal mutation methods; `explain` and `worklist` calculate operational decisions.
3. `careflow/api.py` is the FastAPI adapter. It validates request bodies, verifies the timed signed session cookie, checks case/role access, calls the engine, and serializes JSON or Jinja2 HTML.
4. `careflow/persistence.py` is the SQLite repository. It stores the current case snapshot alongside ordered audits and normalized operational records. Each public write uses a SQLite transaction.

`careflow/seed.py` and `careflow/demo.py` exercise the same pathway, engine, and repository boundaries. `careflow/ai.py` is a read-only content provider boundary; it never receives a `Case` or `SQLiteStore` and cannot mutate workflow state.

## Request and mutation flow

```text
Browser or API client
        |
        v
FastAPI route (Pydantic input + signed-session authentication)
        |
        v
Case access / role / patient-isolation checks
        |
        v
SQLiteStore.load_case()  ---> current snapshot + ordered audit + evidence
        |
        v
Case.transition() or Case.assign()  ---> state/evidence/approval validation
        |
        v
SQLiteStore.save_case()  ---> cases + audit_events + evidence + approvals
        |
        v
JSON payload or server-rendered HTML
```

For `POST /cases`, the route constructs a new `Case` at `result_issued`, assigns an active `lab`-role owner, records the opening audit event, and saves it. For a transition, the route rehydrates the snapshot, passes the authenticated user ID and role to the engine, and saves only after the engine accepts the action. The optional request `actor` label is not used as the audit identity. Transition authorization is role-based: the authenticated role must match the current state's owner role, but the assigned owner ID is not an additional transition gate.

## Pathway data flow

The configured screening-to-care graph is:

| Current state | Owner role | Allowed next action(s) and proof |
| --- | --- | --- |
| `result_issued` | `lab` | `notify` → `patient_notified` |
| `patient_notified` | `screening_program` | `acknowledge` → `patient_acknowledged` (`ack_record`), or `unreachable` → `escalated` |
| `patient_acknowledged` | `primary_care` | `review` → `referral_created` (`clinical_review`) |
| `referral_created` | `care_coordinator` | `accept` → `referral_accepted` (`acceptance`), or `reject` → `referral_rejected` |
| `referral_rejected` | `care_coordinator` | `reassign` → `referral_created` |
| `referral_accepted` | `specialist` | `book` → `appointment_booked` (`appointment`) |
| `appointment_booked` | `specialist` | `attend` → `care_received` (`attendance`), or `no_show` → `escalated` |
| `care_received` | `care_coordinator` | `close` → `closed` (`closure_note`) |
| `escalated` | `clinician_on_call` | `resolve` → `patient_notified` (`human_decision`) |
| `closed` | — | Terminal; no further transition |

The pathway allows `clinician_on_call` and `care_coordinator` to submit a matching, non-blank documented approval. That approval can satisfy a missing transition evidence requirement and is copied into the audit and `approvals` table.

## Persistence model

`SQLiteStore` keeps the state needed for a reproducible local run in one database:

| Record | Purpose |
| --- | --- |
| `users` | Active/inactive demo identities and roles |
| `cases` | Current snapshot: state, owner, patient link, urgent flag, timestamps |
| `audit_events` | Ordered opening, assignment, and transition history |
| `evidence` | Case-level evidence kinds and provenance |
| `approvals` | Human approval role, note, name, and timestamp |
| `consents` | Patient/case-scoped status and optional expiry |
| `message_drafts` | Provider output awaiting human approval |
| `notification_outbox` | consent-gated queued events; no transport worker is present |
| `access_logs` | Successful authenticated case reads and operational reads/mutations |

`save_case` rejects an unknown state, an open case without an owner, or a closed case with an owner before writing. `load_case` rehydrates the stored audit/evidence rather than replaying transitions. The audit is therefore the source used by `careflow.metrics.compute_metrics` for observed measures.

### SQLite lifecycle and recovery

`SQLiteStore` applies numbered, forward-only migrations under `BEGIN IMMEDIATE`.
The baseline migration records the existing pre-versioned schema without
rewriting its rows; the current lifecycle metadata migration records the UTC
timestamp convention and supported one-process/one-worker topology. Revision
history is stored in `schema_migrations` and mirrored in `PRAGMA user_version`.
Migration checksums make edited or incomplete history fail closed. The store
uses a 5-second busy timeout, `foreign_keys=ON`, and `synchronous=FULL`.

Readiness runs the supported-revision check, migration-history check,
`PRAGMA integrity_check`, `PRAGMA foreign_key_check`, and a filesystem write
probe. The app does not report ready for a behind, future, partial, corrupt,
foreign-key-invalid, or unwritable database. Operators use `python -m
careflow.ops migrate` before serving a legacy file and `verify` after upgrades.

Backups use SQLite's online `Connection.backup()` API rather than copying a live
main file. The destination is written to a temporary path, verified, then
atomically renamed. `restore` requires a new target path and verifies the
schema, pathway loading, every case audit, and counts for cases, audits,
consents, access logs, message drafts, and the local notification outbox before the restored
file is selected. No recovery command sends a notification or changes pathway
state.

All timestamps crossing the engine/repository boundary are timezone-aware UTC;
legacy naive values are interpreted as UTC and persisted with an explicit
`+00:00` offset. Consent expiry is exclusive: a check at exactly `expires_at`
is not valid.

## Message and outbox flow

```text
POST /cases/{id}/message-drafts
        |
        v
RuleBasedProvider(context copy, language, template)
        |
        v
message_drafts(status=pending_approval)
        |
        | human approval + valid patient consent
        v
message_drafts(status=approved) + notification_outbox(status=queued)
```

The default `RuleBasedProvider` supports `en`, `hi`, and `bn`. Hindi and Bengali output carries `needs clinical-language review`. Draft creation does not save the case or append a workflow audit event. Approval checks the current case role boundary and a granted, unexpired consent immediately before the atomic draft/outbox write. No API route sends the queued row.

## Reads and metrics

`GET /worklist` loads every case, calls `engine.worklist`, and records a successful worklist access for each database case it loads. The worklist includes overdue, attention, or unassigned open cases and sorts urgent cases before the most overdue cases.

`GET /metrics` loads every case and calls `compute_metrics` with the configured pathway and current clock. Observed durations, rates, documented closure, overdue counts, and delay/drop-off reasons are calculated from ordered audit events plus pathway rules; current snapshots are not treated as proof of a milestone. The separate `careflow.sim.compare()` table is a deterministic what-if simulation labelled as assumption-based, not clinical evidence.

## Controls at each boundary

- **Configuration:** `Pathway.validate` rejects malformed graphs before the app starts.
- **Transport:** Pydantic models reject blank actions, unsupported message languages/templates, and inconsistent IDs.
- **Authentication:** active users receive a timed `careflow_session` signed with `CAREFLOW_SESSION_SECRET`.
- **Authorization:** patients are isolated to their own linked case; worklist/metrics are staff-only; transitions require the current state's owner role or a matching authorized approval (role-based, separate from the owner ID); message approval uses the same owner/approver boundary.
- **Workflow safety:** the engine rejects illegal actions, closed-case changes, missing required evidence, and unauthorized approvals.
- **Persistence:** snapshot, audit, evidence, and approval writes are synchronized in a transaction; ownership invariants are checked before write.
- **Notification safety:** case notifications require granted, unexpired consent and are represented as queued outbox rows only.
- **Traceability:** successful reads/mutations are recorded in `access_logs`; workflow mutations are retained in the case audit and related evidence/approval tables.
