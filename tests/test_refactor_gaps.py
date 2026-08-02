"""
Gap-fill tests for the pyObscuraProto refactoring (final verification).

Covers scenarios not exercised by the main suite:
  - async_request before connect / after disconnect -> clean error
  - Double stop() / disconnect() idempotency
  - Identity handler returning None -> False
  - Async identity handler without an attached event loop -> RuntimeError
  - Concurrent async requests (stress, mock-based)
  - Stream async I/O wrappers (mock-based)
  - wrap_fire_and_forget(None) -> None
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
# pyright: reportOptionalMemberAccess=false


def _find_free_port():
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
# async_request error paths (real backend)
# ===================================================================


class TestAsyncRequestErrorPaths:
    """async_request must error cleanly (not hang) before/after connection."""

    def test_async_request_before_connect_raises(self, crypto_init, capsys):
        """A request on a client that was never connected raises immediately."""
        kp = op.Crypto.generate_sign_keypair()
        client = op.Client(kp.public_key)

        async def run():
            with pytest.raises(RuntimeError, match="not ready"):
                await client.async_request(op.PayloadBuilder(0x9999).build(), timeout=0.5)

        asyncio.run(run())
        capsys.readouterr()

    def test_async_request_after_disconnect_raises(self, crypto_init, capsys):
        """A request on a disconnected client raises immediately (no hang)."""
        port = _next_port()
        server = op.Server()
        client_ready = threading.Event()
        server.start(port)
        time.sleep(0.1)

        client = op.Client(server.public_key)
        client.on_ready(client_ready.set)
        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Client did not connect"
        client.disconnect()
        time.sleep(0.1)

        async def run():
            with pytest.raises(RuntimeError, match="not ready"):
                await client.async_request(op.PayloadBuilder(0x9999).build(), timeout=0.5)

        try:
            asyncio.run(run())
        finally:
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()


# ===================================================================
# Double stop() / disconnect() idempotency
# ===================================================================


class TestDoubleShutdown:
    """stop() / disconnect() called twice must not crash or raise."""

    def test_double_client_disconnect(self, crypto_init, capsys):
        """Calling disconnect() twice is safe."""
        port = _next_port()
        server = op.Server()
        client_ready = threading.Event()
        server.start(port)
        time.sleep(0.1)

        client = op.Client(server.public_key)
        client.on_ready(client_ready.set)
        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Client did not connect"

        try:
            client.disconnect()
            client.disconnect()  # second call must be a no-op / not raise
        finally:
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_double_server_stop(self, crypto_init, capsys):
        """Calling stop() twice on a fully-started server is safe."""
        port = _next_port()
        server = op.Server()
        server.start(port)
        time.sleep(0.1)

        try:
            server.stop()
            server.stop()  # second call must not raise or hang
        finally:
            time.sleep(0.1)
            capsys.readouterr()

    def test_manual_stop_inside_context_manager(self, crypto_init, capsys):
        """Manual stop() inside `async with` then context exit stop() is safe.

        Guards the double-stop path taken when a user stops the server
        explicitly and the context manager __aexit__ stops it again.
        NOTE: the server must be fully started before the manual stop
        (see the known C++ timing window on stop() before accept loop init).
        """
        port = _next_port()

        async def run():
            async with op.Server(port=port) as server:
                time.sleep(0.3)  # allow the accept loop to initialize
                server.stop()

        asyncio.run(run())
        capsys.readouterr()


# ===================================================================
# Identity handler returning None
# ===================================================================


class TestIdentityWrapperNone:
    """Identity handlers returning None must be coerced to False."""

    def test_identity_wrapper_none_no_loop(self):
        """bool(None) == False -> rejection, no loop attached."""
        d = _CallbackDispatcher()
        handler = lambda hdl, pk: None  # noqa: E731
        wrapped = d.wrap_identity(handler)
        assert wrapped("hdl", "pk") is False

    def test_identity_wrapper_none_async(self):
        """Async identity handler returning None -> False."""
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        async def async_none_handler(hdl, pk):
            await asyncio.sleep(0.01)
            return None

        d = _CallbackDispatcher()
        d.attach(loop)
        wrapped = d.wrap_identity(async_none_handler)

        result = wrapped("hdl", "pk")
        assert result is False

        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()

    @pytest.mark.filterwarnings("ignore:coroutine .* was never awaited:RuntimeWarning")
    def test_identity_wrapper_async_no_loop_raises(self):
        """Async identity handler without an attached loop raises RuntimeError."""
        d = _CallbackDispatcher()

        async def async_handler(hdl, pk):
            return True

        wrapped = d.wrap_identity(async_handler)
        with pytest.raises(RuntimeError, match="no event loop"):
            wrapped("hdl", "pk")


# ===================================================================
# Concurrent async requests (mock-based stress)
# ===================================================================


class _StaggeredFuture:
    """Future that becomes ready after a configurable delay, returning a tag."""

    def __init__(self, delay, tag):
        self._ready_at = time.monotonic() + delay
        self._tag = tag

    def ready(self):
        return time.monotonic() >= self._ready_at

    def get(self):
        return self._tag


class _StaggeredClientBackend:
    def __init__(self):
        self._counter = 0

    def async_request(self, payload, timeout_ms=None):
        self._counter += 1
        n = self._counter
        return _StaggeredFuture(delay=0.005 * n, tag=f"resp-{n}")


class _StaggeredServerBackend:
    def __init__(self):
        self._counter = 0

    def async_request(self, hdl, payload, timeout_ms=None):
        self._counter += 1
        n = self._counter
        return _StaggeredFuture(delay=0.005 * n, tag=f"srv-{n}")

    def async_request_to_identity(self, identity_pk, payload):
        self._counter += 1
        n = self._counter
        return _StaggeredFuture(delay=0.005 * n, tag=f"srv-id-{n}")


class TestConcurrentAsyncRequests:
    """Multiple in-flight async_request calls must all complete correctly."""

    def test_client_concurrent_requests_stress(self):
        """10 concurrent client.async_request calls resolve with correct tags."""
        backend = _StaggeredClientBackend()
        client = op.Client.__new__(op.Client)
        client._client = backend

        async def run():
            results = await asyncio.gather(*(client.async_request(None, timeout=5.0) for _ in range(10)))
            return results

        results = asyncio.run(run())
        assert len(results) == 10
        assert results == [f"resp-{i}" for i in range(1, 11)]

    def test_server_concurrent_requests_stress(self):
        """8 concurrent server.async_request calls resolve with correct tags."""
        backend = _StaggeredServerBackend()
        server = op.Server.__new__(op.Server)
        server._server = backend

        async def run():
            results = await asyncio.gather(*(server.async_request(None, None, timeout=5.0) for _ in range(8)))
            return results

        results = asyncio.run(run())
        assert results == [f"srv-{i}" for i in range(1, 9)]

    def test_server_concurrent_to_identity_stress(self):
        """6 concurrent async_request_to_identity calls resolve correctly."""
        backend = _StaggeredServerBackend()
        server = op.Server.__new__(op.Server)
        server._server = backend

        async def run():
            results = await asyncio.gather(
                *(server.async_request_to_identity(None, None, timeout=5.0) for _ in range(6))
            )
            return results

        results = asyncio.run(run())
        assert results == [f"srv-id-{i}" for i in range(1, 7)]


# ===================================================================
# Stream async I/O wrappers (mock-based)
# ===================================================================


class _FakeCppStream:
    """Stand-in for the C++ CppStream exposing the low-level API."""

    def __init__(self):
        self.written = []
        self.ended = False
        self.cancelled = False

    def get_stream_id(self):
        return 1

    def get_op_code(self):
        return None

    def write(self, data):
        self.written.append(bytes(data))

    def end(self):
        self.ended = True

    def cancel(self):
        self.cancelled = True

    def set_data_handler(self, fn):
        self._data_handler = fn

    def set_end_handler(self, fn):
        self._end_handler = fn

    def set_cancel_handler(self, fn):
        self._cancel_handler = fn


class TestStreamAsyncIO:
    """Stream.async_write / async_end / async_cancel delegate correctly."""

    def test_stream_async_write_end_cancel(self):
        cpp = _FakeCppStream()
        stream = op.Stream(cpp)

        async def run():
            await stream.async_write(b"hello")
            await stream.async_end()
            await stream.async_cancel()

        asyncio.run(run())
        assert cpp.written == [b"hello"]
        assert cpp.ended is True
        assert cpp.cancelled is True

    def test_stream_sync_io(self):
        cpp = _FakeCppStream()
        stream = op.Stream(cpp)
        stream.write(b"sync")
        stream.end()
        stream.cancel()
        assert cpp.written == [b"sync"]
        assert cpp.ended is True
        assert cpp.cancelled is True

    def test_stream_properties(self):
        cpp = _FakeCppStream()
        stream = op.Stream(cpp)
        assert stream.stream_id == 1
        assert stream.op_code is None


# ===================================================================
# wrap_fire_and_forget(None)
# ===================================================================


class TestFireAndForgetNone:
    """wrap_fire_and_forget(None) returns None (parity with wrap(None))."""

    def test_fire_and_forget_none_returns_none(self):
        d = _CallbackDispatcher()
        assert d.wrap_fire_and_forget(None) is None

        d.attach()
        assert d.wrap_fire_and_forget(None) is None

        d.set_error_handler(lambda e: None)
        assert d.wrap_fire_and_forget(None) is None


# ===================================================================
# Client constructor validation
# ===================================================================


class TestClientValidation:
    """Client() rejects non-PublicKey server keys."""

    def test_client_rejects_non_public_key(self):
        with pytest.raises(TypeError, match="PublicKey"):
            op.Client("not-a-public-key")


# ===================================================================
# async_start_stream wrappers (mock-based)
# ===================================================================


class TestAsyncStartStream:
    """Client.async_start_stream / Server.async_start_stream delegate correctly."""

    def test_client_async_start_stream(self):
        class _FakeBackend:
            def start_stream(self, *args):
                return _FakeCppStream()

        client = op.Client.__new__(op.Client)
        client._client = _FakeBackend()
        client._dispatcher = _CallbackDispatcher()

        async def run():
            stream = await client.async_start_stream()
            assert isinstance(stream, op.Stream)
            await stream.async_write(b"x")
            return stream

        stream = asyncio.run(run())
        assert stream._s.written == [b"x"]

    def test_server_async_start_stream(self):
        class _FakeBackend:
            def start_stream(self, hdl, *args):
                return _FakeCppStream()

        server = op.Server.__new__(op.Server)
        server._server = _FakeBackend()
        server._dispatcher = _CallbackDispatcher()

        async def run():
            stream = await server.async_start_stream("hdl")
            assert isinstance(stream, op.Stream)
            await stream.async_end()
            return stream

        stream = asyncio.run(run())
        assert stream._s.ended is True


# ===================================================================
# Dispatcher async error re-raise paths (no error handler)
# ===================================================================


class TestDispatcherNoErrorHandlerAsync:
    """With a loop attached but no error handler, async errors re-raise."""

    def test_fire_and_forget_sync_error_no_error_handler_raises(self):
        """Fire-and-forget with loop attached re-raises sync errors when no error handler."""
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        d = _CallbackDispatcher()
        d.attach(loop)

        def failing_fn():
            raise ValueError("sync in async mode")

        wrapped = d.wrap_fire_and_forget(failing_fn)
        with pytest.raises(ValueError, match="sync in async mode"):
            wrapped()

        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()

    def test_fire_and_forget_sync_error_error_handler_called(self):
        """Fire-and-forget with loop + error handler forwards sync errors."""
        errors = []
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        d = _CallbackDispatcher()
        d.set_error_handler(lambda e: errors.append(e))
        d.attach(loop)

        def failing_fn():
            raise ValueError("sync in async mode 2")

        wrapped = d.wrap_fire_and_forget(failing_fn)
        assert wrapped() is None
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)

        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()

    def test_wrap_async_sync_error_no_error_handler_raises(self):
        """wrap() with loop attached re-raises sync errors when no error handler."""
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        d = _CallbackDispatcher()
        d.attach(loop)

        def failing_fn():
            raise ValueError("wrap sync in async mode")

        wrapped = d.wrap(failing_fn)
        with pytest.raises(ValueError, match="wrap sync in async mode"):
            wrapped()

        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()

    def test_wrap_async_coro_error_no_error_handler_raises(self):
        """wrap() with loop attached re-raises coroutine errors when no error handler."""
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        d = _CallbackDispatcher()
        d.attach(loop)

        async def failing_async():
            raise RuntimeError("coro in async mode")

        wrapped = d.wrap(failing_async)
        with pytest.raises(RuntimeError, match="coro in async mode"):
            wrapped()

        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()

    def test_wrap_async_sync_error_error_handler_returns_none(self):
        """wrap() with loop + error handler: sync error is swallowed (returns None)."""
        errors = []
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        d = _CallbackDispatcher()
        d.set_error_handler(lambda e: errors.append(e))
        d.attach(loop)

        def failing_fn():
            raise ValueError("wrap sync with handler")

        wrapped = d.wrap(failing_fn)
        assert wrapped() is None
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)

        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()


# ===================================================================
# _create_unpacking_handler validation errors
# ===================================================================


class TestUnpackingValidation:
    """Unsupported handler signatures raise at registration time."""

    def test_hdl_annotation_on_client_raises(self):
        """ConnectionHdl-annotated handler on a client raises TypeError."""
        from ObscuraProto import _create_unpacking_handler  # pyright: ignore[reportPrivateUsage]

        def bad_handler(hdl: op.ConnectionHdl):
            pass

        with pytest.raises(TypeError, match="client"):
            _create_unpacking_handler(bad_handler, receives_hdl_from_native=False)

    def test_mixed_payload_and_unpack_raises(self):
        """Mixing Payload and auto-unpack params raises TypeError."""
        from ObscuraProto import _create_unpacking_handler  # pyright: ignore[reportPrivateUsage]

        def bad_handler(name: str, payload: op.Payload):
            pass

        with pytest.raises(TypeError, match="mix"):
            _create_unpacking_handler(bad_handler, receives_hdl_from_native=False)

    def test_unsupported_type_hint_raises_when_invoked(self):
        """Unsupported type hint raises a descriptive TypeError at invocation."""
        from ObscuraProto import _create_unpacking_handler  # pyright: ignore[reportPrivateUsage]

        def bad_handler(value: object):
            pass

        wrapped = _create_unpacking_handler(bad_handler, receives_hdl_from_native=False)
        payload = op.PayloadBuilder(0x7777).add_param("ignored").build()
        with pytest.raises(TypeError, match="OpCode 0x7777.*bad_handler"):
            wrapped(payload)

    def test_unpack_read_error_raises_descriptive(self):
        """Reader underflow (read more than payload has) is reported, not swallowed."""
        from ObscuraProto import _create_unpacking_handler  # pyright: ignore[reportPrivateUsage]

        def handler(name: str):
            pass

        wrapped = _create_unpacking_handler(handler, receives_hdl_from_native=False)
        empty = op.PayloadBuilder(0x7778).build()
        with pytest.raises(TypeError, match="OpCode 0x7778.*handler"):
            wrapped(empty)


# ===================================================================
# Server.send and async_start_stream with op_code
# ===================================================================


class TestRequestUnpackingValidation:
    """_create_request_unpacking_handler error paths (client-side, receives no hdl)."""

    @staticmethod
    def _make_reader():
        """Build a PayloadReader wrapping an empty payload."""
        payload = op.PayloadBuilder(0x0000).build()
        return op.PayloadReader(payload)

    def test_request_handler_non_payload_return_raises(self):
        """A request handler returning a non-Payload raises TypeError."""
        from ObscuraProto import _create_request_unpacking_handler  # pyright: ignore[reportPrivateUsage]

        def bad_handler() -> str:
            return "not a payload"

        wrapped = _create_request_unpacking_handler(bad_handler, receives_hdl_from_native=False)
        with pytest.raises(TypeError, match="must return a 'Payload'"):
            wrapped(self._make_reader())

    def test_request_handler_coroutine_return_passthrough(self):
        """A coroutine return is passed through untouched (dispatcher handles it)."""
        from ObscuraProto import _create_request_unpacking_handler  # pyright: ignore[reportPrivateUsage]

        async def async_handler() -> op.Payload:
            return op.PayloadBuilder(0x0001).build()

        wrapped = _create_request_unpacking_handler(async_handler, receives_hdl_from_native=False)
        result = wrapped(self._make_reader())
        import inspect as _inspect

        assert _inspect.iscoroutine(result)
        result.close()

    def test_request_handler_unsupported_type_hint_returns_error_payload(self):
        """Unsupported type hint in a request handler yields an error Payload."""
        from ObscuraProto import _create_request_unpacking_handler  # pyright: ignore[reportPrivateUsage]

        def bad_handler(value: object):
            pass

        wrapped = _create_request_unpacking_handler(bad_handler, receives_hdl_from_native=False)
        result = wrapped(self._make_reader())
        assert isinstance(result, op.Payload)
        assert result.op_code == 0x0000


class TestServerSendAndStreamOpcode:
    """server.send() and async_start_stream(op_code) delegate correctly."""

    def test_server_send(self, crypto_init, capsys):
        """server.send(hdl, payload) forwards to the C++ backend."""
        sent = []

        class _FakeBackend:
            def send(self, hdl, payload):
                sent.append((hdl, payload))

        server = op.Server.__new__(op.Server)
        server._server = _FakeBackend()
        payload = op.PayloadBuilder(0x1111).build()
        server.send("hdl-1", payload)
        assert sent == [("hdl-1", payload)]
        capsys.readouterr()

    def test_server_async_start_stream_with_opcode(self):
        class _FakeBackend:
            def start_stream(self, hdl, *args):
                return _FakeCppStream()

        server = op.Server.__new__(op.Server)
        server._server = _FakeBackend()
        server._dispatcher = _CallbackDispatcher()

        async def run():
            stream = await server.async_start_stream("hdl", 0x3001)
            assert isinstance(stream, op.Stream)
            await stream.async_cancel()

        asyncio.run(run())

    def test_client_async_start_stream_with_opcode(self):
        class _FakeBackend:
            def start_stream(self, *args):
                return _FakeCppStream()

        client = op.Client.__new__(op.Client)
        client._client = _FakeBackend()
        client._dispatcher = _CallbackDispatcher()

        async def run():
            stream = await client.async_start_stream(0x3001)
            assert isinstance(stream, op.Stream)
            await stream.async_end()

        asyncio.run(run())
