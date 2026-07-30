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

OP_ANON_REGISTER = 0x6001
OP_AUTH_GREETING = 0x6002
OP_ECHO_IDENTITY = 0x6003
PORT = 9007


@pytest.fixture(scope="module")
def crypto_init():
    op.Crypto.init()


def test_client_hello_serialization_with_identity(crypto_init):
    """Test that ClientHello correctly serializes/deserializes identity fields."""
    identity_kp = _bindings.Crypto.generate_sign_keypair()

    hello = _bindings.ClientHello()
    hello.supported_versions = [op.V1_0]
    hello.ephemeral_pk = _bindings.Crypto.generate_kx_keypair().public_key
    hello.has_client_identity = True
    hello.identity_pk = identity_kp.public_key
    hello.identity_sig = _bindings.Crypto.sign(hello.ephemeral_pk.data, identity_kp.private_key)

    serialized = hello.serialize()
    deserialized = _bindings.ClientHello.deserialize(serialized)

    assert deserialized.has_client_identity is True
    assert deserialized.identity_pk.data == hello.identity_pk.data
    assert deserialized.identity_sig.data == hello.identity_sig.data
    assert deserialized.ephemeral_pk.data == hello.ephemeral_pk.data


def test_client_hello_serialization_without_identity(crypto_init):
    """Test that ClientHello serializes correctly without identity."""
    hello = _bindings.ClientHello()
    hello.supported_versions = [op.V1_0]
    hello.ephemeral_pk = _bindings.Crypto.generate_kx_keypair().public_key

    serialized = hello.serialize()
    deserialized = _bindings.ClientHello.deserialize(serialized)

    assert deserialized.has_client_identity is False
    assert deserialized.ephemeral_pk.data == hello.ephemeral_pk.data


def test_public_key_equality_and_hash(crypto_init):
    """Test that PublicKey supports __eq__ and __hash__."""
    kp = _bindings.Crypto.generate_sign_keypair()
    pk1 = kp.public_key
    pk2 = kp.public_key

    assert pk1 == pk2
    assert hash(pk1) == hash(pk2)
    assert {pk1: "value"}[pk2] == "value"

    other_kp = _bindings.Crypto.generate_sign_keypair()
    assert pk1 != other_kp.public_key


def test_anon_handlers_registration(crypto_init):
    """Test that anonymous handler registration works from Python."""
    server = _bindings.WsServer(_bindings.Crypto.generate_sign_keypair())

    def anon_handler(hdl: op.ConnectionHdl, payload: op.Payload):
        pass

    def anon_request_handler(hdl: op.ConnectionHdl, reader: _bindings.PayloadReader) -> _bindings.Payload:
        return _bindings.PayloadBuilder(0xFFFF).build()

    def anon_default_handler(hdl: op.ConnectionHdl, payload: op.Payload):
        pass

    try:
        server.register_anon_op_handler(OP_ANON_REGISTER, anon_handler)
        server.register_anon_request_handler(OP_ANON_REGISTER, anon_request_handler)
        server.set_anon_default_payload_handler(anon_default_handler)
        assert True
    except Exception as e:
        pytest.fail(f"Anonymous handler registration raised: {e}")


def test_client_identity_handler_registration(crypto_init):
    """Test that identity handler registration works from Python."""
    server = _bindings.WsServer(_bindings.Crypto.generate_sign_keypair())

    def identity_handler(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        return True

    try:
        server.set_client_identity_handler(identity_handler)
        assert True
    except Exception as e:
        pytest.fail(f"Identity handler registration raised: {e}")


def test_server_identity_methods(crypto_init):
    """Test server identity methods for registration without errors."""
    server = _bindings.WsServer(_bindings.Crypto.generate_sign_keypair())

    def identity_handler(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        return True

    server.set_client_identity_handler(identity_handler)

    # send_to_identity and get_client_identity require an active connection,
    # but we can verify they exist and are callable
    assert hasattr(server, "send_to_identity")
    assert hasattr(server, "sync_request_to_identity")
    assert hasattr(server, "get_client_identity")
    assert True


def test_client_identity_methods(crypto_init):
    """Test client identity methods."""
    server_keys = _bindings.Crypto.generate_sign_keypair()
    client = _bindings.WsClient(server_keys)

    identity_kp = _bindings.Crypto.generate_sign_keypair()
    try:
        client.set_client_identity(identity_kp)
        assert True
    except Exception as e:
        pytest.fail(f"set_client_identity raised: {e}")

    assert True


def test_high_level_anon_handlers(crypto_init):
    """Test high-level Python Server anonymous handler decorators."""
    server = op.Server()

    @server.on_anon_payload(OP_ANON_REGISTER)
    def handle_anon_register(hdl: op.ConnectionHdl, data: bytes):
        pass

    @server.on_anon_request(OP_ANON_REGISTER)
    def handle_anon_request(hdl: op.ConnectionHdl, token: str) -> op.Payload:
        return op.PayloadBuilder(0x6004).add_param(True).build()

    @server.anon_default_payload_handler
    def handle_anon_default(hdl: op.ConnectionHdl, payload: op.Payload):
        pass

    assert True


def test_high_level_identity_handler(crypto_init):
    """Test high-level Python Server identity handler decorator."""
    server = op.Server()

    allowed_key = _bindings.Crypto.generate_sign_keypair().public_key

    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        return pk == allowed_key

    @server.on_anon_payload(OP_ANON_REGISTER)
    def anon_register(hdl: op.ConnectionHdl, data: bytes):
        pass

    assert True


def test_client_set_identity_high_level(crypto_init):
    """Test that high-level Client.set_client_identity works."""
    server_keys = _bindings.Crypto.generate_sign_keypair()
    client = op.Client(server_keys.public_key)
    identity_kp = _bindings.Crypto.generate_sign_keypair()

    client.set_client_identity(identity_kp)

    assert hasattr(client, "set_client_identity")


def test_integration_anonymous_then_authenticated(crypto_init, capsys):
    """Full integration test: anonymous registration then authenticated session."""
    client_a_ready = threading.Event()
    client_b_ready = threading.Event()

    server_anon_reg_received = threading.Event()
    server_auth_greeting_received = threading.Event()
    client_b_greeting_received = threading.Event()

    server_anon_payloads = {}
    server_auth_payloads = {}

    server = op.Server()
    client_identity_kp = _bindings.Crypto.generate_sign_keypair()

    @server.on_anon_payload(OP_ANON_REGISTER)
    def handle_anon_register(hdl: op.ConnectionHdl, payload: op.Payload):
        print("[SERVER] Anonymous registration received")
        server_anon_payloads["register"] = payload
        server_anon_reg_received.set()
        server.send_anonymous(hdl, op.PayloadBuilder(OP_ANON_REGISTER).add_param("registered").build())

    @server.on_client_identity
    def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
        accepted = pk == client_identity_kp.public_key
        print(f"[SERVER] Identity check: {'ACCEPTED' if accepted else 'REJECTED'}")
        return accepted

    @server.on_payload(OP_AUTH_GREETING)
    def handle_auth_greeting(hdl: op.ConnectionHdl, payload: op.Payload):
        print("[SERVER] Authenticated greeting received")
        server_auth_payloads["greeting"] = payload
        server_auth_greeting_received.set()
        client_pk = server.get_client_identity(hdl)
        server.send_to_identity(client_pk, op.PayloadBuilder(OP_AUTH_GREETING).add_param("hello back").build())

    try:
        server.start(PORT)
        time.sleep(0.2)

        # --- Client A: Anonymous ---
        print("\n--- Phase 1: Anonymous Client ---")
        server_anon_reg_received.clear()

        client_a = op.Client(server.public_key)

        @client_a.on_ready
        def on_ready_a():
            print("[CLIENT-A] Ready")
            client_a_ready.set()

        @client_a.on_payload(OP_ANON_REGISTER)
        def handle_anon_response(payload: op.Payload):
            print("[CLIENT-A] Received anon response")

        client_a.connect(f"ws://localhost:{PORT}")
        assert client_a_ready.wait(timeout=5), "Client A did not become ready"

        client_a.send(op.PayloadBuilder(OP_ANON_REGISTER).add_param("anon data").build())
        assert server_anon_reg_received.wait(timeout=5), "Server did not receive anon registration"
        print("[TEST] Anonymous registration successful")

        client_a.disconnect()
        time.sleep(0.2)

        # --- Client B: Authenticated ---
        print("\n--- Phase 2: Authenticated Client ---")
        server_auth_greeting_received.clear()

        client_b = op.Client(server.public_key)
        client_b.set_client_identity(client_identity_kp)

        @client_b.on_ready
        def on_ready_b():
            print("[CLIENT-B] Ready")
            client_b_ready.set()

        @client_b.on_payload(OP_AUTH_GREETING)
        def handle_auth_response(payload: op.Payload):
            print("[CLIENT-B] Received auth greeting")
            client_b_greeting_received.set()

        client_b.connect(f"ws://localhost:{PORT}")
        assert client_b_ready.wait(timeout=5), "Client B did not become ready"

        client_b.send(op.PayloadBuilder(OP_AUTH_GREETING).add_param("hello from authed").build())
        assert server_auth_greeting_received.wait(timeout=5), "Server did not receive auth greeting"
        assert client_b_greeting_received.wait(timeout=5), "Client B did not receive greeting response"
        print("[TEST] Authenticated session successful")

    finally:
        print("\n[TEST] Cleaning up...")
        try:
            client_b.disconnect()
        except Exception:
            pass
        server.stop()
        time.sleep(0.2)
        captured = capsys.readouterr()
        if captured.out:
            print(captured.out)
