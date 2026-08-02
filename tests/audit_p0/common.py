"""Shared helpers for the pyObscuraProto audit suite (X1-X6).

Read-only wrt the C++ core and the wrapper sources: nothing here touches files
outside tests/audit_p0/.
"""

import faulthandler
import os
import sys
import threading

faulthandler.enable()
# Every scenario self-terminates with a thread dump 10s in; the external runner
# adds a 30s backstop.
faulthandler.dump_traceback_later(10, exit=True)

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
sys.path.insert(0, _SRC)

import ObscuraProto as op  # noqa: E402

op.Crypto.init()

_PORT_LOCK = threading.Lock()
_PORT_COUNTER = [31000]


def next_port():
    with _PORT_LOCK:
        _PORT_COUNTER[0] += 1
        return _PORT_COUNTER[0]


def make_cfg(timeouts_enabled=True, check_interval_ms=5000):
    """Config with rate/connection limits disabled; timeouts configurable."""
    cfg = op.Config.with_defaults()
    cfg.rate_limit.enabled = False
    cfg.connection_limits.enabled = False
    cfg.timeouts.enabled = timeouts_enabled
    cfg.timeouts.check_interval_ms = check_interval_ms
    cfg.timeouts.idle_ms = 300000
    cfg.timeouts.handshake_ms = 10000
    return cfg


def build_payload(opcode, *vals):
    b = op.PayloadBuilder(opcode)
    for v in vals:
        b.add_param(v)
    return b.build()


def result(status, detail=""):
    """Emit the runner-parseable result line."""
    print(f"RESULT: {status} {detail}")
    sys.stdout.flush()
