from __future__ import annotations

from pathlib import Path

import pytest

from dockyard.config import TEMPLATE, load_config
from dockyard.errors import DockyardError
from dockyard.risks import HIGH, MEDIUM, audit_compose

from ..conftest import write_project


def test_template_is_a_valid_config(tmp_path: Path) -> None:
    (tmp_path / "compose.yaml").write_text("services: {}\n")
    (tmp_path / "dockyard.toml").write_text(TEMPLATE)
    cfg = load_config(tmp_path / "dockyard.toml")
    assert (cfg.name, cfg.version) == ("myapp", "1.0.0") and cfg.health[0].kind == "http"


def test_a_full_config_loads(project: Path) -> None:
    cfg = load_config(project)
    assert cfg.extra_files == ["app.env"] and cfg.pull is False
    assert cfg.health[0].params == {"port": 47123}


def cfg_text(tmp_path: Path, text: str) -> Path:
    (tmp_path / "compose.yaml").write_text("services: {}\n")
    path = tmp_path / "dockyard.toml"
    path.write_text(text)
    return path


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not toml [", "not valid TOML"),
        ('[app]\nname="x"\nversion="1"\n[bogus]\n', "unknown section"),
        ("[pack]\n", "missing \\[app\\]"),
        ('[app]\nname="X Y"\nversion="1"\n', "app.name"),
        ('[app]\nname="x"\nversion="1 2"\n', "app.version"),
        ('[app]\nname="x"\nversion="1"\nflavour="y"\n', "unknown key"),
        ('[app]\nname="x"\nversion="1"\ncompose="nope.yaml"\n', "not found"),
        ('[app]\nname="x"\nversion="1"\n[pack]\npull="yes"\n', "pack.pull"),
        ('[app]\nname="x"\nversion="1"\n[pack]\nextra_files="a"\n', "list of paths"),
        ('[app]\nname="x"\nversion="1"\n[pack]\nextra_files=["../x"]\n', "outside"),
        ('[app]\nname="x"\nversion="1"\n[pack]\nextra_files=["missing"]\n', "not found"),
        ('[app]\nname="x"\nversion="1"\n[[health]]\nkind="ftp"\n', "kind must be"),
        ('[app]\nname="x"\nversion="1"\n[[health]]\nkind="http"\n', "needs url"),
        (
            '[app]\nname="x"\nversion="1"\n[[health]]\nkind="http"\nurl="u"\nbogus=1\n',
            "unknown health key",
        ),
        ('[app]\nname="x"\nversion="1"\n[[health]]\nkind="tcp"\nport=70000\n', "1-65535"),
        ('[app]\nname="x"\nversion="1"\n[[health]]\nkind="command"\nargv="curl"\n', "argv"),
        ('[app]\nname="x"\nversion="1"\n[[health]]\nkind="command"\nargv=[]\n', "argv"),
        (
            '[app]\nname="x"\nversion="1"\n[[health]]\nkind="tcp"\nport=1\ntimeout=99999\n',
            "timeout",
        ),
        (
            '[app]\nname="x"\nversion="1"\n[[health]]\nkind="tcp"\nport=1\n[[health]]\nkind="tcp"\nport=2\n',
            "unique",
        ),
        ('[app]\nname="x"\nversion="1"\nhealth=3\n', "unknown key"),
    ],
)
def test_bad_configs_say_what_is_wrong(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(DockyardError, match=message):
        load_config(cfg_text(tmp_path, text))


def test_missing_file_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(DockyardError, match="cannot read"):
        load_config(tmp_path / "nope.toml")


def test_nested_extra_files_keep_their_relative_path(tmp_path: Path) -> None:
    path = write_project(tmp_path / "p", extra='extra_files = ["conf/a.env", "app.env"]')
    (path.parent / "conf").mkdir()
    (path.parent / "conf" / "a.env").write_text("A=1")
    assert load_config(path).extra_files == ["app.env", "conf/a.env"]


def svc(**kw: object) -> dict[str, object]:
    return {"services": {"web": {"image": "x", **kw}}}


def msgs(config: dict[str, object], level: str | None = None) -> list[str]:
    return [r.message for r in audit_compose(config) if level in (None, r.level)]


def test_a_plain_service_has_no_findings() -> None:
    assert audit_compose(svc(ports=[{"published": "80", "host_ip": "127.0.0.1"}])) == []


@pytest.mark.parametrize(
    ("kw", "needle"),
    [
        ({"privileged": True}, "privileged"),
        ({"network_mode": "host"}, "host network"),
        ({"pid": "host"}, "PID"),
        ({"ipc": "host"}, "IPC"),
        ({"cap_add": ["SYS_ADMIN"]}, "SYS_ADMIN"),
        ({"cap_add": ["CAP_ALL"]}, "ALL"),
        ({"volumes": [{"type": "bind", "source": "/var/run/docker.sock"}]}, "Docker socket"),
        ({"volumes": [{"type": "bind", "source": "/etc"}]}, "/etc"),
    ],
)
def test_high_risk_settings_are_flagged_high(kw: dict[str, object], needle: str) -> None:
    found = [m for m in msgs(svc(**kw), HIGH) if needle in m]
    assert found, audit_compose(svc(**kw))


def test_medium_findings() -> None:
    config = svc(
        devices=["/dev/ttyUSB0"],
        volumes=[{"type": "bind", "source": "/srv/data"}, {"type": "volume", "source": "v"}, "odd"],
        ports=[{"published": "8080"}],
    )
    assert len(msgs(config, MEDIUM)) == 3
    assert not msgs(config, HIGH)


def test_malformed_configs_do_not_crash() -> None:
    assert audit_compose({}) == [] and audit_compose({"services": "x"}) == []
    assert audit_compose({"services": {"a": "not a dict"}}) == []
