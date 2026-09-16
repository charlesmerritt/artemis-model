"""
Tests for pipeline/s5_imagery/feature_sources.py.

``AlphaEarthEmbeddings.features_at`` needs Earth Engine and is not called here.
What is: the constructor's guards, the declared shape of the contract, and that
the protocol is structural — an offline stand-in satisfies it without inheriting
anything, which is the property the correction tests rely on.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s5_imagery import feature_sources as fs


def test_alphaearth_declares_the_64_embedding_bands():
    source = fs.AlphaEarthEmbeddings(year=2022)
    assert source.feature_names == [f"A{i:02d}" for i in range(64)]
    assert len(set(source.feature_names)) == 64


def test_alphaearth_description_records_the_vintage_and_scale():
    source = fs.AlphaEarthEmbeddings(year=2022)
    assert "2022" in source.description
    assert "10 m" in source.description
    assert fs.EMBEDDING_COLLECTION in source.description


def test_alphaearth_rejects_years_before_coverage():
    with pytest.raises(ValueError, match="start in 2017"):
        fs.AlphaEarthEmbeddings(year=2012)


def test_alphaearth_rejects_a_nonsense_chunk_size():
    with pytest.raises(ValueError, match="chunk_size"):
        fs.AlphaEarthEmbeddings(year=2022, chunk_size=0)


def test_alphaearth_rejects_a_nonsense_scale():
    with pytest.raises(ValueError, match="scale_m"):
        fs.AlphaEarthEmbeddings(year=2022, scale_m=0)


def test_alphaearth_satisfies_the_protocol():
    assert isinstance(fs.AlphaEarthEmbeddings(year=2022), fs.FeatureSource)


def test_the_protocol_is_structural():
    """Any object with the three members qualifies — no base class, no Earth Engine."""

    class Offline:
        feature_names = ["a", "b"]
        description = "fixture"

        def features_at(self, lonlats):
            return np.zeros((len(list(lonlats)), 2))

    assert isinstance(Offline(), fs.FeatureSource)
