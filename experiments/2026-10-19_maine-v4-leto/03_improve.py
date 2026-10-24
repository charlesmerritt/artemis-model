"""Step 3: the v4-improved TreeMap 2022 and NWOS for Maine, one county at a time.

Per county, :func:`county_improvement.improve_county` with the v4 recipe (see ``common``):
rule ``bookends_or_obata_and_hansen``, EVT gate v4 (v3 and the quarry patch veto),
``DisturbanceWindow.Y2012_2022``. Obata is the p17/r39 scene, which reads 0 over Maine, so
v4 here is bookends only, as in the FL statewide run off that scene. The counties are then stitched into
``work/rasters/v4/maine/``. Next, :mod:`add_back_stand_age` dates the add-back pixels from
Hansen, and :mod:`add_back_ids` gives each add-back pixel a young plot under a new ``TM_ID``
(``maine/young_plots/``), drawn from FIA plots in ``common.FIA_STATES``. TreeMap holds
only 96 young plots there, none of the white pine group, so a pixel with no young plot keeps
its neighbour plot at its dated age (``UnmatchedYoungPlotPolicy.KEEP_NEIGHBOUR``, match level
``neighbour``). Finished counties are skipped.

    uv run python experiments/2026-10-19_maine-v4-leto/03_improve.py --stage counties [--workers 3]
    uv run python experiments/2026-10-19_maine-v4-leto/03_improve.py --stage young   # needs FIADB
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from enum import StrEnum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402

sys.path.insert(0, str(c.REPO))
from pipeline.s1_initial_state import county_improvement as ci  # noqa: E402
from pipeline.s1_initial_state.add_back_methods import ConsensusRule  # noqa: E402
from pipeline.s1_initial_state.evt_gate import EvtGatePolicy  # noqa: E402


class Stage(StrEnum):
    COUNTIES = "counties"   # improve each county, stitch, date the add-back pixels
    YOUNG = "young"         # young plots under new IDs (reads FIADB)


def use_maine(names: dict[str, str]) -> None:
    """Point county_improvement's county names and stitch name at Maine."""
    ci.AOI_COUNTIES = names
    ci.AOI_NAME = c.RUN_NAME


def improve(fips: str, names: dict[str, str]) -> dict:
    use_maine(names)
    summary_path = ci.county_dir(c.RUN_ROOT, fips) / "summary.json"
    if summary_path.exists():
        return json.loads(summary_path.read_text())
    record: dict = {}
    with c.measured("improve", record):
        summary = ci.improve_county(fips, c.improvement_inputs(), c.RUN_ROOT,
                                    rule=ConsensusRule.BOOKENDS_OR_OBATA_AND_HANSEN,
                                    evt_gate=EvtGatePolicy.AGRICULTURE_DEVELOPED_PASTURE_QUARRY_V4,
                                    window=ci.DisturbanceWindow.Y2012_2022)
    summary["measured"] = record
    summary_path.write_text(json.dumps(summary, indent=2))
    return summary


def _improve(args):
    fips, names = args
    try:
        return improve(fips, names)
    except Exception:
        return {"county_fips": fips, "error": traceback.format_exc()}


def run_counties(workers: int, only: list[str]) -> None:
    from pipeline.s1_initial_state.add_back_stand_age import write_stand_age

    counties = c.maine_counties()
    names = dict(zip(counties["ADMIN_FIPS"], counties["NAME"]))
    use_maine(names)
    # Largest first so the long counties do not start last.
    order = counties.assign(_a=counties.geometry.envelope.area).sort_values("_a", ascending=False)
    fips_list = [f for f in order["ADMIN_FIPS"] if not only or f in only]
    errors = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(_improve, (f, names)) for f in fips_list]):
            s = future.result()
            if "error" in s:
                errors.append(s["county_fips"])
                print(s["error"], flush=True)
                continue
            print(json.dumps({k: s.get(k) for k in ("county_fips", "county", "hole_acres",
                                                    "added_back_acres", "pad_px", "measured")}),
                  flush=True)
    if errors:
        raise SystemExit(f"counties failed: {errors}")
    if only:
        return
    record: dict = {}
    with c.measured("stitch", record):
        total = ci.stitch(sorted(names), c.RUN_ROOT, c.improvement_inputs(), name=c.RUN_NAME)
    with c.measured("stand_age", record):
        write_stand_age(c.RUN_DIR, obata=None, hansen=c.HANSEN_VRT)
    total["measured"] = record
    c.write_json(c.WORK / "03_improve.json", total)
    print(json.dumps({k: total[k] for k in ("added_back_acres", "patches", "added_back_acres_by_mode",
                                            "evt_gate", "rule", "measured")}, indent=1))


def run_young() -> None:
    from pipeline.s1_initial_state.add_back_ids import (
        UnmatchedYoungPlotPolicy,
        read_vat,
        write_add_back_ids,
    )
    from pipeline.s1_initial_state.young_plot_library import read_young_plot_library

    vat_path = Path(str(c.TREEMAP_TIF) + ".vat.dbf")
    record: dict = {}
    with c.measured("young_plots", record):
        report = write_add_back_ids(
            c.RUN_DIR, vat=read_vat(vat_path),
            library=read_young_plot_library(vat_path, c.FIADB, c.TREE_TABLE, states=c.FIA_STATES),
            tree_table=c.TREE_TABLE, obata=None, hansen=c.HANSEN_VRT,
            unmatched=UnmatchedYoungPlotPolicy.KEEP_NEIGHBOUR)
    report["measured"] = record
    c.write_json(c.WORK / "03_young.json", report)
    print(json.dumps(report, indent=1))
    if not all(report["checks"].values()):
        raise SystemExit("a check failed: the young_plots outputs do not join")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", type=Stage, default=Stage.COUNTIES, choices=list(Stage))
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("fips", nargs="*", help="only these counties (no stitch)")
    args = parser.parse_args()
    if args.stage is Stage.COUNTIES:
        run_counties(args.workers, args.fips)
    else:
        run_young()


if __name__ == "__main__":
    main()
