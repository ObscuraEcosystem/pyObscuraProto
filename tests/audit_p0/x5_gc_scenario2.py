"""X5 scenario 2: Server GC while a client request is in flight.

A server (with a request handler registered) is connected to a client that
fires an async request; while that request is still in flight, a background
thread deletes the last Python reference to the Server and runs gc.collect().
The C++ dtor -> stop() -> close sessions + join of the server io-thread on the
calling thread. Asserts the dtor completes bounded (no deadlock) with the
server actually freed, even with pending async I/O outstanding.

NOTE: in this build the C++ server never dispatches message handlers while the
client is driven from asyncio.run, so the registered handler is never invoked;
the scenario therefore exercises the teardown path (session close + join) under
an in-flight request, which is what is observable here.
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
# The in-flight request waits up to 8s; keep the backstop well clear of that.
faulthandler.dump_traceback_later(15, exit=True)

import ObscuraProto as op  # noqa: E402

op.Crypto.init()

port = 31202
cfg = op.Config.with_defaults()
cfg.rate_limit.enabled = False
cfg.connection_limits.enabled = False


async def main():
    srv = op.Server(config=cfg)
    srv.attach_event_loop()

    @srv.on_request(0x4201)
    def handle_req(hdl: op.ConnectionHdl, val: str) -> op.Payload:
        # Intended mid-callback GC target; see module note.
        return op.PayloadBuilder(0x4202).add_param(val).build()

    srv.start(port)

    client = op.Client(srv.public_key, config=cfg)
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

    # Keep the only Python reference to the Server inside the holder so the
    # delete_server thread can drop it with gc.collect().
    holder = {"server": srv}
    ref = weakref.ref(srv)
    del srv
    gc_result = {}

    def delete_server():
        time.sleep(0.15)  # let the request go in flight first
        t0 = time.monotonic()
        holder["server"] = None
        gc.collect()
        gc_result["dt"] = time.monotonic() - t0
        gc_result["cleared"] = ref() is None

    th = threading.Thread(target=delete_server, daemon=True)
    th.start()

    # Client request is in flight while the Server is GC'd mid-connection.
    try:
        resp = await client.async_request(op.PayloadBuilder(0x4201).add_param("x").build(), timeout=8.0)
        print(f"X5-S2 client request unexpectedly returned 0x{resp.op_code:04x}")
    except Exception as e:  # noqa: BLE001
        print(f"X5-S2 client request ended: {type(e).__name__}: {e}")

    th.join(timeout=20)
    dt = gc_result.get("dt")
    cleared = gc_result.get("cleared")
    print(f"X5-S2 Server GC mid-request: dtor+join={dt:.3f}s ref_cleared={cleared}")
    if dt is None:
        print("RESULT: FAIL gc did not complete")
        sys.stdout.flush()
        os._exit(1)
    ok = cleared and dt < 12.0
    print("RESULT: PASS" if ok else "RESULT: FAIL")
    sys.stdout.flush()
    os._exit(0 if ok else 1)


asyncio.run(main())
