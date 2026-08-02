"""X5 scenario 2: Server GC while an async request callback is actively running.

Server: async on_request = await asyncio.sleep(1). Client fires a request; while
the callback sleeps, a background thread deletes the last Python reference to
the Server and runs gc.collect(). C++ dtor -> stop() -> join(server_thread_) on
the calling thread. The io-thread is inside the dispatcher polling loop (GIL
released), so the running coroutine still finishes on the event loop and the
join completes. Expect bounded wait (~1-2s), not a deadlock.
"""

import asyncio
import gc
import os
import sys
import threading
import time
import weakref

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src")))

import faulthandler  # noqa: E402

faulthandler.enable()
faulthandler.dump_traceback_later(10, exit=True)

import ObscuraProto as op  # noqa: E402

op.Crypto.init()

port = 31202
cfg = op.Config.with_defaults()
cfg.rate_limit.enabled = False
cfg.connection_limits.enabled = False


async def main():
    server = op.Server(config=cfg)
    server.attach_event_loop()

    @server.on_request(0x4201)
    async def handle_req(hdl: op.ConnectionHdl, val: str) -> op.Payload:
        await asyncio.sleep(1.0)
        return op.PayloadBuilder(0x4202).add_param(val).build()

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
    await asyncio.sleep(0.3)

    holder = {"server": server}
    ref = weakref.ref(server)
    gc_result = {}

    def delete_server():
        time.sleep(0.25)  # let the async handler enter its sleep(1)
        t0 = time.monotonic()
        holder["server"] = None
        gc.collect()
        gc_result["dt"] = time.monotonic() - t0
        gc_result["cleared"] = ref() is None

    th = threading.Thread(target=delete_server, daemon=True)
    th.start()

    # Client request runs while the server is being GC'd mid-callback.
    try:
        resp = await client.async_request(op.PayloadBuilder(0x4201).add_param("x").build(), timeout=12.0)
        print(f"X5-S2 client got response 0x{resp.op_code:04x}")
    except Exception as e:  # noqa: BLE001
        print(f"X5-S2 client request ended: {type(e).__name__}: {e}")

    th.join(timeout=15)
    dt = gc_result.get("dt")
    cleared = gc_result.get("cleared")
    print(f"X5-S2 Server GC mid-callback: dtor+join={dt:.2f}s ref_cleared={cleared}")
    if dt is None:
        print("RESULT: FAIL gc did not complete")
        sys.stdout.flush()
os._exit(1)
    ok = dt < 12.0
    print("RESULT: PASS" if ok else "RESULT: FAIL")
    sys.stdout.flush()
os._exit(0 if ok else 1)


asyncio.run(main())
