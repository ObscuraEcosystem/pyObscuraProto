"""
Stream op_code tests for C++ v1.1 features.
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

PORT = 9200


@pytest.fixture(scope="module")
def crypto_init():
    op.Crypto.init()


def test_stream_op_code_property():
    from ObscuraProto import CppStream, Stream

    sent = []

    def mock_send(payload):
        sent.append(payload)

    cpp = CppStream(1, mock_send, 0x3001)
    stream = Stream(cpp)
    assert stream.op_code == 0x3001
    assert stream.stream_id == 1

    cpp2 = CppStream(2, mock_send)
    stream2 = Stream(cpp2)
    assert stream2.op_code is None
    assert stream2.stream_id == 2

    cpp3 = CppStream(3, mock_send, 0xABCD)
    stream3 = Stream(cpp3)
    assert stream3.op_code == 0xABCD
    assert stream3.stream_id == 3


def test_op_code_streaming(crypto_init, capsys):
    stream_op = 0x3001
    server_received = threading.Event()
    client_data_ready = threading.Event()
    server_done = threading.Event()
    server_chunks = []
    client_chunks = []
    received_op_code = [None]

    server = op.Server()

    @server.on_anon_stream(stream_op)
    def handle_stream(stream: op.Stream):
        received_op_code[0] = stream.op_code
        server_received.set()

        @stream.on_data
        def on_data(data: bytes):
            server_chunks.append(data)
            stream.write(b"echo:" + data)

        @stream.on_end
        def on_end():
            stream.end()
            server_done.set()

    client = op.Client(server.public_key)

    @client.on_ready
    def on_ready():
        stream = client.start_stream(stream_op)

        @stream.on_data
        def on_client_data(data: bytes):
            client_chunks.append(data)
            client_data_ready.set()

        @stream.on_end
        def on_end():
            pass

        stream.write(b"Hello via op_code!")
        time.sleep(0.1)
        stream.end()

    try:
        server.start(PORT)
        time.sleep(0.1)
        client.connect(f"ws://localhost:{PORT}")

        assert server_received.wait(timeout=5), "Server did not receive stream"
        assert received_op_code[0] == stream_op
        assert server_done.wait(timeout=5), "Server did not finish"
        assert client_data_ready.wait(timeout=5), "Client did not receive echo"
        assert len(server_chunks) == 1
        assert server_chunks[0] == b"Hello via op_code!"
        assert len(client_chunks) == 1
        assert client_chunks[0] == b"echo:Hello via op_code!"
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)


def test_on_stream_filters_by_op_code(crypto_init, capsys):
    op_alice = 0x3001
    op_bob = 0x3002
    alice_seen = []
    bob_seen = []
    alice_ready = threading.Event()
    bob_ready = threading.Event()
    alice_done = threading.Event()
    bob_done = threading.Event()

    server = op.Server()

    @server.on_anon_stream(op_alice)
    def handle_alice(stream: op.Stream):
        alice_seen.append(stream.op_code)
        alice_ready.set()

        @stream.on_end
        def on_end():
            stream.end()
            alice_done.set()

    @server.on_anon_stream(op_bob)
    def handle_bob(stream: op.Stream):
        bob_seen.append(stream.op_code)
        bob_ready.set()

        @stream.on_end
        def on_end():
            stream.end()
            bob_done.set()

    client = op.Client(server.public_key)

    @client.on_ready
    def on_ready():
        stream_a = client.start_stream(op_alice)
        time.sleep(0.1)
        stream_a.end()

        stream_b = client.start_stream(op_bob)
        time.sleep(0.1)
        stream_b.end()

    try:
        server.start(PORT + 1)
        time.sleep(0.1)
        client.connect(f"ws://localhost:{PORT + 1}")

        assert alice_ready.wait(timeout=5), "Handler for op_alice was not called"
        assert bob_ready.wait(timeout=5), "Handler for op_bob was not called"
        assert alice_done.wait(timeout=5), "Stream A did not complete"
        assert bob_done.wait(timeout=5), "Stream B did not complete"

        assert len(alice_seen) == 1
        assert alice_seen[0] == op_alice
        assert len(bob_seen) == 1
        assert bob_seen[0] == op_bob
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)


def test_on_incoming_stream_still_works(crypto_init, capsys):
    incoming_streams = []
    incoming_received = threading.Event()
    stream_done = threading.Event()

    server = op.Server()

    @server.on_incoming_stream
    def handle_incoming(stream: op.Stream):
        incoming_streams.append(stream.op_code)
        incoming_received.set()

        @stream.on_end
        def on_end():
            stream.end()
            stream_done.set()

    client = op.Client(server.public_key)

    @client.on_ready
    def on_ready():
        stream = client.start_stream(0x3001)
        time.sleep(0.1)
        stream.end()

    try:
        server.start(PORT + 2)
        time.sleep(0.1)
        client.connect(f"ws://localhost:{PORT + 2}")

        assert incoming_received.wait(timeout=5), "on_incoming_stream was not called"
        assert stream_done.wait(timeout=5), "Stream did not complete"
        assert len(incoming_streams) == 1
        assert incoming_streams[0] == 0x3001
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)


def test_exported_symbols(crypto_init):
    assert hasattr(op, "V1_1")
    assert op.V1_1 == 0x0101
    assert hasattr(op, "SUPPORTED_VERSIONS")
    assert 0x0101 in op.SUPPORTED_VERSIONS
    assert 0x0100 in op.SUPPORTED_VERSIONS
    assert hasattr(op.Config, "supported_versions")

    cfg = op.Config()
    assert hasattr(cfg, "supported_versions")
    assert isinstance(cfg.supported_versions, list)

    cfg2 = op.Config.with_defaults()
    assert hasattr(cfg2, "supported_versions")
    assert isinstance(cfg2.supported_versions, list)
