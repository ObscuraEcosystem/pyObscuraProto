"""
Cross-language payload compatibility tests.

Verifies that PayloadBuilder and PayloadReader work correctly for
cross-language round-trips (C++ <-> Python and Python <-> Python).

C++ PayloadBuilder overloads (in declaration order that matters for
pybind11 overload resolution):
    byte_vector, std::string, const char*, bool,
    int8_t, uint8_t, int16_t, uint16_t, int32_t, uint32_t, int64_t, uint64_t,
    float, double

Note: Python float always maps to the C++ float (4-byte) overload
because pybind11 tries overloads in declaration order and float is
declared before double. The reader (read_float) handles both sizes.
"""

import os
import sys

import pytest

# Add the src directory to the path to find the ObscuraProto package
src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, src_dir)

try:
    from ObscuraProto import _bindings

    PayloadBuilder = _bindings.PayloadBuilder
    PayloadReader = _bindings.PayloadReader
    Payload = _bindings.Payload
except ImportError as e:
    pytest.fail(
        f"Could not import ObscuraProto bindings: {e}. Searched in: {sys.path}",
        pytrace=False,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bytes_to_list(b: bytes) -> list:
    """Convert a bytes object to a list of ints for comparison with read_bytes()."""
    return list(b)


# Relative tolerance for float32 comparisons (23-bit mantissa -> ~7 decimal digits)
_F32_REL_TOL = 1e-6
# Relative tolerance for float64 comparisons (52-bit mantissa -> ~15 decimal digits)
_F64_REL_TOL = 1e-14


# ---------------------------------------------------------------------------
# Test 1: Python round-trip for parameter types
# ---------------------------------------------------------------------------


class TestRoundTripAllTypes:
    """Build a payload with every type, read them back, verify values match."""

    def test_basic_types_round_trip(self):
        """One of each type, read back with type-specific size checks."""
        builder = PayloadBuilder(0x1001)
        builder.add_param(-120)  # int8
        builder.add_param(250)  # uint8
        builder.add_param(-32000)  # int16
        builder.add_param(65000)  # uint16
        builder.add_param(-2_000_000_000)  # int32
        builder.add_param(4_000_000_000)  # uint32
        builder.add_param(-9_000_000_000_000_000_000)  # int64
        builder.add_param(18_000_000_000_000_000_000)  # uint64
        builder.add_param(3.14159)  # float (4-byte via pybind11)
        builder.add_param(True)  # bool
        builder.add_param(False)  # bool
        builder.add_param("hello cross-language")  # string
        builder.add_param(b"\x00\x01\xff\xfe")  # bytes

        payload = builder.build()
        reader = PayloadReader(payload)

        # int8
        assert reader.peek_next_param_size() == 1
        assert reader.read_int() == -120

        # uint8
        assert reader.peek_next_param_size() == 1
        assert reader.read_uint() == 250

        # int16
        assert reader.peek_next_param_size() == 2
        assert reader.read_int() == -32000

        # uint16
        assert reader.peek_next_param_size() == 2
        assert reader.read_uint() == 65000

        # int32
        assert reader.peek_next_param_size() == 4
        assert reader.read_int() == -2_000_000_000

        # uint32
        assert reader.peek_next_param_size() == 4
        assert reader.read_uint() == 4_000_000_000

        # int64
        assert reader.peek_next_param_size() == 8
        assert reader.read_int() == -9_000_000_000_000_000_000

        # uint64
        assert reader.peek_next_param_size() == 8
        assert reader.read_uint() == 18_000_000_000_000_000_000

        # float (stored as 4-byte float by pybind11)
        assert reader.peek_next_param_size() == 4
        assert reader.read_float() == pytest.approx(3.14159, rel=_F32_REL_TOL)

        # bool: True
        assert reader.peek_next_param_size() == 1
        assert reader.read_bool() is True

        # bool: False
        assert reader.peek_next_param_size() == 1
        assert reader.read_bool() is False

        # string
        assert reader.read_string() == "hello cross-language"

        # bytes
        assert reader.read_bytes() == _bytes_to_list(b"\x00\x01\xff\xfe")

        assert not reader.has_more()

    def test_serialize_deserialize_round_trip(self):
        """Build, serialize, deserialize, re-read -- simulate C++ deserialization."""
        builder = PayloadBuilder(0xABCD)
        builder.add_param(42)
        builder.add_param("serialize-test")
        builder.add_param(b"binary-data")

        original = builder.build()
        serialized = original.serialize()
        restored = Payload.deserialize(serialized)

        assert restored.op_code == 0xABCD

        reader = PayloadReader(restored)
        assert reader.read_int() == 42
        assert reader.read_string() == "serialize-test"
        assert reader.read_bytes() == _bytes_to_list(b"binary-data")
        assert not reader.has_more()

    def test_string_types(self):
        """Strings (including unicode and edge values) round-trip."""
        builder = PayloadBuilder(0x3001)
        builder.add_param("")
        builder.add_param("a")
        builder.add_param("hello world")
        builder.add_param("привет мир")  # Unicode Cyrillic
        builder.add_param("\x00")  # embedded null
        builder.add_param("\n\t\r")  # whitespace
        builder.add_param(" ")  # single space

        payload = builder.build()
        reader = PayloadReader(payload)

        assert reader.read_string() == ""
        assert reader.read_string() == "a"
        assert reader.read_string() == "hello world"
        assert reader.read_string() == "привет мир"
        assert reader.read_string() == "\x00"
        assert reader.read_string() == "\n\t\r"
        assert reader.read_string() == " "
        assert not reader.has_more()

    def test_bytes_types(self):
        """Bytes round-trip."""
        builder = PayloadBuilder(0x3002)
        builder.add_param(b"")
        builder.add_param(b"\x00")
        builder.add_param(b"\x00\x01\xff\xfe")
        builder.add_param(b"hello bytes")
        builder.add_param(bytes(range(256)))  # all byte values 0..255

        payload = builder.build()
        reader = PayloadReader(payload)

        assert reader.read_bytes() == _bytes_to_list(b"")
        assert reader.read_bytes() == _bytes_to_list(b"\x00")
        assert reader.read_bytes() == _bytes_to_list(b"\x00\x01\xff\xfe")
        assert reader.read_bytes() == _bytes_to_list(b"hello bytes")
        assert reader.read_bytes() == list(range(256))
        assert not reader.has_more()

    def test_float_values(self):
        """Float values round-trip through 4-byte float (pybind11 default)."""
        builder = PayloadBuilder(0x4001)
        builder.add_param(0.0)
        builder.add_param(-1.0)
        builder.add_param(3.141592653589793)
        builder.add_param(1e-10)
        builder.add_param(1e20)

        payload = builder.build()
        reader = PayloadReader(payload)

        # pybind11 always routes Python float -> C++ float (4-byte) overload
        # due to declaration order, so precision is limited to ~7 decimal digits.
        assert reader.peek_next_param_size() == 4
        assert reader.read_float() == pytest.approx(0.0, rel=_F32_REL_TOL)

        assert reader.peek_next_param_size() == 4
        assert reader.read_float() == pytest.approx(-1.0, rel=_F32_REL_TOL)

        assert reader.peek_next_param_size() == 4
        assert reader.read_float() == pytest.approx(3.141592653589793, rel=_F32_REL_TOL)

        assert reader.peek_next_param_size() == 4
        assert reader.read_float() == pytest.approx(1e-10, rel=_F32_REL_TOL)

        assert reader.peek_next_param_size() == 4
        assert reader.read_float() == pytest.approx(1e20, rel=_F32_REL_TOL)

        assert not reader.has_more()

    def test_bool_values(self):
        """Boolean values round-trip."""
        builder = PayloadBuilder(0x5001)
        builder.add_param(True)
        builder.add_param(False)
        builder.add_param(True)

        payload = builder.build()
        reader = PayloadReader(payload)

        assert reader.peek_next_param_size() == 1
        assert reader.read_bool() is True

        assert reader.peek_next_param_size() == 1
        assert reader.read_bool() is False

        assert reader.peek_next_param_size() == 1
        assert reader.read_bool() is True

        assert not reader.has_more()


# ---------------------------------------------------------------------------
# Test 2: Edge cases for all types
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Boundary / edge-case values for every supported type."""

    def test_integer_edge_cases(self):
        """Boundary values for all integer types.

        Note: pybind11 overload resolution always picks the smallest C++
        type that fits the Python value. So:
          - 0 always maps to int8_t (1 byte)
          - The actual stored type for values like 0, 1, 127 is int8_t,
            not the "intended" type from the spec.
          - The round-trip value is still correct regardless of storage type.
        """
        builder = PayloadBuilder(0x2001)

        # Append all edge case values as specified.
        # Order: int8, uint8, int16, uint16, int32, uint32, int64, uint64,
        # each with (min, 0, max).
        #
        # pybindli will store each value in the smallest C++ type that fits.
        # This means 0 is always int8_t, and small positive values may be
        # int8_t or uint8_t rather than the "intended" wider type.
        # The reader read_int/read_uint dispatch on the actual stored size,
        # so the value round-trips correctly regardless.

        # int8: -128, 0, 127
        builder.add_param(-128)  # -> int8_t   (1 byte)
        builder.add_param(0)  # -> int8_t   (1 byte)
        builder.add_param(127)  # -> int8_t   (1 byte)

        # uint8: 0, 255
        builder.add_param(255)  # -> uint8_t  (1 byte, 0 not added separately)

        # int16: -32768, 0, 32767
        builder.add_param(-32768)  # -> int16_t  (2 bytes)
        builder.add_param(32767)  # -> int16_t  (2 bytes, 0 not added separately)

        # uint16: 0, 65535
        builder.add_param(65535)  # -> uint16_t (2 bytes, 0 not added separately)

        # int32: -2147483648, 0, 2147483647
        builder.add_param(-2147483648)  # -> int32_t  (4 bytes)
        builder.add_param(2147483647)  # -> int32_t  (4 bytes, 0 not added)

        # uint32: 0, 4294967295
        builder.add_param(4294967295)  # -> uint32_t (4 bytes, 0 not added)

        # int64: -9223372036854775808, 0, 9223372036854775807
        builder.add_param(-9223372036854775808)  # -> int64_t  (8 bytes)
        builder.add_param(9223372036854775807)  # -> int64_t  (8 bytes, 0 not added)

        # uint64: 0, 18446744073709551615
        builder.add_param(18446744073709551615)  # -> uint64_t (8 bytes, 0 not added)

        payload = builder.build()
        reader = PayloadReader(payload)

        # ---------------------------------------------------------------
        # int8 edges: all stored as int8_t (1 byte)
        # ---------------------------------------------------------------
        assert reader.peek_next_param_size() == 1
        assert reader.read_int() == -128
        assert reader.peek_next_param_size() == 1
        assert reader.read_int() == 0
        assert reader.peek_next_param_size() == 1
        assert reader.read_int() == 127

        # ---------------------------------------------------------------
        # uint8 edges: 255 stored as uint8_t (1 byte)
        # ---------------------------------------------------------------
        assert reader.peek_next_param_size() == 1
        assert reader.read_uint() == 255

        # ---------------------------------------------------------------
        # int16 edges
        # ---------------------------------------------------------------
        assert reader.peek_next_param_size() == 2
        assert reader.read_int() == -32768
        assert reader.peek_next_param_size() == 2
        assert reader.read_int() == 32767

        # ---------------------------------------------------------------
        # uint16 edges
        # ---------------------------------------------------------------
        assert reader.peek_next_param_size() == 2
        assert reader.read_uint() == 65535

        # ---------------------------------------------------------------
        # int32 edges
        # ---------------------------------------------------------------
        assert reader.peek_next_param_size() == 4
        assert reader.read_int() == -2147483648
        assert reader.peek_next_param_size() == 4
        assert reader.read_int() == 2147483647

        # ---------------------------------------------------------------
        # uint32 edges
        # ---------------------------------------------------------------
        assert reader.peek_next_param_size() == 4
        assert reader.read_uint() == 4294967295

        # ---------------------------------------------------------------
        # int64 edges
        # ---------------------------------------------------------------
        assert reader.peek_next_param_size() == 8
        assert reader.read_int() == -9223372036854775808
        assert reader.peek_next_param_size() == 8
        assert reader.read_int() == 9223372036854775807

        # ---------------------------------------------------------------
        # uint64 edges
        # ---------------------------------------------------------------
        assert reader.peek_next_param_size() == 8
        assert reader.read_uint() == 18446744073709551615

        assert not reader.has_more()

    def test_string_edge_cases(self):
        """String edge cases: empty, very long, null, whitespace, unicode."""
        builder = PayloadBuilder(0x2002)
        builder.add_param("")  # empty string
        builder.add_param("a" * 10000)  # very long string
        builder.add_param("\x00")  # single null byte
        builder.add_param("\n\t\r")  # control characters
        builder.add_param(" ")  # single space
        builder.add_param("ascii only")
        builder.add_param("Unicode: \u00e9\u00e0\u00fc\u00f1")  # Latin-1 Supplement
        builder.add_param("Emoji-free text")  # intentionally no emoji

        payload = builder.build()
        reader = PayloadReader(payload)

        assert reader.read_string() == ""
        assert reader.read_string() == "a" * 10000
        assert reader.read_string() == "\x00"
        assert reader.read_string() == "\n\t\r"
        assert reader.read_string() == " "
        assert reader.read_string() == "ascii only"
        assert reader.read_string() == "Unicode: \u00e9\u00e0\u00fc\u00f1"
        assert reader.read_string() == "Emoji-free text"
        assert not reader.has_more()

    def test_bytes_edge_cases(self):
        """Bytes edge cases: empty, special sequences, large payload."""
        builder = PayloadBuilder(0x2003)
        builder.add_param(b"")  # empty bytes
        builder.add_param(b"\x00")  # single null
        builder.add_param(b"\x00\x01\xff")  # mix of zero, one, max byte
        builder.add_param(b"\x00" * 1000)  # many consecutive nulls
        builder.add_param(b"\xff" * 500)  # many consecutive 0xFF
        builder.add_param(bytes(range(128)))  # all 7-bit values

        payload = builder.build()
        reader = PayloadReader(payload)

        assert reader.read_bytes() == _bytes_to_list(b"")
        assert reader.read_bytes() == _bytes_to_list(b"\x00")
        assert reader.read_bytes() == _bytes_to_list(b"\x00\x01\xff")
        assert reader.read_bytes() == _bytes_to_list(b"\x00" * 1000)
        assert reader.read_bytes() == _bytes_to_list(b"\xff" * 500)
        assert reader.read_bytes() == list(range(128))
        assert not reader.has_more()

    def test_float_edge_cases(self):
        """Float edge cases (all go through 4-byte float in pybind11)."""
        builder = PayloadBuilder(0x2004)

        # Normal values
        builder.add_param(0.0)
        builder.add_param(-0.0)
        builder.add_param(1.0)
        builder.add_param(-1.0)
        builder.add_param(3.14159)
        builder.add_param(1e-10)
        builder.add_param(-1e-10)

        payload = builder.build()
        reader = PayloadReader(payload)

        # All stored as 4-byte float (pybind11 picks float before double)
        assert reader.peek_next_param_size() == 4
        assert reader.read_float() == pytest.approx(0.0, rel=_F32_REL_TOL)

        assert reader.peek_next_param_size() == 4
        assert reader.read_float() == pytest.approx(-0.0, rel=_F32_REL_TOL)

        assert reader.peek_next_param_size() == 4
        assert reader.read_float() == pytest.approx(1.0, rel=_F32_REL_TOL)

        assert reader.peek_next_param_size() == 4
        assert reader.read_float() == pytest.approx(-1.0, rel=_F32_REL_TOL)

        assert reader.peek_next_param_size() == 4
        assert reader.read_float() == pytest.approx(3.14159, rel=_F32_REL_TOL)

        assert reader.peek_next_param_size() == 4
        assert reader.read_float() == pytest.approx(1e-10, rel=_F32_REL_TOL)

        assert reader.peek_next_param_size() == 4
        assert reader.read_float() == pytest.approx(-1e-10, rel=_F32_REL_TOL)

        assert not reader.has_more()


# ---------------------------------------------------------------------------
# Test 3: Multiple params of mixed types in sequence
# ---------------------------------------------------------------------------


class TestMixedTypes:
    """Mixed-type sequences -- the typical usage pattern in real protocols."""

    def test_ten_params_mixed(self):
        """10+ params of different types read in strict order."""
        builder = PayloadBuilder(0x3001)
        # Mix of int, string, bytes, bool, float in various sizes
        builder.add_param(42)  #  1: int8 (fits in 1-byte signed)
        builder.add_param("alpha")  #  2: string
        builder.add_param(b"\xca\xfe")  #  3: bytes
        builder.add_param(True)  #  4: bool
        builder.add_param(-1)  #  5: int8 (signed -1)
        builder.add_param(3.14)  #  6: float (4-byte)
        builder.add_param(0)  #  7: int8 (zero)
        builder.add_param("beta")  #  8: string
        builder.add_param(65535)  #  9: uint16
        builder.add_param(False)  # 10: bool
        builder.add_param(-32768)  # 11: int16
        builder.add_param(b"\x00")  # 12: bytes
        builder.add_param(2.71828)  # 13: float (4-byte)
        builder.add_param("gamma")  # 14: string
        builder.add_param(2147483647)  # 15: int32

        payload = builder.build()
        reader = PayloadReader(payload)

        #  1
        assert reader.peek_next_param_size() == 1
        assert reader.read_int() == 42
        #  2
        assert reader.read_string() == "alpha"
        #  3
        assert reader.read_bytes() == _bytes_to_list(b"\xca\xfe")
        #  4
        assert reader.peek_next_param_size() == 1
        assert reader.read_bool() is True
        #  5
        assert reader.peek_next_param_size() == 1
        assert reader.read_int() == -1
        #  6
        assert reader.peek_next_param_size() == 4
        assert reader.read_float() == pytest.approx(3.14, rel=_F32_REL_TOL)
        #  7
        assert reader.peek_next_param_size() == 1
        assert reader.read_int() == 0
        #  8
        assert reader.read_string() == "beta"
        #  9
        assert reader.peek_next_param_size() == 2
        assert reader.read_uint() == 65535
        # 10
        assert reader.peek_next_param_size() == 1
        assert reader.read_bool() is False
        # 11
        assert reader.peek_next_param_size() == 2
        assert reader.read_int() == -32768
        # 12
        assert reader.read_bytes() == _bytes_to_list(b"\x00")
        # 13
        assert reader.peek_next_param_size() == 4
        assert reader.read_float() == pytest.approx(2.71828, rel=_F32_REL_TOL)
        # 14
        assert reader.read_string() == "gamma"
        # 15
        assert reader.peek_next_param_size() == 4
        assert reader.read_int() == 2147483647

        assert not reader.has_more()

    def test_mixed_with_string_before_bytes(self):
        """String followed by bytes -- disambiguates overload resolution."""
        builder = PayloadBuilder(0x3002)
        builder.add_param("text")
        builder.add_param(b"binary")
        builder.add_param(True)
        builder.add_param(b"more binary")
        builder.add_param("more text")

        payload = builder.build()
        reader = PayloadReader(payload)

        assert reader.read_string() == "text"
        assert reader.read_bytes() == _bytes_to_list(b"binary")
        assert reader.read_bool() is True
        assert reader.read_bytes() == _bytes_to_list(b"more binary")
        assert reader.read_string() == "more text"
        assert not reader.has_more()


# ---------------------------------------------------------------------------
# Test 4: Empty payload
# ---------------------------------------------------------------------------


class TestEmptyPayload:
    """Empty payload (no params) round-trip."""

    def test_empty_payload_no_params(self):
        """Create Payload with no params, verify empty."""
        builder = PayloadBuilder(0x0000)
        payload = builder.build()

        assert payload.op_code == 0x0000
        assert len(payload.parameters) == 0

        reader = PayloadReader(payload)
        assert not reader.has_more()

    def test_empty_payload_serialize_deserialize(self):
        """Serialize and deserialize an empty payload."""
        builder = PayloadBuilder(0x0000)
        payload = builder.build()
        serialized = payload.serialize()
        restored = Payload.deserialize(serialized)

        assert restored.op_code == 0x0000
        assert len(restored.parameters) == 0

        reader = PayloadReader(restored)
        assert not reader.has_more()

    def test_empty_payload_nonzero_opcode(self):
        """Empty payload with non-zero op_code preserves op_code."""
        builder = PayloadBuilder(0xFFFF)
        payload = builder.build()

        assert payload.op_code == 0xFFFF
        assert len(payload.parameters) == 0

        reader = PayloadReader(payload)
        assert not reader.has_more()

    def test_empty_payload_peek_raises(self):
        """peek_next_param_size on empty payload should raise."""
        builder = PayloadBuilder(0x0000)
        payload = builder.build()
        reader = PayloadReader(payload)

        assert not reader.has_more()
        with pytest.raises(Exception):
            reader.peek_next_param_size()


# ---------------------------------------------------------------------------
# Additional: verify that all opcode values survive round-trip
# ---------------------------------------------------------------------------


class TestOpCodePreservation:
    """OpCode is preserved through serialization/deserialization."""

    @pytest.mark.parametrize("opcode", [0x0000, 0x0001, 0x00FF, 0x1001, 0xABCD, 0xFFFF])
    def test_opcode_preserved(self, opcode):
        """OpCode is preserved after build and after serialize/deserialize."""
        builder = PayloadBuilder(opcode)
        payload = builder.build()
        assert payload.op_code == opcode

        serialized = payload.serialize()
        restored = Payload.deserialize(serialized)
        assert restored.op_code == opcode


# ---------------------------------------------------------------------------
# Additional: type interchangeability (signed <-> unsigned)
# ---------------------------------------------------------------------------


class TestTypeInterchangeability:
    """Reading a signed parameter as unsigned and vice-versa."""

    def test_read_signed_as_unsigned(self):
        """uint8 value 255 read as signed int gives -1 (two's complement)."""
        builder = PayloadBuilder(0x6001)
        builder.add_param(255)  # stored as uint8_t
        payload = builder.build()
        reader = PayloadReader(payload)

        assert reader.peek_next_param_size() == 1
        assert reader.read_int() == -1

    def test_read_unsigned_as_signed(self):
        """int8 value -1 read as unsigned gives 255 (two's complement)."""
        builder = PayloadBuilder(0x6002)
        builder.add_param(-1)  # stored as int8_t
        payload = builder.build()
        reader = PayloadReader(payload)

        assert reader.peek_next_param_size() == 1
        assert reader.read_uint() == 255
