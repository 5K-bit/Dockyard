"""Install a verified bundle with Docker: load images offline, start, check health."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .bundle import Progress, open_bundle
from .docker import Docker
from .errors import DockyardError, HealthError
from .health import CheckResult, run_checks
from .manifest import COMPOSE_FILE, MANIFEST_FILE, Manifest, parse_manifest
from .risks import HIGH, Risk, audit_compose

RECORD_DIR = ".dockyard"
RECORD_FILE = "install.json"


@dataclass(frozen=True)
class InstallOptions:
    target: Path
    project: str | None = None
    accept_risks: bool = False
    allow_command_checks: bool = False
    start: bool = True
    skip_health: bool = False
    upgrade: bool = False
    expected_digest: str | None = None


@dataclass(frozen=True)
class InstallResult:
    manifest: Manifest
    project: str
    target: Path
    risks: list[Risk]
    checks: list[CheckResult]


def _record_path(target: Path) -> Path:
    return target / RECORD_DIR / RECORD_FILE


def read_record(target: Path) -> dict[str, Any]:
    path = _record_path(target)
    if not path.is_file():
        raise DockyardError(f"{target} is not a Dockyard install (no {RECORD_DIR}/{RECORD_FILE})")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise DockyardError(f"{path} is not valid JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("project"), str):
        raise DockyardError(f"{path} is not a Dockyard install record")
    return data


def installed_manifest(target: Path) -> Manifest:
    path = target / MANIFEST_FILE
    if not path.is_file():
        raise DockyardError(f"{target} has no {MANIFEST_FILE}")
    return parse_manifest(path.read_text(encoding="utf-8"))


def _prepare_target(target: Path, manifest: Manifest, upgrade: bool) -> bool:
    """Return True if the directory was created by this install."""
    if target.exists():
        if not target.is_dir():
            raise DockyardError(f"{target} exists and is not a directory")
        if any(target.iterdir()):
            try:
                record = read_record(target)
            except DockyardError as exc:
                raise DockyardError(f"{target} is not empty and is not a Dockyard install") from exc
            if record.get("app") != manifest.app_name:
                raise DockyardError(
                    f"{target} holds {record.get('app')!r}, not {manifest.app_name!r}"
                )
            if not upgrade:
                raise DockyardError(
                    f"{manifest.app_name} {record.get('version')} is already installed here; "
                    "use --upgrade to replace it"
                )
        return False
    target.mkdir(parents=True)
    return True


def install(
    source: Path,
    docker: Docker,
    options: InstallOptions,
    *,
    progress: Progress = lambda _msg: None,
    sleep: Callable[[float], None] | None = None,
) -> InstallResult:
    with open_bundle(source, expected_digest=options.expected_digest) as bundle:
        manifest = bundle.manifest
        progress(
            f"verified {manifest.app_name} {manifest.app_version} "
            f"({bundle.verified.files} files, {bundle.verified.bytes / 1e6:.1f} MB)"
        )
        docker.check_ready()
        os_name, arch = docker.server_platform()
        for image in manifest.images:
            if (image.os, image.arch) != (os_name, arch):
                raise DockyardError(
                    f"image {image.ref} is {image.os}/{image.arch} but this Docker runs "
                    f"{os_name}/{arch}. Pack the bundle on a matching platform."
                )
        target = options.target.resolve()
        created = _prepare_target(target, manifest, options.upgrade)
        project = options.project or manifest.app_name
        try:
            _place_files(bundle.root, target, manifest)
            compose = target / COMPOSE_FILE
            risks = audit_compose(docker.compose_config(compose))
            blocking = [r for r in risks if r.level == HIGH]
            for risk in risks:
                progress(f"{risk.level}: {risk.service}: {risk.message}")
            if blocking and not options.accept_risks:
                raise DockyardError(
                    "compose.yaml asks for high-risk settings (listed above). Review it and "
                    "rerun with --accept-risks if you trust this bundle."
                )
            for image in manifest.images:
                progress(f"loading {image.ref}")
                docker.load(bundle.root / image.file)
                info = docker.inspect_image(image.ref)
                if info is None or info.get("Id") != image.image_id:
                    found = None if info is None else info.get("Id")
                    raise DockyardError(
                        f"after loading, {image.ref} is {found}, expected {image.image_id}"
                    )
        except BaseException:
            if created:
                shutil.rmtree(target, ignore_errors=True)
            raise
        _write_record(target, manifest, project, bundle.archive_digest)
        checks: list[CheckResult] = []
        if options.start:
            progress("starting")
            docker.compose_up(project, compose)
            if not options.skip_health and manifest.health:
                progress("checking health")
                checks = _health(manifest, docker, project, compose, options, sleep)
    return InstallResult(manifest, project, target, risks, checks)


def _place_files(root: Path, target: Path, manifest: Manifest) -> None:
    shutil.copyfile(root / COMPOSE_FILE, target / COMPOSE_FILE)
    shutil.copyfile(root / MANIFEST_FILE, target / MANIFEST_FILE)
    for extra in manifest.extra_files:
        rel = extra.path.removeprefix("files/")
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / extra.path, dest)


def _write_record(
    target: Path, manifest: Manifest, project: str, archive_digest: str | None
) -> None:
    record = {
        "app": manifest.app_name,
        "version": manifest.app_version,
        "project": project,
        "installed": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dockyard_version": __version__,
        "archive_sha256": archive_digest,
        "images": [{"ref": i.ref, "id": i.image_id} for i in manifest.images],
    }
    path = _record_path(target)
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def _health(
    manifest: Manifest,
    docker: Docker,
    project: str,
    compose: Path,
    options: InstallOptions,
    sleep: Callable[[float], None] | None,
) -> list[CheckResult]:
    kwargs: dict[str, Any] = {"sleep": sleep} if sleep else {}
    results = run_checks(
        manifest.health,
        docker=docker,
        project=project,
        compose_file=compose,
        allow_command=options.allow_command_checks,
        **kwargs,
    )
    failed = [r for r in results if not r.ok]
    if failed:
        logs = docker.compose_logs(project, compose)
        detail = "; ".join(f"{r.name}: {r.detail}" for r in failed)
        raise HealthError(
            f"not healthy: {detail}" + (f"\n--- recent logs ---\n{logs}" if logs else "")
        )
    return results


def check_health(target: Path, docker: Docker, *, allow_command: bool = False) -> list[CheckResult]:
    """Run an installed app's checks and return every result, passing or not."""
    record = read_record(target)
    manifest = installed_manifest(target)
    if not manifest.health:
        raise DockyardError("this bundle defines no health checks")
    return run_checks(
        manifest.health,
        docker=docker,
        project=str(record["project"]),
        compose_file=target / COMPOSE_FILE,
        allow_command=allow_command,
    )


def status(target: Path, docker: Docker) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    record = read_record(target)
    rows = docker.compose_ps(str(record["project"]), target / COMPOSE_FILE)
    return record, rows


def uninstall(
    target: Path, docker: Docker, *, remove_images: bool = False, volumes: bool = False
) -> list[str]:
    record = read_record(target)
    docker.compose_down(str(record["project"]), target / COMPOSE_FILE, volumes=volumes)
    removed: list[str] = []
    if remove_images:
        for image in record.get("images", []):
            ref = image.get("ref") if isinstance(image, dict) else None
            if isinstance(ref, str) and docker.remove_image(ref):
                removed.append(ref)
    record["uninstalled"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    _record_path(target).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return removed
