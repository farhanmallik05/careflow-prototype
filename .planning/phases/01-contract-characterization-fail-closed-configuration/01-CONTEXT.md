# Phase 1: Contract Characterization & Fail-Closed Configuration - Context

**Gathered:** 2026-10-19
**Status:** Complete
**Mode:** Autonomous implementation

## Phase Boundary

Make runtime mode and configuration explicit, preserve the local synthetic demo,
and fail closed before pilot mode can accidentally use demo identity. Expose
only safe liveness/readiness diagnostics and reject demo seed/reset outside demo
mode.

## Implementation Decisions

- Keep `demo` as the default local/judge mode for backwards compatibility.
- Add typed standard-library settings rather than a new dependency.
- Treat pilot settings as required even before the OIDC callback is implemented;
  an incomplete pilot deployment must refuse startup/app construction.
- Keep the synthetic-only flag explicit in both modes and never expose secrets in
  diagnostics.
