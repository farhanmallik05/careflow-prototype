# Project Research Summary

**Project:** CareFlow
**Domain:** Deployment-agnostic production hardening for a pathway-driven care-case orchestration pilot
**Researched:** 2026-10-19
**Confidence:** MEDIUM-HIGH (high confidence in the security, SQLite, and reproducibility patterns; medium confidence in provider- and deployment-specific choices)

## Executive Summary

CareFlow is a Python/FastAPI modular monolith that moves a synthetic screening result through assignment, evidence, escalation/rejection, patient-message review, and documented closure. Experts would preserve this domain core and harden the boundaries around it: verified managed OIDC identity becomes a local principal; a centralized authorization policy decides relationship and resource access; the existing pathway engine remains the authority for legal transitions; and a transactional SQLite repository records business, security, and operational history separately. The recommended strategy is a narrow, deployment-agnostic pilot rather than a rewrite, microservices split, new frontend, or cloud-specific integration.

Keep Python 3.12, the tested FastAPI/Pydantic/Uvicorn/Jinja2/PyYAML/Docker/stdlib-`sqlite3` stack, and add only Authlib 1.9.1 and pydantic-settings 2.16.0 initially. Use OIDC authorization code + PKCE with strict issuer/audience/nonce/state validation, an opaque server-managed session, CSRF and trusted-origin checks, versioned SQLite migrations, online backups, one worker on one local writable volume, health/readiness endpoints, and redacted JSON stdout logs. The greatest risks are the current ID-only demo login, role-only/broad case access, caller-controlled evidence and approval, lost updates and unsafe schema/file operations, and confusing a queued draft with a delivered message. Fail closed in pilot mode, keep data synthetic, make all consequential actions attributable and transactional, and prove the boundaries with offline, concurrency, browser, migration/restore, and Docker tests.

## Key Findings

### Recommended Stack

The detailed recommendation is in [`STACK.md`](./STACK.md). Preserve the existing runtime and direct dependency contract; do not combine this hardening work with a broad framework refresh. Authlib should provide provider-neutral OIDC plumbing, and pydantic-settings should provide one typed, validated configuration boundary. Standard-library `secrets`, `logging`, `json`, `contextvars`, and `sqlite3` are sufficient for session-token generation, CSRF comparison, structured logs, request correlation, migrations, integrity checks, and backups. Use a hash-checked pip-tools lock and pin the reviewed Python base image by digest.

SQLite is acceptable only as an explicit single-host synthetic-pilot boundary: one Uvicorn worker/process, one local writable volume containing the database and any WAL sidecars, bounded busy timeouts, short transactions, and optimistic case-version or compare-and-swap protection. Replace schema side effects with ordered migrations using `PRAGMA user_version`; use `Connection.backup()` plus integrity/foreign-key checks rather than copying a live file. Keep seed/demo mode explicit and never bake a mutable database into the image.

**Core technologies and boundary decisions:**

- **Python 3.12**: existing pinned runtime; avoid a runtime migration while changing security boundaries.
- **FastAPI 0.136.1 / Pydantic 2.13.4 / Starlette 0.46.2 / Uvicorn 0.47.0**: retain the tested set initially; evaluate newer framework pins separately and together.
- **Jinja2 3.1.6 + PyYAML 6.0.2**: preserve the server-rendered UI and safe-loaded pathway policy; validate the complete pathway before readiness.
- **Authlib 1.9.1**: standards-based OIDC discovery, authorization-code/PKCE, and token validation without an identity-vendor SDK.
- **pydantic-settings 2.16.0**: typed `Settings` object passed into `create_app`; fail closed on unsafe pilot configuration and do not auto-load a repository `.env` in the container.
- **Opaque SQLite-backed application sessions**: retain `itsdangerous==2.2.0` only for compatibility/transient OAuth state, not as the revocable identity session.
- **stdlib `sqlite3` with a migration runner and `Connection.backup()`**: keeps the repository simple and portable while making upgrades, recovery, and integrity testable.
- **Docker + project-local pip-tools 7.6.2**: preserve the clean-Linux/container contract, hash-check the lock, run non-root with `/data`, migrate before serving, and keep `make test` offline.
- **stdlib JSON logging and explicit health contracts**: avoid a telemetry backend until a deployment owns collection, sampling, retention, and privacy policy.

**Unresolved choices to keep out of domain code:** the actual OIDC provider and group/role claim, exact public HTTPS origin and trusted proxy behavior, local-vs-rollback SQLite journal mode, backup destination/retention/RPO/RTO, consent purpose and jurisdiction, and the future point at which SQLite must be replaced. Recheck package versions and the selected base-image digest during implementation.

### Expected Features

The feature map is in [`FEATURES.md`](./FEATURES.md). These are safety and supportability gates for the current pilot, not a new clinical feature program.

**Must have (table stakes):**

- **Managed OIDC trust boundary and explicit demo mode** — verified `(issuer, subject)` maps to a local user; ID-only/demo login is unavailable in pilot mode.
- **Secure, revocable session lifecycle** — server-side opaque sessions, rotation, idle/absolute expiry, logout, secure cookies, CSRF, and trusted-origin checks.
- **Deny-by-default relationship authorization** — one server-side policy covers JSON, HTML, worklists, metrics, drafts, audit/access logs, patient scope, staff assignment/team scope, and every alias.
- **Patient-safe and team-safe projections** — authorize before serialization; patients receive plain-language status and safe next action, not staff audit/evidence/approval details.
- **Identity-bound evidence, approvals, consent, and append-only attribution** — server derives actor and role; consent is live, scoped, expiry-aware, and checked immediately before approval/queueing.
- **Review-first local messaging** — drafts are provenance-bearing and human-approved; the local outbox says `queued`, never `sent`, and delivery is absent.
- **Persistence correctness** — versioned migrations, integrity checks, UTC-aware timestamps, explicit transaction boundaries, conflict handling, idempotent mutations, and safe outbox state transitions.
- **Supportability and recovery** — liveness/readiness, redacted structured logs, request IDs, security events, retention expectations, online backup/restore verification, and a deployment runbook.
- **Synthetic-only and reproducible release guardrails** — no PHI claim, no baked runtime DB, pinned runtime/lock, offline `make test`, deterministic fixtures, and clean-container smoke coverage.

**Should have (differentiators after the gates):**

- Explainable policy decisions that tell staff the non-sensitive reason an action is allowed or blocked.
- Review queue with immutable content revisions, diffs, reviewer notes, and optional dual control for sensitive drafts.
- Safe degraded/read-only mode when optional dependencies or writable storage are unavailable.
- Synthetic replay and failure-injection harness for concurrent transitions, consent withdrawal, migration interruption, and recovery.
- Integrity verifier and redacted evidence bundle for support without sharing a raw database.
- Portable fake-IdP/log-collector conformance adapters and user-centered reliability signals.

**Defer (v2+):**

- CareFlow-owned passwords, password reset, local MFA, SCIM, multi-IdP federation, or enterprise identity administration.
- Real patient/PHI ingestion, regulatory/compliance claims, legal holds, encryption/key-management program, or clinical approval.
- Email/SMS/push/portal delivery, delivery guarantees, provider callbacks, bounce handling, or a message worker.
- Autonomous clinical decisions, AI transitions/approvals, auto-advance/auto-close, or treatment recommendations.
- Multi-tenant SaaS, shared-host HA, multi-region recovery, EHR/lab/scheduling/MPI integrations, raw exports, or a frontend/mobile rewrite.
- Vendor-specific database, hosting, telemetry, or observability lock-in.

### Architecture Approach

The architecture recommendation is in [`ARCHITECTURE.md`](./ARCHITECTURE.md). Keep a modular monolith and make the HTTP layer an adapter over explicit identity, session, authorization, application-service, repository, and operations boundaries. The critical flow is **verified OIDC claims → local principal → authorization/resource decision → application service → engine transition → one SQLite unit of work**. Representation negotiation happens only after authorization, so HTML and JSON share policy and payload semantics. The engine receives only a canonical internal actor ID and pathway role; it must not import FastAPI, OIDC/JWT code, SQL, cookies, or logging policy.

**Major components:**

1. **`careflow/engine.py` + pathway model** — pure pathway validation, state transitions, ownership, evidence/approval rules, `explain()`, and worklist ordering.
2. **`careflow/pathways/screening_to_care.yaml`** — versioned workflow states, owners, SLAs, approvers, required evidence, and terminal states; no identity-provider or deployment policy.
3. **`careflow/api.py`** — FastAPI assembly, lifecycle, route schemas, request context, response negotiation, exception mapping, health endpoints, and dependency wiring; no duplicated authorization or direct state mutation.
4. **`careflow/identity.py` (new)** — OIDC discovery, authorization-code/PKCE callback, state/nonce/JWKS/issuer/audience validation, normalized claims, and `(issuer, subject)` linking.
5. **`careflow/sessions.py` (new)** — high-entropy opaque cookie handles, hashed session records, rotation, expiry, revocation, and cookie policy; never stores claims/tokens in the browser.
6. **`careflow/authorization.py` (new)** — authentication, role/permission, patient/owner/team relationship, and resource-scope decisions shared by every route and representation.
7. **`careflow/services.py` or `careflow/case_service.py` (new)** — request-scoped use cases that authorize, load, invoke the engine, persist, and record required business/access/security events atomically.
8. **`careflow/persistence.py` + `careflow/db.py`/`careflow/migrations/` (new)** — repository, unit of work, identity/session/security records, schema revisions, locking/version checks, scoped queries, backups, and integrity diagnostics.
9. **`careflow/observability.py` and `careflow/ops.py`/`careflow/cli.py` (new)** — safe JSON diagnostics, correlation IDs, health checks, migration, backup, restore, and verification commands.
10. **`careflow/templates/` and `careflow/static/`** — presentation only; no authorization or business decisions, no secrets/tokens, and no raw staff payload in patient pages.

### Existing-file implications

| Existing path | Production-readiness implication |
|---|---|
| `careflow/api.py` | Convert app creation to typed settings/lifespan validation; route all HTML/JSON and aliases through shared principal/authorization/application-service paths; derive actor/role from the session; add CSRF, safe errors, request IDs, `no-store`, security headers, liveness/readiness, and controlled 409/503 responses. Keep compatibility routes only when their policy is explicit. |
| `careflow/engine.py` | Preserve state-machine behavior and public transition/worklist tests. Accept canonical actor/role and version/conflict context from above; keep pathway invariants, evidence, approval, ownership, SLA, and terminal-state rules here. Do not add OIDC, SQL, cookies, or HTTP concerns. |
| `careflow/persistence.py` | Replace `SCHEMA`-only startup with migrations/version checks; add users/identity links/role assignments/sessions/login transactions/security events as needed; add unit-of-work transactions, UTC normalization, optimistic case versioning, bounded lock handling, immutable consent/audit history, scoped queries, idempotent outbox transitions, backup and integrity checks. Do not make it an OIDC or HTML policy layer. |
| `careflow/pathways/screening_to_care.yaml` | Keep policy declarative and authoritative. Add explicit version/validation/readiness checks and settle owner versus same-role delegate semantics before authorization tests; do not encode provider groups, secrets, or deployment configuration. |
| `careflow/templates/` | Split patient-safe and staff-safe contexts; keep autoescape; remove/disable demo-login links outside explicit demo mode; use POST + CSRF for mutations; show urgency, owner, SLA, evidence, consent, review, and “queued/local outbox—not sent” accurately without exposing internal notes or claims. |
| `careflow/static/` | Preserve the server-rendered UI rather than rewrite it. Keep assets compatible with a restrictive CSP, avoid client-side authorization or token storage, provide visible synthetic-only/status/read-only/error cues, and add accessibility/keyboard checks for urgent worklist and confirmation flows. |
| `tests/` | Retain engine regression tests and add fake-IdP OIDC validation, session/CSRF/rotation, table-driven JSON/HTML authorization, patient-safe projection, consent/evidence/approval provenance, migrations/legacy upgrade, concurrency/CAS, outbox idempotency, UTC boundaries, redaction/health, backup/restore, browser/proxy, non-root Docker, volume replacement, and offline release tests. |
| `Dockerfile` | Pin `python:3.12-slim` by reviewed digest; install only the locked requirements; copy runtime files intentionally; run as fixed non-root user; provide `/data` writable volume and Python healthcheck; migrate in a controlled pre-serve step; use one Uvicorn worker; never seed or bake `careflow.db` into the image. |
| `Makefile` | Keep `make test` deterministic/offline and document `python -m pytest -q` equivalent. Add explicit `migrate`, `backup`, `restore/verify`, seed/demo, Docker smoke, and release-check targets without hiding destructive reset behavior. |
| `README.md` | Become the deployment-agnostic runbook: exact setup/run/test/deploy commands, environment variables, OIDC callback registration, demo versus pilot mode, synthetic-only warning, one-worker/SQLite limits, health probes, volume/secret injection, migration/backup/restore/rollback, retention, and honest RPO/RTO/residual-risk statements. |
| `.env.example` | List canonical placeholder-only settings (`CAREFLOW_ENV`, DB path, session policy, OIDC issuer/client/redirect/role claim, trusted origins, SQLite policy, rule-based message provider); never include real secrets or auto-load it in the container. Make demo/pilot mode explicit and reject unsafe combinations. |

### Critical Pitfalls

The detailed risk map and phase assignments are in [`PITFALLS.md`](./PITFALLS.md).

1. **Identity lookup or decoded JWT mistaken for authentication (Phase 2/3)** — use maintained OIDC authorization-code + PKCE, strict issuer/audience/signature/expiry/nonce/state checks, `(iss, sub)` links, explicit role mapping, fake-IdP negative tests, and no client-supplied actor.
2. **Security defaults and `SameSite` treated as the session/CSRF policy (Phase 1/3/5)** — require secrets and OIDC settings in pilot mode, use opaque revocable sessions and rotation, secure `__Host-`-style cookies, synchronizer CSRF tokens, exact trusted origins, and no GET mutations.
3. **Role authorization treated as case authorization (Phase 4)** — centralize deny-by-default resource policy; enforce patient ownership and documented staff assignment/team scope before serialization; test every JSON/HTML route, alias, collection, metric, draft, and audit view.
4. **Full staff payload or opaque IDs exposed to patients (Phase 4/5)** — separate projections, scoped repository queries, indistinguishable forbidden/not-found behavior where needed, `Cache-Control: no-store`, and canary tests for internal notes/message text.
5. **Caller-controlled evidence, approvals, consent, or rewritable audit history (Phase 4/5)** — create server-bound records, preserve append-only event history, model consent grants/withdrawals/expiry, and make approval + outbox insertion atomic and consent-gated.
6. **Shared SQLite load–mutate–save loses updates; schema/file operations are unsafe (Phase 1/5/6)** — one-worker topology, explicit unit of work, busy timeout, CAS/version conflicts, ordered migrations, pre-upgrade online backup, integrity checks, and restore rehearsal; never copy only a live main file.
7. **Queued notification described as sent or consent revocation ignored (Phase 4/5)** — keep `queued` visibly local and unsent; make duplicate approval/claim/state transitions idempotent; defer transport and recheck consent at any future dispatch boundary.
8. **Naive timestamps, sensitive logs, weak health checks, or unsafe container (Phase 5/6/7)** — use UTC-aware injected clocks, separate domain/access/security/operational records, redact logs, separate live/readiness, pin image/lock, run non-root with a mounted volume, and test through a browser/proxy and clean Linux container.

## Implications for Roadmap

Based on the dependency graph in `FEATURES.md`, the build order in `ARCHITECTURE.md`, and the phase warnings in `PITFALLS.md`, use the following seven phases. The first phase is intentionally foundational: migrations and settings must exist before new identity/session tables or pilot cutover, even though identity is the most visible feature.

### Phase 1: Existing-contract characterization and fail-closed configuration

**Rationale:** Establish the current API/HTML/engine/data contract before changing security boundaries; make demo versus pilot behavior explicit and remove ambiguous environment defaults. This prevents a green test suite from validating the wrong authentication contract.

**Delivers:** Typed settings boundary, deployment mode, canonical environment names, startup/readiness validation skeleton, UTC boundary decision, regression fixtures for current responses/case IDs/audit/outbox, and explicit synthetic-only preflight.

**Addresses:** Fail-closed configuration, explicit demo mode, synthetic-only guardrails, reproducible release baseline.

**Avoids:** Random/fallback secrets, accidental demo login, documentation drift, baked demo data, and mixing framework upgrades with security changes.

### Phase 2: SQLite schema lifecycle and recovery foundation

**Rationale:** New identity, role, session, security, and version fields require safe upgrades. A migration and backup contract must precede persistence changes, not be retrofitted after data is in use.

**Delivers:** Baseline/ordered migrations, `schema_migrations`/revision checks, legacy database recognition, `BEGIN IMMEDIATE` migration lock, integrity/foreign-key checks, online backup/restore verification, writable-volume diagnostics, and an explicit migration/backup CLI or Make targets.

**Uses:** Python stdlib `sqlite3`, `PRAGMA user_version`, `Connection.backup()`, one local volume, and the existing repository boundary.

**Avoids:** `CREATE TABLE IF NOT EXISTS` as migration, future-schema serving, unsafe live-file copy, data loss on container replacement, and untested rollback assumptions.

### Phase 3: Identity and secure session boundary

**Rationale:** Authorization and audit attribution need a trusted principal; provider claims must not leak directly into routes or the engine. Build the session seam before enabling pilot OIDC, with the migration tables from Phase 2.

**Delivers:** `Principal`, identity links, local role mapping, opaque hashed sessions, login transactions, session rotation/expiry/revocation/logout, secure cookie policy, CSRF/origin contract, explicit demo adapter, and Authlib OIDC authorization-code + PKCE callback with fake-provider tests.

**Implements:** `careflow/identity.py`, `careflow/sessions.py`, configuration-backed provider boundary, and request context in `api.py`.

**Research flag:** **Deep phase research required** for the selected IdP's discovery/JWKS rotation, group/role claims, logout semantics, provisioning/link approval, exact redirect origin, trusted proxy, and outage behavior. Do not select a provider SDK or make real IdP/network access part of offline tests.

### Phase 4: Authorization, consent, evidence, and audit invariants

**Rationale:** A verified identity is not permission to see every case. Centralize resource policy and safe projections before exposing the new identity path to all existing routes and before making messaging trustworthy.

**Delivers:** `AuthorizationService`, application/case service, route/action matrix, patient-to-own-case and documented staff owner/team scope, patient/team DTOs, canonical actor derivation, identity-bound evidence/approvals, versioned consent history, append-only business/security attribution, and human-reviewed local outbox semantics.

**Addresses:** Table-stakes authorization, data minimization, consent freshness, review-first messaging, explainable denials, and audit integrity.

**Avoids:** Horizontal privilege escalation, patient disclosure, caller-controlled approvals, consent bypass, full-record collection leaks, and “queued = sent.”

**Research flag:** **Product-policy research required** for owner versus same-role delegate/team scope, consent purpose/expiry/withdrawal semantics, retention/access rules, and whether a single organization is the declared pilot boundary. These decisions must be recorded before finalizing the authorization matrix.

### Phase 5: Operational observability and server-rendered HTTP safety

**Rationale:** Once request context and authorization exist, failures can be correlated safely. Browser protections and operational diagnostics must be completed before deployment rather than inferred from `TestClient` success.

**Delivers:** JSON stdout logs with allowlisted fields and redaction, security-event separation, request IDs, liveness/readiness, safe error envelopes/pages, `no-store` and security headers, HTML/JSON parity, POST+CSRF mutation flows, accessibility/keyboard checks, and optional explainable policy/read-only degraded state.

**Implements:** `careflow/observability.py`, health routes/lifespan in `api.py`, safe `templates/`/`static/` contexts, and bounded operational diagnostics.

**Avoids:** Token/PHI leakage, health endpoints that expose case data, XSS/cache/clickjacking/CSRF issues, logging failures that mutate twice, and conflating domain audit with operational logs.

**Research flag:** **Targeted browser/proxy validation**, not broad library research; verify exact CSP/HSTS/trusted-proxy/cookie behavior through the intended HTTPS edge and validate cache isolation in a real browser.

### Phase 6: Container, deployment, backup, and rollback hardening

**Rationale:** The service is not production-ready until a clean Linux/container deployment can preserve data, run as non-root, start only after migrations, and recover from replacement or failed upgrade.

**Delivers:** Digest-pinned non-root Docker image, intentional runtime copy context, `/data` volume, one-worker command, Python healthcheck, migration-before-serve, secret injection, clean build/run smoke test, documented upgrades/rollback, backup cadence/retention, restore drill, proxy/TLS/redirect configuration, and synthetic-only image/data scan.

**Avoids:** Root images, mutable base drift, secrets in layers, runtime DB in image, network-filesystem SQLite/WAL misuse, host-header/open-redirect errors, and unsupported multi-worker scaling.

**Research flag:** **Deep deployment research required** once the host/volume/proxy is selected: SQLite capacity and journal mode, backup destination/encryption, RPO/RTO, forwarded-header trust, TLS termination, non-root filesystem behavior, and operational retention.

### Phase 7: Pilot verification and release gate

**Rationale:** Cross-boundary tests are the only credible proof that the pilot contract works outside sequential in-process happy paths.

**Delivers:** Offline `make test`, fake-IdP negative suite, authorization matrix, browser smoke, concurrency/CAS checks, migration interruption/restore drills, log-redaction canaries, health failure matrix, Docker non-root/volume-replacement smoke, synthetic-only preflight, and a residual-risk/runbook review.

**Addresses:** Reproducible release gate and all minimum exit checks in `PITFALLS.md`.

**Avoids:** Treating a passing local pytest run as a security assessment, claiming PHI/compliance/HA/delivery capability, and shipping unresolved source/docs contract drift.

### Phase Ordering Rationale

- Settings and contract characterization come first so every later phase has an explicit demo/pilot boundary and stable regression expectations.
- Migrations and verified backup precede identity/session schema work; otherwise a security improvement can strand existing cases or create mixed schema versions.
- A local principal/session seam precedes OIDC and centralized authorization; OIDC proves identity but does not define CareFlow roles, case relationships, or engine permissions.
- Authorization precedes consent/messaging exposure, and application-service transactions keep case state, evidence/approval, consent decision, outbox, and required audit/security records consistent.
- Observability follows request/auth context so events can correlate safely, but it lands before deployment; container/runbook work then proves the operational assumptions.
- The release gate crosses every boundary and remains offline/reproducible; no real IdP, PHI, delivery provider, cloud database, or hosted telemetry service is needed to prove this milestone.

### Research Flags

Phases likely needing deeper research during planning:

- **Phase 3:** Selected OIDC provider behavior, claims, JWKS rotation, logout, provisioning/linking, redirect/proxy policy, and outage handling.
- **Phase 4:** Owner/delegate/team scope, consent purpose and expiry, retention/access rules, and single-organization pilot policy.
- **Phase 6:** Deployment host, local volume characteristics, WAL/rollback journaling, backup protection, RPO/RTO, TLS/proxy, and non-root runtime behavior.

Phases with standard patterns (skip broad research-phase, but verify with tests):

- **Phase 1:** Pydantic settings, explicit feature flags, regression characterization, and synthetic fixtures.
- **Phase 2:** SQLite `user_version`, transactions, online backup, integrity checks, and migration-runner patterns are well documented; repository-specific compatibility tests remain mandatory.
- **Phase 5:** Standard-library JSON logging, correlation IDs, FastAPI middleware, security headers, CSRF, and health/readiness contracts are established patterns; browser/proxy smoke is still required.
- **Phase 7:** Test orchestration and Docker smoke are standard; the exact matrix must be derived from CareFlow's route and data policy.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | MEDIUM-HIGH | Existing repository manifests and official OIDC/SQLite/Docker/Python documentation strongly support the conservative stack; package versions, image digest, IdP, and deployment topology require implementation-time validation. |
| Features | MEDIUM-HIGH | Table stakes follow `PROJECT.md`, repository gaps, OWASP/OIDC/SQLite guidance, and operational reasoning; “competitive” differentiators are engineering judgment, not a market survey. |
| Architecture | HIGH for boundaries; MEDIUM for exact decomposition | Modular-monolith, principal/policy separation, transaction, and SQLite constraints are well supported; module names, provider seam, and scale envelope depend on the eventual deployment. |
| Pitfalls | HIGH for current repository risks and protocol primitives | Code/document evidence and primary standards agree; compliance, capacity, retention, and recovery recommendations remain deployment/jurisdiction dependent. |

**Overall confidence:** MEDIUM-HIGH

### Gaps to Address

- **Identity provider is undecided:** select one provider before Phase 3 planning and verify issuer, discovery/JWKS, claims, logout, provisioning, key rotation, and outage behavior without coupling the engine to it.
- **Authorization semantics are not fully product-defined:** decide whether same-role delegates, teams, or only assigned owners may act; encode that decision in the route/action matrix and pathway policy.
- **Deployment and recovery envelope is unknown:** select the supported host/volume/proxy enough to measure SQLite capacity, WAL suitability, backup protection, RPO/RTO, retention, and restore time; keep the application contract portable.
- **Consent/privacy governance is incomplete:** define purpose, scope, expiry boundary, withdrawal behavior, retention, deletion, access review, and jurisdiction before any real-data or external-provider discussion.
- **Legacy schema/session compatibility needs a rehearsal:** test baseline recognition, migration interruption, old signed-cookie rejection/clearing, and restore against a copy before pilot cutover.
- **Release evidence is incomplete:** current local research observed 39 passing tests but could not invoke `make` on Windows; clean Linux must prove `make test`, Docker build/run, health, volume replacement, and offline behavior.

## Sources

### Research documents (traceability)

- [`STACK.md`](./STACK.md) — conservative Python/FastAPI/SQLite/Docker stack, Authlib and pydantic-settings recommendations, OIDC/session/CSRF/configuration, migration/backup, health/logging, lockfile, and adoption order.
- [`FEATURES.md`](./FEATURES.md) — table stakes, differentiators, anti-features, dependency graph, file anchors, acceptance signals, and pilot-versus-enterprise boundaries.
- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — modular-monolith component boundaries, identity/session/authorization data flow, transaction and migration design, observability, failure isolation, build order, and verification strategy.
- [`PITFALLS.md`](./PITFALLS.md) — repository-specific critical/moderate pitfalls, warning signs, prevention/early detection checks, phase map, release exit checks, and unresolved research gaps.
- `.planning/PROJECT.md` — milestone constraints: synthetic-only, managed OIDC, auditable local outbox, deployment agnosticism, reproducibility, and explicit out-of-scope items.

### Primary and authoritative references cited by the research

- [OpenID Connect Core 1.0](https://openid.net/specs/openid-connect-core-1_0.html) and [OpenID Connect Discovery 1.0](https://openid.net/specs/openid-connect-discovery-1_0.html) — issuer, subject, audience, nonce, ID-token validation, discovery, and authorization-code behavior.
- [RFC 9700: OAuth 2.0 Security Best Current Practice](https://www.rfc-editor.org/rfc/rfc9700.html) and [RFC 7636: PKCE](https://datatracker.ietf.org/doc/html/rfc7636) — authorization-code/PKCE, redirect, CSRF, token, and password-grant guidance.
- [Authlib FastAPI integration](https://docs.authlib.org/en/stable/oauth2/client/web/fastapi.html) — Starlette/FastAPI client integration and temporary OAuth state handling; [Authlib PyPI](https://pypi.org/project/Authlib/) — release/support metadata and deprecated JOSE-module warning.
- [Starlette middleware](https://www.starlette.dev/middleware/) and [FastAPI middleware](https://fastapi.tiangolo.com/advanced/middleware/) — signed/readable session-cookie semantics, HTTPS/trusted-host controls, and middleware boundaries.
- [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) — typed environment/secrets loading and validation behavior.
- [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html), [Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html), [Session Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html), [CSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/CSRF_Prevention_Cheat_Sheet.html), [Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html), and [ASVS](https://owasp.org/www-project-application-security-verification-standard/) — deny-by-default authorization, identity linking, session/CSRF controls, redaction, event coverage, and verification checklist guidance.
- [Python `secrets`](https://docs.python.org/3.12/library/secrets.html) and [Python `logging`](https://docs.python.org/3.12/library/logging.html) — secure token generation and portable logging foundation.
- [Python `sqlite3.Connection.backup()`](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.backup), [SQLite Online Backup API](https://www.sqlite.org/backup.html), [SQLite PRAGMA](https://www.sqlite.org/pragma.html), [SQLite WAL](https://www.sqlite.org/wal.html), and [SQLite `ALTER TABLE`](https://sqlite.org/lang_altertable.html) — consistent snapshots, revisions, integrity/foreign-key checks, same-host WAL constraints, locking, and migration limits.
- [pip-tools](https://pypi.org/project/pip-tools/) — project-local hash-checked lock generation.
- [Docker build best practices](https://docs.docker.com/build/building/best-practices/) and [Dockerfile `HEALTHCHECK`](https://docs.docker.com/reference/dockerfile/#healthcheck) — clean context, pinned images, non-root execution, volumes, and health signaling.
- [OpenTelemetry observability primer](https://opentelemetry.io/docs/concepts/observability-primer/) — provider-neutral distinction among logs, metrics, traces, correlation, and reliability signals; used as a later-compatible model, not a dependency decision.

---
*Research completed: 2026-10-19*
*Ready for roadmap: yes*
