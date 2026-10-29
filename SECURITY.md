# Security Policy

## Intended use

Dockyard installs applications you built or have decided to trust, on machines you administer. Docker gives containers a great deal of authority; Dockyard adds checks, not a sandbox.

## What Dockyard protects, and what it does not

- **Checksums catch damage, not attackers.** `SHA256SUMS` lives inside the bundle, so someone who alters a bundle can alter it too. Send the archive SHA-256 (printed by `pack`, written to the `.sha256` sidecar) by a different route and pass it as `--digest`.
- **Archives are hostile input.** Extraction accepts only plain files and directories, rejects traversal, links and duplicates, and caps entry count and size.
- **The Compose file is code.** Privileged mode, host namespaces, the Docker socket, dangerous capabilities and system bind mounts stop the install unless you pass `--accept-risks`. Other risks are printed. The audit has gaps.
- **No network at install.** Images are loaded, never pulled or built.
- **Command health checks are opt-in** (`--allow-command-checks`) and run without a shell.
- **Bundles are not encrypted or signed** in 0.1.0. Anything you add to a bundle is readable by whoever holds it.

The full analysis is in [docs/threat-model.md](docs/threat-model.md).

## Reporting a vulnerability

If you find a way to make a crafted bundle write outside the install directory, pass verification after tampering when `--digest` was supplied, install without the risk audit, or run a command without `--allow-command-checks`, please report it privately through GitHub's **Security → Report a vulnerability** on this repository rather than a public issue.

Include the Dockyard version (`dockyard --version`), your OS, Docker and Python versions, and the smallest bundle or config that reproduces it. Expect an initial response within a few days.
