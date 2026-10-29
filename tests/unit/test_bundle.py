from __future__ import annotations

import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from dockyard import bundle as bundle_mod
from dockyard.bundle import (
    PackResult,
    open_bundle,
    pack,
    parse_checksums,
    read_manifest_only,
    safe_extract,
    sha256_file,
    sidecar_digest,
    verify_dir,
)
from dockyard.config import load_config
from dockyard.errors import DockyardError, VerificationError

from ..fakes import FakeDocker


def unpack(result: PackResult, dest: Path) -> Path:
    return safe_extract(result.archive, dest)


# -- pack ----------------------------------------------------------------------


def test_pack_produces_a_verifiable_bundle(bundle: PackResult, tmp_path: Path) -> None:
    assert bundle.archive.name == "demo-1.2.3.dockyard"
    assert bundle.sidecar.read_text().split()[0] == bundle.digest == sha256_file(bundle.archive)
    with open_bundle(bundle.archive, expected_digest=bundle.digest) as opened:
        names = {
            p.relative_to(opened.root).as_posix() for p in opened.root.rglob("*") if p.is_file()
        }
        assert names == {
            "manifest.json", "SHA256SUMS", "compose.yaml", "images/acme_web_1.0.tar",
            "files/app.env", "dockyard.pyz", "INSTALL.txt",
        }  # fmt: skip
        assert opened.manifest.images[0].ref == "acme/web:1.0"
        assert opened.manifest.health[0].kind == "tcp"
        assert opened.verified.files == 6  # SHA256SUMS does not list itself
    assert sidecar_digest(bundle.archive) == bundle.digest
    assert read_manifest_only(bundle.archive).app_version == "1.2.3"


def test_pack_is_reproducible_for_the_same_inputs(
    tmp_path: Path, project: Path, fake: FakeDocker
) -> None:
    cfg = load_config(project)
    a = pack(cfg, fake.docker(), tmp_path / "a", source_date_epoch=1_793_300_000)
    b = pack(cfg, fake.docker(), tmp_path / "b", source_date_epoch=1_793_300_000)
    assert a.digest == b.digest
    c = pack(cfg, fake.docker(), tmp_path / "c", source_date_epoch=1_793_300_001)
    assert c.digest != a.digest


def test_pack_refuses_to_overwrite_an_existing_bundle(
    bundle: PackResult, project: Path, fake: FakeDocker
) -> None:
    with pytest.raises(DockyardError, match="already exists"):
        pack(load_config(project), fake.docker(), bundle.archive.parent)


def test_pack_names_the_missing_image(tmp_path: Path, project: Path, fake: FakeDocker) -> None:
    fake.images.clear()
    with pytest.raises(DockyardError, match="not on this machine"):
        pack(load_config(project), fake.docker(), tmp_path / "o")
    assert not (tmp_path / "o" / "demo-1.2.3.dockyard").exists()


def test_pack_can_pull_first(tmp_path: Path, project: Path, fake: FakeDocker) -> None:
    fake.images.clear()
    pack(load_config(project), fake.docker(), tmp_path / "o", pull=True)
    assert ["pull", "acme/web:1.0"] in [c[1:3] for c in fake.calls]


def test_services_without_an_image_cannot_be_bundled(
    tmp_path: Path, project: Path, fake: FakeDocker
) -> None:
    fake.compose = {"services": {"api": {"build": "."}}}
    with pytest.raises(DockyardError, match="no `image:`"):
        pack(load_config(project), fake.docker(), tmp_path / "o")
    fake.compose = {"services": {}}
    with pytest.raises(DockyardError, match="no services"):
        pack(load_config(project), fake.docker(), tmp_path / "o")


def test_risky_compose_settings_are_recorded_in_the_manifest(
    tmp_path: Path, project: Path, fake: FakeDocker
) -> None:
    fake.compose = {"services": {"web": {"image": "acme/web:1.0", "privileged": True}}}
    result = pack(load_config(project), fake.docker(), tmp_path / "o")
    assert result.risks == 1 and "high: web: runs privileged" in result.manifest.warnings[0]


def test_two_services_sharing_an_image_bundle_it_once(
    tmp_path: Path, project: Path, fake: FakeDocker
) -> None:
    fake.compose = {"services": {"a": {"image": "acme/web:1.0"}, "b": {"image": "acme/web:1.0"}}}
    assert len(pack(load_config(project), fake.docker(), tmp_path / "o").manifest.images) == 1


# -- tampering with an unpacked bundle ----------------------------------------------


@pytest.fixture
def root(bundle: PackResult, tmp_path: Path) -> Path:
    return unpack(bundle, tmp_path / "x")


def test_clean_bundle_verifies(root: Path) -> None:
    assert verify_dir(root).files == 6  # SHA256SUMS does not list itself


def test_modified_image_is_detected(root: Path) -> None:
    (root / "images" / "acme_web_1.0.tar").write_bytes(b"evil")
    with pytest.raises(VerificationError, match="checksum mismatch: images/acme_web_1.0.tar"):
        verify_dir(root)


def test_modified_compose_is_detected(root: Path) -> None:
    (root / "compose.yaml").write_text("services: {}\n")
    with pytest.raises(VerificationError, match="checksum mismatch: compose.yaml"):
        verify_dir(root)


def test_extra_and_missing_files_are_detected(root: Path) -> None:
    (root / "extra.sh").write_text("echo hi")
    with pytest.raises(VerificationError, match="not listed.*extra.sh"):
        verify_dir(root)
    (root / "extra.sh").unlink()
    (root / "files" / "app.env").unlink()
    with pytest.raises(VerificationError, match="missing: files/app.env"):
        verify_dir(root)


def test_manifest_and_checksum_file_must_agree(root: Path) -> None:
    sums = (root / "SHA256SUMS").read_text().splitlines()
    forged = [
        "f" * 64 + "  compose.yaml" if line.endswith("  compose.yaml") else line for line in sums
    ]
    (root / "SHA256SUMS").write_text("\n".join(forged) + "\n")
    with pytest.raises(VerificationError, match="checksum mismatch|disagree"):
        verify_dir(root)


def test_a_consistently_rewritten_bundle_passes_checksums_which_is_why_the_digest_matters(
    root: Path,
) -> None:
    (root / "compose.yaml").write_text("services: {}\n")
    manifest = json.loads((root / "manifest.json").read_text())
    new = sha256_file(root / "compose.yaml")
    manifest["compose"]["sha256"] = new
    (root / "manifest.json").write_text(json.dumps(manifest))
    lines = []
    for line in (root / "SHA256SUMS").read_text().splitlines():
        digest, _, name = line.partition("  ")
        if name == "compose.yaml":
            digest = new
        elif name == "manifest.json":
            digest = sha256_file(root / "manifest.json")
        lines.append(f"{digest}  {name}")
    (root / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    assert verify_dir(root)  # internally consistent: only an outside digest can catch this


def test_image_size_must_match_manifest(root: Path) -> None:
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["images"][0]["size"] += 1
    (root / "manifest.json").write_text(json.dumps(manifest))
    sums = (root / "SHA256SUMS").read_text().splitlines()
    fixed = [
        f"{sha256_file(root / 'manifest.json')}  manifest.json"
        if line.endswith("  manifest.json")
        else line
        for line in sums
    ]
    (root / "SHA256SUMS").write_text("\n".join(fixed) + "\n")
    with pytest.raises(VerificationError, match="size mismatch"):
        verify_dir(root)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlinks_inside_a_bundle_are_refused(root: Path) -> None:
    (root / "link").symlink_to("/etc/passwd")
    with pytest.raises(VerificationError, match="symbolic link"):
        verify_dir(root)


def test_missing_manifest_or_checksums(root: Path) -> None:
    (root / "SHA256SUMS").unlink()
    with pytest.raises(VerificationError, match="missing SHA256SUMS"):
        verify_dir(root)


@pytest.mark.parametrize("text", ["nothex  a\n", "a" * 64 + " a\n", "a" * 64 + "  ../x\n"])
def test_malformed_checksum_files(text: str) -> None:
    with pytest.raises(VerificationError):
        parse_checksums(text)


def test_duplicate_checksum_entries() -> None:
    line = "a" * 64 + "  x\n"
    with pytest.raises(VerificationError, match="twice"):
        parse_checksums(line * 2)


# -- hostile archives ------------------------------------------------------------


def make_tar(path: Path, members: list[tuple[str, bytes | None, bytes]]) -> Path:
    """members: (name, data or None for a directory, tar type)."""
    with tarfile.open(path, "w:gz") as tar:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            if kind == b"dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == b"file":
                info.size = len(data or b"")
                tar.addfile(info, io.BytesIO(data or b""))
            else:
                info.type = {
                    b"sym": tarfile.SYMTYPE,
                    b"hard": tarfile.LNKTYPE,
                    b"fifo": tarfile.FIFOTYPE,
                }[kind]
                info.linkname = "/etc/passwd"
                tar.addfile(info)
    return path


@pytest.mark.parametrize(
    ("members", "message"),
    [
        ([("b/../../evil", b"x", b"file")], "unsafe path"),
        ([("b/a/../../../evil", b"x", b"file")], "unsafe path"),
        ([("/abs/evil", b"x", b"file")], "unsafe"),
        ([("b/link", None, b"sym")], "link or special"),
        ([("b/link", None, b"hard")], "link or special"),
        ([("b/pipe", None, b"fifo")], "link or special"),
        ([("b", None, b"dir"), ("b/x", b"1", b"file"), ("b/x", b"2", b"file")], "twice"),
        ([("a", None, b"dir"), ("b", None, b"dir")], "exactly one top-level"),
        ([("loose.txt", b"x", b"file")], "file at the top level"),
        ([(".hidden/x", b"x", b"file")], "unsafe"),
        ([], "exactly one top-level"),
    ],
)
def test_hostile_archives_are_refused_before_anything_is_written(
    tmp_path: Path, members: list[tuple[str, bytes | None, bytes]], message: str
) -> None:
    archive = make_tar(tmp_path / "evil.dockyard", members)
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(VerificationError, match=message):
        safe_extract(archive, dest)
    assert list(dest.iterdir()) == []
    assert not (tmp_path / "evil").exists()


def test_entry_count_and_size_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = make_tar(tmp_path / "a", [("b/x", b"12345", b"file"), ("b/y", b"123", b"file")])
    with pytest.raises(VerificationError, match="more than 7 bytes|GiB"):
        safe_extract(archive, tmp_path / "d1", max_bytes=7)
    monkeypatch.setattr(bundle_mod, "MAX_FILES", 1)
    with pytest.raises(VerificationError, match="more than 1 entries"):
        safe_extract(archive, tmp_path / "d2")


def test_corrupt_and_foreign_files_are_not_bundles(tmp_path: Path, bundle: PackResult) -> None:
    junk = tmp_path / "junk"
    junk.write_bytes(b"not an archive")
    with pytest.raises(VerificationError, match="not a readable bundle"):
        safe_extract(junk, tmp_path / "d")
    truncated = tmp_path / "trunc"
    truncated.write_bytes(bundle.archive.read_bytes()[:2000])
    with pytest.raises(VerificationError):
        open_bundle(truncated)


def test_flipping_a_byte_inside_the_archive_is_caught(tmp_path: Path, bundle: PackResult) -> None:
    data = bytearray(bundle.archive.read_bytes())
    data[len(data) // 2] ^= 0xFF
    bad = tmp_path / "flipped"
    bad.write_bytes(bytes(data))
    with pytest.raises(VerificationError):
        open_bundle(bad)


def test_digest_mismatch_stops_before_extraction(bundle: PackResult) -> None:
    with pytest.raises(VerificationError, match="does not match the expected"):
        open_bundle(bundle.archive, expected_digest="0" * 64)


def test_digest_is_case_and_space_insensitive(bundle: PackResult) -> None:
    with open_bundle(bundle.archive, expected_digest=f" {bundle.digest.upper()}\n"):
        pass


def test_open_bundle_input_errors(tmp_path: Path, root: Path) -> None:
    with pytest.raises(DockyardError, match="no bundle"):
        open_bundle(tmp_path / "nope")
    with pytest.raises(DockyardError, match="applies to an archive"):
        open_bundle(root, expected_digest="0" * 64)
    assert open_bundle(root).archive_digest is None


def test_scratch_space_is_cleaned_up(bundle: PackResult) -> None:
    opened = open_bundle(bundle.archive)
    scratch = opened.root.parent
    assert scratch.exists()
    opened.close()
    assert not scratch.exists()


def test_sidecar_missing_or_garbled(bundle: PackResult) -> None:
    bundle.sidecar.write_text("garbage")
    assert sidecar_digest(bundle.archive) is None
    bundle.sidecar.unlink()
    assert sidecar_digest(bundle.archive) is None


def test_the_bundled_installer_is_a_runnable_zipapp(root: Path) -> None:
    import subprocess
    import sys

    out = subprocess.run(  # noqa: S603
        [sys.executable, "-I", str(root / "dockyard.pyz"), "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.startswith("dockyard ")
