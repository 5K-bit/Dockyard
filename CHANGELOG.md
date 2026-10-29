# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-29

First working version.

### Added

- `dockyard pack`: bundle a Docker Compose application (images via `docker save`, Compose file, extra files, manifest, checksums, and the tool itself as `dockyard.pyz`) into one deterministic archive with a SHA-256 sidecar.
- `dockyard verify` with optional `--digest` for an out-of-band archive check; safe extraction that refuses traversal, links, duplicates and oversized archives.
- `dockyard install`: readiness and platform checks, Compose risk audit, image load with ID verification, start with no pull and no build, health checks; `--upgrade`, `--no-start`, `--accept-risks`.
- Health checks: http, tcp, container and (opt-in) command.
- `dockyard health`, `status`, `uninstall [--remove-images --volumes]`, `init`.
- Exit codes: 0 ok, 1 error, 2 verification failure, 3 health failure.
