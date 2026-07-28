"""
Integration tests for anonymous sessions.
Ported from ObscuraProto/tests/integration/anonymous_test.cpp (6 tests).
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
except ImportError as e:
    pytest.fail(f"Could not import ObscuraProto: {e}", pytrace=False)

# Opcodes matching the C++ tests
OP_ECHO = 0x0001
OP_PING = 0x0002
OP_SERVER_REQUEST = 0x8005

# Global port counter (thread-safe)
_port_counter = 30000
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


def test_anon_op_handler(crypto_init, capsys):
    """
    Server registers an anonymous op handler for OP_ECHO.
    Client sends a payload with "hello", server verifies the content.
    """
    port = _next_port()
    print(f"\n[TEST] test_anon_op_handler on port {port}")

    server_got_message = threading.Event()
    client_ready = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.on_anon_payload(OP_ECHO)
    def handle_anon_echo(hdl: op.ConnectionHdl, payload: op.Payload):
        reader = op.PayloadReader(payload)
        val = reader.read_string()
        print(f"[SERVER] Received anon payload: {val}")
        assert val == "hello", f"Expected 'hello', got '{val}'"
        server_got_message.set()

    @client.on_ready
    def on_ready():
        print("[CLIENT] Ready")
        client_ready.set()

    try:
        server.start(port)
        time.sleep(0.1)

        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Client did not become ready"

        client.send(op.PayloadBuilder(OP_ECHO).add_param("hello").build())
        assert server_got_message.wait(timeout=5), "Server did not receive message"

        print("[TEST] test_anon_op_handler PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_anon_sync_request(crypto_init, capsys):
    """
    Server registers an anonymous request handler for OP_ECHO.
    Client sends a synchronous request and verifies the echo response.
    """
    port = _next_port()
    print(f"\n[TEST] test_anon_sync_request on port {port}")

    client_ready = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.on_anon_request(OP_ECHO)
    def handle_anon_request(hdl: op.ConnectionHdl, msg: str) -> op.Payload:
        print(f"[SERVER] Anon request: {msg}")
        return op.PayloadBuilder(OP_ECHO).add_param(f"echo: {msg}").build()

    @client.on_ready
    def on_ready():
        print("[CLIENT] Ready")
        client_ready.set()

    try:
        server.start(port)
        time.sleep(0.1)

        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Client did not become ready"

        response = client.sync_request(op.PayloadBuilder(OP_ECHO).add_param("world").build())
        assert response.op_code == OP_ECHO, f"Expected OP_ECHO, got 0x{response.op_code:04x}"
        reader = op.PayloadReader(response)
        val = reader.read_string()
        assert val == "echo: world", f"Expected 'echo: world', got '{val}'"
        print(f"[TEST] Sync request response: {val}")

        print("[TEST] test_anon_sync_request PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_anon_async_request(crypto_init, capsys):
    """
    Server registers an anonymous request handler for OP_ECHO.
    Client sends a synchronous request (same as sync in Python bindings).
    """
    port = _next_port()
    print(f"\n[TEST] test_anon_async_request on port {port}")

    client_ready = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.on_anon_request(OP_ECHO)
    def handle_anon_request(hdl: op.ConnectionHdl, msg: str) -> op.Payload:
        print(f"[SERVER] Anon request: {msg}")
        return op.PayloadBuilder(OP_ECHO).add_param(f"resp: {msg}").build()

    @client.on_ready
    def on_ready():
        print("[CLIENT] Ready")
        client_ready.set()

    try:
        server.start(port)
        time.sleep(0.1)

        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Client did not become ready"

        # Use synchronous request (Python bindings don't have native async_request)
        response = client.sync_request(op.PayloadBuilder(OP_ECHO).add_param("async").build())
        assert response.op_code == OP_ECHO, f"Expected OP_ECHO, got 0x{response.op_code:04x}"
        reader = op.PayloadReader(response)
        val = reader.read_string()
        assert val == "resp: async", f"Expected 'resp: async', got '{val}'"
        print(f"[TEST] Async (sync) request response: {val}")

        print("[TEST] test_anon_async_request PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_anon_server_initiated_request(crypto_init, capsys):
    """
    Server initiates a request to the client via anon_op_handler.
    Client has a request handler for OP_SERVER_REQUEST and responds.
    """
    port = _next_port()
    print(f"\n[TEST] test_anon_server_initiated_request on port {port}")

    client_ready = threading.Event()
    client_got_request = threading.Event()
    server_done = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.on_anon_payload(OP_PING)
    def handle_ping(hdl: op.ConnectionHdl, payload: op.Payload):
        print("[SERVER] Got PING, initiating request to client")

        def do_request():
            try:
                resp = server.sync_request(
                    hdl,
                    op.PayloadBuilder(OP_SERVER_REQUEST).add_param("req from server").build(),
                )
                reader = op.PayloadReader(resp)
                val = reader.read_string()
                print(f"[SERVER] Got response: {val}")
                assert val == "resp from client", f"Expected 'resp from client', got '{val}'"
                server_done.set()
            except Exception as e:
                print(f"[SERVER] Error in request thread: {e}")

        threading.Thread(target=do_request, daemon=True).start()

    @client.on_request(OP_SERVER_REQUEST)
    def handle_server_request(msg: str) -> op.Payload:
        print(f"[CLIENT] Got server request: {msg}")
        assert msg == "req from server", f"Expected 'req from server', got '{msg}'"
        client_got_request.set()
        return op.PayloadBuilder(OP_SERVER_REQUEST).add_param("resp from client").build()

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

        assert client_got_request.wait(timeout=5), "Client did not receive server request"
        assert server_done.wait(timeout=5), "Server did not receive response"

        print("[TEST] test_anon_server_initiated_request PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_anon_streaming(crypto_init, capsys):
    """
    Server registers an anonymous stream handler for OP_ECHO.
    Client starts a stream, writes data, server verifies.
    """
    port = _next_port()
    print(f"\n[TEST] test_anon_streaming on port {port}")

    client_ready = threading.Event()
    server_got_stream = threading.Event()
    server_got_data = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.on_anon_stream(OP_ECHO)
    def handle_anon_stream(stream: op.Stream):
        print(f"[SERVER] Anon stream received, op_code={stream.op_code}")
        assert stream.op_code == OP_ECHO, f"Expected OP_ECHO, got {stream.op_code}"
        server_got_stream.set()

        @stream.on_data
        def on_data(data: bytes):
            print(f"[SERVER] Stream data: {data}")
            assert data == b"anon stream data", f"Expected b'anon stream data', got {data}"
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

        client_stream = client.start_stream(OP_ECHO)
        assert server_got_stream.wait(timeout=5), "Server did not receive stream"

        client_stream.write(b"anon stream data")
        assert server_got_data.wait(timeout=5), "Server did not receive stream data"

        print("[TEST] test_anon_streaming PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_send_works_for_anonymous(crypto_init, capsys):
    """
    Basic anonymous send test.
    Server has anon_op_handler, client sends a payload, server verifies.
    """
    port = _next_port()
    print(f"\n[TEST] test_send_works_for_anonymous on port {port}")

    client_ready = threading.Event()
    server_got_send = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.on_anon_payload(OP_ECHO)
    def handle_echo(hdl: op.ConnectionHdl, payload: op.Payload):
        reader = op.PayloadReader(payload)
        val = reader.read_string()
        print(f"[SERVER] Received: {val}")
        assert val == "send test", f"Expected 'send test', got '{val}'"
        server_got_send.set()

    @client.on_ready
    def on_ready():
        print("[CLIENT] Ready")
        client_ready.set()

    try:
        server.start(port)
        time.sleep(0.1)

        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Client did not become ready"

        client.send(op.PayloadBuilder(OP_ECHO).add_param("send test").build())
        assert server_got_send.wait(timeout=5), "Server did not receive send"

        print("[TEST] test_send_works_for_anonymous PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)
