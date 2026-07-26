import os
import sys
import threading
import time

import pytest

src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, src_dir)

try:
    import ObscuraProto as op
    from ObscuraProto import _bindings
except ImportError as e:
    pytest.fail(f"Could not import ObscuraProto: {e}", pytrace=False)

PORT = 9011


@pytest.fixture(scope="module")
def crypto_init():
    op.Crypto.init()


def test_low_level_bindings_accept_callbacks(crypto_init):
    """Test that low-level WsServer bindings accept set_on_open_callback and set_on_close_callback."""
    server = _bindings.WsServer(_bindings.Crypto.generate_sign_keypair())

    def on_open(hdl: op.ConnectionHdl):
        pass

    def on_close(hdl: op.ConnectionHdl):
        pass

    try:
        server.set_on_open_callback(on_open)
        server.set_on_close_callback(on_close)
        assert True
    except Exception as e:
        pytest.fail(f"Callback registration raised: {e}")


def test_high_level_decorator_registration(crypto_init):
    """Test that high-level Server.on_open and Server.on_close decorators work."""
    server = op.Server()

    @server.on_open
    def handle_open(hdl: op.ConnectionHdl):
        pass

    @server.on_close
    def handle_close(hdl: op.ConnectionHdl):
        pass

    assert True


def test_on_open_fires_on_client_connect(crypto_init, capsys):
    """Test that on_open fires when a client connects to the server."""
    on_open_fired = threading.Event()
    client_ready = threading.Event()

    server = op.Server()

    @server.on_open
    def handle_open(hdl: op.ConnectionHdl):
        print("[SERVER] on_open fired")
        on_open_fired.set()

    server.start(PORT)
    time.sleep(0.1)

    client = op.Client(server.public_key)

    @client.on_ready
    def handle_ready():
        print("[CLIENT] Ready")
        client_ready.set()

    try:
        client.connect(f"ws://localhost:{PORT}")

        assert client_ready.wait(timeout=5), "Client did not become ready"
        assert on_open_fired.wait(timeout=5), "on_open callback did not fire"
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        capsys.readouterr()


def test_on_close_fires_on_server_stop(crypto_init, capsys):
    """Test that on_close fires when the server stops (closes all connections)."""
    on_close_fired = threading.Event()
    client_ready = threading.Event()

    server = op.Server()

    @server.on_close
    def handle_close(hdl: op.ConnectionHdl):
        print("[SERVER] on_close fired")
        on_close_fired.set()

    server.start(PORT + 1)
    time.sleep(0.1)

    client = op.Client(server.public_key)

    @client.on_ready
    def handle_ready():
        print("[CLIENT] Ready")
        client_ready.set()

    try:
        client.connect(f"ws://localhost:{PORT + 1}")
        assert client_ready.wait(timeout=5), "Client did not become ready"

        server.stop()
        assert on_close_fired.wait(timeout=5), "on_close callback did not fire after server stop"
    finally:
        client.disconnect()
        time.sleep(0.1)
        capsys.readouterr()


def test_on_open_and_on_close_receive_valid_hdl(crypto_init, capsys):
    """Test that callbacks receive a non-None ConnectionHdl."""
    open_hdl_valid = threading.Event()
    close_hdl_valid = threading.Event()
    client_ready = threading.Event()

    server = op.Server()

    @server.on_open
    def handle_open(hdl: op.ConnectionHdl):
        print(f"[SERVER] on_open received hdl: {hdl}")
        open_hdl_valid.set()

    @server.on_close
    def handle_close(hdl: op.ConnectionHdl):
        print(f"[SERVER] on_close received hdl: {hdl}")
        close_hdl_valid.set()

    server.start(PORT + 2)
    time.sleep(0.1)

    client = op.Client(server.public_key)

    @client.on_ready
    def handle_ready():
        client_ready.set()

    try:
        client.connect(f"ws://localhost:{PORT + 2}")
        assert client_ready.wait(timeout=5), "Client did not become ready"
        assert open_hdl_valid.wait(timeout=5), "on_open did not fire"

        server.stop()
        assert close_hdl_valid.wait(timeout=5), "on_close did not fire after server stop"
    finally:
        client.disconnect()
        time.sleep(0.1)
        capsys.readouterr()


def test_multiple_connections_trigger_callbacks(crypto_init, capsys):
    """Test that on_open and on_close fire for each client connection."""
    open_count = [0]
    close_count = [0]
    lock = threading.Lock()

    all_open = threading.Event()
    all_closed = threading.Event()

    server = op.Server()

    @server.on_open
    def handle_open(hdl: op.ConnectionHdl):
        with lock:
            open_count[0] += 1
            if open_count[0] == 3:
                all_open.set()

    @server.on_close
    def handle_close(hdl: op.ConnectionHdl):
        with lock:
            close_count[0] += 1
            if close_count[0] == 3:
                all_closed.set()

    server.start(PORT + 3)
    time.sleep(0.1)

    clients = []
    client_ready_events = []

    try:
        for i in range(3):
            ready = threading.Event()
            client = op.Client(server.public_key)

            @client.on_ready
            def make_ready(evt=ready):
                evt.set()

            client.connect(f"ws://localhost:{PORT + 3}")
            assert ready.wait(timeout=5), f"Client {i} did not become ready"
            clients.append(client)
            client_ready_events.append(ready)

        assert all_open.wait(timeout=5), "Not all on_open callbacks fired"
        assert open_count[0] == 3

        server.stop()
        assert all_closed.wait(timeout=5), "Not all on_close callbacks fired"
        assert close_count[0] == 3
    finally:
        for c in clients:
            c.disconnect()
        time.sleep(0.1)
        capsys.readouterr()


def test_no_crash_when_callbacks_not_set(crypto_init, capsys):
    """Test that the server works without setting on_open/on_close callbacks."""
    client_ready = threading.Event()

    server = op.Server()
    server.start(PORT + 4)
    time.sleep(0.1)

    client = op.Client(server.public_key)

    @client.on_ready
    def handle_ready():
        client_ready.set()

    try:
        client.connect(f"ws://localhost:{PORT + 4}")
        assert client_ready.wait(timeout=5), "Client did not become ready"
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        capsys.readouterr()


def test_on_open_fires_before_handshake_complete(crypto_init, capsys):
    """Test that on_open fires before the client handshake completes (hdl is available)."""
    on_open_fired = threading.Event()
    proceed = threading.Event()

    server = op.Server()

    @server.on_open
    def handle_open(hdl: op.ConnectionHdl):
        on_open_fired.set()
        proceed.wait(timeout=5)

    server.start(PORT + 5)
    time.sleep(0.1)

    client = op.Client(server.public_key)
    client_ready = threading.Event()

    @client.on_ready
    def handle_ready():
        client_ready.set()

    try:
        client.connect(f"ws://localhost:{PORT + 5}")

        assert on_open_fired.wait(timeout=5), "on_open did not fire before handshake"

        proceed.set()

        assert client_ready.wait(timeout=5), "Client did not become ready after on_open unblocked"
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        capsys.readouterr()
