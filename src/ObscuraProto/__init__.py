"""
ObscuraProto high-level Python library.
"""

import asyncio
import atexit
import builtins
import concurrent.futures
import inspect
import logging
import threading
import time
import warnings
import weakref

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

__all__ = [
    "Server",
    "Client",
    "Stream",
    "PayloadBuilder",
    "PayloadReader",
    "Payload",
    "Config",
    "RateLimitConfig",
    "ConnectionLimitConfig",
    "MessageLimitConfig",
    "TimeoutConfig",
    "ReservedOpcodes",
    "RateLimiter",
    "SecureBuffer",
    "DecryptedResult",
    "Crypto",
    "KeyPair",
    "PublicKey",
    "PrivateKey",
    "Signature",
    "TimeoutError",
    "InvalidArgument",
    "LogicError",
    "ConnectionHdl",
    "CppStream",
    "Role",
    "V1_0",
    "V1_1",
    "SUPPORTED_VERSIONS",
    "uint",
]

# ---------------------------------------------------------------------------
# Thread-local "inside a callback/IO thread" flag.
#
# The C++ websocket layer invokes Python callbacks on its own I/O threads.
# Blocking calls made from inside such a callback (sync_request, stop,
# disconnect) would deadlock because the very thread that must service the
# request / join is the one making the call.  Every wrapper produced by
# _CallbackDispatcher sets this flag for the duration of the user callback
# (thread-local, so concurrent sockets on their own threads are unaffected).
# ---------------------------------------------------------------------------

_callback_thread_local = threading.local()


def _in_callback_thread() -> bool:
    """Return True if the current thread is executing a wrapped callback.

    Used to reject blocking calls that would self-deadlock from a C++ I/O
    thread (e.g. ``sync_request`` while inside a payload handler).
    """
    return getattr(_callback_thread_local, "in_callback", False)


def _set_callback_thread_flag(value: bool) -> None:
    """Set or clear the per-thread "inside callback" flag."""
    _callback_thread_local.in_callback = value


def _warn_if_event_loop_thread() -> None:
    """Warn if the current thread is running an asyncio event loop.

    Blocking calls (``sync_request``) made from inside an async handler would
    stall the very event loop that must deliver the response. Plain synchronous
    code has no running loop, so ``asyncio.get_running_loop()`` raises
    ``RuntimeError`` there and no warning is emitted.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    warnings.warn(
        "sync_request is blocking and must not be called from an async handler: "
        "it would stall the event loop. Use 'await async_request' instead.",
        stacklevel=3,
    )


#: Default timeout (seconds) for waiting on an async request handler from a
#: C++ I/O thread. Kept short so a wedged handler does not stall the I/O thread
#: (and the GIL) indefinitely.
_CALLBACK_RESULT_TIMEOUT = 5.0

#: Polling interval (seconds) used while waiting on a future. ``time.sleep``
#: releases the GIL, so other Python threads keep making progress in between.
_CALLBACK_POLL_INTERVAL = 0.05

#: Default timeout (seconds) for awaiting a C++ ``async_request`` response from
#: the event loop. A bounded default prevents a waiter from polling forever
#: while holding one of the request executor's workers.
_REQUEST_DEFAULT_TIMEOUT = 30.0


def _reap_future_result(future):
    """Consume a future's result/exception so its outcome is never "lost".

    Attached as a done-callback to a future that was abandoned after a timeout.
    The underlying coroutine keeps running on the event loop; when it finishes
    the callback retrieves the result/exception (``future.result()``), which
    prevents the "Future exception was never retrieved" warning and releases
    any resources held by the result. Runs only after the future is done, so
    ``future.result()`` never blocks.
    """
    try:
        future.result()
    except BaseException:
        pass


def _abandon_timed_out_future(future):
    """Cancel a timed-out future and reap its eventual outcome.

    Best-effort cleanup for futures that outlive their caller's timeout:

    - If the future supports cancellation (``asyncio.Future`` /
      ``concurrent.futures.Future``), request it. The coroutine may already be
      running on the event loop, in which case cancellation is a no-op; the
      reaper callback below still drains the result when it completes.
    - Attach a done-callback that retrieves the result/exception, so a late
      failure does not surface as an unretrieved-future warning.

    The reaper runs via ``add_done_callback`` (never a separate task), so
    repeated timeouts cannot accumulate hanging tasks.
    """
    cancel = getattr(future, "cancel", None)
    if callable(cancel):
        try:
            cancel()
        except Exception:
            pass
    add_done_callback = getattr(future, "add_done_callback", None)
    if callable(add_done_callback):
        try:
            add_done_callback(_reap_future_result)
        except Exception:
            pass


def _wait_future_with_polling(future, timeout: float):
    """Wait for a concurrent.futures.Future while periodically releasing the GIL.

    ``future.done()`` is cheap and non-blocking; the ``time.sleep`` between
    polls releases the GIL in CPython, so a wedged async handler no longer
    starves every other C++ callback thread that needs the GIL.

    On timeout the future is cancelled (if supported) and a done-callback is
    attached that reaps the eventual result/exception, so the underlying
    coroutine keeps running on the event loop without leaking an unretrieved
    outcome (see ``_abandon_timed_out_future``).

    Args:
        future: The ``concurrent.futures.Future`` returned by
            ``asyncio.run_coroutine_threadsafe``.
        timeout: Maximum seconds to wait (parameterisable, default ~5s).

    Returns:
        The future's result.

    Raises:
        TimeoutError: If the future does not complete within ``timeout``
            seconds.
    """
    deadline = time.monotonic() + timeout
    while not future.done():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _abandon_timed_out_future(future)
            raise TimeoutError(f"async callback did not complete within {timeout}s")
        time.sleep(min(_CALLBACK_POLL_INTERVAL, remaining))
    return future.result()


# ---------------------------------------------------------------------------
# Executors and awaitable C++ futures.
#
# Two dedicated module-level thread pools, created lazily and re-created if
# shut down (so re-importing the module or late callbacks never hit a closed
# pool):
#   - _EXECUTOR (single worker): ordered stream I/O (write/end/cancel/
#     start_stream). A single worker preserves FIFO submission order, which
#     stream protocol semantics require -- end() must never overtake the last
#     write on the same stream, and with max_workers>1 the ThreadPoolExecutor
#     queue gives no ordering guarantee between tasks. Stream operations are
#     short (buffer enqueue + GIL release), so the serialization cost is
#     negligible compared to the correctness win.
#   - _REQUEST_EXECUTOR (4 workers): waiting on C++ async_request futures.
#     Kept separate from the stream pool so a slow response cannot head-of-line
#     block stream operations (or vice versa).
# Both pools are shut down gracefully at interpreter exit via atexit.
# ---------------------------------------------------------------------------

#: Workers for the stream executor. 1 guarantees FIFO ordering of stream ops.
_STREAM_EXECUTOR_MAX_WORKERS = 1

#: Workers for the request executor. One thread is held per in-flight async
#: request while it waits for the response; 4 covers concurrent requests.
_REQUEST_EXECUTOR_MAX_WORKERS = 4

_executor_lock = threading.Lock()
_stream_executor: concurrent.futures.ThreadPoolExecutor | None = None
_request_executor: concurrent.futures.ThreadPoolExecutor | None = None


def _get_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Return the module-level stream executor, recreating it if shut down."""
    global _stream_executor
    with _executor_lock:
        if _stream_executor is None or _stream_executor._shutdown:  # pyright: ignore[reportPrivateUsage]
            _stream_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=_STREAM_EXECUTOR_MAX_WORKERS,
                thread_name_prefix="obscura-stream",
            )
        return _stream_executor


def _get_request_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Return the module-level request executor, recreating it if shut down."""
    global _request_executor
    with _executor_lock:
        if _request_executor is None or _request_executor._shutdown:  # pyright: ignore[reportPrivateUsage]
            _request_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=_REQUEST_EXECUTOR_MAX_WORKERS,
                thread_name_prefix="obscura-request",
            )
        return _request_executor


def _shutdown_executors() -> None:
    """Gracefully shut down both module-level executors at interpreter exit."""
    global _stream_executor, _request_executor
    with _executor_lock:
        if _stream_executor is not None and not _stream_executor._shutdown:  # pyright: ignore[reportPrivateUsage]
            _stream_executor.shutdown(wait=True)
            _stream_executor = None
        if _request_executor is not None and not _request_executor._shutdown:  # pyright: ignore[reportPrivateUsage]
            _request_executor.shutdown(wait=True)
            _request_executor = None


atexit.register(_shutdown_executors)


async def _run_in_stream_executor(fn, *args):
    """Run a blocking stream operation on the shared stream executor.

    The single worker guarantees that ordered stream operations (write -> end
    -> cancel) execute in submission order.

    Args:
        fn: The stream operation (e.g. ``CppStream.write``).
        args: Positional arguments for ``fn``.

    Returns:
        The operation result.
    """
    loop = asyncio.get_running_loop()
    try:
        cf_future = _get_executor().submit(fn, *args)
    except RuntimeError:
        # The module executor may have been shut down by the atexit handler
        # between the _get_executor() check and submit() while the interpreter
        # is exiting. Fall back to the loop's own default executor so the
        # operation still completes instead of failing with a RuntimeError.
        return await asyncio.to_thread(fn, *args)
    return await asyncio.wrap_future(cf_future, loop=loop)


#: Track consumed single-use CppPayloadFuture objects. pybind11 classes expose
#: no ``__dict__``, so the flag cannot live on the object itself; a WeakSet
#: gives the same protection with automatic cleanup (entries vanish with the
#: objects, so there is no unbounded growth).
_consumed_cpp_futures = weakref.WeakSet()
_consume_lock = threading.Lock()


def _consume_cpp_future(cpp_future):
    """Call ``get()`` on a single-use ``CppPayloadFuture`` exactly once.

    The underlying ``std::future`` is consumed by ``get()``; a second call
    raises a cryptic ``std::future_error`` from the bindings, so a clear
    :class:`LogicError` is raised instead via the consumed-future registry.

    Args:
        cpp_future: The ``CppPayloadFuture`` to consume.

    Returns:
        The response Payload.

    Raises:
        LogicError: If ``cpp_future`` was already consumed.
    """
    with _consume_lock:
        if cpp_future in _consumed_cpp_futures:
            raise LogicError("CppPayloadFuture is single-use: get() may only be called once")
        _consumed_cpp_futures.add(cpp_future)
    return cpp_future.get()


async def _await_cpp_future(
    cpp_future, loop=None, timeout: float | None = _REQUEST_DEFAULT_TIMEOUT, what: str = "async_request"
):
    """Await the response of a CppPayloadFuture without blocking the event loop.

    The C++ ``std::future`` behind ``cpp_future`` is fulfilled from the
    websocket I/O thread. A waiter function runs on the dedicated request
    executor (see ``_get_request_executor``): it waits for the response and
    publishes the outcome back onto the event loop with
    ``loop.call_soon_threadsafe``. The loop itself never busy-polls -- it only
    wakes when the result is published. ``asyncio.wait_for`` enforces the
    Python-side timeout as a fallback.

    ``CppPayloadFuture`` is SINGLE-USE (see ``_consume_cpp_future``): ``get()``
    is only invoked once the response is present (``ready()``), never on a
    stale/timeout path.

    Timeout ownership: when ``timeout`` is ``None`` or ``<= 0`` the wait is
    UNLIMITED -- no ``asyncio.wait_for`` guard is applied and the C++ layer is
    the sole owner of the timeout (it resolves the future with ``TimeoutError``
    on expiry). This mirrors the ``async_request*`` bindings where
    ``timeout_ms=0`` means unlimited/config-default.

    Args:
        cpp_future: A ``CppPayloadFuture`` from one of the ``async_request*``
            bindings.
        loop: Optional event loop; defaults to the running loop.
        timeout: Maximum seconds to wait (default :data:`_REQUEST_DEFAULT_TIMEOUT`,
            30s). ``None`` or a value ``<= 0`` disables the Python-side guard
            (unlimited wait; C++ owns the timeout). On expiry a
            :class:`TimeoutError` is raised.
        what: Operation name used in the timeout message.

    Returns:
        The response Payload.

    Raises:
        TimeoutError: If the response does not arrive within ``timeout``.
        LogicError: If ``cpp_future`` was already consumed by ``get()``.
    """
    loop = loop if loop is not None else asyncio.get_running_loop()
    if timeout is not None and timeout <= 0:
        timeout = None  # 0/negative/None -> unlimited: C++ owns the timeout.
    result_future = loop.create_future()
    deadline = None if timeout is None else time.monotonic() + timeout

    def _publish(value, exc):
        if result_future.cancelled():
            return
        if exc is not None:
            result_future.set_exception(exc)
        else:
            result_future.set_result(value)

    def _fetch():
        try:
            while not cpp_future.ready():
                if deadline is None:
                    # Unlimited wait: the C++ layer owns the timeout and will
                    # resolve the future (with TimeoutError) on expiry.
                    time.sleep(_CALLBACK_POLL_INTERVAL)
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"{what} timed out after {timeout}s")
                time.sleep(min(_CALLBACK_POLL_INTERVAL, remaining))
            value = _consume_cpp_future(cpp_future)
        except Exception as exc:
            loop.call_soon_threadsafe(_publish, None, exc)
        else:
            loop.call_soon_threadsafe(_publish, value, None)

    try:
        _get_request_executor().submit(_fetch)
    except RuntimeError:
        # The request executor may have been shut down by the atexit handler
        # between _get_request_executor() and submit() while the interpreter is
        # exiting. Fall back to a one-off thread so the wait still completes.
        threading.Thread(target=_fetch, daemon=True).start()

    if timeout is None:
        return await result_future
    try:
        return await asyncio.wait_for(result_future, timeout)
    except builtins.TimeoutError:
        raise TimeoutError(f"{what} timed out after {timeout}s") from None


class _CallbackDispatcher:
    """Forwards callback invocations to the asyncio event loop.

    When attached to an event loop, wraps callbacks so that:
    - If the callback returns a coroutine object (async handler), it schedules
      the coroutine on the event loop via asyncio.run_coroutine_threadsafe
    - If the callback is synchronous, it calls it directly on the calling thread

    Without an attached event loop, callbacks are still wrapped so that
    exceptions can be forwarded to the error handler.

    Optionally catches exceptions raised by user callbacks and forwards them
    to a user-provided error handler. The error handler is read at invocation
    time, so a handler registered AFTER wrapping still takes effect.

    The dispatcher splits callbacks into two categories:
    - Fire-and-forget callbacks: used for payload handlers, stream handlers
    - With-result callbacks: used for request handlers that expect a response

    Usage:
        dispatcher = _CallbackDispatcher()
        dispatcher.attach()  # attach to the current event loop
        wrapped_fn = dispatcher.wrap(user_fn)
    """

    def __init__(self, result_timeout: float = _CALLBACK_RESULT_TIMEOUT):
        self._loop = None
        self._error_handler = None
        #: Maximum seconds to wait for an async handler result from a C++ I/O
        #: thread. Short default keeps a wedged handler from stalling the
        #: I/O thread (and the GIL) for long.
        self._result_timeout = result_timeout

    def set_error_handler(self, handler):
        """Set a handler for callback exceptions.

        Args:
            handler: Callable[[Exception], None] or None to disable.
                     Called when a user callback raises an exception.
        """
        self._error_handler = handler

    def attach(self, loop=None):
        """Attach to an event loop.

        Args:
            loop: Optional event loop. If None, uses the running loop
                  or the default event loop.
        """
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    return  # no loop available
        self._loop = loop

    def wrap(self, fn):
        """Wrap a callback for safe invocation from C++ threads, waiting for result.

        If an event loop is attached and the callback produces a coroutine,
        the coroutine is scheduled on the event loop and this blocks on
        ``future.result(timeout=result_timeout)`` with periodic GIL release.
        Catches exceptions and forwards them to the error handler, or re-raises
        if no error handler is set.

        The wrapped callback marks the calling thread as being inside a
        callback/IO thread for its duration (see ``_in_callback_thread``).

        Args:
            fn: The callback function to wrap, or None.

        Returns:
            The wrapped function, or None if fn is None.
        """
        if fn is None:
            return None
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        if self._loop is None:
            return self._wrap_sync(fn)
        return self._wrap_async_with_result(fn)

    def wrap_fire_and_forget(self, fn):
        """Wrap a callback without waiting for the result.

        Like :meth:`wrap`, but does NOT call ``.result()`` -- schedules the
        coroutine on the event loop and returns immediately.

        Args:
            fn: The callback function to wrap, or None.

        Returns:
            The wrapped function, or None if fn is None.
        """
        if fn is None:
            return None
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        if self._loop is not None:
            return self._wrap_async_fire_and_forget(fn)
        return self._wrap_sync(fn)

    def wrap_identity(self, handler):
        """Wrap a client identity handler for async dispatch.

        The wrapped handler receives ``(hdl, public_key)`` and returns
        ``True`` to accept or ``False`` to reject. If the handler is async,
        it is scheduled on the event loop with a ``result_timeout``-second
        timeout (default ~5s) and GIL-releasing polling while waiting.

        The return value is coerced to ``bool``: returning ``None`` is
        equivalent to returning ``False`` (both sync and async handlers).

        The error handler is read at invocation time, so a handler
        registered (or replaced) AFTER wrapping still takes effect.

        The wrapped callback marks the calling thread as being inside a
        callback/IO thread for its duration.

        Args:
            handler: The identity verification callback.

        Returns:
            The wrapped function.
        """
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

        loop = self._loop
        result_timeout = self._result_timeout

        def identity_wrapper(hdl, pk):
            _set_callback_thread_flag(True)
            try:
                result = handler(hdl, pk)
                if asyncio.iscoroutine(result):
                    if loop is not None:
                        future = asyncio.run_coroutine_threadsafe(result, loop)
                        return bool(_wait_future_with_polling(future, result_timeout))
                    raise RuntimeError("Cannot schedule async identity handler: no event loop attached")
                return bool(result)
            except Exception as e:
                # Read the error handler at invocation time so handlers
                # registered AFTER wrapping still take effect.
                error_handler = self._error_handler
                if error_handler:
                    error_handler(e)
                    return False
                raise
            finally:
                _set_callback_thread_flag(False)

        return identity_wrapper

    def _wrap_sync(self, fn):
        """Wrap for sync mode (no event loop) -- adds error catching only.

        The error handler is read at invocation time, so a handler
        registered (or replaced) AFTER wrapping still takes effect.
        If no error handler is configured, exceptions are re-raised.

        The wrapped callback marks the calling thread as being inside a
        callback/IO thread for its duration.
        """

        def wrapped(*args, **kwargs):
            _set_callback_thread_flag(True)
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                # Read the error handler at invocation time so a handler
                # registered (or replaced) AFTER wrapping still takes effect.
                error_handler = self._error_handler
                if error_handler:
                    error_handler(e)
                    return None
                raise
            finally:
                _set_callback_thread_flag(False)

        return wrapped

    def _wrap_async_fire_and_forget(self, fn):
        """Wrap for async mode -- dispatches to event loop without waiting for result.

        Schedules coroutines on the event loop via ``run_coroutine_threadsafe``
        but does NOT call ``.result()``, so the calling thread returns immediately.

        If ``error_handler`` is set, exceptions raised by async coroutines are
        forwarded via a done callback on the event loop (instead of being lost).

        The wrapped callback marks the calling thread as being inside a
        callback/IO thread for its duration.
        """
        assert self._loop is not None, "attach_event_loop() must be called before using async dispatch"
        loop = self._loop

        def dispatched(*args, **kwargs):
            _set_callback_thread_flag(True)
            try:
                result = fn(*args, **kwargs)
                if inspect.iscoroutine(result):
                    future = asyncio.run_coroutine_threadsafe(result, loop)
                    # Read the error handler at invocation time so handlers
                    # registered AFTER wrapping still take effect.
                    error_handler = self._error_handler
                    if error_handler:

                        def _forward_error(fut):
                            try:
                                fut.result()
                            except Exception as e:
                                assert error_handler is not None
                                error_handler(e)

                        future.add_done_callback(_forward_error)
                    return None  # fire-and-forget
                return result
            except Exception as e:
                # Read the error handler at invocation time so handlers
                # registered AFTER wrapping still take effect.
                error_handler = self._error_handler
                if error_handler:
                    error_handler(e)
                else:
                    raise
                return None
            finally:
                _set_callback_thread_flag(False)

        return dispatched

    def _wrap_async_with_result(self, fn):
        """Wrap for async mode -- dispatches to event loop and waits for result.

        Schedules coroutines on the event loop and waits for the return value
        with periodic GIL release (``future.done()`` polling + ``time.sleep``),
        bounded by ``result_timeout`` (default ~5s). On timeout, or any other
        exception, the error is forwarded to the error handler, or re-raised if
        no error handler is set.

        The wrapped callback marks the calling thread as being inside a
        callback/IO thread for its duration.
        """
        assert self._loop is not None, "attach_event_loop() must be called before using async dispatch"
        loop = self._loop
        result_timeout = self._result_timeout

        def dispatched(*args, **kwargs):
            _set_callback_thread_flag(True)
            try:
                result = fn(*args, **kwargs)
                if inspect.iscoroutine(result):
                    future = asyncio.run_coroutine_threadsafe(result, loop)
                    try:
                        return _wait_future_with_polling(future, result_timeout)
                    except Exception as e:
                        # Read the error handler at invocation time so handlers
                        # registered AFTER wrapping still take effect.
                        error_handler = self._error_handler
                        if error_handler:
                            error_handler(e)
                        else:
                            raise
                        return None
                return result
            except Exception as e:
                # Read the error handler at invocation time so handlers
                # registered AFTER wrapping still take effect.
                error_handler = self._error_handler
                if error_handler:
                    error_handler(e)
                else:
                    raise
                return None
            finally:
                _set_callback_thread_flag(False)

        return dispatched


try:
    # This is the C++ extension module built by CMake.
    from . import _obscuraproto as _bindings  # pyright: ignore[reportAttributeAccessIssue]
except ImportError:
    # If the extension is not in the same directory, it might be in the build/lib directory.
    # This is a fallback for development environments. For a real installation,
    # the package structure would handle this.
    import os
    import sys

    # Heuristic to find the build directory.
    # Assumes the project root is two levels up from this file's directory.
    proj_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    build_dir = os.path.join(proj_root, "build")

    # The compiled library is directly in the build directory now
    if os.path.isdir(build_dir):
        sys.path.insert(0, build_dir)

    try:
        # The module name includes version and platform info, so we search for it.
        if os.path.isdir(build_dir):
            for f in os.listdir(build_dir):
                if f.startswith("_obscuraproto") and f.endswith(".so"):
                    import importlib.util

                    spec = importlib.util.spec_from_file_location("_obscuraproto", os.path.join(build_dir, f))
                    _bindings = importlib.util.module_from_spec(spec)  # pyright: ignore[reportArgumentType]
                    spec.loader.exec_module(_bindings)  # pyright: ignore[reportOptionalMemberAccess]
                    sys.modules["_obscuraproto"] = _bindings
                    break
            else:
                raise ImportError("Could not find the _obscuraproto.*.so module in the build directory.")
        else:
            raise ImportError("Build directory not found.")

    except ImportError as e:
        raise ImportError(
            "Could not import the compiled ObscuraProto C++ bindings (_obscuraproto). "
            "Please make sure the project is built. "
            f"Original error: {e}"
        )


# --- Marker type for automatic unpacking ---
class uint(int):
    """A marker type for function signature hints.
    Indicates that an integer parameter should be read from a payload as unsigned.

    Example:
        @server.on_payload(0x1234)
        def my_handler(value: uint):
            # value will be read using PayloadReader.read_uint()
            print(f"Received unsigned value: {value}")
    """

    pass


# --- Re-export low-level components ---
Role = _bindings.Role
Crypto = _bindings.Crypto
Payload = _bindings.Payload
PayloadBuilder = _bindings.PayloadBuilder
PayloadReader = _bindings.PayloadReader
KeyPair = _bindings.KeyPair
PublicKey = _bindings.PublicKey
PrivateKey = _bindings.PrivateKey
Signature = _bindings.Signature
V1_0 = _bindings.V1_0
V1_1 = _bindings.V1_1
SUPPORTED_VERSIONS = _bindings.SUPPORTED_VERSIONS
ConnectionHdl = _bindings.ConnectionHdl
CppStream = _bindings.CppStream

# Config
Config = _bindings.Config
RateLimitConfig = _bindings.RateLimitConfig
ConnectionLimitConfig = _bindings.ConnectionLimitConfig
MessageLimitConfig = _bindings.MessageLimitConfig
TimeoutConfig = _bindings.TimeoutConfig
ReservedOpcodes = _bindings.ReservedOpcodes

# Rate limiting and secure memory
RateLimiter = _bindings.RateLimiter
SecureBuffer = _bindings.SecureBuffer
DecryptedResult = _bindings.DecryptedResult

# Exceptions — subclasses of the Python builtins, so base-class catches work:
# InvalidArgument subclasses ValueError, LogicError subclasses RuntimeError,
# TimeoutError subclasses builtins.TimeoutError.
TimeoutError = _bindings.TimeoutError
InvalidArgument = _bindings.InvalidArgument
LogicError = _bindings.LogicError


class Stream:
    """A bidirectional, multiplexed data stream over an encrypted WebSocket.

    Wraps the C++ ``CppStream`` to provide Pythonic decorator-based registration
    of data/end/cancel handlers and async-friendly I/O.

    You don't create Stream directly — obtain one via ``server.start_stream(hdl)``,
    ``client.start_stream()``, or an ``@on_incoming_stream`` decorated handler.

    The Stream class provides both synchronous and asynchronous I/O methods:

        stream.write(b"data")  # synchronous
        await stream.async_write(b"data")  # asynchronous

        stream.end()  # synchronous
        await stream.async_end()  # asynchronous

        stream.cancel()  # synchronous
        await stream.async_cancel()  # asynchronous
    """

    def __init__(self, cpp_stream):
        self._s = cpp_stream

    def set_dispatcher(self, dispatcher):
        """Internal: attach a callback dispatcher for thread-safe handler dispatch."""
        self._dispatcher = dispatcher

    @property
    def stream_id(self) -> int:
        """Unique stream identifier."""
        return self._s.get_stream_id()

    @property
    def op_code(self):
        """The stream's op code, or None if not set."""
        return self._s.get_op_code()

    # --- Synchronous I/O (use inside C++ callbacks) ---

    def write(self, data: bytes):
        """Send a data chunk over the stream (thread-safe, releases GIL)."""
        self._s.write(data)

    def end(self):
        """Signal end of outgoing data (half-close, releases GIL)."""
        self._s.end()

    def cancel(self):
        """Abort the stream immediately (releases GIL)."""
        self._s.cancel()

    # --- Async I/O (use inside async code) ---

    async def async_write(self, data: bytes):
        """Send a data chunk without blocking the event loop."""
        await _run_in_stream_executor(self._s.write, data)

    async def async_end(self):
        """Signal end of outgoing data without blocking the event loop."""
        await _run_in_stream_executor(self._s.end)

    async def async_cancel(self):
        """Abort the stream without blocking the event loop."""
        await _run_in_stream_executor(self._s.cancel)

    # --- Decorator-style handler registration ---

    def on_data(self, handler):
        """Register a callback for incoming data chunks.

        Can be used as a decorator::

            @stream.on_data
            def on_chunk(data: bytes):
                print(f"Got {len(data)} bytes")

        Args:
            handler: Callable[[bytes], None]
        """

        def wrapper(data_list):
            # Return the handler result so async handlers are detected by the
            # fire-and-forget dispatcher (which schedules the coroutine).
            return handler(bytes(data_list))

        dispatcher = getattr(self, "_dispatcher", None)
        self._s.set_data_handler(dispatcher.wrap_fire_and_forget(wrapper) if dispatcher else wrapper)
        return handler

    def on_end(self, handler):
        """Register a callback for when the remote side finishes writing.

        Can be used as a decorator::

            @stream.on_end
            def on_end():
                stream.end()  # echo the half-close
        """
        dispatcher = getattr(self, "_dispatcher", None)
        self._s.set_end_handler(dispatcher.wrap_fire_and_forget(handler) if dispatcher else handler)
        return handler

    def on_cancel(self, handler):
        """Register a callback for when the remote side cancels the stream.

        Can be used as a decorator::

            @stream.on_cancel
            def on_cancel():
                print("Stream was cancelled")
        """
        dispatcher = getattr(self, "_dispatcher", None)
        self._s.set_cancel_handler(dispatcher.wrap_fire_and_forget(handler) if dispatcher else handler)
        return handler

    def on_error(self, handler):
        """Register a handler for callback errors on this stream.

        Catches exceptions from on_data, on_end, on_cancel handlers::

            @stream.on_error
            def handle_error(error: Exception):
                print(f"Stream callback error: {error}")

        Args:
            handler: Callable[[Exception], None]

        Returns:
            The handler function (for use as a decorator).
        """
        dispatcher = getattr(self, "_dispatcher", None)
        if dispatcher:
            dispatcher.set_error_handler(handler)
        return handler


def _create_unpacking_handler(handler, receives_hdl_from_native=False):
    """
    Internal helper to create a wrapper function that intelligently calls a handler
    by inspecting its type hints. It can pass the connection handle, the raw payload,
    or auto-unpacked arguments.
    """
    sig = inspect.signature(handler)
    params = sig.parameters

    hdl_param = None
    payload_param = None
    unpack_params = []

    for param in params.values():
        if param.annotation is ConnectionHdl:
            hdl_param = param
        elif param.annotation is Payload:
            payload_param = param
        elif param.annotation is not param.empty:
            unpack_params.append(param)

    # --- Basic validation ---
    if hdl_param and not receives_hdl_from_native:
        raise TypeError(
            f"Handler '{handler.__name__}' is annotated with ConnectionHdl "
            "but is registered on a client, which does not receive it."
        )
    if payload_param and unpack_params:
        raise TypeError(
            f"Handler '{handler.__name__}' cannot mix auto-unpacking "
            "parameters and a 'Payload' parameter. Choose one method."
        )

    # --- Create the specialized wrapper ---
    def unpacking_wrapper(*args):
        # Determine what C++ passed us based on the context
        hdl = args[0] if receives_hdl_from_native else None
        payload = args[1] if receives_hdl_from_native else args[0]

        handler_kwargs = {}

        if hdl_param:
            handler_kwargs[hdl_param.name] = hdl

        if payload_param:
            handler_kwargs[payload_param.name] = payload
            # When using raw payload, no further unpacking is done.
            return handler(**handler_kwargs)

        # If there are params to unpack, do it.
        if unpack_params:
            reader = PayloadReader(payload)
            type_map = {
                str: reader.read_string,
                int: reader.read_int,
                uint: reader.read_uint,
                float: reader.read_float,
                bool: reader.read_bool,
                bytes: reader.read_bytes,
            }

            try:
                for param in unpack_params:
                    type_hint = param.annotation
                    if type_hint in type_map:
                        handler_kwargs[param.name] = type_map[type_hint]()
                    else:
                        # This case covers missing or unsupported type hints for unpacking
                        raise TypeError(f"Unsupported or missing type hint for parameter '{param.name}'.")

            except Exception as e:
                op_code_hex = f"0x{payload.op_code:04x}" if payload else "N/A"
                logger.error(
                    "Failed to auto-unpack payload for OpCode %s. "
                    "Check handler '%s' signature "
                    "matches the payload structure. Details: %s",
                    op_code_hex,
                    handler.__name__,
                    e,
                )
                raise TypeError(
                    f"Failed to auto-unpack payload for OpCode {op_code_hex} in handler '{handler.__name__}': {e}"
                ) from e

        # Call the handler with the arguments we've prepared.
        # This works even if there are no unpack_params (fire-and-forget handlers).
        return handler(**handler_kwargs)

    return unpacking_wrapper


def _create_request_unpacking_handler(handler, receives_hdl_from_native=False):
    """
    Internal helper to create a wrapper function for request handlers.
    It intelligently calls a handler by inspecting its type hints, passing the
    connection handle (for server), or auto-unpacked arguments from a PayloadReader.
    The handler is expected to return a Payload object.
    """
    sig = inspect.signature(handler)
    params = sig.parameters

    hdl_param = None
    unpack_params = []

    # Identify hdl parameter if present
    param_list = list(params.values())
    if receives_hdl_from_native and param_list and param_list[0].annotation is ConnectionHdl:
        hdl_param = param_list[0]
        unpack_params = param_list[1:]
    else:
        unpack_params = param_list

    def unpacking_request_wrapper(*args):
        # Determine what C++ passed us based on the context
        # For server: (hdl, reader_obj)
        # For client: (reader_obj)
        if receives_hdl_from_native:
            hdl = args[0]
            reader_obj = args[1]
        else:
            hdl = None
            reader_obj = args[0]  # This will be the PayloadReader object passed from C++

        handler_kwargs = {}
        if hdl_param:
            handler_kwargs[hdl_param.name] = hdl

        # Unpack parameters from the PayloadReader
        reader = reader_obj  # In C++, PayloadReader is passed by reference, Python gets a binding object

        type_map = {
            str: reader.read_string,
            int: reader.read_int,
            uint: reader.read_uint,
            float: reader.read_float,
            bool: reader.read_bool,
            bytes: reader.read_bytes,
        }

        try:
            for param in unpack_params:
                type_hint = param.annotation
                if type_hint is PayloadReader:  # If the handler explicitly requests PayloadReader
                    handler_kwargs[param.name] = reader
                elif type_hint in type_map:
                    handler_kwargs[param.name] = type_map[type_hint]()
                else:
                    raise TypeError(f"Unsupported or missing type hint for parameter '{param.name}'.")

        except Exception as e:
            logger.error(
                "Failed to auto-unpack request payload for handler '%s'. "
                "Check that the handler signature matches the expected payload structure. Details: %s",
                handler.__name__,
                e,
            )
            # For request handlers, if unpacking fails, we must return an error payload
            # or allow the C++ layer to handle the exception. For now, a generic error.
            # A more robust solution might involve an error payload specific opcode.
            # The C++ will handle the Python exception, but returning a Payload is cleaner.
            error_payload = PayloadBuilder(0x0000).add_param(f"Error: {e}").build()
            return error_payload

        # Call the handler, expecting a Payload return
        response_payload = handler(**handler_kwargs)

        # If handler is async (coroutine function), the result is a coroutine object.
        # Skip the isinstance check — the _CallbackDispatcher will schedule it
        # on the event loop and get the actual Payload result.
        if inspect.iscoroutine(response_payload):
            return response_payload

        if not isinstance(response_payload, _bindings.Payload):
            raise TypeError(
                f"Request handler '{handler.__name__}' must return a "
                f"'Payload' object, but returned {type(response_payload)}"
            )
        return response_payload

    return unpacking_request_wrapper


# --- High-level wrapper classes ---


class Server:
    """
    An ObscuraProto WebSocket server.

    This class wraps the C++ WsServer to provide a Pythonic interface with
    decorators for handling events.

    The server can be used as an async context manager for automatic resource management:

        async with Server(port=9001) as server:
            @server.on_payload(0x1001)
            def handle(hdl, data: str):
                print(f"Received: {data}")
            await asyncio.Future()  # run forever
    """

    def __init__(self, config=None, port=None):
        """Initializes the server, generating its long-term signing key.

        Args:
            config: An optional Config object. If None, default config is used.
            port: Optional port number. If provided, the server will be started
                  automatically when used as an async context manager.
        """
        self._long_term_key = _bindings.Crypto.generate_sign_keypair()
        cfg = config if config is not None else _bindings.Config.with_defaults()
        self._server = _bindings.WsServer(self._long_term_key, cfg)
        self._dispatcher = _CallbackDispatcher()
        self._port = port

    def attach_event_loop(self, loop=None):
        """Attach all callbacks to an asyncio event loop for thread-safe dispatch.

        Call this if you use async handlers (coroutines) in your callbacks::

            server = Server()
            server.attach_event_loop()


            @server.on_payload(0x1001)
            async def handle(data: str):
                result = await some_async_operation(data)
                ...

        Args:
            loop: Optional event loop. If None, uses the running/default loop.
        """
        self._dispatcher.attach(loop)

    def on_error(self, handler):
        """Register a handler for callback errors.

        Use this to catch exceptions that occur in your event handlers
        (on_payload, on_request, on_stream, etc.)::

            @server.on_error
            def handle_error(error: Exception):
                print(f"Callback error: {error}")

        Args:
            handler: Callable[[Exception], None]

        Returns:
            The handler function (for use as a decorator).
        """
        self._dispatcher.set_error_handler(handler)
        return handler

    @property
    def public_key(self):
        """The server's long-term public key, needed by clients to connect."""
        return self._long_term_key.public_key

    def start(self, port):
        """
        Starts the WebSocket server on the given port.
        This runs the server in a background thread.
        """
        logger.info("Starting server on port %s...", port)
        self._server.run(port)
        logger.info("Server started.")

    def stop(self):
        """Stops the server.

        Raises:
            LogicError: If called from a callback/IO thread. ``stop()`` joins
                the server's I/O thread; calling it from inside a handler that
                runs on that very thread would self-deadlock. Call ``stop()``
                from outside a callback (e.g. from the main thread or after
                the handler returns).
        """
        if _in_callback_thread():
            raise LogicError("Server.stop() cannot be called from a callback/IO thread (self-join deadlock)")
        logger.info("Stopping server...")
        self._server.stop()
        logger.info("Server stopped.")

    async def __aenter__(self):
        """Enter async context: starts the server if a port was provided.

        Usage::

            async with Server(port=9001) as server:
                ...
        """
        if self._port is not None:
            self.start(self._port)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit async context: stops the server."""
        self.stop()
        return False  # do not suppress exceptions

    def on_open(self, handler):
        """Decorator to register a callback for when a new WebSocket connection is opened.

        The decorated function receives a ConnectionHdl::

            @server.on_open
            def on_connection_open(hdl):
                print(f"New connection: {hdl}")

        Args:
            handler: Callable[[ConnectionHdl], None]
        """
        self._server.set_on_open_callback(self._dispatcher.wrap_fire_and_forget(handler))
        return handler

    def on_close(self, handler):
        """Decorator to register a callback for when a WebSocket connection is closed.

        The decorated function receives a ConnectionHdl::

            @server.on_close
            def on_connection_close(hdl):
                print(f"Connection closed: {hdl}")

        Args:
            handler: Callable[[ConnectionHdl], None]
        """
        self._server.set_on_close_callback(self._dispatcher.wrap_fire_and_forget(handler))
        return handler

    def send(self, hdl, payload):
        """Sends a payload to a specific client."""
        self._server.send(hdl, payload)

    def sync_request(self, hdl, payload) -> Payload:
        """Sends a synchronous request to a specific client and returns the response.

        .. warning::

            Do not call this from an async handler. ``sync_request`` blocks the
            calling thread until the response arrives; from an async handler it
            would stall the event loop. Use ``await async_request`` instead.

        Raises:
            LogicError: If called from a callback/IO thread. ``sync_request``
                blocks until the response arrives, but the C++ I/O thread that
                must service that response is the very thread executing the
                callback -- this would self-deadlock. Use ``async_request``
                from inside handlers instead.
        """
        _warn_if_event_loop_thread()
        if _in_callback_thread():
            raise LogicError("sync_request cannot be called from a callback/IO thread")
        return self._server.sync_request(hdl, payload)

    async def async_request(self, hdl, payload, timeout: float | None = None) -> Payload:
        """Sends a request to a specific client and returns the response.

        Uses the C++ async_request bridge: the C++ side returns a CppPayloadFuture
        immediately (no thread-pool thread is blocked). The response is awaited
        without blocking the event loop (see ``_await_cpp_future``).

        Timeout ownership: ``timeout`` is forwarded to the C++ layer as
        ``timeout_ms`` (``int(timeout * 1000)``); the C++ layer is the sole
        owner of the request timeout. When ``timeout`` is ``None`` or ``<= 0``
        the C++ layer is called without an explicit timeout (unlimited / config
        default) and the Python-side ``asyncio.wait_for`` guard is disabled.

        Args:
            hdl: Connection handle of the target client.
            payload: The request payload to send.
            timeout: Maximum seconds to wait for the response (default None =
                unlimited; C++ applies its configured ``request_ms``). Raises
                ``TimeoutError`` if the remote side never responds.
        """
        timeout_ms = None if timeout is None or timeout <= 0 else int(timeout * 1000)
        if timeout_ms is None:
            cpp_future = self._server.async_request(hdl, payload)
        else:
            cpp_future = self._server.async_request(hdl, payload, timeout_ms)
        return await _await_cpp_future(cpp_future, timeout=timeout, what="async_request")

    def start_stream(self, hdl, stream_op_code=None):
        """Starts a new outgoing stream to a specific client.

        Args:
            hdl: Connection handle of the target client.
            stream_op_code: Optional op_code for the stream.

        Returns a :class:`Stream` that can be used to write data.

        Example:
            stream = server.start_stream(hdl)
            stream.write(b"hello")
            stream.end()

            # With op_code:
            stream = server.start_stream(hdl, 0x3001)
        """
        if stream_op_code is not None:
            stream = Stream(self._server.start_stream(hdl, stream_op_code))
        else:
            stream = Stream(self._server.start_stream(hdl))
        stream.set_dispatcher(self._dispatcher)
        return stream

    async def async_start_stream(self, hdl, stream_op_code=None):
        """Async version of :meth:`start_stream` — does not block the event loop.

        Args:
            hdl: Connection handle of the target client.
            stream_op_code: Optional op_code for the stream.
        """
        if stream_op_code is not None:
            cpp_stream = await _run_in_stream_executor(self._server.start_stream, hdl, stream_op_code)
        else:
            cpp_stream = await _run_in_stream_executor(self._server.start_stream, hdl)
        stream = Stream(cpp_stream)
        stream.set_dispatcher(self._dispatcher)
        return stream

    def on_incoming_stream(self, handler):
        """Decorator to register a handler for incoming streams from clients.

        The decorated function receives a :class:`Stream`::

            @server.on_incoming_stream
            def handle_stream(stream: Stream):
                @stream.on_data
                def on_data(data: bytes):
                    print(f"Received: {data}")
        """

        def wrapper(cpp_stream):
            stream = Stream(cpp_stream)
            stream.set_dispatcher(self._dispatcher)
            return handler(stream)

        self._server.register_incoming_stream_handler(self._dispatcher.wrap(wrapper))
        return handler

    def on_stream(self, op_code):
        """Decorator to register a handler for incoming authenticated streams
        with a specific op_code.

        The decorated function receives a :class:`Stream`::

            @server.on_stream(0x3001)
            def handle_stream(stream: Stream):
                @stream.on_data
                def on_data(data: bytes):
                    print(f"Received: {data}")

        Args:
            op_code: The op_code of streams to handle.
        """

        def decorator(handler):
            def wrapper(cpp_stream):
                stream = Stream(cpp_stream)
                stream.set_dispatcher(self._dispatcher)
                return handler(stream)

            self._server.register_stream_handler(op_code, self._dispatcher.wrap(wrapper))
            return handler

        return decorator

    def on_anon_stream(self, op_code):
        """Decorator to register a handler for incoming anonymous streams
        with a specific op_code.

        The decorated function receives a :class:`Stream`::

            @server.on_anon_stream(0x4001)
            def handle_anon_stream(stream: Stream):
                @stream.on_data
                def on_data(data: bytes):
                    print(f"Received: {data}")

        Args:
            op_code: The op_code of anonymous streams to handle.
        """

        def decorator(handler):
            def wrapper(cpp_stream):
                stream = Stream(cpp_stream)
                stream.set_dispatcher(self._dispatcher)
                return handler(stream)

            self._server.register_anon_stream_handler(op_code, self._dispatcher.wrap(wrapper))
            return handler

        return decorator

    def on_payload(self, opcode):
        """
        Decorator to register a handler for a specific opcode.

        The decorated function will be called with arguments unpacked from the
        payload based on type hints. If no type hints are provided, it will be
        called with ``(hdl, payload)``.

        Handler signature::

            Callable[[ConnectionHdl, ...], Any]

        Example::

            @server.on_payload(0x1001)
            def handle_login(hdl, username: str, password: str, attempt: uint):
                print(f"Login attempt for '{username}'")
        """

        def decorator(handler):
            wrapper = _create_unpacking_handler(handler, receives_hdl_from_native=True)
            self._server.register_op_handler(opcode, self._dispatcher.wrap_fire_and_forget(wrapper))
            return handler

        return decorator

    def default_payload_handler(self, handler):
        """
        Decorator for the default handler, with auto-unpacking based on type hints.

        Handler signature::

            Callable[[ConnectionHdl, ...], Any]
        """
        wrapper = _create_unpacking_handler(handler, receives_hdl_from_native=True)
        self._server.set_default_payload_handler(self._dispatcher.wrap_fire_and_forget(wrapper))
        return handler

    def on_request(self, opcode):
        """
        Registers a handler for a specific opcode that expects a response.

        The decorated function will be called with ConnectionHdl (for the server)
        and arguments unpacked from the payload reader based on type hints.
        The handler must return a Payload object as a response.

        Handler signature::

            Callable[[ConnectionHdl, PayloadReader], Payload]

        Example::

            @server.on_request(0x1002)
            def handle_sum_request(hdl: ConnectionHdl, a: int, b: int) -> Payload:
                result = a + b
                return PayloadBuilder(0x1003).add_param(result).build()
        """

        def decorator(handler):
            wrapper = _create_request_unpacking_handler(handler, receives_hdl_from_native=True)
            self._server.register_request_handler(opcode, self._dispatcher.wrap(wrapper))
            return handler

        return decorator

    # --- Anonymous Sessions ---

    def send_anonymous(self, hdl, payload):
        """Sends a payload to an anonymous session."""
        self._server.send_anonymous(hdl, payload)

    def on_anon_payload(self, opcode):
        """
        Decorator to register a handler for a specific opcode on anonymous sessions.

        The decorated function will be called with arguments unpacked from the
        payload based on type hints. If no type hints are provided, it will be
        called with ``(hdl, payload)``.

        Handler signature::

            Callable[[ConnectionHdl, ...], Any]

        Example::

            @server.on_anon_payload(0x5001)
            def handle_anon_register(hdl, key_data: bytes):
                print(f"Anonymous client wants to register")
        """

        def decorator(handler):
            wrapper = _create_unpacking_handler(handler, receives_hdl_from_native=True)
            self._server.register_anon_op_handler(opcode, self._dispatcher.wrap_fire_and_forget(wrapper))
            return handler

        return decorator

    def anon_default_payload_handler(self, handler):
        """
        Decorator for the default handler for anonymous sessions,
        with auto-unpacking based on type hints.

        Handler signature::

            Callable[[ConnectionHdl, ...], Any]
        """
        wrapper = _create_unpacking_handler(handler, receives_hdl_from_native=True)
        self._server.set_anon_default_payload_handler(self._dispatcher.wrap_fire_and_forget(wrapper))
        return handler

    def on_anon_request(self, opcode):
        """
        Registers a request handler for anonymous sessions.

        The decorated function will be called with ConnectionHdl and arguments
        unpacked from the payload reader based on type hints.
        The handler must return a Payload object as a response.

        Handler signature::

            Callable[[ConnectionHdl, PayloadReader], Payload]

        Example::

            @server.on_anon_request(0x5002)
            def handle_anon_auth(hdl: ConnectionHdl, token: str) -> Payload:
                return PayloadBuilder(0x5003).add_param(True).build()
        """

        def decorator(handler):
            wrapper = _create_request_unpacking_handler(handler, receives_hdl_from_native=True)
            self._server.register_anon_request_handler(opcode, self._dispatcher.wrap(wrapper))
            return handler

        return decorator

    # --- Client Identity ---

    def _set_client_identity_handler(self, handler):
        """
        Internal: sets a handler that is called when a client authenticates with
        an identity key. The handler receives (hdl, public_key) and should return
        True to accept or False to reject the connection.

        Called from the public ``on_client_identity`` decorator.
        """
        wrapped = self._dispatcher.wrap_identity(handler)
        self._server.set_client_identity_handler(wrapped)

    def on_client_identity(self, handler):
        """
        Decorator to register a handler for client identity verification.

        The decorated function receives ``(hdl, public_key)`` and should return
        ``True`` to accept or ``False`` to reject the connection.

        The return value is coerced to ``bool``: returning ``None`` is
        equivalent to returning ``False`` (both sync and async handlers).

        Handler signature::

            Callable[[ConnectionHdl, PublicKey], bool]

        Args:
            handler: Callable[[ConnectionHdl, PublicKey], bool]
        """
        self._set_client_identity_handler(handler)
        return handler

    def get_client_identity(self, hdl) -> PublicKey:
        """Gets the verified identity public key for an authenticated session."""
        return self._server.get_client_identity(hdl)

    def send_to_identity(self, identity_pk, payload):
        """Sends a payload to a specific client identified by their public key."""
        self._server.send_to_identity(identity_pk, payload)

    async def async_request_to_identity(self, identity_pk, payload, timeout: float = 30.0) -> Payload:
        """Sends a request to a client identified by their public key (async).

        Uses the C++ async_request_to_identity bridge: the C++ side returns a
        CppPayloadFuture immediately. The response is awaited without blocking
        the event loop (see ``_await_cpp_future``).

        Args:
            identity_pk: Public key of the target client.
            payload: The request payload to send.
            timeout: Maximum seconds to wait for the response (default 30.0).
                     Raises ``TimeoutError`` if the remote side never responds.
        """
        cpp_future = self._server.async_request_to_identity(identity_pk, payload)
        return await _await_cpp_future(cpp_future, timeout=timeout, what="async_request_to_identity")

    def sync_request_to_identity(self, identity_pk, payload) -> Payload:
        """Sends a synchronous request to a client identified by their public key.

        .. warning::

            Do not call this from an async handler. ``sync_request_to_identity``
            blocks the calling thread until the response arrives; from an async
            handler it would stall the event loop. Use
            ``await async_request_to_identity`` instead.

        Raises:
            LogicError: If called from a callback/IO thread. ``sync_request``
                blocks until the response arrives, but the C++ I/O thread that
                must service that response is the very thread executing the
                callback -- this would self-deadlock. Use
                ``async_request_to_identity`` from inside handlers instead.
        """
        _warn_if_event_loop_thread()
        if _in_callback_thread():
            raise LogicError("sync_request cannot be called from a callback/IO thread")
        return self._server.sync_request_to_identity(identity_pk, payload)


class Client:
    """
    An ObscuraProto WebSocket client.

    Wraps the C++ WsClient for a Pythonic interface with decorators.

    The client can be used as an async context manager for automatic resource management:

        async with Client(server.public_key, uri="ws://localhost:9001") as client:
            @client.on_ready
            def ready():
                client.send(PayloadBuilder(0x1001).add_param("Hello").build())
            await asyncio.Future()  # run forever
    """

    def __init__(self, server_public_key, config=None, uri=None):
        """
        Args:
            server_public_key: The public key of the server to connect to.
            config: An optional Config object. If None, default config is used.
            uri: Optional WebSocket URI. If provided, the client will connect
                 automatically when used as an async context manager.
        """
        if not isinstance(server_public_key, _bindings.PublicKey):
            raise TypeError("server_public_key must be a PublicKey object.")

        key_view = _bindings.KeyPair()
        key_view.public_key = server_public_key
        cfg = config if config is not None else _bindings.Config.with_defaults()
        self._client = _bindings.WsClient(key_view, cfg)
        self._dispatcher = _CallbackDispatcher()
        self._uri = uri

    def attach_event_loop(self, loop=None):
        """Attach all callbacks to an asyncio event loop for thread-safe dispatch.

        Call this if you use async handlers (coroutines) in your callbacks::

            client = Client(server_pk)
            client.attach_event_loop()


            @client.on_payload(0x2001)
            async def handle(data: str):
                result = await process_data(data)
                ...

        Args:
            loop: Optional event loop. If None, uses the running/default loop.
        """
        self._dispatcher.attach(loop)

    def on_error(self, handler):
        """Register a handler for callback errors.

        Use this to catch exceptions that occur in your event handlers::

            @client.on_error
            def handle_error(error: Exception):
                print(f"Callback error: {error}")

        Args:
            handler: Callable[[Exception], None]

        Returns:
            The handler function (for use as a decorator).
        """
        self._dispatcher.set_error_handler(handler)
        return handler

    def set_client_identity(self, keypair):
        """Sets the client's Ed25519 identity keypair for authentication.

        The server will receive this identity during the handshake and can verify
        it via the on_client_identity handler.

        Args:
            keypair: A KeyPair containing the client's Ed25519 keys.
        """
        self._client.set_client_identity(keypair)

    def connect(self, uri):
        """Connects to the server at the given WebSocket URI (e.g., "ws://localhost:9002")."""
        logger.info("Connecting to %s...", uri)
        self._client.connect(uri)

    def disconnect(self):
        """Disconnects from the server.

        Raises:
            LogicError: If called from a callback/IO thread. ``disconnect()``
                joins the client's I/O thread; calling it from inside a handler
                that runs on that very thread would self-deadlock. Call
                ``disconnect()`` from outside a callback (e.g. from the main
                thread or after the handler returns).
        """
        if _in_callback_thread():
            raise LogicError("Client.disconnect() cannot be called from a callback/IO thread (self-join deadlock)")
        self._client.disconnect()

    async def __aenter__(self):
        """Enter async context: connects to the server if a URI was provided.

        Usage::

            async with Client(server_pk, uri="ws://localhost:9001") as client:
                ...
        """
        if self._uri is not None:
            self.connect(self._uri)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit async context: disconnects from the server."""
        self.disconnect()
        return False  # do not suppress exceptions

    def send(self, payload):
        """Sends a payload to the server."""
        self._client.send(payload)

    def sync_request(self, payload, timeout_ms=None) -> Payload:
        """Sends a synchronous request to the server and returns the response.

        .. warning::

            Do not call this from an async handler. ``sync_request`` blocks the
            calling thread until the response arrives; from an async handler it
            would stall the event loop. Use ``await async_request`` instead.

        Args:
            payload: The request payload to send.
            timeout_ms: Optional timeout in milliseconds (0 = unlimited / use
                the C++ config default). ``None`` keeps the C++ default
                behavior. The C++ layer is the sole owner of the timeout.

        Raises:
            LogicError: If called from a callback/IO thread. ``sync_request``
                blocks until the response arrives, but the C++ I/O thread that
                must service that response is the very thread executing the
                callback -- this would self-deadlock. Use ``async_request``
                from inside handlers instead.
        """
        _warn_if_event_loop_thread()
        if _in_callback_thread():
            raise LogicError("sync_request cannot be called from a callback/IO thread")
        if timeout_ms is None:
            return self._client.sync_request(payload)
        return self._client.sync_request(payload, int(timeout_ms))

    async def async_request(self, payload, timeout: float | None = None) -> Payload:
        """Sends a request to the server and returns the response.

        Uses the C++ async_request bridge: the C++ side returns a CppPayloadFuture
        immediately (no thread-pool thread is blocked). The response is awaited
        without blocking the event loop (see ``_await_cpp_future``).

        Timeout ownership: ``timeout`` is forwarded to the C++ layer as
        ``timeout_ms`` (``int(timeout * 1000)``); the C++ layer is the sole
        owner of the request timeout. When ``timeout`` is ``None`` or ``<= 0``
        the C++ layer is called without an explicit timeout (unlimited / config
        default) and the Python-side ``asyncio.wait_for`` guard is disabled.

        Args:
            payload: The request payload to send.
            timeout: Maximum seconds to wait for the response (default None =
                unlimited; C++ applies its configured ``request_ms``). Raises
                ``TimeoutError`` if the remote side never responds.
        """
        timeout_ms = None if timeout is None or timeout <= 0 else int(timeout * 1000)
        if timeout_ms is None:
            cpp_future = self._client.async_request(payload)
        else:
            cpp_future = self._client.async_request(payload, timeout_ms)
        return await _await_cpp_future(cpp_future, timeout=timeout, what="async_request")

    def start_stream(self, stream_op_code=None):
        """Starts a new outgoing stream to the server.

        Args:
            stream_op_code: Optional op_code for the stream.

        Returns a :class:`Stream` that can be used to write data.

        Example:
            stream = client.start_stream()
            stream.write(b"hello")
            stream.end()

            # With op_code:
            stream = client.start_stream(0x3001)
        """
        if stream_op_code is not None:
            stream = Stream(self._client.start_stream(stream_op_code))
        else:
            stream = Stream(self._client.start_stream())
        stream.set_dispatcher(self._dispatcher)
        return stream

    async def async_start_stream(self, stream_op_code=None):
        """Async version of :meth:`start_stream` — does not block the event loop.

        Args:
            stream_op_code: Optional op_code for the stream.
        """
        if stream_op_code is not None:
            cpp_stream = await _run_in_stream_executor(self._client.start_stream, stream_op_code)
        else:
            cpp_stream = await _run_in_stream_executor(self._client.start_stream)
        stream = Stream(cpp_stream)
        stream.set_dispatcher(self._dispatcher)
        return stream

    def on_incoming_stream(self, handler):
        """Decorator to register a handler for incoming streams from the server.

        The decorated function receives a :class:`Stream`::

            @client.on_incoming_stream
            def handle_stream(stream: Stream):
                @stream.on_data
                def on_data(data: bytes):
                    print(f"Received: {data}")
        """

        def wrapper(cpp_stream):
            stream = Stream(cpp_stream)
            stream.set_dispatcher(self._dispatcher)
            return handler(stream)

        self._client.register_incoming_stream_handler(self._dispatcher.wrap(wrapper))
        return handler

    def on_stream(self, op_code):
        """Decorator to register a handler for incoming streams from the server
        with a specific op_code.

        The decorated function receives a :class:`Stream`::

            @client.on_stream(0x3001)
            def handle_stream(stream: Stream):
                @stream.on_data
                def on_data(data: bytes):
                    print(f"Received: {data}")

        Args:
            op_code: The op_code of streams to handle.
        """

        def decorator(handler):
            def wrapper(cpp_stream):
                stream = Stream(cpp_stream)
                stream.set_dispatcher(self._dispatcher)
                return handler(stream)

            self._client.register_stream_handler(op_code, self._dispatcher.wrap(wrapper))
            return handler

        return decorator

    def on_ready(self, handler):
        """Decorator to register a callback for when the client is connected and ready.

        Args:
            handler: Callable[[], None]
        """
        self._client.set_on_ready_callback(self._dispatcher.wrap_fire_and_forget(handler))
        return handler

    def on_disconnect(self, handler):
        """Decorator to register a callback for when the client disconnects.

        Args:
            handler: Callable[[], None]
        """
        self._client.set_on_disconnect_callback(self._dispatcher.wrap_fire_and_forget(handler))
        return handler

    def on_payload(self, opcode):
        """
        Decorator to register a handler for a specific opcode from the server.

        The decorated function will be called with arguments unpacked from the
        payload based on type hints. If no type hints are provided, it will be
        called with the raw ``payload`` object.

        Handler signature::

            Callable[[...], Any]

        Example::

            @client.on_payload(0x2001)
            def handle_message(author: str, message: str):
                print(f"{author}: {message}")
        """

        def decorator(handler):
            wrapper = _create_unpacking_handler(handler, receives_hdl_from_native=False)
            self._client.register_op_handler(opcode, self._dispatcher.wrap_fire_and_forget(wrapper))
            return handler

        return decorator

    def default_payload_handler(self, handler):
        """
        Decorator for the default handler, with auto-unpacking based on type hints.

        Handler signature::

            Callable[[...], Any]
        """
        wrapper = _create_unpacking_handler(handler, receives_hdl_from_native=False)
        self._client.set_default_payload_handler(self._dispatcher.wrap_fire_and_forget(wrapper))
        return handler

    def on_request(self, opcode):
        """
        Registers a handler for a specific opcode that expects a response.

        The decorated function will be called with arguments unpacked from the
        payload reader based on type hints. The handler must return a Payload
        object as a response.

        Handler signature::

            Callable[[PayloadReader], Payload]

        Example::

            @client.on_request(0x1002)
            def handle_sum_request(a: int, b: int) -> Payload:
                result = a + b
                return PayloadBuilder(0x1003).add_param(result).build()
        """

        def decorator(handler):
            wrapper = _create_request_unpacking_handler(handler, receives_hdl_from_native=False)
            self._client.register_request_handler(opcode, self._dispatcher.wrap(wrapper))
            return handler

        return decorator
