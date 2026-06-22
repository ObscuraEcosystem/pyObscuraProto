import threading
import time

from ObscuraProto import (
    Client,
    ConnectionHdl,  # Import ConnectionHdl
    Crypto,
    PayloadBuilder,
    Server,
    uint,  # Import the new marker type
)

# --- Opcodes ---
OP_CLIENT_MSG = 0x1001
OP_SERVER_RESPONSE = 0x2002

# --- Synchronization Events ---
client_ready_event = threading.Event()
server_received_event = threading.Event()
client_received_event = threading.Event()


def main():
    """Main function to run the example."""
    # 1. Initialize Crypto
    if Crypto.init() != 0:
        print("[SYSTEM] Failed to initialize crypto library!")
        return
    print("[SYSTEM] Crypto library initialized.")

    # 2. Setup and Start Server
    port = 9002
    server = Server()

    @server.on_payload(OP_CLIENT_MSG)
    def handle_client_message(hdl: ConnectionHdl, message: str, value: uint):
        print("\n--- Server Received Message ---")
        print(f"[SERVER] Received: message='{message}', value={value}")

        # Send a response back
        response = PayloadBuilder(OP_SERVER_RESPONSE).add_param("Hello from server!").build()
        server.send(hdl, response)

        server_received_event.set()

    server.start(port)
    # Give the server a moment to start up
    time.sleep(0.1)

    # 3. Setup and Start Client
    client = Client(server.public_key)

    @client.on_ready
    def on_client_ready():
        print("\n--- Client Ready ---")
        print("[CLIENT] Handshake complete. Ready to send data.")
        client_ready_event.set()

    @client.on_disconnect
    def on_client_disconnect():
        print("[CLIENT] Disconnected from server.")

    @client.on_payload(OP_SERVER_RESPONSE)
    def handle_server_response(response: str):
        print("\n--- Client Received Response ---")
        print(f"[CLIENT] Received: response='{response}'")
        client_received_event.set()

    client.connect(f"ws://localhost:{port}")

    # 4. Wait for client to be ready
    print("[SYSTEM] Waiting for client to complete handshake...")
    ready = client_ready_event.wait(timeout=5)
    if not ready:
        print("[SYSTEM] Client did not become ready in time!")
        server.stop()
        return

    # 5. Client sends a message
    print("\n--- Client Sending Message ---")
    client_payload = PayloadBuilder(OP_CLIENT_MSG).add_param("Hello from client!").add_param(uint(42)).build()
    client.send(client_payload)

    # 6. Wait for the full exchange to complete
    print("[SYSTEM] Waiting for message exchange to complete...")
    server_received = server_received_event.wait(timeout=5)
    client_received = client_received_event.wait(timeout=5)

    if server_received and client_received:
        print("\n[SYSTEM] Communication successful.")
    else:
        print("\n[SYSTEM] Communication failed or timed out.")

    # 7. Shutdown
    print("\n[SYSTEM] Shutting down...")
    client.disconnect()
    server.stop()
    print("[SYSTEM] Shutdown complete.")


if __name__ == "__main__":
    main()
