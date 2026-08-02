"""X4 path 4: server.sync_request from inside the client-identity handler.

Criterion: LogicError from the guard (wrap_identity, __init__.py ~505-558)
OR clean operation.
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
from ObscuraProto import _bindings  # noqa: E402

op.Crypto.init()

port = 31104
cfg = op.Config.with_defaults()
cfg.rate_limit.enabled = False
cfg.connection_limits.enabled = False

server = op.Server(config=cfg)
server.attach_event_loop()

out = {}


@server.on_client_identity
def check_identity(hdl: op.ConnectionHdl, pk: _bindings.PublicKey) -> bool:
    try:
        server.sync_request(hdl, op.PayloadBuilder(0x4001).add_param("x").build())
        out["res"] = "NO-GUARD (sync_request returned)"
    except op.LogicError as e:
        out["res"] = f"GUARD LogicError: {e}"
    except Exception as e:  # noqa: BLE001
        out["res"] = f"OTHER {type(e).__name__}: {e}"
    print(f"RESULT: PASS {out['res']}" if out["res"].startswith("GUARD") else f"RESULT: FAIL {out['res']}")
    return True


server.start(port)

client = op.Client(server.public_key, config=cfg)
client.attach_event_loop()
client.set_client_identity(_bindings.Crypto.generate_sign_keypair())
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
