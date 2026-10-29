# Evidence

Recorded on 2026-10-29 in the project's Linux test environment (Docker with the vfs storage driver, Python 3.13).

- `test-run.txt`: the full test run with coverage.
- `acceptance-transcript.txt`: `examples/acceptance.py` end to end with real Docker: build, pack, delete the image, install from the unpacked bundle with the bundled `dockyard.pyz` in a clean virtual environment, check the app answers, then show a corrupted bundle and a wrong digest are refused. Paths are scrubbed; image digests differ from run to run because Docker builds are not reproducible.

What this does not show: a run between two physical computers, a multi-service application, Windows, or CI. GitHub Actions had not been run when this was written.
