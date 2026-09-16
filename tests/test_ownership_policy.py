"""The ownership policy YAML, pinned to the canonical Harris NWOS legend.

Canonical source: the legend embedded in RDS-2025-0045's own product metadata
(``US_forest_ownership.tif.xml``, ArcGIS sync 2025-07-24), for Harris, Caputo &
Butler (2025), "Forest ownership in the conterminous United States circa 2022:
distribution of seven ownership types", doi:10.2737/RDS-2025-0045:

    0 Unknown Forest            4 Corporate/Other Private Forest   8 Local Forest
    1 Non-Forest                5 Tribal Forest
    2 Water                     6 Federal Forest
    3 Family Forest             7 State Forest

The seven ownership types are codes 3-8 plus the unknown-forest residual; 1 and
2 are land-cover classes, not owners, and are masked before FVS.
"""

import pytest

from pipeline.s3_management.owner_classes import load_ownership_policy

# Verbatim from US_forest_ownership.tif.xml (RDS-2025-0045), <idAbs> legend.
CANONICAL_HARRIS_LEGEND = {
    0: "Unknown Forest",
    1: "Non-Forest",
    2: "Water",
    3: "Family Forest",
    4: "Corporate/Other Private Forest",
    5: "Tribal Forest",
    6: "Federal Forest",
    7: "State Forest",
    8: "Local Forest",
}
CANONICAL_MASKED = {1, 2}  # land cover, not ownership

CANONICAL_TPO_GROUPS = {"Private", "Federal (NF)", "Other public"}  # config/tpo_targets.yaml


@pytest.fixture(scope="module")
def policy():
    return load_ownership_policy()


def test_policy_covers_exactly_the_canonical_legend(policy):
    assigned = {
        value
        for cls in policy["classes"].values()
        for value in cls["harris_values"]
    }
    assert assigned | set(policy["masked_harris_values"]) == set(CANONICAL_HARRIS_LEGEND)
    assert not assigned & set(policy["masked_harris_values"])  # one value, one meaning
    assert set(policy["masked_harris_values"]) == CANONICAL_MASKED


def test_every_class_label_names_its_canonical_harris_class(policy):
    # A class whose harris_values do not spell its canonical label is a mis-join.
    for cls in policy["classes"].values():
        for value in cls["harris_values"]:
            assert CANONICAL_HARRIS_LEGEND[value].lower().startswith(cls["label"].split()[0].lower()), (
                f"{cls['label']!r} claims Harris {value} = {CANONICAL_HARRIS_LEGEND[value]!r}"
            )


def test_every_tpo_group_is_one_the_tpo_budgets_express(policy):
    for cls in policy["classes"].values():
        assert cls["tpo_group"] in CANONICAL_TPO_GROUPS


def test_masked_values_are_excluded_from_the_fvs_pipeline(policy):
    from pipeline.s3_management.owner_classes import MASKED, harris_value_to_class

    for value in policy["masked_harris_values"]:
        assert harris_value_to_class(value) == MASKED


def test_policy_records_the_canonical_source(policy):
    text = policy_text()
    assert "RDS-2025-0045" in text
    assert "doi:10.2737/RDS-2025-0045" in text
    # The legend is verified against the product's own metadata, not folklore.
    assert "US_forest_ownership.tif.xml" in text


def policy_text() -> str:
    with open("config/ownership_policy.yaml") as fh:
        return fh.read()
