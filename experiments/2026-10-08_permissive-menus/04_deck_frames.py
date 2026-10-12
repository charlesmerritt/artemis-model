"""Step 5 — the deck's cut-by-cut frames: step 4's plan painted back on FL5, one per cycle.

Each frame is standing merchantable volume under the plan (the greens of the 2026-10-05
deck), with every pixel whose stand is cut that cycle painted by cut type: clearcut or
partial (thin, selection, restoration). Pixel-to-stand matching, the riparian mask and the
map style are the 2026-10-05 deck's (`05_scenario_frames.py`, `deck_maps.py`), so these
frames sit beside its grow-only and constrained-menu frames like for like.

Writes `outputs/deck/frames/cut_<year>.png` and `outputs/deck/frames.json` (per cycle:
painted acres by cut type, mean standing volume, planned and target removals).

    uv run python experiments/2026-10-08_permissive-menus/04_deck_frames.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

EXP = Path(__file__).resolve().parent
REPO = EXP.parents[1]
DECK = REPO / "experiments/2026-10-05_fl5-harvest-deck"
sys.path[:0] = [str(DECK), str(REPO)]

import deck_maps as M  # noqa: E402
import deck_paths as P  # noqa: E402
from pipeline.ids import as_id_series  # noqa: E402

spec = importlib.util.spec_from_file_location("scenario_frames", DECK / "05_scenario_frames.py")
SF = importlib.util.module_from_spec(spec)
spec.loader.exec_module(SF)

OUT = EXP / "outputs/deck"
FRAMES = OUT / "frames"
LIBRARY = EXP / "work/library_repeat_offsets_25.csv"
CYCLES = EXP / "work/fvs_cycles.csv.gz"
CLEARCUT = "#b3261e"
PARTIAL = "#f2a33a"
VOL_MAX = 5000.0
N_CYCLES = 10


def main() -> None:
    FRAMES.mkdir(parents=True, exist_ok=True)
    with rasterio.open(P.NWOS_TIF) as src:
        transform, shape = src.transform, src.shape

    plan = pd.read_csv(OUT / "plan.csv.gz", dtype={"PLT_CN": str})
    plan["PLT_CN"] = as_id_series(plan["PLT_CN"], column="PLT_CN")
    lib = pd.read_csv(LIBRARY, dtype={"PLT_CN": str, "unit_id": str})
    hash_of = lib.set_index(["unit_id", "prescription"])["run_hash"]
    plan["run_hash"] = hash_of.reindex(list(zip(plan["stand_id"], plan["prescription"]))).to_numpy()
    grow = (lib[lib["prescription"] == "no_management"].drop_duplicates("PLT_CN")
            .set_index("PLT_CN")["run_hash"])
    plan["grow_hash"] = plan["PLT_CN"].map(grow)
    if plan[["run_hash", "grow_hash"]].isna().any().any():
        raise SystemExit("a plan stand has no run in the library")

    cyc = pd.read_csv(CYCLES, usecols=["run_hash", "cycle", "MCuFt"])
    standing = cyc.pivot_table(index="run_hash", columns="cycle", values="MCuFt", aggfunc="first")
    events = pd.read_csv(OUT / "events.csv.gz")
    by_cycle = pd.read_csv(OUT / "landscape_by_cycle.csv").set_index("cycle")

    ix = SF.pixel_plan_index(transform, shape, plan["stand_id"])
    rip = SF.riparian_mask(transform, shape)
    in_plan = ix >= 0
    pos = pd.Series(np.arange(len(plan)), index=plan["stand_id"])

    stats = {"pixels_in_plan_acres": round(float(in_plan.sum() * P.viz.CELL_ACRES)), "cycles": []}
    for k in range(N_CYCLES + 1):
        year = P.viz.INV_YEAR + 5 * k
        vol = standing[k].reindex(plan["run_hash"]).to_numpy()
        vol_px = np.zeros(shape, dtype=np.float32)
        vol_px[in_plan] = vol[ix[in_plan]]
        rip_px = in_plan & rip
        vol_px[rip_px] = standing[k].reindex(plan["grow_hash"]).to_numpy()[ix[rip_px]]
        rgb = M.base_rgb()
        M.paint_greens(rgb, in_plan, vol_px, VOL_MAX)
        row = {"cycle": k, "year": year, "mean_standing_cuft_ac": round(float(vol_px[in_plan].mean()), 1)}
        if k >= 1:
            ev = events[events["cycle"] == k]
            for cut, colour in (("partial", PARTIAL), ("clearcut", CLEARCUT)):
                flag = np.zeros(len(plan), dtype=bool)
                flag[pos.reindex(ev.loc[ev["cut"] == cut, "stand_id"].unique()).to_numpy()] = True
                px = np.zeros(shape, dtype=bool)
                px[in_plan] = flag[ix[in_plan]]
                px &= ~rip
                rgb[px] = M.hex_rgb(colour)
                row[f"{cut}_acres_painted"] = round(float(px.sum() * P.viz.CELL_ACRES))
                row[f"{cut}_acres_plan"] = round(float(ev.loc[ev["cut"] == cut]
                                                        .drop_duplicates("stand_id")["acres"].sum()))
            b = by_cycle.loc[k]
            row.update(planned_cuft=float(b["volume_cuft"]), target_cuft=float(b["target_cuft"]),
                       deviation_pct=round(float(b["deviation_pct"]), 1))
        M.render_one(rgb, FRAMES / f"cut_{year}.png", labels=False, stamp=str(year))
        stats["cycles"].append(row)
        print(row, flush=True)
    (OUT / "frames.json").write_text(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
