"""LANDFIRE EVT 2022 breakdown of the FL5 add-back lands, and the gate scenarios (#95).

Cross-tabs every hole and add-back pixel of a stitched county-improvement run against
LF2022 EVT, models EVT filters by re-running the 5 ac MMU on the gated proposals, and
renders ``evt_breakdown.html`` from ``evt_breakdown.template.html``.

    uv run python docs/evt2022-add-back-gate/make_evt_breakdown.py \\
        <run>/aoi_5county --evt-tif evt2022_aoi.tif --evt-csv LF2022_EVT.csv

``<run>`` is ``r2:artemis-r2/data/raster_improvement_data/processed/improved-rasters-evt2022-gated``.
``--evt-tif`` is LF2022_EVT_CONUS.tif clipped to the run grid (pixel-aligned, no resampling).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from scipy import ndimage

HERE = Path(__file__).parent
ACRES = 0.2224
MIN_ACRES = 5.0
TARGET = (9823, 9323)              # Southeastern Ruderal Grassland / Shrubland
RUDERAL = (9823, 9323, 9321)
PASTURE = 7997
ROADS = 7299
NATURAL_PHYS = {"Exotic Herbaceous", "Exotic Tree-Shrub", "Conifer", "Hardwood", "Conifer-Hardwood",
                "Riparian", "Shrubland", "Grassland", "Sparsely Vegetated"}
TWO_OR_MORE = (3, 5, 6, 7)         # method bits: 1 bookends, 2 Obata, 4 Hansen
HANSEN_ONLY = 4


def read(path: Path) -> np.ndarray:
    with rasterio.open(path) as src:
        return src.read(1)


def mmu(mask: np.ndarray) -> np.ndarray:
    labels, _ = ndimage.label(mask, structure=np.ones((3, 3)))
    sizes = np.bincount(labels.ravel())
    keep = np.nonzero(sizes * ACRES >= MIN_ACRES)[0]
    return np.isin(labels, keep[keep != 0])


def group(phys: str, code: int) -> str:
    phys = str(phys)
    if code in RUDERAL:
        return "Ruderal"
    if code == PASTURE:
        return "Pasture/hay"
    if phys == "Agricultural":
        return "Cropland"
    if phys.startswith("Developed-Roads"):
        return "Roads"
    if phys.startswith(("Developed", "Quarries")):
        return "Developed"
    if phys == "Riparian":
        return "Riparian/wetland"
    if phys in ("Conifer", "Hardwood", "Conifer-Hardwood"):
        return "Forest"
    return "Other"


def class_table(evt, hole, added, proposed, gated) -> pd.DataFrame:
    df = pd.DataFrame({"evt": evt[hole], "added": added[hole], "proposed": proposed[hole],
                       "two": (np.isin(gated, TWO_OR_MORE) & added)[hole],
                       "honly": ((gated == HANSEN_ONLY) & added)[hole]})
    return df.groupby("evt").agg(hole=("evt", "size"), added=("added", "sum"),
                                 proposed=("proposed", "sum"), two=("two", "sum"),
                                 honly=("honly", "sum")).reset_index()


def scenarios(evt, hole, gated, phys) -> pd.DataFrame:
    gp = hole & (gated > 0) & (gated < 255)
    developed = [v for v, p in phys.items() if str(p).startswith(("Developed", "Quarries"))]
    roads = evt == ROADS
    road1 = ndimage.binary_dilation(roads)
    target = np.isin(evt, TARGET)
    risky = np.isin(evt, [PASTURE, *developed])
    keep = {
        "S0 current gate (crops blocked)": np.ones_like(hole),
        "S1 + block pasture/hay (7997)": evt != PASTURE,
        "S2 + block Developed-Roads (7299)": ~np.isin(evt, [PASTURE, ROADS]),
        "S3 + block all Developed/urban & quarries": ~np.isin(evt, [PASTURE, *developed]),
        "S4 S3 + 1-px road buffer": ~np.isin(evt, [PASTURE, *developed]) & ~road1,
        "S6 allowlist: ruderal grassland/shrubland only": target,
        "H1 drop Hansen-only proposals": gated != HANSEN_ONLY,
        "H2 pasture/developed need >=2 methods": ~risky | np.isin(gated, TWO_OR_MORE),
        "H3 pasture/developed need all 3 methods": ~risky | (gated == 7),
        "H4 any-two rule everywhere": np.isin(gated, TWO_OR_MORE),
        "V2 adopted: block roads + developed low/med/high": ~np.isin(evt, [7296, 7297, 7298, ROADS]),
    }
    base = mmu(gp) & hole
    rows = []
    for name, k in keep.items():
        m = mmu(gp & k) & hole
        rows.append({"scenario": name, "added_ac": m.sum() * ACRES,
                     "lost_vs_S0_ac": (base.sum() - m.sum()) * ACRES, "lost_pct": 1 - m.sum() / base.sum(),
                     "pasture_ac": (m & (evt == PASTURE)).sum() * ACRES, "roads_ac": (m & roads).sum() * ACRES,
                     "road_adjacent_ac": (m & road1).sum() * ACRES,
                     "dev_ac": (m & np.isin(evt, developed)).sum() * ACRES,
                     "target_added_ac": (m & target).sum() * ACRES,
                     "target_mmu_casualty_ac": ((base & target).sum() - (m & target).sum()) * ACRES})
    return pd.DataFrame(rows).round(3)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run", type=Path, help="stitched run folder (aoi_5county)")
    p.add_argument("--evt-tif", type=Path, required=True)
    p.add_argument("--evt-csv", type=Path, required=True)
    p.add_argument("--out", type=Path, default=HERE)
    a = p.parse_args()

    evt = read(a.evt_tif).astype(np.int32)
    prov = read(a.run / "treemap2022_provenance.tif")
    raw = read(a.run / "add_back_method_bits_raw.tif")
    gated = read(a.run / "add_back_method_bits.tif")
    if evt.shape != prov.shape:
        raise ValueError("EVT is not on the run grid")
    hole = np.isin(prov, (2, 3, 4))
    added = np.isin(prov, (2, 4))
    proposed = hole & (raw > 0) & (raw < 255)

    legend = pd.read_csv(a.evt_csv).set_index("VALUE")
    t = class_table(evt, hole, added, proposed, gated)
    t["name"] = t["evt"].map(legend["EVT_NAME"])
    t["phys"] = t["evt"].map(legend["EVT_PHYS"])
    t["group"] = [group(ph, int(v)) for ph, v in zip(t["phys"], t["evt"])]
    for c in ("hole", "added", "proposed", "two", "honly"):
        t[c] = (t[c] * ACRES).round(1)
    t = t.sort_values(["added", "hole"], ascending=False)
    t.to_csv(a.out / "evt2022_add_back_breakdown.csv", index=False)
    sc = scenarios(evt, hole, gated, legend["EVT_PHYS"])
    sc.to_csv(a.out / "evt2022_gate_scenarios.csv", index=False)

    groups = (t.groupby("group")[["hole", "proposed", "added", "two", "honly"]].sum()
              .sort_values("added", ascending=False).reset_index().round(1))
    shown = t[(t["added"] >= 50) | (t["hole"] >= 2000)].assign(evt=lambda d: d["evt"].astype(int))
    data = {"classes": shown.to_dict("records"), "groups": groups.to_dict("records"),
            "scenarios": sc.round(1).to_dict("records"),
            "totals": {k: float(t[k].sum()) for k in ("hole", "added", "proposed")}}
    page = (HERE / "evt_breakdown.template.html").read_text()
    (a.out / "evt_breakdown.html").write_text(page.replace("__DATA__", json.dumps(data, separators=(",", ":"))))
    print(groups.to_string(index=False))


if __name__ == "__main__":
    main()
