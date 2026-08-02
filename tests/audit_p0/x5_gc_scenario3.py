"""X5 scenario 3: Server GC with an active connection (idle callbacks).

Server running with a connected client; delete the last Python reference to the
Server and gc.collect(). C++ dtor -> stop() -> close sessions + join of the
server io-thread. Expect fast completion (idle io-thread).
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

port = 31203
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

holder = {"server": server}
ref = weakref.ref(server)
t0 = time.monotonic()
holder["server"] = None
del server
gc.collect()
dt = time.monotonic() - t0
cleared = ref() is None
print(f"X5-S3 Server GC with active connection: dtor+join={dt * 1000:.1f}ms ref_cleared={cleared}")
ok = cleared and dt < 10.0
print("RESULT: PASS" if ok else "RESULT: FAIL")
sys.stdout.flush()
os._exit(0 if ok else 1)
