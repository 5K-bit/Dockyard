"""A fake ``docker`` command line, so failure paths can be tested without a daemon."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dockyard.docker import Docker


def image_id(ref: str) -> str:
    return "sha256:" + hashlib.sha256(ref.encode()).hexdigest()


class FakeDocker:
    """Records every call and answers like Docker would. ``.docker()`` wraps it."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.images: dict[str, str] = {}  # ref -> id (what the "daemon" has)
        self.platform = "linux/x86_64"
        self.daemon_up = True
        self.compose_available = True
        self.compose: dict[str, Any] = {
            "services": {
                "web": {
                    "image": "acme/web:1.0",
                    "ports": [{"published": "8080", "host_ip": "127.0.0.1"}],
                }
            }
        }
        self.ps_rows: list[dict[str, Any]] = [
            {"Service": "web", "Name": "demo-web-1", "State": "running"}
        ]
        self.load_gives_wrong_id = False
        self.up_fails = False
        self.logs_text = "web | listening"
        self.image_arch = "amd64"

    def docker(self) -> Docker:
        return Docker(self)

    def add_image(self, ref: str) -> None:
        self.images[ref] = image_id(ref)

    def names(self) -> list[str]:
        return [" ".join(c[1:3]) for c in self.calls]

    def __call__(self, argv: Sequence[str], timeout: float | None) -> tuple[int, str, str]:
        args = list(argv)[1:]
        self.calls.append(list(argv))
        if args[:1] == ["version"]:
            return (
                (0, "29.0.0", "")
                if self.daemon_up
                else (1, "", "Cannot connect to the Docker daemon")
            )
        if args[:2] == ["compose", "version"]:
            return (0, "v2", "") if self.compose_available else (1, "", "unknown command")
        if args[:1] == ["info"]:
            return 0, self.platform, ""
        if args[:2] == ["image", "inspect"]:
            ref = args[2]
            if ref not in self.images:
                return 1, "[]", "No such image"
            os_name, _, arch = self.platform.partition("/")
            data = [{"Id": self.images[ref], "Os": os_name, "Architecture": self.image_arch}]
            return 0, json.dumps(data), ""
        if args[:1] == ["pull"]:
            self.add_image(args[1])
            return 0, "", ""
        if args[:1] == ["save"]:
            ref = args[args.index("--output") + 2]
            Path(args[args.index("--output") + 1]).write_text(
                f"{ref} {self.images[ref]}\n" + "x" * 1000, encoding="utf-8"
            )
            return 0, "", ""
        if args[:1] == ["load"]:
            first = Path(args[2]).read_text(encoding="utf-8").splitlines()[0]
            ref, ident = first.split(" ")
            self.images[ref] = "sha256:" + "0" * 64 if self.load_gives_wrong_id else ident
            return 0, f"Loaded image: {ref}", ""
        if args[:2] == ["image", "rm"]:
            return (0, "", "") if self.images.pop(args[2], None) else (1, "", "no such image")
        if args[:1] == ["compose"]:
            action = args[args.index("--project-directory") + 2]
            if action == "config":
                return 0, json.dumps(self.compose), ""
            if action == "up":
                return (1, "", "port is already allocated") if self.up_fails else (0, "", "")
            if action == "down":
                return 0, "", ""
            if action == "ps":
                return 0, "\n".join(json.dumps(r) for r in self.ps_rows), ""
            if action == "logs":
                return 0, self.logs_text, ""
        return 1, "", f"fake docker: unhandled {args}"
