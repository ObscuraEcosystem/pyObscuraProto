"""X1: environment check - import works, key wrapper/C++ surface facts."""

import glob
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src")))

import faulthandler  # noqa: E402

faulthandler.enable()
faulthandler.dump_traceback_later(10, exit=True)

import ObscuraProto as op  # noqa: E402
from ObscuraProto import _bindings  # noqa: E402

op.Crypto.init()

pkg_dir = os.path.dirname(op.__file__)
so_files = glob.glob(os.path.join(pkg_dir, "_obscuraproto*.so"))
so = so_files[0] if so_files else "MISSING"

print(f"X1 wrapper file: {op.__file__}")
print(f"X1 bindings so: {_bindings.__file__}")
print(f"X1 so on disk: {so} mtime={os.path.getmtime(so) if so_files else 'n/a'}")
print(f"X1 request executor workers: {op._REQUEST_EXECUTOR_MAX_WORKERS}")
print(f"X1 server sync_request guard: {op.Server.sync_request.__doc__.splitlines()[0].strip()}")
print(f"X1 client sync_request guard: {op.Client.sync_request.__doc__.splitlines()[0].strip()}")
print(f"X1 _in_callback_thread(): {op._in_callback_thread()}")
print(f"X1 TimeoutError bound: {op.TimeoutError.__name__}")
print(f"X1 send_response on WsClient: {hasattr(_bindings.WsClient, 'send_response')}")
print(f"X1 send_response on WsServer: {hasattr(_bindings.WsServer, 'send_response')}")
print(f"X1 request_ms settable on TimeoutConfig: {'request_ms' in dir(_bindings.TimeoutConfig)}")
print(f"X1 raw WsClient.sync_request overloads: {[o for o in dir(_bindings.WsClient) if 'sync_request' in o]}")
print(f"X1 raw WsClient.async_request overloads: {[o for o in dir(_bindings.WsClient) if 'async_request' in o]}")
print("RESULT: PASS env-import-ok")
