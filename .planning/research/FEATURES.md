# Feature Landscape

**Project:** CareFlow production-readiness pilot  
**Domain:** Deployment-agnostic case orchestration for synthetic screening-to-care journeys  
**Researched:** 2026-10-19  
**Overall confidence:** MEDIUM-HIGH

## Scope and baseline

This is a safety/supportability feature map for the existing pilot, not a list of
new clinical capabilities. The current prototype already demonstrates the core
case journey: the state machine and policy live in `careflow/engine.py` and
`careflow/pathways/screening_to_care.yaml`; SQLite persistence, consent,
evidence, approvals, message drafts, the local outbox, and access records live
in `careflow/persistence.py`; and the FastAPI/dashboard adapter is in
`careflow/api.py`. Deterministic seed data, Docker, and offline tests are
provided by `careflow/seed.py`, `Dockerfile`, `Makefile`, and `tests/`.

The production-readiness bar is therefore: a patient or care-team member must
be reliably identified, permitted only the data and actions appropriate to the
relationship, shown a safe and comprehensible status, and able to leave a
traceable record of every consequential action. Operators must be able to detect
failure, recover a deployment, and prove what happened without exposing patient
or secret data. This milestone must continue to be synthetic-only and must not
imply PHI compliance or clinical production approval; the existing boundary is
documented in `README.md` and `docs/safety.md`.

The current maps identify the main gaps: the local login is identity selection
rather than authentication, staff access is broader than assignment scope,
schema creation has no migration version, case writes have lost-update risk,
there are no health/readiness or structured application logs, and backup,
retention, and recovery are undocumented. See `.planning/codebase/CONCERNS.md`
and `.planning/codebase/ARCHITECTURE.md` for the implementation evidence.

## Table stakes: must-have pilot safety

These features are release gates for a deployment-agnostic pilot. Missing one
means the pilot is either unsafe, not supportable, or too easy to misrepresent
as a production clinical system.

### Identity and authorization

| Feature | Why expected / pilot risk | Complexity | Acceptance signals | Repository anchor |
|---|---|---:|---|---|
| **Managed OIDC trust boundary** | Do not let CareFlow own passwords or infer identity from a submitted user ID. Use an interoperable OIDC authorization-code flow with state/nonce validation, exact redirect registration, issuer/audience/signature/expiry checks, and a stable `(issuer, subject)` mapping to an internal user. RFC 9700 recommends authorization-code/PKCE protections and rejects password-grant-style patterns. | High | Valid provider claims create or resolve the intended internal user; bad issuer, audience, signature, nonce, state, expiry, or unknown subject is rejected; the current ID-only login cannot be enabled in pilot mode. | `careflow/api.py`, `careflow/persistence.py`, `.env.example`, `tests/test_auth.py`, `README.md` |
| **Explicit demo authentication mode** | Deterministic seeded users are useful for demos but must never be an accidental production login path. Make demo login opt-in and visibly label it; production configuration must fail closed if OIDC settings are absent. | Medium | A clean deployment with no OIDC configuration is either explicitly demo-only or refuses protected service startup/readiness; `/demo-login/{user_id}` is unavailable when demo mode is off; `demo-*` identities cannot be provisioned in pilot mode. | `careflow/api.py`, `careflow/seed.py`, `docs/safety.md`, `README.md` |
| **Secure session lifecycle and logout** | Sessions are equivalent to the authenticated identity. Use high-entropy server-managed or verifiable sessions, rotate on login/privilege changes, set `Secure`, `HttpOnly`, and explicit `SameSite`, enforce idle/absolute expiry, and make logout/revocation meaningful. SameSite is defense in depth, not a replacement for CSRF protection. | High | Cookie-flag, expiry, fixation, tampering, replay, logout, and revocation tests pass; cookie-authenticated mutations reject missing/invalid CSRF or trusted-origin proof; session secrets are never logged or returned. | `careflow/api.py`, `.env.example`, `tests/test_auth.py`, `docs/safety.md` |
| **Centralized deny-by-default authorization** | Every HTML page, JSON route, message-draft operation, worklist, metric, and audit/access-log view needs a server-side decision. Separate authentication, role permission, case relationship/scope, and workflow ownership; do not trust request-body `actor` or client UI hiding. OWASP specifically recommends least privilege, deny by default, checks on every request, and authorization tests. | High | A route/action matrix exists and is tested for patient, owner, same-role non-owner, approver, inactive user, and unauthenticated cases; adding a new route without a policy test is a review failure; `actor` is diagnostic metadata only. | `careflow/api.py`, `careflow/engine.py`, `careflow/pathways/screening_to_care.yaml`, `tests/test_auth.py`, `tests/test_api.py` |
| **Relationship-scoped case access** | Basic RBAC is not enough: the current implementation allows any authenticated operational role broad case visibility and lets a same-role user transition without being the assigned owner. For a pilot, enforce patient-to-own-case access and a documented staff scope (assigned case, team/work queue, or explicit operational scope); defer full tenant isolation but do not leave scope implicit. | High | Patient A cannot discover or infer Patient B's case; staff outside the declared scope receives a safe denial; assigned-owner and approver paths are distinct and documented; worklist/metrics contain only authorized cases. | `careflow/api.py`, `careflow/persistence.py`, `careflow/pathways/screening_to_care.yaml`, `tests/test_auth.py` |
| **Authorization-safe error behavior** | Authentication failures, authorization failures, missing objects, transition conflicts, validation errors, and service/configuration failures need stable status codes and non-sensitive details. Do not expose stack traces, database paths, internal claims, or whether an unauthorized patient case exists unless the policy explicitly permits it. | Medium | Negative tests assert 401/403/404/409/422/503 contracts and unchanged case state; production responses contain a correlation ID but no secret, token, patient message, or SQL detail; logs retain bounded diagnostic context. | `careflow/api.py`, `tests/test_api.py`, `tests/test_auth.py` |
| **Identity-bound evidence and approvals** | Required evidence and approval fields currently can be caller-provided strings/free text. A pilot needs server-created evidence/approval records bound to the verified principal, case, action, role, timestamp, and provenance; the state machine remains the authority for legal transitions. | High | A transition cannot satisfy a required evidence kind with an unpersisted or unrelated string; approver identity is derived from the session and role, not `name` in the request; rejected attempts do not mutate the case; audit and approval rows point to the same principal. | `careflow/api.py`, `careflow/engine.py`, `careflow/persistence.py`, `tests/test_api.py`, `tests/test_persistence.py` |

### Privacy, consent, and human-safe UX

| Feature | Why expected / pilot risk | Complexity | Acceptance signals | Repository anchor |
|---|---|---:|---|---|
| **Synthetic-only runtime guardrail** | Synthetic data is the safety boundary, not a documentation footnote. Keep seeded identifiers, names, emails, cases, and messages visibly synthetic; prevent production configuration from silently using demo data or implying a compliant system of record. | Medium | Every demo record is clearly labelled; seed/reset is unavailable against a declared pilot data path; startup banner and docs state “synthetic only”; no outbound transport or real provider is configured by default; tests scan fixtures for real-looking secrets/contacts. | `careflow/seed.py`, `docs/safety.md`, `README.md`, `.env.example`, `Dockerfile`, `.dockerignore` |
| **Data minimization and patient-safe projections** | A patient-facing response should not expose staff-only audit metadata, internal evidence details, approval notes, assignments, or operational diagnostics merely because the patient can read a case. Team views should show only fields needed for coordination. | High | Patient API/HTML responses contain plain-language status, next safe action, and approved patient-facing content only; staff-only timeline/evidence/access-log fields are denied or redacted; authorization tests cover every projection, not only the route. | `careflow/api.py`, `careflow/templates/`, `tests/test_auth.py`, `docs/safety.md` |
| **Consent as a live, scoped decision** | Consent must be tied to the patient and case (or an explicitly documented scope), have status and expiry, and be checked immediately before approval/queueing. Revocation or expiry must prevent a message from entering the outbox; a previously approved local record is not proof of delivery. | High | Grant, expiry-boundary, withdrawal, wrong-case, and missing-consent tests pass; approval and outbox insertion are atomic; a second approval is idempotently rejected; every denial explains that consent is missing/expired without revealing extra data. | `careflow/persistence.py`, `careflow/api.py`, `tests/test_persistence.py`, `docs/safety.md` |
| **Review-first patient messaging** | Draft generation may assist staff but must not send, transition, close, or escalate a case. Every draft needs provenance, status, review flag, reviewer identity/note, and a visible distinction between generated, approved, queued, and sent (with “sent” unavailable in this milestone). Non-English content requires the existing clinical-language review flag. | High | Drafts always start `pending_approval`; patients cannot generate or approve; approval requires allowed staff role, non-blank note, linked active patient, and current consent; output says “queued/local outbox,” never “sent”; generated text is previewed before approval. | `careflow/ai.py`, `careflow/api.py`, `careflow/persistence.py`, `docs/safety.md`, `tests/test_auth.py`, `tests/test_persistence.py` |
| **Patient and team UX safety rails** | Patients need plain language and no clinical interpretation. Teams need owner, state, SLA, required evidence, attention reason, and next action. Irreversible or high-risk actions need confirmation and visible approval requirements; stale concurrent edits must not silently overwrite another team's work. | Medium | Browser/HTML and JSON flows agree on role visibility; patient status avoids internal labels; team UI makes urgency/overdue/escalated work prominent; closure/escalation confirmation and conflict messages are actionable; accessibility and keyboard checks cover the existing server-rendered dashboard. | `careflow/api.py`, `careflow/templates/`, `careflow/static/`, `careflow/engine.py`, `README.md` |

### Reliability, observability, audit, and recovery

| Feature | Why expected / pilot risk | Complexity | Acceptance signals | Repository anchor |
|---|---|---:|---|---|
| **Fail-closed configuration and secret hygiene** | Missing session/OIDC/security settings, invalid URLs, unsafe cookie mode, or writable-path failures should not produce a deceptively running service. Secrets must come from deployment configuration, never source, images, seed data, or logs. | Medium | Startup/readiness rejects missing or unsafe pilot settings; no predictable fallback secret in non-demo mode; `.env.example` contains placeholders only; image/context excludes databases, cookies, `.env`, and planning artifacts; container runs without root where feasible. | `careflow/api.py`, `.env.example`, `Dockerfile`, `.dockerignore`, `README.md` |
| **Versioned schema initialization and migrations** | `CREATE TABLE IF NOT EXISTS` alone cannot safely upgrade an existing database. Add schema versioning, ordered idempotent migrations, compatibility checks, and a pre-upgrade backup requirement; refuse to serve an unsupported or partially migrated database. | High | Fresh install, upgrade from the current schema, repeated migration, interrupted migration, and unsupported-version tests pass; startup reports schema revision; rollback instructions are tested against a copy, not the only database. | `careflow/persistence.py`, `README.md`, `Makefile`, `tests/test_persistence.py` |
| **Atomic concurrency and lost-update protection** | The current load/mutate/save sequence and shared connection can lose a transition or corrupt audit ordering under concurrent requests. Pilot operation needs a documented one-process/worker limit plus locking/timeout and optimistic version or compare-and-swap protection. | High | Concurrent transitions result in one committed winner and a safe conflict for the loser; no duplicate or missing audit sequence; assignments and approvals cannot overwrite newer state; SQLite busy/locked errors become bounded retry or actionable 503 responses. | `careflow/api.py`, `careflow/persistence.py`, `careflow/engine.py`, `tests/test_api.py`, `tests/test_persistence.py` |
| **Idempotent mutation and outbox lifecycle** | Retries happen when browsers, reverse proxies, or operators cannot see a response. Case creation, transition, approval, and queue insertion need request/dedupe semantics; outbox rows need atomic claim, retry/backoff, failure, and acknowledgement states even though no delivery worker ships in this milestone. | Medium-HIGH | Repeating a client request cannot double-approve, double-queue, or mark an unknown/already-sent notification as sent; two workers cannot claim one row; failed rows are inspectable and retryable; consent is rechecked before any future transport adapter. | `careflow/persistence.py`, `careflow/api.py`, `tests/test_persistence.py`, `docs/safety.md` |
| **Liveness/readiness and safe diagnostics** | Operators need to distinguish “process is alive” from “can serve cases.” Readiness should verify configuration, schema, pathway validity, writable storage, and dependency health without returning case or patient data. | Medium | Unauthenticated health endpoints expose only status/version/check names; liveness survives a database outage while readiness fails; corrupt schema/pathway and unwritable volume produce a clear not-ready response; container health check uses the endpoint. | `careflow/api.py`, `careflow/persistence.py`, `careflow/pathways/screening_to_care.yaml`, `Dockerfile`, `README.md` |
| **Structured, privacy-conscious observability** | Plain `print` and server logs are not enough for support. Emit JSON logs to stdout with timestamp, level, service/version, request/correlation ID, route, result, latency, actor pseudonym/role where safe, and bounded error class. Redact cookies, tokens, message bodies, patient identifiers, secrets, and raw claims. Keep security events distinct from domain audit history. | Medium | Logs parse as structured records; auth success/failure, authorization denial, validation failure, startup/shutdown, database errors, migration, and high-risk mutations are observable; redaction tests prove no token/secret/message/PHI leakage; request ID appears in response and logs. | `careflow/api.py`, `careflow/persistence.py`, `README.md`, `tests/` |
| **Append-only, attributable audit trail** | A supportable care case needs reconstruction: who (verified subject), what action, before/after state, owner, evidence/approval references, consent decision, time in UTC, request ID, and result. Failed security-sensitive actions also need a privacy-safe security event, while case history must not be casually rewritten. | High | Every successful opening/assignment/transition/draft/approval is attributable and ordered; failed transition/authorization/authentication events are discoverable in the appropriate log; audit views are themselves access-controlled; export/retention policy is documented; tampering or sequence gaps fail integrity checks. | `careflow/engine.py`, `careflow/persistence.py`, `careflow/api.py`, `docs/safety.md`, `tests/test_auth.py`, `tests/test_persistence.py` |
| **Backup, restore, and recovery runbook** | A local SQLite file, container replacement, disk-full event, or bad migration must not erase the case journey. Use a consistent SQLite backup method (online backup or `VACUUM INTO`), include WAL sidecars when applicable, protect backups, and verify restore integrity. State RPO/RTO and the supported single-host topology honestly. | High | A clean restore reproduces cases, audit, consent, drafts, and outbox; `PRAGMA integrity_check` and application startup validation run before serving; backup/restore and rollback are documented with exact commands; a simulated failed upgrade has a tested recovery path. | `careflow/persistence.py`, `Dockerfile`, `README.md`, `Makefile`, `.planning/codebase/CONCERNS.md` |
| **Reproducible release gate** | Production readiness is not credible if a clean Linux build differs from the tested environment. Preserve the pinned `.python-version`, lockfile, offline `make test`, deterministic fixtures, Docker build/run, non-secret config, and a smoke test for auth, health, migration, and one safe case flow. | Medium | Clean checkout installs only project dependencies, `make test` passes offline, Docker starts without host-specific files, seed is synthetic and repeatable, and the runbook has setup/start/test/deploy/upgrade/rollback/backup commands. | `.python-version`, `requirements.lock`, `Dockerfile`, `Makefile`, `README.md`, `tests/` |

## Differentiators: valuable after the safety baseline

These make the pilot easier to trust, operate, and evaluate without expanding
into a clinical product. They are deliberately not substitutes for the table
stakes above.

| Feature | Value proposition | Complexity | Acceptance signals | Pilot timing |
|---|---|---:|---|---|
| **Explainable policy and permission decisions** | Show a staff member why an action is allowed or blocked: current state owner role, case relationship, required evidence, approval policy, consent freshness, and next safe action. This turns opaque 403/409 responses into supportable workflow guidance. Build on `explain()`/`worklist()` rather than duplicating pathway rules. | Medium | The same policy decision drives API, dashboard, and tests; a denied action identifies the missing non-sensitive condition; no patient-facing response leaks staff policy details. | High-value pilot enhancement |
| **Review queue with content diff and dual control** | For non-English, model-generated, urgent, or closure-adjacent drafts, display source context, generated text, edits, reviewer note, consent timestamp, and a second-person approval option. It strengthens the existing human-in-the-loop boundary without sending messages. | Medium-HIGH | Approved content is exactly the reviewed version; edits create a new immutable revision; reviewer and approver are attributable; the queue cannot bypass consent or deliver externally. | Pilot differentiator; dual control can be later |
| **Safe degraded/read-only mode** | When OIDC metadata, optional message generation, or writable storage is unavailable, preserve safe status reads/diagnostics where possible while disabling mutations and approvals. A clear “read-only/degraded” state is safer than partial writes or misleading success. | Medium | Dependency outage yields predictable 503/read-only behavior; no case transition or message approval commits while not ready; operators can identify the blocking dependency from redacted diagnostics. | Pilot differentiator |
| **Synthetic replay and failure-injection harness** | Deterministically replay a seeded journey, concurrent transitions, consent withdrawal, migration interruption, disk/read-only failures, and outbox retries. This gives a small team confidence without a hosted test environment. | Medium | A single offline command reproduces the scenario and expected audit/worklist/outbox result; recovery drills are run before a release; fixtures never contain real data. | Pilot differentiator |
| **Integrity verifier and redacted evidence bundle** | Provide a support command that checks schema, pathway, case invariants, audit continuity, foreign keys, and outbox references, then produces a redacted diagnostic bundle keyed by correlation/case pseudonyms. This reduces “send us the database” pressure. | Medium-HIGH | Verification detects tampered/malformed snapshots and exits non-zero; bundle excludes names, emails, message text, cookies, tokens, and secrets; bundle version matches the runbook. | Pilot differentiator |
| **Portable identity/observability conformance adapters** | Keep OIDC claim mapping, session policy, logging schema, health contract, and message-provider boundary behind stable interfaces. Add a local fake IdP and stdout collector contract rather than selecting a cloud or vendor. | Medium | A fake OIDC issuer and a second standards-compliant configuration pass the same contract tests; changing deployment does not change domain rules in `careflow/engine.py`; no provider SDK is required for the offline suite. | Pilot differentiator; provider adapters later |
| **Permission-review and access-anomaly view** | Let an operator review active users, roles, assigned scope, recent denials, unusual case access, and inactive identities without exposing message bodies. This addresses privilege creep and makes least privilege visible. | Medium-HIGH | Review data is itself role-protected and redacted; inactive/revoked identity loses access promptly; anomalies link to security events, not raw patient content. | Later pilot/early enterprise |
| **User-centered reliability signals** | Supplement infrastructure health with user-facing SLIs such as successful case transition rate, time-to-worklist load, conflict rate, readiness duration, and queued-draft age. Keep observed metrics separate from the existing assumption-based simulation. | Medium | Metrics carry sample sizes and time windows, avoid patient identifiers, and distinguish observed case data from `careflow.sim.compare()` assumptions; an operator can tell “up” from “working.” | Pilot differentiator |

## Anti-features: deliberately do not build for this milestone

| Anti-feature | Why avoid it now | Do instead |
|---|---|---|
| **CareFlow-owned passwords, password reset, credential recovery, or local MFA** | It creates credential liability, duplicates the provider's security controls, and contradicts the managed-identity boundary. The current ID-only login is suitable only as an explicit demo mode. | Integrate managed OIDC; map verified claims to a local user/role/scope record; document provider responsibilities. |
| **Real patient/PHI ingestion or a production-compliance claim** | The milestone is intentionally synthetic-only. Calling the pilot HIPAA/GDPR/compliant or accepting real screening results would create a dangerous expectation before deployment, threat modeling, retention, encryption, and governance are selected. | Keep `docs/safety.md` and `README.md` prominent; use deterministic `example.test` identities and a startup/banner boundary; make a future PHI milestone a separate decision. |
| **Actual email, SMS, push, portal delivery, webhooks, or delivery guarantees** | Delivery introduces provider credentials, retries, bounce/complaint handling, channel consent, content policy, and incident response. It is explicitly out of scope and would make “approved” easy to confuse with “sent.” | Keep approved content in a consent-gated, auditable local outbox; implement a transport adapter only in a separately researched milestone. |
| **Autonomous clinical decisions or AI-driven transitions/approvals** | CareFlow coordinates documented work; it must not diagnose, triage, prescribe, choose treatment, silently resolve escalation, or let an AI approve/send content. | Keep `careflow/engine.py` authoritative, require human evidence/approval, and use `careflow/ai.py` only as a read-only draft provider with visible review flags. |
| **Broad auto-advance, auto-close, or “best next clinical action” automation** | Automation can hide missed contact, no-show, consent withdrawal, or an unsafe state. Existing escalation and closure controls are valuable precisely because they surface human decisions. | Preserve explicit transitions, owner/SLA/worklist attention, and human-controlled closure/escalation. |
| **Multi-tenant SaaS, cross-organization tenancy, or an admin/SCIM control plane** | No hosting or tenancy model has been selected. Building it now would force tenant isolation, billing, org lifecycle, and shared-infrastructure decisions that are outside a single synthetic pilot. | Provide a clear single-deployment scope interface and avoid claiming tenant isolation; research ABAC/ReBAC, SCIM, and organization provisioning later. |
| **Full EHR/lab/scheduling/master-patient-index integration** | Integrations and identity matching are broad reliability and privacy projects, not prerequisites for proving this case-orchestration control plane. | Use stable inbound/outbound adapter contracts and synthetic fixtures; do not add clinical data breadth. |
| **New mobile app or frontend rewrite** | A new client multiplies authentication, offline/cache, accessibility, and authorization surfaces while the existing server-rendered dashboard is adequate for the pilot. | Harden `careflow/templates/`, `careflow/static/`, and JSON routes; add a new client only when a validated user need exists. |
| **Unrestricted bulk export, raw database downloads, or debug access to audit logs** | Audit and diagnostics can contain sensitive identifiers, notes, and session context. A support shortcut can become the largest privacy leak. | Offer a role-protected, redacted evidence bundle with retention and access logging; keep raw database/backup access operationally restricted. |
| **Vendor-specific deployment, database, identity, or telemetry lock-in** | The target is intentionally deployment-agnostic. Selecting a cloud service now would turn portable requirements into provider assumptions and make recovery harder to rehearse locally. | Define contracts for OIDC, storage, logs, health, and message providers; keep SQLite/Docker support explicit for the pilot and document limits. |

## Feature dependencies

```text
OIDC trust validation
  -> internal (issuer, subject) identity mapping
  -> secure session lifecycle / logout
  -> centralized authorization policy
  -> patient-to-case and staff relationship scopes
  -> role-safe patient/team projections and audit attribution

Case relationship scopes + pathway owner policy
  -> authorized evidence/approval resources
  -> consent-aware message review
  -> atomic approval + local outbox
  -> safe future transport adapter (later, not this milestone)

Schema versioning + startup validation
  -> migrations
  -> backup before upgrade
  -> restore/integrity verification
  -> readiness and recovery runbook

Transaction boundaries + version/lock policy
  -> concurrent transition safety
  -> idempotent mutation keys
  -> outbox claim/retry/dedupe
  -> reliable observed metrics

Structured privacy-safe logs + request/correlation IDs
  -> security-event detection
  -> support diagnostics
  -> redacted evidence bundle
  -> user-centered reliability/SLO signals

Synthetic-only mode + deterministic fixtures
  -> offline authorization/message/recovery tests
  -> Docker smoke test
  -> repeatable pilot demonstrations
```

Important ordering constraint: do not add an OIDC callback or new message
transport before the authorization, session, consent, logging, and audit
boundaries exist. Do not call a database backup/recovery feature complete until
schema versioning and SQLite concurrency/storage assumptions are explicit.

## Pilot baseline versus later enterprise capabilities

| Pilot baseline (required now) | Later enterprise capability (defer) |
|---|---|
| One deployment boundary, one documented process/worker topology, verified OIDC claims, secure cookie/session policy, and explicit demo mode | Multiple organizations/tenants, federation across many IdPs, SCIM/JIT provisioning, delegated administration, and policy authoring UI |
| Patient-owned-case isolation plus documented staff assignment/team scope; centralized server-side checks on every surface | Full ABAC/ReBAC policy engine, cross-organization relationships, break-glass workflow, periodic entitlement certification, and fine-grained data labels |
| Synthetic data banner/guardrail, minimum necessary projections, consent freshness, local reviewed outbox, and redacted logs | PHI governance, encryption/key-management architecture, legal holds, data-subject workflows, retention automation, and regulatory assurance |
| Versioned SQLite schema, tested single-host backup/restore, integrity check, bounded concurrency, and recovery runbook | HA service database, replicas, point-in-time recovery, multi-region disaster recovery, and 24/7 SRE operations |
| Structured stdout logs, health/readiness, request correlation, basic observed reliability counters, and access/security events | Central SIEM, OpenTelemetry collector/export pipeline, long-term audit warehouse, anomaly detection, and organization-wide SLO program |
| Human-reviewed drafts, explicit approval/consent, no actual delivery, and deterministic rule-based provider | Provider-specific delivery channels, translation governance, message preference center, delivery receipts, bounce handling, and patient communications operations |

## MVP recommendation

Prioritize in this order:

1. **Identity and configuration boundary:** managed OIDC contract, explicit demo
   mode, fail-closed secrets/configuration, secure session/CSRF/logout behavior.
2. **Authorization and data projections:** a tested route/action matrix,
   relationship-scoped cases, canonical server-derived actor, and separate
   patient-safe/team-safe representations.
3. **Consent and message safety:** identity-bound evidence/approvals, live
   consent checks, immutable draft provenance, human review, atomic local
   outbox, and an unambiguous “not sent” state.
4. **Persistence correctness:** schema version/migrations, UTC timestamps,
   transaction/locking or optimistic-version policy, conflict handling, and
   idempotent outbox lifecycle.
5. **Support and recovery:** liveness/readiness, structured redacted logs,
   security events, append-only audit integrity, backup/restore verification,
   and an exact deployment/upgrade/rollback runbook.
6. **Release verification:** extend `tests/test_auth.py`,
   `tests/test_api.py`, and `tests/test_persistence.py` with negative,
   concurrent, migration, redaction, recovery, and message-consent cases; keep
   `make test` offline and Docker-reproducible.

Defer full tenant administration, enterprise identity lifecycle, real data,
clinical integrations, actual delivery, and broad observability backends until
the deployment owner, data classification, and operational support model are
chosen. The differentiators should be added only if they reduce pilot risk or
operator burden without weakening the table-stakes gates.

## Sources and confidence

### Repository evidence (HIGH confidence for current state)

- `PROJECT.md` — active production-readiness requirements and explicit
  out-of-scope boundaries.
- `.planning/codebase/ARCHITECTURE.md` — current layers, data flows, and
  application boundaries.
- `.planning/codebase/CONCERNS.md` — observed authentication, authorization,
  concurrency, migration, privacy, logging, and recovery gaps.
- `.planning/codebase/TESTING.md` — existing test contract and missing
  verification areas.
- `careflow/api.py`, `careflow/persistence.py`, `careflow/engine.py`,
  `careflow/ai.py`, and `careflow/pathways/screening_to_care.yaml` — current
  runtime behavior and domain invariants.
- `README.md`, `docs/safety.md`, `Dockerfile`, `.dockerignore`, `.env.example`,
  and `Makefile` — current safety, deployment, and reproducibility contract.

### External authoritative guidance

- [OpenID Connect Core 1.0 incorporating errata set 2 (2023-12-15)](https://openid.net/specs/openid-connect-core-1_0.html)
  — **HIGH**: relying-party identity claims, ID-token validation, nonce, and
  authorization-code flow.
- [RFC 9700: Best Current Practice for OAuth 2.0 (2025-01)](https://www.rfc-editor.org/rfc/rfc9700.html)
  — **HIGH**: authorization-code/PKCE, CSRF, redirect validation, token
  handling, privilege restriction, and deprecation of password grants.
- [OWASP Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)
  — **HIGH**: least privilege, deny by default, per-request checks,
  relationship-aware authorization, safe failures, and authorization tests.
- [OWASP Session Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
  — **HIGH**: secure cookie attributes, entropy, expiration, renewal,
  revocation, and server-side session protections.
- [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
  — **HIGH**: security/operational event coverage, “when/where/who/what,”
  redaction, integrity, access control, retention, and verification.
- [OWASP Application Security Verification Standard](https://owasp.org/www-project-application-security-verification-standard/)
  — **MEDIUM-HIGH**: current security verification baseline (latest stable
  version listed by OWASP is 5.0.0); used as a checklist source, not a claim
  that this pilot is certified.
- [SQLite Online Backup API](https://www.sqlite.org/backup.html)
  — **HIGH**: consistent snapshots of a running SQLite database and handling
  of locking/backup errors.
- [SQLite Write-Ahead Logging](https://www.sqlite.org/wal.html)
  — **HIGH**: same-host concurrency tradeoffs, checkpointing, sidecar files,
  and the need to keep database/WAL state together during backup.
- [OpenTelemetry Observability Primer](https://opentelemetry.io/docs/concepts/observability-primer/)
  — **HIGH**: logs, metrics, traces, SLIs/SLOs, and correlation as the
  provider-neutral observability model.
- [Dockerfile reference: `HEALTHCHECK`](https://docs.docker.com/reference/dockerfile/#healthcheck)
  — **HIGH**: container health signaling; the deployment still needs a
  provider-neutral readiness contract in the application.

Market/table-stakes language above is an engineering judgment derived from
these standards and the repository's stated pilot requirements, not a claim of
regulatory certification or a survey of every care-management product.
