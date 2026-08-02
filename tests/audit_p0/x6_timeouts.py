"""X6: timeout semantics.

Silent server: no request handler registered for the test opcode, no default
handler -> requests are never answered.

1. client sync_request(timeout_ms=2000)          -> C++ watchdog, ~2s
2. wrapper async_request(timeout=1.0)            -> py-side 1s wins vs cpp 30000ms
3. raw async_request(timeout_ms=2000) + py 5s    -> cpp 2s wins (effective=min)
4. server sync_request without timeout_ms        -> request_ms from Config (30000
   default; NOT settable from Python) -> still pending at 6s
5. send_response not bound on either class      -> expected GAP
"""

import asyncio
import os
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src")))

import faulthandler  # noqa: E402

faulthandler.enable()
faulthandler.dump_traceback_later(10, exit=True)

import ObscuraProto as op  # noqa: E402
from ObscuraProto import _bindings  # noqa: E402

op.Crypto.init()

port = 31301
cfg = op.Config.with_defaults()
cfg.rate_limit.enabled = False
cfg.connection_limits.enabled = False

server = op.Server(config=cfg)
server.attach_event_loop()

# NOTE: no request handler and no default handler -> silent server.
connected_hdl = {}
hdl_evt = threading.Event()


@server.on_open
def on_open(hdl):
    connected_hdl["hdl"] = hdl
    hdl_evt.set()


server.start(port)

client = op.Client(server.public_key, config=cfg)
client.attach_event_loop()
ready = threading.Event()


@client.on_ready
def on_ready():
    ready.set()


client.connect(f"ws://localhost:{port}")
if not ready.wait(timeout=8):
    print("RESULT: FAIL client not ready")
    sys.stdout.flush()
os._exit(1)
time.sleep(0.3)

req = op.PayloadBuilder(0x6001).add_param("x").build()


async def main():
    # 6.1a raw client sync_request with explicit timeout_ms=2000
    t0 = time.monotonic()
    try:
        client._client.sync_request(req, 2000)
        print("X6-1a NO TIMEOUT FIRED")
    except Exception as e:  # noqa: BLE001
        dt = time.monotonic() - t0
        print(f"X6-1a client sync_request(timeout_ms=2000) fired at {dt:.2f}s: {type(e).__name__}")

    # 6.1b wrapper async_request with py-side timeout=1.0 (cpp default 30000ms)
    t0 = time.monotonic()
    try:
        await client.async_request(req, timeout=1.0)
        print("X6-1b NO TIMEOUT FIRED")
    except Exception as e:  # noqa: BLE001
        dt = time.monotonic() - t0
        print(f"X6-1b wrapper async_request(py timeout=1.0) fired at {dt:.2f}s: {type(e).__name__}")

    # 6.1c raw async_request(timeout_ms=2000) awaited with py timeout=5.0
    cpp_fut = client._client.async_request(req, 2000)
    t0 = time.monotonic()
    try:
        await op._await_cpp_future(cpp_fut, timeout=5.0)
        print("X6-1c NO TIMEOUT FIRED")
    except Exception as e:  # noqa: BLE001
        dt = time.monotonic() - t0
        print(f"X6-1c raw async_request(cpp 2000ms) vs py 5.0s fired at {dt:.2f}s: {type(e).__name__}")

    # 6.2 server sync_request without timeout_ms -> request_ms from Config
    # (30000 default, unsettable from Python). Client never responds.
    res = {}

    def srv_worker():
        try:
            hdl_evt.wait(timeout=5)
            t0 = time.monotonic()
            server.sync_request(connected_hdl["hdl"], op.PayloadBuilder(0x6002).add_param("s").build())
            res["dt"] = time.monotonic() - t0
        except Exception as e:  # noqa: BLE001
            res["err"] = f"{type(e).__name__}: {e}"

    threading.Thread(target=srv_worker, daemon=True).start()
    await asyncio.sleep(6)
    if "dt" in res:
        print(f"X6-2 server sync_request (no timeout_ms) returned in {res['dt']:.2f}s")
    elif "err" in res:
        print(f"X6-2 server sync_request (no timeout_ms) error: {res['err']}")
    else:
        print("X6-2 server sync_request (no timeout_ms) STILL PENDING at 6s -> request_ms=30000 from Config")

    # 6.3 send_response GAP
    print(
        f"X6-3 send_response on WsClient={hasattr(_bindings.WsClient, 'send_response')} "
        f"WsServer={hasattr(_bindings.WsServer, 'send_response')}"
    )
    print("RESULT: PASS timeout-semantics-observed")
    sys.stdout.flush()


os._exit(0)


asyncio.run(main())
