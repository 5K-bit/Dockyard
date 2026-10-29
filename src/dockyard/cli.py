"""Command-line interface. Thin: parsing and printing only."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .bundle import open_bundle, pack, sidecar_digest
from .config import CONFIG_NAME, TEMPLATE, load_config
from .docker import Docker
from .errors import DockyardError, HealthError, VerificationError
from .install import InstallOptions, check_health, install, installed_manifest, status, uninstall

EXIT_OK, EXIT_ERROR, EXIT_VERIFY, EXIT_HEALTH = 0, 1, 2, 3


def _say(message: str) -> None:
    print(message, flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dockyard", description="Offline deployment kit for Docker Compose apps. By OBEOS."
    )
    p.add_argument("--version", action="version", version=f"dockyard {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help=f"write a starter {CONFIG_NAME}")
    s.add_argument("--dir", default=".")

    s = sub.add_parser("pack", help="bundle the app's images and files into one archive")
    s.add_argument("--config", default=CONFIG_NAME)
    s.add_argument("--out", default="dist-bundles", help="output directory")
    pull = s.add_mutually_exclusive_group()
    pull.add_argument("--pull", dest="pull", action="store_true", default=None)
    pull.add_argument("--no-pull", dest="pull", action="store_false")

    s = sub.add_parser("verify", help="check a bundle (archive or unpacked directory)")
    s.add_argument("bundle")
    s.add_argument("--digest", help="expected SHA-256 of the archive, obtained separately")

    s = sub.add_parser("install", help="install a bundle with no internet access")
    s.add_argument("bundle")
    s.add_argument("--dir", required=True, help="install directory")
    s.add_argument("--project", help="Compose project name (default: the app name)")
    s.add_argument("--digest", help="expected SHA-256 of the archive, obtained separately")
    s.add_argument("--accept-risks", action="store_true", help="allow high-risk compose settings")
    s.add_argument(
        "--allow-command-checks", action="store_true", help="allow health checks that run commands"
    )
    s.add_argument("--no-start", action="store_true", help="load images and place files only")
    s.add_argument("--skip-health", action="store_true")
    s.add_argument("--upgrade", action="store_true", help="replace an existing install of this app")

    s = sub.add_parser("health", help="run an installed app's health checks")
    s.add_argument("--dir", required=True)
    s.add_argument("--allow-command-checks", action="store_true")

    s = sub.add_parser("status", help="show an install and its containers")
    s.add_argument("--dir", required=True)

    s = sub.add_parser("uninstall", help="stop the app (and optionally remove images or volumes)")
    s.add_argument("--dir", required=True)
    s.add_argument("--remove-images", action="store_true")
    s.add_argument(
        "--volumes", action="store_true", help="also delete the app's named volumes (data!)"
    )
    return p


def main(argv: Sequence[str] | None = None, *, docker: Docker | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args, docker or Docker())
    except VerificationError as exc:
        print(f"dockyard: verification failed: {exc}", file=sys.stderr)
        return EXIT_VERIFY
    except HealthError as exc:
        print(f"dockyard: {exc}", file=sys.stderr)
        return EXIT_HEALTH
    except DockyardError as exc:
        print(f"dockyard: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except BrokenPipeError:
        return EXIT_OK


def _epoch() -> int | None:
    value = os.environ.get("SOURCE_DATE_EPOCH")
    if value is None:
        return None
    if not value.isdigit():
        raise DockyardError("SOURCE_DATE_EPOCH must be a number of seconds")
    return int(value)


def _run(args: argparse.Namespace, docker: Docker) -> int:
    cmd = args.command
    if cmd == "init":
        path = Path(args.dir) / CONFIG_NAME
        if path.exists():
            raise DockyardError(f"{path} already exists")
        path.write_text(TEMPLATE, encoding="utf-8", newline="\n")
        _say(f"Wrote {path}. Edit it, then run `dockyard pack`.")
        return EXIT_OK
    if cmd == "pack":
        cfg = load_config(Path(args.config))
        result = pack(
            cfg, docker, Path(args.out), pull=args.pull, source_date_epoch=_epoch(), progress=_say
        )
        m = result.manifest
        _say(f"Bundle: {result.archive} ({result.archive.stat().st_size / 1e6:.1f} MB)")
        _say(f"SHA-256: {result.digest}")
        _say(
            f"{m.app_name} {m.app_version}: {len(m.images)} image(s), "
            f"{len(m.health)} health check(s)"
        )
        if result.risks:
            _say(
                f"{result.risks} compose warning(s) recorded in the manifest; "
                "see `dockyard verify`."
            )
        _say("Give the SHA-256 to the person installing, by a different route than the file.")
        return EXIT_OK
    if cmd == "verify":
        source = Path(args.bundle)
        expected = args.digest or (sidecar_digest(source) if source.is_file() else None)
        with open_bundle(source, expected_digest=expected) as bundle:
            m = bundle.manifest
            _say(
                f"{m.app_name} {m.app_version}, packed {m.created} by dockyard {m.dockyard_version}"
            )
            for image in m.images:
                _say(f"  image {image.ref}  {image.os}/{image.arch}  {image.size / 1e6:.1f} MB")
            for spec in m.health:
                _say(f"  health {spec.name} ({spec.kind})")
            for warning in m.warnings:
                _say(f"  warning {warning}")
            _say(f"checksums OK: {bundle.verified.files} files")
            if bundle.archive_digest:
                origin = (
                    "your --digest"
                    if args.digest
                    else "the .sha256 file next to it"
                    if expected
                    else None
                )
                _say(
                    f"archive SHA-256 {bundle.archive_digest}"
                    + (f" (matches {origin})" if origin else " (no expected digest given)")
                )
        _say("verification: OK")
        return EXIT_OK
    if cmd == "install":
        source = Path(args.bundle)
        options = InstallOptions(
            target=Path(args.dir),
            project=args.project,
            accept_risks=args.accept_risks,
            allow_command_checks=args.allow_command_checks,
            start=not args.no_start,
            skip_health=args.skip_health,
            upgrade=args.upgrade,
            expected_digest=args.digest,
        )
        installed = install(source, docker, options, progress=_say)
        for check in installed.checks:
            _say(f"  health {check.name}: {check.detail} ({check.seconds:.1f}s)")
        state = "installed and running" if options.start else "installed (not started)"
        _say(
            f"{installed.manifest.app_name} {installed.manifest.app_version} {state} "
            f"in {installed.target}"
        )
        return EXIT_OK
    if cmd == "health":
        target = Path(args.dir)
        results = check_health(target, docker, allow_command=args.allow_command_checks)
        for r in results:
            _say(f"{'ok  ' if r.ok else 'FAIL'} {r.name}: {r.detail}")
        return EXIT_OK if all(r.ok for r in results) else EXIT_HEALTH
    if cmd == "status":
        target = Path(args.dir)
        record, rows = status(target, docker)
        m = installed_manifest(target)
        _say(
            f"{record['app']} {record['version']} (project {record['project']}), "
            f"installed {record['installed']}"
        )
        if record.get("uninstalled"):
            _say(f"uninstalled {record['uninstalled']}")
        for row in rows:
            _say(
                f"  {row.get('Service', '?'):<16} {row.get('State', '?'):<10} "
                f"{row.get('Status', '')}"
            )
        _say(f"  images: {', '.join(i.ref for i in m.images)}")
        return EXIT_OK
    if cmd == "uninstall":
        removed = uninstall(
            Path(args.dir), docker, remove_images=args.remove_images, volumes=args.volumes
        )
        _say("stopped" + (f"; removed images: {', '.join(removed)}" if removed else ""))
        return EXIT_OK
    raise DockyardError(f"unknown command {cmd!r}")
