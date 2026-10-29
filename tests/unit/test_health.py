from __future__ import annotations

import socket
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from dockyard.errors import DockyardError
from dockyard.health import run_checks
from dockyard.manifest import HealthSpec

from ..fakes import FakeDocker


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
            return
        code = {"/ok": 200, "/down": 503}.get(self.path, 404)
        body = b"all systems ok"
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def go(spec: HealthSpec, **kw: Any):  # type: ignore[no-untyped-def]
    return run_checks(
        [spec], docker=kw.pop("docker", None), project="p", compose_file=Path("c.yaml"), **kw
    )[0]


def http(path: str, server: str, **params: Any) -> HealthSpec:
    return HealthSpec("h", "http", {"url": server + path, **params}, 0)


def test_http_pass_and_body_match(server: str) -> None:
    assert go(http("/ok", server, expect_body="systems ok")).ok


def test_http_status_and_body_mismatches_say_why(server: str) -> None:
    assert "status 503, expected 200" in go(http("/down", server)).detail
    assert go(http("/down", server, expect_status=503)).ok
    assert "does not contain" in go(http("/ok", server, expect_body="nope")).detail


def test_redirects_are_not_followed(server: str) -> None:
    result = go(http("/redirect", server))
    assert not result.ok and "302" in result.detail


def test_http_refuses_other_schemes_and_reports_connection_errors() -> None:
    assert "unsupported URL" in go(HealthSpec("h", "http", {"url": "file:///etc/passwd"}, 0)).detail
    assert not go(HealthSpec("h", "http", {"url": "http://127.0.0.1:1/"}, 0)).ok


def test_tcp_open_and_closed() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert go(HealthSpec("t", "tcp", {"port": port}, 0)).ok
    assert not go(HealthSpec("t", "tcp", {"port": port}, 0)).ok


PY_OK = [sys.executable, "-c", "raise SystemExit(0)"]
PY_FAIL = [sys.executable, "-c", "raise SystemExit(1)"]


def test_command_checks_need_explicit_permission() -> None:
    spec = HealthSpec("c", "command", {"argv": PY_OK}, 0)
    with pytest.raises(DockyardError, match="--allow-command-checks"):
        go(spec)
    assert go(spec, allow_command=True).ok
    assert not go(HealthSpec("c", "command", {"argv": PY_FAIL}, 0), allow_command=True).ok
    missing = go(HealthSpec("c", "command", {"argv": ["/no/such/prog"]}, 0), allow_command=True)
    assert not missing.ok and missing.detail.startswith("/no/such/prog")
    bad = go(HealthSpec("c", "command", {"argv": "true"}, 0), allow_command=True)
    assert "non-empty list" in bad.detail


def test_command_checks_do_not_use_a_shell() -> None:
    marker = "$(touch /tmp/never)"
    argv = [
        sys.executable,
        "-c",
        "import sys; sys.exit(0 if sys.argv[1] == sys.argv[2] else 1)",
        marker,
        marker,
    ]
    assert go(HealthSpec("c", "command", {"argv": argv}, 0), allow_command=True).ok  # literal text


def test_container_check(fake: FakeDocker) -> None:
    spec = HealthSpec("c", "container", {"service": "web"}, 0)
    assert go(spec, docker=fake.docker()).ok
    fake.ps_rows = [{"Service": "web", "Name": "w-1", "State": "restarting"}]
    assert "restarting" in go(spec, docker=fake.docker()).detail
    fake.ps_rows = [{"Service": "web", "Name": "w-1", "State": "running", "Health": "unhealthy"}]
    assert not go(spec, docker=fake.docker()).ok
    fake.ps_rows = [{"Service": "web", "Name": "w-1", "State": "running", "Health": "healthy"}]
    assert "healthy" in go(spec, docker=fake.docker()).detail
    fake.ps_rows = []
    assert "no containers" in go(spec, docker=fake.docker()).detail
    with pytest.raises(DockyardError, match="needs Docker"):
        go(spec)


def test_checks_retry_until_they_pass_or_time_out() -> None:
    attempts: list[float] = []
    now = [0.0]

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        attempts.append(seconds)
        now[0] += seconds

    spec = HealthSpec("t", "tcp", {"port": 1}, 10)  # nothing listens on port 1
    result = go(spec, clock=clock, sleep=sleep, interval=3)
    assert not result.ok and attempts == [3, 3, 3, 3] and result.seconds >= 10
