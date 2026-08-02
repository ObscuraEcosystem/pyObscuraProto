"""X4 path 6: RAW _bindings.WsServer identity handler WITHOUT the wrapper
dispatcher (as in test_auth_anonymous.py:105) calling raw sync_request.

The Python flag _in_callback_thread is NOT set here. The identity handler runs
during handshake, BEFORE the session is registered, so the C++ async_request
should throw LogicError("Session not ready") immediately -- no block, no guard.
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

port = 31106
cfg = op.Config.with_defaults()
cfg.rate_limit.enabled = False
cfg.connection_limits.enabled = False

server = op.Server(config=cfg)
raw = server._server
out = {}
identity_called = threading.Event()


def raw_identity_handler(hdl, pk):
    identity_called.set()
    t0 = time.monotonic()
    try:
        raw.sync_request(hdl, op.PayloadBuilder(0x4001).add_param("x").build())
        out["res"] = f"NO-ERROR after {time.monotonic() - t0:.2f}s"
        return True
    except op.LogicError as e:
        out["res"] = f"CXX-LogicError after {time.monotonic() - t0:.2f}s: {e}"
        return False
    except Exception as e:  # noqa: BLE001
        out["res"] = f"OTHER {type(e).__name__} after {time.monotonic() - t0:.2f}s: {e}"
        return False


raw.set_client_identity_handler(raw_identity_handler)
server.start(port)

client = op.Client(server.public_key, config=cfg)
client.attach_event_loop()
client.set_client_identity(_bindings.Crypto.generate_sign_keypair())
ready = threading.Event()


@client.on_ready
def on_ready():
    ready.set()


@client.on_disconnect
def on_disconnect():
    ready.set()


client.connect(f"ws://localhost:{port}")
if not ready.wait(timeout=8):
    print("RESULT: FAIL client neither ready nor disconnected")
    sys.stdout.flush()
    os._exit(1)
time.sleep(0.3)
identity_called.wait(timeout=5)
res_str = out.get("res", "NOT-CALLED")
print(f"X4-P6 raw-identity sync_request: {res_str}")
# PASS requires BOTH: the handler was invoked AND the raw sync_request raised
# the CXX LogicError marker "Session not ready" (NO-ERROR would otherwise also
# satisfy the old identity_called-only verdict).
if identity_called.is_set() and "Session not ready" in res_str:
    print("RESULT: PASS")
    sys.stdout.flush()
    os._exit(0)
print(
    f"RESULT: FAIL identity={identity_called.is_set()} res={res_str!r} "
    "(expected CXX-LogicError 'Session not ready')"
)
sys.stdout.flush()
os._exit(1)
