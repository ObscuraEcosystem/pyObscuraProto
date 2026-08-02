"""
Integration tests for server edge cases: stream lifecycle, sync requests,
connection/payload limits, default handlers, multiple clients, timeouts.
Ported from ObscuraProto/tests/integration/server_edge_test.cpp (9 tests).
"""

import asyncio
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
OP_UNHANDLED = 0x9002
OP_UNHANDLED_ANON = 0x9004

# Global port counter (thread-safe)
_port_counter = 30300
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


def test_stream_end_and_cancel(crypto_init, capsys):
    """
    Test stream.end() and stream.cancel() lifecycle.
    Client starts a stream, sends data, ends it. Server receives end.
    Server starts a stream, cancels it. Client receives cancel.
    """
    port = _next_port()
    print(f"\n[TEST] test_stream_end_and_cancel on port {port}")

    client_identity_kp = _bindings.Crypto.generate_sign_keypair()
    client_ready = threading.Event()
    server_got_stream = threading.Event()
    server_got_data = threading.Event()
    server_got_end = threading.Event()
    client_got_cancel = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        return pk.data == client_identity_kp.public_key.data

    @server.on_stream(OP_ECHO)
    def handle_echo_stream(stream: op.Stream):
        print(f"[SERVER] Stream started, id={stream.stream_id}")
        server_got_stream.set()

        @stream.on_data
        def on_data(data: bytes):
            print(f"[SERVER] Got data: {data}")
            server_got_data.set()

        @stream.on_end
        def on_end():
            print("[SERVER] Got end")
            server_got_end.set()

    @client.on_stream(OP_ECHO)
    def handle_client_stream(stream: op.Stream):
        @stream.on_cancel
        def on_cancel():
            print("[CLIENT] Got cancel")
            client_got_cancel.set()

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

        # Phase 1: client-initiated stream with end
        client_stream = client.start_stream(OP_ECHO)
        assert server_got_stream.wait(timeout=5), "Server did not get stream"
        client_stream.write(b"data")
        assert server_got_data.wait(timeout=5), "Server did not get data"
        client_stream.end()
        assert server_got_end.wait(timeout=5), "Server did not get end"
        print("[TEST] Phase 1: end completed")

        # Phase 2: server-initiated stream with cancel
        server_ping = threading.Event()
        client_stream2_ready = threading.Event()

        @client.on_stream(OP_PING)
        def handle_ping_stream(stream: op.Stream):
            @stream.on_cancel
            def on_cancel():
                print("[CLIENT] Got cancel for ping stream")
                client_got_cancel.set()

            client_stream2_ready.set()

        @server.on_payload(OP_PING)
        def handle_ping(hdl: op.ConnectionHdl, payload: op.Payload):
            srv_stream = server.start_stream(hdl, OP_PING)
            print("[SERVER] Cancelling server stream")
            srv_stream.cancel()
            server_ping.set()

        # Stream handler is already registered, send PING
        client.send(op.PayloadBuilder(OP_PING).build())
        assert server_ping.wait(timeout=5), "Server ping handler did not fire"
        assert client_stream2_ready.wait(timeout=5), "Client stream2 handler did not fire"
        assert client_got_cancel.wait(timeout=5), "Client did not receive cancel"

        print("[TEST] Phase 2: cancel completed")
        print("[TEST] test_stream_end_and_cancel PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_server_sync_request(crypto_init, capsys):
    """
    Server uses _server.sync_request() to send a synchronous request to the client.
    """
    port = _next_port()
    print(f"\n[TEST] test_server_sync_request on port {port}")

    client_identity_kp = _bindings.Crypto.generate_sign_keypair()
    client_ready = threading.Event()
    server_done = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        return pk.data == client_identity_kp.public_key.data

    @server.on_payload(OP_PING)
    def handle_ping(hdl: op.ConnectionHdl, payload: op.Payload):
        def do_request():
            try:
                resp = server.sync_request(
                    hdl,
                    op.PayloadBuilder(OP_PING).add_param("server sync").build(),
                )
                assert resp.op_code == OP_PING
                reader = op.PayloadReader(resp)
                val = reader.read_string()
                print(f"[SERVER] Got response: {val}")
                assert val == "pong from client"
                server_done.set()
            except Exception as e:
                print(f"[SERVER] Error: {e}")

        threading.Thread(target=do_request, daemon=True).start()

    @client.on_request(OP_PING)
    def handle_client_request(msg: str) -> op.Payload:
        print(f"[CLIENT] Got server request: {msg}")
        assert msg == "server sync"
        return op.PayloadBuilder(OP_PING).add_param("pong from client").build()

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
        assert server_done.wait(timeout=5), "Server request did not complete"

        print("[TEST] test_server_sync_request PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_server_request_to_identity(crypto_init, capsys):
    """
    Server uses sync_request_to_identity and async_request_to_identity.
    Both send requests to a client identified by public key.
    """
    port = _next_port()
    print(f"\n[TEST] test_server_request_to_identity on port {port}")

    client_identity_kp = _bindings.Crypto.generate_sign_keypair()
    client_ready = threading.Event()
    server_done_sync = threading.Event()
    server_done_async = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        return pk.data == client_identity_kp.public_key.data

    @server.on_payload(OP_ECHO)
    def handle_echo(hdl: op.ConnectionHdl, payload: op.Payload):
        def do_requests():
            try:
                # Sync request to identity
                sync_resp = server.sync_request_to_identity(
                    client_identity_kp.public_key,
                    op.PayloadBuilder(OP_PING).add_param("sync to id").build(),
                )
                assert sync_resp.op_code == OP_PING
                sync_reader = op.PayloadReader(sync_resp)
                sync_val = sync_reader.read_string()
                print(f"[SERVER] Sync to identity response: {sync_val}")
                assert sync_val == "sync ok"
                server_done_sync.set()

                # Async request to identity (using asyncio.run on the coroutine)
                async_resp = asyncio.run(
                    server.async_request_to_identity(
                        client_identity_kp.public_key,
                        op.PayloadBuilder(OP_ECHO).add_param("async to id").build(),
                    )
                )
                assert async_resp.op_code == OP_ECHO
                async_reader = op.PayloadReader(async_resp)
                async_val = async_reader.read_string()
                print(f"[SERVER] Async to identity response: {async_val}")
                assert async_val == "async ok"
                server_done_async.set()
            except Exception as e:
                print(f"[SERVER] Error in request thread: {e}")

        threading.Thread(target=do_requests, daemon=True).start()

    @client.on_request(OP_PING)
    def handle_ping(msg: str) -> op.Payload:
        print(f"[CLIENT] Got PING request: {msg}")
        assert msg == "sync to id"
        return op.PayloadBuilder(OP_PING).add_param("sync ok").build()

    @client.on_request(OP_ECHO)
    def handle_echo_request(msg: str) -> op.Payload:
        print(f"[CLIENT] Got ECHO request: {msg}")
        assert msg == "async to id"
        return op.PayloadBuilder(OP_ECHO).add_param("async ok").build()

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

        client.send(op.PayloadBuilder(OP_ECHO).build())
        assert server_done_sync.wait(timeout=5), "Sync request to identity did not complete"
        assert server_done_async.wait(timeout=5), "Async request to identity did not complete"

        print("[TEST] test_server_request_to_identity PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_connection_limits_enforced(crypto_init, capsys):
    """
    Server config limits max_total to 1 connection.
    First client connects successfully, second client gets disconnected.
    """
    port = _next_port()
    print(f"\n[TEST] test_connection_limits_enforced on port {port}")

    client_identity_kp = _bindings.Crypto.generate_sign_keypair()
    cfg = op.Config.with_defaults()
    cfg.rate_limit.enabled = False
    cfg.connection_limits.max_total = 1
    cfg.connection_limits.max_per_ip = 5
    cfg.timeouts.enabled = False

    server = op.Server(config=cfg)

    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        return pk.data == client_identity_kp.public_key.data

    client1 = op.Client(server.public_key, config=cfg)
    client1.set_client_identity(client_identity_kp)
    c1_ready = threading.Event()

    @client1.on_ready
    def on_c1_ready():
        print("[CLIENT-1] Ready")
        c1_ready.set()

    client2 = op.Client(server.public_key, config=cfg)
    client2.set_client_identity(client_identity_kp)
    c2_disconnected = threading.Event()
    c2_connected = threading.Event()

    @client2.on_ready
    def on_c2_ready():
        print("[CLIENT-2] Connected (unexpected)")
        c2_connected.set()

    @client2.on_disconnect
    def on_c2_disconnect():
        print("[CLIENT-2] Disconnected (expected)")
        c2_disconnected.set()

    try:
        server.start(port)
        time.sleep(0.1)

        client1.connect(f"ws://localhost:{port}")
        assert c1_ready.wait(timeout=5), "Client 1 did not become ready"

        client2.connect(f"ws://localhost:{port}")
        assert c2_disconnected.wait(timeout=5), "Client 2 was not disconnected"
        assert not c2_connected.is_set(), "Client 2 should not have connected"

        print("[TEST] test_connection_limits_enforced PASSED")
    finally:
        client1.disconnect()
        client2.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_payload_size_limit_enforced(crypto_init, capsys):
    """
    Server config limits max_decrypted_payload to 20 bytes.
    Small payload passes, large payload is rejected.
    """
    port = _next_port()
    print(f"\n[TEST] test_payload_size_limit_enforced on port {port}")

    client_identity_kp = _bindings.Crypto.generate_sign_keypair()
    cfg = op.Config.with_defaults()
    cfg.message_limits.max_decrypted_payload = 20
    cfg.rate_limit.enabled = False
    cfg.timeouts.enabled = False

    server = op.Server(config=cfg)
    server_got_small = threading.Event()

    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        return pk.data == client_identity_kp.public_key.data

    @server.on_payload(OP_ECHO)
    def handle_echo(hdl: op.ConnectionHdl, payload: op.Payload):
        print("[SERVER] Got small payload")
        server_got_small.set()

    client = op.Client(server.public_key, config=cfg)
    client.set_client_identity(client_identity_kp)
    client_ready = threading.Event()

    @client.on_ready
    def on_ready():
        print("[CLIENT] Ready")
        client_ready.set()

    try:
        server.start(port)
        time.sleep(0.1)

        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Client did not become ready"

        # Small payload should pass
        client.send(op.PayloadBuilder(OP_ECHO).add_param("small").build())
        assert server_got_small.wait(timeout=5), "Server did not receive small payload"

        # Large payload should be rejected
        large_msg = "this is a very long payload that exceeds the limit"
        print(f"[TEST] Sending large payload ({len(large_msg)} chars)...")
        with pytest.raises(RuntimeError):
            client.send(op.PayloadBuilder(OP_ECHO).add_param(large_msg).build())
        print("[TEST] Large payload correctly rejected")

        print("[TEST] test_payload_size_limit_enforced PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_default_payload_handlers(crypto_init, capsys):
    """
    Server registers a default payload handler for unhandled opcodes.
    Client sends an unhandled opcode, server default handler catches it.
    """
    port = _next_port()
    print(f"\n[TEST] test_default_payload_handlers on port {port}")

    client_identity_kp = _bindings.Crypto.generate_sign_keypair()
    client_ready = threading.Event()
    server_got_default = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        return pk.data == client_identity_kp.public_key.data

    @server.default_payload_handler
    def handle_default(hdl: op.ConnectionHdl, payload: op.Payload):
        print(f"[SERVER] Default handler called, op_code=0x{payload.op_code:04x}")
        assert payload.op_code == OP_UNHANDLED
        server_got_default.set()

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

        client.send(op.PayloadBuilder(OP_UNHANDLED).build())
        assert server_got_default.wait(timeout=5), "Server default handler did not fire"

        print("[TEST] test_default_payload_handlers PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_multiple_clients_data_exchange(crypto_init, capsys):
    """
    Three authenticated clients connect and send messages.
    Server receives all messages and counts them.
    """
    port = _next_port()
    print(f"\n[TEST] test_multiple_clients_data_exchange on port {port}")

    num_clients = 3
    client_identities = [_bindings.Crypto.generate_sign_keypair() for _ in range(num_clients)]

    server = op.Server()
    msgs_received = [0]
    all_msgs_received = threading.Event()
    lock = threading.Lock()

    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        return True

    @server.on_payload(OP_ECHO)
    def handle_echo(hdl: op.ConnectionHdl, payload: op.Payload):
        reader = op.PayloadReader(payload)
        val = reader.read_string()
        print(f"[SERVER] Received: {val}")
        assert val == "hello from client"
        with lock:
            msgs_received[0] += 1
            if msgs_received[0] == num_clients:
                all_msgs_received.set()

    clients = []

    try:
        server.start(port)
        time.sleep(0.1)

        for i in range(num_clients):
            client = op.Client(server.public_key)
            client.set_client_identity(client_identities[i])
            client_ready = threading.Event()

            @client.on_ready
            def make_ready(evt=client_ready):
                evt.set()

            @client.on_disconnect
            def on_dc():
                pass

            client.connect(f"ws://localhost:{port}")
            assert client_ready.wait(timeout=5), f"Client {i} did not become ready"
            clients.append(client)
            print(f"[TEST] Client {i} connected")

        for i, c in enumerate(clients):
            c.send(op.PayloadBuilder(OP_ECHO).add_param("hello from client").build())
            print(f"[TEST] Client {i} sent message")

        assert all_msgs_received.wait(timeout=5), "Not all messages received"
        assert msgs_received[0] == num_clients, f"Expected {num_clients}, got {msgs_received[0]}"

        print("[TEST] test_multiple_clients_data_exchange PASSED")
    finally:
        for c in clients:
            try:
                c.disconnect()
            except Exception:
                pass
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_idle_timeout_fires(crypto_init, capsys):
    """
    Server config sets idle timeout to 200ms.
    Client connects but does nothing, gets disconnected by timeout.
    """
    port = _next_port()
    print(f"\n[TEST] test_idle_timeout_fires on port {port}")

    cfg = op.Config.with_defaults()
    cfg.timeouts.handshake_ms = 50000
    cfg.timeouts.idle_ms = 200
    cfg.timeouts.check_interval_ms = 100
    cfg.rate_limit.enabled = False

    server = op.Server(config=cfg)
    client = op.Client(server.public_key, config=cfg)
    client_ready = threading.Event()
    client_disconnected = threading.Event()

    @client.on_ready
    def on_ready():
        print("[CLIENT] Ready")
        client_ready.set()

    @client.on_disconnect
    def on_disconnect():
        print("[CLIENT] Disconnected")
        client_disconnected.set()

    try:
        server.start(port)
        time.sleep(0.1)

        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Client did not become ready"

        # Wait for idle timeout to fire
        assert client_disconnected.wait(timeout=5), "Client was not disconnected by idle timeout"

        print("[TEST] test_idle_timeout_fires PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_anon_default_handler(crypto_init, capsys):
    """
    Server registers an anonymous default payload handler.
    Anonymous client sends an unhandled opcode, default handler catches it.
    """
    port = _next_port()
    print(f"\n[TEST] test_anon_default_handler on port {port}")

    client_ready = threading.Event()
    got_default = threading.Event()

    server = op.Server()
    client = op.Client(server.public_key)

    @server.anon_default_payload_handler
    def handle_anon_default(hdl: op.ConnectionHdl, payload: op.Payload):
        print(f"[SERVER] Anon default handler called, op_code=0x{payload.op_code:04x}")
        assert payload.op_code == OP_UNHANDLED_ANON
        got_default.set()

    @client.on_ready
    def on_ready():
        print("[CLIENT] Ready")
        client_ready.set()

    try:
        server.start(port)
        time.sleep(0.1)

        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Client did not become ready"

        client.send(op.PayloadBuilder(OP_UNHANDLED_ANON).build())
        assert got_default.wait(timeout=5), "Anon default handler did not fire"

        print("[TEST] test_anon_default_handler PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_client_async_request_polling_bridge(crypto_init, capsys):
    """
    Client.async_request uses the CppPayloadFuture polling bridge (Phase 7).

    The C++ async_request() returns a CppPayloadFuture immediately (no thread-pool
    thread is blocked); the event loop polls ready() and only calls get() when the
    response has arrived.
    """
    port = _next_port()
    print(f"\n[TEST] test_client_async_request_polling_bridge on port {port}")

    client_ready = threading.Event()

    server = op.Server()

    @server.on_anon_request(OP_ECHO)
    def handle_echo(hdl: op.ConnectionHdl, msg: str) -> op.Payload:
        return op.PayloadBuilder(OP_ECHO).add_param(f"resp: {msg}").build()

    client = op.Client(server.public_key)

    @client.on_ready
    def on_ready():
        print("[CLIENT] Ready")
        client_ready.set()

    try:
        server.start(port)
        time.sleep(0.1)

        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Client did not become ready"

        async def run_async_request():
            return await client.async_request(op.PayloadBuilder(OP_ECHO).add_param("bridge").build())

        response = asyncio.run(run_async_request())
        assert response.op_code == OP_ECHO, f"Expected OP_ECHO, got 0x{response.op_code:04x}"
        reader = op.PayloadReader(response)
        assert reader.read_string() == "resp: bridge", "Unexpected response value"
        print("[TEST] test_client_async_request_polling_bridge PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_server_async_request_polling_bridge(crypto_init, capsys):
    """
    Server.async_request(hdl, payload) uses the CppPayloadFuture polling bridge.

    The server initiates a request to a connected anonymous client and awaits the
    response via the CppPayloadFuture polling loop.
    """
    port = _next_port()
    print(f"\n[TEST] test_server_async_request_polling_bridge on port {port}")

    client_ready = threading.Event()
    server_done = threading.Event()

    server = op.Server()

    @server.on_anon_payload(OP_PING)
    def handle_ping(hdl: op.ConnectionHdl, msg: str):
        def do_request():
            try:
                async_resp = asyncio.run(
                    server.async_request(hdl, op.PayloadBuilder(OP_ECHO).add_param("srv ping").build())
                )
                assert async_resp.op_code == OP_ECHO, f"Expected OP_ECHO, got 0x{async_resp.op_code:04x}"
                async_reader = op.PayloadReader(async_resp)
                assert async_reader.read_string() == "pong ok", "Unexpected response value"
                server_done.set()
            except Exception as e:
                print(f"[SERVER] Error in request thread: {e}")

        threading.Thread(target=do_request, daemon=True).start()

    client = op.Client(server.public_key)

    @client.on_request(OP_ECHO)
    def handle_echo_request(msg: str) -> op.Payload:
        return op.PayloadBuilder(OP_ECHO).add_param("pong ok").build()

    @client.on_ready
    def on_ready():
        print("[CLIENT] Ready")
        client_ready.set()

    try:
        server.start(port)
        time.sleep(0.1)

        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Client did not become ready"

        client.send(op.PayloadBuilder(OP_PING).add_param("trigger").build())
        assert server_done.wait(timeout=5), "Server async_request did not complete"

        print("[TEST] test_server_async_request_polling_bridge PASSED")
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)
