from __future__ import annotations

import socket
from pathlib import Path

import pytest

from dockyard.bundle import PackResult, open_bundle
from dockyard.cli import main
from dockyard.config import CONFIG_NAME
from dockyard.errors import VerificationError

from ..fakes import FakeDocker


def run(fake: FakeDocker, *argv: str) -> int:
    return main(list(argv), docker=fake.docker())


def test_version_and_missing_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0 and capsys.readouterr().out.startswith("dockyard ")
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_init_writes_a_template_and_refuses_to_overwrite(
    tmp_path: Path, fake: FakeDocker, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(fake, "init", "--dir", str(tmp_path)) == 0
    assert (tmp_path / CONFIG_NAME).read_text().startswith("# Dockyard bundle")
    assert run(fake, "init", "--dir", str(tmp_path)) == 1
    assert "already exists" in capsys.readouterr().err


def test_pack_verify_install_health_status_uninstall(
    tmp_path: Path, project: Path, fake: FakeDocker, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    assert run(fake, "pack", "--config", str(project), "--out", str(out)) == 0
    said = capsys.readouterr().out
    archive = out / "demo-1.2.3.dockyard"
    assert "SHA-256:" in said and "different route" in said
    assert run(fake, "verify", str(archive)) == 0
    said = capsys.readouterr().out
    assert "matches the .sha256 file next to it" in said and "verification: OK" in said
    fake.images.clear()  # the other machine
    target = tmp_path / "app"
    with socket.socket() as s:
        s.bind(("127.0.0.1", 47123))
        s.listen()
        assert run(fake, "install", str(archive), "--dir", str(target)) == 0
    said = capsys.readouterr().out
    assert "installed and running" in said and "health web:" in said
    assert run(fake, "status", "--dir", str(target)) == 0
    assert "web" in capsys.readouterr().out
    assert run(fake, "health", "--dir", str(target)) == 3  # the port is closed again
    assert "FAIL web" in capsys.readouterr().out
    assert run(fake, "uninstall", "--dir", str(target), "--remove-images") == 0
    assert "removed images: acme/web:1.0" in capsys.readouterr().out


def test_exit_codes_distinguish_failure_kinds(
    tmp_path: Path, bundle: PackResult, fake: FakeDocker, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(fake, "verify", str(bundle.archive), "--digest", "0" * 64) == 2
    assert "verification failed" in capsys.readouterr().err
    assert run(fake, "verify", str(tmp_path / "missing")) == 1
    fake.images.clear()
    assert run(fake, "install", str(bundle.archive), "--dir", str(tmp_path / "a")) == 3  # unhealthy
    err = capsys.readouterr().err
    assert "not healthy" in err and "recent logs" in err


def test_verify_accepts_an_unpacked_directory_and_uses_digest_flag(
    tmp_path: Path, bundle: PackResult, fake: FakeDocker, capsys: pytest.CaptureFixture[str]
) -> None:
    from dockyard.bundle import safe_extract

    root = safe_extract(bundle.archive, tmp_path / "x")
    assert run(fake, "verify", str(root)) == 0
    assert "no expected digest" not in capsys.readouterr().out
    assert run(fake, "verify", str(bundle.archive), "--digest", bundle.digest) == 0
    assert "matches your --digest" in capsys.readouterr().out


def test_install_flags_reach_the_installer(
    tmp_path: Path, bundle: PackResult, fake: FakeDocker, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.images.clear()
    fake.compose = {"services": {"web": {"image": "acme/web:1.0", "privileged": True}}}
    target = str(tmp_path / "app")
    assert run(fake, "install", str(bundle.archive), "--dir", target, "--no-start") == 1
    assert "--accept-risks" in capsys.readouterr().err
    code = run(
        fake, "install", str(bundle.archive), "--dir", target,
        "--accept-risks", "--no-start", "--project", "custom",
    )  # fmt: skip
    assert code == 0 and "installed (not started)" in capsys.readouterr().out
    assert run(fake, "install", str(bundle.archive), "--dir", target, "--no-start") == 1
    assert "--upgrade" in capsys.readouterr().err


def test_source_date_epoch_makes_pack_reproducible(
    tmp_path: Path,
    project: Path,
    fake: FakeDocker,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1793300000")
    run(fake, "pack", "--config", str(project), "--out", str(tmp_path / "a"))
    first = (tmp_path / "a" / "demo-1.2.3.dockyard.sha256").read_text()
    run(fake, "pack", "--config", str(project), "--out", str(tmp_path / "b"))
    assert first == (tmp_path / "b" / "demo-1.2.3.dockyard.sha256").read_text()
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "soon")
    assert run(fake, "pack", "--config", str(project), "--out", str(tmp_path / "c")) == 1
    assert "SOURCE_DATE_EPOCH" in capsys.readouterr().err


def test_pack_surfaces_compose_warnings(
    tmp_path: Path, project: Path, fake: FakeDocker, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.compose = {"services": {"web": {"image": "acme/web:1.0", "network_mode": "host"}}}
    assert run(fake, "pack", "--config", str(project), "--out", str(tmp_path / "o")) == 0
    assert "1 compose warning(s)" in capsys.readouterr().out
    archive = tmp_path / "o" / "demo-1.2.3.dockyard"
    assert run(fake, "verify", str(archive)) == 0
    assert "warning high: web: uses the host network" in capsys.readouterr().out


def test_bad_config_is_a_clean_error(
    tmp_path: Path, fake: FakeDocker, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(fake, "pack", "--config", str(tmp_path / "none.toml")) == 1
    assert capsys.readouterr().err.startswith("dockyard: cannot read")


def test_no_docker_binary_is_a_clean_error(
    tmp_path: Path, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from dockyard.docker import Docker

    code = main(
        ["pack", "--config", str(project), "--out", str(tmp_path / "o")],
        docker=Docker(binary="/no/such/docker"),
    )
    assert code == 1 and "not installed or not on PATH" in capsys.readouterr().err


def test_acceptance_tamper_fixture_fails_only_the_checksum(
    bundle: PackResult, tmp_path: Path, fake: FakeDocker
) -> None:
    """The real-Docker job's corrupted copy must keep the archive structure intact."""
    import importlib.util
    import tarfile

    spec = importlib.util.spec_from_file_location(
        "acceptance", Path(__file__).resolve().parents[2] / "examples" / "acceptance.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bad = tmp_path / "tampered.dockyard"
    altered = module.tamper_payload(bundle.archive, bad)
    with tarfile.open(bundle.archive) as a, tarfile.open(bad) as b:
        assert [(m.name, m.size, m.type) for m in a] == [(m.name, m.size, m.type) for m in b]
    with pytest.raises(VerificationError, match=f"checksum mismatch: {altered.split('/', 1)[1]}"):
        open_bundle(bad)
