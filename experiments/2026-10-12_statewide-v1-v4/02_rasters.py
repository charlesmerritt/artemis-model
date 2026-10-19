"""Step 2: the v1-v4 improved rasters, county by county, for all of Florida.

    uv run python experiments/2026-10-12_statewide-v1-v4/02_rasters.py gate --recipe v1
    uv run python experiments/2026-10-12_statewide-v1-v4/02_rasters.py counties --recipe v1
    uv run python experiments/2026-10-12_statewide-v1-v4/02_rasters.py stitch --recipe v1

`gate` reruns the five FL5 counties with the EE Hansen export the releases read and compares
every raster the release also has, pixel for pixel. `counties` runs each Florida county not yet
done in its own process (statewide Hansen, `common.HANSEN_FL_TIF`), appending its wall time,
peak RSS or traceback to `<recipe>/run_log.jsonl`. `stitch` mosaics them into `<recipe>/florida`.
`check` tests the state mosaic against TreeMap, the county summaries and the gate rerun.
`obata-gap` reruns FL5 with no Obata evidence, as every county off Landsat p17/r39 runs, and
scales the statewide add-back off that scene by what FL5 loses.
`seams` counts added-back patches that cross a county line and disagree across it.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

EXP = Path(__file__).resolve().parent
REPO = EXP.parents[1]
sys.path[:0] = [str(EXP), str(REPO)]

import common  # noqa: E402
from common import Recipe  # noqa: E402
from pipeline.s1_initial_state import county_improvement as ci  # noqa: E402
from pipeline.s1_initial_state.add_back_methods import ConsensusRule  # noqa: E402
from pipeline.s1_initial_state.evt_gate import EvtGatePolicy  # noqa: E402

STATE_NAME = "florida"
BIG_COUNTY_PX = 20_000_000   # run alone: Monroe's bbox (the Keys) is 47M px, the next 10.5M


class Stage(StrEnum):
    GATE = "gate"
    COUNTIES = "counties"
    STITCH = "stitch"
    CHECK = "check"
    OBATA_GAP = "obata-gap"
    SEAMS = "seams"


@dataclass(frozen=True)
class RecipeSettings:
    rule: ConsensusRule
    gate: EvtGatePolicy
    window: ci.DisturbanceWindow


SETTINGS = {
    Recipe.V1: RecipeSettings(ConsensusRule.UNION, EvtGatePolicy.AGRICULTURE_V1,
                              ci.DisturbanceWindow.PUBLISHED),
    Recipe.V2: RecipeSettings(ConsensusRule.UNION, EvtGatePolicy.AGRICULTURE_DEVELOPED_V2,
                              ci.DisturbanceWindow.PUBLISHED),
    Recipe.V3: RecipeSettings(ConsensusRule.UNION, EvtGatePolicy.AGRICULTURE_DEVELOPED_PASTURE_V3,
                              ci.DisturbanceWindow.PUBLISHED),
    Recipe.V4: RecipeSettings(ConsensusRule.BOOKENDS_OR_OBATA_AND_HANSEN,
                              EvtGatePolicy.AGRICULTURE_DEVELOPED_PASTURE_QUARRY_V4,
                              ci.DisturbanceWindow.Y2012_2022),
}


def inputs(hansen: Path) -> ci.Inputs:
    return ci.Inputs(obata=common.OBATA_TIF, hansen=hansen)


def gate_dir(recipe: Recipe) -> Path:
    return common.WORK / "gate" / str(recipe)


def run_county(fips: str, recipe: Recipe, hansen: Path, out_root: Path) -> dict:
    """One county in this (fresh) process: its outputs, wall time and peak RSS, or its error."""
    s = SETTINGS[recipe]
    t = time.perf_counter()
    record = {"fips": fips, "recipe": str(recipe)}
    try:
        summary = ci.improve_county(fips, inputs(hansen), out_root, rule=s.rule, evt_gate=s.gate,
                                    window=s.window)
        record.update(status="ok", added_back_acres=summary["added_back_acres"],
                      patches=summary["patches"], pad_px=summary["pad_px"])
    except Exception:
        record.update(status="error", error=traceback.format_exc())
    record.update(seconds=round(time.perf_counter() - t, 1),
                  peak_rss_gb=round(common.peak_rss_gb(), 3))
    return record


class MemoryWatch:
    """Lowest MemAvailable seen while it runs, sampled every `every` seconds."""

    def __init__(self, every: float = 2.0):
        self.every, self.min_gb, self._stop = every, float("inf"), threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            self.min_gb = min(self.min_gb, available_gb())
            self._stop.wait(self.every)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()


def available_gb() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024 ** 2
    raise RuntimeError("no MemAvailable in /proc/meminfo")


def grid_pixels() -> dict[str, int]:
    """Each county's padded grid size, as `county_grid` builds it."""
    counties = common.florida_counties()
    pad = 2 * ci.PAD_PX
    out = {}
    for fips, geom in zip(counties["ADMIN_FIPS"], counties.geometry):
        minx, miny, maxx, maxy = geom.bounds
        out[fips] = int((np.ceil((maxx - minx) / 30) + pad + 1) * (np.ceil((maxy - miny) / 30) + pad + 1))
    return out


def done(out_root: Path) -> set[str]:
    log = out_root / "run_log.jsonl"
    if not log.exists():
        return set()
    rows = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    return {r["fips"] for r in rows if r["status"] == "ok"
            and (ci.county_dir(out_root, r["fips"]) / "summary.json").exists()}


def run_counties(fips_list: list[str], recipe: Recipe, hansen: Path, out_root: Path,
                 workers: int) -> list[dict]:
    """Each county in its own process, largest grid first, appending to `run_log.jsonl`."""
    out_root.mkdir(parents=True, exist_ok=True)
    log = out_root / "run_log.jsonl"
    sizes = grid_pixels()
    todo = sorted(set(fips_list) - done(out_root), key=lambda f: -sizes[f])
    big = [f for f in todo if sizes[f] >= BIG_COUNTY_PX]
    rest = [f for f in todo if sizes[f] < BIG_COUNTY_PX]
    records = []
    for batch, n in ((big, 1), (rest, workers)):
        if not batch:
            continue
        with ProcessPoolExecutor(max_workers=n, max_tasks_per_child=1) as pool:
            futures = {pool.submit(run_county, f, recipe, hansen, out_root): f for f in batch}
            for future in as_completed(futures):
                r = future.result() | {"grid_px": sizes[futures[future]],
                                       "available_gb": round(available_gb(), 2)}
                with log.open("a") as fh:
                    fh.write(json.dumps(r) + "\n")
                print(f"{r['fips']} {r['status']:<5} {r['seconds']:>7.1f} s "
                      f"{r['peak_rss_gb']:>6.2f} GB  {r['grid_px'] / 1e6:5.1f}M px", flush=True)
                records.append(r)
    return records


def compare(ours: Path, released: Path) -> dict:
    """Differing pixels per raster both folders have, on the released raster's grid."""
    out = {}
    for path in sorted(released.glob("*.tif")):
        mine = ours / path.name
        if not mine.exists():
            out[path.name] = "missing from the rerun"
            continue
        with rasterio.open(path) as src:
            theirs = src.read(1)
            grid = ci.Grid(src.transform, (src.height, src.width))
            nodata = src.nodata
        values = ci.read_on_grid(mine, grid, fill=nodata)
        out[path.name] = int((values != theirs).sum())
    return out


def stage_gate(recipe: Recipe, workers: int) -> dict:
    record: dict = {}
    out_root = gate_dir(recipe)
    fl5 = list(ci.AOI_COUNTIES)
    with MemoryWatch() as watch, common.measured("counties", record):
        counties = run_counties(fl5, recipe, common.HANSEN_AOI_TIF, out_root, workers)
    with common.measured("stitch", record):
        ci.stitch(fl5, out_root, inputs(common.HANSEN_AOI_TIF))
    record["min_available_gb"] = round(watch.min_gb, 2)
    record["county_runs"] = counties
    record["differing_pixels"] = compare(out_root / ci.AOI_NAME,
                                         common.FL5_RELEASED[str(recipe)])
    common.write_json(common.OUTPUTS / f"02_gate_{recipe}.json", record)
    return record


def stage_counties(recipe: Recipe, workers: int, fips: list[str] | None) -> dict:
    record: dict = {}
    fips = fips or list(common.florida_counties()["ADMIN_FIPS"])
    with MemoryWatch() as watch, common.measured("counties", record):
        runs = run_counties(fips, recipe, common.HANSEN_FL_TIF, common.recipe_dir(recipe), workers)
    record["min_available_gb"] = round(watch.min_gb, 2)
    record["errors"] = [r["fips"] for r in runs if r["status"] != "ok"]
    print(json.dumps(record, indent=2))
    return record


def stage_stitch(recipe: Recipe) -> dict:
    record: dict = {}
    fips = list(common.florida_counties()["ADMIN_FIPS"])
    missing = sorted(set(fips) - done(common.recipe_dir(recipe)))
    if missing:
        raise SystemExit(f"{len(missing)} counties not done: {missing}")
    with MemoryWatch() as watch, common.measured("stitch", record):
        total = ci.stitch(fips, common.recipe_dir(recipe), inputs(common.HANSEN_FL_TIF), STATE_NAME)
    record["min_available_gb"] = round(watch.min_gb, 2)
    log = common.recipe_dir(recipe) / "run_log.jsonl"
    runs = pd.DataFrame([json.loads(line) for line in log.read_text().splitlines() if line.strip()])
    runs.to_csv(common.OUTPUTS / f"02_county_runs_{recipe}.csv", index=False)
    record["summary"] = {k: total[k] for k in ("added_back_acres", "improved_forest_acres",
                                               "published_forest_acres", "patches",
                                               "stitch_overlap_pixels")}
    common.write_json(common.OUTPUTS / f"02_stitch_{recipe}.json", record)
    return record


def provenance_acres(path: Path) -> dict[str, float]:
    with rasterio.open(path) as src:
        counts = np.bincount(src.read(1).ravel(), minlength=256)
    return {ci.TreeMapProvenance(code).name.lower(): round(float(counts[code]) * ci.ACRES_PER_PIXEL, 1)
            for code in ci.TreeMapProvenance}


def stage_check(recipe: Recipe) -> dict:
    """Published mosaic == TreeMap; provenance == county summaries; FL5 == the gate rerun."""
    state = common.recipe_dir(recipe) / STATE_NAME
    record: dict = {}
    with rasterio.open(state / "treemap2022_published.tif") as src:
        published = src.read(1)
        grid = ci.Grid(src.transform, (src.height, src.width))
        nodata = src.nodata
    treemap = ci.read_on_grid(common.TREEMAP_TIF, grid, fill=nodata)
    has = published != nodata
    record["published_differs_from_treemap_px"] = int((published[has] != treemap[has]).sum())
    record["published_px"] = int(has.sum())
    del published, treemap, has
    acres = provenance_acres(state / "treemap2022_provenance.tif")
    total = json.loads((state / "summary.json").read_text())
    record["provenance_acres"] = acres
    record["summary_added_back_acres"] = total["added_back_acres"]
    record["raster_added_back_acres"] = round(acres["added_back"] + acres["added_back_young"], 1)
    record["fl5_differs_from_gate_px"] = {}
    for name in ("treemap2022_improved.tif", "treemap2022_provenance.tif"):
        with rasterio.open(gate_dir(recipe) / ci.AOI_NAME / name) as src:
            gate, fl5 = src.read(1), ci.Grid(src.transform, (src.height, src.width))
        statewide = ci.read_on_grid(state / name, fl5, fill=src.nodata)
        in_fl5 = gate != src.nodata
        record["fl5_differs_from_gate_px"][name] = int((statewide[in_fl5] != gate[in_fl5]).sum())
    common.write_json(common.OUTPUTS / f"02_check_{recipe}.json", record)
    return record


def county_index(grid: ci.Grid) -> np.ndarray:
    """1..67 by FIPS order on `grid`, 0 outside Florida; the same pixel-centre rule as the runs."""
    from rasterio import features

    counties = common.florida_counties()
    shapes = [(geom, i) for i, geom in enumerate(counties.geometry, start=1)]
    return features.rasterize(shapes, out_shape=grid.shape, transform=grid.transform, fill=0,
                              dtype="uint8")


def accepted_by_rule(bits: np.ndarray, rule: ConsensusRule) -> np.ndarray:
    """The rule applied to gated method bits (1 bookends, 2 Obata, 4 Hansen)."""
    if rule is ConsensusRule.UNION:
        return bits > 0
    return ((bits & 1) > 0) | (((bits & 2) > 0) & ((bits & 4) > 0))


def seam_counts(added: np.ndarray, tm: np.ndarray, prov: np.ndarray, county: np.ndarray,
                left_out: np.ndarray) -> dict:
    """Patches (8-connected added-back pixels) spanning counties, and how many disagree.

    One county run gives a patch one donor and one establishment mode, so a crossing
    patch with more than one of either was decided differently on each side. `left_out`
    pixels pass the rule but were not added back; one touching an added pixel of another
    county was cut off at the line.
    """
    from scipy import ndimage

    labels, _ = ndimage.label(added, structure=np.ones((3, 3), dtype=bool))
    idx = np.flatnonzero(added)
    df = pd.DataFrame({"patch": labels.ravel()[idx], "county": county.ravel()[idx],
                       "tm": tm.ravel()[idx], "prov": prov.ravel()[idx]})
    per = df.groupby("patch").agg(px=("county", "size"), counties=("county", "nunique"),
                                  donors=("tm", "nunique"), modes=("prov", "nunique"))
    crossing = per[per["counties"] > 1]
    split_donor = crossing[crossing["donors"] > 1]
    split_mode = crossing[crossing["modes"] > 1]
    cut = np.zeros(added.shape, dtype=bool)
    rows, cols = added.shape
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == dc == 0:
                continue
            here = (slice(max(0, -dr), rows - max(0, dr)), slice(max(0, -dc), cols - max(0, dc)))
            there = (slice(max(0, dr), rows - max(0, -dr)), slice(max(0, dc), cols - max(0, -dc)))
            cut[here] |= left_out[here] & added[there] & (county[there] != county[here])
    acres = ci.ACRES_PER_PIXEL
    return {"patches": int(len(per)), "crossing_patches": int(len(crossing)),
            "crossing_acres": round(float(crossing["px"].sum()) * acres, 1),
            "split_donor_patches": int(len(split_donor)),
            "split_donor_acres": round(float(split_donor["px"].sum()) * acres, 1),
            "split_mode_patches": int(len(split_mode)),
            "split_mode_acres": round(float(split_mode["px"].sum()) * acres, 1),
            "cut_at_line_px": int(cut.sum())}


def stage_seams(recipe: Recipe) -> dict:
    state = common.recipe_dir(recipe) / STATE_NAME
    with rasterio.open(state / "treemap2022_provenance.tif") as src:
        prov, grid = src.read(1), ci.Grid(src.transform, (src.height, src.width))
    added = ci.TreeMapProvenance.added_back(prov)
    bits = ci.read_on_grid(state / "add_back_method_bits.tif", grid, fill=0)
    rejected = ci.read_on_grid(state / "evt2022_patch_rejected.tif", grid, fill=0) == 1
    left_out = accepted_by_rule(bits, SETTINGS[recipe].rule) & (bits != ci.OUTSIDE) & ~added & ~rejected
    del bits, rejected
    tm = ci.read_on_grid(state / "treemap2022_improved.tif", grid, fill=0)
    record = {"recipe": str(recipe)}
    with common.measured("seams", record):
        record |= seam_counts(added, tm, prov, county_index(grid), left_out)
    common.write_json(common.OUTPUTS / f"02_seams_{recipe}.json", record)
    return record


def no_obata_tif() -> Path:
    """A one-pixel zero raster on the TreeMap lattice: Obata proposes nothing anywhere."""
    path = common.INPUTS / "obata_none.tif"
    if not path.exists():
        with rasterio.open(common.TREEMAP_TIF) as src:
            transform = src.transform
        path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(path, "w", driver="GTiff", width=1, height=1, count=1, dtype="float32",
                           crs="EPSG:5070", transform=transform) as dst:
            dst.write(np.zeros((1, 1, 1), dtype=np.float32))
    return path


def scene_split(prov: np.ndarray, transform, scene: rasterio.coords.BoundingBox,
                loss: float) -> dict:
    """Add-back inside and outside the Obata scene's bbox, by pixel centre.

    Outside it only the evidence without Obata ran, so the add-back found there is the
    `1 - loss` share FL5 keeps without Obata, and `loss / (1 - loss)` of it was missed.
    """
    rows, cols = np.arange(prov.shape[0]), np.arange(prov.shape[1])
    y = transform.f + (rows + 0.5) * transform.e
    x = transform.c + (cols + 0.5) * transform.a
    in_scene = (((y >= scene.bottom) & (y <= scene.top))[:, None]
                & ((x >= scene.left) & (x <= scene.right))[None, :])
    added = ci.TreeMapProvenance.added_back(prov)
    land = np.isin(prov, [p for p in ci.TreeMapProvenance if p is not ci.TreeMapProvenance.WATER])
    outside = float((added & ~in_scene).sum()) * ci.ACRES_PER_PIXEL
    return {"added_back_in_scene_ac": round(float((added & in_scene).sum()) * ci.ACRES_PER_PIXEL, 1),
            "added_back_outside_ac": round(outside, 1),
            "land_share_in_scene": round(float((land & in_scene).sum() / land.sum()), 3),
            "fl5_loss_without_obata": round(loss, 3),
            "est_missing_outside_ac": round(outside * loss / (1 - loss), 1)}


def stage_obata_gap(recipe: Recipe, workers: int) -> dict:
    """FL5 added back with and without Obata, and what the counties off its scene miss."""
    s = SETTINGS[recipe]
    out_root = common.WORK / "obata_gap" / str(recipe)
    fl5 = list(ci.AOI_COUNTIES)
    no_obata = ci.Inputs(obata=no_obata_tif(), hansen=common.HANSEN_AOI_TIF)
    with ProcessPoolExecutor(max_workers=workers, max_tasks_per_child=1) as pool:
        list(pool.map(_improve_without_obata, fl5, [recipe] * len(fl5), [out_root] * len(fl5)))
    without = ci.stitch(fl5, out_root, no_obata)
    with_obata = json.loads((gate_dir(recipe) / ci.AOI_NAME / "summary.json").read_text())
    record = {k: {"with_obata": with_obata[k], "without_obata": without[k]}
              for k in ("added_back_acres", "patches", "improved_forest_acres")}
    record["settings"] = {"rule": str(s.rule), "gate": str(s.gate), "window": str(s.window)}
    loss = 1 - without["added_back_acres"] / with_obata["added_back_acres"]
    with rasterio.open(common.OBATA_TIF) as src:
        scene = src.bounds
    with rasterio.open(common.recipe_dir(recipe) / STATE_NAME / "treemap2022_provenance.tif") as src:
        record["statewide"] = scene_split(src.read(1), src.transform, scene, loss)
    common.write_json(common.OUTPUTS / f"02_obata_gap_{recipe}.json", record)
    return record


def _improve_without_obata(fips: str, recipe: Recipe, out_root: Path) -> None:
    s = SETTINGS[recipe]
    ci.improve_county(fips, ci.Inputs(obata=no_obata_tif(), hansen=common.HANSEN_AOI_TIF), out_root,
                      rule=s.rule, evt_gate=s.gate, window=s.window)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", type=Stage, choices=list(Stage))
    parser.add_argument("--recipe", type=Recipe, choices=list(Recipe), required=True)
    parser.add_argument("--workers", type=int, default=common.workers(4))
    parser.add_argument("--counties", nargs="+", default=None, help="FIPS (default: all 67)")
    args = parser.parse_args()
    if args.stage is Stage.GATE:
        print(json.dumps(stage_gate(args.recipe, args.workers)["differing_pixels"], indent=2))
    elif args.stage is Stage.COUNTIES:
        stage_counties(args.recipe, args.workers, args.counties)
    elif args.stage is Stage.STITCH:
        print(json.dumps(stage_stitch(args.recipe), indent=2))
    elif args.stage is Stage.CHECK:
        print(json.dumps(stage_check(args.recipe), indent=2))
    elif args.stage is Stage.SEAMS:
        print(json.dumps(stage_seams(args.recipe), indent=2))
    else:
        print(json.dumps(stage_obata_gap(args.recipe, args.workers), indent=2))


if __name__ == "__main__":
    main()
