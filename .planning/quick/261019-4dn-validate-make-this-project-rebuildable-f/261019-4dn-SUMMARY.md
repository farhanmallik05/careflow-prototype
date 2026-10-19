# Quick Task 261019-4dn: Validate and repair clean rebuildability

## Result

CareFlow now has an explicit rebuild contract for a clean Linux checkout and a
digest-pinned Docker runtime. Application source and tests were not changed.

## Changes

- Pinned `.python-version` to CPython `3.12.12`.
- Kept the existing version-level `requirements.lock` and verified every direct
  dependency is represented exactly once; artifact hashes are not claimed.
- Reworked `Dockerfile` to use the reviewed Python 3.12.12 digest, copy only
  runtime inputs, run as non-root `careflow`, declare `/data`, seed only an
  empty `/data/careflow.db` at first start, and expose a seeded HTTP healthcheck.
- Expanded `.dockerignore` and `.gitignore` for planning/development files,
  databases, SQLite sidecars, local data volumes, archives, caches, and env files.
- Added `.PHONY` declarations and retained `make test` as the single canonical
  test command.
- Updated `README.md` with exact runtime verification, local setup, seed/run,
  Docker named-volume, image replacement, save/load, and secret handling steps.
- Refreshed the architecture/stack/concerns planning artifacts and `AGENTS.md`
  so they describe the current container contract rather than the old build-time
  seed/root/floating-tag behavior.

## Verification

Passed:

- `python -m pytest -q` — 39 passed.
- `python -m compileall -q careflow tests`.
- `git diff --check`.
- Runtime/lock/direct-dependency contract script.
- README/Makefile/Dockerfile/env contract script.
- Tracked-text scan for machine-specific absolute paths and high-confidence key
  patterns — no matches.
- `git rev-parse --show-toplevel`, chronological `git log`, and
  `git fsck --full --no-progress` — repository valid; only two dangling blobs
  were reported.

Not run successfully in this host session:

- `make test` — GNU Make is not installed on the Windows host; it maps exactly
  to the passing `python -m pytest -q` command.
- `docker build --pull -t careflow:rebuild-check .` — Docker CLI is installed,
  but the Docker Desktop Linux daemon is unavailable.
- CPython 3.12.12 execution — the host interpreter is Python 3.13.14; the exact
  3.12.12 runtime is pinned for clean Linux/ Docker use and documented as a
  prerequisite for local execution.

## Commit scope

Only reproducibility/configuration/documentation/planning metadata changed; no
application source, pathway, templates, or tests were modified.
