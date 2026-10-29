"""A thin wrapper over the ``docker`` command line.

Every call is an argument list run without a shell. The runner is injectable so tests can
exercise failure paths without a daemon; the default runner uses :mod:`subprocess`.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .errors import DockyardError

Runner = Callable[[Sequence[str], float | None], tuple[int, str, str]]

_ARCH = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


def default_runner(argv: Sequence[str], timeout: float | None) -> tuple[int, str, str]:
    try:
        done = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise DockyardError(f"cannot run {argv[0]!r}: it is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise DockyardError(f"{' '.join(argv[:3])} timed out after {timeout:g}s") from exc
    return done.returncode, done.stdout, done.stderr


def normalise_arch(value: str) -> str:
    return _ARCH.get(value.lower(), value.lower())


class Docker:
    def __init__(self, runner: Runner | None = None, binary: str = "docker") -> None:
        self._runner = runner or default_runner
        self.binary = binary

    def run(
        self, args: Sequence[str], *, timeout: float | None = 600, check: bool = True
    ) -> tuple[int, str, str]:
        argv = [self.binary, *args]
        code, out, err = self._runner(argv, timeout)
        if check and code != 0:
            detail = (err or out).strip().splitlines()
            tail = "; ".join(detail[-3:]) if detail else f"exit code {code}"
            raise DockyardError(f"`docker {' '.join(args[:2])}` failed: {tail}")
        return code, out, err

    # -- environment ---------------------------------------------------------

    def check_ready(self) -> None:
        """Fail clearly when Docker or the Compose plugin is missing or the daemon is down."""
        code, _, err = self.run(
            ["version", "--format", "{{.Server.Version}}"], check=False, timeout=30
        )
        if code != 0:
            raise DockyardError(
                "cannot reach the Docker daemon: " + (err.strip().splitlines() or ["no output"])[-1]
            )
        code, _, _ = self.run(["compose", "version"], check=False, timeout=30)
        if code != 0:
            raise DockyardError("the Docker Compose plugin (`docker compose`) is not available")

    def server_platform(self) -> tuple[str, str]:
        _, out, _ = self.run(["info", "--format", "{{.OSType}}/{{.Architecture}}"], timeout=30)
        os_name, _, arch = out.strip().partition("/")
        return os_name, normalise_arch(arch)

    # -- images --------------------------------------------------------------

    def inspect_image(self, ref: str) -> dict[str, Any] | None:
        code, out, _ = self.run(["image", "inspect", ref], check=False, timeout=60)
        if code != 0:
            return None
        try:
            data = json.loads(out)
        except ValueError:
            return None
        return data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None

    def pull(self, ref: str) -> None:
        self.run(["pull", ref], timeout=3600)

    def save(self, ref: str, path: Path) -> None:
        self.run(["save", "--output", str(path), ref], timeout=3600)

    def load(self, path: Path) -> None:
        self.run(["load", "--input", str(path)], timeout=3600)

    def remove_image(self, ref: str) -> bool:
        code, _, _ = self.run(["image", "rm", ref], check=False, timeout=120)
        return code == 0

    # -- compose -------------------------------------------------------------

    def _compose(self, project: str | None, compose_file: Path) -> list[str]:
        base = ["compose", "--file", str(compose_file)]
        if project:
            base += ["--project-name", project]
        base += ["--project-directory", str(compose_file.parent)]
        return base

    def compose_config(self, compose_file: Path) -> dict[str, Any]:
        _, out, _ = self.run(
            [*self._compose(None, compose_file), "config", "--format", "json"], timeout=60
        )
        try:
            data = json.loads(out)
        except ValueError as exc:
            raise DockyardError("`docker compose config` did not return JSON") from exc
        if not isinstance(data, dict):
            raise DockyardError("`docker compose config` returned an unexpected document")
        return data

    def compose_up(self, project: str, compose_file: Path) -> None:
        self.run(
            [
                *self._compose(project, compose_file),
                "up",
                "--detach",
                "--no-build",
                "--pull",
                "never",
            ],
            timeout=900,
        )

    def compose_down(self, project: str, compose_file: Path, *, volumes: bool = False) -> None:
        args = [*self._compose(project, compose_file), "down"]
        if volumes:
            args.append("--volumes")
        self.run(args, timeout=600)

    def compose_ps(self, project: str, compose_file: Path) -> list[dict[str, Any]]:
        _, out, _ = self.run(
            [*self._compose(project, compose_file), "ps", "--all", "--format", "json"], timeout=60
        )
        rows: list[dict[str, Any]] = []
        for line in out.splitlines():  # newer Compose prints one JSON object per line
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            rows.extend(item if isinstance(item, list) else [item])
        return [r for r in rows if isinstance(r, dict)]

    def compose_logs(self, project: str, compose_file: Path, tail: int = 20) -> str:
        _, out, err = self.run(
            [*self._compose(project, compose_file), "logs", "--no-color", "--tail", str(tail)],
            check=False,
            timeout=60,
        )
        return (out or err).strip()
