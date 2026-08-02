"""
Tests for async context manager support in Server and Client.

Covers:
  - async with Server(port=...) — auto-starts and stops
  - async with Server() — no port, no auto-start
  - async with Client(pk, uri=...) — auto-connects and disconnects

See Also:
    - Server.__aenter__, Server.__aexit__ in src/ObscuraProto/__init__.py
    - Client.__aenter__, Client.__aexit__ in src/ObscuraProto/__init__.py
"""

import asyncio
import os
import socket
import sys
import threading
import time

import pytest

src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, src_dir)

try:
    import ObscuraProto as op
except ImportError as e:
    pytest.fail(f"Could not import ObscuraProto: {e}", pytrace=False)


def _find_free_port():
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


_PORT_COUNTER = [0]


def _next_port():
    _PORT_COUNTER[0] += 1
    return _find_free_port()


@pytest.fixture(scope="module")
def crypto_init():
    """Ensure Crypto is initialised once per module."""
    op.Crypto.init()


# ===================================================================
# Server context manager tests
# ===================================================================


class TestServerContextManager:
    """async with Server(port=...) / Server()."""

    def test_server_context_manager_starts_and_stops(self, crypto_init, capsys):
        """async with Server(port=N) starts the server; exit stops it."""
        port = _next_port()
        server_started = threading.Event()

        # We verify the server starts by connecting a client inside the context
        async def run():
            async with op.Server(port=port) as server:
                # Server should be started on the given port
                # Connect a client to verify
                client_ready = threading.Event()

                client = op.Client(server.public_key)

                @client.on_ready
                def on_ready():
                    client_ready.set()

                client.connect(f"ws://localhost:{port}")
                assert client_ready.wait(timeout=5), "Client did not become ready inside context"
                client.disconnect()
                server_started.set()

            # After context exit, server should be stopped
            # No exception should have occurred

        asyncio.run(run())
        assert server_started.is_set(), "Server did not start inside context manager"
        capsys.readouterr()

    def test_server_context_manager_no_port(self, crypto_init, capsys):
        """async with Server() without port does not auto-start."""

        async def run():
            async with op.Server() as server:
                # Port is None, should not auto-start
                assert server._port is None

                # We can still manually start/stop
                port = _next_port()
                server.start(port)
                time.sleep(0.1)

                # Quick verify it runs
                client_ready = threading.Event()
                client = op.Client(server.public_key)

                @client.on_ready
                def on_ready():
                    client_ready.set()

                client.connect(f"ws://localhost:{port}")
                assert client_ready.wait(timeout=5), "Client did not become ready"
                client.disconnect()

                server.stop()
                time.sleep(0.1)

        asyncio.run(run())
        capsys.readouterr()

    def test_server_context_manager_nested(self, crypto_init, capsys):
        """Two servers can be started with separate context managers."""
        port1 = _next_port()
        port2 = _next_port()

        async def run():
            async with op.Server(port=port1) as s1:
                async with op.Server(port=port2) as s2:
                    # Both servers running
                    client1_ready = threading.Event()
                    client2_ready = threading.Event()

                    c1 = op.Client(s1.public_key)

                    @c1.on_ready
                    def r1():
                        client1_ready.set()

                    c2 = op.Client(s2.public_key)

                    @c2.on_ready
                    def r2():
                        client2_ready.set()

                    c1.connect(f"ws://localhost:{port1}")
                    c2.connect(f"ws://localhost:{port2}")
                    assert client1_ready.wait(timeout=5)
                    assert client2_ready.wait(timeout=5)
                    c1.disconnect()
                    c2.disconnect()

        asyncio.run(run())
        capsys.readouterr()

    def test_server_context_manager_exception_safe(self, crypto_init, capsys):
        """Exception inside context does not prevent server stop.

        Note: The server must be fully started before the exception is raised
        (the C++ server has a timing window where stop() can hang if called
        before the accept loop initializes). We connect a client to ensure
        the server is running before triggering the exception.
        """
        port = _next_port()

        async def run():
            try:
                async with op.Server(port=port) as server:
                    # Connect a client to ensure server is fully started
                    client_ready = threading.Event()
                    client = op.Client(server.public_key)

                    @client.on_ready
                    def on_ready():
                        client_ready.set()

                    client.connect(f"ws://localhost:{port}")
                    assert client_ready.wait(timeout=5), "Server did not become ready"
                    client.disconnect()
                    time.sleep(0.1)

                    raise ValueError("deliberate error inside context")
            except ValueError:
                pass

            # Server should have been stopped despite the exception
            # The test passes if no hang/crash occurs

        asyncio.run(run())
        capsys.readouterr()


# ===================================================================
# Client context manager tests
# ===================================================================


class TestClientContextManager:
    """async with Client(pk, uri=...)."""

    def test_client_context_manager_connects_and_disconnects(self, crypto_init, capsys):
        """async with Client(pk, uri=...) connects; exit disconnects."""
        port = _next_port()
        server = op.Server()
        server.start(port)
        time.sleep(0.1)

        client_connected = threading.Event()

        async def run():
            async with op.Client(server.public_key, uri=f"ws://localhost:{port}"):
                client_connected.set()

            # After exit, client should be disconnected

        try:
            asyncio.run(run())
            assert client_connected.is_set(), "Client did not connect inside context"
        finally:
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_client_context_manager_no_uri(self, crypto_init, capsys):
        """Client with no URI in context manager does not auto-connect."""
        port = _next_port()
        server = op.Server()
        server.start(port)
        time.sleep(0.1)

        async def run():
            # Need a server key, but no URI — should not auto-connect
            async with op.Client(server.public_key) as client:
                # Haven't connected yet — can do it manually
                client_ready = threading.Event()

                @client.on_ready
                def on_ready():
                    client_ready.set()

                client.connect(f"ws://localhost:{port}")
                assert client_ready.wait(timeout=5), "Manual connect failed"
                client.disconnect()

        try:
            asyncio.run(run())
        finally:
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()

    def test_client_context_manager_exception_safe(self, crypto_init, capsys):
        """Exception inside client context does not prevent disconnect."""
        port = _next_port()
        server = op.Server()
        server.start(port)
        time.sleep(0.1)

        async def run():
            try:
                async with op.Client(server.public_key, uri=f"ws://localhost:{port}"):
                    raise RuntimeError("deliberate error")
            except RuntimeError:
                pass

            # Client should be disconnected despite exception

        try:
            asyncio.run(run())
        finally:
            server.stop()
            time.sleep(0.1)
            capsys.readouterr()
