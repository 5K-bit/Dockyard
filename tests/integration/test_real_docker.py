"""The lab scenario against a real Docker daemon. Skipped when there is none."""

from __future__ import annotations

import importlib.util
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ACCEPTANCE = Path(__file__).resolve().parents[2] / "examples" / "acceptance.py"


def docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return (
            subprocess.run(
                ["docker", "info"], capture_output=True, timeout=20, check=False
            ).returncode
            == 0
        )  # noqa: S603, S607
    except (OSError, subprocess.TimeoutExpired):
        return False


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(sys.platform == "win32", reason="needs Linux containers"),
    pytest.mark.skipif(not docker_ready(), reason="no Docker daemon"),
    pytest.mark.skipif(
        not (shutil.which("go") or shutil.which("docker")), reason="cannot build the demo"
    ),
]


@pytest.fixture(scope="module")
def transcript() -> str:
    if not port_free(18080):
        pytest.skip("port 18080 is busy")
    spec = importlib.util.spec_from_file_location("acceptance", ACCEPTANCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["acceptance"] = module
    spec.loader.exec_module(module)
    return module.run_scenario()  # type: ignore[no-any-return]


def test_image_is_really_gone_before_the_install(transcript: str) -> None:
    assert "Untagged: dockyard-demo/api:1.0" in transcript or "Deleted:" in transcript
    assert (
        "ModuleNotFoundError: No module named 'dockyard'" in transcript
    )  # no Dockyard on the target


def test_the_bundled_installer_installs_and_the_app_answers(transcript: str) -> None:
    assert "loading dockyard-demo/api:1.0" in transcript
    assert "demo 1.0.0 installed and running" in transcript
    assert "dockyard demo app" in transcript
    assert "ok   api: http://127.0.0.1:18080/health: status 200" in transcript


def test_a_corrupted_bundle_is_refused_and_loads_nothing(transcript: str) -> None:
    # Where the flipped byte lands decides the wording (a failed CRC, a bad header, a checksum
    # mismatch); what matters is that the install is refused with exit 2 and nothing is loaded.
    refused = transcript.split("tampered.dockyard", 1)[1].split("$ docker images", 1)[0]
    assert "dockyard: verification failed:" in refused
    assert "[exit 2]" in refused
    assert "# image present after refused install: no" in transcript


def test_the_archive_digest_is_checked_by_a_system_tool_before_it_is_unpacked(
    transcript: str,
) -> None:
    check = transcript.index("sha256sum -c -")
    assert ": OK" in transcript[check:]
    assert check < transcript.index("-c import dockyard")


def test_a_wrong_expected_digest_is_refused(transcript: str) -> None:
    assert "does not match the expected" in transcript
