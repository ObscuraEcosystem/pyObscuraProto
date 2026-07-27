### 1.2.0
- Added full-cycle integration tests ported from ObscuraProto C++ tests (32 tests across 6 files in `tests/integration/`):
  - `test_anonymous.py` (6 tests) — anonymous session op/request/stream handlers
  - `test_authenticated.py` (5 tests) — authenticated session with identity
  - `test_full_cycle.py` (2 tests) — V1.0 and V1.1 full cycle with anon + auth clients
  - `test_server_edge.py` (9 tests) — stream lifecycle, sync requests, limits, timeouts
  - `test_stream_routing.py` (6 tests) — op_code routing, fallback, server-initiated streams
  - `test_version_config.py` (4 tests) — version negotiation edge cases

### 1.1.0
- Underlying C++ library updated to v1.1.0 (OpCode-routed streams, configurable protocol versions).
- Added `V1_1` constant and updated `SUPPORTED_VERSIONS` to include V1_1 as preferred.
- Added `Config.supported_versions` configuration option to specify which protocol versions to support.
- Added `Stream.op_code` property to retrieve the stream's op code.
- Added optional `stream_op_code` parameter to `start_stream()` methods on both `Server` and `Client`.
- Added `@server.on_stream(op_code)` decorator for handling incoming authenticated streams with specific op codes.
- Added `@server.on_anon_stream(op_code)` decorator for handling incoming anonymous streams with specific op codes.
- Added `@client.on_stream(op_code)` decorator for handling incoming streams from the server with specific op codes.
