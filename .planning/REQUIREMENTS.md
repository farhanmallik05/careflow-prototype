# Requirements: CareFlow

**Defined:** 2026-10-19
**Core Value:** Patients and care teams can move a screening case safely from result to documented next action or closure, with every responsibility, permission, decision, and communication traceable.

## v1 Requirements

Requirements for the production-readiness pilot milestone. Each requirement is atomic, user-observable, and maps to exactly one roadmap phase.

### Configuration and Reproducibility

- [ ] **CONF-01**: An operator can start CareFlow in an explicit `demo` or `pilot` mode, and the selected mode is visible in safe startup/readiness diagnostics.
- [ ] **CONF-02**: Pilot mode refuses to serve protected functionality when required OIDC, session, trusted-origin, or data-path configuration is missing or unsafe.
- [ ] **CONF-03**: The application accepts one canonical, typed configuration surface for runtime, database, session, OIDC, logging, and synthetic-data settings, with placeholder-only values in `.env.example`.
- [ ] **CONF-04**: A clean checkout can install the pinned Python runtime dependencies without global packages, absolute machine paths, network-only test fixtures, or committed secrets.
- [ ] **CONF-05**: Demo seed/reset behavior is unavailable against a declared pilot data path, and the runtime displays an unambiguous synthetic-data-only boundary.

### Identity and Sessions

- [ ] **AUTH-01**: A pilot user can sign in through a configured OIDC authorization-code flow with PKCE and an exact registered redirect URI.
- [ ] **AUTH-02**: CareFlow rejects OIDC callbacks with invalid or missing state, nonce, issuer, audience, signature, expiry, authorization code, or required identity claims.
- [ ] **AUTH-03**: A verified `(issuer, subject)` identity resolves to an approved local CareFlow user without using mutable email or a caller-supplied user ID as the identity key.
- [ ] **AUTH-04**: An unmapped, inactive, ambiguous, or role-unassigned external identity cannot obtain a CareFlow session.
- [ ] **AUTH-05**: CareFlow creates an opaque, server-managed session that stores only a high-entropy cookie handle in the browser and supports rotation, idle expiry, absolute expiry, revocation, and logout.
- [ ] **AUTH-06**: Session cookies use explicit secure attributes, and cookie-authenticated state changes reject missing or invalid CSRF and trusted-origin protections.
- [ ] **AUTH-07**: The existing synthetic demo login is explicitly opt-in for demo/test mode and is unavailable in pilot mode.
- [ ] **AUTH-08**: Local logout revokes the CareFlow session and clears the cookie without depending on upstream identity-provider availability, and the UI does not claim that upstream SSO has ended.
- [ ] **AUTH-09**: Role and permission changes or local deactivation take effect on the next request without trusting stale role claims in an existing browser session.

### Authorization and Safe Views

- [ ] **AUTHZ-01**: Every HTML page, JSON route, compatibility alias, collection, worklist, metric, message-draft operation, audit view, and access-log view has an explicit server-side authorization classification.
- [ ] **AUTHZ-02**: Authorization is deny-by-default and evaluates authentication, role/permission, resource relationship, requested action, and current case scope before data serialization.
- [ ] **AUTHZ-03**: A patient can read only linked cases and patient-safe status information, and cannot discover or mutate another patient's case through guessed or substituted identifiers.
- [ ] **AUTHZ-04**: Staff access is limited to the documented pilot scope of assigned/team-authorized cases, and a same-role non-owner cannot mutate a case solely because their role owns its current state.
- [ ] **AUTHZ-05**: Worklists, metrics, patient pages, case pages, drafts, evidence, approvals, audits, and access logs contain only records the requesting principal is authorized to see.
- [ ] **AUTHZ-06**: Patient-safe and staff-safe projections are separate, and patient responses never expose internal notes, staff-only audit details, approval notes, evidence provenance, raw claims, or message drafts unless explicitly approved for that audience.
- [ ] **AUTHZ-07**: HTML and JSON representations make the same authorization decision; only successful representation formatting and safe error presentation vary by `Accept` header.
- [ ] **AUTHZ-08**: Authentication, authorization, missing-resource, validation, conflict, and service failures return stable non-sensitive responses with a correlation identifier and without stack traces, secrets, database paths, or patient content.

### Case Integrity, Consent, and Messaging

- [ ] **CASE-01**: The authenticated principal and active local role, rather than request-body actor or role fields, are the authoritative identity for case transitions, assignments, evidence, approvals, message drafts, and audit records.
- [ ] **CASE-02**: Evidence records are created or validated server-side and are bound to the case, action/state, authenticated principal, timestamp, and provenance before they can satisfy a pathway requirement.
- [ ] **CASE-03**: Approval records are bound to the verified approver, required role, case, action, note, timestamp, and applicable pathway rule; a caller-supplied approval name cannot authorize a transition.
- [ ] **CASE-04**: Concurrent case mutations cannot silently overwrite a newer state, owner, evidence set, approval, or audit sequence; one request wins and a conflicting request receives an actionable conflict response.
- [ ] **CASE-05**: Consent is represented with scoped, versioned grant/withdraw/expiry history and is checked immediately before any patient-message approval or queue operation.
- [ ] **CASE-06**: Approved message drafts are human-reviewed, provenance-bearing, consent-gated, and inserted atomically with their local outbox record; non-English drafts preserve the clinical-language review flag.
- [ ] **CASE-07**: The local notification outbox distinguishes generated, pending review, approved, queued, failed, and unavailable delivery states, and never represents a queued draft as sent.
- [ ] **CASE-08**: Repeating a case mutation, draft approval, or outbox operation cannot duplicate a transition, approval, audit record, or queued notification, and consent withdrawal follows the documented no-delivery behavior.
- [ ] **CASE-09**: The pathway engine remains the authority for legal transitions, owners, SLAs, evidence requirements, approvals, terminal states, and attention flags, independent of HTTP or identity-provider code.

### Persistence and Recovery

- [ ] **DATA-01**: A fresh or existing SQLite database reports a schema version and is upgraded through ordered, repeatable migrations without deleting existing case identifiers or audit history.
- [ ] **DATA-02**: CareFlow refuses readiness when the database schema is behind, unsupported, partially migrated, unreadable, unwritable, or fails foreign-key or integrity checks.
- [ ] **DATA-03**: Case state, audit history, evidence, approvals, consent decisions, access/security events, and outbox changes required by one mutation commit atomically or are not visible as a partial operation.
- [ ] **DATA-04**: Database transactions use bounded lock/busy behavior and a documented one-process/worker pilot topology without claiming multi-host or high-availability SQLite support.
- [ ] **DATA-05**: An operator can create a consistent online SQLite backup, verify its schema and integrity, and restore it to a new path without copying only an unsafe live main file.
- [ ] **DATA-06**: The restore and upgrade runbook verifies cases, pathway loading, audit history, consent, drafts, outbox rows, and health before a restored database is served.
- [ ] **DATA-07**: Persisted timestamps crossing API, engine, logs, migrations, and backups are UTC-aware or explicitly normalized, and boundary behavior is covered by deterministic tests.

### Operations and Privacy

- [ ] **OPS-01**: The service exposes separate unauthenticated liveness and readiness checks; liveness reflects process health while readiness checks configuration, schema, pathway validity, and writable storage without returning case data.
- [ ] **OPS-02**: Requests and important operations emit structured JSON diagnostics with bounded request/correlation IDs, route/result/latency, and safe error categories to stdout.
- [ ] **OPS-03**: Logs and operational errors redact cookies, tokens, secrets, raw OIDC claims, patient identifiers, message bodies, evidence details, and database contents.
- [ ] **OPS-04**: Security events, access logs, domain audit events, and operational logs have distinct purposes, retention expectations, and access controls documented for the synthetic pilot.
- [ ] **OPS-05**: A support diagnostic or integrity check can identify schema, pathway, foreign-key, audit-continuity, and outbox-reference failures without requiring raw database or patient-content export.
- [ ] **OPS-06**: The deployment runbook documents configuration, startup, migration, health verification, backup, restore, upgrade, rollback, synthetic-data limits, supported topology, residual risks, and honest recovery expectations.

### Web and Deployment Safety

- [ ] **WEB-01**: All cookie-authenticated HTML and JSON mutations use POST or another intentional unsafe method with CSRF protection; state-changing GET links are removed outside explicit demo mode.
- [ ] **WEB-02**: Authenticated responses use cache and browser security policies that prevent cross-user page reuse, framing, unsafe script behavior, and accidental secret/content disclosure.
- [ ] **WEB-03**: Patient and staff server-rendered views accurately communicate status, urgency, ownership, review requirements, consent, conflicts, synthetic-only scope, and “queued/local outbox—not sent” semantics.
- [ ] **WEB-04**: The container runs from a reviewed, reproducible image as a non-root process with an intentional writable data volume, one supported worker, a health check, and no baked mutable runtime database.
- [ ] **WEB-05**: Clean Linux/container smoke checks prove startup, migration, health, volume persistence/replacement, safe secret injection, one case flow, and offline test reproducibility.

### Release Verification

- [ ] **REL-01**: The offline project test command covers existing engine behavior and the new identity, session, authorization, privacy projection, consent, audit, migration, concurrency, recovery, logging, health, and container contracts.
- [ ] **REL-02**: A deterministic fake OIDC provider or equivalent offline harness covers successful login and negative issuer, audience, signature, nonce, state, expiry, key, mapping, inactive-user, and replay cases.
- [ ] **REL-03**: A route/action authorization matrix covers unauthenticated, patient, assigned staff, same-role non-owner, approver, inactive, and out-of-scope principals across JSON, HTML, aliases, collections, and mutations.
- [ ] **REL-04**: Migration interruption, backup/restore, database corruption, unwritable storage, concurrent mutation, consent withdrawal, outbox retry, and redacted-log scenarios have deterministic verification paths.
- [ ] **REL-05**: Release documentation and checks explicitly state that the milestone supports synthetic pilot data only and does not provide PHI compliance, clinical approval, actual message delivery, multi-tenant isolation, or high availability.

## v2 Requirements

Deferred until a deployment owner, data classification, and support model are selected.

### Enterprise Identity and Tenancy

- **V2-AUTH-01**: CareFlow supports multiple identity providers and provider-specific federation policies.
- **V2-AUTH-02**: Organizations can provision and deprovision users through SCIM or an administrative identity lifecycle.
- **V2-AUTH-03**: CareFlow provides multi-tenant SaaS isolation with organization administration, delegated roles, and entitlement review.
- **V2-AUTH-04**: CareFlow supports break-glass access, step-up authentication, local MFA policy, and formal access certification.

### Data and Integrations

- **V2-DATA-01**: CareFlow supports real patient/PHI ingestion with a selected compliance, encryption, retention, legal-hold, and governance program.
- **V2-DATA-02**: CareFlow integrates with EHR, laboratory, scheduling, or master-patient-index systems.
- **V2-DATA-03**: CareFlow provides high-availability, replicas, point-in-time recovery, or multi-region disaster recovery beyond the documented single-host SQLite pilot.

### Communications and Automation

- **V2-MSG-01**: CareFlow delivers approved messages through email, SMS, push, or an in-app patient channel with provider callbacks and delivery receipts.
- **V2-MSG-02**: CareFlow offers translation governance, patient communication preferences, bounce handling, and channel-specific consent operations.
- **V2-AUTO-01**: CareFlow makes autonomous clinical decisions, auto-advances/auto-closes cases, or allows AI to approve transitions or send messages.

## Out of Scope

| Feature | Reason |
|---------|--------|
| CareFlow-owned passwords, password reset, or local MFA | Managed OIDC/SSO is the chosen identity boundary; owning credentials adds avoidable liability. |
| Real patient or protected health information | This milestone is synthetic-only and must not imply a clinical or regulatory production system. |
| Actual email, SMS, push, portal delivery, or delivery guarantees | Approved drafts remain in an auditable local queue; transport is a separate milestone. |
| Autonomous diagnosis, triage, treatment, approval, auto-close, or clinical recommendation | CareFlow coordinates documented human work and does not make clinical decisions. |
| Multi-tenant SaaS or cross-organization tenancy | The deployment model is intentionally undecided and the pilot scope is single-deployment/portable. |
| New mobile app, frontend rewrite, or client-side authorization model | Harden the existing server-rendered UI and API before multiplying security surfaces. |
| Vendor-specific hosting, database, telemetry, or identity lock-in | The milestone must remain deployment-agnostic and reproducible on clean Linux/Docker. |
| Unrestricted raw database, audit, or diagnostic exports | Support output must be redacted and access-controlled to avoid creating a privacy leak. |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| CONF-01 through CONF-05 | Pending | Pending |
| AUTH-01 through AUTH-09 | Pending | Pending |
| AUTHZ-01 through AUTHZ-08 | Pending | Pending |
| CASE-01 through CASE-09 | Pending | Pending |
| DATA-01 through DATA-07 | Pending | Pending |
| OPS-01 through OPS-06 | Pending | Pending |
| WEB-01 through WEB-05 | Pending | Pending |
| REL-01 through REL-05 | Pending | Pending |

**Coverage:**
- v1 requirements: 54 total
- Mapped to phases: 0
- Unmapped: 58 ⚠️

---
*Requirements defined: 2026-10-19*
*Last updated: 2026-10-19 after research-backed project initialization*
