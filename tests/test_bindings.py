import sys
import os
import pytest

# Add the src directory to the path to find the ObscuraProto package
src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, src_dir)

try:
    # We import the raw C++ bindings for testing low-level functionalities
    from ObscuraProto import _bindings
    PayloadBuilder = _bindings.PayloadBuilder
    PayloadReader = _bindings.PayloadReader
    KeyPair = _bindings.KeyPair
    ConnectionHdl = _bindings.ConnectionHdl # Need this for Server test
    Payload = _bindings.Payload # Need this for mock return values
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
    builder.add_param(val_i8)   # Stored as int8_t (1 byte)
    builder.add_param(val_u8)   # Stored as uint8_t (1 byte)
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
    
    # Mock ConnectionHdl and PayloadReader for signature
    # Note: We cannot easily trigger the C++ callback from Python without a full
    # network simulation, so this test focuses on successful registration.
    mock_hdl = ConnectionHdl() # Placeholder, actual value from C++
    mock_payload = PayloadBuilder(0x00).build()
    mock_reader = PayloadReader(mock_payload) # Placeholder, actual value from C++

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

    # Mock PayloadReader for signature
    # Note: Similar to the server test, this focuses on successful registration.
    mock_payload = PayloadBuilder(0x00).build()
    mock_reader = PayloadReader(mock_payload) # Placeholder, actual value from C++

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