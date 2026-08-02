"""
Tests for the _CallbackDispatcher, attach_event_loop, on_error, and on_fail features.

Covers Tasks 2-4 (and Task 1 Stream.op_code is already covered by
tests/v1_1_features/test_stream_opcode.py).

See Also:
    - _CallbackDispatcher in src/ObscuraProto/__init__.py
    - Server.attach_event_loop, Server.on_error, Client.attach_event_loop,
      Client.on_error, Client.on_fail, Stream.on_error
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
# Unit tests for _CallbackDispatcher (pure Python, no native deps)
# ===================================================================


class TestCallbackDispatcherUnit:
    """Direct unit-level tests for _CallbackDispatcher."""

    # -- wrap(None) -------------------------------------------------------

    def test_wrap_none_returns_none(self):
        """wrap(None) always returns None regardless of config."""
        d = _CallbackDispatcher()
        assert d.wrap(None) is None

        d.attach()
        assert d.wrap(None) is None

        d.set_error_handler(lambda e: None)
        assert d.wrap(None) is None

    # -- attach_event_loop -------------------------------------------------

    def test_no_loop_pass_through(self):
        """Without attach(), wrap still wraps fn and calls it correctly."""
        d = _CallbackDispatcher()
        sentinel = lambda: 42  # noqa: E731

        wrapped = d.wrap(sentinel)
        assert wrapped is not sentinel, "Should always wrap (no pass-through)"
        assert wrapped() == 42

    def test_no_loop_with_error_handler_wraps(self):
        """Without attach() but with error handler, wrap wraps the fn."""
        d = _CallbackDispatcher()
        errors = []
        d.set_error_handler(lambda e: errors.append(e))

        wrapped = d.wrap(lambda: 1 / 0)
        assert wrapped is not None
        assert wrapped is not d._wrap_sync

        wrapped()
        assert len(errors) == 1
        assert isinstance(errors[0], ZeroDivisionError)

    def test_attach_loop_without_running_loop(self):
        """attach(loop) accepts an explicit loop when no loop is running."""
        loop = asyncio.new_event_loop()
        d = _CallbackDispatcher()
        d.attach(loop)
        assert d._loop is loop
        loop.close()

    # -- wrap with error handler (sync) ------------------------------------

    def test_error_handler_called_sync(self):
        """Error handler is invoked when wrapped sync fn raises."""
        d = _CallbackDispatcher()
        errors = []
        d.set_error_handler(lambda e: errors.append(e))

        def failing_fn():
            raise ValueError("sync fail")

        wrapped = d.wrap(failing_fn)
        assert wrapped is not None
        assert wrapped is not failing_fn

        result = wrapped()
        assert result is None, "Return value should be None on error"
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)
        assert "sync fail" in str(errors[0])

    def test_error_handler_not_called_on_success(self):
        """Error handler is NOT invoked when wrapped fn succeeds."""
        d = _CallbackDispatcher()
        errors = []
        d.set_error_handler(lambda e: errors.append(e))

        wrapped = d.wrap(lambda x: x * 2)
        assert wrapped is not None
        assert wrapped(21) == 42
        assert len(errors) == 0

    def test_error_handler_called_with_positional_args(self):
        """Error handler receives exceptions from fn with positional args."""
        d = _CallbackDispatcher()
        errors = []
        d.set_error_handler(lambda e: errors.append(e))

        def fn(a, b):
            raise TypeError(f"bad {a} {b}")

        wrapped = d.wrap(fn)
        assert wrapped is not None
        wrapped(1, 2)
        assert len(errors) == 1
        assert "bad 1 2" in str(errors[0])

    def test_return_value_preserved_on_success(self):
        """Successful fn return value is preserved through the wrapper."""
        d = _CallbackDispatcher()
        d.set_error_handler(lambda e: None)

        wrapped = d.wrap(lambda a, b: a + b)
        assert wrapped is not None
        assert wrapped(3, 4) == 7

    # -- multiple error handler calls ---------------------------------------

    def test_multiple_errors_collected(self):
        """Multiple invocations of a failing wrapped fn collect all errors."""
        d = _CallbackDispatcher()
        errors = []
        d.set_error_handler(lambda e: errors.append(e))

        def always_fail():
            raise RuntimeError("boom")

        wrapped = d.wrap(always_fail)
        assert wrapped is not None

        for _ in range(3):
            wrapped()

        assert len(errors) == 3

    # -- no error handler --------------------------------------------------

    def test_no_error_handler_sync_pass_through(self):
        """Without error handler, wrap returns a working wrapper that re-raises."""
        d = _CallbackDispatcher()
        fn = lambda: 99  # noqa: E731
        wrapped = d.wrap(fn)
        assert wrapped is not fn
        assert wrapped() == 99

        with pytest.raises(ZeroDivisionError):
            d.wrap(lambda: 1 / 0)()

    # -- set_error_handler idempotent / replaceable -------------------------

    def test_set_error_handler_replaces_previous(self):
        """Calling set_error_handler replaces the previous handler (on re-wrap)."""
        d = _CallbackDispatcher()
        first = []
        second = []
        d.set_error_handler(lambda e: first.append(e))

        wrapped = d.wrap(lambda: 1 / 0)
        assert wrapped is not None
        wrapped()
        assert len(first) == 1

        d.set_error_handler(lambda e: second.append(e))
        # Re-wrap to pick up the new handler
        wrapped2 = d.wrap(lambda: 1 / 0)
        assert wrapped2 is not None
        wrapped2()
        assert len(first) == 1  # unchanged
        assert len(second) == 1  # new handler got it

    def test_set_error_handler_to_none_disables(self):
        """Setting error handler to None disables error catching -> exceptions re-raise."""
        d = _CallbackDispatcher()
        d.set_error_handler(lambda e: None)  # active

        wrapped = d.wrap(lambda: 1 / 0)
        assert wrapped is not None

        d.set_error_handler(None)  # disable
        # Re-wrap to pick up the new config
        fn = lambda: 42  # noqa: E731
        re_wrapped = d.wrap(fn)
        assert re_wrapped is not fn  # still wrapped (not pass-through)
        assert re_wrapped() == 42
        with pytest.raises(ZeroDivisionError):
            d.wrap(lambda: 1 / 0)()

    def test_error_handler_registered_after_wrap_takes_effect(self):
        """P1-2: an error handler set AFTER wrapping still catches errors.

        The error handler is read at invocation time, so wrapping a callback
        before registering @on_error must still forward errors.
        """
        errors = []
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        d = _CallbackDispatcher()
        d.attach(loop)

        # Wrap a failing callback BEFORE any error handler is registered.
        wrapped = d.wrap_fire_and_forget(lambda: 1 / 0)
        assert wrapped is not None

        # Register the error handler AFTER wrapping (the bug scenario).
        d.set_error_handler(lambda e: errors.append(e))

        try:
            result = wrapped()
            assert result is None
            assert len(errors) == 1
            assert isinstance(errors[0], ZeroDivisionError)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)
            loop.close()

    def test_error_handler_replaced_after_wrap_takes_effect_sync(self):
        """P1-2: replacing the error handler after wrapping is picked up at invocation."""
        d = _CallbackDispatcher()
        first = []
        second = []
        d.set_error_handler(lambda e: first.append(e))

        wrapped = d.wrap(lambda: 1 / 0)  # sync path (_wrap_sync)
        assert wrapped is not None

        # Replace the handler AFTER wrapping -- no re-wrap needed.
        d.set_error_handler(lambda e: second.append(e))

        wrapped()
        assert len(first) == 0, "Old captured handler should not be used"
        assert len(second) == 1, "Handler read at invocation time should be used"

    def test_error_handler_after_wrap_sync_mode(self):
        """P1-2 sync gap: error handler registered AFTER wrap takes effect in sync mode.

        Regression test for the wrap-time pass-through gating: a fire-and-forget
        callback wrapped with no loop and no error handler must still forward
        exceptions to an error handler registered later.
        """
        d = _CallbackDispatcher()
        errors = []
        wrapped = d.wrap_fire_and_forget(lambda: 1 / 0)
        d.set_error_handler(lambda e: errors.append(e))
        wrapped()  # should NOT raise, error handler should be called
        assert len(errors) == 1
        assert isinstance(errors[0], ZeroDivisionError)

    def test_no_error_handler_raises(self):
        """With no error handler at all, wrapped callbacks still raise."""
        d = _CallbackDispatcher()
        wrapped = d.wrap_fire_and_forget(lambda: 1 / 0)
        with pytest.raises(ZeroDivisionError):
            wrapped()

    def test_wrap_sync_error_handler_after_wrap(self):
        """wrap() in sync mode also picks up an error handler registered after wrapping."""
        d = _CallbackDispatcher()
        errors = []
        wrapped = d.wrap(lambda: 1 / 0)
        d.set_error_handler(lambda e: errors.append(e))
        wrapped()  # should NOT raise
        assert len(errors) == 1
        assert isinstance(errors[0], ZeroDivisionError)

    def test_wrap_sync_no_error_handler_raises(self):
        """wrap() with no error handler re-raises the exception."""
        d = _CallbackDispatcher()
        wrapped = d.wrap(lambda: 1 / 0)
        with pytest.raises(ZeroDivisionError):
            wrapped()

    # -- attach_event_loop with async handler dispatch ----------------------

    def test_async_handler_dispatched_to_loop(self):
        """Async handler scheduled on loop when loop is attached."""
        results = []
        loop = asyncio.new_event_loop()

        async def async_fn(x):
            results.append(x)
            return x * 2

        d = _CallbackDispatcher()
        d.attach(loop)

        # Run the loop in a separate thread so future.result() can complete
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        try:
            wrapped = d.wrap(async_fn)
            assert wrapped is not None
            assert wrapped is not async_fn

            # Calling the wrapped sync function will schedule the coroutine
            # on the loop and block on future.result()
            ret = wrapped(42)
            assert ret == 84
            assert results == [42]
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)
            loop.close()

    def test_async_handler_error_forwarded(self):
        """Error from an async handler is forwarded to the error handler."""
        errors = []
        loop = asyncio.new_event_loop()

        async def failing_async():
            raise ValueError("async fail")

        d = _CallbackDispatcher()
        d.set_error_handler(lambda e: errors.append(e))
        d.attach(loop)

        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        try:
            wrapped = d.wrap(failing_async)
            assert wrapped is not None
            ret = wrapped()
            assert ret is None
            assert len(errors) == 1
            assert isinstance(errors[0], ValueError)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)
            loop.close()

    def test_sync_handler_with_attached_loop(self):
        """Sync (non-coroutine) handler is called directly even when loop attached."""
        results = []
        loop = asyncio.new_event_loop()

        def sync_fn(x):
            results.append(x)
            return x * 2

        d = _CallbackDispatcher()
        d.attach(loop)

        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        try:
            wrapped = d.wrap(sync_fn)
            assert wrapped is not None
            ret = wrapped(21)
            assert ret == 42
            assert results == [21]
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)
            loop.close()


# ===================================================================
# Integration tests — Server.attach_event_loop pass-through
# ===================================================================


class TestAttachEventLoop:
    """attach_event_loop with no event loop set — pass-through mode."""

    def test_attach_event_loop_pass_through(self, crypto_init, capsys):
        """Server lifecycle works without calling attach_event_loop()."""
        port = _next_port()
        on_open_fired = threading.Event()
        client_ready = threading.Event()

        server = op.Server()

        @server.on_open
        def handle_open(hdl):
            on_open_fired.set()

        server.start(port)
        time.sleep(0.1)

        client = op.Client(server.public_key)

        @client.on_ready
        def handle_ready():
            client_ready.set()

        try:
            client.connect(f"ws://localhost:{port}")
            assert client_ready.wait(timeout=5), "Client did not become ready"
            assert on_open_fired.wait(timeout=5), "on_open did not fire"
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_attach_event_loop_called_with_loop(self, crypto_init, capsys):
        """Server.attach_event_loop(loop) accepts an explicit loop."""
        port = _next_port()
        on_open_fired = threading.Event()
        client_ready = threading.Event()

        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        server = op.Server()
        server.attach_event_loop(loop)

        @server.on_open
        def handle_open(hdl):
            on_open_fired.set()

        server.start(port)
        time.sleep(0.1)

        client = op.Client(server.public_key)

        @client.on_ready
        def handle_ready():
            client_ready.set()

        try:
            client.connect(f"ws://localhost:{port}")
            assert client_ready.wait(timeout=5), "Client did not become ready"
            assert on_open_fired.wait(timeout=5), "on_open did not fire with loop"
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)
            loop.close()
            capsys.readouterr()

    def test_client_attach_event_loop(self, crypto_init, capsys):
        """Client.attach_event_loop(loop) accepts an explicit loop."""
        port = _next_port()
        client_ready = threading.Event()

        server = op.Server()

        @server.on_open
        def handle_open(hdl):
            pass

        server.start(port)
        time.sleep(0.1)

        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        client = op.Client(server.public_key)
        client.attach_event_loop(loop)

        @client.on_ready
        def handle_ready():
            client_ready.set()

        try:
            client.connect(f"ws://localhost:{port}")
            assert client_ready.wait(timeout=5), "Client did not become ready"
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)
            loop.close()
            capsys.readouterr()


# ===================================================================
# Integration tests — Server/Client on_error
# ===================================================================


class TestOnError:
    """Server / Client on_error callback."""

    def test_on_error_handler_invoked(self, crypto_init, capsys):
        """Server.on_error handler is called when a payload handler raises."""
        port = _next_port()
        opcode = 0x7101
        error_caught = threading.Event()
        error_received = []
        client_ready = threading.Event()

        server = op.Server()

        @server.on_error
        def handle_error(err):
            error_received.append(err)
            error_caught.set()

        @server.on_anon_payload(opcode)
        def handle_payload(hdl: op.ConnectionHdl, val: str):
            raise ValueError("intentional error from payload handler")

        server.start(port)
        time.sleep(0.1)

        client = op.Client(server.public_key)

        @client.on_ready
        def on_ready():
            client_ready.set()

        try:
            client.connect(f"ws://localhost:{port}")
            assert client_ready.wait(timeout=5), "Client did not become ready"

            client.send(op.PayloadBuilder(opcode).add_param("hello").build())

            assert error_caught.wait(timeout=5), "on_error was not called"
            assert len(error_received) == 1
            assert isinstance(error_received[0], ValueError)
            assert "intentional error" in str(error_received[0])
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_on_error_handler_not_set(self, crypto_init, capsys):
        """Without on_error, an exception in a handler does not crash the process."""
        port = _next_port()
        opcode = 0x7102
        client_ready = threading.Event()

        server = op.Server()

        @server.on_anon_payload(opcode)
        def handle_payload(hdl: op.ConnectionHdl, val: str):
            raise RuntimeError("this should be caught by native layer or silently ignored")

        server.start(port)
        time.sleep(0.1)

        client = op.Client(server.public_key)

        @client.on_ready
        def on_ready():
            client_ready.set()

        try:
            client.connect(f"ws://localhost:{port}")
            assert client_ready.wait(timeout=5), "Client did not become ready"

            # Send a payload that triggers the exception-raising handler.
            client.send(op.PayloadBuilder(opcode).add_param("test").build())

            # Allow the native layer time to process the exception.
            time.sleep(0.5)

            # Verify the server is still alive by sending a second payload.
            # If the process crashed, this would hang/time out the test.
            client.send(op.PayloadBuilder(opcode).add_param("still_alive").build())
            time.sleep(0.3)

            # If we get here, the process did not crash.
            assert True
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_client_on_error_handler_invoked(self, crypto_init, capsys):
        """Client.on_error handler is called when a client-side handler raises."""
        port = _next_port()
        opcode_trigger = 0x7103
        opcode_response = 0x8103
        error_caught = threading.Event()
        error_received = []
        client_ready = threading.Event()
        trigger_received = threading.Event()

        server = op.Server()

        @server.on_anon_payload(opcode_trigger)
        def handle_trigger(hdl: op.ConnectionHdl, val: str):
            trigger_received.set()
            # Server sends a payload that will trigger the client's error handler
            server.send_anonymous(hdl, op.PayloadBuilder(opcode_response).add_param("boom").build())

        server.start(port)
        time.sleep(0.1)

        client = op.Client(server.public_key)

        @client.on_error
        def handle_error(err):
            error_received.append(err)
            error_caught.set()

        @client.on_payload(opcode_response)
        def handle_response(payload: op.Payload):
            raise ValueError("client handler error")

        @client.on_ready
        def on_ready():
            client_ready.set()

        try:
            client.connect(f"ws://localhost:{port}")
            assert client_ready.wait(timeout=5), "Client did not become ready"

            # Send a trigger from client; server responds with the error-inducing payload
            client.send(op.PayloadBuilder(opcode_trigger).add_param("go").build())

            assert trigger_received.wait(timeout=5), "Server did not receive trigger"

            # The client's on_payload handler should raise, and on_error should catch it
            assert error_caught.wait(timeout=5), "Client on_error was not called"
            assert len(error_received) >= 1
            assert isinstance(error_received[0], ValueError)
            assert "client handler error" in str(error_received[0])
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_on_error_on_both_sides_independent(self, crypto_init, capsys):
        """Server and Client error handlers work independently."""
        port = _next_port()
        op_echo = 0x7104
        server_errors = []
        client_errors = []
        server_error_event = threading.Event()
        client_error_event = threading.Event()
        client_ready = threading.Event()

        server = op.Server()

        @server.on_error
        def server_error(err):
            server_errors.append(err)
            server_error_event.set()

        @server.on_anon_payload(op_echo)
        def handle_echo(hdl: op.ConnectionHdl, val: str):
            raise ValueError("server handler error")

        server.start(port)
        time.sleep(0.1)

        client = op.Client(server.public_key)

        @client.on_error
        def client_error(err):
            client_errors.append(err)
            client_error_event.set()

        @client.on_ready
        def on_ready():
            client_ready.set()

        try:
            client.connect(f"ws://localhost:{port}")
            assert client_ready.wait(timeout=5), "Client did not become ready"

            # Trigger server-side error
            client.send(op.PayloadBuilder(op_echo).add_param("boom").build())
            assert server_error_event.wait(timeout=5), "Server on_error not called"
            assert len(server_errors) == 1

            # Client should NOT have received an error (the error was on server side)
            time.sleep(0.3)
            assert not client_error_event.is_set(), "Client on_error should not fire for server error"
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()


# ===================================================================
# Integration tests — Stream.on_error
# ===================================================================


class TestStreamOnError:
    """Stream.on_error callback."""

    def test_on_error_with_stream(self, crypto_init, capsys):
        """Stream.on_error catches exceptions from stream data handlers."""
        port = _next_port()
        error_caught = threading.Event()
        error_received = []
        client_ready = threading.Event()
        stream_started = threading.Event()
        stream_done = threading.Event()

        server = op.Server()

        @server.on_incoming_stream
        def handle_stream(stream: op.Stream):
            @stream.on_error
            def handle_error(err):
                error_received.append(err)
                error_caught.set()

            @stream.on_data
            def on_data(data: bytes):
                raise ValueError("stream data handler error")

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

            stream = client.start_stream()
            assert stream_started.wait(timeout=5), "Stream was not started on server"

            stream.write(b"trigger error")
            assert error_caught.wait(timeout=5), "Stream on_error was not called"
            assert len(error_received) >= 1
            assert isinstance(error_received[0], ValueError)

            stream.end()
            time.sleep(0.3)
            stream_done.set()
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_on_error_on_stream_with_end_handler(self, crypto_init, capsys):
        """Stream.on_error catches exceptions from on_end handler too."""
        port = _next_port()
        error_caught = threading.Event()
        error_received = []
        client_ready = threading.Event()
        stream_started = threading.Event()
        client_data_received = threading.Event()

        server = op.Server()

        @server.on_incoming_stream
        def handle_stream(stream: op.Stream):
            @stream.on_error
            def handle_error(err):
                error_received.append(err)
                error_caught.set()

            @stream.on_data
            def on_data(data: bytes):
                stream.write(b"echo:" + data)

            @stream.on_end
            def on_end():
                raise RuntimeError("on_end handler error")

            stream_started.set()

        server.start(port)
        time.sleep(0.1)

        client = op.Client(server.public_key)

        @client.on_ready
        def on_ready():
            client_ready.set()
            stream = client.start_stream()

            @stream.on_data
            def on_client_data(data: bytes):
                client_data_received.set()

            stream.write(b"hello")
            time.sleep(0.3)
            stream.end()

        try:
            client.connect(f"ws://localhost:{port}")
            assert client_ready.wait(timeout=5), "Client did not become ready"
            assert stream_started.wait(timeout=5), "Stream was not started"
            assert client_data_received.wait(timeout=5), "Client did not receive data echo"
            assert error_caught.wait(timeout=5), "Stream on_error was not called for on_end exception"
            assert len(error_received) >= 1
            assert isinstance(error_received[0], RuntimeError)
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()


# ===================================================================
# Integration tests — async handlers with attach_event_loop
# ===================================================================


class TestAsyncHandlers:
    """Async handlers dispatched via attach_event_loop."""

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

    def test_async_payload_handler_works(self, crypto_init, event_loop_thread, capsys):
        """Async on_payload handler works with attach_event_loop."""
        port = _next_port()
        opcode = 0x7201
        handler_called = threading.Event()
        received_values = []
        client_ready = threading.Event()

        loop = event_loop_thread

        server = op.Server()
        server.attach_event_loop(loop)

        @server.on_anon_payload(opcode)
        async def handle_async(hdl: op.ConnectionHdl, val: str):
            received_values.append(val)
            handler_called.set()

        server.start(port)
        time.sleep(0.1)

        client = op.Client(server.public_key)

        @client.on_ready
        def on_ready():
            client_ready.set()

        try:
            client.connect(f"ws://localhost:{port}")
            assert client_ready.wait(timeout=5), "Client did not become ready"

            client.send(op.PayloadBuilder(opcode).add_param("async_test").build())

            assert handler_called.wait(timeout=5), "Async handler was not invoked"
            assert len(received_values) == 1
            assert received_values[0] == "async_test"
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_async_request_handler_works(self, crypto_init, event_loop_thread, capsys):
        """Async on_request handler works with attach_event_loop."""
        port = _next_port()
        op_req = 0x7202
        op_resp = 0x8202
        handler_called = threading.Event()
        received_values = []
        client_ready = threading.Event()

        loop = event_loop_thread

        server = op.Server()
        server.attach_event_loop(loop)

        @server.on_anon_request(op_req)
        async def handle_request(hdl: op.ConnectionHdl, val: str) -> op.Payload:
            received_values.append(val)
            handler_called.set()
            return op.PayloadBuilder(op_resp).add_param(f"reply:{val}").build()

        server.start(port)
        time.sleep(0.1)

        client = op.Client(server.public_key)

        @client.on_ready
        def on_ready():
            client_ready.set()

        try:
            client.connect(f"ws://localhost:{port}")
            assert client_ready.wait(timeout=5), "Client did not become ready"

            response = client.sync_request(op.PayloadBuilder(op_req).add_param("hello_async").build())
            assert handler_called.wait(timeout=5), "Async request handler was not invoked"
            assert len(received_values) == 1
            assert received_values[0] == "hello_async"

            reader = op.PayloadReader(response)
            assert reader.read_string() == "reply:hello_async"
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_async_incoming_stream_handler(self, crypto_init, event_loop_thread, capsys):
        """Async on_incoming_stream handler works with attach_event_loop."""
        port = _next_port()
        handler_called = threading.Event()
        client_ready = threading.Event()
        client_data_received = threading.Event()
        client_chunks = []

        loop = event_loop_thread

        server = op.Server()
        server.attach_event_loop(loop)

        @server.on_incoming_stream
        async def handle_stream(stream: op.Stream):
            handler_called.set()

            @stream.on_data
            def on_data(data: bytes):
                stream.write(b"echo:" + data)

            @stream.on_end
            def on_end():
                stream.end()

        server.start(port)
        time.sleep(0.1)

        client = op.Client(server.public_key)

        @client.on_ready
        def on_ready():
            client_ready.set()
            stream = client.start_stream()

            @stream.on_data
            def on_data(data: bytes):
                client_chunks.append(data)
                client_data_received.set()

            stream.write(b"async_stream_test")
            time.sleep(0.2)
            stream.end()

        try:
            client.connect(f"ws://localhost:{port}")
            assert client_ready.wait(timeout=5), "Client did not become ready"
            assert handler_called.wait(timeout=5), "Async incoming stream handler not called"
            assert client_data_received.wait(timeout=5), "Client did not receive stream echo"
            assert len(client_chunks) == 1
            assert client_chunks[0] == b"echo:async_stream_test"
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_async_handler_error_caught_by_on_error(self, crypto_init, event_loop_thread, capsys):
        """Error from async handler is forwarded to on_error when loop is attached."""
        port = _next_port()
        opcode = 0x7203
        error_caught = threading.Event()
        error_received = []
        client_ready = threading.Event()

        loop = event_loop_thread

        server = op.Server()

        @server.on_error
        def handle_error(err):
            error_received.append(err)
            error_caught.set()

        server.attach_event_loop(loop)

        @server.on_anon_payload(opcode)
        async def handle_async(hdl: op.ConnectionHdl, val: str):
            raise RuntimeError("async handler failure")

        server.start(port)
        time.sleep(0.1)

        client = op.Client(server.public_key)

        @client.on_ready
        def on_ready():
            client_ready.set()

        try:
            client.connect(f"ws://localhost:{port}")
            assert client_ready.wait(timeout=5), "Client did not become ready"

            client.send(op.PayloadBuilder(opcode).add_param("trigger_error").build())

            assert error_caught.wait(timeout=5), "on_error was not called for async handler error"
            assert len(error_received) == 1
            assert isinstance(error_received[0], RuntimeError)
            assert "async handler failure" in str(error_received[0])
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_mixed_sync_and_async_handlers(self, crypto_init, event_loop_thread, capsys):
        """Sync and async handlers coexist when loop is attached."""
        port = _next_port()
        op_sync = 0x7204
        op_async = 0x7205
        sync_called = threading.Event()
        async_called = threading.Event()
        client_ready = threading.Event()
        sync_results = []
        async_results = []

        loop = event_loop_thread

        server = op.Server()
        server.attach_event_loop(loop)

        @server.on_anon_payload(op_sync)
        def handle_sync(hdl: op.ConnectionHdl, val: str):
            sync_results.append(val)
            sync_called.set()

        @server.on_anon_payload(op_async)
        async def handle_async(hdl: op.ConnectionHdl, val: str):
            async_results.append(val)
            async_called.set()

        server.start(port)
        time.sleep(0.1)

        client = op.Client(server.public_key)

        @client.on_ready
        def on_ready():
            client_ready.set()

        try:
            client.connect(f"ws://localhost:{port}")
            assert client_ready.wait(timeout=5), "Client did not become ready"

            client.send(op.PayloadBuilder(op_sync).add_param("sync_val").build())
            client.send(op.PayloadBuilder(op_async).add_param("async_val").build())

            assert sync_called.wait(timeout=5), "Sync handler not called"
            assert async_called.wait(timeout=5), "Async handler not called"
            assert sync_results == ["sync_val"]
            assert async_results == ["async_val"]
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()


# ===================================================================
# P1-1: async_request timeout enforcement
# ===================================================================


class _NeverReadyFuture:
    """Stand-in for CppPayloadFuture that never becomes ready."""

    def ready(self):
        return False

    def get(self):
        raise AssertionError("get() must not be called after a timeout")


class _QuickFuture:
    """Stand-in for CppPayloadFuture that becomes ready after ~20ms."""

    def __init__(self, result="response"):
        self._ready_at = time.monotonic() + 0.02
        self._result = result

    def ready(self):
        return time.monotonic() >= self._ready_at

    def get(self):
        return self._result


class _FakeServerBackend:
    def async_request(self, hdl, payload, timeout_ms=None):
        return _QuickFuture()

    def async_request_to_identity(self, identity_pk, payload):
        return _QuickFuture()


class _FakeClientBackend:
    def async_request(self, payload, timeout_ms=None):
        return _QuickFuture()


class _FakeServerBackendNeverReady:
    def async_request(self, hdl, payload, timeout_ms=None):
        return _NeverReadyFuture()

    def async_request_to_identity(self, identity_pk, payload):
        return _NeverReadyFuture()


class _FakeClientBackendNeverReady:
    def async_request(self, payload, timeout_ms=None):
        return _NeverReadyFuture()


class TestAsyncRequestTimeout:
    """async_request raises TimeoutError when the remote never responds (P1-1)."""

    def test_client_async_request_success(self):
        backend = _FakeClientBackend()
        client = op.Client.__new__(op.Client)
        client._client = backend
        result = asyncio.run(client.async_request(None, timeout=5.0))
        assert result == "response"

    def test_client_async_request_times_out(self):
        backend = _FakeClientBackendNeverReady()
        client = op.Client.__new__(op.Client)
        client._client = backend
        with pytest.raises(TimeoutError, match="timed out after 0.1s"):
            asyncio.run(client.async_request(None, timeout=0.1))

    def test_server_async_request_times_out(self):
        backend = _FakeServerBackendNeverReady()
        server = op.Server.__new__(op.Server)
        server._server = backend
        with pytest.raises(TimeoutError, match="timed out after 0.1s"):
            asyncio.run(server.async_request(None, None, timeout=0.1))

    def test_server_async_request_to_identity_times_out(self):
        backend = _FakeServerBackendNeverReady()
        server = op.Server.__new__(op.Server)
        server._server = backend
        with pytest.raises(TimeoutError, match="timed out after 0.1s"):
            asyncio.run(server.async_request_to_identity(None, None, timeout=0.1))
