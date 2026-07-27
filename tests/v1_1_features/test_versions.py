"""
Version constants and negotiation tests for C++ v1.1 features.
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

PORT = 9100


@pytest.fixture(scope="module")
def crypto_init():
    op.Crypto.init()


def test_v1_1_constant():
    assert op.V1_1 == 0x0101
    assert op.V1_0 == 0x0100
    assert 0x0101 in op.SUPPORTED_VERSIONS
    assert 0x0100 in op.SUPPORTED_VERSIONS


def test_version_negotiation_unit():
    negotiator = op._bindings.VersionNegotiator

    assert negotiator.negotiate([op.V1_1, op.V1_0], [op.V1_1, op.V1_0]) == op.V1_1
    assert negotiator.negotiate([op.V1_0, op.V1_1], [op.V1_1, op.V1_0]) == op.V1_0
    assert negotiator.negotiate([op.V1_0], [op.V1_0]) == op.V1_0
    assert negotiator.negotiate([op.V1_1], [op.V1_1]) == op.V1_1
    assert negotiator.negotiate([op.V1_0], [op.V1_1]) is None
    assert negotiator.negotiate([op.V1_1], [op.V1_0]) is None
    assert negotiator.negotiate([], [op.V1_0]) is None
    assert negotiator.negotiate([op.V1_0], []) is None
    assert negotiator.negotiate([], []) is None


def test_negotiation_same_defaults(crypto_init, capsys):
    port = PORT
    server = op.Server()
    client = op.Client(server.public_key)
    client_ready = threading.Event()

    @client.on_ready
    def on_ready():
        client_ready.set()

    try:
        server.start(port)
        time.sleep(0.1)
        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Client did not become ready with default versions"
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)


def test_negotiation_both_v1_0(crypto_init, capsys):
    port = PORT + 1
    cfg_s = op.Config.with_defaults()
    cfg_s.supported_versions = [op.V1_0]
    cfg_c = op.Config.with_defaults()
    cfg_c.supported_versions = [op.V1_0]

    server = op.Server(config=cfg_s)
    client = op.Client(server.public_key, config=cfg_c)
    client_ready = threading.Event()

    @client.on_ready
    def on_ready():
        client_ready.set()

    try:
        server.start(port)
        time.sleep(0.1)
        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Client did not become ready with V1_0 only"
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)


def test_negotiation_both_v1_1(crypto_init, capsys):
    port = PORT + 2
    cfg_s = op.Config.with_defaults()
    cfg_s.supported_versions = [op.V1_1]
    cfg_c = op.Config.with_defaults()
    cfg_c.supported_versions = [op.V1_1]

    server = op.Server(config=cfg_s)
    client = op.Client(server.public_key, config=cfg_c)
    client_ready = threading.Event()

    @client.on_ready
    def on_ready():
        client_ready.set()

    try:
        server.start(port)
        time.sleep(0.1)
        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Client did not become ready with V1_1 only"
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)


def test_negotiation_fallback_v1_0(crypto_init, capsys):
    port = PORT + 3
    cfg_s = op.Config.with_defaults()
    cfg_s.supported_versions = [op.V1_1, op.V1_0]
    cfg_c = op.Config.with_defaults()
    cfg_c.supported_versions = [op.V1_0]

    server = op.Server(config=cfg_s)
    client = op.Client(server.public_key, config=cfg_c)
    client_ready = threading.Event()

    @client.on_ready
    def on_ready():
        client_ready.set()

    try:
        server.start(port)
        time.sleep(0.1)
        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Expected fallback to V1_0 failed"
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)


def test_negotiation_client_prefers_v1_0(crypto_init, capsys):
    port = PORT + 4
    cfg_s = op.Config.with_defaults()
    cfg_s.supported_versions = [op.V1_1, op.V1_0]
    cfg_c = op.Config.with_defaults()
    cfg_c.supported_versions = [op.V1_0, op.V1_1]

    server = op.Server(config=cfg_s)
    client = op.Client(server.public_key, config=cfg_c)
    client_ready = threading.Event()

    @client.on_ready
    def on_ready():
        client_ready.set()

    try:
        server.start(port)
        time.sleep(0.1)
        client.connect(f"ws://localhost:{port}")
        assert client_ready.wait(timeout=5), "Client did not become ready"
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)


def test_negotiation_no_common_fails(crypto_init, capsys):
    port = PORT + 5
    cfg_s = op.Config.with_defaults()
    cfg_s.supported_versions = [op.V1_1]
    cfg_c = op.Config.with_defaults()
    cfg_c.supported_versions = [op.V1_0]

    server = op.Server(config=cfg_s)
    client = op.Client(server.public_key, config=cfg_c)
    client_ready = threading.Event()

    @client.on_ready
    def on_ready():
        client_ready.set()

    try:
        server.start(port)
        time.sleep(0.1)
        client.connect(f"ws://localhost:{port}")
        assert not client_ready.wait(timeout=5), "Handshake should have failed"
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)


def test_negotiation_no_common_fails_reverse(crypto_init, capsys):
    port = PORT + 6
    cfg_s = op.Config.with_defaults()
    cfg_s.supported_versions = [op.V1_0]
    cfg_c = op.Config.with_defaults()
    cfg_c.supported_versions = [op.V1_1]

    server = op.Server(config=cfg_s)
    client = op.Client(server.public_key, config=cfg_c)
    client_ready = threading.Event()

    @client.on_ready
    def on_ready():
        client_ready.set()

    try:
        server.start(port)
        time.sleep(0.1)
        client.connect(f"ws://localhost:{port}")
        assert not client_ready.wait(timeout=5), "Handshake should have failed"
    finally:
        client.disconnect()
        server.stop()
        time.sleep(0.1)
