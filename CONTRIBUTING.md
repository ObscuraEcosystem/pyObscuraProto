# Contributing to pyObscuraProto

## System Dependencies

- **CMake** (≥ 3.14) — build system
- **libsodium** — cryptographic library used by ObscuraProto

### macOS
```bash
brew install cmake libsodium
```

### Linux (Debian/Ubuntu)
```bash
sudo apt-get update
sudo apt-get install -y cmake libsodium-dev
```

## Development Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/kretoffer/pyObscuraProto.git
   cd pyObscuraProto
   ```

2. Create and activate a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install development dependencies:
   ```bash
   pip install -r dev-requirements.txt
   ```

4. Install pre-commit hooks:
   ```bash
   pre-commit install
   ```

5. Build the project:
   ```bash
   pip install -e .
   ```

## Code Quality Tools

### Ruff (linter & formatter)

Lint check:
```bash
ruff check src/ tests/ examples/
```

Auto-fix lint issues:
```bash
ruff check --fix src/ tests/ examples/
```

Format check:
```bash
ruff format --check src/ tests/ examples/
```

Format code:
```bash
ruff format src/ tests/ examples/
```

### Pyright (type checker)

```bash
pyright src/ tests/ examples/
```

## Pre-commit Hooks

Pre-commit hooks run automatically on `git commit` and include:

1. **ruff** — lints and auto-fixes fixable issues
2. **ruff-format** — ensures code is properly formatted
3. **pyright** — checks for type errors

To run all hooks manually without committing:
```bash
pre-commit run --all-files
```

To bypass hooks temporarily (not recommended):
```bash
git commit --no-verify
```

## CI Pipeline

Every push and pull request triggers GitHub Actions which runs:

1. `ruff check` — lint validation
2. `ruff format --check` — formatting validation
3. Build the C++ extension and install the package
4. `pyright` — type checking
5. `pytest` — test suite

## Code Style Guidelines

- **Line length**: 120 characters
- **Naming**: Follow PEP 8 conventions
- **Imports**: Sorted automatically by Ruff (I rule)
- **Type hints**: Use type annotations for all function signatures
- **Docstrings**: Use Google-style docstrings (auto-formatted by Ruff)

## Testing

Run tests:
```bash
pytest
```

## Pull Request Process

1. Ensure all pre-commit hooks pass
2. Ensure CI passes on your branch
3. Update documentation if needed
4. Request review from maintainers
