"""Flag Compose settings that give a container more reach than an app normally needs.

This works on the resolved configuration (``docker compose config --format json``). It is
a screen, not a security review: it names a few settings that deserve a human look before
code from a bundle is started on a machine. ``high`` findings stop an install unless the
operator accepts them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

HIGH = "high"
MEDIUM = "medium"

_DANGEROUS_CAPS = {"ALL", "SYS_ADMIN", "SYS_PTRACE", "SYS_MODULE", "NET_ADMIN", "DAC_READ_SEARCH"}
_SOCKETS = ("/var/run/docker.sock", "/run/docker.sock")


@dataclass(frozen=True)
class Risk:
    level: str
    service: str
    message: str


def audit_compose(config: dict[str, Any]) -> list[Risk]:
    risks: list[Risk] = []
    services = config.get("services")
    if not isinstance(services, dict):
        return risks
    for name in sorted(services):
        svc = services[name]
        if not isinstance(svc, dict):
            continue
        if svc.get("privileged") is True:
            risks.append(Risk(HIGH, name, "runs privileged (full access to the host's devices)"))
        for key, label in (
            ("network_mode", "host network"),
            ("pid", "host PID namespace"),
            ("ipc", "host IPC namespace"),
            ("userns_mode", "host user namespace"),
        ):
            if svc.get(key) == "host":
                risks.append(Risk(HIGH, name, f"uses the {label}"))
        caps = {str(c).upper().removeprefix("CAP_") for c in svc.get("cap_add") or []}
        if caps & _DANGEROUS_CAPS:
            risks.append(
                Risk(HIGH, name, f"adds capabilities {', '.join(sorted(caps & _DANGEROUS_CAPS))}")
            )
        if svc.get("devices"):
            risks.append(Risk(MEDIUM, name, "maps host devices into the container"))
        for vol in svc.get("volumes") or []:
            if not isinstance(vol, dict) or vol.get("type") != "bind":
                continue
            source = str(vol.get("source", ""))
            if source.rstrip("/") in _SOCKETS or source.endswith("docker.sock"):
                risks.append(
                    Risk(HIGH, name, f"mounts the Docker socket ({source}): control of the host")
                )
            elif source in {
                "/",
                "/etc",
                "/root",
                "/var",
                "/home",
                "/usr",
                "/boot",
                "/proc",
                "/sys",
            }:
                risks.append(Risk(HIGH, name, f"mounts the host directory {source}"))
            else:
                risks.append(Risk(MEDIUM, name, f"mounts the host path {source}"))
        for port in svc.get("ports") or []:
            if isinstance(port, dict) and port.get("published") and not port.get("host_ip"):
                risks.append(
                    Risk(MEDIUM, name, f"publishes port {port['published']} on all interfaces")
                )
    return risks
