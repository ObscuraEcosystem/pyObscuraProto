"""
Tests for the new/refactored _CallbackDispatcher methods.

Covers:
  - wrap_fire_and_forget() — non-blocking async dispatch
  - wrap() with result waiting (explicit behavioral check)
  - No-loop fallback for wrap_fire_and_forget()
  - Auto-detection of a running event loop
  - wrap_identity() — no-loop, async, error, error-handler paths
  - Stream on_data/on_end/on_cancel through fire-and-forget dispatch

See Also:
    - _CallbackDispatcher in src/ObscuraProto/__init__.py
"""

import asyncio
import os
import socket
import sys
import threading
import time

import pytest

src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, src_dir)

try:
    import ObscuraProto as op
    from ObscuraProto import _CallbackDispatcher  # pyright: ignore[reportPrivateUsage]
except ImportError as e:
    pytest.fail(f"Could not import ObscuraProto: {e}", pytrace=False)

# pyright: reportPrivateUsage=false
# pyright: reportOptionalCall=false


def _find_free_port():
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


_PORT_COUNTER = [0]


def _next_port():
    _PORT_COUNTER[0] += 1
    return _find_free_port()


@pytest.fixture(scope="module")
def crypto_init():
    """Ensure Crypto is initialised once per module."""
    op.Crypto.init()


# ===================================================================
# Unit tests for fire-and-forget vs with-result dispatch
# ===================================================================


class TestFireAndForgetVsWithResult:
    """Verify wrap_fire_and_forget vs wrap (with_result) behavior."""

    # -- fire-and-forget does NOT block ---------------------------------

    def test_fire_and_forget_no_block(self):
        """Fire-and-forget returns immediately, does not await coroutine."""
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        completed = threading.Event()

        async def slow_handler():
            await asyncio.sleep(0.5)
            completed.set()
            return 42

        d = _CallbackDispatcher()
        d.attach(loop)

        wrapped = d.wrap_fire_and_forget(slow_handler)
        assert wrapped is not None

        start = time.time()
        result = wrapped()
        elapsed = time.time() - start

        # Must return well before the 0.5s sleep finishes
        assert elapsed < 0.3, f"fire-and-forget blocked for {elapsed:.3f}s"
        assert result is None, "fire-and-forget should return None"

        # The coroutine eventually completes in the background
        assert completed.wait(timeout=2), "Async handler did not complete"

        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()

    # -- wrap (with_result) DOES block and returns value -----------------

    def test_with_result_waits_for_result(self):
        """wrap() waits for the coroutine and returns its result."""
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        async def returns_value():
            await asyncio.sleep(0.1)
            return 42

        d = _CallbackDispatcher()
        d.attach(loop)

        wrapped = d.wrap(returns_value)
        assert wrapped is not None

        start = time.time()
        result = wrapped()
        elapsed = time.time() - start

        assert result == 42
        assert elapsed >= 0.09, f"Did not appear to wait, elapsed={elapsed:.3f}s"

        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()

    # -- fire-and-forget no-loop fallback ---------------------------------

    def test_fire_and_forget_no_loop_fallback(self):
        """Without event loop, fire-and-forget falls back to sync wrap and calls fn."""
        d = _CallbackDispatcher()
        fn = lambda: 42  # noqa: E731
        wrapped = d.wrap_fire_and_forget(fn)
        assert wrapped is not fn, "Should wrap even without loop / error handler"
        assert wrapped() == 42

    # -- fire-and-forget with error handler but no loop ------------------

    def test_fire_and_forget_no_loop_with_error_handler(self):
        """Without loop but with error handler, fire-and-forget wraps for error catching."""
        d = _CallbackDispatcher()
        errors = []
        d.set_error_handler(lambda e: errors.append(e))

        def failing_fn():
            raise ValueError("sync fail")

        wrapped = d.wrap_fire_and_forget(failing_fn)
        assert wrapped is not failing_fn

        result = wrapped()
        assert result is None
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)

    # -- async handler via fire-and-forget with error handler -------------

    def test_fire_and_forget_async_error_forwarded(self):
        """Async error in fire-and-forget mode is forwarded to error handler."""
        errors = []
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        async def failing_async():
            raise RuntimeError("async fire-and-forget error")

        d = _CallbackDispatcher()
        d.set_error_handler(lambda e: errors.append(e))
        d.attach(loop)

        wrapped = d.wrap_fire_and_forget(failing_async)
        assert wrapped is not None

        result = wrapped()
        assert result is None

        # Allow the done callback to fire
        time.sleep(0.2)
        assert len(errors) == 1
        assert isinstance(errors[0], RuntimeError)

        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()


# ===================================================================
# Unit tests for auto-detection of running event loop
# ===================================================================


class TestAutoDetectLoop:
    """Auto-detection of a running event loop in wrap / wrap_fire_and_forget."""

    def test_dispatcher_auto_detects_loop(self):
        """When a loop is running, wrap() auto-detects it via get_running_loop()."""

        async def check():
            d = _CallbackDispatcher()
            assert d._loop is None, "No loop initially"

            fn = lambda: 42  # noqa: E731
            wrapped = d.wrap(fn)

            # Loop should be auto-detected
            assert d._loop is not None, "Loop should be auto-detected from running loop"
            assert wrapped is not fn, "Should wrap (not pass-through) when loop is available"
            assert wrapped() == 42

        asyncio.run(check())

    def test_fire_and_forget_auto_detects_loop(self):
        """wrap_fire_and_forget also auto-detects a running event loop."""

        async def check():
            d = _CallbackDispatcher()
            assert d._loop is None

            async def echo(x):
                return x

            wrapped = d.wrap_fire_and_forget(echo)
            assert d._loop is not None, "Loop should be auto-detected"
            assert wrapped is not echo

            # Fire-and-forget returns None immediately
            result = wrapped("hello")
            assert result is None

        asyncio.run(check())

    def test_attach_no_arg_auto_detects(self):
        """attach() with no argument auto-detects a running loop."""

        async def check():
            d = _CallbackDispatcher()
            assert d._loop is None
            d.attach()
            assert d._loop is not None
            assert d._loop.is_running()

        asyncio.run(check())

    def test_attach_not_running_fallback(self):
        """attach() outside a running loop falls back gracefully (no crash)."""
        d = _CallbackDispatcher()
        assert d._loop is None

        # This is called when no event loop is running
        # Should not crash, _loop may remain None
        d.attach()
        # _loop may be None or the default event loop
        # Either is acceptable as long as no exception is raised


# ===================================================================
# Unit tests for wrap_identity
# ===================================================================


class TestIdentityWrapper:
    """Tests for _CallbackDispatcher.wrap_identity()."""

    def test_identity_wrapper_no_loop(self):
        """Without loop, identity wrapper still wraps and calls the handler."""
        d = _CallbackDispatcher()
        handler = lambda hdl, pk: True  # noqa: E731
        wrapped = d.wrap_identity(handler)
        assert wrapped is not handler, "Should always wrap (no pass-through)"
        assert wrapped("hdl1", "pk1") is True

    def test_identity_wrapper_sync_no_loop_returns_false(self):
        """Identity wrapper without loop coerces the handler result to bool."""
        d = _CallbackDispatcher()
        handler = lambda hdl, pk: 0  # noqa: E731
        wrapped = d.wrap_identity(handler)
        assert wrapped("hdl1", "pk1") is False

    def test_identity_wrapper_sync(self):
        """Identity wrapper with loop attached calls sync handler directly."""
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        called_with = []

        def handler(hdl, pk):
            called_with.append((hdl, pk))
            return True

        d = _CallbackDispatcher()
        d.attach(loop)

        wrapped = d.wrap_identity(handler)
        assert wrapped is not handler

        result = wrapped("hdl1", "pk1")
        assert result is True
        assert called_with == [("hdl1", "pk1")]

        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()

    def test_identity_wrapper_async(self):
        """Identity wrapper with event loop dispatches async handler correctly."""
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        async def async_handler(hdl, pk):
            await asyncio.sleep(0.05)
            return True

        d = _CallbackDispatcher()
        d.attach(loop)

        wrapped = d.wrap_identity(async_handler)
        assert wrapped is not async_handler

        result = wrapped("hdl", "pk")
        assert result is True

        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()

    def test_identity_wrapper_async_reject(self):
        """Identity wrapper correctly returns False for rejection."""
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        async def reject_handler(hdl, pk):
            await asyncio.sleep(0.05)
            return False

        d = _CallbackDispatcher()
        d.attach(loop)

        wrapped = d.wrap_identity(reject_handler)
        result = wrapped("hdl", "pk")
        assert result is False

        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()

    def test_identity_wrapper_error(self):
        """Identity wrapper raises when handler raises and no error handler set."""
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        def failing_handler(hdl, pk):
            raise RuntimeError("identity check failed")

        d = _CallbackDispatcher()
        d.attach(loop)

        wrapped = d.wrap_identity(failing_handler)

        with pytest.raises(RuntimeError, match="identity check failed"):
            wrapped("hdl", "pk")

        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()

    def test_identity_wrapper_error_handler(self):
        """Identity wrapper calls error handler when one is configured."""
        errors = []
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        def failing_handler(hdl, pk):
            raise ValueError("handler error")

        d = _CallbackDispatcher()
        d.set_error_handler(lambda e: errors.append(e))
        d.attach(loop)

        wrapped = d.wrap_identity(failing_handler)
        result = wrapped("hdl", "pk")

        assert result is False
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)
        assert "handler error" in str(errors[0])

        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()

    def test_identity_wrapper_sync_returns_bool(self):
        """Identity wrapper coerces return value to bool."""
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        def truthy_handler(hdl, pk):
            return 1  # truthy, should become True

        d = _CallbackDispatcher()
        d.attach(loop)

        wrapped = d.wrap_identity(truthy_handler)
        result = wrapped("hdl", "pk")
        assert result is True

        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()

    def test_identity_wrapper_error_handler_after_wrap_sync_mode(self):
        """P1-2 sync gap: error handler registered AFTER wrapping catches identity errors.

        Regression test for the wrap-time pass-through gating in wrap_identity:
        with no loop and no error handler at wrap time, a later-registered error
        handler must still receive exceptions from the identity handler.
        """
        d = _CallbackDispatcher()
        errors = []

        def failing_handler(hdl, pk):
            raise ValueError("identity check failed")

        wrapped = d.wrap_identity(failing_handler)  # no loop, no error handler yet
        d.set_error_handler(lambda e: errors.append(e))

        result = wrapped("hdl", "pk")
        assert result is False
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)
        assert "identity check failed" in str(errors[0])

    def test_identity_wrapper_no_error_handler_raises(self):
        """Identity wrapper with no error handler re-raises the exception."""
        d = _CallbackDispatcher()
        wrapped = d.wrap_identity(lambda hdl, pk: 1 / 0)
        with pytest.raises(ZeroDivisionError):
            wrapped("hdl", "pk")


# ===================================================================
# Integration tests — Stream fire-and-forget (on_data/on_end/on_cancel)
# ===================================================================


class TestStreamFireAndForget:
    """Stream handlers registered via fire-and-forget dispatch."""

    @pytest.fixture
    def event_loop_thread(self):
        """Provide an event loop running in a background thread."""
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        yield loop
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()

    def test_stream_fire_and_forget_data(self, crypto_init, event_loop_thread, capsys):
        """Stream on_data handler works via fire-and-forget dispatch."""
        port = _next_port()
        stream_started = threading.Event()
        data_received = threading.Event()
        client_ready = threading.Event()

        loop = event_loop_thread

        server = op.Server()
        server.attach_event_loop(loop)

        @server.on_incoming_stream
        def handle_stream(stream: op.Stream):
            @stream.on_data
            def on_data(data: bytes):
                data_received.set()

            stream_started.set()

        server.start(port)
        time.sleep(0.1)

        client = op.Client(server.public_key)

        @client.on_ready
        def on_ready():
            client_ready.set()

        try:
            client.connect(f"ws://localhost:{port}")
            assert client_ready.wait(timeout=5), "Client did not become ready"

            s = client.start_stream()
            assert stream_started.wait(timeout=5), "Stream was not started on server"

            s.write(b"test fire-and-forget data")
            assert data_received.wait(timeout=5), "Stream on_data was not called"
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_stream_async_on_data_fire_and_forget(self, crypto_init, event_loop_thread, capsys):
        """Async stream on_data handler works via fire-and-forget dispatch.

        Regression test: Stream.on_data must return the handler's result so the
        dispatcher can detect and schedule coroutines from async handlers.
        """
        port = _next_port()
        stream_started = threading.Event()
        data_received = threading.Event()
        client_ready = threading.Event()
        received_chunks = []

        loop = event_loop_thread

        server = op.Server()
        server.attach_event_loop(loop)

        @server.on_incoming_stream
        def handle_stream(stream: op.Stream):
            @stream.on_data
            async def on_data(data: bytes):
                received_chunks.append(data)
                data_received.set()

            stream_started.set()

        server.start(port)
        time.sleep(0.1)

        client = op.Client(server.public_key)

        @client.on_ready
        def on_ready():
            client_ready.set()

        try:
            client.connect(f"ws://localhost:{port}")
            assert client_ready.wait(timeout=5), "Client did not become ready"

            s = client.start_stream()
            assert stream_started.wait(timeout=5), "Stream was not started on server"

            s.write(b"async data test")
            assert data_received.wait(timeout=5), "Async stream on_data was not called"
            assert received_chunks == [b"async data test"]
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_stream_fire_and_forget_end(self, crypto_init, event_loop_thread, capsys):
        """Stream on_end handler works via fire-and-forget dispatch."""
        port = _next_port()
        stream_started = threading.Event()
        end_received = threading.Event()
        client_ready = threading.Event()

        loop = event_loop_thread

        server = op.Server()
        server.attach_event_loop(loop)

        @server.on_incoming_stream
        def handle_stream(stream: op.Stream):
            @stream.on_data
            def on_data(data: bytes):
                pass

            @stream.on_end
            def on_end():
                end_received.set()

            stream_started.set()

        server.start(port)
        time.sleep(0.1)

        client = op.Client(server.public_key)

        @client.on_ready
        def on_ready():
            client_ready.set()

        try:
            client.connect(f"ws://localhost:{port}")
            assert client_ready.wait(timeout=5), "Client did not become ready"

            s = client.start_stream()
            assert stream_started.wait(timeout=5), "Stream was not started on server"

            s.write(b"data")
            s.end()
            assert end_received.wait(timeout=5), "Stream on_end was not called"
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_stream_fire_and_forget_cancel(self, crypto_init, event_loop_thread, capsys):
        """Stream on_cancel handler works via fire-and-forget dispatch."""
        port = _next_port()
        stream_started = threading.Event()
        cancel_received = threading.Event()
        client_ready = threading.Event()

        loop = event_loop_thread

        server = op.Server()
        server.attach_event_loop(loop)

        @server.on_incoming_stream
        def handle_stream(stream: op.Stream):
            @stream.on_data
            def on_data(data: bytes):
                pass

            @stream.on_cancel
            def on_cancel():
                cancel_received.set()

            stream_started.set()

        server.start(port)
        time.sleep(0.1)

        client = op.Client(server.public_key)

        @client.on_ready
        def on_ready():
            client_ready.set()

        try:
            client.connect(f"ws://localhost:{port}")
            assert client_ready.wait(timeout=5), "Client did not become ready"

            s = client.start_stream()
            assert stream_started.wait(timeout=5), "Stream was not started on server"

            # Server doesn't write back, so we just send data then cancel from client
            s.write(b"data before cancel")
            time.sleep(0.2)
            s.cancel()
            assert cancel_received.wait(timeout=5), "Stream on_cancel was not called"
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()
