from __future__ import annotations

from pathlib import Path

import pytest

from dockyard.config import load_config

from .fakes import FakeDocker

COMPOSE = "services:\n  web:\n    image: acme/web:1.0\n"


@pytest.fixture
def fake() -> FakeDocker:
    f = FakeDocker()
    f.add_image("acme/web:1.0")
    return f


def write_project(root: Path, *, health: str = "", extra: str = "", pull: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "compose.yaml").write_text(COMPOSE, encoding="utf-8")
    (root / "app.env").write_text("GREETING=hello\n", encoding="utf-8")
    (root / "dockyard.toml").write_text(
        '[app]\nname = "demo"\nversion = "1.2.3"\ncompose = "compose.yaml"\n'
        f"[pack]\npull = {'true' if pull else 'false'}\n{extra}\n{health}",
        encoding="utf-8",
    )
    return root / "dockyard.toml"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return write_project(
        tmp_path / "proj",
        extra='extra_files = ["app.env"]',
        health='[[health]]\nname = "web"\nkind = "tcp"\nport = 47123\ntimeout = 0\n',
    )


@pytest.fixture
def bundle(tmp_path: Path, project: Path, fake: FakeDocker):  # type: ignore[no-untyped-def]
    from dockyard.bundle import pack

    result = pack(
        load_config(project), fake.docker(), tmp_path / "out", source_date_epoch=1_793_300_000
    )
    return result
