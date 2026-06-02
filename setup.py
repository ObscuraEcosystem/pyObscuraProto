import os
import re
import sys
import subprocess
import platform

from setuptools import setup, Extension, find_packages
from setuptools.command.build_ext import build_ext
from setuptools.command.install import install


class CMakeExtension(Extension):
    def __init__(self, name, sourcedir=''):
        Extension.__init__(self, name, sources=[])
        self.sourcedir = os.path.abspath(sourcedir)


class CMakeBuild(build_ext):
    def run(self):
        try:
            subprocess.check_call(['cmake', '--version'])
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise RuntimeError(
                "CMake must be installed to build the following extensions: " +
                ", ".join(e.name for e in self.extensions)
            )

        for ext in self.extensions:
            self.build_extension(ext)

    def build_extension(self, ext):
        extdir = os.path.abspath(
            os.path.dirname(self.get_ext_fullpath(ext.name))
        )
        # required for Windows VC++ to find the debug libraries
        debug = os.environ.get("DEBUG", "0") == "1" or self.debug

        cfg = "Debug" if debug else "Release"
        
        # CMake lets you override the output directory and name of the extension.
        # We specify the output directory to be the same as the python package
        # so that the built extension is placed alongside the python modules
        # and can be imported directly.
        cmake_args = [
            f"-DCMAKE_LIBRARY_OUTPUT_DIRECTORY={extdir}",
            f"-DPYTHON_EXECUTABLE={sys.executable}",
            f"-DCMAKE_BUILD_TYPE={cfg}",  # Release or Debug
        ]
        
        # Assuming the project's root CMakeLists.txt is in the same directory as setup.py
        # and it builds the _obscuraproto target.
        build_args = ['--config', cfg]

        if platform.system() == "Windows":
            cmake_args += ["-T", "ClangCL" if os.environ.get("CC") == "clang" else "MSVC"]
            if self.compiler.vcruntime:
                cmake_args += [f"-DCMAKE_GENERATOR_TOOLSET=v{self.compiler.vcruntime}"]
            build_args += ["--", "/m"]
        else:
            build_args += ["--", "-j4"] # Parallel builds

        env = os.environ.copy()
        env['CXXFLAGS'] = f'{env.get("CXXFLAGS", "")} -DVERSION_INFO="{self.distribution.get_version()}"'
        
        # Create build directory for CMake out-of-source build
        build_directory = os.path.join(self.build_temp, ext.name)
        os.makedirs(build_directory, exist_ok=True)

        print("-" * 10, "Running CMake prepare", "-" * 40)
        subprocess.check_call(
            ["cmake", ext.sourcedir] + cmake_args, cwd=build_directory, env=env
        )
        print("-" * 10, "Running CMake build", "-" * 40)
        subprocess.check_call(
            ["cmake", "--build", "."] + build_args, cwd=build_directory
        )


setup(
    name='pyObscuraProto',
    version='1.0', # Placeholder version
    author='Kretov Artem',
    author_email='20kretovartem000@gmail.com',
    description='A Python wrapper for ObscuraProto',
    long_description='A Python wrapper for ObscuraProto, providing high-level WebSocket server and client functionalities.',
    packages=find_packages(where='src'),
    package_dir={'': 'src'},
    ext_modules=[CMakeExtension('ObscuraProto._obscuraproto', sourcedir='.')],
    cmdclass={'build_ext': CMakeBuild},
    install_requires=['pybind11>=2.11.1'], # Specify minimum pybind11 version
    setup_requires=['cmake'], # Add cmake as a build dependency
    zip_safe=False,
)
