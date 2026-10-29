# Threat model

Dockyard moves software across an air gap, so its main question is: **what stops a bad or damaged bundle from doing harm on the target?**

## Assets

The target machine, its Docker daemon (effectively root), and the data the app will hold.

## Trust boundaries

- The bundle is **untrusted input** until verified, and still carries code that will run as containers.
- The Compose file inside it is code. Dockyard rates it, but you decide whether to run it.
- The `docker` CLI and daemon on the target are trusted.

## Threats and mitigations

| Threat | Mitigation | Residual risk |
|---|---|---|
| Bundle damaged in transit (bad copy, bad disk) | `SHA256SUMS` over every file; the archive digest sidecar | None for accidental damage |
| Bundle deliberately modified | `--digest` compares the archive with a SHA-256 you obtained by another route | **Checksums inside the bundle prove nothing against an attacker**, who can rewrite them. Without `--digest`, a consistently rewritten bundle passes. |
| Malicious archive (path traversal, absolute paths, symlinks, devices, zip-bomb sizes) | Only plain files and directories; rejects `..`, absolute paths, links, FIFOs, duplicates, extra top-level entries; caps on entry count and total size; files are copied by hand rather than `extractall` | Disk use up to the caps |
| Compose file asks for host takeover (`privileged`, host namespaces, `docker.sock`, dangerous capabilities, system bind mounts) | `docker compose config` is audited; high-risk settings stop the install without `--accept-risks`; medium risks are printed | A Compose file can still be harmful in ways the audit does not know. It is a seat belt, not a sandbox. |
| Image differs from what was packed | Loaded image ID must match the manifest | Trusts Docker's image ID |
| Health check used to run commands | `command` checks are off unless `--allow-command-checks`; no shell is used | Allowed commands run as you |
| Health URL abused (redirect to another host, odd schemes) | http/https only; redirects are not followed | None known |
| Network access at install | `--no-build --pull never`; Dockyard itself opens connections only for health checks you configured | A container may do what it likes once started |
| Secrets packed by accident | Files are only those listed in the config | Whatever you list is in the bundle, unencrypted |

## Out of scope

Signing bundles (planned), encrypting them, vulnerability scanning of images, and sandboxing the application.
