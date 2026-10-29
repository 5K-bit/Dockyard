# 2. Standard library only, and the Docker CLI rather than an SDK

**Status:** accepted

**Context.** The target machine is offline, so every dependency has to be carried there. The Docker SDK for Python is another thing to carry and to trust.

**Decision.** No runtime dependencies. Dockyard calls the `docker` CLI through one wrapper with an injectable runner. The tool ships inside each bundle as `dockyard.pyz`.

**Consequences.** The target needs Python 3.11+ and Docker with Compose, nothing else. Behaviour follows the CLI's output formats, so the wrapper is covered by tests against a real daemon as well as a fake.
