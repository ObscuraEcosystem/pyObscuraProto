import asyncio
import time
from ObscuraProto import (
    Crypto,
    Client,
    PayloadBuilder,
    PayloadReader,
    Payload,
    PublicKey,
)

# --- Opcodes ---
OP_ADD_REQUEST = 0x1000
OP_ADD_RESPONSE = 0x1001

async def main():
    """Main function to run the client example."""
    # 1. Initialize Crypto
    if Crypto.init() != 0:
        print("[SYSTEM] Failed to initialize crypto library!")
        return
    print("[SYSTEM] Crypto library initialized.")

    # 2. Setup Client
    port = 9003
    
    # Read server public key from the temporary file
    temp_dir = "."
    public_key_path = f"{temp_dir}/server_public_key.pem"
    
    try:
        with open(public_key_path, "rb") as f:
            server_public_key_bytes = f.read()
        server_public_key = PublicKey()
        server_public_key.data = list(server_public_key_bytes)
        print(f"[CLIENT] Server public key loaded from {public_key_path}")
    except FileNotFoundError:
        print(f"[CLIENT] Error: Server public key file not found at {public_key_path}. Make sure the server is running and has generated the key.")
        return
    except Exception as e:
        print(f"[CLIENT] Error loading server public key: {e}")
        return

    client = Client(server_public_key)

    # Apply decorators using the helper class methods
    @client.on_ready
    def on_ready():
        print("[CLIENT] Handshake complete. Ready to send data.")

    @client.on_disconnect
    def on_disconnect():
        print("[CLIENT] Disconnected from server.")

    client.connect(f"ws://localhost:{port}")

    await asyncio.sleep(5)

    # Client sends a request and waits for the response
    print("[CLIENT] Sending add request (5 + 7)...")
    add_request_payload = PayloadBuilder(OP_ADD_REQUEST).add_param(5).add_param(7).build()
    
    try:
        response_payload = await client.async_request(add_request_payload)
        
        print("[CLIENT] Received response from server.")
        if response_payload.op_code == OP_ADD_RESPONSE:
            reader = PayloadReader(response_payload)
            sum_result = reader.read_int()
            print(f"[CLIENT] Add result: {sum_result}")
            assert sum_result == 12
        else:
            print(f"[CLIENT] Received unexpected response opcode: {hex(response_payload.op_code)}")
        
    except Exception as e:
        print(f"[CLIENT] An error occurred during the request: {e}")
        
        print("[SYSTEM] Request-response exchange completed.")
    
    finally:
        # Shutdown
        print("[SYSTEM] Shutting down client...")
        client.disconnect()
        print("[SYSTEM] Client shutdown complete.")

if __name__ == "__main__":
    asyncio.run(main())
