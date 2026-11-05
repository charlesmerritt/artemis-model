"""The land-use prototype gate: automatic labels, per-class AlphaEarth prototypes, the fitted gate."""

import numpy as np

from pipeline.s1_initial_state.landuse_prototype_gate import (
    EMBED_YEARS,
    LandUse,
    LandUseGroup,
    LabelInputs,
    PrototypeGate,
    fit_gate_years,
    fit_prototypes,
    label_pixels,
    label_years,
    nearest_class,
    trailing_nonforest_years,
)

T, F = True, False


def inputs(n, **kw):
    """One row of ``n`` pixels; every source says nothing unless set."""
    base = dict(
        published=np.zeros(n, bool), hole=np.zeros(n, bool),
        tree={y: np.zeros(n, bool) for y in (2016, 2022, 2024)},
        evt={y: np.full(n, 9001) for y in (2016, 2022, 2024)},
        strata=np.zeros(n, np.uint8), cut_year=np.zeros(n, np.uint16),
        cdl_crop_years=np.zeros(n, np.uint8), cdl_hay_years=np.zeros(n, np.uint8),
        solar=np.zeros(n, bool),
    )
    base.update(kw)
    return LabelInputs(**base)


def test_forest_classes_are_forest_and_land_uses_are_not():
    assert LandUse.MATURE_FOREST.group is LandUseGroup.FOREST
    assert LandUse.REGENERATING_FOREST.group is LandUseGroup.FOREST
    for c in (LandUse.CROP, LandUse.PASTURE_HAY, LandUse.QUARRY, LandUse.DEVELOPED, LandUse.SOLAR):
        assert c.group is LandUseGroup.NON_FOREST


def test_mature_forest_is_published_treemap_that_was_tree_at_every_landfire_vintage_and_never_cut():
    all_tree = {y: np.array([T, T, T]) for y in (2016, 2022, 2024)}
    x = inputs(3, published=np.array([T, T, F]), tree=all_tree, cut_year=np.array([0, 2015, 0], np.uint16))
    assert label_pixels(x).tolist() == [LandUse.MATURE_FOREST, 0, 0]


def test_regenerating_forest_is_a_hole_logged_before_2016_or_cut_and_back_to_tree_by_2024():
    tree24 = np.array([T, T, F, T])
    x = inputs(4, hole=np.array([T, T, T, F]),
               tree={2016: np.zeros(4, bool), 2022: np.zeros(4, bool), 2024: tree24},
               strata=np.array([1, 0, 1, 1], np.uint8), cut_year=np.array([0, 2018, 0, 0], np.uint16))
    assert label_pixels(x).tolist() == [LandUse.REGENERATING_FOREST, LandUse.REGENERATING_FOREST, 0, 0]


def test_crop_is_cdl_crop_three_of_five_years_never_tree():
    x = inputs(3, cdl_crop_years=np.array([3, 2, 5], np.uint8),
               tree={2016: np.array([F, F, T]), 2022: np.zeros(3, bool), 2024: np.zeros(3, bool)})
    assert label_pixels(x).tolist() == [LandUse.CROP, 0, 0]


def test_pasture_and_hay_is_7997_at_every_vintage_and_cdl_hay_or_pasture_three_of_five_years():
    evt = {y: np.array([7997, 7997, 7997]) for y in (2016, 2022, 2024)}
    evt[2016] = np.array([7997, 7997, 9001])
    x = inputs(3, evt=evt, cdl_hay_years=np.array([3, 1, 4], np.uint8))
    assert label_pixels(x).tolist() == [LandUse.PASTURE_HAY, 0, 0]


def test_quarry_is_7295_at_any_vintage_and_not_tree_now():
    evt = {2016: np.array([7295, 9001, 9001]), 2022: np.array([9001, 9001, 7295]),
           2024: np.array([9001, 7295, 9001])}
    tree = {2016: np.zeros(3, bool), 2022: np.zeros(3, bool), 2024: np.array([F, F, T])}
    assert label_pixels(inputs(3, evt=evt, tree=tree)).tolist() == [LandUse.QUARRY, LandUse.QUARRY, 0]


def test_developed_is_developed_at_every_vintage():
    evt = {2016: np.array([7299, 7296]), 2022: np.array([7299, 7296]), 2024: np.array([7299, 9001])}
    assert label_pixels(inputs(2, evt=evt)).tolist() == [LandUse.DEVELOPED, 0]


def test_solar_wins_over_every_other_label():
    x = inputs(2, solar=np.array([T, F]), cdl_crop_years=np.array([5, 5], np.uint8))
    assert label_pixels(x).tolist() == [LandUse.SOLAR, LandUse.CROP]


def test_each_label_is_embedded_only_in_the_years_its_label_holds():
    assert label_years(LandUse.MATURE_FOREST, 0) == EMBED_YEARS == (2017, 2018, 2019, 2020, 2021, 2022)
    assert label_years(LandUse.CROP, 0) == (2019, 2020, 2021, 2022)     # CDL counts 2019-2023
    assert label_years(LandUse.REGENERATING_FOREST, 2019) == (2019, 2020, 2021, 2022)
    assert label_years(LandUse.REGENERATING_FOREST, 2012) == EMBED_YEARS
    assert label_years(LandUse.REGENERATING_FOREST, 0) == EMBED_YEARS    # S1: logged before 2016


def unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def test_prototypes_are_unit_centroids_per_class_and_a_pixel_takes_the_nearest():
    rng = np.random.default_rng(0)
    axes = np.eye(8)
    x = np.vstack([unit(axes[0] + 0.05 * rng.normal(size=(30, 8))),
                   unit(axes[1] + 0.05 * rng.normal(size=(30, 8)))])
    y = np.repeat([LandUse.MATURE_FOREST, LandUse.CROP], 30)
    protos = fit_prototypes(x, y, k=2, seed=0)
    assert set(protos) == {LandUse.MATURE_FOREST, LandUse.CROP}
    assert all(np.allclose(np.linalg.norm(p, axis=1), 1) for p in protos.values())
    assert nearest_class(unit([axes[1], axes[0]]), protos).tolist() == [LandUse.CROP, LandUse.MATURE_FOREST]


def test_a_class_with_fewer_rows_than_k_keeps_one_prototype_per_row():
    protos = fit_prototypes(unit(np.eye(4)[:2]), np.array([LandUse.QUARRY] * 2), k=5, seed=0)
    assert protos[LandUse.QUARRY].shape == (2, 4)


def test_trailing_non_forest_years_count_back_from_the_last_year():
    m, c = LandUse.MATURE_FOREST, LandUse.CROP
    classes = np.array([[m, m, m, c, c, c],    # converted three years ago
                        [c, c, c, c, c, m],    # forest last year: 0
                        [c, c, c, c, c, c]])
    assert trailing_nonforest_years(classes).tolist() == [3, 0, 6]


def test_the_gate_years_maximize_balanced_accuracy_and_ties_take_the_longer_run():
    trailing = np.array([6, 5, 4, 1, 0, 0, 2])
    nonforest = np.array([T, T, T, T, F, F, F])
    # N=1: 4/4 land use, 2/3 forest (BA .833); N=3: 3/4, 3/3 (BA .875); N=4 the same, the longer run wins.
    assert fit_gate_years(trailing, nonforest) == 4


def test_the_gate_round_trips_through_json(tmp_path):
    protos = {LandUse.CROP: unit(np.eye(3)[:2]), LandUse.MATURE_FOREST: unit(np.eye(3)[2:])}
    gate = PrototypeGate(protos, years=3, metrics={"a": 1})
    gate.save(tmp_path / "g.json")
    back = PrototypeGate.load(tmp_path / "g.json")
    assert back.years == 3 and set(back.prototypes) == set(protos)
    assert np.allclose(back.prototypes[LandUse.CROP], protos[LandUse.CROP])
    assert back.gated(np.array([2, 3, 6])).tolist() == [False, True, True]
