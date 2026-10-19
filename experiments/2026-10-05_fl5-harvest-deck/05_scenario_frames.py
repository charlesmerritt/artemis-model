"""Scenario frames, slides 8-8d: grow-only and the annealed TPO plan, painted back on FL5.

Slide 8   outputs/frames/grow_<year>.png   standing merchantable volume, no management, 2022-2072
Slide 8b  outputs/frames/tpo_<year>.png    the same under the converged annealed plan; red marks
                                           every pixel whose stand is cut in that cycle
Slide 8c-d outputs/scenario_frames.json    per-cycle TPO target and planned harvest for the chart

The plan is ``weekly-artifact/2026-09-28/annealed_plan.csv``, the convergence sweep over the
2026-09-14 library (20 restarts x cooling 0.95, 0.98). Its stands are pixel classes,
``TM<TM_ID>_<County>_O<Harris code>`` over the *published* TreeMap and NWOS, so this paints
each pixel with its class's chosen trajectory; pixels in no plan stand (NWOS non-forest, water)
stay non-forest. Riparian pixels take the plot's ``no_management`` run, as the plan's
riparian stands do: the riparian mask is the LETO units' (``work/management_units.gpkg``), not
the 2026-08-24 BMP layer the plan carved with, so the two differ at the margin.

Per-cycle standing volume comes from ``data/interim/timing_library/trajectory_cycles.csv``,
which ``weekly-artifact/2026-09-14/make_timing_library.py`` rebuilds by running FVS.

    uv run python experiments/2026-10-05_fl5-harvest-deck/05_scenario_frames.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deck_maps as M  # noqa: E402
import deck_paths as P  # noqa: E402
from pipeline.ids import as_id_series  # noqa: E402

PLAN_DIR = P.REPO / "weekly-artifact/2026-09-28"   # the converged re-run of the 2026-09-14 search
PLAN_CSV = PLAN_DIR / "annealed_plan.csv"
BY_CYCLE_CSV = PLAN_DIR / "harvest_by_cycle.csv"
CYCLES_CSV = P.REPO / "data/interim/timing_library/trajectory_cycles.csv"
FRAMES = P.OUTPUTS / "frames"
CUT = "#c62828"
VOL_MAX = 5000.0           # merch ft3/ac at the top of the green ramp
N_CYCLES = 10
GROW_ONLY = "no_management"


def read(path: Path) -> np.ndarray:
    with rasterio.open(path) as src:
        return src.read(1)


def pixel_plan_index(transform, shape, stand_ids: pd.Series) -> np.ndarray:
    """Row of ``stand_ids`` each pixel's class belongs to, -1 where the pixel is in no plan stand."""
    tm = read(P.IMPROVED / "treemap2022_published.tif").astype(np.int64)
    own = read(P.IMPROVED / "nwos2022_published.tif").astype(np.int64)
    c = M.counties()
    names = sorted(c["NAME"])
    county = rasterize(((g, names.index(n) + 1) for g, n in zip(c.geometry, c["NAME"])),
                       out_shape=shape, transform=transform, fill=0, dtype="uint8").astype(np.int64)
    ok = (tm != P.viz.TREEMAP_NODATA) & (county > 0)
    # One integer key per (TM_ID, county, owner) class; strings only for the few unique classes.
    key = (tm * 16 + county) * 16 + own
    uniq, inverse = np.unique(key[ok], return_inverse=True)
    tm_u, rest = np.divmod(uniq, 256)
    county_u, own_u = np.divmod(rest, 16)
    labels = pd.Series([f"TM{t}_{names[c_ - 1]}_O{o}" for t, c_, o in zip(tm_u, county_u, own_u)])
    row = labels.map(pd.Series(np.arange(len(stand_ids)), index=stand_ids.to_numpy())).fillna(-1)
    out = np.full(shape, -1, dtype=np.int64)
    out[ok] = row.to_numpy(dtype=np.int64)[inverse]
    return out


def riparian_mask(transform, shape) -> np.ndarray:
    units = gpd.read_file(P.WORK / "management_units.gpkg")
    rip = units[units["RIPARIAN"] == 1]
    return rasterize(((g, 1) for g in rip.geometry), out_shape=shape, transform=transform,
                     fill=0, dtype="uint8").astype(bool)


def main() -> None:
    FRAMES.mkdir(parents=True, exist_ok=True)
    with rasterio.open(P.NWOS_TIF) as src:
        transform, shape = src.transform, src.shape

    plan = pd.read_csv(PLAN_CSV, dtype={"PLT_CN": str})
    plan["PLT_CN"] = as_id_series(plan["PLT_CN"], column="PLT_CN")
    cyc = pd.read_csv(CYCLES_CSV, dtype={"PLT_CN": str})
    cyc["PLT_CN"] = as_id_series(cyc["PLT_CN"], column="PLT_CN")
    cyc = cyc[cyc["cycle"].between(0, N_CYCLES)]
    cyc["run"] = cyc["PLT_CN"] + "::" + cyc["prescription"]
    standing = cyc.pivot_table(index="run", columns="cycle", values="MCuFt", aggfunc="first")
    removed = cyc.pivot_table(index="run", columns="cycle", values="removed_merch_cuft_per_ac",
                              aggfunc="sum").fillna(0)

    ix = pixel_plan_index(transform, shape, plan["stand_id"])
    rip = riparian_mask(transform, shape)
    in_plan = ix >= 0

    runs = {
        "grow": (plan["PLT_CN"] + "::" + GROW_ONLY).to_numpy(),
        "tpo": plan["fvs_run_id"].to_numpy(),
    }
    grow_runs = runs["grow"]
    missing = sorted(set(grow_runs) - set(standing.index))
    if missing:
        raise SystemExit(f"{len(missing)} plan plots have no no_management run, e.g. {missing[:3]}")

    stats = {"cycles": [], "pixels_in_plan_acres": round(float(in_plan.sum() * P.viz.CELL_ACRES)),
             "riparian_in_plan_acres": round(float((in_plan & rip).sum() * P.viz.CELL_ACRES))}
    by_cycle = pd.read_csv(BY_CYCLE_CSV)
    for k in range(N_CYCLES + 1):
        year = P.viz.INV_YEAR + 5 * k
        row = {"cycle": k, "year": year}
        for name, run_ids in runs.items():
            vol = standing[k].reindex(run_ids).to_numpy()
            cut = removed[k].reindex(run_ids).fillna(0).to_numpy() > 0
            vol_px = np.zeros(shape, dtype=np.float32)
            vol_px[in_plan] = vol[ix[in_plan]]
            # Riparian pixels never leave no_management.
            rip_px = in_plan & rip
            vol_px[rip_px] = standing[k].reindex(grow_runs).to_numpy()[ix[rip_px]]
            rgb = M.base_rgb()
            M.paint_greens(rgb, in_plan, vol_px, VOL_MAX)
            if name == "tpo":
                cut_px = np.zeros(shape, dtype=bool)
                cut_px[in_plan] = cut[ix[in_plan]]
                cut_px &= ~rip
                rgb[cut_px] = M.hex_rgb(CUT)
                row["cut_acres_painted"] = round(float(cut_px.sum() * P.viz.CELL_ACRES))
            row[f"{name}_mean_cuft_ac"] = round(float(vol_px[in_plan].mean()), 1)
            M.render_one(rgb, FRAMES / f"{name}_{year}.png", labels=False, stamp=str(year))
        if k >= 1:
            b = by_cycle[by_cycle["cycle"] == k].iloc[0]
            row.update(target_cuft=float(b["target_cuft"]), planned_cuft=float(b["cuft"]),
                       deviation_pct=round(float(b["deviation_pct"]), 1))
        stats["cycles"].append(row)
        print(row, flush=True)
    (P.OUTPUTS / "scenario_frames.json").write_text(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
