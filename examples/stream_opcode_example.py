"""
Streaming with application-level op codes over ObscuraProto.

Demonstrates:
  - Server: @on_anon_stream(op_code) to handle anonymous streams by op code
  - Client: start_stream(stream_op_code) to tag outgoing streams
  - stream.op_code property to retrieve the op code on the receiver side
  - Two logical channels (op_code 0x3001 = chat, 0x3002 = file transfer)
"""

import time

import ObscuraProto as op

op.Crypto.init()

server = op.Server()

CHAT_OP = 0x3001
FILE_OP = 0x3002


# ---- Server ----
@server.on_anon_stream(CHAT_OP)
def handle_chat(stream: op.Stream):
    print(f"[SERVER] Chat stream opened, op_code={stream.op_code:#x}")

    @stream.on_data
    def on_data(data: bytes):
        msg = data.decode()
        print(f"[SERVER] Chat message: {msg}")
        stream.write(b"chat_ack: " + data)

    @stream.on_end
    def on_end():
        print("[SERVER] Chat stream closed")
        stream.end()


@server.on_anon_stream(FILE_OP)
def handle_file(stream: op.Stream):
    print(f"[SERVER] File stream opened, op_code={stream.op_code:#x}")
    chunks = []

    @stream.on_data
    def on_data(data: bytes):
        chunks.append(data)
        print(f"[SERVER] File chunk received ({len(data)} bytes)")

    @stream.on_end
    def on_end():
        total = sum(len(c) for c in chunks)
        print(f"[SERVER] File transfer complete, total {total} bytes")
        stream.write(f"received {total} bytes".encode())
        stream.end()


server.start(9008)
time.sleep(0.1)

# ---- Client ----
client = op.Client(server.public_key)


@client.on_ready
def on_ready():
    print("[CLIENT] Connected. Opening chat and file streams...")

    # Chat stream — op_code 0x3001
    chat = client.start_stream(CHAT_OP)
    print(f"[CLIENT] Chat stream, op_code={chat.op_code:#x}")

    @chat.on_data
    def on_chat_ack(data: bytes):
        print(f"[CLIENT] Chat ack: {data}")

    @chat.on_end
    def on_end_handler():
        print("[CLIENT] Chat stream done")

    chat.write(b"Hello via op_code stream!")
    time.sleep(0.1)
    chat.end()

    # File transfer stream — op_code 0x3002
    file_stream = client.start_stream(FILE_OP)
    print(f"[CLIENT] File stream, op_code={file_stream.op_code:#x}")

    @file_stream.on_data
    def on_file_ack(data: bytes):
        print(f"[CLIENT] File ack: {data}")

    @file_stream.on_end
    def on_end():
        print("[CLIENT] File stream done")

    file_stream.write(b"chunk1_data_" * 100)
    file_stream.write(b"chunk2_data_" * 100)
    time.sleep(0.1)
    file_stream.end()

    print("[CLIENT] All streams done")


client.connect("ws://localhost:9008")
time.sleep(1)

# ---- Cleanup ----
client.disconnect()
server.stop()
print("\nDone.")
