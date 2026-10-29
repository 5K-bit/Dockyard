"""Build ``dockyard.pyz``: this package as a single runnable file.

The bundle carries it, so the target machine needs only Python 3.11+ and Docker, not a
Dockyard installation.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zipapp
from pathlib import Path

_MAIN = "from dockyard.cli import main\n\nraise SystemExit(main())\n"
# 1981-01-01: inside the zip format's range in every time zone, and fixed for reproducible builds
_EPOCH = 347155200


def build_pyz(target: Path) -> None:
    package = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "app"
        shutil.copytree(
            package, staging / "dockyard", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        (staging / "__main__.py").write_text(_MAIN, encoding="utf-8")
        for path in [staging, *staging.rglob("*")]:
            os.utime(path, (_EPOCH, _EPOCH))
        zipapp.create_archive(staging, target, compressed=True)
