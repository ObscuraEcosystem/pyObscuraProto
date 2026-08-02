"""X3 (P0-d): head-of-line on the server io-thread.

Server:
  - OP_SLOW handler: async, await asyncio.sleep(1)
  - OP_FAST handler: sync, returns immediately
Client A sends OP_SLOW; while the server io-thread is busy with it, client B
sends OP_FAST. If the single server io-thread serializes callbacks, B waits for
A's handler to finish (~1s). If parallel, B is served in <0.3s.
"""

import asyncio
import threading
import time

from common import make_cfg, next_port, op, result

OP_SLOW = 0x3001
OP_FAST = 0x3002


async def main():
    port = next_port()
    cfg = make_cfg()
    server = op.Server(config=cfg)
    server.attach_event_loop()

    @server.on_client_identity
    def accept_identity(hdl, pk):
        return True

    @server.on_request(OP_SLOW)
    async def slow(hdl: op.ConnectionHdl, val: str) -> op.Payload:
        await asyncio.sleep(1.0)
        return op.PayloadBuilder(0x3011).add_param(val).build()

    @server.on_request(OP_FAST)
    def fast(hdl: op.ConnectionHdl, val: str) -> op.Payload:
        return op.PayloadBuilder(0x3012).add_param(val).build()

    server.start(port)

    req_slow = op.PayloadBuilder(OP_SLOW).add_param("s").build()
    req_fast = op.PayloadBuilder(OP_FAST).add_param("f").build()

    def make_client(name):
        c = op.Client(server.public_key, config=cfg)
        c.attach_event_loop()
        c.set_client_identity(op.Crypto.generate_sign_keypair())
        ev = threading.Event()

        @c.on_ready
        def _r():
            ev.set()

        c.connect(f"ws://localhost:{port}")
        if not ev.wait(timeout=8):
            raise RuntimeError(f"{name} not ready")
        return c

    B = make_client("B")
    await asyncio.sleep(0.2)

    # Baseline: B OP_FAST on an idle server.
    t0 = time.monotonic()
    B.sync_request(req_fast)
    base = time.monotonic() - t0
    print(f"X3 baseline B(OP_FAST, idle) latency={base * 1000:.1f}ms")

    A = make_client("A")
    await asyncio.sleep(0.2)

    a_task = asyncio.create_task(A.async_request(req_slow))
    await asyncio.sleep(0.15)  # A's handler is now occupying the server io-thread

    lat = {}

    def b_worker():
        t0 = time.monotonic()
        try:
            B.sync_request(req_fast)
            lat["v"] = time.monotonic() - t0
        except Exception as e:  # noqa: BLE001
            lat["err"] = f"{type(e).__name__}: {e}"

    th = threading.Thread(target=b_worker, daemon=True)
    th.start()
    await a_task
    th.join(timeout=5)

    if "err" in lat:
        result("FAIL", f"B request error: {lat['err']}")
        return
    bl = lat["v"]
    print(f"X3 B(OP_FAST) under A-load latency={bl * 1000:.1f}ms (baseline={base * 1000:.1f}ms)")
    if base < 0.3 and bl >= 0.5:
        result("PASS", f"HOL-CONFIRMED B_latency={bl:.2f}s baseline={base:.2f}s")
    else:
        result("PASS", f"HOL-NOT-CONFIRMED B_latency={bl:.2f}s baseline={base:.2f}s")


asyncio.run(main())
