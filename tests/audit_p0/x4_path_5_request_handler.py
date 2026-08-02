"""X4 path 5: server.sync_request from inside a server request handler.

Criterion: LogicError from the guard (~1271) OR clean operation.
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

port = 31105
cfg = op.Config.with_defaults()
cfg.rate_limit.enabled = False
cfg.connection_limits.enabled = False

server = op.Server(config=cfg)
server.attach_event_loop()


@server.on_client_identity
def accept_identity(hdl, pk):
    return True


out = {}


@server.on_request(0x4101)
def handle_req(hdl: op.ConnectionHdl, val: str) -> op.Payload:
    try:
        server.sync_request(hdl, op.PayloadBuilder(0x4104).add_param("y").build())
        out["res"] = "NO-GUARD (sync_request returned)"
    except op.LogicError as e:
        out["res"] = f"GUARD LogicError: {e}"
    except Exception as e:  # noqa: BLE001
        out["res"] = f"OTHER {type(e).__name__}: {e}"
    return op.PayloadBuilder(0x4102).add_param("resp").build()


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
time.sleep(0.2)

resp = client.sync_request(op.PayloadBuilder(0x4101).add_param("x").build())
print(f"X4-P5 client got response opcode=0x{resp.op_code:04x} server_out={out.get('res', '')}")
ok = out.get("res", "").startswith("GUARD") and resp is not None
print("RESULT: PASS" if ok else "RESULT: FAIL")
sys.stdout.flush()
os._exit(0 if ok else 1)
