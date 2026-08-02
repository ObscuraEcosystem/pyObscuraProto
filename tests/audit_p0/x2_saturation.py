"""X2 (P0-b): request-executor saturation.

Server: async on_request = await asyncio.sleep(1).
Client: 8 parallel async_request via asyncio.gather.
Expected if 4 client workers parallelize: ~2-3s (2 waves of 4).
If server serializes (single io-thread): ~8s.
"""

import asyncio
import threading
import time

from common import make_cfg, next_port, op, result

OP_REQ = 0x2001
OP_RESP = 0x2002


async def main():
    port = next_port()
    cfg = make_cfg()
    server = op.Server(config=cfg)
    server.attach_event_loop()

    @server.on_client_identity
    def accept_identity(hdl, pk):
        return True

    @server.on_request(OP_REQ)
    async def handle_req(hdl: op.ConnectionHdl, val: str) -> op.Payload:
        await asyncio.sleep(1.0)
        return op.PayloadBuilder(OP_RESP).add_param(val).build()

    server.start(port)

    client = op.Client(server.public_key, config=cfg)
    client.attach_event_loop()
    client.set_client_identity(op.Crypto.generate_sign_keypair())
    ready = threading.Event()

    @client.on_ready
    def on_ready():
        ready.set()

    client.connect(f"ws://localhost:{port}")
    if not ready.wait(timeout=8):
        result("FAIL", "client not ready in 8s")
        return
    await asyncio.sleep(0.3)

    req = op.PayloadBuilder(OP_REQ).add_param("x").build()
    N = 8
    t0 = time.monotonic()
    responses = await asyncio.gather(*(client.async_request(req) for _ in range(N)))
    total = time.monotonic() - t0
    ok = len(responses) == N and all(r is not None for r in responses)
    print(f"X2 N=8 x async_request(sleep 1s): total={total:.2f}s responses={len(responses)}")
    # ~2-3s => client executor parallelism (4 workers, 2 waves); ~8s => serialization
    result("PASS" if ok else "FAIL", f"total={total:.2f}s N=8 client_workers=4")


asyncio.run(main())
