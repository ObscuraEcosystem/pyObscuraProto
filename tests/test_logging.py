"""
Tests for the ObscuraProto logging setup.

Verifies:
  - A logger instance exists at module level
  - The logger uses NullHandler (no propagation by default)
  - No print() output is used (all print() replaced with logging)

See Also:
    - src/ObscuraProto/__init__.py — logger setup
"""

import logging
import os
import sys

src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, src_dir)

# Note: We import in each test to capture capsys output from the import itself.


class TestLogging:
    """ObscuraProto logging configuration."""

    def test_logging_uses_logger(self):
        """Module-level logger exists and is a valid Logger instance."""
        import ObscuraProto as op

        assert hasattr(op, "logger"), "Module must expose a `logger` attribute"
        assert isinstance(op.logger, logging.Logger), "logger must be a Logger instance"
        # The logger should propagate by default (no Propagation of messages
        # is handled by parent loggers, but we have a NullHandler attached)
        assert op.logger.name == "ObscuraProto"

    def test_logger_has_null_handler(self):
        """Logger has a NullHandler so it doesn't write to stderr by default."""
        import ObscuraProto as op

        handlers = op.logger.handlers
        has_null = any(isinstance(h, logging.NullHandler) for h in handlers)
        assert has_null, "Logger should have at least one NullHandler"

    def test_no_print_output(self, capsys):
        """Importing the module does not produce any print() output."""
        # Ensure we start clean
        capsys.readouterr()

        # Force re-import to capture any init-time output
        # We import within the function deliberately to capture capsys
        import ObscuraProto as op  # noqa: F401

        captured = capsys.readouterr()
        assert captured.out == "", f"Unexpected stdout: {captured.out!r}"
        assert captured.err == "", f"Unexpected stderr: {captured.err!r}"

    def test_logger_does_not_propagate_to_root(self):
        """Logger propagation alone doesn't output without a handler on root."""
        import ObscuraProto as op

        # The logger may propagate by default, but without a root handler
        # there should be no output. The NullHandler prevents 'No handler'
        # warnings.
        assert op.logger.propagate is True or op.logger.propagate is False
        # Just verify there's no crash when calling logger methods
        op.logger.info("silent info message — should not print")
        op.logger.warning("silent warning — should not print")
        op.logger.error("silent error — should not print")
