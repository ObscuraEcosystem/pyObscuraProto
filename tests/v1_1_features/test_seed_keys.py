"""
Seed-based key derivation tests for C++ ObscuraProto v1.1.1 (pyObscuraProto).

Covers the v1.1.1 seed API bindings (Crypto.keypair_from_seed /
Crypto.derive_public_key), the TimeoutConfig.request_ms field, C++-owned
request timeouts (TimeoutError), and the Stream noexcept contract after close.
"""

import builtins
import os
import sys
import tempfile
import threading
import time

import pytest

src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
sys.path.insert(0, src_dir)

try:
    import ObscuraProto as op
except ImportError as e:
    pytest.fail(f"Could not import ObscuraProto: {e}", pytrace=False)


@pytest.fixture(scope="module")
def crypto_init():
    op.Crypto.init()


# --- Crypto.keypair_from_seed / Crypto.derive_public_key ---


def test_keypair_from_seed_32_bytes(crypto_init):
    seed = bytes(range(32))
    kp = op.Crypto.keypair_from_seed(seed)
    assert isinstance(kp, op.KeyPair)
    assert len(kp.private_key.data) == 64
    assert len(kp.public_key.data) == 32


def test_keypair_from_seed_wrong_size_raises_valueerror(crypto_init):
    # Wrong-size seeds (including 64 bytes) must raise ValueError:
    # the C++ layer requires a strictly 32-byte seed.
    for bad_len in (31, 33, 64):
        with pytest.raises(ValueError) as excinfo:
            op.Crypto.keypair_from_seed(bytes(bad_len))
        assert isinstance(excinfo.value, op.InvalidArgument)
        assert not isinstance(excinfo.value, op.LogicError)


def test_derive_public_key_64_bytes_ok(crypto_init):
    seed = bytes(range(32))
    kp = op.Crypto.keypair_from_seed(seed)
    pk = op.Crypto.derive_public_key(bytes(kp.private_key.data))
    assert isinstance(pk, op.PublicKey)
    assert len(pk.data) == 32


def test_derive_public_key_wrong_size_raises_valueerror(crypto_init):
    for bad_len in (32, 63):
        with pytest.raises(ValueError) as excinfo:
            op.Crypto.derive_public_key(bytes(bad_len))
        assert isinstance(excinfo.value, op.InvalidArgument)


def test_seed_derivation_is_deterministic(crypto_init):
    seed = b"\x5a" * 32
    kp1 = op.Crypto.keypair_from_seed(seed)
    kp2 = op.Crypto.keypair_from_seed(seed)
    assert bytes(kp1.public_key.data) == bytes(kp2.public_key.data)
    assert bytes(kp1.private_key.data) == bytes(kp2.private_key.data)

    other = op.Crypto.keypair_from_seed(b"\x5b" * 32)
    assert bytes(other.public_key.data) != bytes(kp1.public_key.data)


def test_keypair_from_seed_matches_rfc8032_vector(crypto_init):
    # RFC 8032 (Ed25519) Test 1 — pinned in the C++ test suite.
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    expected_pk = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    expected_sk = seed + expected_pk

    kp = op.Crypto.keypair_from_seed(seed)
    assert bytes(kp.public_key.data) == expected_pk
    assert bytes(kp.private_key.data) == expected_sk

    derived = op.Crypto.derive_public_key(expected_sk)
    assert bytes(derived.data) == expected_pk


def test_derive_public_key_matches_keypair(crypto_init):
    seed = bytes(range(1, 33))
    kp = op.Crypto.keypair_from_seed(seed)
    pk = op.Crypto.derive_public_key(bytes(kp.private_key.data))
    assert bytes(pk.data) == bytes(kp.public_key.data)


# --- TimeoutConfig.request_ms ---


def test_timeout_config_request_ms_default():
    tc = op.TimeoutConfig()
    assert tc.request_ms == 30000


def test_timeout_config_request_ms_settable():
    tc = op.TimeoutConfig()
    tc.request_ms = 1234
    assert tc.request_ms == 1234
    assert tc.request_ms != tc.check_interval_ms


def test_request_ms_parsed_from_yaml():
    yaml_text = "server:\n  timeouts:\n    request_ms: 12345\n    handshake_ms: 7000\n"
    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
        f.write(yaml_text)
        path = f.name
    try:
        cfg = op.Config.from_yaml(path)
        assert cfg.timeouts.request_ms == 12345
        # Unrelated field parsed too (sanity that the section matched).
        assert cfg.timeouts.handshake_ms == 7000
    finally:
        os.unlink(path)


# --- C++-owned request timeouts (TimeoutError) ---


def test_timeout_error_hierarchy():
    # The bindings map ObscuraProto::TimeoutError onto builtins.TimeoutError.
    assert issubclass(op.TimeoutError, builtins.TimeoutError)
    assert issubclass(op.TimeoutError, Exception)
    # NOTE: pybind11 attaches it to builtins.TimeoutError (an OSError subclass),
    # so it is NOT a RuntimeError subclass on the Python side.
    assert not issubclass(op.TimeoutError, RuntimeError)
    assert op.InvalidArgument is not op.TimeoutError


def test_cpp_timeout_error_raises_against_silent_server(crypto_init, capsys):
    """timeout_ms=1 against a silent server raises op.TimeoutError (C++ owner)."""
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()

    server = op.Server()
    server.start(port)
    time.sleep(0.3)

    client = op.Client(server.public_key)
    ready = threading.Event()
    client.on_ready(ready.set)

    try:
        client.connect(f"ws://localhost:{port}")
        assert ready.wait(timeout=5), "Client did not become ready"

        payload = op.PayloadBuilder(0x7777).add_param("x").build()
        with pytest.raises(op.TimeoutError) as excinfo:
            client._client.sync_request(payload, 1)
        # Base-class catches also work.
        assert isinstance(excinfo.value, builtins.TimeoutError)
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        capsys.readouterr()


def test_async_request_python_passthrough_timeout(crypto_init, capsys):
    """Client.async_request forwards timeout -> C++ timeout_ms (C++ owner)."""
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()

    server = op.Server()
    server.start(port)
    time.sleep(0.3)

    client = op.Client(server.public_key)
    ready = threading.Event()
    client.on_ready(ready.set)

    try:
        client.connect(f"ws://localhost:{port}")
        assert ready.wait(timeout=5), "Client did not become ready"

        payload = op.PayloadBuilder(0x7778).add_param("x").build()

        async def run():
            return await client.async_request(payload, timeout=0.05)

        t0 = time.monotonic()
        with pytest.raises(op.TimeoutError):
            import asyncio

            asyncio.run(run())
        elapsed = time.monotonic() - t0
        assert elapsed < 5.0, f"Timed out too late: {elapsed:.3f}s"
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        capsys.readouterr()


# --- High-level Python passthrough (Client.sync_request(timeout_ms), timeout<=0 unlimited) ---


def test_client_sync_request_timeout_ms_passthrough_success(crypto_init, capsys):
    """Client.sync_request(payload, timeout_ms) forwards to the C++ overload and returns."""
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()

    server = op.Server()
    echo_called = threading.Event()

    @server.on_anon_request(0x7E01)
    def echo_handler(a: int) -> op.Payload:
        echo_called.set()
        return op.PayloadBuilder(0x7E02).add_param(a * 3).build()

    server.start(port)
    time.sleep(0.3)

    client = op.Client(server.public_key)
    ready = threading.Event()
    client.on_ready(ready.set)

    try:
        client.connect(f"ws://localhost:{port}")
        assert ready.wait(timeout=5), "Client did not become ready"

        payload = op.PayloadBuilder(0x7E01).add_param(14).build()
        response = client.sync_request(payload, timeout_ms=5000)
        assert response.op_code == 0x7E02
        assert op.PayloadReader(response).read_int() == 42
        assert echo_called.is_set()
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        capsys.readouterr()


def test_client_sync_request_timeout_ms_raises_on_silent_server(crypto_init, capsys):
    """High-level Client.sync_request(payload, timeout_ms) surfaces op.TimeoutError."""
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()

    server = op.Server()
    server.start(port)
    time.sleep(0.3)

    client = op.Client(server.public_key)
    ready = threading.Event()
    client.on_ready(ready.set)

    try:
        client.connect(f"ws://localhost:{port}")
        assert ready.wait(timeout=5), "Client did not become ready"

        payload = op.PayloadBuilder(0x7E11).build()
        t0 = time.monotonic()
        with pytest.raises(op.TimeoutError):
            client.sync_request(payload, timeout_ms=250)
        elapsed = time.monotonic() - t0
        assert elapsed < 5.0, f"Timed out too late: {elapsed:.3f}s"
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        capsys.readouterr()


def test_async_request_timeout_zero_is_unlimited(crypto_init, capsys):
    """timeout<=0 must disable the Python wait_for guard, not raise TimeoutError fast.

    A responding server with a slow handler (300ms) would trip a Python-side
    wait_for of 50ms, but with timeout=0 the C++ layer owns the timeout, so
    the response must come through.
    """
    import asyncio
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()

    server = op.Server()

    @server.on_anon_request(0x7E21)
    def slow_echo_handler(a: int) -> op.Payload:
        time.sleep(0.3)
        return op.PayloadBuilder(0x7E22).add_param(a + 1).build()

    server.start(port)
    time.sleep(0.3)

    client = op.Client(server.public_key)
    ready = threading.Event()
    client.on_ready(ready.set)

    try:
        client.connect(f"ws://localhost:{port}")
        assert ready.wait(timeout=5), "Client did not become ready"

        payload = op.PayloadBuilder(0x7E21).add_param(41).build()

        async def run():
            return await client.async_request(payload, timeout=0)

        t0 = time.monotonic()
        response = asyncio.run(run())
        elapsed = time.monotonic() - t0
        assert response.op_code == 0x7E22
        assert op.PayloadReader(response).read_int() == 42
        assert elapsed >= 0.2, f"Responded too fast ({elapsed:.3f}s): handler sleep not honored"
        assert elapsed < 5.0, f"Responded too late: {elapsed:.3f}s"
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        capsys.readouterr()


def test_async_request_negative_timeout_is_unlimited(crypto_init, capsys):
    """timeout=-1 behaves like timeout=0: no Python wait_for, C++ owns timeout."""
    import asyncio
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()

    server = op.Server()

    @server.on_anon_request(0x7E31)
    def echo_handler(a: int) -> op.Payload:
        return op.PayloadBuilder(0x7E32).add_param(a + 2).build()

    server.start(port)
    time.sleep(0.3)

    client = op.Client(server.public_key)
    ready = threading.Event()
    client.on_ready(ready.set)

    try:
        client.connect(f"ws://localhost:{port}")
        assert ready.wait(timeout=5), "Client did not become ready"

        payload = op.PayloadBuilder(0x7E31).add_param(40).build()

        async def run():
            return await client.async_request(payload, timeout=-1)

        response = asyncio.run(run())
        assert response.op_code == 0x7E32
        assert op.PayloadReader(response).read_int() == 42
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        capsys.readouterr()


# --- Stream noexcept contract ---


def test_stream_ops_noexcept_with_broken_send_fn(crypto_init):
    """write/end/cancel never raise even if the C++ send path fails."""

    def broken_send(payload):
        raise RuntimeError("transport is gone")

    cpp = op.CppStream(1, broken_send)
    cpp.write(b"data")
    cpp.end()
    cpp.cancel()


def test_stream_ops_noexcept_after_disconnect(crypto_init, capsys):
    """write/end/cancel after the client disconnects silently drop (no throw)."""
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()

    server = op.Server()
    server.start(port)
    time.sleep(0.3)

    client = op.Client(server.public_key)
    ready = threading.Event()
    client.on_ready(ready.set)

    try:
        client.connect(f"ws://localhost:{port}")
        assert ready.wait(timeout=5), "Client did not become ready"
        stream = client.start_stream()
        client.disconnect()
        time.sleep(0.1)
        stream.write(b"late")
        stream.end()
        stream.cancel()
    finally:
        server.stop()
        time.sleep(0.1)
        capsys.readouterr()
