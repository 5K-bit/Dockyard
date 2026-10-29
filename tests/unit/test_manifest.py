from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from dockyard.errors import VerificationError
from dockyard.manifest import (
    FileEntry,
    HealthSpec,
    ImageEntry,
    Manifest,
    parse_manifest,
    safe_relpath,
    valid_app_name,
    valid_version,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
H = "a" * 64
ID = "sha256:" + "b" * 64


def sample() -> Manifest:
    return Manifest(
        app_name="demo",
        app_version="1.2.3",
        created="2026-10-29T19:00:00Z",
        dockyard_version="0.1.0",
        compose=FileEntry("compose.yaml", H),
        images=[ImageEntry("acme/web:1.0", "images/acme_web_1.0.tar", H, 10, ID, "linux", "amd64")],
        extra_files=[FileEntry("files/app.env", H)],
        health=[HealthSpec("web", "http", {"url": "http://127.0.0.1:8080/health"}, 30.0)],
        warnings=["medium: web: publishes port 8080 on all interfaces"],
    )


def as_dict() -> dict[str, Any]:
    return json.loads(sample().to_json())


def test_round_trip_and_golden() -> None:
    text = sample().to_json()
    assert parse_manifest(text) == sample()
    path = FIXTURES / "manifest.golden.json"
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(text, encoding="utf-8", newline="\n")
    assert text == path.read_text(encoding="utf-8")


def test_all_files_covers_compose_images_and_extras() -> None:
    assert set(sample().all_files()) == {"compose.yaml", "images/acme_web_1.0.tar", "files/app.env"}


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/etc/passwd",
        "../x",
        "a/../b",
        "a//b",
        "a\\b",
        "a/./b",
        "C:/x",
        " x",
        "x ",
        ".hidden",
        "a/.hidden",
        "a\x00b",
        "images/",
        "-rf",
    ],
)
def test_unsafe_paths_are_rejected(path: str) -> None:
    with pytest.raises(VerificationError):
        safe_relpath(path)


@pytest.mark.parametrize("path", ["compose.yaml", "images/a_b-1.0.tar", "files/conf/app.env"])
def test_ordinary_paths_pass(path: str) -> None:
    assert safe_relpath(path) == path


def test_name_and_version_rules() -> None:
    assert valid_app_name("my-app_1.0") and not valid_app_name("My App")
    assert not valid_app_name("-x") and not valid_app_name("a" * 64)
    assert valid_version("1.2.3+build.5") and not valid_version("1 2") and not valid_version("")


def mutate(change: Any) -> str:
    data = as_dict()
    change(data)
    return json.dumps(data)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(schema=2), "schema 2"),
        (lambda d: d.pop("app"), "missing 'app'"),
        (lambda d: d["app"].update(name="Bad Name"), "invalid app name"),
        (lambda d: d["app"].update(version="1 2"), "invalid app version"),
        (lambda d: d["compose"].update(file="../compose.yaml"), "unsafe path"),
        (lambda d: d["compose"].update(sha256="xyz"), "SHA-256"),
        (lambda d: d["images"][0].update(file="compose.yaml"), "images/"),
        (lambda d: d["images"][0].update(file="images/sub/x.tar"), "directly in images/"),
        (lambda d: d["images"][0].update(image_id="abc"), "invalid image id"),
        (lambda d: d["images"][0].update(size=-1), "negative"),
        (lambda d: d["images"][0].update(size=True), "wrong type"),
        (lambda d: d["images"][0].update(ref="--privileged"), "invalid image reference"),
        (lambda d: d["images"][0].update(ref="a b"), "invalid image reference"),
        (lambda d: d.update(images=[]), "no images"),
        (lambda d: d.update(images=["x"]), "not an object"),
        (lambda d: d["health"][0].update(kind="shell"), "unknown health check kind"),
        (lambda d: d["health"].append("x"), "not an object"),
        (lambda d: d["extra_files"][0].update(path="/abs"), "unsafe path"),
    ],
)
def test_invalid_manifests_are_rejected_with_a_reason(change: Any, message: str) -> None:
    with pytest.raises(VerificationError, match=message):
        parse_manifest(mutate(change))


@pytest.mark.parametrize("text", ["", "not json", "[]", "null", '"x"'])
def test_non_manifest_documents_are_rejected(text: str) -> None:
    with pytest.raises(VerificationError):
        parse_manifest(text)
