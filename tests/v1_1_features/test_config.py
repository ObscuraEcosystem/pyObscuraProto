"""
Config.supported_versions test for C++ v1.1 features.
"""

import os
import sys

import pytest

src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
sys.path.insert(0, src_dir)

try:
    import ObscuraProto as op
except ImportError as e:
    pytest.fail(f"Could not import ObscuraProto: {e}", pytrace=False)


@pytest.fixture(scope="module")
def crypto_init():
    op.Crypto.init()


def test_config_supported_versions(crypto_init):
    cfg = op.Config()
    versions = cfg.supported_versions
    assert isinstance(versions, list)

    cfg.supported_versions = [op.V1_1, op.V1_0]
    assert op.V1_1 in cfg.supported_versions
    assert op.V1_0 in cfg.supported_versions
    assert len(cfg.supported_versions) == 2

    cfg.supported_versions = [op.V1_1]
    assert cfg.supported_versions == [op.V1_1]

    cfg.supported_versions = []
    assert cfg.supported_versions == []

    cfg2 = op.Config.with_defaults()
    assert hasattr(cfg2, "supported_versions")
    assert isinstance(cfg2.supported_versions, list)
