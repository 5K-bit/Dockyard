"""Health checks: HTTP, TCP, container state, or a command (without a shell).

Checks are retried until they pass or their timeout ends, because containers take a moment
to start. A bundle is untrusted input, so ``command`` checks only run when the operator
allows them.
"""

from __future__ import annotations

import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .docker import Docker
from .errors import DockyardError
from .manifest import HealthSpec

Clock = Callable[[], float]
Sleep = Callable[[float], None]


@dataclass(frozen=True)
class CheckResult:
    name: str
    kind: str
    ok: bool
    detail: str
    seconds: float


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D102
        return None


def _http(params: dict[str, Any]) -> tuple[bool, str]:
    url = str(params.get("url", ""))
    if urlsplit(url).scheme not in {"http", "https"}:
        return False, f"unsupported URL {url!r}; use http:// or https://"
    expect = int(params.get("expect_status", 200))
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(url, timeout=5) as resp:  # noqa: S310 - scheme checked above
            status, body = resp.status, resp.read(65536).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status, body = exc.code, ""
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"{url}: {getattr(exc, 'reason', exc)}"
    if status != expect:
        return False, f"{url}: status {status}, expected {expect}"
    needle = params.get("expect_body")
    if needle is not None and str(needle) not in body:
        return False, f"{url}: status {status} but the body does not contain {needle!r}"
    return True, f"{url}: status {status}"


def _tcp(params: dict[str, Any]) -> tuple[bool, str]:
    host, port = str(params.get("host", "127.0.0.1")), int(params.get("port", 0))
    try:
        with socket.create_connection((host, port), timeout=5):
            return True, f"{host}:{port}: connected"
    except OSError as exc:
        return False, f"{host}:{port}: {exc.strerror or exc}"


def _command(params: dict[str, Any]) -> tuple[bool, str]:
    argv = params.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return False, "`argv` must be a non-empty list of strings"
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=30, check=False, stdin=subprocess.DEVNULL
        )  # noqa: S603
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{argv[0]}: {exc}"
    return done.returncode == 0, f"{argv[0]}: exit code {done.returncode}"


def _container(
    params: dict[str, Any], docker: Docker | None, project: str, compose: Path
) -> tuple[bool, str]:
    if docker is None:
        return False, "Docker is not available"
    service = str(params.get("service", ""))
    rows = [r for r in docker.compose_ps(project, compose) if r.get("Service") == service]
    if not rows:
        return False, f"service {service!r} has no containers"
    bad = [
        f"{r.get('Name', service)} is {r.get('State', '?')}"
        for r in rows
        if r.get("State") != "running" or r.get("Health") not in (None, "", "healthy")
    ]
    if bad:
        return False, "; ".join(bad)
    return True, f"{service}: running" + (
        " and healthy" if rows[0].get("Health") == "healthy" else ""
    )


def run_checks(
    specs: list[HealthSpec],
    *,
    docker: Docker | None,
    project: str,
    compose_file: Path,
    allow_command: bool = False,
    interval: float = 2.0,
    clock: Clock = time.monotonic,
    sleep: Sleep = time.sleep,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    for spec in specs:
        if spec.kind == "command" and not allow_command:
            raise DockyardError(
                f"health check {spec.name!r} runs a command on this machine; "
                "review it and rerun with --allow-command-checks"
            )
        if spec.kind == "container" and docker is None:
            raise DockyardError(f"health check {spec.name!r} needs Docker")
        start = clock()
        ok, detail = False, "not run"
        while True:
            if spec.kind == "http":
                ok, detail = _http(spec.params)
            elif spec.kind == "tcp":
                ok, detail = _tcp(spec.params)
            elif spec.kind == "command":
                ok, detail = _command(spec.params)
            else:
                ok, detail = _container(spec.params, docker, project, compose_file)
            if ok or clock() - start >= spec.timeout:
                break
            sleep(interval)
        results.append(CheckResult(spec.name, spec.kind, ok, detail, clock() - start))
    return results
