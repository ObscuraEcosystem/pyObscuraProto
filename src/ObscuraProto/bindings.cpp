#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/operators.h>
#include <pybind11/functional.h>
#include <pybind11/chrono.h>
#include <map>

#include <obscuraproto/config.hpp>
#include <obscuraproto/crypto.hpp>
#include <obscuraproto/handshake_messages.hpp>
#include <obscuraproto/keys.hpp>
#include <obscuraproto/packet.hpp>
#include <obscuraproto/rate_limiter.hpp>
#include <obscuraproto/secure_buffer.hpp>
#include <obscuraproto/stream.hpp>
#include <obscuraproto/session.hpp>
#include <obscuraproto/version.hpp>
#include <obscuraproto/ws_client.hpp>
#include <obscuraproto/ws_server.hpp>


namespace py = pybind11;
using namespace ObscuraProto;
using namespace ObscuraProto::net;

// Per https://github.com/pybind/pybind11/issues/1803
// PYBIND11_DECLARE_HOLDER_TYPE causes an error with an undefined
// variable if it's used with a templated type. websocketpp::connection_hdl
// is a using declaration for a std::weak_ptr. To bind it, we need to
// "trick" C++ into believing that it's a real type.
struct WsConnectionHdlWrapper {
    WsConnectionHdl hdl;
};

// Wrapper around std::future<Payload> for non-blocking async requests.
// The C++ async_request() methods return immediately (they set up a promise
// that is fulfilled from the websocket I/O thread when the RESPONSE arrives).
// Python code polls ready() on the event loop and only calls get() when ready,
// so no thread-pool thread is blocked for the request duration.
//
// NOTE: CppPayloadFuture is SINGLE-USE. Once get() has been called (after
// ready() returned true), the underlying std::future is consumed and the
// object must not be polled or re-used. Call ready()/get() exactly once.
class CppPayloadFuture {
public:
    explicit CppPayloadFuture(std::future<Payload> future) : future_(std::move(future)) {}

    // Poll without blocking.
    bool ready() const {
        return future_.wait_for(std::chrono::seconds(0)) == std::future_status::ready;
    }

    // Blocking get. Releases the GIL; returns immediately when called after ready().
    Payload get() {
        return future_.get();
    }

private:
    std::future<Payload> future_;
};


PYBIND11_MODULE(_obscuraproto, m) {
    m.doc() = "Python bindings for the ObscuraProto C++ library";

    // Exceptions — map ObscuraProto C++ exceptions to Python types.
    // Hierarchy in errors.hpp:
    //   Exception <- RuntimeError <- TimeoutError
    //   Exception <- LogicError <- InvalidArgument
    // pybind11's registered translators are tried in reverse registration
    // order and match by C++ catch-clause type, so InvalidArgument (a C++
    // subclass of LogicError) must be registered LAST: its own translator is
    // then tried before the LogicError translator and wins the typeid dispatch.
    // The registered Python types subclass the Python builtins, so base-class
    // catches (except ValueError, except RuntimeError, except TimeoutError)
    // also work on the Python side.
    py::register_exception<TimeoutError>(m, "TimeoutError", PyExc_TimeoutError);
    py::register_exception<LogicError>(m, "LogicError", PyExc_RuntimeError);
    py::register_exception<InvalidArgument>(m, "InvalidArgument", PyExc_ValueError);

    // Version
    m.attr("V1_0") = py::int_(Versions::V1_0);
    m.attr("V1_1") = py::int_(Versions::V1_1);
    m.attr("SUPPORTED_VERSIONS") = py::cast(SUPPORTED_VERSIONS);

    py::class_<VersionNegotiator>(m, "VersionNegotiator")
        .def_static("negotiate", &VersionNegotiator::negotiate);

    // Config
    py::class_<RateLimitConfig>(m, "RateLimitConfig")
        .def(py::init<>())
        .def_readwrite("enabled", &RateLimitConfig::enabled)
        .def_readwrite("messages_per_second", &RateLimitConfig::messages_per_second)
        .def_readwrite("burst_size", &RateLimitConfig::burst_size)
        .def_readwrite("handshake_attempts_per_minute", &RateLimitConfig::handshake_attempts_per_minute)
        .def_readwrite("connections_per_minute", &RateLimitConfig::connections_per_minute)
        .def_static("defaults", &RateLimitConfig::defaults);

    py::class_<ConnectionLimitConfig>(m, "ConnectionLimitConfig")
        .def(py::init<>())
        .def_readwrite("enabled", &ConnectionLimitConfig::enabled)
        .def_readwrite("max_per_ip", &ConnectionLimitConfig::max_per_ip)
        .def_readwrite("max_total", &ConnectionLimitConfig::max_total)
        .def_static("defaults", &ConnectionLimitConfig::defaults);

    py::class_<MessageLimitConfig>(m, "MessageLimitConfig")
        .def(py::init<>())
        .def_readwrite("enabled", &MessageLimitConfig::enabled)
        .def_readwrite("max_ws_frame_size", &MessageLimitConfig::max_ws_frame_size)
        .def_readwrite("max_decrypted_payload", &MessageLimitConfig::max_decrypted_payload)
        .def_static("defaults", &MessageLimitConfig::defaults);

    py::class_<TimeoutConfig>(m, "TimeoutConfig")
        .def(py::init<>())
        .def_readwrite("enabled", &TimeoutConfig::enabled)
        .def_readwrite("handshake_ms", &TimeoutConfig::handshake_ms)
        .def_readwrite("idle_ms", &TimeoutConfig::idle_ms)
        .def_readwrite("check_interval_ms", &TimeoutConfig::check_interval_ms)
        .def_readwrite("request_ms", &TimeoutConfig::request_ms)
        .def_static("defaults", &TimeoutConfig::defaults);

    py::class_<ReservedOpcodes>(m, "ReservedOpcodes")
        .def(py::init<>())
        .def_readwrite("RESPONSE", &ReservedOpcodes::RESPONSE)
        .def_readwrite("STREAM_START", &ReservedOpcodes::STREAM_START)
        .def_readwrite("STREAM_DATA", &ReservedOpcodes::STREAM_DATA)
        .def_readwrite("STREAM_END", &ReservedOpcodes::STREAM_END)
        .def_readwrite("STREAM_CANCEL", &ReservedOpcodes::STREAM_CANCEL)
        .def_static("defaults", &ReservedOpcodes::defaults);

    py::class_<Config>(m, "Config")
        .def(py::init<>())
        .def_readwrite("rate_limit", &Config::rate_limit)
        .def_readwrite("connection_limits", &Config::connection_limits)
        .def_readwrite("message_limits", &Config::message_limits)
        .def_readwrite("timeouts", &Config::timeouts)
        .def_readwrite("opcodes", &Config::opcodes)
        .def_readwrite("supported_versions", &Config::supported_versions)
        .def_static("from_yaml", &Config::from_yaml)
        .def_static("with_defaults", &Config::with_defaults);

    // RateLimiter — sliding-window/token-bucket rate enforcement. All methods
    // are synchronous and mutex-guarded (no I/O), so the GIL stays held.
    py::class_<RateLimiter>(m, "RateLimiter")
        .def(py::init<const RateLimitConfig&>(), py::arg("config"),
             "Construct a rate limiter from a RateLimitConfig.")
        .def("check_connection_rate", &RateLimiter::check_connection_rate, py::arg("ip"),
             "Returns True if the connection rate for this IP is within limits.")
        .def("record_connection", &RateLimiter::record_connection, py::arg("ip"),
             "Record a connection attempt for this IP.")
        .def("check_handshake_rate", &RateLimiter::check_handshake_rate, py::arg("ip"),
             "Returns True if the handshake rate for this IP is within limits.")
        .def("record_handshake", &RateLimiter::record_handshake, py::arg("ip"),
             "Record a handshake attempt for this IP.")
        .def("check_message_rate", &RateLimiter::check_message_rate, py::arg("conn_id"),
             "Returns True if the message rate for this connection is within limits.")
        .def("record_message", &RateLimiter::record_message, py::arg("conn_id"),
             "Record a message for this connection (consumes a token).")
        .def("check_active_connections", &RateLimiter::check_active_connections, py::arg("ip"),
             "Returns True if the active connection limit for this IP is not exceeded.")
        .def("register_connection", &RateLimiter::register_connection, py::arg("ip"),
             "Register a new connection for this IP and return its connection ID.")
        .def("unregister_connection", &RateLimiter::unregister_connection,
             py::arg("conn_id"), py::arg("ip"),
             "Unregister a connection, releasing its tokens and active slot.")
        .def("active_total", &RateLimiter::active_total,
             "Returns the total number of active connections.")
        .def("cleanup", &RateLimiter::cleanup,
             "Expire stale sliding-window timestamps and drop idle per-IP state.");

    // SecureBuffer — heap memory allocated via sodium_malloc, zeroed on clear()
    // (sodium_memzero) and on destruction. Data is exchanged with Python as
    // byte copies only; Python never receives a reference to the live buffer,
    // so the contents cannot be corrupted from the Python side.
    py::class_<SecureBuffer>(m, "SecureBuffer")
        .def(py::init<>(), "Construct an empty secure buffer.")
        .def(py::init<size_t>(), py::arg("size"),
             "Construct a secure buffer of the given size (zero-initialized).")
        .def("resize", &SecureBuffer::resize, py::arg("new_size"),
             "Resize the buffer, preserving as much data as fits.")
        .def("size", &SecureBuffer::size, "Returns the buffer size in bytes.")
        .def("empty", &SecureBuffer::empty, "Returns True if the buffer is empty.")
        .def("clear", &SecureBuffer::clear,
             "Securely zero (sodium_memzero) and free the buffer memory.")
        .def("to_bytes", [](const SecureBuffer &self) -> py::bytes {
            if (self.empty()) {
                return py::bytes("");
            }
            return py::bytes(reinterpret_cast<const char*>(self.data()), self.size());
        }, "Returns a copy of the buffer contents as bytes. Safe: Python never "
           "gets a reference to the internal memory.")
        .def("from_bytes", [](SecureBuffer &self, const std::string &data) {
            self.assign(reinterpret_cast<const uint8_t*>(data.data()), data.size());
        }, py::arg("data"),
           "Replace the buffer contents with a copy of the given bytes.")
        .def("__bytes__", [](const SecureBuffer &self) -> py::bytes {
            if (self.empty()) {
                return py::bytes("");
            }
            return py::bytes(reinterpret_cast<const char*>(self.data()), self.size());
        }, "Returns the buffer contents as bytes (copy).")
        .def("__len__", &SecureBuffer::size, "Returns the buffer size in bytes.");

    // Keys
    py::class_<PublicKey>(m, "PublicKey")
        .def(py::init<>())
        .def_readwrite("data", &PublicKey::data)
        .def("__eq__", [](const PublicKey &self, const PublicKey &other) {
            return self.data == other.data;
        })
        .def("__hash__", [](const PublicKey &self) {
            return py::hash(py::bytes(
                reinterpret_cast<const char*>(self.data.data()), self.data.size()));
        })
        .def("__repr__", [](const PublicKey &self) {
            return "<obscuraproto.PublicKey>";
        });

    py::class_<PrivateKey>(m, "PrivateKey")
        .def(py::init<>())
        .def_readwrite("data", &PrivateKey::data);

    py::class_<KeyPair>(m, "KeyPair")
        .def(py::init<>())
        .def_readwrite("public_key", &KeyPair::publicKey)
        .def_readwrite("private_key", &KeyPair::privateKey);
    
    py::class_<Signature>(m, "Signature")
        .def(py::init<>())
        .def_readwrite("data", &Signature::data);

    // Handshake Messages
    py::class_<ClientHello>(m, "ClientHello")
        .def(py::init<>())
        .def_readwrite("supported_versions", &ClientHello::supported_versions)
        .def_readwrite("ephemeral_pk", &ClientHello::ephemeral_pk)
        .def_readwrite("has_client_identity", &ClientHello::has_client_identity)
        .def_readwrite("identity_pk", &ClientHello::identity_pk)
        .def_readwrite("identity_sig", &ClientHello::identity_sig)
        .def("serialize", &ClientHello::serialize)
        .def_static("deserialize", &ClientHello::deserialize);

    py::class_<ServerHello>(m, "ServerHello")
        .def(py::init<>())
        .def_readwrite("selected_version", &ServerHello::selected_version)
        .def_readwrite("ephemeral_pk", &ServerHello::ephemeral_pk)
        .def_readwrite("signature", &ServerHello::signature)
        .def("serialize", &ServerHello::serialize)
        .def_static("deserialize", &ServerHello::deserialize);

    // Crypto
    py::class_<Crypto>(m, "Crypto")
        .def_static("init", &Crypto::init)
        .def_static("generate_kx_keypair", &Crypto::generate_kx_keypair)
        .def_static("generate_sign_keypair", &Crypto::generate_sign_keypair)
        .def_static("sign", &Crypto::sign)
        .def_static("verify", &Crypto::verify)
        .def_static("client_compute_session_keys", &Crypto::client_compute_session_keys)
        .def_static("server_compute_session_keys", &Crypto::server_compute_session_keys)
        .def_static("keypair_from_seed", [](const py::bytes &seed) -> KeyPair {
            // The seed bytes pointer is only valid for the duration of this call,
            // so it is consumed immediately inside the lambda body (never stored).
            char *ptr = nullptr;
            Py_ssize_t len = 0;
            if (PyBytes_AsStringAndSize(seed.ptr(), &ptr, &len) != 0) {
                throw py::error_already_set();
            }
            return Crypto::keypair_from_seed(
                reinterpret_cast<const uint8_t*>(ptr), static_cast<size_t>(len));
        }, py::arg("seed"),
           "Deterministically derives an Ed25519 key pair from a 32-byte seed "
           "(RFC 8032). Raises InvalidArgument (a ValueError) if the seed length "
           "is not exactly 32 bytes.")
        .def_static("derive_public_key", [](const py::bytes &private_key) -> PublicKey {
            char *ptr = nullptr;
            Py_ssize_t len = 0;
            if (PyBytes_AsStringAndSize(private_key.ptr(), &ptr, &len) != 0) {
                throw py::error_already_set();
            }
            return Crypto::derive_public_key(
                reinterpret_cast<const uint8_t*>(ptr), static_cast<size_t>(len));
        }, py::arg("private_key"),
           "Derives the Ed25519 public key from a 64-byte private key (seed || public). "
           "Raises InvalidArgument (a ValueError) if the private key length is not "
           "exactly 64 bytes.")
        .def_static("encrypt", &Crypto::encrypt)
        .def_static("decrypt", &Crypto::decrypt);
    
    py::class_<Crypto::SessionKeys>(m, "SessionKeys")
        .def(py::init<>())
        .def_readwrite("rx", &Crypto::SessionKeys::rx)
        .def_readwrite("tx", &Crypto::SessionKeys::tx);

    // Result struct returned by Crypto.decrypt. Registered so the existing
    // Crypto.decrypt binding is usable from Python.
    py::class_<Crypto::DecryptedResult>(m, "DecryptedResult")
        .def(py::init<>())
        .def_readwrite("payload", &Crypto::DecryptedResult::payload)
        .def_readwrite("counter", &Crypto::DecryptedResult::counter);

    // Packet
    py::class_<Payload>(m, "Payload")
        .def(py::init<>(), "Default constructor")
        .def_readwrite("op_code", &Payload::op_code, "The operation code.")
        .def_readwrite("parameters", &Payload::parameters, "The raw parameters data.")
        .def("serialize", &Payload::serialize, "Serializes the payload into a single byte vector.")
        .def_static("deserialize", &Payload::deserialize, "Deserializes a byte vector into a Payload object.");

    py::class_<PayloadBuilder>(m, "PayloadBuilder")
        .def(py::init<Payload::OpCode>(), "Constructor that takes an opcode.")
        .def("add_param", py::overload_cast<const byte_vector&>(&PayloadBuilder::add_param))
        .def("add_param", py::overload_cast<const std::string&>(&PayloadBuilder::add_param))
        .def("add_param", py::overload_cast<const char*>(&PayloadBuilder::add_param))
        .def("add_param", py::overload_cast<bool>(&PayloadBuilder::add_param))
        .def("add_param", py::overload_cast<int8_t>(&PayloadBuilder::add_param))
        .def("add_param", py::overload_cast<uint8_t>(&PayloadBuilder::add_param))
        .def("add_param", py::overload_cast<int16_t>(&PayloadBuilder::add_param))
        .def("add_param", py::overload_cast<uint16_t>(&PayloadBuilder::add_param))
        .def("add_param", py::overload_cast<int32_t>(&PayloadBuilder::add_param))
        .def("add_param", py::overload_cast<uint32_t>(&PayloadBuilder::add_param))
        .def("add_param", py::overload_cast<int64_t>(&PayloadBuilder::add_param))
        .def("add_param", py::overload_cast<uint64_t>(&PayloadBuilder::add_param))
        .def("add_param", py::overload_cast<float>(&PayloadBuilder::add_param))
        .def("add_param", py::overload_cast<double>(&PayloadBuilder::add_param))
        .def("build", &PayloadBuilder::build, "Builds the final Payload object.");

    py::class_<PayloadReader>(m, "PayloadReader")
        .def(py::init<const Payload&>(), "Constructor that takes a payload to read from.")
        .def("has_more", &PayloadReader::has_more, "Returns true if there are more parameters to read.")
        .def("peek_next_param_size", &PayloadReader::peek_next_param_size, "Returns the size of the next parameter in bytes without advancing the reader.")
        .def("read_string", &PayloadReader::read_param<std::string>, "Reads a string parameter.")
        .def("read_bytes", &PayloadReader::read_param<byte_vector>, "Reads a bytes parameter.")
        .def("read_bool", &PayloadReader::read_param<bool>, "Reads a boolean parameter.")
        .def("read_int", [](PayloadReader &self) -> int64_t {
            size_t size = self.peek_next_param_size();
            switch (size) {
                case 1:
                    return self.read_param<int8_t>();
                case 2:
                    return self.read_param<int16_t>();
                case 4:
                    return self.read_param<int32_t>();
                case 8:
                    return self.read_param<int64_t>();
                default:
                    throw std::runtime_error("Invalid size for a signed integer parameter: " + std::to_string(size));
            }
        }, "Reads a signed integer, determining its size from the packet.")
        .def("read_uint", [](PayloadReader &self) -> uint64_t {
            size_t size = self.peek_next_param_size();
            switch (size) {
                case 1:
                    return self.read_param<uint8_t>();
                case 2:
                    return self.read_param<uint16_t>();
                case 4:
                    return self.read_param<uint32_t>();
                case 8:
                    return self.read_param<uint64_t>();
                default:
                    throw std::runtime_error("Invalid size for an unsigned integer parameter: " + std::to_string(size));
            }
        }, "Reads an unsigned integer, determining its size from the packet.")
        .def("read_float", [](PayloadReader &self) -> double {
            size_t size = self.peek_next_param_size();
            switch (size) {
                case 4:
                    return self.read_param<float>();
                case 8:
                    return self.read_param<double>();
                default:
                    throw std::runtime_error("Invalid size for a float/double parameter: " + std::to_string(size));
            }
        }, "Reads a float or double, determining its size from the packet and returning it as a double.");
    
    // Stream
    py::class_<Stream, std::shared_ptr<Stream>>(m, "CppStream")
        .def(py::init<uint32_t, std::function<void(Payload)>, std::optional<Payload::OpCode>>(),
             py::arg("stream_id"), py::arg("send_fn"), py::arg("op_code") = std::nullopt,
             "Constructor (stream_id, send_fn, op_code) - for testing. Use start_stream() in production.")
        .def("get_stream_id", &Stream::get_stream_id,
             "Returns the stream's unique ID.")
        .def("get_op_code", &Stream::get_op_code,
             "Returns the stream's optional op_code.")
        .def("write", [](Stream &self, const std::string &data) {
            byte_vector vec(reinterpret_cast<const uint8_t*>(data.data()),
                            reinterpret_cast<const uint8_t*>(data.data() + data.size()));
            self.write(vec);
        }, py::call_guard<py::gil_scoped_release>(),
             "Send a data chunk over the stream.")
        .def("end", &Stream::end, py::call_guard<py::gil_scoped_release>(),
             "Signal end of outgoing data (half-close).")
        .def("cancel", &Stream::cancel, py::call_guard<py::gil_scoped_release>(),
             "Abort the stream immediately.")
        .def("set_data_handler", &Stream::set_data_handler,
             "Register callback for incoming data chunks.")
        .def("set_end_handler", &Stream::set_end_handler,
             "Register callback for remote end-of-stream.")
        .def("set_cancel_handler", &Stream::set_cancel_handler,
             "Register callback for remote stream cancel.");

    // Session
    py::enum_<Role>(m, "Role")
        .value("CLIENT", Role::CLIENT)
        .value("SERVER", Role::SERVER)
        .export_values();

    // WS Connection Handle
    py::class_<WsConnectionHdlWrapper>(m, "ConnectionHdl")
        .def(py::init<>())
        .def("__repr__", [](const WsConnectionHdlWrapper &self) {
            return "<obscuraproto.ConnectionHdl at " + std::to_string(reinterpret_cast<uintptr_t>(&self)) + ">";
        });

    // CppPayloadFuture — pollable wrapper around std::future<Payload>
    py::class_<CppPayloadFuture>(m, "CppPayloadFuture")
        .def("ready", &CppPayloadFuture::ready,
             "Returns True if the response is available (non-blocking).")
        .def("get", &CppPayloadFuture::get, py::call_guard<py::gil_scoped_release>(),
             "Blocks until the response is available and returns the response Payload.")
        .def("__repr__", [](const CppPayloadFuture &self) {
            return std::string("<obscuraproto.CppPayloadFuture ready=") +
                   (self.ready() ? "True" : "False") + ">";
        });

    // WS Server
    py::class_<WsServerWrapper, std::shared_ptr<WsServerWrapper>>(m, "WsServer")
        .def(py::init<KeyPair, Config>(), py::arg("keypair"), py::arg("config") = Config::with_defaults())
        .def("run", &WsServerWrapper::run, py::call_guard<py::gil_scoped_release>(),
             "Runs the server in a background thread.")
        .def("stop", &WsServerWrapper::stop, py::call_guard<py::gil_scoped_release>(),
             "Stops the server thread.")
        .def("send", [](WsServerWrapper &self, WsConnectionHdlWrapper hdl, const Payload &payload) {
            self.send(hdl.hdl, payload);
        }, "Send a payload to a specific client.")
        .def("sync_request", [](WsServerWrapper &self, WsConnectionHdlWrapper hdl, const Payload &payload) {
            return self.sync_request(hdl.hdl, payload);
        }, py::call_guard<py::gil_scoped_release>(), "Sends a request to a client and returns a response.")
        .def("async_request", [](WsServerWrapper &self, WsConnectionHdlWrapper hdl, const Payload &payload) {
            return CppPayloadFuture(self.async_request(hdl.hdl, payload));
        }, py::call_guard<py::gil_scoped_release>(),
             "Sends a request to a client and returns a pollable CppPayloadFuture that completes when the response arrives.")
        .def("async_request", [](WsServerWrapper &self, WsConnectionHdlWrapper hdl, const Payload &payload, uint32_t timeout_ms) {
            return CppPayloadFuture(self.async_request(hdl.hdl, payload, timeout_ms));
        }, py::call_guard<py::gil_scoped_release>(), py::arg("hdl"), py::arg("payload"), py::arg("timeout_ms"),
             "Sends a request to a client and returns a pollable CppPayloadFuture, bounded by a timeout in milliseconds (0 = unlimited).")
        // Internal bridge to C++ — called from Python decorators only
        .def("register_op_handler", [](WsServerWrapper &self, Payload::OpCode op_code, 
                                        std::function<void(WsConnectionHdlWrapper, Payload)> callback) {
            self.register_op_handler(op_code, [callback](WsConnectionHdl hdl, Payload payload) {
                callback(WsConnectionHdlWrapper{hdl}, payload);
            });
        }, "Register a handler for a specific opcode.")
        .def("register_request_handler", [](WsServerWrapper &self, Payload::OpCode op_code, 
                                            std::function<Payload(WsConnectionHdlWrapper, PayloadReader&)> callback) {
            self.register_request_handler(op_code, [callback](WsConnectionHdl hdl, PayloadReader& reader) {
                return callback(WsConnectionHdlWrapper{hdl}, reader);
            });
        }, "Register a request handler for a specific opcode, expecting a Payload response.")
        // Intentionally NOT bound — response is returned from request handlers via return value
        .def("set_default_payload_handler", [](WsServerWrapper &self,
                                                std::function<void(WsConnectionHdlWrapper, Payload)> callback) {
            self.set_default_payload_handler([callback](WsConnectionHdl hdl, Payload payload) {
                callback(WsConnectionHdlWrapper{hdl}, payload);
            });
        }, "Sets the default handler for unhandled opcodes.")
        .def("start_stream", [](WsServerWrapper &self, WsConnectionHdlWrapper hdl) {
            return self.start_stream(hdl.hdl);
        }, py::call_guard<py::gil_scoped_release>(),
             py::arg("hdl"),
             "Start a new outgoing stream to a specific client.")
        .def("start_stream", [](WsServerWrapper &self, WsConnectionHdlWrapper hdl, Payload::OpCode stream_op_code) {
            return self.start_stream(hdl.hdl, stream_op_code);
        }, py::call_guard<py::gil_scoped_release>(),
             py::arg("hdl"), py::arg("stream_op_code"),
             "Start a new outgoing stream to a specific client with a specific op_code.")
        .def("register_incoming_stream_handler", [](WsServerWrapper &self,
            std::function<void(std::shared_ptr<Stream>)> callback) {
            self.register_incoming_stream_handler(std::move(callback));
        }, "Register a handler for incoming streams from clients.")
        .def("register_stream_handler", [](WsServerWrapper &self, Payload::OpCode op_code,
            std::function<void(std::shared_ptr<Stream>)> callback) {
            self.register_stream_handler(op_code, std::move(callback));
        }, py::arg("op_code"), py::arg("callback"),
             "Register a handler for incoming authenticated streams with a specific op_code.")
        .def("register_anon_stream_handler", [](WsServerWrapper &self, Payload::OpCode op_code,
            std::function<void(std::shared_ptr<Stream>)> callback) {
            self.register_anon_stream_handler(op_code, std::move(callback));
        }, py::arg("op_code"), py::arg("callback"),
             "Register a handler for incoming anonymous streams with a specific op_code.")

        // --- Anonymous Sessions ---
        .def("send_anonymous", [](WsServerWrapper &self, WsConnectionHdlWrapper hdl, const Payload &payload) {
            self.send_anonymous(hdl.hdl, payload);
        }, "Send a payload to an anonymous session.")
        .def("register_anon_op_handler", [](WsServerWrapper &self, Payload::OpCode op_code,
                                            std::function<void(WsConnectionHdlWrapper, Payload)> callback) {
            self.register_anon_op_handler(op_code, [callback](WsConnectionHdl hdl, Payload payload) {
                callback(WsConnectionHdlWrapper{hdl}, payload);
            });
        }, "Register a handler for a specific opcode on anonymous sessions.")
        .def("register_anon_request_handler", [](WsServerWrapper &self, Payload::OpCode op_code,
                                                 std::function<Payload(WsConnectionHdlWrapper, PayloadReader&)> callback) {
            self.register_anon_request_handler(op_code, [callback](WsConnectionHdl hdl, PayloadReader& reader) {
                return callback(WsConnectionHdlWrapper{hdl}, reader);
            });
        }, "Register a request handler for anonymous sessions.")
        .def("set_anon_default_payload_handler", [](WsServerWrapper &self,
                                                     std::function<void(WsConnectionHdlWrapper, Payload)> callback) {
            self.set_anon_default_payload_handler([callback](WsConnectionHdl hdl, Payload payload) {
                callback(WsConnectionHdlWrapper{hdl}, payload);
            });
        }, "Sets the default handler for unhandled opcodes from anonymous clients.")

        // --- Client Identity ---
        .def("set_client_identity_handler", [](WsServerWrapper &self,
                                                std::function<bool(WsConnectionHdlWrapper, PublicKey)> callback) {
            self.set_client_identity_handler([callback](WsConnectionHdl hdl, PublicKey pk) {
                return callback(WsConnectionHdlWrapper{hdl}, pk);
            });
        }, "Sets a handler that is called when a client authenticates with an identity key.")
        .def("get_client_identity", [](WsServerWrapper &self, WsConnectionHdlWrapper hdl) {
            return self.get_client_identity(hdl.hdl);
        }, "Gets the verified identity public key for an authenticated session.")
        .def("send_to_identity", &WsServerWrapper::send_to_identity,
             "Send a payload to a specific client identified by their public key.")
        .def("sync_request_to_identity", &WsServerWrapper::sync_request_to_identity,
             py::call_guard<py::gil_scoped_release>(),
             "Sends a synchronous request to a specific client identified by their public key.")
        .def("async_request_to_identity", [](WsServerWrapper &self, const PublicKey &identity_pk, const Payload &payload) {
            return CppPayloadFuture(self.async_request_to_identity(identity_pk, payload));
        }, py::call_guard<py::gil_scoped_release>(),
             "Sends a request to a specific client identified by their public key and returns a pollable CppPayloadFuture.")
        .def("set_on_open_callback", [](WsServerWrapper &self,
                                        std::function<void(WsConnectionHdlWrapper)> callback) {
            self.set_on_open_callback([callback](WsConnectionHdl hdl) {
                callback(WsConnectionHdlWrapper{hdl});
            });
        }, "Registers a callback called when a new WebSocket connection is opened.")
        .def("set_on_close_callback", [](WsServerWrapper &self,
                                         std::function<void(WsConnectionHdlWrapper)> callback) {
            self.set_on_close_callback([callback](WsConnectionHdl hdl) {
                callback(WsConnectionHdlWrapper{hdl});
            });
        }, "Registers a callback called when a WebSocket connection is closed.");

    // WS Client
    py::class_<WsClientWrapper, std::shared_ptr<WsClientWrapper>>(m, "WsClient")
        .def(py::init<KeyPair, Config>(), py::arg("keypair"), py::arg("config") = Config::with_defaults())
        .def("connect", &WsClientWrapper::connect, py::call_guard<py::gil_scoped_release>(),
             "Connects to the server and performs handshake.")
        .def("disconnect", &WsClientWrapper::disconnect, py::call_guard<py::gil_scoped_release>(),
             "Disconnects from the server.")
        .def("send", &WsClientWrapper::send, py::call_guard<py::gil_scoped_release>(),
             "Sends a payload to the server.")
        .def("sync_request", [](WsClientWrapper &self, const Payload &payload) {
            return self.sync_request(payload);
        }, py::call_guard<py::gil_scoped_release>(), "Sends a request to the server and returns a response.")
        .def("sync_request", [](WsClientWrapper &self, const Payload &payload, uint32_t timeout_ms) {
            return self.sync_request(payload, timeout_ms);
        }, py::call_guard<py::gil_scoped_release>(), py::arg("payload"), py::arg("timeout_ms"),
             "Sends a request to the server and returns a response, bounded by a timeout in milliseconds (0 = unlimited).")
        .def("async_request", [](WsClientWrapper &self, const Payload &payload) {
            return CppPayloadFuture(self.async_request(payload));
        }, py::call_guard<py::gil_scoped_release>(),
             "Sends a request to the server and returns a pollable CppPayloadFuture that completes when the response arrives.")
        .def("async_request", [](WsClientWrapper &self, const Payload &payload, uint32_t timeout_ms) {
            return CppPayloadFuture(self.async_request(payload, timeout_ms));
        }, py::call_guard<py::gil_scoped_release>(), py::arg("payload"), py::arg("timeout_ms"),
             "Sends a request to the server and returns a pollable CppPayloadFuture, bounded by a timeout in milliseconds (0 = unlimited).")
        .def("set_client_identity", &WsClientWrapper::set_client_identity,
             "Sets the client's Ed25519 identity keypair for authentication.")
        .def("set_on_ready_callback", &WsClientWrapper::set_on_ready_callback)
        .def("set_on_disconnect_callback", &WsClientWrapper::set_on_disconnect_callback)
        // Internal bridge to C++ — called from Python decorators only
        .def("register_op_handler", &WsClientWrapper::register_op_handler)
        .def("register_request_handler", &WsClientWrapper::register_request_handler, "Register a request handler for a specific opcode, expecting a Payload response.")
        // Intentionally NOT bound — response is returned from request handlers via return value
        .def("set_default_payload_handler", &WsClientWrapper::set_default_payload_handler)
        .def("start_stream", py::overload_cast<>(&WsClientWrapper::start_stream),
             py::call_guard<py::gil_scoped_release>(),
             "Start a new outgoing stream to the server.")
        .def("start_stream", py::overload_cast<Payload::OpCode>(&WsClientWrapper::start_stream),
             py::call_guard<py::gil_scoped_release>(),
             py::arg("stream_op_code"),
             "Start a new outgoing stream to the server with a specific op_code.")
        .def("register_incoming_stream_handler", &WsClientWrapper::register_incoming_stream_handler,
             "Register a handler for incoming streams from the server.")
        .def("register_stream_handler", &WsClientWrapper::register_stream_handler,
             py::arg("op_code"), py::arg("callback"),
             "Register a handler for incoming streams with a specific op_code.");
}
