"""
Full-cycle integration tests covering anonymous and authenticated sessions,
payload handlers, request handlers, stream handlers, and server-initiated
operations for both V1.0 and V1.1.
Ported from ObscuraProto/tests/integration/full_cycle_test.cpp (2 tests).
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
OP_UNHANDLED = 0x9003
OP_SRV_INIT = 0x8004
OP_SERVER_REQUEST = 0x8005

# Global port counter (thread-safe)
_port_counter = 30200
_port_lock = threading.Lock()


def _next_port():
    global _port_counter
    with _port_lock:
        p = _port_counter
        _port_counter += 1
        return p


def _make_config():
    """Create a config with rate limiting and timeouts disabled, supporting V1.1 and V1.0."""
    cfg = op.Config.with_defaults()
    cfg.supported_versions = [op.V1_1, op.V1_0]
    cfg.rate_limit.enabled = False
    cfg.timeouts.enabled = False
    return cfg


@pytest.fixture(scope="module")
def crypto_init():
    """Ensure Crypto is initialized once per module."""
    op.Crypto.init()


def test_full_cycle_v1_1(crypto_init, capsys):
    """
    Full cycle test for V1.1 protocol.

    Covers:
      - Anonymous: op_handler, request_handler, stream_handler (with op_code), default_handler
      - Authenticated: same + identity check
      - Server-initiated: async_request, send_to_identity, start_stream
    """
    port = _next_port()
    print(f"\n[TEST] test_full_cycle_v1_1 on port {port}")

    # Events for synchronization
    anon_op_done = threading.Event()
    anon_req_done = threading.Event()
    anon_stream_done = threading.Event()
    anon_stream_data_done = threading.Event()
    anon_default_done = threading.Event()
    auth_op_done = threading.Event()
    auth_req_done = threading.Event()
    auth_stream_done = threading.Event()
    auth_stream_data_done = threading.Event()
    srv_init_req_done = threading.Event()
    identity_msg_done = threading.Event()
    srv_stream_done = threading.Event()
    srv_stream_data_done = threading.Event()

    client_identity_kp = _bindings.Crypto.generate_sign_keypair()
    cfg = _make_config()

    server = op.Server(config=cfg)

    # --- Anonymous handlers ---
    @server.on_anon_payload(OP_ECHO)
    def handle_anon_echo(hdl: op.ConnectionHdl, payload: op.Payload):
        reader = op.PayloadReader(payload)
        val = reader.read_string()
        print(f"[SERVER] Anon op: {val}")
        assert val == "anon_op", f"Expected 'anon_op', got '{val}'"
        anon_op_done.set()

    @server.on_anon_request(OP_PING)
    def handle_anon_ping(hdl: op.ConnectionHdl, msg: str) -> op.Payload:
        print(f"[SERVER] Anon req: {msg}")
        assert msg == "anon_req", f"Expected 'anon_req', got '{msg}'"
        anon_req_done.set()
        return op.PayloadBuilder(OP_PING).add_param("pong").build()

    @server.on_anon_stream(OP_ECHO)
    def handle_anon_stream(stream: op.Stream):
        print(f"[SERVER] Anon stream, op_code={stream.op_code}")
        assert stream.op_code == OP_ECHO
        anon_stream_done.set()

        @stream.on_data
        def on_data(data: bytes):
            print(f"[SERVER] Anon stream data: {data}")
            assert data == b"anon_stream"
            anon_stream_data_done.set()

    @server.anon_default_payload_handler
    def handle_anon_default(hdl: op.ConnectionHdl, payload: op.Payload):
        print(f"[SERVER] Anon default, op_code=0x{payload.op_code:04x}")
        assert payload.op_code == OP_UNHANDLED
        anon_default_done.set()

    # --- Authenticated handlers ---
    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        return pk.data == client_identity_kp.public_key.data

    @server.on_payload(OP_ECHO)
    def handle_auth_echo(hdl: op.ConnectionHdl, payload: op.Payload):
        reader = op.PayloadReader(payload)
        val = reader.read_string()
        print(f"[SERVER] Auth op: {val}")
        assert val == "auth_op", f"Expected 'auth_op', got '{val}'"
        auth_op_done.set()

    @server.on_request(OP_PING)
    def handle_auth_ping(hdl: op.ConnectionHdl, msg: str) -> op.Payload:
        print(f"[SERVER] Auth req: {msg}")
        assert msg == "auth_req", f"Expected 'auth_req', got '{msg}'"
        auth_req_done.set()
        return op.PayloadBuilder(OP_PING).add_param("auth_pong").build()

    @server.on_stream(OP_ECHO)
    def handle_auth_stream(stream: op.Stream):
        print(f"[SERVER] Auth stream, op_code={stream.op_code}")
        assert stream.op_code == OP_ECHO
        auth_stream_done.set()

        @stream.on_data
        def on_data(data: bytes):
            print(f"[SERVER] Auth stream data: {data}")
            assert data == b"auth_stream"
            auth_stream_data_done.set()
            stream.write(b"stream_ok")

    @server.on_payload(OP_SRV_INIT)
    def handle_srv_init(hdl: op.ConnectionHdl, payload: op.Payload):
        print("[SERVER] SRV_INIT received, initiating server actions")

        def do_server_actions():
            try:
                # Server-initiated sync request
                resp = server._server.sync_request(
                    hdl,
                    op.PayloadBuilder(OP_PING).add_param("srv_req").build(),
                )
                reader = op.PayloadReader(resp)
                val = reader.read_string()
                print(f"[SERVER] SRV req response: {val}")
                assert val == "srv_resp"
                srv_init_req_done.set()
            except Exception as e:
                print(f"[SERVER] Error in srv_init thread: {e}")

        threading.Thread(target=do_server_actions, daemon=True).start()

        # Send to identity
        server.send_to_identity(
            client_identity_kp.public_key,
            op.PayloadBuilder(OP_ECHO).add_param("to_identity").build(),
        )

        # Server-initiated stream
        srv_stream = server.start_stream(hdl, OP_ECHO)
        srv_stream.write(b"srv_stream")

    # --- Anonymous client ---
    anon_client = op.Client(server.public_key, config=cfg)
    anon_ready = threading.Event()

    @anon_client.on_ready
    def on_anon_ready():
        print("[ANON-CLIENT] Ready")
        anon_ready.set()

    # --- Authenticated client ---
    auth_client = op.Client(server.public_key, config=cfg)
    auth_client.set_client_identity(client_identity_kp)
    auth_ready = threading.Event()

    @auth_client.on_request(OP_PING)
    def handle_auth_client_ping(msg: str) -> op.Payload:
        print(f"[AUTH-CLIENT] Server req: {msg}")
        assert msg == "srv_req"
        return op.PayloadBuilder(OP_PING).add_param("srv_resp").build()

    @auth_client.on_payload(OP_ECHO)
    def handle_auth_client_echo(payload: op.Payload):
        reader = op.PayloadReader(payload)
        val = reader.read_string()
        print(f"[AUTH-CLIENT] Received echo: {val}")
        if val == "to_identity":
            identity_msg_done.set()

    @auth_client.on_stream(OP_ECHO)
    def handle_auth_client_stream(stream: op.Stream):
        print(f"[AUTH-CLIENT] Server stream, op_code={stream.op_code}")
        assert stream.op_code == OP_ECHO
        srv_stream_done.set()

        @stream.on_data
        def on_data(data: bytes):
            print(f"[AUTH-CLIENT] Server stream data: {data}")
            assert data == b"srv_stream"
            srv_stream_data_done.set()

    @auth_client.on_ready
    def on_auth_ready():
        print("[AUTH-CLIENT] Ready")
        auth_ready.set()

    try:
        server.start(port)
        time.sleep(0.1)

        # === Anonymous client actions ===
        anon_client.connect(f"ws://localhost:{port}")
        assert anon_ready.wait(timeout=5), "Anonymous client did not become ready"

        # Anon send
        anon_client.send(op.PayloadBuilder(OP_ECHO).add_param("anon_op").build())
        assert anon_op_done.wait(timeout=5), "Anon op handler did not fire"

        # Anon sync request
        anon_resp = anon_client._client.sync_request(op.PayloadBuilder(OP_PING).add_param("anon_req").build())
        assert anon_resp.op_code == OP_PING
        anon_reader = op.PayloadReader(anon_resp)
        anon_val = anon_reader.read_string()
        assert anon_val == "pong", f"Expected 'pong', got '{anon_val}'"
        assert anon_req_done.wait(timeout=5), "Anon req handler did not fire"

        # Anon stream
        anon_stream = anon_client.start_stream(OP_ECHO)
        assert anon_stream_done.wait(timeout=5), "Anon stream handler did not fire"
        anon_stream.write(b"anon_stream")
        assert anon_stream_data_done.wait(timeout=5), "Anon stream data handler did not fire"

        # Anon unhandled op -> default handler
        anon_client.send(op.PayloadBuilder(OP_UNHANDLED).build())
        assert anon_default_done.wait(timeout=5), "Anon default handler did not fire"

        # === Authenticated client actions ===
        auth_client.connect(f"ws://localhost:{port}")
        assert auth_ready.wait(timeout=5), "Authenticated client did not become ready"

        # Auth send
        auth_client.send(op.PayloadBuilder(OP_ECHO).add_param("auth_op").build())
        assert auth_op_done.wait(timeout=5), "Auth op handler did not fire"

        # Auth sync request
        auth_resp = auth_client._client.sync_request(op.PayloadBuilder(OP_PING).add_param("auth_req").build())
        assert auth_resp.op_code == OP_PING
        auth_reader = op.PayloadReader(auth_resp)
        auth_val = auth_reader.read_string()
        assert auth_val == "auth_pong", f"Expected 'auth_pong', got '{auth_val}'"
        assert auth_req_done.wait(timeout=5), "Auth req handler did not fire"

        # Auth stream
        auth_stream = auth_client.start_stream(OP_ECHO)
        assert auth_stream_done.wait(timeout=5), "Auth stream handler did not fire"

        @auth_stream.on_data
        def on_auth_stream_data(data: bytes):
            print(f"[AUTH-CLIENT] Stream echo: {data}")
            assert data == b"stream_ok"

        auth_stream.write(b"auth_stream")
        assert auth_stream_data_done.wait(timeout=5), "Auth stream data handler did not fire"

        # Server-initiated request (triggers SRV_INIT handler)
        auth_client.send(op.PayloadBuilder(OP_SRV_INIT).build())
        assert srv_init_req_done.wait(timeout=5), "SRV init req handler did not fire"
        assert identity_msg_done.wait(timeout=5), "Identity message handler did not fire"
        assert srv_stream_done.wait(timeout=5), "SRV stream handler did not fire"
        assert srv_stream_data_done.wait(timeout=5), "SRV stream data handler did not fire"

        print("[TEST] test_full_cycle_v1_1 PASSED")
    finally:
        anon_client.disconnect()
        auth_client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)


def test_full_cycle_v1_0(crypto_init, capsys):
    """
    Full cycle test for V1.0 protocol (no op_code on streams).

    Covers:
      - Anonymous: op_handler, request_handler, stream_handler (no op_code), default_handler
      - Authenticated: same + identity check
      - Server-initiated: async_request, send_to_identity, start_stream (no op_code)
    """
    port = _next_port()
    print(f"\n[TEST] test_full_cycle_v1_0 on port {port}")

    # Events for synchronization
    anon_op_done = threading.Event()
    anon_req_done = threading.Event()
    anon_stream_done = threading.Event()
    anon_stream_data_done = threading.Event()
    auth_op_done = threading.Event()
    auth_req_done = threading.Event()
    auth_stream_done = threading.Event()
    auth_stream_data_done = threading.Event()
    srv_init_req_done = threading.Event()
    identity_msg_done = threading.Event()
    srv_stream_done = threading.Event()
    srv_stream_data_done = threading.Event()

    client_identity_kp = _bindings.Crypto.generate_sign_keypair()
    cfg = _make_config()

    server = op.Server(config=cfg)

    # --- Anonymous handlers ---
    @server.on_anon_payload(OP_ECHO)
    def handle_anon_echo(hdl: op.ConnectionHdl, payload: op.Payload):
        reader = op.PayloadReader(payload)
        val = reader.read_string()
        print(f"[SERVER] Anon op: {val}")
        assert val == "v1.0_anon_op", f"Expected 'v1.0_anon_op', got '{val}'"
        anon_op_done.set()

    @server.on_anon_request(OP_PING)
    def handle_anon_ping(hdl: op.ConnectionHdl, msg: str) -> op.Payload:
        print(f"[SERVER] Anon req: {msg}")
        assert msg == "v1.0_anon_req", f"Expected 'v1.0_anon_req', got '{msg}'"
        anon_req_done.set()
        return op.PayloadBuilder(OP_PING).add_param("pong").build()

    # V1.0 uses generic incoming stream handler (no op_code)
    v1_0_stream_seq = [0]

    @server.on_incoming_stream
    def handle_incoming_stream(stream: op.Stream):
        seq = v1_0_stream_seq[0]
        v1_0_stream_seq[0] += 1
        print(f"[SERVER] Incoming stream #{seq}, op_code={stream.op_code}")
        assert stream.op_code is None, f"Expected None op_code for V1.0, got {stream.op_code}"

        if seq == 0:
            anon_stream_done.set()

            @stream.on_data
            def on_data(data: bytes):
                print(f"[SERVER] Anon stream data: {data}")
                assert data == b"v1.0_anon_stream"
                anon_stream_data_done.set()
        else:
            auth_stream_done.set()

            @stream.on_data
            def on_data(data: bytes):
                print(f"[SERVER] Auth stream data: {data}")
                assert data == b"v1.0_auth_stream"
                auth_stream_data_done.set()
                stream.write(b"ok")

    # --- Authenticated handlers ---
    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        return pk.data == client_identity_kp.public_key.data

    @server.on_payload(OP_ECHO)
    def handle_auth_echo(hdl: op.ConnectionHdl, payload: op.Payload):
        reader = op.PayloadReader(payload)
        val = reader.read_string()
        print(f"[SERVER] Auth op: {val}")
        assert val == "v1.0_auth_op", f"Expected 'v1.0_auth_op', got '{val}'"
        auth_op_done.set()

    @server.on_request(OP_PING)
    def handle_auth_ping(hdl: op.ConnectionHdl, msg: str) -> op.Payload:
        print(f"[SERVER] Auth req: {msg}")
        assert msg == "v1.0_auth_req", f"Expected 'v1.0_auth_req', got '{msg}'"
        auth_req_done.set()
        return op.PayloadBuilder(OP_PING).add_param("auth_pong").build()

    @server.on_payload(OP_SRV_INIT)
    def handle_srv_init(hdl: op.ConnectionHdl, payload: op.Payload):
        print("[SERVER] SRV_INIT received, initiating server actions")

        def do_server_actions():
            try:
                resp = server._server.sync_request(
                    hdl,
                    op.PayloadBuilder(OP_PING).add_param("srv_req").build(),
                )
                reader = op.PayloadReader(resp)
                val = reader.read_string()
                print(f"[SERVER] SRV req response: {val}")
                assert val == "v1.0_srv_resp"
                srv_init_req_done.set()
            except Exception as e:
                print(f"[SERVER] Error in srv_init thread: {e}")

        threading.Thread(target=do_server_actions, daemon=True).start()

        server.send_to_identity(
            client_identity_kp.public_key,
            op.PayloadBuilder(OP_ECHO).add_param("v1.0_to_id").build(),
        )

        # V1.0 stream without op_code
        srv_stream = server.start_stream(hdl)
        srv_stream.write(b"v1.0_srv")

    # --- Anonymous client (V1.0 only) ---
    anon_cfg = _make_config()
    anon_cfg.supported_versions = [op.V1_0]
    anon_client = op.Client(server.public_key, config=anon_cfg)
    anon_ready = threading.Event()

    @anon_client.on_ready
    def on_anon_ready():
        print("[ANON-CLIENT] Ready")
        anon_ready.set()

    # --- Authenticated client (V1.0 only) ---
    auth_cfg = _make_config()
    auth_cfg.supported_versions = [op.V1_0]
    auth_client = op.Client(server.public_key, config=auth_cfg)
    auth_client.set_client_identity(client_identity_kp)
    auth_ready = threading.Event()

    @auth_client.on_request(OP_PING)
    def handle_auth_client_ping(msg: str) -> op.Payload:
        print(f"[AUTH-CLIENT] Server req: {msg}")
        assert msg == "srv_req"
        return op.PayloadBuilder(OP_PING).add_param("v1.0_srv_resp").build()

    @auth_client.on_payload(OP_ECHO)
    def handle_auth_client_echo(payload: op.Payload):
        reader = op.PayloadReader(payload)
        val = reader.read_string()
        print(f"[AUTH-CLIENT] Received echo: {val}")
        if val == "v1.0_to_id":
            identity_msg_done.set()

    # V1.0 client uses generic incoming stream handler
    @auth_client.on_incoming_stream
    def handle_client_incoming_stream(stream: op.Stream):
        print(f"[AUTH-CLIENT] Incoming stream, op_code={stream.op_code}")
        assert stream.op_code is None
        srv_stream_done.set()

        @stream.on_data
        def on_data(data: bytes):
            print(f"[AUTH-CLIENT] Stream data: {data}")
            assert data == b"v1.0_srv"
            srv_stream_data_done.set()

    @auth_client.on_ready
    def on_auth_ready():
        print("[AUTH-CLIENT] Ready")
        auth_ready.set()

    try:
        server.start(port)
        time.sleep(0.1)

        # === Anonymous client actions ===
        anon_client.connect(f"ws://localhost:{port}")
        assert anon_ready.wait(timeout=5), "Anonymous client did not become ready"

        # Anon send
        anon_client.send(op.PayloadBuilder(OP_ECHO).add_param("v1.0_anon_op").build())
        assert anon_op_done.wait(timeout=5), "Anon op handler did not fire"

        # Anon sync request
        anon_resp = anon_client._client.sync_request(op.PayloadBuilder(OP_PING).add_param("v1.0_anon_req").build())
        assert anon_resp.op_code == OP_PING
        anon_reader = op.PayloadReader(anon_resp)
        anon_val = anon_reader.read_string()
        assert anon_val == "pong", f"Expected 'pong', got '{anon_val}'"
        assert anon_req_done.wait(timeout=5), "Anon req handler did not fire"

        # Anon stream (V1.0 without op_code)
        anon_stream = anon_client.start_stream()
        assert anon_stream_done.wait(timeout=5), "Anon stream handler did not fire"
        anon_stream.write(b"v1.0_anon_stream")
        assert anon_stream_data_done.wait(timeout=5), "Anon stream data handler did not fire"

        # === Authenticated client actions ===
        auth_client.connect(f"ws://localhost:{port}")
        assert auth_ready.wait(timeout=5), "Authenticated client did not become ready"

        # Auth send
        auth_client.send(op.PayloadBuilder(OP_ECHO).add_param("v1.0_auth_op").build())
        assert auth_op_done.wait(timeout=5), "Auth op handler did not fire"

        # Auth sync request
        auth_resp = auth_client._client.sync_request(op.PayloadBuilder(OP_PING).add_param("v1.0_auth_req").build())
        assert auth_resp.op_code == OP_PING
        auth_reader = op.PayloadReader(auth_resp)
        auth_val = auth_reader.read_string()
        assert auth_val == "auth_pong", f"Expected 'auth_pong', got '{auth_val}'"
        assert auth_req_done.wait(timeout=5), "Auth req handler did not fire"

        # Auth stream (V1.0 without op_code)
        auth_stream = auth_client.start_stream()
        assert auth_stream_done.wait(timeout=5), "Auth stream handler did not fire"

        @auth_stream.on_data
        def on_auth_stream_data(data: bytes):
            print(f"[AUTH-CLIENT] Stream echo: {data}")
            assert data == b"ok"

        auth_stream.write(b"v1.0_auth_stream")
        assert auth_stream_data_done.wait(timeout=5), "Auth stream data handler did not fire"

        # Server-initiated request
        auth_client.send(op.PayloadBuilder(OP_SRV_INIT).build())
        assert srv_init_req_done.wait(timeout=5), "SRV init req handler did not fire"
        assert identity_msg_done.wait(timeout=5), "Identity message handler did not fire"
        assert srv_stream_done.wait(timeout=5), "SRV stream handler did not fire"
        assert srv_stream_data_done.wait(timeout=5), "SRV stream data handler did not fire"

        print("[TEST] test_full_cycle_v1_0 PASSED")
    finally:
        anon_client.disconnect()
        auth_client.disconnect()
        server.stop()
        time.sleep(0.1)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)
