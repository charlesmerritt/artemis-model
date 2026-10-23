"""v1 through ``improve_county`` reproduces the published gated FL5 run, pixel for pixel.

The published run (``r2:artemis-r2/data/raster_improvement_data/processed/
improved-rasters-evt2022-gated/``) applied ``evt2022_agriculture_v1`` from a script outside
this repo. Running the in-repo gate on the same inputs must give the same rasters.

Set ``ARTEMIS_FL5_GATE_REGRESSION`` to a folder holding ``inputs/`` (the AOI clips, laid out
as ``Inputs`` below expects) and ``published_v1/`` (the run's ``aoi_5county`` rasters).
Issue #97 has the ``gdal_translate -projwin`` recipe that clips the inputs from R2.
"""

import os
from pathlib import Path

import numpy as np
import pytest
import rasterio

from pipeline.s1_initial_state import county_improvement as ci
from pipeline.s1_initial_state.evt_gate import EvtGatePolicy

ROOT = Path(os.environ.get("ARTEMIS_FL5_GATE_REGRESSION", "/nonexistent"))

pytestmark = [
    pytest.mark.production_data,
    pytest.mark.skipif(not (ROOT / "published_v1").is_dir(),
                       reason="ARTEMIS_FL5_GATE_REGRESSION does not point at the FL5 inputs"),
]

# Ours -> the published run's name for the same raster.
COMPARED = {name: name for name in (
    "treemap2022_improved.tif", "treemap2022_provenance.tif", "nwos2022_improved.tif",
    "nwos2022_provenance.tif", "add_back_method_bits.tif", "add_back_method_bits_raw.tif",
    "evt2022_add_back_eligible.tif")} | {"evt2022_gate_rejected.tif": "evt2022_agriculture_rejected.tif"}


@pytest.fixture(scope="module")
def v1_run(tmp_path_factory):
    inputs_dir = ROOT / "inputs"
    inputs = ci.Inputs(
        treemap=inputs_dir / "TreeMap-2022/Data/TreeMap2022_CONUS.tif",
        landfire=inputs_dir / "landfire",
        scored=inputs_dir / "hole_prob_similarity_statewide.tif",
        model=inputs_dir / "hole_model.json",
        ownership=inputs_dir / "us_forest_ownership_fl.tif",
        counties=inputs_dir / "county/countyp010g.shp",
        obata=next((inputs_dir / "obata").glob("lastDist-*.tif")),
        hansen=inputs_dir / "hansen/hansen_aoi.tif",
    )
    out_root = tmp_path_factory.mktemp("fl5_v1")
    for fips in ci.AOI_COUNTIES:
        ci.improve_county(fips, inputs, out_root, evt_gate=EvtGatePolicy.AGRICULTURE_V1)
    # The establishment lists need TreeMap's 4.8 GB tree table; the published run skipped them.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ci, "stitch_establishment", lambda *a, **k: {"status": "not run"})
        ci.stitch(list(ci.AOI_COUNTIES), out_root, inputs)
    return out_root / ci.AOI_NAME


@pytest.mark.parametrize("name", COMPARED)
def test_v1_reproduces_the_published_gated_raster(v1_run, name):
    with (rasterio.open(ROOT / "published_v1" / COMPARED[name]) as published,
          rasterio.open(v1_run / name) as ours):
        assert ours.transform == published.transform
        assert ours.nodata == published.nodata
        mismatched = int((ours.read(1) != published.read(1)).sum())
    assert mismatched == 0


def test_v1_reproduces_the_published_rejections_by_evt_class(v1_run):
    import pandas as pd

    ours = pd.read_csv(v1_run / ci.GATE_REJECTIONS_CSV).set_index("evt_value")["rejected_pixels"]
    published = pd.read_csv(ROOT / "published_v1" / ci.GATE_REJECTIONS_CSV)
    published = published.set_index(published.columns[0])[published.columns[1]]
    assert ours.sort_index().to_dict() == published.sort_index().to_dict()


def test_v1_records_its_policy_in_the_summary_and_manifest(v1_run):
    import json

    summary = json.loads((v1_run / "summary.json").read_text())
    manifest = json.loads((v1_run / "manifest.json").read_text())
    assert summary["evt_gate"]["policy"] == manifest["evt_gate"] == "evt2022_agriculture_v1"
    assert np.isclose(summary["added_back_acres"], 189_024, atol=1)
