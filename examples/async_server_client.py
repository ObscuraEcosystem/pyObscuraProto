"""
Async Server/Client Example for pyObscuraProto

This example demonstrates the new async features and context managers
introduced in the pyObscuraProto refactoring.

Features shown:
- Async context managers (async with Server/Client)
- Decorator-based handler registration
- Async request/response patterns (client- and server-initiated)
- Async stream operations
- Error handling (on_error registered first)
- Identity handler returning bool (None coerced to False)
"""

import asyncio
import logging

from ObscuraProto import (
    Client,
    ConnectionHdl,
    Crypto,
    Payload,
    PayloadBuilder,
    PayloadReader,
    Server,
    uint,
)

# Configure logging for the example
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

PORT = 9001


async def main():
    """Run the async server and client example."""
    logger.info("=== Async Server/Client Example ===")

    # Events used to coordinate the demo (set from async handlers).
    client_ready = asyncio.Event()
    server_echo_done = asyncio.Event()
    stream_echo_received = asyncio.Event()

    # --- Server Setup ---
    logger.info("Starting server on port %s...", PORT)
    async with Server(port=PORT) as server:
        # Let the C++ accept loop finish initializing before the client connects.
        # Mitigates known C++ timing window: Server.stop() called before accept
        # loop initializes can hang (~20ms race condition).
        await asyncio.sleep(0.1)
        logger.info(
            "Server public key: %s...",
            bytes(bytearray(server.public_key.data)).hex()[:16],
        )

        # Attach the event loop for thread-safe dispatch of async handlers.
        server.attach_event_loop()

        # Server error handler -- registered FIRST so every later handler
        # benefits from it (error handlers are read at invocation time).
        @server.on_error
        def handle_server_error(error: Exception):
            logger.error("[Server] Error: %s", error)

        # Server payload handler with auto-unpacking.
        @server.on_payload(0x1001)
        async def handle_message(hdl: ConnectionHdl, message: str, value: uint):
            logger.info("[Server] Received message: '%s' with value: %s", message, value)
            # Send an async request back to the client and await its response.
            response = PayloadBuilder(0x2001).add_param(f"Echo: {message}").build()
            await server.async_request(hdl, response, timeout=5.0)
            logger.info("[Server] Client acknowledged the echo")
            server_echo_done.set()

        # Server request handler -- must return a Payload (build the result).
        @server.on_request(0x3001)
        async def handle_calculation(hdl: ConnectionHdl, a: int, b: int) -> Payload:
            logger.info("[Server] Calculating: %s + %s", a, b)
            result = a + b
            return PayloadBuilder(0x3002).add_param(result).build()

        # Server stream handler.
        @server.on_incoming_stream
        def handle_stream(stream):
            logger.info("[Server] New stream: %s", stream.stream_id)

            @stream.on_data
            async def on_data(data: bytes):
                logger.info("[Server] Stream data: %s", data)
                await stream.async_write(b"echo: " + data)

            @stream.on_end
            async def on_end():
                logger.info("[Server] Stream ended")
                await stream.async_end()

        # Server client identity handler -- accept every client for this example.
        # Returns bool: True to accept, False to reject. None is coerced to False.
        @server.on_client_identity
        def verify_identity(hdl: ConnectionHdl, pk) -> bool:
            logger.info(
                "[Server] Verifying identity: %s...",
                bytes(bytearray(pk.data)).hex()[:16],
            )
            return True

        logger.info("\nServer ready. Running the client example...\n")

        # --- Client Setup ---
        client = Client(server.public_key, uri=f"ws://localhost:{PORT}")
        # Present an identity so the server routes us to the regular handlers.
        client.set_client_identity(Crypto.generate_sign_keypair())
        client.attach_event_loop()

        @client.on_error
        def handle_client_error(error: Exception):
            logger.error("[Client] Error: %s", error)

        # Responds to the server-initiated async request (echo acknowledgement).
        @client.on_request(0x2001)
        def handle_echo(message: str) -> Payload:
            logger.info("[Client] Got echo request: '%s'", message)
            return PayloadBuilder(0x2002).add_param("ack").build()

        @client.on_ready
        def on_ready():
            logger.info("[Client] Connected and ready")
            client_ready.set()

        @client.on_disconnect
        def on_disconnect():
            logger.info("[Client] Disconnected")

        async with client:
            # Wait for the handshake to complete.
            await asyncio.wait_for(client_ready.wait(), timeout=5)

            # 1. Client-initiated async request: calculate 20 + 22 on the server.
            logger.info("\n[Client] Sending async calculation request...")
            response = await client.async_request(
                PayloadBuilder(0x3001).add_param(20).add_param(22).build(), timeout=5.0
            )
            reader = PayloadReader(response)
            logger.info("[Client] Server says 20 + 22 = %s", reader.read_int())

            # 2. Server-initiated async request: send a payload that triggers the
            #    server's handle_message -> async_request echo round-trip.
            logger.info("[Client] Sending payload to trigger a server-initiated async request...")
            client.send(PayloadBuilder(0x1001).add_param("hello").add_param(1).build())
            await asyncio.wait_for(server_echo_done.wait(), timeout=5)

            # 3. Stream echo: start a stream and verify the server echoes the data.
            logger.info("[Client] Starting a stream...")
            stream = client.start_stream()

            @stream.on_data
            def on_stream_data(data: bytes):
                logger.info("[Client] Stream echo: %s", data)
                stream_echo_received.set()

            stream.write(b"stream test")
            await asyncio.wait_for(stream_echo_received.wait(), timeout=5)
            stream.end()
            await asyncio.sleep(0.2)

    logger.info("\nExample finished successfully.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\nShutting down...")
