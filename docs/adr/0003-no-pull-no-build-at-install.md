# 3. Install never pulls or builds

**Status:** accepted

**Context.** An offline install that quietly tries the network will hang or, worse, succeed by fetching something the bundle did not contain.

**Decision.** Images come only from `docker load`. Compose is started with `--no-build --pull never`, so a missing image fails at once.

**Consequences.** A Compose service must reference images that `pack` captured. `pack` reads the resolved Compose file and requires every service to name an `image:`; build first, then pack.
