"""X4 path 2: server.sync_request from inside server on_open callback.

Criterion: LogicError from the guard (__init__.py ~1271) OR clean operation.
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

port = 31102
cfg = op.Config.with_defaults()
cfg.rate_limit.enabled = False
cfg.connection_limits.enabled = False

server = op.Server(config=cfg)
server.attach_event_loop()


@server.on_client_identity
def accept_identity(hdl, pk):
    return True


out = {}


@server.on_open
def on_open(hdl):
    try:
        server.sync_request(hdl, op.PayloadBuilder(0x4001).add_param("x").build())
        out["res"] = "NO-GUARD (sync_request returned)"
    except op.LogicError as e:
        out["res"] = f"GUARD LogicError: {e}"
    except Exception as e:  # noqa: BLE001
        out["res"] = f"OTHER {type(e).__name__}: {e}"
    print(f"RESULT: PASS {out['res']}" if out["res"].startswith("GUARD") else f"RESULT: FAIL {out['res']}")


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
    print("RESULT: FAIL client not ready")
    sys.stdout.flush()
os._exit(1)
time.sleep(0.3)
sys.stdout.flush()
os._exit(0 if "GUARD" in out.get("res", "") else 1)
