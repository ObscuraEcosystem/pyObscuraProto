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

## 3. Running Tests

Tests are written using `pytest` and `pytest-asyncio`.

1.  **Run all tests:**
    ```bash
    pytest
    ```
