# Architecture Patterns

**Domain:** Deployment-agnostic production hardening for a pathway-driven care-case orchestration service
**Project:** CareFlow
**Researched:** 2026-10-19
**Overall confidence:** HIGH for protocol and SQLite constraints; MEDIUM for the proposed decomposition because deployment scale and the selected identity provider are intentionally undecided.

## Executive Recommendation

Keep CareFlow as a modular monolith. Do not split the pathway engine, API, or SQLite repository into services for this milestone. Instead, make the HTTP layer an adapter around four explicit boundaries: an identity adapter, a server-side session repository, an authorization policy service, and operational/diagnostic adapters. Keep `careflow/engine.py` pure and synchronous, keep `careflow/persistence.py` as the repository boundary, and make OIDC, sessions, logging, migrations, and backups replaceable through small interfaces rather than provider SDK calls scattered through routes.

The most important boundary is **verified identity → local principal → authorization decision**. OIDC proves an identity to CareFlow; it must not directly become a CareFlow role, case owner, or patient record. Persist the stable application user ID that existing cases already reference, link it to the OIDC `(issuer, subject)` pair, and resolve current role assignments on each request. This preserves existing case foreign keys and makes deactivation or role changes effective without trusting stale claims in a cookie.

Use a browser-facing, opaque, server-side session. The cookie should contain only a high-entropy random session handle; the database stores a hash of the handle, the internal user ID, expiry and revocation state, and limited authentication context. This is preferable to extending the current signed cookie because it gives deterministic logout, revocation, role-change behavior, forced reauthentication, and a provider-neutral place to move sessions later. Keep the current signed demo login only behind an explicit development/test mode and do not treat it as the production identity path.

Centralize authorization before representation negotiation. JSON and server-rendered HTML must invoke the same `Principal` resolver and `AuthorizationService`; the `Accept` header can change a successful response or error template, but never the access decision. The pathway engine remains responsible for legal transitions and evidence/approval invariants. Authorization decides whether the principal may attempt an operation; the engine decides whether that operation is a valid state-machine mutation.

## Current-to-Target Shape

```text
Browser / JSON client
        |
        v
FastAPI routes in careflow/api.py (or extracted routers)
        |
        +--> Request context: request_id, Principal, CSRF decision
        |        |
        |        +--> careflow/identity.py: OIDC callback only; verified claims
        |        +--> careflow/sessions.py: opaque session lookup/revocation
        |        +--> careflow/authorization.py: authenticated + resource policy
        |        +--> careflow/observability.py: safe structured diagnostics
        |
        +--> application/case orchestration
                 |
                 +--> careflow/engine.py: Pathway / Case / worklist (pure domain)
                 +--> careflow/persistence.py: SQLite repository + one transaction
                              |
                              +--> cases, audit_events, evidence, approvals
                              +--> users, identity_links, role_assignments, sessions
                              +--> access_logs, security_events, notification_outbox

OIDC Provider <-- bounded authorization-code callback exchange only
Operations   <-- stdout diagnostics + explicit migration/backup commands
```

The proposed new modules are boundaries, not a reason to rewrite the application in one pass. `careflow/api.py` may continue to assemble the app and contain compatibility routes while authentication, authorization, and operational concerns are extracted behind interfaces. `careflow/app.py` should continue to re-export the deployment-facing `app` and `create_app` names.

## Component Boundaries

| Component | Responsibility | Must not own | Communicates with |
|---|---|---|---|
| `careflow/engine.py` | Pathway validation, `Case` transitions/assignment, evidence and approval rules, `explain`, and worklist ordering | HTTP, cookies, OIDC, SQL, logging policy, role-to-group mapping | Application service receives canonical actor ID/role and calls it |
| `careflow/pathways/screening_to_care.yaml` | Versioned workflow states, owners, SLAs, approvers, evidence, and terminal states | User authentication or deployment configuration | `Pathway` in `careflow/engine.py` |
| `careflow/persistence.py` | Synchronous repository, transactions, aggregate snapshots, operational records, identity/session/security-log storage | OIDC protocol, HTML negotiation, policy decisions, provider SDKs | Application services, auth/session adapters, migration runner |
| `careflow/migrations/` and `careflow/db.py` (new) | Ordered, forward-only schema migrations, schema version/checksum, startup compatibility checks | Business data decisions, backup scheduling | `SQLiteStore`, deployment/runbook, tests |
| `careflow/identity.py` (new) | Standards-compliant OIDC authorization-code adapter, discovery/JWKS, claims verification and normalization | Local role authorization, password storage, case access, long-lived token storage | `/auth/*` routes, `IdentityRepository`, `SessionService` |
| `careflow/sessions.py` (new) | Opaque session creation, lookup, rotation, expiry, revocation, cookie policy | Identity proof, pathway roles, HTML rendering | `identity.py`, request context, persistence |
| `careflow/authorization.py` (new) | Common authentication, role, permission, and case/patient resource decisions | State transition legality, data mutation, template rendering | Routes/application services, `Pathway`, `SQLiteStore` |
| `careflow/services.py` or `careflow/case_service.py` (new) | Request-scoped use cases: load, authorize, mutate, persist and record security/access audit atomically | Protocol implementation details and HTML formatting | Routes, engine, repository |
| `careflow/observability.py` (new) | Request IDs, safe structured logs, error classification, redaction, health diagnostics | Durable case audit semantics and secrets | FastAPI middleware, services, process stdout |
| `careflow/api.py` | FastAPI assembly, route schemas, response negotiation, dependency wiring, exception-to-JSON/HTML mapping | Duplicated authorization or independent state-machine rules | All adapters and services |
| `careflow/templates/` and `careflow/static/` | Presentation only | Access control and business decisions | Authorized route payloads |
| `careflow/ops.py` or `careflow/cli.py` (new) | Explicit migration, backup, restore verification, and diagnostic commands | Request-path business behavior | `careflow/db.py`, `SQLiteStore`, deployment operator |

### Identity and stable role boundary

Introduce a local principal model distinct from the existing `User` persistence shape:

```python
Principal(
    user_id="internal-careflow-id",
    roles=frozenset({"care_coordinator"}),
    auth_method="oidc",
    issuer="https://idp.example/tenant",
    session_id_fingerprint="...",  # never the raw cookie value
    auth_time=..., acr=..., amr=(...),
)
```

The exact Python type can live in `careflow/identity.py` or a small `careflow/auth_types.py`; it must be transport-neutral and safe to pass into a use case. The engine should receive only the canonical `actor` ID and the pathway role it needs. It should not import `Principal`, FastAPI, a JWT library, or `sqlite3`.

Additive persistence is recommended:

- Keep `users.id` as the internal application identifier referenced by `cases.patient_user_id`, `audit_events.actor`, `approvals`, `message_drafts`, and `access_logs`.
- Add `identity_links` with `(issuer, subject)` unique, `user_id` foreign key, `last_seen_at`, and optional provider metadata. OIDC `sub` is only stable within its issuer, so neither email nor `sub` alone is a safe global key.
- Add `role_assignments` (or an equivalent local authorization table) so provider groups/claims are mapped to CareFlow roles and permissions. Keep the existing primary pathway role as a compatibility projection until all callers use the set of roles.
- Keep `is_active` as a local kill switch. A disabled local user must not receive a session even if the OIDC provider authenticates them.
- Store verified display name/email as profile attributes for UI convenience, but never use a mutable email address as the identity key.

Map external groups or role claims through explicit, deployment configuration in `careflow/config.py` (new), with an allowlist of recognized external values. Unknown, conflicting, or absent role mappings fail closed at login. Do not accept a role, `actor`, patient ID, or approval role from a request body as authoritative; preserve compatibility fields only for clients while deriving the actor and role from `Principal`.

Keep pathway roles and platform permissions separate. A person may have a pathway role such as `care_coordinator` and a deployment-only permission such as `diagnostics.read`; a platform administrator should not accidentally become a valid pathway owner merely because they can inspect diagnostics. This avoids overloading `USER_ROLES` in `careflow/persistence.py` as the long-term authorization model.

## Authentication and Session Data Flow

### OIDC login

1. A browser requests `/auth/login` or an HTML page requiring authentication. The route creates a short-lived server-side login transaction in `oidc_login_transactions`: provider identifier, exact return path, random `state`, random `nonce`, S256 PKCE verifier, creation/expiry timestamps, and a one-use marker. Store the verifier only long enough to complete the callback; never log the row, query string, or token values.
2. CareFlow redirects to the configured issuer's authorization endpoint with `openid`, the minimum profile claims needed for the pilot, `response_type=code`, exact pre-registered redirect URI, `state`, `nonce`, and PKCE S256. The issuer and redirect URI come from validated configuration, not user input.
3. `/auth/callback` verifies that the transaction exists, is unexpired and unused, and that `state` matches. It exchanges the code server-to-server with a strict timeout, then validates the ID token using issuer discovery/JWKS and the OIDC-required `iss`, `sub`, `aud`, `exp`, `iat`, and `nonce` claims. Refresh/access tokens are not needed for ordinary CareFlow requests and must not be persisted.
4. `careflow/identity.py` returns normalized claims. The identity repository resolves `(issuer, subject)` to an internal user. A deployment may pre-provision the link; automatic email matching should not silently link accounts. If no approved link or role assignment exists, login is denied without creating a session.
5. `SessionService` creates a fresh opaque session, writes only the session hash and bounded authentication metadata, rotates away any pre-auth session, sets the secure cookie, marks the login transaction used, and redirects only to a stored relative path. JSON callers receive a stable success shape; HTML callers receive a redirect.

Use one configured provider for the pilot. If multiple providers are added later, bind the provider alias and issuer to the transaction and verify the response issuer to prevent mix-up attacks. Do not accept a dynamic issuer, client ID, or redirect URI from a request.

### Ordinary request

1. FastAPI middleware in `careflow/api.py` or a dedicated request-context module generates/validates a bounded `X-Request-ID`, extracts the cookie, and asks `SessionService` for the session. The raw cookie is never placed in logs or response bodies.
2. The session resolves to a local user and current role/permission assignments. A revoked, expired, inactive, or malformed session yields an unauthenticated `Principal`; role changes take effect on the next request because roles are not trusted from a stale cookie.
3. Route dependencies call `AuthorizationService` with the principal and resource context. Resource checks occur before serialization and before choosing HTML versus JSON. A patient can see only linked cases/status; operational views, metrics, message drafting/approval, audit views, and access-log views use explicit permissions.
4. A read or mutation calls the application service. The service loads a case through `SQLiteStore`, passes the canonical `principal.user_id` and pathway role to `Case.transition`/`Case.assign`, and never sets `case.state` in the route.
5. A mutation commits the case snapshot, engine audit rows, evidence/approval rows, and the required security/access event in one SQLite transaction. If the transaction fails, the in-memory aggregate is discarded and the client receives a request-ID-bearing 503/409 as appropriate.
6. The service returns a representation-neutral payload. `api.py` serializes the same payload as JSON or passes it to a Jinja template. Templates cannot bypass the authorizer or query the store directly.

### Logout and expiry

`POST /auth/logout` always revokes the local session server-side and clears the cookie, even if the session is already expired. The response is JSON for API clients and a same-origin redirect for HTML. Provider RP-initiated logout can be an optional best-effort redirect after local revocation; local logout must not depend on provider availability. Do not claim that a local logout ends every upstream SSO session.

Use both idle and absolute session expiry. Rotate the session identifier after OIDC login, privilege changes, and any reauthentication event. Use a `__Host-`-style cookie name when the deployment is HTTPS-only, with `Secure`, `HttpOnly`, `Path=/`, no `Domain`, and explicit `SameSite=Lax` (top-level OIDC callback navigation needs a deliberate setting). Require explicit CSRF protection for every state-changing cookie-authenticated JSON or HTML request; `SameSite` is defense in depth, not a CSRF substitute. Never put session IDs, ID tokens, access tokens, or refresh tokens in URLs, local storage, or logs.

## Authorization for JSON and HTML

The route surface should use shared dependencies or service calls such as:

```python
principal = require_principal(request)
authorize_case(principal, case_id, action="read")
result = case_service.read_case(principal, case_id)
return render_or_json(request, result, template="case.html")
```

This is a boundary sketch, not a requirement to use these exact names. The important properties are:

- `require_principal` and `AuthorizationService` are the only source of authenticated identity and access decisions.
- The request body's `actor`, `role`, `user_id`, and patient selectors cannot elevate access.
- The same resource policy is used for `/cases/{case_id}`, `/patient/{patient_id}`, worklists, metrics, draft endpoints, audit/access-log views, and any future HTML form routes.
- Authorization is applied before `_case_payload`, `_case_view_context`, `_patient_case_view`, or other serializers can expose data.
- HTML and JSON use the same status semantics. Unauthenticated HTML may redirect to login with a safe local return path; unauthenticated JSON receives 401. Forbidden and not-found behavior is deliberately consistent, with patient resources preferably returning indistinguishable 404 responses where existence disclosure matters.
- HTML unsafe methods include a CSRF token bound to the local session and validate `Origin`/`Referer` where deployment policy permits. JSON clients using browser cookies use the same CSRF contract; future non-browser API clients can receive a separate bearer-token adapter without changing the authorization policy.
- `Accept: text/html` is representation negotiation only. It must never turn a 403 into a 200 template or skip a check.

The engine continues to enforce role-sensitive transition rules through `actor_role`, but the value is supplied by the verified local principal. `AuthorizationService` handles broad permission and resource scope; `Case.transition` handles the state graph, required evidence, approval waiver, closure, and owner invariants. Keeping both checks is intentional defense in depth, not duplication of the same rule.

## Persistence, Migrations, and Backups

### Repository and transaction boundary

`SQLiteStore` in `careflow/persistence.py` can remain synchronous and continue to own a connection for the pilot. Add a transaction/unit-of-work API rather than allowing routes to interleave `save_case()` and `record_access()` independently. Case mutations should atomically persist the current snapshot and append-only business audit plus the security/access event. Reads may use the existing rehydration path, but a failed required audit write must be visible as a controlled degraded response rather than silently dropped.

Do not make `careflow/engine.py` depend on migration or transaction objects. The service layer can call the repository transaction boundary and then invoke the engine on a rehydrated aggregate. This preserves existing `tests/test_engine.py` behavior and lets the engine remain useful to `careflow/seed.py`, `careflow/demo.py`, and offline simulations.

### Migration model

Replace the current `SCHEMA`-only `CREATE TABLE IF NOT EXISTS` startup behavior with a small forward-only migration runner in `careflow/migrations/` and `careflow/db.py`:

1. A baseline migration describes the schema that `careflow/persistence.py` currently creates. A fresh database applies it; an existing database is recognized by its legacy tables, checked with foreign-key and integrity checks, and recorded as baseline version 1 without deleting data.
2. Later numbered migrations add `identity_links`, `role_assignments`, `sessions`, `oidc_login_transactions`, `security_events`, CSRF/session metadata, and request correlation fields as needed. Each migration runs in a transaction, records version, timestamp, and checksum in `schema_migrations`, and is idempotent only through the migration runner—not by silently changing old migration files.
3. Run migrations as an explicit `python -m careflow.ops migrate --db ...`/`make migrate` operation or a single controlled startup phase. Use `BEGIN IMMEDIATE` or an equivalent lock so two app processes cannot apply the same migration concurrently. The app must fail readiness, and preferably fail startup, when the schema is behind or a migration cannot commit; do not serve mixed application/schema versions.
4. For changes SQLite cannot express safely with a simple `ADD COLUMN`, use the documented create-copy-rename transaction, recreate indexes/foreign keys, run `PRAGMA foreign_key_check`, then commit. Never use `PRAGMA writable_schema` as an application migration shortcut.
5. Before every production migration, create and verify a backup. After migration, check `PRAGMA integrity_check`, schema version, expected table/index presence, seeded/synthetic fixture compatibility, and pathway loading. Keep migration rollback forward-only; for destructive mistakes restore a known-good database snapshot rather than trying to reverse arbitrary DDL.

The first compatibility deployment should add the migration runner and new tables without changing case identifiers. Existing signed cookies are not database records and should not be converted into trusted production sessions; deploy a version that clears/rejects them and requires a fresh OIDC login. Development/test mode may retain the demo adapter explicitly. Existing `cases.patient_user_id` and audit actors remain valid because the internal `users.id` namespace is preserved.

### SQLite operating mode

Synchronous access is sufficient for the current pilot. Keep FastAPI route handlers synchronous, use explicit short transactions, parameterized queries, foreign keys, and a bounded `busy_timeout`. WAL can improve same-host read/write concurrency, but it is not safe as a portable default on network filesystems; make it a validated configuration choice and document that all database processes must be on the same host. Do not imply that a local SQLite file is a multi-host database.

The repository must expose a readiness diagnostic for readable/writable storage, current schema version, and basic integrity. It should not include database contents in the response.

### Backup and restore

Implement a deployment-neutral, explicit backup command in `careflow/ops.py` using Python's SQLite backup API (or a transactionally equivalent `VACUUM INTO` strategy), writing to a temporary destination and atomically renaming only after integrity verification. Do not copy only the main file while a WAL database is active; SQLite documents that the `-wal` file is part of persistent state and that the online backup API produces a consistent snapshot. A backup command should:

- acquire a consistent snapshot without blocking ordinary requests longer than necessary;
- never mutate or truncate the live database on backup failure;
- reopen the destination and run `PRAGMA integrity_check` plus the migration/schema checks;
- emit a safe operation result with backup ID, schema version, size, checksum, and timestamps, not patient/message contents;
- leave encryption, retention, off-host storage, and access control to the deployment operator without assuming a cloud vendor.

Restore should be an explicit operator command/runbook step: stop or drain the app, restore to a new path, verify integrity and schema, atomically select the restored path, restart, and verify health and synthetic smoke flows. Treat backups as sensitive application data even though this milestone is synthetic-only.

## Observability and Privacy-Conscious Diagnostics

Add `careflow/observability.py` using Python's standard logging interfaces and JSON records to stdout. A deployment can collect stdout with any host, container runtime, or log system. Do not bind the application to a hosted error tracker or metrics vendor.

Every diagnostic record should be bounded and use an allowlist of fields such as UTC timestamp, application version, environment, component, event code, severity, request ID, route template, HTTP method/status, duration, outcome, schema version, and a pseudonymous internal principal identifier when needed. Generate a request ID if the inbound value is missing or malformed; echo only the safe value in an `X-Request-ID` response header. Log the exception class and a sanitized reason code, not raw SQL, provider responses, claim sets, request bodies, cookies, or template payloads.

Separate three records with different retention and access policies:

1. **Business audit** in `audit_events`: immutable case openings, assignments, transitions, evidence and approval provenance. The engine remains the source of business truth; it receives the canonical internal actor ID.
2. **Resource access audit** in `access_logs`: successful case/patient/draft reads and mutations, with only necessary endpoint/action metadata. Keep it restricted and retention-controlled; do not expand it into an unbounded generic application log.
3. **Security/operational events** in a new `security_events` table or an equivalently durable security sink: authentication success/failure, OIDC validation failure, session create/revoke/expiry, CSRF rejection, authorization denial, configuration failure, migration/backup result, and startup/shutdown. Use nullable principal/target references, fixed event codes, outcome, request ID, and redacted metadata.

Log failures to authenticate and authorize, but use reason codes such as `invalid_session`, `unknown_identity`, `role_not_mapped`, `case_scope_denied`, or `csrf_failed`; never log the raw token, cookie, authorization code, full callback URL, email, patient name, message body, or health data. If correlation of a case or user is operationally necessary, use an HMAC-based pseudonym with a deployment secret and document its retention; do not rely on reversible or plaintext identifiers in process logs.

The structured logger must be best effort and must not take the service down when stdout or a remote collector is unavailable. Durable business/security audit has a stricter contract: a case mutation must not report success if its required audit transaction did not commit. Public health endpoints expose only status and build/schema information needed by an orchestrator. An authenticated, explicitly permissioned diagnostics endpoint may expose dependency status, migration version, backup recency, and bounded counters, but never database rows, OIDC secrets, environment dumps, or raw exception traces.

Recommended health separation:

- `GET /health/live`: process/event-loop (or thread) liveness only; no database or IdP dependency.
- `GET /health/ready`: validated configuration, pathway loaded, current schema, database read/write probe, and required local storage. Do not make IdP network availability a prerequisite for serving existing sessions.
- `GET /health/startup` or startup logs: one-time migration/config result and dependency versions, safe for operators rather than patients.

OIDC and optional AI/message-provider failures return stable, request-ID-bearing 5xx/4xx responses and are logged with safe event codes. They must not expose provider response bodies or partially mutate a case. Existing authenticated sessions should continue when the IdP is temporarily unavailable; only new login or an explicitly required reauthentication should depend on it.

## Failure Isolation

| Failure | Expected behavior | Isolation boundary |
|---|---|---|
| OIDC issuer/JWKS/token endpoint unavailable during login | Bounded timeout, safe login error, no session; existing local sessions remain usable until local expiry | `careflow/identity.py` callback adapter |
| OIDC returns invalid issuer/audience/nonce/signature/role mapping | Deny login, consume/delete transaction, emit redacted security event | Identity normalization; no route or engine call |
| Session store is locked/unreadable | Return 503 with request ID and fail closed; do not treat the cookie as proof | `careflow/sessions.py` / repository |
| User locally inactive or role revoked | No principal/permission on the next request; optionally revoke all sessions | Identity repository + session lookup |
| Authorization denied | Stable 401/403 (or deliberately indistinguishable 404 for scoped resources), security event; no engine mutation | `careflow/authorization.py` |
| Pathway YAML invalid or missing | Startup/readiness failure with safe diagnostic; no partially initialized app | `Pathway.load` + app bootstrap |
| SQLite constraint/lock/write failure | Transaction rollback; no success response; controlled 409/503 and diagnostic event | `SQLiteStore` unit of work |
| Structured log sink/stdout failure | Continue serving; retain durable audit obligations where required and alert via health/metrics if possible | `careflow/observability.py` |
| Migration fails | Do not start/advertise readiness; leave prior committed schema intact | `careflow/db.py` |
| Backup or restore verification fails | Keep live DB unchanged; report operation failure; do not call it a successful backup | `careflow/ops.py` |
| Template rendering failure after an authorized read | Return safe error with request ID; no alternative data path | `api.py` representation adapter |

## Anti-Patterns to Avoid

### Provider leakage into the domain

Do not import an OIDC SDK, decode JWTs, or inspect external groups in `careflow/engine.py`, `careflow/persistence.py` domain methods, pathway YAML, or templates. Provider-specific claim names belong in the identity adapter/configuration. This prevents a future provider change from changing pathway semantics.

### Roles in signed cookies or request bodies

Do not extend the current signed `careflow_session` payload to carry roles, patient IDs, or permissions. A valid signature only proves that CareFlow signed an old payload; it does not provide revocation or current authorization. Do not trust `TransitionInput.actor`, `AssignInput.actor`, approval `role`, or a client-supplied patient ID for identity. Derive these values from `Principal` and the authorized resource.

### Duplicate JSON and HTML authorization

Do not put one set of checks in JSON routes and another in template routes. Keep one route/service policy and choose the representation only after it succeeds. A template is not an authorization boundary.

### OIDC on every request

Do not call the issuer's UserInfo/JWKS/introspection endpoint for every case read. Cache discovery/JWKS according to protocol-library guidance, validate once at login, store bounded local session state, and use local revocation/expiry for ordinary requests. Reauthentication can be an explicit policy for high-risk actions.

### Schema-by-side-effect and unsafe file copies

Do not grow `SCHEMA` with more `CREATE IF NOT EXISTS` statements and call that migration. Do not copy a live SQLite main file while ignoring `-wal`/`-shm`. Use numbered migrations and the SQLite backup API.

### Sensitive process logs

Do not log callback query strings, authorization codes, cookies, JWTs, group claims, emails, patient names, draft text, evidence details, or raw database errors. Do not make a hosted observability provider a runtime requirement for the portable pilot.

## What Can Remain Synchronous

The following should remain synchronous for the pilot:

- `Pathway`, `Case`, `explain`, and `worklist` in `careflow/engine.py`;
- `SQLiteStore` reads/writes and the case unit of work in `careflow/persistence.py`;
- ordinary FastAPI `def` route handlers, because the repository is synchronous and the current `TestClient`/offline tests exercise this shape;
- local session lookup, role mapping, authorization, serialization, health probes, and durable audit writes;
- explicit migration, backup, restore verification, seed, and demo commands outside the request process.

The OIDC code exchange and any configured message provider call are network-bound but can remain synchronous behind strict connect/read timeouts because they occur only in explicit login/draft operations. Keep those calls isolated so a slow provider cannot hold a database transaction or mutate a case. Move to async only when measured concurrency needs justify changing the repository and test contract; do not introduce an async ORM or broker as a prerequisite for this milestone.

## Scalability and Portability Envelope

| Concern | Pilot / about 100 users | About 10K users on one deployment | 1M users or multi-host deployment |
|---|---|---|---|
| Case state | Existing synchronous `Case` aggregate and SQLite snapshot/audit transaction | Keep engine unchanged; add indexes, bounded pagination, and shorter read transactions | Preserve engine/application interfaces; move repository to a server database after a separate migration project |
| Sessions | SQLite-backed opaque sessions, one process or same-host workers | Same-host SQLite may work with careful locking; external session repository interface is ready | Use a managed/shared session store; no network filesystem SQLite |
| OIDC | One configured issuer, cached discovery/JWKS, fake provider tests | Same adapter, explicit provider/role mapping and rate/timeout controls | Add provider registry/tenant model only as a separate scope; keep issuer+subject identity key |
| Observability | JSON stdout, bounded security events and health endpoints | Central collection selected by deployment, retention and alerting documented | Vendor/collector integration behind logging interfaces; no business data in default telemetry |
| Backup | Online SQLite snapshot and verified restore command | Scheduled off-host encrypted snapshots, restore drills | Managed database backup/replication and a new RPO/RTO design |

SQLite remains an appropriate portable pilot store only while the database lives on a local filesystem and write concurrency is modest. The repository and migration boundaries should make a future PostgreSQL or other managed database adapter possible without moving pathway rules into SQL or identity rules into the engine.

## Migration and Build Order

The ordering below is dependency-driven. Do not enable production OIDC before the principal/session/policy boundary exists, and do not change the schema without the migration/backup contract.

### 1. Characterize the existing contract and configuration

Add regression coverage for engine invariants, current API response shapes, signed-demo behavior, case IDs, audit rows, outbox consent gating, and HTML/JSON parity. Create a validated `careflow/config.py` settings boundary. In production mode, require explicit OIDC settings, secure cookie policy, public base URL, writable database, and a non-default secret; retain defaults only for explicit local/demo mode. Use UTC-aware timestamps at new boundaries while preserving existing rehydration compatibility.

### 2. Add migrations and a verified database lifecycle

Introduce the baseline migration, `schema_migrations`, backup-before-migrate checks, integrity checks, and an offline migration/backup command. Run this before adding identity/session tables. Test fresh databases, the current legacy schema, interrupted migrations, read-only paths, WAL snapshots, and restore verification. This step protects the data model while all later components evolve.

### 3. Extract the stable principal and authorization contract

Add `Principal`, `IdentityRepository` protocols, `AuthorizationService`, shared FastAPI dependencies, and the case/application service. Adapt the current signed-cookie login to produce the same principal in development/tests. Route every current JSON and HTML endpoint through the shared policy, including message drafts, worklists, metrics, patient pages, and any audit/access-log views. Add an authorization matrix test rather than relying on route-by-route inspection.

### 4. Add server-side sessions and browser protections

Create the `sessions` and short-lived `oidc_login_transactions` schema migrations. Implement opaque cookie sessions, revocation, idle/absolute expiry, rotation, logout, secure cookie configuration, CSRF tokens, safe return paths, and request-ID-aware errors. Keep a clearly marked demo adapter for offline fixtures; do not let it become the production default. Test fixation, replay, expiry, logout, role revocation, missing/invalid cookies, HTML forms, and JSON cookie requests.

### 5. Integrate the provider-neutral OIDC adapter

Implement the authorization-code + PKCE + nonce flow in `careflow/identity.py` behind a fake-provider test seam. Add discovery/JWKS validation, issuer/audience/redirect checks, claim normalization, explicit group/role mapping, identity links, inactive-user handling, and login failure events. Pre-provision or explicitly approve identity links; do not mass-link by email. Test provider timeouts and malformed responses offline. Switch production configuration to OIDC only after the local session and authorization tests pass.

### 6. Make mutations and security audit atomic; add diagnostics

Move case mutation orchestration from route closures into the application service and commit case snapshot, business audit, access/security event in one repository unit of work. Add safe JSON logs, health/liveness/readiness endpoints, stable error envelopes, startup configuration diagnostics, and permissioned operational diagnostics. Verify that logging failures do not crash ordinary requests while required mutation audit failures do not produce false success.

### 7. Operationalize backup, restore, deployment, and rollback

Document the migration-before-start sequence, writable storage, secret injection, OIDC callback registration, health probes, backup cadence/retention, restore drill, and rollback policy in `README.md` and an operations document. Keep Docker and `make test` offline/reproducible. Run a clean Linux/container smoke test with a fake or disabled external provider; a real provider integration test belongs in a separately controlled environment, never in the offline test command.

### Required sequencing constraints

- `careflow/engine.py` changes are not a prerequisite for identity. Preserve its public transition/worklist behavior and pass canonical actor data from above it.
- The migration runner and a verified backup must precede any identity/session schema change.
- `Principal` and shared authorization must precede enabling OIDC, otherwise claims and roles will leak into routes inconsistently.
- Server-side session rotation/revocation and CSRF protections must precede production HTML/JSON cookie use.
- Structured diagnostics should land after request context/auth exists so events can correlate safely, but before production cutover so failures are observable.
- Backup/restore verification and startup/readiness checks must precede declaring the deployment production-ready.

## Verification Strategy

Add focused tests at each boundary while keeping the existing suite intact:

- `tests/test_engine.py`: no behavior change to pathway transitions, evidence, approval, ownership, and worklist ordering.
- `tests/test_persistence.py`: migration baseline/upgrade, identity links, role assignments, session revocation, transaction rollback, security events, backup verification, and legacy data rehydration.
- `tests/test_api.py`: JSON and HTML authorization parity, response contracts, CSRF, safe redirects, request IDs, health behavior, and message-draft permissions.
- `tests/test_identity.py` (new): fake OIDC discovery/token/JWKS responses, PKCE/nonce/state, issuer/audience checks, claim mapping, and provider failure isolation with no network calls in the default test command.
- `tests/test_authorization.py` (new): a table-driven matrix for patient, pathway staff, approver, inactive, unmapped, and platform-diagnostics principals across every route family.
- `tests/test_sessions.py` (new): high-entropy opaque cookies, no user/role data in cookie payload, rotation, expiry, logout revocation, replay rejection, and no token logging.
- `tests/test_ops.py` or equivalent (new): migration locking/failure, integrity checks, online backup, restore smoke test, safe diagnostics, and logger failure isolation.

The clean-container check must run `make test`, then start the app against a writable mounted database directory, exercise liveness/readiness, and confirm that the seed/demo path remains explicitly synthetic. No test or build should require a real OIDC provider, network access, or committed secret.

## Sources and Confidence

- **HIGH:** OpenID Connect Core 1.0 incorporating errata set 2 (2023), especially the ID token `iss`/`sub`/`aud`/`exp`/`iat`/`nonce` requirements and authorization-code flow: <https://openid.net/specs/openid-connect-core-1_0.html>
- **HIGH:** RFC 9700, *Best Current Practice for OAuth 2.0* (January 2025), especially authorization-code + PKCE, exact redirect URI validation, CSRF/mix-up protections, and avoiding implicit/password grants: <https://www.rfc-editor.org/rfc/rfc9700.html>
- **HIGH:** FastAPI official advanced middleware documentation, confirming ASGI middleware and trusted-host/HTTPS controls are portable framework boundaries: <https://fastapi.tiangolo.com/advanced/middleware/>
- **HIGH:** SQLite Online Backup API (updated 2025-11-13), recommending consistent online snapshots instead of unsafe live-file copying: <https://sqlite.org/backup.html>
- **HIGH:** SQLite Write-Ahead Logging documentation (updated 2026-08-25), including same-host/network-filesystem limitations, checkpoint behavior, and the requirement to keep `-wal` state with the database: <https://sqlite.org/wal.html>
- **HIGH:** SQLite `ALTER TABLE` documentation (updated 2026-10-05), covering the limited ALTER support and safe create-copy-rename procedure for complex changes: <https://sqlite.org/lang_altertable.html>
- **HIGH:** OWASP Logging Cheat Sheet, accessed 2026-10-19, for event separation, security-event coverage, correlation IDs, redaction, log failure handling, retention, and access control: <https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html>
- **HIGH:** OWASP Session Management Cheat Sheet, accessed 2026-10-19, for opaque high-entropy server-side session IDs, rotation/revocation, secure cookie attributes, CSRF defense in depth, and avoiding tokens in browser storage/logs: <https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html>

The specific module names, role mappings, SQLite pragmas, retention periods, and OIDC claim names still need phase-level decisions against the selected deployment and identity provider. The boundaries above are intentionally provider- and host-neutral; those details must not be allowed to move business rules into `careflow/engine.py` or make `careflow/api.py` the only place where authorization can be tested.
