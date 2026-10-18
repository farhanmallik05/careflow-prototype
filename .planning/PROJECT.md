# CareFlow

## What This Is

CareFlow is a pathway-driven case-orchestration service for multi-party care journeys. Its current LoopCare prototype guides a screening result through assignment, evidence collection, escalation or rejection, patient communication drafting, and documented closure while keeping ownership, consent, approvals, and audit history explicit. The next milestone hardens this existing prototype for a deployment-agnostic pilot used by patients and care teams.

## Core Value

Patients and care teams can move a screening case safely from result to documented next action or closure, with every responsibility, permission, decision, and communication traceable.

## Requirements

### Validated

- ✓ A declarative screening-to-care pathway can enforce legal state transitions, state owners, SLAs, evidence requirements, approvals, terminal states, and attention flags — existing prototype
- ✓ Open cases retain exactly one owner and overdue, rejected, escalated, or unassigned work surfaces in an urgent-first worklist — existing prototype
- ✓ Evidence, approvals, consent, access, assignments, and transitions are persisted with an audit trail — existing prototype
- ✓ Patients and staff have distinct case access behavior, and patient message drafts can be approved into an auditable local notification queue — existing prototype
- ✓ The application runs as a reproducible Python/FastAPI/SQLite service with a pinned runtime, lockfile, Dockerfile, deterministic seed data, and offline tests — existing prototype

### Active

- [ ] A deployment can integrate managed OIDC/SSO without CareFlow owning passwords, while identity, role, session, and logout behavior remain testable and secure.
- [ ] Production configuration fails safely when required secrets or security settings are missing, and environment-specific settings are documented without committing secrets.
- [ ] Patient and care-team authorization is enforced consistently across HTML pages, JSON APIs, message-draft operations, worklists, metrics, and audit/access-log views.
- [ ] Synthetic-only data boundaries are explicit and enforced so the pilot cannot be mistaken for a protected-health-information production system.
- [ ] Database initialization, migrations or schema versioning, backup/restore, and writable-storage behavior are reproducible across clean Linux and container deployments.
- [ ] Health checks, structured application logs, actionable error responses, and operational diagnostics make a running deployment supportable without exposing patient or secret data.
- [ ] Security-sensitive actions and failures have auditable, privacy-conscious records, with retention and access expectations documented.
- [ ] Approved patient message drafts remain consent-gated, reviewable, and auditable in the local outbox; delivery is deliberately not part of this milestone.
- [ ] The service has a documented deployment-agnostic runbook covering configuration, startup, upgrades, recovery, verification, and rollback.
- [ ] The complete production-readiness baseline is covered by the project's offline test command and remains reproducible through Docker.

### Out of Scope

- Real patient or protected health information — this milestone is synthetic-data-only and must not imply clinical-production compliance.
- Building a proprietary password system — managed OIDC/SSO is the intended identity boundary.
- Multi-tenant SaaS architecture — tenant isolation and shared-infrastructure concerns are deferred until a deployment model is selected.
- Actual email, SMS, or in-app message delivery — approved drafts remain in an auditable local queue for this milestone.
- Clinical decision automation or medical recommendations — CareFlow orchestrates documented work; it does not diagnose or prescribe.
- A new frontend framework or mobile application — preserve the existing server-rendered dashboard and API unless research proves a focused change is necessary.
- A provider-specific hosting or cloud deployment — the hardening work must remain deployment-agnostic.

## Context

The codebase is a brownfield Python 3.12 modular monolith. `careflow/engine.py` owns the pathway state machine and business invariants; `careflow/pathways/screening_to_care.yaml` defines workflow policy; `careflow/persistence.py` provides transactional SQLite persistence; and `careflow/api.py` exposes FastAPI JSON and server-rendered dashboard behavior. The project already includes deterministic seed/demo commands, message-draft generation, access logging, consent checks, a Dockerfile, `.env.example`, a pinned dependency lockfile, and tests in `tests/`.

The current prototype intentionally uses synthetic users and signed sessions, with a development fallback session secret and no external identity provider. The production-readiness milestone should replace or isolate that boundary rather than obscure it. Research should focus on portable OIDC integration, secure session and authorization patterns, SQLite operational safety for a pilot, privacy-conscious observability, health/readiness checks, and reproducible container operations.

The primary audience is patients and the care teams coordinating their journeys. The first deployment target is intentionally undecided: the implementation should provide stable interfaces and documented assumptions without coupling the project to a specific cloud, identity vendor, database service, or observability platform.

## Constraints

- **Data safety**: Synthetic data only — no real patient or protected health information may be required, generated, or committed for this milestone.
- **Identity**: Managed OIDC/SSO boundary — CareFlow should consume verified identity claims and map them to application roles rather than own passwords.
- **Messaging**: Auditable queue only — patient communication remains consent-gated and reviewable; delivery is deferred.
- **Portability**: Deployment-agnostic — keep the existing Python/FastAPI/SQLite/Docker stack portable across clean Linux environments.
- **Reproducibility**: Pinned runtime, lockfile, Dockerfile, offline `make test`, and deterministic fixtures must remain working after every change.
- **Compatibility**: Preserve the current pathway engine and existing user-visible workflow unless a security or reliability fix requires a deliberate behavior change.
- **Security**: Secrets must come from environment or deployment secret managers; no credentials, private keys, or absolute machine paths may enter repository artifacts.
- **Scope**: Production-readiness means a safe, observable pilot baseline, not a claim of regulatory certification or clinical production approval.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Prioritize production readiness over adding new clinical pathways | The current prototype already demonstrates the core case journey; the immediate risk is safe operation and deployment | — Pending |
| Serve patients and care teams as the first audience | Both sides need a consistent, traceable journey from screening result to next action | — Pending |
| Keep deployment provider-agnostic | No hosting target has been selected; portable interfaces preserve future options | — Pending |
| Use managed OIDC/SSO rather than a CareFlow-owned password system | Reduces credential liability and fits organizational identity workflows | — Pending |
| Enforce synthetic-only data for v1 | Enables safe demonstration and pilot hardening without implying PHI compliance | — Pending |
| Keep messaging as an auditable queue | Consent, review, and provenance are valuable now; delivery adds provider and operational scope | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-10-19 after project initialization*
