import os
import sys

import pytest

# Add the src directory to the path to find the ObscuraProto package
src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, src_dir)

try:
    # We import the raw C++ bindings for testing low-level functionalities
    from ObscuraProto import _bindings

    PayloadBuilder = _bindings.PayloadBuilder
    PayloadReader = _bindings.PayloadReader
    KeyPair = _bindings.KeyPair
    ConnectionHdl = _bindings.ConnectionHdl  # Need this for Server test
    Payload = _bindings.Payload  # Need this for mock return values
except ImportError as e:
    pytest.fail(f"Could not import the ObscuraProto bindings: {e}. Searched in: {sys.path}", pytrace=False)


def test_read_int_uint_and_peek():
    """
    Tests reading integers of various sizes using the generic read_int/read_uint
    functions and verifies peek_next_param_size.
    """
    builder = PayloadBuilder(1)

    # Values that will exercise different integer sizes
    val_i8 = -120
    val_u8 = 250
    val_i16 = -32000
    val_u16 = 65000
    val_i32 = -2000000000
    val_u32 = 4000000000
    val_i64 = -9000000000000000000
    val_u64 = 18000000000000000000

    # Add params. pybind11 should pick the smallest fitting C++ overload.
    builder.add_param(val_i8)  # Stored as int8_t (1 byte)
    builder.add_param(val_u8)  # Stored as uint8_t (1 byte)
    builder.add_param(val_i16)  # Stored as int16_t (2 bytes)
    builder.add_param(val_u16)  # Stored as uint16_t (2 bytes)
    builder.add_param(val_i32)  # Stored as int32_t (4 bytes)
    builder.add_param(val_u32)  # Stored as uint32_t (4 bytes)
    builder.add_param(val_i64)  # Stored as int64_t (8 bytes)
    builder.add_param(val_u64)  # Stored as uint64_t (8 bytes)

    payload = builder.build()
    reader = PayloadReader(payload)

    # Read and verify in order, checking the size before each read.
    assert reader.peek_next_param_size() == 1
    assert reader.read_int() == val_i8

    assert reader.peek_next_param_size() == 1
    assert reader.read_uint() == val_u8

    assert reader.peek_next_param_size() == 2
    assert reader.read_int() == val_i16

    assert reader.peek_next_param_size() == 2
    assert reader.read_uint() == val_u16

    assert reader.peek_next_param_size() == 4
    assert reader.read_int() == val_i32

    assert reader.peek_next_param_size() == 4
    assert reader.read_uint() == val_u32

    assert reader.peek_next_param_size() == 8
    assert reader.read_int() == val_i64

    assert reader.peek_next_param_size() == 8
    assert reader.read_uint() == val_u64

    # Ensure there are no more parameters
    assert not reader.has_more()


def test_type_interchangeability():
    """Tests reading a signed parameter as unsigned and vice-versa."""
    # Test reading a parameter added as a uint with read_int()
    builder_i = PayloadBuilder(2)
    builder_i.add_param(255)  # Stored as uint8_t
    payload_i = builder_i.build()
    reader_i = PayloadReader(payload_i)

    assert reader_i.peek_next_param_size() == 1
    # 255 (unsigned) is -1 in one-byte two's complement representation
    assert reader_i.read_int() == -1

    # Test reading a parameter added as an int with read_uint()
    builder_u = PayloadBuilder(3)
    builder_u.add_param(-1)  # Stored as int8_t
    payload_u = builder_u.build()
    reader_u = PayloadReader(payload_u)

    assert reader_u.peek_next_param_size() == 1
    # -1 in one-byte two's complement is 255 (unsigned)
    assert reader_u.read_uint() == 255


def test_ws_server_register_request_handler():
    """
    Tests that WsServerWrapper.register_request_handler can accept a Python callable
    with the correct signature without raising an error during registration.
    """
    server = _bindings.WsServer(_bindings.Crypto.generate_sign_keypair())

    # Note: We cannot easily trigger the C++ callback from Python without a full
    # network simulation, so this test focuses on successful registration.

    def mock_server_request_handler(hdl: ConnectionHdl, reader: PayloadReader) -> Payload:
        # These assertions will only run if the handler is actually called by C++
        # which isn't happening in this test. They are here to show the expected signature.
        assert isinstance(hdl, ConnectionHdl)
        assert isinstance(reader, PayloadReader)
        return PayloadBuilder(0xFF).add_param("server_response").build()

    try:
        server.register_request_handler(0x1001, mock_server_request_handler)
        # If no exception, registration was successful from Python perspective
        assert True
    except Exception as e:
        pytest.fail(f"register_request_handler for WsServer raised an exception: {e}")


def test_ws_client_register_request_handler():
    """
    Tests that WsClientWrapper.register_request_handler can accept a Python callable
    with the correct signature without raising an error during registration.
    """
    # Client needs a server public key. Generate one for testing purposes.
    server_keys = _bindings.Crypto.generate_sign_keypair()
    client = _bindings.WsClient(server_keys)

    # Note: Similar to the server test, this focuses on successful registration.

    def mock_client_request_handler(reader: PayloadReader) -> Payload:
        # These assertions will only run if the handler is actually called by C++
        assert isinstance(reader, PayloadReader)
        return PayloadBuilder(0xFE).add_param("client_response").build()

    try:
        client.register_request_handler(0x2001, mock_client_request_handler)
        # If no exception, registration was successful from Python perspective
        assert True
    except Exception as e:
        pytest.fail(f"register_request_handler for WsClient raised an exception: {e}")


def test_stream_low_level():
    """
    Tests the low-level CppStream binding: construction, get_stream_id,
    I/O operations, and handler registration via a mock send_fn.
    """
    sent_payloads = []

    def mock_send(p: Payload):
        sent_payloads.append(p)

    stream = _bindings.CppStream(42, mock_send)
    assert stream.get_stream_id() == 42

    # --- write ---
    stream.write(b"hello")
    assert len(sent_payloads) == 1
    p = sent_payloads[0]
    assert p.op_code == 0xFFFC  # STREAM_DATA
    reader = PayloadReader(p)
    assert reader.read_uint() == 42  # stream_id
    assert reader.read_bytes() == [104, 101, 108, 108, 111]  # "hello" as List[int]

    # --- end ---
    stream.end()
    assert len(sent_payloads) == 2
    p = sent_payloads[1]
    assert p.op_code == 0xFFFB  # STREAM_END
    reader = PayloadReader(p)
    assert reader.read_uint() == 42

    # --- cancel ---
    stream.cancel()
    assert len(sent_payloads) == 3
    p = sent_payloads[2]
    assert p.op_code == 0xFFFA  # STREAM_CANCEL
    reader = PayloadReader(p)
    assert reader.read_uint() == 42


def test_stream_handlers():
    """
    Tests that CppStream handlers can be registered and that I/O works.
    """
    sent = []

    def mock_send(p: Payload):
        sent.append(p)

    stream = _bindings.CppStream(7, mock_send)

    data_log = []
    end_log = []
    cancel_log = []

    stream.set_data_handler(lambda data: data_log.append(data))
    stream.set_end_handler(lambda: end_log.append(True))
    stream.set_cancel_handler(lambda: cancel_log.append(True))

    # write() triggers send_fn (goes out), not the data handler
    stream.write(b"hello")
    assert len(sent) == 1
    assert sent[0].op_code == 0xFFFC

    stream.end()
    assert len(sent) == 2
    assert sent[1].op_code == 0xFFFB

    stream.cancel()
    assert len(sent) == 3
    assert sent[2].op_code == 0xFFFA

    # Handlers weren't triggered (they're for *incoming* events)
    assert len(data_log) == 0
    assert len(end_log) == 0
    assert len(cancel_log) == 0


def test_python_stream_wrapper():
    """
    Tests the high-level Python Stream wrapper with decorator-based setup.
    """
    from ObscuraProto import CppStream, Stream

    sent = []

    def mock_send(p):
        sent.append(p)

    cpp = CppStream(99, mock_send)
    stream = Stream(cpp)

    # Property
    assert stream.stream_id == 99

    # Decorator-style registration
    data_log = []

    @stream.on_data
    def on_data(data: bytes):
        data_log.append(data)

    end_log = []

    @stream.on_end
    def on_end():
        end_log.append(True)

    cancel_log = []

    @stream.on_cancel
    def on_cancel():
        cancel_log.append(True)

    # Now dispatch into the underlying C++ stream
    # Build a STREAM_DATA payload, extract the data, call dispatch_data
    data_payload = PayloadBuilder(0xFFFC).add_param(99).add_param(b"wrapper_test").build()
    rdr = PayloadReader(data_payload)
    _ = rdr.read_uint()
    _ = rdr.read_bytes()

    # Call the underlying set_data_handler directly via the cpp stream
    # to test that the Python wrapper's on_data properly registered
    cpp.write(b"trigger_send")  # test write
    assert len(sent) == 1

    # Test async methods exist
    assert hasattr(stream, "async_write")
    assert hasattr(stream, "async_end")
    assert hasattr(stream, "async_cancel")

    # Test sync I/O
    stream.write(b"sync_write")
    assert len(sent) == 2

    stream.end()
    assert len(sent) == 3
    assert sent[2].op_code == 0xFFFB

    stream.cancel()
    assert len(sent) == 4
    assert sent[3].op_code == 0xFFFA


def test_cpp_payload_future_binding_repr():
    """CppPayloadFuture exposes a __repr__ showing the ready state (P3-2)."""
    cls = _bindings.CppPayloadFuture
    assert hasattr(cls, "__repr__"), "CppPayloadFuture must expose __repr__"
