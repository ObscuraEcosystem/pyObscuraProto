import os
import sys
import tempfile
import threading
import time

import pytest

src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, src_dir)

try:
    import ObscuraProto as op
except ImportError as e:
    pytest.fail(f"Could not import ObscuraProto: {e}", pytrace=False)

PORT = 9008


@pytest.fixture(scope="module")
def crypto_init():
    op.Crypto.init()


def test_config_defaults(crypto_init):
    """Test that Config.with_defaults() returns a valid config."""
    cfg = op.Config.with_defaults()

    assert cfg.rate_limit.enabled is True
    assert cfg.rate_limit.messages_per_second == 100
    assert cfg.rate_limit.burst_size == 200
    assert cfg.rate_limit.handshake_attempts_per_minute == 10
    assert cfg.rate_limit.connections_per_minute == 30

    assert cfg.connection_limits.enabled is True
    assert cfg.connection_limits.max_per_ip == 10
    assert cfg.connection_limits.max_total == 1000

    assert cfg.message_limits.enabled is True
    assert cfg.message_limits.max_ws_frame_size == 1048576
    assert cfg.message_limits.max_decrypted_payload == 65535

    assert cfg.timeouts.enabled is True
    assert cfg.timeouts.handshake_ms == 10000
    assert cfg.timeouts.idle_ms == 300000
    assert cfg.timeouts.check_interval_ms == 5000

    assert cfg.opcodes.RESPONSE == 0xFFFF
    assert cfg.opcodes.STREAM_START == 0xFFFD
    assert cfg.opcodes.STREAM_DATA == 0xFFFC
    assert cfg.opcodes.STREAM_END == 0xFFFB
    assert cfg.opcodes.STREAM_CANCEL == 0xFFFA


def test_config_custom(crypto_init):
    """Test creating a Config from Python with custom values."""
    cfg = op.Config()

    cfg.rate_limit.enabled = False
    cfg.rate_limit.messages_per_second = 50

    cfg.connection_limits.max_per_ip = 5
    cfg.connection_limits.max_total = 100

    cfg.message_limits.max_decrypted_payload = 1024

    cfg.timeouts.idle_ms = 60000
    cfg.timeouts.check_interval_ms = 2000

    cfg.opcodes.RESPONSE = 0xEEEE

    assert cfg.rate_limit.enabled is False
    assert cfg.rate_limit.messages_per_second == 50
    assert cfg.connection_limits.max_per_ip == 5
    assert cfg.connection_limits.max_total == 100
    assert cfg.message_limits.max_decrypted_payload == 1024
    assert cfg.timeouts.idle_ms == 60000
    assert cfg.opcodes.RESPONSE == 0xEEEE


def test_config_from_yaml(crypto_init):
    """Test Config.from_yaml() with a valid YAML file."""
    yaml_content = """
rate_limiting:
    enabled: true
    messages_per_second: 200
    burst_size: 500
    handshake_attempts_per_minute: 20
    connections_per_minute: 50

connection_limits:
    enabled: true
    max_per_ip: 20
    max_total: 5000

message_limits:
    enabled: true
    max_ws_frame_size: 2097152
    max_decrypted_payload: 131072

timeouts:
    enabled: false
    handshake_ms: 5000
    idle_ms: 120000
    check_interval_ms: 10000

opcodes:
    RESPONSE: 0xFFFF
    STREAM_START: 0xFFFD
    STREAM_DATA: 0xFFFC
    STREAM_END: 0xFFFB
    STREAM_CANCEL: 0xFFFA
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write(yaml_content)
        yaml_path = f.name

    try:
        cfg = op.Config.from_yaml(yaml_path)

        assert cfg.rate_limit.enabled is True
        assert cfg.rate_limit.messages_per_second == 200
        assert cfg.rate_limit.burst_size == 500
        assert cfg.rate_limit.handshake_attempts_per_minute == 20
        assert cfg.rate_limit.connections_per_minute == 50

        assert cfg.connection_limits.max_per_ip == 20
        assert cfg.connection_limits.max_total == 5000

        assert cfg.message_limits.max_ws_frame_size == 2097152
        assert cfg.message_limits.max_decrypted_payload == 131072

        assert cfg.timeouts.enabled is False

        assert cfg.opcodes.RESPONSE == 0xFFFF
    finally:
        os.unlink(yaml_path)


def test_config_from_yaml_missing_file(crypto_init):
    """Test Config.from_yaml() with a non-existent file (should return defaults)."""
    cfg = op.Config.from_yaml("/nonexistent/path/config.yml")
    assert cfg.rate_limit.messages_per_second == 100


def test_server_with_default_config(crypto_init):
    """Test that Server can be created with default config."""
    server = op.Server()
    assert server is not None
    server.stop()


def test_server_with_custom_config(crypto_init, capsys):
    """Test server with strict rate limits."""
    cfg = op.Config()
    cfg.rate_limit.messages_per_second = 1000
    cfg.timeouts.handshake_ms = 30000
    cfg.timeouts.idle_ms = 600000

    server = op.Server(config=cfg)
    client = op.Client(server.public_key, config=cfg)

    client_ready = threading.Event()

    @client.on_ready
    def on_ready():
        client_ready.set()

    try:
        server.start(PORT)
        time.sleep(0.1)
        client.connect(f"ws://localhost:{PORT}")
        assert client_ready.wait(timeout=5), "Client did not become ready"
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)


def test_config_with_message_limit(crypto_init, capsys):
    """Test server with strict message size limit."""
    cfg = op.Config()
    cfg.message_limits.max_decrypted_payload = 100

    server = op.Server(config=cfg)
    client = op.Client(server.public_key, config=cfg)

    client_ready = threading.Event()
    received_payloads = []

    @server.on_anon_payload(0x5001)
    def handle_test(hdl: op.ConnectionHdl, payload: op.Payload):
        received_payloads.append(payload)

    @client.on_ready
    def on_ready():
        client_ready.set()

    try:
        server.start(PORT + 1)
        time.sleep(0.1)
        client.connect(f"ws://localhost:{PORT + 1}")
        assert client_ready.wait(timeout=5), "Client did not become ready"

        small_payload = op.PayloadBuilder(0x5001).add_param("small").build()
        client.send(small_payload)
        time.sleep(0.3)

        # The small payload should have been delivered
        assert len(received_payloads) > 0
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)


def test_config_with_timeouts_disabled(crypto_init, capsys):
    """Test server with timeouts disabled."""
    cfg = op.Config()
    cfg.timeouts.enabled = False

    server = op.Server(config=cfg)
    client = op.Client(server.public_key, config=cfg)

    client_ready = threading.Event()

    @client.on_ready
    def on_ready():
        client_ready.set()

    try:
        server.start(PORT + 2)
        time.sleep(0.1)
        client.connect(f"ws://localhost:{PORT + 2}")
        assert client_ready.wait(timeout=5), "Client did not become ready"
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)


def test_config_all_limits_disabled(crypto_init, capsys):
    """Test server with all rate/message/timeout limits disabled."""
    cfg = op.Config()
    cfg.rate_limit.enabled = False
    cfg.connection_limits.enabled = False
    cfg.message_limits.enabled = False
    cfg.timeouts.enabled = False

    server = op.Server(config=cfg)
    client = op.Client(server.public_key, config=cfg)

    client_ready = threading.Event()
    server_received = threading.Event()

    @server.on_anon_payload(0x6001)
    def handle_echo(hdl: op.ConnectionHdl, payload: op.Payload):
        server_received.set()

    @client.on_ready
    def on_ready():
        client_ready.set()

    try:
        server.start(PORT + 3)
        time.sleep(0.1)
        client.connect(f"ws://localhost:{PORT + 3}")
        assert client_ready.wait(timeout=5), "Client did not become ready"

        client.send(op.PayloadBuilder(0x6001).add_param("test").build())
        assert server_received.wait(timeout=5), "Server did not receive payload"
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
