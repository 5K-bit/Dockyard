"""The bundle manifest: strict, versioned, validated on read.

A bundle may come from anywhere, so every field is checked before it is used. Names that
end up on disk must be plain relative POSIX paths inside the bundle.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .errors import VerificationError

SCHEMA = 1
MANIFEST_FILE = "manifest.json"
CHECKSUM_FILE = "SHA256SUMS"
COMPOSE_FILE = "compose.yaml"
PYZ_FILE = "dockyard.pyz"
README_FILE = "INSTALL.txt"
IMAGES_DIR = "images"

HEALTH_KINDS = ("http", "tcp", "container", "command")

_APP_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_PATH_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ +=@-]{0,199}$")


def valid_app_name(name: str) -> bool:
    return bool(_APP_NAME.fullmatch(name))


def valid_version(version: str) -> bool:
    return bool(_VERSION.fullmatch(version))


def safe_relpath(path: str) -> str:
    """Return ``path`` if it is a plain relative POSIX path with ordinary components."""
    if not path or path != path.strip() or "\\" in path or "\x00" in path or path.startswith("/"):
        raise VerificationError(f"unsafe path in bundle: {path!r}")
    parts = path.split("/")
    for part in parts:
        if part in {".", ".."} or not _PATH_PART.fullmatch(part) or part.endswith(" "):
            raise VerificationError(f"unsafe path in bundle: {path!r}")
    return path


@dataclass(frozen=True)
class ImageEntry:
    ref: str
    file: str
    sha256: str
    size: int
    image_id: str
    os: str
    arch: str


@dataclass(frozen=True)
class FileEntry:
    path: str
    sha256: str


@dataclass(frozen=True)
class HealthSpec:
    name: str
    kind: str
    params: dict[str, Any]
    timeout: float = 60.0


@dataclass(frozen=True)
class Manifest:
    app_name: str
    app_version: str
    created: str
    dockyard_version: str
    compose: FileEntry
    images: list[ImageEntry]
    extra_files: list[FileEntry] = field(default_factory=list)
    health: list[HealthSpec] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        data = {
            "schema": SCHEMA,
            "app": {"name": self.app_name, "version": self.app_version},
            "created": self.created,
            "dockyard_version": self.dockyard_version,
            "compose": {"file": self.compose.path, "sha256": self.compose.sha256},
            "images": [
                {
                    "ref": i.ref,
                    "file": i.file,
                    "sha256": i.sha256,
                    "size": i.size,
                    "image_id": i.image_id,
                    "os": i.os,
                    "arch": i.arch,
                }
                for i in self.images
            ],
            "extra_files": [{"path": f.path, "sha256": f.sha256} for f in self.extra_files],
            "health": [
                {"name": h.name, "kind": h.kind, "timeout": h.timeout, **h.params}
                for h in self.health
            ],
            "warnings": self.warnings,
        }
        return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True) + "\n"

    def all_files(self) -> dict[str, str]:
        """Path -> sha256 for every file the manifest vouches for (except itself)."""
        files = {self.compose.path: self.compose.sha256}
        files.update({i.file: i.sha256 for i in self.images})
        files.update({f.path: f.sha256 for f in self.extra_files})
        return files


def _req(data: dict[str, Any], key: str, kind: type | tuple[type, ...], where: str) -> Any:
    if key not in data:
        raise VerificationError(f"manifest: {where}missing {key!r}")
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, kind):
        raise VerificationError(f"manifest: {where}{key!r} has the wrong type")
    return value


def _hex(value: str, where: str) -> str:
    if not _SHA256.fullmatch(value):
        raise VerificationError(f"manifest: {where} is not a SHA-256 digest")
    return value


def parse_manifest(text: str) -> Manifest:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise VerificationError(f"manifest is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise VerificationError("manifest is not a JSON object")
    schema = _req(data, "schema", int, "")
    if schema != SCHEMA:
        raise VerificationError(
            f"manifest schema {schema} is not supported (this Dockyard reads {SCHEMA})"
        )
    app = _req(data, "app", dict, "")
    name = _req(app, "name", str, "app.")
    version = _req(app, "version", str, "app.")
    if not valid_app_name(name):
        raise VerificationError(f"manifest: invalid app name {name!r}")
    if not valid_version(version):
        raise VerificationError(f"manifest: invalid app version {version!r}")
    compose = _req(data, "compose", dict, "")
    compose_entry = FileEntry(
        safe_relpath(_req(compose, "file", str, "compose.")),
        _hex(_req(compose, "sha256", str, "compose."), "compose.sha256"),
    )
    images: list[ImageEntry] = []
    for raw in _req(data, "images", list, ""):
        if not isinstance(raw, dict):
            raise VerificationError("manifest: an image entry is not an object")
        file = safe_relpath(_req(raw, "file", str, "image."))
        if not file.startswith(f"{IMAGES_DIR}/") or file.count("/") != 1:
            raise VerificationError(f"manifest: image file {file!r} must be directly in images/")
        image_id = _req(raw, "image_id", str, "image.")
        if not _IMAGE_ID.fullmatch(image_id):
            raise VerificationError(f"manifest: invalid image id {image_id!r}")
        size = _req(raw, "size", int, "image.")
        if size < 0:
            raise VerificationError("manifest: negative image size")
        ref = _req(raw, "ref", str, "image.")
        if not ref or ref.startswith("-") or any(c.isspace() for c in ref):
            raise VerificationError(f"manifest: invalid image reference {ref!r}")
        images.append(
            ImageEntry(
                ref,
                file,
                _hex(_req(raw, "sha256", str, "image."), "image.sha256"),
                size,
                image_id,
                _req(raw, "os", str, "image."),
                _req(raw, "arch", str, "image."),
            )
        )
    if not images:
        raise VerificationError("manifest lists no images")
    extras = [
        FileEntry(
            safe_relpath(_req(raw, "path", str, "extra file ")),
            _hex(_req(raw, "sha256", str, "extra file "), "extra file sha256"),
        )
        for raw in _req(data, "extra_files", list, "")
        if isinstance(raw, dict)
    ]
    health: list[HealthSpec] = []
    for raw in _req(data, "health", list, ""):
        if not isinstance(raw, dict):
            raise VerificationError("manifest: a health check is not an object")
        kind = _req(raw, "kind", str, "health check ")
        if kind not in HEALTH_KINDS:
            raise VerificationError(f"manifest: unknown health check kind {kind!r}")
        timeout = _req(raw, "timeout", (int, float), "health check ")
        params = {k: v for k, v in raw.items() if k not in {"name", "kind", "timeout"}}
        health.append(
            HealthSpec(_req(raw, "name", str, "health check "), kind, params, float(timeout))
        )
    warnings = [str(w) for w in _req(data, "warnings", list, "")]
    return Manifest(
        name,
        version,
        _req(data, "created", str, ""),
        _req(data, "dockyard_version", str, ""),
        compose_entry,
        images,
        extras,
        health,
        warnings,
    )
