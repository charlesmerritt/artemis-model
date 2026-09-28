"""The regime library is written for Harris owner classes and nothing else."""

import pytest

from pipeline.s4_fvs.regime_library import load_library, validate_library

RULES = {"offsets_must_be_multiples_of": 5, "max_year_offset": 50}


def test_regime_written_for_an_owner_harris_does_not_have_is_rejected():
    library = {"constraints": RULES,
               "regimes": {"land_trust_thin": {"owner_classes": ["ngo"], "operations": []}}}
    with pytest.raises(ValueError, match="ngo"):
        validate_library(library)


def test_regime_that_names_no_owner_class_is_rejected():
    library = {"constraints": RULES, "regimes": {"orphan": {"operations": []}}}
    with pytest.raises(ValueError, match="owner_classes"):
        validate_library(library)


def test_shipped_library_names_only_harris_owner_classes():
    harris = {"family", "corporate", "tribal", "federal", "state", "local", "unknown"}
    for regime in load_library()["regimes"].values():
        assert set(regime["owner_classes"]) <= harris
