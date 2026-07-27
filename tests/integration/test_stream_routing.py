"""
Integration tests for stream routing: op_code-specific handlers, fallback to generic,
backward compatibility, anonymous streams, server-initiated streams.
Ported from ObscuraProto/tests/integration/stream_routing_test.cpp (6 tests).
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

# Opcodes matching the C++ tests
OP_ECHO = 0x0001
OP_PING = 0x0002
OP_NO_HANDLER = 0x9001

# Global port counter (thread-safe)
_port_counter = 30400
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


def test_op_code_specific_stream_routing(crypto_init, capsys):
    """
    Server registers an op_code-specific stream handler for OP_ECHO.
    Authenticated client starts a stream with OP_ECHO, sends data.
    Server echoes back, client verifies the echo.
    """
    port = _next_port()
    print(f"\n[TEST] test_op_code_specific_stream_routing on port {port}")

    client_identity_kp = _bindings.Crypto.generate_sign_keypair()
    client_ready = threading.Event()
    server_got_stream = threading.Event()
    server_got_data = threading.Event()
    client_got_data = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        return pk.data == client_identity_kp.public_key.data

    @server.on_stream(OP_ECHO)
    def handle_echo_stream(stream: op.Stream):
        print(f"[SERVER] Stream with op_code={stream.op_code}")
        assert stream.op_code == OP_ECHO
        server_got_stream.set()

        @stream.on_data
        def on_data(data: bytes):
            msg = data.decode()
            print(f"[SERVER] Got data: {msg}")
            assert msg == "hello from client"
            server_got_data.set()
            stream.write(b"world")

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
            assert msg == "world"
            client_got_data.set()

        client_stream.write(b"hello from client")
        assert server_got_data.wait(timeout=5), "Server did not get data"
        assert client_got_data.wait(timeout=5), "Client did not get echo"

        print("[TEST] test_op_code_specific_stream_routing PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_fallback_to_generic_stream_handler(crypto_init, capsys):
    """
    Server has only a generic incoming stream handler (no op_code-specific one).
    Client starts a stream with OP_NO_HANDLER, falls back to generic handler.
    """
    port = _next_port()
    print(f"\n[TEST] test_fallback_to_generic_stream_handler on port {port}")

    client_identity_kp = _bindings.Crypto.generate_sign_keypair()
    client_ready = threading.Event()
    server_got_stream = threading.Event()
    server_got_data = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        return pk.data == client_identity_kp.public_key.data

    @server.on_incoming_stream
    def handle_incoming(stream: op.Stream):
        print(f"[SERVER] Incoming stream, op_code={stream.op_code}")
        assert stream.op_code == OP_NO_HANDLER
        server_got_stream.set()

        @stream.on_data
        def on_data(data: bytes):
            msg = data.decode()
            print(f"[SERVER] Got data: {msg}")
            assert msg == "fallback data"
            server_got_data.set()

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

        client_stream = client.start_stream(OP_NO_HANDLER)
        assert server_got_stream.wait(timeout=5), "Server did not get stream"

        client_stream.write(b"fallback data")
        assert server_got_data.wait(timeout=5), "Server did not get data"

        print("[TEST] test_fallback_to_generic_stream_handler PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_backward_compatible_start_stream(crypto_init, capsys):
    """
    Client starts a stream without op_code, server uses generic incoming stream handler.
    Bidirectional data exchange works (server echoes).
    """
    port = _next_port()
    print(f"\n[TEST] test_backward_compatible_start_stream on port {port}")

    client_identity_kp = _bindings.Crypto.generate_sign_keypair()
    client_ready = threading.Event()
    server_got_data = threading.Event()
    client_got_data = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        return pk.data == client_identity_kp.public_key.data

    @server.on_incoming_stream
    def handle_incoming(stream: op.Stream):
        print(f"[SERVER] Incoming stream, op_code={stream.op_code}")
        assert stream.op_code is None, f"Expected None, got {stream.op_code}"

        @stream.on_data
        def on_data(data: bytes):
            msg = data.decode()
            print(f"[SERVER] Got data: {msg}")
            assert msg == "hello"
            server_got_data.set()
            stream.write(b"echo")

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

        client_stream = client.start_stream()
        print(f"[CLIENT] Stream started, op_code={client_stream.op_code}")
        assert client_stream.op_code is None

        @client_stream.on_data
        def on_data(data: bytes):
            msg = data.decode()
            print(f"[CLIENT] Got echo: {msg}")
            assert msg == "echo"
            client_got_data.set()

        client_stream.write(b"hello")
        assert server_got_data.wait(timeout=5), "Server did not get data"
        assert client_got_data.wait(timeout=5), "Client did not get echo"

        print("[TEST] test_backward_compatible_start_stream PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_anonymous_stream_routing(crypto_init, capsys):
    """
    Server registers an anonymous stream handler for OP_PING.
    Anonymous client starts a stream with OP_PING, writes data, server receives.
    """
    port = _next_port()
    print(f"\n[TEST] test_anonymous_stream_routing on port {port}")

    client_ready = threading.Event()
    server_got_stream = threading.Event()
    server_got_data = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.on_anon_stream(OP_PING)
    def handle_anon_stream(stream: op.Stream):
        print(f"[SERVER] Anon stream, op_code={stream.op_code}")
        assert stream.op_code == OP_PING
        server_got_stream.set()

        @stream.on_data
        def on_data(data: bytes):
            msg = data.decode()
            print(f"[SERVER] Got data: {msg}")
            assert msg == "anon data"
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

        client_stream = client.start_stream(OP_PING)
        assert server_got_stream.wait(timeout=5), "Server did not get stream"

        client_stream.write(b"anon data")
        assert server_got_data.wait(timeout=5), "Server did not get data"

        print("[TEST] test_anonymous_stream_routing PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_server_initiated_op_code_stream(crypto_init, capsys):
    """
    Server starts a stream with OP_ECHO to an authenticated client.
    Client has a stream handler for OP_ECHO, receives data.
    """
    port = _next_port()
    print(f"\n[TEST] test_server_initiated_op_code_stream on port {port}")

    client_identity_kp = _bindings.Crypto.generate_sign_keypair()
    client_ready = threading.Event()
    server_got_ready = threading.Event()
    client_got_stream = threading.Event()
    client_got_data = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)
    client_hdl = [None]

    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        return pk.data == client_identity_kp.public_key.data

    @server.on_payload(OP_PING)
    def handle_ping(hdl: op.ConnectionHdl, payload: op.Payload):
        client_hdl[0] = hdl
        print("[SERVER] Got PING, storing hdl")
        server_got_ready.set()

    @client.on_stream(OP_ECHO)
    def handle_client_stream(stream: op.Stream):
        print(f"[CLIENT] Server stream, op_code={stream.op_code}")
        assert stream.op_code == OP_ECHO
        client_got_stream.set()

        @stream.on_data
        def on_data(data: bytes):
            msg = data.decode()
            print(f"[CLIENT] Got data: {msg}")
            assert msg == "from server"
            client_got_data.set()

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

        client.send(op.PayloadBuilder(OP_PING).build())
        assert server_got_ready.wait(timeout=5), "Server did not get PING"

        # Server starts a stream to the client
        srv_stream = server.start_stream(client_hdl[0], OP_ECHO)
        assert client_got_stream.wait(timeout=5), "Client did not get server stream"

        srv_stream.write(b"from server")
        assert client_got_data.wait(timeout=5), "Client did not get data from server"

        print("[TEST] test_server_initiated_op_code_stream PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_anon_server_initiated_op_code_stream(crypto_init, capsys):
    """
    Server starts an anonymous stream with OP_ECHO.
    Anonymous client has a stream handler for OP_ECHO, receives data.
    """
    port = _next_port()
    print(f"\n[TEST] test_anon_server_initiated_op_code_stream on port {port}")

    client_ready = threading.Event()
    client_got_stream = threading.Event()
    client_got_data = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.on_anon_payload(OP_PING)
    def handle_ping(hdl: op.ConnectionHdl, payload: op.Payload):
        print("[SERVER] Got PING, starting anon stream")
        srv_stream = server.start_stream(hdl, OP_ECHO)
        srv_stream.write(b"anon srv data")

    @client.on_stream(OP_ECHO)
    def handle_client_stream(stream: op.Stream):
        print(f"[CLIENT] Server anon stream, op_code={stream.op_code}")
        assert stream.op_code == OP_ECHO
        client_got_stream.set()

        @stream.on_data
        def on_data(data: bytes):
            msg = data.decode()
            print(f"[CLIENT] Got data: {msg}")
            assert msg == "anon srv data"
            client_got_data.set()

    @client.on_ready
    def on_ready():
        print("[CLIENT] Ready")
        client_ready.set()

    try:
        server.start(port)
        time.sleep(0.1)

        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Client did not become ready"

        client.send(op.PayloadBuilder(OP_PING).build())
        assert client_got_stream.wait(timeout=5), "Client did not get server stream"
        assert client_got_data.wait(timeout=5), "Client did not get data"

        print("[TEST] test_anon_server_initiated_op_code_stream PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)
