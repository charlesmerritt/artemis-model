"""Compile temporary mutants in isolated namespaces; never edit source files."""
import importlib.util
import inspect
import dataclasses
import subprocess
import sys
import types
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pipeline.s1_initial_state import leto_initial_state, statewide_repair, score_holes_statewide  # noqa: E402
from pipeline.s1_initial_state.segmentation import artifacts, boundary_overlay, comparison, cellular_automata  # noqa: E402

BASELINE = "066aee6"

def load_test(filename):
    spec = importlib.util.spec_from_file_location(filename.removesuffix('.py'), ROOT / 'tests' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

parity=load_test('test_s1_leto_parity.py')
repair=load_test('test_statewide_repair.py')
tiles=load_test('test_score_holes_statewide.py')
boundary=load_test('test_s1_boundary_overlay.py')
artifact=load_test('test_s1_segmentation_artifacts.py')
compare=load_test('test_s1_segmentation_comparison.py')
ca=load_test('test_s1_cellular_automata.py')

def compiled_mutant(function, old, new):
    source=inspect.getsource(function)
    assert old in source, (function.__name__, old)
    namespace=dict(function.__globals__)
    exec(compile(source.replace(old,new,1), '<temporary spatial mutation>', 'exec'), namespace)
    return namespace[function.__name__]

results=[]

def probe(label, test, owner, attribute, replacement, args=()):
    with patch.object(owner, attribute, replacement):
        try:
            test(*args)
        except AssertionError:
            results.append({'mutation':label,'result':'killed by assertion'})
        else:
            raise AssertionError('Mutant survived: '+label)
    test(*args)

probe('normalization multiplies by retained group sum', parity.test_weight_filter_matches_leto_group_normalization,
      parity, 'filter_and_normalize_weights', compiled_mutant(leto_initial_state.filter_and_normalize_weights,
      'retained["WEIGHT"] / retained.groupby', 'retained["WEIGHT"] * retained.groupby'))
probe('weighted tree density adds rather than multiplies weight', parity.test_tree_preparation_matches_leto_csv_pipeline,
      parity,'prepare_direct_tree_rows', compiled_mutant(leto_initial_state.prepare_direct_tree_rows,
      'joined["TREE_COUNT"] * joined["WEIGHT"]','joined["TREE_COUNT"] + joined["WEIGHT"]'))
probe('missing height fills zero rather than ACTUALHT', parity.test_tree_preparation_matches_leto_csv_pipeline,
      parity,'prepare_direct_tree_rows', compiled_mutant(leto_initial_state.prepare_direct_tree_rows,
      'joined["HT"].fillna(joined["ACTUALHT"])','joined["HT"].fillna(0)'))
probe('imputed MU_ID remains donor identity', parity.test_nearest_imputation_matches_leto_generate_near_table,
      parity,'impute_missing_tree_rows', compiled_mutant(leto_initial_state.impute_missing_tree_rows,
      'donor_trees["MU_ID"] = missing_id','donor_trees["MU_ID"] = donor_id'))
probe('logged regrowth assigned S2 rather than S1', repair.test_stratify_block_classifies_each_history_with_its_vintage_legend,
      repair,'stratify_block', compiled_mutant(statewide_repair.stratify_block,
      'out[logged16 & tree24] = 1','out[logged16 & tree24] = 2'))
probe('2024 forest classified with 2016 legend', repair.test_stratify_block_classifies_each_history_with_its_vintage_legend,
      repair,'stratify_block', compiled_mutant(statewide_repair.stratify_block,
      'tree24 = np.isin(evt24, codes24.tree)','tree24 = np.isin(evt24, codes16.tree)'))
probe('tile row budget grows by one', tiles.test_strata_grid_tiles_split_into_2d_tiles_within_budget_and_width,
      score_holes_statewide,'grid_tiles', compiled_mutant(score_holes_statewide.grid_tiles,
      'max_tile_pixels // max_tile_width','max_tile_pixels // max_tile_width + 1'))
probe('tile left edge shifted one pixel', tiles.test_strata_grid_tiles_split_into_2d_tiles_within_budget_and_width,
      score_holes_statewide,'grid_tiles', compiled_mutant(score_holes_statewide.grid_tiles,
      'transform.c + col0 * OUTPUT_SCALE_M','transform.c + (col0 + 1) * OUTPUT_SCALE_M'))
probe('wrong hectares-to-acres conversion', boundary.test_boundary_output_meets_s1_contract,
      boundary_overlay,'HECTARES_TO_ACRES',1.0,args=(boundary.candidate_units.__wrapped__(),))
probe('wrong segmentation method constant', boundary.test_boundary_output_meets_s1_contract,
      boundary_overlay,'SEGMENTATION_METHOD','wrong',args=(boundary.candidate_units.__wrapped__(),))
with tempfile.TemporaryDirectory() as temp:
    probe('fingerprint byte size zeroed', artifact.test_boundary_artifact_contains_complete_attributed_contract_and_manifest,
          artifacts,'source_fingerprint', compiled_mutant(artifacts.source_fingerprint,
          'byte_size += stat.st_size','byte_size += 0'),args=(Path(temp),))
probe('comparison acreage divisor wrong',compare.test_compare_segmentations_measures_unit_acres_from_geometry,
      comparison,'SQUARE_METERS_PER_ACRE',10_000)
probe('bootstrap fixes AOI indices instead of resampling', compare.test_hierarchical_bootstrap_resamples_aois_before_nested_seeds,
      comparison,'hierarchical_paired_bootstrap', compiled_mutant(comparison.hierarchical_paired_bootstrap,
      'rng.integers(0, len(groups), size=len(groups))','np.arange(len(groups))'))
# Compare a subset against unchanged baseline test code. These comparisons
# establish that the added assertions close gaps the old checks allowed.
def load_baseline(filename):
    source = subprocess.run(
        ["git", "show", f"{BASELINE}:tests/{filename}"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout
    module = types.ModuleType("baseline_" + filename.removesuffix(".py"))
    module.__file__ = str(ROOT / "tests" / filename)
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def before_after(label, filename, test_name, owner, attribute, replacement,
                 args_factory=lambda temp: (), rejection=AssertionError):
    with patch.object(owner, attribute, replacement):
        old_module = load_baseline(filename)
        with tempfile.TemporaryDirectory() as temp:
            getattr(old_module, test_name)(*args_factory(Path(temp)))
        new_module = {parity.__name__: parity, repair.__name__: repair,
                      tiles.__name__: tiles, boundary.__name__: boundary,
                      artifact.__name__: artifact, compare.__name__: compare,
                      ca.__name__: ca}[filename.removesuffix(".py")]
        with tempfile.TemporaryDirectory() as temp:
            try:
                getattr(new_module, test_name)(*args_factory(Path(temp)))
            except rejection:
                pass
            else:
                raise AssertionError("New test allowed faulty behavior: " + label)
    with tempfile.TemporaryDirectory() as temp:
        getattr(new_module, test_name)(*args_factory(Path(temp)))
    result = next((item for item in results if item["mutation"] == label), None)
    if result is None:
        result = {"mutation": label}
        results.append(result)
    result["result"] = "baseline passed; replacement rejected; original restored and passed"

before_after('fingerprint byte size zeroed', 'test_s1_segmentation_artifacts.py',
             'test_boundary_artifact_contains_complete_attributed_contract_and_manifest',
             artifacts, 'source_fingerprint', compiled_mutant(artifacts.source_fingerprint,
             'byte_size += stat.st_size', 'byte_size += 0'), args_factory=lambda temp: (temp,))
before_after('bootstrap fixes AOI indices instead of resampling', 'test_s1_segmentation_comparison.py',
             'test_hierarchical_bootstrap_resamples_aois_before_nested_seeds',
             comparison, 'hierarchical_paired_bootstrap', compiled_mutant(comparison.hierarchical_paired_bootstrap,
             'rng.integers(0, len(groups), size=len(groups))', 'np.arange(len(groups))'))
before_after('tile order reversed', 'test_score_holes_statewide.py',
             'test_strata_grid_tiles_split_into_2d_tiles_within_budget_and_width',
             score_holes_statewide, 'grid_tiles', compiled_mutant(score_holes_statewide.grid_tiles,
             'return tiles', 'return list(reversed(tiles))'))

original_from_root = boundary_overlay.BoundaryOverlayDataPaths.from_root

def wrong_source_layout(data_root, config_path):
    return dataclasses.replace(original_from_root(data_root, config_path),
                               parcels=data_root / 'wrong-parcels.gdb')

before_after('source constructor points to wrong parcel path', 'test_s1_boundary_overlay.py',
             'test_boundary_preflight_accepts_complete_temp_source_tree',
             boundary_overlay.BoundaryOverlayDataPaths, 'from_root', wrong_source_layout,
             args_factory=lambda temp: (temp,), rejection=FileNotFoundError)

original_attribute = cellular_automata.attribute_management_units

def missing_weights(*args, **kwargs):
    units, weights = original_attribute(*args, **kwargs)
    return units, weights.iloc[:0]

original_labels_to_units = cellular_automata._labels_to_units

def crossing_boundary(*args, **kwargs):
    units = original_labels_to_units(*args, **kwargs).copy()
    index = units.index[units.geometry.bounds['maxx'] <= 300.0][0]
    minx, miny, _maxx, maxy = units.loc[index].geometry.bounds
    units.loc[index, 'geometry'] = ca.box(minx, miny, 301.0, maxy)
    return units


def ca_args(temp):
    import pytest
    patcher = pytest.MonkeyPatch()
    # Keep fixture patches alive for the duration of the test call. The
    # temporary VAT reader is reset by the caller after each comparison.
    scenario = ca.scenario.__wrapped__(temp, patcher)
    return (scenario,)

original_vat = cellular_automata.load_treemap_attributes
try:
    before_after('CA returns empty donor weights', 'test_s1_cellular_automata.py',
                 'test_produces_the_shared_canonical_output_contract',
                 cellular_automata, 'attribute_management_units', missing_weights,
                 args_factory=ca_args)
    before_after('CA polygon crosses ownership split while centroid stays west',
                 'test_s1_cellular_automata.py', 'test_never_crosses_the_ownership_hard_boundary',
                 cellular_automata, '_labels_to_units', crossing_boundary,
                 args_factory=ca_args)
finally:
    cellular_automata.load_treemap_attributes = original_vat

print(json.dumps(results, indent=2))
(ROOT / 'research/code_quality/spatial_probe_results.json').write_text(json.dumps(results, indent=2)+'\n')
