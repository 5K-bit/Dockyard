# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Security

- Quick start and `INSTALL.txt` verify the archive's independently supplied digest with `sha256sum` before anything is extracted or run; the bundled installer is no longer used to vouch for itself.
- `install` refuses symbolic links at destination files and parent directories (including the install record), and bundle files that would overwrite `.dockyard/`.
- `install` stages and validates (risk audit, image load and ID check) before touching the target; a rejected upgrade leaves the existing installation unchanged. Files are replaced atomically.

### Fixed

- The real-Docker acceptance corruption fixture now preserves the archive structure and changes one image byte, so it exercises the checksum check rather than failing with a missing manifest.

### Changed

- The release workflow now requires the full CI workflow to pass and installs and smoke-tests the built wheel and sdist before publishing.

## [0.1.0] - 2026-10-31

First working version.

### Added

- `dockyard pack`: bundle a Docker Compose application (images via `docker save`, Compose file, extra files, manifest, checksums, and the tool itself as `dockyard.pyz`) into one deterministic archive with a SHA-256 sidecar.
- `dockyard verify` with optional `--digest` for an out-of-band archive check; safe extraction that refuses traversal, links, duplicates and oversized archives.
- `dockyard install`: readiness and platform checks, Compose risk audit, image load with ID verification, start with no pull and no build, health checks; `--upgrade`, `--no-start`, `--accept-risks`.
- Health checks: http, tcp, container and (opt-in) command.
- `dockyard health`, `status`, `uninstall [--remove-images --volumes]`, `init`.
- Exit codes: 0 ok, 1 error, 2 verification failure, 3 health failure.

### Fixed

- A crafted bundle archive listing a file and an entry beneath it crashed the unpacker with a raw `FileExistsError`. Structural conflicts are now refused during validation, and a write failure while unpacking (for example a full disk) is reported as a verification error.
- `manifest.json` and `SHA256SUMS` are read with a 4 MiB cap and must be valid UTF-8, in both the verifier and the quick-listing reader (which also stops scanning after the entry limit).
- A bundle damaged in transit could be reported as missing files. Python's tar reader stops quietly at a damaged header and gzip checks its CRC only at the end of the stream, so most single-byte corruptions (33 of 40 in a trial) unpacked a truncated bundle and failed later with a misleading message. Unpacking now reads the archive to its end, so any corruption is refused during unpacking with the cause named. Nothing was ever loaded in either case.
