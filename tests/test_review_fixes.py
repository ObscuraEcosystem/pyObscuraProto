"""
Tests for the round-2 review fixes in pyObscuraProto.

Covers the functionality added/fixed in this iteration:
  - Exception mapping: ObscuraProto.TimeoutError / InvalidArgument / LogicError
    subclass the Python builtins and C++ exceptions translate correctly
    (bindings.cpp exception registration + the new timeout_ms overloads).
  - Deadlock fixes: the thread-local callback flag rejects sync_request /
    Server.stop / Client.disconnect from inside callbacks; a wedged async
    handler does not stall the process (GIL released while polling).
  - Awaitable futures: _await_cpp_future / _consume_cpp_future semantics
    (correct payload, Python-side timeout, single-use).
  - Unified executor: FIFO ordering of stream ops under concurrent submission
    and lazy re-creation of the pools after _shutdown_executors().
  - RateLimiter (token bucket + sliding windows + register/unregister/cleanup).
  - SecureBuffer (zero-initialized allocations, round-trip, clear, resize).
  - Crypto.DecryptedResult (encrypt/decrypt round-trip with counter).

These tests must not break the existing 218-test suite.
"""

import asyncio
import builtins
import os
import socket
import sys
import threading
import time
import warnings

import pytest

src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, src_dir)

try:
    import ObscuraProto as op
    from ObscuraProto import (  # pyright: ignore[reportPrivateUsage]
        _abandon_timed_out_future,
        _await_cpp_future,
        _bindings,  # pyright: ignore[reportPrivateUsage]
        _CallbackDispatcher,
        _consume_cpp_future,
        _create_request_unpacking_handler,
        _get_executor,
        _get_request_executor,
        _run_in_stream_executor,
        _set_callback_thread_flag,
        _shutdown_executors,
        _wait_future_with_polling,
    )
except ImportError as e:
    pytest.fail(f"Could not import ObscuraProto: {e}", pytrace=False)

# pyright: reportPrivateUsage=false
# pyright: reportOptionalCall=false
# pyright: reportOptionalMemberAccess=false


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
# 1. Exception mapping
# ===================================================================


class TestExceptionMapping:
    """ObscuraProto exceptions subclass Python builtins and C++ errors translate."""

    def test_timeout_error_subclasses_builtin_timeout(self):
        """ObscuraProto.TimeoutError is a subclass of builtins.TimeoutError."""
        assert issubclass(op.TimeoutError, builtins.TimeoutError)
        assert issubclass(_bindings.TimeoutError, builtins.TimeoutError)
        # A plain `except TimeoutError` catches it.
        try:
            raise op.TimeoutError("boom")
        except TimeoutError:
            pass
        else:
            pytest.fail("op.TimeoutError was not caught by a builtin except clause")

    def test_invalid_argument_subclasses_valueerror_not_logicexception(self):
        """InvalidArgument subclasses ValueError but NOT LogicError."""
        assert issubclass(op.InvalidArgument, ValueError)
        assert not issubclass(op.InvalidArgument, op.LogicError)
        with pytest.raises(ValueError):
            raise op.InvalidArgument("bad arg")

    def test_logic_error_subclasses_runtimeerror(self):
        """LogicError subclasses RuntimeError but not ValueError."""
        assert issubclass(op.LogicError, RuntimeError)
        assert not issubclass(op.LogicError, ValueError)
        with pytest.raises(RuntimeError):
            raise op.LogicError("logic")

    def test_sync_request_without_connection_raises_logic_error(self, crypto_init):
        """C++ sync_request on a never-connected client raises LogicError."""
        kp = op.Crypto.generate_sign_keypair()
        client = _bindings.WsClient(kp)
        payload = op.PayloadBuilder(0x1111).add_param("x").build()
        with pytest.raises(op.LogicError):
            client.sync_request(payload)
        # The base-class catch also works (RuntimeError).
        with pytest.raises(RuntimeError):
            client.sync_request(payload)

    def test_encrypt_invalid_key_raises_invalid_argument(self, crypto_init):
        """C++ InvalidArgument (wrong key size) maps to op.InvalidArgument."""
        payload = op.PayloadBuilder(0x1234).add_param("x").build()
        with pytest.raises(op.InvalidArgument):
            op.Crypto.encrypt(payload, 1, [1, 2, 3])  # not a 32-byte key
        # The ValueError base-class catch also works.
        with pytest.raises(ValueError):
            op.Crypto.encrypt(payload, 1, [1, 2, 3])

    def test_sync_request_timeout_ms_raises_timeout_error(self, crypto_init, capsys):
        """sync_request(payload, timeout_ms) raises TimeoutError after ~timeout."""
        port = _next_port()
        server = op.Server()
        server.start(port)
        time.sleep(0.3)

        client = op.Client(server.public_key)
        ready = threading.Event()
        client.on_ready(ready.set)

        try:
            client.connect(f"ws://localhost:{port}")
            assert ready.wait(timeout=5), "Client did not become ready"

            # No handler registered for 0x9999, so the request is never answered.
            payload = op.PayloadBuilder(0x9999).build()
            t0 = time.monotonic()
            with pytest.raises(op.TimeoutError):
                client._client.sync_request(payload, 250)
            elapsed = time.monotonic() - t0
            assert elapsed >= 0.15, f"Timed out too early: {elapsed:.3f}s"
            assert elapsed < 5.0, f"Timed out too late: {elapsed:.3f}s"
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_sync_request_timeout_ms_zero_responds(self, crypto_init, capsys):
        """timeout_ms=0 means unlimited: a responding server returns normally."""
        port = _next_port()
        server = op.Server()

        @server.on_anon_request(0x3401)
        def echo_handler(a: int) -> op.Payload:
            return op.PayloadBuilder(0x3402).add_param(a + 1).build()

        server.start(port)
        time.sleep(0.3)

        client = op.Client(server.public_key)
        ready = threading.Event()
        client.on_ready(ready.set)

        try:
            client.connect(f"ws://localhost:{port}")
            assert ready.wait(timeout=5), "Client did not become ready"

            payload = op.PayloadBuilder(0x3401).add_param(41).build()
            response = client._client.sync_request(payload, 0)
            assert response.op_code == 0x3402
            assert op.PayloadReader(response).read_int() == 42
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_async_request_timeout_ms_zero_responds(self, crypto_init, capsys):
        """async_request(payload, timeout_ms=0) means unlimited: response resolves."""
        port = _next_port()
        server = op.Server()

        @server.on_anon_request(0x3403)
        def echo_handler(a: int) -> op.Payload:
            return op.PayloadBuilder(0x3404).add_param(a * 2).build()

        server.start(port)
        time.sleep(0.3)

        client = op.Client(server.public_key)
        ready = threading.Event()
        client.on_ready(ready.set)

        try:
            client.connect(f"ws://localhost:{port}")
            assert ready.wait(timeout=5), "Client did not become ready"

            future = client._client.async_request(op.PayloadBuilder(0x3403).add_param(21).build(), 0)
            response = future.get()
            assert response.op_code == 0x3404
            assert op.PayloadReader(response).read_int() == 42
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_async_request_timeout_ms_overload_raises_on_get(self, crypto_init, capsys):
        """CppPayloadFuture from async_request(payload, timeout_ms) resolves with TimeoutError."""
        port = _next_port()
        server = op.Server()
        server.start(port)
        time.sleep(0.3)

        client = op.Client(server.public_key)
        ready = threading.Event()
        client.on_ready(ready.set)

        try:
            client.connect(f"ws://localhost:{port}")
            assert ready.wait(timeout=5), "Client did not become ready"

            future = client._client.async_request(op.PayloadBuilder(0x9998).build(), 200)
            t0 = time.monotonic()
            with pytest.raises(op.TimeoutError):
                future.get()
            elapsed = time.monotonic() - t0
            assert elapsed >= 0.1, f"Timed out too early: {elapsed:.3f}s"
            assert elapsed < 5.0, f"Timed out too late: {elapsed:.3f}s"
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()


# ===================================================================
# 2. Deadlock fixes
# ===================================================================


class TestCallbackDeadlockGuards:
    """Blocking calls from callback/IO threads must raise LogicError, not deadlock."""

    def test_sync_request_from_callback_thread_raises(self):
        """Client.sync_request raises LogicError while the callback flag is set."""
        kp = op.Crypto.generate_sign_keypair()
        client = op.Client(kp.public_key)
        _set_callback_thread_flag(True)
        try:
            with pytest.raises(op.LogicError, match="callback"):
                client.sync_request(op.PayloadBuilder(1).build())
        finally:
            _set_callback_thread_flag(False)

    def test_server_stop_from_callback_thread_raises(self):
        """Server.stop raises LogicError while the callback flag is set (self-join guard)."""
        server = op.Server()
        _set_callback_thread_flag(True)
        try:
            with pytest.raises(op.LogicError, match="self-join"):
                server.stop()
        finally:
            _set_callback_thread_flag(False)

    def test_client_disconnect_from_callback_thread_raises(self):
        """Client.disconnect raises LogicError while the callback flag is set."""
        kp = op.Crypto.generate_sign_keypair()
        client = op.Client(kp.public_key)
        _set_callback_thread_flag(True)
        try:
            with pytest.raises(op.LogicError, match="self-join"):
                client.disconnect()
        finally:
            _set_callback_thread_flag(False)

    def test_server_sync_request_from_callback_thread_raises(self):
        """Server.sync_request raises LogicError while the callback flag is set."""
        server = op.Server()
        _set_callback_thread_flag(True)
        try:
            with pytest.raises(op.LogicError, match="callback"):
                server.sync_request(None, op.PayloadBuilder(1).build())
        finally:
            _set_callback_thread_flag(False)

    def test_server_sync_request_to_identity_from_callback_thread_raises(self):
        """Server.sync_request_to_identity raises LogicError from a callback thread."""
        server = op.Server()
        _set_callback_thread_flag(True)
        try:
            with pytest.raises(op.LogicError, match="callback"):
                server.sync_request_to_identity(None, op.PayloadBuilder(1).build())
        finally:
            _set_callback_thread_flag(False)

    def test_sync_request_warns_when_called_from_event_loop(self):
        """sync_request called on an event-loop thread emits a UserWarning."""
        kp = op.Crypto.generate_sign_keypair()
        client = op.Client(kp.public_key)
        server = op.Server()

        class _FakeBackend:
            def sync_request(self, hdl_or_payload, payload=None):
                # Server path: (hdl, payload); client path: (payload,).
                return None

            def sync_request_to_identity(self, identity_pk, payload):
                return None

        client._client = _FakeBackend()
        server._server = _FakeBackend()

        async def run():
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                client.sync_request(op.PayloadBuilder(1).build())
                server.sync_request(None, op.PayloadBuilder(1).build())
                server.sync_request_to_identity(None, op.PayloadBuilder(1).build())
                blocking = [w for w in caught if "blocking" in str(w.message)]
                return len(blocking)

        assert asyncio.run(run()) == 3

    def test_real_callback_guard_raises_logic_error(self, crypto_init, capsys):
        """Inside a real payload handler all three blocking calls raise LogicError."""
        port = _next_port()
        results = []
        results_lock = threading.Lock()
        handler_done = threading.Event()
        client_ready = threading.Event()

        server = op.Server()
        client = op.Client(server.public_key)

        @server.on_anon_payload(0x4101)
        def handle(hdl: op.ConnectionHdl, val: str):
            outcomes = []
            try:
                client.sync_request(op.PayloadBuilder(0x4102).build())
                outcomes.append("sync_request:no-raise")
            except op.LogicError:
                outcomes.append("sync_request:LogicError")
            try:
                server.stop()
                outcomes.append("stop:no-raise")
            except op.LogicError:
                outcomes.append("stop:LogicError")
            try:
                client.disconnect()
                outcomes.append("disconnect:no-raise")
            except op.LogicError:
                outcomes.append("disconnect:LogicError")
            with results_lock:
                results.extend(outcomes)
            handler_done.set()

        server.start(port)
        time.sleep(0.3)

        @client.on_ready
        def on_ready():
            client_ready.set()

        try:
            client.connect(f"ws://localhost:{port}")
            assert client_ready.wait(timeout=5), "Client did not become ready"

            client.send(op.PayloadBuilder(0x4101).add_param("trigger").build())
            # Without the guards this would hang; the event proves the handler returned.
            assert handler_done.wait(timeout=5), "Handler did not complete (would be a deadlock)"
            with results_lock:
                assert results == ["sync_request:LogicError", "stop:LogicError", "disconnect:LogicError"]
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_server_survives_handler_exception_second_connection(self, crypto_init, capsys):
        """After a handler throws (no on_error), a second connection still works."""
        port = _next_port()
        op_bad = 0x4201
        op_good = 0x4202
        good_received = threading.Event()

        server = op.Server()

        @server.on_anon_payload(op_bad)
        def bad_handler(hdl: op.ConnectionHdl, val: str):
            raise RuntimeError("boom without on_error")

        @server.on_anon_payload(op_good)
        def good_handler(hdl: op.ConnectionHdl, val: str):
            good_received.set()

        server.start(port)
        time.sleep(0.3)

        def make_client():
            c = op.Client(server.public_key)
            ready = threading.Event()
            c.on_ready(ready.set)
            c.connect(f"ws://localhost:{port}")
            assert ready.wait(timeout=5), "Client did not become ready"
            return c

        c1 = None
        c2 = None
        try:
            c1 = make_client()
            c1.send(op.PayloadBuilder(op_bad).add_param("first").build())
            time.sleep(0.5)  # give the native layer time to swallow the exception

            c2 = make_client()
            c2.send(op.PayloadBuilder(op_good).add_param("second").build())
            assert good_received.wait(timeout=5), "Second connection was not serviced"
        finally:
            if c1 is not None:
                c1.disconnect()
            if c2 is not None:
                c2.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()


class TestWaitFutureWithPolling:
    """_wait_future_with_polling: parameterizable timeout + GIL release while waiting."""

    def test_timeout_parameterizable(self):
        """A non-default timeout (0.25s) is honoured, not the ~5s default."""
        release = threading.Event()

        def slow():
            release.wait(timeout=30)
            return "late"

        future = _get_executor().submit(slow)
        t0 = time.monotonic()
        try:
            with pytest.raises(TimeoutError, match="0.25s"):
                _wait_future_with_polling(future, 0.25)
        finally:
            release.set()
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.2, f"Timed out too early: {elapsed:.3f}s"
        assert elapsed < 5.0, f"Timed out too late: {elapsed:.3f}s"

    def test_releases_gil_while_waiting(self):
        """A spinner thread keeps making progress while the polling wait blocks."""
        release = threading.Event()
        stop = threading.Event()
        ticks = [0]

        def slow():
            release.wait(timeout=30)
            return "late"

        def spinner():
            while not stop.is_set():
                ticks[0] += 1
                time.sleep(0.001)

        future = _get_executor().submit(slow)
        spinner_thread = threading.Thread(target=spinner, daemon=True)
        spinner_thread.start()
        try:
            with pytest.raises(TimeoutError):
                _wait_future_with_polling(future, 0.5)
        finally:
            release.set()
            stop.set()
            spinner_thread.join(timeout=2)
        assert ticks[0] > 50, f"Spinner was starved: only {ticks[0]} ticks in 0.5s"

    def test_returns_result_when_future_completes(self):
        """A future that completes within the timeout yields its result."""

        def quick():
            time.sleep(0.1)
            return "done"

        future = _get_executor().submit(quick)
        assert _wait_future_with_polling(future, 5.0) == "done"

    def test_wedged_async_handler_times_out_and_loop_responsive(self):
        """A wedged async handler times out; the event loop stays responsive."""
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        try:
            dispatcher = _CallbackDispatcher(result_timeout=0.3)
            dispatcher.attach(loop)

            async def wedged():
                await asyncio.sleep(30)

            wrapped = dispatcher.wrap(wedged)
            t0 = time.monotonic()
            with pytest.raises(TimeoutError, match="0.3s"):
                wrapped()
            elapsed = time.monotonic() - t0
            assert elapsed >= 0.2, f"Timed out too early: {elapsed:.3f}s"
            assert elapsed < 5.0, f"Timed out too late: {elapsed:.3f}s"

            # The loop must still schedule coroutines after the wedged handler.
            async def ping():
                return "pong"

            future = asyncio.run_coroutine_threadsafe(ping(), loop)
            assert future.result(timeout=2) == "pong"
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)
            loop.close()


# ===================================================================
# 3. Awaitable C++ futures
# ===================================================================


class TestAwaitableFutures:
    """_await_cpp_future / _consume_cpp_future semantics."""

    def test_await_async_request_returns_payload(self, crypto_init, capsys):
        """await client.async_request returns the correct Payload (e2e)."""
        port = _next_port()
        server = op.Server()

        @server.on_anon_request(0x4301)
        def echo(a: str) -> op.Payload:
            return op.PayloadBuilder(0x4302).add_param(f"echo:{a}").build()

        server.start(port)
        time.sleep(0.3)

        client = op.Client(server.public_key)
        ready = threading.Event()
        client.on_ready(ready.set)

        async def run():
            response = await client.async_request(op.PayloadBuilder(0x4301).add_param("hi").build(), timeout=5.0)
            return response

        try:
            client.connect(f"ws://localhost:{port}")
            assert ready.wait(timeout=5), "Client did not become ready"
            response = asyncio.run(run())
            assert response.op_code == 0x4302
            assert op.PayloadReader(response).read_string() == "echo:hi"
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_python_timeout_raises_timeout_error(self, crypto_init, capsys):
        """A Python-side async_request timeout raises op.TimeoutError promptly."""
        port = _next_port()
        server = op.Server()
        server.start(port)
        time.sleep(0.3)

        client = op.Client(server.public_key)
        ready = threading.Event()
        client.on_ready(ready.set)

        async def run():
            # No handler for 0x4309, so the response never arrives.
            await client.async_request(op.PayloadBuilder(0x4309).build(), timeout=0.25)

        try:
            client.connect(f"ws://localhost:{port}")
            assert ready.wait(timeout=5), "Client did not become ready"
            t0 = time.monotonic()
            with pytest.raises(op.TimeoutError):
                asyncio.run(run())
            elapsed = time.monotonic() - t0
            assert elapsed >= 0.15, f"Timed out too early: {elapsed:.3f}s"
            assert elapsed < 5.0, f"Timed out too late: {elapsed:.3f}s"
        finally:
            client.disconnect()
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_double_await_same_future_raises_logic_error(self):
        """CppPayloadFuture is single-use: a second await raises LogicError."""

        class _SingleUseFuture:
            def __init__(self):
                self._ready = False

            def ready(self):
                if not self._ready:
                    self._ready_at = time.monotonic() + 0.02
                    self._ready = True
                return time.monotonic() >= self._ready_at

            def get(self):
                return "payload"

        async def run():
            future = _SingleUseFuture()
            first = await _await_cpp_future(future, timeout=2.0)
            assert first == "payload"
            with pytest.raises(op.LogicError, match="single-use"):
                await _await_cpp_future(future, timeout=2.0)

        asyncio.run(run())

    def test_consume_cpp_future_is_single_use(self):
        """_consume_cpp_future raises LogicError on a second get() of the same future."""

        class _ReadyFuture:
            def ready(self):
                return True

            def get(self):
                return "value"

        future = _ReadyFuture()
        assert _consume_cpp_future(future) == "value"
        with pytest.raises(op.LogicError, match="single-use"):
            _consume_cpp_future(future)

    def test_timeout_does_not_consume_future(self):
        """On the timeout path get() must never be called (keeps P1-1 behaviour)."""

        class _NeverReadyFuture:
            def ready(self):
                return False

            def get(self):
                raise AssertionError("get() must not be called on a timeout path")

        async def run():
            with pytest.raises(op.TimeoutError):
                await _await_cpp_future(_NeverReadyFuture(), timeout=0.1)

        asyncio.run(run())

    def test_await_with_explicit_loop_argument(self):
        """_await_cpp_future accepts an explicitly passed event loop."""

        class _ReadyFuture:
            def ready(self):
                return True

            def get(self):
                return "value"

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(_await_cpp_future(_ReadyFuture(), loop=loop, timeout=2.0))
            assert result == "value"
        finally:
            loop.close()

    def test_request_handler_payload_reader_annotation(self):
        """A request handler annotated with PayloadReader receives the reader."""

        def handler(reader: op.PayloadReader) -> op.Payload:
            return op.PayloadBuilder(0x0001).add_param(reader.read_int()).build()

        wrapped = _create_request_unpacking_handler(handler, receives_hdl_from_native=False)
        payload = op.PayloadBuilder(0x0101).add_param(123).build()
        result = wrapped(op.PayloadReader(payload))
        assert isinstance(result, op.Payload)
        assert op.PayloadReader(result).read_int() == 123


# ===================================================================
# 4. Unified executor
# ===================================================================


class TestUnifiedExecutor:
    """Single stream executor keeps FIFO order; pools re-create after shutdown."""

    def test_stream_ops_ordered_under_concurrent_submission(self):
        """write -> end -> cancel order survives asyncio.gather submission."""

        class _FakeCppStream:
            def __init__(self):
                self.ops = []

            def get_stream_id(self):
                return 1

            def get_op_code(self):
                return None

            def write(self, data):
                self.ops.append(("write", bytes(data)))

            def end(self):
                self.ops.append(("end",))

            def cancel(self):
                self.ops.append(("cancel",))

            def set_data_handler(self, fn):
                pass

            def set_end_handler(self, fn):
                pass

            def set_cancel_handler(self, fn):
                pass

        stream = op.Stream(_FakeCppStream())

        async def run():
            for i in range(25):
                await asyncio.gather(
                    stream.async_write(b"chunk-%d" % i),
                    stream.async_end(),
                    stream.async_cancel(),
                )

        asyncio.run(run())
        ops = stream._s.ops
        assert len(ops) == 75
        for i in range(0, len(ops), 3):
            assert ops[i : i + 3] == [("write", b"chunk-%d" % (i // 3)), ("end",), ("cancel",)]

    def test_executors_recreated_after_shutdown(self):
        """_shutdown_executors() then _get_executor() re-creates working pools."""
        stream_executor = _get_executor()
        request_executor = _get_request_executor()

        _shutdown_executors()

        new_stream = _get_executor()
        new_request = _get_request_executor()
        assert new_stream is not None and new_request is not None
        assert new_stream is not stream_executor
        assert new_request is not request_executor
        # The re-created pools actually execute work.
        assert new_request.submit(lambda: 42).result(timeout=5) == 42


# ===================================================================
# 5. RateLimiter
# ===================================================================


class TestRateLimiter:
    """Token bucket, sliding windows and connection registration."""

    @staticmethod
    def _config(mps=1, burst=3, conns_per_min=2, handshake_per_min=2):
        cfg = op.RateLimitConfig()
        cfg.enabled = True
        cfg.messages_per_second = mps
        cfg.burst_size = burst
        cfg.handshake_attempts_per_minute = handshake_per_min
        cfg.connections_per_minute = conns_per_min
        return cfg

    def test_token_bucket_allows_burst_rejects_overflow(self):
        """burst_size messages pass, the burst+1 check is rejected."""
        rl = op.RateLimiter(self._config(mps=1, burst=3))
        conn_id = 7
        allowed = 0
        while rl.check_message_rate(conn_id):
            rl.record_message(conn_id)
            allowed += 1
        assert allowed == 3, f"Expected 3 allowed messages, got {allowed}"
        assert not rl.check_message_rate(conn_id)

    def test_sliding_window_connections_per_minute(self):
        """connections_per_minute connections pass, the next one is rejected."""
        rl = op.RateLimiter(self._config(conns_per_min=2))
        ip = "203.0.113.7"
        allowed = 0
        while rl.check_connection_rate(ip):
            rl.record_connection(ip)
            allowed += 1
        assert allowed == 2, f"Expected 2 allowed connections, got {allowed}"
        assert not rl.check_connection_rate(ip)

    def test_sliding_window_handshake_per_minute(self):
        """Handshake window is enforced independently of the connection window."""
        rl = op.RateLimiter(self._config(handshake_per_min=1))
        ip = "198.51.100.9"
        assert rl.check_handshake_rate(ip)
        rl.record_handshake(ip)
        assert not rl.check_handshake_rate(ip)

    def test_register_unregister_active_total(self):
        """register_connection/unregister_connection drive active_total."""
        rl = op.RateLimiter(self._config())
        cid = rl.register_connection("192.0.2.1")
        assert isinstance(cid, int)
        assert rl.active_total() == 1
        cid2 = rl.register_connection("192.0.2.2")
        assert rl.active_total() == 2
        rl.unregister_connection(cid, "192.0.2.1")
        assert rl.active_total() == 1
        rl.unregister_connection(cid2, "192.0.2.2")
        assert rl.active_total() == 0

    def test_cleanup_preserves_active_connections(self):
        """cleanup() must not crash or drop entries with active connections."""
        rl = op.RateLimiter(self._config(conns_per_min=2))
        ip = "192.0.2.55"
        cid = rl.register_connection(ip)
        rl.record_connection(ip)
        rl.cleanup()
        assert rl.active_total() == 1
        rl.unregister_connection(cid, ip)
        rl.cleanup()
        assert rl.active_total() == 0

    def test_check_active_connections_per_ip_without_message_rate(self):
        """Per-IP active limit applies when the message-rate config is disabled."""
        rl = op.RateLimiter(self._config(mps=0, conns_per_min=2))
        ip = "192.0.2.80"
        cid = rl.register_connection(ip)
        assert rl.check_active_connections(ip)
        rl.register_connection(ip)
        assert not rl.check_active_connections(ip)  # 2 active >= limit of 2
        rl.unregister_connection(cid, ip)


# ===================================================================
# 6. SecureBuffer
# ===================================================================


class TestSecureBuffer:
    """Zero-initialized allocations, round-trip, clear and resize."""

    def test_constructor_zero_initialized(self):
        """SecureBuffer(n) must be all zero bytes (regression guard for 0xdb bugs)."""
        for n in (1, 8, 64, 128):
            buf = op.SecureBuffer(n)
            assert buf.to_bytes() == b"\x00" * n
            assert len(buf) == n

    def test_from_bytes_to_bytes_roundtrip(self):
        """from_bytes/to_bytes round-trip and bytes()/__len__ agree."""
        buf = op.SecureBuffer()
        data = bytes(range(32))
        buf.from_bytes(data)
        assert buf.to_bytes() == data
        assert bytes(buf) == data
        assert len(buf) == len(data)
        assert not buf.empty()

    def test_clear_zeroes_and_empties(self):
        """clear() wipes contents and resets the size to 0."""
        buf = op.SecureBuffer(16)
        buf.from_bytes(b"A" * 16)
        buf.clear()
        assert buf.size() == 0
        assert buf.empty()
        assert buf.to_bytes() == b""

    def test_resize_larger_preserves_prefix_zeros_tail(self):
        """Growing the buffer keeps the prefix and zero-fills the tail."""
        buf = op.SecureBuffer(4)
        buf.from_bytes(b"\x01\x02\x03\x04")
        buf.resize(8)
        assert buf.size() == 8
        assert buf.to_bytes() == b"\x01\x02\x03\x04\x00\x00\x00\x00"

    def test_resize_smaller_truncates(self):
        """Shrinking the buffer truncates the tail."""
        buf = op.SecureBuffer(8)
        buf.from_bytes(b"\x01\x02\x03\x04\x05\x06\x07\x08")
        buf.resize(3)
        assert buf.size() == 3
        assert buf.to_bytes() == b"\x01\x02\x03"


# ===================================================================
# 7. Crypto.DecryptedResult
# ===================================================================


class TestDecryptedResult:
    """Crypto.encrypt/decrypt round-trip returns a usable DecryptedResult."""

    def test_encrypt_decrypt_roundtrip(self, crypto_init):
        """payload and counter survive an encrypt/decrypt round-trip."""
        key = list(range(32))
        payload = op.PayloadBuilder(0x1357).add_param("secret").add_param(99).build()
        counter = 42

        encrypted = op.Crypto.encrypt(payload, counter, key)
        result = op.Crypto.decrypt(encrypted, key)

        assert isinstance(result, op.DecryptedResult)
        assert result.counter == counter
        assert isinstance(result.payload, op.Payload)
        assert result.payload.op_code == 0x1357
        reader = op.PayloadReader(result.payload)
        assert reader.read_string() == "secret"
        assert reader.read_int() == 99

    def test_decrypt_wrong_key_raises(self, crypto_init):
        """Decrypting with the wrong key raises (tag verification failure)."""
        key = list(range(32))
        wrong_key = list(range(31, -1, -1))
        payload = op.PayloadBuilder(0x2468).add_param("x").build()
        encrypted = op.Crypto.encrypt(payload, 1, key)
        with pytest.raises(RuntimeError):
            op.Crypto.decrypt(encrypted, wrong_key)


# ===================================================================
# 8. Edge-path coverage: fallbacks and error handling in __init__.py
# ===================================================================


class TestEdgePathCoverage:
    """Executor fallbacks, _abandon_timed_out_future resilience, timeout=None."""

    def test_abandon_future_tolerates_cancel_raising(self):
        """_abandon_timed_out_future swallows exceptions from cancel()."""

        class _BadCancel:
            def cancel(self):
                raise RuntimeError("cancel failed")

        # No add_done_callback present: only the cancel path runs.
        _abandon_timed_out_future(_BadCancel())

    def test_abandon_future_tolerates_add_done_callback_raising(self):
        """_abandon_timed_out_future swallows exceptions from add_done_callback."""

        class _BadDoneCallback:
            def cancel(self):
                return True

            def add_done_callback(self, fn):
                raise RuntimeError("callback registration failed")

        _abandon_timed_out_future(_BadDoneCallback())

    def test_abandon_future_cancels_and_reaps_concurrent_future(self):
        """A concurrent.futures.Future is cancelled and its outcome reaped."""

        def late():
            time.sleep(0.2)
            return 42

        future = _get_executor().submit(late)
        _abandon_timed_out_future(future)
        time.sleep(0.3)
        assert future.done()

    def test_await_cpp_future_timeout_none_uses_default(self):
        """timeout=None falls back to the module default (no immediate failure)."""

        class _ReadyFuture:
            def ready(self):
                return True

            def get(self):
                return "ok"

        async def run():
            return await _await_cpp_future(_ReadyFuture(), timeout=None)

        assert asyncio.run(run()) == "ok"

    def test_run_in_stream_executor_falls_back_on_runtime_error(self, monkeypatch):
        """A broken executor submit falls back to asyncio.to_thread."""

        class _BrokenExecutor:
            def submit(self, *args, **kwargs):
                raise RuntimeError("executor is shut down")

        monkeypatch.setattr(op, "_get_executor", lambda: _BrokenExecutor())

        async def run():
            return await _run_in_stream_executor(lambda: "fallback")

        assert asyncio.run(run()) == "fallback"

    def test_await_cpp_future_falls_back_on_runtime_error(self, monkeypatch):
        """A broken request executor falls back to a one-off wait thread."""

        class _ReadyFuture:
            def ready(self):
                return True

            def get(self):
                return "thread-ok"

        class _BrokenExecutor:
            def submit(self, *args, **kwargs):
                raise RuntimeError("executor is shut down")

        monkeypatch.setattr(op, "_get_request_executor", lambda: _BrokenExecutor())

        async def run():
            return await _await_cpp_future(_ReadyFuture(), timeout=5.0)

        assert asyncio.run(run()) == "thread-ok"
