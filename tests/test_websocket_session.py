import os
import sys
import threading
import time

import pytest

# Add the src directory to the path to find the ObscuraProto package
src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, src_dir)

try:
    import ObscuraProto as op
except ImportError as e:
    pytest.fail(f"Could not import the ObscuraProto package: {e}. Searched in: {sys.path}", pytrace=False)

# Opcodes for our test
OP_C2S_ECHO = 0x7001
OP_C2S_UNHANDLED = 0x7002
OP_S2C_RESPONSE = 0x8001
OP_S2C_UNHANDLED = 0x8002

PORT = 9003


@pytest.fixture(scope="module")
def crypto_init():
    """Fixture to ensure Crypto is initialized only once per module."""
    op.Crypto.init()


def test_websocket_session(crypto_init, capsys):
    """
    Tests the full high-level websocket session, including opcode handlers.
    """
    # --- Test state and synchronization ---
    client_ready = threading.Event()

    server_received_payloads = {}
    client_received_payloads = {}

    server_echo_received = threading.Event()
    server_unhandled_received = threading.Event()
    client_response_received = threading.Event()
    client_unhandled_received = threading.Event()

    # --- Server Setup ---
    server = op.Server()

    @server.on_payload(OP_C2S_ECHO)
    def handle_echo(hdl: op.ConnectionHdl, payload: op.Payload):
        print("[SERVER] Echo handler called")
        server_received_payloads[OP_C2S_ECHO] = payload
        # Echo back and also send another message
        server.send(hdl, payload)
        server.send(hdl, op.PayloadBuilder(OP_S2C_UNHANDLED).build())
        server_echo_received.set()

    @server.default_payload_handler
    def default_server_handler(hdl: op.ConnectionHdl, payload: op.Payload):
        print("[SERVER] Default handler called")
        server_received_payloads[payload.op_code] = payload
        # Send a specific response for the unhandled message
        server.send(hdl, op.PayloadBuilder(OP_S2C_RESPONSE).add_param("Handled by default").build())
        server_unhandled_received.set()

    # --- Client Setup ---
    client = op.Client(server.public_key)

    @client.on_ready
    def on_ready():
        print("[CLIENT] Ready handler called")
        client_ready.set()

    @client.on_payload(OP_C2S_ECHO)  # Expecting the echo back
    def client_echo_handler(payload: op.Payload):
        print("[CLIENT] Echo handler called")
        client_received_payloads[OP_C2S_ECHO] = payload

    @client.on_payload(OP_S2C_RESPONSE)
    def client_response_handler(payload: op.Payload):
        print("[CLIENT] Response handler called")
        client_received_payloads[OP_S2C_RESPONSE] = payload
        client_response_received.set()

    @client.default_payload_handler
    def default_client_handler(payload: op.Payload):
        print("[CLIENT] Default handler called")
        client_received_payloads[payload.op_code] = payload
        client_unhandled_received.set()

    # --- Test Execution ---
    try:
        server.start(PORT)
        time.sleep(0.1)  # Give server time to start
        client.connect(f"ws://localhost:{PORT}")

        # 1. Wait for client to be ready
        assert client_ready.wait(timeout=5), "Client did not become ready"

        # 2. Send messages from client
        print("\n[TEST] Client sending messages...")
        client.send(op.PayloadBuilder(OP_C2S_ECHO).add_param("echo me").build())
        client.send(op.PayloadBuilder(OP_C2S_UNHANDLED).add_param("unhandled").build())

        # 3. Wait for all events to be processed
        print("[TEST] Waiting for events...")
        assert server_echo_received.wait(timeout=5), "Server did not receive echo payload"
        assert server_unhandled_received.wait(timeout=5), "Server did not receive unhandled payload"
        assert client_response_received.wait(timeout=5), "Client did not receive response payload"
        assert client_unhandled_received.wait(timeout=5), "Client did not receive unhandled payload"

        # 4. Assertions
        # Server should have received two payloads
        assert len(server_received_payloads) == 2
        assert OP_C2S_ECHO in server_received_payloads
        assert OP_C2S_UNHANDLED in server_received_payloads

        # Client should have received three payloads (echo, response, unhandled)
        assert len(client_received_payloads) == 3
        assert OP_C2S_ECHO in client_received_payloads  # The echo back
        assert OP_S2C_RESPONSE in client_received_payloads
        assert OP_S2C_UNHANDLED in client_received_payloads

        # Check payload contents
        reader_echo = op.PayloadReader(server_received_payloads[OP_C2S_ECHO])
        assert reader_echo.read_string() == "echo me"

        reader_resp = op.PayloadReader(client_received_payloads[OP_S2C_RESPONSE])
        assert reader_resp.read_string() == "Handled by default"

    finally:
        # --- Cleanup ---
        print("\n[TEST] Cleaning up...")
        client.disconnect()
        server.stop()
        # Allow time for threads to join
        time.sleep(0.1)
        captured = capsys.readouterr()
        print(captured.out)
        print(captured.err)
        print("[TEST] Cleanup complete.")
