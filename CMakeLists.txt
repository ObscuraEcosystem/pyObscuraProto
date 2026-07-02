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
    GIT_TAG        v1.0
)
FetchContent_MakeAvailable(ObscuraProto)

# Find system libsodium and propagate to the obscuraproto target.
# Handles all platforms: Linux (standard paths), macOS (Homebrew /opt/homebrew & /usr/local),
# Windows (vcpkg / standard paths).
find_library(SODIUM_LIBRARY sodium
    HINTS
        /opt/homebrew/lib
        /usr/local/lib
        /usr/lib
        /usr/lib64
)
find_path(SODIUM_INCLUDE_DIR sodium.h
    HINTS
        /opt/homebrew/include
        /usr/local/include
        /usr/include
)
if(SODIUM_LIBRARY)
    target_link_libraries(obscuraproto PUBLIC "${SODIUM_LIBRARY}")
    message(STATUS "Found libsodium library: ${SODIUM_LIBRARY}")
endif()
if(SODIUM_INCLUDE_DIR)
    target_include_directories(obscuraproto PUBLIC "${SODIUM_INCLUDE_DIR}")
    message(STATUS "Found libsodium include: ${SODIUM_INCLUDE_DIR}")
endif()

pybind11_add_module(_obscuraproto src/ObscuraProto/bindings.cpp)
target_link_libraries(_obscuraproto PRIVATE obscuraproto)
