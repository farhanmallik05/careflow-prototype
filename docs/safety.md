# Safety, privacy, and simulation limits

## Simulated-only scope

CareFlow is a local/demo prototype. The seeded users, patient names, email addresses, cases, timestamps, consent records, evidence, approvals, and notifications are synthetic. Seeded addresses use `example.test`; no record represents a real person or clinical encounter.

The prototype does **not**:

- diagnose, triage, recommend treatment, or make an autonomous clinical decision;
- connect to an EHR, laboratory, scheduling system, or external model service; the pilot has a provider-neutral managed OIDC seam but this repository's offline tests use only `FakeOIDCProvider`;
- send SMS, email, push, or other real patient notifications; or
- accept production patient/health information as a compliant system of record.

The dashboard and API are useful for demonstrating pathway controls and operational traceability. They are not a clinical product, a clinical decision-support tool, or a substitute for professional judgment.

## Human-governed escalation and closure

The pathway makes escalation visible rather than silently resolving it:

- `unreachable` after `patient_notified` and `no_show` after `appointment_booked` enter `escalated`.
- `escalated` is owned by the `clinician_on_call` role and requires the `human_decision` evidence to resolve back to `patient_notified`.
- A transition must be submitted by the authenticated current-state owner role, or by `clinician_on_call`/`care_coordinator` with a matching, non-blank documented approval.
- `care_received` cannot close without `closure_note`, unless an authorized approver supplies a documented approval that the engine records in the audit and `approvals` table.
- The message provider receives a copied, read-only case context. It cannot access the store, call a transition, close a case, escalate a case, or approve a message.

The worklist flags overdue and attention states and puts urgent cases first. It is a queue for human review, not an automated triage decision.

## Message and notification limits

The default `RuleBasedProvider` is deterministic and offline. It supports English, Hindi, and Bengali templates. Hindi and Bengali drafts carry the exact flag `needs clinical-language review`; staff must review drafts before approval. English output is not flagged by the provider, but it still remains a draft until an authorized human approves it.

Approval requires a case-scoped patient consent that is currently `granted` and not expired. The approval and outbox insert are atomic. The resulting `patient_message` row has status `queued` in SQLite. There is no delivery worker or external transport in this repository, so **an approved draft is not a sent notification**. The local outbox is a simulation record only.

Setting `CAREFLOW_MESSAGE_PROVIDER=llm` selects an intentionally unconfigured boundary. It does not make a network request or use an API key; normal requests fail clearly until a provider is injected by code.

## Privacy controls and remaining limits

The current code provides these demo-level controls:

- a server-managed opaque `careflow_session` handle for active local users, with pilot idle/absolute expiry, rotation, revocation, and local logout;
- a provider-neutral OIDC authorization-code/PKCE seam that rejects invalid state, nonce, issuer, audience, signature/key, expiry, claims, code, and mappings;
- pilot `Secure`/`HttpOnly`/`SameSite=Lax` cookies plus trusted-origin and session-bound CSRF checks for cookie mutations;
- patient isolation for case and patient-status reads;
- staff-only worklist and metrics access;
- message drafting restricted to staff, with approval restricted to the current case's owner role or an authorized approver;
- consent checks immediately before case notification/outbox writes; and
- successful case reads, mutations, worklist accesses, and metrics accesses recorded in `access_logs`.

These controls do not amount to production security or privacy compliance. CareFlow still does not own passwords or provide an upstream identity lifecycle, encryption-at-rest policy, key rotation, retention/deletion policy, rate limiting, or production audit export. A deployment must supply a real `OIDCProvider` adapter and a deployment-managed signing/session configuration. Use a deployment secret manager, keep `CAREFLOW_SESSION_SECRET` out of source and images, and keep the local SQLite database out of version control. Do not load real or sensitive data into this demo.

## SQLite recovery boundary

Phase 2 adds a versioned, integrity-checked SQLite lifecycle, but it remains a
single-host pilot control. Run `make migrate DB=careflow.db` before serving a
legacy file, `make verify-db DB=careflow.db` after upgrades, and create a
verified online snapshot with `make backup DB=careflow.db
BACKUP=careflow.backup.sqlite`. Restore to a new path with `make restore
BACKUP=careflow.backup.sqlite RESTORE_TARGET=careflow.restore.sqlite`; the
restore command checks schema, foreign keys, SQLite integrity, pathway loading,
case/audit continuity, consent, draft, and queued-outbox counts before it can be
selected. Never replace the only live file in place, and protect backup files
like the source database.

The supported topology is one CareFlow process with one Uvicorn worker on one
local writable filesystem. SQLite uses a bounded 5-second busy timeout and will
not be made highly available by adding workers, replicas, or a network-mounted
database. Persisted timestamps are UTC-aware ISO-8601 values with an explicit
offset; legacy naive timestamps are interpreted as UTC. Consent expires at the
exact `expires_at` boundary.

Transition authorization is deliberately role-based in the current prototype: an authenticated user with the current state's owner role may submit the transition even when their user ID is different from the case's assigned owner ID. The owner field still drives assignment, worklist display, and invariant checks; production deployment would need an explicit identity-to-assignment policy if that distinction matters.

## Metrics and simulation limits

Operational metrics are recomputed from persisted audit events, evidence, approvals, and pathway SLAs. Empty denominators are exposed as `None` with their sample size; current state alone is not treated as proof that a milestone occurred.

The dashboard's what-if table comes from `careflow.sim.compare()` with seeded random values and assumed drop-off/effect sizes. It is labelled **modelled from assumptions, not clinical evidence**. It describes illustrative scenarios only and must not be used to estimate clinical outcomes, safety, effectiveness, or resource requirements.

## Data handling for a demo

1. Use the deterministic seed (`python -m careflow.seed --db careflow.db`) to create the synthetic fixture.
2. Treat `careflow.db` as a local demo artifact (it is ignored by the repository) and remove exported cookie jars after use; neither should be uploaded with real data.
3. Use an environment-provided session secret for any shared/container run; do not write it to `.env`, the Docker image, or documentation.
4. Reset the demo database before a presentation if you need the known 40-case starting point.
