# pyObscuraProto

<p align="center">
  <a href="https://github.com/kretoffer/pyObscuraProto/actions"><img src="https://img.shields.io/github/actions/workflow/status/kretoffer/pyObscuraProto/autotests.yml?style=for-the-badge&logo=github&label=tests&color=8A2BE2" alt="Tests"></a>
  <a href="https://github.com/kretoffer/pyObscuraProto/stargazers"><img src="https://img.shields.io/github/stars/kretoffer/pyObscuraProto?style=for-the-badge&logo=githubsponsors&logoColor=FFFFFF&label=stars&color=FFD700" alt="Stars"></a>
  <a href="https://github.com/kretoffer/pyObscuraProto/issues"><img src="https://img.shields.io/github/issues/kretoffer/pyObscuraProto?style=for-the-badge&logo=openbugbounty&logoColor=FFFFFF&label=issues&color=FF6B6B" alt="Issues"></a>
  <a href="https://www.python.org"><img src="https://img.shields.io/badge/python-3.13%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.13+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/kretoffer/pyObscuraProto?style=for-the-badge&logo=libreoffice" alt="LICENSE"></a>
</p>

Python wrapper for the [ObscuraProto](https://github.com/anomalyco/ObscuraProto) C++ library — end-to-end encrypted communication over WebSocket.

## Features

- **End-to-end encryption** — Noise Protocol Framework (NX pattern) with libsodium
- **Server authentication** — long-term signing keypair, clients verify the server's public key
- **Automatic version negotiation** — client and server agree on protocol version during handshake
- **Binary payload builder/reader** — type-safe fluent API (`PayloadBuilder` / `PayloadReader`)
- **Auto-unpacking** — payload parameters are unpacked automatically based on Python type hints
- **Bidirectional streaming** — multiplexed data streams over a single encrypted connection
- **Anonymous & authenticated sessions** — handle clients with or without identity; identity verification callbacks
- **Configuration system** — rate limits, connection limits, message size limits, timeouts; load from YAML or set from Python
- **Fully typed** — complete Python type annotations; checked with pyright
- **High performance** — C++ core via pybind11, GIL released during I/O

## Installation

```bash
pip install pyObscuraProto
```

### Build from source

```bash
git clone --recurse-submodules https://github.com/anomalyco/pyObscuraProto.git
cd pyObscuraProto
python -m venv .venv && source .venv/bin/activate
pip install cmake
pip install -e .
```

Requires CMake 3.14+, C++17 compiler, and libsodium (`brew install libsodium` on macOS, `apt install libsodium-dev` on Linux).

## Quick Start

```python
import ObscuraProto as op

op.Crypto.init()

server = op.Server()
server.start(9001)

client = op.Client(server.public_key)
client.connect("ws://localhost:9001")

@client.on_ready
def on_ready():
    payload = op.PayloadBuilder(0x1001).add_param("hello").build()
    client.send(payload)
```

See [examples/](examples/) for more.

## Streaming API

Bidirectional multiplexed streams over a single encrypted connection.

```python
import ObscuraProto as op

op.Crypto.init()

# --- Server ---
server = op.Server()

@server.on_incoming_stream
def handle_stream(stream: op.Stream):
    @stream.on_data
    def on_data(data: bytes):
        stream.write(b"echo: " + data)

    @stream.on_end
    def on_end():
        stream.end()

server.start(9006)

# --- Client ---
client = op.Client(server.public_key)

@client.on_ready
def on_ready():
    stream = client.start_stream()

    @stream.on_data
    def on_data(data: bytes):
        print(f"Echo: {data}")

    stream.write(b"hello")
    stream.end()

client.connect("ws://localhost:9006")
```

Full example: [examples/streaming_example.py](examples/streaming_example.py)

## Anonymous & Authenticated Sessions

Clients connecting **without** an identity key are treated as **anonymous** — their messages are routed through anonymous handlers. Clients that present a verified Ed25519 identity key are **authenticated** and use the regular handlers.

### Anonymous handlers

```python
@server.on_anon_payload(0x5001)
def handle_anon_register(hdl: op.ConnectionHdl, data: bytes):
    print(f"Anonymous registration: {data}")
    server.send_anonymous(hdl, op.PayloadBuilder(0x5001).add_param("ok").build())

@server.on_anon_request(0x5002)
def handle_anon_auth(hdl: op.ConnectionHdl, token: str) -> op.Payload:
    return op.PayloadBuilder(0x5003).add_param(True).build()

@server.anon_default_payload_handler
def handle_anon_default(hdl: op.ConnectionHdl, payload: op.Payload):
    print(f"Unhandled anonymous opcode: {payload.op_code:04x}")
```

### Client authentication

```python
# --- Server ---
server = op.Server()

@server.on_client_identity
def check_identity(hdl: op.ConnectionHdl, pk: op.PublicKey) -> bool:
    # Accept only known public keys
    return pk.data == allowed_key.data

# --- Client ---
client = op.Client(server.public_key)
client.set_client_identity(my_identity_keypair)  # Ed25519 keypair
client.connect("ws://localhost:9001")

# Server can now address this client by identity:
server.send_to_identity(client_pk, payload)
identity = server.get_client_identity(hdl)
```

Full example: [examples/client_identity_example.cpp](https://github.com/kretoffer/ObscuraProto/blob/main/examples/client_identity_example.cpp)

## Configuration

ObscuraProto supports fine-grained configuration of rate limits, connection limits, message size limits, and timeouts. Create a `Config` object and pass it to `Server` or `Client`:

```python
cfg = op.Config()

# Rate limiting — token bucket per connection
cfg.rate_limit.messages_per_second = 200
cfg.rate_limit.burst_size = 500

# Connection limits — max per IP and total
cfg.connection_limits.max_per_ip = 20
cfg.connection_limits.max_total = 5000

# Message size limits
cfg.message_limits.max_decrypted_payload = 65535

# Timeouts
cfg.timeouts.idle_ms = 600000      # 10 min idle disconnect
cfg.timeouts.handshake_ms = 15000  # 15 sec handshake timeout

server = op.Server(config=cfg)
client = op.Client(server.public_key, config=cfg)
```

Or load from a YAML file (see [config_example.yml](https://github.com/kretoffer/ObscuraProto/blob/main/config_example.yml)):

```python
cfg = op.Config.from_yaml("path/to/config.yml")
```

| Config field | Default | Description |
|---|---|---|
| `rate_limit.enabled` | `true` | Enable/disable all rate limiting |
| `rate_limit.messages_per_second` | `100` | Max messages per connection per second |
| `rate_limit.burst_size` | `200` | Token bucket burst |
| `rate_limit.handshake_attempts_per_minute` | `10` | Max handshake attempts per IP per minute |
| `rate_limit.connections_per_minute` | `30` | Max new connections per IP per minute |
| `connection_limits.max_per_ip` | `10` | Max concurrent connections from one IP |
| `connection_limits.max_total` | `1000` | Max total concurrent connections |
| `message_limits.max_ws_frame_size` | `1048576` | Max raw WebSocket frame size (bytes) |
| `message_limits.max_decrypted_payload` | `65535` | Max decrypted payload size (bytes) |
| `timeouts.handshake_ms` | `10000` | Handshake timeout (ms) |
| `timeouts.idle_ms` | `300000` | Idle connection timeout (ms) |
| `timeouts.check_interval_ms` | `5000` | Timeout check interval (ms) |

## API Reference

| Class / Function | Description |
|---|---|
| `Server` | Encrypted WebSocket server. Decorators: `@on_payload(opcode)`, `@on_request(opcode)`, `@on_anon_payload(opcode)`, `@on_anon_request(opcode)`, `@on_incoming_stream`, `@default_payload_handler`, `@anon_default_payload_handler`, `@on_client_identity` |
| `Client(server_pk)` | Encrypted WebSocket client. Decorators: `@on_ready`, `@on_disconnect`, `@on_payload(opcode)`, `@on_request(opcode)`, `@on_incoming_stream` |
| `Stream` | Bidirectional data stream. Decorators: `@on_data`, `@on_end`, `@on_cancel`. I/O: `write()`, `end()`, `cancel()`, `async_write()`, `async_end()`, `async_cancel()` |
| `PayloadBuilder(opcode)` | Build binary payloads. `add_param(str / int / uint / bool / float / bytes)`, `.build()` |
| `PayloadReader(payload)` | Read binary payloads. `read_string()`, `read_int()`, `read_uint()`, `read_bool()`, `read_float()`, `read_bytes()` |
| `Payload` | Raw payload with `.op_code` and `.parameters`. Has `.serialize()` / `Payload.deserialize()` |
| `uint` | Type hint marker: `def handler(value: uint)` reads the parameter as unsigned |
| `Config` | Server/client configuration. Sub-structs: `rate_limit`, `connection_limits`, `message_limits`, `timeouts`, `opcodes`. Methods: `from_yaml(path)`, `with_defaults()` |
| `Crypto` | Static crypto: `init()`, `generate_kx_keypair()`, `generate_sign_keypair()`, `sign()`, `verify()`, `encrypt()`, `decrypt()` |
| `KeyPair` / `PublicKey` / `PrivateKey` | Key types with `.data` field |
| `ConnectionHdl` | Opaque connection handle for targeting specific clients |

## Examples

| Example | Description |
|---|---|---|
| [python_websocket_example.py](examples/python_websocket_example.py) | Minimal send/response with auto-unpacking |
| [request_response_example/](examples/request_response_example/) | Request-response pattern (async server + client) |
| [streaming_example.py](examples/streaming_example.py) | Bidirectional streaming echo |
| [client_identity_example.cpp](https://github.com/kretoffer/ObscuraProto/blob/main/examples/client_identity_example.cpp) | Anonymous registration + authenticated session (C++) |

## Development

```bash
source .venv/bin/activate
pip install -e .
pre-commit install
```

- **Ruff** — linting & formatting
- **Pyright** — type checking
- **pytest** — testing (`python -m pytest tests/`)
- **Pre-commit** — runs checks before every commit

See [CONTRIBUTING.md](CONTRIBUTING.md) for full guidelines.

## License

MIT © 2025 Kretov Artem. See [LICENSE](LICENSE).
