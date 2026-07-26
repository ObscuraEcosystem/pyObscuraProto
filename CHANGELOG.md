### 1.0.2
- Added `on_open` / `on_close` decorators to `Server` — connection lifecycle callbacks that fire on WebSocket open and close events.
- Underlying C++ library updated to v1.0.2 (adds `set_on_open_callback` / `set_on_close_callback` to `WsServerWrapper`, enables `SO_REUSEADDR`).
