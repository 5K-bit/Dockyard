from __future__ import annotations

import json
from pathlib import Path

import pytest

from dockyard.bundle import PackResult
from dockyard.errors import DockyardError, HealthError, VerificationError
from dockyard.install import (
    InstallOptions,
    check_health,
    install,
    read_record,
    status,
    uninstall,
)

from ..fakes import FakeDocker


def opts(tmp_path: Path, **kw: object) -> InstallOptions:
    return InstallOptions(target=tmp_path / "app", **kw)  # type: ignore[arg-type]


@pytest.fixture
def target_machine(fake: FakeDocker) -> FakeDocker:
    """A different machine: it does not have the application image yet."""
    fake.images.clear()
    return fake


def test_install_loads_images_places_files_starts_and_checks(
    bundle: PackResult, tmp_path: Path, target_machine: FakeDocker
) -> None:
    log: list[str] = []
    fake = target_machine
    fake.ps_rows = [{"Service": "web", "Name": "w", "State": "running"}]
    result = install(
        bundle.archive, fake.docker(), opts(tmp_path, skip_health=True), progress=log.append
    )
    target = tmp_path / "app"
    assert (target / "compose.yaml").is_file() and (
        target / "app.env"
    ).read_text() == "GREETING=hello\n"
    assert (target / "manifest.json").is_file()
    assert "acme/web:1.0" in fake.images
    record = read_record(target)
    assert record["app"] == "demo" and record["project"] == "demo"
    assert record["archive_sha256"] == bundle.digest
    assert result.checks == [] and any("loading acme/web:1.0" in line for line in log)
    up = next(c for c in fake.calls if "up" in c)
    assert (
        "--no-build" in up and up[up.index("--pull") + 1] == "never"
    )  # never reaches for a registry


def test_install_runs_health_checks_after_starting(
    bundle: PackResult, tmp_path: Path, target_machine: FakeDocker
) -> None:
    import socket

    with socket.socket() as s:  # the bundle's check is tcp:47123; open it
        try:
            s.bind(("127.0.0.1", 47123))
        except OSError:
            pytest.skip("port 47123 busy")
        s.listen()
        result = install(bundle.archive, target_machine.docker(), opts(tmp_path))
    assert [c.ok for c in result.checks] == [True]


def test_failed_health_check_raises_with_logs(
    bundle: PackResult, tmp_path: Path, target_machine: FakeDocker
) -> None:
    with pytest.raises(HealthError, match="(?s)not healthy.*recent logs.*web \\| listening"):
        install(bundle.archive, target_machine.docker(), opts(tmp_path))  # nothing listens on 47123
    assert (tmp_path / "app" / ".dockyard" / "install.json").is_file()  # installed, but flagged


def test_no_start_loads_without_starting(
    bundle: PackResult, tmp_path: Path, target_machine: FakeDocker
) -> None:
    install(bundle.archive, target_machine.docker(), opts(tmp_path, start=False))
    assert not any("up" in c for c in target_machine.calls)


def test_an_unverified_bundle_never_reaches_docker(
    bundle: PackResult, tmp_path: Path, target_machine: FakeDocker
) -> None:
    target_machine.calls.clear()  # packing used the same fake
    with pytest.raises(VerificationError):
        install(bundle.archive, target_machine.docker(), opts(tmp_path, expected_digest="0" * 64))
    data = bytearray(bundle.archive.read_bytes())
    data[len(data) // 2] ^= 1
    bad = tmp_path / "bad.dockyard"
    bad.write_bytes(bytes(data))
    with pytest.raises(VerificationError):
        install(bad, target_machine.docker(), opts(tmp_path))
    assert target_machine.calls == [] and not (tmp_path / "app").exists()


def test_docker_missing_or_down_is_a_clear_error(
    bundle: PackResult, tmp_path: Path, target_machine: FakeDocker
) -> None:
    target_machine.daemon_up = False
    with pytest.raises(DockyardError, match="cannot reach the Docker daemon"):
        install(bundle.archive, target_machine.docker(), opts(tmp_path))
    target_machine.daemon_up, target_machine.compose_available = True, False
    with pytest.raises(DockyardError, match="Compose plugin"):
        install(bundle.archive, target_machine.docker(), opts(tmp_path))
    assert not (tmp_path / "app").exists()


def test_platform_mismatch_is_refused_before_loading(
    bundle: PackResult, tmp_path: Path, target_machine: FakeDocker
) -> None:
    target_machine.platform = "linux/aarch64"
    with pytest.raises(DockyardError, match="linux/amd64 but this Docker runs linux/arm64"):
        install(bundle.archive, target_machine.docker(), opts(tmp_path))
    assert not any(c[1] == "load" for c in target_machine.calls)


def test_a_loaded_image_with_the_wrong_id_is_refused(
    bundle: PackResult, tmp_path: Path, target_machine: FakeDocker
) -> None:
    target_machine.load_gives_wrong_id = True
    with pytest.raises(DockyardError, match="expected sha256:"):
        install(bundle.archive, target_machine.docker(), opts(tmp_path))
    assert not (tmp_path / "app").exists()  # a directory created by this install is removed
    assert not any("up" in c for c in target_machine.calls)


def test_high_risk_compose_is_refused_unless_accepted(
    bundle: PackResult, tmp_path: Path, target_machine: FakeDocker
) -> None:
    target_machine.compose = {"services": {"web": {"image": "acme/web:1.0", "privileged": True}}}
    log: list[str] = []
    with pytest.raises(DockyardError, match="--accept-risks"):
        install(bundle.archive, target_machine.docker(), opts(tmp_path), progress=log.append)
    assert any("high: web: runs privileged" in line for line in log)
    assert not any(c[1] == "load" for c in target_machine.calls) and not (tmp_path / "app").exists()
    install(
        bundle.archive, target_machine.docker(), opts(tmp_path, accept_risks=True, skip_health=True)
    )
    assert (tmp_path / "app" / "compose.yaml").exists()


def test_existing_directories(
    bundle: PackResult, tmp_path: Path, target_machine: FakeDocker
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "mine.txt").write_text("keep")
    with pytest.raises(DockyardError, match="not empty and is not a Dockyard install"):
        install(bundle.archive, target_machine.docker(), opts(tmp_path))
    assert (tmp_path / "app" / "mine.txt").read_text() == "keep"
    (tmp_path / "file").write_text("x")
    with pytest.raises(DockyardError, match="not a directory"):
        install(bundle.archive, target_machine.docker(), InstallOptions(target=tmp_path / "file"))


def test_reinstall_needs_upgrade_and_the_same_app(
    bundle: PackResult, tmp_path: Path, target_machine: FakeDocker
) -> None:
    install(bundle.archive, target_machine.docker(), opts(tmp_path, skip_health=True))
    with pytest.raises(DockyardError, match="use --upgrade"):
        install(bundle.archive, target_machine.docker(), opts(tmp_path, skip_health=True))
    install(bundle.archive, target_machine.docker(), opts(tmp_path, skip_health=True, upgrade=True))
    rec = tmp_path / "app" / ".dockyard" / "install.json"
    data = json.loads(rec.read_text())
    data["app"] = "other"
    rec.write_text(json.dumps(data))
    with pytest.raises(DockyardError, match="holds 'other'"):
        install(bundle.archive, target_machine.docker(), opts(tmp_path, upgrade=True))


def test_compose_failure_to_start_is_reported(
    bundle: PackResult, tmp_path: Path, target_machine: FakeDocker
) -> None:
    target_machine.up_fails = True
    with pytest.raises(DockyardError, match="port is already allocated"):
        install(bundle.archive, target_machine.docker(), opts(tmp_path))


def test_check_health_status_and_uninstall(
    bundle: PackResult, tmp_path: Path, target_machine: FakeDocker
) -> None:
    fake = target_machine
    install(bundle.archive, fake.docker(), opts(tmp_path, skip_health=True))
    target = tmp_path / "app"
    results = check_health(target, fake.docker())
    assert [r.ok for r in results] == [False]  # nothing listens on the check's port
    record, rows = status(target, fake.docker())
    assert record["version"] == "1.2.3" and rows[0]["Service"] == "web"
    removed = uninstall(target, fake.docker(), remove_images=True, volumes=True)
    assert removed == ["acme/web:1.0"] and "acme/web:1.0" not in fake.images
    down = next(c for c in fake.calls if "down" in c)
    assert "--volumes" in down
    assert "uninstalled" in read_record(target)


def test_uninstall_keeps_volumes_by_default(
    bundle: PackResult, tmp_path: Path, target_machine: FakeDocker
) -> None:
    install(bundle.archive, target_machine.docker(), opts(tmp_path, skip_health=True))
    uninstall(tmp_path / "app", target_machine.docker())
    assert "--volumes" not in next(c for c in target_machine.calls if "down" in c)


def test_commands_on_a_directory_that_is_not_an_install(tmp_path: Path, fake: FakeDocker) -> None:
    (tmp_path / "x").mkdir()
    for call in (
        lambda: read_record(tmp_path / "x"),
        lambda: status(tmp_path / "x", fake.docker()),
    ):
        with pytest.raises(DockyardError, match="not a Dockyard install"):
            call()
    (tmp_path / "x" / ".dockyard").mkdir()
    (tmp_path / "x" / ".dockyard" / "install.json").write_text("[1]")
    with pytest.raises(DockyardError, match="not a Dockyard install record"):
        read_record(tmp_path / "x")
    (tmp_path / "x" / ".dockyard" / "install.json").write_text("{")
    with pytest.raises(DockyardError, match="not valid JSON"):
        read_record(tmp_path / "x")
