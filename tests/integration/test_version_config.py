"""
Integration tests for version configuration: V1.0-only server, V1.0-only client,
V1.1-only both sides, and no common version resulting in failed connection.
Ported from ObscuraProto/tests/integration/version_config_test.cpp (4 tests).
"""

import os
import sys
import threading
import time

import pytest

src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
sys.path.insert(0, src_dir)

try:
    import ObscuraProto as op
    from ObscuraProto import _bindings
except ImportError as e:
    pytest.fail(f"Could not import ObscuraProto: {e}", pytrace=False)

OP_ECHO = 0x0001

# Global port counter (thread-safe)
_port_counter = 30500
_port_lock = threading.Lock()


def _next_port():
    global _port_counter
    with _port_lock:
        p = _port_counter
        _port_counter += 1
        return p


@pytest.fixture(scope="module")
def crypto_init():
    """Ensure Crypto is initialized once per module."""
    op.Crypto.init()


def test_v1_0_only_server(crypto_init, capsys):
    """
    Server supports only V1.0. Client uses default (both V1.1 and V1.0).
    Streams should work without op_code (V1.0 behavior).
    """
    port = _next_port()
    print(f"\n[TEST] test_v1_0_only_server on port {port}")

    server_cfg = op.Config.with_defaults()
    server_cfg.supported_versions = [op.V1_0]

    client_ready = threading.Event()
    server_got_stream = threading.Event()
    server_got_data = threading.Event()

    server = op.Server(config=server_cfg)
    client = op.Client(server.public_key)

    @server.on_incoming_stream
    def handle_incoming(stream: op.Stream):
        print(f"[SERVER] Incoming stream, op_code={stream.op_code}")
        assert stream.op_code is None, f"Expected None for V1.0, got {stream.op_code}"
        server_got_stream.set()

        @stream.on_data
        def on_data(data: bytes):
            msg = data.decode()
            print(f"[SERVER] Got data: {msg}")
            assert msg == "v1.0 data"
            server_got_data.set()

    @client.on_ready
    def on_ready():
        print("[CLIENT] Ready")
        client_ready.set()

    try:
        server.start(port)
        time.sleep(0.1)

        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Client did not become ready"

        # Client starts stream with OP_ECHO but server is V1.0 -> no op_code
        client_stream = client.start_stream(OP_ECHO)
        assert server_got_stream.wait(timeout=5), "Server did not get stream"
        print(f"[CLIENT] Stream op_code={client_stream.op_code}")

        client_stream.write(b"v1.0 data")
        assert server_got_data.wait(timeout=5), "Server did not get data"

        print("[TEST] test_v1_0_only_server PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_v1_0_only_client(crypto_init, capsys):
    """
    Client supports only V1.0. Server uses default (both V1.1 and V1.0).
    Streams should work without op_code (V1.0 behavior).
    """
    port = _next_port()
    print(f"\n[TEST] test_v1_0_only_client on port {port}")

    client_cfg = op.Config.with_defaults()
    client_cfg.supported_versions = [op.V1_0]

    client_ready = threading.Event()
    server_got_stream = threading.Event()
    server_got_data = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key, config=client_cfg)

    @server.on_incoming_stream
    def handle_incoming(stream: op.Stream):
        print(f"[SERVER] Incoming stream, op_code={stream.op_code}")
        assert stream.op_code is None, f"Expected None for V1.0, got {stream.op_code}"
        server_got_stream.set()

        @stream.on_data
        def on_data(data: bytes):
            msg = data.decode()
            print(f"[SERVER] Got data: {msg}")
            assert msg == "from v1.0 client"
            server_got_data.set()

    @client.on_ready
    def on_ready():
        print("[CLIENT] Ready")
        client_ready.set()

    try:
        server.start(port)
        time.sleep(0.1)

        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Client did not become ready"

        # Client starts stream with OP_ECHO but client is V1.0 -> no op_code
        client_stream = client.start_stream(OP_ECHO)
        assert server_got_stream.wait(timeout=5), "Server did not get stream"
        print(f"[CLIENT] Stream op_code={client_stream.op_code}")

        client_stream.write(b"from v1.0 client")
        assert server_got_data.wait(timeout=5), "Server did not get data"

        print("[TEST] test_v1_0_only_client PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_v1_1_only_both_sides(crypto_init, capsys):
    """
    Both server and client support only V1.1.
    Streams with op_code should work, bidirectional exchange works.
    """
    port = _next_port()
    print(f"\n[TEST] test_v1_1_only_both_sides on port {port}")

    client_identity_kp = _bindings.Crypto.generate_sign_keypair()
    cfg = op.Config.with_defaults()
    cfg.supported_versions = [op.V1_1]

    client_ready = threading.Event()
    server_got_stream = threading.Event()
    server_got_data = threading.Event()
    client_got_data = threading.Event()

    server = op.Server(config=cfg)
    client = op.Client(server.public_key, config=cfg)

    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        return pk.data == client_identity_kp.public_key.data

    @server.on_stream(OP_ECHO)
    def handle_echo_stream(stream: op.Stream):
        print(f"[SERVER] Stream, op_code={stream.op_code}")
        assert stream.op_code == OP_ECHO
        server_got_stream.set()

        @stream.on_data
        def on_data(data: bytes):
            msg = data.decode()
            print(f"[SERVER] Got data: {msg}")
            assert msg == "v1.1 data"
            server_got_data.set()
            stream.write(b"v1.1 ok")

    @client.on_ready
    def on_ready():
        print("[CLIENT] Ready")
        client_ready.set()

    try:
        client.set_client_identity(client_identity_kp)

        server.start(port)
        time.sleep(0.1)

        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Client did not become ready"

        client_stream = client.start_stream(OP_ECHO)
        assert server_got_stream.wait(timeout=5), "Server did not get stream"

        @client_stream.on_data
        def on_data(data: bytes):
            msg = data.decode()
            print(f"[CLIENT] Got echo: {msg}")
            assert msg == "v1.1 ok"
            client_got_data.set()

        client_stream.write(b"v1.1 data")
        assert server_got_data.wait(timeout=5), "Server did not get data"
        assert client_got_data.wait(timeout=5), "Client did not get echo"

        print("[TEST] test_v1_1_only_both_sides PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_no_common_version(crypto_init, capsys):
    """
    Server supports only V1.1, client supports only V1.0.
    Handshake should fail, client should get disconnected.
    """
    port = _next_port()
    print(f"\n[TEST] test_no_common_version on port {port}")

    server_cfg = op.Config.with_defaults()
    server_cfg.supported_versions = [op.V1_1]

    client_cfg = op.Config.with_defaults()
    client_cfg.supported_versions = [op.V1_0]

    client_disconnected = threading.Event()
    handshake_ok = threading.Event()

    server = op.Server(config=server_cfg)
    client = op.Client(server.public_key, config=client_cfg)

    @client.on_ready
    def on_ready():
        print("[CLIENT] Ready (unexpected)")
        handshake_ok.set()

    @client.on_disconnect
    def on_disconnect():
        print("[CLIENT] Disconnected (expected)")
        client_disconnected.set()

    try:
        server.start(port)
        time.sleep(0.1)

        client.connect(f"ws://localhost:{port}")
        assert client_disconnected.wait(timeout=5), "Client was not disconnected"
        assert not handshake_ok.is_set(), "Handshake should have failed"

        print("[TEST] test_no_common_version PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)
