# Architecture

Dockyard has two halves that share one file format: **pack** runs on a machine with internet and Docker; **install** runs on the target machine, which has Docker and Compose but no internet.

```
 packing machine                                 target machine (offline)
 ───────────────                                 ───────────────────────
 dockyard.toml ─► pack ─► app-1.0.0.dockyard ═══► verify ─► install ─► health
                    │          + .sha256 sidecar      │          │
                    │                                 │          ├─ docker compose config  (risk audit)
                    ├─ docker save  (images/*.tar)    │          ├─ docker load            (image ID check)
                    ├─ compose + extra files          │          └─ compose up --no-build --pull never
                    ├─ manifest.json + SHA256SUMS     └─ safe extraction, checksums, digest
                    └─ dockyard.pyz (the tool itself)
```

## The bundle

A gzip tar with exactly one top-level directory:

| Path | Purpose |
|---|---|
| `manifest.json` | Schema 1: app name and version, images (reference, image ID, platform, tar path), files, health checks. Parsed strictly; unknown keys are rejected. |
| `SHA256SUMS` | Digest of every other file. |
| `compose.yaml` | The Compose file, placed in the install directory. |
| `images/*.tar` | One `docker save` archive per image. |
| `files/...` | Extra files the app needs (configuration, data). |
| `dockyard.pyz` | A zipapp of Dockyard, so the target needs only Python 3.11+, not a `pip install`. |
| `INSTALL.txt` | Three lines of human instructions. |

Packing is deterministic when `SOURCE_DATE_EPOCH` is set: sorted entries, zeroed owners, fixed timestamps. Image tars come from Docker and are only as reproducible as Docker makes them.

## Modules

| Module | Responsibility |
|---|---|
| `config.py` | Strict `dockyard.toml` loading. |
| `manifest.py` | Manifest model, validation, path and name rules. |
| `bundle.py` | `pack`, safe extraction, checksum verification, archive digest. |
| `docker.py` | A thin wrapper over the `docker` CLI with an injectable runner. The only module that starts processes. |
| `risks.py` | Reads `docker compose config` output and rates risky settings. |
| `health.py` | http, tcp, container and command checks with retry. |
| `install.py` | The install, health, status and uninstall flows and the `.dockyard/install.json` record. |
| `cli.py` | Parsing and printing; maps errors to exit codes. |

Everything above `docker.py` is testable with `tests/fakes.py::FakeDocker`. A second set of tests runs against a real daemon when one exists.

## Install flow

1. **Verify.** Extract safely, check `SHA256SUMS`, and compare the archive digest when `--digest` is given. Nothing touches Docker if this fails (exit 2).
2. **Ready check.** The daemon answers and the Compose plugin exists.
3. **Platform.** Each image's OS and architecture must match the daemon's.
4. **Target directory.** Empty or new. A directory that already holds an install of the same app needs `--upgrade`.
5. **Place files**, then **audit the Compose file** with `docker compose config`. High-risk settings stop the install unless `--accept-risks`.
6. **Load images** and compare each loaded image ID with the manifest.
7. **Record** the install, **start** with `compose up --detach --no-build --pull never`, then run **health checks**. Failing checks exit 3 and leave the app installed so you can inspect it.

## Exit codes

`0` success · `1` error · `2` verification failure · `3` health failure.
