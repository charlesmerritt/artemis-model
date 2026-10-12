"""Step 2 — one FVS input database for every raster set's stands.

* A plot stand is 2026-09-14's `build_input_db` row, unchanged: the plot's own StandInit and
  TreeInit rows from `FIA_5county_consolidated.db`, anchored to INV_YEAR 2022.
* A scaled-young stand `Y<TM_ID>` is `landscape.young_fvs_inputs`: the donor plot's StandInit
  row at the establishment age, with the scaled establishment list for trees.

One database serves every set, so a keyfile that two sets share is one FVS run. That is only
sound if a stand's rows are the same in every set; the establishment lists are therefore
checked identical across sets for every donor they share.

Writes `work/FVS_Data.db` and `work/fvs_inputs.json`.

    uv run python experiments/2026-10-12_raster-set-schedules/02_fvs_inputs.py
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

EXP = Path(__file__).resolve().parent
REPO = EXP.parents[1]
sys.path[:0] = [str(EXP), str(REPO)]

import landscape as L  # noqa: E402
import raster_sets as RS  # noqa: E402
from pipeline.ids import as_id_series  # noqa: E402

spec = importlib.util.spec_from_file_location("timing_library_20260914",
                                              REPO / "weekly-artifact/2026-09-14/make_timing_library.py")
TL = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = TL
spec.loader.exec_module(TL)

FVS_DATA_DB = RS.WORK / "FVS_Data.db"


def stands_of(rs: RS.RasterSet) -> pd.DataFrame:
    return pd.read_csv(RS.work_dir(rs) / "carved_stands.csv", dtype={"PLT_CN": str, "unit_id": str})


def union_lists() -> pd.DataFrame:
    """Every set's scaled-young lists, one copy per donor, refusing a donor whose list differs."""
    frames = []
    for rs in RS.IMPROVED:
        lists = pd.read_csv(RS.work_dir(rs) / "establishment_tree_lists.csv",
                            dtype={"TM_ID": str, "PLT_CN": str})
        frames.append(lists[lists["establishment_mode"] == str(L.PixelOrigin.SCALED_YOUNG)]
                      .assign(raster_set=rs.name))
    lists = pd.concat(frames, ignore_index=True)
    cols = ["TM_ID", "PLT_CN", "SPCD", "TPA_UNADJ", "DIA", "HT", "establishment_age"]
    per_set = lists.groupby(["raster_set", "TM_ID"])[cols].apply(
        lambda g: g.sort_values("SPCD").round(9).to_csv(index=False))
    differing = per_set.groupby(level="TM_ID").nunique()
    if (differing > 1).any():
        raise AssertionError(f"donors whose list differs between sets: {list(differing[differing > 1].index)[:5]}")
    return lists.drop_duplicates(["TM_ID", "SPCD"]).drop(columns="raster_set")


def main() -> None:
    plots: set[str] = set()
    young: set[str] = set()
    for rs in RS.ALL:
        cns = stands_of(rs)["PLT_CN"]
        young |= {c for c in cns if c.startswith(L.YOUNG_PREFIX)}
        plots |= {c for c in cns if not c.startswith(L.YOUNG_PREFIX)}

    lists = union_lists()
    young_listed = {L.young_stand_cn(t) for t in lists["TM_ID"]}
    if not young <= young_listed:
        raise AssertionError(f"young stands without a list: {sorted(young - young_listed)[:5]}")
    lists = lists[lists["TM_ID"].map(L.young_stand_cn).isin(young)]

    # The plot stands, exactly as 2026-09-14 writes them; the donors ride along for their site rows.
    TL.WORK, TL.FVS_DATA_DB = RS.WORK, FVS_DATA_DB
    donors = set(as_id_series(lists["PLT_CN"], column="PLT_CN"))
    _, fingerprint = TL.build_input_db(plots | donors)

    con = sqlite3.connect(FVS_DATA_DB)
    stand_rows = pd.read_sql("SELECT * FROM FVS_StandInit_Plot", con)
    stand_rows["STAND_CN"] = as_id_series(stand_rows["STAND_CN"], column="STAND_CN")
    y_stand, y_trees = L.young_fvs_inputs(lists, stand_rows[stand_rows["STAND_CN"].isin(donors)])
    y_stand["STAND_ID"] = "S" + y_stand["STAND_CN"]
    y_trees["STAND_ID"] = "S" + y_trees["STAND_CN"]
    y_stand["INV_YEAR"] = TL.INV_YEAR
    y_stand.to_sql("FVS_StandInit_Plot", con, if_exists="append", index=False)
    y_trees.to_sql("FVS_TreeInit_Plot", con, if_exists="append", index=False)
    con.commit()
    n_stand = con.execute("SELECT COUNT(*) FROM FVS_StandInit_Plot").fetchone()[0]
    con.close()

    report = {"plot_stands": len(plots | donors), "plots_in_some_landscape": len(plots),
              "donor_plots_only_for_site_rows": len(donors - plots),
              "young_stands": len(y_stand), "young_tree_rows": len(y_trees),
              "stand_rows": n_stand, "plot_tree_list_fingerprint": fingerprint}
    (RS.WORK / "fvs_inputs.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
