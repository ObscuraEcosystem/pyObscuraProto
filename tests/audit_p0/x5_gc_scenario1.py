"""X5 scenario 1: Client GC with an active connection.

Client connected to a running server; delete the last Python reference to the
Client and gc.collect(). pybind11 dealloc -> C++ dtor -> disconnect() -> join of
the client io-thread under the GIL. Expect fast completion (idle io-thread).
"""

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

port = 31201
cfg = op.Config.with_defaults()
cfg.rate_limit.enabled = False
cfg.connection_limits.enabled = False

server = op.Server(config=cfg)
server.attach_event_loop()
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
time.sleep(0.3)

ref = weakref.ref(client)
t0 = time.monotonic()
del client
gc.collect()
dt = time.monotonic() - t0
cleared = ref() is None
print(f"X5-S1 Client GC: destructor+join took {dt * 1000:.1f}ms, ref_cleared={cleared}")
ok = cleared and dt < 10.0
print("RESULT: PASS" if ok else "RESULT: FAIL")
sys.stdout.flush()
os._exit(0 if ok else 1)
