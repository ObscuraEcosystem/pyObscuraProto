import asyncio

from ObscuraProto import (
    ConnectionHdl,
    Crypto,
    Payload,
    PayloadBuilder,
    Server,
)

# --- Opcodes ---
OP_ADD_REQUEST = 0x1000
OP_ADD_RESPONSE = 0x1001


async def main():
    """Main function to run the server example."""
    # 1. Initialize Crypto
    if Crypto.init() != 0:
        print("[SYSTEM] Failed to initialize crypto library!")
        return
    print("[SYSTEM] Crypto library initialized.")

    # 2. Setup and Start Server
    port = 9003
    server = Server()

    @server.on_request(OP_ADD_REQUEST)
    def handle_add_request(hdl: ConnectionHdl, a: int, b: int) -> Payload:
        print(f"[SERVER] Received add request: {a} + {b}")
        result = a + b
        print(f"[SERVER] Sending response: {result}")
        return PayloadBuilder(OP_ADD_RESPONSE).add_param(result).build()

    try:
        server.start(port)
        print(f"[SERVER] Server started on port {port}.")
        # Save server public key to a temporary file for the client to read
        temp_dir = "."
        public_key_path = f"{temp_dir}/server_public_key.pem"
        with open(public_key_path, "wb") as f:
            f.write(bytes(server.public_key.data))
        print(f"[SERVER] Server public key saved to {public_key_path}")

        print("[SERVER] Press Ctrl+C to stop the server.")
        # Keep the server running indefinitely until interrupted
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        print("[SERVER] Server stopping...")
    except Exception as e:
        print(f"[SERVER] An error occurred: {e}")
    finally:
        # 6. Shutdown
        print("[SYSTEM] Shutting down server...")
        server.stop()
        print("[SYSTEM] Server shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
