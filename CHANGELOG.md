### 1.1.1

Major refactoring of the Python bindings

- **Typed exceptions**: `ObscuraProto.TimeoutError` (subclass of builtin `TimeoutError`), `LogicError` (subclass of `RuntimeError`), `InvalidArgument` (subclass of `ValueError`) registered via `py::register_exception`. Previously every C++ exception surfaced as a generic `RuntimeError`.
- **Request timeouts**: the C++ bindings add `sync_request(payload, timeout_ms)` / `async_request(payload, timeout_ms)` overloads (`uint32_t` milliseconds, `0` = unlimited). The Python layer exposes a `timeout` parameter (seconds, default `30.0`) on `Client.async_request`, `Server.async_request` and `async_request_to_identity`; a missing response raises `ObscuraProto.TimeoutError`.
- **Deadlock protection**: a thread-local callback/IO-thread flag rejects blocking calls from inside handlers — `sync_request`, `sync_request_to_identity`, `Server.stop` and `Client.disconnect` raise `LogicError` instead of self-deadlocking. Calling `sync_request` from the event-loop thread emits a warning.
- **GIL-free callback result polling**: `wrap_with_result` polls with `time.sleep` (GIL released) and a parameterizable timeout (default 5 s, previously 30 s while holding the GIL).
- **Awaitable C++ futures**: `CppPayloadFuture` is awaited through an `asyncio.Future` fulfilled via `loop.call_soon_threadsafe` (no more `asyncio.sleep` polling on the event loop); Python-side timeout default 30 s; single-use guard — awaiting the same future twice raises `LogicError`.
- **Unified executor model**: stream operations (`write`/`end`/`cancel`/`start_stream`) run through a single-worker `ThreadPoolExecutor` (FIFO order guarantee), request futures through a separate 4-worker pool; lazy initialization, `atexit` shutdown and a fallback path when the pools are shut down.
- **New bindings**: `RateLimiter` (constructor takes a `RateLimitConfig`; 11 methods), `SecureBuffer` (`to_bytes`/`from_bytes`/`clear` — memory zeroed via `sodium_memzero`), `Crypto.DecryptedResult` (fields `payload`, `counter`) — `Crypto.decrypt` is now usable from Python.
- **C++ core (v1.1.0)**: server message handling fully wrapped in try/catch (an exception from a user handler no longer kills the ws thread); `secure_buffer.hpp` zeroes memory after allocation (`sodium_memzero`); self-join guards in `ws_client.cpp` / `ws_server.cpp`.
- **Tests**: 267 passing (was 218), 97% coverage on `__init__.py`.
