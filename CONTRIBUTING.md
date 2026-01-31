# Contributing

## Setup

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

## Quality gates

```bash
python -m ruff check .
python -m mypy --strict src
python -m pytest
```

