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
OP_UNPACK_TEST = 0x9001
OP_RAW_TEST = 0x9002
OP_RESPONSE = 0x9003

PORT = 9004


@pytest.fixture(scope="module")
def crypto_init():
    """Fixture to ensure Crypto is initialized only once per module."""
    op.Crypto.init()


def test_auto_unpacking(crypto_init, capsys):
    """
    Tests the automatic payload unpacking based on handler type hints.
    """
    # --- Test state and synchronization ---
    client_ready = threading.Event()
    test_finished = threading.Event()

    # Store received data for assertion
    received_data = {}

    # --- Server Setup ---
    server = op.Server()

    # 1. Test handler with auto-unpacking
    @server.on_payload(OP_UNPACK_TEST)
    def handle_unpack(hdl: op.ConnectionHdl, name: str, value: op.uint, flag: bool, pi: float):
        print(f"[SERVER] Unpacking handler called with: {name}, {value}, {flag}, {pi}")
        received_data["unpacked"] = (name, value, flag, pi)
        # Send back the received data for verification
        response_payload = (
            op.PayloadBuilder(OP_RESPONSE).add_param(name).add_param(value).add_param(flag).add_param(pi).build()
        )
        server.send(hdl, response_payload)

    # 2. Test handler with raw payload (fallback)
    @server.on_payload(OP_RAW_TEST)
    def handle_raw(hdl: op.ConnectionHdl, payload: op.Payload):
        print("[SERVER] Raw handler called")
        reader = op.PayloadReader(payload)
        data = reader.read_string()
        received_data["raw"] = data
        # Don't send a response, just receive
        test_finished.set()

    # --- Client Setup ---
    client = op.Client(server.public_key)

    @client.on_ready
    def on_ready():
        client_ready.set()

    @client.on_payload(OP_RESPONSE)
    def handle_response(name: str, value: op.uint, flag: bool, pi: float):
        print(f"[CLIENT] Response handler called with: {name}, {value}, {flag}, {pi}")
        received_data["response"] = (name, value, flag, pi)
        # Now send the second part of the test
        client.send(op.PayloadBuilder(OP_RAW_TEST).add_param("raw_payload_test").build())

    # --- Test Execution ---
    try:
        server.start(PORT)
        time.sleep(0.1)
        client.connect(f"ws://localhost:{PORT}")

        assert client_ready.wait(timeout=5), "Client did not become ready"

        # Send the first message to trigger the unpacking handler
        payload_to_unpack = (
            op.PayloadBuilder(OP_UNPACK_TEST)
            .add_param("test_name")
            .add_param(op.uint(12345678))
            .add_param(True)
            .add_param(3.14159)
            .build()
        )
        client.send(payload_to_unpack)

        # Wait for the full round trip to complete
        assert test_finished.wait(timeout=5), "Test did not complete in time"

        # --- Assertions ---
        assert "unpacked" in received_data
        assert received_data["unpacked"] == ("test_name", 12345678, True, pytest.approx(3.14159))

        assert "response" in received_data
        assert received_data["response"] == ("test_name", 12345678, True, pytest.approx(3.14159))

        assert "raw" in received_data
        assert received_data["raw"] == "raw_payload_test"

    finally:
        # --- Cleanup ---
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        print(captured.out)
