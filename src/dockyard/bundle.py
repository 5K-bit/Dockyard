"""Pack, extract and verify bundles.

A bundle is a gzip tar holding one directory:

    <name>-<version>/
        manifest.json     what the bundle contains (schema-checked on read)
        SHA256SUMS        SHA-256 of every other file, in ``sha256sum`` format
        compose.yaml      the application definition
        images/*.tar      ``docker save`` output, one file per image
        files/...         extra files shipped next to the compose file
        dockyard.pyz      this tool as one file, for machines without it
        INSTALL.txt       plain-words install steps

The checksums catch corruption and accidental edits. They do not prove who made the
bundle: whoever can change the archive can change SHA256SUMS too. Compare the archive's
own digest (written to ``<bundle>.sha256``) with one you obtained separately.
"""

from __future__ import annotations

import gzip
import hashlib
import re
import shutil
import tarfile
import tempfile
import time
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .config import Config
from .docker import Docker
from .errors import DockyardError, VerificationError
from .manifest import (
    CHECKSUM_FILE,
    COMPOSE_FILE,
    IMAGES_DIR,
    MANIFEST_FILE,
    PYZ_FILE,
    README_FILE,
    FileEntry,
    ImageEntry,
    Manifest,
    parse_manifest,
    safe_relpath,
)
from .pyz import build_pyz
from .risks import audit_compose

CHUNK = 1024 * 1024
MAX_FILES = 10_000
MAX_MANIFEST_BYTES = 4 * 1024**2  # 10,000 entries of a few hundred bytes each
MAX_CHECKSUM_BYTES = 4 * 1024**2
DEFAULT_MAX_BYTES = 200 * 1024**3
_SUM_LINE = re.compile(r"^([0-9a-f]{64})  (.+)$")

Progress = Callable[[str], None]

INSTALL_TXT = """\
Install this bundle on a machine with Docker, Docker Compose and Python 3.11+.
No internet access is needed.

1. Check the archive against the digest you were given by a different route than the file,
   using a tool already on this machine. Do this BEFORE unpacking or running anything from
   the bundle (the bundle's own checksums prove nothing about who made it):
       echo "<digest you were given>  {archive}" | sha256sum -c -
   (macOS: use "shasum -a 256 -c -".) Stop if it does not print "{archive}: OK".
2. Unpack it:
       tar xzf {archive}
       cd {root}
3. Verify and install:
       python dockyard.pyz verify .
       python dockyard.pyz install . --dir /opt/{name}
4. Check it again later:
       python dockyard.pyz health --dir /opt/{name}

Review compose.yaml before installing: it decides what the containers may do on this machine.
"""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_name(ref: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", ref).strip("._-") or "image"


# -- pack ----------------------------------------------------------------------


@dataclass(frozen=True)
class PackResult:
    archive: Path
    sidecar: Path
    digest: str
    manifest: Manifest
    risks: int


def _created(source_date_epoch: int | None) -> int:
    return int(time.time()) if source_date_epoch is None else source_date_epoch


def compose_images(config: dict[str, object]) -> list[str]:
    services = config.get("services")
    if not isinstance(services, dict) or not services:
        raise DockyardError("the compose file defines no services")
    refs: set[str] = set()
    for name in sorted(services):
        svc = services[name]
        image = svc.get("image") if isinstance(svc, dict) else None
        if not isinstance(image, str) or not image:
            raise DockyardError(
                f"service {name!r} has no `image:`. Build it first and give it an image name "
                "so Dockyard can bundle it."
            )
        refs.add(image)
    return sorted(refs)


def pack(
    cfg: Config,
    docker: Docker,
    out_dir: Path,
    *,
    pull: bool | None = None,
    source_date_epoch: int | None = None,
    progress: Progress = lambda _msg: None,
) -> PackResult:
    docker.check_ready()
    resolved = docker.compose_config(cfg.compose)
    refs = compose_images(resolved)
    risks = audit_compose(resolved)
    do_pull = cfg.pull if pull is None else pull
    stamp = _created(source_date_epoch)
    root_name = f"{cfg.name}-{cfg.version}"
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"{root_name}.dockyard"
    if archive.exists():
        raise DockyardError(f"{archive} already exists; remove it or choose another --out")
    with tempfile.TemporaryDirectory(prefix="dockyard-pack-") as tmp:
        root = Path(tmp) / root_name
        (root / IMAGES_DIR).mkdir(parents=True)
        images: list[ImageEntry] = []
        for ref in refs:
            if do_pull:
                progress(f"pulling {ref}")
                docker.pull(ref)
            info = docker.inspect_image(ref)
            if info is None:
                raise DockyardError(
                    f"image {ref!r} is not on this machine. Pull or build it, or set "
                    "`pull = true` in dockyard.toml."
                )
            target = root / IMAGES_DIR / f"{_file_name(ref)}.tar"
            if target.exists():
                raise DockyardError(f"image references collide on the file name {target.name}")
            progress(f"saving {ref}")
            docker.save(ref, target)
            images.append(
                ImageEntry(
                    ref=ref,
                    file=f"{IMAGES_DIR}/{target.name}",
                    sha256=sha256_file(target),
                    size=target.stat().st_size,
                    image_id=str(info["Id"]),
                    os=str(info.get("Os", "")),
                    arch=str(info.get("Architecture", "")),
                )
            )
        shutil.copyfile(cfg.compose, root / COMPOSE_FILE)
        extras: list[FileEntry] = []
        for rel in cfg.extra_files:
            dest = root / "files" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(cfg.compose.parent / rel, dest)
            extras.append(FileEntry(f"files/{rel}", sha256_file(dest)))
        warnings = [f"{r.level}: {r.service}: {r.message}" for r in risks]
        manifest = Manifest(
            app_name=cfg.name,
            app_version=cfg.version,
            created=datetime.fromtimestamp(stamp, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            dockyard_version=__version__,
            compose=FileEntry(COMPOSE_FILE, sha256_file(root / COMPOSE_FILE)),
            images=images,
            extra_files=extras,
            health=cfg.health,
            warnings=warnings,
        )
        (root / MANIFEST_FILE).write_text(manifest.to_json(), encoding="utf-8", newline="\n")
        (root / README_FILE).write_text(
            INSTALL_TXT.format(archive=archive.name, root=root_name, name=cfg.name),
            encoding="utf-8",
            newline="\n",
        )
        build_pyz(root / PYZ_FILE)
        _write_checksums(root)
        progress("writing archive")
        _write_archive(root, archive, stamp)
    digest = sha256_file(archive)
    sidecar = archive.with_name(archive.name + ".sha256")
    sidecar.write_text(f"{digest}  {archive.name}\n", encoding="utf-8", newline="\n")
    return PackResult(archive, sidecar, digest, manifest, len(risks))


def _all_files(root: Path) -> list[Path]:
    return sorted(
        (p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix()
    )


def _write_checksums(root: Path) -> None:
    lines = [
        f"{sha256_file(path)}  {path.relative_to(root).as_posix()}"
        for path in _all_files(root)
        if path.name != CHECKSUM_FILE or path.parent != root
    ]
    (root / CHECKSUM_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _write_archive(root: Path, archive: Path, stamp: int) -> None:
    """A deterministic gzip tar: sorted entries, fixed times and ownership."""
    entries = [root, *sorted(root.rglob("*"), key=lambda p: p.relative_to(root.parent).as_posix())]
    tmp_target = archive.with_name(archive.name + ".partial")
    try:
        with (
            tmp_target.open("wb") as raw,
            gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, compresslevel=6) as gz,
            tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar,
        ):
            for path in entries:
                info = tar.gettarinfo(str(path), arcname=path.relative_to(root.parent).as_posix())
                info.mtime, info.uid, info.gid, info.uname, info.gname = stamp, 0, 0, "", ""
                info.mode = 0o755 if path.is_dir() else 0o644
                if path.is_file():
                    with path.open("rb") as handle:
                        tar.addfile(info, handle)
                else:
                    tar.addfile(info)
        tmp_target.replace(archive)
    finally:
        tmp_target.unlink(missing_ok=True)


# -- extract -------------------------------------------------------------------


def _is_plain_dir_name(name: str) -> bool:
    return name != "." and not name.startswith(".") and "/" not in name


def safe_extract(archive: Path, dest: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> Path:
    """Extract a bundle archive into ``dest`` and return its single top-level directory.

    Only plain files and directories are accepted. Absolute paths, ``..``, links, devices,
    duplicate names and anything over the file and size limits stop the extraction before
    anything is written.
    """
    try:
        tar = tarfile.open(archive, mode="r:*")  # noqa: SIM115 - closed below
    except (tarfile.TarError, OSError, EOFError, zlib.error) as exc:
        raise VerificationError(f"{archive.name} is not a readable bundle archive: {exc}") from exc
    with tar:
        try:
            members = tar.getmembers()
        except (tarfile.TarError, OSError, EOFError, zlib.error) as exc:
            raise VerificationError(f"{archive.name} is truncated or corrupt: {exc}") from exc
        if len(members) > MAX_FILES:
            raise VerificationError(f"bundle has more than {MAX_FILES} entries")
        roots: set[str] = set()
        seen: set[str] = set()
        total = 0
        for member in members:
            if not (member.isfile() or member.isdir()):
                raise VerificationError(f"bundle contains a link or special file: {member.name!r}")
            name = member.name.rstrip("/")
            if name in seen:
                raise VerificationError(f"bundle lists {name!r} twice")
            seen.add(name)
            first, _, rest = name.partition("/")
            if not _is_plain_dir_name(first) or safe_relpath(first) != first:
                raise VerificationError(f"bundle has an unsafe top-level name: {first!r}")
            roots.add(first)
            if rest:
                safe_relpath(rest)
            elif member.isfile():
                raise VerificationError(
                    "bundle has a file at the top level; expected one directory"
                )
            if member.isfile():
                total += member.size
        files = {m.name.rstrip("/") for m in members if m.isfile()}
        for name in sorted(seen):
            parts = name.split("/")
            for depth in range(1, len(parts)):
                inside = "/".join(parts[:depth])
                if inside in files:
                    raise VerificationError(f"bundle lists {name!r} inside {inside!r}, a file")
        if len(roots) != 1:
            raise VerificationError("bundle must contain exactly one top-level directory")
        if total > max_bytes:
            raise VerificationError(f"bundle expands to more than {max_bytes // 1024**3} GiB")
        root = dest / next(iter(roots))
        try:
            for member in members:
                target = dest / member.name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    raise VerificationError(f"cannot read {member.name!r} from the archive")
                with source, target.open("wb") as out:
                    shutil.copyfileobj(source, out, CHUNK)
            # tarfile stops quietly at a damaged header and gzip only checks its CRC at the end
            # of the stream, so read what is left: corruption must fail here, by name, rather
            # than surface later as a bundle that is mysteriously missing files.
            while tar.fileobj.read(CHUNK):
                pass
        except (OSError, tarfile.TarError, EOFError, zlib.error) as exc:
            raise VerificationError(f"could not unpack {archive.name}: {exc}") from exc
    return root


# -- verify --------------------------------------------------------------------


def parse_checksums(text: str) -> dict[str, str]:
    sums: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        match = _SUM_LINE.match(line)
        if not match:
            raise VerificationError(f"{CHECKSUM_FILE} line {number} is malformed")
        path = safe_relpath(match.group(2))
        if path in sums:
            raise VerificationError(f"{CHECKSUM_FILE} lists {path!r} twice")
        sums[path] = match.group(1)
    return sums


def _walk_plain_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise VerificationError(f"bundle contains a symbolic link: {path.relative_to(root)}")
        if path.is_file():
            yield path


@dataclass(frozen=True)
class Verified:
    root: Path
    manifest: Manifest
    files: int
    bytes: int


def _read_text(path: Path, limit: int) -> str:
    """Read a small control file, refusing one that is oversized or not UTF-8."""
    if path.stat().st_size > limit:
        raise VerificationError(f"{path.name} is larger than {limit // 1024**2} MiB")
    try:
        return path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        raise VerificationError(f"{path.name} is not valid UTF-8") from None


def verify_dir(root: Path) -> Verified:
    """Check a bundle directory against its manifest and checksum file."""
    manifest_path, sums_path = root / MANIFEST_FILE, root / CHECKSUM_FILE
    for required in (manifest_path, sums_path):
        if not required.is_file():
            raise VerificationError(f"bundle is missing {required.name}")
    manifest = parse_manifest(_read_text(manifest_path, MAX_MANIFEST_BYTES))
    sums = parse_checksums(_read_text(sums_path, MAX_CHECKSUM_BYTES))
    actual = {p.relative_to(root).as_posix(): p for p in _walk_plain_files(root)}
    actual.pop(CHECKSUM_FILE, None)
    missing = sorted(set(sums) - set(actual))
    extra = sorted(set(actual) - set(sums))
    if missing:
        raise VerificationError(
            f"files listed in {CHECKSUM_FILE} are missing: {', '.join(missing)}"
        )
    if extra:
        raise VerificationError(f"files not listed in {CHECKSUM_FILE}: {', '.join(extra)}")
    total = 0
    for rel, expected in sums.items():
        path = actual[rel]
        if sha256_file(path) != expected:
            raise VerificationError(f"checksum mismatch: {rel}")
        total += path.stat().st_size
    for rel, digest in manifest.all_files().items():
        if sums.get(rel) != digest:
            raise VerificationError(f"manifest and {CHECKSUM_FILE} disagree about {rel}")
    if MANIFEST_FILE not in sums:
        raise VerificationError(f"{CHECKSUM_FILE} does not cover {MANIFEST_FILE}")
    for image in manifest.images:
        if actual[image.file].stat().st_size != image.size:
            raise VerificationError(f"size mismatch: {image.file}")
    return Verified(root, manifest, len(sums), total)


@dataclass
class OpenBundle:
    """A verified bundle on disk, with the scratch space that holds it if it was an archive."""

    verified: Verified
    archive_digest: str | None
    _tmp: tempfile.TemporaryDirectory[str] | None

    @property
    def root(self) -> Path:
        return self.verified.root

    @property
    def manifest(self) -> Manifest:
        return self.verified.manifest

    def close(self) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()

    def __enter__(self) -> OpenBundle:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def open_bundle(
    source: Path,
    *,
    expected_digest: str | None = None,
    scratch: Path | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> OpenBundle:
    """Verify and open a bundle archive or an already-unpacked bundle directory."""
    if source.is_dir():
        if expected_digest is not None:
            raise DockyardError("--digest applies to an archive, not an unpacked directory")
        return OpenBundle(verify_dir(source.resolve()), None, None)
    if not source.is_file():
        raise DockyardError(f"no bundle at {source}")
    digest = sha256_file(source)
    if expected_digest is not None and digest != expected_digest.strip().lower():
        raise VerificationError(
            f"archive digest {digest} does not match the expected {expected_digest.strip().lower()}"
        )
    tmp = tempfile.TemporaryDirectory(prefix="dockyard-", dir=scratch)
    try:
        root = safe_extract(source, Path(tmp.name), max_bytes=max_bytes)
        return OpenBundle(verify_dir(root), digest, tmp)
    except BaseException:
        tmp.cleanup()
        raise


def sidecar_digest(archive: Path) -> str | None:
    """The digest recorded next to the archive by ``pack``, if there is one."""
    sidecar = archive.with_name(archive.name + ".sha256")
    if not sidecar.is_file():
        return None
    match = re.match(r"^([0-9a-f]{64})\b", sidecar.read_text(encoding="utf-8", errors="replace"))
    return match.group(1) if match else None


def read_manifest_only(archive: Path) -> Manifest:
    """Read the manifest from an archive without extracting it (for quick listings)."""
    try:
        with tarfile.open(archive, mode="r:*") as tar:
            for count, member in enumerate(tar, 1):
                if count > MAX_FILES + 1:
                    break
                if (
                    member.isfile()
                    and member.name.count("/") == 1
                    and member.name.endswith("/" + MANIFEST_FILE)
                ):
                    if member.size > MAX_MANIFEST_BYTES:
                        raise VerificationError(
                            f"{MANIFEST_FILE} is larger than {MAX_MANIFEST_BYTES // 1024**2} MiB"
                        )
                    handle = tar.extractfile(member)
                    if handle is not None:
                        try:
                            text = handle.read(MAX_MANIFEST_BYTES + 1).decode("utf-8")
                        except UnicodeDecodeError:
                            raise VerificationError(f"{MANIFEST_FILE} is not valid UTF-8") from None
                        return parse_manifest(text)
    except (tarfile.TarError, OSError, EOFError, zlib.error) as exc:
        raise VerificationError(f"{archive.name} is not a readable bundle archive: {exc}") from exc
    raise VerificationError("no manifest.json in the archive")
