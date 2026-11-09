"""The interactive management-unit viewer: ``qa/leto/viewer/<FIPS>.html``, one per county.

Stage tests run the two-county fixture of ``test_leto_stage`` and read the page's embedded
data (``<script type="application/json" id="leto-data">``) the way the page does.
"""

import json
import re

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Polygon, box

from pipeline import leto_viewer
from tests import test_leto_stage as fixture
from tests.test_leto_stage import ALPHA, BETA, PLOT_22, _run

world = fixture.world                         # the two-county raster-repair run folder


def _page(world, fips):
    return (world["run_dir"] / "qa" / "leto" / "viewer" / f"{fips}.html").read_text()


def _data(world, fips):
    match = re.search(r'<script type="application/json" id="leto-data">(.*?)</script>',
                      _page(world, fips), re.S)
    return json.loads(match.group(1))


def _frame(table):
    return pd.DataFrame(table["rows"], columns=table["columns"])


def _decode(rings, origin):
    """The page's decoding, independently: delta-encoded metres from ``origin``."""
    out = []
    for ring in rings:
        xy = np.cumsum(np.asarray(ring, dtype=float).reshape(-1, 2), axis=0) / leto_viewer.SCALE
        out.append(xy + origin)
    return Polygon(out[0], out[1:])


# ── the page in the run folder ─────────────────────────────────────────────────────────


def test_each_county_has_a_viewer_linked_from_the_qa_index_that_fetches_nothing(world):
    _run(world, [ALPHA, BETA])

    index = (world["run_dir"] / "qa" / "leto" / "index.html").read_text()
    for fips in (ALPHA, BETA):
        page = _page(world, fips)
        assert f'href="viewer/{fips}.html"' in index
        assert "<canvas" in page
        assert not re.search(r"""(src|href)=["'](https?:)?//""", page)
        assert "fetch(" not in page and "XMLHttpRequest" not in page


def test_the_page_holds_every_unit_with_its_attributes_and_exact_plot(world):
    _run(world, [ALPHA, BETA])

    layer = gpd.read_file(world["run_dir"] / "management_units.gpkg", layer="units")
    for fips in (ALPHA, BETA):
        units = _frame(_data(world, fips)["units"])
        mine = layer[layer["county_fips"] == fips]
        assert sorted(units["unit_id"]) == sorted(mine["unit_id"])
        assert {"owner_class", "unit_class", "origin", "acres", "stand_age", "SEG_ID",
                "PLT_CN", "FORTYPCD", "representative_share"} <= set(units.columns)
        by_id = units.set_index("unit_id")
        for row in mine.itertuples():
            assert by_id.loc[row.unit_id, "acres"] == pytest.approx(row.Acres, abs=1e-3)
    beta = _frame(_data(world, BETA)["units"])
    assert PLOT_22 in set(beta["PLT_CN"])                    # 19 digits, a string, intact


def test_each_units_outline_decodes_to_its_own_shape(world):
    _run(world, ALPHA)

    data = _data(world, ALPHA)
    layer = gpd.read_file(world["run_dir"] / "management_units.gpkg", layer="units").set_index("unit_id")
    units = _frame(data["units"])
    for unit_id, rings in zip(units["unit_id"], data["unit_rings"]):
        decoded = _decode(rings, data["origin"])
        truth = layer.geometry[unit_id]
        assert decoded.symmetric_difference(truth).area < 0.01 * truth.area + 50     # m²


def test_parents_are_the_cas_stands_with_their_cell_statistics_and_donors(world):
    _run(world, [ALPHA, BETA])

    accounting = pd.read_csv(world["run_dir"] / "qa" / "leto" / "area_accounting.csv",
                             dtype={"county_fips": str}).set_index("county_fips")
    for fips in (ALPHA, BETA):
        data = _data(world, fips)
        parents = _frame(data["parents"])
        assert len(parents) == len(data["parent_rings"]) == accounting.loc[fips, "parent_stands"]
        for name in ("STDAGE", "BALIVE", "QMD", "TPA"):
            assert {f"{name}_mean", f"{name}_sd"} <= set(parents.columns)
        assert (parents["cells"] > 0).all()
        assert parents["dominant_forest_type_share"].between(0, 1).all()
        units = _frame(data["units"])
        for seg, plt_cn in zip(units["SEG_ID"], units["PLT_CN"]):
            donors = data["donors"][str(seg)]
            assert plt_cn in {d[0] for d in donors}           # the representative plot is a donor
            assert sum(d[2] for d in donors) == pytest.approx(1.0)


def test_the_cell_layers_are_the_cas_inputs_over_the_countys_bounds(world):
    _run(world, ALPHA)

    data = _data(world, ALPHA)
    names = [layer["name"] for layer in data["cell_layers"]]
    assert names == ["forest type", "stand age", "basal area", "QMD", "TPA", "add-back"]
    for layer in data["cell_layers"]:
        assert layer["png"].startswith("data:image/png;base64,")
        assert layer["legend"]
    west, south, east, north = data["bounds"]
    assert east > west and north > south


def test_the_page_shows_the_runs_ca_and_speckle_settings(world):
    _run(world, ALPHA)

    settings = _data(world, ALPHA)["settings"]
    record = json.loads((world["run_dir"] / "leto.json").read_text())["regions"]["florida"]["settings"]
    assert settings["segmentation"] == record["segmentation"]
    assert settings["speckle"] == record["speckle"]


# ── the encoding ───────────────────────────────────────────────────────────────────────


def test_rings_round_trip_through_the_delta_encoding_holes_included():
    shape = box(1000, 2000, 1300, 2210).difference(box(1100, 2050, 1160, 2110))

    rings = leto_viewer.encode_geometry(shape, origin=(900.0, 1900.0), tolerance=0.0)

    assert len(rings) == 2
    assert _decode(rings, (900.0, 1900.0)).equals(shape)
