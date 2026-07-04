cmake_minimum_required(VERSION 3.14)
project(ObscuraProto LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

set(BUILD_TESTING OFF CACHE BOOL "" FORCE)

include(FetchContent)

FetchContent_Declare(
    pybind11
    GIT_REPOSITORY https://github.com/pybind/pybind11.git
    GIT_TAG        v2.11.1
)
FetchContent_MakeAvailable(pybind11)

FetchContent_Declare(
    ObscuraProto
    GIT_REPOSITORY https://github.com/ObscuraEcosystem/ObscuraProto.git
    GIT_TAG        v1.0.1
)
FetchContent_MakeAvailable(ObscuraProto)

pybind11_add_module(_obscuraproto src/ObscuraProto/bindings.cpp)
target_link_libraries(_obscuraproto PRIVATE obscuraproto)
