"""X4 path 7: RAW _bindings.WsServer request handler (no dispatcher) calling
raw sync_request from the io-thread while the session IS ready.

No Python guard is in effect (_in_callback_thread not set). With timeouts
disabled, resolve_request_timeout(0) -> 0 -> sync_request does future.get()
forever. The server io-thread is blocked in get(); the client's response cannot
be dispatched on that same thread -> true deadlock (10s faulthandler dump).
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

op.Crypto.init()

port = 31107
cfg = op.Config.with_defaults()
cfg.rate_limit.enabled = False
cfg.connection_limits.enabled = False
cfg.timeouts.enabled = False  # -> effective timeout 0 -> infinite wait

server = op.Server(config=cfg)
raw = server._server
out = {}

raw.set_client_identity_handler(lambda hdl, pk: True)


def raw_req_handler(hdl, reader):
    t0 = time.monotonic()
    try:
        raw.sync_request(hdl, op.PayloadBuilder(0x4104).add_param("y").build())
        out["res"] = f"RETURNED after {time.monotonic()-t0:.2f}s"
    except Exception as e:  # noqa: BLE001
        out["res"] = f"ERR {type(e).__name__} after {time.monotonic()-t0:.2f}s: {e}"
    return op.PayloadBuilder(0x4102).add_param("done").build()


raw.register_request_handler(0x4101, raw_req_handler)
server.start(port)

client = op.Client(server.public_key, config=cfg)
client.attach_event_loop()
client.set_client_identity(op.Crypto.generate_sign_keypair())
ready = threading.Event()


@client.on_ready
def on_ready():
    ready.set()


@client.on_request(0x4104)
def respond(reader: op.PayloadReader) -> op.Payload:
    print("X4-P7 client responded to server request (response cannot be dispatched: io-thread blocked)")
    return op.PayloadBuilder(0x4105).add_param("pong").build()


async def main():
    client.connect(f"ws://localhost:{port}")
    if not ready.wait(timeout=8):
        print("RESULT: FAIL client not ready")
        sys.stdout.flush()
        os._exit(1)
    await asyncio.sleep(0.2)

    t0 = time.monotonic()
    try:
        await client.async_request(op.PayloadBuilder(0x4101).add_param("x").build(), timeout=8.0)
        print("RESULT: FAIL client request unexpectedly succeeded")
        sys.stdout.flush()
        os._exit(1)
    except op.TimeoutError:
        dt = time.monotonic() - t0
        print(f"X4-P7 client request timed out after {dt:.2f}s; server raw handler still {out.get('res','PENDING')}")
        print("RESULT: HANG raw-path io-thread deadlock confirmed (no guard; infinite sync_request get)")
        sys.stdout.flush()
        os._exit(1)


asyncio.run(main())
