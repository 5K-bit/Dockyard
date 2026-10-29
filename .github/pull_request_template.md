## Summary

## Testing
- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] `mypy` passes
- [ ] `python -m pytest --cov` passes
- [ ] Bundle handling changes have a test with a hostile or corrupted bundle
- [ ] No new network calls on the install path (or they are opt-in and covered in `docs/threat-model.md`)
- [ ] Golden files regenerated and reviewed (if manifest output changed)
