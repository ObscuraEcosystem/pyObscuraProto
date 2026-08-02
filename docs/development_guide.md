# Development Guide for pyObscuraProto

This document outlines the steps to set up the development environment, build the C++ extension, run tests, and execute examples for the `pyObscuraProto` Python wrapper.

## 1. Setup Development Environment

It is highly recommended to use a Python virtual environment to manage project dependencies.

1.  **Create a virtual environment:**
    ```bash
    python3 -m venv .venv
    ```

2.  **Activate the virtual environment:**
    *   On Linux/macOS:
        ```bash
        source .venv/bin/activate
        ```
    *   On Windows:
        ```bash
        .venv\Scripts\activate
        ```

3.  **Install development dependencies:**
    ```bash
    .venv/bin/pip install -r dev-requirements.txt
    ```

## 2. Building the C++ Extension

The `pyObscuraProto` package includes a C++ extension built using CMake and `pybind11`. Ensure CMake is installed on your system before proceeding.

1.  **Build and install the package in editable mode:**
    This command will build the C++ extension and install the Python package in an editable mode, allowing changes to Python files without re-installation.
    ```bash
    .venv/bin/pip install -e .
    ```
    If you only need to build the C++ extension without installing the Python package in editable mode, you can use:
    ```bash
    .venv/bin/python setup.py build_ext --inplace
    ```

### C++ Core Version

The C++ core `ObscuraProto` is fetched via CMake `FetchContent`; the pinned version is `v1.1.1` (`GIT_TAG` in `CMakeLists.txt`). This is the only tag that provides the seed API (`keypair_from_seed` / `derive_public_key`).

When the pin is bumped, force a clean rebuild of the C++ sources — `FetchContent` caches the previously fetched sources in the build directory, so an incremental rebuild may keep using the old version:

```bash
rm -rf build/
.venv/bin/python setup.py build_ext --inplace   # or: .venv/bin/pip install -e .
```

## 3. Running Tests

Tests are written using `pytest` and `pytest-asyncio`.

1.  **Run all tests:**
    ```bash
    pytest
    ```

## 4. audit_p0 Scenario Audit

`tests/audit_p0/run_audit.py` launches standalone scenario scripts in subprocesses, applies an external timeout, and classifies each run as `PASS` / `FAIL` / `HANG` / `UNKNOWN`. The observed status of every scenario is frozen in the `EXPECTED` table inside `run_audit.py`; the runner exits `1` if any scenario diverges from `EXPECTED`. In CI this runs as the `audit` job of `autotests.yml` (ubuntu-latest, parallel to `build-and-test`, `timeout-minutes: 25`).

### Running locally

```bash
python tests/audit_p0/run_audit.py
```

The interpreter is resolved in priority order: explicit `AUDIT_PYTHON` override → the interpreter running the script (`sys.executable`) → `.venv` interpreter. To force a specific interpreter:

```bash
AUDIT_PYTHON=/path/to/python tests/audit_p0/run_audit.py
```

Exit codes: `0` — every scenario matches `EXPECTED`; `1` — at least one divergence (this is what fails the CI `audit` job).

### Updating EXPECTED

- The rule: change a scenario and its `EXPECTED` entry in **one commit** — never flip `EXPECTED` separately from the scenario code that justifies it.
- `UNKNOWN` is **always red**: a scenario reporting `UNKNOWN` fails the run regardless of the `EXPECTED` value.
- `HANG` is detected from faulthandler thread dumps: CPython 3.13+ prints `Thread 0x... (most recent call first):`, and the structurally unique marker `"(most recent call first)"` is what the runner greps for. Do not rely on exit codes alone — a scenario may also print its own `RESULT: HANG ...` line.
- The current `FAIL` entries in `EXPECTED` are transitional placeholders with `TODO` — the scenarios themselves are not finished (stubs use `os._exit` / `IndentationError`), so the `audit` job is expected to stay red until they are implemented.
