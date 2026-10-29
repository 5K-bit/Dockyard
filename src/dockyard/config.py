"""``dockyard.toml``: what to bundle and how to know it works afterwards."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import DockyardError
from .manifest import HEALTH_KINDS, HealthSpec, safe_relpath, valid_app_name, valid_version

CONFIG_NAME = "dockyard.toml"

TEMPLATE = """\
# Dockyard bundle definition. Run `dockyard pack` next to this file.

[app]
name = "myapp"            # lower-case letters, digits, . _ -
version = "1.0.0"
compose = "compose.yaml"  # every service needs an `image:`; Dockyard bundles those images

[pack]
pull = false              # true: `docker pull` each image first (needs network on THIS machine)
extra_files = []          # files next to the compose file to ship with it, e.g. ["app.env"]

# What must be true after install. Kinds: http, tcp, container, command.
[[health]]
name = "web"
kind = "http"
url = "http://127.0.0.1:8080/health"
expect_status = 200
timeout = 60
"""

_APP_KEYS = {"name", "version", "compose"}
_PACK_KEYS = {"pull", "extra_files"}
_HEALTH_KEYS = {
    "http": {"name", "kind", "timeout", "url", "expect_status", "expect_body"},
    "tcp": {"name", "kind", "timeout", "host", "port"},
    "container": {"name", "kind", "timeout", "service"},
    "command": {"name", "kind", "timeout", "argv"},
}
_HEALTH_REQUIRED = {"http": {"url"}, "tcp": {"port"}, "container": {"service"}, "command": {"argv"}}


@dataclass(frozen=True)
class Config:
    base_dir: Path
    name: str
    version: str
    compose: Path
    pull: bool = False
    extra_files: list[str] = field(default_factory=list)
    health: list[HealthSpec] = field(default_factory=list)


def _table(data: dict[str, Any], key: str, allowed: set[str], required: bool) -> dict[str, Any]:
    value = data.get(key)
    if value is None:
        if required:
            raise DockyardError(f"{CONFIG_NAME}: missing [{key}] section")
        return {}
    if not isinstance(value, dict):
        raise DockyardError(f"{CONFIG_NAME}: [{key}] must be a table")
    unknown = set(value) - allowed
    if unknown:
        raise DockyardError(
            f"{CONFIG_NAME}: unknown key(s) in [{key}]: {', '.join(sorted(unknown))}"
        )
    return value


def load_config(path: Path) -> Config:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DockyardError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise DockyardError(f"{path.name} is not valid TOML: {exc}") from exc
    unknown_top = set(data) - {"app", "pack", "health"}
    if unknown_top:
        raise DockyardError(f"{CONFIG_NAME}: unknown section(s): {', '.join(sorted(unknown_top))}")
    app = _table(data, "app", _APP_KEYS, True)
    pack = _table(data, "pack", _PACK_KEYS, False)
    name, version = app.get("name"), app.get("version")
    if not isinstance(name, str) or not valid_app_name(name):
        raise DockyardError(
            f"{CONFIG_NAME}: app.name must be lower-case letters, digits, . _ - (got {name!r})"
        )
    if not isinstance(version, str) or not valid_version(version):
        raise DockyardError(f"{CONFIG_NAME}: app.version is missing or has unusual characters")
    base = path.resolve().parent
    compose = (base / str(app.get("compose", "compose.yaml"))).resolve()
    if not compose.is_file():
        raise DockyardError(f"compose file not found: {compose}")
    pull = pack.get("pull", False)
    extras = pack.get("extra_files", [])
    if not isinstance(pull, bool):
        raise DockyardError(f"{CONFIG_NAME}: pack.pull must be true or false")
    if not isinstance(extras, list) or not all(isinstance(e, str) for e in extras):
        raise DockyardError(f"{CONFIG_NAME}: pack.extra_files must be a list of paths")
    cleaned: list[str] = []
    for extra in extras:
        target = (compose.parent / extra).resolve()
        try:
            rel = target.relative_to(compose.parent).as_posix()
        except ValueError as exc:
            raise DockyardError(
                f"extra file {extra!r} is outside the compose file's folder"
            ) from exc
        if not target.is_file():
            raise DockyardError(f"extra file not found: {extra}")
        try:
            cleaned.append(safe_relpath(rel))
        except Exception as exc:
            raise DockyardError(f"extra file {extra!r} has an unsupported name") from exc
    health_raw = data.get("health", [])
    if not isinstance(health_raw, list):
        raise DockyardError(f"{CONFIG_NAME}: [[health]] must be an array of tables")
    health: list[HealthSpec] = []
    seen: set[str] = set()
    for item in health_raw:
        health.append(_health(item, seen))
    return Config(base, name, version, compose, pull, sorted(set(cleaned)), health)


def _health(item: Any, seen: set[str]) -> HealthSpec:
    if not isinstance(item, dict):
        raise DockyardError(f"{CONFIG_NAME}: each [[health]] entry must be a table")
    kind = item.get("kind")
    if kind not in HEALTH_KINDS:
        raise DockyardError(f"{CONFIG_NAME}: health kind must be one of {', '.join(HEALTH_KINDS)}")
    unknown = set(item) - _HEALTH_KEYS[kind]
    if unknown:
        raise DockyardError(
            f"{CONFIG_NAME}: unknown health key(s) for {kind}: {', '.join(sorted(unknown))}"
        )
    missing = _HEALTH_REQUIRED[kind] - set(item)
    if missing:
        raise DockyardError(
            f"{CONFIG_NAME}: health check {kind} needs {', '.join(sorted(missing))}"
        )
    name = item.get("name", kind)
    if not isinstance(name, str) or not name or name in seen:
        raise DockyardError(f"{CONFIG_NAME}: health check names must be unique non-empty strings")
    seen.add(name)
    timeout = item.get("timeout", 60)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int | float)
        or not 0 <= timeout <= 3600
    ):
        raise DockyardError(f"{CONFIG_NAME}: health timeout must be between 0 and 3600 seconds")
    params = {k: v for k, v in item.items() if k not in {"name", "kind", "timeout"}}
    if kind == "tcp" and (not isinstance(params["port"], int) or not 0 < params["port"] < 65536):
        raise DockyardError(f"{CONFIG_NAME}: tcp port must be 1-65535")
    if kind == "command" and (
        not isinstance(params["argv"], list)
        or not params["argv"]
        or not all(isinstance(a, str) for a in params["argv"])
    ):
        raise DockyardError(f"{CONFIG_NAME}: command argv must be a non-empty list of strings")
    return HealthSpec(name, kind, params, float(timeout))
