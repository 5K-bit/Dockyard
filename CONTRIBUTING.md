# Contributing

Thanks for your interest in Dockyard.

## Ways to help

- Report bugs, especially Compose files or Docker versions where pack or install misbehaves (send a **sanitised** example).
- Improve the risk audit, health checks or docs.

## Development

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check . && ruff format --check .
mypy
python -m pytest --cov                            # real-Docker tests run when a daemon exists
python examples/acceptance.py --out /tmp/evidence    # pack, ship, install, health, tamper (needs Docker and Go)
```

## Guidelines

- The core uses only the Python standard library.
- `docker.py` is the only module that starts processes; everything else takes a `Docker` object so tests can use `FakeDocker`.
- Treat every bundle as hostile until verified. No `extractall`, no shell, no network at install.
- Changing the manifest? Bump the schema deliberately and regenerate `tests/fixtures/manifest.golden.json` after review.
- Tests must not rely on a fixed port you do not own, a public registry or the real internet.
- Do not add claims (performance, platform support, security) that a test or recorded evidence does not back up.
- Use clear commit messages and keep pull requests focused.
