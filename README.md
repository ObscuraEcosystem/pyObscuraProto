<p align="center">
  <h1>pyObscuraProto</h1>
  <a href="https://github.com/ObscuraEcosystem/pyObscuraProto/actions"><img src="https://img.shields.io/github/actions/workflow/status/ObscuraEcosystem/pyObscuraProto/autotests.yml?style=for-the-badge&logo=github&label=tests&color=8A2BE2" alt="Tests"></a>
  <a href="https://github.com/ObscuraEcosystem/pyObscuraProto/stargazers"><img src="https://img.shields.io/github/stars/ObscuraEcosystem/pyObscuraProto?style=for-the-badge&logo=githubsponsors&logoColor=FFFFFF&label=stars&color=FFD700" alt="Stars"></a>
  <a href="https://github.com/ObscuraEcosystem/pyObscuraProto/issues"><img src="https://img.shields.io/github/issues/ObscuraEcosystem/pyObscuraProto?style=for-the-badge&logo=openbugbounty&logoColor=FFFFFF&label=issues&color=FF6B6B" alt="Issues"></a>
  <a href="https://www.python.org"><img src="https://img.shields.io/badge/python-3.13%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.13+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/ObscuraEcosystem/pyObscuraProto?style=for-the-badge&logo=libreoffice" alt="LICENSE"></a>
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
- **Connection lifecycle callbacks** — `@server.on_open` / `@server.on_close` for tracking connect and disconnect events
- **Configuration system** — rate limits, connection limits, message size limits, timeouts; load from YAML or set from Python
- **Fully typed** — complete Python type annotations; checked with pyright
- **High performance** — C++ core via pybind11, GIL released during I/O
- **Typed exceptions** — C++ errors mapped to Python types: `TimeoutError` (builtin), `LogicError` (`RuntimeError`), `InvalidArgument` (`ValueError`)
- **Request timeouts** — per-request timeout on all request APIs (`sync_request` and async), forwarded to the C++ core; `timeout <= 0` disables the Python-side wrapper
- **Seed key derivation** — `Crypto.keypair_from_seed()` (strictly 32-byte seeds, otherwise `ValueError`) and `Crypto.derive_public_key()`
- **Deadlock-safe callbacks** — blocking calls (`sync_request`, `stop`, `disconnect`) from callback/IO threads raise `LogicError` instead of deadlocking

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

Requires CMake 3.14+ and a C++17 compiler.

## Quick Start

```python
import asyncio
from ObscuraProto import Server, Client, PayloadBuilder

# Server
async with Server(port=9001) as server:
    @server.on_payload(0x1001)
    def handle(hdl, data: str):
        print(f"Got payload: {data}")
    await asyncio.Future()  # run forever

# Client  
async with Client(server.public_key, uri="ws://localhost:9001") as client:
    @client.on_ready
    def ready():
        client.send(PayloadBuilder(0x1001).add_param("Hello").build())
    await asyncio.Future()
```

See [examples/](examples/) for more.

## Streaming API

Bidirectional multiplexed streams over a single encrypted connection.

```python
import asyncio
from ObscuraProto import Server, Client

# --- Server ---
async with Server(port=9006) as server:
    @server.on_incoming_stream
    def handle_stream(stream):
        @stream.on_data
        def on_data(data: bytes):
            stream.write(b"echo: " + data)

        @stream.on_end
        def on_end():
            stream.end()

    await asyncio.Future()  # run forever

# --- Client ---
async with Client(server.public_key, uri="ws://localhost:9006") as client:
    @client.on_ready
    def on_ready():
        stream = client.start_stream()

        @stream.on_data
        def on_data(data: bytes):
            print(f"Echo: {data}")

        stream.write(b"hello")
        stream.end()

    await asyncio.Future()  # run forever
```

Full example: [examples/streaming_example.py](examples/streaming_example.py)

Since v1.1.1, `write()`, `end()` and `cancel()` are `noexcept` — after the stream is closed they silently drop the call instead of raising an exception.

### Stream Properties

The `Stream` class provides several useful properties:

```python
# Get the stream's op code (if set)
op_code = stream.op_code  # Returns int or None
```

### Starting Streams with Custom Op Codes

Both `Server.start_stream()` and `Client.start_stream()` accept an optional `stream_op_code` parameter:

```python
# Server starts a stream with a specific op code
stream = server.start_stream(hdl, stream_op_code=0x3001)

# Client starts a stream with a specific op code
stream = client.start_stream(stream_op_code=0x3001)
```

### Handling Streams by Op Code

Use decorators to handle streams with specific op codes:

```python
# Server handles authenticated streams with specific op codes
@server.on_stream(0x3001)
def handle_stream_3001(stream):
    @stream.on_data
    def on_data(data: bytes):
        print(f"Received on stream 0x3001: {data}")

# Server handles anonymous streams with specific op codes
@server.on_anon_stream(0x4001)
def handle_anon_stream_4001(stream):
    @stream.on_data
    def on_data(data: bytes):
        print(f"Received anonymous on stream 0x4001: {data}")

# Client handles incoming streams from server with specific op codes
@client.on_stream(0x3001)
def handle_incoming_stream_3001(stream):
    @stream.on_data
    def on_data(data: bytes):
        print(f"Received from server on stream 0x3001: {data}")
```

## Async Support

The library provides full async support for modern Python applications:

- **attach_event_loop()** — Attach callbacks to an asyncio event loop for thread-safe dispatch
- **async_request()** — Send requests and get futures for responses. The C++ side returns a `CppPayloadFuture` immediately; the response is awaited through an `asyncio.Future` fulfilled with `loop.call_soon_threadsafe` — the event loop never busy-polls and **no thread-pool thread is blocked** waiting. Accepts a `timeout` parameter (seconds, default 30 s) and raises `ObscuraProto.TimeoutError` if the remote side never responds.
- **async_write()**, **async_end()**, **async_cancel()** — Async versions of stream I/O operations
- **async_start_stream()** — Async version of start_stream() that doesn't block the event loop
- **async_request_to_identity()** — Send requests to a client identified by their public key (async; uses the same awaitable bridge and `timeout` parameter as `async_request()`)
- **Context managers** — Use `async with Server(port=...)` and `async with Client(pk, uri=...)` for automatic resource management

Example async server setup:

```python
import asyncio
from ObscuraProto import Server, Client, PayloadBuilder

# Server
async with Server(port=9001) as server:
    @server.on_payload(0x1001)
    async def handle(hdl, data: str):
        result = await process_data(data)
        server.send(hdl, PayloadBuilder(0x1002).add_param(result).build())
    await asyncio.Future()  # run forever

# Client
async with Client(server.public_key, uri="ws://localhost:9001") as client:
    @client.on_ready
    def ready():
        client.send(PayloadBuilder(0x1001).add_param("Hello").build())
    await asyncio.Future()  # run forever
```

## Request Timeouts

Every async request API accepts a `timeout` parameter in **seconds** (float, default `30.0`). If the remote side does not respond in time, `ObscuraProto.TimeoutError` is raised:

```python
import logging
from ObscuraProto import Client, TimeoutError

logger = logging.getLogger(__name__)

async def request_with_timeout(client: Client, payload) -> None:
    try:
        response = await client.async_request(payload, timeout=5.0)
        print(f"Response: {response.op_code:04x}")
    except TimeoutError:
        logger.warning("request timed out, continuing")
```

Timeout-aware request APIs:

- `Client.sync_request(payload, timeout_ms=...)`
- `Client.async_request(payload, timeout=30.0)`
- `Server.async_request(hdl, payload, timeout=30.0)`
- `Server.async_request_to_identity(identity_pk, payload, timeout=30.0)`

Every Python API forwards its timeout to the C++ core: `Client.sync_request` takes `timeout_ms` (milliseconds, `0` = unlimited), while `Client.async_request` and `Server.async_request` take `timeout` in **seconds** and forward it as `timeout_ms`. Passing `timeout <= 0` or `None` disables the Python-side `asyncio.wait_for` — the C++ core then owns the timeout. On expiry, `ObscuraProto.TimeoutError` (a subclass of the builtin `TimeoutError`) is raised.

The default request timeout is set via `Config.timeouts.request_ms` (default `30000` ms, `0` = unlimited, loadable from YAML).

## Logging

The library uses Python's `logging` module under the `ObscuraProto` logger with a NullHandler by default. Users can configure logging as needed:

```python
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ObscuraProto")
logger.setLevel(logging.DEBUG)  # Enable debug logging
```

## Error Handling

Errors in handlers are raised (not silently swallowed), unless an on_error handler is set. Users should catch exceptions at the business logic level. Use error handlers to set custom error handling:

```python
# Server error handler
@server.on_error
def handle_error(error: Exception):
    print(f"Server callback error: {error}")

# Client error handler
@client.on_error
def handle_error(error: Exception):
    print(f"Client callback error: {error}")

# Stream error handler
@stream.on_error
def handle_error(error: Exception):
    print(f"Stream callback error: {error}")
```

**Identity handler return value:** The `@server.on_client_identity` handler must return `bool` — `True` to accept, `False` to reject. Returning `None` is coerced to `False` (both sync and async handlers).

### Typed exceptions

Since 1.1.1, C++ exceptions are mapped to typed Python exceptions instead of a generic `RuntimeError`:

| Exception | Python base class | Raised when |
|---|---|---|
| `ObscuraProto.TimeoutError` | builtin `TimeoutError` | an async request did not respond within `timeout` |
| `ObscuraProto.LogicError` | `RuntimeError` | a blocking call from a callback/IO thread (deadlock guard), or awaiting a single-use `CppPayloadFuture` twice |
| `ObscuraProto.InvalidArgument` | `ValueError` | invalid arguments surfaced from C++ (e.g. wrong key size) |

Base-class `except` clauses keep working, so `except TimeoutError`, `except RuntimeError` and `except ValueError` all catch the corresponding ObscuraProto exceptions:

```python
from ObscuraProto import Client, PayloadBuilder, TimeoutError, LogicError

async def guarded_request(client: Client):
    try:
        response = await client.async_request(PayloadBuilder(0x1001).build(), timeout=2.0)
        return response
    except TimeoutError:
        print("server did not answer in time")
    except LogicError:
        print("request already consumed or called from a callback thread")
```

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
client.connect("ws://localhost:9001")

# Server can now address this client by identity:
server.send_to_identity(client_pk, payload)
identity = server.get_client_identity(hdl)
```

Full example: [examples/client_identity_example.cpp](https://github.com/ObscuraEcosystem/ObscuraProto/blob/main/examples/client_identity_example.cpp)

## Threading Model & Deadlock Protection

The C++ websocket layer invokes Python callbacks on its own I/O threads. Blocking calls made from inside such a callback would self-deadlock — the very thread that must service the request is the one making it. Since 1.1.1 the bindings detect this with a thread-local callback flag and raise `ObscuraProto.LogicError` instead of hanging:

- `Client.sync_request()` / `Server.sync_request()` / `sync_request_to_identity()` — raise `LogicError` when called from a callback thread
- `Server.stop()` and `Client.disconnect()` — raise `LogicError` when called from a callback thread (self-join guard)

```python
from ObscuraProto import Client, LogicError

def on_ready(client: Client):
    try:
        client.disconnect()
    except LogicError:
        print("disconnect() is not allowed from a callback thread")
```

Calling a blocking `sync_request` from an **async handler** (the event-loop thread) emits a warning ("sync_request is blocking and must not be called from an async handler") because it would stall the event loop. From inside handlers, use `await async_request()` instead:

```python
from ObscuraProto import Server, PayloadBuilder

@server.on_payload(0x1001)
async def handle(hdl, data: str):
    # Wrong: sync_request would stall the event loop / deadlock the I/O thread
    # response = server.sync_request(hdl, PayloadBuilder(0x1002).build())
    # Correct:
    response = await server.async_request(hdl, PayloadBuilder(0x1002).build())
```

Stream operations and async request futures run on dedicated module-level thread pools: a single-worker stream executor preserves FIFO ordering of `write`/`end`/`cancel`, and a separate 4-worker request executor waits on C++ response futures without blocking the event loop.

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

# Protocol versions to support (default: [V1_0, V1_1])
cfg.supported_versions = [op.V1_0, op.V1_1]

server = op.Server(config=cfg)
client = op.Client(server.public_key, config=cfg)
```

Or load from a YAML file (see [config_example.yml](https://github.com/ObscuraEcosystem/ObscuraProto/blob/main/config_example.yml)):

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
| `timeouts.request_ms` | `30000` | Request timeout (ms); `0` = unlimited |

## RateLimiter & SecureBuffer

Standalone low-level bindings added in 1.1.1. For the built-in connection handling prefer the `Config.rate_limit` settings above; these classes are for custom rate enforcement and secure key material.

### RateLimiter

Token-bucket plus sliding-window rate enforcement, built from a `RateLimitConfig`:

```python
from ObscuraProto import RateLimiter, RateLimitConfig

cfg = RateLimitConfig()
cfg.enabled = True
cfg.messages_per_second = 100
cfg.burst_size = 200
rl = RateLimiter(cfg)

conn_id = rl.register_connection("203.0.113.7")   # returns an int connection id
if rl.check_message_rate(conn_id):
    rl.record_message(conn_id)
rl.unregister_connection(conn_id, "203.0.113.7")
```

Methods: `check_connection_rate(ip)`, `record_connection(ip)`, `check_handshake_rate(ip)`, `record_handshake(ip)`, `check_message_rate(conn_id)`, `record_message(conn_id)`, `check_active_connections(ip)`, `register_connection(ip)`, `unregister_connection(conn_id, ip)`, `active_total()`, `cleanup()`.

### SecureBuffer

Heap memory allocated with `sodium_malloc` and zeroed with `sodium_memzero` on `clear()` and destruction. Python only ever receives **copies** of the contents, never a reference to the internal memory:

```python
from ObscuraProto import SecureBuffer

buf = SecureBuffer(32)             # zero-initialized allocation
buf.from_bytes(b"secret-key-material")
data = buf.to_bytes()              # copy — internal memory stays opaque
len(buf)                           # 19
buf.clear()                        # wipes memory with sodium_memzero
```

Additional methods: `resize(new_size)`, `size()`, `empty()`; supports `bytes(buf)` and `len(buf)`.

## API Reference

| Class / Function | Description |
|---|---|
| `Server` | Encrypted WebSocket server. Decorators: `@server.on_payload(op_code)`, `@server.on_request(op_code)`, `@server.on_open`, `@server.on_close`, `@server.on_client_identity`, `@server.on_incoming_stream`, `@server.on_stream(op_code)`, `@server.on_anon_payload(op_code)`, `@server.on_anon_request(op_code)`, `@server.on_anon_stream(op_code)`, `@server.anon_default_payload_handler`, `@server.on_error`. Requests: `sync_request(hdl, payload)`, `async_request(hdl, payload, timeout=30.0)`, `async_request_to_identity(identity_pk, payload, timeout=30.0)` |
| `Client` | Encrypted WebSocket client. Decorators: `@client.on_ready`, `@client.on_disconnect`, `@client.on_payload(op_code)`, `@client.on_request(op_code)`, `@client.on_incoming_stream`, `@client.on_stream(op_code)`, `@client.on_error`. Requests: `sync_request(payload, timeout_ms=0)`, `async_request(payload, timeout=30.0)` |
| `Stream` | Bidirectional data stream. Decorators: `@stream.on_data`, `@stream.on_end`, `@stream.on_cancel`, `@stream.on_error`. `write()`/`end()`/`cancel()` are `noexcept` — after close they silently drop |
| `PayloadBuilder(opcode)` | Build binary payloads. `add_param(str / int / uint / bool / float / bytes)`, `.build()` |
| `PayloadReader(payload)` | Read binary payloads. `read_string()`, `read_int()`, `read_uint()`, `read_bool()`, `read_float()`, `read_bytes()` |
| `Payload` | Raw payload with `.op_code` and `.parameters`. Has `.serialize()` / `Payload.deserialize()` |
| `uint` | Type hint marker: `def handler(value: uint)` reads the parameter as unsigned |
| `Config` | Server/client configuration. Sub-structs: `rate_limit`, `connection_limits`, `message_limits`, `timeouts`, `opcodes`, `supported_versions`. Methods: `from_yaml(path)`, `with_defaults()` |
| `Crypto` | Static crypto: `init()`, `generate_kx_keypair()`, `generate_sign_keypair()`, `keypair_from_seed(seed)` (strictly 32 bytes, otherwise `ValueError`), `derive_public_key(privkey)`, `sign()`, `verify()`, `encrypt()`, `decrypt()` — `decrypt()` returns a `DecryptedResult` |
| `DecryptedResult` | Result of `Crypto.decrypt()`: fields `payload` (`Payload`) and `counter` |
| `RateLimiter(config)` | Token-bucket / sliding-window rate limiter built from a `RateLimitConfig`. Methods: `check_connection_rate(ip)`, `record_connection(ip)`, `check_handshake_rate(ip)`, `record_handshake(ip)`, `check_message_rate(conn_id)`, `record_message(conn_id)`, `check_active_connections(ip)`, `register_connection(ip)`, `unregister_connection(conn_id, ip)`, `active_total()`, `cleanup()` |
| `RateLimitConfig` | Configuration for `RateLimiter`: `enabled`, `messages_per_second`, `burst_size`, `handshake_attempts_per_minute`, `connections_per_minute`; static `defaults()` |
| `SecureBuffer` | Secure heap memory (sodium): `SecureBuffer(size=0)`, `to_bytes()`, `from_bytes(data)`, `clear()` (`sodium_memzero`), `resize(new_size)`, `size()`, `empty()`; supports `bytes()` and `len()` |
| `TimeoutError` / `LogicError` / `InvalidArgument` | Typed exceptions: subclass of builtin `TimeoutError` / `RuntimeError` / `ValueError` respectively |
| `KeyPair` / `PublicKey` / `PrivateKey` | Key types with `.data` field |
| `ConnectionHdl` | Opaque connection handle for targeting specific clients |
| `V1_0`, `V1_1` | Protocol version constants |
| `SUPPORTED_VERSIONS` | Default supported protocol versions constant |

## Examples

| Example | Description |
|---|---|---|
| [python_websocket_example.py](examples/python_websocket_example.py) | Minimal send/response with auto-unpacking |
| [request_response_example/](examples/request_response_example/) | Request-response pattern (async server + client) |
| [streaming_example.py](examples/streaming_example.py) | Bidirectional streaming echo |
| [client_identity_example.cpp](https://github.com/ObscuraEcosystem/ObscuraProto/blob/main/examples/client_identity_example.cpp) | Anonymous registration + authenticated session (C++) |

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

### CI — `audit_p0`

The `audit_p0` scenario suite (`tests/audit_p0/run_audit.py`) runs in the main CI workflow (`autotests.yml`) as the `audit` job — ubuntu-latest, in parallel with `build-and-test`, `timeout-minutes: 25`. Each scenario is launched as a subprocess with an external timeout and classified:

| Status | Meaning |
|---|---|
| `PASS` | scenario exited cleanly and matched the expected outcome |
| `FAIL` | scenario completed, but the observed result diverged from expectations |
| `HANG` | scenario did not finish before the external timeout — faulthandler thread dump detected via the `"(most recent call first)"` marker (CPython 3.13+) |
| `UNKNOWN` | no usable exit status was reported — always treated as red |

The observed status of every scenario is frozen in the `EXPECTED` table inside `run_audit.py` (baseline v1.1.1, 14 entries). The runner exits `1` on any divergence (including any `UNKNOWN`) and `0` when everything matches — this is what the CI `audit` job reports. Current `FAIL` entries in `EXPECTED` are **transitional placeholders** marked with `TODO`: those scenarios are not finished yet, so a red `audit` job does not necessarily mean a regression. See [docs/development_guide.md](docs/development_guide.md) for running the audit locally and updating `EXPECTED`.

See [CONTRIBUTING.md](CONTRIBUTING.md) for full guidelines.

## Known Issues

### C++ Timing Window — `Server.stop()` Race Condition

Calling `Server.stop()` before the accept loop has fully initialized can hang indefinitely (~20ms race condition between thread startup and stop signal).

**Mitigation:** Keep the server alive briefly after handshake completes before exiting the context manager, or add a small delay before stopping:

```python
async with Server(port=9001) as server:
    # ... setup handlers ...
    await asyncio.sleep(0.1)  # allow accept loop to initialize
    # ... run server ...
# context manager exit calls stop() safely
```

### Flaky Integration Test — `test_full_cycle_v1_1`

The test `tests/integration/test_full_cycle.py::test_full_cycle_v1_1` is timing-sensitive and may fail intermittently under heavy load. It passes reliably when run in isolation.

```bash
# Run in isolation to verify
python -m pytest tests/integration/test_full_cycle.py::test_full_cycle_v1_1 -v
```

## License

MIT © 2025 Kretov Artem. See [LICENSE](LICENSE).
