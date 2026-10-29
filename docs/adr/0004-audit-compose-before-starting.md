# 4. Rate the Compose file before starting anything

**Status:** accepted

**Context.** A bundle's Compose file runs with the Docker daemon's authority. Installing from a stranger's USB stick should not silently grant `privileged: true`.

**Decision.** Run `docker compose config`, rate the normalised result, and stop on high-risk settings unless `--accept-risks` is given.

**Consequences.** Some legitimate apps (those that need the Docker socket, say) need an explicit flag. The audit is a guard rail with known gaps, documented in the threat model.
