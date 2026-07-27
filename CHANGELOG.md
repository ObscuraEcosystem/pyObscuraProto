### 1.1.0
- Underlying C++ library updated to v1.1.0 (OpCode-routed streams, configurable protocol versions).
- Added `V1_1` constant and updated `SUPPORTED_VERSIONS` to include V1_1 as preferred.
- Added `Config.supported_versions` configuration option to specify which protocol versions to support.
- Added `Stream.op_code` property to retrieve the stream's op code.
- Added optional `stream_op_code` parameter to `start_stream()` methods on both `Server` and `Client`.
- Added `@server.on_stream(op_code)` decorator for handling incoming authenticated streams with specific op codes.
- Added `@server.on_anon_stream(op_code)` decorator for handling incoming anonymous streams with specific op codes.
- Added `@client.on_stream(op_code)` decorator for handling incoming streams from the server with specific op codes.
