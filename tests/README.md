# Tests

This repo uses a small test pyramid with explicit pytest markers.

## Markers

- `unit`: fast, isolated tests (parsing, extraction, matching, config parsing).
- `integration`: filesystem-level tests (discovery, formatting, writing outputs).
- `e2e`: CLI-level tests (generate/validate/estimate/pack behavior and failure modes).

## Commands

```bash
python -m pytest
python -m pytest -m unit
python -m pytest -m integration
python -m pytest -m e2e
```

## Fixtures

- `tests/fixtures/project_src/src/`: container-layout fixture (regular package, namespace package, top-level module, excluded subtree).
- `tests/fixtures/sample_package/`: symbol extraction fixture focused on Python constructs.
