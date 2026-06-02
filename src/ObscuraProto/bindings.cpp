#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/operators.h>
#include <pybind11/functional.h>
#include <pybind11/chrono.h>
#include <map>

#include <obscuraproto/crypto.hpp>
#include <obscuraproto/handshake_messages.hpp>
#include <obscuraproto/keys.hpp>
#include <obscuraproto/packet.hpp>
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


PYBIND11_MODULE(_obscuraproto, m) {
    m.doc() = "Python bindings for the ObscuraProto C++ library";

    // Version
    m.attr("V1_0") = py::int_(Versions::V1_0);
    m.attr("SUPPORTED_VERSIONS") = py::cast(SUPPORTED_VERSIONS);

    py::class_<VersionNegotiator>(m, "VersionNegotiator")
        .def_static("negotiate", &VersionNegotiator::negotiate);

    // Keys
    py::class_<PublicKey>(m, "PublicKey")
        .def(py::init<>())
        .def_readwrite("data", &PublicKey::data);

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
        .def_static("encrypt", &Crypto::encrypt)
        .def_static("decrypt", &Crypto::decrypt);
    
    py::class_<Crypto::SessionKeys>(m, "SessionKeys")
        .def(py::init<>())
        .def_readwrite("rx", &Crypto::SessionKeys::rx)
        .def_readwrite("tx", &Crypto::SessionKeys::tx);

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
    
    // Session
    py::enum_<Role>(m, "Role")
        .value("CLIENT", Role::CLIENT)
        .value("SERVER", Role::SERVER)
        .export_values();

    // WS Connection Handle
    py::class_<WsConnectionHdlWrapper>(m, "ConnectionHdl")
        .def(py::init<>())
        .def("__repr__", [](const WsConnectionHdlWrapper &self) {
            return "<obscuraproto.ConnectionHdl>";
        });

    // WS Server
    py::class_<WsServerWrapper>(m, "WsServer")
        .def(py::init<KeyPair>())
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
        .def("set_default_payload_handler", [](WsServerWrapper &self,
                                                std::function<void(WsConnectionHdlWrapper, Payload)> callback) {
            self.set_default_payload_handler([callback](WsConnectionHdl hdl, Payload payload) {
                callback(WsConnectionHdlWrapper{hdl}, payload);
            });
        }, "Sets the default handler for unhandled opcodes.");

    // WS Client
    py::class_<WsClientWrapper>(m, "WsClient")
        .def(py::init<KeyPair>())
        .def("connect", &WsClientWrapper::connect, py::call_guard<py::gil_scoped_release>(),
             "Connects to the server and performs handshake.")
        .def("disconnect", &WsClientWrapper::disconnect, py::call_guard<py::gil_scoped_release>(),
             "Disconnects from the server.")
        .def("send", &WsClientWrapper::send, py::call_guard<py::gil_scoped_release>(),
             "Sends a payload to the server.")
        .def("sync_request", [](WsClientWrapper &self, const Payload &payload) {
            return self.sync_request(payload);
        }, py::call_guard<py::gil_scoped_release>(), "Sends a request to the server and returns a response.")
        .def("set_on_ready_callback", &WsClientWrapper::set_on_ready_callback)
        .def("set_on_disconnect_callback", &WsClientWrapper::set_on_disconnect_callback)
        .def("register_op_handler", &WsClientWrapper::register_op_handler)
        .def("register_request_handler", &WsClientWrapper::register_request_handler, "Register a request handler for a specific opcode, expecting a Payload response.")
        .def("set_default_payload_handler", &WsClientWrapper::set_default_payload_handler);
}
