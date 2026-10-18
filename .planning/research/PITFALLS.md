# Domain Pitfalls

**Domain:** FastAPI care-case pilot hardening with OIDC/SSO, role and relationship authorization, consent/audit records, SQLite, server-rendered pages, and Docker  
**Project:** CareFlow  
**Researched:** 2026-10-19  
**Overall confidence:** HIGH for repository-specific findings; HIGH for OIDC/SQLite/Docker primitives from primary sources; MEDIUM for deployment and compliance-program recommendations because the identity provider, host, jurisdiction, and traffic profile are not selected.

## How to use this document

This is a roadmap-risk document, not a certification checklist. Each item has a concrete warning sign, a prevention strategy, an early-detection check, and a roadmap phase. The phase names below are recommendations; preserve the dependency order when numbering the final roadmap.

### Recommended phase map

1. **Identity and secure session boundary** — managed OIDC, claim validation, local account mapping, cookie/session lifecycle, secret configuration.
2. **Authorization, consent, evidence, and audit invariants** — object-level access policy, patient-safe projections, provenance, consent history, and immutable domain events.
3. **SQLite lifecycle and consistency** — migrations, transaction/concurrency policy, timestamps, outbox state machine, backup and restore.
4. **Observability, privacy, and supportability** — health/readiness, structured logs, redaction, retention, diagnostics, and operational alerts.
5. **Server-rendered web and HTTP security** — CSRF, security headers, cache policy, safe error pages, and HTML/JSON policy parity.
6. **Container and deployment hardening** — image reproducibility, non-root runtime, writable data volume, trusted proxy/TLS configuration, and upgrade/rollback runbook.
7. **Pilot verification and release gate** — route/authorization matrix, fake-IdP tests, browser checks, concurrency tests, migration/restore drills, and Docker smoke tests.

## Critical pitfalls

### 1. Treating an identity lookup or decoded JWT as authentication

**Risk:** Critical — security, privacy, audit integrity  
**Roadmap:** Phase 1

**What teams get wrong:** They replace the current demo login with “read a user ID from a token” or “decode a JWT,” without proving that the token was issued for CareFlow by the configured issuer and for the configured audience. The result is an authenticated-looking session whose role and audit attribution cannot be trusted.

**Concrete warning signs in this repository:**

- `LoginInput` in `careflow/api.py:106-127` accepts a caller-supplied user identifier, and `/demo-login/{user_id}` in `careflow/api.py:760-770` creates a session from a GET link.
- There is no OIDC callback, discovery/JWKS configuration, issuer allowlist, or external identity lifecycle in `careflow/api.py` or `careflow/persistence.py`.
- `CONCERNS.md` and `ARCHITECTURE.md` contain statements about an unauthenticated prototype that have drifted from the current signed-cookie routes. Documentation drift is itself a security warning because it hides which boundary is authoritative.

**Prevention:**

- Use a maintained OIDC client and the server-side Authorization Code flow. Bind the browser transaction with high-entropy `state` and validate the ID-token `nonce`; use PKCE as an additional code-injection defense.
- Pin one configured issuer and client audience for the pilot. Validate the signature using the provider's JWKS, `iss`, `aud`/`azp` where applicable, `exp`, `iat`, and the expected `nonce`; handle signing-key rotation by refreshing JWKS safely rather than accepting an unknown key.
- Persist the federated identity as `(issuer, subject)` (`iss`, `sub`), not as email or `preferred_username`. Do not auto-link an existing CareFlow user merely because an email matches.
- Map verified identities to a local user record and server-owned role/assignment data. A missing, ambiguous, inactive, or unmapped identity must fail closed; a client header or request-body `actor` must never supply authority.
- Test local logout separately from IdP logout. Document whether the pilot only clears the CareFlow session or also invokes the provider's end-session endpoint.

**Early detection:**

- Add offline fake-provider tests for bad signature, wrong issuer, wrong audience, expired token, wrong nonce, unknown `kid`, replayed callback state, and inactive/unmapped local user.
- Start in pilot mode with OIDC configuration absent and assert a clear non-zero startup failure; assert that `/auth/login` and `/demo-login/*` are disabled rather than silently becoming a back door.
- Review an audit event after login and verify it contains the local user bound to the verified `(iss, sub)`, never a value supplied in JSON.

**Pilot-safe vs larger program:** A single issuer, one client, explicit claim mapping, short local sessions, and a documented local-logout behavior are a reasonable pilot baseline. SCIM provisioning/deprovisioning, multiple issuers, step-up/MFA policy, break-glass accounts, identity proofing, and enterprise access reviews require a larger identity-governance program.

### 2. Allowing security defaults and cookie behavior to choose the deployment policy

**Risk:** Critical — session forgery, session instability, CSRF  
**Roadmap:** Phase 1, with browser controls completed in Phase 5

**What teams get wrong:** They leave a development secret fallback, accept a random secret per process, or assume `SameSite=Lax` is a complete CSRF defense. They then run multiple workers or place the app behind TLS without validating cookie and origin behavior.

**Concrete warning signs in this repository:**

- `careflow/api.py:553-567` accepts either of two environment variable names and generates a random secret when neither is set. `CONCERNS.md` describes a source-controlled default secret, so the code and concern record need reconciliation. A constant would permit forgery; a random per-process value invalidates all sessions on restart and makes workers disagree.
- `set_session_cookie` in `careflow/api.py:715-724` sets `HttpOnly` and `SameSite=Lax` but not `Secure`; cookie-authenticated mutations have no CSRF token or trusted-origin check.
- The GET demo-login route and public demo-user links in `careflow/templates/landing.html` are appropriate only for an explicitly local synthetic mode, not a shared pilot.

**Prevention:**

- Define one canonical configuration schema. In non-development/pilot mode, require an explicit high-entropy secret or use a server-side session store; never silently generate or use a known fallback. Validate the secret, session lifetime, external origin, and OIDC settings before serving requests.
- Rotate the session identifier after successful OIDC login. Use `Secure`, `HttpOnly`, an appropriate `SameSite` policy, a narrow path, and preferably a `__Host-` cookie where the deployment permits it. Do not set a broad `Domain` attribute.
- Protect every cookie-authenticated state change: case creation, transition, assignment, draft creation/approval, logout, and any future HTML form. Use a synchronizer token or signed double-submit token plus an explicit trusted `Origin`/`Referer` policy. OIDC `state` protects the login transaction; it is not a substitute for mutation CSRF protection.
- Keep demo login behind a development-only feature flag and make the flag impossible to enable accidentally in a production configuration.
- Choose and document a secret rotation behavior. A pilot may invalidate all sessions on rotation; do not claim individual revocation unless session IDs/revocation state are modeled.

**Early detection:**

- Run the app without the secret in each deployment mode and assert that production startup fails. Run two app instances with the same configured secret and assert that a session created by one is accepted by the other; run with different secrets and assert it is rejected.
- Inspect `Set-Cookie` in an HTTPS staging smoke test for `Secure`, `HttpOnly`, `SameSite`, path, and absence of `Domain`.
- Submit each mutation without a CSRF token and with an untrusted `Origin`; expect rejection without changing case, consent, audit, or outbox state.

### 3. Treating role authorization as sufficient case authorization

**Risk:** Critical — horizontal privilege escalation and unauthorized mutation  
**Roadmap:** Phase 2

**What teams get wrong:** They implement “staff” and “patient” checks once, then assume a valid role grants access to every case and every operation. Role authorization answers *what kind of action* a person may perform; it does not answer *which case*, *which organization*, or *which relationship* they may access.

**Concrete warning signs in this repository:**

- `authorize_case_access` in `careflow/api.py:636-642` restricts only patients; any authenticated non-patient can read any case.
- `authorize_transition` in `careflow/api.py:664-679` checks the current state's owner role and approver roles but not `case.owner == user.id`. The distinction between role permission and assignment ownership is documented in `docs/safety.md:47-49` but is not yet a deliberate pilot policy.
- `/worklist`, `/metrics`, `/message-drafts`, and patient/case collection routes enumerate broadly. Hidden UI links do not protect their underlying JSON or compatibility aliases.

**Prevention:**

- Write an explicit subject/resource/action matrix for every route and representation: patient status, patient case detail, staff case detail, worklist, metrics, evidence, approval, access-log views, draft creation/list/get/approval, assignment, transition, and all aliases.
- Centralize decisions such as `can_read_case`, `can_view_audit`, `can_mutate_case`, `can_assign`, and `can_approve_message`; call them after authentication and before serialization. Deny by default.
- Use relationship/attribute checks in addition to role: linked patient, assigned owner or permitted team, organization membership, case state, and action. Filter collections in SQL/repository queries, not only after loading all rows into Python.
- Re-evaluate the active local role and assignment on every request. Never trust a role in a client header, hidden form field, or request-body `actor`.

**Early detection:**

- Maintain a route inventory test that exercises JSON, `Accept: text/html`, compatibility aliases, direct object IDs, and every mutation as each seeded role.
- For every denied request, assert both the response and unchanged case/audit/evidence/approval/outbox state. Include a non-owner with the same role, a staff member from a different organization fixture, and a patient changing `CASE-001` to `CASE-002`.
- Add a policy test that fails when a new route is added without an authorization classification.

**Pilot-safe vs larger program:** A single organization can be safe for a pilot only if that scope is explicit and enforced. Multi-tenant isolation, delegated teams, cross-organization referrals, break-glass access, periodic entitlement review, and separation of duties belong to a larger authorization program.

### 4. Returning a full staff case record to a patient or relying on opaque-looking IDs

**Risk:** Critical — privacy disclosure  
**Roadmap:** Phase 2, with representation and cache checks in Phase 5

**What teams get wrong:** They let a patient read “their own case,” then return the same DTO used by staff. They also treat `CASE-001` or a UUID-like path as authorization. A predictable identifier is not an access control.

**Concrete warning signs in this repository:**

- `get_case` in `careflow/api.py:1059-1075` allows the linked patient through `authorize_case_access` but returns `_case_payload`, which includes `audit`, evidence, owner, state timing, and patient references.
- `careflow/templates/patient.html` links patients to the full `/cases/{case_id}` page. `get_message_draft` and `list_case_message_drafts` authorize the case relation but do not apply an explicit staff-only view policy.
- `get_patient` in `careflow/api.py:1077-1105` returns complete case payloads to any non-patient staff user. Case IDs and seeded user IDs are easy to enumerate.

**Prevention:**

- Define separate patient-safe and staff DTOs. A patient projection should contain only the intended plain-language status, safe next step, consent-relevant information, and timestamps; do not expose internal audit notes, approval notes, staff identifiers, evidence details, or draft text unless explicitly required.
- Apply object authorization before loading/serializing the object and use scoped repository queries for collections. Return a consistent not-found/forbidden policy that does not reveal whether another patient's case exists.
- Add `Cache-Control: no-store` to authenticated HTML/JSON responses and ensure a reverse proxy cannot cache a patient page for another user.
- Keep identifiers non-sensitive where possible, but test authorization with guessed and substituted IDs regardless of identifier format.

**Early detection:**

- Add assertions that a patient can see their own status but not staff audit/approval/evidence details, message drafts, or another case's existence. Test both JSON and HTML.
- Put a sentinel internal note and sentinel message in fixtures; assert they never appear in patient responses, browser page source, response headers, access logs, or error pages.

### 5. Accepting caller-controlled evidence, approvals, consent state, and mutable audit history

**Risk:** Critical — case-integrity and consent failures  
**Roadmap:** Phase 2; outbox consequences continue in Phase 3

**What teams get wrong:** They interpret a string such as `clinical_review` or an approval name as proof that an event occurred. They also update a single consent row in place and call a rewritable database table an immutable audit trail.

**Concrete warning signs in this repository:**

- `TransitionInput.evidence` in `careflow/api.py:159-174` is a list of arbitrary strings. `ApprovalInput` accepts a free-text `name`, role, and note.
- `SQLiteStore.save_case` in `careflow/persistence.py:708-811` deletes/reinserts audit rows and upserts evidence/approval records. `add_consent` in `careflow/persistence.py:406-434` upserts the current consent row, so prior status changes are not an immutable history.
- `approve_message_draft` in `careflow/persistence.py:632-706` checks current consent atomically with approval/queueing, but there is no explicit consent-event history or dispatch-time revocation behavior.

**Prevention:**

- Create evidence and approval resources server-side with the authenticated principal, server timestamp, applicable case/action/state, and provenance/source. Bind the approval to the verified local user ID; treat a supplied display name as non-authoritative text.
- Reject evidence records that do not exist, are not attached to the case, or do not apply to the action/state. Make waiver rules explicit and narrow; do not let a generic approval string waive any future requirement.
- Append immutable case events with monotonic sequence/version and preserve the original event. If the pilot keeps database-local audit, restrict update/delete privileges and provide an integrity check; do not describe it as tamper-proof or legally non-repudiable.
- Represent consent as versioned grant/withdraw/expire events with subject, case, purpose, source, actor/provenance, effective time, and expiry. Recheck consent at every operation that would disclose or deliver a message.

**Early detection:**

- Submit a transition with fake evidence and a mismatched approval name; assert rejection or a record bound to the authenticated user, never acceptance based only on strings.
- Update/withdraw consent and verify the prior grant remains discoverable in an audit history. Queue a message, withdraw consent, and assert the documented pilot behavior (no delivery worker; a future dispatcher must cancel or recheck it).
- Mutate a case concurrently or replay an old audit payload and assert a version/integrity error rather than silent rewrite.

**Pilot-safe vs larger program:** Authenticated server-side provenance, append-only application behavior, consent rechecks, and a restricted SQLite file are useful pilot controls. Independent tamper-evident storage, WORM retention, legal hold, formal consent language, clinical-signature policy, and certified records-management controls require a larger compliance program.

### 6. Mistaking “synthetic-only” documentation for a technical privacy boundary

**Risk:** Critical — privacy and scope failure  
**Roadmap:** Phase 2 and Phase 6

**What teams get wrong:** They label seeded data synthetic, then leave the same unrestricted inputs and deployment path available for a user to paste real information. A disclaimer is not a data-loss-prevention control.

**Concrete warning signs in this repository:**

- `README.md` and `docs/safety.md` clearly state synthetic-only scope, but `CreateCaseInput` and repository methods accept arbitrary names, email addresses, notes, evidence details, and message text.
- `careflow/templates/landing.html` exposes demo-user links, and `Dockerfile:5-6` copies the application and seeds a database during image build. A shared deployment can therefore look populated and real even though it is only a demo fixture.
- `careflow/persistence.py` stores patient identifiers, emails, case history, consent, approval notes, and message payloads in a local unencrypted SQLite file; no retention/deletion or backup privacy policy exists.

**Prevention:**

- Make synthetic mode an explicit startup/deployment setting. In pilot mode, disable demo login and seed/reset routes unless a development flag is present; refuse known demo users in a non-demo data store and display a prominent synthetic-only banner.
- Keep seed databases and runtime data on separate paths/volumes. Never run a reset seed command against a mounted pilot database. Add a preflight scan of image, database, exports, and fixtures for demo markers and accidental secrets.
- Minimize data accepted and returned. Keep the message provider rule-based/offline for this milestone; do not send case or patient context to an external model or notification provider.
- Define deletion, backup access, and export handling even for synthetic data so the pilot habits do not become production defaults.

**Early detection:**

- Start with pilot configuration and assert `/demo-login/*` is unavailable, no demo users are seeded, and an empty writable data volume remains empty until an explicit initialization command.
- Run a clean-image scan for `demo-`, `example.test`, session secrets, `.env`, `careflow.db`, `.planning`, and test artifacts; treat unexpected matches as a release failure.
- Include a test fixture containing a canary string and assert it is absent from operational logs and patient-safe responses where it is not required.

**Boundary:** Synthetic-only, private-network operation, access control, no real delivery, and explicit non-clinical documentation are pilot-safe controls. They are not HIPAA/GDPR compliance, clinical-production approval, a BAA, de-identification certification, or a guarantee that a user cannot enter PHI. Real data requires a jurisdiction-specific risk analysis, data inventory, legal basis/BAA, encryption and key management, retention/deletion, incident response, access review, and vendor due diligence.

### 7. Using one shared SQLite connection with load–mutate–save case updates

**Risk:** Critical — lost updates, lock errors, audit corruption  
**Roadmap:** Phase 3

**What teams get wrong:** They turn on `check_same_thread=False`, assume that makes SQLite thread-safe, and then run FastAPI requests concurrently. A transition loads a snapshot, mutates it, and saves it later; two requests can both pass validation and the last save can overwrite the other state/audit event.

**Concrete warning signs in this repository:**

- `SQLiteStore.__init__` in `careflow/persistence.py:301-313` owns one connection with `check_same_thread=False` but no application lock, connection-per-request policy, `busy_timeout`, or WAL policy.
- Transition and assignment routes in `careflow/api.py:829-874` use separate load and save operations. `save_case` rewrites the aggregate snapshot rather than applying a versioned event.
- `CONCERNS.md` records no concurrency or lost-update tests; current tests are sequential and in-process.

**Prevention:**

- Choose and enforce the pilot topology: one process/one worker, one local durable filesystem, and a bounded write rate unless the repository has a tested multi-connection policy.
- Prefer a connection per request or a deliberately synchronized repository. Set a bounded busy timeout, use short transactions, and make mutation transactions explicit. Add an optimistic `version`/compare-and-swap condition so the second writer receives a conflict rather than overwriting history.
- If WAL is selected, keep all processes on the same host and monitor/checkpoint the `-wal` file; never place a WAL database on a network filesystem. WAL still allows only one writer at a time.
- Keep the case row, audit event, evidence/approval changes, and any outbox insert in the transaction whose outcome is reported to the caller.

**Early detection:**

- Use two real SQLite connections and concurrent threads to transition and assign the same case. Assert one valid outcome, one conflict, contiguous audit sequence, and no 500/`database is locked` leak.
- Run the service with the deployment's intended worker count. A pilot release should fail or be explicitly blocked when multiple workers point at an unsupported SQLite topology.
- Measure lock wait, transaction duration, and conflict counts under a synthetic burst rather than relying on a sequential `TestClient` suite.

### 8. Treating `CREATE TABLE IF NOT EXISTS` as schema migration and copying a live database unsafely

**Risk:** Critical — upgrade data loss and unrecoverable pilot  
**Roadmap:** Phase 3, with volume/rollback work in Phase 6

**What teams get wrong:** They add a column or constraint to `SCHEMA`, restart against an old file, and assume it upgraded. They also copy only `careflow.db` while a WAL or journal contains committed data, or rebuild the container and lose the writable database.

**Concrete warning signs in this repository:**

- `SQLiteStore.initialize` in `careflow/persistence.py:315-319` executes `SCHEMA` but has no schema-version table, ordered migrations, compatibility check, or migration command.
- `Dockerfile:1-7` creates `careflow.db` in the image at build time; there is no `VOLUME`, backup command, restore check, migration gate, or rollback procedure.
- The project writes audit, consent, access, and outbox history with no retention or archival policy, so a “small pilot” can still produce an important and growing file.

**Prevention:**

- Add a schema version and ordered, idempotent migrations. Run migrations as an explicit single-owner startup/release step, validate the resulting schema before readiness, and fail closed on an unknown future version.
- Put runtime data in a deliberate writable volume outside the image. Do not seed or reset production data in a Docker build or ordinary container startup.
- Back up with SQLite's backup API or a coordinated checkpoint/backup procedure; treat `-wal` and `-shm` as part of the database state when copying files. Verify backups by opening a restored copy, running integrity checks, loading cases, and checking audit continuity.
- Write an upgrade/rollback runbook: backup, migrate, smoke test, readiness, rollback limits, and who can restore. Protect backup copies as sensitive data.

**Early detection:**

- Keep fixtures for each prior schema and test upgrade from each one. Kill the process during migration and verify the next start is safe.
- Replace a container while reusing the data volume and assert cases, consent, audit, and outbox rows survive. Restore the backup into a clean image and run `PRAGMA integrity_check` plus application-level invariants.
- Check that readiness is false during migration or when the schema is incompatible.

**Pilot-safe vs larger program:** A single-host volume, scheduled encrypted backup, and a practiced restore are a pilot baseline. Multi-region recovery, point-in-time recovery, tested RPO/RTO, high availability, database encryption/key rotation, and a managed service are required before making larger availability or real-data claims.

### 9. Treating a queued notification as delivered and ignoring consent revocation

**Risk:** High — privacy and reliability  
**Roadmap:** Phase 2 for policy; Phase 3 for outbox mechanics

**What teams get wrong:** They expose a status called “sent” or add a worker before defining claim, retry, idempotency, revocation, and failure semantics. A queued local record is then mistaken for a patient communication.

**Concrete warning signs in this repository:**

- `approve_message_draft` in `careflow/persistence.py:632-706` intentionally queues only; there is no delivery worker or external transport.
- `mark_notification_sent` in `careflow/persistence.py:1061-1073` updates any matching ID, does not require `queued`/`processing`, does not check row count, and increments attempts if called repeatedly.
- The outbox payload includes message text, and consent is checked at approval/queue time. There is no claim lease, failure transition, backoff, or dispatch-time consent check.

**Prevention:**

- Keep the pilot vocabulary precise: `queued` means local simulation record, never “sent.” Do not add a transport in this milestone.
- If dispatch is later added, implement an atomic claim with lease/expiry, guarded state transitions, bounded retries/backoff, idempotency/dedupe, failure reason, and a clear operator retry path. Recheck consent immediately before dispatch and define what withdrawal does to queued/processing records.
- Never put message bodies or recipient addresses in ordinary application logs. Restrict outbox and draft access using the same case policy as case data.

**Early detection:** Test duplicate approval, duplicate `mark_notification_sent`, unknown IDs, two workers claiming one row, worker crash/restart, retry exhaustion, consent expiry/revocation after queueing, and a queued row that must remain visibly unsent.

### 10. Mixing naive local time with UTC-aware timestamps at SLA and consent boundaries

**Risk:** High — incorrect escalation, expiry, and audit ordering  
**Roadmap:** Phase 3, with edge-case regression tests in Phase 7

**What teams get wrong:** They store local naive timestamps in one path and UTC-aware timestamps in another, then trust comparisons at daylight-saving or consent-expiry boundaries.

**Concrete warning signs in this repository:**

- `_default_clock` in `careflow/api.py:60-63` returns `datetime.now()` without timezone information, while persistence defaults use UTC-aware timestamps and `engine.hours` subtracts values directly.
- Consent validity in `careflow/persistence.py:902-932` has an exact expiry comparison, but the input timestamp convention is not enforced at the API boundary.

**Prevention:**

- Make UTC-aware datetimes the sole internal and storage convention. Normalize external timestamps at ingress and serialize with an explicit offset/Z.
- Inject one clock abstraction into API, engine, persistence, and outbox code. Define whether expiry at exactly `expires_at` is invalid and document it.
- Test DST changes, clock skew, timestamps before case creation, exact SLA/consent boundaries, and ordering of events from different workers.

**Early detection:** Run the suite with both aware and naive inputs and assert naive input is rejected or normalized at one boundary; never allow a late `TypeError` from `engine.py` to become a production 500.

## Moderate and recurring pitfalls

### 11. Assuming Jinja autoescape solves server-rendered web security

**Risk:** High — XSS, CSRF, cache leakage, clickjacking  
**Roadmap:** Phase 5

**What teams get wrong:** They see autoescape configured and stop there. Authenticated HTML is still cookie-authenticated, errors may contain internal details, and browser/proxy behavior can leak a previously viewed patient page.

**Concrete warning signs in this repository:**

- `careflow/api.py:53-57` enables Jinja autoescape, which is good, but `careflow/templates/base.html` adds no CSP, frame, referrer, MIME, HSTS, or cache policy headers.
- `careflow/api.py:595-609` renders `HTTPException.detail` into `error.html`; future database or provider errors must not expose SQL, file paths, tokens, or patient details.
- `careflow/templates/landing.html` and `/demo-login/*` use GET to establish a signed session. There are no HTML mutation forms yet, but future forms must not rely on hidden buttons for authorization.

**Prevention:**

- Keep autoescape on and never add `|safe` for names, notes, evidence, approval text, message drafts, or audit metadata. Validate length and control characters as well as escaping.
- Add security headers at the application or trusted edge: `Content-Security-Policy`/`frame-ancestors`, `X-Content-Type-Options`, `Referrer-Policy`, and HSTS only when HTTPS is guaranteed. Use `Cache-Control: no-store` and an appropriate `Pragma` policy for authenticated responses.
- Give users generic error details and a correlation ID; log the diagnostic server-side with redaction. Apply the same authorization policy to HTML and JSON content negotiation.
- If a future page mutates state, use POST plus CSRF protection; do not make login, logout, transition, assignment, approval, or deletion actions GET links.

**Early detection:** Render canary values such as `<script>`, quotes, newlines, and long strings through user name, evidence, approval, and message fields. Assert escaped HTML, safe JSON, security headers, `no-store`, and no canary in an unauthorized error page. Run a browser smoke test, because `TestClient` does not model browser cache, cookie, or TLS behavior.

### 12. Confusing case audit/access rows with security and operational logging

**Risk:** High — missed incidents, privacy leakage, unbounded storage  
**Roadmap:** Phase 4, with domain-event changes in Phase 2

**What teams get wrong:** They call `audit_events` an audit/compliance solution and omit failed authentication, authorization denials, configuration changes, startup failures, database locks, and provider failures from any reviewable log. Or they put patient/message content and cookies into debug logs.

**Concrete warning signs in this repository:**

- `careflow/api.py` records successful case access through `record_access`, but failed authentication and authorization are not persisted or emitted to a structured security log.
- `careflow/` has no application logger; `careflow/demo.py` and `careflow/seed.py` print human-readable output. Collection reads can write one `access_logs` row per case (`careflow/api.py:1108-1143`).
- `access_logs`, audit metadata, approval notes, and outbox payloads can contain sensitive identifiers or free text, while no retention or restricted log-reader policy is documented.

**Prevention:**

- Separate (1) immutable case/domain events, (2) access/security events, and (3) operational process logs. Give each a purpose, schema, retention, access policy, and export path.
- Emit structured JSON to stdout with UTC timestamp, service/version, request/interaction ID, event type, outcome, actor pseudonym, case reference or hash where necessary, and latency. Log authentication failures, authorization denials, input validation failures, schema/migration errors, database locks, and startup/shutdown.
- Never log cookies, authorization headers, ID/access/refresh tokens, secrets, message bodies, consent notes, evidence details, or unnecessary patient names/emails. Sanitize newlines and delimiters to prevent log injection.
- Bound access-log growth and do not make a failed logging sink mutate a case twice or turn a successful case mutation into an ambiguous retry.

**Early detection:** Send hostile input and invalid cookies, then inspect all stdout, error pages, and database logs for secrets, raw tokens, message bodies, and unsanitized newlines. Simulate a full disk or logging failure and verify the service fails safely with an operational alert rather than silently losing security events.

### 13. Adding a health endpoint that leaks data or calling an unready service “healthy”

**Risk:** High — unreliable deployment and information disclosure  
**Roadmap:** Phase 4

**What teams get wrong:** They expose a health endpoint that runs a full worklist/metrics query and returns case counts, or use one endpoint that says both “process is alive” and “database/schema/configuration is ready.”

**Concrete warning signs in this repository:**

- No liveness/readiness endpoint, structured startup diagnostic, schema check, or database-lock diagnostic exists in `careflow/api.py` or `Makefile`.
- `/metrics` is an authenticated business endpoint that hydrates every case; it is not an infrastructure health check.

**Prevention:**

- Provide separate liveness and readiness checks. Liveness should not depend on SQLite or the OIDC provider; readiness should verify the configured database path is usable, schema version is supported, pathway validation succeeds, required secrets/configuration exist, and migrations are complete.
- Return only generic status, version/schema identifiers, and dependency status appropriate for the caller; never return patient, case, consent, or secret material.
- Add startup and shutdown logs, correlation IDs, bounded request timing, and alerts for locked database, disk-full, failed migrations, stale outbox, and repeated OIDC/JWKS failures.

**Early detection:** Start with missing secret, incompatible schema, read-only data directory, invalid pathway YAML, and closed database. Assert readiness is non-200 with an actionable operator-safe reason while liveness remains useful for process supervision.

### 14. Building a Docker image that is reproducible but not safe to run

**Risk:** High — supply-chain, privilege, data-loss, and deployment failure  
**Roadmap:** Phase 6

**What teams get wrong:** They equate “installs the lockfile” with production hardening. A mutable base image, root process, oversized build context, baked database, and no volume/healthcheck create a fragile artifact.

**Concrete warning signs in this repository:**

- `Dockerfile:1-7` uses floating `python:3.12-slim`, runs as root, has no `USER`, `HEALTHCHECK`, `VOLUME`, migration step, or data path, and seeds `careflow.db` during image build.
- `Dockerfile:5` uses `COPY . .`. `.dockerignore` excludes the local database and common caches, but not `.planning`, documentation, or every possible local artifact. `CONCERNS.md` also records unhashed dependency artifacts.
- The image command starts one Uvicorn process, but a platform operator could add multiple workers without understanding the SQLite policy.

**Prevention:**

- Pin a reviewed base-image digest and update it through an auditable process. Use a hash-checked dependency lock or equivalent artifact verification and generate an SBOM/vulnerability report.
- Copy only required source/templates/static/configuration files, keep secrets out of build args and layers, run as a fixed non-root UID, and make only the deliberate data directory writable.
- Keep seed/demo data out of the production image or make initialization an explicit command against an empty volume. Add a healthcheck and use an exec-form command with a documented one-worker topology.
- Verify the container starts with a mounted data volume, survives replacement, receives termination signals, and never writes database state into the immutable image layer.

**Early detection:** Build with a clean context and inspect `docker history`/image contents; run as the image user, verify the data directory permissions, run the healthcheck, replace the container with the same volume, and run the test suite/smoke checks in a clean Linux image rather than the developer interpreter.

### 15. Letting reverse-proxy headers and deployment defaults determine OIDC redirects and transport security

**Risk:** High — callback misbinding, insecure cookies, host-header/open-redirect issues  
**Roadmap:** Phase 1 and Phase 6

**What teams get wrong:** They build callback URLs from an untrusted `Host` or `X-Forwarded-*` header, register a wildcard redirect URI, or terminate TLS without telling the application which external origin is trusted.

**Concrete warning signs in this repository:**

- `README.md` documents local HTTP and Docker HTTP only; there is no explicit public origin, trusted proxy, allowed-host, or TLS policy.
- The current cookie omits `Secure`, and the OIDC integration has not yet defined a callback path or exact redirect registration.

**Prevention:**

- Configure the external HTTPS origin explicitly and register exact redirect URIs. Trust forwarded headers only from known proxies; reject unexpected host/origin values and never redirect to a user-supplied URL.
- Terminate TLS at a documented trusted edge, set `Secure` cookies, use HSTS only after HTTPS is universal, and test the complete path through that edge.
- Keep provider configuration, client secret, and redirect origin in the deployment secret/configuration manager; print only a redacted configuration summary at startup.

**Early detection:** Run a staging proxy smoke test with HTTP/HTTPS, alternate `Host`, forged `X-Forwarded-Proto`, mismatched callback origin, and a malicious `next` parameter. Every case should fail safely and leave no token in the URL, logs, or referrer.

### 16. Letting full scans and unbounded audit/access rows become the pilot's reliability limit

**Risk:** Moderate to high — latency, lock contention, disk exhaustion  
**Roadmap:** Phase 3, Phase 4, and release measurement in Phase 7

**What teams get wrong:** They test 40 seeded cases, then assume the same Python hydration and per-case access logging will remain acceptable. Collection reads in `careflow/api.py:1108-1143` load every case and write an access row for every record; `save_case` rewrites the full audit snapshot.

**Prevention:**

- Add query-oriented projections and indexes for worklist/metrics, paginate collections, and compute case explanations once per request. Use one collection access event or bounded batch rather than one write per row where the policy permits.
- Define retention classes for access logs, audit history, outbox rows, and backups. Monitor database size, WAL size, query latency, lock waits, and free disk.
- Set pilot volume limits in the runbook. Do not silently allow a second worker or a network filesystem as a scaling strategy.

**Early detection:** Load 100, 1,000, and the maximum pilot dataset with realistic histories. Record p50/p95 latency, SQLite size/WAL size, lock errors, and log volume; inspect query plans before declaring the pilot capacity.

### 17. Mistaking an in-process happy-path test suite for a production security gate

**Risk:** Critical — false release confidence  
**Roadmap:** Phase 7

**What teams get wrong:** They rely on `TestClient` and sequential temporary SQLite tests, mock away the identity provider, and treat passing unit tests as evidence that the deployed browser, proxy, database, and container are secure.

**Concrete warning signs in this repository:**

- `TESTING.md` describes unit/integration coverage but no browser or deployed-service E2E; there are no concurrency, migration/restore, Docker non-root, proxy, or health-check tests.
- `tests/test_auth.py` covers signed demo sessions, but not OIDC state/nonce/JWKS validation, claim mapping, CSRF, cookie `Secure`, role/assignment/organization matrices, or session rotation/revocation.
- `CONCERNS.md`, `ARCHITECTURE.md`, `README.md`, and current code have contradictory authentication/test-contract statements. A green suite can therefore be testing the wrong contract.

**Prevention:**

- Keep the required offline `make test` deterministic. Add a local fake OIDC provider or recorded, non-secret fixtures; verify the real token-validation library rather than only mocking its return value.
- Add a route matrix for every JSON/HTML representation and alias; positive and negative tests for every role, case relationship, owner, consent status, and transition. Assert no mutation on denied requests.
- Add browser smoke checks for cookies, CSRF, cache headers, security headers, HTML escaping, login/logout, and patient-safe pages. Add concurrent SQLite, migration, backup/restore, readiness, container build/run, non-root, and volume-replacement tests.
- Make source/docs contract drift a test or release check: auth-required routes, synthetic-only statement, supported seed flow, exact environment variables, and expected test count should agree.

**Early detection:** The release gate should run, from a clean Linux environment, `make test`, a clean Docker build, a container smoke test, a migration/restore check, and the authorization matrix. A passing local `pytest` run alone is not sufficient.

**Boundary:** These tests provide a pilot baseline, not a penetration test, independent security assessment, clinical validation, or regulatory certification. A larger program still needs threat modeling, dependency/SBOM monitoring, DAST/fuzzing, external penetration testing, incident exercises, and formal privacy/compliance review.

### 18. Sending case context to an external message/LLM provider without a data boundary

**Risk:** Moderate now; potentially critical with real data — privacy, patient safety, vendor risk  
**Roadmap:** Phase 2 policy; Phase 6 provider-egress decision

**What teams get wrong:** They turn on an “LLM” environment variable because the interface exists, then send patient names, case IDs, urgency, states, evidence, or draft content to a provider without a data-processing agreement, retention control, prompt-injection review, or human approval.

**Concrete warning signs in this repository:**

- `careflow/api.py:895-907` passes patient name and case context to the configured message provider. `CAREFLOW_MESSAGE_PROVIDER=llm` is intentionally unconfigured today, which is the safer behavior.
- `docs/safety.md` says the message provider cannot transition or close a case, but provider egress, model retention, data residency, and content moderation are not part of this milestone.

**Prevention:**

- Keep `RuleBasedProvider` as the only enabled pilot provider and make `llm` startup-fail or remain explicitly unavailable. Approved drafts must stay drafts/outbox records; no model may make a clinical decision or bypass human review/consent.
- If external generation is later approved, minimize and pseudonymize context, configure provider data retention/residency and contract controls, bound output length/content, record provider/version and reviewer, and treat model output as untrusted text subject to HTML escaping and clinical-language review.
- Never let generated text become evidence, approval, transition authority, or consent proof.

**Early detection:** In offline tests, assert no network call is possible and no patient name/message leaves the provider boundary. Run prompt/content canaries and verify generated output cannot create a transition or approval.

## Phase-specific warnings and exit checks

| Roadmap phase | Must address | Minimum early exit checks |
|---|---|---|
| Phase 1 — Identity/session | OIDC validation, `(iss, sub)` mapping, session rotation, secret fail-closed, cookie flags, provider/logout policy | Fake-IdP invalid-claim suite; two-instance session test; no-secret startup failure; exact callback/redirect test |
| Phase 2 — Authorization/consent/audit | Route matrix, owner/team/patient scope, patient-safe projections, evidence/approval provenance, consent history, append-only event policy | Every route/alias in JSON and HTML as every role; denied requests are non-mutating; patient canary cannot see staff fields; consent revoke/replay tests |
| Phase 3 — SQLite consistency | Schema versions/migrations, one-writer policy, transaction/versioning, UTC, outbox state machine, backup/restore | Old-schema upgrade; concurrent mutation; `SQLITE_BUSY` behavior; outbox claim/retry/revoke; restored database integrity and application load |
| Phase 4 — Operations/privacy | Liveness/readiness, structured redacted logs, retention, safe diagnostics, lock/disk/outbox alerts | Readiness failure matrix; log redaction/injection test; no patient data in health output; logging/full-disk behavior |
| Phase 5 — Server-rendered HTTP | CSRF, security headers, cache isolation, HTML/JSON parity, safe errors, no GET mutations | Browser smoke through HTTPS proxy; missing/invalid CSRF; XSS canary; `no-store`; alternate `Accept` and alias tests |
| Phase 6 — Container/deployment | Pinned image/dependencies, non-root, deliberate volume, no baked runtime DB/secrets, trusted proxy, rollback runbook | Clean Linux build; image-content scan; non-root/volume replacement; healthcheck; proxy header/redirect tests; backup restore |
| Phase 7 — Pilot gate | Reproducible offline test, browser/E2E, concurrency, migration, Docker and privacy boundary | `make test` plus clean Docker smoke, route matrix, restore drill, synthetic-only preflight, documented residual risks |

## Pilot-safe controls versus larger compliance claims

The following are reasonable claims for this milestone if implemented and tested: **synthetic data only; one explicitly scoped organization; one SQLite writer/host with a durable volume and practiced backup restore; managed OIDC without CareFlow-owned passwords; role plus case/assignment authorization; consent-gated local outbox with no delivery; redacted structured logs; health/readiness checks; and a reproducible Docker/offline test gate.**

Do **not** claim from those controls: HIPAA/GDPR or other regulatory compliance, a BAA, PHI-safe operation, clinical-production approval, tamper-proof/non-repudiable records, guaranteed disaster recovery, high availability, multi-tenant isolation, enterprise identity governance, or delivered patient communications. Those claims require selected legal jurisdiction and deployment, formal risk/privacy analysis, encryption and key management, retention/deletion and subject-rights processes, independent audit/log storage, vendor contracts, incident response, access reviews, penetration testing, and operational SLO/RPO/RTO evidence.

## Sources and confidence

### Repository evidence — HIGH confidence

- `careflow/api.py` — current OIDC/session gap, cookie flags, route authorization, HTML/JSON negotiation, demo login, message-provider boundary, and full case payload behavior.
- `careflow/persistence.py` — SQLite connection/schema/migration gap, consent upsert, audit snapshot rewrite, case rehydration, outbox lifecycle, and access-log behavior.
- `careflow/engine.py` and `careflow/pathways/screening_to_care.yaml` — pathway/state-machine invariants and role policy.
- `careflow/templates/base.html`, `careflow/templates/landing.html`, `careflow/templates/patient.html`, and `careflow/templates/case.html` — server-rendered exposure and demo-login surface.
- `Dockerfile`, `.dockerignore`, `.env.example`, `Makefile`, and `README.md` — image, configuration, seed, run, and reproducibility behavior.
- `tests/test_api.py`, `tests/test_auth.py`, `tests/test_html.py`, `tests/test_messages.py`, `tests/test_persistence.py`, `tests/test_engine.py`, and `.planning/codebase/TESTING.md` — current test boundaries and missing deployed/concurrent checks.
- `.planning/PROJECT.md` and `.planning/codebase/{CONCERNS,ARCHITECTURE,INTEGRATIONS,TESTING}.md` — milestone constraints and known concerns. These documents should be reconciled with source before roadmap tasks are finalized.

### Primary ecosystem guidance — HIGH confidence

- OpenID Connect Core 1.0 incorporating errata set 2 (2023-12-15), especially ID-token validation, `iss`/`sub`/`aud`/`exp`/`nonce`, exact redirect URI, TLS, and privacy considerations: <https://openid.net/specs/openid-connect-core-1_0.html>
- RFC 7636, Proof Key for Code Exchange by OAuth Public Clients: <https://datatracker.ietf.org/doc/html/rfc7636>
- OWASP Authentication Cheat Sheet, including OIDC validation, federated identity linking, TLS, re-authentication, and authentication-event monitoring: <https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html>
- OWASP Authorization Cheat Sheet, including least privilege, deny-by-default, per-request/object checks, ABAC/ReBAC considerations, logging, and authorization tests: <https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html>
- OWASP Cross-Site Request Forgery Prevention Cheat Sheet: <https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html>
- OWASP Logging Cheat Sheet, including security-event coverage, UTC/interaction IDs, sensitive-data exclusion, log integrity, retention, and verification: <https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html>
- SQLite Write-Ahead Logging documentation, including same-host limitation, one-writer behavior, checkpointing, sidecar files, and `SQLITE_BUSY`: <https://www.sqlite.org/wal.html>
- SQLite Online Backup API: <https://www.sqlite.org/backup.html>
- Docker Build best practices, including `.dockerignore`, pinned base digests, non-root `USER`, minimal images, volumes, and CI image testing: <https://docs.docker.com/build/building/best-practices/>

## Gaps requiring phase-specific research

- The pilot identity provider is not selected; provider-specific discovery, logout, group claims, JWKS rotation, outage behavior, and provisioning details must be researched in Phase 1.
- The deployment topology and volume/backup provider are not selected; precise SQLite capacity, encryption, RPO/RTO, and proxy behavior must be validated in Phases 3 and 6.
- The intended meaning of “owner” versus “same-role delegate” is a product decision, not an implementation detail; settle it before Phase 2 authorization tests.
- Consent purpose, retention, jurisdiction, and whether any real data could ever enter the pilot remain unresolved. Do not expand the data boundary until a privacy/legal review defines those conditions.
