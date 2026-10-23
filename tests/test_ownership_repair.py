"""Ownership repair: forest the improved TreeMap carries that NWOS gives no owner."""

import numpy as np

from pipeline.s1_initial_state.ownership_repair import (
    NON_FOREST,
    UNKNOWN_FOREST,
    WATER,
    OwnershipProvenance as P,
    OwnershipRepairScope,
    repair_ownership,
)

FAMILY, CORPORATE, STATE = 3, 4, 7


def run(nwos, forest, added=None, scope=OwnershipRepairScope.ALL_FOREST, reach=5):
    nwos = np.array(nwos, dtype=np.uint8)
    forest = np.array(forest, dtype=bool)
    added = np.zeros_like(forest) if added is None else np.array(added, dtype=bool)
    return repair_ownership(nwos, forest, added, scope=scope, max_distance_px=reach)


def test_forest_called_non_forest_takes_the_nearest_known_owner():
    owner, prov = run([[FAMILY, NON_FOREST, NON_FOREST, NON_FOREST, CORPORATE]],
                      [[1, 1, 1, 1, 1]])
    assert owner.tolist() == [[FAMILY, FAMILY, FAMILY, CORPORATE, CORPORATE]]
    assert prov.tolist() == [[P.PUBLISHED, P.IMPUTED, P.IMPUTED, P.IMPUTED, P.PUBLISHED]]


def test_water_under_forest_is_imputed_too():
    owner, _ = run([[STATE, WATER]], [[1, 1]])
    assert owner.tolist() == [[STATE, STATE]]


def test_no_known_owner_within_reach_becomes_unknown_forest():
    owner, prov = run([[FAMILY, NON_FOREST, NON_FOREST, NON_FOREST]], [[1, 1, 1, 1]], reach=2)
    assert owner.tolist() == [[FAMILY, FAMILY, FAMILY, UNKNOWN_FOREST]]
    assert prov[0, 3] == P.UNRESOLVED


def test_no_known_owner_anywhere_becomes_unknown_forest():
    owner, prov = run([[NON_FOREST, NON_FOREST]], [[1, 1]])
    assert owner.tolist() == [[UNKNOWN_FOREST, UNKNOWN_FOREST]]
    assert prov.tolist() == [[P.UNRESOLVED, P.UNRESOLVED]]


def test_nwos_unknown_forest_is_already_forest_and_is_kept():
    owner, prov = run([[FAMILY, UNKNOWN_FOREST]], [[1, 1]])
    assert owner.tolist() == [[FAMILY, UNKNOWN_FOREST]]
    assert prov.tolist() == [[P.PUBLISHED, P.PUBLISHED]]


def test_pixels_the_improved_treemap_calls_non_forest_are_untouched():
    owner, prov = run([[FAMILY, NON_FOREST, WATER]], [[1, 0, 0]])
    assert owner.tolist() == [[FAMILY, NON_FOREST, WATER]]
    assert prov.tolist() == [[P.PUBLISHED, P.NOT_FOREST, P.NOT_FOREST]]


def test_added_back_scope_leaves_published_forest_as_nwos_has_it():
    # Pixel 1 is published TreeMap forest that NWOS calls non-forest; pixel 2 was added back.
    owner, prov = run([[FAMILY, NON_FOREST, NON_FOREST]], [[1, 1, 1]], added=[[0, 0, 1]],
                      scope=OwnershipRepairScope.ADDED_BACK)
    assert owner.tolist() == [[FAMILY, NON_FOREST, FAMILY]]
    assert prov.tolist() == [[P.PUBLISHED, P.PUBLISHED, P.IMPUTED]]


def test_known_owners_are_never_changed():
    nwos = [[FAMILY, CORPORATE, STATE, 5, 6, 8]]
    owner, _ = run(nwos, [[1, 1, 1, 1, 1, 1]])
    assert owner.tolist() == nwos
