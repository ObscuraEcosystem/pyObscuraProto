"""X6: timeout semantics (asserted).

Silent server: no request handler registered for the test opcode, no default
handler -> requests are never answered.

5 asserted checks:
1. client sync_request(timeout_ms=2000)          -> C++ watchdog, ~2s
2. wrapper async_request(timeout=1.0)            -> py-side 1s wins vs cpp 30000ms
3. raw async_request(timeout_ms=2000) + py 5s    -> cpp 2s wins (effective=min)
4. server sync_request without timeout_ms        -> request_ms from Config (30000
   default; NOT settable from Python) -> must still be pending at 6s
5. send_response not bound on either class       -> expected GAP

Each of checks 1-3 asserts that a TimeoutError fired on the expected distance
(+-1s tolerance, and not before 0.3s so an immediate bogus throw is caught);
check 4 asserts the request did NOT fire within the 6s window; check 5 asserts
the API gap. RESULT: FAIL + exit 1 on any divergence; RESULT: PASS only when
all 5 checks hold. RESULT is always the last stdout line (flushed).
"""

import asyncio
import os
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src")))

import faulthandler  # noqa: E402

faulthandler.enable()
# X6 needs ~11-12s of wall time (2+1+2s sync probes + 6s pending check), so the
# shared 10s backstop would kill it before RESULT: PASS. Use a 25s backstop.
faulthandler.dump_traceback_later(25, exit=True)

import ObscuraProto as op  # noqa: E402
from ObscuraProto import _bindings  # noqa: E402

op.Crypto.init()

port = 31301
cfg = op.Config.with_defaults()
cfg.rate_limit.enabled = False
cfg.connection_limits.enabled = False

server = op.Server(config=cfg)
server.attach_event_loop()

# NOTE: no request handler and no default handler -> silent server.
connected_hdl = {}
hdl_evt = threading.Event()


@server.on_open
def on_open(hdl):
    connected_hdl["hdl"] = hdl
    hdl_evt.set()


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

req = op.PayloadBuilder(0x6001).add_param("x").build()

# Tolerance around the expected timeout distance (s).
TOL = 1.0
# Floor: a genuine timeout must wait at least this long; anything faster is a
# bogus immediate throw (e.g. wrong-path LogicError), not the observed timeout.
MIN_DT = 0.3

failures = []


def check(name, ok, detail):
    print(f"X6-{name} {'PASS' if ok else 'FAIL'}: {detail}")
    if not ok:
        failures.append(detail)


def expect_timeout(name, expected, dt, exc):
    """Assert a TimeoutError fired on the expected distance (+-TOL)."""
    exc_name = type(exc).__name__ if exc is not None else "NO-EXCEPTION"
    ok = isinstance(exc, op.TimeoutError) and expected - TOL <= dt <= expected + TOL and dt >= MIN_DT
    if ok:
        check(name, True, f"TimeoutError at {dt:.2f}s (expected {expected:.1f}s +-{TOL:.0f}s)")
    else:
        check(
            name,
            False,
            f"{exc_name} at {dt:.2f}s, expected TimeoutError at {expected:.1f}s +-{TOL:.0f}s",
        )


async def main():
    # 6.1a raw client sync_request with explicit timeout_ms=2000
    t0 = time.monotonic()
    exc = None
    try:
        client._client.sync_request(req, 2000)
    except Exception as e:  # noqa: BLE001
        exc = e
    expect_timeout("1a", 2.0, time.monotonic() - t0, exc)

    # 6.1b wrapper async_request with py-side timeout=1.0 (cpp default 30000ms)
    t0 = time.monotonic()
    exc = None
    try:
        await client.async_request(req, timeout=1.0)
    except Exception as e:  # noqa: BLE001
        exc = e
    expect_timeout("1b", 1.0, time.monotonic() - t0, exc)

    # 6.1c raw async_request(timeout_ms=2000) awaited with py timeout=5.0
    cpp_fut = client._client.async_request(req, 2000)
    t0 = time.monotonic()
    exc = None
    try:
        await op._await_cpp_future(cpp_fut, timeout=5.0)
    except Exception as e:  # noqa: BLE001
        exc = e
    expect_timeout("1c", 2.0, time.monotonic() - t0, exc)

    # 6.2 server sync_request without timeout_ms -> request_ms from Config
    # (30000 default, unsettable from Python). Client never responds -> must
    # NOT fire within the 6s observation window.
    res = {}

    def srv_worker():
        try:
            hdl_evt.wait(timeout=5)
            t0 = time.monotonic()
            server.sync_request(connected_hdl["hdl"], op.PayloadBuilder(0x6002).add_param("s").build())
            res["dt"] = time.monotonic() - t0
        except Exception as e:  # noqa: BLE001
            res["err"] = f"{type(e).__name__}: {e}"

    threading.Thread(target=srv_worker, daemon=True).start()
    await asyncio.sleep(6)
    if "dt" in res:
        check(
            "2",
            False,
            f"server sync_request (no timeout_ms) returned at {res['dt']:.2f}s, expected still-pending at 6s (request_ms=30000)",
        )
    elif "err" in res:
        check(
            "2",
            False,
            f"server sync_request (no timeout_ms) errored: {res['err']}, expected still-pending at 6s (request_ms=30000)",
        )
    else:
        check("2", True, "still pending at 6s (request_ms=30000 from Config, did not fire in window)")

    # 6.3 send_response GAP
    c_has = hasattr(_bindings.WsClient, "send_response")
    s_has = hasattr(_bindings.WsServer, "send_response")
    check("3", not c_has and not s_has, f"send_response gap WsClient={c_has} WsServer={s_has}")

    if failures:
        print(f"RESULT: FAIL {'; '.join(failures)}")
        sys.stdout.flush()
        os._exit(1)
    print("RESULT: PASS timeout-semantics-observed")
    sys.stdout.flush()


asyncio.run(main())
