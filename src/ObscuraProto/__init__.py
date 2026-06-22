"""
ObscuraProto high-level Python library.
"""

import asyncio  # Added for asyncio integration
import inspect

try:
    # This is the C++ extension module built by CMake.
    from . import _obscuraproto as _bindings
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
V1_0 = _bindings.V1_0
SUPPORTED_VERSIONS = _bindings.SUPPORTED_VERSIONS
ConnectionHdl = _bindings.ConnectionHdl


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
                print(
                    f"[ERROR] Failed to auto-unpack payload for OpCode {op_code_hex}. "
                    f"Check handler '{handler.__name__}' signature "
                    f"matches the payload structure. Details: {e}"
                )
                return  # Suppress further errors

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
            # We don't have opcode easily here, as it's extracted by C++ before passing PayloadReader
            print(
                f"[ERROR] Failed to auto-unpack request payload for handler '{handler.__name__}'. "
                f"Check that the handler signature matches the expected payload structure. Details: {e}"
            )
            # For request handlers, if unpacking fails, we must return an error payload
            # or allow the C++ layer to handle the exception. For now, a generic error.
            # A more robust solution might involve an error payload specific opcode.
            # The C++ will handle the Python exception, but returning a Payload is cleaner.
            error_payload = PayloadBuilder(0x0000).add_param(f"Error: {e}").build()
            return error_payload

        # Call the handler, expecting a Payload return
        response_payload = handler(**handler_kwargs)
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
    """

    def __init__(self):
        """Initializes the server, generating its long-term signing key."""
        self._long_term_key = _bindings.Crypto.generate_sign_keypair()
        self._server = _bindings.WsServer(self._long_term_key)

    @property
    def public_key(self):
        """The server's long-term public key, needed by clients to connect."""
        return self._long_term_key.public_key

    def start(self, port):
        """
        Starts the WebSocket server on the given port.
        This runs the server in a background thread.
        """
        print(f"[PY-SERVER] Starting on port {port}...")
        self._server.run(port)
        print("[PY-SERVER] Started.")

    def stop(self):
        """Stops the server."""
        print("[PY-SERVER] Stopping...")
        self._server.stop()
        print("[PY-SERVER] Stopped.")

    def send(self, hdl, payload):
        """Sends a payload to a specific client."""
        self._server.send(hdl, payload)

    async def async_request(self, hdl, payload) -> Payload:
        """Sends a request to a specific client and returns a future for the response."""
        return await asyncio.to_thread(self._server.sync_request, hdl, payload)

    def on_payload(self, opcode):
        """
        Decorator to register a handler for a specific opcode.

        The decorated function will be called with arguments unpacked from the
        payload based on type hints. If no type hints are provided, it will be
        called with `(hdl, payload)`.

        Example:
            @server.on_payload(0x1001)
            def handle_login(hdl, username: str, password: str, attempt: uint):
                print(f"Login attempt for '{username}'")
        """

        def decorator(handler):
            wrapper = _create_unpacking_handler(handler, receives_hdl_from_native=True)
            self._server.register_op_handler(opcode, wrapper)
            return handler

        return decorator

    def default_payload_handler(self, handler):
        """
        Decorator for the default handler, with auto-unpacking based on type hints.
        """
        wrapper = _create_unpacking_handler(handler, receives_hdl_from_native=True)
        self._server.set_default_payload_handler(wrapper)
        return handler

    def on_request(self, opcode):
        """
        Registers a handler for a specific opcode that expects a response.

        The decorated function will be called with ConnectionHdl (for the server)
        and arguments unpacked from the payload reader based on type hints.
        The handler must return a Payload object as a response.

        Example:
            @server.on_request(0x1002)
            def handle_sum_request(hdl: ConnectionHdl, a: int, b: int) -> Payload:
                result = a + b
                return PayloadBuilder(0x1003).add_param(result).build()
        """

        def decorator(handler):
            wrapper = _create_request_unpacking_handler(handler, receives_hdl_from_native=True)
            self._server.register_request_handler(opcode, wrapper)
            return handler

        return decorator


class Client:
    """
    An ObscuraProto WebSocket client.

    Wraps the C++ WsClient for a Pythonic interface with decorators.
    """

    def __init__(self, server_public_key):
        """
        Args:
            server_public_key: The public key of the server to connect to.
        """
        if not isinstance(server_public_key, _bindings.PublicKey):
            raise TypeError("server_public_key must be a PublicKey object.")

        key_view = _bindings.KeyPair()
        key_view.public_key = server_public_key
        self._client = _bindings.WsClient(key_view)

    def connect(self, uri):
        """Connects to the server at the given WebSocket URI (e.g., "ws://localhost:9002")."""
        print(f"[PY-CLIENT] Connecting to {uri}...")
        self._client.connect(uri)

    def disconnect(self):
        """Disconnects from the server."""
        self._client.disconnect()

    def send(self, payload):
        """Sends a payload to the server."""
        self._client.send(payload)

    async def async_request(self, payload) -> Payload:
        """Sends a request to the server and returns a future for the response."""
        return await asyncio.to_thread(self._client.sync_request, payload)

    def on_ready(self, handler):
        """Decorator to register a callback for when the client is connected and ready."""
        self._client.set_on_ready_callback(handler)
        return handler

    def on_disconnect(self, handler):
        """Decorator to register a callback for when the client disconnects."""
        self._client.set_on_disconnect_callback(handler)
        return handler

    def on_payload(self, opcode):
        """
        Decorator to register a handler for a specific opcode from the server.

        The decorated function will be called with arguments unpacked from the
        payload based on type hints. If no type hints are provided, it will be
        called with the raw `payload` object.

        Example:
            @client.on_payload(0x2001)
            def handle_message(author: str, message: str):
                print(f"{author}: {message}")
        """

        def decorator(handler):
            wrapper = _create_unpacking_handler(handler, receives_hdl_from_native=False)
            self._client.register_op_handler(opcode, wrapper)
            return handler

        return decorator

    def default_payload_handler(self, handler):
        """
        Decorator for the default handler, with auto-unpacking based on type hints.
        """
        wrapper = _create_unpacking_handler(handler, receives_hdl_from_native=False)
        self._client.set_default_payload_handler(wrapper)
        return handler

    def on_request(self, opcode):
        """
        Registers a handler for a specific opcode that expects a response.

        The decorated function will be called with ConnectionHdl (for the server)
        and arguments unpacked from the payload reader based on type hints.
        The handler must return a Payload object as a response.

        Example:
            @client.on_request(0x1002)
            def handle_sum_request(a: int, b: int) -> Payload:
                result = a + b
                return PayloadBuilder(0x1003).add_param(result).build()
        """

        def decorator(handler):
            wrapper = _create_request_unpacking_handler(handler, receives_hdl_from_native=False)
            self._client.register_request_handler(opcode, wrapper)
            return handler

        return decorator
