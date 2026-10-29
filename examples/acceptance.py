"""Pack, ship and install the demo app as if onto a second machine, with real Docker.

Needs a working Docker daemon and Go (or an existing ``dockyard-demo/api:1.0`` image).

    python examples/acceptance.py --out docs/evidence

What it does, and what it proves:

1. builds the demo image and packs it into a bundle;
2. deletes the image and every container, so the "target" has nothing;
3. unpacks the bundle into a fresh directory and runs the bundled ``dockyard.pyz`` in a clean
   virtual environment that does not have Dockyard installed;
4. checks the app answers on its port, then corrupts a copy of the bundle and shows it is
   refused before anything is loaded.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo-app"
IMAGE = "dockyard-demo/api:1.0"


SCRUB: dict[str, str] = {str(ROOT): ".", sys.executable: "python3"}


def scrub(text: str) -> str:
    for needle, replacement in SCRUB.items():
        text = text.replace(needle, replacement)
    return text


def sh(argv: list[str], log: list[str], *, cwd: Path | None = None, check: bool = True) -> str:
    log.append(f"$ {scrub(' '.join(argv))}")
    done = subprocess.run(argv, capture_output=True, text=True, cwd=cwd, check=False)  # noqa: S603
    text = scrub((done.stdout + done.stderr).strip())
    if text:
        log.append(text)
    if check and done.returncode != 0:
        raise SystemExit("\n".join(log) + f"\nFAILED with exit code {done.returncode}")
    log.append(f"[exit {done.returncode}]")
    return done.stdout


def build_demo_image(work: Path, log: list[str]) -> None:
    """Build the demo image. Uses the host Go toolchain when present, so no registry is needed."""
    if shutil.which("go"):
        build = work / "build"
        shutil.copytree(DEMO, build)
        env = {**os.environ, "CGO_ENABLED": "0"}
        subprocess.run(["go", "build", "-o", "app", "main.go"], cwd=build, env=env, check=True)  # noqa: S603, S607
        (build / "Dockerfile").write_text(
            'FROM scratch\nCOPY app /app\nEXPOSE 8080\nENTRYPOINT ["/app"]\n'
        )
        sh(["docker", "build", "-q", "-t", IMAGE, str(build)], log)
    else:
        sh(["docker", "build", "-q", "-t", IMAGE, str(DEMO)], log)


def run_scenario() -> str:
    """Run the whole scenario against the local Docker daemon and return the transcript."""
    log: list[str] = []
    with tempfile.TemporaryDirectory(prefix="dockyard-acceptance-") as tmp:
        work = Path(tmp)
        SCRUB[str(work)] = "<work>"
        dockyard = [sys.executable, "-m", "dockyard"]
        env_note = "SOURCE_DATE_EPOCH is not set here; digests differ on every run"
        log.append(f"# {env_note}")
        build_demo_image(work, log)

        sh([*dockyard, "pack", "--out", str(work / "dist")], log, cwd=DEMO)
        archive = work / "dist" / "demo-1.0.0.dockyard"
        sh([*dockyard, "verify", str(archive)], log)

        log.append("# --- the other machine: no image, no containers, no Dockyard ---")
        subprocess.run(
            ["docker", "rm", "-f", "dockyard-demo-api-1"], capture_output=True, check=False
        )  # noqa: S603, S607
        sh(["docker", "rmi", "-f", IMAGE], log)
        machine = work / "machine2"
        machine.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(machine, filter="data")
        bundle_dir = machine / "demo-1.0.0"
        venv.create(work / "clean-venv", with_pip=False)
        python = str(work / "clean-venv" / ("Scripts" if os.name == "nt" else "bin") / "python")
        sh([python, "-c", "import dockyard"], log, check=False)  # expected to fail: not installed

        target = work / "install"
        pyz = str(bundle_dir / "dockyard.pyz")
        sh([python, pyz, "verify", str(bundle_dir)], log)
        sh([python, pyz, "install", str(bundle_dir), "--dir", str(target)], log)
        with urllib.request.urlopen("http://127.0.0.1:18080/", timeout=5) as resp:  # noqa: S310
            log.append(f"$ curl http://127.0.0.1:18080/\n{resp.read().decode().strip()}")
        sh([python, pyz, "status", "--dir", str(target)], log)
        sh([python, pyz, "health", "--dir", str(target)], log)

        log.append("# --- a corrupted copy is refused before anything is loaded ---")
        sh([python, pyz, "uninstall", "--dir", str(target), "--remove-images"], log)
        data = bytearray(archive.read_bytes())
        data[len(data) // 2] ^= 0xFF
        bad = work / "dist" / "tampered.dockyard"
        bad.write_bytes(bytes(data))
        sh([*dockyard, "install", str(bad), "--dir", str(work / "install2")], log, check=False)
        images = sh(["docker", "images", "-q", IMAGE], log).strip()
        log.append(f"# image present after refused install: {'yes' if images else 'no'}")
        sh([*dockyard, "verify", str(archive), "--digest", "0" * 64], log, check=False)
    return "\n".join(log) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    transcript = run_scenario()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "acceptance-transcript.txt").write_text(transcript, encoding="utf-8")
    print(f"wrote {args.out / 'acceptance-transcript.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
