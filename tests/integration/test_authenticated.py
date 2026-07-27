"""
Integration tests for authenticated sessions.
Ported from ObscuraProto/tests/integration/authenticated_test.cpp (5 tests).
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
OP_SERVER_REQUEST = 0x8005
OP_IDENTITY_GREETING = 0x6004

# Global port counter (thread-safe)
_port_counter = 30100
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


def test_auth_send_receive(crypto_init, capsys):
    """
    Authenticated send: client sets identity, server checks identity handler,
    sends a payload, server receives it.
    """
    port = _next_port()
    print(f"\n[TEST] test_auth_send_receive on port {port}")

    client_identity_kp = _bindings.Crypto.generate_sign_keypair()
    client_ready = threading.Event()
    server_got_message = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        accepted = pk.data == client_identity_kp.public_key.data
        print(f"[SERVER] Identity check: {'ACCEPTED' if accepted else 'REJECTED'}")
        return accepted

    @server.on_payload(OP_ECHO)
    def handle_auth_echo(hdl: op.ConnectionHdl, payload: op.Payload):
        reader = op.PayloadReader(payload)
        val = reader.read_string()
        print(f"[SERVER] Received auth payload: {val}")
        assert val == "auth hello", f"Expected 'auth hello', got '{val}'"
        server_got_message.set()

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

        client.send(op.PayloadBuilder(OP_ECHO).add_param("auth hello").build())
        assert server_got_message.wait(timeout=5), "Server did not receive message"

        print("[TEST] test_auth_send_receive PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_auth_sync_request(crypto_init, capsys):
    """
    Authenticated sync request: client sends a sync request,
    server request handler processes it and returns a response.
    """
    port = _next_port()
    print(f"\n[TEST] test_auth_sync_request on port {port}")

    client_identity_kp = _bindings.Crypto.generate_sign_keypair()
    client_ready = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        return pk.data == client_identity_kp.public_key.data

    @server.on_request(OP_ECHO)
    def handle_auth_request(hdl: op.ConnectionHdl, msg: str) -> op.Payload:
        print(f"[SERVER] Auth request: {msg}")
        return op.PayloadBuilder(OP_ECHO).add_param(f"auth: {msg}").build()

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

        response = client._client.sync_request(op.PayloadBuilder(OP_ECHO).add_param("world").build())
        assert response.op_code == OP_ECHO, f"Expected OP_ECHO, got 0x{response.op_code:04x}"
        reader = op.PayloadReader(response)
        val = reader.read_string()
        assert val == "auth: world", f"Expected 'auth: world', got '{val}'"
        print(f"[TEST] Auth sync request response: {val}")

        print("[TEST] test_auth_sync_request PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_auth_server_initiated_request(crypto_init, capsys):
    """
    Server initiates a request to an authenticated client.
    Server gets PING, spawns a thread to do sync_request, client responds.
    """
    port = _next_port()
    print(f"\n[TEST] test_auth_server_initiated_request on port {port}")

    client_identity_kp = _bindings.Crypto.generate_sign_keypair()
    client_ready = threading.Event()
    client_got_request = threading.Event()
    server_done = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        return pk.data == client_identity_kp.public_key.data

    @server.on_payload(OP_PING)
    def handle_ping(hdl: op.ConnectionHdl, payload: op.Payload):
        print("[SERVER] Got PING, initiating request to client")

        def do_request():
            try:
                resp = server._server.sync_request(
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
        client.set_client_identity(client_identity_kp)

        server.start(port)
        time.sleep(0.1)

        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Client did not become ready"

        client.send(op.PayloadBuilder(OP_PING).build())

        assert client_got_request.wait(timeout=5), "Client did not receive server request"
        assert server_done.wait(timeout=5), "Server did not receive response"

        print("[TEST] test_auth_server_initiated_request PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_send_to_identity(crypto_init, capsys):
    """
    Server sends a payload to a specific client identity after receiving
    a payload from that client. Tests the send_to_identity API.
    """
    port = _next_port()
    print(f"\n[TEST] test_send_to_identity on port {port}")

    client_identity_kp = _bindings.Crypto.generate_sign_keypair()
    client_ready = threading.Event()
    client_greeted = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        return pk.data == client_identity_kp.public_key.data

    @server.on_payload(OP_IDENTITY_GREETING)
    def handle_greeting(hdl: op.ConnectionHdl, payload: op.Payload):
        print("[SERVER] Received greeting, sending to identity")
        client_pk = server.get_client_identity(hdl)
        server.send_to_identity(
            client_pk,
            op.PayloadBuilder(OP_IDENTITY_GREETING).add_param("hello identified!").build(),
        )

    @client.on_payload(OP_IDENTITY_GREETING)
    def handle_greeting_response(payload: op.Payload):
        reader = op.PayloadReader(payload)
        val = reader.read_string()
        print(f"[CLIENT] Received identity greeting: {val}")
        assert val == "hello identified!", f"Expected 'hello identified!', got '{val}'"
        client_greeted.set()

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

        client.send(op.PayloadBuilder(OP_IDENTITY_GREETING).build())
        assert client_greeted.wait(timeout=5), "Client did not receive greeting response"

        print("[TEST] test_send_to_identity PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_identity_rejection(crypto_init, capsys):
    """
    Server identity handler returns False, rejecting the client.
    Client should get disconnected.
    """
    port = _next_port()
    print(f"\n[TEST] test_identity_rejection on port {port}")

    client_identity_kp = _bindings.Crypto.generate_sign_keypair()
    client_disconnected = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        print("[SERVER] Rejecting identity")
        return False

    @client.on_disconnect
    def on_disconnect():
        print("[CLIENT] Disconnected")
        client_disconnected.set()

    try:
        client.set_client_identity(client_identity_kp)

        server.start(port)
        time.sleep(0.1)

        client.connect(f"ws://localhost:{port}")
        assert client_disconnected.wait(timeout=5), "Client did not get disconnected"

        print("[TEST] test_identity_rejection PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)
