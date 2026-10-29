# 1. The bundle is one gzip tar with a manifest and checksums

**Status:** accepted

**Context.** The bundle travels on a USB stick or a file share, often by a person who is not the one who built it. It must be one file, inspectable without Dockyard, and verifiable.

**Decision.** A gzip tar with a single directory holding `manifest.json`, `SHA256SUMS`, the Compose file, `docker save` tars and extras. A sidecar `.sha256` records the archive digest.

**Consequences.** Anyone can `tar -tf` it and `sha256sum -c` it. Checksums inside the bundle only catch accidents; resisting tampering requires the digest to travel by a different route, which is why `--digest` exists and `pack` says so.
