"""X4 path 1: client.sync_request from inside client on_ready callback.

Criterion: LogicError from the _in_callback_thread guard (__init__.py ~1758)
OR clean operation without hanging.
"""

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

port = 31101
cfg = op.Config.with_defaults()
cfg.rate_limit.enabled = False
cfg.connection_limits.enabled = False

server = op.Server(config=cfg)
server.attach_event_loop()


@server.on_client_identity
def accept_identity(hdl, pk):
    return True


@server.on_request(0x4001)
def handle_req(hdl: op.ConnectionHdl, val: str) -> op.Payload:
    return op.PayloadBuilder(0x4002).add_param("resp").build()


server.start(port)

client = op.Client(server.public_key, config=cfg)
client.attach_event_loop()
client.set_client_identity(op.Crypto.generate_sign_keypair())

out = {}
on_ready_called = threading.Event()


@client.on_ready
def on_ready():
    try:
        client.sync_request(op.PayloadBuilder(0x4001).add_param("x").build())
        out["res"] = "NO-GUARD (sync_request returned)"
    except op.LogicError as e:
        out["res"] = f"GUARD LogicError: {e}"
    except Exception as e:  # noqa: BLE001
        out["res"] = f"OTHER {type(e).__name__}: {e}"
    print(f"RESULT: PASS {out['res']}" if out["res"].startswith("GUARD") else f"RESULT: FAIL {out['res']}")
    sys.stdout.flush()
    on_ready_called.set()
    os._exit(0 if "GUARD" in out.get("res", "") else 1)


client.connect(f"ws://localhost:{port}")
if not on_ready_called.wait(timeout=8):
    print("RESULT: FAIL on_ready-not-called")
    sys.stdout.flush()
    os._exit(1)
# on_ready calls os._exit, so control normally never reaches here; loop as a
# safety net so the process stays alive if the exit path ever changes.
while True:
    time.sleep(0.1)
