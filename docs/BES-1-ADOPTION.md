# BES-1 adoption: Dockyard

**State:** Proposed, docs-only adoption; no production or operational certification.  
**Scope:** standalone Blackfong engineering tool.  
**Role:** offline Docker Compose application bundler and installer.  
**Standard:** [BES-1](https://github.com/5K-bit/O.B.E.O.S/blob/main/docs/standards/BES-1.md).

## Observed automation baseline

Existing workflow(s) found: `ci.yml, release.yml`; existence is not passing CI. All existing build, release and hosting behavior remains unchanged.

## Required lifecycle evidence

| Stage | Evidence required |
| --- | --- |
| Built | Problem, interfaces, exact commit, acceptance conditions, risks and rollback |
| Verified | Explicit test commands/CI run at that commit; missing tests and mock dependencies called out |
| Released | Versioned artifact/deployed revision plus real installation/deployment and rollback instructions |
| Operational | Observed production/real-device acceptance, service outage behavior and recovery, dated and linked |

## Project-specific release and operational gates

Build/install from clean Docker hosts, tampered archive/image/hash rejection, extraction traversal/permissions defense, risky Compose screen, offline restore and rollback validation.

**Do not cross this boundary:** A unit-test-only release cannot certify a real Docker host deployment.

## Next work

1. Link the actual automated build/test run and record skipped or external acceptance gates.
2. Implement missing safe validation (CI where absent) through an independent PR; do not redefine successful compilation as operational readiness.
3. Capture safe failure/recovery evidence and an explicit rollback path in the appropriate target environment.
4. Reuse optional BES-1 task aliases only when existing commands and supported OS environments are validated. Add security scanners and tracing only after a project-specific risk/maintenance review.

**AI assistance:** document generated or reviewed parts of substantive changes.  
**Human verification:** operator records real acceptance; unexecuted checks remain **not run**.
