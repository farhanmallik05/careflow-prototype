# Roadmap: CareFlow Production-Readiness Pilot

## Overview

CareFlow's production-readiness pilot hardens the existing Python/FastAPI/SQLite modular monolith without changing its pathway-driven case journey. The work proceeds from contract characterization and fail-closed configuration, through a versioned and recoverable database lifecycle, verified managed OIDC and secure local sessions, relationship-aware authorization and consent-gated case operations, privacy-conscious observability and server-rendered web safety, and deployment hardening. The final phase is a cross-boundary release gate that proves the result on the offline test contract and clean Linux/Docker paths. The current local baseline is 39 passing tests; clean Linux and Docker verification remains pending.

The sequence intentionally follows the dependency graph: configuration and existing-contract characterization precede persistence lifecycle work; persistence precedes identity and sessions; identity precedes authorization; authorization precedes evidence, audit, consent, and messaging expansion; observability and web safety precede container hardening; and release verification is last.

## Milestone Guardrails

- **Synthetic data only:** this milestone must not accept, require, or imply real patient data, PHI handling, clinical production approval, or regulatory compliance.
- **Managed identity only:** do not build CareFlow-owned passwords, password reset, local MFA, SCIM, or multi-provider enterprise identity administration.
- **Local outbox only:** approved drafts remain human-reviewed, consent-gated, auditable local records; no email, SMS, push, portal delivery, or delivery guarantee is added.
- **Human-controlled clinical workflow:** preserve the pathway engine as the authority; do not add diagnosis, treatment recommendations, autonomous transitions, auto-close, or AI approval.
- **Portable pilot topology:** keep the modular monolith, server-rendered UI, deployment-agnostic interfaces, and documented single-host/one-worker SQLite boundary; do not add multi-tenant SaaS, HA, or vendor lock-in.
- **Privacy-conscious support:** diagnostics and evidence bundles must be redacted and access-controlled; raw database, claims, message, token, and unrestricted audit exports remain out of scope.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): planned milestone work
- Decimal phases (2.1, 2.2): urgent insertions, if later required

- [x] **Phase 1: Contract Characterization & Fail-Closed Configuration** - Establish the current contract and make demo, pilot, configuration, and synthetic-data boundaries explicit.
- [x] **Phase 2: SQLite Lifecycle & Recovery Foundation** - Version, validate, transact, back up, and restore the pilot database safely.
- [ ] **Phase 3: Identity & Secure Session Boundary** - Authenticate through managed OIDC and issue revocable, server-managed local sessions.
- [x] **Phase 4: Authorization, Case Integrity & Consent-Gated Messaging** - Enforce relationship scope and bind every consequential case and messaging action to verified authority.
- [ ] **Phase 5: Observability & Server-Rendered HTTP Safety** - Make failures diagnosable without leakage and keep HTML/JSON browser behavior safe and consistent.
- [ ] **Phase 6: Container, Deployment & Rollback Hardening** - Run the pilot reproducibly as a non-root container with durable data and an honest recovery runbook.
- [ ] **Phase 7: Pilot Verification & Release Gate** - Prove the complete synthetic pilot contract across offline tests, browser behavior, recovery, and Docker.

## Phase Details

### Phase 1: Contract Characterization & Fail-Closed Configuration
**Goal**: Operators can choose a clearly bounded runtime mode and the service refuses unsafe pilot configuration while the existing case, audit, and outbox contract remains characterized.
**Depends on**: Nothing (first phase)
**Requirements**: CONF-01, CONF-02, CONF-03, CONF-04, CONF-05
**Success Criteria** (what must be TRUE):
  1. An operator can start CareFlow in explicit `demo` or `pilot` mode and see the selected mode in safe startup/readiness diagnostics.
  2. Pilot startup/readiness fails closed when required OIDC, session, trusted-origin, or data-path settings are absent or unsafe, while one typed configuration surface and placeholder-only `.env.example` values document the accepted settings.
  3. A clean checkout can install the pinned Python/dependency contract using project-local dependencies without absolute machine paths, network-only fixtures, or committed secrets, while the existing 39-test local baseline remains characterized.
  4. A declared pilot data path rejects demo seed/reset operations and the running service presents an unambiguous synthetic-data-only boundary.
**Research flag**: Standard patterns are sufficient for settings validation, explicit feature flags, regression characterization, and synthetic fixture hygiene; repository-specific contract checks remain mandatory.
**Plans**: TBD
**Likely parallelizable plan boundaries**:
- `01-A` Existing engine/API/HTML/audit/outbox contract characterization can run in parallel with `01-B` typed settings and mode validation.
- `01-C` Synthetic-only preflight, seed/reset separation, `.env.example` hygiene, and reproducibility checks follow `01-B`; both can consume `01-A` fixtures.

### Phase 2: SQLite Lifecycle & Recovery Foundation
**Goal**: Operators can initialize, upgrade, verify, back up, and restore the pilot database without losing case identifiers or history and without serving an unsafe schema or storage path.
**Depends on**: Phase 1
**Requirements**: DATA-01, DATA-02, DATA-03, DATA-04, DATA-05, DATA-06, DATA-07
**Success Criteria** (what must be TRUE):
  1. A fresh or legacy SQLite database reports its schema version and can be upgraded through ordered, repeatable migrations without deleting case identifiers or audit history.
  2. Readiness is refused for a behind, unsupported, partially migrated, unreadable, unwritable, foreign-key-invalid, or integrity-invalid database.
  3. A mutation either commits its required case state, audit, evidence, approval, consent, access/security, and outbox changes together or exposes none of the partial operation.
  4. The pilot has a documented one-process/worker SQLite topology with bounded lock behavior, and persisted timestamps crossing API, engine, logs, migrations, and backups are UTC-aware or explicitly normalized.
  5. An operator can create a consistent online backup, verify its schema and integrity, restore it to a new path, and validate cases, pathway loading, audit history, consent, drafts, outbox rows, and health before serving it.
**Research flag**: Standard SQLite migration, locking, online-backup, integrity, and UTC patterns are sufficient; legacy-schema compatibility, interruption, writable-volume, and restore rehearsals are repository-specific checks.
**Plans**: TBD
**Likely parallelizable plan boundaries**:
- `02-A` Baseline/ordered migrations, schema revision, legacy recognition, and migration locking come first.
- After the schema contract is stable, `02-B` integrity/readiness/storage checks and `02-C` transaction/UTC behavior can proceed in parallel.
- `02-D` online backup, restore verification, and operator commands depend on `02-A` and `02-B`.

### Phase 3: Identity & Secure Session Boundary
**Goal**: Pilot users can authenticate through a configured managed OIDC provider and use secure, revocable local sessions without CareFlow trusting caller-supplied identity or roles.
**Depends on**: Phase 2
**Requirements**: AUTH-01, AUTH-02, AUTH-03, AUTH-04, AUTH-05, AUTH-06, AUTH-07, AUTH-08, AUTH-09
**Success Criteria** (what must be TRUE):
  1. A configured pilot user can complete an OIDC authorization-code flow with PKCE and an exact registered redirect URI and receive a CareFlow session.
  2. Callbacks with invalid or missing state, nonce, issuer, audience, signature, expiry, authorization code, key, or required identity claims are rejected without creating a session.
  3. A verified `(issuer, subject)` resolves to an approved local user, while mutable email and caller-supplied user IDs are never used as the identity key.
  4. Unmapped, inactive, ambiguous, or role-unassigned identities cannot obtain a session; synthetic demo login is explicitly opt-in for demo/test mode and unavailable in pilot mode; local role/deactivation changes take effect on the next request.
  5. The browser holds only a high-entropy opaque session handle, while the server enforces rotation, idle/absolute expiry, revocation, logout, secure cookie attributes, CSRF, and trusted-origin checks; local logout works without upstream IdP availability and does not claim upstream SSO ended.
**Research flag**: **Deep phase research required** for the selected IdP's discovery/JWKS rotation, group/role claims, logout semantics, provisioning/link approval, exact redirect origin, trusted proxy, and outage behavior. Offline tests must use a fake provider rather than a real network provider.
**Plans**: TBD
**Likely parallelizable plan boundaries**:
- `03-A` Provider-neutral principal/identity-link contract and selected-IdP research can run in parallel with `03-B` server-side session repository and cookie policy, both after Phase 2 migrations.
- `03-C` Fake-provider OIDC authorization-code/PKCE validation and local mapping depend on `03-A` and `03-B`.
- `03-D` Demo adapter, logout/revocation, CSRF/origin, and role-change re-evaluation follow the principal/session seam; `03-C` can be tested in parallel with its non-OIDC session cases.

### Phase 4: Authorization, Case Integrity & Consent-Gated Messaging
**Goal**: Authorized patients and care-team members can see and mutate only in-scope cases, with evidence, approvals, consent, audit, and local message records bound to verified authority.
**Depends on**: Phase 3
**Requirements**: AUTHZ-01, AUTHZ-02, AUTHZ-03, AUTHZ-04, AUTHZ-05, AUTHZ-06, AUTHZ-07, CASE-01, CASE-02, CASE-03, CASE-04, CASE-05, CASE-06, CASE-07, CASE-08, CASE-09
**Success Criteria** (what must be TRUE):
  1. Every HTML page, JSON route, alias, collection, worklist, metric, draft operation, audit view, and access-log view has an explicit deny-by-default server-side decision made before serialization, and HTML and JSON make the same authorization decision.
  2. Patients can read only linked cases and patient-safe status; staff can read and act only within the documented assignment/team scope; guessed or substituted identifiers cannot reveal another patient's records, and scoped collections contain no unauthorized rows.
  3. The authenticated principal and active local role are authoritative for transitions, assignments, evidence, approvals, drafts, and audit records; evidence and approvals are server-bound to case/action/state/role/provenance, and the pathway engine remains the authority for legal transitions and workflow invariants.
  4. Concurrent or replayed case mutations, draft approvals, and outbox operations cannot silently overwrite newer state or duplicate transitions, approvals, audit records, or queued notifications; the losing/conflicting request receives an actionable result.
  5. Consent has scoped, versioned grant/withdraw/expiry history checked immediately before approval or queueing, and human-reviewed drafts enter an atomic local outbox with provenance and explicit generated/pending-review/approved/queued/failed/unavailable states; non-English drafts retain the clinical-language review flag and nothing is represented as sent.
**Research flag**: **Product-policy research required** for owner versus same-role delegate/team scope, consent purpose/expiry/withdrawal semantics, retention/access rules, and the declared single-organization pilot boundary. Record these decisions before finalizing the route/action matrix.
**Plans**: TBD
**UI hint**: yes
**Likely parallelizable plan boundaries**:
- `04-A` Route/action inventory, deny-by-default policy, and owner/team/patient scope decisions are the prerequisite boundary.
- After `04-A`, `04-B` safe patient/staff projections and `04-C` application-service actor derivation can proceed in parallel.
- `04-D` identity-bound evidence, approvals, audit attribution, and engine integration follows `04-B`/`04-C`; consent history can be modeled alongside it.
- `04-E` atomic reviewed-draft approval, outbox lifecycle, replay/idempotency, and withdrawal behavior follows the policy and persistence seams.

### Phase 5: Observability & Server-Rendered HTTP Safety
**Goal**: Operators can distinguish alive from ready and diagnose failures without privacy leakage, while patients and staff receive safe, accurate, consistent HTML and JSON behavior.
**Depends on**: Phase 4
**Requirements**: AUTHZ-08, OPS-01, OPS-02, OPS-03, OPS-04, OPS-05, WEB-01, WEB-02, WEB-03
**Success Criteria** (what must be TRUE):
  1. Separate unauthenticated liveness and readiness checks expose only safe status; readiness verifies configuration, schema, pathway validity, and writable storage without returning case data.
  2. Requests and important operations produce bounded structured JSON diagnostics with correlation/request IDs, route/result/latency, and safe categories, while cookies, tokens, secrets, claims, patient identifiers, messages, evidence, and database contents are redacted.
  3. Security events, access logs, domain audit events, and operational logs have distinct documented purposes, retention expectations, and access controls, and a support diagnostic identifies integrity failures without requiring raw database or patient-content export.
  4. Authentication, authorization, missing-resource, validation, conflict, and service failures return stable non-sensitive HTML/JSON responses with a correlation identifier and no stack traces, secrets, paths, or patient content.
  5. Cookie-authenticated mutations use POST or another intentional unsafe method with CSRF protection; authenticated responses prevent cache reuse/framing/unsafe script behavior, and server-rendered patient/staff views accurately communicate status, urgency, ownership, review, consent, conflict, synthetic-only scope, and “queued/local outbox—not sent.”
**Research flag**: Targeted browser/proxy validation is required for exact CSP/HSTS/trusted-proxy/cookie behavior and cache isolation; broad library research is not required.
**Plans**: TBD
**UI hint**: yes
**Likely parallelizable plan boundaries**:
- `05-A` Request context, correlation IDs, structured logging, and redaction can proceed in parallel with `05-B` liveness/readiness and bounded support diagnostics.
- `05-C` Stable safe error envelopes/pages and HTML/JSON negotiation depend on `05-A`/`05-B`.
- `05-D` POST+CSRF mutation paths, cache/security headers, safe templates/static contexts, and browser/accessibility checks follow `05-C`.

### Phase 6: Container, Deployment & Rollback Hardening
**Goal**: A deployment-agnostic operator can run, replace, upgrade, and recover the pilot as a safe reproducible container with durable data and an honest support envelope.
**Depends on**: Phase 5
**Requirements**: OPS-06, WEB-04, WEB-05
**Success Criteria** (what must be TRUE):
  1. The reviewed image runs as a non-root process with an intentional writable data volume, one supported worker, a health check, and no baked mutable runtime database or secret.
  2. Clean Linux/container smoke checks prove startup, migration, health, volume persistence/replacement, safe secret injection, one synthetic case flow, and offline test reproducibility.
  3. The deployment runbook gives exact configuration, startup, migration, health, backup, restore, upgrade, rollback, synthetic-data, topology, residual-risk, and recovery-expectation guidance without selecting a provider-specific hosting contract.
**Research flag**: **Deep deployment research required** once the host/volume/proxy is selected: SQLite capacity and journal mode, backup destination/protection, RPO/RTO, forwarded-header trust, TLS termination, non-root filesystem behavior, and retention.
**Plans**: TBD
**Likely parallelizable plan boundaries**:
- `06-A` Digest-pinned image, locked dependencies, non-root user, `/data` volume, one-worker command, and healthcheck can proceed alongside `06-B` deployment/upgrade/rollback runbook drafting.
- `06-C` Clean Linux/container smoke, image-content/secret scan, proxy checks, and volume replacement depend on `06-A` and `06-B`.

### Phase 7: Pilot Verification & Release Gate
**Goal**: Release evidence proves the complete CareFlow synthetic pilot contract across offline tests, identity and authorization matrices, recovery scenarios, browser behavior, and Docker.
**Depends on**: Phase 6
**Requirements**: REL-01, REL-02, REL-03, REL-04, REL-05
**Success Criteria** (what must be TRUE):
  1. The offline project test command covers existing engine behavior and the identity, session, authorization, privacy projection, consent, audit, migration, concurrency, recovery, logging, health, and container contracts.
  2. A deterministic fake OIDC provider or equivalent offline harness proves successful login and negative issuer, audience, signature, nonce, state, expiry, key, mapping, inactive-user, and replay cases.
  3. A route/action authorization matrix proves the expected behavior for unauthenticated, patient, assigned staff, same-role non-owner, approver, inactive, and out-of-scope principals across JSON, HTML, aliases, collections, and mutations.
  4. Deterministic verification paths cover migration interruption, backup/restore, corruption, unwritable storage, concurrent mutation, consent withdrawal, outbox retry, and redacted-log scenarios without relying on external services.
  5. Release documentation and checks explicitly state synthetic-pilot-only support and do not claim PHI compliance, clinical approval, actual message delivery, multi-tenant isolation, or high availability.
**Research flag**: No broad research phase is required; the exact matrix and smoke orchestration must be derived from the implemented route and data policy, with clean Linux/Docker evidence required before release.
**Plans**: TBD
**Likely parallelizable plan boundaries**:
- `07-A` Offline regression/release command and `07-B` fake-IdP/authorization matrix suites can run in parallel after Phase 6.
- `07-C` migration/recovery/concurrency/log-redaction checks and `07-D` browser/container smoke can run in parallel against the stabilized deployment contract.
- `07-E` Final release gate, documentation/guardrail review, and residual-risk sign-off depend on all prior verification boundaries.

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Contract Characterization & Fail-Closed Configuration | 1/1 | Complete | 2026-10-19 |
| 2. SQLite Lifecycle & Recovery Foundation | Complete | Complete | 2026-10-19 |
| 3. Identity & Secure Session Boundary | 0/TBD | Not started | - |
| 4. Authorization, Case Integrity & Consent-Gated Messaging | Complete | Complete | 2026-10-19 |
| 5. Observability & Server-Rendered HTTP Safety | 0/TBD | Not started | - |
| 6. Container, Deployment & Rollback Hardening | 0/TBD | Not started | - |
| 7. Pilot Verification & Release Gate | 0/TBD | Not started | - |

**Coverage:** 54/54 v1 requirements mapped exactly once. See `.planning/REQUIREMENTS.md` for the requirement-level traceability table.
